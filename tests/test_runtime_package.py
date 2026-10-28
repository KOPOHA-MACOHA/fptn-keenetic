"""Complete packages bind runtime, original-source metadata and both executables."""
import hashlib,importlib.util,json,subprocess,tarfile,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('runtime_package',ROOT/'scripts/package-runtime.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
class RuntimePackageTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.base=Path(self.tmp.name)
        self.root=self.base/'repo';self.client=self.base/'client';self.decoder=self.base/'decoder';self.output=self.base/'out'
        for directory in [self.root,self.client,self.decoder,self.output]:directory.mkdir()
        release={'platform':'mipsel-mips32r2-softfloat','upstreamRevision':'c'*40};assets={'platform':release['platform']}
        for name,directory,key in [('fptn-client-cli',self.client,'sha256'),('sources.lock.json',self.client,'sourceLockSha256'),('LICENSE-FPTN',self.client,'clientLicenseSha256'),('brotli',self.decoder,'decoderSha256'),('LICENSE-BROTLI',self.decoder,'decoderLicenseSha256'),('BROTLI-SOURCE.txt',self.decoder,'decoderSourceSha256')]:
            data=(json.dumps({'fptn':'c'*40}) if name=='sources.lock.json' else 'fixture '+name).encode();(directory/name).write_bytes(data)
            (release if key in ['sha256','sourceLockSha256'] else assets)[key]=hashlib.sha256(data).hexdigest()
        (self.root/'client-release.json').write_text(json.dumps(release));(self.root/'runtime-assets.json').write_text(json.dumps(assets))
        for name in ['web/index.html','scripts/install-web-ui.sh','install.sh']:
            path=self.root/name;path.parent.mkdir(exist_ok=True);path.write_text('runtime '+name)
        names=['client-release.json','runtime-assets.json','web/index.html']
        (self.root/'web/SHA256SUMS').write_text(''.join(hashlib.sha256((self.root/p).read_bytes()).hexdigest()+'  '+p+'\n' for p in names))
    def build(self):return module.build(self.root,self.client,self.decoder,self.output,'a'*40)
    def test_package_is_self_contained_and_global_manifest_verifies(self):
        archive=self.build()
        with tarfile.open(archive) as tar:
            names=tar.getnames()
            for name in ['client/fptn-client-cli','client/brotli','scripts/install-web-ui.sh','install.sh','web/index.html','SHA256SUMS','release-revision']:
                self.assertIn('fptn-keenetic/'+name,names)
        result=subprocess.run(['sha256sum','-c','SHA256SUMS'],cwd=self.output/'fptn-keenetic',capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
    def test_wrong_decoder_and_mixed_platforms_are_not_packaged(self):
        (self.decoder/'brotli').write_text('other decoder')
        with self.assertRaises(ValueError):self.build()
        meta=json.loads((self.root/'runtime-assets.json').read_text());meta['platform']='arm64';(self.root/'runtime-assets.json').write_text(json.dumps(meta))
        with self.assertRaises(ValueError):self.build()
    def test_modified_runtime_is_rejected(self):
        (self.root/'web/index.html').write_text('other release')
        with self.assertRaises(ValueError):self.build()
    def test_entware_platform_read_as_data_not_shell(self):
        prefix=self.base/'opt';(prefix/'etc').mkdir(parents=True);release=prefix/'etc/entware_release';marker=self.base/'executed'
        release.write_text('arch=mipsel\ncpu_type=mips32r2\nfloat=soft\nINJECT=$(touch '+str(marker)+')\n')
        import os
        command=['sh',str(ROOT/'scripts/fptn-platform.sh')]
        result=subprocess.run(command,env={**os.environ,'FPTN_PREFIX':str(prefix)},capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(result.stdout.strip(),'mipsel-mips32r2-softfloat');self.assertFalse(marker.exists())
        release.write_text('arch=mips\ncpu_type=mips32r2\nfloat=soft\n')
        self.assertNotEqual(subprocess.run(command,env={**os.environ,'FPTN_PREFIX':str(prefix)},capture_output=True).returncode,0)

    def test_arm_archive_selects_platform_metadata_and_rejects_wrong_binary(self):
        release=json.loads((self.root/'client-release.json').read_text());assets=json.loads((self.root/'runtime-assets.json').read_text())
        platform='armv7-softfloat';release['platform']=assets['platform']=platform
        catalog=self.root/'platform-releases.json';catalog.write_text(json.dumps({'schema':1,'platforms':{platform:{'client':release,'assets':assets}}}))
        with (self.root/'web/SHA256SUMS').open('a') as manifest:manifest.write(hashlib.sha256(catalog.read_bytes()).hexdigest()+'  platform-releases.json\n')
        archive=module.build(self.root,self.client,self.decoder,self.output,'a'*40,platform)
        self.assertEqual(archive.name,'fptn-keenetic-armv7-softfloat.tar.gz')
        with tarfile.open(archive) as tar:self.assertIn('fptn-keenetic/platform-releases.json',tar.getnames())
        import shutil
        shutil.rmtree(self.output/'fptn-keenetic');(self.client/'fptn-client-cli').write_bytes(b'wrong architecture')
        with self.assertRaises(ValueError):module.build(self.root,self.client,self.decoder,self.output,'a'*40,platform)
