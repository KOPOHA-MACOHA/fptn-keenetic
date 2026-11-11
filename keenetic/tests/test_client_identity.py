"""Build identity is tied to binary bytes and pinned source metadata."""
import hashlib,importlib.util,json,os,shutil,subprocess,tempfile,unittest
from pathlib import Path
import client_fixture
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('release_builder',ROOT/'scripts/package-client-release.py');builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)
class IdentityTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        client_fixture.install(self.root);self.lib=self.root/'libexec/fptn';self.conf=self.root/'etc/fptn';self.conf.mkdir(parents=True)
        shutil.copyfile(ROOT/'scripts/fptn-client-identity.sh',self.lib/'client-identity.sh')
        self.env={**os.environ,'FPTN_PREFIX':str(self.root)}
        self.record={'schema':1,'platform':'mipsel-mips32r2-softfloat','buildRevision':'a'*40,'sha256':hashlib.sha256((self.lib/'fptn-client-cli').read_bytes()).hexdigest(),'clientVersion':'0.4.0','upstreamRevision':'b'*40,'upstreamVersion':'0.4.6','sourceLockSha256':'c'*64}
    def call(self,action,*args):return subprocess.run(['sh',str(ROOT/'scripts/fptn-client-identity.sh'),action,*map(str,args)],env=self.env,text=True,capture_output=True,timeout=10)
    def warm(self):subprocess.run(['sh',str(ROOT/'scripts/fptn-client-capabilities.sh')],env=self.env,text=True,capture_output=True,check=True,timeout=10)
    def save(self):(self.conf/'client-release.json').write_text(json.dumps(self.record))
    def test_verified_versions_and_source_commit_match_binary(self):
        self.warm();self.save();r=self.call('inspect');self.assertEqual(r.returncode,0,r.stderr);data=json.loads(r.stdout)
        self.assertTrue(data['clientIdentityVerified']);self.assertEqual(data['upstreamVersion'],'0.4.6');self.assertEqual(data['clientBuildRevision'],'a'*40)
        self.assertEqual(data['clientSha256'],self.record['sha256'])
    def test_replaced_binary_or_wrong_record_does_not_claim_old_source_version(self):
        self.warm();self.save();(self.lib/'fptn-client-cli').write_text('#!/bin/sh\nexit 1\n')
        data=json.loads(self.call('inspect').stdout);self.assertFalse(data['clientIdentityVerified']);self.assertIsNone(data['upstreamVersion'])
        self.assertIsNone(data['clientSha256']) # cache-only status never runs changed code
    def test_missing_cache_does_not_execute_client_in_status(self):
        marker=self.root/'executed';p=self.lib/'fptn-client-cli';p.write_text('#!/bin/sh\necho ran > '+str(marker)+'\nexit 1\n');p.chmod(0o700)
        self.save();self.assertFalse(json.loads(self.call('inspect').stdout)['clientIdentityVerified']);self.assertFalse(marker.exists())
    def test_invalid_release_records_are_rejected(self):
        for change in [{'sha256':'x'*64},{'buildRevision':'main'},{'platform':'arm64'},{'upstreamVersion':'bad'},{'sourceLockSha256':'short'}]:
            (self.conf/'candidate').write_text(json.dumps({**self.record,**change}));self.assertNotEqual(self.call('validate',self.conf/'candidate').returncode,0)
    def test_missing_legacy_record_displays_unknown_original_without_invention(self):
        self.warm();data=json.loads(self.call('inspect').stdout);self.assertEqual(data['clientVersion'],'0.4.0');self.assertFalse(data['clientIdentityVerified']);self.assertIsNone(data['upstreamVersion'])
    def test_producer_binds_actual_artifact_and_checks_source_lock(self):
        directory=self.root/'artifact';directory.mkdir();(directory/'fptn-client-cli').write_bytes(b'compiled ELF bytes')
        source=self.root/'build-source';source.mkdir()
        lock={'fptn':'1'*40,'boringssl':'2'*40,'yaff':'2'*40,'ntp_client':'2'*40,'camouflage_tls':'2'*40}
        (source/'sources.lock.json').write_text(json.dumps(lock));(source/'sources.upstream.json').write_text(json.dumps({'revision':'1'*40,'release':'v9.8.7'}))
        (source/'Dockerfile').write_text('RUN cmake -DFPTN_VERSION=7.8.9')
        shutil.copyfile(source/'sources.lock.json',directory/'sources.lock.json')
        (directory/'SHA256SUMS').write_text(''.join(hashlib.sha256((directory/name).read_bytes()).hexdigest()+'  '+name+'\n' for name in ['fptn-client-cli','sources.lock.json']))
        result=builder.generate(source,directory,'d'*40)
        self.assertEqual(result['sha256'],hashlib.sha256(b'compiled ELF bytes').hexdigest());self.assertEqual(result['upstreamVersion'],'9.8.7');self.assertEqual(result['clientVersion'],'7.8.9')
        self.assertEqual(result['buildRevision'],'d'*40);self.assertIn('  client-release.json',(directory/'SHA256SUMS').read_text())
        (directory/'fptn-client-cli').write_bytes(b'changed bytes')
        with self.assertRaises(ValueError):builder.generate(source,directory,'d'*40)
        lock['fptn']='f'*40;(directory/'sources.lock.json').write_text(json.dumps(lock))
        with self.assertRaises(ValueError):builder.generate(source,directory,'d'*40)
