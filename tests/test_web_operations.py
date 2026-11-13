"""Recover interrupted web actions without touching live owners or unrelated processes."""
import json
import os
from pathlib import Path
import subprocess
import time
import unittest
import test_web_ui

ROOT=Path(__file__).resolve().parents[1]

class WebOperationTest(unittest.TestCase):
    setUp=test_web_ui.WebUiTest.setUp
    request=test_web_ui.WebUiTest.request
    wait_worker=test_web_ui.WebUiTest.wait_worker

    def helper(self,action,body=None):
        return subprocess.run(['sh',str(ROOT/'scripts/fptn-web-auth.sh'),action],env={**self.env,'FPTN_OWNER_PID':str(os.getpid())},input=json.dumps(body or {}),capture_output=True,text=True,timeout=10)

    def reserve(self,action='start'):
        result=self.helper('op-reserve',{'action':action});self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        return json.loads(result.stdout)['id']

    def stale(self):
        path=self.run/'action/owner.json';owner=json.loads(path.read_text());owner['pid']=99999999;path.write_text(json.dumps(owner))

    def test_live_owner_and_legacy_lock_cannot_be_reclaimed(self):
        self.reserve();state=json.loads(self.helper('op-state').stdout)
        self.assertFalse(state['canRecover']);self.assertEqual(self.request(body={'action':'recover'})[0],409)
        (self.run/'action/owner.json').unlink()
        self.assertFalse(json.loads(self.helper('op-state').stdout)['canRecover'])
        self.assertEqual(self.request(body={'action':'recover'})[0],409)

    def test_orphaned_marked_child_blocks_recovery_until_it_exits(self):
        marker=self.reserve();self.stale()
        child=subprocess.Popen(['sleep','30'],env={**os.environ,'FPTN_OPERATION_ID':marker})
        try:
            self.assertFalse(json.loads(self.helper('op-state').stdout)['canRecover'])
            self.assertEqual(self.request(body={'action':'recover'})[0],409)
        finally:child.terminate();child.wait(timeout=5)
        state=json.loads(self.helper('op-state').stdout);self.assertTrue(state['canRecover'],state)
        status,data=self.request(body={'action':'recover'});self.assertEqual(status,202,data)
        self.assertTrue(self.wait_worker()['ok']);self.assertFalse((self.root/'trace').exists())

    def test_interrupted_save_restores_snapshot_and_previously_running_client(self):
        self.reserve('save');lock=self.run/'action'
        previous={'token':self.token+'\n','server':self.names[0]+'\n','connection.json':json.dumps({'bypass':'obfuscation','strategy':'dual-rolling-tunnel','sni':'google.com'})}
        for key,value in previous.items():(lock/('old-'+key)).write_text(value)
        (lock/'was-running').write_text('yes\n');(lock/'rollback-needed').touch()
        (self.conf/'token').write_text('partial-change');(self.conf/'server').write_text('partial-change')
        self.client_run.mkdir();self.stale()
        status,data=self.request();self.assertEqual(status,200,data);self.assertTrue(data['operationState']['canRecover'],data['operationState'])
        status,data=self.request(body={'action':'recover'});self.assertEqual(status,202,data)
        result=self.wait_worker();self.assertTrue(result['ok'],result)
        for key,value in previous.items():self.assertEqual((self.conf/key).read_text(),value)
        self.assertTrue(self.client_run.exists());self.assertEqual([a for a in (self.root/'trace').read_text().splitlines() if a!='status'],['stop','start'])
        self.assertFalse(list((self.conf/'web-recovery').glob('*/old-token')))
        self.assertFalse(list((self.root/'tmp').glob('fptn-private-*/*')))

    def test_finished_or_update_action_only_releases_lock_and_preserves_running_vpn(self):
        for action in ['save','update']:
            self.reserve(action);self.stale();self.client_run.mkdir(exist_ok=True)
            # A just-finished worker can still disappear during the conservative
            # /proc inspection. HTTP 409 asks the caller to retry once it settles;
            # it is not a guarantee that the first request must recover immediately.
            deadline=time.monotonic()+3
            while True:
                status,data=self.request(body={'action':'recover'})
                if status!=409 or time.monotonic()>=deadline:break
                time.sleep(.05)
            self.assertEqual(status,202,data)
            self.assertTrue(self.wait_worker()['ok']);self.assertTrue(self.client_run.exists())
            self.assertFalse((self.root/'trace').exists())

    def test_pid_reuse_record_does_not_own_an_unmarked_process(self):
        self.reserve();owner=self.run/'action/owner.json';record=json.loads(owner.read_text());record['start']='0';owner.write_text(json.dumps(record))
        state=json.loads(self.helper('op-state').stdout);self.assertTrue(state['canRecover'],state)
        child=subprocess.Popen(['sleep','30'])
        try:
            status,data=self.request(body={'action':'recover'});self.assertEqual(status,202,data)
            self.assertTrue(self.wait_worker()['ok']);self.assertIsNone(child.poll())
        finally:child.terminate();child.wait(timeout=5)

    def test_concurrent_reservations_have_one_owner_and_complete_journal(self):
        env={**self.env,'FPTN_OWNER_PID':str(os.getpid())};children=[]
        for _ in range(3):children.append(subprocess.Popen(['sh',str(ROOT/'scripts/fptn-web-auth.sh'),'op-reserve'],env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True))
        for child in children:
            child.stdin.write(json.dumps({'action':'check'}));child.stdin.close();child.stdin=None
        try:results=[child.communicate(timeout=10) for child in children]
        finally:
            for child in children:
                if child.poll() is None:child.kill();child.communicate()
        self.assertEqual(sum(c.returncode==0 for c in children),1,results)
        winner=next(json.loads(out)['id'] for c,(out,err) in zip(children,results) if c.returncode==0)
        owner=json.loads((self.run/'action/owner.json').read_text());self.assertEqual(owner['id'],winner)
        self.assertEqual(json.loads((self.run/'action/request.json').read_text()),{'action':'check'})

    def test_submitted_token_never_enters_persistent_journal(self):
        secret='unique-submitted-secret'
        result=self.helper('op-reserve',{'action':'save','token':secret,'password':'not-journalled','server':'CZ'})
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual(json.loads((self.run/'action/request.json').read_text()),{'action':'save','server':'CZ'})
        scratch=list((self.root/'tmp').glob('fptn-private-*/*/request-token'))
        self.assertEqual(len(scratch),1);self.assertEqual(scratch[0].read_text(),secret)
        self.assertEqual(scratch[0].stat().st_mode&0o777,0o600)

    def test_recovery_without_volatile_old_token_stops_client_and_unlocks(self):
        self.reserve('save');lock=self.run/'action'
        (lock/'old-token-present').touch();(lock/'rollback-needed').touch()
        (lock/'was-running').write_text('yes');self.client_run.mkdir();self.stale()
        status,data=self.request(body={'action':'recover'});self.assertEqual(status,202,data)
        result=self.wait_worker();self.assertTrue(result['ok'],result)
        self.assertIn('токен утрачен',result['message'])
        self.assertFalse(self.client_run.exists());self.assertFalse(lock.exists())

    def test_history_scrub_removes_credentials_but_preserves_code_and_external_symlink_target(self):
        marker=self.reserve();self.env['FPTN_OPERATION_ID']=marker
        backup=self.conf/'web-install-backup.abc123';backup.mkdir()
        (backup/'token').write_text('old-secret');(backup/'web-auth.json').write_text('old-password')
        (backup/'request.json').write_text(json.dumps({'action':'save','token':'submitted-secret','server':'CZ'}))
        (backup/'fptnctl').write_text('program')
        outside=self.root/'outside';outside.mkdir();(outside/'token').write_text('external')
        (backup/'outside').symlink_to(outside,target_is_directory=True)
        result=self.helper('op-sanitize-history');self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertFalse((backup/'token').exists());self.assertFalse((backup/'web-auth.json').exists())
        self.assertEqual(json.loads((backup/'request.json').read_text()),{'action':'save','server':'CZ'})
        self.assertEqual((backup/'fptnctl').read_text(),'program');self.assertEqual((outside/'token').read_text(),'external')
