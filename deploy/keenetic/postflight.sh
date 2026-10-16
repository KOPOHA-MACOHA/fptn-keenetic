#!/bin/sh
# Read-only post-install verification for a Keenetic FPTN client install.
# This script never starts/stops services, changes config, or changes routing.

set +e
FAILURES=0
WARNINGS=0

pass() { printf '[PASS] %s\n' "$1"; }
fail() { printf '[FAIL] %s\n' "$1"; FAILURES=$((FAILURES + 1)); }
warn() { printf '[WARN] %s\n' "$1"; WARNINGS=$((WARNINGS + 1)); }
info() { printf '[INFO] %s\n' "$1"; }

echo "FPTN read-only post-install verification"
echo "No services will be started/stopped and no configuration will be changed."
echo ""

CONF="/opt/etc/fptn-client.conf"
INIT="/opt/etc/init.d/S53fptn-client"
PANEL="/opt/share/www/fptn/index.php"
LIGHTTPD_CONF="/opt/etc/lighttpd/lighttpd.conf"
FPTN_LIGHTTPD_CONF="/opt/etc/lighttpd/conf.d/85-fptn.conf"

if [ -f "$CONF" ]; then
    if grep -Eq "^ENABLED=['\"]?no['\"]?$" "$CONF"; then
        pass "FPTN config explicitly has ENABLED=no"
    else
        fail "FPTN config is missing or ENABLED is not exactly no; do not start the tunnel"
    fi
else
    fail "Missing $CONF"
fi

if [ -x "$INIT" ]; then
    pass "FPTN init script exists and is executable"
else
    fail "FPTN init script missing or not executable"
fi

if [ -x /opt/bin/fptn-client-cli ]; then
    pass "FPTN client binary exists and is executable"
else
    fail "FPTN client binary missing or not executable"
fi

if command -v pgrep >/dev/null 2>&1; then
    if pgrep -x fptn-client-cli >/dev/null 2>&1; then
        fail "FPTN client process is running although this check expects a disabled install"
    else
        pass "No fptn-client-cli process is running"
    fi
else
    if ps 2>/dev/null | grep '[f]ptn-client-cli' >/dev/null 2>&1; then
        fail "FPTN client process appears to be running"
    else
        warn "Could not reliably check for a running FPTN process (pgrep unavailable)"
    fi
fi

if command -v ndmc >/dev/null 2>&1 && [ -f "$CONF" ]; then
    TUN_IF=$(sed -n "s/^TUN_INTERFACE=['\"]\{0,1\}\([^'\"]*\)['\"]\{0,1\}$/\1/p" "$CONF" | head -n 1)
    if [ -n "$TUN_IF" ]; then
        KTUN=$(echo "$TUN_IF" | sed -E 's/opkgtun([0-9]+)/OpkgTun\1/i')
        IF_INFO=$(ndmc -c "show interface $KTUN" 2>&1)
        if [ -n "$IF_INFO" ] && ! echo "$IF_INFO" | grep -qiE 'error|no such|not found|ошибк|не найден'; then
            if echo "$IF_INFO" | grep -qi 'Fptn'; then
                pass "KeeneticOS interface $KTUN exists and is marked FPTN"
            else
                fail "Interface $KTUN exists but is not marked FPTN"
            fi
        else
            if grep -Eq "^ENABLED=['\\"]?no['\\"]?$" "$CONF"; then
                pass "KeeneticOS interface creation deferred because FPTN is disabled"
            else
                fail "Could not confirm KeeneticOS interface $KTUN"
            fi
        fi
    else
        fail "Could not read TUN_INTERFACE from $CONF"
    fi
else
    warn "Skipped Keenetic interface check (ndmc or config unavailable)"
fi

if [ -f "$PANEL" ]; then
    pass "Web panel PHP file exists"
else
    fail "Web panel PHP file is missing"
fi

if [ -f "$FPTN_LIGHTTPD_CONF" ]; then
    pass "Dedicated FPTN Lighttpd snippet exists"
    PANEL_PORT=$(sed -n 's/.*== *":\([0-9][0-9]*\)".*/\1/p' "$FPTN_LIGHTTPD_CONF" | head -n 1)
    case "$PANEL_PORT" in
        ''|*[!0-9]*) fail "Could not determine panel port from $FPTN_LIGHTTPD_CONF" ;;
        *)
            info "Configured FPTN panel port: $PANEL_PORT"
            if command -v netstat >/dev/null 2>&1 &&
               netstat -lnt 2>/dev/null | grep -E "[.:]${PANEL_PORT}[[:space:]]" >/dev/null; then
                pass "TCP listener for panel port $PANEL_PORT is present"
            else
                fail "No TCP listener detected for panel port $PANEL_PORT"
            fi
            ;;
    esac
else
    fail "Dedicated FPTN Lighttpd snippet is missing"
fi

if command -v lighttpd >/dev/null 2>&1 && [ -f "$LIGHTTPD_CONF" ]; then
    if lighttpd -tt -f "$LIGHTTPD_CONF" >/dev/null 2>&1; then
        pass "Shared Lighttpd configuration validates"
    else
        fail "Shared Lighttpd configuration validation failed; do not restart Lighttpd"
    fi
else
    fail "Lighttpd executable or main configuration is missing"
fi

# These listeners existed before the planned install on the target router.
# Report them as warnings, not hard failures, because deployments may differ.
if command -v netstat >/dev/null 2>&1; then
    if netstat -lnt 2>/dev/null | awk '$4 ~ /:8088$/ {found=1} END {exit !found}'; then
        pass "Pre-existing TCP listener on port 8088 is still present"
    else
        warn "Pre-existing TCP listener on port 8088 was not detected"
    fi
    if netstat -lun 2>/dev/null | awk '$4 ~ /:3000$/ {found=1} END {exit !found}'; then
        pass "Pre-existing UDP listener on port 3000 is still present"
    else
        warn "Pre-existing UDP listener on port 3000 was not detected"
    fi
else
    warn "netstat unavailable; listener checks were skipped"
fi

echo ""
if [ "$FAILURES" -eq 0 ]; then
    echo "RESULT: PASS ($WARNINGS warning(s)). This only checks basic state; it does not prove VPN connectivity."
    exit 0
fi
echo "RESULT: FAIL ($FAILURES failure(s), $WARNINGS warning(s)). Keep the FPTN tunnel disabled and inspect the messages above."
exit 1
