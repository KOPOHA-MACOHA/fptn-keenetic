"""Model kernel route/address snapshots without contacting public probe hosts."""
import json,os,subprocess,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
MOCK=r'''#!/usr/bin/env python3
import json,os,sys
from pathlib import Path
s=json.loads(Path(os.environ['UPLINK_STATE']).read_text());a=sys.argv[1:]
with open(os.environ['UPLINK_TRACE'],'a') as f:f.write(' '.join(a)+'\n')
if a==['-4','route','show','table','main','default']:print(s['routes']);sys.exit(0)
name=a[a.index('dev')+1] if 'dev' in a else ''
if 'link' in a:
 data=s['links'].get(name)
 if data:print(data);sys.exit(0)
 sys.exit(1)
if 'addr' in a:print(s['addresses'].get(name,''));sys.exit(0)
raise SystemExit(99)
'''
class UplinkTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.state={'routes':'default via 192.0.2.1 dev eth0 src 192.0.2.2 metric 100','links':{'eth0':'2: eth0: <BROADCAST,UP,LOWER_UP> mtu 1500 state UP','ppp0':'4: ppp0: <POINTOPOINT,UP,LOWER_UP> mtu 1492 state UNKNOWN'},'addresses':{'eth0':'2: eth0 inet 192.0.2.2/24 scope global eth0','ppp0':'4: ppp0 inet 198.51.100.10 peer 198.51.100.1/32 scope global ppp0'}}
        (self.root/'ip').write_text(MOCK);(self.root/'ip').chmod(0o700)
        self.env={**os.environ,'PATH':str(self.root)+':'+os.environ['PATH'],'UPLINK_STATE':str(self.root/'state.json'),'UPLINK_TRACE':str(self.root/'trace')}
    def call(self,selected=''):
        (self.root/'state.json').write_text(json.dumps(self.state));return subprocess.run(['sh',str(ROOT/'scripts/fptn-uplink.sh'),selected],env=self.env,text=True,capture_output=True,timeout=10)
    def value(self,selected=''):
        result=self.call(selected);self.assertEqual(result.returncode,0,result.stderr);return json.loads(result.stdout)
    def test_metric_order_and_point_to_point_without_gateway(self):
        self.state['routes']+='\ndefault dev ppp0 metric 5'
        data=self.value();self.assertEqual(data['interface'],'ppp0');self.assertEqual(data['gateway'],'');self.assertEqual(data['address'],'198.51.100.10')
        self.assertEqual(self.value('eth0')['interface'],'eth0')
    def test_unusable_and_recursive_routes_never_win(self):
        self.state['routes']='unreachable default metric 1\ndefault dev opkgtun0 metric 0\ndefault dev ppp0 linkdown metric 1\n'+self.state['routes']
        self.assertEqual(self.value()['interface'],'eth0')
        self.state['links']['eth0']='2: eth0: <BROADCAST> mtu 1500 state DOWN'
        self.assertNotEqual(self.call().returncode,0)
    def test_new_address_gateway_or_recreated_link_changes_signature(self):
        original=self.value()['signature'];self.state['addresses']['eth0']='2: eth0 inet 192.0.2.3/24 scope global eth0'
        self.assertNotEqual(self.value()['signature'],original)
        self.state['links']['eth0']='7: eth0: <UP,LOWER_UP> mtu 1500 state UP'
        self.assertEqual(self.value()['index'],7)
        self.state['routes']='default via 192.0.2.254 dev eth0 metric 1'
        self.assertEqual(self.value()['gateway'],'192.0.2.254')
    def test_preferred_source_must_still_be_assigned_and_not_deprecated(self):
        self.state['addresses']['eth0']='2: eth0 inet 192.0.2.3/24 scope global eth0\n2: eth0 inet 192.0.2.2/24 scope global secondary eth0'
        self.assertEqual(self.value()['address'],'192.0.2.2')
        self.state['addresses']['eth0']=self.state['addresses']['eth0'].replace('secondary','deprecated')
        self.assertEqual(self.value()['address'],'192.0.2.3')
    def test_explicit_interface_never_uses_different_wan_or_executes_input(self):
        for interface in ['eth9','opkgtun0','eth0;touch x','-o','x'*16]:
            with self.subTest(interface=interface):self.assertNotEqual(self.call(interface).returncode,0)
        trace=(self.root/'trace').read_text();self.assertNotIn(' route get ',trace);self.assertNotIn(' add ',trace);self.assertNotIn(' replace ',trace)
