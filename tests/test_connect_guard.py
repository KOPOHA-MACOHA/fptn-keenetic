import importlib.util
import pathlib
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("connect", ROOT / "scripts/prepare-connect-test.py")
connect = importlib.util.module_from_spec(spec)
spec.loader.exec_module(connect)


class ConnectGuardTest(unittest.TestCase):
    def test_external_routes_remove_mutating_commands(self):
        original = '''#include "routing/route_manager.h"
namespace {
void unsafe() { run("ip route replace default"); }
}
namespace fptn::routing {
void RouteManager::Apply() { run("iptables -A"); }
// NOLINT(bugprone-exception-escape)
fptn::common::network::IPv4Address ResolveDomain(const std::string& domain) { return {}; }
}  // namespace fptn::routing
'''
        result = connect.external_route_source(original)
        self.assertNotIn("ip route replace", result)
        self.assertNotIn("iptables -A", result)
        self.assertIn("worker_running_(false)", result)
        self.assertIn("IPv4Address ResolveDomain(", result)
        self.assertEqual(result.count("namespace fptn::routing {"), 1)

    def test_unexpected_layout_is_rejected(self):
        with self.assertRaises(ValueError):
            connect.external_route_source("changed upstream")

    def test_connection_gate_precedes_initialization(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            cli = root / "src/fptn-client/fptn-client-cli.cpp"
            cli.parent.mkdir(parents=True)
            cli.write_text('    argparse::ArgumentParser args("fptn-client", FPTN_VERSION);\n'
                '    std::cerr << "Keenetic prototype: connection is disabled; "\n'
                '                 "only --help and --version are supported.\\n";\n'
                '    return EXIT_FAILURE;\n'
                '    if (fptn::logger::init("fptn-client-cli")) {}\n'
                '      bool use_login_race = preferred_names.empty();\n'
                '          SPDLOG_WARN("Server \'{}\' does not exist! Check your token!",\n'
                '              preferred_names.front());\n'
                '          use_login_race = true;')
            route = cli.parent / "routing/route_manager.cpp"
            route.parent.mkdir()
            route.write_text('namespace { }\n// NOLINT(bugprone-exception-escape)\n'
                'fptn::common::network::IPv4Address ResolveDomain() {}')
            connect.prepare(root)
            text = cli.read_text()
            self.assertLess(text.index('if (!args.get<bool>("--keenetic-connect"))'), text.index("logger::init"))
            self.assertIn('--enable-split-tunnel false', text)
            self.assertIn('!= "fptn0"', text)
            self.assertIn('preferred_names.size() != 1', text)
            self.assertIn('not found; refusing fallback', text)
