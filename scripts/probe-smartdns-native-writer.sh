#!/bin/sh
# One controlled reply-only native-writer test. No install or config edits.
set -eu
export LC_ALL=C LANG=C
umask 077
NATIVE_SOCKET=/var/run/fqdn-dns-mirror.sock
for cmd in socat timeout nslookup ndmc; do
  command -v "$cmd" >/dev/null 2>&1 || { echo "Нет команды $cmd" >&2; exit 1; }
done
[ "$(id -u)" = 0 ] || { echo 'Нужен root.' >&2; exit 1; }
[ -S "$NATIVE_SOCKET" ] || { echo 'Штатный сокет не найден.' >&2; exit 1; }
mkdir -p /opt/tmp
WORK=$(mktemp -d /opt/tmp/fqdn-writer.XXXXXX)
trap 'rm -rf "$WORK"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP
echo '[ПРОВЕРКА] До отправки: штатная группа domain-list9.'
ndmc -c 'show object-group fqdn domain-list9'
timeout -s TERM 6 nslookup api.ipify.org 127.0.0.1 > "$WORK/answer" 2>&1
cat "$WORK/answer"
awk '
  /^Name:/ {answer=1}
  answer && /^Address/ {
    for(i=2;i<=NF;i++) {
      if($i !~ /^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$/) continue
      n=split($i,a,"."); valid=(n==4)
      for(j=1;j<=n;j++) if(a[j]<0 || a[j]>255) valid=0
      if(valid && !seen[$i]++) print $i
    }
  }
' "$WORK/answer" > "$WORK/ips"
COUNT=$(wc -l < "$WORK/ips")
[ "$COUNT" -gt 0 ] && [ "$COUNT" -le 16 ] || { echo 'Нет корректного ограниченного списка IPv4 в ответе.' >&2; exit 1; }
# libndm binary XML: control byte, length-prefixed name/value, child nodes, END.
# Every element below uses explicit child/end commands (no sibling shorthand).
byte() { printf '%b' "\\0$(printf '%03o' "$1")"; }
uint32() {
  for shift in 24 16 8 0; do byte "$((($1 >> shift) & 255))"; done
}
string() { uint32 "${#1}"; printf '%s' "$1"; }
node() { byte "$1"; string "$2"; string "$3"; }
end() { byte 192; }
{
  node 0 '' ''
  node 1 reply ''
  while IFS= read -r addr; do
    node 1 section ''
    node 1 fqdn api.ipify.org; end
    node 1 ipv4 ''
    node 1 a "$addr"; end
    node 1 t 3; end
    end
    end
  done < "$WORK/ips"
  end
} > "$WORK/reply.bin"
echo '[ПРОВЕРКА] Один reply с IPv4 из свежего ответа SmartDNS. Тестовый TTL = 3; qid не передаётся.'
timeout -s TERM 3 socat -u STDIN "UNIX-SENDTO:$NATIVE_SOCKET" < "$WORK/reply.bin"
echo '[ПРОВЕРКА] Сразу после отправки: проверяем ipv4 и last-updated внутри api.ipify.org.'
ndmc -c 'show object-group fqdn domain-list9'
echo '[ПРОВЕРКА] Это проверка приёма reply без qid, не подтверждение готового моста. Настройки не редактировались.'
