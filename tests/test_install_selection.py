"""Exercise installer TTY/port conflict handling and firmware ownership in isolation."""
import json
import os
from pathlib import Path
import pty
import select
import shutil
import signal
import subprocess
import tempfile
import time
import unittest

ROOT=Path(__file__).resolve().parents[1]

class PortSelectionTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.bin=self.root/'bin';self.bin.mkdir()
        self.conf=self.root/'conf';self.conf.mkdir();self.web=self.root/'web';self.web.mkdir()
        self.fragment=self.root/'95-fptn.conf'
        self.listeners=self.root/'listeners';self.listeners.write_text('')
        mock=self.bin/'netstat';mock.write_text('#!/bin/sh\ncat "$LISTENERS_FILE"\n');mock.chmod(0o700)
        self.env={**os.environ,'PATH':str(self.bin)+':'+os.environ['PATH'],'LISTENERS_FILE':str(self.listeners)}
        text=(ROOT/'scripts/install-web-ui.sh').read_text();text=text[text.index('valid_port() {'):text.index('INIT=\n')]
        self.script=self.root/'test.sh'
        self.script.write_text('set -eu\nfail() { echo "$*" >&2; exit 1; }\nCONF='+str(self.conf)+'\nWEB='+str(self.web)+'\nFRAGMENT='+str(self.fragment)+'\nPORT=${1:-}\n'+text+'\nselect_port\nprintf "SELECTED=%s\\n" "$PORT"\n')
    def occupied(self,*ports):
        self.listeners.write_text(''.join('tcp6 0 0 :::'+str(p)+' :::* LISTEN 123/foreign\n' for p in ports))
    def run_plain(self,port='',**env):
        return subprocess.run(['sh',str(self.script),port],env={**self.env,**env},capture_output=True,text=True,timeout=5,start_new_session=True)
    def interactive(self,answers,port=''):
        pid,fd=pty.fork()
        if pid==0:os.execvpe('sh',['sh',str(self.script),port],self.env)
        out=b'';sent=0;deadline=time.monotonic()+6
        try:
            while time.monotonic()<deadline:
                if select.select([fd],[],[],0.1)[0]:
                    try:b=os.read(fd,65536)
                    except OSError:break
                    if not b:break
                    out+=b
                    prompts=out.count('TCP-порт панели [Enter'.encode())
                    if prompts>sent and sent<len(answers):
                        os.write(fd,(answers[sent]+'\n').encode());sent+=1
            else:self.fail('TTY selection hung: '+out.decode())
        finally:
            os.close(fd)
            done,status=os.waitpid(pid,os.WNOHANG)
            if not done:
                os.kill(pid,signal.SIGKILL);_,status=os.waitpid(pid,0)
        return os.waitstatus_to_exitcode(status),out.decode()
    def test_default_and_repeated_conflicts(self):
        code,out=self.interactive(['']);self.assertEqual(code,0,out);self.assertIn('SELECTED=1111',out)
        self.occupied(1111,1112)
        code,out=self.interactive(['','','']);self.assertEqual(code,0,out);self.assertIn('SELECTED=1113',out)
    def test_custom_conflict_offers_next_but_allows_another_custom_value(self):
        self.occupied(2222)
        code,out=self.interactive(['2222','3456']);self.assertEqual(code,0,out)
        self.assertIn('Enter — 2223',out);self.assertIn('SELECTED=3456',out)
    def test_invalid_and_last_port(self):
        self.occupied(65535)
        code,out=self.interactive(['0','-1','0001','1;id','65536','65535','2345'])
        self.assertEqual(code,0,out);self.assertNotIn('Enter — 65536',out);self.assertIn('SELECTED=2345',out)
    def test_headless_requires_explicit_port_and_rejects_conflict(self):
        self.assertNotEqual(self.run_plain().returncode,0)
        self.assertEqual(self.run_plain('3333').returncode,0)
        self.occupied(3333);self.assertNotEqual(self.run_plain('3333').returncode,0)
    def test_updates_preserve_saved_port_without_a_prompt(self):
        (self.conf/'web-origin').write_text('http://192.168.4.1:3456\n')
        result=self.run_plain(FPTN_OPERATION_ID='a'*32)
        self.assertEqual(result.returncode,0,result.stderr);self.assertIn('SELECTED=3456',result.stdout)
        self.assertNotIn('Enter',result.stdout)
        self.occupied(3456);self.assertNotEqual(self.run_plain(FPTN_OPERATION_ID='a'*32).returncode,0)
    def test_own_listener_requires_matching_origin_fragment_and_executable(self):
        daemon=subprocess.Popen(['sleep','20']);self.addCleanup(lambda: (daemon.terminate(),daemon.wait()))
        (self.bin/'lighttpd').symlink_to(shutil.which('sleep'))
        (self.conf/'web-origin').write_text('http://192.168.4.1:3456\n')
        (self.web/'.fptn-web-owned').touch()
        self.fragment.write_text('# Managed by fptn-web-ui\n$SERVER["socket"] == "192.168.4.1:3456" {\n}\n')
        self.listeners.write_text('tcp 0 0 192.168.4.1:3456 0.0.0.0:* LISTEN '+str(daemon.pid)+'/lighttpd\n')
        result=self.run_plain('3456');self.assertEqual(result.returncode,0,result.stderr)
        self.fragment.write_text('# Managed by fptn-web-ui\n$SERVER["socket"] == "192.168.4.1:1111" {\n}\n')
        self.assertNotEqual(self.run_plain('3456').returncode,0)
        self.occupied(3456);self.assertNotEqual(self.run_plain('3456').returncode,0)

