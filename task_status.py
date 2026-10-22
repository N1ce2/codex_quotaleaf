"""Read-only local task projection. No conversations, prompts or credentials are saved."""
from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, BinaryIO

WAIT_TOOLS = {'request_user_input', 'request_user_input_async', 'request_permissions'}
@dataclass(frozen=True)
class _RolloutTurn:
    id: str
    state: str
    started_at: float
    completed_at: float = 0


@dataclass
class _RolloutCache:
    stamp: tuple[int, int, int]
    pending: dict[str, str]
    offset: int
    turn: _RolloutTurn | None


_PENDING_CACHE: dict[str, _RolloutCache] = {}


def _event_time(value: object) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
        except (ValueError, OverflowError, OSError):
            pass
    return 0


def _rollout_turn(record: dict[str, Any], previous: _RolloutTurn | None) -> _RolloutTurn | None:
    if record.get('type') != 'event_msg':
        return previous
    payload = record.get('payload') or {}
    states = {'task_started': 'running', 'task_complete': 'completed',
              'turn_aborted': 'interrupted'}
    state = states.get(payload.get('type', ''))
    turn_id = payload.get('turn_id')
    if state is None or not isinstance(turn_id, str) or not turn_id:
        return previous
    same_turn = previous is not None and previous.id == turn_id
    started = _event_time(payload.get('started_at'))
    if not started and same_turn and previous is not None:
        started = previous.started_at
    if not started and state == 'running':
        started = _event_time(record.get('timestamp'))
    completed = (_event_time(payload.get('completed_at')) or
                 _event_time(record.get('timestamp'))) if state == 'completed' else 0
    # A late terminal event for an older turn cannot end the current turn.
    if previous is None or same_turn or started > previous.started_at:
        return _RolloutTurn(turn_id, state, started, completed)
    return previous


def _find_turn_before(stream: BinaryIO, end: int) -> _RolloutTurn | None:
    """Find a lifecycle marker outside the initial tail without parsing all chat items."""
    while end > 0:
        start = max(0, end - 2_000_000)
        stream.seek(start)
        if start:
            stream.readline()
        size = end - stream.tell()
        if size > 0:
            data = stream.read(size)
            if not data.endswith(b'\n'):
                data += stream.readline()
            for line in reversed(data.splitlines()):
                if not any(marker in line for marker in (
                    b'"task_started"', b'"task_complete"', b'"turn_aborted"'
                )):
                    continue
                try:
                    turn = _rollout_turn(json.loads(line), None)
                except (ValueError, UnicodeError):
                    continue
                if turn is not None:
                    return turn
        end = start
    return None


def _read_rollout(path: str | Path) -> tuple[bool, _RolloutTurn | None]:
    """Read lifecycle and input events together, incrementally and without saving content."""
    pending: dict[str, str] = {}
    turn = None
    try:
        stat=Path(path).stat()
        stamp=(stat.st_mtime_ns,stat.st_size,stat.st_ino)
        cached=_PENDING_CACHE.get(str(path))
        if cached and cached.stamp == stamp:
            return bool(cached.pending), cached.turn
        with Path(path).open('rb') as stream:
            if cached and stat.st_size > cached.stamp[1] and stat.st_ino == cached.stamp[2]:
                stream.seek(cached.offset)
                pending=dict(cached.pending)
                turn=cached.turn
            else:
                stream.seek(max(0,stat.st_size-2_000_000))
                if stream.tell():stream.readline()
            start = stream.tell()
            while True:
                offset=stream.tell()
                line=stream.readline()
                # An appending writer can leave a partial JSON line. Re-read it
                # next time instead of permanently discarding its request event.
                if not line or not line.endswith(b'\n'):
                    break
                try:
                    record = json.loads(line)
                except (ValueError, UnicodeError):
                    continue
                payload = record.get('payload') or {}
                kind = payload.get('type') if record.get('type') == 'event_msg' else record.get('type')
                turn = _rollout_turn(record, turn)
                if kind in ('task_started', 'turn_aborted'):
                    pending.clear()
                elif kind == 'task_complete':
                    pending = {key:name for key,name in pending.items() if name == 'request_user_input_async'}
                elif kind == 'response_item':
                    name = payload.get('name', '').rsplit('.', 1)[-1]
                    if payload.get('type') == 'function_call' and name in WAIT_TOOLS:
                        pending[payload.get('call_id', name)] = name
                    elif payload.get('type') == 'function_call_output':
                        key=payload.get('call_id', '')
                        if pending.get(key) != 'request_user_input_async':pending.pop(key,None)
                    elif payload.get('type') == 'message' and payload.get('role') == 'user':
                        pending.clear()
                elif kind in ('request_user_input', 'exec_approval_request', 'apply_patch_approval_request'):
                    pending[payload.get('call_id') or payload.get('id') or kind] = kind
                elif kind in ('server_request_resolved', 'serverRequest/resolved'):
                    pending.pop(payload.get('requestId') or payload.get('id') or '',None)
            if turn is None and start:
                turn = _find_turn_before(stream, start)
    except OSError:
        return False, None
    if len(_PENDING_CACHE)>512:_PENDING_CACHE.clear()
    _PENDING_CACHE[str(path)] = _RolloutCache(stamp, dict(pending), offset, turn)
    return bool(pending), turn


