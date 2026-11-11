#!/usr/bin/env python3
"""Preview/apply scoped xrdp changes without collapsing repeated INI keys.

No service is restarted and no existing desktop session is closed. Python's
ConfigParser must not serialize sesman.ini: its repeated Xorg param= lines are
ordered program arguments, not duplicate settings to discard.
"""
import argparse
import difflib
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid

HEADER = re.compile(r'^\s*\[([^]\r\n]+)\]\s*(?:[;#].*)?$')
SETTING = re.compile(r'^(\s*)([^=;#\s][^=]*?)(\s*=\s*)(.*)$')


def sections(text):
    """Return section spans; keep repeated settings and their exact order."""
    lines = text.splitlines(keepends=True)
    spans = []
    for index, line in enumerate(lines):
        match = HEADER.match(line.rstrip('\r\n'))
        if match:
            if spans:
                spans[-1][2] = index
            spans.append([match[1].casefold(), index, len(lines)])
    return lines, spans


def values(text, section, key):
    lines, spans = sections(text)
    result = []
    for name, start, end in spans:
        if name == section.casefold():
            for line in lines[start + 1:end]:
                match = SETTING.match(line.rstrip('\r\n'))
                if match and match[2].strip().casefold() == key.casefold():
                    result.append(match[4].strip())
    return result


def edit_ini(text, changes):
    newline = '\r\n' if '\r\n' in text else '\n'
    for section, settings in changes.items():
        for key, value in settings.items():
            lines, spans = sections(text)
            matches = [span for span in spans if span[0] == section.casefold()]
            if len(matches) > 1:
                raise ValueError('Ambiguous repeated section: ' + section)
            if not matches:
                text += ('' if not text or text.endswith(('\n', '\r')) else newline)
                text += f'[{section}]{newline}'
                lines, spans = sections(text)
                matches = [spans[-1]]
            _, start, end = matches[0]
            hits = []
            for i in range(start + 1, end):
                match = SETTING.match(lines[i].rstrip('\r\n'))
                if match and match[2].strip().casefold() == key.casefold():
                    hits.append((i, match))
            if len(hits) > 1:
                raise ValueError('Ambiguous repeated setting: ' + section + '.' + key)
            if hits:
                i, match = hits[0]
                ending = lines[i][len(lines[i].rstrip('\r\n')):]
                # xrdp treats inline text as part of the value; do not pretend
                # semicolons inside a value are necessarily comments.
                lines[i] = match[1] + match[2] + match[3] + value + ending
            else:
                if end and not lines[end - 1].endswith(('\n', '\r')):
                    lines[end - 1] += newline
                lines.insert(end, f'{key}={value}{newline}')
            text = ''.join(lines)
    return text


def vpn4(value):
    address = ipaddress.ip_address(value)
    if address.version != 4 or address not in ipaddress.ip_network('100.64.0.0/10'):
        raise ValueError('Exact Tailscale IPv4 address required')
    return str(address)


def text_clipboard_supported(version_output):
    match = re.search(r'\bxrdp\s+(\d+)\.(\d+)\.(\d+)', version_output)
    # 0.9.24's published man page documents file,image restrictions. Do not
    # silently enable an unrestricted clipboard on an older/unknown package.
    return bool(match and tuple(map(int, match.groups())) >= (0, 9, 24))


def require_text_clipboard():
    binary = shutil.which('xrdp')
    if not binary and Path('/usr/sbin/xrdp').is_file():
        binary = '/usr/sbin/xrdp'
    if not binary:
        raise ValueError('Cannot verify installed xrdp clipboard support')
    result = subprocess.run([binary, '--version'], capture_output=True, text=True, timeout=10)
    if result.returncode or not text_clipboard_supported(result.stdout + result.stderr):
        raise ValueError('Text clipboard requires verified xrdp >= 0.9.24; no configuration changed')


