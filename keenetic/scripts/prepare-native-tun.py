#!/usr/bin/env python3
"""Apply after prepare-connect-test.py; keep routing entirely outside FPTN."""
import pathlib
import sys


def prepare(root, header):
    cli = root / 'src/fptn-client/fptn-client-cli.cpp'
    text = cli.read_text()
    anchor = '    args.add_argument("--keenetic-connect")'
    if text.count(anchor) != 1:
        raise ValueError('Connection opt-in argument changed')
    text = text.replace(anchor,
        '    args.add_argument("--keenetic-native")\n'
        '        .default_value(false).implicit_value(true)\n'
        '        .help("Attach existing Keenetic OpkgTunN; firmware owns addressing and routing");\n' + anchor, 1)
    old = '''    if (args.get<std::string>("--tun-interface-name") != "fptn0") {
      std::cerr << "Tunnel test requires --tun-interface-name fptn0.\\n";
      return EXIT_FAILURE;
    }'''
    new = '''    const bool keenetic_native = args.get<bool>("--keenetic-native");
    const auto keenetic_tun = args.get<std::string>("--tun-interface-name");
    const auto keenetic_suffix = keenetic_tun.size() > 7 ? keenetic_tun.substr(7) : "";
    const bool keenetic_native_name = keenetic_tun.rfind("opkgtun", 0) == 0 &&
        keenetic_tun.size() < 16 && !keenetic_suffix.empty() &&
        keenetic_suffix.find_first_not_of("0123456789") == std::string::npos &&
        (keenetic_suffix.size() == 1 || keenetic_suffix.front() != '0');
    if ((keenetic_native && !keenetic_native_name) ||
        (!keenetic_native && keenetic_tun != "fptn0")) {
      std::cerr << "Native mode requires opkgtunN; legacy test mode requires fptn0.\\n";
      return EXIT_FAILURE;
    }'''
    if text.count(old) != 1:
        raise ValueError('Interface guard changed')
    text = text.replace(old, new, 1)
    cli.write_text(text)
    target = root / 'src/common/network/tun/linux_tun_device.h'
    original = target.read_text()
    if 'class LinuxTunDevice {' not in original or 'tun_ = std::make_unique<tuntap::tun>();' not in original:
        raise ValueError('Linux TUN implementation changed')
    target.write_text(header.read_text())
    print('Native mode: attach existing opkgtunN, verify IPv4/MTU/up, never mutate interface configuration')


if __name__ == '__main__':
    if len(sys.argv) != 3:
        sys.exit('usage: prepare-native-tun.py UPSTREAM_DIR HEADER')
    prepare(pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]))
