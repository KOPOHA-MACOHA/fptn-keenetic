#!/usr/bin/env python3
"""Apply narrow native-only selection/failover patch after existing safety patches."""
from pathlib import Path
import sys


def replace(text, old, new):
    if text.count(old) != 1:
        raise ValueError('Server-policy upstream layout changed: '+old[:100])
    return text.replace(old, new, 1)


SELECTION = r'''
      const auto policy_path = args.get<std::string>("--keenetic-server-policy");
      if (!policy_path.empty()) {
        if (!args.get<bool>("--keenetic-native")) {
          throw std::runtime_error("Server policy requires --keenetic-native");
        }
        auto read_json = [](const std::string& path) {
          std::ifstream file(path, std::ios::binary);
          if (!file) throw std::runtime_error("Cannot read server policy");
          std::string content(65537, '\0');
          file.read(content.data(), static_cast<std::streamsize>(content.size()));
          content.resize(static_cast<std::size_t>(file.gcount()));
          if (content.size() > 65536) throw std::runtime_error("Server policy too large");
          return nlohmann::json::parse(content);
        };
        const auto policy = read_json(policy_path);
        const auto mode = policy.at("mode").get<std::string>();
        if (mode != "auto" && mode != "manual") throw std::runtime_error("Invalid selection mode");
        const auto favorites = policy.at("favorites").get<std::vector<std::string>>();
        const auto excluded = policy.at("excluded").get<std::vector<std::string>>();
        const bool probe_only = args.get<bool>("--keenetic-probe-only");
        std::vector<std::string> failed;
        const auto skip_path = args.get<std::string>("--keenetic-skip-file");
        if (!skip_path.empty()) failed = read_json(skip_path).get<std::vector<std::string>>();
        const auto manual = !probe_only && mode == "manual" && !args.get<bool>("--keenetic-auto-select")
                              ? preferred_servers : std::string{};
        auto servers = config.GetServers();
        for (auto& server : servers) {
          if (server.username.empty()) server.username = config.GetUsername();
          if (server.password.empty()) server.password = config.GetPassword();
        }
        const auto tiers = fptn::keenetic::ServerTiers(servers,
            probe_only ? std::vector<std::string>{} : favorites, excluded, failed, manual);
        const auto report_path = args.get<std::string>("--keenetic-probe-report");
        std::mutex report_mutex;
        nlohmann::json report{{"checked_at", std::chrono::system_clock::to_time_t(std::chrono::system_clock::now())},
            {"method", "fptn-login"}, {"scope", probe_only ? "all-allowed" : "selection"},
            {"complete", false}, {"results", nlohmann::json::array()}};
        bool report_failed = !fptn::keenetic::WritePrivateJson(report_path, report.dump());
        if (tiers.empty()) {
          SPDLOG_ERROR("KEENETIC_NO_ELIGIBLE_SERVERS");
          return EXIT_FAILURE;
        }
        auto result = fptn::keenetic::SelectServer<fptn::utils::speed_estimator::LoginResult>(tiers,
          [&](const fptn::utils::speed_estimator::ServerInfo& server)
              -> std::optional<fptn::utils::speed_estimator::LoginResult> {
            SPDLOG_INFO("KEENETIC_PROBE: {}", server.name);
            const auto started = std::chrono::steady_clock::now();
            std::optional<fptn::utils::speed_estimator::LoginResult> login;
            int code = 0;
            try {
              fptn::protocol::https::ApiClient client(server.host, server.port, sni,
                  server.md5_fingerprint, censorship_strategy);
              const auto body = nlohmann::json{{"username", server.username}, {"password", server.password}}.dump();
              const auto response = client.Post("/api/v1/login", body, "application/json", 5);
              code = response.code;
              if (response.code == 200) {
                const auto jwt = response.Json().at("access_token").get<std::string>();
                if (!jwt.empty()) login = fptn::utils::speed_estimator::LoginResult{.server=server, .access_token=jwt};
              }
            } catch (...) { /* report failure without credentials or response bodies */ }
            const auto ms = std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now()-started).count();
            {
              const std::lock_guard<std::mutex> lock(report_mutex);
              report["results"].push_back({{"name", server.name}, {"ok", login.has_value()}, {"latency_ms", ms}, {"code", code}});
            }
            return login;
          });
        report["complete"] = true;
        if (!fptn::keenetic::WritePrivateJson(report_path, report.dump())) report_failed = true;
        if (probe_only) {
          if (report_path.empty() || report_failed) throw std::runtime_error("Cannot write server check report");
          spdlog::shutdown();
          return EXIT_SUCCESS; // no TUN, RouteManager or VPN session is constructed
        }
        if (!result) {
          SPDLOG_ERROR("KEENETIC_ALL_SERVERS_UNAVAILABLE");
          return EXIT_FAILURE;
        }
        selected_server = result->server;
        pre_obtained_token = std::move(result->access_token);
        SPDLOG_INFO("KEENETIC_SELECTED: {}", selected_server.name);
      } else {
'''


