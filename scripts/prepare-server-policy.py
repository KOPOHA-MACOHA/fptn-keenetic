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
        std::vector<std::string> failed;
        const auto skip_path = args.get<std::string>("--keenetic-skip-file");
        if (!skip_path.empty()) failed = read_json(skip_path).get<std::vector<std::string>>();
        const auto manual = mode == "manual" && !args.get<bool>("--keenetic-auto-select")
                              ? preferred_servers : std::string{};
        auto servers = config.GetServers();
        for (auto& server : servers) {
          if (server.username.empty()) server.username = config.GetUsername();
          if (server.password.empty()) server.password = config.GetPassword();
        }
        const auto tiers = fptn::keenetic::ServerTiers(servers, favorites, excluded, failed, manual);
        if (tiers.empty()) {
          SPDLOG_ERROR("KEENETIC_NO_ELIGIBLE_SERVERS");
          return EXIT_FAILURE;
        }
        auto result = fptn::keenetic::SelectServer<fptn::utils::speed_estimator::LoginResult>(tiers,
          [&](const fptn::utils::speed_estimator::ServerInfo& server)
              -> std::optional<fptn::utils::speed_estimator::LoginResult> {
            SPDLOG_INFO("KEENETIC_PROBE: {}", server.name);
            fptn::protocol::https::ApiClient client(server.host, server.port, sni,
                server.md5_fingerprint, censorship_strategy);
            const auto body = nlohmann::json{{"username", server.username}, {"password", server.password}}.dump();
            const auto response = client.Post("/api/v1/login", body, "application/json", 5);
            if (response.code != 200) return std::nullopt;
            const auto message = response.Json();
            const auto jwt = message.at("access_token").get<std::string>();
            if (jwt.empty()) return std::nullopt;
            return fptn::utils::speed_estimator::LoginResult{.server=server, .access_token=jwt};
          });
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
    args.add_argument("--keenetic-native")''')
    cli = replace(cli, '      const auto preferred_names =', SELECTION+'      const auto preferred_names =')
    end = '''        pre_obtained_token = std::move(login_result->access_token);
      }
    } catch (const std::runtime_error& err) {'''
    cli = replace(cli, end, '''        pre_obtained_token = std::move(login_result->access_token);
      }
      } // original single-server behavior when no native policy is supplied
    } catch (const std::runtime_error& err) {''')
    cli = replace(cli, '    fptn::utils::WaitForSignal(vpn_client);', '    fptn::utils::WaitForSignal(vpn_client, !args.get<std::string>("--keenetic-server-policy").empty());')
    loop = loop_path.read_text()
    loop = replace(loop, '#include "vpn/vpn_manager.h"', '#include "vpn/vpn_manager.h"\n#include "keenetic-server-policy.h"')
    loop = replace(loop, 'void WaitForSignal(fptn::vpn::VpnManager& vpn_client) {', 'void WaitForSignal(fptn::vpn::VpnManager& vpn_client, bool native_failover = false) {')
    loop = replace(loop, '  std::function<void()> check_connection = [&]() {', '''  fptn::keenetic::LossWindow loss_window;
  std::function<void()> check_connection = [&]() {
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
