#!/usr/bin/env python3
"""Paired RDP draft bridge: fill or synchronize a Claude draft, never submit."""
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
SYNC_PREFIX = 'HC-DRAFT/2 '
MAX_TEXT = 4000
TTL = 15
PROMPT_HINTS = ('reply to claude', 'write your prompt', 'message claude',
                'how can i help', '回复 claude', '向 claude', '发送消息', '你想聊', '给 claude',
                '发送给 claude 的提示词')


def packet(key, payload, prefix=PREFIX):
    body = base64.b64encode(json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode()).decode()
    mac = hmac.new(key, body.encode('ascii'), hashlib.sha256).hexdigest()
    return prefix + base64.b64encode(json.dumps({'body': body, 'mac': mac}, separators=(',', ':')).encode()).decode()


def unpack(key, raw, now=None):
    prefix = SYNC_PREFIX if raw.startswith(SYNC_PREFIX) else PREFIX
    if not raw.startswith(prefix) or len(raw) > 65536:
        raise ValueError('Not a bridge packet')
    outer = json.loads(base64.b64decode(raw[len(prefix):], validate=True))
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
    sync = prefix == SYNC_PREFIX
    if value.get('kind') != ('sync' if sync else 'draft'):
        raise ValueError('Not a draft')
    if sync and (not re.fullmatch('[a-f0-9]{32}', value.get('stream', ''))
                 or not isinstance(value.get('seq'), int) or isinstance(value['seq'], bool)
                 or not 1 <= value['seq'] <= 2147483647
                 or not re.fullmatch('[a-f0-9]{64}', value.get('base', ''))):
        raise ValueError('Invalid sync revision')
    text = value.get('text')
    if (not isinstance(text, str) or (not sync and not text.strip()) or len(text) > MAX_TEXT
            or any((ord(c) < 32 and c not in '\r\n\t') or 0xD800 <= ord(c) <= 0xDFFF for c in text)):
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


def prompt_usable(item, Atspi):
    state = item.get_state_set()
    attrs = item.get_attributes() or []
    if isinstance(attrs, dict):
        attrs = list(attrs.values())
    hint = (item.get_name() or '') + ' ' + ' '.join(attrs)
    return (any(s in hint.lower() for s in PROMPT_HINTS)
            and all(state.contains(s) for s in (Atspi.StateType.EDITABLE,
                    Atspi.StateType.ENABLED, Atspi.StateType.VISIBLE, Atspi.StateType.SHOWING)))


def valid_atspi_prompt(item):
    from gi.repository import Atspi
    return claude_pid(item.get_application().get_process_id()) and prompt_usable(item, Atspi)


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
            # Only a positively identified prompt, never an arbitrary focused field.
            if prompt_usable(item, Atspi):
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


def editor_focused(node):
    from gi.repository import Atspi
    return node.get_state_set().contains(Atspi.StateType.FOCUSED)


def paste_prompt(node, text, runner=desktop_command, read=accessible_text,
                 clip_read=clipboard_read, clip_write=clipboard_write,
                 expected='', focused=editor_focused):
    window = runner(['xdotool', 'getwindowfocus'])
    if not window.isdigit():
        return False
    if runner(['xdotool', 'getwindowpid', window]) != str(node.get_application().get_process_id()):
        return False
    clip_write(text)
    # Recheck the exact window and empty editor after acquiring clipboard text.
    def unchanged():
        return (focused(node) and read(node) == expected
                and runner(['xdotool', 'getwindowfocus']) == window)
    if clip_read() != text or not unchanged():
        return False
    # Chromium ignores XSendEvent (--window) paste events. Use XTEST only after
    # checking its actual keyboard focus. Never send Return or submit.
    if expected:
        runner(['xdotool', 'key', '--clearmodifiers', 'ctrl+a'])
        if clip_read() != text or not unchanged():
            return False
    runner(['xdotool', 'key', '--clearmodifiers', 'ctrl+v' if text else 'BackSpace'])
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
    def write(node, text, expected=''):
        return paste_prompt(node, text, expected=expected)
    return find, focus, read, write


