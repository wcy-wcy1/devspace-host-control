import json
import os
from pathlib import Path
import stat
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import desktop_config as desktop
from linux_session import centos_pam

SCRIPTS = Path(__file__).parent
SESMAN = (SCRIPTS / 'tests/fixtures/sesman.ini').read_text(encoding='utf-8')
XRDP = '; custom setting stays intact\n[Globals]\nport=3389\n\n[Channels]\ncliprdr=false\nrdpdr=false\n'


class DesktopConfigurationTests(unittest.TestCase):
    def test_clipboard_change_preserves_xorg_and_xvnc_byte_for_byte(self):
        xrdp, sesman = desktop.configure(XRDP, SESMAN, 'text', '100.80.0.2')
        self.assertEqual(sesman[sesman.index('[Xorg]'):], SESMAN[SESMAN.index('[Xorg]'):])
        self.assertEqual(len(desktop.values(sesman, 'Xorg', 'param')), 8)
        self.assertEqual(desktop.values(xrdp, 'Globals', 'port'), ['tcp://100.80.0.2:3389'])
        self.assertEqual(desktop.values(sesman, 'Security', 'RestrictInboundClipboard'), ['file,image'])
        self.assertEqual(desktop.values(sesman, 'Security', 'RestrictOutboundClipboard'), ['file,image'])
        self.assertEqual(desktop.values(xrdp, 'Channels', 'rdpdr'), ['false'])

    def test_disable_and_enable_are_idempotent(self):
        for mode in ('disabled', 'text'):
            result = desktop.configure(XRDP, SESMAN, mode)
            self.assertEqual(desktop.configure(*result, mode), result)
        xrdp, sesman = desktop.configure(*desktop.configure(XRDP, SESMAN, 'text'), 'disabled')
        self.assertEqual(desktop.values(xrdp, 'Channels', 'cliprdr'), ['false'])
        self.assertEqual(desktop.values(sesman, 'Security', 'RestrictInboundClipboard'), ['all'])

    def test_preserves_crlf_comments_and_unrelated_sections(self):
        original = SESMAN.replace('\n', '\r\n')
        _, edited = desktop.configure(XRDP, original, 'text')
        self.assertEqual(edited[edited.index('[Xorg]'):], original[original.index('[Xorg]'):])
        self.assertNotIn('\n', edited.replace('\r\n', ''))
        self.assertIn('; Representative EL9', edited)

    def test_handles_missing_target_section_and_no_final_newline(self):
        edited = desktop.edit_ini('[Other]\nkey=value', {'Security': {'AllowRootLogin': 'false'}})
        self.assertEqual(desktop.values(edited, 'Other', 'key'), ['value'])
        self.assertEqual(desktop.values(edited, 'Security', 'AllowRootLogin'), ['false'])

    def test_rejects_ambiguous_target_key_or_section(self):
        for text in ('[Channels]\ncliprdr=true\ncliprdr=false\n',
                     '[Channels]\ncliprdr=true\n[channels]\ncliprdr=false\n'):
            with self.assertRaisesRegex(ValueError, 'Ambiguous'):
                desktop.edit_ini(text, {'Channels': {'cliprdr': 'true'}})

    def test_detects_real_failure_shape_before_writing(self):
        damaged = '[Xorg]\nparam=.xorgxrdp.%s.log\n'
        with self.assertRaisesRegex(ValueError, 'Broken Xorg'):
            desktop.configure(XRDP, damaged, 'text')
        report = desktop.diagnose(XRDP, damaged, 'login was successful\nX server failed to start')
        codes = {x['code'] for x in report['findings']}
        self.assertIn('xorg_parameters_broken', codes)
        self.assertIn('xorg_start_failed', codes)
        self.assertNotIn('authentication_failed', codes)
        self.assertFalse(report['live_desktop_verified'])

    def test_old_failure_does_not_override_latest_success(self):
        log = ('Received system login request\npam_authenticate failed\n'
               'Received system login request\nX server :10 is working')
        report = desktop.diagnose(XRDP, SESMAN, log)
        self.assertNotIn('authentication_failed', {x['code'] for x in report['findings']})

    def test_diagnostics_do_not_echo_log_contents(self):
        log = 'Received system login request\nAUTHFAIL password=secret-value\n'
        text = json.dumps(desktop.diagnose(XRDP, SESMAN, log))
        self.assertIn('authentication_failed', text)
        self.assertNotIn('secret-value', text)

    def test_diagnostics_detect_unrestricted_clipboard_formats(self):
        enabled = XRDP.replace('cliprdr=false', 'cliprdr=true')
        report = desktop.diagnose(enabled, SESMAN)
        self.assertIn('clipboard_formats_unrestricted', {x['code'] for x in report['findings']})
        xrdp, sesman = desktop.configure(XRDP, SESMAN, 'text')
        self.assertEqual(desktop.diagnose(xrdp, sesman)['findings'], [])

    def test_rdp_requires_exact_address_and_safe_user(self):
        for ip in ('0.0.0.0', '100.64.0.0/10', '::', '192.168.0.1'):
            with self.assertRaises(ValueError):
                desktop.rdp_document(ip, 'worker', 'text')
        for user in ('root', 'worker\nredirectclipboard:i:1', 'domain\\user'):
            with self.assertRaises(ValueError):
                desktop.rdp_document('100.80.0.2', user, 'text')

    def test_rdp_enables_only_requested_redirection(self):
        doc = desktop.rdp_document('100.80.0.2', 'worker', 'text')
        self.assertIn('redirectclipboard:i:1\r\n', doc)
        self.assertIn('drivestoredirect:s:\r\n', doc)
        self.assertIn('devicestoredirect:s:\r\n', doc)
        self.assertIn('redirectprinters:i:0', doc)
        self.assertNotIn('password', doc)
        self.assertIn('redirectclipboard:i:0', desktop.rdp_document('100.80.0.2', 'worker', 'disabled'))

    def test_default_server_clipboard_is_text_only(self):
        xrdp, sesman = desktop.configure(XRDP, SESMAN)
        self.assertEqual(desktop.values(xrdp, 'Channels', 'cliprdr'), ['true'])
        for key in ('RestrictInboundClipboard', 'RestrictOutboundClipboard'):
            self.assertEqual(desktop.values(sesman, 'Security', key), ['file,image'])
        self.assertEqual(desktop.values(xrdp, 'Channels', 'rdpdr'), ['false'])

    def test_default_generated_client_enables_clipboard(self):
        result = subprocess.run([sys.executable, str(SCRIPTS / 'desktop_config.py'), 'rdp',
                                 '--server-ip', '100.80.0.2', '--desktop-user', 'worker',
                                 '--out', 'unused-preview.rdp'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('redirectclipboard:i:1', result.stdout)

    def test_version_gate_rejects_unknown_or_old_xrdp(self):
        for value in ('xrdp 0.9.21', 'not an xrdp version', 'xrdp 0.8.99'):
            self.assertFalse(desktop.text_clipboard_supported(value))
        for value in ('xrdp 0.10.6.1\n build options', 'xrdp 0.9.24', 'xrdp 1.0.0'):
            self.assertTrue(desktop.text_clipboard_supported(value))

    def test_preview_makes_no_changes_or_backups(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            xrdp, sesman = root / 'xrdp.ini', root / 'sesman.ini'
            xrdp.write_text(XRDP, encoding='utf-8')
            sesman.write_text(SESMAN, encoding='utf-8')
            before = [p.read_bytes() for p in (xrdp, sesman)]
            result = subprocess.run([sys.executable, str(SCRIPTS / 'desktop_config.py'), 'configure',
                                     '--xrdp-ini', str(xrdp), '--sesman-ini', str(sesman),
                                     '--clipboard', 'text'], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(before, [p.read_bytes() for p in (xrdp, sesman)])
            self.assertEqual(len(list(root.iterdir())), 2)

    def test_backups_and_noop_apply(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'sesman.ini'
            old = SESMAN.encode('utf-8')
            path.write_bytes(old)
            path.chmod(0o600)
            new = desktop.configure(XRDP, SESMAN, 'text')[1].encode('utf-8')
            backups = desktop.apply_changes([(path, old, new)])
            self.assertEqual(Path(backups[0]).read_bytes(), old)
            self.assertEqual(path.read_bytes(), new)
            if os.name != 'nt':
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(desktop.apply_changes([(path, new, new)]), [])

    def test_concurrent_change_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'config.ini'
            path.write_bytes(b'external edit')
            with self.assertRaises(ValueError):
                desktop.apply_changes([(path, b'old', b'new')])
            self.assertEqual(path.read_bytes(), b'external edit')

    def test_second_file_failure_rolls_back_first(self):
        with tempfile.TemporaryDirectory() as folder:
            first, second = Path(folder) / 'first', Path(folder) / 'second'
            first.write_bytes(b'first old')
            second.write_bytes(b'second old')
            real_replace = desktop.replace_checked
            def fail_second(path, old, new):
                if Path(path) == second:
                    raise OSError('simulated disk failure')
                return real_replace(path, old, new)
            with mock.patch.object(desktop, 'replace_checked', side_effect=fail_second):
                with self.assertRaises(OSError):
                    desktop.apply_changes([(first, b'first old', b'first new'),
                                           (second, b'second old', b'second new')])
            self.assertEqual(first.read_bytes(), b'first old')
            self.assertEqual(second.read_bytes(), b'second old')

    def test_rdp_output_refuses_existing_file(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / 'server.rdp'
            out.write_bytes(b'user settings')
            result = subprocess.run([sys.executable, str(SCRIPTS / 'desktop_config.py'), 'rdp',
                                     '--server-ip', '100.80.0.2', '--desktop-user', 'worker',
                                     '--out', str(out), '--apply'], capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(out.read_bytes(), b'user settings')


@unittest.skipUnless(os.name == 'nt' and shutil.which('pwsh'), 'Windows PowerShell launcher test')
class WindowsLauncherTests(unittest.TestCase):
    def invoke(self, enable=None):
        # A fake foreground executable checks argument/environment handling;
        # never start the real desktop-control agent in an offline test.
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            config = root / 'agent.json'
            config.write_text(json.dumps({'session_minutes': 30}), encoding='utf-8')
            fake = root / 'fake-agent.ps1'
            report = root / 'observed.json'
            fake.write_text("""if ($args -contains '--check') { $global:LASTEXITCODE=0; return }
@{arguments=@($args); modulePathAbsent=([string]::IsNullOrEmpty($env:PSModulePath))} |
    ConvertTo-Json | Set-Content -LiteralPath $env:HOSTCONTROL_TEST_REPORT
$global:LASTEXITCODE=0
""", encoding='utf-8')
            launcher = str(SCRIPTS / 'start-windows-agent.ps1')
            env = dict(os.environ, HOSTCONTROL_TEST_REPORT=str(report))
            # Run through a wrapper to check restoration in the same process.
            command = ("$before=$env:PSModulePath; & $env:HOSTCONTROL_TEST_LAUNCHER "
                       "-Config $env:HOSTCONTROL_TEST_CONFIG -AgentExe $env:HOSTCONTROL_TEST_AGENT ")
            if enable is not None:
                command += '-EnableForMinutes ' + str(enable) + ' '
            command += "; if ($env:PSModulePath -ne $before) { throw 'Module path not restored' }"
            env.update(HOSTCONTROL_TEST_LAUNCHER=launcher, HOSTCONTROL_TEST_CONFIG=str(config),
                       HOSTCONTROL_TEST_AGENT=str(fake))
            result = subprocess.run(['pwsh', '-NoProfile', '-NonInteractive', '-Command',
                                     "$ErrorActionPreference='Stop'; " + command],
                                    capture_output=True, text=True, encoding='utf-8', errors='replace',
                                    env=env, timeout=20)
            data = json.loads(report.read_text(encoding='utf-8-sig')) if report.exists() else None
            return result, data

    def test_default_paused_and_child_module_path_isolated(self):
        result, data = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('--enable-for', data['arguments'])
        self.assertTrue(data['modulePathAbsent'])

    def test_explicit_bounded_enable(self):
        result, data = self.invoke(10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(data['arguments'][-2:], ['--enable-for', '10'])

    def test_excessive_authorization_does_not_launch(self):
        result, data = self.invoke(31)
        self.assertNotEqual(result.returncode, 0)
        self.assertIsNone(data)


class KeyringTests(unittest.TestCase):
    def test_centos_auth_continues_to_keyring_and_is_idempotent(self):
        old = ('# existing PAM policy\nauth include password-auth\n'
               'session required pam_selinux.so close\nsession include password-auth\n')
        new = centos_pam(old)
        self.assertIn('auth substack password-auth', new)
        self.assertIn('session required pam_selinux.so close', new)
        self.assertIn('auth optional pam_gnome_keyring.so', new)
        self.assertIn('session optional pam_gnome_keyring.so auto_start', new)
        self.assertEqual(centos_pam(new), new)

    def test_rejects_unrecognized_pam_stack(self):
        with self.assertRaises(ValueError):
            centos_pam('auth required a-custom-module.so\n')


if __name__ == '__main__':
    unittest.main()
