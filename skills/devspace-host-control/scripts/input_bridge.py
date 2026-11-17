#!/usr/bin/env python3
"""Opt-in RDP text clipboard bridge: fill an empty Claude prompt, never submit."""
import argparse
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

PREFIX = 'HC-DRAFT/1 '
MAX_TEXT = 4000
TTL = 15
PROMPT_HINTS = ('reply to claude', 'write your prompt', 'message claude',
                'how can i help', '回复 claude', '向 claude', '发送消息', '你想聊', '给 claude',
                '发送给 claude 的提示词')


def packet(key, payload):
    body = base64.b64encode(json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode()).decode()
    mac = hmac.new(key, body.encode('ascii'), hashlib.sha256).hexdigest()
    return PREFIX + base64.b64encode(json.dumps({'body': body, 'mac': mac}, separators=(',', ':')).encode()).decode()


def unpack(key, raw, now=None):
    if not raw.startswith(PREFIX) or len(raw) > 65536:
        raise ValueError('Not a bridge packet')
    outer = json.loads(base64.b64decode(raw[len(PREFIX):], validate=True))
    body, mac = outer['body'], outer['mac']
    if not isinstance(body, str) or not isinstance(mac, str):
        raise ValueError('Invalid envelope')
    expected = hmac.new(key, body.encode('ascii'), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(mac, expected):
        raise ValueError('Wrong pairing')
    value = json.loads(base64.b64decode(body, validate=True))
    at = value.get('at')
    if (not isinstance(at, int) or isinstance(at, bool)
            or not -2 <= (time.time() if now is None else now) - at <= TTL):
        raise ValueError('Expired packet; do not retry automatically')
    if not re.fullmatch('[a-f0-9]{32}', value.get('id', '')):
        raise ValueError('Invalid message ID')
    if value.get('kind') != 'draft':
        raise ValueError('Not a draft')
    text = value.get('text')
    if (not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT
            or any(ord(c) < 32 and c not in '\r\n\t' for c in text)):
        raise ValueError('Invalid text')
    return value


def load_key(path):
    p = Path(path)
    if p.is_symlink() or p.stat().st_uid != os.getuid() or p.stat().st_mode & 0o077:
        raise ValueError('Bridge configuration must be private and owned by the desktop user')
    key = bytes.fromhex(json.loads(p.read_text(encoding='utf-8'))['key'])
    if len(key) != 32:
        raise ValueError('Invalid bridge key')
    return key


def clipboard_read():
    result = subprocess.run(['xclip', '-selection', 'clipboard', '-out', '-target', 'UTF8_STRING'],
                            capture_output=True, timeout=2)
    return result.stdout.decode('utf-8') if result.returncode == 0 and len(result.stdout) <= 65536 else ''


def clipboard_write(value):
    subprocess.run(['xclip', '-selection', 'clipboard', '-in', '-target', 'UTF8_STRING'],
                   input=value.encode(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   timeout=2, check=True)


def claude_pid(pid, proc=Path('/proc')):
    try:
        folder = proc / str(pid)
        return folder.stat().st_uid == os.getuid() and (folder / 'comm').read_text().strip() == 'claude-desktop'
    except OSError:
        return False


def candidates(desktop, Atspi, verify=claude_pid):
    found = []
    visited = 0
    deadline = time.monotonic() + 4
    for index in range(desktop.get_child_count()):
        app = desktop.get_child_at_index(index)
        if not verify(app.get_process_id()):
            continue
        stack = [app]
        while stack:
            item = stack.pop()
            visited += 1
            if visited > 5000 or time.monotonic() > deadline:
                raise ValueError('Claude accessibility tree is too large; select the message input')
            state = item.get_state_set()
            attrs = item.get_attributes() or []
            if isinstance(attrs, dict):
                attrs = list(attrs.values())
            hint = (item.get_name() or '') + ' ' + ' '.join(attrs)
            prompt = any(s in hint.lower() for s in PROMPT_HINTS)
            usable = all(state.contains(s) for s in (Atspi.StateType.EDITABLE,
                         Atspi.StateType.ENABLED, Atspi.StateType.VISIBLE, Atspi.StateType.SHOWING))
            # Only a positively identified prompt, never an arbitrary focused field.
            if usable and prompt:
                found.append(item)
                continue
            for i in range(min(item.get_child_count(), 5000)):
                child = item.get_child_at_index(i)
                if child is not None:
                    stack.append(child)
    return found


def fill_prompt(text, find, focus, read, write):
    try:
        nodes = find()
        if len(nodes) != 1:
            return 'not_ready'
        target = nodes[0]
        if read(target).strip():
            return 'has_draft'
        if not focus(target):
            return 'not_ready'
        # Refocus/navigation may change the target. Re-observe before mutation.
        fresh = find()
        if len(fresh) != 1 or fresh[0] != target or read(target).strip():
            return 'not_ready'
    except Exception:
        # No paste has occurred. A closed/unresponsive app is not an unknown
        # input outcome; the panel can accurately ask the user to open Claude.
        return 'not_ready'
    try:
        if not write(target, text):
            return 'uncertain'
        return 'filled' if read(target) == text else 'uncertain'
    except Exception:
        return 'uncertain'


def accessible_text(node, depth=0):
    if depth > 12:
        raise ValueError('Unrecognized prompt text structure')
    raw = node.get_text_iface().get_text(0, -1)
    if '\ufffc' not in raw:
        return raw
    children = [node.get_child_at_index(i) for i in range(node.get_child_count())]
    if len(children) > 256 or raw.count('\ufffc') != len(children):
        raise ValueError('Unrecognized embedded prompt text')
    chunks = [accessible_text(child, depth + 1) for child in children]
    # Chromium represents ProseMirror paragraphs as embedded objects. An empty
    # paragraph exposes its trailing <br> as a newline, not an existing draft.
    if raw == '\ufffc' * len(children) and all(c.get_role_name() == 'paragraph' for c in children):
        return '\n'.join('' if chunk == '\n' else chunk for chunk in chunks)
    iterator = iter(chunks)
    return ''.join(next(iterator) if c == '\ufffc' else c for c in raw)


def desktop_command(command):
    return subprocess.run(command, capture_output=True, text=True, timeout=3, check=True).stdout.strip()


def paste_prompt(node, text, runner=desktop_command, read=accessible_text,
                 clip_read=clipboard_read, clip_write=clipboard_write):
    window = runner(['xdotool', 'getwindowfocus'])
    if not window.isdigit():
        return False
    if runner(['xdotool', 'getwindowpid', window]) != str(node.get_application().get_process_id()):
        return False
    clip_write(text)
    # Recheck the exact window and empty editor after acquiring clipboard text.
    if (clip_read() != text or read(node).strip()
            or runner(['xdotool', 'getwindowfocus']) != window):
        return False
    # Chromium ignores XSendEvent (--window) paste events. Use XTEST only after
    # checking its actual keyboard focus. Never send Return or submit.
    runner(['xdotool', 'key', '--clearmodifiers', 'ctrl+v'])
    for _ in range(10):
        if read(node) == text:
            return True
        time.sleep(0.15)
    return False  # Unknown result is never replayed.


def atspi_tools():
    import gi
    gi.require_version('Atspi', '2.0')
    from gi.repository import Atspi
    # Bound D-Bus calls instead of hanging on a dead application.
    Atspi.set_timeout(1500, 3000)
    def find():
        return candidates(Atspi.get_desktop(0), Atspi)
    def read(node):
        return accessible_text(node)
    def focus(node):
        pid = str(node.get_application().get_process_id())
        ancestor = node
        title = None
        for _ in range(40):
            if ancestor is None:
                break
            if ancestor.get_role_name() == 'frame':
                title = ancestor.get_name()
                break
            ancestor = ancestor.get_parent()
        if not title:
            return False
        # Electron also owns tray/IPC windows; match the accessibility frame's
        # exact title and PID instead of choosing a visible icon by class.
        windows = desktop_command(['xdotool', 'search', '--all', '--pid', pid,
                                   '--name', '^' + re.escape(title) + '$']).splitlines()
        if len(windows) != 1 or not windows[0].isdigit():
            return False
        if desktop_command(['xdotool', 'getwindowpid', windows[0]]) != pid:
            return False
        # A normally closed Claude window can remain hidden in the tray. Map
        # only the exact, PID-verified document frame before focusing its editor.
        desktop_command(['xdotool', 'windowmap', '--sync', windows[0]])
        desktop_command(['xdotool', 'windowraise', windows[0]])
        desktop_command(['xdotool', 'windowfocus', '--sync', windows[0]])
        return node.get_component_iface().grab_focus()
    def write(node, text):
        return paste_prompt(node, text)
    return find, focus, read, write


class Receiver:
    def __init__(self, key, state, fill):
        self.key, self.state, self.fill = key, Path(state), fill
        self.seen = json.loads(self.state.read_text()) if self.state.exists() else []
        if not isinstance(self.seen, list):
            raise ValueError('Invalid bridge journal')

    def receive(self, raw, now=None):
        message = unpack(self.key, raw, now)
        identifier = message['id']
        if identifier in self.seen:
            return None  # Includes attempts with an unknown outcome; never replay.
        self.seen = (self.seen + [identifier])[-4096:]
        self.last_text = message['text']
        temporary = self.state.with_suffix('.tmp')
        with temporary.open('w', encoding='utf-8') as stream:
            os.chmod(temporary, 0o600)
            json.dump(self.seen, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.state)  # Persist consumption before any UI input.
        try:
            status = self.fill(message['text'])
        except Exception:
            status = 'uncertain'
        return packet(self.key, {'kind': 'result', 'id': identifier, 'at': int(time.time()), 'status': status})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('check', 'receive'))
    parser.add_argument('--config', default=str(Path.home() / '.config/hostcontrol/input-bridge.json'))
    args = parser.parse_args()
    if os.getuid() == 0:
        raise ValueError('Run only as the intended desktop user')
    tools = atspi_tools()
    if args.mode == 'check':
        print(json.dumps({'identified_prompt_count': len(tools[0]()), 'will_submit': False}))
        return
    import fcntl
    directory = Path.home() / '.local/state/hostcontrol-input'
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if directory.is_symlink() or directory.stat().st_uid != os.getuid() or directory.stat().st_mode & 0o077:
        raise ValueError('Bridge state directory must be private')
    with (directory / 'receiver.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        receiver = Receiver(load_key(args.config), directory / 'seen.json', lambda text: fill_prompt(text, *tools))
        while True:
            try:
                raw = clipboard_read()
                if raw.startswith(PREFIX):
                    result = receiver.receive(raw)
                    # Do not overwrite clipboard changes made while working.
                    if result and clipboard_read() in (raw, receiver.last_text):
                        clipboard_write(result)
            except (ValueError, KeyError, TypeError, UnicodeError, OSError, subprocess.SubprocessError):
                pass  # Other clipboard content is not a request. Never log message text.
            time.sleep(0.6)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, ImportError) as error:
        print('Claude input bridge: ' + str(error), file=sys.stderr)
        sys.exit(1)
