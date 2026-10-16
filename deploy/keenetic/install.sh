#!/bin/sh
# Интерактивный установщик FPTN-клиента для Keenetic (Entware) / Interactive FPTN Client Installer for Keenetic
# Автор / Author: Antigravity
# Поддержка языков / Language: Русский (RU) & English (EN)

set -e

# Устанавливаем UTF-8 локаль для предотвращения крякозябр в любых SSH-клиентах
export LANG="${LANG:-ru_RU.UTF-8}"
export LC_ALL="${LC_ALL:-ru_RU.UTF-8}"

echo "==========================================================="
echo "  Установка FPTN-клиента и веб-панели для Keenetic (Entware)"
echo "  Installing FPTN Client & Web Panel for Keenetic (Entware)"
echo "==========================================================="
echo ""

# 1. Проверка среды Entware / Check Entware environment
if [ ! -d "/opt/etc" ] || [ ! -x "/opt/bin/opkg" ]; then
    echo "Ошибка: Среда Entware не найдена на роутере! / Error: Entware environment not found!"
    echo "Убедитесь, что Entware установлен и работает (/opt доступен) / Ensure Entware is installed and /opt is mounted."
    exit 1
fi

# После слияния в master установщик и веб-обновление берут исходники из этого форка.
GITHUB_RAW_BASE="https://raw.githubusercontent.com/KOPOHA-MACOHA/fptn-keenetic/master"

# Обработка команды удаления / Uninstall command handler
if [ "$1" = "--uninstall" ] || [ "$1" = "-u" ] || [ "$1" = "uninstall" ]; then
    echo "Запуск процесса удаления FPTN... / Starting FPTN uninstallation..."
    
    KTUN_NAME=""
    FPTN_CONFIG_PRESENT=no
    if [ -f "/opt/etc/fptn-client.conf" ]; then
        FPTN_CONFIG_PRESENT=yes
        . "/opt/etc/fptn-client.conf" 2>/dev/null || true
        if [ -n "$TUN_INTERFACE" ]; then
            KTUN_NAME=$(echo "$TUN_INTERFACE" | sed -E 's/opkgtun([0-9]+)/OpkgTun\1/i')
        fi
    fi

    echo "[1/5] Остановка служб FPTN / Stopping FPTN services..."
    if [ -f "/opt/etc/init.d/S53fptn-client" ]; then
        /opt/etc/init.d/S53fptn-client stop >/dev/null 2>&1 || true
    fi
    pkill -9 -f "fptn-client-cli" >/dev/null 2>&1 || true
    # Старый watchdog мог остаться от предыдущей версии — удаляем его процесс
    # один раз при миграции, но новая установка такой процесс не запускает.
    pkill -9 -f "fptn-watchdog" >/dev/null 2>&1 || true

    echo "[2/5] Удаление бинарников и файлов веб-панели / Removing binary & web files..."
    rm -f /opt/bin/fptn-client-cli /opt/bin/fptn-watchdog.sh /opt/bin/fptn-ndm-setup.sh /opt/etc/fptn-client.conf /opt/etc/init.d/S53fptn-client /opt/etc/fptn-watchdog.sh
    # Restore the pre-FPTN Lighttpd fragment if the installer saved one.
    if [ -f /opt/etc/lighttpd/conf.d/85-fptn.conf.pre-fptn.bak ]; then
        cp -p /opt/etc/lighttpd/conf.d/85-fptn.conf.pre-fptn.bak /opt/etc/lighttpd/conf.d/85-fptn.conf
        rm -f /opt/etc/lighttpd/conf.d/85-fptn.conf.pre-fptn.bak
    else
        rm -f /opt/etc/lighttpd/conf.d/85-fptn.conf
    fi
    rm -rf /opt/share/www/fptn

    echo "[3/5] Обновление конфигурации Lighttpd / Updating Lighttpd..."
    if [ -f "/opt/etc/init.d/S80lighttpd" ]; then
        /opt/etc/init.d/S80lighttpd restart >/dev/null 2>&1 || true
    fi

    echo "[4/5] Очистка старых записей cron / Cleaning legacy crontab entries..."
    # Задание могло быть добавлено и в /opt/etc/crontab, и в пользовательский
    # spool — разные версии установщика делали по-разному.
    if which crontab >/dev/null 2>&1; then
        (crontab -l 2>/dev/null | grep -v "fptn-watchdog" | crontab - 2>/dev/null) || true
    fi
    for _f in /opt/etc/crontab /opt/var/spool/cron/crontabs/root /var/spool/cron/crontabs/root; do
        if [ -f "$_f" ] && grep -q "fptn-watchdog" "$_f"; then
            sed -i '/fptn-watchdog/d' "$_f"
        fi
    done

    if [ -n "$KTUN_NAME" ] && [ "$FPTN_CONFIG_PRESENT" = "yes" ]; then
        echo "[5/5] Проверка интерфейса FPTN $KTUN_NAME / Checking FPTN interface..."
        if command -v ndmc >/dev/null 2>&1; then
            _uninstall_if=$(ndmc -c "show interface $KTUN_NAME" 2>&1 || true)
            if echo "$_uninstall_if" | grep -qi "Fptn"; then
                ndmc -c "no interface $KTUN_NAME" 2>/dev/null || true
                ndmc -c "system configuration save" 2>/dev/null || true
            else
                echo "Интерфейс $KTUN_NAME не помечен как FPTN; оставляю его нетронутым."
                echo "Interface not marked FPTN; leaving it untouched."
            fi
        fi
    else
        echo "[5/5] Конфигурация FPTN не найдена; интерфейсы KeeneticOS не изменяются."
    fi

    echo ""
    echo "==========================================================="
    echo "  Удаление успешно завершено! / Uninstallation finished!"
    echo "  FPTN полностью удален с роутера. / FPTN successfully removed."
    echo "==========================================================="
    exit 0
