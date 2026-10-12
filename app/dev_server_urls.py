"""Print local / LAN URLs when running the Flask dev server for device testing."""

from __future__ import annotations

import socket


def collect_lan_ipv4_addresses() -> list[str]:
    """Private LAN IPv4 addresses for this machine (not internet-facing)."""
    found: set[str] = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, family=socket.AF_INET):
            ip = str(info[4][0])
            if ip and not ip.startswith("127."):
                found.add(ip)
    except OSError:
        pass
    return sorted(found)


def print_dev_access_urls(*, host: str, port: int) -> None:
    """Show URLs for laptop + phone/tablet on the same Wi-Fi (dev only)."""
    line = "=" * 58
    print("")
    print(line)
    print("  DEV SERVER — same Wi-Fi device testing only")
    print(line)
    print(f"  This PC:       http://127.0.0.1:{port}/")
    if host in {"0.0.0.0", "::"}:
        ips = collect_lan_ipv4_addresses()
        if ips:
            print("  Phone / tablet:")
            for ip in ips:
                print(f"                 http://{ip}:{port}/")
        else:
            print("  Phone / tablet:  http://<YOUR-LAPTOP-IP>:{}/".format(port))
            print("                   (run ipconfig — use Wi-Fi IPv4 Address)")
    print("")
    print("  Development only — not for public internet exposure.")
    print("  Windows: allow Python through Firewall on Private networks.")
    print(line)
    print("")
