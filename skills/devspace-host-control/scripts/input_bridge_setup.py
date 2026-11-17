#!/usr/bin/env python3
"""Prepare the optional input receiver for one Linux desktop user. No restarts."""
import argparse
import json
import os
from pathlib import Path
import sys

from desktop_config import apply_changes
from launcher import desktop_entry

SCRIPTS = Path(__file__).resolve().parent


def configure(username, key=None, apply=False):
    import pwd
    account = pwd.getpwnam(username)
    home = Path(account.pw_dir)
    if account.pw_uid == 0 or home.is_symlink() or home.stat().st_uid != account.pw_uid:
        raise ValueError('Use the intended non-root desktop account')
    helper = home / '.local/bin/hostcontrol-input-bridge.py'
    entry = home / '.config/autostart/hostcontrol-input-bridge.desktop'
    config = home / '.config/hostcontrol/input-bridge.json'
    if not apply:
        return {'mode': 'PLAN', 'helper': str(helper), 'autostart': str(entry),
                'restart_required_for_existing_claude': True, 'network_ports_added': False}
    if os.geteuid() != 0:
        raise ValueError('Setup requires root; the receiver never runs as root')
    if key is None or len(key) != 32:
        raise ValueError('A 32-byte bridge pairing key is required via stdin')
    for relative in ('.local', '.local/bin', '.config', '.config/autostart', '.config/hostcontrol'):
        p = home / relative
        if p.is_symlink() or (p.exists() and p.stat().st_uid != account.pw_uid):
            raise ValueError('Unsafe session directory')
        if not p.exists():
            p.mkdir(mode=0o700)
            os.chown(p, account.pw_uid, account.pw_gid)
    body = desktop_entry(helper).replace('HostControl Claude Desktop', 'HostControl Claude input bridge')
    body = body.replace('Start Claude in the paired remote desktop', 'Receive explicitly paired local draft text')
    body = body.replace(str(helper) + '"', str(helper) + '" receive')
    config_bytes = (json.dumps({'key': key.hex()}) + '\n').encode()
    if config.exists() and json.loads(config.read_text()) != json.loads(config_bytes):
        raise ValueError('Existing pairing differs; preserve it or explicitly remove it before re-pairing')
    for p, data in ((config, config_bytes), (helper, (SCRIPTS / 'input_bridge.py').read_bytes()),
                    (entry, body.encode())):
        if p.is_symlink() or (p.exists() and p.stat().st_uid != account.pw_uid):
            raise ValueError('Unsafe receiver file')
        if p.exists():
            apply_changes([(p, p.read_bytes(), data)])
        else:
            with p.open('xb') as stream:
                stream.write(data)
        p.chmod(0o600)
        os.chown(p, account.pw_uid, account.pw_gid)
    return {'mode': 'APPLIED', 'services_restarted': False, 'will_submit': False,
            'note': 'Reopen Claude after saving work so its accessibility flag takes effect.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--desktop-user', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    try:
        key = bytes.fromhex(sys.stdin.readline().strip()) if args.apply else None
        print(json.dumps(configure(args.desktop_user, key, args.apply), indent=2))
    except (ValueError, OSError, KeyError) as error:
        print('Input receiver setup: ' + str(error), file=sys.stderr)
        sys.exit(1)
