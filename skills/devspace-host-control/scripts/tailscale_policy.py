#!/usr/bin/env python3
"""Plan, apply and read back an exact-device Tailscale policy. No third-party deps."""
import argparse
import copy
import getpass
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request


class PolicyError(ValueError):
    pass


def loads(text):
    """Read JSON/HuJSON comments and trailing commas without altering strings."""
    text = text.lstrip('\ufeff')
    clean, i, quoted, escaped = [], 0, False, False
    while i < len(text):
        char = text[i]
        if quoted:
            clean.append(char)
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == '"':
                quoted = False
            i += 1
        elif char == '"':
            quoted = True
            clean.append(char)
            i += 1
        elif text.startswith('//', i):
            end = text.find('\n', i)
            i = len(text) if end < 0 else end
            clean.append(' ')
        elif text.startswith('/*', i):
            end = text.find('*/', i + 2)
            if end < 0:
                raise PolicyError('Unterminated policy comment')
            clean.append(' ' + '\n' * text[i:end].count('\n'))
            i = end + 2
        else:
            clean.append(char)
            i += 1
    text = ''.join(clean)
    clean, quoted, escaped = [], False, False
    for i, char in enumerate(text):
        if not quoted and char == ',':
            tail = text[i + 1:].lstrip()
            if tail.startswith(('}', ']')):
                continue
        clean.append(char)
        if quoted:
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise PolicyError('Duplicate JSON key: ' + key)
            result[key] = value
        return result

    def invalid_constant(value):
        raise PolicyError('Invalid JSON constant: ' + value)

    document = json.loads(''.join(clean), object_pairs_hook=pairs,
                          parse_constant=invalid_constant)
    if not isinstance(document, dict):
        raise PolicyError('Expected a complete policy object including outer braces')
    return document


