#!/bin/sh
# lighttpd CGI, runs as its existing root user. No shell-evaluated user input.
set -eu
export LC_ALL=C LANG=C
PATH=/opt/sbin:/opt/bin:/opt/usr/bin:/usr/sbin:/usr/bin:/sbin:/bin
[ -z "${FPTN_TEST_PATH:-}" ] || PATH=$FPTN_TEST_PATH
export PATH
umask 077
ROOT=${FPTN_PREFIX:-/opt}
CONF=$ROOT/etc/fptn
LIB=$ROOT/libexec/fptn
RUN=$ROOT/var/run/fptn-web
LOCK=$RUN/action
CTL=$ROOT/bin/fptnctl
SELF=$(readlink -f "$0")

decode_token() {
  CLEAN=$(tr -d '[:space:]')
  [ -n "$CLEAN" ] && [ "${#CLEAN}" -le 262144 ] || return 1
  COMPRESSED=no
  case "$CLEAN" in
    fptnb:*) PAYLOAD=${CLEAN#fptnb:}; COMPRESSED=yes ;;
    fptnb//*) PAYLOAD=${CLEAN#fptnb//}; COMPRESSED=yes ;;
    fptn://*) PAYLOAD=${CLEAN#fptn://} ;;
    fptn:*) PAYLOAD=${CLEAN#fptn:} ;;
    *) PAYLOAD=$CLEAN ;;
  esac
  PAYLOAD=$(printf '%s' "$PAYLOAD" | tr '_-' '/+' | tr -d '=')
  case $((${#PAYLOAD}%4)) in 0) ;; 2) PAYLOAD=$PAYLOAD== ;; 3) PAYLOAD=$PAYLOAD= ;; *) return 1 ;; esac
  if [ "$COMPRESSED" = yes ]; then
    DECODED=$(printf '%s' "$PAYLOAD" | base64 -d 2>/dev/null |
      nice -n 10 timeout -s TERM 10 "$LIB/brotli" -d -c 2>/dev/null | head -c 1048577)
  else DECODED=$(printf '%s' "$PAYLOAD" | base64 -d 2>/dev/null | head -c 1048577)
  fi
  [ "${#DECODED}" -le 1048576 ] || return 1
  printf '%s' "$DECODED" | jq -ce 'select(type=="object" and
    (.servers|type=="array") and (.servers|length>0 and length<=512) and
    all(.servers[]; .name|type=="string" and length>0 and length<=128 and
      all(explode[]; .>=32 and .!=127 and .!=44)))' 2>/dev/null
}

worker() {
  ACTION=$(cat "$LOCK/action")
  rollback=no
  was_running=no
  success=no
  SUCCESS_MESSAGE='Готово.'
  finish_worker() {
    trap '' INT TERM HUP
    if [ "$rollback" = yes ]; then
      if "$CTL" stop >> "$LOCK/output" 2>&1; then
        for key in token server connection.json server-policy.json; do
          if [ -f "$LOCK/old-$key" ]; then cp -p "$LOCK/old-$key" "$CONF/$key"
          else rm -f "$CONF/$key"; fi
        done
        if [ "$was_running" = yes ]; then "$CTL" start >> "$LOCK/output" 2>&1 || true; fi
      else
        echo 'Не удалось остановить клиент для отката.' >> "$LOCK/output"
      fi
    fi
    if [ "$success" = yes ]; then
      jq -n --arg action "$ACTION" --arg message "$SUCCESS_MESSAGE" '{ok:true,action:$action,message:$message}' > "$RUN/result.new"
    else
      printf '%s\n' '{"ok":false,"message":"Операция не выполнена. Подробности — в журнале службы."}' > "$RUN/result.new"
    fi
    mv "$RUN/result.new" "$RUN/result.json"
    # Output comes from the CLI, never from the submitted request/token.
    [ ! -f "$LOCK/worker.log" ] || tail -c 8192 "$LOCK/worker.log" >> "$LOCK/output"
    tail -c 16384 "$LOCK/output" > "$RUN/action.log"
    rm -f "$RUN/failed-request.json"
    if [ "$rollback" = yes ]; then
      mkdir -p "$CONF/web-recovery"
      for key in token server connection.json server-policy.json; do
        [ ! -f "$LOCK/old-$key" ] || cp -p "$LOCK/old-$key" "$CONF/web-recovery/$key"
      done
    fi
    rm -rf "$LOCK"
  }
  trap finish_worker EXIT
  trap 'exit 1' INT TERM HUP
  : > "$LOCK/output"
  case "$ACTION" in
    start) "$CTL" start >> "$LOCK/output" 2>&1; SUCCESS_MESSAGE='Служба запущена.' ;;
    stop) "$CTL" stop >> "$LOCK/output" 2>&1; SUCCESS_MESSAGE='Служба остановлена.' ;;
    restart) "$CTL" stop >> "$LOCK/output" 2>&1; "$CTL" start >> "$LOCK/output" 2>&1; SUCCESS_MESSAGE='Клиент переподключён.' ;;
    check) "$CTL" check-servers >> "$LOCK/output" 2>&1; SUCCESS_MESSAGE='Проверка входа на серверы завершена.' ;;
    save)
      # All validation precedes stopping or writing the current configuration.
      TOKEN=$(jq -r '.token // ""' "$LOCK/request.json")
      [ -n "$TOKEN" ] || TOKEN=$(cat "$CONF/token")
      TOKEN_JSON=$(printf '%s' "$TOKEN" | decode_token) || exit 1
      SERVER=$(jq -r '.server' "$LOCK/request.json")
      printf '%s' "$TOKEN_JSON" | jq -e --arg name "$SERVER" 'any(.servers[]; .name==$name)' >/dev/null || exit 1
      jq '.connection' "$LOCK/request.json" | "$CTL" validate-settings > "$LOCK/new-connection.json" || exit 1
      if jq -e 'has("policy")' "$LOCK/request.json" >/dev/null; then
        POLICY=$(jq '.policy' "$LOCK/request.json" | "$CTL" validate-server-policy)
      else POLICY=$("$CTL" get-server-policy); fi
      printf '%s' "$TOKEN_JSON" | jq -e --arg server "$SERVER" --argjson policy "$POLICY" '
        [.servers[].name] as $names |
        ($policy.excluded|map(ascii_downcase)) as $denied |
        [$names[] | select((ascii_downcase as $k | $denied|index($k))==null)] as $allowed |
        ($allowed|length>0 and length<=64) and
        all(($policy.favorites+$policy.excluded)[]; . as $n | $names|index($n)!=null) and
        ($policy.mode=="auto" or ($allowed|index($server)!=null))' >/dev/null || {
          echo 'Проверьте списки серверов: нужен хотя бы один разрешённый, максимум 64; ручной сервер не должен быть исключён.' >> "$LOCK/output"; exit 1;
        }
      printf '%s\n' "$POLICY" > "$LOCK/new-server-policy.json"
      for key in token server connection.json server-policy.json; do
        [ ! -f "$CONF/$key" ] || cp -p "$CONF/$key" "$LOCK/old-$key"
      done
      rm -f "$RUN/server-checks.json"
      # Existing CLI status is informational; inspect its owned runtime record too.
      if [ -d "$ROOT/var/run/fptn-keenetic" ]; then was_running=yes; fi
      "$CTL" stop >> "$LOCK/output" 2>&1
      rollback=yes
      printf '%s\n' "$TOKEN" > "$CONF/token.new"
      printf '%s\n' "$SERVER" > "$CONF/server.new"
      cp "$LOCK/new-connection.json" "$CONF/connection.json.new"
      cp "$LOCK/new-server-policy.json" "$CONF/server-policy.json.new"
      for key in token server connection.json server-policy.json; do
        chmod 600 "$CONF/$key.new"; mv "$CONF/$key.new" "$CONF/$key"
      done
      unset TOKEN TOKEN_JSON
      if [ "$was_running" = yes ]; then "$CTL" start >> "$LOCK/output" 2>&1; fi
      if [ "$was_running" = yes ]; then SUCCESS_MESSAGE='Настройки применены. Клиент переподключён.'
      else SUCCESS_MESSAGE='Настройки сохранены. Служба остановлена — нажмите «Запустить» для подключения.'; fi
      rollback=no
      ;;
    *) exit 1 ;;
  esac
  success=yes
}
if [ "${1:-}" = --worker ] && [ -z "${REQUEST_METHOD:-}" ]; then worker; exit 0; fi

