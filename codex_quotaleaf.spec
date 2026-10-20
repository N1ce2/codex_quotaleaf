# Build in an isolated Windows environment: python -m PyInstaller codex_quotaleaf.spec
from pathlib import Path

root = Path(SPECPATH)
a = Analysis(
    [str(root/'quota_card.py')], pathex=[str(root)],
    binaries=[], datas=[(str(root/'quota-card.ico'),'.')],
    hiddenimports=[], hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=['PyQt5.QtWebEngine','PyQt5.QtWebEngineCore','PyQt5.QtWebEngineWidgets',
              'PyQt5.QtQml','PyQt5.QtQuick','tkinter','matplotlib','numpy','pandas'],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,a.scripts,a.binaries,a.datas,[],
    name='codex_quotaleaf', debug=False, bootloader_ignore_signals=False,
    strip=False,upx=False,console=False,disable_windowed_traceback=False,
    icon=str(root/'quota-card.ico'),
)
