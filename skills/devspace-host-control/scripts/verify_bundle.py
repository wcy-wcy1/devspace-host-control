#!/usr/bin/env python3
"""Check packaged runtime checksums and PE/ELF target architecture."""
import hashlib
import json
import pathlib
import struct

def main():
    assets=pathlib.Path(__file__).resolve().parents[1]/'assets'
    manifest=json.loads((assets/'SHA256SUMS.json').read_text(encoding='utf-8'))
    required={
        'runtime/windows-amd64/hostagent.exe','runtime/windows-amd64/hostctl.exe',
        'runtime/linux-amd64/hostmcp','runtime/linux-amd64/hostctl',
        'runtime/linux-arm64/hostmcp','runtime/linux-arm64/hostctl',
    }
    if set(manifest)!=required:raise SystemExit('Expected the six supported runtime files')
    for relative,expected in manifest.items():
        path=(assets/relative).resolve()
        if not path.is_relative_to(assets.resolve()):raise SystemExit('Invalid manifest path')
        data=path.read_bytes()
        if hashlib.sha256(data).hexdigest()!=expected['sha256'] or len(data)!=expected['size']:
            raise SystemExit(f'Integrity mismatch: {relative}')
        if 'windows-amd64/' in relative:
            if data[:2]!=b'MZ':raise SystemExit('Invalid PE')
            offset=struct.unpack_from('<I',data,0x3c)[0]
            if data[offset:offset+4]!=b'PE\0\0' or struct.unpack_from('<H',data,offset+4)[0]!=0x8664:
                raise SystemExit('Incorrect Windows architecture')
        else:
            machine=183 if 'linux-arm64/' in relative else 62
            if data[:4]!=b'\x7fELF' or struct.unpack_from('<H',data,18)[0]!=machine:
                raise SystemExit('Incorrect Linux architecture')
        print('Verified:',relative)
    print('Integrity and architecture match. This does not establish publisher identity or prove live deployment.')

if __name__=='__main__':main()
