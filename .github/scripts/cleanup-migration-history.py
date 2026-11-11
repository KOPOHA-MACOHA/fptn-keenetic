#!/usr/bin/env python3
"""Remove only the history staging refs after successful, verified release copying."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile

spec = importlib.util.spec_from_file_location('mirror', Path(__file__).with_name('mirror-releases.py'))
mirror = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mirror)
api = mirror.Api()
target = api.call('GET', mirror.TARGET)
if target['id'] != mirror.TARGET_ID or target['full_name'] != mirror.TARGET:
    raise SystemExit('Target repository identity changed')
proof = api.call('GET', mirror.TARGET, '/actions/runs/42997776470')
if (proof.get('status') != 'completed' or proof.get('conclusion') != 'success' or
        proof.get('head_sha') != 'a11d0b49295bda8696b6b22a66eab14e8beacfb7' or
        proof.get('path') != '.github/workflows/mirror-releases.yml' or
        proof.get('head_repository', {}).get('id') != mirror.TARGET_ID):
    raise SystemExit('Release copy has not passed verification')
inventory = json.loads(Path('.github/migration/releases.json').read_text())
for release in inventory['releases']:
    commit = subprocess.check_output(['git', 'rev-parse', 'refs/tags/'+release['tag_name']+'^{commit}'], text=True).strip()
    if commit != release['target_commitish']:
        raise SystemExit('Original tag target changed; keep staging refs')
public_checks = 0
with tempfile.TemporaryDirectory() as directory:
    for release in inventory['releases']:
        if release['tag_name'].startswith('entware-feed-'):
            selected = {'Packages', 'Packages.gz', 'package.json'}
        elif release['tag_name'] == inventory['latest']:
            selected = {'SHA256SUMS'}
        else:
            continue
        for asset in release['assets']:
            if asset['name'] not in selected:
                continue
            url = ('https://github.com/'+mirror.TARGET+'/releases/download/'+
                   mirror.urllib.parse.quote(release['tag_name'], safe='')+'/'+
                   mirror.urllib.parse.quote(asset['name'], safe=''))
            path = Path(directory)/asset['name']
            subprocess.run(['curl', '--proto', '=https', '--proto-redir', '=https', '-fsSL',
                            '--retry', '3', '--max-time', '60', url, '-o', str(path)], check=True)
            if path.stat().st_size != asset['size'] or mirror.digest(path) != asset['digest']:
                raise SystemExit('Public installation metadata differs from the frozen source')
            public_checks += 1
if public_checks != 10:
    raise SystemExit('Missing public channel checks')
rows = json.loads(Path('.github/migration/history-branches.json').read_text())
heads = subprocess.check_output(['git', 'ls-remote', '--heads', 'origin'], text=True)
remote = {}
for line in heads.splitlines():
    sha, ref = line.split('\t', 1)
    remote[ref] = sha
remove = []
for row in rows:
    if not row['branch'].startswith('migration/history/'):
        raise SystemExit('Unexpected staging branch name')
    ref = 'refs/heads/'+row['branch']
    if ref not in remote:
        continue
    if remote[ref] != row['sha']:
        raise SystemExit('Staging ref was changed; preserving all remaining refs')
    remove.append(':'+ref)
if remove:
    subprocess.run(['git', 'push', 'origin', *remove], check=True)
report = {'schema': 1, 'copyRunId': 42997776470, 'deletedTemporaryBranches': len(remove),
          'preservedOriginalTags': len(inventory['releases']), 'repositoryId': mirror.TARGET_ID,
          'publicDownloadChecks': public_checks}
Path(os.environ['RUNNER_TEMP'], 'history-cleanup.json').write_text(json.dumps(report, indent=2)+'\n')
print(json.dumps(report))
