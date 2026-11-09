#!/usr/bin/env python3
"""Publish tested IPKs before the runtime channel advances; fixed repository only."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[1]
def load(name):
    spec=importlib.util.spec_from_file_location(name,ROOT/'scripts'/name)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
pub=load('release-publication.py');ipk=load('package-ipk.py')


def version(value):
    if not isinstance(value,str) or not re.fullmatch(r'\d+\.\d+\.\d+-[1-9]\d*',value):raise ValueError('Invalid package version')
    panel,serial=value.split('-');return (*map(int,panel.split('.')),int(serial))


def verify_evidence(evidence,runtime,release):
    if evidence.get('schema')!=1 or evidence.get('runtimeRevision')!=runtime:raise ValueError('Wrong IPK evidence')
    if set(evidence.get('platforms',{}))!=set(ipk.PLATFORMS):raise ValueError('Incomplete IPK platforms')
    assets={x['name']:x for x in release['assets']}
    for platform,(arch,feed) in ipk.PLATFORMS.items():
        row=evidence['platforms'][platform];version(row['version'])
        expected=f'fptn-keenetic_{row["version"]}_{arch}.ipk'
        if row['filename']!=expected or row['architecture']!=arch or row['feed']!=feed or row['revision']!=runtime:raise ValueError('Wrong IPK identity')
        if set(row.get('indexes',{}))!={'Packages','Packages.gz'}:raise ValueError('Incomplete package index hashes')
        for value in row['indexes'].values():pub.digest(value)
        if assets.get(expected,{}).get('digest')!='sha256:'+pub.digest(row['sha256']):raise ValueError('IPK release digest mismatch')
    return evidence['platforms']


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--runtime',required=True);a=p.parse_args()
    runtime=pub.sha(a.runtime)
    if os.environ.get('GITHUB_REPOSITORY')!=pub.REPOSITORY:raise ValueError('Wrong repository')
    api=pub.GitHub(os.environ['GH_TOKEN']);repo=pub.Repo(ROOT)
    if pub.main_head(api)!=runtime:raise ValueError('Main moved before package feed publication')
    release=pub.runtime_release(api,repo,runtime,current_run=int(os.environ['GITHUB_RUN_ID']))
    if not release:raise ValueError('Runtime validation proof is missing')
    evidence=json.loads(api.download(release,'ipk-evidence.json'))
    rows=verify_evidence(evidence,runtime,release)
    def gh(*args):subprocess.run(['gh',*args,'--repo',pub.REPOSITORY],check=True)
    with tempfile.TemporaryDirectory() as directory:
        stage=Path(directory)
        # Upload every immutable package before advancing any mutable index.
        for platform,row in rows.items():
            tag='entware-feed-'+row['feed'];prior=api.get('/repos/'+pub.REPOSITORY+'/releases/tags/'+tag,missing=True)
            if prior:
                previous=[x for x in prior['assets'] if x['name']=='package.json']
                if previous:
                    old=json.loads(api.download(prior,'package.json'))
                    if version(old['version'])>version(row['version']):raise ValueError('Package feed downgrade forbidden')
            else:
                gh('release','create',tag,'--target',runtime,'--prerelease','--latest=false','--title','Entware feed: '+row['feed'],'--notes','Verified FPTN package feed. Immutable versioned IPKs; index advances only after runtime checks.')
            target=stage/platform;target.mkdir()
            gh('release','download','runtime-'+runtime,'--pattern',row['filename'],'--dir',str(target))
            path=target/row['filename']
            if pub.checksum(path.read_bytes())!=row['sha256']:raise ValueError('Downloaded IPK differs')
            old_assets={x['name']:x for x in (prior or {}).get('assets',[])}
            if row['filename'] in old_assets:
                if old_assets[row['filename']].get('digest')!='sha256:'+row['sha256']:raise ValueError('Immutable package filename collision')
            else:gh('release','upload',tag,str(path))
            # Copy the exact prebuilt index; its hashes are bound to the validated runtime.
            for name in ['Packages','Packages.gz']:
                asset=platform+'-'+name
                data=api.download(release,asset)
                if pub.checksum(data)!=row['indexes'][name]:raise ValueError('Package index differs')
                (target/name).write_bytes(data)
            (target/'package.json').write_text(json.dumps(row,indent=2)+'\n')
        if pub.main_head(api)!=runtime:raise ValueError('Main moved; package indexes unchanged')
        for platform,row in rows.items():
            target=stage/platform
            gh('release','upload','entware-feed-'+row['feed'],str(target/'Packages'),str(target/'Packages.gz'),str(target/'package.json'),'--clobber')
    print(json.dumps({'status':'published','runtime':runtime,'platforms':list(rows)}))

if __name__=='__main__':main()
