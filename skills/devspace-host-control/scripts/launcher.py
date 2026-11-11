#!/usr/bin/env python3
"""Generate a double-click Windows kit and enable Linux login/reconnect launch.

Only paths and connection settings are generated; no password, key or control
URL is copied. Linux configuration previews by default and never restarts RDP.
"""
import argparse
import json
import os
from pathlib import Path
import re
import shlex
import sys

from desktop_config import apply_changes, rdp_document, values, vpn4

SCRIPTS = Path(__file__).resolve().parent
CMD = '''@echo off
setlocal
set "PSModulePath="
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-hostcontrol.ps1"
if errorlevel 1 pause
'''
AGENT_WRAPPER = '''$ErrorActionPreference = 'Stop'
$hcLaunch = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'launch.json') -Raw -Encoding UTF8 | ConvertFrom-Json
& (Join-Path $PSScriptRoot 'start-windows-agent.ps1') -Config $hcLaunch.agent_config -AgentExe $hcLaunch.agent_exe
'''


def generate(destination, agent_config, agent_exe, server_ip, username, clipboard='text'):
    config = Path(agent_config).resolve(strict=True)
    executable = Path(agent_exe).resolve(strict=True)
    agent = json.loads(config.read_text(encoding='utf-8-sig'))
    server_ip = vpn4(server_ip)
    if agent.get('peer_ip') != server_ip:
        raise ValueError('Server IP differs from the paired agent configuration')
    match = re.fullmatch(r'(100\.[0-9.]+):(\d+)', agent.get('listen', ''))
    if not match or not 1 <= int(match[2]) <= 65535 or vpn4(match[1]) == server_ip:
        raise ValueError('An exact, distinct Windows Tailscale listener is required')
    rdp = rdp_document(server_ip, username, clipboard)
    root = Path(destination)
    root.mkdir(parents=True, exist_ok=False)
    launch = {'schema_version': 1, 'agent_config': str(config), 'agent_exe': str(executable),
              'listen_ip': match[1], 'listen_port': int(match[2]), 'clipboard': clipboard}
    files = {'Start-Claude.cmd': CMD.replace('\n', '\r\n').encode('ascii'),
             'start-agent.ps1': AGENT_WRAPPER.encode('utf-8'),
             'launch.json': (json.dumps(launch, indent=2, ensure_ascii=False) + '\n').encode('utf-8'),
             'server-desktop.rdp': rdp.encode('utf-8')}
    for name in ('start-hostcontrol.ps1', 'start-windows-agent.ps1'):
        files[name] = (SCRIPTS / name).read_bytes()
    for name, data in files.items():
        (root / name).write_bytes(data)
    return root / 'Start-Claude.cmd'


def reconnect_hook(text, username, helper):
    if not re.fullmatch(r'[a-z_][a-z0-9_-]*', username) or username == 'root':
        raise ValueError('A non-root desktop user is required')
    first, separator, rest = text.partition('\n')
    if not separator or first.rstrip('\r') not in ('#!/bin/sh', '#!/bin/bash', '#!/usr/bin/bash'):
        raise ValueError('Unrecognized reconnect script; inspect it before modifying')
    begin = '# BEGIN HOSTCONTROL CLAUDE ' + username
    end = '# END HOSTCONTROL CLAUDE ' + username
    pattern = re.compile(r'(?m)^' + re.escape(begin) + r'\n.*?^' + re.escape(end) + r'\n', re.S)
    if begin in rest or end in rest:
        if len(pattern.findall(rest)) != 1:
            raise ValueError('Ambiguous HostControl reconnect hook')
        rest = pattern.sub('', rest)
    hook = (begin + '\n' + 'if [ "$(id -un)" = ' + shlex.quote(username) + ' ]; then\n'
            '    /usr/bin/python3 ' + shlex.quote(str(helper)) + ' >/dev/null 2>&1 &\nfi\n' + end + '\n')
    return first + '\n' + hook + rest


def desktop_entry(helper):
    escaped = str(helper).replace('\\', '\\\\').replace('"', '\\"').replace('`', '\\`').replace('$', '\\$')
    if '\n' in escaped or '\r' in escaped:
        raise ValueError('Invalid desktop helper path')
    return ('[Desktop Entry]\nType=Application\nName=HostControl Claude Desktop\n'
            'Comment=Start Claude in the paired remote desktop\n'
            'Exec=/usr/bin/python3 "' + escaped + '"\nTerminal=false\nOnlyShowIn=XFCE;\n')


