"""Offline behavioral checks. Never changes a live tailnet, host or firewall."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


def module(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(name + '.py'))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


policy = module('tailscale_policy')
automation = module('automation')
WIN, SERVER = '100.80.0.1', '100.80.0.2'
DEFAULT = {'grants': [{'src': ['*'], 'dst': ['*'], 'ip': ['*']}],
           'ssh': [{'action': 'check', 'src': ['autogroup:member'],
                    'dst': ['autogroup:self'], 'users': ['autogroup:nonroot', 'root']}]}


class FakeAPI:
    def __init__(self, live):
        self.live = copy.deepcopy(live)
        self.writes = []
        self.validated = []
        self.fail_validation = False
        self.fail_write = False
        self.concurrent_change = False
        self.wrong_readback = False

    def get(self):
        return copy.deepcopy(self.live), 'etag-1'

    def validate(self, document):
        self.validated.append(copy.deepcopy(document))
        if self.fail_validation:
            raise policy.PolicyError('Validation failed')

    def set(self, document, etag):
        self.writes.append((copy.deepcopy(document), etag))
        if self.concurrent_change:
            raise policy.PolicyError('Concurrent modification')
        self.live = copy.deepcopy(document)
        if self.fail_write:
            raise policy.PolicyError('Unknown write result')
        if self.wrong_readback:
            self.live['grants'].append(DEFAULT['grants'][0])


class PolicyTests(unittest.TestCase):
    def make_plan(self, root, original=DEFAULT):
        candidate, _ = policy.build_policy(original, WIN, SERVER, replace_default=True)
        root = Path(root)
        (root / 'plan.json').write_text(json.dumps({
            'windows_ip': WIN, 'server_ip': SERVER, 'port': 7443,
            'original_sha256': policy.digest(original), 'candidate_sha256': policy.digest(candidate)}))
        (root / 'policy.json').write_text(json.dumps(candidate))
        return candidate

    def test_hujson_keeps_strings_and_supports_comments_and_trailing_commas(self):
        raw = r'''// default
        {"url":"https://example.com/a//b", "text":"/* keep */ ,}",
         "quoted":"escaped \\\" // text", /* remove */ "items":[1,2,],}
        '''
        parsed = policy.loads(raw)
        self.assertEqual(parsed['url'], 'https://example.com/a//b')
        self.assertEqual(parsed['text'], '/* keep */ ,}')
        self.assertEqual(parsed['items'], [1, 2])

    def test_duplicate_keys_and_incomplete_fragments_rejected(self):
        for text in ('{"grants":[],"grants":[]}', '"grants":[]', '[]', '{/* unfinished', '{"x":NaN}'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                policy.loads(text)

    def test_default_removal_requires_explicit_scope_and_preserves_ssh(self):
        with self.assertRaises(policy.PolicyError):
            policy.build_policy(DEFAULT, WIN, SERVER)
        built, removed = policy.build_policy(DEFAULT, WIN, SERVER, True)
        self.assertEqual(built['ssh'], DEFAULT['ssh'])
        self.assertEqual(len(removed), 1)
        self.assertEqual(DEFAULT['grants'][0]['ip'], ['*'])
        self.assertEqual(policy.build_policy(built, WIN, SERVER)[0], built)

    def test_legacy_default_replaced_but_unknown_acl_not_deleted(self):
        legacy = {'acls': [{'action': 'accept', 'src': ['*'], 'dst': ['*:*']}]}
        policy.audit(policy.build_policy(legacy, WIN, SERVER, True)[0], WIN, SERVER)
        legacy['acls'].append({'action': 'accept', 'src': ['group:staff'], 'dst': ['*:22']})
        with self.assertRaises(policy.PolicyError):
            policy.build_policy(legacy, WIN, SERVER, True)

    def test_preserves_disjoint_business_rules_and_metadata(self):
        document = copy.deepcopy(DEFAULT)
        other = {'src': ['100.90.0.1/32'], 'dst': ['100.90.0.2/32'], 'ip': ['tcp:443']}
        document['grants'].append(other)
        document['groups'] = {'group:staff': ['someone@example.com']}
        built, _ = policy.build_policy(document, WIN, SERVER, True)
        self.assertIn(other, built['grants'])
        self.assertEqual(built['groups'], document['groups'])

    def test_rejects_additive_alias_group_subnet_and_ipv6_bypasses(self):
        for grant in (
            {'src': ['*'], 'dst': [SERVER], 'ip': ['tcp:3389']},
            {'src': [SERVER], 'dst': ['group:staff'], 'ip': ['*']},
            {'src': ['100.64.0.0/10'], 'dst': [WIN], 'ip': ['tcp:7443']},
            {'src': ['fd7a:115c:a1e0::1'], 'dst': ['tag:desktop'], 'ip': ['*']},
            {'src': ['fd7a:115c:a1e0::1'], 'dst': ['fd7a:115c:a1e0::2'], 'ip': ['*']},
            {'src': ['server-alias'], 'dst': [WIN], 'ip': ['*']},
        ):
            document = copy.deepcopy(DEFAULT)
            document['hosts'] = {'server-alias': SERVER}
            document['grants'].append(grant)
            with self.subTest(grant=grant), self.assertRaises(policy.PolicyError):
                policy.build_policy(document, WIN, SERVER, True)

    def test_public_network_and_equal_addresses_rejected(self):
        for first, second in [('203.0.113.10', SERVER), (WIN, WIN), ('100.64.0.0/10', SERVER)]:
            with self.assertRaises(policy.PolicyError):
                policy.desired(first, second)

    def test_discovery_requires_selected_unambiguous_peer(self):
        status = {'BackendState': 'Running', 'Self': {'OS': 'windows', 'TailscaleIPs': [WIN]},
                  'Peer': {'a': {'OS': 'linux', 'HostName': 'controller', 'TailscaleIPs': [SERVER]}}}
        self.assertEqual(policy.discover(status, 'controller')['server_ip'], SERVER)
        with self.assertRaises(policy.PolicyError):
            policy.discover(status)
        status['Peer']['b'] = dict(status['Peer']['a'])
        with self.assertRaises(policy.PolicyError):
            policy.discover(status, 'controller')

    def test_apply_validates_conditional_write_readback_and_idempotency(self):
        with tempfile.TemporaryDirectory() as root:
            expected = self.make_plan(root)
            api = FakeAPI(DEFAULT)
            result = policy.apply_plan(api, root)
            self.assertTrue(result['changed'])
            self.assertEqual(api.writes, [(expected, 'etag-1')])
            self.assertEqual(api.validated, [expected])
            self.assertFalse(policy.apply_plan(api, root)['changed'])
            self.assertEqual(len(api.writes), 1)

    def test_one_command_sync_discovers_plans_saves_and_verifies(self):
        status = {'BackendState': 'Running', 'Self': {'OS': 'windows', 'TailscaleIPs': [WIN]},
                  'Peer': {'a': {'OS': 'linux', 'HostName': 'controller',
                                 'TailscaleIPs': [SERVER], 'Online': True}}}
        with tempfile.TemporaryDirectory() as root:
            api = FakeAPI(DEFAULT)
            result = policy.sync(api, status, Path(root) / 'plan', 'controller', replace_default=True)
            self.assertEqual(result['status'], 'verified')
            self.assertEqual(len(api.writes), 1)
            self.assertEqual(result['devices']['server_ip'], SERVER)
            self.assertTrue((Path(root) / 'plan/original.hujson').is_file())
            result = policy.sync(api, status, Path(root) / 'second', 'controller')
            self.assertFalse(result['changed'])
            self.assertEqual(len(api.writes), 1)

    def test_one_command_sync_stops_for_offline_server_before_write(self):
        status = {'BackendState': 'Running', 'Self': {'OS': 'windows', 'TailscaleIPs': [WIN]},
                  'Peer': {'a': {'OS': 'linux', 'HostName': 'controller',
                                 'TailscaleIPs': [SERVER], 'Online': False}}}
        with tempfile.TemporaryDirectory() as root:
            api = FakeAPI(DEFAULT)
            with self.assertRaises(policy.PolicyError):
                policy.sync(api, status, Path(root) / 'plan', 'controller', replace_default=True)
            self.assertEqual(api.writes, [])

    def test_refuses_changed_policy_and_plan_before_any_write(self):
        with tempfile.TemporaryDirectory() as root:
            self.make_plan(root)
            api = FakeAPI(dict(DEFAULT, groups={'group:other': []}))
            with self.assertRaises(policy.PolicyError):
                policy.apply_plan(api, root)
            self.assertEqual(api.writes, [])
            (Path(root) / 'policy.json').write_text(json.dumps(DEFAULT))
            with self.assertRaises(policy.PolicyError):
                policy.apply_plan(FakeAPI(DEFAULT), root)

    def test_failed_validation_prevents_write(self):
        with tempfile.TemporaryDirectory() as root:
            self.make_plan(root)
            api = FakeAPI(DEFAULT)
            api.fail_validation = True
            with self.assertRaises(policy.PolicyError):
                policy.apply_plan(api, root)
            self.assertEqual(api.writes, [])

    def test_concurrency_unknown_outcome_and_bad_readback_never_retry(self):
        for flag in ('concurrent_change', 'fail_write', 'wrong_readback'):
            with self.subTest(flag=flag), tempfile.TemporaryDirectory() as root:
                self.make_plan(root)
                api = FakeAPI(DEFAULT)
                setattr(api, flag, True)
                with self.assertRaises(policy.PolicyError):
                    policy.apply_plan(api, root)
                self.assertEqual(len(api.writes), 1)

    def test_http_client_does_not_forward_redirects_or_write_without_etag(self):
        with self.assertRaises(policy.PolicyError):
            policy.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://other.example')
        with self.assertRaises(policy.PolicyError):
            policy.API('example', 'local-test-credential').set(DEFAULT, None)


class JournalTests(unittest.TestCase):
    def evidence(self, stage):
        result = {'checks': {name: True for name in automation.STAGES[stage]},
                  'observed_at': '2026-01-01T00:00:00+00:00', 'source': 'offline test fixture'}
        if stage == 'desktop':
            result['clipboard_mode'] = 'text'
        if stage == 'timezone':
            result['timezone'] = 'America/Los_Angeles'
        return result

    def test_legacy_desktop_evidence_requires_new_acceptance(self):
        state = {'stages': {}}
        for stage in automation.STAGES:
            automation.record(state, stage, self.evidence(stage))
        state['stages']['desktop']['evidence'] = {
            'checks': {name: True for name in ('packages_installed', 'rdp_vpn_bound',
                                              'rdp_source_guard', 'redirection_disabled')},
            'observed_at': '2026-01-01T00:00:00+00:00', 'source': 'old journal'}
        self.assertFalse(automation.next_steps(state)['complete'])
        self.assertIn('desktop', automation.next_steps(state)['ready'])
        self.assertNotIn('acceptance', automation.verified_stages(state))
        with self.assertRaises(ValueError):
            automation.record(state, 'claude', self.evidence('claude'))
        automation.record(state, 'desktop', self.evidence('desktop'))
        self.assertNotIn('acceptance', state['stages'])
        self.assertIn('firewall', automation.next_steps(state)['ready'])

    def test_clipboard_choice_must_match_desktop_evidence(self):
        state = {'clipboard': 'disabled', 'stages': {}}
        for stage in ('discovery', 'timezone', 'tailscale', 'policy'):
            automation.record(state, stage, self.evidence(stage))
        with self.assertRaisesRegex(ValueError, 'clipboard_mode'):
            automation.record(state, 'desktop', self.evidence('desktop'))
        evidence = self.evidence('desktop')
        evidence['clipboard_mode'] = 'disabled'
        automation.record(state, 'desktop', evidence)
        self.assertIn('desktop', automation.verified_stages(state))
        state['clipboard'] = 'text'
        self.assertNotIn('desktop', automation.verified_stages(state))

    def test_no_completion_from_install_alone(self):
        state = {'stages': {}}
        self.assertEqual(automation.next_steps(state)['ready'], ['discovery'])
        with self.assertRaises(ValueError):
            automation.record(state, 'acceptance', self.evidence('acceptance'))
        for stage in automation.STAGES:
            automation.record(state, stage, self.evidence(stage))
        self.assertTrue(automation.next_steps(state)['complete'])
        automation.record(state, 'tailscale', {'reason': 'IP changed'}, 'waiting')
        self.assertFalse(automation.next_steps(state)['complete'])
        self.assertNotIn('acceptance', state['stages'])
        self.assertNotIn('policy', state['stages'])

    def test_datacenter_timezone_change_invalidates_desktop_but_keeps_pairing(self):
        state = {'stages': {}}
        for stage in automation.STAGES:
            automation.record(state, stage, self.evidence(stage))
        automation.select_timezone(state, 'Europe/Berlin')
        self.assertIn('pairing', automation.verified_stages(state))
        self.assertNotIn('desktop', state['stages'])
        self.assertNotIn('acceptance', state['stages'])
        with self.assertRaisesRegex(ValueError, 'differs'):
            automation.record(state, 'timezone', self.evidence('timezone'))
        evidence = self.evidence('timezone')
        evidence['timezone'] = 'Europe/Berlin'
        automation.record(state, 'timezone', evidence)
        self.assertIn('desktop', automation.next_steps(state)['ready'])

    def test_legacy_completion_without_timezone_needs_new_observations(self):
        state = {'stages': {}}
        for stage in automation.STAGES:
            automation.record(state, stage, self.evidence(stage))
        del state['stages']['timezone']
        state.pop('server_timezone')
        self.assertFalse(automation.next_steps(state)['complete'])
        self.assertIn('timezone', automation.next_steps(state)['ready'])
        self.assertNotIn('desktop', automation.verified_stages(state))

    def test_missing_evidence_rejected(self):
        with self.assertRaises(ValueError):
            automation.record({'stages': {}}, 'discovery', {'checks': {}})

    def test_credentials_and_private_urls_not_saved(self):
        for item in ({'password': 'example'}, {'note': 'tskey-example'},
                     {'note': 'http://127.0.0.1:5000/#private'}, {'token': 'example'}):
            with self.subTest(item=item), self.assertRaises(ValueError):
                automation.no_secrets(item)
        automation.no_secrets({'checks': {'private_keys_local': True}})

    def test_discovery_never_requests_ssh_hostkey_bypass(self):
        with patch.object(automation, 'run_json', return_value={}) as run:
            automation.inspect_remote('server.example')
            args = run.call_args.args[0]
            self.assertIn('StrictHostKeyChecking=yes', args)
            self.assertIn('BatchMode=yes', args)
        with self.assertRaises(ValueError):
            automation.inspect_remote('-oProxyCommand=anything')


if __name__ == '__main__':
    unittest.main()