fi
# Динамическое определение версии релиза из version.txt
REMOTE_VER=$(curl -sL --connect-timeout 5 "${GITHUB_RAW_BASE}/deploy/keenetic/version.txt" 2>/dev/null | tr -d '\r\n')
if [ -z "$REMOTE_VER" ]; then
    REMOTE_VER="v1.3.2-keenetic"
fi

# Универсальная функция скачивания с каскадом прокси-зеркал / Download helper with mirror fallback
download_content_valid() {
    _file="$1"
    [ -s "$_file" ] || return 1
    case "$_file" in
        */index.php)
            head -c 5 "$_file" 2>/dev/null | grep -q '^<?php'
            ;;
        */S53fptn-client)
            head -n 1 "$_file" 2>/dev/null | grep -q '^#!/bin/sh'
            ;;
        *)
            return 0
            ;;
    esac
}

download_file() {
    _url="$1"
    _dest="$2"
    _timeout="${3:-120}"

    rm -f "$_dest"
    if curl -sSL --fail --connect-timeout 10 --max-time "$_timeout" -o "$_dest" "$_url"; then
        if download_content_valid "$_dest"; then return 0; fi
    fi

    # Прокси получают полный исходный URL. Не подставляем зеркало upstream:
    # оно могло бы вернуть старую панель/службу вместо файлов этого форка.
    for _prefix in "https://ghproxy.net/" "https://ghfast.top/"; do
        _mirror_url="${_prefix}${_url}"
        rm -f "$_dest"
        if curl -sSL --fail --connect-timeout 10 --max-time "$_timeout" -o "$_dest" "$_mirror_url"; then
            if download_content_valid "$_dest"; then return 0; fi
        fi
    done
    rm -f "$_dest"
    return 1
}

# Функция проверки занятости порта посторонними сервисами (кроме lighttpd)
is_port_busy() {
    _p="$1"
    _listeners=$(netstat -tulnp 2>/dev/null | grep -E ":${_p}\s" 2>/dev/null || netstat -tuln 2>/dev/null | grep -E ":${_p}\s" 2>/dev/null || true)
    if [ -n "$_listeners" ]; then
        # Если порт занят НЕ lighttpd (например nginx, xray, adguard)
        if echo "$_listeners" | grep -v "lighttpd" >/dev/null 2>&1; then
            return 0
        fi
    fi
    return 1
}

AUTO_ACCEPT="no"
if [ "$1" = "-y" ] || [ "$1" = "--auto" ]; then
    AUTO_ACCEPT="yes"
fi

# Функция считывания ввода пользователя с подстановкой по умолчанию
read_input() {
    _var_name="$1"
    _default_val="$2"
    _res=""
    if [ "$AUTO_ACCEPT" = "yes" ]; then
        _res="$_default_val"
        echo "$_res"
    else
        read -r _res 2>/dev/null || _res=""
        _res=${_res:-$_default_val}
    fi
    eval "$_var_name=\"\$_res\""
}

# 2. Интерактивный опрос параметров / Interactive Configuration Prompt
DEFAULT_PORT=8088
if [ -f "/opt/etc/lighttpd/conf.d/80-nfqws.conf" ]; then
    NFQWS_PORT=$(grep -oE "server.port := [0-9]+" /opt/etc/lighttpd/conf.d/80-nfqws.conf | awk '{print $3}')
    if [ -n "$NFQWS_PORT" ]; then
        DEFAULT_PORT=$NFQWS_PORT
    fi
fi

# Проверяем, свободен ли дефолтный порт
if is_port_busy "$DEFAULT_PORT"; then
    echo "Предупреждение: Порт $DEFAULT_PORT уже занят! / Warning: Port $DEFAULT_PORT is currently busy!"
    CHECK_P=8089
    while is_port_busy "$CHECK_P"; do
        CHECK_P=$((CHECK_P + 1))
    done
    DEFAULT_PORT=$CHECK_P
    echo "Автоматически выбран свободный порт / Auto-selected available port: $DEFAULT_PORT"
fi

printf "Введите порт для веб-панели FPTN / Enter FPTN web panel port (default %s): " "$DEFAULT_PORT"
read_input USER_PORT "$DEFAULT_PORT"

if is_port_busy "$USER_PORT"; then
    echo "ВНИМАНИЕ: Выбранный порт $USER_PORT сейчас занят! / WARNING: Selected port $USER_PORT is busy!"
fi

