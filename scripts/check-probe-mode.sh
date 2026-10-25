#!/bin/sh
# Run in the cross-build image. Only fake credentials and a closed loopback port.
set -eu
python - <<'PY'
import base64,json,pathlib
data={'version':1,'service_name':'offline-test','username':'probe','password':'probe',
      'servers':[{'name':'offline-test','host':'127.0.0.1','port':9,'md5_fingerprint':'0'*32}]}
pathlib.Path('/tmp/offline-token').write_text('fptn:'+base64.b64encode(json.dumps(data).encode()).decode())
pathlib.Path('/tmp/offline-policy.json').write_text(json.dumps({'mode':'auto','favorites':[],'excluded':[]}))
PY
if LC_ALL=C LANG=C timeout 45 qemu-mipsel /out/fptn-client-cli \
  --keenetic-connect --keenetic-native --keenetic-probe-only \
  --keenetic-server-policy /tmp/offline-policy.json --keenetic-probe-report /tmp/offline-report.json \
  --access-token "$(cat /tmp/offline-token)" --out-network-interface lo \
  --gateway-ip 127.0.0.1 --gateway-ipv6 ::1 --tun-interface-name opkgtun0 \
  --bypass-method obfuscation --enable-ad-block false --blacklist-domains '' > /out/PROBE-CHECK.txt 2>&1; then
  cat /out/PROBE-CHECK.txt
else
  result=$?
  cat /out/PROBE-CHECK.txt
  [ ! -f /tmp/offline-report.json ] || cat /tmp/offline-report.json
  exit "$result"
fi
python - <<'PY'
import json
data=json.load(open('/tmp/offline-report.json'))
assert data['complete'] and data['method']=='fptn-login'
assert len(data['results'])==1 and data['results'][0]['name']=='offline-test' and not data['results'][0]['ok']
print('MIPS probe-only: completed without TUN, unavailable login reported, clean process exit: PASS')
PY
