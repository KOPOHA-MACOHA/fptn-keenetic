"""Execute port transactions against isolated files and fake HTTP/init commands."""
from pathlib import Path
import json,os,shutil,subprocess,tempfile,time,unittest
ROOT=Path(__file__).resolve().parents[1]
class WebPortTest(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);self.bin=self.root/'bin';self.bin.mkdir()
  if not shutil.which('jq'):self.skipTest('jq unavailable')
  self.conf=self.root/'etc/fptn';self.conf.mkdir(parents=True);self.fragments=self.root/'etc/lighttpd/conf.d';self.fragments.mkdir(parents=True)
  self.fragment=self.fragments/'95-fptn.conf';self.original='# Managed by fptn-web-ui\n$SERVER["socket"] == "192.168.1.1:1111" {\n server.document-root = "/opt/share/fptn-web"\n}\n'
  self.fragment.write_text(self.original);(self.root/'etc/lighttpd/lighttpd.conf').write_text('original shared config\n')
  (self.conf/'web-origin').write_text('http://192.168.1.1:1111\n');(self.conf/'web-meta.json').write_text('{"uiVersion":"0.18.8"}')
  web=self.root/'share/fptn-web';web.mkdir(parents=True);(web/'.fptn-web-owned').touch()
  lock=self.root/'var/run/fptn-web/action';lock.mkdir(parents=True);(lock/'action').write_text('change-port\n');(lock/'owner.json').write_text(json.dumps({'id':'a'*32}))
  init=self.root/'etc/init.d/S80lighttpd';init.parent.mkdir(parents=True)
  init.write_text('#!/bin/sh\nprintf "restart\\n" >> "$FPTN_PREFIX/restarts"\nexit 1\n');init.chmod(0o700)
  mock='''#!/usr/bin/env python3
import os,sys,json,pathlib,fcntl
r=pathlib.Path(os.environ['FPTN_PREFIX']);cmd=pathlib.Path(sys.argv[0]).name
if cmd=='id':print(0)
elif cmd=='netstat':
 if (r/'occupied').exists():print('tcp6 0 0 :::2222 :::* LISTEN 55/foreign')
 if (r/'stale-old').exists() and (r/'etc/fptn/web-port-change/confirmed').exists():print('tcp 0 0 192.168.1.1:1111 0.0.0.0:* LISTEN 55/lighttpd')
elif cmd=='lighttpd':
 if (r/'bad-config').exists():sys.exit(1)
elif cmd=='date':
 # apply and confirm call date concurrently; serialize the fixture clock.
 with (r/'clock.lock').open('a') as guard:
  fcntl.flock(guard,fcntl.LOCK_EX)
  p=r/'clock';n=int(p.read_text()) if p.exists() else 1000;p.write_text(str(n+(200 if (r/'fast-clock').exists() else 0)));print(n)
elif cmd=='curl':
 if (r/'http-down').exists() and ':2222/' in ' '.join(sys.argv):sys.exit(7)
 print(json.dumps({'uiVersion':'0.18.8','configured':True}))
else:sys.exit(3)
'''
  for name in ['id','netstat','lighttpd','curl','date']:
   p=self.bin/name;p.write_text(mock);p.chmod(0o700)
  self.env={**os.environ,'FPTN_PREFIX':str(self.root),'FPTN_OPERATION_ID':'a'*32,'PATH':str(self.bin)+':'+os.environ['PATH']}
 def run_cmd(self,*args):return subprocess.run(['sh',str(ROOT/'scripts/fptn-web-port.sh'),*args],env=self.env,text=True,capture_output=True,timeout=8)
 def start(self):return subprocess.Popen(['sh',str(ROOT/'scripts/fptn-web-port.sh'),'apply','2222'],env=self.env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
 def wait(self,predicate):
  deadline=time.monotonic()+5
  while time.monotonic()<deadline:
   if predicate():return
   time.sleep(.02)
  self.fail('Timed out')
 def test_confirm_keeps_vpn_untouched_and_other_lighttpd_settings_and_ignores_init_failed(self):
  child=self.start()
  try:
   self.wait(lambda:(self.conf/'web-port-change/info.json').exists() and ':2222' in self.fragment.read_text())
   self.assertIn(':1111',self.fragment.read_text());self.assertEqual((self.conf/'web-origin').read_text().strip(),'http://192.168.1.1:1111')
   denied=self.run_cmd('confirm','a'*32,'http://192.168.1.1:1111');self.assertNotEqual(denied.returncode,0)
   result=self.run_cmd('confirm','b'*32,'http://192.168.1.1:2222');self.assertNotEqual(result.returncode,0)
   result=self.run_cmd('confirm','a'*32,'http://192.168.1.1:2222');self.assertEqual(result.returncode,0,result.stderr)
   out,err=child.communicate(timeout=6);self.assertEqual(child.returncode,0,out+err)
   self.assertNotIn(':1111',self.fragment.read_text());self.assertIn(':2222',self.fragment.read_text())
   self.assertEqual((self.conf/'web-origin').read_text().strip(),'http://192.168.1.1:2222')
   self.assertEqual((self.root/'etc/lighttpd/lighttpd.conf').read_text(),'original shared config\n')
  finally:
   if child.poll() is None:child.terminate();child.communicate(timeout=6)
 def test_old_listener_left_by_failed_restart_is_not_reported_as_a_success(self):
  (self.root/'stale-old').touch();child=self.start()
  try:
   self.wait(lambda:(self.conf/'web-port-change/info.json').exists() and ':2222' in self.fragment.read_text())
   result=self.run_cmd('confirm','a'*32,'http://192.168.1.1:2222');self.assertEqual(result.returncode,0,result.stderr)
   out,err=child.communicate(timeout=6);self.assertNotEqual(child.returncode,0,out+err)
   self.assertEqual(self.fragment.read_text(),self.original)
   self.assertEqual((self.conf/'web-origin').read_text(),'http://192.168.1.1:1111\n')
  finally:
   if child.poll() is None:child.terminate();child.communicate(timeout=6)
 def test_conflicting_ipv6_wildcard_and_bad_ports_leave_files_untouched(self):
  (self.root/'occupied').touch()
  for port in ['2222','0','-1','65536','02222','foo']:
   result=self.run_cmd('apply',port);self.assertNotEqual(result.returncode,0,result.stdout+result.stderr)
   self.assertEqual(self.fragment.read_text(),self.original)
   self.assertFalse((self.conf/'web-port-change').exists())
 def test_unconfirmed_port_rolls_back_even_when_init_reports_failed(self):
  (self.root/'fast-clock').touch();result=self.run_cmd('apply','2222')
  self.assertNotEqual(result.returncode,0,result.stdout+result.stderr)
  self.assertEqual(self.fragment.read_text(),self.original)
  self.assertEqual((self.conf/'web-origin').read_text(),'http://192.168.1.1:1111\n')
  self.assertFalse((self.conf/'web-port-change').exists())
 def test_failed_http_readiness_rolls_back(self):
  (self.root/'fast-clock').touch();(self.root/'http-down').touch();result=self.run_cmd('apply','2222')
  self.assertNotEqual(result.returncode,0);self.assertEqual(self.fragment.read_text(),self.original)
 def test_killed_worker_leaves_a_complete_journal_for_explicit_rollback(self):
  child=self.start()
  try:
   self.wait(lambda:(self.conf/'web-port-change/info.json').exists() and ':2222' in self.fragment.read_text())
   child.kill();child.communicate(timeout=6)
   result=self.run_cmd('rollback');self.assertEqual(result.returncode,0,result.stdout+result.stderr)
   self.assertEqual(self.fragment.read_text(),self.original)
   self.assertEqual((self.conf/'web-origin').read_text(),'http://192.168.1.1:1111\n')
   self.assertFalse((self.conf/'web-port-change').exists())
  finally:
   if child.poll() is None:child.kill();child.communicate(timeout=6)
 def test_abandoned_transition_is_not_overwritten_by_a_new_request(self):
  pending=self.conf/'web-port-change';pending.mkdir();(pending/'info.json').write_text('{"previous":true}')
  for action in [('check','2222'),('apply','2222'),('apply','1111')]:
   result=self.run_cmd(*action);self.assertNotEqual(result.returncode,0,result.stdout+result.stderr)
   self.assertEqual(self.fragment.read_text(),self.original);self.assertEqual((pending/'info.json').read_text(),'{"previous":true}')
 def test_foreign_fragment_and_missing_owner_are_rejected(self):
  self.fragment.write_text('foreign config');result=self.run_cmd('apply','2222');self.assertNotEqual(result.returncode,0);self.assertEqual(self.fragment.read_text(),'foreign config')
  self.fragment.write_text(self.original);(self.root/'var/run/fptn-web/action/owner.json').write_text('{}')
  result=self.run_cmd('apply','2222');self.assertNotEqual(result.returncode,0);self.assertEqual(self.fragment.read_text(),self.original)