def pending_input(path: str | Path) -> bool:
    """Only explicit pending input/approval events count; slow tools never imply approval."""
    return _read_rollout(path)[0]


@dataclass(frozen=True)
class Task:
    id: str
    turn: str
    title: str
    state: str
    completed_at: float = 0


def read_tasks(home: str | Path | None = None) -> list[Task]:
    """Local history is implementation-specific: fail visibly if its schema changes."""
    root = Path(home or os.environ.get('CODEX_HOME') or Path.home()/'.codex')
    histories = sorted(root.glob('thread_history_*.sqlite'), key=lambda p:p.stat().st_mtime, reverse=True)
    states = sorted(root.glob('state_*.sqlite'), key=lambda p:p.stat().st_mtime, reverse=True)
    if not histories or not states:
        raise OSError('当前 Codex 未提供本地任务索引')
    def connect(path: Path) -> sqlite3.Connection:
        db = sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True, timeout=.2)
        db.execute('PRAGMA query_only=ON')
        return db
    with closing(connect(states[0])) as db:
        threads = {row[0]:row[1:] for row in db.execute(
            'SELECT id,title,rollout_path,source FROM threads WHERE archived=0')}
    with closing(connect(histories[0])) as db:
        # One task per conversation, not one per tool or sub-agent.
        rows = db.execute('''SELECT thread_id,turn_id,status,completed_at,started_at FROM
            (SELECT *, ROW_NUMBER() OVER (PARTITION BY thread_id ORDER BY rollout_ordinal DESC) AS rn
             FROM thread_turns) WHERE rn=1''').fetchall()
    tasks = []
    latest = {row[0]: row[1:] for row in rows}
    for tid, (title, path, source) in threads.items():
        if 'subagent' in str(source).lower():
            continue
        indexed = latest.get(tid)
        turn, status, completed, started = indexed or ('', '', 0, 0)
        state = {'inProgress':'running', 'completed':'completed',
                 'failed':'failed', 'interrupted':'interrupted'}.get(status, 'unknown')
        pending, event = _read_rollout(path)
        if event is not None and (
            indexed is None or
            (event.id != turn and event.started_at > (started or 0)) or
            (event.id == turn and state in ('running', 'unknown'))
        ):
            completed = event.completed_at or (completed if event.id == turn else 0)
            turn, state = event.id, event.state
        elif indexed is None:
            continue
        if state in ('running','completed') and pending:
            state = 'waiting'
        tasks.append(Task(tid, turn, title or '未命名任务', state, completed or 0))
    return tasks


class TaskTracker:
    def __init__(self, started_at=None):
        self.started_at = time.time() if started_at is None else started_at
        self.tasks = []
        self.completed = {}
        self.seen = set()
        self.previous = {}
        self.acknowledged = set()
        self.error = '正在读取任务状态'

    def update(self, tasks):
        notices = []
        current_keys={(t.id,t.turn,t.state) for t in tasks}
        self.acknowledged.intersection_update(current_keys)
        for task in tasks:
            key = (task.id, task.turn)
            previous = self.previous.get(task.id)
            if task.state == 'completed' and task.completed_at >= self.started_at and key not in self.seen:
                self.seen.add(key)
                self.completed[task.id] = task
                notices.append(('completed', task))
            if task.state == 'waiting' and previous != (task.turn, 'waiting'):
                notices.append(('waiting', task))
            if task.state in ('running', 'waiting'):
                self.completed.pop(task.id, None)
        visible = {t.id for t in tasks}
        self.completed = {k:v for k,v in self.completed.items() if k in visible}
        self.tasks = tasks
        self.previous = {t.id:(t.turn, t.state) for t in tasks}
        self.error = ''
        return notices

    def counts(self):
        return tuple(len(self.group(state)) for state in ('waiting','completed','running'))

    def group(self, state):
        candidates=list(self.completed.values()) if state=='completed' else [t for t in self.tasks if t.state==state]
        return [t for t in candidates if (t.id,t.turn,t.state) not in self.acknowledged]

    def current_group(self):
        if not self.error:
            for state in ('waiting','completed','running'):
                tasks=self.group(state)
                if tasks:return state,tasks
        return '',[]

    def label(self):
        if self.error:
            return '任务状态不可用' if self.tasks else self.error
        waiting, completed, running = self.counts()
        if waiting:return f'有 {waiting} 个任务等待确认'
        if completed:return f'有 {completed} 个任务已完成'
        if running:return f'{running} 个任务正在运行'
        return '暂无任务提醒'

    def mini_label(self):
        if self.error:return '状态未知'
        state,tasks=self.current_group()
        if tasks:return f"{dict(waiting='待确认',completed='完成',running='运行')[state]} {len(tasks)}"
        return '已查看' if any(t.state in ('running','waiting') for t in self.tasks) else '空闲'

    def poll_seconds(self, active, idle):
        # Unknown state must not put an active session into power-saving mode.
        if self.error or any(t.state in ('running', 'unknown') for t in self.tasks):
            return active
        return idle

    def acknowledge(self, task=None):
        if task is None:
            self.completed.clear()
            return
        if task.state == 'running':
            return
        self.acknowledged.add((task.id,task.turn,task.state))
        current=self.completed.get(task.id)
        if current and current.turn==task.turn:self.completed.pop(task.id,None)
