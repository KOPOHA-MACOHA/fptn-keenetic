"""Run the real supervisor with changing read-only kernel snapshots and real child PIDs."""
import json,os,subprocess,time,unittest
from pathlib import Path
import test_native_service
MOCK=r'''#!/usr/bin/env python3
import json,os,sys
from pathlib import Path
s=json.loads(Path(os.environ['WAN_SNAPSHOT']).read_text());a=sys.argv[1:]
if a==['-4','route','show','table','main','default']:print(s['routes']);sys.exit(0)
name=a[a.index('dev')+1] if 'dev' in a else ''
if name=='opkgtun0':print('52: opkgtun0: <UP,LOWER_UP> mtu 1420 state UP');sys.exit(0)
if 'link' in a:
 if name in s['links']:print(s['links'][name]);sys.exit(0)
 sys.exit(1)
if 'addr' in a:print(s['addresses'].get(name,''));sys.exit(0)
raise SystemExit(99)
'''
class WanFailoverTest(unittest.TestCase):
    setUp=test_native_service.NativeServiceTest.setUp
    cmd=test_native_service.NativeServiceTest.cmd
    prepare_native_client=test_native_service.NativeServiceTest.prepare_native_client
    wait_for=test_native_service.NativeServiceTest.wait_for
    def prepare(self):
        self.env['FPTN_MOCK_NATIVE_TRANSPORT']='1';root=self.prepare_native_client()
        self.snapshot={'routes':'default via 192.0.2.1 dev wan_a metric 10','links':{'wan_a':'2: wan_a: <UP,LOWER_UP> mtu 1500 state UP','wan_b':'3: wan_b: <UP,LOWER_UP> mtu 1492 state UNKNOWN'},'addresses':{'wan_a':'2: wan_a inet 192.0.2.2/24 scope global wan_a','wan_b':'3: wan_b inet 198.51.100.2 peer 198.51.100.1/32 scope global wan_b'}}
        self.env['WAN_SNAPSHOT']=str(self.root/'wan.json');self.save()
        (self.bin/'ip').write_text(MOCK);(self.bin/'ip').chmod(0o700)
        self.addCleanup(lambda:self.cmd('stop'))
        return root
    def save(self):
        stage=self.root/'wan.new';stage.write_text(json.dumps(self.snapshot));stage.replace(self.root/'wan.json')
    def arguments(self):return (self.root/'client-args').read_text().splitlines()
    def test_point_to_point_starts_without_fake_gateway(self):
        self.prepare();self.snapshot['routes']='default dev wan_b metric 1';self.save()
        result=self.cmd('start');self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        args=self.arguments();self.assertEqual(args[args.index('--out-network-interface')+1],'wan_b');self.assertEqual(args[args.index('--gateway-ip')+1],'')
    def test_running_connection_restarts_after_wan_and_address_change(self):
        root=self.prepare();result=self.cmd('start');self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        run=root/'var/run/fptn-keenetic';old=(run/'client.pid').read_text()
        self.snapshot['routes']='default dev wan_b metric 1';self.save()
        self.wait_for(lambda:(run/'client.pid').exists() and (run/'client.pid').read_text()!=old and (run/'ready').exists(),timeout=28)
        args=self.arguments();self.assertEqual(args[args.index('--out-network-interface')+1],'wan_b')
        self.assertEqual(json.loads((run/'failed-servers.json').read_text()),[])
    def test_initial_network_loss_waits_then_recovers_without_another_start(self):
        root=self.prepare();self.snapshot['routes']='';self.save()
        result=self.cmd('start');self.assertEqual(result.returncode,0,result.stdout+result.stderr);self.assertIn('Ожидаем',result.stdout)
        self.assertFalse((self.root/'client-args').exists())
        status=json.loads(self.cmd('status-json').stdout);self.assertEqual(status['state'],'waiting-network');self.assertTrue(status['canStop'])
        self.snapshot['routes']='default dev wan_b metric 1';self.save()
        self.wait_for(lambda:(root/'var/run/fptn-keenetic/ready').exists(),timeout=15)
        self.assertEqual(self.arguments()[self.arguments().index('--out-network-interface')+1],'wan_b')
