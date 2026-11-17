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
        self.assertTrue(bridge.paste_prompt(node, '测试', runner, read, lambda: clipboard[0], put))
        inputs = [c for c in calls if c[1] == 'key']
        self.assertEqual(inputs, [['xdotool', 'key', '--clearmodifiers', 'ctrl+v']])
        calls.clear()
        windows = iter(('123', '456'))
        def changed(command):
            calls.append(command)
            return next(windows) if command[1] == 'getwindowfocus' else '42'
        self.assertFalse(bridge.paste_prompt(node, '测试', changed, lambda n: '', lambda: clipboard[0], put))
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


if __name__ == '__main__':
    unittest.main()
