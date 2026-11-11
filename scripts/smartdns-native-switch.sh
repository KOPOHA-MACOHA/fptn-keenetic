#!/bin/sh
# One-time migration; no installed daemon or netfilter rules.
set -eu
export LC_ALL=C LANG=C
umask 077
B=${2:-/opt/etc/dns-before-native.w9vuob}
CONF=/opt/etc/smartdns/smartdns.conf
INIT=/opt/etc/init.d/S38smartdns
fail() { echo "$*" >&2; exit 1; }
rollback() {
  set +e
  echo 'Восстанавливаю SmartDNS на :53 и прежние upstream Keenetic.'
  ndmc -c 'opkg dns-override'
  ndmc -c 'dns-proxy filter engine opkg'
  ndmc -c 'no ip name-server 192.168.1.1:6053'
  cp -p "$B/smartdns-restore53.conf" "$CONF"
  "$INIT" restart
  while IFS= read -r command; do [ -z "$command" ] || ndmc -c "$command"; done < "$B/restore-upstreams.txt"
  echo 'Откат выполнен в running-config. Startup-config не сохранялся.'
}
[ "$(id -u)" = 0 ] || fail 'Нужен root.'
[ -f "$B/keenetic-running.txt" ] && [ -f "$B/smartdns.conf" ] || fail 'Нет резервной копии.'
case "${1:-apply}" in
  rollback)
    [ -f "$B/smartdns-restore53.conf" ] && [ -f "$B/restore-upstreams.txt" ] || fail 'Нет файлов отката.'
    rollback; exit;;
  apply) ;;
  *) fail 'Режимы: apply или rollback; второй аргумент — каталог резервной копии.';;
esac
[ ! -e "$B/switch-started" ] || fail 'Переключение уже запускалось. Повторно автоматически не применяю.'
for cmd in ndmc timeout nslookup netstat awk; do command -v "$cmd" >/dev/null 2>&1 || fail "Нет $cmd."; done
if netstat -lnptu 2>/dev/null | awk '$4 ~ /:6053$/ {found=1} END {exit !found}'; then fail 'Порт 6053 занят.'; fi
netstat -lnptu 2>/dev/null | awk '$4 ~ /:53$/ && /smartdns/ {found=1} END {exit !found}' || fail 'SmartDNS не обнаружен на :53; сначала нужна проверка текущего состояния.'
awk '$1=="bind" {udp++} $1=="bind-tcp" {tcp++} END {exit !(udp==1 && tcp==1)}' "$CONF" || fail 'Ожидалась одна bind и одна bind-tcp.'
# Removing firmware DoH/DoT would break a SmartDNS using their loopback stubs.
if awk '$1 ~ /^server(-https|-tls|-quic)?$/ && $2 ~ /127\.|localhost|::1/ {found=1}
        $1=="resolv-file" || $1=="conf-file" {found=1} END {exit !found}' "$CONF"; then
  fail 'Есть локальный upstream, resolv-file или вложенный конфиг SmartDNS. Нужна отдельная проверка; ничего не изменено.'
fi
if awk '/^[^[:space:]!]/ {section=$0}
  section=="dns-proxy" && $1 ~ /^(tls|https)$/ && $2=="upstream" && /[[:space:]](on|domain)[[:space:]]/ {found=1}
  END {exit !found}' "$B/keenetic-running.txt"; then
  fail 'DoH/DoT привязаны к интерфейсу или домену. Нужна точечная настройка; ничего не изменено.'
fi
awk '/^ip name-server / && (NF==3 || (NF==4 && $4=="\"\"")) {print}
  /^[^[:space:]!]/ {section=$0}
  section=="dns-proxy" && $1 ~ /^(tls|https)$/ && $2=="upstream" {sub(/^[[:space:]]+/, ""); print "dns-proxy " $0}' \
  "$B/keenetic-running.txt" > "$B/restore-upstreams.txt"
awk '/^ip name-server / && (NF==3 || (NF==4 && $4=="\"\"")) {print "no " $0}' \
  "$B/keenetic-running.txt" > "$B/remove-plain-dns.txt"
# The user may already have edited the file's ports without restarting it.
# Rollback must match the observed live resolver on :53, not an unstarted edit.
awk '$1=="bind" {$2="[::]:53"} $1=="bind-tcp" {$2="[::]:53"} {print}' "$CONF" > "$B/smartdns-restore53.conf"
cp -p "$CONF" "$B/smartdns-before-switch.conf"
cp -p "$CONF" "$CONF.native-new"
awk '$1=="bind" {$2="192.168.1.1:6053"} $1=="bind-tcp" {$2="192.168.1.1:6053"} {print}' "$CONF" > "$CONF.native-new"
touch "$B/switch-started"
trap 'code=$?; trap - EXIT; [ "$code" = 0 ] || rollback; exit "$code"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP
mv "$CONF.native-new" "$CONF"
"$INIT" restart
echo '=== SmartDNS напрямую на новом порту ==='
timeout -s TERM 8 nslookup api.ipify.org 192.168.1.1:6053
ndmc -c 'ip name-server 192.168.1.1:6053'
while IFS= read -r command; do [ -z "$command" ] || ndmc -c "$command"; done < "$B/remove-plain-dns.txt"
ndmc -c 'dns-proxy no tls upstream'
ndmc -c 'dns-proxy no https upstream'
ndmc -c 'dns-proxy no filter engine'
ndmc -c 'no opkg dns-override'
sleep 2
echo '=== Штатный DNS Keenetic на :53 ==='
timeout -s TERM 8 nslookup api.ipify.org 192.168.1.1
awk '$1=="dns_server" && $3=="192.168.1.1:6053" {found=1} END {exit !found}' /var/ndnproxymain.conf || fail 'SmartDNS отсутствует среди фактических upstream Keenetic.'
echo '=== Фактические upstream штатного DNS ==='
awk '$1=="dns_server" {sub(/#.*/, ""); print}' /var/ndnproxymain.conf
echo '=== Слушающие DNS-порты ==='
netstat -lnptu 2>/dev/null | awk 'NR<=2 || $4 ~ /:(53|6053)$/'
trap - EXIT INT TERM HUP
echo 'Переключение выполнено в running-config. Startup-config пока не сохранялся.'
echo 'Не перезагружайте роутер до проверки и сохранения. Пришлите вывод.'
