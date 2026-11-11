"""Transport options preserve firmware ownership and never rewrite routing."""
import json
from pathlib import Path
import subprocess,unittest
import test_native_service
ROOT=Path(__file__).resolve().parents[1]
class NativeTransportOptionsTest(unittest.TestCase):
    setUp=test_native_service.NativeServiceTest.setUp
    cmd=test_native_service.NativeServiceTest.cmd
    state=test_native_service.NativeServiceTest.state
    prepare_native_client=test_native_service.NativeServiceTest.prepare_native_client
    def settings(self,**values):return {'bypass':'obfuscation','strategy':'dual-rolling-tunnel','sni':'google.com',**values}
    def write(self,root,**values):(root/'etc/fptn/connection.json').write_text(json.dumps(self.settings(**values)))
    def mock_ndmc(self):
        p=self.bin/'ndmc';p.write_text('''#!/usr/bin/env python3
import json,os,sys
from pathlib import Path
p=Path(os.environ['FPTN_MOCK_STATE']);state=json.loads(p.read_text())
assert sys.argv[1]=='-c'
parts=sys.argv[2].split();assert parts[:4]==['interface','OpkgTun0','ip','mtu'] and len(parts)==5
state['mtu']=int(parts[4]);state['trace'].append(['ndmc',*sys.argv[1:]]);p.write_text(json.dumps(state))
''');p.chmod(0o700)
    def test_legacy_settings_use_actual_firmware_mtu_without_mutation(self):
        root=self.prepare_native_client();state=self.state();state['mtu']=1460;self.state_path.write_text(json.dumps(state))
        try:
            r=self.cmd('start');self.assertEqual(r.returncode,0,r.stdout+r.stderr)
            args=(self.root/'client-args').read_text().splitlines();self.assertEqual(args[args.index('--mtu-size')+1],'1460')
            self.assertEqual(self.state()['mutations'],0);self.assertFalse(any(a[0]=='ndmc' for a in self.state()['trace']))
        finally:self.cmd('stop')
    def test_explicit_mtu_applied_only_to_owned_interface_through_firmware(self):
        root=self.prepare_native_client();self.mock_ndmc();self.write(root,mtu=1360,outInterface='eth2.2')
        try:
            r=self.cmd('start');self.assertEqual(r.returncode,0,r.stdout+r.stderr)
            args=(self.root/'client-args').read_text().splitlines();self.assertEqual(args[args.index('--mtu-size')+1],'1360');self.assertEqual(args[args.index('--out-network-interface')+1],'eth2.2')
            self.assertEqual(self.state()['mtu'],1360);self.assertEqual(self.state()['mutations'],0)
            writes=[a for a in self.state()['trace'] if a[0]=='ndmc'];self.assertEqual(writes,[['ndmc','-c','interface OpkgTun0 ip mtu 1360']])
        finally:self.cmd('stop')
    def test_mismatched_selected_uplink_does_not_fall_back_to_another_interface(self):
        root=self.prepare_native_client();self.write(root,outInterface='eth3')
        try:
            r=self.cmd('start');self.assertEqual(r.returncode,0,r.stdout+r.stderr);self.assertFalse((self.root/'client-args').exists());self.assertIn('Ожидаем',r.stdout);self.assertEqual(self.state()['mutations'],0)
            self.assertTrue((root/'var/run/fptn-keenetic/supervisor.pid').exists())
        finally:self.cmd('stop')
    def test_validation_rejects_bad_mtu_and_injected_or_recursive_interface(self):
        for values in [{'mtu':575},{'mtu':65536},{'mtu':True},{'mtu':1500.5},{'mtu':None},{'outInterface':'eth2.2;id'},{'outInterface':'-x'},{'outInterface':'opkgtun0'},{'outInterface':'x'*16}]:
            with self.subTest(values=values):
                r=subprocess.run(['sh',str(ROOT/'scripts/fptn-native-service.sh'),'validate-settings'],input=json.dumps(self.settings(**values)),env=self.env,capture_output=True,text=True,timeout=10)
                self.assertNotEqual(r.returncode,0)
    def test_cannot_change_mtu_of_foreign_interface(self):
        root=self.prepare_native_client();self.mock_ndmc();(root/'etc/fptn/native-interface').unlink()
        r=self.cmd('set-interface-mtu','1360');self.assertNotEqual(r.returncode,0)
        self.assertFalse(any(a[0]=='ndmc' for a in self.state()['trace']))