def configure(xrdp, sesman, clipboard='text', server_ip=None):
    if clipboard not in ('disabled', 'text'):
        raise ValueError('Clipboard must be disabled or text')
    channels = {name: 'false' for name in
                ('cliprdr', 'rdpdr', 'rdpsnd', 'drdynvc', 'rail', 'xrdpvr', 'tcutils')}
    channels['cliprdr'] = 'true' if clipboard == 'text' else 'false'
    changes = {'Channels': channels}
    if server_ip:
        changes['Globals'] = {'port': 'tcp://' + vpn4(server_ip) + ':3389'}
    args = values(sesman, 'Xorg', 'param')
    if not args or not args[0].startswith('/'):
        raise ValueError('Broken Xorg executable/param list; restore a known-good sesman.ini backup first')
    restriction = 'file,image' if clipboard == 'text' else 'all'
    new_sesman = edit_ini(sesman, {'Security': {
        'RestrictInboundClipboard': restriction,
        'RestrictOutboundClipboard': restriction}})
    if values(new_sesman, 'Xorg', 'param') != args:
        raise ValueError('Xorg arguments changed; refusing to write')
    return edit_ini(xrdp, changes), new_sesman


def replace_checked(path, expected, data):
    """Atomic file replace preserving permissions; reject concurrent changes."""
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError('Expected a regular non-symlink configuration file: ' + str(path))
    original = path.stat()
    descriptor, temporary = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, stat.S_IMODE(original.st_mode))
        if os.name != 'nt':
            os.chown(temporary, original.st_uid, original.st_gid)
            # Retain SELinux labels and other metadata where supported.
            shutil.copystat(path, temporary)
        current = path.stat()
        if ((current.st_ino, current.st_mtime_ns, current.st_ctime_ns, current.st_size)
                != (original.st_ino, original.st_mtime_ns, original.st_ctime_ns, original.st_size)
                or path.read_bytes() != expected):
            raise ValueError('Configuration changed concurrently; re-read and plan again')
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def apply_changes(changes):
    """Back up all inputs before writing; roll back our own partial writes."""
    pending = [(Path(p), old, new) for p, old, new in changes if old != new]
    for p, old, _ in pending:
        if p.is_symlink() or p.read_bytes() != old:
            raise ValueError('Configuration changed since preview or is a symlink: ' + str(p))
    backups = []
    for p, old, _ in pending:
        backup = p.with_name(p.name + '.hostcontrol-' + uuid.uuid4().hex + '.bak')
        with backup.open('xb') as stream:
            stream.write(old)
        shutil.copystat(p, backup)
        if os.name != 'nt':
            original = p.stat()
            os.chown(backup, original.st_uid, original.st_gid)
        backups.append(str(backup))
    written = []
    try:
        for p, old, new in pending:
            replace_checked(p, old, new)
            written.append((p, old, new))
    except Exception as error:
        failures = []
        for p, old, new in reversed(written):
            try:
                replace_checked(p, new, old)
            except Exception:
                failures.append(str(p))
        if failures:
            raise RuntimeError('Rollback needs manual review: ' + ', '.join(failures)) from error
        raise
    return backups


def rdp_document(server_ip, username, clipboard):
    if not re.fullmatch(r'[a-z_][a-z0-9_-]*', username) or username == 'root':
        raise ValueError('Non-root desktop username required')
    if clipboard not in ('disabled', 'text'):
        raise ValueError('Clipboard must be disabled or text')
    lines = [f'full address:s:{vpn4(server_ip)}:3389', f'username:s:{username}',
             'screen mode id:i:2', 'session bpp:i:32', 'authentication level:i:2',
             'enablecredsspsupport:i:1', 'prompt for credentials:i:1',
             f'redirectclipboard:i:{int(clipboard == "text")}',
             'redirectprinters:i:0', 'redirectcomports:i:0', 'redirectsmartcards:i:0',
             'redirectwebauthn:i:0', 'redirectlocation:i:0', 'redirectdirectx:i:0',
             'drivestoredirect:s:', 'devicestoredirect:s:', 'usbdevicestoredirect:s:',
             'audiomode:i:2', 'audiocapturemode:i:0']
    return '\r\n'.join(lines) + '\r\n'


