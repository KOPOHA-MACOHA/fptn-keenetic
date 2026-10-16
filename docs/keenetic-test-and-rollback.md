# Safe Keenetic test and rollback plan

This is a manual checklist, not an installer. Nothing in this document has been run on a router.

## Before testing

- Do not test this branch until the pull request is reviewed/merged and the syntax workflow passes.
- Do not test while relying on FPTN as the only path to administer the router. Keep local/LAN access and a second way to reach the router available.
- Confirm the router has enough free space: `df -h /opt`.
- Keep the backup directory private. `/opt/etc/fptn-client.conf` can contain the subscription token and web-panel password. Never upload the backup or paste its contents into an issue.

## Read-only baseline before any installation

This is the next step before testing FPTN. It collects a snapshot only; it does not install packages, stop services, write router configuration, or change routes. It does write the downloaded script to `/tmp`.

**Do not run yet unless you are ready to collect the baseline.** Pin the script to the reviewed immutable commit so the branch can move without changing what is executed:

```sh
COMMIT="54df538481b48f55276fd66bc1f9035f1c29f277"
URL="https://raw.githubusercontent.com/KOPOHA-MACOHA/fptn-keenetic/$COMMIT/deploy/keenetic/preflight.sh"
curl -fsSL "$URL" -o /tmp/fptn-preflight.sh && sh /tmp/fptn-preflight.sh
```

The report includes private IP addresses, interface names and routes. Keep it private; do not paste the raw report into a public issue or PR. If you want help interpreting it, share only the relevant sanitized sections: free storage, memory/load, architecture/feed list, installed relevant packages, and whether any OpkgTun interfaces or FPTN processes already exist. Redact public IPs, subscription tokens, passwords, hostnames and any identifying network details.

## Full web panel installation (preferred for users who need GUI control)

The router already has Lighttpd, PHP CGI, PHP sessions and the CGI module installed. The installer should reuse an explicit existing Lighttpd listener and publish the panel at `/fptn/`; if no explicit listener is configured, it asks for a panel port. It validates the existing Lighttpd configuration before modifying it and validates the resulting configuration before restarting the shared service.

**Do not run yet.** First run the updated read-only preflight script and inspect the selected Lighttpd directives and its config-validation result. If the existing config fails validation, stop here and fix that separately.

For the later install, use an immutable commit so the installer and downloaded companion files come from the same reviewed tree:

```sh
# EXAMPLE ONLY — DO NOT RUN YET.
COMMIT="3a772176f38810925e8e8a0a21aba4696aab533a"
BASE="https://raw.githubusercontent.com/KOPOHA-MACOHA/fptn-keenetic/$COMMIT"
curl -fsSL "$BASE/deploy/keenetic/install.sh" -o /tmp/fptn-install.sh
FPTN_GITHUB_RAW_BASE="$BASE" sh /tmp/fptn-install.sh
```

The installer leaves a new client service disabled, so the tunnel should not start automatically after installation. The web panel will be available at the URL printed at the end, typically `http://ROUTER-LAN-IP:PORT/fptn/` or on the existing Lighttpd port at `/fptn/`. On first visit, set a unique administrator password of at least 10 characters. Do not expose the panel to the internet; use it only from your trusted LAN.

This full-panel mode does install any missing packages, replaces the client binary after a basic smoke test, writes the panel configuration and creates a persistent KeeneticOS OpkgTun interface. It may restart the existing Lighttpd service after validating its configuration. It is lower risk than an unreviewed install, but it is not risk-free. The available-RAM check aborts below 32 MiB and warns below 96 MiB; your current baseline is about 64 MiB, so expect a warning.

## 1. Create a private backup

Run these commands manually over SSH only after you are ready to test. They only create a backup; they do not stop services or change the running configuration.

```sh
umask 077
BACKUP="/opt/backup/fptn-pretest-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$BACKUP"
chmod 700 "$BACKUP"

for f in \
  /opt/bin/fptn-client-cli \
  /opt/etc/fptn-client.conf \
  /opt/etc/fptn-version \
  /opt/etc/init.d/S53fptn-client \
  /opt/share/www/fptn/index.php \
  /opt/etc/fptn-servers.json \
  /opt/etc/fptn-monitor.conf \
  /opt/etc/lighttpd/conf.d/85-fptn.conf \
  /opt/etc/lighttpd/conf.d/85-fptn.conf.pre-fptn.bak
do
  if [ -f "$f" ]; then
    mkdir -p "$BACKUP/$(dirname "$f" | sed 's#^/opt/##')"
    cp -p "$f" "$BACKUP/$(dirname "$f" | sed 's#^/opt/##')/"
  fi
done

ip addr > "$BACKUP/ip-addr.txt" 2>&1
ip route > "$BACKUP/ip-route.txt" 2>&1
ip rule > "$BACKUP/ip-rule.txt" 2>&1
ndmc -c "show running-config" > "$BACKUP/keenetic-running-config.txt" 2>&1 || true
echo "Backup directory: $BACKUP"
```

