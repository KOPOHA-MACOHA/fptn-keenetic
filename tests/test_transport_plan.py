"""Bounded real FPTN attempt planning without contacting a VPN or public website."""
import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import test_native_service

ROOT=Path(__file__).resolve().parents[1]

class TransportPlanTest(unittest.TestCase):
    def setUp(self):
        if not shutil.which('php-cgi') and not shutil.which('php'):self.skipTest('PHP unavailable locally; installed in CI')
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.conf=self.root/'etc/fptn';self.conf.mkdir(parents=True);self.run=self.root/'var/run/fptn-keenetic';self.run.mkdir(parents=True)
        lib=self.root/'libexec/fptn';lib.mkdir(parents=True)
        (lib/'brotli').write_text('#!/bin/sh\ncat\n');(lib/'brotli').chmod(0o700)
        self.base={'bypass':'obfuscation','strategy':'dual-rolling-tunnel','sni':'google.com'}
        self.policy={'mode':'auto','favorites':['CZ'],'excluded':['EX']}
        self.settings={'enabled':True,'profiles':['sni-spoofing-chrome-149','sni-spoofing-firefox-151'],'domains':['example.com','example.net'],'budget':60}
        self.token={'servers':[{'name':n} for n in ['CZ','DE','EX']]}
        self.env={**os.environ,'FPTN_PREFIX':str(self.root)};self.write()
    def write(self):
        for name,value in [('connection.json',self.base),('server-policy.json',self.policy),('fallback.json',self.settings)]: (self.conf/name).write_text(json.dumps(value))
        (self.conf/'server').write_text('CZ\n');(self.conf/'token').write_text('fptn:'+base64.b64encode(json.dumps(self.token).encode()).decode())
    def call(self,action,body=None,**env):
        return subprocess.run(['sh',str(ROOT/'scripts/fptn-web-auth.sh'),action],env={**self.env,**env},input=json.dumps(body or {}),text=True,capture_output=True,timeout=10)
    def plan(self,**env):
        r=self.call('transport-plan',**env);self.assertEqual(r.returncode,0,r.stdout+r.stderr);return json.loads(r.stdout)
    def test_favorites_all_methods_before_others_exclusion_and_strategy_invariant(self):
        plan=self.plan();items=plan['plans'];self.assertEqual(items[-1]['stage'],'others');self.assertEqual(items[-1]['eligible'],['DE'])
        for item in items[:-1]:self.assertEqual(item['eligible'],['CZ']);self.assertTrue(item['budgeted']);self.assertLessEqual(item['timeout'],30)
        pairs=[]
        for item in items:
            self.assertIn('EX',item['policy']['excluded']);self.assertEqual(item['connection']['strategy'],'dual-rolling-tunnel')
            for name in item['eligible']:
                pair=(name,item['connection']['bypass'],item['connection']['sni']);self.assertNotIn(pair,pairs);pairs.append(pair)
        self.assertEqual(plan['budget'],60)
    def test_manual_first_then_auto_on_reconnect_and_exclusion_wins(self):
        self.policy['mode']='manual';self.write();(self.conf/'server').write_text('DE\n')
        self.assertEqual(self.plan()['plans'][0]['eligible'],['DE'])
        self.assertEqual(self.plan(FPTN_FORCE_AUTO='yes')['plans'][0]['eligible'],['CZ'])
        self.policy['favorites']=['CZ','DE'];self.policy['excluded']=[' cz ','EX'];self.write()
        for item in self.plan()['plans']:self.assertNotIn('CZ',item['eligible']);self.assertIn('CZ',item['policy']['excluded'])
    def test_good_transport_cache_is_keyed_to_configuration_and_only_for_favorites(self):
        plan=self.plan();(self.run/'transport-plan.json').write_text(json.dumps(plan));(self.run/'active-server').write_text('CZ\n')
        effective={**self.base,'bypass':'sni-spoofing-firefox-151','sni':'example.net'};(self.run/'effective-connection.json').write_text(json.dumps(effective))
        self.assertEqual(self.call('transport-cache').returncode,0)
        self.assertEqual(self.plan()['plans'][0]['stage'],'cached');self.assertEqual(self.plan()['plans'][0]['connection']['bypass'],effective['bypass'])
        self.base['strategy']='rolling-tunnel';self.write();self.assertNotEqual(self.plan()['plans'][0]['stage'],'cached')
        (self.conf/'last-good-transport.json').write_text('broken optimization file');self.assertEqual(self.plan()['enabled'],True)
    def test_validation_bounds_disabled_default_and_shell_input_is_not_executed(self):
        for change in [{'profiles':['x;touch /tmp/not-allowed']},{'domains':['x;bad.example']},{'domains':['bad..example']},{'budget':500},{'profiles':self.settings['profiles']*2}]:
            r=self.call('transport-validate',{'fallback':{**self.settings,**change},'policy':self.policy});self.assertNotEqual(r.returncode,0)
        self.assertNotEqual(self.call('transport-validate',{'fallback':self.settings,'policy':{**self.policy,'favorites':[]}}).returncode,0)
        self.settings['enabled']=False;self.write();self.assertEqual(self.plan(),{'enabled':False})
    def test_compressed_prefix_pipeline_uses_bounded_decoder_without_network(self):
        for prefix in ['fptnb:','fptnb//']:
            (self.conf/'token').write_text(prefix+base64.b64encode(json.dumps(self.token).encode()).decode());self.assertTrue(self.plan()['enabled'])