# Проверяем наличие уже существующего конфига для сохранения настроек при обновлении
PREV_TOKEN=""
PREV_LTUN=""
PREV_TUN_IP=""
if [ -f "/opt/etc/fptn-client.conf" ]; then
    . "/opt/etc/fptn-client.conf" 2>/dev/null || true
    PREV_TOKEN="$TOKEN"
    PREV_LTUN="$TUN_INTERFACE"
    PREV_TUN_IP="$TUN_ADDRESS"
fi

# Адрес туннеля. Одно значение уходит и клиенту (--tun-interface-ip), и ndm:
# ndm транслирует в него трафик локальной сети, клиент в него же переписывает
# получателя входящих пакетов. Разойдутся — обратного трафика не будет.
USER_TUN_IP="${PREV_TUN_IP:-10.0.0.1}"

# Автоподбор свободного имени туннельного интерфейса в KeeneticOS
DEFAULT_KTUN="OpkgTun1"
DEFAULT_LTUN="opkgtun1"

if [ -n "$PREV_LTUN" ]; then
    DEFAULT_LTUN="$PREV_LTUN"
    DEFAULT_KTUN=$(echo "$PREV_LTUN" | sed -E 's/opkgtun([0-9]+)/OpkgTun\1/i')
elif which ndmc >/dev/null 2>&1; then
    TUN_IDX=1
    while [ "$TUN_IDX" -le 10 ]; do
        C_KTUN="OpkgTun${TUN_IDX}"
        C_LTUN="opkgtun${TUN_IDX}"
        # Сообщение об ошибке ndmc пишет в stderr и при этом возвращает 0,
        # поэтому судить можно только по тексту вывода — и stderr обязательно
        # надо забрать в stdout, иначе проверка ничего не увидит.
        # Добавляем || true, чтобы при включенном set -e скрипт не падал при ошибке ndmc.
        IF_INFO=$(ndmc -c "show interface $C_KTUN" 2>&1 || true)
        if [ -z "$IF_INFO" ] || echo "$IF_INFO" | grep -qiE "error|no such|not found"; then
            # Интерфейс свободен
            DEFAULT_KTUN=$C_KTUN
            DEFAULT_LTUN=$C_LTUN
            break
        elif echo "$IF_INFO" | grep -qi "Fptn"; then
            # Этот интерфейс уже принадлежит FPTN
            DEFAULT_KTUN=$C_KTUN
            DEFAULT_LTUN=$C_LTUN
            break
        fi
        TUN_IDX=$((TUN_IDX + 1))
    done
fi

USER_KTUN="$DEFAULT_KTUN"
USER_LTUN="$DEFAULT_LTUN"

if [ -n "$PREV_TOKEN" ]; then
    printf "Найден токен [%s...]. Нажмите Enter для сохранения или введите новый / Existing token found [%s...]. Press Enter to keep or type new: " "$(echo "$PREV_TOKEN" | cut -c 1-12)" "$(echo "$PREV_TOKEN" | cut -c 1-12)"
    read_input USER_TOKEN "$PREV_TOKEN"
else
    printf "Введите токен подписки FPTN (опционально) / Enter FPTN subscription token (optional): "
    read_input USER_TOKEN ""
fi

echo ""
echo "Параметры установки / Installation Parameters:"
echo "  Веб-порт / Web Port:               $USER_PORT"
echo "  Интерфейс KeeneticOS / OS Interface: $USER_KTUN ($USER_LTUN)"
if [ -n "$USER_TOKEN" ]; then
    echo "  Токен подписки / Subscription Token: [Указан / Specified]"
else
    echo "  Токен подписки / Subscription Token: [Не указан / Not specified]"
fi
echo ""
printf "Продолжить установку? / Continue installation? (y/n, default y): "
read_input CONFIRM "y"
if [ "$CONFIRM" != "y" ] && [ "$CONFIRM" != "Y" ]; then
    echo "Установка отменена / Installation cancelled."
    exit 0
fi

# Safety preflight: never reconfigure a Keenetic interface that belongs to another service.
# An existing interface is reusable only when its description identifies it as FPTN.
if command -v ndmc >/dev/null 2>&1; then
    _preflight_if=$(ndmc -c "show interface $USER_KTUN" 2>&1 || true)
    if [ -n "$_preflight_if" ] &&
       ! echo "$_preflight_if" | grep -qiE "error|no such|not found" &&
       ! echo "$_preflight_if" | grep -qi "Fptn"; then
        echo "ОШИБКА: интерфейс $USER_KTUN уже существует и не помечен как FPTN."
        echo "Ничего не изменено. Выберите другое имя интерфейса и повторите установку."
        echo "ERROR: $USER_KTUN already exists and is not marked as FPTN; refusing to reconfigure it."
        exit 1
    fi
fi

# FPTN routing depends on Keenetic's ndmc CLI. Fail before opkg changes packages.
if ! command -v ndmc >/dev/null 2>&1; then
    echo "ОШИБКА: ndmc не найден. Это установщик для KeeneticOS; установка отменена до изменения Entware."
    echo "ERROR: ndmc is missing; aborting before modifying Entware packages."
    exit 1
fi

# Do not silently reuse a non-FPTN interface; the earlier check should have
# rejected it, but fail closed if the command output is ambiguous.
if [ -z "$_preflight_if" ]; then
    _preflight_if=$(ndmc -c "show interface $USER_KTUN" 2>&1 || true)
