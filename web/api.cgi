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

restore_saved_settings() {
  "$CTL" stop >> "$LOCK/output" 2>&1 || return 1
  if [ -f "$LOCK/old-interface-mtu" ]; then "$CTL" set-interface-mtu "$(cat "$LOCK/old-interface-mtu")" >> "$LOCK/output" 2>&1 || return 1; fi
  for key in token server connection.json server-policy.json fallback.json; do
    if [ -f "$LOCK/old-$key" ]; then cp -p "$LOCK/old-$key" "$CONF/$key" || return 1
    else rm -f "$CONF/$key" || return 1; fi
  done
  rm -f "$LOCK/rollback-needed"
  if [ "$(cat "$LOCK/was-running" 2>/dev/null)" = yes ]; then
    "$CTL" start >> "$LOCK/output" 2>&1 || { echo 'Прежние настройки восстановлены, но подключение не подтвердилось.' >> "$LOCK/output"; return 1; }
  fi
}
worker() {
  FPTN_OWNER_PID=$$ sh "$LIB/web-auth.sh" op-own >/dev/null
  ACTION=$(cat "$LOCK/action")
  rollback=no
  was_running=no
  success=no
  SUCCESS_MESSAGE='Готово.'
  finish_worker() {
    trap ':' INT TERM HUP
    if [ "$rollback" = yes ] && [ -f "$LOCK/rollback-needed" ]; then
      if ! restore_saved_settings; then echo 'Не удалось завершить откат. Используйте восстановление в панели.' >> "$LOCK/output"; fi
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
    if [ -f "$LOCK/rollback-needed" ]; then
      mkdir -p "$CONF/web-recovery"
      for key in token server connection.json server-policy.json fallback.json; do
        [ ! -f "$LOCK/old-$key" ] || cp -p "$LOCK/old-$key" "$CONF/web-recovery/$key"
      done
    fi
    if [ ! -f "$LOCK/rollback-needed" ]; then rm -rf "$LOCK"; fi
  }
  trap finish_worker EXIT
  trap 'exit 1' INT TERM HUP
  : > "$LOCK/output"
  case "$ACTION" in
    recover)
      if [ -f "$LOCK/rollback-needed" ]; then restore_saved_settings || exit 1; fi
      SUCCESS_MESSAGE='Управление восстановлено. Состояние службы проверено заново.'
      [ ! -f "$LOCK/install-backup" ] || SUCCESS_MESSAGE='Управление восстановлено. Обновление было прервано — повторите установку.' ;; 
    start) "$CTL" start >> "$LOCK/output" 2>&1; SUCCESS_MESSAGE='Служба запущена.' ;;
    stop) "$CTL" stop >> "$LOCK/output" 2>&1; SUCCESS_MESSAGE='Служба остановлена.' ;;
    restart) "$CTL" stop >> "$LOCK/output" 2>&1; "$CTL" start >> "$LOCK/output" 2>&1; SUCCESS_MESSAGE='Клиент переподключён.' ;;
    setup-interface) "$CTL" setup-interface-save >> "$LOCK/output" 2>&1; SUCCESS_MESSAGE='Интерфейс FPTN создан, конфигурация Keenetic сохранена. Маршруты задаются в Keenetic.' ;;
    enable|disable) "$CTL" "$ACTION" >> "$LOCK/output" 2>&1; SUCCESS_MESSAGE='Настройка автозапуска сохранена.' ;;
    check) "$CTL" check-servers >> "$LOCK/output" 2>&1; SUCCESS_MESSAGE='Проверка входа на серверы завершена.' ;;
    check-update) sh "$LIB/web-update.sh" check >> "$LOCK/output" 2>&1; SUCCESS_MESSAGE='Проверка обновлений завершена.' ;;
    update) sh "$LIB/web-update.sh" apply >> "$LOCK/output" 2>&1; SUCCESS_MESSAGE='Обновление установлено. Обновите страницу панели.' ;;
    save)
      # All validation precedes stopping or writing the current configuration.
      TOKEN=$(jq -r '.token // ""' "$LOCK/request.json")
      [ -n "$TOKEN" ] || TOKEN=$(cat "$CONF/token" 2>/dev/null || true)
      TOKEN_JSON=$(printf '%s' "$TOKEN" | decode_token) || exit 1
      SERVER=$(jq -r '.server' "$LOCK/request.json")
      printf '%s' "$TOKEN_JSON" | jq -e --arg name "$SERVER" 'any(.servers[]; .name==$name)' >/dev/null || exit 1
      jq '.connection' "$LOCK/request.json" | "$CTL" validate-settings > "$LOCK/new-connection.json" || exit 1
      if jq -e '.ipv6==true' "$LOCK/new-connection.json" >/dev/null; then "$CTL" check-ipv6 >> "$LOCK/output" 2>&1 || exit 1; fi
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
      if jq -e 'has("fallback")' "$LOCK/request.json" >/dev/null; then FALLBACK=$(jq .fallback "$LOCK/request.json")
      else FALLBACK=$("$CTL" get-fallback); fi
      jq --argjson fallback "$FALLBACK" --argjson policy "$POLICY" '{fallback:$fallback,policy:$policy}' "$LOCK/request.json" | "$CTL" validate-fallback > "$LOCK/new-fallback.json" || exit 1
      for key in token server connection.json server-policy.json fallback.json; do
        [ ! -f "$CONF/$key" ] || cp -p "$CONF/$key" "$LOCK/old-$key"
      done
      rm -f "$RUN/server-checks.json"
      # Existing CLI status is informational; inspect its owned runtime record too.
      if [ -d "$ROOT/var/run/fptn-keenetic" ]; then was_running=yes; fi
      printf '%s\n' "$was_running" > "$LOCK/was-running"
      if jq -e '.mtu>0' "$LOCK/new-connection.json" >/dev/null; then
        "$CTL" get-interface-mtu > "$LOCK/old-interface-mtu" || exit 1
      fi
      : > "$LOCK/rollback-needed"
      rollback=yes
      "$CTL" stop >> "$LOCK/output" 2>&1
      printf '%s\n' "$TOKEN" > "$CONF/token.new"
      printf '%s\n' "$SERVER" > "$CONF/server.new"
      cp "$LOCK/new-connection.json" "$CONF/connection.json.new"
      cp "$LOCK/new-server-policy.json" "$CONF/server-policy.json.new"
      cp "$LOCK/new-fallback.json" "$CONF/fallback.json.new"
      for key in token server connection.json server-policy.json fallback.json; do
        chmod 600 "$CONF/$key.new"; mv "$CONF/$key.new" "$CONF/$key"
      done
      unset TOKEN TOKEN_JSON
      if [ "$was_running" = yes ]; then "$CTL" start >> "$LOCK/output" 2>&1; fi
      if [ "$was_running" = yes ]; then SUCCESS_MESSAGE='Настройки применены. Клиент переподключён.'
      else SUCCESS_MESSAGE='Настройки сохранены. Служба остановлена — нажмите «Запустить» для подключения.'; fi
      rm -f "$LOCK/rollback-needed"
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

