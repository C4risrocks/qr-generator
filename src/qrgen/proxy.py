"""Client IP resolution behind a trusted reverse proxy (e.g. Traefik)."""

from __future__ import annotations

import ipaddress
import logging
import os

from fastapi import Request

logger = logging.getLogger("qrgen.proxy")

_OPEN_NETWORKS = ("0.0.0.0/0", "::/0")


def trusted_proxy_networks() -> list[ipaddress._BaseNetwork]:
    """Parse FORWARDED_ALLOW_IPS (IPs or CIDRs, comma/space separated).

    Open catch-alls such as ``0.0.0.0/0`` are rejected outright: trusting
    them would let any client spoof its IP via X-Forwarded-For.
    """
    networks: list[ipaddress._BaseNetwork] = []
    for part in os.environ.get("FORWARDED_ALLOW_IPS", "").replace(",", " ").split():
        try:
            network = ipaddress.ip_network(part, strict=False)
        except ValueError:
            logger.warning("ignoring invalid FORWARDED_ALLOW_IPS entry: %r", part)
            continue
        if str(network) in _OPEN_NETWORKS:
            logger.error("refusing open FORWARDED_ALLOW_IPS entry: %r", part)
            continue
        networks.append(network)
    return networks


def client_ip(request: Request) -> str:
    """Best-effort client IP address.

    ``X-Forwarded-For`` is only honored when the direct peer is a configured
    trusted proxy, and entries are then read right-to-left: a trusted proxy
    appends the address it actually sees, so any attacker-controlled entries
    sit to the left of it. The rightmost valid IP is therefore the only one
    that cannot be spoofed.
    """
    direct = request.client.host if request.client else "unknown"
    forwarded_for = request.headers.get("x-forwarded-for", "")
    if not forwarded_for:
        return direct
    try:
        peer = ipaddress.ip_address(direct)
    except ValueError:
        return direct
    if not any(peer in net for net in trusted_proxy_networks()):
        return direct
    for candidate in reversed(forwarded_for.split(",")):
        try:
            ipaddress.ip_address(candidate.strip())
        except ValueError:
            continue
        return candidate.strip()
    return direct
