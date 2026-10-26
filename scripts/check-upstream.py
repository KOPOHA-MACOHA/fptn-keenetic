#!/usr/bin/env python3
"""Propose stable FPTN upgrades without editing main or moving existing branches."""
import base64
import json
import os
from pathlib import Path
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

REPOSITORY = 'KOPOHA-MACOHA/fptn-keenetic'
UPSTREAM = 'fptn-project/fptn'
SHA = re.compile(r'[0-9a-f]{40}\Z')
VERSION = re.compile(r'v?(\d+)\.(\d+)\.(\d+)\Z')


class GitHub:
    def __init__(self, token):
        self.token = token

    def request(self, method, path, body=None, missing=False):
        # Only this fork may be changed, including when called with test inputs.
        if method != 'GET' and not path.startswith('/repos/' + REPOSITORY + '/'):
            raise ValueError('Writes outside the authorized fork are forbidden')
        request = urllib.request.Request('https://api.github.com' + path,
            data=None if body is None else json.dumps(body).encode(), method=method,
            headers={'Authorization':'Bearer ' + self.token, 'Accept':'application/vnd.github+json',
                     'X-GitHub-Api-Version':'2022-11-28', 'User-Agent':'fptn-keenetic-upstream-check'})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            if missing and error.code == 404:
                return None
            # Never print token, headers, or unbounded upstream-controlled content.
            raise RuntimeError(f'GitHub {method} {path.split("?")[0]}: HTTP {error.code}. '
                'For PR creation, check that GitHub Actions may create pull requests in repository settings.') from None

    def get(self, path, missing=False):
        return self.request('GET', path, missing=missing)

    def post(self, path, body):
        return self.request('POST', path, body)


def valid_sha(value):
    if not isinstance(value, str) or not SHA.fullmatch(value):
        raise ValueError('Expected a full commit SHA')
    return value


def stable_release(releases):
    candidates = []
    for release in releases:
        match = VERSION.fullmatch(release.get('tag_name', ''))
        if match and not release.get('draft', True) and not release.get('prerelease', True):
            candidates.append((tuple(map(int, match.groups())), release))
    if not candidates:
        raise ValueError('No stable semantic FPTN release found; source lock was not changed')
    return max(candidates, key=lambda item: item[0])[1]


def read_lock(api, repository, ref):
    value = api.get(f'/repos/{repository}/contents/sources.lock.json?ref={ref}')
    if value.get('encoding') != 'base64' or value.get('size', 0) > 65536:
        raise ValueError('Unexpected source lock format')
    lock = json.loads(base64.b64decode(value['content']))
    if set(lock) != {'fptn','boringssl','camouflage_tls','ntp_client','yaff'}:
        raise ValueError('Source lock schema changed; review the updater')
    for sha in lock.values():
        valid_sha(sha)
    return lock


def propose(api, run_url):
    release = stable_release(api.get(f'/repos/{UPSTREAM}/releases?per_page=100'))
    tag = release['tag_name']
    candidate = valid_sha(api.get(f'/repos/{UPSTREAM}/commits/{urllib.parse.quote(tag, safe="")}')['sha'])
    main = valid_sha(api.get(f'/repos/{REPOSITORY}/git/ref/heads/main')['object']['sha'])
    lock = read_lock(api, REPOSITORY, main)
    before = lock['fptn']
    comparison = api.get(f'/repos/{UPSTREAM}/compare/{before}...{candidate}')
    status = comparison['status']
    base_result = {'changed':False, 'release':tag, 'revision':candidate, 'source_sha':'', 'pr_url':''}
    if status == 'identical':
        return {**base_result, 'message':f'Already using stable FPTN {tag} ({candidate[:12]}).'}
    if status == 'behind':
        return {**base_result, 'message':f'Stable {tag} is older than the pinned source; refusing a downgrade.'}
    if status != 'ahead' or comparison.get('ahead_by', 0) < 1:
        raise ValueError('Stable release diverges from the pinned source; manual review required')
    branch = f'auto/upstream-{tag}-{candidate[:12]}'
    pulls = api.get(f'/repos/{REPOSITORY}/pulls?state=all&head=KOPOHA-MACOHA:{branch}&base=main&per_page=100')
    if pulls:
        pull = pulls[0]
        return {**base_result, 'pr_url':pull['html_url'],
                'message':'This release already has a PR; no duplicate branch or build was created.'}
    existing = api.get(f'/repos/{REPOSITORY}/git/ref/heads/{branch}', missing=True)
    if existing:
        head = valid_sha(existing['object']['sha'])
        # Resume after a branch was created but PR creation failed. Never rewrite it.
        if read_lock(api, REPOSITORY, head)['fptn'] != candidate:
            raise ValueError('Existing update branch has different sources; preserving it for review')
    else:
        tree = api.get(f'/repos/{REPOSITORY}/git/commits/{main}')['tree']['sha']
        updated = {**lock, 'fptn':candidate}
        metadata = {'schema':1, 'repository':UPSTREAM, 'release':tag, 'revision':candidate}
        entries = [{'path':name, 'mode':'100644', 'type':'blob', 'content':json.dumps(value, indent=2)+'\n'}
                   for name,value in [('sources.lock.json',updated),('sources.upstream.json',metadata)]]
        tree = api.post(f'/repos/{REPOSITORY}/git/trees', {'base_tree':tree,'tree':entries})['sha']
        head = valid_sha(api.post(f'/repos/{REPOSITORY}/git/commits',
            {'message':f'Update FPTN upstream to {tag}', 'tree':tree,'parents':[main]})['sha'])
        api.post(f'/repos/{REPOSITORY}/git/refs', {'ref':'refs/heads/'+branch,'sha':head})
    body = (f'Update original FPTN from `{before}` to stable [{tag}]'
            f'(https://github.com/{UPSTREAM}/releases/tag/{tag}) (`{candidate}`).\n\n'
            'The four pinned dependency revisions are preserved. Keenetic adaptations, '
            'routing ownership and router settings are unchanged.\n\n'
            f'Validation runs against this exact PR commit: {run_url}\n\n'
            'The scheduled workflow calls the build explicitly because PRs created by '
            'GITHUB_TOKEN do not trigger pull_request workflows. Check the compatibility '
            'patches, cross-compilation and QEMU results before merging. Router validation '
            'and publishing a new web update catalog remain separate steps. No automatic merge or deployment.')
    pull = api.post(f'/repos/{REPOSITORY}/pulls', {'title':f'Update original FPTN to {tag}',
        'head':branch,'base':'main','body':body,'draft':True})
    return {**base_result, 'changed':True, 'source_sha':head, 'pr_url':pull['html_url'],
            'message':'Created draft PR; compatibility checks and the client build will run next.'}


def main():
    if os.environ.get('GITHUB_REPOSITORY') != REPOSITORY:
        sys.exit('This workflow is limited to ' + REPOSITORY)
    token = os.environ.get('GH_TOKEN')
    if not token:
        sys.exit('GH_TOKEN is required')
    run_url = f'https://github.com/{REPOSITORY}/actions/runs/{os.environ["GITHUB_RUN_ID"]}'
    try:
        result = propose(GitHub(token), run_url)
    except (ValueError, RuntimeError, KeyError, TypeError) as error:
        sys.exit(str(error))
    print(json.dumps(result))
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a') as stream:
            for key in ('changed','source_sha','pr_url'):
                value = result[key]
                stream.write(f'{key}={str(value).lower() if isinstance(value,bool) else value}\n')
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as stream:
            stream.write(result['message']+'\n\n'+result['pr_url']+'\n')


if __name__ == '__main__':
    main()