# Validate the target before accepting credentials, including login requests.
[ -r "$CONF/web-origin" ] || error '503 Service Unavailable' 'Панель не настроена.'
ORIGIN=$(cat "$CONF/web-origin")
EXPECTED_HOST=${ORIGIN#http://}
REQUEST_HOST=${HTTP_HOST:-}
REQUEST_ORIGIN=${HTTP_ORIGIN:-}
# Browsers omit the default HTTP port in Host/Origin, even for a URL with :80.
case "$EXPECTED_HOST" in *:80)
  EXPECTED_HOST=${EXPECTED_HOST%:80}
  [ "$REQUEST_HOST" != "$EXPECTED_HOST:80" ] || REQUEST_HOST=$EXPECTED_HOST
  [ "$REQUEST_ORIGIN" != "http://$EXPECTED_HOST:80" ] || REQUEST_ORIGIN=http://$EXPECTED_HOST
  ORIGIN=http://$EXPECTED_HOST
esac
[ "$REQUEST_HOST" = "$EXPECTED_HOST" ] || error '403 Forbidden' 'Неверный адрес панели.'
[ -z "$REQUEST_ORIGIN" ] || [ "$REQUEST_ORIGIN" = "$ORIGIN" ] || error '403 Forbidden' 'Запрос с другого сайта запрещён.'
if [ "${QUERY_STRING:-}" = auth-info ] && [ "${REQUEST_METHOD:-}" = GET ]; then
  INFO=$(sh "$LIB/web-auth.sh" info) || error '503 Service Unavailable' 'Не удалось проверить настройки входа.'
  respond '200 OK' "$(printf '%s' "$INFO" | jq '. + {uiVersion:"0.16.0"}')"