def text_hash(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def prompt_context(node):
    """Bind to the document/frame as well as the editable node; never return text."""
    context = [node.get_application().get_process_id()]
    for _ in range(40):
        if node is None:
            break
        role = node.get_role_name()
        if role in ('frame', 'document web', 'document frame'):
            context.append((role, node.get_name()))
            try:
                context.append(node.get_document_iface().get_attribute_value('DocURL'))
            except Exception:
                pass
        if role == 'frame':
            break
        node = node.get_parent()
    return tuple(context)


class DraftSync:
    """An in-memory lease for one editor, with compare-before-replace revisions."""
    def __init__(self, find, focus, read, write, context=prompt_context, validate=None):
        self.find, self.focus, self.read, self.write, self.context = find, focus, read, write, context
        self.validate = validate
        self.owner = None

    def apply(self, message):
        stream, seq, base, text = (message[k] for k in ('stream', 'seq', 'base', 'text'))
        try:
            # Once attached, validate the exact retained editor instead of walking
            # the entire app on every keystroke burst. Never choose a new target.
            owned = self.owner and self.owner['stream'] == stream
            if owned and self.owner['blocked']:
                return 'conflict'
            if owned and self.validate:
                nodes = [self.owner['target']] if self.validate(self.owner['target']) else []
            else:
                nodes = self.find()
            if len(nodes) != 1:
                return 'not_ready'
            target = nodes[0]
            current, context = self.read(target), self.context(target)
            if self.owner and self.owner['stream'] == stream:
                owner = self.owner
                if (owner['blocked'] or seq <= owner['seq'] or target != owner['target']
                        or context != owner['context'] or base != owner['hash']
                        or text_hash(current) != owner['hash']):
                    owner['blocked'] = True
                    return 'conflict'
            elif current != '' or base != text_hash(''):
                return 'conflict'
            else:
                self.owner = {'stream': stream, 'seq': 0, 'target': target, 'context': context,
                              'hash': text_hash(''), 'blocked': False}
            # Consume the revision before focus/input; unknown outcomes poison the lease.
            owner = self.owner
            owner['seq'], owner['blocked'] = seq, True
            if not self.focus(target):
                return 'not_ready'
            fresh = ([target] if self.validate(target) else []) if self.validate else self.find()
            if (len(fresh) != 1 or fresh[0] != target or self.context(target) != context
                    or self.read(target) != current):
                return 'conflict'
        except Exception:
            if self.owner:
                self.owner['blocked'] = True
            return 'not_ready'
        try:
            if current != text and not self.write(target, text, current):
                return 'uncertain'
            if self.read(target) != text or self.context(target) != context:
                return 'uncertain'
            owner['hash'], owner['blocked'] = text_hash(text), False
            return 'synced'
        except Exception:
            return 'uncertain'


class Receiver:
    def __init__(self, key, state, fill, sync=None):
        self.key, self.state, self.fill = key, Path(state), fill
        self.sync = sync
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
            status = (self.sync.apply(message) if self.sync else 'not_ready') if message['kind'] == 'sync' else self.fill(message['text'])
        except Exception:
            status = 'uncertain'
        sync = message['kind'] == 'sync'
        return packet(self.key, {'kind': 'sync_result' if sync else 'result', 'id': identifier,
                                'at': int(time.time()), 'status': status}, SYNC_PREFIX if sync else PREFIX)


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
        receiver = Receiver(load_key(args.config), directory / 'seen.json',
                            lambda text: fill_prompt(text, *tools),
                            DraftSync(*tools, validate=valid_atspi_prompt))
        while True:
            try:
                raw = clipboard_read()
                if raw.startswith((PREFIX, SYNC_PREFIX)):
                    result = receiver.receive(raw)
                    # Do not overwrite clipboard changes made while working.
                    if result and clipboard_read() in (raw, receiver.last_text):
                        clipboard_write(result)
            except (ValueError, KeyError, TypeError, UnicodeError, OSError, subprocess.SubprocessError):
                pass  # Other clipboard content is not a request. Never log message text.
            time.sleep(0.15)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, ImportError) as error:
        print('Claude input bridge: ' + str(error), file=sys.stderr)
        sys.exit(1)
