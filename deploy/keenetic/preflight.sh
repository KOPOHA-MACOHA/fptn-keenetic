#!/bin/sh
# Read-only preflight snapshot for a Keenetic/Entware router.
# This script does not install packages, write files, stop services, or change routes.
# It deliberately avoids dumping process command lines or the full router config.

set +e
export LC_ALL=C

section() { printf '\n===== %s =====\n' "$1"; }

section "Timestamp and platform"
date
uname -a
printf 'uname -m: '; uname -m
printf 'Entware: '; [ -x /opt/bin/opkg ] && echo present || echo missing
printf 'ndmc: '; command -v ndmc >/dev/null 2>&1 && echo present || echo missing

section "Entware architectures"
if [ -x /opt/bin/opkg ]; then
    /opt/bin/opkg print-architecture 2>&1
fi

section "Free storage"
df -Pk /opt 2>&1
df -Pk /tmp 2>&1

section "Memory"
if [ -r /proc/meminfo ]; then
    grep -E '^(MemTotal|MemFree|MemAvailable|Buffers|Cached|SwapTotal|SwapFree):' /proc/meminfo
else
    free 2>&1
fi

section "Relevant installed packages"
if [ -x /opt/bin/opkg ]; then
    /opt/bin/opkg list-installed 2>/dev/null |
        grep -E '^(fptn|lighttpd|php8|curl|ca-bundle|ca-certificates|procps-ng|libxml2)' |
        sed -E 's/ - .*$/ [version redacted]/'
fi

section "Current load and CPU counters"
# Do not run top: some implementations print full process arguments, which may contain tokens.
cat /proc/loadavg 2>/dev/null
grep '^cpu ' /proc/stat 2>/dev/null

section "Relevant service processes (names only)"
if command -v pidof >/dev/null 2>&1; then
    for _name in fptn-client-cli lighttpd php-cgi adguardhome dnsmasq smartdns mihomo xray nfqws2; do
        _pids=$(pidof "$_name" 2>/dev/null)
        [ -z "$_pids" ] || printf '%s: running (PID hidden)\n' "$_name"
    done
else
    echo "pidof not available; skipped."
fi

section "Network interfaces and routes"
ip -brief link 2>/dev/null || ip link show 2>/dev/null
ip -4 route show 2>/dev/null
ip -6 route show 2>/dev/null
ip rule show 2>/dev/null

section "Keenetic OpkgTun interfaces"
if command -v ndmc >/dev/null 2>&1; then
    for _i in 1 2 3 4 5 6 7 8 9 10; do
        _out=$(ndmc -c "show interface OpkgTun$_i" 2>&1)
        if [ -n "$_out" ] && ! echo "$_out" | grep -qiE 'error|no such|not found'; then
            printf '%s\n' "--- OpkgTun$_i ---"
            printf '%s\n' "$_out" | grep -iE 'description|state|status|address|security-level|mtu|type' |
                sed -E 's/(token|password|secret)[^:]*:.*/\1: [redacted]/I'
        fi
    done
fi

section "Lighttpd configuration summary (selected directives only)"
for _f in /opt/etc/lighttpd/lighttpd.conf /opt/etc/lighttpd/conf.d/*.conf; do
    [ -f "$_f" ] || continue
    grep -E '^[[:space:]]*server[.](port|bind|document-root)[[:space:]]*[:=]|^[[:space:]]*\\\$SERVER\\["socket"\\]' "$_f" 2>/dev/null |
        sed "s#^#$(_f_label=$(basename "$_f"); printf '%s' "$_f_label") : #"
done
for _f in /opt/etc/init.d/S80lighttpd /opt/etc/lighttpd/lighttpd.conf /opt/etc/lighttpd/conf.d/85-fptn.conf; do
    [ ! -e "$_f" ] || printf 'present: %s\n' "$_f"
done
if command -v lighttpd >/dev/null 2>&1 && [ -f /opt/etc/lighttpd/lighttpd.conf ]; then
    if lighttpd -tt -f /opt/etc/lighttpd/lighttpd.conf >/dev/null 2>&1; then
        echo "Current Lighttpd config validation: PASS"
    else
        echo "Current Lighttpd config validation: FAIL (details suppressed)"
    fi
fi
echo "Read-only preflight complete. No changes were made."
