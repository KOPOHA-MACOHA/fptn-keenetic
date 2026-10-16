#!/bin/sh
# Интерактивный установщик FPTN-клиента для Keenetic (Entware) / Interactive FPTN Client Installer for Keenetic
# Use --no-web-panel for a client-only install that does not install/restart Lighttpd or PHP.
# Автор / Author: Antigravity
# Поддержка языков / Language: Русский (RU) & English (EN)

set -e

# Устанавливаем UTF-8 локаль для предотвращения крякозябр в любых SSH-клиентах
export LANG="${LANG:-ru_RU.UTF-8}"
export LC_ALL="${LC_ALL:-ru_RU.UTF-8}"

AUTO_ACCEPT="no"
INSTALL_WEB_PANEL="yes"
for _arg in "$@"; do
    case "$_arg" in
        -y|--auto) AUTO_ACCEPT="yes" ;;
        --no-web-panel) INSTALL_WEB_PANEL="no" ;;
    esac
done

echo "==========================================================="
echo "  Установка FPTN-клиента для Keenetic (Entware)"
echo "  Installing FPTN Client for Keenetic (Entware)"
if [ "$INSTALL_WEB_PANEL" = "no" ]; then
    echo "  Режим без веб-панели / Client-only mode"
fi
echo "==========================================================="
echo ""

# 1. Проверка среды Entware / Check Entware environment
if [ ! -d "/opt/etc" ] || [ ! -x "/opt/bin/opkg" ]; then
    echo "Ошибка: Среда Entware не найдена на роутере! / Error: Entware environment not found!"
    echo "Убедитесь, что Entware установлен и работает (/opt доступен) / Ensure Entware is installed and /opt is mounted."
    exit 1
fi

# После слияния в master установщик и веб-обновление берут исходники из этого форка.
# Override with an immutable reviewed commit when testing a PR before merge.
GITHUB_RAW_BASE="${FPTN_GITHUB_RAW_BASE:-https://raw.githubusercontent.com/KOPOHA-MACOHA/fptn-keenetic/master}"

# Обработка команды удаления / Uninstall command handler
if [ "$1" = "--uninstall" ] || [ "$1" = "-u" ] || [ "$1" = "uninstall" ]; then
    echo "Запуск процесса удаления FPTN... / Starting FPTN uninstallation..."
    
    KTUN_NAME=""
    FPTN_CONFIG_PRESENT=no
    if [ -f "/opt/etc/fptn-client.conf" ]; then
        FPTN_CONFIG_PRESENT=yes
        TUN_INTERFACE=""
        . "/opt/etc/fptn-client.conf" 2>/dev/null || true
        if [ -n "$TUN_INTERFACE" ]; then
            KTUN_NAME=$(echo "$TUN_INTERFACE" | sed -E 's/opkgtun([0-9]+)/OpkgTun\1/i')
        fi
    fi

    FPTN_PANEL_PRESENT=no
    if [ -f "/opt/etc/fptn-web-panel.installed" ] || [ -f "/opt/etc/lighttpd/conf.d/85-fptn.conf" ]; then
        FPTN_PANEL_PRESENT=yes
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
    if [ "$FPTN_PANEL_PRESENT" = "yes" ] && [ -x "/opt/etc/init.d/S80lighttpd" ]; then
        /opt/etc/init.d/S80lighttpd restart >/dev/null 2>&1 || true
    else
        echo "Панель FPTN не обнаружена; Lighttpd не перезапускается."
    fi
    rm -f /opt/etc/fptn-web-panel.installed

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

# Return busy if ANY process already listens on the requested TCP/UDP port.
# Do not ignore Lighttpd: it may already own a socket on this port.
is_port_busy() {
    _p="$1"
    _listeners=$(netstat -tuln 2>/dev/null | grep -E "[:.]\${_p}[[:space:]]" 2>/dev/null || true)
    [ -n "$_listeners" ]
}

