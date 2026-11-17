import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest

import input_bridge as bridge
import claude_session


KEY = bytes(range(32))
IDENTIFIER = 'a' * 32


def request(text='中文输入\nSecond line 😀', **overrides):
    value = {'kind': 'draft', 'id': IDENTIFIER, 'at': int(time.time()), 'text': text}
    value.update(overrides)
    return bridge.packet(KEY, value)


def sync_message(text, seq=1, base='', stream='b' * 32):
    return {'kind': 'sync', 'id': ('%032x' % seq), 'at': int(time.time()),
            'stream': stream, 'seq': seq, 'base': bridge.text_hash(base), 'text': text}


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.node = object()
        self.nodes = [self.node]
        self.text = ''
        self.context = 'conversation-1'
        self.writes = []
        def write(node, text, expected):
            self.assertEqual(self.text, expected)
            self.writes.append(text)
            self.text = text
            return True
        self.sync = bridge.DraftSync(lambda: self.nodes, lambda n: True,
                                    lambda n: self.text, write, lambda n: self.context)

    def test_sync_appends_replaces_deletes_and_clears_owned_draft(self):
        previous = ''
        for seq, text in enumerate(('中', '中文\n第二行 😀', '中文', '', '重新输入'), 1):
            self.assertEqual(self.sync.apply(sync_message(text, seq, previous)), 'synced')
            self.assertEqual(self.text, text)
            previous = text
        self.assertEqual(len(self.writes), 5)

    def test_existing_unowned_draft_is_never_overwritten(self):
        self.text = 'keep me'
        self.assertEqual(self.sync.apply(sync_message('new')), 'conflict')
        self.assertEqual(self.writes, [])

    def test_remote_edit_submission_or_navigation_blocks_future_updates(self):
        for change in ('edit', 'send', 'context', 'node'):
            self.setUp()
            self.assertEqual(self.sync.apply(sync_message('first')), 'synced')
            if change == 'edit': self.text = 'remote edit'
            if change == 'send': self.text = ''
            if change == 'context': self.context = 'conversation-2'
            if change == 'node': self.nodes = [object()]
            self.assertEqual(self.sync.apply(sync_message('second', 2, 'first')), 'conflict')
            self.assertEqual(self.writes, ['first'])
            self.assertEqual(self.sync.apply(sync_message('third', 3, 'first')), 'conflict')

    def test_old_revisions_wrong_base_and_other_panel_cannot_replace(self):
        self.assertEqual(self.sync.apply(sync_message('first')), 'synced')
        self.assertEqual(self.sync.apply(sync_message('other', 1, stream='c' * 32)), 'conflict')
        self.assertEqual(self.sync.apply(sync_message('older', 1, 'first')), 'conflict')
        self.assertEqual(self.writes, ['first'])
        self.setUp()
        self.sync.apply(sync_message('first'))
        self.assertEqual(self.sync.apply(sync_message('second', 2, 'wrong')), 'conflict')

    def test_unknown_outcome_and_restart_require_explicit_empty_reconnect(self):
        def failed(node, text, expected):
            self.text = text
            raise TimeoutError()
        self.sync.write = failed
        self.assertEqual(self.sync.apply(sync_message('maybe')), 'uncertain')
        self.assertEqual(self.sync.apply(sync_message('retry', 2, '')), 'conflict')
        self.setUp()
        self.text = 'draft remaining after receiver restart'
        self.assertEqual(self.sync.apply(sync_message('next', 2, 'previous')), 'conflict')
        self.text = ''
        self.assertEqual(self.sync.apply(sync_message('new', stream='c' * 32)), 'synced')

    def test_change_during_focus_never_writes(self):
        def focus(node):
            self.text = 'remote edit'
            return True
        self.sync.focus = focus
        self.assertEqual(self.sync.apply(sync_message('new')), 'conflict')
        self.assertEqual(self.writes, [])

    def test_retained_target_fast_path_validates_without_rescanning(self):
        calls = []
        self.sync.find = lambda: calls.append('scan') or self.nodes
        self.sync.validate = lambda n: n in self.nodes
        self.assertEqual(self.sync.apply(sync_message('first')), 'synced')
        self.assertEqual(self.sync.apply(sync_message('second', 2, 'first')), 'synced')
        self.assertEqual(calls, ['scan'])
        self.nodes = []
        self.assertEqual(self.sync.apply(sync_message('third', 3, 'second')), 'not_ready')
        self.assertEqual(self.writes, ['first', 'second'])

    def test_v2_validation_and_empty_text(self):
        value = sync_message('')
        raw = bridge.packet(KEY, value, bridge.SYNC_PREFIX)
        self.assertEqual(bridge.unpack(KEY, raw), value)
        for changes in ({'seq': 0}, {'seq': True}, {'base': 'bad'}, {'stream': 'bad'},
                        {'text': 'a\x00b'}, {'text': 'x' * 4001}, {'kind': 'draft'}):
            invalid = dict(value, **changes)
            with self.assertRaises(ValueError):
                bridge.unpack(KEY, bridge.packet(KEY, invalid, bridge.SYNC_PREFIX))
        with self.assertRaises(ValueError):
            bridge.unpack(KEY, bridge.packet(KEY, value))

    def test_receiver_persists_id_before_sync_and_never_logs_draft(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / 'seen.json'
            receiver = bridge.Receiver(KEY, state, lambda t: self.fail('legacy fill'), self.sync)
            raw = bridge.packet(KEY, sync_message('中文\n😀'), bridge.SYNC_PREFIX)
            result = receiver.receive(raw)
            outer = json.loads(base64.b64decode(result[len(bridge.SYNC_PREFIX):]))
            payload = json.loads(base64.b64decode(outer['body']))
            self.assertEqual(payload['status'], 'synced')
            self.assertIsNone(receiver.receive(raw))
            self.assertIsNone(bridge.Receiver(KEY, state, None, self.sync).receive(raw))
            self.assertNotIn('中文', state.read_text())


class ChatTests(unittest.TestCase):
    def test_explicit_submit_requires_exact_owned_revision_and_is_not_repeated(self):
        node, storage, enters = object(), [''], []
        def write(n, text, expected):
            storage[0] = text
            return True
        def submit(n, text, message):
            enters.append(text)
            return True
        sync = bridge.DraftSync(lambda: [node], lambda n: True, lambda n: storage[0], write,
                                lambda n: 'chat', submit=submit)
        message = dict(sync_message('hello', 2, 'hello'), kind='submit')
        self.assertEqual(sync.apply(message), 'conflict')
        self.assertEqual(sync.apply(sync_message('hello')), 'synced')
        self.assertEqual(sync.apply(dict(message, text='older')), 'conflict')
        self.assertEqual(sync.apply(message), 'submitted')
        self.assertEqual(sync.apply(dict(message, seq=3)), 'conflict')
        self.assertEqual(enters, ['hello'])

    def test_submit_unknown_outcome_is_journaled_before_enter_and_blocks_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / 'seen.json'
            node, storage, enters = object(), [''], []
            def write(n, text, expected): storage[0] = text; return True
            def submit(n, text, message):
                self.assertIn(message['id'], json.loads(state.read_text()))
                enters.append(text)
                raise TimeoutError()
            sync = bridge.DraftSync(lambda: [node], lambda n: True, lambda n: storage[0], write,
                                    lambda n: 'chat', submit=submit)
            receiver = bridge.Receiver(KEY, state, None, sync)
            sync.apply(sync_message('hello'))
            message = dict(sync_message('hello', 2, 'hello'), kind='submit')
            raw = bridge.packet(KEY, message, bridge.CHAT_PREFIX)
            result = receiver.receive(raw)
            outer = json.loads(base64.b64decode(result[len(bridge.CHAT_PREFIX):]))
            self.assertEqual(json.loads(base64.b64decode(outer['body']))['status'], 'uncertain')
            self.assertIsNone(receiver.receive(raw))
            self.assertIsNone(bridge.Receiver(KEY, state, None, sync).receive(raw))
            self.assertEqual(enters, ['hello'])
            self.assertNotIn('hello', state.read_text())

    def test_submit_key_requires_matching_native_window_focus_and_text(self):
        class Node:
            def get_application(self): return self
            def get_process_id(self): return 42
        calls = []
        def run(command):
            calls.append(command)
            return '123' if command[1] == 'getwindowfocus' else '42'
        node = Node()
        self.assertTrue(bridge.submit_prompt(node, 'hello', run, lambda n: 'hello', lambda n: True))
        self.assertEqual([c for c in calls if c[1] == 'key'],
                         [['xdotool', 'key', '--clearmodifiers', 'Return']])
        calls.clear()
        for expected, focused in (('older', True), ('hello', False), ('', True)):
            self.assertFalse(bridge.submit_prompt(node, expected, run, lambda n: 'hello', lambda n: focused))
        self.assertFalse(any(c[1] == 'key' for c in calls))

    def test_chat_protocol_is_separate_and_rejects_empty_submit_or_bad_watch(self):
        message = dict(sync_message('hello', 2, 'hello'), kind='submit')
        self.assertEqual(bridge.unpack(KEY, bridge.packet(KEY, message, bridge.CHAT_PREFIX)), message)
        poll = {'kind': 'poll', 'id': IDENTIFIER, 'at': int(time.time()),
                'stream': 'b' * 32, 'turn': 'c' * 32}
        self.assertEqual(bridge.unpack(KEY, bridge.packet(KEY, poll, bridge.CHAT_PREFIX)), poll)
        for value, prefix in ((dict(message, text=''), bridge.CHAT_PREFIX),
                              (message, bridge.SYNC_PREFIX), (poll, bridge.SYNC_PREFIX),
                              (dict(poll, turn='../file'), bridge.CHAT_PREFIX)):
            with self.assertRaises(ValueError):
                bridge.unpack(KEY, bridge.packet(KEY, value, prefix))

    def test_reply_watch_binds_only_new_submitted_turn_and_handles_progress(self):
        node, panel, old, anchor, reply = (object() for _ in range(5))
        def turn(n, author, text, complete=False):
            return dict(node=n, author=author, text=text, complete=complete, truncated=False)
        view = {'panel': panel, 'busy': False, 'turns': [turn(old, 'user', 'hello')]}
        watch = bridge.ReplyWatch(lambda: [node], lambda n: view, lambda *a: True)
        message = dict(sync_message('hello', 2, 'hello'), kind='submit')
        self.assertTrue(watch.dispatch(node, 'hello', message))
        poll = dict(stream=message['stream'], turn=message['id'])
        self.assertEqual(watch.poll(poll), {'status': 'waiting', 'accepted': False})
        view['turns'].append(turn(anchor, 'user', 'hello'))
        self.assertEqual(watch.poll(poll), {'status': 'waiting', 'accepted': True})
        view['turns'].append(turn(reply, 'claude', '中文\npartial'))
        self.assertEqual(watch.poll(poll)['status'], 'streaming')
        view['turns'][-1]['text'] = '中文\ncomplete'
        view['turns'][-1]['complete'] = True
        result = watch.poll(poll)
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(result['text'], '中文\ncomplete')
        self.assertEqual(watch.poll(dict(poll, turn='bad')), {'status': 'not_ready'})
        view['panel'] = object()
        self.assertEqual(watch.poll(poll), {'status': 'changed'})

    def test_busy_or_later_user_turn_is_never_treated_as_our_reply(self):
        node, panel, anchor = object(), object(), object()
        view = {'panel': panel, 'busy': True, 'turns': []}
        submits = []
        watch = bridge.ReplyWatch(lambda: [node], lambda n: view, lambda *a: submits.append(a) or True)
        message = dict(sync_message('hello', 2, 'hello'), kind='submit')
        self.assertFalse(watch.dispatch(node, 'hello', message))
        self.assertEqual(submits, [])
        view['busy'] = False
        watch.dispatch(node, 'hello', message)
        view['turns'] = [dict(node=anchor, author='user', text='hello'),
                         dict(node=object(), author='user', text='another conversation turn')]
        self.assertEqual(watch.poll(dict(stream=message['stream'], turn=message['id'])), {'status': 'changed'})

    def test_message_body_omits_controls_summaries_and_bounds_large_output(self):
        class Node:
            def __init__(self, role, text='', children=(), classes=''):
                self.role, self.text, self.children, self.classes = role, text, children, classes
            def get_attributes(self): return {'class': self.classes}
            def get_role_name(self): return self.role
            def get_text_iface(self): return self
            def get_text(self, start, end): return self.text
            def get_child_count(self): return len(self.children)
            def get_child_at_index(self, i): return self.children[i]
        root = Node('article', children=[Node('heading', 'Claude said: duplicate', classes='sr-only'),
                    Node('paragraph', '中文\nreply'), Node('tool bar', children=[Node('static', 'Copy')])])
        self.assertEqual(bridge.message_body(root), ('中文\nreply', False))
        text, truncated = bridge.message_body(Node('paragraph', 'x' * (bridge.MAX_REPLY + 1)))
        self.assertEqual(len(text), bridge.MAX_REPLY)
        self.assertTrue(truncated)


class BridgeTests(unittest.TestCase):
    def test_unicode_multiline_authenticated_roundtrip_and_rejections(self):
        self.assertEqual(bridge.unpack(KEY, request())['text'], '中文输入\nSecond line 😀')
        for raw, key in ((request(), b'wrong'), (request(at=0), KEY),
                         (request(kind='result'), KEY), (request(text='x' * 4001), KEY),
                         (request(text='a\x00b'), KEY), (request(id='../file'), KEY),
                         ('ordinary clipboard text', KEY)):
            with self.subTest(raw=raw[:20]), self.assertRaises((ValueError, KeyError)):
                bridge.unpack(key, raw)

    def test_fill_does_not_overwrite_or_submit_and_requires_single_target(self):
        node = object()
        storage = {node: ''}
        writes = []
        def write(n, text):
            writes.append((n, text))
            storage[n] = text
            return True
        tools = (lambda: [node], lambda n: True, storage.get, write)
        self.assertEqual(bridge.fill_prompt('测试\n多行', *tools), 'filled')
        self.assertEqual(storage[node], '测试\n多行')
        self.assertEqual(bridge.fill_prompt('new', *tools), 'has_draft')
        self.assertEqual(len(writes), 1)
        for targets in ([], [node, node]):
            self.assertEqual(bridge.fill_prompt('new', lambda: targets, *tools[1:]), 'not_ready')

    def test_navigation_or_new_draft_during_focus_blocks_write(self):
        node = object()
        sequence = iter(([node], []))
        writes = []
        result = bridge.fill_prompt('text', lambda: next(sequence), lambda n: True,
                                    lambda n: '', lambda *a: writes.append(a))
        self.assertEqual(result, 'not_ready')
        self.assertEqual(writes, [])

    def test_unknown_ui_outcome_is_persisted_and_never_replayed(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder) / 'seen.json'
            calls = []
            def uncertain(text):
                self.assertIn(IDENTIFIER, json.loads(state.read_text()))
                calls.append(text)
                raise RuntimeError('application exited after input')
            receiver = bridge.Receiver(KEY, state, uncertain)
            self.assertIsNotNone(receiver.receive(request()))
            self.assertIsNone(receiver.receive(request()))
            resumed = bridge.Receiver(KEY, state, uncertain)
            self.assertIsNone(resumed.receive(request()))
            self.assertEqual(len(calls), 1)
            self.assertNotIn('中文', state.read_text())

    def test_readback_mismatch_does_not_claim_filled(self):
        node = object()
        self.assertEqual(bridge.fill_prompt('expected', lambda: [node], lambda n: True,
                                           lambda n: '', lambda n, t: True), 'uncertain')

    def test_paste_requires_same_native_window_and_empty_editor(self):
        class App:
            def get_process_id(self): return 42
        class Node:
            def get_application(self): return App()
        calls, clipboard = [], ['']
        node = Node()
        def runner(command):
            calls.append(command)
            if command[1] == 'getwindowfocus': return '123'
            if command[1] == 'getwindowpid': return '42'
            return ''
        def put(text): clipboard[0] = text
        def read(n): return '测试' if any(c[1] == 'key' for c in calls) else ''
        self.assertTrue(bridge.paste_prompt(node, '测试', runner, read, lambda: clipboard[0], put, focused=lambda n: True))
        inputs = [c for c in calls if c[1] == 'key']
        self.assertEqual(inputs, [['xdotool', 'key', '--clearmodifiers', 'ctrl+v']])
        calls.clear()
        windows = iter(('123', '456'))
        def changed(command):
            calls.append(command)
            return next(windows) if command[1] == 'getwindowfocus' else '42'
        self.assertFalse(bridge.paste_prompt(node, '测试', changed, lambda n: '', lambda: clipboard[0], put, focused=lambda n: True))
        self.assertFalse(any(c[1] == 'key' for c in calls))

    def test_replace_and_clear_only_focused_matching_editor(self):
        class Node:
            def get_application(self): return self
            def get_process_id(self): return 42
        node = Node()
        for new in ('new\n中文', ''):
            text, clip, calls = ['old'], [''], []
            def runner(command):
                calls.append(command)
                if command[1] == 'getwindowfocus': return '123'
                if command[1] == 'getwindowpid': return '42'
                if command[-1] in ('ctrl+v', 'BackSpace'): text[0] = new
                return ''
            self.assertTrue(bridge.paste_prompt(node, new, runner, lambda n: text[0],
                                               lambda: clip[0], lambda t: clip.__setitem__(0, t),
                                               expected='old', focused=lambda n: True))
            self.assertEqual([c[-1] for c in calls if c[1] == 'key'],
                             ['ctrl+a', 'ctrl+v' if new else 'BackSpace'])
            calls.clear()
            self.assertFalse(bridge.paste_prompt(node, new, runner, lambda n: 'old',
                                                lambda: clip[0], lambda t: clip.__setitem__(0, t),
                                                expected='old', focused=lambda n: False))
            self.assertFalse(any(c[1] == 'key' for c in calls))

    def test_focus_failure_is_not_ready_but_paste_failure_is_uncertain(self):
        node = object()
        calls = []
        def failed_focus(n):
            raise TimeoutError('application is unresponsive')
        self.assertEqual(bridge.fill_prompt('text', lambda: [node], failed_focus,
                                           lambda n: '', lambda *args: calls.append(args)), 'not_ready')
        self.assertEqual(calls, [])
        def failed_paste(n, text):
            calls.append(text)
            raise TimeoutError('lost acknowledgement after input')
        self.assertEqual(bridge.fill_prompt('text', lambda: [node], lambda n: True,
                                           lambda n: '', failed_paste), 'uncertain')
        self.assertEqual(calls, ['text'])

    def test_chromium_embedded_paragraphs_preserve_text_and_empty_prompt(self):
        class Node:
            def __init__(self, text, role='paragraph', children=()):
                self.text, self.role, self.children = text, role, children
            def get_text_iface(self): return self
            def get_text(self, start, end): return self.text
            def get_role_name(self): return self.role
            def get_child_count(self): return len(self.children)
            def get_child_at_index(self, i): return self.children[i]
        empty = Node('\ufffc', 'entry', [Node('\n')])
        self.assertEqual(bridge.accessible_text(empty), '')
        multiline = Node('\ufffc\ufffc\ufffc', 'entry', [Node('中文'), Node('line 2'), Node('\n')])
        self.assertEqual(bridge.accessible_text(multiline), '中文\nline 2\n')
        with self.assertRaises(ValueError):
            bridge.accessible_text(Node('\ufffc\ufffc', 'entry', [Node('one')]))

    def test_accessibility_flag_requires_opt_in_config(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            proc = root / 'proc'
            session = proc / '42'
            session.mkdir(parents=True)
            (session / 'status').write_text('Uid:\t1001\n')
            (session / 'comm').write_text('xfce4-session')
            (session / 'environ').write_bytes(b'DISPLAY=:10\0')
            calls = []
            spawn = lambda command, **kwargs: calls.append(command)
            environment = {'HOME': str(root)}
            claude_session.start(proc, 1001, environment, '/usr/bin/claude-desktop', spawn)
            self.assertEqual(calls[-1], ['/usr/bin/claude-desktop'])
            config = root / '.config/hostcontrol/input-bridge.json'
            config.parent.mkdir(parents=True)
            config.write_text('{}')
            claude_session.start(proc, 1001, environment, '/usr/bin/claude-desktop', spawn)
            self.assertEqual(calls[-1], ['/usr/bin/claude-desktop', '--force-renderer-accessibility'])
            # Old diagnostic settings must never disable hardware acceleration.
            (config.parent / 'claude-launch.json').write_text('{"software_rendering": true}')
            claude_session.start(proc, 1001, environment, '/usr/bin/claude-desktop', spawn)
            self.assertEqual(calls[-1], ['/usr/bin/claude-desktop', '--force-renderer-accessibility'])

    def test_only_claude_prompt_candidates_are_selected(self):
        class States:
            EDITABLE, ENABLED, VISIBLE, SHOWING = range(4)
        class API:
            StateType = States
        class Node:
            def __init__(self, name, children=(), pid=1, editable=False):
                self.name, self.children, self.pid, self.editable = name, children, pid, editable
            def get_child_count(self): return len(self.children)
            def get_child_at_index(self, i): return self.children[i]
            def get_process_id(self): return self.pid
            def get_name(self): return self.name
            def get_attributes(self): return {'tag': 'div', 'xml-roles': 'textbox'}
            def get_state_set(self): return self
            def contains(self, state): return self.editable
        search = Node('Search chats', editable=True)
        prompt = Node('输入您发送给 Claude 的提示词', editable=True)
        unrelated = Node('Message Claude', editable=True)
        desktop = Node('desktop', (Node('Claude', (search, prompt), pid=7),
                                    Node('Other app', (unrelated,), pid=9)))
        self.assertEqual(bridge.candidates(desktop, API, lambda pid: pid == 7), [prompt])

    @unittest.skipUnless(os.name == 'nt' and shutil.which('powershell.exe'), 'Windows PowerShell protocol')
    def test_powershell_to_python_protocol_and_verified_ack(self):
        script = Path(__file__).with_name('claude-input.ps1')
        code = (". '" + str(script).replace("'", "''") + "' -FunctionsOnly\n"
                "$key=[byte[]](0..31)\n"
                "$text=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('" +
                base64.b64encode('中文\nemoji 😀'.encode()).decode() + "'))\n"
                "New-BridgePacket $key ('a'*32) $text\n")
        encoded = base64.b64encode(code.encode('utf-16-le')).decode()
        result = subprocess.run(['powershell.exe', '-NoProfile', '-EncodedCommand', encoded],
                                capture_output=True, text=True, timeout=20, check=True)
        self.assertEqual(bridge.unpack(KEY, result.stdout.strip())['text'], '中文\nemoji 😀')
        ack = bridge.packet(KEY, {'kind': 'result', 'id': IDENTIFIER, 'at': int(time.time()), 'status': 'filled'})
        code = (". '" + str(script).replace("'", "''") + "' -FunctionsOnly\n"
                "$key=[byte[]](0..31)\n"
                "Read-BridgeResult $key ('a'*32) '" + ack + "'\n"
                "Read-BridgeResult $key ('b'*32) '" + ack + "'\n")
        encoded = base64.b64encode(code.encode('utf-16-le')).decode()
        result = subprocess.run(['powershell.exe', '-NoProfile', '-EncodedCommand', encoded],
                                capture_output=True, text=True, timeout=20, check=True)
        self.assertEqual(result.stdout.strip(), 'filled')

    @unittest.skipUnless(os.name == 'nt' and shutil.which('powershell.exe'), 'Windows PowerShell protocol')
    def test_powershell_sync_protocol_debounce_ime_and_latest_queue(self):
        script = Path(__file__).with_name('claude-input.ps1')
        ack = bridge.packet(KEY, {'kind': 'sync_result', 'id': IDENTIFIER,
                                 'at': int(time.time()), 'status': 'synced'}, bridge.SYNC_PREFIX)
        code = (". '" + str(script).replace("'", "''") + "' -FunctionsOnly\n"
                "$key=[byte[]](0..31)\n"
                "New-BridgePacket $key ('a'*32) '' ('b'*32) 1 (Get-DraftHash '')\n"
                "Read-BridgeResult $key ('a'*32) '" + ack + "' -Sync\n"
                "Read-BridgeResult $key ('a'*32) '" + ack + "'\n"
                "$now=[DateTime]::UtcNow\n"
                "$s=@{enabled=$true;paused=$false;composing=$false;pending=$null;confirmed='';changed=$now}\n"
                "Get-SyncDecision $s 'latest' $now\n"
                "$s.changed=$now.AddSeconds(-1)\n"
                "Get-SyncDecision $s 'latest' $now\n"
                "$s.pending=@{text='older'}\n"
                "Get-SyncDecision $s 'latest' $now\n"
                "$s.pending=$null; $s.composing=$true\n"
                "Get-SyncDecision $s 'latest' $now\n"
                "$s.composing=$false; $s.confirmed='older'\n"
                "Get-SyncDecision $s 'latest' $now\n"
                "$s.confirmed='latest'\n"
                "Get-SyncDecision $s 'latest' $now\n"
                "$s.paused=$true\n"
                "Get-SyncDecision $s 'different' $now\n"
                "$s.clipHashes=@((Get-DraftHash \"old`nline\"),(Get-DraftHash 'latest'))\n"
                "Test-OtherClipboard $s \"old`r`nline\"\n"
                "Test-OtherClipboard $s ''\n"
                "Test-OtherClipboard $s 'latest'\n"
                "Test-OtherClipboard $s 'unrelated copy'\n")
        result = subprocess.run(['powershell.exe', '-NoProfile', '-EncodedCommand',
                                 base64.b64encode(code.encode('utf-16-le')).decode()],
                                capture_output=True, text=True, timeout=20, check=True)
        lines = result.stdout.strip().splitlines()
        self.assertEqual(bridge.unpack(KEY, lines[0])['text'], '')
        self.assertEqual(lines[1:], ['synced', 'wait', 'send', 'wait', 'wait', 'send', 'wait', 'wait',
                                   'False', 'False', 'False', 'True'])

    @unittest.skipUnless(os.name == 'nt' and shutil.which('powershell.exe'), 'Windows PowerShell protocol')
    def test_powershell_explicit_enter_waits_for_latest_sync_and_parses_reply(self):
        script = Path(__file__).with_name('claude-input.ps1')
        ack = bridge.packet(KEY, {'kind': 'chat_result', 'id': IDENTIFIER,
                                 'at': int(time.time()), 'status': 'complete', 'accepted': True,
                                 'text': 'reply', 'truncated': False}, bridge.CHAT_PREFIX)
        code = (". '" + str(script).replace("'", "''") + "' -FunctionsOnly\n"
                "$key=[byte[]](0..31)\n"
                "New-BridgePacket $key ('a'*32) 'latest' ('b'*32) 2 (Get-DraftHash 'latest') 'submit'\n"
                "New-BridgePacket $key ('c'*32) '' ('b'*32) 0 '' 'poll' ('a'*32)\n"
                "(Read-ChatResult $key ('a'*32) '" + ack + "').text\n"
                "Read-ChatResult $key ('b'*32) '" + ack + "'\n"
                "$now=[DateTime]::UtcNow\n"
                "$s=@{enabled=$true;paused=$false;composing=$false;pending=$null;confirmed='older';changed=$now;sendWanted=$true}\n"
                "Get-SyncDecision $s 'latest' $now\n"
                "$s.confirmed='latest'\n"
                "Get-SyncDecision $s 'latest' $now\n"
                "$s.pending=@{kind='sync'}\n"
                "Get-SyncDecision $s 'latest' $now\n"
                "Get-EnterAction $false $false $false\n"
                "Get-EnterAction $true $false $false\n"
                "Get-EnterAction $false $true $false\n"
                "Get-EnterAction $false $false $true\n")
        result = subprocess.run(['powershell.exe', '-NoProfile', '-EncodedCommand',
                                 base64.b64encode(code.encode('utf-16-le')).decode()],
                                capture_output=True, text=True, timeout=20, check=True)
        lines = result.stdout.strip().splitlines()
        self.assertEqual(bridge.unpack(KEY, lines[0])['kind'], 'submit')
        self.assertEqual(bridge.unpack(KEY, lines[1])['kind'], 'poll')
        self.assertEqual(lines[2:], ['reply', 'send', 'submit', 'wait', 'submit', 'newline', 'ime', 'ignore'])


if __name__ == '__main__':
    unittest.main()
