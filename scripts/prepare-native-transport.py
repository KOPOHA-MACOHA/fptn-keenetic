#!/usr/bin/env python3
"""Bind the original transport sockets, and remove irrelevant native gateway discovery."""
import pathlib
import sys


def replace(text, old, new, count=1):
    if text.count(old) != count:
        raise ValueError('Upstream transport layout changed: '+old[:100])
    return text.replace(old, new)


def prepare(root, header):
    cli = root/'src/fptn-client/fptn-client-cli.cpp'
    text = cli.read_text()
    text = '#include "common/network/keenetic-transport.h"\n'+text
    text = replace(text, '    args.add_argument("--keenetic-native")',
        '    args.add_argument("--keenetic-native-transport").default_value(false).implicit_value(true)\n'
        '        .help("Native uplink binding and gateway-free point-to-point transport");\n'
        '    args.add_argument("--keenetic-native")')
    anchor = '        args.get<std::string>("--out-network-interface");'
    text = replace(text, anchor, anchor+'\n'
        '    if (args.get<bool>("--keenetic-native"))\n'
        '      fptn::keenetic::ConfigureTransport(out_network_interface_name);')
    for family, discovery in [('ip', 'GetDefaultGatewayIPAddress'), ('ipv6', 'GetDefaultGatewayIPv6Address')]:
        old = f'            ? fptn::routing::{discovery}(tun_interface_name)'
        kind = 'IPv4Address' if family == 'ip' else 'IPv6Address'
        text = replace(text, old, f'            ? (args.get<bool>("--keenetic-native") ? fptn::common::network::{kind}()\n'
            f'                : fptn::routing::{discovery}(tun_interface_name))')
    text = replace(text, '    if (using_gateway_ip.IsEmpty()) {',
        '    if (using_gateway_ip.IsEmpty() && !args.get<bool>("--keenetic-native")) {')
    cli.write_text(text)
    for relative, name in [('api_client/api_client.cpp', 'api'), ('websocket_client/websocket_client.cpp', 'ws')]:
        path = root/'src/fptn-protocol-lib/https'/relative
        text = '#include "common/network/keenetic-transport.h"\n'+path.read_text()
        if name == 'api':
            text = replace(text, '    socket.open(boost::asio::ip::tcp::v4());',
                '    socket.open(boost::asio::ip::tcp::v4());\n'
                '    fptn::keenetic::BindTransport(socket.native_handle());')
            text = replace(text, 'boost::beast::get_lowest_layer(stream).connect(\n          resolve_result.results)',
                'fptn::keenetic::Connect(boost::beast::get_lowest_layer(stream),\n          resolve_result.results)', 2)
            text = replace(text, 'boost::beast::get_lowest_layer(stream).connect(resolve_result.results)',
                'fptn::keenetic::Connect(boost::beast::get_lowest_layer(stream), resolve_result.results)')
            layer = 'stream'
        else:
            layer = 'ws_'
        text = replace(text, f'co_await boost::beast::get_lowest_layer({layer}).async_connect(\n'
            '        results, boost::asio::redirect_error(boost::asio::use_awaitable, ec));',
            f'co_await fptn::keenetic::AsyncConnect(boost::beast::get_lowest_layer({layer}), results, ec);')
        path.write_text(text)
    (root/'src/common/network/keenetic-transport.h').write_text(header.read_text())


if __name__ == '__main__':
    prepare(pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]))
