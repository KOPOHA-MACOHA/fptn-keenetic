"""Publication state-machine tests with real temporary Git history and fake GitHub.

No network, production branches, releases, binaries, or router state are changed.
"""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('publication', ROOT/'scripts/release-publication.py')
pub = importlib.util.module_from_spec(spec); spec.loader.exec_module(pub)


class FakeGitHub(pub.GitHub):
    def __init__(self, repo):
        self.repo = repo
        self.runs = {}
        self.releases = {}
        self.tags = {}
        self.bytes = {}
        self.jobs = {}
        self.writes = []
        self.asset_id = 0
        self.next_run = 10
        self.before_patch = None

    def add_run(self, workflow, ref, success=True, event='push', state='completed'):
        self.next_run += 1
        run = {'id': self.next_run, 'run_attempt': 1, 'head_sha': ref, 'head_branch': 'main',
               'head_repository': {'full_name': pub.REPOSITORY}, 'event': event,
               'path': '.github/workflows/'+workflow, 'status': state,
               'conclusion': 'success' if success else 'failure'}
        self.runs[run['id']] = run
        self.jobs[run['id']] = []
        return run

    def add_release(self, tag, ref, files):
        release = {'tag_name': tag, 'draft': False, 'prerelease': True,
                   'target_commitish': ref, 'assets': []}
        self.tags[tag] = ref
        for name, data in files.items():
            self.asset_id += 1
            a = {'id': self.asset_id, 'name': name, 'size': len(data),
                 'digest': 'sha256:'+pub.checksum(data),
                 'browser_download_url': f'https://github.com/{pub.REPOSITORY}/releases/download/{tag}/{name}'}
            release['assets'].append(a)
            self.bytes[a['id']] = data
        self.releases[tag] = release
        return release

    def release(self, tag):
        return self.releases.get(tag)

    def download(self, release, name):
        a = self.asset(release, name)
        data = self.bytes[a['id']]
        if len(data) != a['size'] or pub.checksum(data) != a['digest'][7:]:
            raise ValueError('corrupt download')
        return data

    def request(self, method, path, body=None, missing=False):
        prefix = '/repos/'+pub.REPOSITORY+'/'
        if not path.startswith(prefix):
            raise AssertionError('Unexpected repository')
        endpoint = path[len(prefix):]
        if method == 'GET':
            if endpoint == 'git/ref/heads/main':
                return {'object': {'sha': self.repo.git('rev-parse', 'refs/heads/main')}}
            if endpoint.startswith('git/commits/'):
                ref = endpoint.split('/')[-1]
                return {'tree': {'sha': self.repo.git('rev-parse', ref+'^{tree}')}}
            if endpoint.startswith('git/ref/tags/'):
                tag = endpoint.removeprefix('git/ref/tags/')
                return {'object': {'type': 'commit', 'sha': self.tags[tag]}}
            if endpoint.startswith('actions/workflows/'):
                workflow = endpoint.split('/')[2]
                matches = [r for r in self.runs.values() if r['path'].endswith('/'+workflow)]
                query = endpoint.partition('?')[2]
                from urllib.parse import parse_qs
                options = parse_qs(query)
                if 'head_sha' in options: matches = [r for r in matches if r['head_sha']==options['head_sha'][0]]
                if 'status' in options: matches = [r for r in matches if r['conclusion']==options['status'][0]]
                return {'workflow_runs': sorted(matches, key=lambda r:r['id'], reverse=True)}
            if endpoint.startswith('actions/runs/'):
                run_id = int(endpoint.split('/')[2])
                if '/jobs?' in endpoint: return {'jobs': self.jobs[run_id]}
                return self.runs[run_id]
            raise AssertionError('Unexpected GET '+endpoint)
        self.writes.append((method, endpoint, body))
        if method == 'POST' and endpoint == 'git/trees':
            with tempfile.TemporaryDirectory() as temp:
                env = {**os.environ, 'GIT_INDEX_FILE': str(Path(temp)/'index')}
                def git(*args, data=None):
                    return subprocess.run(['git','-C',str(self.repo.root),*args],env=env,input=data,capture_output=True,check=True).stdout.decode().strip()
                git('read-tree',body['base_tree'])
                for e in body['tree']:
                    blob=git('hash-object','-w','--stdin',data=e['content'].encode())
                    git('update-index','--add','--cacheinfo',e['mode'],blob,e['path'])
                return {'sha':git('write-tree')}
        if method == 'POST' and endpoint == 'git/commits':
            out=subprocess.run(['git','-C',str(self.repo.root),'commit-tree',body['tree'],'-p',body['parents'][0]],input=body['message'].encode(),capture_output=True,check=True)
            return {'sha':out.stdout.decode().strip()}
        if method == 'PATCH' and endpoint == 'git/refs/heads/main':
            if self.before_patch:self.before_patch()
            assert body.get('force') is False
            old=self.repo.git('rev-parse','refs/heads/main')
            if not self.repo.ancestor(old,body['sha']): raise RuntimeError('non-fast-forward')
            self.repo.git('update-ref','refs/heads/main',body['sha'],old)
            return {'object':{'sha':body['sha']}}
        raise AssertionError('Unexpected write '+endpoint)


class PublicationTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        subprocess.run(['git','init','-q','-b','main',str(self.root)],check=True)
        self.repo=pub.Repo(self.root)
        self.repo.git('config','user.name','Fixture');self.repo.git('config','user.email','fixture@example.invalid')
        self.api=FakeGitHub(self.repo)
        self.write('Dockerfile','RUN cmake -DFPTN_VERSION=0.8.0\n')
        self.write('sources.lock.json',pub.encoded({k:'a'*40 for k in ['fptn','boringssl','camouflage_tls','ntp_client','yaff']}))
        self.write('sources.upstream.json',pub.encoded({'release':'0.4.6','revision':'a'*40}))
        self.write('src/protocol.h','original protocol\n')
        self.write('scripts/install-web-ui.sh','#!/bin/sh\n# fixture installer\n')
        self.write('scripts/package-runtime.py','# fixture packager\n')
        self.write('install.sh','#!/bin/sh\n# fixture bootstrap\n')
        self.write('web/index.html','<html data-ui-version="0.18.3">\n')
        self.write('web/app.js',"const UI_VERSION = '0.18.3';\n")
        self.write('web/api.cgi','#!/bin/sh\n')
        self.source=self.commit('source')
        self.rows,self.assets=self.publish_clients(self.source)
        self.write('client-release.json',pub.encoded(self.rows[pub.PLATFORMS[0]]))
        self.write('runtime-assets.json',pub.encoded(self.assets[pub.PLATFORMS[0]]))
        self.write('platform-releases.json',pub.encoded({'schema':1,'platforms':{p:{'client':self.rows[p],'assets':self.assets[p]} for p in pub.PLATFORMS[1:]}}))
        self.refresh_manifest()
        self.runtime=self.commit('pin fixture binaries')
        self.publish_runtime(self.runtime)
        channel={'schema':1,'version':'0.18.3','revision':self.runtime,'installerSha256':pub.checksum(self.repo.read(self.runtime,'scripts/install-web-ui.sh')),
                 'clientRevision':self.source,'clientVersion':'0.8.0','clientSha256':self.rows[pub.PLATFORMS[0]]['sha256'],
                 'upstreamVersion':'0.4.6','upstreamRevision':'a'*40}
        channel['platforms']={p:{'clientVersion':'0.8.0','clientRevision':self.source,'clientSha256':self.rows[p]['sha256'],'upstreamVersion':'0.4.6','upstreamRevision':'a'*40} for p in pub.PLATFORMS}
        self.write('web/update.json',pub.encoded(channel));self.main=self.commit('published channel')

    def write(self,name,value):
        p=self.root/name;p.parent.mkdir(parents=True,exist_ok=True)
        p.write_bytes(value if isinstance(value,bytes) else value.encode())

    def commit(self,message):
        self.repo.git('add','.');self.repo.git('commit','-q','-m',message)
        return self.repo.head()

    def refresh_manifest(self):
        names=['web/index.html','web/app.js','web/api.cgi','client-release.json','runtime-assets.json','platform-releases.json']
        self.write('web/SHA256SUMS',''.join(pub.checksum((self.root/n).read_bytes())+'  '+n+'\n' for n in names))

    def publish_clients(self,source,arm_ready=True):
        self.api.add_run(pub.WORKFLOWS['mips'],source)
        self.api.add_run(pub.WORKFLOWS['arm'],source,success=arm_ready)
        expected=self.repo.expected(source)
        rows,assets={},{}
        for p in pub.PLATFORMS:
            client=('compiled '+p+' '+source).encode();decoder=('decoder '+p).encode();license=b'original MIT license';decoder_license=b'decoder license';decoder_source=b'pinned decoder origin'
            row={'schema':1,'platform':p,'buildRevision':source,'sha256':pub.checksum(client),**expected}
            tag=('native-' if p==pub.PLATFORMS[0] else 'port-'+p+'-')+source
            asset={'schema':1,'platform':p,'decoderRelease':('decoder-'+'d'*40 if p==pub.PLATFORMS[0] else tag),
                   'decoderSha256':pub.checksum(decoder),'decoderLicenseSha256':pub.checksum(decoder_license),
                   'decoderSourceSha256':pub.checksum(decoder_source),'clientLicenseSha256':pub.checksum(license)}
            files={'fptn-client-cli':client,'client-release.json':pub.encoded(row),'sources.lock.json':self.repo.read(source,'sources.lock.json'),'LICENSE-FPTN':license}
            if p!=pub.PLATFORMS[0]: files.update({'runtime-assets.json':pub.encoded(asset),'brotli':decoder,'LICENSE-BROTLI':decoder_license,'BROTLI-SOURCE.txt':decoder_source})
            self.api.add_release(tag,source,files)
            rows[p]=row;assets[p]={**asset,**({'clientRelease':tag} if p!=pub.PLATFORMS[0] else {})}
        return rows,assets

    def publish_runtime(self,ref,legacy=False,live=False,parent=None):
        workflow=pub.WORKFLOWS['publication'] if live else pub.WORKFLOWS['runtime']
        run=self.api.add_run(workflow,parent or ref,event='workflow_run' if live else 'push',state='in_progress' if live else 'completed')
        self.api.jobs[run['id']]=[{'id':100+run['id'],'name':('validate / ' if live else '')+f'runtime-test-{ref}','conclusion':'success','run_attempt':1},
                                  {'id':200+run['id'],'name':('validate / ' if live else '')+f'runtime-package-{ref}','conclusion':'success','run_attempt':1}]
        files={name:('complete archive '+name+' '+ref).encode() for name in pub.ARCHIVES}
        sums=''.join(pub.checksum(files[name])+'  '+name+'\n' for name in pub.ARCHIVES).encode();files['SHA256SUMS']=sums
        if not legacy:
            files['runtime-evidence.json']=pub.encoded({'schema':1,'repository':pub.REPOSITORY,'runtimeRevision':ref,'runId':run['id'],'runAttempt':1,
                'archives':{n:pub.checksum(files[n]) for n in pub.ARCHIVES},'manifestSha256':pub.checksum(sums)})
        else:
            for j,n in zip(self.api.jobs[run['id']],['test','package']):j['name']=n
        self.api.add_release('runtime-'+ref,ref,files)
        return run

    def changed_client(self):
        self.write('src/protocol.h','new reviewed source\n')
        return self.commit('new client source')

    def changed_ui(self):
        self.write('web/app.js',"const UI_VERSION = '0.18.3';\n// UI-only fix\n")
        self.refresh_manifest()
        return self.commit('UI-only fix')

    def test_ci_only_change_and_channel_commit_do_not_prompt_an_update(self):
        self.write('scripts/release-publication.py','# changed CI tool only\n');self.commit('CI maintenance')
        result=pub.prepare(self.api,self.repo)
        self.assertEqual(result['status'],'up-to-date');self.assertTrue(result['publishedVerified'])
        self.assertEqual(self.api.writes,[])

    def test_ui_only_release_reuses_binaries_and_publishes_after_checks(self):
        head=self.changed_ui();self.publish_runtime(head)
        self.assertEqual(pub.prepare(self.api,self.repo)['status'],'promote')
        result=pub.promote(self.api,self.repo,head)
        self.assertEqual(result['status'],'published')
        channel=json.loads(self.repo.read(result['commit'],'web/update.json'))
        self.assertEqual(channel['revision'],head);self.assertEqual(channel['clientRevision'],self.source)
        changed=self.repo.git('diff-tree','--no-commit-id','--name-only','-r',result['commit']).splitlines()
        self.assertEqual(changed,['web/update.json'])

    def test_full_client_pin_then_explicit_reusable_validation_then_promotion(self):
        head=self.changed_client();rows,assets=self.publish_clients(head)
        plan=pub.prepare(self.api,self.repo)
        self.assertEqual(plan['status'],'validate');pinned=plan['runtime']
        self.assertEqual(self.repo.git('rev-parse','refs/heads/main'),pinned)
        changed=set(self.repo.git('diff-tree','--no-commit-id','--name-only','-r',pinned).splitlines())
        self.assertLessEqual(changed,set(pub.PIN_FILES));self.assertNotIn('web/update.json',changed)
        self.repo.git('checkout','-q','--detach',pinned)
        self.assertTrue(pub.compatible(self.repo,pinned,pub.records(self.repo,pinned)))
        # No automatic empty retry if validation has not completed or failed.
        self.assertEqual(pub.prepare(self.api,self.repo)['status'],'waiting')
        run=self.publish_runtime(pinned,live=True,parent=head)
        result=pub.promote(self.api,self.repo,pinned,current_run=run['id'])
        self.assertEqual(result['status'],'published')
        self.assertEqual(json.loads(self.repo.read(result['commit'],'client-release.json'))['sha256'],rows[pub.PLATFORMS[0]]['sha256'])

    def test_generated_pins_package_with_the_real_runtime_builder_for_all_platforms(self):
        source=self.changed_client();self.publish_clients(source)
        plan=pub.prepare(self.api,self.repo);self.assertEqual(plan['status'],'validate')
        pinned=plan['runtime'];self.repo.git('checkout','-q','--detach',pinned)
        self.repo.git('restore','--source='+pinned,'--staged','--worktree','.')
        self.repo.manifest(pinned)  # Check every generated checksum, not just JSON fields.
        spec=importlib.util.spec_from_file_location('actual_package',ROOT/'scripts/package-runtime.py')
        package=importlib.util.module_from_spec(spec);spec.loader.exec_module(package)
        import tarfile
        for platform in pub.PLATFORMS:
            with tempfile.TemporaryDirectory() as temp:
                root=Path(temp);client=root/'client';decoder=root/'decoder';output=root/'output'
                for folder in [client,decoder,output]:folder.mkdir()
                tag=('native-' if platform==pub.PLATFORMS[0] else 'port-'+platform+'-')+source
                release=self.api.releases[tag]
                for name in ['fptn-client-cli','sources.lock.json','LICENSE-FPTN']:
                    (client/name).write_bytes(self.api.download(release,name))
                if platform==pub.PLATFORMS[0]:
                    data={'brotli':('decoder '+platform).encode(),'LICENSE-BROTLI':b'decoder license','BROTLI-SOURCE.txt':b'pinned decoder origin'}
                else:data={n:self.api.download(release,n) for n in ['brotli','LICENSE-BROTLI','BROTLI-SOURCE.txt']}
                for name,value in data.items():(decoder/name).write_bytes(value)
                archive=package.build(self.root,client,decoder,output,pinned,platform)
                with tarfile.open(archive) as tar:
                    self.assertEqual(tar.extractfile('fptn-keenetic/client/fptn-client-cli').read(),(client/'fptn-client-cli').read_bytes())
                    self.assertEqual(tar.extractfile('fptn-keenetic/release-revision').read().decode().strip(),pinned)
                checked=subprocess.run(['sha256sum','-c','SHA256SUMS'],cwd=output/'fptn-keenetic',capture_output=True,text=True)
                self.assertEqual(checked.returncode,0,checked.stdout+checked.stderr)

    def test_missing_or_failed_arm_build_does_not_create_commits(self):
        head=self.changed_client();self.publish_clients(head,arm_ready=False)
        self.assertEqual(pub.prepare(self.api,self.repo)['status'],'waiting');self.assertEqual(self.api.writes,[])

    def test_latest_failed_or_running_attempt_cannot_use_an_old_success(self):
        head=self.changed_client();self.publish_clients(head)
        run=self.api.add_run(pub.WORKFLOWS['mips'],head,success=False)
        self.assertEqual(pub.prepare(self.api,self.repo)['status'],'waiting')
        run.update(status='in_progress',conclusion=None)
        self.assertEqual(pub.prepare(self.api,self.repo)['status'],'waiting');self.assertEqual(self.api.writes,[])

    def test_newer_ui_commit_can_use_completed_ancestor_build_with_identical_inputs(self):
        source=self.changed_client();self.publish_clients(source);head=self.changed_ui()
        self.assertEqual(pub.prepare(self.api,self.repo,dry_run=True)['status'],'would-pin')
        self.assertNotEqual(head,source);self.assertEqual(self.api.writes,[])

    def test_wrong_client_metadata_or_corrupt_download_is_rejected(self):
        source=self.changed_client();self.publish_clients(source)
        tag='native-'+source;a=self.api.asset(self.api.releases[tag],'client-release.json')
        self.api.bytes[a['id']]=b'corrupted'
        with self.assertRaises(ValueError):pub.prepare(self.api,self.repo)
        self.assertEqual(self.api.writes,[])

    def test_retargeted_tag_and_missing_architecture_archive_cannot_publish(self):
        head=self.changed_ui();self.publish_runtime(head)
        self.api.tags['runtime-'+head]=self.source
        with self.assertRaises(ValueError):pub.promote(self.api,self.repo,head)
        self.api.tags['runtime-'+head]=head
        release=self.api.releases['runtime-'+head];release['assets']=[a for a in release['assets'] if a['name']!=pub.ARCHIVES[1]]
        with self.assertRaises(ValueError):pub.promote(self.api,self.repo,head)
        self.assertEqual(self.api.writes,[])

    def test_runtime_evidence_and_checksum_list_are_bound_to_all_assets(self):
        head=self.changed_ui();self.publish_runtime(head)
        release=self.api.releases['runtime-'+head];a=self.api.asset(release,'SHA256SUMS')
        wrong=b'0'*64+b'  other.tar.gz\n';self.api.bytes[a['id']]=wrong;a.update(size=len(wrong),digest='sha256:'+pub.checksum(wrong))
        with self.assertRaises(ValueError):pub.promote(self.api,self.repo,head)
        self.assertEqual(self.api.writes,[])

    def test_failed_or_cancelled_runtime_proof_does_not_move_channel(self):
        head=self.changed_ui();run=self.publish_runtime(head)
        for conclusion in ['failure','cancelled']:
            run['conclusion']=conclusion
            self.assertEqual(pub.promote(self.api,self.repo,head)['status'],'waiting')
        self.assertEqual(self.api.writes,[])

    def test_failed_later_test_attempt_blocks_old_package_evidence(self):
        head=self.changed_ui();run=self.publish_runtime(head);run['run_attempt']=2
        self.api.jobs[run['id']].append({'id':9999,'name':f'runtime-test-{head}','conclusion':'failure','run_attempt':2})
        self.assertEqual(pub.promote(self.api,self.repo,head)['status'],'waiting');self.assertEqual(self.api.writes,[])

    def test_legacy_published_runtime_can_be_verified_without_evidence_asset(self):
        self.publish_runtime(self.runtime,legacy=True)
        self.assertEqual(pub.prepare(self.api,self.repo)['status'],'up-to-date');self.assertEqual(self.api.writes,[])

    def test_main_advance_before_promotion_and_during_patch_preserves_other_changes(self):
        head=self.changed_ui();self.publish_runtime(head)
        self.write('README.md','concurrent maintainer change\n');new=self.commit('concurrent')
        self.assertEqual(pub.promote(self.api,self.repo,head)['status'],'stale')
        self.assertEqual(self.repo.git('rev-parse','refs/heads/main'),new)
        # Return to the fixture's candidate without rewriting any production ref.
        self.repo.git('checkout','-q','--detach',head);self.repo.git('update-ref','refs/heads/main',head,new)
        self.api.before_patch=lambda:self.repo.git('update-ref','refs/heads/main',new,head)
        self.assertEqual(pub.promote(self.api,self.repo,head)['status'],'stale')
        self.assertEqual(self.repo.git('rev-parse','refs/heads/main'),new)

    def test_dry_run_performs_no_remote_or_local_ref_writes(self):
        head=self.changed_ui();self.publish_runtime(head)
        result=pub.promote(self.api,self.repo,head,dry_run=True)
        self.assertEqual(result['status'],'would-promote');self.assertEqual(self.api.writes,[])
        self.assertEqual(self.repo.git('rev-parse','refs/heads/main'),head)

    def test_manual_resume_validates_without_creating_another_pin_or_build(self):
        self.changed_ui()
        self.assertEqual(pub.prepare(self.api,self.repo)['status'],'waiting')
        self.assertEqual(pub.prepare(self.api,self.repo,manual=True)['status'],'validate')
        self.assertEqual(pub.prepare(self.api,self.repo,manual=True,dry_run=True)['status'],'would-validate')
        self.assertEqual(self.api.writes,[])

    def test_downgrade_and_stale_manifest_are_refused(self):
        self.write('web/index.html','<html data-ui-version="0.18.2">\n');self.write('web/app.js',"const UI_VERSION = '0.18.2';\n");self.refresh_manifest();self.commit('older panel')
        with self.assertRaises(ValueError):pub.prepare(self.api,self.repo)
        self.assertEqual(self.api.writes,[])

    def test_new_docker_copy_inputs_are_tracked_even_outside_existing_folders(self):
        self.write('build-extra/input.h','one\n')
        self.write('Dockerfile','RUN cmake -DFPTN_VERSION=0.8.0\nCOPY build-extra/input.h /work/input.h\n')
        before=self.commit('add generated input');self.publish_clients(before)
        self.write('build-extra/input.h','two\n');after=self.commit('change generated input')
        self.assertNotEqual(self.repo.client_key(before),self.repo.client_key(after))
        self.assertEqual(pub.prepare(self.api,self.repo)['status'],'waiting')
        self.assertEqual(self.api.writes,[])

    def test_client_and_original_source_downgrade_are_refused(self):
        for field,old in [('clientVersion','0.7.0'),('upstreamVersion','0.4.5')]:
            rows={p:{**row,field:old} for p,row in self.rows.items()}
            with self.assertRaises(ValueError):pub.check_channel_versions(self.repo,self.main,rows)
        self.assertEqual(self.api.writes,[])

    def test_evidence_cannot_claim_another_repository_or_revision(self):
        head=self.changed_ui();self.publish_runtime(head)
        release=self.api.releases['runtime-'+head];a=self.api.asset(release,'runtime-evidence.json')
        original=json.loads(self.api.bytes[a['id']])
        for change in [{'repository':'another/repo'},{'runtimeRevision':self.source},{'runAttempt':None}]:
            data=pub.encoded({**original,**change});self.api.bytes[a['id']]=data;a.update(size=len(data),digest='sha256:'+pub.checksum(data))
            with self.assertRaises(ValueError):pub.promote(self.api,self.repo,head)
        self.assertEqual(self.api.writes,[])

    def test_wrong_client_source_layout_never_reuses_binaries(self):
        self.changed_client()
        self.assertEqual(pub.prepare(self.api,self.repo)['status'],'waiting');self.assertEqual(self.api.writes,[])


