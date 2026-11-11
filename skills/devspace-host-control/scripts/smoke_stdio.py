#!/usr/bin/env python3
"""Verify PKI CLI + MCP stdio without listening, connecting, or controlling UI."""
import argparse
import json
import pathlib
import subprocess
import tempfile

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--hostctl', required=True)
    p.add_argument('--hostmcp', required=True)
    args=p.parse_args()
    ctl=str(pathlib.Path(args.hostctl).resolve())
    mcp=str(pathlib.Path(args.hostmcp).resolve())
    def run(*arguments):
        return subprocess.run([ctl,*map(str,arguments)],check=True,capture_output=True,text=True,timeout=30).stdout.strip()
    with tempfile.TemporaryDirectory(prefix='hostcontrol-smoke-') as directory:
        root=pathlib.Path(directory);agent=root/'agent';client=root/'client'
        run('init','--dir',agent,'--ip','100.80.0.1')
        run('request','--dir',client)
        run('approve','--dir',agent,'--csr',client/'client.csr','--out',client/'client.pem')
        fingerprint=run('fingerprint','--cert',agent/'agent.pem')
        cfg={'endpoint':'https://100.80.0.1:7443','ca':str(agent/'ca.pem'),
             'cert':str(client/'client.pem'),'key':str(client/'client-key.pem'),
             'server_fingerprint':fingerprint}
        config=root/'client.json';config.write_text(json.dumps(cfg),encoding='utf-8')
        messages=[{'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-11-25'}},
                  {'jsonrpc':'2.0','method':'notifications/initialized'},
                  {'jsonrpc':'2.0','id':2,'method':'tools/list'}]
        completed=subprocess.run([mcp,'--config',str(config)],input=''.join(json.dumps(x)+'\n' for x in messages),
                                 capture_output=True,text=True,check=True,timeout=15)
        responses=[json.loads(line) for line in completed.stdout.splitlines()]
        assert len(responses)==2, 'stdout contains non-protocol data or unexpected messages'
        tools=responses[1]['result']['tools'];names={x['name'] for x in tools}
        assert len(names)==18 and {'fs_read','exec_start','desktop_capture','desktop_inspect','desktop_click','browser_open'} <= names
        assert responses[0]['result']['protocolVersion']=='2025-11-25'
        print('Executable smoke passed: local PKI roundtrip, initialize, notification, 18 tool schemas; no network connection or UI input.')

if __name__=='__main__':main()
