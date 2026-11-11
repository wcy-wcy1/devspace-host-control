#!/usr/bin/env python3
"""Read-only discovery and a resumable deployment journal for the skill executor.

This coordinates the agent's real installation tools; a journal entry alone is
never proof that an external service is working. Secrets do not belong here.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import subprocess
import sys
import uuid

STAGES = {
    'discovery': ('authorized_target', 'os_arch_observed', 'existing_services_observed', 'workspace_chosen'),
    'tailscale': ('windows_running', 'server_running', 'same_tailnet', 'exact_ips_observed'),
    'policy': ('complete_policy_read', 'no_conflicting_allow', 'saved_policy_read_back'),
    'desktop': ('packages_installed', 'rdp_vpn_bound', 'rdp_source_guard',
                'clipboard_mode_verified', 'non_text_redirection_disabled', 'xorg_arguments_valid'),
    'pairing': ('private_keys_local', 'both_fingerprints_pinned', 'agent_config_valid', 'client_config_valid'),
    'firewall': ('effective_windows_rules', 'effective_server_rules', 'ipv6_restricted'),
    'claude': ('gui_launch_observed', 'account_signed_in', 'mcp_discovery_observed'),
    'launcher': ('entry_generated', 'linux_login_reconnect_configured',
                 'normal_rdp_credentials', 'no_control_auto_enable'),
    'session': ('local_time_limited_enable', 'pause_stop_path_available'),
    'acceptance': ('file_roundtrip', 'harmless_exec', 'fresh_frame_gui_input', 'network_denials', 'stop_verified'),
}
DEPENDENCIES = {
    'discovery': (), 'tailscale': ('discovery',), 'policy': ('tailscale',),
    'desktop': ('discovery', 'policy'), 'pairing': ('tailscale',),
    'firewall': ('pairing', 'desktop'), 'claude': ('desktop', 'pairing', 'firewall'),
    'launcher': ('claude',), 'session': ('launcher',), 'acceptance': ('session',),
}
SECRET_KEYS = re.compile(r'(?:password|private.?key|secret|token|authurl|control.?url|authorization)', re.I)


def now():
    return datetime.now(timezone.utc).isoformat()


def no_secrets(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if SECRET_KEYS.search(key) and not isinstance(item, bool):
                raise ValueError('Sensitive field is not allowed in the deployment journal: ' + key)
            no_secrets(item)
    elif isinstance(value, list):
        for item in value:
            no_secrets(item)
    elif isinstance(value, str):
        if ('-----BEGIN ' in value or 'tskey-' in value
                or re.search(r'https?://\S*(?:#|[?&](?:token|key|auth)=)', value, re.I)):
            raise ValueError('Credentials or private control URLs must stay outside the journal')


def save(path, data):
    no_secrets(data)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('x', encoding='utf-8', newline='\n') as stream:
            json.dump(data, stream, indent=2, ensure_ascii=False)
            stream.write('\n')
        if os.name != 'nt':
            temporary.chmod(0o600)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def load(path):
    document = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    if document.get('schema_version') != 1:
        raise ValueError('Unsupported deployment journal version')
    no_secrets(document)
    if document.get('clipboard', 'text') not in ('text', 'disabled'):
        raise ValueError('Unsupported clipboard mode in deployment journal')
    return document


def observed(state, stage, evidence):
    checks = evidence.get('checks', {})
    return (isinstance(checks, dict)
            and all(checks.get(check) is True for check in STAGES[stage])
            and bool(evidence.get('observed_at') and evidence.get('source'))
            and (stage != 'desktop' or evidence.get('clipboard_mode') == state.get('clipboard', 'text')))


def verified_stages(state):
    """Old journal labels cannot verify new checks or stale dependencies."""
    verified = set()
    for stage in STAGES:
        item = state.get('stages', {}).get(stage, {})
        if (item.get('status') == 'verified'
                and all(dep in verified for dep in DEPENDENCIES[stage])
                and observed(state, stage, item.get('evidence', {}))):
            verified.add(stage)
    return verified


def record(state, stage, evidence, status='verified'):
    no_secrets(evidence)
    if not isinstance(evidence, dict):
        raise ValueError('Evidence must be an object')
    verified = verified_stages(state)
    for dependency in DEPENDENCIES[stage]:
        if dependency not in verified:
            raise ValueError('Required stage is not verified: ' + dependency)
    if status == 'verified':
        checks = evidence.get('checks', {})
        missing = [check for check in STAGES[stage] if checks.get(check) is not True]
        if missing:
            raise ValueError('Missing observed checks: ' + ', '.join(missing))
        if not evidence.get('observed_at') or not evidence.get('source'):
            raise ValueError('Record observation time and a command/artifact reference, without secrets')
        if stage == 'desktop' and evidence.get('clipboard_mode') != state.get('clipboard', 'text'):
            raise ValueError('Desktop evidence must name the currently selected clipboard_mode')
    stages = state.setdefault('stages', {})
    # Changing a prerequisite invalidates dependent observations, recursively.
    invalidated = {stage}
    for candidate in STAGES:
        if any(item in invalidated for item in DEPENDENCIES[candidate]):
            invalidated.add(candidate)
            stages.pop(candidate, None)
    stages[stage] = {'status': status, 'recorded_at': now(), 'evidence': evidence}
    state['updated_at'] = now()
    return state


def next_steps(state):
    verified = verified_stages(state)
    pending = [stage for stage in STAGES if stage not in verified]
    ready = [stage for stage in pending if all(dep in verified for dep in DEPENDENCIES[stage])]
    return {'complete': not pending, 'ready': ready, 'pending': pending,
            'clipboard': state.get('clipboard', 'text'),
            'checks': {stage: list(STAGES[stage]) for stage in ready},
            'resume_rule': 'Re-observe live addresses, policy, listeners and session state before reusing journal evidence.'}


def run_json(command, timeout=25):
    result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', timeout=timeout)
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {'available': False, 'exit_code': result.returncode}


def inspect_local():
    executable = shutil.which('tailscale')
    if not executable and os.name == 'nt':
        candidate = Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'Tailscale/tailscale.exe'
        if candidate.is_file():
            executable = str(candidate)
    result = {'os': platform.system().lower(), 'architecture': platform.machine(),
              'ssh_available': bool(shutil.which('ssh')), 'tailscale_executable': executable,
              'questions': []}
    if executable:
        status = run_json([executable, 'status', '--json'])
        result['tailscale_state'] = status.get('BackendState')
        result['tailscale_ips'] = status.get('TailscaleIPs') or []
        result['tailnet'] = (status.get('CurrentTailnet') or {}).get('Name')
        result['peers'] = [{'name': x.get('HostName'), 'os': x.get('OS'),
                            'ips': x.get('TailscaleIPs', []), 'online': x.get('Online')}
                           for x in (status.get('Peer') or {}).values()]
        if status.get('BackendState') != 'Running':
            result['questions'].append('Complete the local Tailscale sign-in; credentials stay in the browser.')
    else:
        result['questions'].append('Install the official Tailscale client and complete its local sign-in.')
    return result


REMOTE_INSPECT = r'''
import json,platform,subprocess,shutil
from pathlib import Path
def cmd(args):
 p=subprocess.run(args,capture_output=True,text=True,timeout=15)
 return p.stdout
release={}
for line in Path('/etc/os-release').read_text().splitlines():
 if '=' in line:
  k,v=line.split('=',1);release[k]=v.strip('"')
ts={}
if shutil.which('tailscale'):
 try: ts=json.loads(cmd(['tailscale','status','--json']))
 except (ValueError,subprocess.SubprocessError): pass
print(json.dumps({'os':release.get('ID'),'version':release.get('VERSION_ID'),
 'architecture':platform.machine(),'tailscale_state':ts.get('BackendState'),
 'tailscale_ips':ts.get('TailscaleIPs') or [],'tailnet':(ts.get('CurrentTailnet') or {}).get('Name'),
 'listeners':cmd(['ss','-lnt']),'rdp_active':cmd(['systemctl','is-active','xrdp']).strip(),
 'disk':cmd(['df','-h','/']),'memory':cmd(['free','-m'])}))
'''


def inspect_remote(server, user='root', port=22):
    if (not re.fullmatch(r'[A-Za-z0-9_.:-]+', server) or server.startswith('-')
            or not re.fullmatch(r'[a-z_][a-z0-9_-]*', user) or not 1 <= port <= 65535):
        raise ValueError('Invalid SSH target')
    command = ['ssh', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
               '-o', 'ConnectTimeout=10', '-p', str(port), user + '@' + server,
               'python3 -c ' + shlex.quote(REMOTE_INSPECT)]
    result = run_json(command, timeout=60)
    if result.get('available') is False:
        return {'available': False, 'question': 'Established SSH authentication is unavailable. Use an authenticated session or local secure credential prompt; never ask for a password in chat.'}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='mode', required=True)
    p = sub.add_parser('inspect')
    p.add_argument('--server')
    p.add_argument('--ssh-user', default='root')
    p.add_argument('--ssh-port', type=int, default=22)
    p = sub.add_parser('init')
    p.add_argument('--state', required=True)
    p.add_argument('--server', required=True)
    p.add_argument('--workspace', required=True)
    p.add_argument('--desktop-user', default='hostcontrol')
    p.add_argument('--session-minutes', type=int, default=30)
    p.add_argument('--clipboard', choices=('text', 'disabled'), default='text')
    for name in ('next', 'record'):
        p = sub.add_parser(name)
        p.add_argument('--state', required=True)
        if name == 'record':
            p.add_argument('--stage', choices=STAGES, required=True)
            p.add_argument('--evidence', required=True)
            p.add_argument('--status', choices=('verified', 'waiting', 'failed'), default='verified')
    args = parser.parse_args()
    if args.mode == 'inspect':
        result = {'local': inspect_local()}
        if args.server:
            result['server'] = inspect_remote(args.server, args.ssh_user, args.ssh_port)
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif args.mode == 'init':
        if Path(args.state).exists():
            raise ValueError('Deployment journal exists; resume it instead of overwriting')
        workspace = Path(args.workspace).resolve()
        if not workspace.is_dir() or not 1 <= args.session_minutes <= 480:
            raise ValueError('Existing workspace and a session duration from 1 to 480 minutes required')
        if not re.fullmatch(r'[a-z_][a-z0-9_-]*', args.desktop_user) or args.desktop_user == 'root':
            raise ValueError('A non-root desktop user is required')
        state = {'schema_version': 1, 'deployment_id': uuid.uuid4().hex,
                 'created_at': now(), 'server': args.server, 'workspace': str(workspace),
                 'desktop_user': args.desktop_user, 'session_minutes': args.session_minutes,
                 'clipboard': args.clipboard,
                 'stages': {}}
        save(args.state, state)
        print(json.dumps(next_steps(state), indent=2))
    else:
        state = load(args.state)
        if args.mode == 'record':
            evidence = json.loads(Path(args.evidence).read_text(encoding='utf-8-sig'))
            record(state, args.stage, evidence, args.status)
            save(args.state, state)
        print(json.dumps(next_steps(state), indent=2))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print(json.dumps({'status': 'needs_attention', 'message': str(error)}, ensure_ascii=False), file=sys.stderr)
        sys.exit(2)
