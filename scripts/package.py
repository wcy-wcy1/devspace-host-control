#!/usr/bin/env python3
"""Synchronize skill source/checksums and produce portable release archives.

Build and test changed binaries before packaging. This script does not claim to
have run tests or rewrite historical build evidence.
"""
import hashlib
import json
import pathlib
import shutil
import zipfile

from verify_repository import ROOT, SKILL, main as verify, source_files


def archive(root, destination, prefix):
    with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
        for relative, path in sorted(source_files(root).items()):
            if 'private' in relative.parts or path.name.startswith('.env'):
                raise SystemExit(f'Private material cannot be packaged: {relative}')
            name = relative.as_posix()
            entry = zipfile.ZipInfo.from_file(path, arcname=prefix + '/' + name)
            entry.create_system = 3
            mode = 0o755 if '/runtime/' in '/' + name or path.suffix == '.sh' else 0o644
            entry.external_attr = (0o100000 | mode) << 16
            entry.compress_type = zipfile.ZIP_DEFLATED
            bundle.writestr(entry, path.read_bytes())
    with zipfile.ZipFile(destination) as bundle:
        if bundle.testzip() is not None:
            raise SystemExit('Archive CRC check failed')


def main():
    source = ROOT / 'hostcontrol'
    mirror = SKILL / 'assets/source/hostcontrol'
    canonical = source_files(source)
    obsolete = source_files(mirror).keys() - canonical.keys()
    if obsolete:
        raise SystemExit('Review/remove obsolete mirrored files first: ' + ', '.join(map(str, obsolete)))
    for relative, path in canonical.items():
        destination = mirror / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
    assets = SKILL / 'assets'
    manifest = {}
    for path in sorted((assets / 'runtime').rglob('*')):
        if path.is_file():
            data = path.read_bytes()
            manifest[path.relative_to(assets).as_posix()] = {
                'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data)}
    (assets / 'SHA256SUMS.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    verify()
    output = ROOT / 'dist'
    output.mkdir(exist_ok=True)
    names = ['devspace-host-control.zip', 'devspace-hostcontrol-source.zip']
    archive(SKILL, output / names[0], 'devspace-host-control')
    archive(source, output / names[1], 'hostcontrol')
    lines = [hashlib.sha256((output / name).read_bytes()).hexdigest() + '  ' + name for name in names]
    (output / 'SHA256SUMS.txt').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print('Release archives and SHA256SUMS.txt written to dist/.')
    print('Review assets/build-evidence.md when changing source or runtime; it is a historical test record.')


if __name__ == '__main__':
    main()
