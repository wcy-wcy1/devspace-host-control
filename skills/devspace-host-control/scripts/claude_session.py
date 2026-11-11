#!/usr/bin/python3
"""Start Claude in this user's XFCE session; no credentials or remote commands."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

SESSION_KEYS = ('DISPLAY', 'XAUTHORITY', 'DBUS_SESSION_BUS_ADDRESS',
                'XDG_RUNTIME_DIR', 'XDG_SESSION_TYPE', 'GNOME_KEYRING_CONTROL')


def processes(root, uid):
    for path in Path(root).iterdir():
        if not path.name.isdigit():
            continue
        try:
            status = (path / 'status').read_text()
            owner = next(line.split()[1] for line in status.splitlines() if line.startswith('Uid:'))
            if int(owner) == uid:
                yield path, (path / 'comm').read_text().strip()
        except (OSError, StopIteration, ValueError):
            continue  # A process may exit while being inspected.


def session_environment(root, uid, display=None):
    matches = []
    for path, name in processes(root, uid):
        if name != 'xfce4-session':
            continue
        try:
            env = dict(item.split('=', 1) for item in
                       (path / 'environ').read_bytes().decode('utf-8', errors='replace').split('\0')
                       if '=' in item)
        except OSError:
            continue
        if env.get('DISPLAY') and (not display or env['DISPLAY'].removesuffix('.0') == display.removesuffix('.0')):
            matches.append({key: env[key] for key in SESSION_KEYS if key in env})
    if len(matches) != 1:
        raise ValueError('Cannot identify exactly one matching XFCE session; log in to the intended desktop')
    return matches[0]


def start(root, uid, environment, executable, spawn=subprocess.Popen):
    if any(name == 'claude-desktop' for _, name in processes(root, uid)):
        return 'already_running'
    env = dict(environment)
    env.update(session_environment(root, uid, env.get('DISPLAY')))
    spawn([executable], env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
          stderr=subprocess.DEVNULL, start_new_session=True)
    return 'started'


def main():
    if os.getuid() == 0:
        raise ValueError('Claude must run as the desktop user, never root')
    executable = shutil.which('claude-desktop', path='/usr/local/bin:/usr/bin:/bin')
    if not executable:
        raise ValueError('Claude Desktop is not installed')
    for attempt in range(20):
        try:
            print(start('/proc', os.getuid(), os.environ, executable))
            return
        except ValueError:
            if attempt == 19:
                raise
            time.sleep(0.5)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError) as error:
        print('Claude session launch: ' + str(error), file=sys.stderr)
        sys.exit(1)
