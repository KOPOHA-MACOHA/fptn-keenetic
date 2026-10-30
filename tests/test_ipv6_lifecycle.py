"""Firmware address ownership and opt-in settings are independent of real router state."""
import json,os,shutil,subprocess,tempfile,unittest
from pathlib import Path
import client_fixture
ROOT=Path(__file__).resolve().parents[1]
MOCK=r'''#!/usr/bin/env python3
import json,os,sys
from pathlib import Path
root=Path(os.environ['FPTN_PREFIX']);state=root/'ipv6-state.json';data=json.loads(state.read_text());name=Path(sys.argv[0]).name
if name=='ip':
 if 'opkgtun0' in sys.argv and data['address']:print('52: opkgtun0 inet6 fd42:6670:746e::1/128 scope global')
elif name=='ndmc':
 with (root/'trace').open('a') as f:f.write(sys.argv[2]+'\n')
 if data.get('fail'):sys.exit(1)
 data['address']=' no ipv6 ' not in sys.argv[2];state.write_text(json.dumps(data))
'''
class IPv6LifecycleTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)/'opt'
        for name in ['bin','etc/fptn','libexec/fptn']:(self.root/name).mkdir(parents=True,exist_ok=True)
        self.lib=self.root/'libexec/fptn';self.conf=self.root/'etc/fptn';client_fixture.install(self.root)
        (self.conf/'native-interface').write_text('OpkgTun0\n');self.state={'address':False,'fail':False};self.save()
        for name in ['ip','ndmc']:
            p=self.root/'bin'/name;p.write_text(MOCK);p.chmod(0o700)
        helper=self.lib/'ipv6.sh';helper.write_text('#!/bin/sh\necho "$1" >> "$FPTN_PREFIX/nat-actions"\n[ ! -f "$FPTN_PREFIX/nat-unavailable" ]\n');helper.chmod(0o700)
        self.env={**os.environ,'FPTN_PREFIX':str(self.root),'PATH':str(self.root/'bin')+':'+os.environ['PATH']}
        self.definitions=(ROOT/'scripts/fptn-native-service.sh').read_text().rsplit('\nneed_root\n',1)[0]
    def save(self):(self.root/'ipv6-state.json').write_text(json.dumps(self.state))
    def call(self,code):return subprocess.run(['sh','-c',self.definitions+'\n'+code,str(self.root/'bin/fptnctl')],env=self.env,capture_output=True,text=True,timeout=10)
    def test_add_address_only_to_owned_interface_and_remove_only_when_owned(self):
        result=self.call('IPV6=true; MTU=1420; prepare_ipv6');self.assertEqual(result.returncode,0,result.stderr)
        self.assertTrue(json.loads((self.root/'ipv6-state.json').read_text())['address']);self.assertTrue((self.conf/'ipv6-address-owned').exists())
        result=self.call('IPV6=false; MTU=1420; prepare_ipv6');self.assertEqual(result.returncode,0,result.stderr)
        self.assertFalse(json.loads((self.root/'ipv6-state.json').read_text())['address']);self.assertFalse((self.conf/'ipv6-address-owned').exists())
        for line in (self.root/'trace').read_text().splitlines():self.assertTrue(line.startswith('interface OpkgTun0 '))
    def test_nonzero_interface_receives_only_its_own_ipv6_address(self):
        (self.conf/'native-interface').write_text('OpkgTun7\n')
        ip=self.root/'bin/ip';ip.write_text(ip.read_text().replace('opkgtun0','opkgtun7'))
        result=self.call('IPV6=true; MTU=1420; prepare_ipv6');self.assertEqual(result.returncode,0,result.stderr)
        self.assertTrue((self.conf/'ipv6-address-owned').exists())
        self.assertIn('interface OpkgTun7 ipv6 address', (self.root/'trace').read_text())

    def test_preexisting_address_is_not_removed_as_owned(self):
        self.state['address']=True;self.save()
        self.assertEqual(self.call('IPV6=true; MTU=1420; prepare_ipv6').returncode,0)
        self.assertFalse((self.conf/'ipv6-address-owned').exists())
        self.assertEqual(self.call('IPV6=false; MTU=1420; prepare_ipv6').returncode,0)
        self.assertTrue(json.loads((self.root/'ipv6-state.json').read_text())['address'])
        self.assertFalse((self.root/'trace').exists())
    def test_failed_creation_can_be_rolled_back_without_blocking_ipv4(self):
        self.state['fail']=True;self.save()
        self.assertNotEqual(self.call('IPV6=true; MTU=1420; prepare_ipv6').returncode,0)
        self.assertEqual(self.call('IPV6=false; MTU=1420; prepare_ipv6').returncode,0)
        self.assertFalse((self.conf/'ipv6-address-owned').exists())
    def test_foreign_interface_low_mtu_and_missing_nat_are_refused(self):
        (self.conf/'native-interface').write_text('foreign')
        self.assertNotEqual(self.call('IPV6=true; MTU=1420; prepare_ipv6').returncode,0)
        (self.conf/'native-interface').write_text('OpkgTun0\n')
        self.assertNotEqual(self.call('IPV6=true; MTU=1000; prepare_ipv6').returncode,0)
        (self.root/'nat-unavailable').touch()
        self.assertNotEqual(self.call('IPV6=true; MTU=1420; prepare_ipv6').returncode,0)
        self.assertFalse((self.root/'trace').exists())
    def test_boolean_setting_is_explicit_and_legacy_default_is_disabled(self):
        base={'bypass':'obfuscation','strategy':'dual-rolling-tunnel','sni':'google.com','mtu':1420}
        def validate(value):return self.call('printf \'%s\' '+"'"+json.dumps(value)+"'"+' | validate_connection')
        result=validate(base);self.assertEqual(result.returncode,0,result.stderr);self.assertFalse(json.loads(result.stdout)['ipv6'])
        for value in [None,'true',1,{}]:self.assertNotEqual(validate({**base,'ipv6':value}).returncode,0)
        self.assertNotEqual(validate({**base,'ipv6':True,'mtu':1279}).returncode,0)
        result=validate({**base,'ipv6':True});self.assertEqual(result.returncode,0,result.stderr);self.assertTrue(json.loads(result.stdout)['ipv6'])

    def test_firmware_hook_queues_owned_supervisor_work_without_mutating_netfilter(self):
        run=self.root/'var/run/fptn-keenetic';run.mkdir(parents=True)
        (self.conf/'connection.json').write_text('{"ipv6":true}')
        result=self.call('supervisor_owned() { return 0; }; sync_ipv6')
        self.assertEqual(result.returncode,0,result.stderr);self.assertTrue((run/'ipv6-resync').exists())
        self.assertFalse((self.root/'nat-actions').exists())
        result=self.call('IPV6=true; restore_ipv6; restore_ipv6')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual((self.root/'nat-actions').read_text().splitlines(),['apply'])
        self.assertFalse((run/'ipv6-resync').exists())
        result=self.call('supervisor_owned() { return 1; }; sync_ipv6')
        self.assertEqual(result.returncode,0,result.stderr);self.assertFalse((run/'ipv6-resync').exists())
        self.assertEqual((self.root/'nat-actions').read_text().splitlines(),['apply'])
