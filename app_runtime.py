"""Source and packaged runtime paths. Never write settings into a one-file extraction."""
import os
from pathlib import Path
import sys

VERSION = '0.1.1'


def runtime_paths():
    source = Path(__file__).resolve().parent
    if getattr(sys, 'frozen', False):
        app = Path(sys.executable).resolve().parent
        local = Path(os.environ.get('LOCALAPPDATA') or Path.home()/'AppData/Local')
        return app, local/'codex_quotaleaf'/'state'
    return source, source/'state'


def startup_target():
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve(), ''
    return Path(sys.executable).with_name('pythonw.exe'), '"'+str(Path(__file__).resolve().parent/'quota_card.py')+'"'