fi
if [ -n "$_preflight_if" ] &&
   ! echo "$_preflight_if" | grep -qiE "error|no such|not found" &&
   ! echo "$_preflight_if" | grep -qi "Fptn"; then
    echo "ОШИБКА: не удалось безопасно подтвердить принадлежность $USER_KTUN к FPTN."
    echo "Установка остановлена до изменений Entware."
    exit 1
fi

# Check storage before opkg can install PHP/Lighttpd and their dependencies.
_FREE_KB=$(df -Pk /opt 2>/dev/null | awk 'NR == 2 {print $4}')
case "$_FREE_KB" in
    ''|*[!0-9]*)
        echo "Предупреждение: не удалось проверить свободное место в /opt."
        ;;
    *)
        if [ "$_FREE_KB" -lt 20480 ]; then
            echo "ОШИБКА: в /opt меньше 20 MiB свободного места ($_FREE_KB KiB)."
            echo "Освободите место перед установкой; пакеты Entware пока не изменялись."
            exit 1
        fi
        ;;
esac

# 3. Установка необходимых системных пакетов / Install Entware packages
echo ""
echo "[1/7] Обновление пакетов и установка зависимостей / Updating packages & dependencies..."
opkg update || true
opkg install lighttpd lighttpd-mod-cgi php8-cgi php8-mod-openssl php8-mod-session libxml2 procps-ng-pgrep procps-ng-pkill curl ca-bundle ca-certificates