class BoundaryTest(unittest.TestCase):
    def test_only_successful_owned_main_push_or_dispatch_events_are_eligible(self):
        base={'conclusion':'success','event':'push','head_branch':'main','head_repository':{'full_name':pub.REPOSITORY}}
        self.assertTrue(pub.eligible_event({'workflow_run':base}))
        for change in [{'event':'pull_request'},{'head_branch':'feature'},{'conclusion':'cancelled'}, {'head_repository':{'full_name':'another/repository'}}]:
            self.assertFalse(pub.eligible_event({'workflow_run':{**base,**change}}))

    def test_api_writes_cannot_escape_the_repository(self):
        api=pub.GitHub('not-a-real-token')
        for method in ['GET','POST','PATCH']:
            with self.assertRaises(ValueError):api.request(method,'/repos/another/repository/git/refs/heads/main',{})
        for method,path,body in [('DELETE','/git/refs/heads/main',{}),('PATCH','/git/refs/heads/main',{'force':True}),('POST','/releases',{})]:
            with self.assertRaises(ValueError):api.request(method,'/repos/'+pub.REPOSITORY+path,body)

    def test_missing_metadata_is_distinct_from_permission_failure_and_redirects(self):
        import urllib.error
        api=pub.GitHub('token-that-must-not-be-printed')
        path='/repos/'+pub.REPOSITORY+'/releases/tags/runtime-'+'a'*40
        with patch.object(api.opener,'open',side_effect=urllib.error.HTTPError('https://api.github.com'+path,404,'missing',{},None)):
            self.assertIsNone(api.get(path,missing=True))
        with patch.object(api.opener,'open',side_effect=urllib.error.HTTPError('https://api.github.com'+path,403,'forbidden',{},None)):
            with self.assertRaises(RuntimeError) as raised:api.get(path,missing=True)
            self.assertNotIn(api.token,str(raised.exception))
        with self.assertRaises(RuntimeError):pub.NoApiRedirect().redirect_request(None,None,302,'',{},'https://another.example/')

    def test_metadata_download_uses_immutable_asset_id_and_keeps_token_out_of_command(self):
        data=b'checked metadata';name='SHA256SUMS';tag='runtime-'+'a'*40
        release={'tag_name':tag,'assets':[{'id':42,'name':name,'digest':'sha256:'+pub.checksum(data),'size':len(data)}]}
        response=subprocess.CompletedProcess([],0,stdout=data,stderr=b'')
        with patch.object(pub.subprocess,'run',return_value=response) as execute:
            self.assertEqual(pub.GitHub('private-api-token').download(release,name),data)
        command=execute.call_args.args[0]
        self.assertEqual(command[-1],f'repos/{pub.REPOSITORY}/releases/assets/42')
        self.assertNotIn('private-api-token',str(command))
        self.assertEqual(execute.call_args.kwargs['env']['GH_TOKEN'],'private-api-token')
        # Error logs retain the HTTP code but never CLI stderr/signed URLs/token.
        failed=subprocess.CompletedProcess([],1,stdout=b'',stderr=b'HTTP 403 private-api-token signed-url')
        with patch.object(pub.subprocess,'run',return_value=failed):
            with self.assertRaises(RuntimeError) as raised:pub.GitHub('private-api-token').download(release,name)
            self.assertIn('HTTP 403',str(raised.exception));self.assertNotIn('private-api-token',str(raised.exception))

    def test_reusable_publication_is_opt_in_and_pr_checker_does_not_enable_it(self):
        import yaml
        def load(name):return yaml.safe_load((ROOT/'.github/workflows'/name).read_text())
        runtime=load('check-runtime.yml');events=runtime.get('on',runtime.get(True))
        self.assertIs(events['workflow_call']['inputs']['publish-runtime']['default'],False)
        checker=load('check-upstream.yml')
        self.assertNotIn('publish-runtime',checker['jobs']['panel']['with'])
        publisher=load('release-publication.yml')
        self.assertTrue(publisher['jobs']['validate']['with']['publish-runtime'])
        self.assertEqual(publisher['jobs']['prepare']['permissions'],{'contents':'write','actions':'read'})
        self.assertFalse(publisher['concurrency']['cancel-in-progress'])

    def test_evidence_matches_exact_archives_and_does_not_enter_install_manifest(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            for name in pub.ARCHIVES:(root/name).write_bytes(name.encode())
            (root/'SHA256SUMS').write_text(''.join(pub.checksum((root/n).read_bytes())+'  '+n+'\n' for n in pub.ARCHIVES))
            result=pub.evidence(root,'a'*40,'123','2')
            self.assertEqual(result['runAttempt'],2)
            self.assertEqual(result['archives'],{n:pub.checksum(n.encode()) for n in pub.ARCHIVES})
            self.assertNotIn('runtime-evidence.json',(ROOT/'web/SHA256SUMS').read_text())
            (root/pub.ARCHIVES[0]).write_bytes(b'changed')
            with self.assertRaises(ValueError):pub.evidence(root,'a'*40,'123','2')


if __name__=='__main__':unittest.main()