class NativeFallbackTest(unittest.TestCase):
    setUp=test_native_service.NativeServiceTest.setUp
    cmd=test_native_service.NativeServiceTest.cmd
    state=test_native_service.NativeServiceTest.state
    prepare_native_client=test_native_service.NativeServiceTest.prepare_native_client
    def prepare_fallback(self):
        if not shutil.which('php-cgi') and not shutil.which('php'):self.skipTest('PHP unavailable locally; installed in CI')
        root=self.prepare_native_client();conf=root/'etc/fptn';lib=root/'libexec/fptn'
        for ext in ['sh','php']:shutil.copyfile(ROOT/f'scripts/fptn-web-auth.{ext}',lib/f'web-auth.{ext}')
        shutil.copyfile(ROOT/'scripts/fptn-transport-plan.php',lib/'transport-plan.php')
        token={'servers':[{'name':n} for n in ['CZ','DE','EX']]};(conf/'token').write_text('fptn:'+base64.b64encode(json.dumps(token).encode()).decode())
        (conf/'connection.json').write_text(json.dumps({'bypass':'obfuscation','strategy':'dual-rolling-tunnel','sni':'google.com'}))
        (conf/'server-policy.json').write_text(json.dumps({'mode':'auto','favorites':['CZ'],'excluded':['EX']}))
        (conf/'fallback.json').write_text(json.dumps({'enabled':True,'profiles':['sni-spoofing-chrome-149'],'domains':['example.com'],'budget':30}))
        self.env['FPTN_MOCK_TRANSPORT_TRACE']=str(self.root/'transport-trace');return root
    def test_failed_favorite_retries_profile_before_any_other_server(self):
        root=self.prepare_fallback();self.env['FPTN_MOCK_REQUIRED_BYPASS']='sni-spoofing-chrome-149'
        original=(root/'etc/fptn/connection.json').read_bytes()
        try:
            result=self.cmd('start');self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            trace=(self.root/'transport-trace').read_text().splitlines();self.assertEqual(trace,['obfuscation google.com','sni-spoofing-chrome-149 example.com'])
            effective=json.loads((root/'var/run/fptn-keenetic/effective-connection.json').read_text());self.assertEqual(effective['strategy'],'dual-rolling-tunnel')
            policy=json.loads((root/'var/run/fptn-keenetic/attempt-policy.json').read_text());self.assertEqual(policy['favorites'],['CZ']);self.assertIn('DE',policy['excluded']);self.assertIn('EX',policy['excluded'])
            self.assertEqual((root/'etc/fptn/connection.json').read_bytes(),original);self.assertEqual(self.state()['mutations'],0)
        finally:self.cmd('stop')
    def test_known_auth_rejection_does_not_retry_favorite_with_more_profiles(self):
        root=self.prepare_fallback();self.env['FPTN_MOCK_AUTH_DENIED']='1'
        try:
            result=self.cmd('start');self.assertNotEqual(result.returncode,0)
            trace=(self.root/'transport-trace').read_text().splitlines();self.assertEqual(trace,['obfuscation google.com','obfuscation google.com'])
        finally:self.cmd('stop')

    def test_expired_budget_skips_remaining_profiles_but_allows_ordinary_others_pass(self):
        root=self.prepare_fallback();self.env['FPTN_MOCK_ALWAYS_FAIL']='1'
        clock=self.bin/'date';clock.write_text('#!/bin/sh\nf="$FPTN_PREFIX/clock"\nn=1000; [ ! -f "$f" ] || read -r n < "$f"\nprintf "%s\\n" "$n"\nprintf "%s\\n" "$((n+100))" > "$f"\n');clock.chmod(0o700)
        try:
            result=self.cmd('start');self.assertNotEqual(result.returncode,0)
            trace=(self.root/'transport-trace').read_text().splitlines();self.assertEqual(trace,['obfuscation google.com','obfuscation google.com'])
        finally:self.cmd('stop')
