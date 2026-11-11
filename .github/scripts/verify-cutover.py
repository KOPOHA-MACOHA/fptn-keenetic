#!/usr/bin/env python3
"""Read-only verification of canonical install URLs and archived build evidence."""
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import urllib.parse

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('cutover_publication', ROOT/'scripts/release-publication.py')
pub = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pub)
CANONICAL = 'KOPOHA-MACOHA/fptn-keenetic'
ARCHIVE = 'KOPOHA-MACOHA/fptn-keenetic-test'


def repository(name):
    result = subprocess.run(['gh', 'api', 'repos/'+name], capture_output=True, check=True)
    return json.loads(result.stdout)


if os.environ.get('GITHUB_REPOSITORY') != CANONICAL:
    raise SystemExit('Canonical repository verification only')
canonical, archive = repository(CANONICAL), repository(ARCHIVE)
if (canonical['id'] != 1453702975 or not canonical['fork'] or
        canonical['parent']['full_name'] != 'fptn-project/fptn' or
        canonical['default_branch'] != 'main' or archive['id'] != 1432556444):
    raise SystemExit('Repository identities do not match the migration record')
api = pub.GitHub(os.environ['GH_TOKEN'])
repo = pub.Repo(ROOT)
entry = api.get('/repos/'+CANONICAL+'/contents/web/update.json?ref=main')
channel = json.loads(base64.b64decode(entry['content']))
runtime = pub.sha(channel['revision'])
release = pub.runtime_release(api, repo, runtime)
if not release:
    raise SystemExit('Published runtime has no valid completed build evidence')
all_releases = api.get('/repos/'+CANONICAL+'/releases?per_page=100')
by_tag = {row['tag_name']: row for row in all_releases}
inventory = json.loads((ROOT/'.github/migration/releases.json').read_text())
for expected in inventory['releases']:
    live = by_tag.get(expected['tag_name'])
    if not live or live['draft']:
        raise SystemExit('Historical release missing after rename')
    assets = {asset['name']: asset for asset in live['assets']}
    for old in expected['assets']:
        # Feeds intentionally replace these three index files on later updates.
        if expected['tag_name'].startswith('entware-feed-') and old['name'] in {'Packages', 'Packages.gz', 'package.json'}:
            continue
        actual = assets.get(old['name'], {})
        if actual.get('size') != old['size'] or actual.get('digest') != old['digest']:
            raise SystemExit('Historical installation asset differs after rename')
public_checks = 0
with tempfile.TemporaryDirectory() as directory:
    for expected in inventory['releases']:
        if expected['tag_name'].startswith('entware-feed-'):
            selected = {'Packages', 'Packages.gz', 'package.json'}
        elif expected['tag_name'] == inventory['latest']:
            selected = {'SHA256SUMS'}
        else:
            continue
        for asset in by_tag[expected['tag_name']]['assets']:
            if asset['name'] not in selected:
                continue
            path = Path(directory)/asset['name']
            url = ('https://github.com/'+CANONICAL+'/releases/download/'+
                   urllib.parse.quote(expected['tag_name'], safe='')+'/'+
                   urllib.parse.quote(asset['name'], safe=''))
            subprocess.run(['curl', '--proto', '=https', '--proto-redir', '=https', '-fsSL',
                            '--retry', '3', '--max-time', '60', url, '-o', str(path)], check=True)
            if path.stat().st_size != asset['size'] or 'sha256:'+hashlib.sha256(path.read_bytes()).hexdigest() != asset['digest']:
                raise SystemExit('Canonical public installation metadata differs')
            public_checks += 1
    installer = Path(directory)/'install-web-ui.sh'
    url = 'https://raw.githubusercontent.com/'+CANONICAL+'/'+runtime+'/scripts/install-web-ui.sh'
    subprocess.run(['curl', '--proto', '=https', '--proto-redir', '=https', '-fsSL',
                    '--retry', '3', '--max-time', '60', url, '-o', str(installer)], check=True)
    if hashlib.sha256(installer.read_bytes()).hexdigest() != channel['installerSha256']:
        raise SystemExit('Pinned installer URL or checksum differs')
if public_checks != 10:
    raise SystemExit('Missing canonical public download checks')
report = {'schema': 1, 'canonicalRepositoryId': canonical['id'], 'archiveRepositoryId': archive['id'],
          'parent': canonical['parent']['full_name'], 'runtimeRevision': runtime,
          'historicalRuntimeProofValid': True, 'historicalReleases': len(inventory['releases']),
          'publicMetadataChecks': public_checks, 'pinnedInstallerChecksumValid': True}
output = ROOT/'out/cutover'
output.mkdir(parents=True, exist_ok=True)
(output/'verification.json').write_text(json.dumps(report, indent=2)+'\n')
print(json.dumps(report))
