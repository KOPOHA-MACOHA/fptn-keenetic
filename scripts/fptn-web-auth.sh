#!/bin/sh
# Local panel credentials and revocable, expiring sessions. Never reads root's shadow.
set -eu
umask 077
export LC_ALL=C LANG=C
ROOT=${FPTN_PREFIX:-/opt}
CONF=$ROOT/etc/fptn
RUN=$ROOT/var/run/fptn-web
CREDENTIALS=${FPTN_AUTH_FILE:-$CONF/web-auth.json}
fail() { echo "$*" >&2; exit 1; }
random() { tr -d '\n-' < /proc/sys/kernel/random/uuid; }
hash_password() { printf '%s\n' "$PASSWORD" | openssl passwd -6 -salt "$SALT" -stdin; }
case "${1:-}" in
 passwd)
  [ "$(id -u)" = 0 ] || fail 'Требуется root.'
  command -v openssl >/dev/null || fail 'Нужен openssl (Entware: opkg install openssl-util).'
  [ -t 0 ] || fail 'Задайте пароль в интерактивном терминале.'
  printf 'Логин панели [admin]: ' >&2; IFS= read -r USERNAME
  USERNAME=${USERNAME:-admin}
  case "$USERNAME" in *[!a-zA-Z0-9_.-]*|'') fail 'Логин: латинские буквы, цифры, точка, дефис, подчёркивание.' ;; esac
  [ "${#USERNAME}" -le 64 ] || fail 'Слишком длинный логин.'
  TTY_STATE=$(stty -g)
  trap 'stty "$TTY_STATE"; unset PASSWORD CONFIRM' EXIT
  trap 'exit 1' INT TERM HUP
  stty -echo
  printf 'Новый пароль панели (от 10 символов): ' >&2; IFS= read -r PASSWORD; printf '\nПовторите пароль: ' >&2; IFS= read -r CONFIRM; printf '\n' >&2
  stty "$TTY_STATE"
  [ "$PASSWORD" = "$CONFIRM" ] || fail 'Пароли не совпали.'
  [ "${#PASSWORD}" -ge 10 ] && [ "${#PASSWORD}" -le 256 ] || fail 'Длина пароля: 10–256 байт.'
  SALT=$(random | cut -c 1-16)
  HASH=$(hash_password)
  case "$HASH" in '$6$'*) ;; *) fail 'OpenSSL не поддерживает SHA-512 crypt.' ;; esac
  mkdir -p "$(dirname "$CREDENTIALS")"
  jq -n --arg username "$USERNAME" --arg hash "$HASH" '{username:$username,hash:$hash}' > "$CREDENTIALS.new"
  chmod 600 "$CREDENTIALS.new"; mv "$CREDENTIALS.new" "$CREDENTIALS"
  # Changing credentials invalidates all sessions, including previously copied ones.
  [ "$CREDENTIALS" != "$CONF/web-auth.json" ] || rm -rf "$RUN/sessions"
  echo 'Логин и пароль панели сохранены.' >&2
  ;;
 login)
  [ -s "$CREDENTIALS" ] || fail 'Задайте логин и пароль командой fptnctl web-password.'
  mkdir -p "$RUN/sessions"; chmod 700 "$RUN" "$RUN/sessions"
  mkdir "$RUN/auth-lock" 2>/dev/null || fail 'Вход уже проверяется. Повторите через несколько секунд.'
  trap 'rm -rf "$RUN/auth-lock"' EXIT
  trap 'exit 1' INT TERM HUP
  NOW=$(date +%s)
  UNTIL=0; [ ! -f "$RUN/auth-delay" ] || read -r UNTIL < "$RUN/auth-delay" || true
  [ "$NOW" -ge "$UNTIL" ] || fail 'Слишком много попыток. Повторите через 30 секунд.'
  BODY=$(head -c 4097)
  printf '%s' "$BODY" | jq -e 'type=="object" and (.username|type=="string" and length<=64) and (.password|type=="string" and length<=256 and (index("\n")==null) and (index("\u0000")==null))' >/dev/null || fail 'Некорректные данные входа.'
  USERNAME=$(printf '%s' "$BODY" | jq -r .username)
  PASSWORD=$(printf '%s' "$BODY" | jq -r .password)
  CRED_STAMP=$(sha256sum "$CREDENTIALS" | awk '{print $1}')
  HASH=$(jq -er '.hash' "$CREDENTIALS"); EXPECTED_USER=$(jq -er '.username' "$CREDENTIALS")
  SALT=$(printf '%s' "$HASH" | cut -d '$' -f 3)
  ACTUAL=$(hash_password)
  unset PASSWORD BODY
  if [ "$USERNAME" != "$EXPECTED_USER" ] || [ "$ACTUAL" != "$HASH" ]; then
    FAILS=0; [ ! -f "$RUN/auth-failures" ] || read -r FAILS < "$RUN/auth-failures" || true
    FAILS=$((FAILS+1)); printf '%s\n' "$FAILS" > "$RUN/auth-failures"
    if [ "$FAILS" -ge 5 ]; then printf '%s\n' "$((NOW+30))" > "$RUN/auth-delay"; rm -f "$RUN/auth-failures"; fi
    fail 'Неверный логин или пароль.'
  fi
  rm -f "$RUN/auth-failures" "$RUN/auth-delay"
  # Bound stale storage. Session names contain only random hex, never credentials.
  for SESSION in "$RUN/sessions/"*; do
    [ -f "$SESSION" ] || continue
    read -r EXPIRY SESSION_STAMP < "$SESSION" || EXPIRY=0
    [ "$EXPIRY" -gt "$NOW" ] || rm -f "$SESSION"
  done
  COUNT=$(find "$RUN/sessions" -type f | wc -l)
  [ "$COUNT" -lt 32 ] || fail 'Слишком много активных сессий. Выйдите из другой панели или сбросьте пароль.'
  [ "$CRED_STAMP" = "$(sha256sum "$CREDENTIALS" | awk '{print $1}')" ] || fail 'Пароль изменился во время входа. Повторите вход.'
  TOKEN=$(random)$(random)
  DIGEST=$(printf '%s' "$TOKEN" | sha256sum | awk '{print $1}')
  printf '%s %s\n' "$((NOW+43200))" "$CRED_STAMP" > "$RUN/sessions/$DIGEST"
  jq -n --arg key "$TOKEN" '{key:$key,expiresIn:43200}'
  ;;
 verify|logout)
  IFS= read -r TOKEN || true
  case "${TOKEN:-}" in ''|*[!a-f0-9]*) exit 1 ;; esac
  [ "${#TOKEN}" -eq 64 ] || exit 1
  DIGEST=$(printf '%s' "$TOKEN" | sha256sum | awk '{print $1}')
  SESSION=$RUN/sessions/$DIGEST
  [ -f "$SESSION" ] || exit 1
  read -r EXPIRY SESSION_STAMP < "$SESSION"
  [ -s "$CREDENTIALS" ] || exit 1
  [ "$SESSION_STAMP" = "$(sha256sum "$CREDENTIALS" | awk '{print $1}')" ] || { rm -f "$SESSION"; exit 1; }
  [ "$EXPIRY" -gt "$(date +%s)" ] || { rm -f "$SESSION"; exit 1; }
  if [ "$1" = logout ]; then rm -f "$SESSION"; fi
  ;;
 *) fail 'Использование: fptn-web-auth.sh passwd|login|verify|logout' ;;
esac
