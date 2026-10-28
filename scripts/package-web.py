#!/usr/bin/env python3
"""Refresh content-versioned asset URLs and router payload checksums before commit."""
import hashlib
from pathlib import Path
import re
import sys

ROOT=Path(__file__).resolve().parents[1]
FILES=['web/index.html','web/index.cgi','web/style.css','web/app.js','web/api.cgi','scripts/fptn-native-service.sh','scripts/fptn-web-auth.sh','scripts/fptn-web-auth.php','scripts/fptn-web-operations.php','scripts/fptn-transport-plan.php','scripts/fptn-web-update.sh','scripts/fptn-client-capabilities.sh','scripts/fptn-client-identity.sh','client-release.json','runtime-assets.json','platform-releases.json','scripts/fptn-platform.sh','scripts/fptn-uplink.sh','scripts/fptn-ipv6.sh']


def prepare(root,check=False):
    html_path=root/'web/index.html'
    html=html_path.read_text()
    version=re.search(r"const UI_VERSION = '([^']+)';",(root/'web/app.js').read_text()).group(1)
    if f'data-ui-version="{version}"' not in html:
        raise ValueError('HTML and JS versions differ')
    for asset in ['app.js','style.css']:
        digest=hashlib.sha256((root/'web'/asset).read_bytes()).hexdigest()[:12]
        html,count=re.subn(re.escape(asset)+r'\?v=[^"\s]+',f'{asset}?v={version}-{digest}',html)
        if count!=1:raise ValueError('Missing or duplicate asset URL: '+asset)
    manifest=''.join(hashlib.sha256(html.encode() if name=='web/index.html' else (root/name).read_bytes()).hexdigest()+'  '+name+'\n' for name in FILES)
    target=root/'web/SHA256SUMS'
    if check:
        if html!=html_path.read_text() or manifest!=target.read_text():
            raise ValueError('Stale web payload: run python3 scripts/package-web.py')
    else:
        html_path.write_text(html);target.write_text(manifest)


if __name__=='__main__':
    prepare(ROOT,'--check' in sys.argv[1:])
