#!/usr/bin/env python3
"""Preview/apply an explicitly selected datacenter timezone on Linux.

No geolocation requests, clock setting, service restart or application termination.
The caller confirms the datacenter region; an existing timezone is not evidence
of physical location. Process inspection cannot prove Chromium's cached timezone.
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import xml.etree.ElementTree as ET


def timezone_name(value):
    if (not isinstance(value, str) or len(value) > 128
            or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_+-]*(?:/[A-Za-z0-9_+-]+)*', value)
            or (value != 'UTC' and '/' not in value)):
        raise ValueError('Use an IANA timezone such as America/Los_Angeles, not a UTC offset or city guess')
    return value


def run(command):
    result = subprocess.run(command, capture_output=True, text=True, timeout=15)
    if result.returncode:
        raise ValueError('Timezone command failed: ' + command[0])
    return result.stdout.strip()


def system_state(runner=run):
    properties = runner(['timedatectl', 'show', '--property=Timezone', '--property=NTPSynchronized',
                         '--property=NTP'])
    values = dict(line.split('=', 1) for line in properties.splitlines() if '=' in line)
    if not values.get('Timezone'):
        raise ValueError('Cannot read the current system timezone')
    return {'timezone': values['Timezone'], 'ntp_enabled': values.get('NTP') == 'yes',
            'ntp_synchronized': values.get('NTPSynchronized') == 'yes'}


def configure(target, apply=False, runner=run, uid=None):
    target = timezone_name(target)
    available = runner(['timedatectl', 'list-timezones']).splitlines()
    if target not in available:
        raise ValueError('Selected timezone is unavailable on this server; no settings changed')
    before = system_state(runner)
    changed = before['timezone'] != target
    if apply and changed:
        if (os.geteuid() if uid is None else uid) != 0:
            raise ValueError('--apply requires root to change the server timezone')
        runner(['timedatectl', 'set-timezone', target])
    after = system_state(runner) if apply else before
    if apply and after['timezone'] != target:
        raise ValueError('Timezone readback does not match; inspect the current setting before retrying')
    return {'mode': 'APPLIED' if apply else 'PLAN', 'before': before, 'target': target,
            'after': after, 'changed': bool(apply and changed),
            'would_change': changed, 'clock_or_ntp_modified': False,
            'services_restarted': False, 'application_timezone_verified': False}


def clock_overrides(xml):
    root = ET.fromstring(xml)
    return [p.get('value') for plugin in root.iter('property') if plugin.get('value') == 'clock'
            for p in plugin.iter('property')
            if p.get('name') in ('timezone', 'time-zone') and p.get('value')]


def session_state(username, target, proc=Path('/proc'), account=None):
    if account is None:
        import pwd
        account = pwd.getpwnam(username)
    if account.pw_uid == 0:
        raise ValueError('Inspect the non-root desktop account')
    counts = {'xfce4-session': 0, 'xfce4-panel': 0, 'claude-desktop': 0}
    overrides = []
    unreadable = 0
    for p in Path(proc).iterdir():
        if not p.name.isdigit():
            continue
        try:
            status = (p / 'status').read_text()
            uid = int(next(line.split()[1] for line in status.splitlines() if line.startswith('Uid:')))
            if uid != account.pw_uid:
                continue
            name = (p / 'comm').read_text().strip()
            if name not in counts:
                continue
            counts[name] += 1
            env = dict(item.split('=', 1) for item in
                       (p / 'environ').read_bytes().decode(errors='replace').split('\0') if '=' in item)
            if env.get('TZ'):
                overrides.append({'process': name, 'matches_target': env['TZ'] == target})
        except (OSError, ValueError, StopIteration):
            unreadable += 1
    panel = Path(account.pw_dir) / '.config/xfce4/xfconf/xfce-perchannel-xml/xfce4-panel.xml'
    clocks = clock_overrides(panel.read_text()) if panel.is_file() else []
    return {'process_counts': counts, 'tz_override_count': len(overrides),
            'conflicting_process_override': any(not x['matches_target'] for x in overrides),
            'clock_override_count': len(clocks), 'conflicting_clock_override': any(x != target for x in clocks),
            'unreadable_or_exited_processes': unreadable,
            'application_timezone_verified': False,
            'note': 'Refresh the timezone-check page; stale Chromium caches may require saving work and reopening Claude.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('check', 'configure'))
    parser.add_argument('--timezone', type=timezone_name)
    parser.add_argument('--desktop-user')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    if args.apply and args.mode != 'configure':
        parser.error('--apply is only valid with configure')
    if args.mode == 'configure':
        if not args.timezone:
            parser.error('Use the confirmed datacenter IANA --timezone; there is no local/automatic default')
        report = configure(args.timezone, args.apply)
        target = args.timezone
    else:
        report = system_state()
        target = args.timezone or report['timezone']
        report['matches_target'] = report['timezone'] == target
    if args.desktop_user:
        report['desktop'] = session_state(args.desktop_user, target)
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, KeyError, ET.ParseError, subprocess.SubprocessError) as error:
        print('Timezone configuration: ' + str(error), file=sys.stderr)
        sys.exit(1)
