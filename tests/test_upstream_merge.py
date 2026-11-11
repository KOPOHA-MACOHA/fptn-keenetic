"""Real Git merge and dependency-reuse guards; no network or GitHub writes."""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
def module(name):
    spec=importlib.util.spec_from_file_location(name,ROOT/'scripts'/name)
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result);return result
merge=module('merge-upstream.py');reuse=module('dependency-reuse.py')

class MergeTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'repo';self.root.mkdir()
        self.git('init','-q');self.git('config','user.name','Fixture');self.git('config','user.email','fixture@example.invalid')
        for name,text in {'CMakeLists.txt':'project(fptn)\n','conanfile.py':'# pinned\n','src/client.cpp':'// original\n',
                          'README.md':'original\n','.github/workflows/build.yml':'original workflow\n'}.items():self.put(name,text)
        self.commit('original');self.original=self.sha()
        self.git('branch','upstream')
        lock={k:self.original for k in ['fptn','boringssl','camouflage_tls','ntp_client','yaff']}
        self.put('sources.lock.json',json.dumps(lock,indent=2)+'\n')
        self.put('build-locks/metadata.json',json.dumps({'sourcesSha256':reuse.digest((self.root/'sources.lock.json').read_bytes())}))
        self.put('README.md','Keenetic README\n');self.put('.github/workflows/build.yml','Keenetic workflow\n')
        self.put('.github/workflows/only-fork.yml','only fork\n');self.commit('fork');self.main=self.sha()
        self.git('checkout','-q','upstream')
        self.put('src/client.cpp','// real upstream fix\n')
        self.put('README.md','updated original\n');self.put('.github/workflows/build.yml','new upstream workflow\n')
        self.put('.github/workflows/new.yml','self-hosted upstream job\n');self.commit('new stable');self.candidate=self.sha()
        self.git('checkout','-q',self.main)
    def git(self,*args):return subprocess.check_output(['git','-C',str(self.root),*args],text=True,stderr=subprocess.DEVNULL)
    def sha(self):return self.git('rev-parse','HEAD').strip()
    def put(self,name,text):
        p=self.root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(text)
    def commit(self,message):self.git('add','.');self.git('commit','-qm',message)
    def prepare(self):return merge.merge_entries(self.root,self.main,self.candidate,'0.4.7',str(self.root))
    def data(self,entries):
        import base64
        return {e['path']:base64.b64decode(e['data']).decode() for e in entries if 'data' in e}
    def test_actual_source_merge_preserves_owned_files_and_archives_workflows(self):
        entries,ready=self.prepare();files=self.data(entries)
        self.assertTrue(ready);self.assertEqual(files['src/client.cpp'],'// real upstream fix\n')
        self.assertNotIn('README.md',files);self.assertNotIn('.github/workflows/build.yml',files)
        self.assertNotIn('.github/workflows/new.yml',files)
        self.assertEqual(files['.github/upstream-workflows/new.yml.disabled'],'self-hosted upstream job\n')
        self.assertEqual(json.loads(files['sources.lock.json'])['fptn'],self.candidate)
        self.assertEqual(json.loads(files['build-locks/source-reuse.json'])['sourcesSha256'],reuse.digest(files['sources.lock.json'].encode()))
        self.assertEqual(self.sha(),self.main);self.assertEqual(self.git('status','--porcelain'),'')
    def test_changed_dependency_contract_requires_explicit_refresh(self):
        self.git('checkout','-q','upstream');self.put('CMakeLists.txt','project(new_dependencies)\n');self.commit('dependency change')
        self.candidate=self.sha();self.git('checkout','-q',self.main)
        entries,ready=self.prepare();files=self.data(entries)
        self.assertFalse(ready);self.assertNotIn('build-locks/source-reuse.json',files)
        self.assertEqual(json.loads(files['sources.upstream.json'])['dependencyReview'],'refresh-required')
    def test_unknown_source_conflict_fails_without_changing_checkout(self):
        self.put('src/client.cpp','// local fork change\n');self.commit('fork conflict');self.main=self.sha()
        with self.assertRaisesRegex(ValueError,'src/client.cpp'):self.prepare()
        self.assertEqual(self.sha(),self.main);self.assertEqual(self.git('status','--porcelain'),'')
    def test_dirty_checkout_is_preserved(self):
        self.put('src/client.cpp','// unsaved\n')
        with self.assertRaisesRegex(ValueError,'local changes'):self.prepare()
        self.assertEqual((self.root/'src/client.cpp').read_text(),'// unsaved\n')
    def test_reuse_plan_accepts_code_changes_but_rejects_recipe_changes(self):
        entries,_=self.prepare();files=self.data(entries)
        for name in ['sources.lock.json','build-locks/source-reuse.json']:self.put(name,files[name])
        self.assertEqual(json.loads(reuse.cached_sources(self.root))['fptn'],self.original)
        self.put('src/client.cpp','// another ordinary code fix\n');reuse.cached_sources(self.root)
        self.put('conanfile.py','# changed recipe\n')
        with self.assertRaisesRegex(ValueError,'no longer matches'):reuse.cached_sources(self.root)
    def test_reuse_plan_rejects_changed_external_pin(self):
        entries,_=self.prepare();files=self.data(entries)
        for name in ['sources.lock.json','build-locks/source-reuse.json']:self.put(name,files[name])
        lock=json.loads(files['sources.lock.json']);lock['boringssl']='f'*40
        self.put('sources.lock.json',json.dumps(lock))
        with self.assertRaises(ValueError):reuse.cached_sources(self.root)

class ImageAuditTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.work=Path(self.tmp.name)
        base=self.work/'upstream';base.mkdir();subprocess.run(['git','init','-q',str(base)],check=True)
        for name in ['CMakeLists.txt','conanfile.py']:(base/name).write_text('# original\n')
        subprocess.run(['git','-C',str(base),'add','.'],check=True)
        new=self.work/'candidate-source';shutil.copytree(base,new,ignore=shutil.ignore_patterns('.git'))
        (new/'FORK-SOURCE.json').write_text(json.dumps({'dependencyPaths':reuse.tracked_dependencies(base)}))
        old=json.dumps({'fptn':'a'*40,'yaff':'b'*40})+'\n';current=json.dumps({'fptn':'c'*40,'yaff':'b'*40})+'\n'
        (self.work/'sources.lock.json').write_text(old);(self.work/'candidate.sources.lock.json').write_text(current)
        for folder in ['build-locks','candidate-build-locks']:
            d=self.work/folder;d.mkdir();(d/'metadata.json').write_text(json.dumps({'sourcesSha256':reuse.digest(old.encode())}));(d/'conan.lock').write_text('locked\n')
            for name in ['graph.json','python.txt','apt.txt','build-profile','ubuntu.sources','shared-build-cache.json']:(d/name).write_text('locked\n')
        (self.work/'candidate-build-locks/source-reuse.json').write_text(json.dumps({'schema':1,'dependencySources':old,'sourcesSha256':reuse.digest(current.encode())}))
        for name in ['profile','candidate.profile']:(self.work/name).write_text('same ABI\n')
    def test_source_only_update_binds_same_locks_to_actual_client_sources(self):
        reuse.audit(self.work)
        meta=json.loads((self.work/'candidate-build-locks/metadata.json').read_text())
        self.assertEqual(meta['sourcesSha256'],reuse.digest((self.work/'candidate.sources.lock.json').read_bytes()))
        self.assertNotEqual(meta,json.loads((self.work/'build-locks/metadata.json').read_text()))
    def test_changed_prepared_recipe_profile_or_lock_is_rejected(self):
        for name in ['candidate-source/conanfile.py','candidate.profile','candidate-build-locks/conan.lock']:
            p=self.work/name;old=p.read_bytes();p.write_text('drift\n')
            with self.assertRaises(ValueError):reuse.audit(self.work)
            p.write_bytes(old)
    def test_missing_source_authorization_is_rejected(self):
        (self.work/'candidate-build-locks/source-reuse.json').unlink()
        with self.assertRaises(FileNotFoundError):reuse.audit(self.work)
