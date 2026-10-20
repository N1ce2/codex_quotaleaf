import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication
from quota_card import QuotaCard, DEFAULTS
from task_status import Task, TaskTracker, pending_input, read_tasks
from desktop_controls import shortcut_parts, open_task_thread, open_codex_app


class TaskLogic(unittest.TestCase):
    def test_priority_acknowledge_and_deduplicate(self):
        tracker=TaskTracker(started_at=100)
        tasks=[Task('a','1','A','running'),Task('b','2','B','completed',101),Task('c','3','C','waiting')]
        self.assertEqual(len(tracker.update(tasks)),2)
        self.assertEqual(tracker.label(),'有 1 个任务等待确认')
        self.assertEqual(tracker.update(tasks),[])
        tracker.update(tasks[:2]);self.assertEqual(tracker.label(),'有 1 个任务已完成')
        tracker.acknowledge();self.assertEqual(tracker.label(),'1 个任务正在运行')
        tracker.update(tasks[:2]);self.assertEqual(tracker.label(),'1 个任务正在运行')

    def test_history_failure_and_interruption_are_not_completion(self):
        tracker=TaskTracker(started_at=100)
        self.assertEqual(tracker.update([Task('a','1','A','completed',99),Task('b','2','B','failed',101)]),[])
        self.assertEqual(tracker.counts(),(0,0,0))
        tracker.update([Task('a','3','A','completed',102)])
        tracker.update([Task('a','4','A','running')])
        self.assertEqual(tracker.counts(),(0,0,1))

    def test_idle_policy_does_not_use_display_priority(self):
        tracker=TaskTracker(100)
        self.assertEqual(tracker.poll_seconds(3,300),3)
        tracker.update([Task('a','1','A','waiting'),Task('b','2','B','running')])
        self.assertEqual(tracker.poll_seconds(3,300),3)
        tracker.update([Task('a','1','A','waiting')])
        self.assertEqual(tracker.poll_seconds(3,300),300)
        tracker.error='unavailable';self.assertEqual(tracker.poll_seconds(3,300),3)

    def test_only_pending_explicit_input_counts(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'session.jsonl'
            records=[{'type':'response_item','payload':{'type':'function_call','name':'functions.request_user_input','call_id':'x'}}]
            path.write_text(''.join(json.dumps(r)+'\n' for r in records))
            self.assertTrue(pending_input(path))
            records.append({'type':'response_item','payload':{'type':'function_call_output','call_id':'x'}})
            path.write_text(''.join(json.dumps(r)+'\n' for r in records));self.assertFalse(pending_input(path))
            records.append({'type':'response_item','payload':{'type':'function_call','name':'exec_command','call_id':'y'}})
            path.write_text(''.join(json.dumps(r)+'\n' for r in records));self.assertFalse(pending_input(path))

    def test_readonly_latest_turn_and_subagent_exclusion(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            db=sqlite3.connect(root/'state_5.sqlite')
            db.execute('CREATE TABLE threads(id,title,rollout_path,source,archived)')
            db.executemany('INSERT INTO threads VALUES(?,?,?,?,0)',[('a','A','','cli'),('b','B','','subagent')]);db.commit();db.close()
            db=sqlite3.connect(root/'thread_history_1.sqlite')
            db.execute('CREATE TABLE thread_turns(thread_id,turn_id,status,completed_at,rollout_ordinal)')
            db.executemany('INSERT INTO thread_turns VALUES(?,?,?,?,?)', [('a','1','completed',100,1),('a','2','inProgress',None,2),('b','3','inProgress',None,1)])
            db.commit();db.close()
            tasks=read_tasks(root)
            self.assertEqual([(t.id,t.turn,t.state) for t in tasks],[('a','2','running')])

    def test_async_input_survives_tool_delivery_but_clears_on_user_reply(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'session.jsonl'
            records=[{'type':'response_item','payload':{'type':'function_call','name':'functions.request_user_input_async','call_id':'x'}},
                     {'type':'response_item','payload':{'type':'function_call_output','call_id':'x'}},
                     {'type':'event_msg','payload':{'type':'task_complete'}}]
            path.write_text(''.join(json.dumps(r)+'\n' for r in records));self.assertTrue(pending_input(path))
            records.append({'type':'response_item','payload':{'type':'message','role':'user'}})
            path.write_text(''.join(json.dumps(r)+'\n' for r in records));self.assertFalse(pending_input(path))

    def test_shortcut_validation(self):
        self.assertEqual(shortcut_parts('Ctrl+Alt+Q'),(0x4003,ord('Q')))
        for text in ('Q','Shift+Q','Ctrl+F12','Ctrl+Alt+Q, Ctrl+X',''):
            self.assertIsNone(shortcut_parts(text))

    def test_appended_partial_line_is_retried(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'session.jsonl'
            start=json.dumps({'type':'event_msg','payload':{'type':'task_started'}})+'\n'
            request=json.dumps({'type':'response_item','payload':{'type':'function_call','name':'request_user_input','call_id':'x'}})
            path.write_text(start+request[:30]);self.assertFalse(pending_input(path))
            with path.open('a') as stream:stream.write(request[30:]+'\n')
            self.assertTrue(pending_input(path))
            with path.open('a') as stream:stream.write(json.dumps({'type':'response_item','payload':{'type':'function_call_output','call_id':'x'}})+'\n')
            self.assertFalse(pending_input(path))


class DesktopLayout(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.card=QuotaCard(preview=True)
        self.card.settings.update(DEFAULTS)
        self.card.apply_settings(save=False)

    def tearDown(self):self.card.close()

    def test_mini_is_proportional_and_expands(self):
        self.card.settings.update(mini=True,scale=150);self.card.apply_settings(save=False)
        self.assertEqual((self.card.width(),self.card.height()),(216,87))
        self.card.settings['mini']=False;self.card.apply_settings(save=False)
        self.assertEqual((self.card.width(),self.card.height()),(540,417))

    def test_click_through_recovery(self):
        self.card.settings['click_through']=True;self.card.apply_settings(save=False)
        self.assertTrue(self.card.windowFlags() & Qt.WindowTransparentForInput)
        self.card.restore()
        self.assertFalse(self.card.windowFlags() & Qt.WindowTransparentForInput)
        self.card.shortcut_activate()
        self.assertTrue(self.card.windowFlags() & Qt.WindowTransparentForInput)

    def test_footer_regions_never_overlap(self):
        for compact in (True,False):
            self.card.settings['compact']=compact;self.card.apply_settings(save=False)
            self.card.show();self.app.processEvents();self.card.grab()
            rects=self.card.controls
            self.assertLess(rects['reset_cards'].right(),rects['tasks'].left())
            self.assertLess(rects['tasks'].right(),rects['settings'].left())

    def test_idle_and_wake_intervals(self):
        self.card.tasks.update([]);self.card.update_poll_interval()
        self.assertEqual(self.card.poll.interval(),300000)
        self.card.tasks.update([Task('a','1','A','running')]);self.card.update_poll_interval()
        self.assertEqual(self.card.poll.interval(),3000)

    def test_single_completed_opens_directly_even_with_running_task(self):
        tasks=[Task('a','1','A','completed',self.card.tasks.started_at+1),Task('b','2','B','running')]
        self.card.tasks.update(tasks)
        with patch('quota_card.open_task_thread',return_value=True) as opener, patch('quota_card.RoundedMenu') as menu:
            self.card.open_task_menu(None)
            opener.assert_called_once_with('a');menu.assert_not_called()
        self.assertEqual(self.card.tasks.label(),'1 个任务正在运行')

    def test_menu_contains_only_highest_priority_and_clears_only_selected(self):
        tasks=[Task('a','1','A','waiting'),Task('b','2','B','waiting'),Task('c','3','C','running'),Task('d','4','D','completed',self.card.tasks.started_at+1)]
        self.card.tasks.update(tasks)
        with patch('quota_card.RoundedMenu') as factory:
            self.card.open_task_menu(None)
            titles=[call.args[0] for call in factory.return_value.addAction.call_args_list]
            self.assertEqual(titles,['等待确认 · A','等待确认 · B'])
        with patch('quota_card.open_task_thread',return_value=True):self.card.open_task(tasks[0])
        self.assertEqual(self.card.tasks.label(),'有 1 个任务等待确认')
        self.assertEqual(self.card.tasks.current_group()[1],[tasks[1]])
        self.card.tasks.update(tasks)
        self.assertEqual(self.card.tasks.current_group()[1],[tasks[1]])

    def test_failed_navigation_keeps_reminder_and_stale_selection_does_not_open(self):
        task=Task('a','1','A','waiting');self.card.tasks.update([task])
        with patch('quota_card.open_task_thread',return_value=False), patch.object(self.card.tray,'showMessage'):
            self.card.open_task(task)
        self.assertEqual(self.card.tasks.label(),'有 1 个任务等待确认')
        self.card.tasks.update([Task('a','2','A','running')])
        with patch('quota_card.open_task_thread') as opener:
            self.card.open_task(task);opener.assert_not_called()

    def test_running_task_stays_visible_after_navigation(self):
        task=Task('a','1','A','running');self.card.tasks.update([task]);self.card.tasks.acknowledge(task)
        with patch('quota_card.open_task_thread',return_value=True) as opener:
            self.card.open_task(task)
            self.card.open_task(task)
            self.assertEqual(opener.call_count,2)
        self.card.tasks.update([task])
        self.assertEqual(self.card.tasks.label(),'1 个任务正在运行')
        self.assertEqual(self.card.tasks.current_group()[1],[task])
        self.assertEqual(self.card.tasks.poll_seconds(3,300),3)

    def test_navigation_uses_exact_thread_uri_and_rejects_invalid_id(self):
        tid='019fd782-23e8-7cd1-99a0-82516a1c6e03'
        with patch('desktop_controls.sys.platform','win32'),patch('os.startfile',create=True) as start:
            self.assertTrue(open_task_thread(tid));start.assert_called_once_with('codex://threads/'+tid)
            self.assertFalse(open_task_thread('../settings'));self.assertEqual(start.call_count,1)

    def test_task_colors_are_fixed_and_ignore_custom_accent(self):
        self.card.settings['accent']='#ff00ff'
        for state,color in [('running','#ffffff'),('waiting','#f5a34d'),('completed','#7dcc9e')]:
            self.card.tasks=TaskTracker(started_at=0)
            self.card.tasks.update([Task('a','1','A',state,1)])
            self.assertEqual(self.card.task_text_color('#999999').name(),color)

    def test_title_click_region_is_codex_only(self):
        from PyQt5.QtCore import QPoint
        self.card.show();self.app.processEvents();self.card.grab()
        area=self.card.controls['codex']
        self.assertEqual(self.card.control_at(QPoint(round(area.center().x()),30)),'codex')
        self.assertNotEqual(self.card.control_at(QPoint(round(area.right()+15),30)),'codex')
        with patch('quota_card.open_codex_app',return_value=True) as opener:
            self.card.open_codex();opener.assert_called_once()

    def test_codex_launch_uses_registered_app_not_new_chat(self):
        from types import SimpleNamespace
        with patch('desktop_controls.sys.platform','win32'),patch('subprocess.run',return_value=SimpleNamespace(returncode=0,stdout='OpenAI.Codex_example!App\n')),patch('os.startfile',create=True) as start:
            self.assertTrue(open_codex_app())
            start.assert_called_once_with('shell:AppsFolder\\OpenAI.Codex_example!App')

    def test_invalid_app_registration_is_not_executed(self):
        from types import SimpleNamespace
        with patch('desktop_controls.sys.platform','win32'),patch('subprocess.run',return_value=SimpleNamespace(returncode=0,stdout='unexpected.exe')),patch('os.startfile',create=True) as start:
            self.assertFalse(open_codex_app());start.assert_not_called()


if __name__=='__main__':unittest.main()