# Функция считывания ввода пользователя с подстановкой по умолчанию
read_input() {
    # Return the answer through REPLY instead of eval: user-entered tokens must
    # never be reparsed as shell code.
    _default_val="$2"
    if [ "$AUTO_ACCEPT" = "yes" ]; then
        REPLY="$_default_val"
        printf '%s\n' "$REPLY"
    else
        REPLY=""
        read -r REPLY 2>/dev/null || REPLY=""
        REPLY=${REPLY:-$_default_val}
    fi
}

# 2. Select a dedicated, memorable web-panel port. Existing Lighttpd
# services keep their current listener; FPTN gets an additional socket.
USER_PORT=""
if [ "$INSTALL_WEB_PANEL" = "yes" ]; then
    DEFAULT_PORT=8080
    if is_port_busy "$DEFAULT_PORT" ||
       grep -R -E '(:|[[:space:]])8080([[:space:]]|[;}]|$)' /opt/etc/lighttpd/lighttpd.conf /opt/etc/lighttpd/conf.d 2>/dev/null | grep -v '^[[:space:]]*#' >/dev/null 2>&1; then
        echo "Порт 8080 уже занят или упомянут в конфигурации Lighttpd."
        CHECK_P=8081
        while is_port_busy "$CHECK_P" ||
              grep -R -E "(:|[[:space:]])${CHECK_P}([[:space:]]|[;}]|$)" /opt/etc/lighttpd/lighttpd.conf /opt/etc/lighttpd/conf.d 2>/dev/null | grep -v '^[[:space:]]*#' >/dev/null 2>&1; do
            CHECK_P=$((CHECK_P + 1))
            [ "$CHECK_P" -le 65535 ] || { echo "Не найден свободный порт."; exit 1; }
        done
        DEFAULT_PORT=$CHECK_P
        echo "Выбран свободный порт: $DEFAULT_PORT"
    fi

    printf "Порт веб-панели FPTN (default %s): " "$DEFAULT_PORT"
    read_input USER_PORT "$DEFAULT_PORT"
    USER_PORT="$REPLY"

    case "$USER_PORT" in
        ''|*[!0-9]*)
            echo "Ошибка: порт должен быть числом от 1 до 65535. Установка отменена."
            exit 1
            ;;
    esac
    if [ "$USER_PORT" -lt 1 ] || [ "$USER_PORT" -gt 65535 ]; then
        echo "Ошибка: порт должен быть в диапазоне 1-65535. Установка отменена."
        exit 1
    fi

    if is_port_busy "$USER_PORT"; then
        echo "ОШИБКА: порт $USER_PORT занят. Выберите свободный порт; установка отменена."
        exit 1
    fi
    if grep -R -E "(:|[[:space:]])${USER_PORT}([[:space:]]|[;}]|$)" /opt/etc/lighttpd/lighttpd.conf /opt/etc/lighttpd/conf.d 2>/dev/null | grep -v '^[[:space:]]*#' >/dev/null 2>&1; then
        echo "ОШИБКА: порт $USER_PORT уже упоминается в конфигурации Lighttpd. Выберите другой; установка отменена."
        exit 1
    fi
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
    USER_TOKEN="$REPLY"
else
    printf "Введите токен подписки FPTN (опционально) / Enter FPTN subscription token (optional): "
    read_input USER_TOKEN ""
    USER_TOKEN="$REPLY"
fi

if [ "$INSTALL_WEB_PANEL" = "no" ] && [ -z "$USER_TOKEN" ]; then
    echo "ОШИБКА: режим без веб-панели требует токен подписки FPTN."
    echo "ERROR: client-only mode requires an FPTN subscription token."
    exit 1
fi

echo ""
echo "Параметры установки / Installation Parameters:"
if [ "$INSTALL_WEB_PANEL" = "yes" ]; then
    echo "  Веб-порт / Web Port:               $USER_PORT (отдельный listener / dedicated listener)"
else
    echo "  Веб-панель не устанавливается / Web panel disabled"
fi
echo "  Интерфейс KeeneticOS / OS Interface: $USER_KTUN ($USER_LTUN)"
if [ -n "$USER_TOKEN" ]; then
    echo "  Токен подписки / Subscription Token: [Указан / Specified]"
else
    echo "  Токен подписки / Subscription Token: [Не указан / Not specified]"
