#!/usr/bin/env python3
"""Reconcile verified main builds, runtime manifests and the public update channel.

No router access, dependency builds, force pushes, cross-repository writes or PATs.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import shlex
import sys
import urllib.error
import urllib.request
import urllib.parse

REPOSITORY = 'KOPOHA-MACOHA/fptn-keenetic'
PLATFORMS = ('mipsel-mips32r2-softfloat', 'aarch64', 'armv7-softfloat')
ARCHIVES = tuple(f'fptn-keenetic-{p}.tar.gz' for p in PLATFORMS)
WORKFLOWS = {'mips': 'build.yml', 'arm': 'build-platforms.yml',
             'runtime': 'check-runtime.yml', 'publication': 'release-publication.yml'}
PIN_FILES = ('client-release.json', 'platform-releases.json', 'runtime-assets.json', 'web/SHA256SUMS')
CLIENT_FIELDS = {'schema', 'platform', 'buildRevision', 'sha256', 'clientVersion',
                 'upstreamRevision', 'upstreamVersion', 'sourceLockSha256'}


def sha(value):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{40}', value):
        raise ValueError('A full commit SHA is required')
    return value


def digest(value):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value):
        raise ValueError('A SHA-256 digest is required')
    return value


def version(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{1,4}\.\d{1,4}\.\d{1,4}', value):
        raise ValueError('A numeric X.Y.Z version is required')
    return tuple(map(int, value.split('.')))


def encoded(value):
    return (json.dumps(value, indent=2) + '\n').encode()


def checksum(data):
    return hashlib.sha256(data).hexdigest()


class NoApiRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, url):
        raise RuntimeError('Unexpected redirect from the GitHub API')



class GitHub:
    def __init__(self, token):
        self.token = token
        self.opener = urllib.request.build_opener(NoApiRedirect())

    def request(self, method, path, body=None, missing=False):
        if not path.startswith('/repos/' + REPOSITORY + '/'):
            raise ValueError('Access outside the authorized repository is forbidden')
        prefix = '/repos/' + REPOSITORY
        writes = {('POST', prefix + '/git/trees'), ('POST', prefix + '/git/commits'),
                  ('PATCH', prefix + '/git/refs/heads/main')}
        if method != 'GET' and (method, path) not in writes:
            raise ValueError('Only publication commits and a normal main update are allowed')
        if method == 'PATCH' and (not isinstance(body, dict) or body.get('force') is not False):
            raise ValueError('Forced ref updates are forbidden')
        request = urllib.request.Request('https://api.github.com' + path, method=method,
            data=None if body is None else json.dumps(body).encode(),
            headers={'Authorization': 'Bearer ' + self.token,
                     'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28',
                     'User-Agent': 'fptn-keenetic-publication'})
        try:
            with self.opener.open(request, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            if missing and error.code == 404:
                return None
            raise RuntimeError(f'GitHub {method} {path.split("?")[0]}: HTTP {error.code}') from None
        except urllib.error.URLError:
            raise RuntimeError(f'GitHub {method} {path.split("?")[0]}: API network request failed') from None

    def get(self, path, missing=False):
        return self.request('GET', path, missing=missing)

    def release(self, tag):
        return self.get(f'/repos/{REPOSITORY}/releases/tags/{tag}', missing=True)

    def asset(self, release, name):
        matches = [a for a in release.get('assets', []) if a.get('name') == name]
        if len(matches) != 1:
            raise ValueError('Missing or duplicate release asset: ' + name)
        asset = matches[0]
        if not isinstance(asset.get('digest'), str) or not asset['digest'].startswith('sha256:'):
            raise ValueError('Missing release asset SHA-256: ' + name)
        digest(asset['digest'][7:])
        if type(asset.get('size')) is not int or asset['size'] < 1:
            raise ValueError('Invalid asset size: ' + name)
        return asset

    def download(self, release, name):
        asset = self.asset(release, name)
        if asset['size'] > 65536:
            raise ValueError('Publication metadata is too large')
        if type(asset.get('id')) is not int or asset['id'] < 1:
            raise ValueError('Invalid release asset ID')
        # Bind the immutable asset ID, not a possibly cached public download URL.
        # gh handles authenticated API requests and strips auth on CDN redirects.
        command = ['gh', 'api', '--hostname', 'github.com', '--method', 'GET', '-H', 'Accept: application/octet-stream',
                   f'repos/{REPOSITORY}/releases/assets/{asset["id"]}']
        try:
            response = subprocess.run(command, capture_output=True, timeout=45,
                                      env={**os.environ, 'GH_TOKEN': self.token})
        except subprocess.TimeoutExpired:
            raise RuntimeError('GitHub metadata download timed out: ' + name) from None
        if response.returncode:
            status = re.search(rb'HTTP (\d{3})', response.stderr)
            suffix = 'HTTP ' + status[1].decode() if status else 'gh exit ' + str(response.returncode)
            raise RuntimeError('GitHub metadata download failed: ' + name + ' (' + suffix + ')')
        data = response.stdout
        if len(data) != asset['size'] or checksum(data) != asset['digest'][7:]:
            raise ValueError('Release metadata checksum mismatch: ' + name)
        return data


class Repo:
    def __init__(self, root):
        self.root = Path(root)
        self.trees = {}

    def git(self, *args, binary=False):
        result = subprocess.run(['git', '-C', str(self.root), *args], capture_output=True, check=True)
        return result.stdout if binary else result.stdout.decode().strip()

    def head(self):
        return sha(self.git('rev-parse', 'HEAD'))

    def tree(self, ref):
        ref = sha(ref)
        if ref not in self.trees:
            entries = {}
            for row in self.git('ls-tree', '-rz', '--full-tree', ref, binary=True).split(b'\0'):
                if not row:
                    continue
                meta, name = row.split(b'\t', 1)
                mode, kind, obj = meta.decode().split()
                if kind == 'blob':
                    entries[name.decode()] = (mode, obj)
            self.trees[ref] = entries
        return self.trees[ref]

    def read(self, ref, name):
        if name not in self.tree(ref):
            raise ValueError('Missing release input: ' + name)
        return self.git('show', f'{sha(ref)}:{name}', binary=True)

    def ancestor(self, older, newer):
        return subprocess.run(['git', '-C', str(self.root), 'merge-base', '--is-ancestor',
                               sha(older), sha(newer)], capture_output=True).returncode == 0

    def client_key(self, ref):
        exact = {'Dockerfile', 'sources.lock.json', 'sources.upstream.json', 'ports/targets.json',
                 'scripts/fix-static-libgcc.py', 'scripts/fix-platform-libgcc.py',
                 'scripts/check-cross-includes.py', 'scripts/verify-elf.py',
                 'scripts/verify-platform-elf.py', 'scripts/check-probe-mode.sh',
                 'scripts/render-platform.py', 'scripts/platform-dependency-key.py'}
        # Discover new build inputs added by future COPY instructions too. These
        # four existing files are export-only material, not compiled into the ELF.
        export_only = {'scripts/run-connect-test.sh', 'docs/tunnel-test.md',
                       'scripts/fptn-native-service.sh', 'docs/native-interface.md'}
        copied = set()
        for line in self.read(ref, 'Dockerfile').decode().splitlines():
            if not line.startswith('COPY ') or line.startswith('COPY --from='):
                continue
            fields = shlex.split(line)
            if len(fields) < 3 or any(x.startswith(('--', '[', '$')) or '*' in x for x in fields[1:-1]):
                raise ValueError('Review changed Docker COPY input syntax')
            for name in fields[1:-1]:
                if not re.fullmatch(r'[A-Za-z0-9_./-]+', name) or name in ('.', '..') or PurePosixPath(name).is_absolute() or '..' in PurePosixPath(name).parts:
                    raise ValueError('Review changed Docker COPY source paths')
                if name not in export_only:
                    copied.add(name.rstrip('/'))
        selected = {p: item[1] for p, item in self.tree(ref).items()
                    if p in exact or p.startswith(('src/', 'profiles/')) or
                    (p.startswith('scripts/prepare-') and p.endswith('.py')) or
                    any(p == name or p.startswith(name + '/') for name in copied)}
        return checksum(json.dumps(selected, sort_keys=True).encode())

    def manifest(self, ref):
        rows = {}
        for line in self.read(ref, 'web/SHA256SUMS').decode().splitlines():
            match = re.fullmatch(r'([0-9a-f]{64})  ([A-Za-z0-9_./-]+)', line)
            if not match:
                raise ValueError('Invalid runtime manifest line')
            expected, name = match.groups()
            if name in rows or PurePosixPath(name).is_absolute() or '..' in PurePosixPath(name).parts:
                raise ValueError('Unsafe or duplicate runtime path')
            if checksum(self.read(ref, name)) != expected:
                raise ValueError('Stale runtime payload checksum: ' + name)
            rows[name] = expected
        if not {'client-release.json', 'platform-releases.json', 'runtime-assets.json',
                'web/index.html', 'web/app.js', 'web/api.cgi'}.issubset(rows):
            raise ValueError('Incomplete runtime manifest')
        return rows

    def runtime_key(self, ref):
        rows = self.manifest(ref)
        for name in ('scripts/install-web-ui.sh', 'install.sh', 'scripts/package-runtime.py'):
            rows[name] = checksum(self.read(ref, name))
        return checksum(json.dumps(rows, sort_keys=True).encode())

    def panel_version(self, ref):
        html = re.findall(r'data-ui-version="([0-9.]+)"', self.read(ref, 'web/index.html').decode())
        js = re.findall(r"const UI_VERSION = '([0-9.]+)';", self.read(ref, 'web/app.js').decode())
        if len(html) != 1 or html != js:
            raise ValueError('Panel entry and script versions differ')
        version(html[0])
        return html[0]

    def expected(self, ref):
        lock = self.read(ref, 'sources.lock.json')
        source = json.loads(self.read(ref, 'sources.upstream.json'))
        versions = re.findall(r'-DFPTN_VERSION=([0-9.]+)', self.read(ref, 'Dockerfile').decode())
        if len(versions) != 1 or source['revision'] != json.loads(lock)['fptn']:
            raise ValueError('Ambiguous client or original-source version')
        original = source['release'].removeprefix('v')
        version(versions[0]); version(original); sha(source['revision'])
        return {'clientVersion': versions[0], 'upstreamVersion': original,
                'upstreamRevision': source['revision'], 'sourceLockSha256': checksum(lock)}


def main_head(api):
    return sha(api.get(f'/repos/{REPOSITORY}/git/ref/heads/main')['object']['sha'])


def records(repo, ref):
    mips = json.loads(repo.read(ref, 'client-release.json'))
    ports = json.loads(repo.read(ref, 'platform-releases.json'))
    if ports.get('schema') != 1 or set(ports.get('platforms', {})) != set(PLATFORMS[1:]):
        raise ValueError('Registered architecture catalog changed')
    result = {PLATFORMS[0]: mips, **{p: ports['platforms'][p]['client'] for p in PLATFORMS[1:]}}
    for p, row in result.items():
        if set(row) != CLIENT_FIELDS or row['schema'] != 1 or row['platform'] != p:
            raise ValueError('Invalid client identity for ' + p)
        sha(row['buildRevision']); digest(row['sha256']); digest(row['sourceLockSha256'])
        sha(row['upstreamRevision']); version(row['clientVersion']); version(row['upstreamVersion'])
    return result


def compatible(repo, ref, rows):
    expected = repo.expected(ref)
    key = repo.client_key(ref)
    return all(repo.ancestor(row['buildRevision'], ref) and
               repo.client_key(row['buildRevision']) == key and
               all(row.get(k) == v for k, v in expected.items()) for row in rows.values())


def check_channel_versions(repo, ref, rows):
    catalog = json.loads(repo.read(ref, 'web/update.json'))
    if version(repo.panel_version(ref)) < version(catalog['version']):
        raise ValueError('Automatic panel downgrade refused')
    for p, row in rows.items():
        previous = catalog.get('platforms', {}).get(p, catalog)
        if version(row['clientVersion']) < version(previous['clientVersion']):
            raise ValueError('Automatic client downgrade refused: ' + p)
        if previous.get('upstreamVersion') and version(row['upstreamVersion']) < version(previous['upstreamVersion']):
            raise ValueError('Automatic original-source downgrade refused: ' + p)
    return catalog


def latest_success(api, workflow, ref):
    data = api.get(f'/repos/{REPOSITORY}/actions/workflows/{workflow}/runs?head_sha={sha(ref)}&branch=main&per_page=100')
    runs = [r for r in data.get('workflow_runs', []) if r.get('head_sha') == ref and
            r.get('head_branch') == 'main' and r.get('event') in ('push', 'workflow_dispatch') and
            r.get('head_repository', {}).get('full_name') == REPOSITORY]
    if not runs:
        return None
    run = max(runs, key=lambda r: (r['id'], r.get('run_attempt', 1)))
    return run if run.get('status') == 'completed' and run.get('conclusion') == 'success' else None


def verified_release(api, tag, ref):
    release = api.release(tag)
    if release is None:
        return None
    if release.get('draft') or release.get('tag_name') != tag or release.get('target_commitish') != sha(ref):
        raise ValueError('Release does not identify the expected commit: ' + tag)
    obj = api.get(f'/repos/{REPOSITORY}/git/ref/tags/{tag}')['object']
    for _ in range(3):
        if obj.get('type') == 'commit':
            if obj.get('sha') != ref:
                raise ValueError('Release tag was moved: ' + tag)
            return release
        if obj.get('type') != 'tag':
            break
        obj = api.get(f'/repos/{REPOSITORY}/git/tags/{sha(obj["sha"])}')['object']
    raise ValueError('Invalid release tag: ' + tag)


def new_client_records(api, repo, head, source):
    if not repo.ancestor(source, head) or repo.client_key(source) != repo.client_key(head):
        return None
    if not latest_success(api, WORKFLOWS['mips'], source) or not latest_success(api, WORKFLOWS['arm'], source):
        return None
    rows, assets = {}, {}
    for p in PLATFORMS:
        tag = ('native-' if p == PLATFORMS[0] else 'port-' + p + '-') + source
        release = verified_release(api, tag, source)
        if release is None:
            return None
        row = json.loads(api.download(release, 'client-release.json'))
        if set(row) != CLIENT_FIELDS or row['schema'] != 1 or row['platform'] != p or row['buildRevision'] != source:
            raise ValueError('Wrong client metadata: ' + p)
        if any(row.get(k) != v for k, v in repo.expected(head).items()):
            raise ValueError('Client original sources or version differ from main: ' + p)
        if row['sha256'] != api.asset(release, 'fptn-client-cli')['digest'][7:]:
            raise ValueError('Client digest and release asset differ: ' + p)
        if row['sourceLockSha256'] != api.asset(release, 'sources.lock.json')['digest'][7:]:
            raise ValueError('Client source lock and release asset differ: ' + p)
        license_hash = api.asset(release, 'LICENSE-FPTN')['digest'][7:]
        if p == PLATFORMS[0]:
            a = json.loads(repo.read(head, 'runtime-assets.json'))
            a['clientLicenseSha256'] = license_hash
        else:
            a = json.loads(api.download(release, 'runtime-assets.json'))
            if a.get('schema') != 1 or a.get('platform') != p or a.get('decoderRelease') != tag:
                raise ValueError('Wrong decoder metadata: ' + p)
            for key, name in [('decoderSha256', 'brotli'), ('decoderLicenseSha256', 'LICENSE-BROTLI'),
                              ('decoderSourceSha256', 'BROTLI-SOURCE.txt'), ('clientLicenseSha256', 'LICENSE-FPTN')]:
                if a.get(key) != api.asset(release, name)['digest'][7:]:
                    raise ValueError('Wrong decoder/license digest: ' + p)
            a['clientRelease'] = tag
        if a.get('schema') != 1 or a.get('platform') != p:
            raise ValueError('Wrong pinned decoder platform')
        for key in ['decoderSha256', 'decoderLicenseSha256', 'decoderSourceSha256', 'clientLicenseSha256']:
            digest(a.get(key))
        if a['clientLicenseSha256'] != license_hash:
            raise ValueError('Wrong client license')
        rows[p], assets[p] = row, a
    if len({a['clientLicenseSha256'] for a in assets.values()}) != 1:
        raise ValueError('Architectures have different original licenses')
    return rows, assets


def pin_contents(repo, head, rows, assets):
    manifest = repo.manifest(head)
    blobs = {'client-release.json': encoded(rows[PLATFORMS[0]]),
             'runtime-assets.json': encoded(assets[PLATFORMS[0]]),
             'platform-releases.json': encoded({'schema': 1, 'platforms':
                 {p: {'client': rows[p], 'assets': assets[p]} for p in PLATFORMS[1:]}})}
    blobs['web/SHA256SUMS'] = ''.join((checksum(blobs[name]) if name in blobs else expected) +
                                    '  ' + name + '\n' for name, expected in manifest.items()).encode()
    return {p: b for p, b in blobs.items() if b != repo.read(head, p)}


def commit_files(api, repo, expected, blobs, message):
    if set(blobs) - set(PIN_FILES) - {'web/update.json'} or not blobs:
        raise ValueError('Unexpected publication file set')
    if main_head(api) != expected:
        return None
    base = api.get(f'/repos/{REPOSITORY}/git/commits/{expected}')['tree']['sha']
    tree = api.request('POST', f'/repos/{REPOSITORY}/git/trees', {'base_tree': base, 'tree':
        [{'path': p, 'mode': repo.tree(expected)[p][0], 'type': 'blob', 'content': data.decode()}
         for p, data in blobs.items()]})['sha']
    commit = sha(api.request('POST', f'/repos/{REPOSITORY}/git/commits',
        {'tree': tree, 'parents': [expected], 'message': message})['sha'])
    if main_head(api) != expected:
        return None
    # A concurrent normal commit makes this non-fast-forward and GitHub rejects it.
    # Never set force, replace history, or retry against a different parent.
    try:
        api.request('PATCH', f'/repos/{REPOSITORY}/git/refs/heads/main', {'sha': commit, 'force': False})
    except RuntimeError:
        if main_head(api) != expected:
            return None
        raise
    return commit


def jobs_for(api, run_id):
    jobs = []
    for page in range(1, 6):
        batch = api.get(f'/repos/{REPOSITORY}/actions/runs/{run_id}/jobs?filter=all&per_page=100&page={page}').get('jobs', [])
        jobs.extend(batch)
        if len(batch) < 100:
            break
    latest = {}
    for job in jobs:
        name = job['name'].rsplit(' / ', 1)[-1]
        if name not in latest or job['id'] > latest[name]['id']:
            latest[name] = job
    return latest


def validation_proof(api, repo, runtime, run_id, package_attempt=None, current_run=None):
    if type(run_id) is not int or run_id < 1:
        raise ValueError('Invalid validation run ID')
    run = api.get(f'/repos/{REPOSITORY}/actions/runs/{run_id}')
    path = run.get('path', '').split('@')[0]
    if run.get('head_branch') != 'main' or run.get('head_repository', {}).get('full_name') != REPOSITORY:
        return False
    if path not in ('.github/workflows/' + WORKFLOWS['runtime'], '.github/workflows/' + WORKFLOWS['publication']):
        return False
    if run.get('event') not in ('push', 'workflow_dispatch', 'workflow_run'):
        return False
    if not repo.ancestor(sha(run.get('head_sha')), runtime):
        return False
    if path.endswith('/' + WORKFLOWS['runtime']) and run.get('head_sha') != runtime:
        return False
    live_self = run_id == current_run and path.endswith('/' + WORKFLOWS['publication']) and run.get('status') == 'in_progress'
    if not live_self and (run.get('status') != 'completed' or run.get('conclusion') != 'success'):
        return False
    jobs = jobs_for(api, run_id)
    names = [f'runtime-test-{runtime}', f'runtime-package-{runtime}']
    if path.endswith('/' + WORKFLOWS['runtime']) and run.get('head_sha') == runtime and not all(n in jobs for n in names):
        names = ['test', 'package']  # Already published pre-automation releases.
    if not all(jobs.get(n, {}).get('conclusion') == 'success' for n in names):
        return False
    package = jobs[names[1]]
    if package_attempt is not None and (type(package_attempt) is not int or package_attempt < 1 or
            package_attempt > run.get('run_attempt', 1) or package.get('run_attempt', 1) != package_attempt):
        return False
    return True


def runtime_release(api, repo, ref, current_run=None):
    release = verified_release(api, 'runtime-' + ref, ref)
    if release is None:
        return None
    archive_assets = {name: api.asset(release, name) for name in ARCHIVES}
    sums = api.download(release, 'SHA256SUMS').decode()
    expected = ''.join(archive_assets[name]['digest'][7:] + '  ' + name + '\n' for name in ARCHIVES)
    if sums != expected:
        raise ValueError('Runtime checksums and architecture assets differ')
    evidence_asset = [a for a in release['assets'] if a['name'] == 'runtime-evidence.json']
    if evidence_asset:
        evidence = json.loads(api.download(release, 'runtime-evidence.json'))
        if evidence.get('schema') != 1 or evidence.get('repository') != REPOSITORY or evidence.get('runtimeRevision') != ref:
            raise ValueError('Wrong runtime evidence')
        if evidence.get('archives') != {n: a['digest'][7:] for n, a in archive_assets.items()} or evidence.get('manifestSha256') != checksum(sums.encode()):
            raise ValueError('Runtime evidence does not describe these archives')
        if type(evidence.get('runAttempt')) is not int or evidence['runAttempt'] < 1:
            raise ValueError('Invalid evidence attempt')
        if not validation_proof(api, repo, ref, evidence.get('runId'), evidence['runAttempt'], current_run):
            return None
    else:
        run = latest_success(api, WORKFLOWS['runtime'], ref)
        if not run or not validation_proof(api, repo, ref, run['id']):
            return None
    return release


def channel_content(repo, ref, rows):
    catalog = check_channel_versions(repo, ref, rows)
    def identity(row):
        return {'clientRevision': row['buildRevision'], 'clientVersion': row['clientVersion'],
                'clientSha256': row['sha256'], 'upstreamVersion': row['upstreamVersion'],
                'upstreamRevision': row['upstreamRevision']}
    catalog.update(identity(rows[PLATFORMS[0]]))
    catalog.update(schema=1, version=repo.panel_version(ref), revision=ref,
                   installerSha256=checksum(repo.read(ref, 'scripts/install-web-ui.sh')),
                   platforms={p: identity(rows[p]) for p in PLATFORMS})
    return encoded(catalog)


def eligible_event(event):
    run = event.get('workflow_run', {})
    return (run.get('conclusion') == 'success' and run.get('head_branch') == 'main' and
            run.get('event') in ('push', 'workflow_dispatch') and
            run.get('head_repository', {}).get('full_name') == REPOSITORY)


def prepare(api, repo, manual=False, dry_run=False):
    head = main_head(api)
    if repo.head() != head:
        return {'status': 'stale', 'reason': 'main moved before preparation'}
    rows = records(repo, head)
    if not compatible(repo, head, rows):
        builds = api.get(f'/repos/{REPOSITORY}/actions/workflows/{WORKFLOWS["mips"]}/runs?branch=main&status=success&per_page=20')
        ready = None
        for run in builds.get('workflow_runs', []):
            source = sha(run['head_sha'])
            if run.get('head_branch') == 'main' and run.get('event') in ('push', 'workflow_dispatch'):
                ready = new_client_records(api, repo, head, source)
                if ready:
                    break
        if ready is None:
            return {'status': 'waiting', 'reason': 'all matching main architecture builds are not ready'}
        new_rows, assets = ready
        check_channel_versions(repo, head, new_rows)
        blobs = pin_contents(repo, head, new_rows, assets)
        if dry_run:
            return {'status': 'would-pin', 'files': sorted(blobs), 'source': new_rows[PLATFORMS[0]]['buildRevision']}
        commit = commit_files(api, repo, head, blobs,
            'release: pin verified architecture artifacts [runtime-only] [skip ci]')
        return {'status': 'validate', 'runtime': commit} if commit else {'status': 'stale'}
    catalog = check_channel_versions(repo, head, rows)
    published = sha(catalog['revision'])
    if not repo.ancestor(published, head):
        raise ValueError('Published runtime is not an ancestor of main')
    if repo.runtime_key(head) == repo.runtime_key(published):
        if not runtime_release(api, repo, published):
            return {'status': 'waiting', 'reason': 'published runtime verification is not available'}
        return {'status': 'up-to-date', 'runtime': published, 'publishedVerified': True}
    if runtime_release(api, repo, head):
        return {'status': 'promote', 'runtime': head}
    if manual:
        return {'status': 'would-validate' if dry_run else 'validate', 'runtime': head}
    return {'status': 'waiting', 'runtime': head, 'reason': 'matching runtime validation/package is not ready; no automatic repeat'}


def promote(api, repo, expected, current_run=None, dry_run=False):
    expected = sha(expected)
    if main_head(api) != expected or repo.head() != expected:
        return {'status': 'stale', 'reason': 'main advanced; channel preserved'}
    rows = records(repo, expected)
    if not compatible(repo, expected, rows):
        raise ValueError('Pinned binaries do not describe the current client inputs')
    if not runtime_release(api, repo, expected, current_run):
        return {'status': 'waiting', 'reason': 'runtime checks and all architecture packages are required'}
    body = channel_content(repo, expected, rows)
    if dry_run:
        return {'status': 'would-promote', 'runtime': expected, 'channel': json.loads(body)}
    commit = commit_files(api, repo, expected, {'web/update.json': body},
                          'release: publish verified update channel [skip ci]')
    return {'status': 'published', 'runtime': expected, 'commit': commit} if commit else {'status': 'stale'}


def evidence(directory, ref, run_id, attempt):
    directory = Path(directory)
    hashes = {name: checksum((directory/name).read_bytes()) for name in ARCHIVES}
    manifest = (directory/'SHA256SUMS').read_bytes()
    if manifest.decode() != ''.join(hashes[n] + '  ' + n + '\n' for n in ARCHIVES):
        raise ValueError('Packaged archive manifest is inconsistent')
    result = {'schema': 1, 'repository': REPOSITORY, 'runtimeRevision': sha(ref),
              'runId': int(run_id), 'runAttempt': int(attempt), 'archives': hashes,
              'manifestSha256': checksum(manifest)}
    if result['runId'] < 1 or result['runAttempt'] < 1:
        raise ValueError('Invalid workflow identity')
    (directory/'runtime-evidence.json').write_bytes(encoded(result))
    return result


def emit(result):
    print(json.dumps(result, sort_keys=True))
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
            output.write('status=' + result['status'] + '\n')
            output.write('runtime=' + result.get('runtime', '') + '\n')
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as summary:
            summary.write('Publication: **' + result['status'] + '**\n\n')
            summary.write('```json\n' + json.dumps(result, indent=2) + '\n```\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['prepare', 'promote', 'evidence'])
    parser.add_argument('--runtime')
    parser.add_argument('--directory')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    if os.environ.get('GITHUB_REPOSITORY') != REPOSITORY:
        raise ValueError('Publication is limited to ' + REPOSITORY)
    if args.command == 'evidence':
        return print(json.dumps(evidence(args.directory, args.runtime,
            os.environ['GITHUB_RUN_ID'], os.environ.get('GITHUB_RUN_ATTEMPT', '1'))))
    if os.environ.get('GITHUB_REF') != 'refs/heads/main':
        raise ValueError('Publication is limited to main')
    event_name = os.environ.get('GITHUB_EVENT_NAME')
    if event_name == 'workflow_run':
        event = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text())
        if not eligible_event(event):
            return emit({'status': 'ignored', 'reason': 'not a successful trusted main build'})
    elif event_name != 'workflow_dispatch':
        raise ValueError('Unexpected publication trigger')
    token = os.environ.get('GH_TOKEN')
    if not token:
        raise ValueError('The job requires its standard GH_TOKEN')
    api, repo = GitHub(token), Repo('.')
    if args.command == 'prepare' and os.environ.get('GITHUB_SHA') != repo.head():
        return emit({'status': 'stale', 'reason': 'main advanced beyond the workflow revision'})
    if args.command == 'prepare':
        return emit(prepare(api, repo, manual=event_name == 'workflow_dispatch' or
                            int(os.environ.get('GITHUB_RUN_ATTEMPT', '1')) > 1, dry_run=args.dry_run))
    return emit(promote(api, repo, args.runtime, current_run=int(os.environ['GITHUB_RUN_ID']), dry_run=args.dry_run))


if __name__ == '__main__':
    try:
        main()
    except urllib.error.URLError:
        print('Publication network request failed; channel preserved.', file=sys.stderr)
        sys.exit(1)
    except (ValueError, RuntimeError, KeyError, subprocess.CalledProcessError) as error:
        print('Publication refused: ' + str(error), file=sys.stderr)
        sys.exit(1)
