import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import unittest
import test_service

SCRIPT=Path(__file__).resolve().parents[1]/'scripts/fptn-native-service.sh'

class NativeServiceTest(unittest.TestCase):
    setUp=test_service.ServiceTest.setUp
    state=test_service.ServiceTest.state

    def cmd(self,*args):
        return subprocess.run(['sh',str(SCRIPT),*args],env=self.env,text=True,capture_output=True,timeout=25)

    def test_no_custom_routing_dns_or_firewall_commands(self):
        source=SCRIPT.read_text()
        for forbidden in ['iptables ', 'ip -4 rule ', 'ip -4 route add', 'normalize_cidrs', 'nslookup ', 'ip route default']:
            self.assertNotIn(forbidden,source)
        self.assertIn('--keenetic-native',source)

    def test_interface_setup_refuses_foreign_and_preserves_existing_user_config(self):
        ndmc=self.bin/'ndmc'; created=self.root/'created'; trace=self.root/'ndmc-trace'
        ndmc.write_text(f'''#!/bin/sh
if [ "$*" = '-c show interface OpkgTun0' ]; then [ -f '{created}' ]; exit $?; fi
printf '%s\\n' "$*" >> '{trace}'
touch '{created}'
'''); ndmc.chmod(0o700)
        result=self.cmd('setup-interface')
        self.assertEqual(result.returncode,0,result.stderr)
        commands=trace.read_text()
        self.assertIn('interface OpkgTun0 no ip global',commands)
        self.assertIn('interface OpkgTun0 ip address 10.0.0.1 255.255.255.255',commands)
        self.assertNotIn('ip route default',commands)
        again=self.cmd('setup-interface'); self.assertEqual(again.returncode,0,again.stderr)
        self.assertEqual(trace.read_text(),commands)
        (self.root/'opt/etc/fptn/native-interface').unlink()
        refused=self.cmd('setup-interface'); self.assertNotEqual(refused.returncode,0)
        self.assertEqual(trace.read_text(),commands)

    def test_native_supervisor_start_stop_does_not_modify_network(self):
        compiler=shutil.which('cc')
        if not compiler: self.skipTest('C compiler unavailable')
        root=self.root/'opt'; lib=root/'libexec/fptn'; conf=root/'etc/fptn'
        lib.mkdir(parents=True); conf.mkdir(parents=True)
        source=self.root/'client.c'
        source.write_text(r'''#include <signal.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>
volatile sig_atomic_t stopped=0;
void stop(int s){stopped=1;}
int main(int argc,char**argv){
 int native=0,name=0; for(int i=1;i<argc;i++){
  if(!strcmp(argv[i],"--keenetic-native")) native=1;
  if(!strcmp(argv[i],"opkgtun0")) name=1;
 }
 if(!native || !name) return 3;
 signal(SIGTERM,stop); puts("Connection #1 READY"); fflush(stdout);
 while(!stopped) sleep(1); return 0;
}
''')
        subprocess.run([compiler,str(source),'-o',str(lib/'fptn-client-cli')],check=True,capture_output=True)
        (lib/'brotli').write_text('unused')
        (lib/'SHA256SUMS').write_text(''.join(hashlib.sha256((lib/f).read_bytes()).hexdigest()+'  '+f+'\n' for f in ['fptn-client-cli','brotli']))
        for key,value in {'token':'secret-token','server':'CZ','native-interface':'OpkgTun0','native-setup-complete':''}.items():
            (conf/key).write_text(value+'\n')
        mock=(self.bin/'ip').read_text()
        mock=mock.replace("    if a[1:3]==['route','show']:","    if a[1:3]==['route','show'] and '30091' in a:",1)
        mock=mock.replace("    mutation()\n    if a[1]=='rule':", "    if a[1:3]==['route','show']: done(output='default via 100.64.0.2 dev eth2.2\\n')\n    mutation()\n    if a[1]=='rule':")
        (self.bin/'ip').write_text(mock)
        try:
            started=self.cmd('start'); self.assertEqual(started.returncode,0,started.stdout+started.stderr)
            self.assertIn('FPTN native',started.stdout)
            self.assertNotIn('secret-token',started.stdout+started.stderr)
            self.assertEqual(self.state()['mutations'],0)
        finally: stopped=self.cmd('stop')
        self.assertEqual(stopped.returncode,0,stopped.stdout+stopped.stderr)
        self.assertEqual(self.state()['mutations'],0)
        self.assertFalse((root/'var/run/fptn-keenetic').exists())