Record the printed backup path somewhere private. Verify the important files exist in it before continuing.

## 2. Pre-flight checks

Before installing anything, confirm:
- the GitHub Actions workflow **Validate Keenetic scripts** passed;
- the binary release actually provides the architecture/ABI expected by this router (the router's `uname -m` alone is not enough);
- the current FPTN token still works and you can get a replacement through the existing bot flow;
- you know how to regain router access if the tunnel stops.

The ELF magic check and `--help` smoke test are useful safeguards, but they do not prove the binary's ABI, shared-library compatibility, or correct routing behavior on this exact device.

## 3. Roll back if the update fails

Set `BACKUP` to the exact directory created above. These commands change the running setup and should only be used when rollback is needed.

```sh
# IMPORTANT: replace this example with your actual backup directory.
BACKUP="/opt/backup/fptn-pretest-YYYYMMDD-HHMMSS"

# Stop the current service if its script is available.
[ ! -x /opt/etc/init.d/S53fptn-client ] || /opt/etc/init.d/S53fptn-client stop

# Restore backed-up files only when their backup copies exist.
[ ! -f "$BACKUP/bin/fptn-client-cli" ] || cp -p "$BACKUP/bin/fptn-client-cli" /opt/bin/fptn-client-cli
[ ! -f "$BACKUP/etc/fptn-client.conf" ] || cp -p "$BACKUP/etc/fptn-client.conf" /opt/etc/fptn-client.conf
[ ! -f "$BACKUP/etc/fptn-version" ] || cp -p "$BACKUP/etc/fptn-version" /opt/etc/fptn-version
[ ! -f "$BACKUP/etc/init.d/S53fptn-client" ] || cp -p "$BACKUP/etc/init.d/S53fptn-client" /opt/etc/init.d/S53fptn-client
[ ! -f "$BACKUP/share/www/fptn/index.php" ] || cp -p "$BACKUP/share/www/fptn/index.php" /opt/share/www/fptn/index.php
[ ! -f "$BACKUP/etc/fptn-servers.json" ] || cp -p "$BACKUP/etc/fptn-servers.json" /opt/etc/fptn-servers.json
[ ! -f "$BACKUP/etc/fptn-monitor.conf" ] || cp -p "$BACKUP/etc/fptn-monitor.conf" /opt/etc/fptn-monitor.conf
[ ! -f "$BACKUP/etc/lighttpd/conf.d/85-fptn.conf" ] || cp -p "$BACKUP/etc/lighttpd/conf.d/85-fptn.conf" /opt/etc/lighttpd/conf.d/85-fptn.conf
[ ! -f "$BACKUP/etc/lighttpd/conf.d/85-fptn.conf.pre-fptn.bak" ] || cp -p "$BACKUP/etc/lighttpd/conf.d/85-fptn.conf.pre-fptn.bak" /opt/etc/lighttpd/conf.d/85-fptn.conf.pre-fptn.bak

chmod +x /opt/bin/fptn-client-cli /opt/etc/init.d/S53fptn-client 2>/dev/null || true
/opt/etc/init.d/S53fptn-client start
[ ! -x /opt/etc/init.d/S80lighttpd ] || /opt/etc/init.d/S80lighttpd restart
```

If rollback commands report errors, do not repeatedly restart the tunnel. Preserve the error output and recover over LAN/console access. Restoring the saved Keenetic running configuration is a separate, potentially disruptive operation; do not blindly import it while remote.



## Extra caution for this fork

- The installer changes persistent KeeneticOS configuration for an OpkgTun interface and restarts Lighttpd, which may serve other local tools. A file backup alone is not enough to restore these changes.
- Before the first install, save the output of ndmc -c "show running-config" and note any existing OpkgTun interfaces. Do not paste the output publicly; it may reveal private network details.
- If the installer stops after changing the KeeneticOS interface, compare the saved running-config with the current one and restore only the FPTN-specific interface/policy changes. Do not blindly import the whole saved configuration over a remote connection.
- Do not use the standalone or embedded uninstaller as a substitute for a verified rollback test. Keep the backup until the router has survived a reboot and the other Entware services have been checked.

## Important limitations

- This backup covers the listed FPTN files and captures some network state; it is not a full flash/router firmware backup.
- It does not automatically restore KeeneticOS interface or policy-routing objects if an installer changed them. Record those separately before testing and compare after.
- The installer and web updater still need a real end-to-end test on the target hardware. A syntax pass is not a runtime certification.
