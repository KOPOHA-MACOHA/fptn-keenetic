#!/usr/bin/env python3
"""Opt-in tunnel test with all RouteManager system mutations removed.

Runs AFTER the cached dependency stage. Does not change the upstream protocol.
The old help-only prototype remains available at its pinned release/commit.
"""
import pathlib
import sys


ROUTE_STUBS = r'''
namespace fptn::routing {

// Network settings belong to Keenetic / the operator, never this test client.
void HealStaleResolvConf() {}

RouteManager::RouteManager(Config config)
    : running_(false), worker_running_(false), config_(std::move(config)) {}

RouteManager::~RouteManager() = default;

bool RouteManager::Apply(std::string tun_name) {
  const std::unique_lock<std::mutex> lock(mutex_);
  if (running_) return false;
  tun_interface_name_ = std::move(tun_name);
  running_ = true;
  SPDLOG_INFO("Keenetic tunnel test: routing, DNS and firewall are externally managed");
  return true;
}

bool RouteManager::Clean() {
  const std::unique_lock<std::mutex> lock(mutex_);
  running_ = false;
  return true;
}

bool RouteManager::AddDnsRoutesIPv4(const std::vector<IPv4Address>&, RoutingPolicy) {
  return false;
}
bool RouteManager::AddDnsRoutesIPv6(const std::vector<IPv6Address>&, RoutingPolicy) {
  return false;
}
bool RouteManager::AddExcludeRouteWithReset(const IPv4Address&,
    fptn::common::network::IPPacketPtr) {
  return false;
}
bool RouteManager::AddDnsRoutesWithReply(const std::vector<IPv4Address>&,
    const std::vector<IPv6Address>&, RoutingPolicy,
    fptn::common::network::IPPacketPtr) {
  return false;
}
void RouteManager::SetTunSink(
    std::function<void(fptn::common::network::IPPacketPtr)>) {}

'''


def replace_once(path, old, new):
    text = path.read_text()
    if text.count(old) != 1:
        raise ValueError(f"unexpected upstream structure in {path}: {old!r}")
    path.write_text(text.replace(old, new, 1))


def external_route_source(original):
    # Retain only upstream includes and the trailing read-only discovery/DNS
    # helpers. Drop the entire mutating implementation and its anonymous helpers.
    helper = "// NOLINT(bugprone-exception-escape)\nfptn::common::network::IPv4Address ResolveDomain("
    if original.count("namespace {") != 1 or original.count(helper) != 1:
        raise ValueError("RouteManager layout changed; refusing to guess")
    result = original.split("namespace {", 1)[0] + ROUTE_STUBS + original.split(helper, 1)[1]
    # Restore the declaration consumed by split().
    result = result.replace(ROUTE_STUBS, ROUTE_STUBS + helper, 1)
    for forbidden in ("ip route add", "ip route del", "ip route replace", "sysctl -", "iptables -", "chattr ", "resolvectl "):
        if forbidden in result:
            raise ValueError(f"Unexpected mutation in external routing source: {forbidden}")
    return result


def prepare(root):
    cli = root / "src/fptn-client/fptn-client-cli.cpp"
    replace_once(cli,
        '    argparse::ArgumentParser args("fptn-client", FPTN_VERSION);',
        '    argparse::ArgumentParser args("fptn-client", FPTN_VERSION);\n'
        '    args.add_argument("--keenetic-connect")\n'
        '        .default_value(false).implicit_value(true)\n'
        '        .help("Explicitly allow tunnel connection; routing stays externally managed");')
    old = ('    std::cerr << "Keenetic prototype: connection is disabled; "\n'
           '                 "only --help and --version are supported.\\n";\n'
           '    return EXIT_FAILURE;')
    new = r'''    if (!args.get<bool>("--keenetic-connect")) {
      std::cerr << "Connection requires --keenetic-connect; routing is externally managed.\n";
      return EXIT_FAILURE;
    }
    if (args.get<bool>("--enable-split-tunnel") ||
        args.get<bool>("--enable-ad-block") ||
        !args.get<std::string>("--blacklist-domains").empty() ||
        !args.get<std::string>("--include-tunnel-networks").empty()) {
      std::cerr << "Tunnel test requires --enable-split-tunnel false "
                   "--enable-ad-block false --blacklist-domains '' "
                   "and no --include-tunnel-networks.\n";
      return EXIT_FAILURE;
    }
    if (args.get<std::string>("--tun-interface-name") != "fptn0") {
      std::cerr << "Tunnel test requires --tun-interface-name fptn0.\n";
      return EXIT_FAILURE;
    }'''
    replace_once(cli, old, new)
    # Upstream silently tries every server if a preferred name is misspelled.
    # The router test must fail instead of starting a parallel login race.
    replace_once(cli,
        '      bool use_login_race = preferred_names.empty();',
        '      if (preferred_names.size() != 1) {\n'
        '        SPDLOG_ERROR("Tunnel test requires exactly one preferred server");\n'
        '        return EXIT_FAILURE;\n'
        '      }\n'
        '      bool use_login_race = false;')
    replace_once(cli,
        '          SPDLOG_WARN("Server \'{}\' does not exist! Check your token!",\n'
        '              preferred_names.front());\n'
        '          use_login_race = true;',
        '          SPDLOG_ERROR("Preferred server \'{}\' not found; refusing fallback",\n'
        '              preferred_names.front());\n'
        '          return EXIT_FAILURE;')
    route = root / "src/fptn-client/routing/route_manager.cpp"
    route.write_text(external_route_source(route.read_text()))
    print("Prepared opt-in tunnel test; RouteManager has no system mutations or workers")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: prepare-connect-test.py UPSTREAM_DIR")
    prepare(pathlib.Path(sys.argv[1]))
