"""Windows-only global shortcut. No keyboard hook or keystroke logging."""
import ctypes
import sys
from uuid import UUID
from PyQt5.QtCore import QAbstractNativeEventFilter


def open_task_thread(thread_id):
    """Dispatch only a validated, official local-thread deep link to Windows."""
    import os
    try:
        identifier=str(UUID(thread_id))
        if sys.platform != 'win32':return False
        os.startfile(f'codex://threads/{identifier}')
        return True
    except (ValueError,TypeError,AttributeError,OSError):
        return False


def shortcut_parts(text):
    parts = text.upper().replace(' ', '').split('+')
    if len(parts) < 2 or len(parts[-1]) != 1 or not parts[-1].isalnum() or not parts[-1].isascii():
        return None
    masks = {'CTRL':2, 'ALT':1, 'SHIFT':4, 'META':8, 'WIN':8}
    if any(p not in masks for p in parts[:-1]):return None
    mod = 0x4000  # MOD_NOREPEAT
    for part in parts[:-1]:mod |= masks[part]
    if not mod & (1|2|8):return None
    return mod, ord(parts[-1])


class GlobalShortcut(QAbstractNativeEventFilter):
    ID = 0x514C

    def __init__(self, app, callback):
        super().__init__()
        self.app, self.callback = app, callback
        self.registered = False
        self.text = ''
        app.installNativeEventFilter(self)

    def register(self, text):
        if sys.platform != 'win32':return False
        parts = shortcut_parts(text)
        if not parts:return False
        old = self.text
        self.unregister()
        if ctypes.windll.user32.RegisterHotKey(None, self.ID, *parts):
            self.registered = True;self.text = text
            return True
        if old:
            previous = shortcut_parts(old)
            self.registered = bool(ctypes.windll.user32.RegisterHotKey(None, self.ID, *previous))
            self.text = old if self.registered else ''
        return False

    def unregister(self):
        if self.registered:
            ctypes.windll.user32.UnregisterHotKey(None, self.ID)
        self.registered = False

    def close(self):
        self.unregister()
        self.app.removeNativeEventFilter(self)

    def nativeEventFilter(self, event_type, message):
        if sys.platform == 'win32':
            from ctypes.wintypes import MSG
            msg = MSG.from_address(int(message))
            if msg.message == 0x0312 and msg.wParam == self.ID:
                self.callback()
                return True, 0
        return False, 0