fi
echo ""
printf "Продолжить установку? / Continue installation? (y/n, default y): "
read_input CONFIRM "y"
CONFIRM="$REPLY"
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

# Router has no swap; reject dangerously low available RAM before opkg/binary changes.
_MEM_AVAILABLE_KB=$(awk '/^MemAvailable:/ {print $2; found=1} END {if (!found) print 0}' /proc/meminfo 2>/dev/null)
case "$_MEM_AVAILABLE_KB" in
    ''|*[!0-9]*) _MEM_AVAILABLE_KB=0 ;;
esac
if [ "$_MEM_AVAILABLE_KB" -gt 0 ]; then
    if [ "$_MEM_AVAILABLE_KB" -lt 32768 ]; then
        echo "ОШИБКА: доступно меньше 32 MiB памяти ($_MEM_AVAILABLE_KB KiB); установка отменена до изменений."
        echo "ERROR: less than 32 MiB RAM is available; aborting before changes."
        exit 1
    elif [ "$_MEM_AVAILABLE_KB" -lt 98304 ]; then
        echo "ПРЕДУПРЕЖДЕНИЕ: доступно меньше 96 MiB RAM ($_MEM_AVAILABLE_KB KiB). Веб-панель и клиент могут увеличить нагрузку на память."
    fi
else
    echo "Предупреждение: MemAvailable не удалось прочитать; автоматическая проверка RAM пропущена."
fi