respond() {
  printf 'Status: %s\r\nContent-Type: application/json; charset=utf-8\r\nCache-Control: no-store\r\nX-Content-Type-Options: nosniff\r\n\r\n' "$1"
  printf '%s\n' "$2"
  exit 0
}
error() { respond "$1" "$(jq -n --arg error "$2" '{error:$error}')"; }

# A random 122-bit key, not the VPN token. No cookie or ambient credentials.
[ -r "$CONF/web-key" ] && [ -r "$CONF/web-origin" ] || error '503 Service Unavailable' 'Панель не настроена.'
ORIGIN=$(cat "$CONF/web-origin")
[ "${HTTP_HOST:-}" = "${ORIGIN#http://}" ] || error '403 Forbidden' 'Неверный адрес панели.'
[ -z "${HTTP_ORIGIN:-}" ] || [ "$HTTP_ORIGIN" = "$ORIGIN" ] || error '403 Forbidden' 'Запрос с другого сайта запрещён.'
case "${HTTP_AUTHORIZATION:-}" in 'Bearer '*) AUTH=${HTTP_AUTHORIZATION#Bearer } ;; *) error '401 Unauthorized' 'Введите код доступа к панели.' ;; esac
[ "${#AUTH}" -le 128 ] || error '401 Unauthorized' 'Неверный код доступа.'
EXPECTED=$(sha256sum "$CONF/web-key" | awk '{print $1}')
ACTUAL=$(printf '%s' "$AUTH" | sha256sum | awk '{print $1}')
[ "$ACTUAL" = "$EXPECTED" ] || error '401 Unauthorized' 'Неверный код доступа.'
unset AUTH HTTP_AUTHORIZATION
mkdir -p "$RUN"

case "${REQUEST_METHOD:-}" in
  GET)
    case "${QUERY_STRING:-}" in
      status)
        TEXT=$("$CTL" status 2>&1 || true)
        SERVICE=$("$CTL" status-json) || error '500 Internal Server Error' 'Не удалось получить состояние службы.'
        META='{}'; [ ! -f "$CONF/web-meta.json" ] || META=$(cat "$CONF/web-meta.json")
        REPORT='null'
        for source in "$ROOT/var/run/fptn-keenetic/server-checks.json" "$RUN/server-checks.json"; do
          [ -r "$source" ] || continue
          CANDIDATE=$(jq -ce 'select((.checked_at|type=="number") and (.results|type=="array")) |
            {checked_at,complete,scope,method,results:[.results[]|{name,ok,latency_ms,code}]}' "$source" 2>/dev/null) || continue
          REPORT=$(printf '%s\n%s\n' "$REPORT" "$CANDIDATE" | jq -s 'map(select(.!=null))|max_by(.checked_at)')
        done
        RSS=0
        if [ -r "$ROOT/var/run/fptn-keenetic/client.pid" ]; then
          read -r CLIENT_PID REST < "$ROOT/var/run/fptn-keenetic/client.pid" || true
          case "${CLIENT_PID:-}" in ''|*[!0-9]*) ;; *)
            RSS=$(awk '$1=="VmRSS:" {print $2}' "/proc/$CLIENT_PID/status" 2>/dev/null || true) ;;
          esac
        fi
        BUSY=false; [ ! -d "$LOCK" ] || BUSY=true
        OPERATION=''; [ ! -f "$LOCK/action" ] || OPERATION=$(cat "$LOCK/action")
        RESULT='null'; [ ! -r "$RUN/result.json" ] || RESULT=$(cat "$RUN/result.json")
        respond '200 OK' "$(jq -n --arg status "$TEXT" --arg rss "${RSS:-0}" --arg operation "$OPERATION" \
          --argjson service "$SERVICE" --argjson report "$REPORT" --argjson meta "$META" --argjson busy "$BUSY" --argjson result "$RESULT" \
          '{status:$status,service:$service,report:$report,uiVersion:"0.4.0",clientVersion:($meta.clientVersion // "неизвестна"),revision:($meta.revision // ""),operation:$operation,rssKiB:($rss|tonumber),busy:$busy,result:$result}')"
        ;;
      settings)
        CONNECTION=$("$CTL" get-settings) || error '500 Internal Server Error' 'Некорректные настройки подключения.'
        POLICY=$("$CTL" get-server-policy) || error '500 Internal Server Error' 'Некорректные списки серверов.'
        SERVERS=$(decode_token < "$CONF/token") || error '500 Internal Server Error' 'Не удалось прочитать серверы из сохранённого токена.'
        SERVER=$(cat "$CONF/server")
        respond '200 OK' "$(printf '%s' "$SERVERS" | jq --arg server "$SERVER" --argjson connection "$CONNECTION" --argjson policy "$POLICY" \
          '{server:$server,servers:[.servers[].name],connection:$connection,policy:$policy}')"
        ;;
      logs)
        { "$CTL" logs 2>&1 || true; [ ! -f "$RUN/action.log" ] || tail -c 8192 "$RUN/action.log"; } |
          tail -c 24576 > "$RUN/log-view.tmp.$$"
        LOG=$(awk -v tokenfile="$CONF/token" '
          BEGIN {getline token < tokenfile; close(tokenfile)}
          {if(tolower($0) ~ /token|authorization/) {print "[строка с секретом скрыта]"; next}
           line=$0; if(length(token)>0) while((p=index(line,token))>0) line=substr(line,1,p-1) "[скрыто]" substr(line,p+length(token)); print line}
        ' "$RUN/log-view.tmp.$$")
        rm -f "$RUN/log-view.tmp.$$"
        respond '200 OK' "$(printf '%s' "$LOG" | jq -Rs '{log:.}')"
        ;;
      *) error '404 Not Found' 'Неизвестный запрос.' ;;
    esac
    ;;
  POST)
    [ "${CONTENT_TYPE:-}" = application/json ] || error '415 Unsupported Media Type' 'Ожидается JSON.'
    case "${CONTENT_LENGTH:-}" in ''|*[!0-9]*) error '400 Bad Request' 'Некорректная длина.' ;; esac
    [ "${#CONTENT_LENGTH}" -le 6 ] && [ "$CONTENT_LENGTH" -le 300000 ] || error '413 Content Too Large' 'Слишком большой запрос.'
    BODY=$(head -c "$CONTENT_LENGTH")
    [ "${#BODY}" -eq "$CONTENT_LENGTH" ] || error '400 Bad Request' 'Неполный запрос.'
    # Byte count is reliable with LC_ALL=C, including Cyrillic server names.
    printf '%s' "$BODY" | jq -e 'type=="object" and (.action|type=="string")' >/dev/null 2>&1 || error '400 Bad Request' 'Некорректный JSON.'
    ACTION=$(printf '%s' "$BODY" | jq -r '.action')
    if [ "$ACTION" = preview ]; then
      TOKEN=$(printf '%s' "$BODY" | jq -er '.token|select(type=="string")') || error '400 Bad Request' 'Укажите токен.'
      SERVERS=$(printf '%s' "$TOKEN" | decode_token) || error '400 Bad Request' 'В токене не найден список серверов.'
      unset TOKEN
      respond '200 OK' "$(printf '%s' "$SERVERS" | jq '{servers:[.servers[].name]}')"
    fi
    case "$ACTION" in start|stop|restart|check) ;; save)
      printf '%s' "$BODY" | jq -e '(.server|type=="string" and length>0 and length<=128) and
        (.token // ""|type=="string" and length<=262144)' >/dev/null 2>&1 || error '400 Bad Request' 'Некорректный сервер или токен.'
      printf '%s' "$BODY" | jq '.connection' | "$CTL" validate-settings >/dev/null 2>&1 || error '400 Bad Request' 'Проверьте стратегию, метод обхода и домен SNI.'
      if printf '%s' "$BODY" | jq -e 'has("policy")' >/dev/null; then
        printf '%s' "$BODY" | jq '.policy' | "$CTL" validate-server-policy >/dev/null 2>&1 || error '400 Bad Request' 'Проверьте списки серверов.'
      fi
      ;; *) error '400 Bad Request' 'Неизвестное действие.' ;; esac
    mkdir "$LOCK" 2>/dev/null || error '409 Conflict' 'Дождитесь завершения предыдущей операции.'
    trap 'rm -rf "$LOCK"' EXIT
    printf '%s' "$BODY" > "$LOCK/request.json"
    printf '%s\n' "$ACTION" > "$LOCK/action"
    rm -f "$RUN/result.json"
    REQUEST_METHOD= nohup sh "$SELF" --worker > "$LOCK/worker.log" 2>&1 < /dev/null &
    trap - EXIT
    respond '202 Accepted' '{"accepted":true}'
    ;;
  *) error '405 Method Not Allowed' 'Метод не поддерживается.' ;;
esac
