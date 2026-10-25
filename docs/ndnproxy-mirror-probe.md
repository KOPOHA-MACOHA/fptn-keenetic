# Capturing the native FQDN mirror format

This is research for bridging SmartDNS replies into Keenetic's native DNS route
engine. It is not a routing implementation and does not establish compatibility.

Verified on the user's Hopper, KeeneticOS 5.1.4:

* SmartDNS 46.1 owns TCP/UDP port 53 with `opkg dns-override` enabled.
* The native `ndnproxy` remains in RPC-only mode. Its loopback port 54321 is
  `rpc_port`, not an ordinary DNS listener.
* `/var/ndnproxymain.conf` contains `fqdn_mirror_path` pointing at
  `/var/run/fqdn-dns-mirror.sock`.
* SmartDNS can populate an isolated initially empty `hash:net` ipset. That alone
  does not establish native route expiry, exclusions, overlapping groups, or GUI
  learned-address bookkeeping.

`scripts/probe-ndnproxy-mirror.sh` starts a separate resolver with its own minimal
configuration, PID/stat files and Unix-domain socket in a private temporary
directory. It never copies or edits production configuration. It uses DNS port
15453, RPC port 15454 and `rr_port` 15455, aborting if a port is already occupied.
The resolver may bind the high DNS port on all addresses; no firewall changes are
made. Its only configured upstream is the existing SmartDNS at 127.0.0.1:53.

One bounded nslookup for `api.ipify.org` exercises the resolver. Socat keeps
receiving at the private mirror socket until three seconds after nslookup ends;
the script prints the total stream length and up to
4096 bytes as octal byte values. A 15-second watchdog stops only processes whose command line
contains the unique private `-c` configuration path, including a daemon child.
Normal exit and signals also stop the temporary resolver and remove its files.
The stock mirror socket is never bound, read, renamed, replaced or written.

The DNS port configuration keys are found in actual Keenetic configurations;
acceptance by the exact router binary and the private payload structure still
require the router test. No guessed payload is sent to the native route engine.
When no message is received, the script explicitly fails rather than declaring
the bridge working. No changes to FPTN, SmartDNS, native lists, routes or startup
configuration are made by this probe.

The first real capture was a 53-byte binary XML document containing
`request/fqdn = api.ipify.org`, without address or TTL fields. A one-shot socat
`UNIX-RECVFROM` receiver exited at that initial notification and could not capture
later responses. The current script uses `UNIX-RECV` and does not stop on the
first nonempty write. Multiple serialized XML documents are concatenated in the
dump; their Unix datagram boundaries are not retained. Receiving bytes alone
still does not prove that response notifications were received or interpreted.

Local validation uses mocked resolver/capture tools and file-backed IPC because
the execution sandbox forbids binding both IP and Unix sockets. It checks success,
missing mirror output, occupied ports, resolver startup failure and cleanup after
daemonization. These checks do not validate the private wire format or the router
binary's behavior.
