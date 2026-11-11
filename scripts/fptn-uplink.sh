#!/bin/sh
# Read-only native uplink discovery. No probe packets, policy rules or route writes.
set -eu
SELECTED=${1:-}
case "$SELECTED" in *[!a-zA-Z0-9_.-]*|-*|opkgtun*) echo 'Некорректный исходящий интерфейс.' >&2; exit 1 ;; esac
[ "${#SELECTED}" -le 15 ] || exit 1
ROUTES=$(ip -4 route show table main default)
CANDIDATES=$(printf '%s\n' "$ROUTES" | awk -v selected="$SELECTED" '
  $1=="default" {
    iface=""; gateway=""; source=""; metric=0; usable=1
    for(i=2;i<=NF;i++) {
      if($i=="dev")iface=$(i+1)
      if($i=="via")gateway=$(i+1)
      if($i=="src")source=$(i+1)
      if($i=="metric") {metric=$(i+1);if(metric!~/^[0-9]+$/)usable=0}
      if($i=="linkdown" || $i=="dead" || $i=="nexthop")usable=0
    }
    if(usable && iface!="" && iface!~/^opkgtun/ && iface!~/[^a-zA-Z0-9_.-]/ && length(iface)<=15 && (selected=="" || iface==selected)) {
      n++;m[n]=metric;row[n]=iface "|" gateway "|" source
    }
  }
  END {for(k=1;k<=n;k++){best=0;for(i=1;i<=n;i++)if(row[i]!="" && (best==0 || m[i]<m[best]))best=i;print row[best];row[best]=""}}
')
while IFS='|' read -r IFACE GATEWAY SOURCE; do
  [ -n "$IFACE" ] || continue
  LINK=$(ip -o link show dev "$IFACE" 2>/dev/null) || continue
  # PPP links often have state UNKNOWN; the administrative UP flag is authoritative.
  printf '%s\n' "$LINK" | awk 'match($0,/<[^>]*>/){flags=substr($0,RSTART+1,RLENGTH-2);n=split(flags,a,",");for(i=1;i<=n;i++)if(a[i]=="UP")ok=1} END{exit !ok}' || continue
  INDEX=$(printf '%s\n' "$LINK" | awk 'NR==1 {sub(/:$/,"",$1);print $1}')
  case "$INDEX" in ''|*[!0-9]*) continue ;; esac
  ADDRESSES=$(ip -4 -o addr show dev "$IFACE" scope global 2>/dev/null) || continue
  ADDRESS=$(printf '%s\n' "$ADDRESSES" | awk -v preferred="$SOURCE" '
    !/ deprecated| tentative| dadfailed/ {for(i=1;i<NF;i++)if($i=="inet"){split($(i+1),a,"/");if(first=="")first=a[1];if(a[1]==preferred)found=a[1]}}
    END {print found!=""?found:first}')
  [ -n "$ADDRESS" ] || continue
  jq -n --arg iface "$IFACE" --arg gateway "$GATEWAY" --arg address "$ADDRESS" --arg index "$INDEX" \
    '{interface:$iface,gateway:$gateway,address:$address,index:($index|tonumber),signature:([$iface,$index,$gateway,$address]|join("|"))}'
  exit 0
done <<EOF
$CANDIDATES
EOF
echo 'Нет доступного исходящего IPv4-подключения.' >&2
exit 1