# 4. Автоопределение архитектуры и скачивание бинарника fptn-client-cli / Detect CPU & Download Binary
echo ""
echo "[2/7] Определение архитектуры процессора / Detecting CPU architecture..."
RAW_ARCH=$(uname -m)
case "$RAW_ARCH" in
    aarch64)
        ARCH_SUFFIX="aarch64"
        ;;
    armv7*)
        ARCH_SUFFIX="armv7"
        ;;
    mips|mipsel|mips*el)
        # uname -m на части Keenetic возвращает просто "mips", хотя Entware
        # использует mipsel. Сначала учитываем явно настроенный soft-float feed.
        ARCH_SUFFIX="mipsel"
        if grep -Rqs 'mipselsf' /opt/etc/opkg.conf /opt/etc/opkg/*.conf 2>/dev/null; then # глушение-обосновано: часть файлов opkg может отсутствовать
            ARCH_SUFFIX="mipselsf"
        fi
        ;;
    *)
        echo "Ошибка: Неподдерживаемая архитектура / Error: Unsupported CPU architecture: $RAW_ARCH"
        exit 1
        ;;
esac

echo "Архитектура процессора / CPU Architecture: $RAW_ARCH ($ARCH_SUFFIX)"

# Не останавливаем службу и не удаляем рабочий бинарник до проверки загрузки.
# После проверки mv заменит файл атомарно; при ошибке старый бинарник останется.
# Проверка валидности бинарного файла (не менее 1 МБ и отсутствие HTML ошибок)
check_binary_valid() {
    _f="$1"
    if [ ! -s "$_f" ]; then return 1; fi
    _sz=$(wc -c < "$_f" 2>/dev/null || echo 0)
    if [ "$_sz" -lt 1000000 ]; then return 1; fi
    # Проверяем ELF magic, а не только размер: HTML/текстовая ошибка может быть большой.
    _magic=$(od -An -tx1 -N4 "$_f" 2>/dev/null | tr -d ' \n')
    if [ "$_magic" != "7f454c46" ]; then return 1; fi
    if head -n 1 "$_f" | grep -qi "Not Found\|DOCTYPE\|404\|error"; then return 1; fi
    return 0
}

echo "Скачивание скомпилированного бинарника / Downloading compiled binary..."
BIN_RAW_URL="${GITHUB_RAW_BASE}/deploy/keenetic/bin/fptn-client-cli-${ARCH_SUFFIX}"
BIN_RELEASE_URL="https://github.com/AntikFull/fptn-keenetic/releases/download/${REMOTE_VER}/fptn-client-cli-${ARCH_SUFFIX}"
BIN_LATEST_URL="https://github.com/AntikFull/fptn-keenetic/releases/latest/download/fptn-client-cli-${ARCH_SUFFIX}"

DOWNLOAD_SUCCESS=no
for _src_url in "$BIN_RAW_URL" "$BIN_RELEASE_URL" "$BIN_LATEST_URL"; do
    if download_file "$_src_url" "/opt/bin/fptn-client-cli.tmp" 180; then
        if check_binary_valid "/opt/bin/fptn-client-cli.tmp"; then
            chmod +x "/opt/bin/fptn-client-cli.tmp"
            # Сначала проверяем именно временный файл. Рабочий бинарник не трогаем,
            # пока новая версия не прошла хотя бы один smoke test.
            _smoke_code=0
            _smoke_output=$("/opt/bin/fptn-client-cli.tmp" --help 2>&1) || _smoke_code=$?
            if [ "$_smoke_code" -eq 0 ]; then
                _smoke_ok=yes
            elif [ "$_smoke_code" -ge 126 ] || echo "$_smoke_output" | grep -Eqi "not found|exec format error|no such file|error loading shared library|cannot execute"; then
                _smoke_ok=no
            else
                # Некоторые CLI возвращают ненулевой код на --help; это само по себе
                # не означает, что бинарник не запускается.
                _smoke_ok=yes
                echo "Предупреждение: --help вернул код $_smoke_code без явной ошибки загрузчика. / Warning: --help returned $_smoke_code without a loader error."
            fi
            if [ "$_smoke_ok" != "yes" ] && [ -r /opt/lib/ld.so.1 ]; then
                _smoke_code=0
                _smoke_output=$(/opt/lib/ld.so.1 --library-path /opt/lib /opt/bin/fptn-client-cli.tmp --help 2>&1) || _smoke_code=$?
                if [ "$_smoke_code" -eq 0 ] || { [ "$_smoke_code" -lt 126 ] && ! echo "$_smoke_output" | grep -Eqi "not found|exec format error|no such file|error loading shared library|cannot execute"; }; then
                    _smoke_ok=yes
                fi
            fi
            if [ "$_smoke_ok" = "yes" ]; then
                # До завершения всех проверок не останавливаем текущий туннель
                # и не заменяем рабочий бинарник.
                DOWNLOAD_SUCCESS=yes
                break
            else
                echo "Бинарник не прошёл smoke test; текущая версия сохранена. / Binary failed smoke test; current version preserved."
                rm -f "/opt/bin/fptn-client-cli.tmp"
            fi
        else
            rm -f "/opt/bin/fptn-client-cli.tmp"
        fi
    fi
done

if [ "$DOWNLOAD_SUCCESS" != "yes" ]; then
    rm -f "/opt/bin/fptn-client-cli.tmp"
    echo "Ошибка: Не удалось скачать и запустить проверенный бинарный файл. Существующий бинарник не изменён. / Error: Failed to download and run a validated binary. Existing binary was preserved."
    exit 1
fi

# Только теперь останавливаем текущую службу и меняем бинарник. Если замена
# не удастся, резервная копия останется доступной, а старую службу попробуем поднять.
if [ -f "/opt/etc/init.d/S53fptn-client" ]; then
    /opt/etc/init.d/S53fptn-client stop >/dev/null 2>&1 || true
fi
if [ -f "/opt/bin/fptn-client-cli" ]; then
    cp -p /opt/bin/fptn-client-cli /opt/bin/fptn-client-cli.pre-update.bak || {
        echo "Ошибка: Не удалось создать резервную копию текущего бинарника; обновление отменено."
        [ ! -x /opt/etc/init.d/S53fptn-client ] || /opt/etc/init.d/S53fptn-client start >/dev/null 2>&1 || true
        exit 1
    }
fi
if ! mv "/opt/bin/fptn-client-cli.tmp" "/opt/bin/fptn-client-cli"; then
    echo "Ошибка: Не удалось заменить бинарник; текущая версия сохранена."
    [ ! -x /opt/etc/init.d/S53fptn-client ] || /opt/etc/init.d/S53fptn-client start >/dev/null 2>&1 || true
    exit 1
fi
chmod +x /opt/bin/fptn-client-cli
echo "$REMOTE_VER" > /opt/etc/fptn-version

# 5. Создание и настройка TUN-интерфейса в KeeneticOS / Register TUN Interface in KeeneticOS
echo ""
echo "[3/7] Регистрация интерфейса $USER_KTUN в KeeneticOS / Registering $USER_KTUN in KeeneticOS..."
if ! which ndmc >/dev/null 2>&1; then
    echo "Внимание: Утилита ndmc CLI не найдена / Warning: ndmc CLI not found. Skip interface setup."
else
    # ndmc возвращает 0 и для несуществующего интерфейса, а текст ошибки пишет
    # в stderr. Прежняя проверка по коду возврата всегда уходила в ветку «уже
    # существует», и интерфейс не создавался.
    # Добавляем || true, чтобы при включенном set -e скрипт не падал при ошибке ndmc.
    EXISTING_IF=$(ndmc -c "show interface $USER_KTUN" 2>&1 || true)
    if [ -n "$EXISTING_IF" ] && ! echo "$EXISTING_IF" | grep -qiE "error|no such|not found"; then
        echo "Интерфейс $USER_KTUN уже существует / Interface $USER_KTUN already exists."
    else
        echo "Создание интерфейса $USER_KTUN типа OpkgTun / Creating OpkgTun interface $USER_KTUN..."
        ndmc -c "interface $USER_KTUN" 2>/dev/null || ndmc -c "interface $USER_KTUN type OpkgTun" 2>/dev/null || true
    fi

    ndmc -c "interface $USER_KTUN description Fptn" 2>/dev/null || true
    ndmc -c "interface $USER_KTUN security-level public" 2>/dev/null || true
    # Адрес и маска должны в точности совпадать с тем, что назначает сам клиент
    # (TUN_ADDRESS в fptn-client.conf передаётся ему как --tun-interface-ip,
    # маска /32). При расхождении ndm уходит в цикл переконфигурации интерфейса,
    # а conntrack перестаёт сопоставлять обратный трафик.
    ndmc -c "interface $USER_KTUN ip address $USER_TUN_IP 255.255.255.255" 2>/dev/null || true
    ndmc -c "interface $USER_KTUN ip mtu 1360" 2>/dev/null || true
    ndmc -c "interface $USER_KTUN ip global 50000" 2>/dev/null || true
    # Подгонку MSS выполняет ndm; правила iptables TCPMSS для этого не нужны
    ndmc -c "interface $USER_KTUN ip tcp adjust-mss pmtu" 2>/dev/null || true
    ndmc -c "access-list _WEBADMIN_$USER_KTUN permit ip 0.0.0.0 0.0.0.0 0.0.0.0 0.0.0.0" >/dev/null 2>&1 || true
    ndmc -c "interface $USER_KTUN ip access-group _WEBADMIN_$USER_KTUN in" >/dev/null 2>&1 || true
    ndmc -c "access-list _WEBADMIN_$USER_KTUN auto-delete" >/dev/null 2>&1 || true
    ndmc -c "interface $USER_KTUN up" 2>/dev/null || true
    ndmc -c "system configuration save" >/dev/null 2>&1 || true
    echo "Интерфейс $USER_KTUN успешно настроен / Interface $USER_KTUN successfully configured."
fi

# 6. Настройка веб-сервера Lighttpd / Configure Lighttpd
echo ""
echo "[4/7] Настройка конфигурации Lighttpd / Configuring Lighttpd web server..."
LIGHTTPD_CONF_DIR="/opt/etc/lighttpd/conf.d"
mkdir -p "$LIGHTTPD_CONF_DIR"
FPTN_LIGHTTPD_CONF="$LIGHTTPD_CONF_DIR/85-fptn.conf"
FPTN_LIGHTTPD_BACKUP="$FPTN_LIGHTTPD_CONF.pre-fptn.bak"
FPTN_LIGHTTPD_HAD_CONFIG=no
if [ -f "$FPTN_LIGHTTPD_CONF" ]; then
    # Keep a one-time copy of a pre-existing FPTN config; never overwrite this backup.
    if [ ! -f "$FPTN_LIGHTTPD_BACKUP" ]; then
        cp -p "$FPTN_LIGHTTPD_CONF" "$FPTN_LIGHTTPD_BACKUP" || {
            echo "Ошибка: не удалось сохранить прежний конфиг $FPTN_LIGHTTPD_CONF; отмена."
            exit 1
        }
    fi
    FPTN_LIGHTTPD_HAD_CONFIG=yes
fi

# Проверяем, задан ли уже server.port в существующих конфигах Lighttpd
SERVER_PORT_DEFINED=no
for _cf in /opt/etc/lighttpd/lighttpd.conf /opt/etc/lighttpd/conf.d/*.conf; do
    if [ -f "$_cf" ] && [ "$_cf" != "$LIGHTTPD_CONF_DIR/85-fptn.conf" ]; then
        if grep -oE "^\s*server\.port\s*[:=]" "$_cf" >/dev/null 2>&1; then
            SERVER_PORT_DEFINED=yes
            break
        fi
    fi
done

if [ "$SERVER_PORT_DEFINED" = "yes" ]; then
    cat << EOF > "$LIGHTTPD_CONF_DIR/85-fptn.conf"
# Настройки веб-интерфейса FPTN
server.modules += ( "mod_cgi" )
\$HTTP["url"] =~ "^/fptn/" {
    cgi.assign = ( ".php" => "/opt/bin/php-cgi" )
    static-file.exclude-extensions += ( ".php" )
}
EOF
else
    cat << EOF > "$LIGHTTPD_CONF_DIR/85-fptn.conf"
# Настройки веб-интерфейса FPTN на порту $USER_PORT
server.port = $USER_PORT
server.modules += ( "mod_cgi" )
\$HTTP["url"] =~ "^/fptn/" {
    cgi.assign = ( ".php" => "/opt/bin/php-cgi" )
    static-file.exclude-extensions += ( ".php" )
}
EOF
fi

# Validate the complete config before restarting a shared web server.
# If invalid, restore the previous FPTN snippet (or remove the new one) and abort.
if ! lighttpd -tt -f /opt/etc/lighttpd/lighttpd.conf >/dev/null 2>&1; then
    echo "ОШИБКА: конфигурация Lighttpd не прошла проверку; перезапуск отменён."
    if [ "$FPTN_LIGHTTPD_HAD_CONFIG" = "yes" ] && [ -f "$FPTN_LIGHTTPD_BACKUP" ]; then
        cp -p "$FPTN_LIGHTTPD_BACKUP" "$FPTN_LIGHTTPD_CONF"
    else
        rm -f "$FPTN_LIGHTTPD_CONF"
    fi
    echo "Проверьте конфиги Lighttpd вручную. Остальные шаги установщика могли уже выполниться."
    exit 1
fi

echo "Перезапуск Lighttpd / Restarting Lighttpd..."
if ! /opt/etc/init.d/S80lighttpd restart; then
    echo "ОШИБКА: Lighttpd не перезапустился."
    if [ "$FPTN_LIGHTTPD_HAD_CONFIG" = "yes" ] && [ -f "$FPTN_LIGHTTPD_BACKUP" ]; then
        cp -p "$FPTN_LIGHTTPD_BACKUP" "$FPTN_LIGHTTPD_CONF"
    else
        rm -f "$FPTN_LIGHTTPD_CONF"
    fi
    /opt/etc/init.d/S80lighttpd restart >/dev/null 2>&1 || true
    echo "Прежний фрагмент конфигурации восстановлен, но установку нужно проверить вручную."
    exit 1
fi

# 7. Установка файлов веб-панели / Install Web Panel
echo ""
echo "[5/7] Копирование файлов веб-панели / Copying web panel files..."
WWW_DIR="/opt/share/www/fptn"
mkdir -p "$WWW_DIR"

SCRIPT_DIR=$(dirname "$0")
if [ -f "$SCRIPT_DIR/index.php" ]; then
    cp "$SCRIPT_DIR/index.php" "$WWW_DIR/index.php"
else
    if ! download_file "${GITHUB_RAW_BASE}/deploy/keenetic/index.php" "$WWW_DIR/index.php" 30; then
        echo "Ошибка: Не удалось скачать index.php / Error: Failed to download index.php"
        exit 1
    fi
fi
chmod 644 "$WWW_DIR/index.php"

# 8. Создание/Обновление файла конфигурации FPTN службы / Save Configuration
echo ""
echo "[6/8] Сохранение конфигурации FPTN / Saving FPTN configuration..."
CONF_PATH="/opt/etc/fptn-client.conf"
CONF_ENABLED="no"
CONF_SERVERS=""
CONF_PASS=""
CONF_TUN_IPV6=""
CONF_TUN_MTU="1360"
CONF_GATEWAY_IP=""
CONF_GATEWAY_IPV6=""
CONF_OUT_IFACE=""
CONF_SNI="rutube.ru"
CONF_BYPASS="sni-spoofing"
CONF_BLACKLIST=""
CONF_EXCLUDE="10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"
CONF_INCLUDE=""
CONF_SPLIT="no"
CONF_SPLIT_MODE="exclude"
CONF_SPLIT_DOMAINS=""
CONF_DISABLE_FALLBACK="no"
CONF_MAX_RESTARTS="15"
CONF_STARTUP_DELAY="5"

if [ -f "$CONF_PATH" ]; then
    . "$CONF_PATH" 2>/dev/null || true
    CONF_ENABLED=$(grep -E "^ENABLED=" "$CONF_PATH" | cut -d'=' -f2- | tr -d "\"'" || echo "no")
    CONF_SERVERS=$(grep -E "^PREFERRED_SERVER=" "$CONF_PATH" | cut -d'=' -f2- | tr -d "\"'" || echo "")
    CONF_PASS=$(grep -E "^WEB_PASSWORD=" "$CONF_PATH" | cut -d'=' -f2- | tr -d "\"'" || echo "")
    CONF_TUN_IPV6="${TUN_IPV6_ADDRESS:-}"
    CONF_TUN_MTU="${TUN_MTU:-1360}"
    CONF_GATEWAY_IP="${GATEWAY_IP:-}"
    CONF_GATEWAY_IPV6="${GATEWAY_IPV6:-}"
    CONF_OUT_IFACE="${OUT_NETWORK_INTERFACE:-}"
    CONF_SNI="${SERVER_SNI:-rutube.ru}"
    CONF_BYPASS="${BYPASS_METHOD:-sni-spoofing}"
    CONF_BLACKLIST="${BLACKLIST_DOMAINS:-}"
    CONF_EXCLUDE="${EXCLUDE_TUNNEL_NETWORKS:-10.0.0.0/8,172.16.0.0/12,192.168.0.0/16}"
    CONF_INCLUDE="${INCLUDE_TUNNEL_NETWORKS:-}"
    CONF_SPLIT="${SPLIT_TUNNEL:-no}"
    CONF_SPLIT_MODE="${SPLIT_TUNNEL_MODE:-exclude}"
    CONF_SPLIT_DOMAINS="${SPLIT_TUNNEL_DOMAINS:-}"
    CONF_DISABLE_FALLBACK="${DISABLE_AUTO_FALLBACK:-no}"
    CONF_MAX_RESTARTS="${MAX_FULL_RESTARTS:-15}"
    CONF_STARTUP_DELAY="${STARTUP_RETRY_DELAY:-5}"
fi

cat << EOF > "$CONF_PATH"
# Конфигурация клиента FPTN (Создано автоматически / Auto-generated)
ENABLED="${CONF_ENABLED:-no}"
TOKEN="$USER_TOKEN"
PREFERRED_SERVER="${CONF_SERVERS}"
TUN_INTERFACE="$USER_LTUN"
# Должно совпадать с адресом, назначенным интерфейсу в KeeneticOS (шаг [3/7]).
TUN_ADDRESS="$USER_TUN_IP"
TUN_IPV6_ADDRESS="${CONF_TUN_IPV6}"
TUN_MTU="${CONF_TUN_MTU}"
GATEWAY_IP="${CONF_GATEWAY_IP}"
GATEWAY_IPV6="${CONF_GATEWAY_IPV6}"
OUT_NETWORK_INTERFACE="${CONF_OUT_IFACE}"
SERVER_SNI="${CONF_SNI}"
BYPASS_METHOD="${CONF_BYPASS}"
BLACKLIST_DOMAINS="${CONF_BLACKLIST}"
EXCLUDE_TUNNEL_NETWORKS="${CONF_EXCLUDE}"
INCLUDE_TUNNEL_NETWORKS="${CONF_INCLUDE}"
SPLIT_TUNNEL="${CONF_SPLIT}"
SPLIT_TUNNEL_MODE="${CONF_SPLIT_MODE}"
SPLIT_TUNNEL_DOMAINS="${CONF_SPLIT_DOMAINS}"
DISABLE_AUTO_FALLBACK="${CONF_DISABLE_FALLBACK}"
MAX_FULL_RESTARTS="${CONF_MAX_RESTARTS}"
STARTUP_RETRY_DELAY="${CONF_STARTUP_DELAY}"
WEB_PASSWORD="${CONF_PASS}"
EOF
chmod 600 "$CONF_PATH"

# Генерация списка серверов, если токен был указан при установке
if [ -n "$USER_TOKEN" ]; then
    echo "Формирование списка серверов из токена / Generating server list from token..."
    if [ -x "/opt/bin/fptn-client-cli" ]; then
        /opt/bin/fptn-client-cli --access-token "$USER_TOKEN" --show-servers > /opt/etc/fptn-servers.json 2>/dev/null || true  # глушение-обосновано: первичный разбор токена может вернуть ошибку при невалидном токене
    fi
    if [ ! -s "/opt/etc/fptn-servers.json" ]; then
        # Резервный разбор через PHP CGI
        /opt/bin/php-cgi -q -r '
            $token = trim($argv[1] ?? "");
            $clean = preg_replace("/^(fptn:\/\/|fptn:)/i", "", $token);
            $clean = preg_replace("/[\s\r\n\t=]+/", "", $clean);
            $dec = base64_decode($clean, true);
            if ($dec) {
                $j = json_decode($dec, true);
                if (!empty($j["servers"])) {
                    file_put_contents("/opt/etc/fptn-servers.json", json_encode($j, JSON_PRETTY_PRINT | JSON_UNESCAPED_UNICODE));
                }
            }
        ' -- "$USER_TOKEN" 2>/dev/null || true  # глушение-обосновано: резервный парсер в случае отсутствия PHP модулей
    fi
fi

# Повторная настройка интерфейса здесь удалена намеренно.
# Ранее этот блок назначал 172.20.0.2/255.255.0.0 и ip defaultgateway 172.20.0.1
# поверх уже настроенного выше 10.0.0.1/255.255.255.255. Три разных адреса с
# разными масками на одном интерфейсе (и шлюз, которого не существует)
# заставляли ndm непрерывно сверять свою конфигурацию с ядром — отсюда
# постоянная загрузка процессора, флапающие маршруты и зависание веб-морды.
# Интерфейс настраивается ровно один раз, в шаге [3/7].

# 9. Установка init-скрипта автозапуска службы / Install Init Script
echo ""
echo "[7/8] Настройка службы автозапуска / Setting up auto-start service..."

if [ -f "$SCRIPT_DIR/S53fptn-client" ]; then
    cp "$SCRIPT_DIR/S53fptn-client" "/opt/etc/init.d/S53fptn-client"
else
    if ! download_file "${GITHUB_RAW_BASE}/deploy/keenetic/S53fptn-client" "/opt/etc/init.d/S53fptn-client" 30; then
        echo "Ошибка: Не удалось скачать init-скрипт / Error: Failed to download init script"
        exit 1
    fi
fi
chmod 755 /opt/etc/init.d/S53fptn-client

# 8. Восстановление выполняется встроенным supervisor в fptn-client-cli.
echo ""
echo "[8/8] Внешний watchdog и cron не устанавливаются: supervisor встроен в клиент."

ROUTER_LAN_IP=""
if which ndmc >/dev/null 2>&1; then
    _home_info=$(ndmc -c "show interface Home" 2>&1 || true)
    ROUTER_LAN_IP=$(echo "$_home_info" | grep -i "address:" | awk '{print $2}' | head -n 1)
fi
if [ -z "$ROUTER_LAN_IP" ]; then
    ROUTER_LAN_IP=$(ip -4 addr show dev br0 2>/dev/null | grep -oE 'inet [0-9.]+' | awk '{print $2}' | head -n 1) # глушение-обосновано: интерфейса br0 может не быть на нестандартных прошивках
fi
if [ -z "$ROUTER_LAN_IP" ]; then
    ROUTER_LAN_IP="192.168.1.1"
fi

echo ""
echo "==========================================================="
echo "     Установка успешно завершена / Installation Finished!"
echo "==========================================================="
echo ""
echo "  1. Веб-панель доступна по адресу / Web panel URL:"
echo "     http://${ROUTER_LAN_IP}:$USER_PORT/fptn/"
echo ""
echo "  2. Запуск туннеля / How to start VPN:"
echo "     - Откройте веб-панель / Open FPTN Web Panel."
echo "     - Проверьте сервер и нажмите 'Запустить' / Click 'Start'."
echo ""
echo "  3. Маршрутизация трафика / Traffic Routing:"
echo "     - В Keenetic Web UI в разделе 'Приоритеты подключений'"
echo "       появится подключение '$USER_KTUN'."
echo "     - Перетащите '$USER_KTUN' в нужную политику маршрутизации."
echo "     - Drag connection '$USER_KTUN' into target routing policy."
echo "==========================================================="
