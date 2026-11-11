"""Real IPv6 forwarding, SNAT and reply conntrack in isolated Linux namespaces."""
import json,os,shutil,socket,subprocess,tempfile,time,unittest,uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
class IPv6NatKernelTest(unittest.TestCase):
    def test_lan_request_reply_and_rebuild_with_only_owned_nat_rules(self):
        if os.geteuid()!=0 or not shutil.which('ip6tables'):self.skipTest('Root/ip6tables required in Actions')
        prefix='fn6-'+uuid.uuid4().hex[:7];names=[prefix+'c',prefix+'r',prefix+'s'];client,router,server=names
        made=[]
        for name in names:
            r=subprocess.run(['ip','netns','add',name],capture_output=True,text=True)
            if r.returncode:
                for previous in made:subprocess.run(['ip','netns','del',previous],capture_output=True)
                if os.environ.get('FPTN_REQUIRE_NETNS')=='1':self.fail(r.stderr)
                self.skipTest('Namespaces unavailable')
            made.append(name)
        self.addCleanup(lambda:[subprocess.run(['ip','netns','del',n],capture_output=True) for n in reversed(names)])
        def ip(ns,*args):return subprocess.run(['ip','-n',ns,*args],check=True,capture_output=True,text=True)
        def run(ns,args,**kwargs):return subprocess.run(['ip','netns','exec',ns,*args],**kwargs)
        def table(*args):return run(router,['ip6tables','-w','5','-t','nat',*args],capture_output=True,text=True,check=True)
        ip(router,'link','add','lan0','type','veth','peer','name','c0');ip(router,'link','set','c0','netns',client)
        ip(router,'link','add','opkgtun7','type','veth','peer','name','s0');ip(router,'link','set','s0','netns',server)
        for ns in names:ip(ns,'link','set','lo','up')
        for ns,iface,addr in [(client,'c0','fd10::2/64'),(router,'lan0','fd10::1/64'),(router,'opkgtun7','fd42:6670:746e::1/128'),(server,'s0','2001:db8:1::2/64')]:
            ip(ns,'link','set',iface,'up');ip(ns,'-6','addr','add',addr,'dev',iface,'nodad')
        ip(client,'-6','route','add','default','via','fd10::1','dev','c0')
        ip(router,'-6','route','add','2001:db8:1::2/128','dev','opkgtun7')
        ip(server,'-6','route','add','fd42:6670:746e::1/128','dev','s0')
        run(router,['sysctl','-q','-w','net.ipv6.conf.all.forwarding=1'],capture_output=True,check=True)
        table('-N','UNRELATED6');table('-A','POSTROUTING','-j','UNRELATED6')
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);conf=root/'etc/fptn';conf.mkdir(parents=True);(conf/'native-interface').write_text('OpkgTun7\n')
            env={**os.environ,'FPTN_PREFIX':str(root)}
            def helper(action,success=True):
                result=run(router,['sh',str(ROOT/'scripts/fptn-ipv6.sh'),action],env=env,capture_output=True,text=True,timeout=20)
                if success:self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                return result
            # No generic route back to LAN exists on the remote side; replies require SNAT.
            script=root/'echo.py';script.write_text('''import socket,sys
s=socket.socket(socket.AF_INET6,socket.SOCK_STREAM);s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);s.bind(('2001:db8:1::2',32189));s.listen(8)
open(sys.argv[1],'w').write('ready')
while True:
 c,a=s.accept()
 with open(sys.argv[2],'a') as f:f.write(a[0]+'\\n')
 with c:c.sendall(c.recv(4096))
''')
            proc=subprocess.Popen(['ip','netns','exec',server,'python3',str(script),str(root/'ready'),str(root/'peers')],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
            self.addCleanup(lambda:proc.poll() is None and proc.terminate())
            deadline=time.monotonic()+5
            while not (root/'ready').exists() and time.monotonic()<deadline:time.sleep(.02)
            self.assertTrue((root/'ready').exists())
            request=['python3','-c',"import socket;s=socket.socket(socket.AF_INET6,socket.SOCK_STREAM);s.settimeout(2);s.connect(('2001:db8:1::2',32189));s.sendall(b'fptn-ipv6-test');assert s.recv(64)==b'fptn-ipv6-test'"]
            helper('apply');helper('apply')
            response=run(client,request,capture_output=True,text=True,timeout=5);self.assertEqual(response.returncode,0,response.stderr)
            self.assertEqual((root/'peers').read_text().strip(),'fd42:6670:746e::1')
            # Model a firmware rebuild of the owned chain; ownership survives and is restored.
            table('-D','POSTROUTING','-o','opkgtun7','-j','FPTN_KN_6');table('-F','FPTN_KN_6');table('-X','FPTN_KN_6')
            helper('apply');self.assertEqual(run(client,request,capture_output=True,timeout=5).returncode,0)
            helper('remove');helper('remove')
            remaining=table('-S').stdout;self.assertNotIn('FPTN_KN_6',remaining);self.assertIn('UNRELATED6',remaining)
            self.assertNotEqual(run(client,request,capture_output=True,timeout=5).returncode,0)
            # An existing foreign chain cannot be claimed or flushed.
            table('-N','FPTN_KN_6');table('-A','FPTN_KN_6','-j','RETURN')
            self.assertNotEqual(helper('apply',False).returncode,0);self.assertIn('-j RETURN',table('-S','FPTN_KN_6').stdout)
            proc.terminate();proc.wait(timeout=5)