def configure_linux(username, sesman, apply=False):
    import pwd
    account = pwd.getpwnam(username)
    if account.pw_uid == 0:
        raise ValueError('A non-root desktop user is required')
    home = Path(account.pw_dir)
    if home.is_symlink() or not home.is_dir() or home.stat().st_uid != account.pw_uid:
        raise ValueError('Desktop home ownership must be correct')
    script = values(Path(sesman).read_text(encoding='utf-8'), 'Globals', 'ReconnectScript')
    if len(script) > 1:
        raise ValueError('Ambiguous ReconnectScript')
    reconnect = Path(script[0] if script else 'reconnectwm.sh')
    if not reconnect.is_absolute():
        reconnect = Path(sesman).parent / reconnect
    missing_reconnect = not reconnect.exists()
    if (reconnect.is_symlink()
            or (missing_reconnect and reconnect != Path(sesman).parent / 'reconnectwm.sh')
            or (not missing_reconnect and (not reconnect.is_file() or reconnect.stat().st_uid != 0))):
        raise ValueError('Expected the installed, root-owned non-symlink reconnect script')
    helper = home / '.local/bin/hostcontrol-start-claude.py'
    autostart = home / '.config/autostart/hostcontrol-claude.desktop'
    old = b'#!/bin/sh\nexit 0\n' if missing_reconnect else reconnect.read_bytes()
    changed = reconnect_hook(old.decode('utf-8'), username, helper).encode('utf-8')
    if not apply:
        return {'mode': 'PLAN', 'new_login': str(autostart), 'reconnect': str(reconnect),
                'create_missing_default_reconnect': missing_reconnect}
    if os.geteuid() != 0:
        raise ValueError('Linux --apply requires root')
    # Touch only the chosen account and the scoped reconnect block.
    for relative in ('.local', '.local/bin', '.config', '.config/autostart'):
        p = home / relative
        if p.is_symlink() or (p.exists() and p.stat().st_uid != account.pw_uid):
            raise ValueError('Session directory is symlinked or has the wrong owner')
        if not p.exists():
            p.mkdir(mode=0o700)
            os.chown(p, account.pw_uid, account.pw_gid)
    for p, data in ((helper, (SCRIPTS / 'claude_session.py').read_bytes()),
                    (autostart, desktop_entry(helper).encode('utf-8'))):
        if p.exists() or p.is_symlink():
            if p.is_symlink() or p.stat().st_uid != account.pw_uid:
                raise ValueError('Existing launcher file is not owned by the desktop user')
            apply_changes([(p, p.read_bytes(), data)])
        else:
            with p.open('xb') as stream:
                stream.write(data)
        p.chmod(0o600)
        os.chown(p, account.pw_uid, account.pw_gid)
    if missing_reconnect:
        with reconnect.open('xb') as stream:
            stream.write(changed)
        reconnect.chmod(0o755)
    else:
        apply_changes([(reconnect, old, changed)])
    return {'mode': 'APPLIED', 'services_restarted': False,
            'note': 'Claude starts on the next XFCE login or RDP reconnect; no SSH credential is needed'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='mode', required=True)
    p = sub.add_parser('generate')
    for name in ('out-dir', 'agent-config', 'agent-exe', 'server-ip', 'desktop-user'):
        p.add_argument('--' + name, required=True)
    p.add_argument('--clipboard', choices=('text', 'disabled'), default='text')
    p = sub.add_parser('linux')
    p.add_argument('--desktop-user', required=True)
    p.add_argument('--sesman-ini', default='/etc/xrdp/sesman.ini')
    p.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    if args.mode == 'generate':
        path = generate(args.out_dir, args.agent_config, args.agent_exe, args.server_ip,
                        args.desktop_user, args.clipboard)
        print('Created double-click entry: ' + str(path))
        print('Agent starts visibly and paused; RDP uses normal desktop login. No credentials copied.')
    else:
        print(json.dumps(configure_linux(args.desktop_user, args.sesman_ini, args.apply), indent=2))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError) as error:
        print('Launcher configuration: ' + str(error), file=sys.stderr)
        sys.exit(1)
