"""Windows process ownership, global shortcuts and focus-aware clipboard input."""
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import subprocess
import threading
import time

import win32api
import win32con
import win32event
import win32job
import win32process
from PySide6.QtCore import QAbstractNativeEventFilter

user32 = ctypes.WinDLL('user32', use_last_error=True)
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetClipboardSequenceNumber.restype = wintypes.DWORD
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]


def restore_own_window(handle):
    # A SW_HIDE launch can override Qt's very first ShowWindow call. Explicitly
    # restore this app's own HWND after Qt updates its visibility/state.
    user32.ShowWindow(handle, win32con.SW_RESTORE)


class GUIThreadInfo(ctypes.Structure):
    _fields_ = [('cbSize', wintypes.DWORD), ('flags', wintypes.DWORD),
                ('hwndActive', wintypes.HWND), ('hwndFocus', wintypes.HWND),
                ('hwndCapture', wintypes.HWND), ('hwndMenuOwner', wintypes.HWND),
                ('hwndMoveSize', wintypes.HWND), ('hwndCaret', wintypes.HWND),
                ('rcCaret', wintypes.RECT)]


def target_snapshot():
    hwnd = int(user32.GetForegroundWindow() or 0)
    pid = wintypes.DWORD()
    tid = user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    info = GUIThreadInfo()
    info.cbSize = ctypes.sizeof(info)
    user32.GetGUIThreadInfo(tid, ctypes.byref(info))
    result = {'window': hwnd, 'focus': int(info.hwndFocus or 0), 'pid': pid.value,
              'runtime': None, 'password': False}
    # Browser controls share an HWND. UIA's runtime ID distinguishes text fields.
    try:
        import comtypes.client
        from comtypes.gen import UIAutomationClient as UIA
        automation = comtypes.client.CreateObject(
            '{ff48dba4-60ef-4201-aa87-54103eef594e}', interface=UIA.IUIAutomation)
        focused = automation.GetFocusedElement()
        if focused:
            result['runtime'] = tuple(focused.GetRuntimeId())
            result['password'] = bool(focused.CurrentIsPassword)
    except Exception:
        # Native Edit password controls can be recognised even without UIA.
        if result['focus']:
            cls = ctypes.create_unicode_buffer(128)
            user32.GetClassNameW(result['focus'], cls, 128)
            if cls.value.lower() == 'edit':
                result['password'] = bool(user32.GetWindowLongW(result['focus'], -16) & 0x20)
    return result


def same_target(before, after):
    if not before or not before['window'] or before['pid'] == os.getpid():
        return False
    if any(before.get(key) != after.get(key) for key in ['window', 'focus', 'pid']):
        return False
    if before.get('runtime') is not None:
        return before['runtime'] == after.get('runtime')
    return True


def modifiers_down():
    return any(user32.GetAsyncKeyState(key) & 0x8000 for key in (0x10, 0x11, 0x12))


def shortcut(letter):
    # Wait for all shortcut modifiers before sending a copy/paste chord.
    if modifiers_down():
        return False
    # Native input is used by the app the user requested, never to submit a form.
    win32api.keybd_event(win32con.VK_CONTROL, 0, 0, 0)
    win32api.keybd_event(ord(letter.upper()), 0, 0, 0)
    win32api.keybd_event(ord(letter.upper()), 0, win32con.KEYEVENTF_KEYUP, 0)
    win32api.keybd_event(win32con.VK_CONTROL, 0, win32con.KEYEVENTF_KEYUP, 0)
    return True