def diagnose(xrdp, sesman, log=''):
    """Return classifications, never raw logs, clipboard data or window titles."""
    findings = []
    args = values(sesman, 'Xorg', 'param')
    if not args or not args[0].startswith('/'):
        findings.append({'code': 'xorg_parameters_broken',
                         'action': 'Restore the ordered [Xorg] param= lines from a known-good backup.'})
    enabled = [v.casefold() for v in values(xrdp, 'Channels', 'cliprdr')] in (['true'], ['1'], ['yes'])
    if not enabled:
        findings.append({'code': 'clipboard_disabled',
                         'action': 'Request text clipboard explicitly, update the .rdp file and log out/reconnect.'})
    else:
        for direction in ('Inbound', 'Outbound'):
            restrictions = values(sesman, 'Security', 'Restrict' + direction + 'Clipboard')
            blocked = {item.strip().casefold() for value in restrictions for item in value.split(',')}
            if not ('all' in blocked or {'file', 'image'} <= blocked):
                findings.append({'code': 'clipboard_formats_unrestricted',
                                 'action': 'Restrict file and image clipboard formats in both directions before reconnecting.'})
                break
    # Only the latest attempt is relevant; old failures should not be reported
    # as current after a successful login.
    attempt = re.split(r'Received system login request|Performing login request', log)[-1]
    if re.search(r'pam_authenticate failed|AUTHFAIL|Authentication failure', attempt, re.I):
        findings.append({'code': 'authentication_failed',
                         'action': 'Use the dedicated desktop credentials, not the SSH root password.'})
    if re.search(r'Error calling exec|X server failed to start|X server could not be started', attempt, re.I):
        findings.append({'code': 'xorg_start_failed',
                         'action': 'Inspect Xorg parameters and its log; authentication may already have succeeded.'})
    return {'xorg_arguments_valid': bool(args and args[0].startswith('/')),
            'findings': findings, 'live_desktop_verified': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('configure', 'rdp', 'check'))
    parser.add_argument('--xrdp-ini', type=Path, default=Path('/etc/xrdp/xrdp.ini'))
    parser.add_argument('--sesman-ini', type=Path, default=Path('/etc/xrdp/sesman.ini'))
    parser.add_argument('--clipboard', choices=('disabled', 'text'), default='text')
    parser.add_argument('--server-ip')
    parser.add_argument('--desktop-user')
    parser.add_argument('--out', type=Path)
    parser.add_argument('--sesman-log', type=Path)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    if args.mode == 'rdp':
        if not args.out or not args.server_ip or not args.desktop_user:
            parser.error('rdp needs --out, --server-ip and --desktop-user')
        data = rdp_document(args.server_ip, args.desktop_user, args.clipboard).encode('utf-8')
        if args.apply:
            with args.out.open('xb') as stream:
                stream.write(data)
            print('Wrote new RDP connection file. No credentials were stored.')
        else:
            print(data.decode('utf-8'), end='')
            print('PLAN only. Use --apply to create the connection file.')
        return
    paths = (args.xrdp_ini, args.sesman_ini)
    if paths[0].resolve() == paths[1].resolve():
        parser.error('xrdp.ini and sesman.ini must be different files')
    raw = [p.read_bytes() for p in paths]
    documents = [data.decode('utf-8') for data in raw]
    if args.mode == 'check':
        log = args.sesman_log.read_text(encoding='utf-8', errors='replace') if args.sesman_log else ''
        report = diagnose(*documents, log)
        print(json.dumps(report, indent=2))
        if not report['xorg_arguments_valid']:
            raise SystemExit(1)
        return
    updated = configure(*documents, args.clipboard, args.server_ip)
    changes = [(p, old, new.encode('utf-8')) for p, old, new in zip(paths, raw, updated)]
    if args.apply:
        if args.clipboard == 'text':
            require_text_clipboard()
        backups = apply_changes(changes)
        print(json.dumps({'changed': bool(backups), 'backups': backups}, indent=2))
        print('No services restarted. Save work, log out of XFCE, then reconnect using the matching .rdp file.')
        if args.clipboard == 'text':
            print('Text clipboard enabled; file and image clipboard formats remain restricted.')
    else:
        for p, old, new in changes:
            sys.stdout.writelines(difflib.unified_diff(
                old.decode('utf-8').splitlines(True), new.decode('utf-8').splitlines(True),
                fromfile=str(p), tofile=str(p) + ' (planned)'))
        print('PLAN only. No files changed; use --apply after review.')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError) as error:
        print('Desktop configuration error: ' + str(error), file=sys.stderr)
        sys.exit(1)
