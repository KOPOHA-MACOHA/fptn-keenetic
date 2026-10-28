#!/bin/sh
# Shared client-release validation and cache-only identity reporting.
set -eu
umask 077
ROOT=${FPTN_PREFIX:-/opt}
CONF=$ROOT/etc/fptn
LIB=$ROOT/libexec/fptn
validate() {
  jq -ce 'def hex($n): type=="string" and length==$n and all(explode[]; (.>=48 and .<=57) or (.>=97 and .<=102));
    def version: type=="string" and length<=32 and (split(".")|length==3 and all(.[]; length>0 and all(explode[]; .>=48 and .<=57)));
    select(.schema==1 and (.platform=="mipsel-mips32r2-softfloat" or .platform=="aarch64" or .platform=="armv7-softfloat") and
      (.buildRevision|hex(40)) and (.sha256|hex(64)) and (.clientVersion|version) and
      (.upstreamRevision|hex(40)) and (.upstreamVersion|version) and (.sourceLockSha256|hex(64))) |
      {schema,platform,buildRevision,sha256,clientVersion,upstreamRevision,upstreamVersion,sourceLockSha256}' "$1"
}
case "${1:-}" in
 validate) validate "$2" ;;
 inspect)
  HELPER=$LIB/client-capabilities.sh
  [ -f "$HELPER" ] || HELPER=$(dirname "$0")/fptn-client-capabilities.sh
  ACTUAL=$(sh "$HELPER" --cached 2>/dev/null) || ACTUAL='{}'
  RECORD='{}'
  if [ -f "$CONF/client-release.json" ]; then RECORD=$(validate "$CONF/client-release.json" 2>/dev/null) || RECORD='{}'; fi
  printf '%s' "$ACTUAL" | jq --argjson release "$RECORD" '
    (.sha256!=null and .sha256==$release.sha256 and .clientVersion==$release.clientVersion) as $verified |
    {clientVersion:(.clientVersion//null),clientSha256:(.sha256//null),clientIdentityVerified:$verified,
     clientBuildRevision:(if $verified then $release.buildRevision else null end),
     upstreamVersion:(if $verified then $release.upstreamVersion else null end),
     upstreamRevision:(if $verified then $release.upstreamRevision else null end)}'
  ;;
 *) echo 'Unknown identity operation' >&2; exit 1 ;;
esac
