#!/bin/sh
# Скрипт удаления FPTN-клиента и веб-панели с Keenetic (Entware) / FPTN Uninstaller for Keenetic
# Автор / Author: Antigravity
# Поддержка языков / Language: Русский (RU) & English (EN)

set -e

export LANG="${LANG:-ru_RU.UTF-8}"
export LC_ALL="${LC_ALL:-ru_RU.UTF-8}"

echo "==========================================================="
echo "  Удаление FPTN-клиента и веб-панели с Keenetic (Entware)"
echo "  Uninstalling FPTN Client & Web Panel from Keenetic"
echo "==========================================================="
echo ""

# Only touch a Keenetic interface when an FPTN config identifies it.
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

# Remember panel ownership before removing its dedicated Lighttpd socket config.
# Restart the shared Lighttpd only when the FPTN panel was actually installed.
FPTN_PANEL_PRESENT=no
if [ -f "/opt/etc/fptn-web-panel.installed" ] || [ -f "/opt/etc/lighttpd/conf.d/85-fptn.conf" ]; then
    FPTN_PANEL_PRESENT=yes
fi

# 1. Остановка процессов и служб / Stop services
echo "[1/5] Остановка служб FPTN / Stopping FPTN services..."
if [ -f "/opt/etc/init.d/S53fptn-client" ]; then
    /opt/etc/init.d/S53fptn-client stop >/dev/null 2>&1 || true
fi
pkill -9 -f "fptn-client-cli" >/dev/null 2>&1 || true
pkill -9 -f "fptn-watchdog" >/dev/null 2>&1 || true

# 2. Удаление файлов / Remove files
echo "[2/5] Удаление бинарников и файлов веб-панели / Removing binary & web files..."
rm -f /opt/bin/fptn-client-cli
rm -f /opt/bin/fptn-client-wrapper.sh
rm -f /opt/bin/fptn-watchdog.sh
rm -f /opt/bin/fptn-ndm-setup.sh
rm -f /opt/etc/fptn-watchdog.sh
rm -f /opt/etc/fptn-client.conf
rm -f /opt/etc/init.d/S53fptn-client
rm -f /opt/etc/fptn-web-panel.installed
if [ -f /opt/etc/lighttpd/conf.d/85-fptn.conf.pre-fptn.bak ]; then
    cp -p /opt/etc/lighttpd/conf.d/85-fptn.conf.pre-fptn.bak /opt/etc/lighttpd/conf.d/85-fptn.conf
    rm -f /opt/etc/lighttpd/conf.d/85-fptn.conf.pre-fptn.bak
else
    rm -f /opt/etc/lighttpd/conf.d/85-fptn.conf
fi
rm -rf /opt/share/www/fptn

# 3. Перезапуск веб-сервера Lighttpd / Restart Lighttpd
echo "[3/5] Удаление отдельного listener FPTN и перезапуск Lighttpd / Remove FPTN listener and restart Lighttpd"
if [ "$FPTN_PANEL_PRESENT" = "yes" ] && [ -x "/opt/etc/init.d/S80lighttpd" ]; then
    /opt/etc/init.d/S80lighttpd restart >/dev/null 2>&1 || true
else
    echo "Панель FPTN не обнаружена; Lighttpd не перезапускается."
fi

# 4. Удаление из планировщика Cron / Clean Crontab
echo "[4/5] Очистка старых записей cron / Cleaning legacy crontab entries..."
if which crontab >/dev/null 2>&1; then
    (crontab -l 2>/dev/null | grep -v "fptn-watchdog" | crontab - 2>/dev/null) || true
fi

# 5. Remove only an interface positively identified as FPTN.
if [ "$FPTN_CONFIG_PRESENT" = "yes" ] && [ -n "$KTUN_NAME" ]; then
    echo "[5/5] Проверка интерфейса FPTN $KTUN_NAME / Checking FPTN interface..."
    if command -v ndmc >/dev/null 2>&1; then
        _uninstall_if=$(ndmc -c "show interface $KTUN_NAME" 2>&1 || true)
        if echo "$_uninstall_if" | grep -qi "Fptn"; then
            ndmc -c "no interface $KTUN_NAME" 2>/dev/null || true
            ndmc -c "system configuration save" 2>/dev/null || true
        else
            echo "Интерфейс $KTUN_NAME не помечен как FPTN; оставляю его нетронутым."
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
