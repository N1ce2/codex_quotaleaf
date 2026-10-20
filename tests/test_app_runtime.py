import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app_runtime import runtime_paths,startup_target


class RuntimePaths(unittest.TestCase):
    def test_source_keeps_existing_local_settings(self):
        with patch.object(sys,'frozen',False,create=True):
            base,state=runtime_paths()
            self.assertEqual(state,base/'state')
            target,args=startup_target()
            self.assertEqual(target.name,'pythonw.exe')
            self.assertIn('quota_card.py',args)

    def test_frozen_uses_user_data_and_launches_executable(self):
        executable=str(Path('C:/portable folder/codex_quotaleaf.exe'))
        with patch.object(sys,'frozen',True,create=True),patch.object(sys,'executable',executable),patch.dict(os.environ,{'LOCALAPPDATA':'C:/test-local'}):
            base,state=runtime_paths()
            self.assertEqual(base,Path(executable).parent)
            self.assertEqual(state,Path('C:/test-local/codex_quotaleaf/state'))
            self.assertEqual(startup_target(),(Path(executable),''))