fi
case "${QUERY_STRING:-}:${REQUEST_METHOD:-}" in login:POST|setup:POST)
  [ "${CONTENT_TYPE:-}" = application/json ] || error '415 Unsupported Media Type' 'Ожидается JSON.'
  case "${CONTENT_LENGTH:-}" in ''|*[!0-9]*) error '400 Bad Request' 'Некорректная длина.' ;; esac
  [ "${#CONTENT_LENGTH}" -le 4 ] && [ "$CONTENT_LENGTH" -le 4096 ] || error '413 Content Too Large' 'Слишком большой запрос.'
  if LOGIN=$(head -c "$CONTENT_LENGTH" | sh "$LIB/web-auth.sh" "$QUERY_STRING" 2>/dev/null); then
    respond '200 OK' "$LOGIN"
  fi
  if ! printf '%s' "$LOGIN" | jq -e '(.error|type=="string")' >/dev/null 2>&1; then error '503 Service Unavailable' 'Не удалось проверить вход.'; fi
  if [ "$QUERY_STRING" = setup ]; then respond '400 Bad Request' "$LOGIN"; fi
  respond '401 Unauthorized' "$LOGIN"
  ;;
esac
case "${HTTP_AUTHORIZATION:-}" in 'Bearer '*) AUTH=${HTTP_AUTHORIZATION#Bearer } ;; *) error '401 Unauthorized' 'Войдите по логину и паролю.' ;; esac
printf '%s\n' "$AUTH" | sh "$LIB/web-auth.sh" verify || error '401 Unauthorized' 'Сессия завершена. Войдите снова.'
if [ "${QUERY_STRING:-}" = logout ] && [ "${REQUEST_METHOD:-}" = POST ]; then
  printf '%s\n' "$AUTH" | sh "$LIB/web-auth.sh" logout
  respond '200 OK' '{"ok":true}'
fi
if [ "${QUERY_STRING:-}" = account ]; then
  case "${REQUEST_METHOD:-}" in
    GET)
      ACCOUNT=$(sh "$LIB/web-auth.sh" account) || error '500 Internal Server Error' 'Не удалось прочитать настройки входа.'
      respond '200 OK' "$ACCOUNT" ;;
    POST)
      [ "${CONTENT_TYPE:-}" = application/json ] || error '415 Unsupported Media Type' 'Ожидается JSON.'
      case "${CONTENT_LENGTH:-}" in ''|*[!0-9]*) error '400 Bad Request' 'Некорректная длина.' ;; esac
      [ "${#CONTENT_LENGTH}" -le 4 ] && [ "$CONTENT_LENGTH" -le 4096 ] || error '413 Content Too Large' 'Слишком большой запрос.'
      if ACCOUNT=$(head -c "$CONTENT_LENGTH" | sh "$LIB/web-auth.sh" change); then respond '200 OK' "$ACCOUNT"; fi
      printf '%s' "$ACCOUNT" | jq -e '(.error|type=="string")' >/dev/null 2>&1 || error '500 Internal Server Error' 'Не удалось сменить вход.'
      respond '400 Bad Request' "$ACCOUNT" ;;
    *) error '405 Method Not Allowed' 'Метод не поддерживается.' ;;
  esac
