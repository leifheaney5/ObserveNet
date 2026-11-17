from __future__ import annotations

from observenet_agent.procnet import parse_default_gateway, parse_proc_net_arp, parse_proc_net_sockets

TCP4 = """  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode
   0: 0100007F:1F90 00000000:0000 0A 00000000:00000000 00:00000000 00000000     0        0 1 1 0 100 0 0 10 0
   1: 00000000:0016 00000000:0000 0A 00000000:00000000 00:00000000 00000000     0        0 2 1 0 100 0 0 10 0
   2: 0A01A8C0:D431 5DB8D822:01BB 01 00000000:00000000 00:00000000 00000000  1000        0 3 1 0 20 4 30 10 -1
   3: 00000000:0000 00000000:0000 0A 00000000:00000000 00:00000000 00000000     0        0 4 1 0 100 0 0 10 0
   garbage line
"""
TCP6 = """  sl  local_address                         remote_address                        st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode
   0: 00000000000000000000000000000000:1F90 00000000000000000000000000000000:0000 0A 00000000:00000000 00:00000000 00000000     0        0 5 1 0 100 0 0 2 -1
   1: 00000000000000000000000001000000:0277 00000000000000000000000000000000:0000 0A 00000000:00000000 00:00000000 00000000     0        0 6 1 0 100 0 0 2 -1
   2: 0000000000000000FFFF00000100007F:0050 00000000000000000000000000000000:0000 0A 00000000:00000000 00:00000000 00000000     0        0 7 1 0 100 0 0 2 -1
"""
UDP4 = """   sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode ref pointer drops
  100: 00000000:0044 00000000:0000 07 00000000:00000000 00:00000000 00000000     0        0 8 2 0 0
  101: 0100007F:0035 00000000:0000 07 00000000:00000000 00:00000000 00000000     0        0 9 2 0 0
  102: 0A01A8C0:C000 08080808:0035 01 00000000:00000000 00:00000000 00000000  1000        0 10 2 0 0
"""
ARP = """IP address       HW type     Flags       HW address            Mask     Device
192.168.1.1      0x1         0x2         AA:BB:CC:00:11:22     *        eth0
192.168.1.50     0x1         0x0         00:00:00:00:00:00     *        eth0
192.168.1.77     0x1         0x2         00:00:00:00:00:00     *        eth0
192.168.1.20     0x1         0x6         3c:22:fb:01:02:03     *        wlan0
short line
"""
ROUTE = """Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT
wlan0\t00000000\t0101A8C0\t0003\t0\t0\t600\t00000000\t0\t0\t0
eth0\t00000000\t0100A8C0\t0003\t0\t0\t100\t00000000\t0\t0\t0
eth0\t0000A8C0\t00000000\t0001\t0\t0\t100\t00FFFFFF\t0\t0\t0
"""


def test_tcp4_only_listen_state_is_reported_with_little_endian_addresses() -> None:
    socks = parse_proc_net_sockets(TCP4, proto="tcp", ipv6=False)
    assert [(s.proto, s.address, s.port) for s in socks] == [
        ("tcp", "127.0.0.1", 8080),
        ("tcp", "0.0.0.0", 22),
    ]


def test_tcp6_wildcard_loopback_and_ipv4_mapped() -> None:
    socks = parse_proc_net_sockets(TCP6, proto="tcp", ipv6=True)
    assert [(s.address, s.port) for s in socks] == [("::", 8080), ("::1", 631), ("127.0.0.1", 80)]


def test_udp_only_unconnected_bound_sockets() -> None:
    socks = parse_proc_net_sockets(UDP4, proto="udp", ipv6=False)
    assert [(s.proto, s.address, s.port) for s in socks] == [("udp", "0.0.0.0", 68), ("udp", "127.0.0.1", 53)]


def test_empty_or_header_only_socket_table() -> None:
    assert parse_proc_net_sockets("", proto="tcp", ipv6=False) == []
    assert parse_proc_net_sockets(TCP4.splitlines()[0], proto="tcp", ipv6=False) == []


def test_arp_skips_incomplete_and_null_mac_entries() -> None:
    neigh = parse_proc_net_arp(ARP)
    assert [(n.ip, n.mac, n.interface) for n in neigh] == [
        ("192.168.1.1", "aa:bb:cc:00:11:22", "eth0"),
        ("192.168.1.20", "3c:22:fb:01:02:03", "wlan0"),
    ]


def test_default_gateway_picks_lowest_metric_default_route() -> None:
    assert parse_default_gateway(ROUTE) == ("192.168.0.1", "eth0")


def test_default_gateway_absent() -> None:
    assert parse_default_gateway("Iface\tDestination\tGateway\n") is None
    only_link = "Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT\neth0\t0000A8C0\t00000000\t0001\t0\t0\t100\t00FFFFFF\t0\t0\t0\n"
    assert parse_default_gateway(only_link) is None