class Hotkeys(QAbstractNativeEventFilter):
    def __init__(self, callback):
        super().__init__()
        self.callback = callback
        self.registered = {}

    @staticmethod
    def parse(sequence):
        tokens = sequence.upper().replace(' ', '').split('+')
        mods = 0x4000
        for token in tokens[:-1]:
            if token not in ['CTRL', 'SHIFT', 'ALT']: raise ValueError('Use Ctrl, Shift or Alt with a letter, Space or function key.')
            mods |= {'CTRL':2,'SHIFT':4,'ALT':1}[token]
        key = tokens[-1]
        vk = 0x20 if key == 'SPACE' else (0x70+int(key[1:])-1 if key.startswith('F') and key[1:].isdigit() and 1<=int(key[1:])<=24 else ord(key) if len(key)==1 and key.isascii() and key.isalnum() else 0)
        if mods == 0x4000 or not vk: raise ValueError('Choose a shortcut with at least one modifier key.')
        if tokens == ['ALT','P']: raise ValueError('Alt+P belongs to SteelSeries Moments. Choose another shortcut.')
        return mods,vk

    def register(self, dictation='Ctrl+Shift+Space', reading='Alt+O'):
        errors = []
        for identity, name in [(0x701,dictation),(0x702,reading)]:
            mods,key = self.parse(name)
            if user32.RegisterHotKey(None, identity, mods, key):
                self.registered[identity] = name
            else:
                errors.append(name)
        return errors

    def escape(self, enabled):
        if enabled and 0x703 not in self.registered:
            if user32.RegisterHotKey(None, 0x703, 0x4000, 0x1B):
                self.registered[0x703] = 'Esc'
        elif not enabled and 0x703 in self.registered:
            user32.UnregisterHotKey(None, 0x703)
            del self.registered[0x703]

    def close(self):
        for identity in list(self.registered):
            user32.UnregisterHotKey(None, identity)
        self.registered.clear()

    def nativeEventFilter(self, event_type, message):
        if event_type in [b'windows_generic_MSG', b'windows_dispatcher_MSG']:
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == 0x0312 and msg.wParam in self.registered:
                self.callback(int(msg.wParam))
                return True, 0
        return False, 0


class ManagedProcess:
    def __init__(self, handle, pid):
        self.handle, self.pid = handle, pid

    def poll(self):
        value = win32process.GetExitCodeProcess(self.handle)
        return None if value == win32con.STILL_ACTIVE else value

    def terminate(self):
        if self.poll() is None:
            # The Python/packaged-engine launcher may have a child process.
            # Stop its owned descendants as well when idling or cancelling.
            import psutil
            try:
                descendants = psutil.Process(self.pid).children(recursive=True)
                for descendant in reversed(descendants):
                    try:
                        descendant.kill()
                    except psutil.Error:
                        pass
            except psutil.Error:
                pass
            win32api.TerminateProcess(self.handle, 1)


class ProcessOwner:
    """A Windows Job kills the whole child tree even after an unexpected UI exit."""
    def __init__(self):
        self.job = win32job.CreateJobObject(None, '')
        limits = win32job.QueryInformationJobObject(self.job, win32job.JobObjectExtendedLimitInformation)
        limits['BasicLimitInformation']['LimitFlags'] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        win32job.SetInformationJobObject(self.job, win32job.JobObjectExtendedLimitInformation, limits)
        self.children = []
        self.lock = threading.RLock()
        self.closed = False

    def spawn(self, command, cwd=None, env=None):
        with self.lock:
            if self.closed:
                raise RuntimeError('Voxlet is closed.')
            startup = win32process.STARTUPINFO()
            startup.dwFlags = win32con.STARTF_USESHOWWINDOW
            startup.wShowWindow = win32con.SW_HIDE
            proc, thread, pid, _ = win32process.CreateProcess(
                None, subprocess.list2cmdline([str(c) for c in command]), None, None, False,
                win32con.CREATE_NO_WINDOW | win32con.CREATE_SUSPENDED,
                env, str(cwd) if cwd else None, startup)
            try:
                win32job.AssignProcessToJobObject(self.job, proc)
                win32process.ResumeThread(thread)
                child = ManagedProcess(proc, pid)
                self.children.append(child)
                return child
            except Exception:
                win32api.TerminateProcess(proc, 1)
                win32api.CloseHandle(proc)
                raise
            finally:
                win32api.CloseHandle(thread)

    def close(self):
        with self.lock:
            if self.closed:
                return
            self.closed = True
            win32job.TerminateJobObject(self.job, 0)
            for child in self.children:
                try:
                    win32api.CloseHandle(child.handle)
                except Exception:
                    pass
            self.children.clear()
            win32api.CloseHandle(self.job)


def split_text(text, limit=350):
    import re
    sentences = re.split(r'(?<=[.!?])\s+|\n+', text.strip())
    chunks = []
    current = ''
    for sentence in sentences:
        while len(sentence) > limit:
            cut = sentence.rfind(' ', 0, limit)
            cut = cut if cut > 0 else limit
            if current:
                chunks.append(current)
                current = ''
            chunks.append(sentence[:cut].strip())
            sentence = sentence[cut:].strip()
        if len(current) + len(sentence) + 1 > limit and current:
            chunks.append(current)
            current = ''
        current = (current + ' ' + sentence).strip()
    if current:
        chunks.append(current)
    return chunks
