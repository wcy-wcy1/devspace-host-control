#!/usr/bin/env python3
"""Paired RDP chat bridge: drafts, explicit submission and scoped reply snapshots."""
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
import http.client
import ipaddress
import ssl
from urllib.parse import urlsplit

PREFIX = 'HC-DRAFT/1 '
SYNC_PREFIX = 'HC-DRAFT/2 '
CHAT_PREFIX = 'HC-DRAFT/3 '
MAX_TEXT = 4000
MAX_REPLY = 24000
MAX_PACKET = 262144
TTL = 15
PROMPT_HINTS = ('reply to claude', 'write your prompt', 'message claude',
                'how can i help', '回复 claude', '向 claude', '发送消息', '你想聊', '给 claude',
                '发送给 claude 的提示词')


def packet(key, payload, prefix=PREFIX):
    body = base64.b64encode(json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode()).decode()
    mac = hmac.new(key, body.encode('ascii'), hashlib.sha256).hexdigest()
    return prefix + base64.b64encode(json.dumps({'body': body, 'mac': mac}, separators=(',', ':')).encode()).decode()


def unpack(key, raw, now=None):
    prefix = next((p for p in (CHAT_PREFIX, SYNC_PREFIX, PREFIX) if raw.startswith(p)), None)
    if prefix is None or len(raw) > MAX_PACKET:
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
    sync = prefix in (SYNC_PREFIX, CHAT_PREFIX)
    kind = value.get('kind')
    if prefix == CHAT_PREFIX and kind in ('poll', 'recover'):
        if not re.fullmatch('[a-f0-9]{32}', value.get('stream', '')):
            raise ValueError('Invalid reply watch')
        if kind == 'poll':
            if not re.fullmatch('[a-f0-9]{32}', value.get('turn', '')):
                raise ValueError('Invalid reply watch')
            return value
    elif kind != ('submit' if prefix == CHAT_PREFIX else 'sync' if sync else 'draft'):
        raise ValueError('Not a draft')
    if sync and kind != 'recover' and (not re.fullmatch('[a-f0-9]{32}', value.get('stream', ''))
                 or not isinstance(value.get('seq'), int) or isinstance(value['seq'], bool)
                 or not 1 <= value['seq'] <= 2147483647
                 or not re.fullmatch('[a-f0-9]{64}', value.get('base', ''))):
        raise ValueError('Invalid sync revision')
    text = value.get('text')
    if (not isinstance(text, str) or (not sync and not text.strip()) or len(text) > MAX_TEXT
            or any((ord(c) < 32 and c not in '\r\n\t') or 0xD800 <= ord(c) <= 0xDFFF for c in text)):
        raise ValueError('Invalid text')
    if kind in ('submit', 'recover') and not text.strip():
        raise ValueError('Cannot submit an empty draft')
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
    return result.stdout.decode('utf-8') if result.returncode == 0 and len(result.stdout) <= MAX_PACKET else ''


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
    refresh_accessibility()
    item.clear_cache()
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


def submit_prompt(node, text, runner=desktop_command, read=accessible_text,
                  focused=editor_focused):
    """An explicit submit, guarded independently from ordinary draft updates."""
    window = runner(['xdotool', 'getwindowfocus'])
    if (not window.isdigit() or not text.strip() or not focused(node)
            or runner(['xdotool', 'getwindowpid', window]) != str(node.get_application().get_process_id())
            or read(node) != text):
        return False
    runner(['xdotool', 'key', '--clearmodifiers', 'Return'])
    return True  # Dispatched once; confirmation is handled separately.


def primary_write(text):
    subprocess.run(['xclip', '-selection', 'primary', '-in', '-target', 'UTF8_STRING'],
                   input=text.encode('utf-8'), stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL, timeout=2, check=True)


