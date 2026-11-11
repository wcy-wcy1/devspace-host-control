#!/usr/bin/env python3
"""Prepare the intended user's XFCE/keyring session without credentials."""
import argparse
import os
from pathlib import Path
import re
import sys

from desktop_config import apply_changes

SESSION = '''#!/bin/sh
# PAM must unlock the login keyring using the desktop login password.
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
export GNOME_KEYRING_CONTROL="$XDG_RUNTIME_DIR/keyring"
if command -v gnome-keyring-daemon >/dev/null 2>&1; then
    eval "$(gnome-keyring-daemon --start --components=secrets,pkcs11)"
    export GNOME_KEYRING_CONTROL
fi
exec startxfce4
'''


def centos_pam(text):
    # A successful "include password-auth" can return before optional keyring
    # auth runs. A substack retains success within that stack, then continues.
    pattern = r'(?m)^(auth\s+)include(\s+password-auth\s*)$'
    text, count = re.subn(pattern, r'\1substack\2', text)
    if not count and not re.search(r'(?m)^auth\s+substack\s+password-auth\s*$', text):
        raise ValueError('Unrecognized CentOS PAM stack; inspect manually')
    for kind, suffix in (('auth', ''), ('session', ' auto_start')):
        if not re.search(r'(?m)^' + kind + r'\s+\S+\s+pam_gnome_keyring\.so(?:\s|$)', text):
            text += ('' if text.endswith('\n') else '\n')
            text += f'{kind} optional pam_gnome_keyring.so{suffix}\n'
    return text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--desktop-user', required=True)
    parser.add_argument('--centos-pam', action='store_true')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    import pwd  # Linux-only execution; pure PAM/session helpers remain testable elsewhere.
    account = pwd.getpwnam(args.desktop_user)
    if account.pw_uid == 0:
        parser.error('Use a non-root desktop account')
    home = Path(account.pw_dir)
    if not home.is_dir() or home.is_symlink() or home.stat().st_uid != account.pw_uid:
        parser.error('The existing home must belong to the desktop user')
    if not args.apply:
        print('PLAN: XFCE session and owned config parents; CentOS PAM:', args.centos_pam)
        return
    if os.geteuid() != 0:
        parser.error('--apply requires root')
    # install -d on a deep path alone can leave root-owned intermediate parents.
    for relative in ('.config', '.local', '.local/share', '.cache'):
        path = home / relative
        if path.is_symlink():
            raise ValueError('Refusing symlinked session directory: ' + str(path))
        if not path.exists():
            path.mkdir(mode=0o700)
            os.chown(path, account.pw_uid, account.pw_gid)
        elif path.stat().st_uid != account.pw_uid:
            raise ValueError('Existing session directory has the wrong owner: ' + str(path))
    if args.centos_pam:
        if not Path('/usr/lib64/security/pam_gnome_keyring.so').is_file():
            raise ValueError('Install gnome-keyring-pam before changing PAM')
        p = Path('/etc/pam.d/xrdp-sesman')
        old = p.read_bytes()
        apply_changes([(p, old, centos_pam(old.decode('utf-8')).encode('utf-8'))])
    for name in ('.Xclients', '.xsession'):
        path = home / name
        if path.exists() or path.is_symlink():
            apply_changes([(path, path.read_bytes(), SESSION.encode('utf-8'))])
        else:
            with path.open('xb') as stream:
                stream.write(SESSION.encode('utf-8'))
        os.chown(path, account.pw_uid, account.pw_gid)
        path.chmod(0o700)
    print('Session prepared. Existing desktop sessions were not restarted.')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError) as error:
        print('Session preparation error: ' + str(error), file=sys.stderr)
        sys.exit(1)
