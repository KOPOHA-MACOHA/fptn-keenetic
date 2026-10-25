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

In its default capture mode, `scripts/probe-ndnproxy-mirror.sh` starts a separate resolver with its own minimal
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
In capture mode the stock mirror socket is never bound, read, renamed, replaced
or written. The separate explicit `--native-check` mode is described below.

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

## Native-handler check

The subsequent 322-byte capture decodes completely into two `request` documents
and one `reply` document. The reply contains two `section` nodes, each with
`fqdn = api.ipify.org`, an `ipv4` node with address `a` and lifetime `t`:
8.47.69.0 and 8.6.112.0, both with `t = 3`. The final `qid` is 12826. It is an
opaque identifier; this observation does not establish that it is safe to invent,
reuse or omit it. The documents match libndm's binary XML serialization (document
node, network-order 32-bit string lengths, node/sibling/end control bytes).

`sh probe-ndnproxy-mirror.sh --native-check` deliberately routes fresh mirror
messages from the separate stock ndnproxy into the existing live
`/var/run/fqdn-dns-mirror.sock`. This mode does not create a receiver on that
socket, synthesize/replay packets, or alter native configuration. It reads the
existing `domain-list9` group before and immediately after the DNS query. The
intended effect is ephemeral learned DNS state for api.ipify.org, subject to the
native handler accepting the secondary resolver's messages. Inspect addresses
under the domain's own FQDN entry; preexisting explicit IP entries alone are not
evidence of learning. The short observed TTL makes the immediate snapshot useful.
Existing manual IP entries are preserved. Stopping the probe does not roll back
learned DNS state; expiry is managed by the native handler.

This mode is an integration diagnostic, not a permanently installed SmartDNS
bridge. A successful nslookup alone does not prove native-handler acceptance.
Local mocks validate the mode's process cleanup and read-only ndmc calls; they do
not simulate acceptance by Keenetic's actual route engine.
