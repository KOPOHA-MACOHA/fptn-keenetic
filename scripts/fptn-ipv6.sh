#!/bin/sh
# Optional NAT66 for packets already routed to the owned FPTN interface.
# No routes, DNS, forwarding policy, global flushes or other interfaces are changed.
set -eu
umask 077
ROOT=${FPTN_PREFIX:-/opt}
CONF=$ROOT/etc/fptn
OWNED=$CONF/ipv6-nat-owned
CHAIN=FPTN_KN_6
ADDRESS=fd42:6670:746e::1
fail() { echo "FPTN IPv6: $*" >&2; exit 1; }
NATIVE=$(cat "$CONF/native-interface" 2>/dev/null || true)
case "$NATIVE" in OpkgTun*) SUFFIX=${NATIVE#OpkgTun} ;; *) SUFFIX= ;; esac
case "$SUFFIX" in ''|*[!0-9]*|0?*) VALID_NATIVE=no ;; *) VALID_NATIVE=yes ;; esac
[ "${#SUFFIX}" -le 8 ] || VALID_NATIVE=no
TUN=$(printf '%s' "$NATIVE" | tr 'A-Z' 'a-z')
ipt() { ip6tables -w 5 -t nat "$@"; }
check() {
  command -v ip6tables >/dev/null 2>&1 || fail 'Нужен ip6tables Entware и поддержка IPv6 NAT в ядре.'
  ipt -S POSTROUTING >/dev/null || fail 'Ядро не предоставляет IPv6 NAT. IPv6 через FPTN не включён.'
}
owned_rules() {
  RULES=$(ipt -S "$CHAIN" 2>/dev/null) || return 1
  printf '%s\n' "$RULES" | while IFS= read -r rule; do
    case "$rule" in
      "-N $CHAIN"|"-A $CHAIN -j SNAT --to-source $ADDRESS"|"-A $CHAIN -j SNAT --to-source [$ADDRESS]") ;;
      *) exit 1 ;;
    esac
  done
}
case "${1:-}" in
  check) check ;;
  apply)
    [ "$VALID_NATIVE" = yes ] || fail 'Не определён собственный OpkgTun FPTN.'
    check
    if [ ! -f "$OWNED" ]; then
      if ipt -S "$CHAIN" >/dev/null 2>&1; then fail 'Имя цепочки занято; чужие правила не изменялись.'; fi
      # Record ownership before creating the chain, so an interrupted setup is recoverable.
      : > "$OWNED"
    fi
    ipt -S "$CHAIN" >/dev/null 2>&1 || ipt -N "$CHAIN" || ipt -S "$CHAIN" >/dev/null
    owned_rules || fail 'В цепочке FPTN есть неизвестные правила; она не перезаписывалась.'
    ipt -C "$CHAIN" -j SNAT --to-source "$ADDRESS" >/dev/null 2>&1 || ipt -A "$CHAIN" -j SNAT --to-source "$ADDRESS"
    ipt -C POSTROUTING -o "$TUN" -j "$CHAIN" >/dev/null 2>&1 || ipt -I POSTROUTING 1 -o "$TUN" -j "$CHAIN"
    ;;
  remove)
    [ -f "$OWNED" ] || exit 0
    [ "$VALID_NATIVE" = yes ] || fail 'Не определён собственный OpkgTun FPTN.'
    check
    if ipt -S "$CHAIN" >/dev/null 2>&1; then
      owned_rules || fail 'Неизвестные правила сохранены; автоматическое удаление отменено.'
    fi
    # Delete only exact owned rules, including harmless duplicates from concurrent hooks.
    n=0
    while ipt -C POSTROUTING -o "$TUN" -j "$CHAIN" >/dev/null 2>&1; do
      [ "$n" -lt 64 ] || fail 'Неожиданно много ссылок на цепочку.'
      ipt -D POSTROUTING -o "$TUN" -j "$CHAIN"; n=$((n+1))
    done
    if ipt -S "$CHAIN" >/dev/null 2>&1; then
      n=0
      while ipt -C "$CHAIN" -j SNAT --to-source "$ADDRESS" >/dev/null 2>&1; do
        [ "$n" -lt 64 ] || fail 'Неожиданно много правил SNAT.'
        ipt -D "$CHAIN" -j SNAT --to-source "$ADDRESS"; n=$((n+1))
      done
      ipt -X "$CHAIN" || fail 'Цепочка ещё используется. Чужие ссылки сохранены.'
    fi
    rm -f "$OWNED"
    ;;
  *) fail 'Использование: ipv6.sh check|apply|remove' ;;
esac
