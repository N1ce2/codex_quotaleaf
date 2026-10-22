"""Regressions for live rollout events getting ahead of the local history index."""

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from task_status import TaskTracker, read_tasks


class TaskIndexLag(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.rollout = self.root / 'session.jsonl'
        self.rollout.touch()
        with closing(sqlite3.connect(self.root / 'state_5.sqlite')) as db, db:
            db.execute('CREATE TABLE threads(id,title,rollout_path,source,archived)')
            db.execute(
                'INSERT INTO threads VALUES(?,?,?,?,0)', ('a', 'A', str(self.rollout), 'cli')
            )
        with closing(sqlite3.connect(self.root / 'thread_history_1.sqlite')) as db, db:
            db.execute("""CREATE TABLE thread_turns(
                thread_id,turn_id,status,completed_at,rollout_ordinal,started_at)""")

    def _index(
        self,
        turn: str = 'old',
        status: str = 'interrupted',
        started: int = 100,
        completed: int | None = None,
    ) -> None:
        with closing(sqlite3.connect(self.root / 'thread_history_1.sqlite')) as db, db:
            db.execute('DELETE FROM thread_turns')
            db.execute(
                'INSERT INTO thread_turns VALUES(?,?,?,?,?,?)',
                ('a', turn, status, completed, 1, started),
            )

    def _event(self, kind: str, turn: str = 'new', started: int = 200) -> None:
        payload: dict[str, object] = {'type': kind, 'turn_id': turn, 'started_at': started}
        if kind == 'task_complete':
            payload['completed_at'] = started + 10
        with self.rollout.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps({'type': 'event_msg', 'payload': payload}) + '\n')

    def _assert_task(self, turn: str, state: str) -> None:
        tasks = read_tasks(self.root)
        self.assertEqual([(task.id, task.turn, task.state) for task in tasks], [('a', turn, state)])

    def test_new_start_overrides_previous_terminal_index(self) -> None:
        for status in ('completed', 'interrupted', 'failed'):
            with self.subTest(status=status):
                self._index(status=status)
                self._event('task_started')
                self._assert_task('new', 'running')
                tracker = TaskTracker(started_at=150)
                tracker.update(read_tasks(self.root))
                self.assertEqual(tracker.mini_label(), '运行 1')
                self.assertEqual(tracker.poll_seconds(3, 300), 3)

    def test_new_thread_without_indexed_turn_is_detected(self) -> None:
        self._event('task_started')
        self._assert_task('new', 'running')

    def test_new_thread_without_events_has_no_task(self) -> None:
        self.assertEqual(read_tasks(self.root), [])

    def test_terminal_events_end_running_even_while_index_lags(self) -> None:
        self._index()
        self._event('task_started')
        self._assert_task('new', 'running')
        self._event('task_complete')
        self._assert_task('new', 'completed')
        tasks = read_tasks(self.root)
        self.assertEqual(tasks[0].completed_at, 210)
        tracker = TaskTracker(started_at=150)
        self.assertEqual([kind for kind, _ in tracker.update(tasks)], ['completed'])
        self.assertEqual(tracker.update(tasks), [])
        self._event('task_started', 'next', 220)
        self._assert_task('next', 'running')
        self._event('turn_aborted', 'next', 220)
        self._assert_task('next', 'interrupted')

    def test_same_turn_terminal_event_overrides_running_index(self) -> None:
        self._index('new', 'inProgress', 200)
        self._event('task_complete')
        self._assert_task('new', 'completed')

    def test_older_events_do_not_override_newer_index(self) -> None:
        self._index('newer', 'inProgress', 300)
        self._event('task_started')
        self._event('task_complete')
        self._assert_task('newer', 'running')

    def test_stale_start_does_not_revive_same_finished_turn(self) -> None:
        self._index('new', 'completed', 200, 210)
        self._event('task_started')
        self._assert_task('new', 'completed')

    def test_indexed_terminal_status_is_not_replaced_by_old_completion(self) -> None:
        self._event('task_complete')
        for status in ('failed', 'interrupted'):
            with self.subTest(status=status):
                self._index('new', status, 200)
                self._assert_task('new', status)

    def test_late_old_completion_does_not_stop_new_turn(self) -> None:
        self._index()
        self._event('task_started')
        self._event('task_complete', 'old', 100)
        self._assert_task('new', 'running')

    def test_partial_start_is_retried_after_writer_finishes_line(self) -> None:
        self._index()
        event = json.dumps(
            {
                'type': 'event_msg',
                'payload': {'type': 'task_started', 'turn_id': 'new', 'started_at': 200},
            }
        )
        self.rollout.write_text(event[:30], encoding='utf-8')
        self._assert_task('old', 'interrupted')
        with self.rollout.open('a', encoding='utf-8') as stream:
            stream.write(event[30:] + '\n')
        self._assert_task('new', 'running')

    def test_truncated_rollout_drops_cached_running_state(self) -> None:
        self._index()
        self._event('task_started')
        self._assert_task('new', 'running')
        self.rollout.write_text('', encoding='utf-8')
        self._assert_task('old', 'interrupted')

    def test_missing_rollout_uses_index(self) -> None:
        self._index('new', 'inProgress', 200)
        self.rollout.unlink()
        self._assert_task('new', 'running')

    def test_explicit_pending_input_still_has_priority(self) -> None:
        self._index()
        self._event('task_started')
        with self.rollout.open('a', encoding='utf-8') as stream:
            stream.write(
                json.dumps(
                    {
                        'type': 'response_item',
                        'payload': {
                            'type': 'function_call',
                            'name': 'functions.request_user_input',
                            'call_id': 'x',
                        },
                    }
                )
                + '\n'
            )
        self._assert_task('new', 'waiting')
        with self.rollout.open('a', encoding='utf-8') as stream:
            stream.write(
                json.dumps(
                    {
                        'type': 'response_item',
                        'payload': {'type': 'function_call_output', 'call_id': 'x'},
                    }
                )
                + '\n'
            )
        self._assert_task('new', 'running')

    def test_start_outside_initial_tail_is_detected(self) -> None:
        self._index()
        self._event('task_started')
        # Complete lines exceed the initial 2 MB window; no lifecycle event in the tail.
        with self.rollout.open('a', encoding='utf-8') as stream:
            stream.write((json.dumps({'type': 'other', 'payload': 'x' * 990}) + '\n') * 4100)
        self._assert_task('new', 'running')
        self._assert_task('new', 'running')

    def test_subagent_and_archived_threads_remain_excluded(self) -> None:
        self._event('task_started')
        with closing(sqlite3.connect(self.root / 'state_5.sqlite')) as db, db:
            db.execute(
                'INSERT INTO threads VALUES(?,?,?,?,?)',
                ('b', 'B', str(self.rollout), 'subagent', 0),
            )
            db.execute(
                'INSERT INTO threads VALUES(?,?,?,?,?)', ('c', 'C', str(self.rollout), 'cli', 1)
            )
        self._assert_task('new', 'running')
