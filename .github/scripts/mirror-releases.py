#!/usr/bin/env python3
"""Copy the frozen release inventory into the prepared fork without overwriting assets."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import urllib.error
import urllib.request

SOURCE = 'KOPOHA-MACOHA/fptn-keenetic'
TARGET = 'KOPOHA-MACOHA/fptn-keenetic-next'
SOURCE_ID = 1432556444
TARGET_ID = 1453702975


def digest(path):
    with path.open('rb') as stream:
        return 'sha256:' + hashlib.file_digest(stream, 'sha256').hexdigest()


def checked_assets(release):
    result = {}
    for asset in release['assets']:
        name = asset['name']
        if (not name or Path(name).name != name or name in ('.', '..') or name in result or
                not isinstance(asset.get('digest'), str) or
                not re.fullmatch(r'sha256:[0-9a-f]{64}', asset['digest']) or
                type(asset.get('size')) is not int or asset['size'] < 0):
            raise ValueError('Invalid or duplicate release asset metadata')
        result[name] = asset
    return result


class Api:
    def __init__(self):
        self.token = os.environ['GH_TOKEN']

    def call(self, method, repo, endpoint='', body=None, missing=False):
        if repo not in (SOURCE, TARGET) or (method != 'GET' and repo != TARGET):
            raise ValueError('Writes are restricted to the temporary fork')
        request = urllib.request.Request('https://api.github.com/repos/'+repo+endpoint,
            data=None if body is None else json.dumps(body).encode(), method=method,
            headers={'Authorization': 'Bearer '+self.token, 'Accept': 'application/vnd.github+json',
                     'X-GitHub-Api-Version': '2022-11-28'})
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            if missing and error.code == 404:
                return None
            raise RuntimeError('GitHub '+method+' failed: HTTP '+str(error.code)) from None


def execute(args, output=None):
    result = subprocess.run(args, stdout=output or subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode:
        raise RuntimeError(args[0]+' failed with exit '+str(result.returncode))
    return result.stdout.decode().strip() if output is None else None


def mirror(api, inventory, directory):
    if os.environ.get('GITHUB_REPOSITORY') != TARGET:
        raise ValueError('Release migration runs only in the temporary fork')
    source = api.call('GET', SOURCE)
    target = api.call('GET', TARGET)
    if (source['id'] != SOURCE_ID or target['id'] != TARGET_ID or not target.get('fork') or
            target.get('parent', {}).get('full_name') != 'fptn-project/fptn' or
            target['default_branch'] != 'main'):
        raise ValueError('Repository identity/default branch changed; do not rename repositories yet')
    head = api.call('GET', SOURCE, '/branches/main')
    if head['commit']['sha'] != inventory['sourceRevision']:
        raise ValueError('Original main changed after inventory; refresh before copying releases')
    # Import original tag objects, preserving their SHAs and reachable history.
    execute(['git', 'fetch', '--no-tags', 'https://github.com/'+SOURCE+'.git',
             'refs/tags/*:refs/migration-tags/*'])
    refs = execute(['git', 'ls-remote', '--tags', 'origin']).splitlines()
    existing = dict(row.split('\t', 1)[::-1] for row in refs)
    refspecs = []
    for release in inventory['releases']:
        tag = release['tag_name']
        local = 'refs/migration-tags/'+tag
        commit = execute(['git', 'rev-parse', local+'^{commit}'])
        obj = execute(['git', 'rev-parse', local])
        if commit != release['target_commitish']:
            raise ValueError('Original release target/tag differs: '+tag)
        if 'refs/tags/'+tag in existing and existing['refs/tags/'+tag] != obj:
            raise ValueError('Target already has a different tag: '+tag)
        refspecs.append(local+':refs/tags/'+tag)
    execute(['git', 'push', 'origin', *refspecs])
    count = 0
    for release in inventory['releases']:
        tag = release['tag_name']
        expected = checked_assets(release)
        current_source = api.call('GET', SOURCE, '/releases/'+str(release['id']))
        live = checked_assets(current_source)
        if ({n: (a['id'], a['size'], a['digest']) for n, a in live.items()} !=
                {n: (a['id'], a['size'], a['digest']) for n, a in expected.items()}):
            raise ValueError('Source release changed after inventory: '+tag)
        current = api.call('GET', TARGET, '/releases/tags/'+tag, missing=True)
        if current is None:
            current = api.call('POST', TARGET, '/releases', {
                'tag_name': tag, 'target_commitish': release['target_commitish'],
                'name': release['name'], 'body': release['body'], 'draft': True,
                'prerelease': release['prerelease'], 'make_latest': 'false'})
        present = checked_assets(current)
        if set(present) - set(expected):
            raise ValueError('Target has unexpected assets; preserving them: '+tag)
        for name, asset in expected.items():
            if name in present:
                if (present[name]['size'], present[name]['digest']) != (asset['size'], asset['digest']):
                    raise ValueError('Existing target asset differs; refusing overwrite: '+tag+'/'+name)
                continue
            path = directory/name
            with path.open('wb') as output:
                execute(['gh', 'api', '-H', 'Accept: application/octet-stream',
                         'repos/'+SOURCE+'/releases/assets/'+str(asset['id'])], output)
            if path.stat().st_size != asset['size'] or digest(path) != asset['digest']:
                raise ValueError('Downloaded asset checksum differs: '+tag+'/'+name)
            execute(['gh', 'release', 'upload', tag, str(path), '--repo', TARGET])
            path.unlink()
        verified = checked_assets(api.call('GET', TARGET, '/releases/tags/'+tag))
        if ({n: (a['size'], a['digest']) for n, a in verified.items()} !=
                {n: (a['size'], a['digest']) for n, a in expected.items()}):
            raise ValueError('Uploaded release verification failed: '+tag)
        api.call('PATCH', TARGET, '/releases/'+str(current['id']), {
            'draft': False, 'prerelease': release['prerelease'], 'make_latest': 'false'})
        count += len(expected)
        print('Verified release '+tag+' ('+str(len(expected))+' assets)', flush=True)
    latest = api.call('GET', TARGET, '/releases/tags/'+inventory['latest'])
    api.call('PATCH', TARGET, '/releases/'+str(latest['id']), {'make_latest': 'true'})
    return {'schema': 1, 'sourceRepositoryId': SOURCE_ID, 'targetRepositoryId': TARGET_ID,
            'releases': len(inventory['releases']), 'assets': count, 'latest': inventory['latest']}


if __name__ == '__main__':
    inventory_path = Path('.github/migration/releases.json')
    inventory = json.loads(inventory_path.read_text())
    with tempfile.TemporaryDirectory() as directory:
        report = mirror(Api(), inventory, Path(directory))
    report['inventorySha256'] = digest(inventory_path)
    Path(os.environ['RUNNER_TEMP'], 'release-migration.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report))