# Create a private rollback snapshot before opkg or persistent KeeneticOS changes.
# This is a backup, not an automatic full-config restore mechanism.
BACKUP_STAMP=$(date +%Y%m%d-%H%M%S)
BACKUP_DIR="/opt/var/backups/fptn-preinstall-$BACKUP_STAMP"
umask 077
mkdir -p "$BACKUP_DIR"
for _backup_file in \
    /opt/bin/fptn-client-cli \
    /opt/bin/fptn-client-cli.pre-update.bak \
    /opt/bin/fptn-client-cli.tmp \
    /opt/etc/fptn-client.conf \
    /opt/etc/fptn-version \
    /opt/etc/fptn-servers.json \
    /opt/etc/fptn-monitor.conf \
    /opt/etc/fptn-web-panel.installed \
    /opt/etc/init.d/S53fptn-client \
    /opt/share/www/fptn/index.php \
    /opt/etc/lighttpd/conf.d/85-fptn.conf \
    /opt/etc/lighttpd/conf.d/85-fptn.conf.pre-fptn.bak; do
    if [ -f "$_backup_file" ]; then
        _relative=${_backup_file#/opt/}
        mkdir -p "$BACKUP_DIR/$(dirname "$_relative")"
        cp -p "$_backup_file" "$BACKUP_DIR/$_relative" || {
            echo "ОШИБКА: не удалось сохранить $_backup_file; установка отменена до изменений."
            rm -rf "$BACKUP_DIR"
            exit 1
        }
    fi
done
if command -v ndmc >/dev/null 2>&1; then
    ndmc -c "show running-config" > "$BACKUP_DIR/keenetic-running-config.txt" 2>/dev/null || true
    chmod 600 "$BACKUP_DIR/keenetic-running-config.txt" 2>/dev/null || true
fi
chmod 700 "$BACKUP_DIR"
echo "Резервная копия перед установкой / Pre-install snapshot: $BACKUP_DIR"

FPTN_CREATED_INTERFACE=no
FPTN_INSTALL_COMMITTED=no
FPTN_LIGHTTPD_TOUCHED=no
rollback_failed_install() {
    [ "$FPTN_INSTALL_COMMITTED" = "yes" ] && return 0
    echo "Установка прервалась: восстанавливаю файлы FPTN из $BACKUP_DIR."
    for _restore_file in \
        /opt/bin/fptn-client-cli \
        /opt/bin/fptn-client-cli.pre-update.bak \
        /opt/bin/fptn-client-cli.tmp \
        /opt/etc/fptn-client.conf \
        /opt/etc/fptn-version \
        /opt/etc/fptn-servers.json \
        /opt/etc/fptn-monitor.conf \
        /opt/etc/fptn-web-panel.installed \
        /opt/etc/init.d/S53fptn-client \
        /opt/share/www/fptn/index.php \
        /opt/etc/lighttpd/conf.d/85-fptn.conf \
        /opt/etc/lighttpd/conf.d/85-fptn.conf.pre-fptn.bak; do
        _rel=${_restore_file#/opt/}
        _saved="$BACKUP_DIR/$_rel"
        if [ -f "$_saved" ]; then
            mkdir -p "$(dirname "$_restore_file")"
            cp -p "$_saved" "$_restore_file" || echo "WARNING: could not restore $_restore_file"
        else
            rm -f "$_restore_file"
        fi
    done
    rmdir /opt/share/www/fptn 2>/dev/null || true
    if [ "$FPTN_CREATED_INTERFACE" = "yes" ]; then
        _rollback_if=$(ndmc -c "show interface $USER_KTUN" 2>&1 || true)
        if [ -n "$_rollback_if" ] && ! echo "$_rollback_if" | grep -qiE "error|no such|not found"; then
            # The preflight confirmed this interface did not exist before this run.
            ndmc -c "no interface $USER_KTUN" >/dev/null 2>&1 || true
            ndmc -c "system configuration save" >/dev/null 2>&1 || true
            echo "Removed newly created interface during failed-install cleanup."
        fi
    fi
    if [ "$FPTN_LIGHTTPD_TOUCHED" = "yes" ] && [ -x /opt/etc/init.d/S80lighttpd ]; then
        if lighttpd -tt -f /opt/etc/lighttpd/lighttpd.conf >/dev/null 2>&1; then
            /opt/etc/init.d/S80lighttpd restart >/dev/null 2>&1 || echo "WARNING: Lighttpd restart failed after restoring its config."
        else
            echo "WARNING: restored Lighttpd config did not validate; not restarting shared Lighttpd."
        fi
    fi
    if [ -f "$BACKUP_DIR/etc/init.d/S53fptn-client" ] &&
       [ -f "$BACKUP_DIR/etc/fptn-client.conf" ] &&
       grep -q "^ENABLED='yes'$" "$BACKUP_DIR/etc/fptn-client.conf"; then
        /opt/etc/init.d/S53fptn-client start >/dev/null 2>&1 || echo "WARNING: previous FPTN service could not be restarted after rollback."
    fi
    echo "Rollback attempt finished. Entware packages already installed by opkg are not automatically removed."
}
trap 'rollback_failed_install' 0

# 3. Установка необходимых системных пакетов / Install Entware packages
echo ""
echo "[1/7] Установка зависимостей Entware / Installing Entware dependencies..."
opkg update || true
if [ "$INSTALL_WEB_PANEL" = "yes" ]; then
    opkg install lighttpd lighttpd-mod-cgi php8-cgi php8-mod-openssl php8-mod-session libxml2 procps-ng-pgrep procps-ng-pkill curl ca-bundle ca-certificates
else
    # Client-only mode deliberately does not install/restart Lighttpd or PHP.
    opkg install procps-ng-pgrep procps-ng-pkill curl ca-bundle ca-certificates
fi

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

# Persistent KeeneticOS interface setup is deferred until all downloads and
# FPTN config/service files are ready. This avoids leaving an orphan OpkgTun
# when an earlier step fails.

if [ "$INSTALL_WEB_PANEL" = "yes" ]; then
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
    
    # Validate the existing shared web server before changing any of its files.
    if ! lighttpd -tt -f /opt/etc/lighttpd/lighttpd.conf >/dev/null 2>&1; then
        echo "ОШИБКА: текущая конфигурация Lighttpd не проходит проверку."
        echo "Установщик не будет менять общий веб-сервер. Сначала исправьте текущую конфигурацию."
        exit 1
    fi

    FPTN_LIGHTTPD_TOUCHED=yes
    cat << EOF > "$LIGHTTPD_CONF_DIR/85-fptn.conf"
# Dedicated FPTN listener; existing SmartDNS/nfqws listener and document root remain unchanged.
server.modules += ( "mod_cgi" )
\$SERVER["socket"] == ":$USER_PORT" {
    server.document-root = "/opt/share/www/fptn"
    index-file.names = ( "index.php", "index.html" )
    cgi.assign = ( ".php" => "/opt/bin/php-cgi" )
    static-file.exclude-extensions += ( ".php" )
}
EOF

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
    touch /opt/etc/fptn-web-panel.installed
    
    
else
    echo "Веб-панель пропущена: Lighttpd и PHP не изменяются."
    echo "Web panel skipped: Lighttpd and PHP are left untouched."
fi

# Serialize arbitrary values as valid POSIX shell single-quoted literals before
# writing a config file that will later be sourced. This is essential for tokens
# and other values containing quotes, dollar signs, backticks or whitespace.
shell_quote() {
    printf "'"
    printf "%s" "$1" | sed "s/'/'\\\\''/g"
    printf "'"
}

# 8. Создание/Обновление файла конфигурации FPTN службы / Save Configuration
echo ""
echo "[6/8] Сохранение конфигурации FPTN / Saving FPTN configuration..."
CONF_PATH="/opt/etc/fptn-client.conf"
# Client-only mode stays disabled until the user explicitly approves runtime testing.
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
ENABLED=$(shell_quote "${CONF_ENABLED:-no}")
TOKEN=$(shell_quote "$USER_TOKEN")
PREFERRED_SERVER=$(shell_quote "$CONF_SERVERS")
TUN_INTERFACE=$(shell_quote "$USER_LTUN")
# Должно совпадать с адресом, назначенным интерфейсу в KeeneticOS (шаг [3/7]).
TUN_ADDRESS=$(shell_quote "$USER_TUN_IP")
TUN_IPV6_ADDRESS=$(shell_quote "$CONF_TUN_IPV6")
TUN_MTU=$(shell_quote "$CONF_TUN_MTU")
GATEWAY_IP=$(shell_quote "$CONF_GATEWAY_IP")
GATEWAY_IPV6=$(shell_quote "$CONF_GATEWAY_IPV6")
OUT_NETWORK_INTERFACE=$(shell_quote "$CONF_OUT_IFACE")
SERVER_SNI=$(shell_quote "$CONF_SNI")
BYPASS_METHOD=$(shell_quote "$CONF_BYPASS")
BLACKLIST_DOMAINS=$(shell_quote "$CONF_BLACKLIST")
EXCLUDE_TUNNEL_NETWORKS=$(shell_quote "$CONF_EXCLUDE")
INCLUDE_TUNNEL_NETWORKS=$(shell_quote "$CONF_INCLUDE")
SPLIT_TUNNEL=$(shell_quote "$CONF_SPLIT")
SPLIT_TUNNEL_MODE=$(shell_quote "$CONF_SPLIT_MODE")
SPLIT_TUNNEL_DOMAINS=$(shell_quote "$CONF_SPLIT_DOMAINS")
DISABLE_AUTO_FALLBACK=$(shell_quote "$CONF_DISABLE_FALLBACK")
MAX_FULL_RESTARTS=$(shell_quote "$CONF_MAX_RESTARTS")
STARTUP_RETRY_DELAY=$(shell_quote "$CONF_STARTUP_DELAY")
WEB_PASSWORD=$(shell_quote "$CONF_PASS")
EOF
chmod 600 "$CONF_PATH"

# Генерация списка серверов, если токен был указан при установке
if [ -n "$USER_TOKEN" ]; then
    echo "Формирование списка серверов из токена / Generating server list from token..."
    if [ -x "/opt/bin/fptn-client-cli" ]; then
        /opt/bin/fptn-client-cli --access-token "$USER_TOKEN" --show-servers > /opt/etc/fptn-servers.json 2>/dev/null || true  # глушение-обосновано: первичный разбор токена может вернуть ошибку при невалидном токене
    fi
    if [ ! -s "/opt/etc/fptn-servers.json" ] && [ "$INSTALL_WEB_PANEL" = "yes" ]; then
        # Резервный разбор через PHP CGI (only available with web panel)
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

# 5. Создание и настройка TUN-интерфейса в KeeneticOS / Register TUN Interface in KeeneticOS
FPTN_CREATED_INTERFACE=no
FPTN_INSTALL_COMMITTED=no
EXISTING_IF=$(ndmc -c "show interface $USER_KTUN" 2>&1 || true)
if [ -n "$EXISTING_IF" ] && ! echo "$EXISTING_IF" | grep -qiE "error|no such|not found"; then
    if ! echo "$EXISTING_IF" | grep -qi "Fptn"; then
        echo "ОШИБКА: $USER_KTUN уже существует и не помечен как FPTN; настройки не изменены."
        exit 1
    fi
    echo "Reusing existing FPTN interface $USER_KTUN."
else
    FPTN_CREATED_INTERFACE=yes
    echo "Creating KeeneticOS interface $USER_KTUN..."
    ndmc -c "interface $USER_KTUN" >/dev/null 2>&1 || true
    ndmc -c "interface $USER_KTUN type OpkgTun" >/dev/null 2>&1 || true
fi

ndmc -c "interface $USER_KTUN description Fptn" >/dev/null 2>&1 || true
ndmc -c "interface $USER_KTUN security-level public" >/dev/null 2>&1 || true
ndmc -c "interface $USER_KTUN ip address $USER_TUN_IP 255.255.255.255" >/dev/null 2>&1 || true
ndmc -c "interface $USER_KTUN ip mtu 1360" >/dev/null 2>&1 || true
ndmc -c "interface $USER_KTUN ip global 50000" >/dev/null 2>&1 || true
ndmc -c "interface $USER_KTUN ip tcp adjust-mss pmtu" >/dev/null 2>&1 || true
ndmc -c "access-list _WEBADMIN_$USER_KTUN permit ip 0.0.0.0 0.0.0.0 0.0.0.0 0.0.0.0" >/dev/null 2>&1 || true
ndmc -c "interface $USER_KTUN ip access-group _WEBADMIN_$USER_KTUN in" >/dev/null 2>&1 || true
ndmc -c "access-list _WEBADMIN_$USER_KTUN auto-delete" >/dev/null 2>&1 || true
ndmc -c "interface $USER_KTUN up" >/dev/null 2>&1 || true

VERIFY_IF=$(ndmc -c "show interface $USER_KTUN" 2>&1 || true)
if ! echo "$VERIFY_IF" | grep -qi "Fptn"; then
    echo "ОШИБКА: не удалось подтвердить создание/настройку $USER_KTUN."
    echo "Установка остановлена; новая запись интерфейса будет удалена, если возможно."
    exit 1
fi
if ! ndmc -c "system configuration save" >/dev/null 2>&1; then
    echo "ОШИБКА: KeeneticOS не подтвердил сохранение конфигурации."
    exit 1
fi
echo "FPTN interface $USER_KTUN configured and saved."

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
if [ "$INSTALL_WEB_PANEL" = "yes" ]; then
    echo "  1. Веб-панель доступна по адресу / Web panel URL:"
    echo "     http://${ROUTER_LAN_IP}:$USER_PORT/"
    echo ""
    echo "  2. Запуск туннеля / How to start VPN:"
    echo "     - Откройте веб-панель / Open FPTN Web Panel."
    echo "     - Проверьте сервер и нажмите 'Запустить' / Click 'Start'."
else
    echo "  Веб-панель не устанавливалась. Lighttpd/PHP не менялись."
    echo "  Web panel was skipped; Lighttpd/PHP were not changed."
    echo "  Служба оставлена отключённой до отдельного подтверждения теста."
    echo "  Service remains disabled until runtime testing is explicitly approved."
    echo "  Туннель не запущен и не стартует автоматически после перезагрузки."
    echo "  The tunnel is not running and will not auto-start after reboot."
fi
echo ""
echo "  Маршрутизация трафика / Traffic Routing:"
echo "     - В Keenetic Web UI в разделе 'Приоритеты подключений'"
echo "       появится подключение '$USER_KTUN'."
echo "     - Перетащите '$USER_KTUN' в нужную политику маршрутизации."
echo "     - Drag connection '$USER_KTUN' into target routing policy."
echo "==========================================================="
FPTN_INSTALL_COMMITTED=yes
trap - 0
