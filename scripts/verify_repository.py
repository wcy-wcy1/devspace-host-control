#!/usr/bin/env python3
"""Verify the maintained module matches the source distributed with the skill."""
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
SKILL = ROOT / 'skills/devspace-host-control'
IGNORED = {'__pycache__', '.git', 'dist'}
REQUIRED_SKILL_FILES = (
    'references/automation.md', 'references/troubleshooting.md', 'references/launcher.md',
    'scripts/automation.py', 'scripts/tailscale_policy.py', 'scripts/desktop_config.py',
    'scripts/linux_session.py', 'scripts/launcher.py', 'scripts/claude_session.py',
    'scripts/start-hostcontrol.ps1', 'scripts/start-windows-agent.ps1',
    'scripts/windows-firewall.ps1', 'scripts/ubuntu-desktop.sh', 'scripts/centos-desktop.sh',
)


def source_files(root):
    return {p.relative_to(root): p for p in root.rglob('*')
            if p.is_file() and not any(part in IGNORED for part in p.relative_to(root).parts)
            and p.suffix not in {'.pyc', '.log', '.pem', '.key', '.csr'}}


def main():
    for name in REQUIRED_SKILL_FILES:
        if not (SKILL / name).is_file():
            raise SystemExit('Unified skill resource is missing: ' + name)
    canonical = source_files(ROOT / 'hostcontrol')
    mirrored = source_files(SKILL / 'assets/source/hostcontrol')
    if canonical.keys() != mirrored.keys():
        raise SystemExit('Source mirror differs: run python scripts/package.py after reviewing changes.')
    for relative, path in canonical.items():
        if path.read_bytes() != mirrored[relative].read_bytes():
            raise SystemExit(f'Source mirror is stale: {relative}')
    subprocess.run([sys.executable, str(SKILL / 'scripts/verify_bundle.py')], check=True)
    print(f'Canonical source and skill mirror match ({len(canonical)} files).')


if __name__ == '__main__':
    main()
