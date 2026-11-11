import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

import timezone_config as tz


class FakeServer:
    def __init__(self, zone='America/New_York', readback_wrong=False):
        self.zone = zone
        self.readback_wrong = readback_wrong
        self.calls = []

    def __call__(self, command):
        self.calls.append(command)
        if command == ['timedatectl', 'list-timezones']:
            return 'America/Los_Angeles\nAmerica/New_York\nEurope/Berlin'
        if command[1] == 'set-timezone':
            if not self.readback_wrong:
                self.zone = command[2]
            return ''
        return 'Timezone=' + self.zone + '\nNTP=yes\nNTPSynchronized=yes'


class TimezoneTests(unittest.TestCase):
    def test_no_local_default_and_no_city_offset_or_argument_guess(self):
        for value in (None, '', 'Los Angeles', 'PST', 'UTC-8', '../America/Los_Angeles',
                      '/etc/localtime', '-x', 'America/Los_Angeles\nUTC'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                tz.timezone_name(value)
        self.assertEqual(tz.timezone_name('America/Los_Angeles'), 'America/Los_Angeles')
        with self.assertRaises(ValueError):
            tz.configure(None, runner=FakeServer(), uid=1001)

    def test_preview_does_not_change_server_or_require_elevation(self):
        server = FakeServer()
        result = tz.configure('America/Los_Angeles', runner=server, uid=1001)
        self.assertEqual(server.zone, 'America/New_York')
        self.assertTrue(result['would_change'])
        self.assertFalse(result['changed'])
        self.assertFalse(any(c[1] == 'set-timezone' for c in server.calls))

    def test_apply_reads_back_and_repeated_apply_is_noop(self):
        server = FakeServer()
        result = tz.configure('America/Los_Angeles', True, server, uid=0)
        self.assertTrue(result['changed'])
        self.assertTrue(result['after']['ntp_synchronized'])
        self.assertFalse(result['clock_or_ntp_modified'])
        self.assertFalse(result['application_timezone_verified'])
        self.assertFalse(tz.configure('America/Los_Angeles', True, server, uid=0)['changed'])
        self.assertEqual([c for c in server.calls if c[1] == 'set-timezone'],
                         [['timedatectl', 'set-timezone', 'America/Los_Angeles']])

    def test_unavailable_zone_or_unprivileged_apply_does_not_write(self):
        for zone, uid in (('Europe/Paris', 0), ('America/Los_Angeles', 1001)):
            server = FakeServer()
            with self.assertRaises(ValueError):
                tz.configure(zone, True, server, uid=uid)
            self.assertFalse(any(c[1] == 'set-timezone' for c in server.calls))

    def test_wrong_readback_is_reported_without_retry(self):
        server = FakeServer(readback_wrong=True)
        with self.assertRaisesRegex(ValueError, 'readback'):
            tz.configure('America/Los_Angeles', True, server, uid=0)
        self.assertEqual(len([c for c in server.calls if c[1] == 'set-timezone']), 1)

    def test_clock_and_process_overrides_do_not_claim_cached_app_verified(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            proc = root / 'proc'
            proc.mkdir()
            p = proc / '123'
            p.mkdir()
            (p / 'status').write_text('Uid:\t1001\t1001\t1001\t1001\n')
            (p / 'comm').write_text('claude-desktop\n')
            (p / 'environ').write_bytes(b'TZ=America/New_York\0PRIVATE_VALUE=not-for-output\0')
            config = root / '.config/xfce4/xfconf/xfce-perchannel-xml/xfce4-panel.xml'
            config.parent.mkdir(parents=True)
            config.write_text('<channel><property name="plugin-1" value="clock">'
                              '<property name="timezone" value="Europe/Berlin"/></property></channel>')
            account = SimpleNamespace(pw_uid=1001, pw_dir=str(root))
            result = tz.session_state('worker', 'America/Los_Angeles', proc, account)
            self.assertTrue(result['conflicting_process_override'])
            self.assertTrue(result['conflicting_clock_override'])
            self.assertFalse(result['application_timezone_verified'])
            self.assertNotIn('not-for-output', json.dumps(result))
            (p / 'environ').write_bytes(b'LANG=en_US.UTF-8\0')
            config.write_text('<channel><property name="plugin-1" value="clock"/></channel>')
            result = tz.session_state('worker', 'America/Los_Angeles', proc, account)
            self.assertFalse(result['conflicting_process_override'])
            self.assertFalse(result['conflicting_clock_override'])


if __name__ == '__main__':
    unittest.main()
