import importlib.util
import pathlib
import tempfile
import unittest
import json
import subprocess
import sys

SPEC=importlib.util.spec_from_file_location('configure',pathlib.Path(__file__).with_name('configure.py'))
configure=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(configure)

class ConfigurationTests(unittest.TestCase):
    def test_rejects_networks_and_public_addresses(self):
        for value in ('100.64.0.0/10','0.0.0.0','127.0.0.1','192.168.1.1','8.8.8.8','::'):
            with self.assertRaises(Exception): configure.vpn(value)

    def test_policy_exact_and_rejects_additive_allow_all(self):
        p=configure.policy('100.80.0.1','100.80.0.2')
        configure.check_policy(p,'100.80.0.1','100.80.0.2')
        p['grants'].append({'src':['*'],'dst':['*'],'ip':['*']})
        with self.assertRaises(ValueError): configure.check_policy(p,'100.80.0.1','100.80.0.2')
        p=configure.policy('100.80.0.1','100.80.0.2');p['acls']=[{'action':'accept','src':['*'],'dst':['*:*']}]
        with self.assertRaises(ValueError): configure.check_policy(p,'100.80.0.1','100.80.0.2')

    def test_cli_generates_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            out=pathlib.Path(directory)/'agent.json'
            command=[sys.executable,str(pathlib.Path(__file__).with_name('configure.py')),'agent','--windows-ip','100.80.0.1','--ubuntu-ip','100.80.0.2','--credential-dir',directory,'--fingerprint','a'*64,'--workspace',directory,'--out',str(out)]
            subprocess.run(command,check=True,capture_output=True)
            data=json.loads(out.read_text(encoding='utf-8'))
            self.assertEqual(data['peer_ip'],'100.80.0.2')
            self.assertEqual(data['listen'],'100.80.0.1:7443')
            self.assertNotEqual(subprocess.run(command,capture_output=True).returncode,0)

    def test_client_and_claude_config(self):
        with tempfile.TemporaryDirectory() as directory:
            base=[sys.executable,str(pathlib.Path(__file__).with_name('configure.py'))]
            common=['--windows-ip','100.80.0.1','--ubuntu-ip','100.80.0.2']
            out=pathlib.Path(directory)/'claude.json'
            subprocess.run(base+['claude']+common+['--client-config','/home/worker/.config/hostcontrol/client.json','--out',str(out)],check=True,capture_output=True)
            data=json.loads(out.read_text(encoding='utf-8'))
            self.assertEqual(data['mcpServers']['my-windows-pc']['command'],'/opt/hostcontrol/hostmcp')

if __name__=='__main__': unittest.main()
