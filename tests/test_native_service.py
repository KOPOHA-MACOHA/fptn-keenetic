import hashlib
import json
import os
import signal
from pathlib import Path
import shutil
import subprocess
import unittest
import time
import test_service
import client_fixture

SCRIPT=Path(__file__).resolve().parents[1]/'scripts/fptn-native-service.sh'

class NativeServiceTest(unittest.TestCase):
    setUp=test_service.ServiceTest.setUp
    state=test_service.ServiceTest.state

    def cmd(self,*args):
        return subprocess.run(['sh',str(SCRIPT),*args],env=self.env,text=True,capture_output=True,timeout=25)

    def test_autostart_owned_init_persists_without_starting_or_network_changes(self):
        root=self.prepare_native_client()
        result=self.cmd('enable');self.assertEqual(result.returncode,0,result.stderr)
        init=root/'etc/init.d/S90fptn'
        self.assertTrue(init.exists()); self.assertTrue((root/'etc/fptn/autostart').exists())
        subprocess.run(['sh','-n',str(init)],check=True)
        self.assertFalse((root/'var/run/fptn-keenetic').exists())
        self.assertEqual(self.state()['mutations'],0)
        self.assertEqual(self.cmd('disable').returncode,0)
        self.assertFalse((root/'etc/fptn/autostart').exists())
        init.write_text('# foreign init\n')
        self.assertNotEqual(self.cmd('enable').returncode,0)
        self.assertEqual(init.read_text(),'# foreign init\n')

    def test_boot_start_returns_promptly_and_waits_for_initial_connection(self):
        root=self.prepare_native_client()
        (root/'etc/fptn/autostart').touch()
        marker=self.root/'fail-first';marker.touch();self.env['FPTN_MOCK_FAIL_ONCE']=str(marker)
        started=time.monotonic()
        try:
            result=self.cmd('_boot');self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertLess(time.monotonic()-started,3)
            self.wait_for(lambda:(root/'var/run/fptn-keenetic/ready').exists(),timeout=20)
            self.assertEqual(self.state()['mutations'],0)
        finally:self.cmd('stop')

    def test_persistent_supervisor_does_not_keep_web_operation_marker(self):
        root=self.prepare_native_client();marker='9'*32;self.env['FPTN_OPERATION_ID']=marker
        try:
            self.assertEqual(self.cmd('start').returncode,0)
            for name in ['supervisor.pid','client.pid']:
                pid=(root/'var/run/fptn-keenetic'/name).read_text().split()[0]
                self.assertNotIn(('FPTN_OPERATION_ID='+marker).encode(),Path('/proc/'+pid+'/environ').read_bytes())
        finally:self.cmd('stop')

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

    def prepare_native_client(self):
        compiler=shutil.which('cc')
        if not compiler: self.skipTest('C compiler unavailable')
        root=self.root/'opt'; lib=root/'libexec/fptn'; conf=root/'etc/fptn'
        lib.mkdir(parents=True); conf.mkdir(parents=True)
        client_fixture.install(root)
        source=self.root/'client.c'
        source.write_text(r'''#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
volatile sig_atomic_t stopped=0;
void stop(int s){stopped=1;}
int main(int argc,char**argv){
 if(argc==2 && !strcmp(argv[1],"--help")){fputs(HELP_FIXTURE,stdout);return 0;}
 if(argc==2 && !strcmp(argv[1],"--version")){puts("0.4.0");return 0;}
 int native=0,name=0; for(int i=1;i<argc;i++){
  if(!strcmp(argv[i],"--keenetic-native")) native=1;
  if(!strcmp(argv[i],"opkgtun0")) name=1;
 }
 if(!native || !name) return 3;
 char *record=getenv("FPTN_MOCK_ARGS");
 if(record){FILE *f=fopen(record,"w");if(!f)return 4;for(int i=1;i<argc;i++)fprintf(f,"%s\n",argv[i]);fclose(f);}
 signal(SIGTERM,stop);
 puts("KEENETIC_SELECTED: CZ");fflush(stdout);
 char *trace=getenv("FPTN_MOCK_TRANSPORT_TRACE");char *method="",*sni="";
 for(int i=1;i+1<argc;i++){if(!strcmp(argv[i],"--bypass-method"))method=argv[i+1];if(!strcmp(argv[i],"--sni"))sni=argv[i+1];}
 if(trace){FILE*f=fopen(trace,"a");if(f){fprintf(f,"%s %s\n",method,sni);fclose(f);}}
 char *required=getenv("FPTN_MOCK_REQUIRED_BYPASS");
 if(getenv("FPTN_MOCK_AUTH_DENIED")){
  for(int i=1;i+1<argc;i++)if(!strcmp(argv[i],"--keenetic-probe-report")){FILE*f=fopen(argv[i+1],"w");if(f){fputs("{\"complete\":true,\"results\":[{\"name\":\"CZ\",\"code\":401,\"ok\":false}]}",f);fclose(f);}}
  return 6;
 }
 if(getenv("FPTN_MOCK_ALWAYS_FAIL") || (required && strcmp(required,method)))return 6;

 char *fail_once=getenv("FPTN_MOCK_FAIL_ONCE");
 if(fail_once && !access(fail_once,F_OK)){unlink(fail_once);return 5;}
 char *suppress=getenv("FPTN_MOCK_SUPPRESS_READY");
 if(!suppress || access(suppress,F_OK)) {puts("Connection #1 READY"); fflush(stdout);}
 while(!stopped) sleep(1); return 0;
}
'''.replace('HELP_FIXTURE',json.dumps(client_fixture.help_text()+('\n--keenetic-native-transport\n' if self.env.get('FPTN_MOCK_NATIVE_TRANSPORT') else ''))))
        subprocess.run([compiler,str(source),'-o',str(lib/'fptn-client-cli')],check=True,capture_output=True)
        (lib/'brotli').write_text('unused')
        (lib/'SHA256SUMS').write_text(''.join(hashlib.sha256((lib/f).read_bytes()).hexdigest()+'  '+f+'\n' for f in ['fptn-client-cli','brotli']))
        for key,value in {'token':'secret-token','server':'CZ','native-interface':'OpkgTun0','native-setup-complete':''}.items():
            (conf/key).write_text(value+'\n')
        shutil.copyfile(SCRIPT.parent/'fptn-uplink.sh',lib/'uplink.sh')
        mock=(self.bin/'ip').read_text()
        mock=mock.replace("if cmd=='ip':\n", "if cmd=='ip':\n    if 'addr' in a: done(output='2: eth2.2    inet 100.64.0.100/16 scope global eth2.2\\n')\n    if 'link' in a and a[-1]=='eth2.2': done(output='2: eth2.2: <UP,LOWER_UP> mtu 1500 state UP\\n')\n",1)
        mock=mock.replace("    if 'link' in a:\n", "    if 'link' in a and 'opkgtun0' in a: done(output='52: opkgtun0: <UP> mtu '+str(s.get('mtu',1420))+' qdisc fq_codel\\n')\n    if 'link' in a:\n",1)
        mock=mock.replace("    if a[1:3]==['route','show']:","    if a[1:3]==['route','show'] and '30091' in a:",1)
        mock=mock.replace("    mutation()\n    if a[1]=='rule':", "    if a[1:3]==['route','show']: done(output='default via 100.64.0.2 dev eth2.2\\n')\n    mutation()\n    if a[1]=='rule':")
        (self.bin/'ip').write_text(mock)
        self.env['FPTN_MOCK_SUPPRESS_READY']=str(self.root/'suppress-ready')
        self.env['FPTN_MOCK_ARGS']=str(self.root/'client-args')
        return root

    def wait_for(self, predicate, timeout=15):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            if predicate(): return
            time.sleep(.1)
        self.fail('Timed out waiting for supervisor transition')

    def test_native_supervisor_start_stop_does_not_modify_network(self):
        root=self.prepare_native_client()
        try:
            started=self.cmd('start'); self.assertEqual(started.returncode,0,started.stdout+started.stderr)
            self.assertIn('FPTN native',started.stdout)
            self.assertNotIn('secret-token',started.stdout+started.stderr)
            self.assertEqual(self.state()['mutations'],0)
        finally: stopped=self.cmd('stop')
        self.assertEqual(stopped.returncode,0,stopped.stdout+stopped.stderr)
        self.assertEqual(self.state()['mutations'],0)
        self.assertFalse((root/'var/run/fptn-keenetic').exists())

    def test_restart_after_crash_and_no_stale_ready(self):
        root=self.prepare_native_client(); run=root/'var/run/fptn-keenetic'
        def pid():
            try: return int((run/'client.pid').read_text().split()[0])
            except (FileNotFoundError, IndexError): return None
        try:
            started=self.cmd('start'); self.assertEqual(started.returncode,0,started.stdout+started.stderr)
            old=pid(); os.kill(old,signal.SIGKILL)
            self.wait_for(lambda: pid() is not None and pid()!=old and (run/'ready').exists())
            self.assertIn('READY получен',self.cmd('status').stdout)
            self.assertEqual(self.state()['mutations'],0)
            (self.root/'suppress-ready').touch()
            old=pid(); os.kill(old,signal.SIGKILL)
            self.wait_for(lambda: pid() is not None and pid()!=old)
            self.assertFalse((run/'ready').exists(), 'Previous attempt READY must not carry over')
            status=self.cmd('status')
            self.assertNotIn('READY получен',status.stdout)
            self.assertIn('подключение',status.stdout)
        finally: stopped=self.cmd('stop')
        self.assertEqual(stopped.returncode,0,stopped.stdout+stopped.stderr)
        self.assertFalse(run.exists())
        self.assertEqual(self.state()['mutations'],0)

    def test_stop_during_backoff_does_not_restart(self):
        root=self.prepare_native_client(); run=root/'var/run/fptn-keenetic'
        try:
            started=self.cmd('start'); self.assertEqual(started.returncode,0,started.stdout+started.stderr)
            old=int((run/'client.pid').read_text().split()[0]); os.kill(old,signal.SIGKILL)
            self.wait_for(lambda: (run/'state').exists() and 'повтор через' in (run/'state').read_text())
            stopped=self.cmd('stop')
            self.assertEqual(stopped.returncode,0,stopped.stdout+stopped.stderr)
            self.assertFalse(run.exists())
            time.sleep(1)
            self.assertFalse(run.exists())
            self.assertEqual(self.state()['mutations'],0)
        finally: self.cmd('stop')

    def test_first_connection_failure_exits_without_retry_loop(self):
        root=self.prepare_native_client(); (self.root/'suppress-ready').touch()
        # Exit before READY, exercising startup failure without a 60s timeout.
        client=root/'libexec/fptn/fptn-client-cli'
        client.write_text('#!/bin/sh\nexit 9\n'); client.chmod(0o700)
        lib=client.parent
        (lib/'SHA256SUMS').write_text(''.join(hashlib.sha256((lib/f).read_bytes()).hexdigest()+'  '+f+'\n' for f in ['fptn-client-cli','brotli']))
        try:
            started=self.cmd('start'); self.assertNotEqual(started.returncode,0)
            self.assertFalse((root/'var/run/fptn-keenetic').exists())
            self.assertEqual(self.state()['mutations'],0)
        finally: self.cmd('stop')

    def test_configured_browser_strategy_and_sni_reach_client(self):
        root=self.prepare_native_client()
        settings={'bypass':'sni-spoofing-firefox-151','strategy':'triple-rolling-tunnel','sni':'example.org'}
        (root/'etc/fptn/connection.json').write_text(json.dumps(settings))
        try:
            started=self.cmd('start'); self.assertEqual(started.returncode,0,started.stdout+started.stderr)
            args=(self.root/'client-args').read_text().splitlines()
            for flag,value in [('--bypass-method',settings['bypass']),('--connection-strategy',settings['strategy']),('--sni',settings['sni'])]:
                self.assertEqual(args[args.index(flag)+1],value)
            self.assertEqual(self.state()['mutations'],0)
        finally: self.cmd('stop')

    def test_policy_reselects_after_failure_before_first_ready(self):
        root=self.prepare_native_client()
        (root/'etc/fptn/server-policy.json').write_text(json.dumps({'mode':'auto','favorites':['CZ'],'excluded':[]}))
        marker=self.root/'fail-once';marker.touch();self.env['FPTN_MOCK_FAIL_ONCE']=str(marker)
        try:
            started=self.cmd('start');self.assertEqual(started.returncode,0,started.stdout+started.stderr)
            args=(self.root/'client-args').read_text().splitlines()
            self.assertIn('--keenetic-auto-select',args)
            self.assertIn('--keenetic-server-policy',args)
            self.assertIn('--keenetic-skip-file',args)
            self.assertEqual(json.loads((root/'var/run/fptn-keenetic/failed-servers.json').read_text()),['CZ'])
            self.assertEqual(self.state()['mutations'],0)
        finally:self.cmd('stop')

    def test_structured_status_uses_owned_process_and_current_link_state(self):
        root=self.prepare_native_client()
        data=json.loads(self.cmd('status-json').stdout)
        self.assertEqual(data['state'],'stopped');self.assertTrue(data['canStart']);self.assertFalse(data['canStop'])
        try:
            started=self.cmd('start');self.assertEqual(started.returncode,0,started.stdout+started.stderr)
            run=root/'var/run/fptn-keenetic'
            (run/'link.json').write_text('{"state":"connected"}')
            data=json.loads(self.cmd('status-json').stdout)
            self.assertEqual(data['state'],'running');self.assertTrue(data['connected'])
            self.assertFalse(data['canStart']);self.assertTrue(data['canStop'])
            (run/'link.json').write_text('{"state":"reconnecting"}')
            data=json.loads(self.cmd('status-json').stdout)
            self.assertEqual(data['state'],'reconnecting');self.assertFalse(data['connected'])
            self.assertEqual(self.state()['mutations'],0)
        finally:self.cmd('stop')

    def test_stop_handles_replaced_executable_without_hanging(self):
        root=self.prepare_native_client();run=root/'var/run/fptn-keenetic'
        try:
            started=self.cmd('start');self.assertEqual(started.returncode,0,started.stdout+started.stderr)
            pid=int((run/'client.pid').read_text().split()[0])
            binary=root/'libexec/fptn/fptn-client-cli';replacement=binary.with_suffix('.new')
            replacement.write_bytes(binary.read_bytes());replacement.chmod(0o700);replacement.replace(binary)
            self.assertTrue(os.readlink(f'/proc/{pid}/exe').endswith(' (deleted)'))
            stopped=self.cmd('stop');self.assertEqual(stopped.returncode,0,stopped.stdout+stopped.stderr)
            self.assertFalse(run.exists());self.assertEqual(self.state()['mutations'],0)
        finally:self.cmd('stop')

    def test_stop_handles_tracked_client_without_live_supervisor(self):
        root=self.prepare_native_client();run=root/'var/run/fptn-keenetic';run.mkdir(parents=True)
        binary=root/'libexec/fptn/fptn-client-cli'
        process=subprocess.Popen([str(binary),'--keenetic-native','--tun-interface-name','opkgtun0'],env=self.env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        try:
            stamp=Path(f'/proc/{process.pid}/stat').read_text().rsplit(') ',1)[1].split()[19]
            boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
            (run/'client.pid').write_text(f'{process.pid} {stamp} {boot}\n')
            (run/'supervisor.pid').write_text(f'999999999 1 {boot}\n')
            stopped=self.cmd('stop');self.assertEqual(stopped.returncode,0,stopped.stdout+stopped.stderr)
            process.wait(timeout=5);self.assertFalse(run.exists())
        finally:
            if process.poll() is None:process.kill();process.wait(timeout=5)

    def test_stop_refuses_foreign_executable_even_with_matching_pid_stamp(self):
        root=self.prepare_native_client();run=root/'var/run/fptn-keenetic';run.mkdir(parents=True)
        process=subprocess.Popen(['sleep','30'])
        try:
            stamp=Path(f'/proc/{process.pid}/stat').read_text().rsplit(') ',1)[1].split()[19]
            boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
            (run/'client.pid').write_text(f'{process.pid} {stamp} {boot}\n')
            stopped=self.cmd('stop');self.assertNotEqual(stopped.returncode,0)
            self.assertIsNone(process.poll());self.assertTrue(run.exists())
        finally:process.terminate();process.wait(timeout=5)

    def test_stop_kills_verified_stuck_supervisor_only_after_client_is_gone(self):
        root=self.prepare_native_client();run=root/'var/run/fptn-keenetic';run.mkdir(parents=True)
        controller=root/'bin/fptnctl';controller.parent.mkdir(parents=True)
        controller.write_text('#!/bin/sh\ntrap "" TERM INT HUP\n: > "$FPTN_PREFIX/supervisor-test-ready"\nwhile :; do sleep 1; done\n');controller.chmod(0o700)
        process=subprocess.Popen(['sh',str(controller),'_run'],env=self.env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        try:
            self.wait_for(lambda:(root/'supervisor-test-ready').exists())
            stamp=Path(f'/proc/{process.pid}/stat').read_text().rsplit(') ',1)[1].split()[19]
            boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
            (run/'supervisor.pid').write_text(f'{process.pid} {stamp} {boot}\n')
            stopped=self.cmd('stop');self.assertEqual(stopped.returncode,0,stopped.stdout+stopped.stderr)
            process.wait(timeout=5);self.assertEqual(process.returncode,-signal.SIGKILL)
            self.assertFalse(run.exists());self.assertEqual(self.state()['mutations'],0)
        finally:
            if process.poll() is None:process.kill();process.wait(timeout=5)

    def test_staged_stop_recognizes_installed_supervisor_through_opt_symlink(self):
        root=self.prepare_native_client()
        alias=self.root/'opt-link';alias.symlink_to(root,target_is_directory=True)
        self.env['FPTN_PREFIX']=str(alias)
        installed=root/'bin/fptnctl';installed.parent.mkdir(parents=True)
        installed.write_text(SCRIPT.read_text());installed.chmod(0o700)
        try:
            started=subprocess.run(['sh',str(alias/'bin/fptnctl'),'start'],env=self.env,text=True,capture_output=True,timeout=25)
            self.assertEqual(started.returncode,0,started.stdout+started.stderr)
            # self.cmd uses a different staged script, as the updater does.
            stopped=self.cmd('stop');self.assertEqual(stopped.returncode,0,stopped.stdout+stopped.stderr)
            self.assertFalse((root/'var/run/fptn-keenetic').exists())
            self.assertEqual(self.state()['mutations'],0)
        finally:self.cmd('stop')
