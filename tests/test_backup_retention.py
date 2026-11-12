"""Real private PHP maintenance against disposable filesystem trees."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
HELPER=ROOT/'scripts/fptn-backup-retention.php'

class BackupRetentionTest(unittest.TestCase):
    def setUp(self):
        self.php=shutil.which('php') or shutil.which('php-cgi')
        if not self.php:self.skipTest('PHP runtime is installed in Actions')
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'opt';self.conf=self.root/'etc/fptn';self.action=self.root/'var/run/fptn-web/action'
        self.conf.mkdir(parents=True);self.action.mkdir(parents=True)
        self.private(self.action/'owner.json',json.dumps({'id':'a'*32}))
        self.private(self.action/'action','update\n')
        self.private(self.conf/'web-meta.json',json.dumps({'revision':'b'*40}))
        self.counter=0
    def private(self,path,text):
        path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text);path.chmod(0o600)
    def invoke(self,operation,path,ok=True):
        env={**os.environ,'FPTN_PREFIX':str(self.root),'FPTN_BACKUP_ACTION':operation,
             'FPTN_BACKUP_PATH':str(path),'FPTN_BACKUP_REVISION':'b'*40,'FPTN_OPERATION_ID':'a'*32}
        for name in ('SERVER_SOFTWARE','SERVER_NAME','GATEWAY_INTERFACE','REQUEST_METHOD','SCRIPT_FILENAME','SCRIPT_NAME','QUERY_STRING','CONTENT_LENGTH','CONTENT_TYPE','PATH_TRANSLATED','REDIRECT_STATUS','HTTP_AUTHORIZATION','FCGI_ROLE'):env.pop(name,None)
        result=subprocess.run([self.php,'-n','-q','-d','cgi.force_redirect=0','-f',str(HELPER)],env=env,text=True,capture_output=True,timeout=10)
        if ok:self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        else:self.assertNotEqual(result.returncode,0,result.stdout+result.stderr)
        return result
    def begin(self):
        self.counter+=1;path=self.conf/f'web-install-backup.{self.counter:06d}'
        path.mkdir(mode=0o700);(path/'web').mkdir();(path/'web/page').write_text('old runtime')
        self.private(self.action/'install-backup',str(path)+'\n')
        self.invoke('begin',path);return path
    def success(self):
        path=self.begin();self.invoke('commit',path);return path
    def record(self,path):return json.loads((path/'.fptn-backup.json').read_text())
    def pin(self,path):
        archive=self.conf/'web-recovery'/('c'*32);archive.mkdir(parents=True,exist_ok=True)
        self.private(archive/'install-backup',str(path)+'\n');return archive
    def test_error_reporting_works_in_cli_and_cgi_without_optional_constants(self):
        for php in dict.fromkeys(filter(None,[shutil.which('php'),shutil.which('php-cgi')])):
            with self.subTest(php=php):
                env={**os.environ,'FPTN_PREFIX':str(self.root),'FPTN_BACKUP_ACTION':'invalid'}
                for name in ('SERVER_SOFTWARE','SERVER_NAME','GATEWAY_INTERFACE','REQUEST_METHOD','SCRIPT_FILENAME','SCRIPT_NAME','QUERY_STRING','CONTENT_LENGTH','CONTENT_TYPE','PATH_TRANSLATED','REDIRECT_STATUS','HTTP_AUTHORIZATION','FCGI_ROLE'):env.pop(name,None)
                result=subprocess.run([php,'-n','-q','-d','cgi.force_redirect=0','-f',str(HELPER)],env=env,text=True,capture_output=True,timeout=10)
                self.assertNotEqual(result.returncode,0)
                self.assertIn('обслуживание резервных копий пропущено',result.stderr)
                self.assertNotIn('Fatal error',result.stdout+result.stderr)

    def test_keep_three_newest_successes_independent_of_clock(self):
        backups=[]
        for i in range(7):
            path=self.success();backups.append(path);os.utime(path,(1000-i,1000-i))
        self.assertEqual([p.exists() for p in backups],[False]*4+[True]*3)
        self.assertTrue(all(self.record(p)['state']=='success' for p in backups[-3:]))
    def test_failed_pending_legacy_and_foreign_receipts_survive(self):
        failed=self.begin();self.invoke('failed',failed)
        pending=self.begin()
        legacy=self.conf/'web-install-backup.LEGACY';legacy.mkdir();(legacy/'important').write_text('keep')
        foreign=self.conf/'web-install-backup.OTHERX';foreign.mkdir()
        fake={**self.record(failed),'directory':foreign.name,'state':'success','owner':'f'*32}
        self.private(foreign/'.fptn-backup.json',json.dumps(fake))
        for _ in range(6):self.success()
        for path in (failed,pending,legacy,foreign):self.assertTrue(path.exists())
        self.assertEqual(self.record(failed)['state'],'failed');self.assertEqual(self.record(pending)['state'],'pending')
    def test_recovery_reference_pins_old_success_in_addition_to_latest_three(self):
        old=self.success();self.pin(old);recent=[self.success() for _ in range(5)]
        self.assertTrue(old.exists());self.assertEqual([p.exists() for p in recent],[False,False,True,True,True])
    def test_symlink_directories_and_markers_are_preserved_and_inner_links_not_followed(self):
        outside=Path(self.tmp.name)/'outside';outside.mkdir();(outside/'secret').write_text('untouched')
        link=self.conf/'web-install-backup.LINKED';link.symlink_to(outside,target_is_directory=True)
        old=self.success();(old/'web/link').symlink_to(outside,target_is_directory=True)
        forged=self.conf/'web-install-backup.FORGED';forged.mkdir();(forged/'.fptn-backup.json').symlink_to(old/'.fptn-backup.json')
        for _ in range(4):self.success()
        self.assertFalse(old.exists());self.assertTrue(link.is_symlink());self.assertTrue(forged.is_dir())
        self.assertEqual((outside/'secret').read_text(),'untouched')
    def test_missing_success_evidence_and_wrong_operation_never_prune(self):
        previous=[self.success() for _ in range(3)];current=self.begin()
        self.private(self.conf/'web-meta.json',json.dumps({'revision':'d'*40}))
        self.invoke('commit',current,ok=False)
        self.assertEqual(self.record(current)['state'],'pending');self.assertTrue(all(p.exists() for p in previous))
        self.private(self.action/'owner.json',json.dumps({'id':'d'*32}))
        self.invoke('commit',current,ok=False);self.assertTrue(all(p.exists() for p in previous))
    def test_unsafe_reference_or_duplicate_sequence_stops_all_deletion(self):
        previous=[self.success() for _ in range(3)]
        archive=self.pin(previous[0]);self.private(archive/'install-backup','/elsewhere/web-install-backup.UNSAFE\n')
        current=self.begin();self.invoke('commit',current,ok=False)
        self.assertTrue(all(p.exists() for p in previous));self.assertEqual(self.record(current)['state'],'success')
        (archive/'install-backup').unlink()
        receipt=self.record(previous[1]);receipt['sequence']=self.record(previous[0])['sequence']
        self.private(previous[1]/'.fptn-backup.json',json.dumps(receipt))
        current=self.begin();self.invoke('commit',current,ok=False);self.assertTrue(all(p.exists() for p in previous))
    def test_credentials_settings_and_other_backup_names_are_untouched(self):
        important={n:'private '+n for n in ['token','server','web-auth.json','autostart','native-interface','cidrs']}
        for name,text in important.items():self.private(self.conf/name,text)
        other=self.conf/'uninstall-backup.123456';other.mkdir();(other/'data').write_text('keep')
        for _ in range(5):self.success()
        for name,text in important.items():self.assertEqual((self.conf/name).read_text(),text)
        self.assertEqual((other/'data').read_text(),'keep')
    def test_corrupt_counter_and_symlink_state_never_follow_or_reset_ownership(self):
        previous=[self.success() for _ in range(3)]
        state=self.conf/'.web-backup-state.json';state.write_text('broken')
        self.counter+=1;current=self.conf/f'web-install-backup.{self.counter:06d}';current.mkdir()
        self.private(self.action/'install-backup',str(current)+'\n')
        self.invoke('begin',current,ok=False);self.assertTrue(all(p.exists() for p in previous))
        state.unlink();outside=Path(self.tmp.name)/'foreign-state';outside.write_text('do not change');state.symlink_to(outside)
        self.invoke('begin',current,ok=False);self.assertEqual(outside.read_text(),'do not change')
