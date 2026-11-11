#!/usr/bin/env python3
"""Generate exact-IP host-control configuration. Never installs or opens ports."""
import argparse
import ipaddress
import json
import pathlib
import re
import sys

NETWORKS = (ipaddress.ip_network('100.64.0.0/10'), ipaddress.ip_network('fd7a:115c:a1e0::/48'))

def vpn(value):
    try:
        ip = ipaddress.ip_address(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError('exact Tailscale IP required') from exc
    if not any(ip in net for net in NETWORKS):
        raise argparse.ArgumentTypeError('address outside Tailscale ranges')
    return str(ip)

def fingerprint(value):
    if not re.fullmatch('[0-9a-f]{64}', value):
        raise argparse.ArgumentTypeError('lowercase certificate SHA256 required')
    return value

def authority(ip, port):
    return f'[{ip}]:{port}' if ':' in ip else f'{ip}:{port}'

def policy(windows_ip, ubuntu_ip, agent_port=7443):
    host = lambda x: x + ('/128' if ':' in x else '/32')
    return {'grants': [
        {'src': [host(windows_ip)], 'dst': [host(ubuntu_ip)], 'ip': ['tcp:3389']},
        {'src': [host(ubuntu_ip)], 'dst': [host(windows_ip)], 'ip': [f'tcp:{agent_port}']},
    ]}

def check_policy(document, windows_ip, ubuntu_ip, agent_port=7443):
    """Conservative complete-policy check: reject unknown or additional grants.

    Standard JSON only. This does not query Tailscale or prove deployed policy.
    """
    if document.get('acls'):
        raise ValueError('legacy ACLs present: review/remove broad allow rules before deployment')
    allowed = policy(windows_ip, ubuntu_ip, agent_port)['grants']
    actual = document.get('grants')
    if not isinstance(actual, list) or len(actual) != 2:
        raise ValueError('expected exactly the two host-control grants; unrelated rules need separate review')
    normalize = lambda grant: json.dumps(grant, sort_keys=True)
    if sorted(map(normalize, actual)) != sorted(map(normalize, allowed)):
        raise ValueError('policy contains a broad, different or unreviewed grant')
    if document.get('autoApprovers'):
        raise ValueError('automatic route approval requires separate network review')

def write_new(path, data):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    try:
        path.chmod(0o600)
    except OSError:
        pass  # Windows ACL protection is handled by the installation workflow.

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['agent', 'client', 'policy', 'check-policy', 'claude'])
    parser.add_argument('--windows-ip', required=True, type=vpn)
    parser.add_argument('--ubuntu-ip', '--server-ip', dest='ubuntu_ip', required=True, type=vpn)
    parser.add_argument('--port', type=int, default=7443)
    parser.add_argument('--credential-dir')
    parser.add_argument('--fingerprint', type=fingerprint)
    parser.add_argument('--workspace', help='existing host directory (agent mode)')
    parser.add_argument('--capabilities', default='files,exec,desktop')
    parser.add_argument('--session-minutes', type=int, default=30)
    parser.add_argument('--policy-file')
    parser.add_argument('--mcp-binary', default='/opt/hostcontrol/hostmcp')
    parser.add_argument('--client-config', help='absolute client config path for Claude fragment')
    parser.add_argument('--out')
    options = parser.parse_args()
    if options.windows_ip == options.ubuntu_ip:
        parser.error('two different device IPs required')
    if not 1 <= options.port <= 65535 or not 1 <= options.session_minutes <= 480:
        parser.error('invalid port or session duration')
    if options.mode == 'check-policy':
        if not options.policy_file:
            parser.error('--policy-file required; export standard JSON, not HuJSON')
        with open(options.policy_file, encoding='utf-8-sig') as stream:
            check_policy(json.load(stream), options.windows_ip, options.ubuntu_ip, options.port)
        print('Policy file matches the two intended grants. Live policy and firewall are NOT verified.')
        return
    if not options.out:
        parser.error('--out required; files are never overwritten')
    if options.mode == 'policy':
        document = policy(options.windows_ip, options.ubuntu_ip, options.port)
    elif options.mode == 'claude':
        if not options.client_config or not pathlib.PurePosixPath(options.client_config).is_absolute():
            parser.error('--client-config must be an absolute Ubuntu path')
        if not pathlib.PurePosixPath(options.mcp_binary).is_absolute():
            parser.error('--mcp-binary must be an absolute Ubuntu path')
        document = {'mcpServers': {'my-windows-pc': {
            'command': options.mcp_binary, 'args': ['--config', options.client_config]}}}
    else:
        if not options.credential_dir or not options.fingerprint:
            parser.error('--credential-dir and --fingerprint required')
        directory = pathlib.Path(options.credential_dir).resolve()
        if options.mode == 'agent':
            caps = options.capabilities.split(',')
            if not caps or any(x not in ('files', 'exec', 'desktop') for x in caps):
                parser.error('capabilities must be files,exec,desktop or a subset')
            if not options.workspace or not pathlib.Path(options.workspace).is_dir():
                parser.error('--workspace must be an existing host directory')
            document = {'listen': authority(options.windows_ip, options.port), 'peer_ip': options.ubuntu_ip,
                        'ca': str(directory/'ca.pem'), 'cert': str(directory/'agent.pem'),
                        'key': str(directory/'agent-key.pem'), 'peer_fingerprint': options.fingerprint,
                        'roots': {'work': str(pathlib.Path(options.workspace).resolve())},
                        'capabilities': caps, 'session_minutes': options.session_minutes}
        else:
            document = {'endpoint': 'https://' + authority(options.windows_ip, options.port),
                        'ca': str(directory/'ca.pem'), 'cert': str(directory/'client.pem'),
                        'key': str(directory/'client-key.pem'), 'server_fingerprint': options.fingerprint}
    write_new(options.out, document)
    print('Wrote configuration. No network, firewall, or Claude settings were modified.')

if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError) as error:
        print(f'Configuration error: {error}', file=sys.stderr)
        sys.exit(1)