MOCK=r'''#!/usr/bin/env python3
import json,os,pathlib,sys
state=pathlib.Path(os.environ['SELECT_STATE']);data=json.loads(state.read_text());a=sys.argv[1:]
def done(code=0,text=''):
 state.write_text(json.dumps(data));print(text);sys.exit(code)
if pathlib.Path(sys.argv[0]).name=='ip':
 name=a[-1];done(0 if name in data['links'] else 1)
cmd=a[1];parts=cmd.split()
if parts==['system','configuration','save']:
 data['writes'].append(cmd);done()
if parts==['show','interface']:
 if data.get('queryFail'):done(1)
 done(text='id: GigabitEthernet0\n'+'\n'.join('id: '+n for n in data['interfaces']))
if parts[:2]==['show','interface']:
 if data.get('race')==parts[-1]:data['interfaces'].append(parts[-1])
 done(0 if parts[-1] in data['interfaces'] else 1)
data['writes'].append(cmd)
if parts[:2]==['no','interface']:
 data['interfaces'].remove(parts[2]);done()
if parts[0]=='interface':
 name=parts[1]
 if len(parts)==2:
  if data.get('createFail'):done(1)
  data['interfaces'].append(name);done()
 if data.get('configFail') and parts[2]=='ip':done(1)
 done()
done(1)
'''
class InterfaceSelectionTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.conf=self.root/'etc/fptn';self.conf.mkdir(parents=True)
        self.bin=self.root/'bin';self.bin.mkdir();shutil.copyfile(ROOT/'scripts/fptn-native-service.sh',self.bin/'fptnctl');self.state=self.root/'state.json'
        self.data={'interfaces':['OpkgTun0','OpkgTun1'],'links':['opkgtun2'],'writes':[]};self.save()
        for name in ['ip','ndmc']:
            f=self.bin/name;f.write_text(MOCK);f.chmod(0o700)
        (self.bin/'id').write_text('#!/bin/sh\necho 0\n');(self.bin/'id').chmod(0o700)
        self.env={**os.environ,'FPTN_PREFIX':str(self.root),'SELECT_STATE':str(self.state),'PATH':str(self.bin)+':'+os.environ['PATH']}
        lib=self.root/'libexec/fptn';lib.mkdir(parents=True)
        (lib/'client-capabilities.sh').write_text('#!/bin/sh\necho \'{"nativeInterfaceSelection":true}\'\n')
    def save(self):self.state.write_text(json.dumps(self.data))
    def run_ctl(self):return subprocess.run(['sh',str(self.bin/'fptnctl'),'setup-interface'],env=self.env,capture_output=True,text=True,timeout=8)
    def test_first_free_uses_both_namespaces_and_reinstall_keeps_it(self):
        result=self.run_ctl();self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual((self.conf/'native-interface').read_text(),'OpkgTun3\n')
        first=json.loads(self.state.read_text());self.assertIn('interface OpkgTun3 no ip global',first['writes'])
        result=self.run_ctl();self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(self.state.read_text()),first)
    def test_configuration_failure_removes_only_new_owned_object(self):
        self.data['configFail']=True;self.save();result=self.run_ctl();self.assertNotEqual(result.returncode,0)
        data=json.loads(self.state.read_text());self.assertEqual(data['interfaces'],['OpkgTun0','OpkgTun1'])
        self.assertIn('no interface OpkgTun3',data['writes']);self.assertFalse((self.conf/'native-interface').exists())
        self.assertFalse((self.conf/'native-setup-lock').exists())
    def test_race_and_failed_inventory_or_creation_never_claim_foreign_interface(self):
        for flag,value in [('race','OpkgTun3'),('queryFail',True),('createFail',True)]:
            with self.subTest(flag=flag):
                self.data={'interfaces':['OpkgTun0','OpkgTun1'],'links':['opkgtun2'],'writes':[],flag:value};self.save()
                self.assertNotEqual(self.run_ctl().returncode,0)
                self.assertFalse((self.conf/'native-interface').exists())
                self.assertNotIn('no interface OpkgTun3',json.loads(self.state.read_text())['writes'])
    def test_existing_legacy_interface_reused_and_malformed_marker_never_executes(self):
        (self.conf/'native-interface').write_text('OpkgTun0\n');(self.conf/'native-setup-complete').touch()
        result=self.run_ctl();self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(self.state.read_text())['writes'],[])
        for value in ['OpkgTun00','OpkgTun-1','eth0','OpkgTun0;id','OpkgTun123456789']:
            (self.conf/'native-interface').write_text(value+'\n');self.assertNotEqual(self.run_ctl().returncode,0)
        self.assertEqual(json.loads(self.state.read_text())['writes'],[])
    def test_saving_configuration_is_explicit_and_works_while_client_is_running(self):
        result=self.run_ctl();self.assertEqual(result.returncode,0,result.stderr)
        self.assertTrue((self.conf/'native-setup-unsaved').exists())
        run=self.root/'var/run/fptn-keenetic';run.mkdir(parents=True)
        result=subprocess.run(['sh',str(self.bin/'fptnctl'),'setup-interface-save'],env=self.env,capture_output=True,text=True,timeout=5)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertFalse((self.conf/'native-setup-unsaved').exists())
        self.assertEqual(json.loads(self.state.read_text())['writes'][-1],'system configuration save')

    def test_incomplete_ownership_does_not_save_unrelated_firmware_configuration(self):
        (self.conf/'native-setup-complete').touch()
        result=subprocess.run(['sh',str(self.bin/'fptnctl'),'setup-interface-save'],env=self.env,capture_output=True,text=True,timeout=5)
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(json.loads(self.state.read_text())['writes'],[])

    def test_old_client_cannot_be_attached_to_a_new_number(self):
        (self.root/'libexec/fptn/client-capabilities.sh').write_text('#!/bin/sh\necho \'{}\'\n')
        result=self.run_ctl();self.assertNotEqual(result.returncode,0)
        self.assertIn('OpkgTunN',result.stderr);self.assertEqual(json.loads(self.state.read_text())['writes'],[])

if __name__=='__main__':unittest.main()
