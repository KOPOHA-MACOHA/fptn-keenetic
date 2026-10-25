# Reply-only native writer diagnostic

The stock-secondary-resolver check succeeded on Hopper KN-3810, KeeneticOS
5.1.4: the previously empty `api.ipify.org` FQDN entry learned 8.6.112.0 and
8.47.69.0. Existing explicit IP entries were not used as evidence. The native
engine reported a lifetime near 150 seconds although the captured DNS mirror
contained `t = 3`. The bridge should therefore delegate native membership and
expiry rather than trying to duplicate those policies in direct ipset writes.

`scripts/probe-smartdns-native-writer.sh` is the next controlled diagnostic. It
queries the existing SmartDNS at 127.0.0.1:53, extracts only IPv4 addresses from
the answer section, serializes one binary XML `reply`, and sends it as a single
Unix datagram to `/var/run/fqdn-dns-mirror.sock`. This intentionally updates only
learned DNS state for api.ipify.org; native configuration is never edited.
The script reads domain-list9 before and immediately after sending. It does not
bind/replace the native socket, start a daemon, install files, change DNS servers,
remove existing explicit IPs or save startup configuration.

The test lifetime is explicitly set to three seconds; nslookup does not expose
the actual TTL. The `qid` field is intentionally absent. Its private queue
semantics have not been established, so a bridge cannot safely invent or reuse
stock query identifiers. The test checks whether a reply without that field is
sufficient for the native learned state to update. Do not infer acceptance from
a successful socket send: look for addresses and refreshed last-updated values
under the FQDN entry in the real router output. This is not an installed bridge.

The encoding follows the observed captures and the public
[libndm binary XML reader](https://github.com/ndmsystems/libndm/blob/master/src/core.c):
document node, network-order 32-bit string lengths and explicit child/end bytes.
An independent local tree reader checks complete consumption, the exact FQDN,
both answer addresses, test TTL and absence of qid. Invalid DNS answer addresses
must prevent sending. Mock socket/ndmc tests do not validate actual router
acceptance or infer native queue semantics.
