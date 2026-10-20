"""Read-only local task projection. No conversations, prompts or credentials are saved."""
from __future__ import annotations

from dataclasses import dataclass
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import time


WAIT_TOOLS = {'request_user_input', 'request_user_input_async', 'request_permissions'}
_PENDING_CACHE = {}


def pending_input(path):
    """Only explicit pending input/approval events count; slow tools never imply approval."""
    pending = {}
    try:
        stat=Path(path).stat()
        stamp=(stat.st_mtime_ns,stat.st_size)
        cached=_PENDING_CACHE.get(str(path))
        if cached and cached['stamp'] == stamp:return bool(cached['pending'])
        with Path(path).open('rb') as stream:
            if cached and stat.st_size >= cached['offset'] and stat.st_ino == cached['inode']:
                stream.seek(cached['offset'])
                pending=dict(cached['pending'])
            else:
                stream.seek(max(0,stat.st_size-2_000_000))
                if stream.tell():stream.readline()
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
                if kind in ('task_started', 'turn_aborted'):
                    pending.clear()
                elif kind == 'task_complete':
                    pending = {key:name for key,name in pending.items() if name == 'request_user_input_async'}
                elif kind == 'response_item':
                    name = payload.get('name', '').rsplit('.', 1)[-1]
                    if payload.get('type') == 'function_call' and name in WAIT_TOOLS:
                        pending[payload.get('call_id', name)] = name
                    elif payload.get('type') == 'function_call_output':
                        key=payload.get('call_id')
                        if pending.get(key) != 'request_user_input_async':pending.pop(key,None)
                    elif payload.get('type') == 'message' and payload.get('role') == 'user':
                        pending.clear()
                elif kind in ('request_user_input', 'exec_approval_request', 'apply_patch_approval_request'):
                    pending[payload.get('call_id') or payload.get('id') or kind] = kind
                elif kind in ('server_request_resolved', 'serverRequest/resolved'):
                    pending.pop(payload.get('requestId') or payload.get('id'),None)
    except OSError:
        return False
    if len(_PENDING_CACHE)>512:_PENDING_CACHE.clear()
    _PENDING_CACHE[str(path)]={'stamp':stamp,'pending':dict(pending),'offset':offset,'inode':stat.st_ino}
    return bool(pending)


@dataclass(frozen=True)
class Task:
    id: str
    turn: str
    title: str
    state: str
    completed_at: float = 0


def read_tasks(home=None):
    """Local history is implementation-specific: fail visibly if its schema changes."""
    root = Path(home or os.environ.get('CODEX_HOME') or Path.home()/'.codex')
    histories = sorted(root.glob('thread_history_*.sqlite'), key=lambda p:p.stat().st_mtime, reverse=True)
    states = sorted(root.glob('state_*.sqlite'), key=lambda p:p.stat().st_mtime, reverse=True)
    if not histories or not states:
        raise OSError('当前 Codex 未提供本地任务索引')
    def connect(path):
        db = sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True, timeout=.2)
        db.execute('PRAGMA query_only=ON')
        return db
    with closing(connect(states[0])) as db:
        threads = {row[0]:row[1:] for row in db.execute(
            'SELECT id,title,rollout_path,source FROM threads WHERE archived=0')}
    with closing(connect(histories[0])) as db:
        # One task per conversation, not one per tool or sub-agent.
        rows = db.execute('''SELECT thread_id,turn_id,status,completed_at FROM
            (SELECT *, ROW_NUMBER() OVER (PARTITION BY thread_id ORDER BY rollout_ordinal DESC) AS rn
             FROM thread_turns) WHERE rn=1''').fetchall()
    tasks = []
    for tid, turn, status, completed in rows:
        if tid not in threads:
            continue
        title, path, source = threads[tid]
        if 'subagent' in str(source).lower():
            continue
        state = {'inProgress':'running', 'completed':'completed',
                 'failed':'failed', 'interrupted':'interrupted'}.get(status, 'unknown')
        if state in ('running','completed') and pending_input(path):
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
        self.acknowledged.add((task.id,task.turn,task.state))
        current=self.completed.get(task.id)
        if current and current.turn==task.turn:self.completed.pop(task.id,None)
