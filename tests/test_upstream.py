"""Release automation with an in-memory GitHub API; never writes upstream."""
import base64
import importlib.util
import json
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('upstream',ROOT/'scripts/check-upstream.py')
upstream=importlib.util.module_from_spec(spec);spec.loader.exec_module(upstream)
A='a'*40;B='b'*40;C='c'*40;D='d'*40

class FakeGitHub:
    def __init__(self):
        self.writes=[];self.reads=[];self.lock={'fptn':A,'boringssl':C,'camouflage_tls':C,'ntp_client':C,'yaff':C}
        self.status='ahead';self.pulls=[];self.existing=None;self.branch_lock=None
        self.releases=[{'tag_name':'0.4.7','draft':False,'prerelease':False}]
    def get(self,path,missing=False):
        self.reads.append(path)
        if '/releases?' in path:return self.releases
        if path.startswith('/repos/fptn-project/fptn/commits/'):return {'sha':B}
        if '/compare/' in path:return {'status':self.status,'ahead_by':1 if self.status=='ahead' else 0}
        if '/git/ref/heads/main' in path:return {'object':{'sha':C}}
        if '/contents/sources.lock.json?' in path:
            lock=self.branch_lock if path.endswith(D) and self.branch_lock else self.lock
            raw=json.dumps(lock).encode();return {'encoding':'base64','size':len(raw),'content':base64.b64encode(raw).decode()}
        if '/pulls?' in path:return self.pulls
        if '/git/ref/heads/auto/' in path:return self.existing
        if '/git/commits/' in path:return {'tree':{'sha':C}}
        raise AssertionError(path)
    def post(self,path,body):
        assert path.startswith('/repos/KOPOHA-MACOHA/fptn-keenetic/')
        self.writes.append((path,body))
        if path.endswith('/git/trees'):return {'sha':C}
        if path.endswith('/git/commits'):return {'sha':D}
        if path.endswith('/git/refs'):return {}
        if path.endswith('/pulls'):return {'html_url':'https://github.com/KOPOHA-MACOHA/fptn-keenetic/pull/1'}
        raise AssertionError(path)

class UpstreamTest(unittest.TestCase):
    def test_stable_release_selection_excludes_previews_drafts_and_non_versions(self):
        entries=[{'tag_name':tag,'draft':draft,'prerelease':pre} for tag,draft,pre in
                 [('0.4.6',False,False),('v0.4.10',False,False),('0.4.9',False,False),('1.0.0',True,False),('2.0.0',False,True),('3.0.0-rc1',False,False),('main',False,False)]]
        self.assertEqual(upstream.stable_release(entries)['tag_name'],'v0.4.10')
    def test_no_new_release_or_downgrade_causes_no_writes(self):
        for status in ['identical','behind']:
            api=FakeGitHub();api.status=status
            self.assertFalse(upstream.propose(api,'https://example.test/run')['changed']);self.assertEqual(api.writes,[])
    def test_diverged_or_malformed_sources_stop_before_writes(self):
        api=FakeGitHub();api.status='diverged'
        with self.assertRaises(ValueError):upstream.propose(api,'run')
        self.assertEqual(api.writes,[])
        api=FakeGitHub();api.lock['boringssl']='main'
        with self.assertRaises(ValueError):upstream.propose(api,'run')
        self.assertEqual(api.writes,[])
    def test_new_release_changes_only_source_lock_and_metadata_in_new_branch(self):
        api=FakeGitHub();result=upstream.propose(api,'https://example.test/run')
        self.assertTrue(result['changed']);self.assertEqual(result['source_sha'],D)
        tree=api.writes[0][1];self.assertEqual(tree['base_tree'],C)
        self.assertEqual([x['path'] for x in tree['tree']],['sources.lock.json','sources.upstream.json'])
        lock=json.loads(tree['tree'][0]['content']);self.assertEqual(lock,{**api.lock,'fptn':B})
        ref=api.writes[2][1];self.assertEqual(ref['sha'],D);self.assertTrue(ref['ref'].startswith('refs/heads/auto/upstream-0.4.7-'))
        pr=api.writes[3][1];self.assertTrue(pr['draft']);self.assertEqual(pr['base'],'main');self.assertIn('https://example.test/run',pr['body'])
    def test_existing_open_or_closed_pr_does_not_rebuild_or_overwrite(self):
        for state in ['open','closed']:
            api=FakeGitHub();api.pulls=[{'state':state,'html_url':'https://example.test/pr'}]
            result=upstream.propose(api,'run');self.assertFalse(result['changed']);self.assertEqual(api.writes,[])
    def test_resume_after_branch_creation_never_updates_existing_ref(self):
        api=FakeGitHub();api.existing={'object':{'sha':D}};api.branch_lock={**api.lock,'fptn':B}
        result=upstream.propose(api,'run');self.assertEqual(result['source_sha'],D)
        self.assertEqual([path.rsplit('/',1)[-1] for path,_ in api.writes],['pulls'])
        api=FakeGitHub();api.existing={'object':{'sha':D}}
        with self.assertRaises(ValueError):upstream.propose(api,'run')
        self.assertEqual(api.writes,[])
    def test_client_ref_is_used_for_each_checkout_and_candidate_cannot_publish(self):
        build=(ROOT/'.github/workflows/build.yml').read_text();runtime=(ROOT/'.github/workflows/check-runtime.yml').read_text()
        for workflow in [build,runtime]:
            self.assertIn('workflow_call:',workflow)
            self.assertEqual(workflow.count('uses: actions/checkout@v4'),workflow.count('ref: ${{ inputs.source-ref || github.sha }}'))
        self.assertIn("if: inputs.source-ref == '' && github.event_name == 'push'",build)
        checker=(ROOT/'.github/workflows/check-upstream.yml').read_text()
        self.assertIn('uses: ./.github/workflows/build.yml',checker);self.assertIn('uses: ./.github/workflows/check-runtime.yml',checker)
    def test_real_api_refuses_writes_to_other_repositories(self):
        with self.assertRaises(ValueError):upstream.GitHub('secret').post('/repos/fptn-project/fptn/pulls',{})
        with self.assertRaises(ValueError):upstream.GitHub('secret').post('/repos/other/project/git/refs',{})