def prompt_point(node):
    from gi.repository import Atspi
    rect = node.get_component_iface().get_extents(Atspi.CoordType.SCREEN)
    if rect.width < 40 or rect.height < 16:
        raise ValueError('Prompt geometry is unavailable')
    return rect.x + 20, rect.y + min(20, rect.height // 2)


def paste_primary_prompt(node, text, expected='', runner=desktop_command,
                         read=accessible_text, focused=editor_focused,
                         write_selection=primary_write, point=prompt_point):
    """Linux PRIMARY paste avoids the RDP CLIPBOARD selection entirely."""
    window = runner(['xdotool', 'getwindowfocus'])
    if not window.isdigit() or runner(['xdotool', 'getwindowpid', window]) != str(node.get_application().get_process_id()):
        return False
    def matches(value):
        return focused(node) and read(node) == value and runner(['xdotool', 'getwindowfocus']) == window
    def wait_for(value):
        # XTEST completion precedes Chromium's accessible-text update. Wait
        # for readback without replaying the key or touching another editor.
        for _ in range(15):
            if not focused(node) or runner(['xdotool', 'getwindowfocus']) != window:
                return False
            if read(node) == value:
                return True
            time.sleep(0.03)
        return False
    if not matches(expected):
        return False
    if expected:
        runner(['xdotool', 'key', '--clearmodifiers', 'ctrl+a'])
        if not matches(expected):
            return False
        runner(['xdotool', 'key', '--clearmodifiers', 'BackSpace'])
        if not wait_for(''):
            return False
    if text:
        # Selecting text can change PRIMARY; publish only after clearing our
        # verified owned revision. Middle paste never synthesizes Return.
        write_selection(text)
        x, y = point(node)
        if not matches(''):
            return False
        # --sync waits for a motion event even when the pointer is already at
        # this position. Read back coordinates instead of stalling each edit.
        runner(['xdotool', 'mousemove', str(x), str(y)])
        location = dict(line.split('=', 1) for line in
                        runner(['xdotool', 'getmouselocation', '--shell']).splitlines() if '=' in line)
        if location.get('X') != str(x) or location.get('Y') != str(y) or not matches(''):
            return False
        runner(['xdotool', 'click', '2'])
    return wait_for(text)


def refresh_accessibility():
    # This receiver has a transport loop rather than Atspi.event_main().
    from gi.repository import GLib
    context = GLib.MainContext.default()
    for _ in range(100):
        if not context.pending():
            break
        context.iteration(False)


def atspi_tools():
    import gi
    gi.require_version('Atspi', '2.0')
    from gi.repository import Atspi
    # Bound D-Bus calls instead of hanging on a dead application.
    Atspi.set_timeout(1500, 3000)
    def find():
        refresh_accessibility()
        desktop = Atspi.get_desktop(0)
        for index in range(desktop.get_child_count()):
            app = desktop.get_child_at_index(index)
            if claude_pid(app.get_process_id()):
                app.clear_cache()
        return candidates(desktop, Atspi)
    def read(node):
        refresh_accessibility()
        node.clear_cache()
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
    def __init__(self, find, focus, read, write, context=prompt_context, validate=None, submit=None):
        self.find, self.focus, self.read, self.write, self.context = find, focus, read, write, context
        self.validate = validate
        self.submit = submit
        self.owner = None

    def apply(self, message):
        stream, seq, base, text = (message[k] for k in ('stream', 'seq', 'base', 'text'))
        submitting = message['kind'] == 'submit'
        try:
            # Once attached, validate the exact retained editor instead of walking
            # the entire app on every keystroke burst. Never choose a new target.
            owned = self.owner and self.owner['stream'] == stream
            if submitting and (not owned or self.submit is None):
                return 'conflict'
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
            if submitting and current != text:
                return 'conflict'
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
            if submitting:
                # The receiver journals the request before entering this path.
                # Keep the lease blocked: no repeated Enter after any outcome.
                return 'submitted' if self.submit(target, text, message) else 'uncertain'
            if current != text and not self.write(target, text, current):
                return 'uncertain'
            if self.read(target) != text or self.context(target) != context:
                return 'uncertain'
            owner['hash'], owner['blocked'] = text_hash(text), False
            return 'synced'
        except Exception:
            return 'uncertain'


def walk_ui(root, limit=6000):
    stack, count, deadline = [root], 0, time.monotonic() + 5
    while stack:
        node = stack.pop()
        count += 1
        if count > limit or time.monotonic() > deadline:
            raise ValueError('Conversation accessibility tree is too large')
        yield node
        stack.extend(node.get_child_at_index(i) for i in reversed(range(min(node.get_child_count(), 600)))
                     if node.get_child_at_index(i) is not None)


USER_LABELS = ('您说：', 'You said: ', 'You said:')
CLAUDE_LABELS = ('Claude 已回复：', 'Claude said:', 'Claude replied:', 'Claude 说：')
CHAT_LABELS = ('对话消息', 'Conversation messages', 'Chat messages')
STOP_LABELS = ('停止回复', '停止生成', '停止回应', 'Stop response', 'Stop generating')


def message_body(root):
    """Only message content; exclude screen-reader summaries and action toolbars."""
    parts, stack, visited = [], [root], 0
    while stack:
        node = stack.pop(); visited += 1
        if visited > 5000:
            raise ValueError('Message is too large')
        attrs = node.get_attributes() or {}
        role = node.get_role_name()
        if 'sr-only' in attrs.get('class', '').split() or role in ('tool bar', 'push button', 'entry'):
            continue
        if role in ('paragraph', 'heading', 'static', 'code'):
            try:
                value = accessible_text(node)
            except Exception:
                value = ''
            if value:
                parts.append(value)
                if sum(map(len, parts)) > MAX_REPLY:
                    break
                continue
        stack.extend(node.get_child_at_index(i) for i in reversed(range(min(node.get_child_count(), 600)))
                     if node.get_child_at_index(i) is not None)
    text = '\n'.join(parts)
    return text[:MAX_REPLY], len(text) > MAX_REPLY


def conversation_view(prompt):
    app = prompt.get_application()
    if not claude_pid(app.get_process_id()):
        raise ValueError('Not the paired Claude application')
    from gi.repository import Atspi
    # This receiver uses a clipboard loop, not Atspi's event_main(). Process
    # queued D-Bus events and invalidate structural caches before a snapshot.
    # Otherwise a long-lived receiver can keep seeing the pre-reply children.
    refresh_accessibility()
    app.clear_cache()
    nodes = list(walk_ui(app))
    panels = [n for n in nodes if n.get_role_name() == 'panel' and n.get_name() in CHAT_LABELS
              and n.get_state_set().contains(Atspi.StateType.SHOWING)]
    if len(panels) != 1:
        # A new empty conversation has no transcript yet.
        if not panels:
            return {'panel': None, 'turns': [], 'busy': False}
        raise ValueError('Cannot identify one current conversation')
    busy = any(n.get_role_name() == 'push button' and n.get_name() in STOP_LABELS
               and n.get_state_set().contains(Atspi.StateType.SHOWING) for n in nodes)
    turns = []
    for article in walk_ui(panels[0]):
        if article.get_role_name() != 'article':
            continue
        author, label = None, None
        for node in walk_ui(article):
            if node.get_role_name() != 'heading' or 'sr-only' not in (node.get_attributes() or {}).get('class', '').split():
                continue
            name = node.get_name() or ''
            for who, prefixes in (('user', USER_LABELS), ('claude', CLAUDE_LABELS)):
                for prefix in prefixes:
                    if name.startswith(prefix):
                        author, label = who, name[len(prefix):]
        if author is None:
            raise ValueError('Unrecognized message author')
        text, truncated = message_body(article) if author == 'claude' else (label, False)
        complete = author == 'claude' and not busy and any(
            n.get_role_name() == 'push button' and n.get_name() in ('复制', 'Copy', 'Copy response')
            for n in walk_ui(article))
        turns.append({'node': article, 'author': author, 'text': text,
                      'truncated': truncated, 'complete': complete})
    return {'panel': panels[0], 'turns': turns, 'busy': busy}


class ReplyWatch:
    def __init__(self, find, read_view=conversation_view, submit=submit_prompt):
        self.find, self.read_view, self.submit = find, read_view, submit
        self.watch = None

    def dispatch(self, node, text, message):
        before = self.read_view(node)
        if before['busy']:
            return False
        self.watch = {'stream': message['stream'], 'turn': message['id'], 'text': text,
                      'before': [t['node'] for t in before['turns']], 'anchor': None, 'panel': None}
        return self.submit(node, text)

    def recover(self, message):
        """Explicit read-only recovery, scoped to the current last user turn."""
        nodes = self.find()
        if len(nodes) != 1:
            return {'status': 'not_ready'}
        view = self.read_view(nodes[0])
        users = [t for t in view['turns'] if t['author'] == 'user']
        if not users or users[-1]['text'] != message['text']:
            return {'status': 'conflict'}
        self.watch = {'stream': message['stream'], 'turn': message['id'],
                      'text': message['text'], 'before': [],
                      'anchor': users[-1]['node'], 'panel': view['panel']}
        return self.poll({'stream': message['stream'], 'turn': message['id']})

    def poll(self, message):
        watch = self.watch
        if not watch or message['stream'] != watch['stream'] or message['turn'] != watch['turn']:
            return {'status': 'not_ready'}
        try:
            nodes = self.find()
            if len(nodes) != 1:
                return {'status': 'not_ready'}
            view = self.read_view(nodes[0])
        except Exception:
            # A streaming accessibility tree can change during a read-only
            # snapshot. Read again with a fresh request; never retry UI input,
            # infer acceptance or publish an ambiguous transcript.
            return {'status': 'waiting', 'accepted': False}
        turns = view['turns']
        matches = [i for i, t in enumerate(turns) if t['author'] == 'user' and t['text'] == watch['text']
                   and t['node'] not in watch['before']]
        if watch['anchor'] is None:
            if len(matches) != 1:
                return {'status': 'waiting', 'accepted': False}
            watch['anchor'], watch['panel'] = turns[matches[0]]['node'], view['panel']
        indices = [i for i, t in enumerate(turns) if t['node'] == watch['anchor']]
        if len(indices) != 1 or view['panel'] != watch['panel']:
            return {'status': 'changed'}
        following = turns[indices[0] + 1:]
        if any(t['author'] == 'user' for t in following):
            return {'status': 'changed'}
        replies = [t for t in following if t['author'] == 'claude']
        if not replies:
            return {'status': 'waiting', 'accepted': True}
        text = '\n\n'.join(t['text'] for t in replies)
        return {'status': 'complete' if replies[-1]['complete'] else 'streaming',
                'accepted': True, 'text': text[:MAX_REPLY],
                'truncated': len(text) > MAX_REPLY or any(t['truncated'] for t in replies)}


class Receiver:
    def __init__(self, key, state, fill, sync=None, replies=None):
        self.key, self.state, self.fill = key, Path(state), fill
        self.sync = sync
        self.replies = replies
        self.seen = json.loads(self.state.read_text()) if self.state.exists() else []
        if not isinstance(self.seen, list):
            raise ValueError('Invalid bridge journal')

    def receive(self, raw, now=None):
        message = unpack(self.key, raw, now)
        identifier = message['id']
        if identifier in self.seen:
            return None  # Includes attempts with an unknown outcome; never replay.
        self.seen = (self.seen + [identifier])[-4096:]
        self.last_text = message.get('text') if message['kind'] in ('draft', 'sync') else None
        temporary = self.state.with_suffix('.tmp')
        with temporary.open('w', encoding='utf-8') as stream:
            os.chmod(temporary, 0o600)
            json.dump(self.seen, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.state)  # Persist consumption before any UI input.
        try:
            if message['kind'] == 'poll':
                result = self.replies.poll(message) if self.replies else {'status': 'not_ready'}
            elif message['kind'] == 'recover':
                result = self.replies.recover(message) if self.replies else {'status': 'not_ready'}
            else:
                status = (self.sync.apply(message) if self.sync else 'not_ready') if message['kind'] in ('sync', 'submit') else self.fill(message['text'])
                result = {'status': status}
        except Exception:
            result = {'status': 'uncertain'}
        if message['kind'] in ('submit', 'poll', 'recover'):
            return packet(self.key, dict(result, kind='chat_result', id=identifier,
                                         at=int(time.time())), CHAT_PREFIX)
        sync = message['kind'] == 'sync'
        return packet(self.key, {'kind': 'sync_result' if sync else 'result', 'id': identifier,
                                'at': int(time.time()), **result}, SYNC_PREFIX if sync else PREFIX)


class RelayClient:
    """Keep-alive, outbound mTLS long-poll; never replay a delivered UI request."""
    def __init__(self, config):
        path = Path(config)
        if path.is_symlink() or path.stat().st_uid != os.getuid() or path.stat().st_mode & 0o077:
            raise ValueError('Relay client configuration must be private')
        value = json.loads(path.read_text())
        endpoint = urlsplit(value['endpoint'])
        address = ipaddress.ip_address(endpoint.hostname)
        if (endpoint.scheme != 'https' or endpoint.username or endpoint.password
                or endpoint.query or endpoint.fragment or endpoint.path not in ('', '/')
                or address not in ipaddress.ip_network('100.64.0.0/10')):
            raise ValueError('Relay endpoint must be the paired exact Tailscale IPv4 address')
        self.host, self.port = endpoint.hostname, endpoint.port or 443
        self.fingerprint = value['server_fingerprint']
        if not re.fullmatch('[a-f0-9]{64}', self.fingerprint):
            raise ValueError('Invalid relay certificate pin')
        self.context = ssl.create_default_context(cafile=value['ca'])
        self.context.load_cert_chain(value['cert'], value['key'])
        self.connection = None

    def close(self):
        if self.connection:
            self.connection.close()
        self.connection = None

    def exchange(self, result):
        if self.connection is None:
            self.connection = http.client.HTTPSConnection(self.host, self.port, context=self.context, timeout=25)
            self.connection.connect()
            if hashlib.sha256(self.connection.sock.getpeercert(binary_form=True)).hexdigest() != self.fingerprint:
                self.close()
                raise ValueError('Relay server identity changed')
        self.connection.request('POST', '/v1/input-relay', json.dumps(result), {'Content-Type': 'application/json'})
        response = self.connection.getresponse()
        raw = response.read(300001)
        if response.status != 200 or len(raw) > 300000:
            raise ValueError('Relay unavailable or response too large')
        message = json.loads(raw)
        if message.get('packet'):
            if (not isinstance(message['packet'], str) or len(message['packet']) > MAX_PACKET
                    or not all(re.fullmatch('[a-f0-9]{32}', message.get(k, '')) for k in ('session', 'ticket'))):
                raise ValueError('Invalid relay delivery')
        return message


def relay_receive(receiver, config):
    client = RelayClient(config)
    result = {}
    while True:
        try:
            delivery = client.exchange(result)
            result = {}
            if not delivery.get('packet'):
                continue
            answer = receiver.receive(delivery['packet'])
            if answer:
                result = {'session': delivery['session'], 'ticket': delivery['ticket'], 'packet': answer}
        except (ValueError, KeyError, TypeError, OSError, http.client.HTTPException):
            # Both request delivery and acknowledgement may be unknown. Reopen
            # the transport, never re-run input or retry an old result packet.
            result = {}
            client.close()
            time.sleep(1)


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
        relay_config = json.loads(Path(args.config).read_text()).get('relay_client_config')
        if relay_config:
            tools = (*tools[:3], lambda node, text, expected='': paste_primary_prompt(node, text, expected, read=tools[2]))
        replies = ReplyWatch(tools[0], submit=lambda node, text: submit_prompt(node, text, read=tools[2]))
        receiver = Receiver(load_key(args.config), directory / 'seen.json',
                            lambda text: fill_prompt(text, *tools),
                            DraftSync(*tools, validate=valid_atspi_prompt, submit=replies.dispatch), replies)
        if relay_config:
            relay_receive(receiver, relay_config)
            return
        while True:
            try:
                raw = clipboard_read()
                if raw.startswith((PREFIX, SYNC_PREFIX, CHAT_PREFIX)):
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