fi
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
        CONFIGURED=false; [ ! -s "$CONF/token" ] || [ ! -s "$CONF/server" ] || CONFIGURED=true
        INTERFACE_READY=false
        NATIVE=$(printf '%s' "$SERVICE" | jq -r '.nativeInterface // ""')
        if [ -n "$NATIVE" ] && [ -f "$CONF/native-setup-complete" ] && ip link show dev "$(printf '%s' "$NATIVE" | tr 'A-Z' 'a-z')" >/dev/null 2>&1; then INTERFACE_READY=true; fi
        INTERFACE_NEEDS_SAVE=false; [ ! -f "$CONF/native-setup-unsaved" ] || INTERFACE_NEEDS_SAVE=true
        AUTOSTART=false; [ ! -f "$CONF/autostart" ] || AUTOSTART=true
        OP_STATE=$(sh "$LIB/web-auth.sh" op-state) || error '500 Internal Server Error' 'Не удалось проверить операцию.'
        BUSY=$(printf '%s' "$OP_STATE" | jq -r .busy)
        OPERATION=''; [ ! -f "$LOCK/action" ] || OPERATION=$(cat "$LOCK/action")
        IDENTITY=$(sh "$LIB/client-identity.sh" inspect 2>/dev/null) || IDENTITY='{}'
        RESULT='null'; [ ! -r "$RUN/result.json" ] || RESULT=$(cat "$RUN/result.json")
        respond '200 OK' "$(jq -n --arg status "$TEXT" --arg rss "${RSS:-0}" --arg operation "$OPERATION" \
          --argjson interfaceNeedsSave "$INTERFACE_NEEDS_SAVE" --argjson configured "$CONFIGURED" --argjson interfaceReady "$INTERFACE_READY" --argjson identity "$IDENTITY" --argjson operationState "$OP_STATE" --argjson autostart "$AUTOSTART" --argjson service "$SERVICE" --argjson report "$REPORT" --argjson meta "$META" --argjson busy "$BUSY" --argjson result "$RESULT" \
          '{interfaceNeedsSave:$interfaceNeedsSave,configured:$configured,interfaceReady:$interfaceReady,operationState:$operationState,autostart:$autostart,status:$status,service:$service,report:$report,uiVersion:"0.16.0",clientVersion:($identity.clientVersion // $meta.clientVersion // "неизвестна"),clientIdentityVerified:($identity.clientIdentityVerified//false),clientBuildRevision:$identity.clientBuildRevision,clientSha256:$identity.clientSha256,upstreamVersion:$identity.upstreamVersion,upstreamRevision:$identity.upstreamRevision,revision:($meta.revision // ""),operation:$operation,rssKiB:($rss|tonumber),busy:$busy,result:$result}')"
        ;;
      updates)
        UPDATE=$(sh "$LIB/web-update.sh" status) || error '500 Internal Server Error' 'Не удалось прочитать обновление.'
        respond '200 OK' "$UPDATE" ;;
      settings)
        CAPABILITIES=$("$CTL" get-capabilities) || error '500 Internal Server Error' 'Не удалось прочитать возможности клиента.'
        CONNECTION=$("$CTL" get-settings) || error '500 Internal Server Error' 'Некорректные настройки подключения.'
        FALLBACK=$("$CTL" get-fallback) || error '500 Internal Server Error' 'Некорректные настройки резервных методов.'
        INTERFACE_MTU=$("$CTL" get-interface-mtu 2>/dev/null) || INTERFACE_MTU=null
        POLICY=$("$CTL" get-server-policy) || error '500 Internal Server Error' 'Некорректные списки серверов.'
        SERVERS='{"servers":[]}'
        SERVER=
        CONFIGURED=false
        if [ -s "$CONF/token" ]; then
          SERVERS=$(decode_token < "$CONF/token") || error '500 Internal Server Error' 'Не удалось прочитать серверы из сохранённого токена.'
          SERVER=$(cat "$CONF/server" 2>/dev/null || true)
          [ -z "$SERVER" ] || CONFIGURED=true
        fi
        respond '200 OK' "$(printf '%s' "$SERVERS" | jq --argjson configured "$CONFIGURED" --arg server "$SERVER" --argjson interfaceMtu "$INTERFACE_MTU" --argjson capabilities "$CAPABILITIES" --argjson fallback "$FALLBACK" --argjson connection "$CONNECTION" --argjson policy "$POLICY" \
          '{configured:$configured,interfaceMtu:$interfaceMtu,capabilities:$capabilities,fallback:$fallback,server:$server,servers:[.servers[].name],connection:$connection,policy:$policy}')"
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
    case "$ACTION" in start|stop|restart|check|enable|disable|recover|check-update|update|setup-interface) ;; save)
      printf '%s' "$BODY" | jq -e '(.server|type=="string" and length>0 and length<=128) and
        (.token // ""|type=="string" and length<=262144)' >/dev/null 2>&1 || error '400 Bad Request' 'Некорректный сервер или токен.'
      printf '%s' "$BODY" | jq '.connection' | "$CTL" validate-settings >/dev/null 2>&1 || error '400 Bad Request' 'Проверьте параметры подключения, MTU и SNI.'
      if printf '%s' "$BODY" | jq -e '.connection.ipv6==true' >/dev/null; then
        "$CTL" check-ipv6 >/dev/null 2>&1 || error '400 Bad Request' 'IPv6 NAT недоступен. Нужны ip6tables Entware и поддержка NAT66 в ядре.'
      fi
      if printf '%s' "$BODY" | jq -e 'has("policy")' >/dev/null; then
        printf '%s' "$BODY" | jq '.policy' | "$CTL" validate-server-policy >/dev/null 2>&1 || error '400 Bad Request' 'Проверьте списки серверов.'
      fi
      if printf '%s' "$BODY" | jq -e 'has("fallback")' >/dev/null; then
        if printf '%s' "$BODY" | jq -e 'has("policy")' >/dev/null; then DRAFT_POLICY=$(printf '%s' "$BODY" | jq .policy)
        else DRAFT_POLICY=$("$CTL" get-server-policy); fi
        printf '%s' "$BODY" | jq --argjson policy "$DRAFT_POLICY" '{fallback:.fallback,policy:$policy}' | "$CTL" validate-fallback >/dev/null 2>&1 || error '400 Bad Request' 'Для подбора нужны 1–8 избранных, до двух профилей и до двух корректных SNI.'
      fi
      ;; *) error '400 Bad Request' 'Неизвестное действие.' ;; esac
    if [ "$ACTION" = recover ]; then
      RESERVED=$(FPTN_OWNER_PID=$$ sh "$LIB/web-auth.sh" op-recover) || error '409 Conflict' 'Операция ещё работает или восстановление недоступно.'
    else
      RESERVED=$(printf '%s' "$BODY" | FPTN_OWNER_PID=$$ sh "$LIB/web-auth.sh" op-reserve) || error '409 Conflict' 'Дождитесь операции или восстановите управление в панели.'
    fi
    OP_ID=$(printf '%s' "$RESERVED" | jq -er .id)
    FPTN_OPERATION_ID=$OP_ID REQUEST_METHOD= nohup sh "$SELF" --worker > "$LOCK/worker.log" 2>&1 < /dev/null &
    respond '202 Accepted' '{"accepted":true}'
    ;;
  *) error '405 Method Not Allowed' 'Метод не поддерживается.' ;;
esac
