"""Offline integration checks; never starts real agents, RDP or Claude."""
import json
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import claude_session
import launcher
import tailscale_policy as policy

SCRIPTS = Path(__file__).parent
WIN, SERVER = '100.80.0.1', '100.80.0.2'


class UnifiedWorkflowTests(unittest.TestCase):
    def test_preserved_policy_plan_to_installer_audit_and_default_launcher(self):
        current = {'grants': [{'src': ['*'], 'dst': ['*'], 'ip': ['*']},
                              {'src': ['100.90.0.1'], 'dst': ['100.90.0.2'], 'ip': ['tcp:443']}],
                   'ssh': [{'action': 'check', 'src': ['autogroup:member'],
                            'dst': ['autogroup:self'], 'users': ['root']}]}
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            policy.create_plan(json.dumps(current), root / 'plan', WIN, SERVER, True)
            candidate = root / 'plan/policy.json'
            self.assertEqual(json.loads(candidate.read_text())['ssh'], current['ssh'])
            command = [sys.executable, str(SCRIPTS / 'tailscale_policy.py'), 'audit',
                       '--current', str(candidate), '--windows-ip', WIN, '--server-ip', SERVER]
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(json.loads(result.stdout)['live_verified'])
            agent, executable = root / 'agent.json', root / 'hostagent.exe'
            agent.write_text(json.dumps({'listen': WIN + ':7443', 'peer_ip': SERVER,
                                         'key': 'private-material-not-to-be-copied'}))
            executable.write_bytes(b'fake executable; never run')
            command = [sys.executable, str(SCRIPTS / 'launcher.py'), 'generate',
                       '--agent-config', str(agent), '--agent-exe', str(executable),
                       '--server-ip', SERVER, '--desktop-user', 'worker', '--out-dir', str(root / 'kit')]
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((root / 'kit/Start-Claude.cmd').is_file())
            self.assertIn('redirectclipboard:i:1', (root / 'kit/server-desktop.rdp').read_text())
            for p in (root / 'kit').iterdir():
                self.assertNotIn(b'private-material-not-to-be-copied', p.read_bytes())
            before = (root / 'kit/launch.json').read_bytes()
            self.assertNotEqual(subprocess.run(command, capture_output=True).returncode, 0)
            self.assertEqual((root / 'kit/launch.json').read_bytes(), before)
            # A broad grant arriving later blocks the exact installer audit.
            candidate.write_text(json.dumps(current))
            result = subprocess.run([sys.executable, str(SCRIPTS / 'tailscale_policy.py'), 'audit',
                                     '--current', str(candidate), '--windows-ip', WIN,
                                     '--server-ip', SERVER], capture_output=True)
            self.assertNotEqual(result.returncode, 0)

    def test_wrong_pair_creates_no_launch_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            config, exe = root / 'agent.json', root / 'agent.exe'
            config.write_text(json.dumps({'listen': WIN + ':7443', 'peer_ip': '100.90.0.2'}))
            exe.touch()
            with self.assertRaisesRegex(ValueError, 'paired'):
                launcher.generate(root / 'kit', config, exe, SERVER, 'worker')
            self.assertFalse((root / 'kit').exists())

    def test_configure_accepts_historical_and_server_alias(self):
        with tempfile.TemporaryDirectory() as folder:
            for name in ('--ubuntu-ip', '--server-ip'):
                path = Path(folder) / (name[2:] + '.json')
                result = subprocess.run([sys.executable, str(SCRIPTS / 'configure.py'), 'policy',
                                         '--windows-ip', WIN, name, SERVER, '--out', str(path)],
                                        capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((Path(folder) / 'ubuntu-ip.json').read_bytes(),
                             (Path(folder) / 'server-ip.json').read_bytes())

    def test_reconnect_hook_preserves_original_body_and_is_idempotent(self):
        original = '#!/bin/sh\n# upstream setup\nexec /usr/bin/true\n'
        path = Path('/home/worker/.local/bin/hostcontrol-start-claude.py')
        edited = launcher.reconnect_hook(original, 'worker', path)
        self.assertTrue(edited.endswith(original.split('\n', 1)[1]))
        self.assertLess(edited.index('BEGIN HOSTCONTROL'), edited.index('exec /usr/bin/true'))
        self.assertEqual(launcher.reconnect_hook(edited, 'worker', path), edited)
        with self.assertRaises(ValueError):
            launcher.reconnect_hook('#!/usr/bin/python3\npass\n', 'worker', path)
        with self.assertRaises(ValueError):
            launcher.reconnect_hook(edited.replace('# END HOSTCONTROL CLAUDE worker', '# broken'), 'worker', path)

    def process(self, root, pid, uid, name, env=None):
        path = root / str(pid)
        path.mkdir()
        (path / 'status').write_text('Uid:\t{0}\t{0}\t{0}\t{0}\n'.format(uid))
        (path / 'comm').write_text(name + '\n')
        (path / 'environ').write_bytes(('\0'.join(k + '=' + v for k, v in (env or {}).items()) + '\0').encode())

    def test_claude_uses_only_intended_user_display_and_session_variables(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.process(root, 1, 0, 'xfce4-session', {'DISPLAY': ':10'})
            self.process(root, 2, 1001, 'xfce4-session', {'DISPLAY': ':11', 'DBUS_SESSION_BUS_ADDRESS': 'bus-11'})
            self.process(root, 3, 1001, 'xfce4-session', {'DISPLAY': ':10', 'DBUS_SESSION_BUS_ADDRESS': 'bus-10', 'EXTRA': 'not copied'})
            spawn = mock.Mock()
            status = claude_session.start(root, 1001, {'DISPLAY': ':10', 'HOME': '/home/worker'}, '/usr/bin/claude-desktop', spawn)
            self.assertEqual(status, 'started')
            env = spawn.call_args.kwargs['env']
            self.assertEqual(env['DBUS_SESSION_BUS_ADDRESS'], 'bus-10')
            self.assertNotIn('EXTRA', env)
            self.assertEqual(spawn.call_args.args[0], ['/usr/bin/claude-desktop'])
            self.assertTrue(spawn.call_args.kwargs['start_new_session'])
            with self.assertRaises(ValueError):
                claude_session.session_environment(root, 1001)

    def test_existing_claude_is_reused_and_missing_session_does_not_spawn(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            spawn = mock.Mock()
            with self.assertRaises(ValueError):
                claude_session.start(root, 1001, {}, 'claude-desktop', spawn)
            spawn.assert_not_called()
            self.process(root, 4, 1001, 'claude-desktop')
            self.assertEqual(claude_session.start(root, 1001, {}, 'claude-desktop', spawn), 'already_running')
            spawn.assert_not_called()


@unittest.skipUnless(shutil.which('pwsh'), 'PowerShell required for mocked launcher scenarios')
class DoubleClickLauncherTests(unittest.TestCase):
    def invoke(self, scenario, shell='pwsh'):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            config, exe = root / 'agent config.json', root / 'hostagent.exe'
            config.write_text(json.dumps({'listen': WIN + ':7443', 'peer_ip': SERVER}))
            exe.touch()
            launcher.generate(root / 'kit', config, exe, SERVER, 'worker')
            env = dict(os.environ, HC_TEST_SCENARIO=scenario, HC_TEST_ROOT=str(root / 'kit'))
            harness = r'''
$ErrorActionPreference='Stop'
. (Join-Path $env:HC_TEST_ROOT 'start-hostcontrol.ps1')
$script:hcFixture=Get-Content (Join-Path $env:HC_TEST_ROOT 'launch.json') -Raw | ConvertFrom-Json
$script:hcSpawned=@()
function Get-NetIPAddress { param($IPAddress,$ErrorAction) if($env:HC_TEST_SCENARIO -ne 'offline') { @{IPAddress=$IPAddress} } }
function Get-NetTCPConnection {
    param($LocalAddress,$LocalPort,$State,$ErrorAction)
    if($env:HC_TEST_SCENARIO -ne 'new' -or $script:hcSpawned.Count) { @{OwningProcess=123} }
}
function Get-CimInstance {
    param($ClassName,$Filter)
    $path=if($env:HC_TEST_SCENARIO -eq 'wrong') {'different.exe'} else {$script:hcFixture.agent_exe}
    [pscustomobject]@{ExecutablePath=$path;CommandLine=('agent --config "{0}"' -f $script:hcFixture.agent_config)}
}
function Start-Process {
    param($FilePath,$ArgumentList,$WindowStyle,[switch]$PassThru)
    $script:hcSpawned+=@{file=$FilePath;arguments=$ArgumentList;window=$WindowStyle}
    if($PassThru) { [pscustomobject]@{HasExited=$false} | Add-Member ScriptMethod Refresh {} -PassThru }
}
function Start-Sleep { param($Milliseconds) }
Start-HostControl (Join-Path $env:HC_TEST_ROOT 'launch.json')
@{started=@($script:hcSpawned)} | ConvertTo-Json -Depth 6 | Set-Content (Join-Path $env:HC_TEST_ROOT 'observed.json')
'''
            result = subprocess.run([shell, '-NoProfile', '-NonInteractive', '-Command', harness],
                                    env=env, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=20)
            report = root / 'kit/observed.json'
            return result, json.loads(report.read_text(encoding='utf-8-sig')) if report.exists() else None

    def test_new_agent_visible_paused_then_rdp(self):
        shells = ['pwsh'] + (['powershell.exe'] if os.name == 'nt' else [])
        for shell in shells:
            with self.subTest(shell=shell):
                result, data = self.invoke('new', shell)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual([p['file'] for p in data['started']], ['powershell.exe', 'mstsc.exe'])
                self.assertEqual(data['started'][0]['window'], 'Normal')
                self.assertNotIn('--enable-for', json.dumps(data))
                self.assertNotIn('RunAs', json.dumps(data))

    def test_existing_matching_agent_only_opens_rdp(self):
        result, data = self.invoke('existing')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([p['file'] for p in data['started']], ['mstsc.exe'])

    def test_wrong_process_or_offline_stops_before_launch(self):
        for scenario in ('wrong', 'offline'):
            with self.subTest(scenario=scenario):
                result, data = self.invoke(scenario)
                self.assertNotEqual(result.returncode, 0)
                self.assertIsNone(data)


if __name__ == '__main__':
    unittest.main()
