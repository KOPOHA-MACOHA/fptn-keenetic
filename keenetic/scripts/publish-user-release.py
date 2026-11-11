#!/usr/bin/env python3
"""Present the validated channel as a user-facing Latest release, without rebuilding."""
import argparse
import base64
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[1]
def load(name):
    spec=importlib.util.spec_from_file_location(name,ROOT/'scripts'/name)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
pub=load('release-publication.py');feeds=load('publish-opkg-feed.py')


def select_assets(release,evidence,runtime):
    rows=feeds.verify_evidence(evidence,runtime,release)
    versions={row['version'] for row in rows.values()}
    if len(versions)!=1:raise ValueError('Architecture package versions differ')
    version=next(iter(versions))
    names=list(pub.ARCHIVES)+[rows[p]['filename'] for p in pub.PLATFORMS]
    assets={x['name']:x for x in release['assets']}
    selected={}
    for name in names:
        asset=assets.get(name,{})
        value=asset.get('digest','')
        if not value.startswith('sha256:'):raise ValueError('Missing verified installation archive')
        selected[name]=pub.digest(value[7:])
    return version,selected


def notes(root,catalog,package_version):
    version=catalog['version'];pub.version(version);pub.version(catalog['clientVersion'])
    body=(f'Панель **{version}** · клиент **{catalog["clientVersion"]}** · пакет **{package_version}**.\n\n')
    changelog=root/'docs'/('release-'+version+'.md')
    if changelog.is_file():body+=changelog.read_text().strip()+'\n\n'
    body+='''### Установка

Автоматическая установка через Entware/opkg (настройки существующей службы сохраняются):

```sh
curl -fsSL https://raw.githubusercontent.com/KOPOHA-MACOHA/fptn-keenetic/main/install.sh | sh -s opkg
```

В Assets ниже:

- `.ipk` — пакет Entware для своей архитектуры;
- `.tar.gz` — полный комплект для ручной установки (`sh install.sh install` внутри распакованной папки);
- `SHA256SUMS` — контрольные суммы всех шести файлов.

MIPS: `mipsel-3.4` / `mipsel-mips32r2-softfloat`; ARM64: `aarch64-3.10`;
ARMv7 soft-float: `armv7-3.2`. Выбор платформы и ограничения:
[инструкция](https://github.com/KOPOHA-MACOHA/fptn-keenetic/blob/main/docs/opkg.md).

Проект экспериментальный; установка и использование — на свой страх и риск.
Аппаратные испытания проводились только на Hopper KN-3810. CI/QEMU не заменяют
испытания на других роутерах. Публикация обычного релиза не означает сертификацию.
'''
    return body


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.parse_args()
    if os.environ.get('GITHUB_REPOSITORY')!=pub.REPOSITORY or os.environ.get('GITHUB_REF')!='refs/heads/main':
        raise ValueError('User releases are restricted to the project main branch')
    api=pub.GitHub(os.environ['GH_TOKEN']);repo=pub.Repo(ROOT)
    def channel():
        entry=api.get('/repos/'+pub.REPOSITORY+'/contents/web/update.json?ref=main')
        return json.loads(base64.b64decode(entry['content']))
    catalog=channel();runtime=pub.sha(catalog['revision'])
    release=pub.runtime_release(api,repo,runtime)
    if not release:raise ValueError('Published channel does not have completed runtime validation')
    evidence=json.loads(api.download(release,'ipk-evidence.json'))
    package_version,assets=select_assets(release,evidence,runtime)
    tag='v'+package_version
    prior=api.get('/repos/'+pub.REPOSITORY+'/releases/tags/'+tag,missing=True)
    latest=api.get('/repos/'+pub.REPOSITORY+'/releases/latest',missing=True)
    if prior:
        pub.verified_release(api,tag,runtime)
        existing={a['name']:a for a in prior['assets']}
        for name,sha in assets.items():
            if name in existing and existing[name].get('digest')!='sha256:'+sha:
                raise ValueError('Immutable user release artifact differs: '+name)
    def gh(*args):subprocess.run(['gh',*args,'--repo',pub.REPOSITORY],check=True)
    with tempfile.TemporaryDirectory() as directory:
        stage=Path(directory)
        for name,sha in assets.items():
            gh('release','download','runtime-'+runtime,'--pattern',name,'--dir',str(stage))
            if pub.checksum((stage/name).read_bytes())!=sha:raise ValueError('Downloaded installation file differs')
        (stage/'SHA256SUMS').write_text(''.join(sha+'  '+name+'\n' for name,sha in assets.items()))
        (stage/'notes.md').write_text(notes(ROOT,catalog,package_version))
        # Re-read the live channel immediately before choosing Latest.
        if channel()['revision']!=runtime:raise ValueError('Channel advanced; refusing a stale Latest release')
        title=f'FPTN Keenetic {catalog["version"]} · клиент {catalog["clientVersion"]}'
        if not prior:
            gh('release','create',tag,*[str(stage/n) for n in [*assets,'SHA256SUMS']],
               '--target',runtime,'--title',title,'--notes-file',str(stage/'notes.md'),'--latest')
        else:
            existing={a['name']:a for a in prior['assets']}
            for name in [*assets,'SHA256SUMS']:
                expected=pub.checksum((stage/name).read_bytes())
                if name in existing:
                    if existing[name].get('digest')!='sha256:'+expected:raise ValueError('Release asset checksum differs')
                else:gh('release','upload',tag,str(stage/name))
            gh('release','edit',tag,'--title',title,'--notes-file',str(stage/'notes.md'),'--prerelease=false','--draft=false','--latest')
        # Preserve all decoder URLs/assets, but retire the mistaken stable marker.
        if latest and latest['tag_name'].startswith('decoder-'):
            gh('release','edit',latest['tag_name'],'--prerelease=true','--latest=false')
    print(json.dumps({'status':'published','tag':tag,'runtime':runtime,'files':len(assets)+1}))


if __name__=='__main__':main()