def prepare(root, header):
    cli_path = root/'src/fptn-client/fptn-client-cli.cpp'
    loop_path = root/'src/fptn-client/utils/signal/main_loop.h'
    cli = cli_path.read_text()
    cli = replace(cli, '#include <iostream>', '#include <iostream>\n#include <fstream>\n#include <nlohmann/json.hpp>\n#include "keenetic-server-policy.h"\n#include "fptn-protocol-lib/https/api_client/api_client.h"')
    cli = replace(cli, '    args.add_argument("--keenetic-native")', '''    args.add_argument("--keenetic-server-policy").default_value(std::string{}).help("Native server policy JSON file");
    args.add_argument("--keenetic-skip-file").default_value(std::string{}).help("Temporary failed-server JSON array");
    args.add_argument("--keenetic-auto-select").default_value(false).implicit_value(true).help("Reselect favorites then other allowed servers");
    args.add_argument("--keenetic-probe-only").default_value(false).implicit_value(true).help("Check allowed FPTN logins without starting a tunnel");
    args.add_argument("--keenetic-probe-report").default_value(std::string{}).help("Private JSON server check report");
    args.add_argument("--keenetic-status-file").default_value(std::string{}).help("Private JSON tunnel status file");
    args.add_argument("--keenetic-native")''')
    cli = replace(cli, '      const auto preferred_names =', SELECTION+'      const auto preferred_names =')
    end = '''        pre_obtained_token = std::move(login_result->access_token);
      }
    } catch (const std::runtime_error& err) {'''
    cli = replace(cli, end, '''        pre_obtained_token = std::move(login_result->access_token);
      }
      } // original single-server behavior when no native policy is supplied
    } catch (const std::runtime_error& err) {''')
    cli = replace(cli, '    fptn::utils::WaitForSignal(vpn_client);', '    fptn::utils::WaitForSignal(vpn_client, !args.get<std::string>("--keenetic-server-policy").empty(), args.get<std::string>("--keenetic-status-file"));')
    cli = replace(cli, '    if (fptn::logger::init("fptn-client-cli")) {', '    if (fptn::logger::init(args.get<bool>("--keenetic-probe-only") ? "fptn-server-check" : "fptn-client-cli")) {')
    cli = replace(cli, '    /* check config */', '''    if (args.get<bool>("--keenetic-probe-only") && args.get<std::string>("--keenetic-server-policy").empty()) {
      throw std::runtime_error("Probe-only mode requires a server policy");
    }
    /* check config */''')
    loop = loop_path.read_text()
    loop = replace(loop, '#include "vpn/vpn_manager.h"', '#include "vpn/vpn_manager.h"\n#include "keenetic-server-policy.h"')
    loop = replace(loop, 'void WaitForSignal(fptn::vpn::VpnManager& vpn_client) {', 'void WaitForSignal(fptn::vpn::VpnManager& vpn_client, bool native_failover = false, const std::string& status_path = {}) {')
    loop = replace(loop, '  std::function<void()> check_connection = [&]() {', r'''  fptn::keenetic::LossWindow loss_window;
  std::string last_state;
  std::function<void()> check_connection = [&]() {
    const std::string state = !vpn_client.IsStarted() ? "stopped" : vpn_client.IsReconnecting() ? "reconnecting" : "connected";
    if (state != last_state && fptn::keenetic::WritePrivateJson(status_path, "{\"state\":\"" + state + "\"}")) last_state = state;
    if (native_failover && loss_window.Expired(vpn_client.IsReconnecting(), std::chrono::steady_clock::now())) {
      SPDLOG_WARN("KEENETIC_FAILOVER: all channels unavailable for 15 seconds");
      io_context.stop();
      return;
    }''')
    # Validate every anchor before writing any file.
    cli_path.write_text(cli)
    loop_path.write_text(loop)
    (root/'src/fptn-client/keenetic-server-policy.h').write_text(header.read_text())
    (loop_path.parent/'keenetic-server-policy.h').write_text(header.read_text())


if __name__ == '__main__':
    if len(sys.argv) != 3:
        sys.exit('usage: prepare-server-policy.py UPSTREAM HEADER')
    prepare(Path(sys.argv[1]), Path(sys.argv[2]))