def canonical(document):
    return json.dumps(document, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def digest(document):
    return hashlib.sha256(canonical(document).encode('utf-8')).hexdigest()


def vpn(value):
    try:
        address = ipaddress.ip_address(value)
    except ValueError as error:
        raise PolicyError('An exact Tailscale IPv4 address is required') from error
    if address.version != 4 or address not in ipaddress.ip_network('100.64.0.0/10'):
        raise PolicyError('An exact Tailscale IPv4 address is required')
    return str(address)


def desired(windows_ip, server_ip, port=7443):
    windows_ip, server_ip = vpn(windows_ip), vpn(server_ip)
    if windows_ip == server_ip or not 1 <= port <= 65535:
        raise PolicyError('Two different devices and a valid agent port are required')
    return [
        {'src': [windows_ip + '/32'], 'dst': [server_ip + '/32'], 'ip': ['tcp:3389']},
        {'src': [server_ip + '/32'], 'dst': [windows_ip + '/32'], 'ip': ['tcp:' + str(port)]},
    ]


def selectors_disjoint(selectors, targets, hosts):
    if not isinstance(selectors, list) or not selectors:
        return False
    for selector in selectors:
        if not isinstance(selector, str):
            return False
        selector = hosts.get(selector, selector)
        try:
            network = ipaddress.ip_network(selector, strict=False)
        except (ValueError, TypeError):
            return False  # Groups/tags/autogroups cannot be resolved from policy alone.
        if network.version != 4:
            return False  # Paired devices may also have IPv6; IPv4 discovery cannot exclude a bypass.
        if any(ipaddress.ip_address(target) in network for target in targets):
            return False
    return True


def audit(document, windows_ip, server_ip, port=7443):
    required = desired(windows_ip, server_ip, port)
    grants = document.get('grants', [])
    if not isinstance(grants, list):
        raise PolicyError('grants must be an array')
    if document.get('autoApprovers'):
        raise PolicyError('Existing automatic route approvals require network review')
    if document.get('acls'):
        raise PolicyError('Remaining legacy ACLs require review; rules are additive')
    hosts = document.get('hosts', {})
    if not isinstance(hosts, dict):
        raise PolicyError('hosts must be an object')
    for i, grant in enumerate(grants):
        if grant in required:
            continue
        if (not isinstance(grant, dict)
                or not selectors_disjoint(grant.get('src'), [windows_ip, server_ip], hosts)
                or not selectors_disjoint(grant.get('dst'), [windows_ip, server_ip], hosts)):
            raise PolicyError('grants[%d] may affect paired devices; review it instead of deleting it' % i)
    for grant in required:
        if grants.count(grant) != 1:
            raise PolicyError('Expected each of the two exact grants exactly once')


def build_policy(document, windows_ip, server_ip, replace_default=False, port=7443):
    result = copy.deepcopy(document)
    required = desired(windows_ip, server_ip, port)
    removed = []
    defaults = {
        'grants': {'src': ['*'], 'dst': ['*'], 'ip': ['*']},
        'acls': {'action': 'accept', 'src': ['*'], 'dst': ['*:*']},
    }
    for field, default in defaults.items():
        rules = result.get(field, [])
        if not isinstance(rules, list):
            raise PolicyError(field + ' must be an array')
        if default in rules:
            if not replace_default:
                raise PolicyError('Default allow-all found. Review its tailnet-wide impact, then use --replace-default')
            removed.append(field + ': default allow-all')
            result[field] = [rule for rule in rules if rule != default]
    grants = result.setdefault('grants', [])
    for grant in required:
        if grant not in grants:
            grants.append(grant)
    audit(result, windows_ip, server_ip, port)
    return result, removed


def discover(status, server_name=None, server_ip=None):
    if status.get('BackendState') != 'Running':
        raise PolicyError('Tailscale login is required; complete it locally and rerun')
    local = status.get('Self') or {}
    if str(local.get('OS', '')).lower() != 'windows':
        raise PolicyError('Run discovery on the intended Windows computer')
    local_ips = [vpn(x) for x in local.get('TailscaleIPs', []) if ':' not in x]
    if len(local_ips) != 1:
        raise PolicyError('Cannot identify one Windows Tailscale IPv4 address')
    if not server_ip and not server_name:
        raise PolicyError('Specify the intended server name or exact Tailscale IP; do not guess a peer')
    matches = []
    for peer in (status.get('Peer') or {}).values():
        names = [peer.get('HostName'), (peer.get('DNSName') or '').rstrip('.')]
        names.append(names[1].split('.')[0])
        if server_name and server_name not in names:
            continue
        if server_ip and server_ip not in peer.get('TailscaleIPs', []):
            continue
        if peer.get('OS') == 'linux':
            matches.append(peer)
    if len(matches) != 1:
        raise PolicyError('Server selection is missing or ambiguous; ask the user which device to use')
    peer = matches[0]
    peer_ips = [vpn(x) for x in peer.get('TailscaleIPs', []) if ':' not in x]
    if len(peer_ips) != 1:
        raise PolicyError('Cannot identify one server Tailscale IPv4 address')
    return {'windows_ip': local_ips[0], 'server_ip': peer_ips[0],
            'server_name': peer.get('HostName'), 'server_online': bool(peer.get('Online')),
            'tailnet': (status.get('CurrentTailnet') or {}).get('Name')}


def write_new(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8', newline='\n') as stream:
        stream.write(text)
    if os.name != 'nt':
        path.chmod(0o600)


def read_status(executable=None, status_file=None):
    if status_file:
        return json.loads(Path(status_file).read_text(encoding='utf-8-sig'))
    executable = executable or shutil.which('tailscale')
    if not executable and os.name == 'nt':
        candidate = Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'Tailscale/tailscale.exe'
        if candidate.is_file():
            executable = str(candidate)
    if not executable:
        raise PolicyError('Install/sign in to Tailscale first, or provide its executable path')
    try:
        result = subprocess.run([executable, 'status', '--json'], capture_output=True,
                                text=True, encoding='utf-8', timeout=20)
    except FileNotFoundError:
        raise PolicyError('Tailscale executable was not found') from None
    return json.loads(result.stdout)


def create_plan(raw, out_dir, windows_ip, server_ip, replace_default=False, port=7443):
    original = loads(raw)
    candidate, removed = build_policy(original, windows_ip, server_ip, replace_default, port)
    root = Path(out_dir)
    root.mkdir(parents=True, exist_ok=False)
    if os.name != 'nt':
        root.chmod(0o700)
    meta = {'windows_ip': vpn(windows_ip), 'server_ip': vpn(server_ip),
            'port': port, 'original_sha256': digest(original),
            'candidate_sha256': digest(candidate), 'removed': removed,
            'status': 'planned', 'live_verified': False}
    write_new(root / 'original.hujson', raw)
    write_new(root / 'policy.json', json.dumps(candidate, indent=2, ensure_ascii=False) + '\n')
    write_new(root / 'plan.json', json.dumps(meta, indent=2) + '\n')
    return meta


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise PolicyError('API redirect refused; credentials were not forwarded')


class API:
    def __init__(self, tailnet, token):
        self.url = 'https://api.tailscale.com/api/v2/tailnet/' + urllib.parse.quote(tailnet, safe='') + '/acl'
        self.token = token
        self.opener = urllib.request.build_opener(NoRedirect())

    def request(self, method='GET', suffix='', document=None, etag=None):
        headers = {'Accept': 'application/json', 'Authorization': 'Bearer ' + self.token}
        data = None
        if document is not None:
            data = json.dumps(document).encode('utf-8')
            headers['Content-Type'] = 'application/json'
        if etag:
            headers['If-Match'] = etag
        request = urllib.request.Request(self.url + suffix, data=data, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=30) as response:
                raw = response.read().decode('utf-8')
                return loads(raw) if raw.strip() else {}, response.headers.get('ETag')
        except urllib.error.HTTPError as error:
            if error.code == 412:
                raise PolicyError('Policy changed concurrently. Fetch it again and make a new plan; no overwrite attempted') from None
            raise PolicyError('Tailscale API returned HTTP %d; check access/scopes or use the authenticated browser' % error.code) from None
        except urllib.error.URLError:
            raise PolicyError('API result is unknown. Read back the live policy before considering another write') from None

    def get(self):
        return self.request()

    def validate(self, document):
        response, _ = self.request('POST', '/validate', document)
        if response:
            raise PolicyError('Server policy validation returned errors/warnings; review before applying')

    def set(self, document, etag):
        if not etag:
            raise PolicyError('Missing ETag; refusing an unconditional policy write')
        return self.request('POST', document=document, etag=etag)


def verify_plan(plan_dir, live):
    root = Path(plan_dir)
    meta = json.loads((root / 'plan.json').read_text(encoding='utf-8'))
    candidate = loads((root / 'policy.json').read_text(encoding='utf-8'))
    if digest(candidate) != meta['candidate_sha256']:
        raise PolicyError('Planned policy was changed; generate a new plan')
    if canonical(live) != canonical(candidate):
        raise PolicyError('Live policy differs from the planned complete policy; it is not verified')
    audit(live, meta['windows_ip'], meta['server_ip'], meta['port'])
    return meta


def apply_plan(api, plan_dir):
    root = Path(plan_dir)
    meta = json.loads((root / 'plan.json').read_text(encoding='utf-8'))
    candidate = loads((root / 'policy.json').read_text(encoding='utf-8'))
    verify_plan(root, candidate)
    live, etag = api.get()
    if canonical(live) == canonical(candidate):
        return {'status': 'verified', 'changed': False, 'sha256': digest(live)}
    if digest(live) != meta['original_sha256']:
        raise PolicyError('Live policy changed since planning; no write attempted')
    api.validate(candidate)
    # Exactly one conditional write. Never retry a POST after an unknown outcome.
    api.set(candidate, etag)
    readback, _ = api.get()
    verify_plan(root, readback)
    return {'status': 'verified', 'changed': True, 'sha256': digest(readback)}


def sync(api, status, out_dir, server_name=None, server_ip=None, replace_default=False, port=7443):
    devices = discover(status, server_name, server_ip)
    if not devices['server_online']:
        raise PolicyError('The selected server is offline; reconnect it before synchronizing policy')
    original, _ = api.get()
    create_plan(json.dumps(original, indent=2), out_dir, devices['windows_ip'],
                devices['server_ip'], replace_default, port)
    result = apply_plan(api, out_dir)
    result['devices'] = devices
    result['plan_dir'] = str(Path(out_dir).resolve())
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='mode', required=True)
    d = sub.add_parser('discover')
    d.add_argument('--status-file')
    d.add_argument('--tailscale')
    d.add_argument('--server-name')
    d.add_argument('--server-ip')
    for name in ('fetch', 'apply', 'sync'):
        p = sub.add_parser(name)
        p.add_argument('--tailnet', required=name != 'sync')
        p.add_argument('--token-env', default='HOSTCONTROL_TS_TOKEN')
        p.add_argument('--prompt-token', action='store_true')
        if name == 'fetch':
            p.add_argument('--out', required=True)
        elif name == 'apply':
            p.add_argument('--plan-dir', required=True)
        else:
            p.add_argument('--server-name')
            p.add_argument('--server-ip')
            p.add_argument('--tailscale')
            p.add_argument('--out-dir', required=True)
            p.add_argument('--replace-default', action='store_true')
            p.add_argument('--port', type=int, default=7443)
    p = sub.add_parser('plan')
    p.add_argument('--current', required=True)
    p.add_argument('--windows-ip', required=True)
    p.add_argument('--server-ip', required=True)
    p.add_argument('--port', type=int, default=7443)
    p.add_argument('--replace-default', action='store_true')
    p.add_argument('--out-dir', required=True)
    p = sub.add_parser('audit')
    p.add_argument('--current', required=True)
    p.add_argument('--windows-ip', required=True)
    p.add_argument('--server-ip', required=True)
    p.add_argument('--port', type=int, default=7443)
    p = sub.add_parser('verify')
    p.add_argument('--current', required=True)
    p.add_argument('--plan-dir', required=True)
    args = parser.parse_args()
    if args.mode == 'discover':
        print(json.dumps(discover(read_status(args.tailscale, args.status_file),
                                  args.server_name, args.server_ip), indent=2))
    elif args.mode == 'plan':
        raw = Path(args.current).read_text(encoding='utf-8-sig')
        meta = create_plan(raw, args.out_dir, args.windows_ip, args.server_ip,
                           args.replace_default, args.port)
        print(json.dumps(meta, indent=2))
    elif args.mode == 'audit':
        document = loads(Path(args.current).read_text(encoding='utf-8-sig'))
        audit(document, args.windows_ip, args.server_ip, args.port)
        print(json.dumps({'status': 'checked', 'live_verified': False, 'sha256': digest(document)}))
    elif args.mode == 'verify':
        live = loads(Path(args.current).read_text(encoding='utf-8-sig'))
        meta = verify_plan(args.plan_dir, live)
        print(json.dumps({'status': 'verified', 'sha256': digest(live),
                          'windows_ip': meta['windows_ip'], 'server_ip': meta['server_ip']}))
    else:
        sync_status = None
        if args.mode == 'sync':
            sync_status = read_status(args.tailscale)
            actual_tailnet = discover(sync_status, args.server_name, args.server_ip)['tailnet']
            if not actual_tailnet:
                raise PolicyError('Cannot determine the current tailnet; inspect membership before applying policy')
            if args.tailnet and args.tailnet != actual_tailnet:
                raise PolicyError('Requested tailnet differs from the paired Windows network; no API write attempted')
            args.tailnet = actual_tailnet
        token = os.environ.get(args.token_env)
        if not token and args.prompt_token:
            if not sys.stdin.isatty():
                raise PolicyError('Token prompt requires a local interactive terminal, never chat or a pipe')
            token = getpass.getpass('Tailscale policy API token (not saved): ')
        if not token:
            raise PolicyError('API authorization unavailable. Use the signed-in admin browser, or enter a token locally')
        api = API(args.tailnet, token)
        if args.mode == 'fetch':
            live, _ = api.get()
            write_new(args.out, json.dumps(live, indent=2, ensure_ascii=False) + '\n')
            print(json.dumps({'status': 'fetched', 'sha256': digest(live)}))
        elif args.mode == 'apply':
            print(json.dumps(apply_plan(api, args.plan_dir)))
        else:
            print(json.dumps(sync(api, sync_status, args.out_dir,
                                  args.server_name, args.server_ip, args.replace_default, args.port)))


if __name__ == '__main__':
    try:
        main()
    except (PolicyError, OSError, json.JSONDecodeError, subprocess.TimeoutExpired) as error:
        print(json.dumps({'status': 'needs_attention', 'message': str(error)}, ensure_ascii=False), file=sys.stderr)
        sys.exit(2)
