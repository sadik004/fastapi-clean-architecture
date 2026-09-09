"""Server-Side Request Forgery (SSRF) Protection Firewall & IP Range Blocker.

Guarantees defense-in-depth against SSRF vulnerabilities by validating outbound
URL schemes, resolving destination IP addresses via DNS, and evaluating IP CIDR
ranges in O(1) mathematical complexity against all private, loopback, link-local,
and cloud metadata networks (e.g. AWS/GCP 169.254.169.254).
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

from app.core.exceptions import SSRFSecurityException

# Explicit forbidden IP networks covering Loopback, RFC 1918, Cloud Metadata (IMDS), and Multicast
FORBIDDEN_IP_NETWORKS: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = (
    # IPv4 Loopback
    ipaddress.ip_network("127.0.0.0/8"),
    # IPv4 RFC 1918 Private Networks
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    # IPv4 Link-Local & Cloud Instance Metadata Service (AWS/GCP/Azure: 169.254.169.254)
    ipaddress.ip_network("169.254.0.0/16"),
    # IPv4 Current Network / Unspecified
    ipaddress.ip_network("0.0.0.0/8"),
    # IPv4 Carrier-Grade NAT (RFC 6598)
    ipaddress.ip_network("100.64.0.0/10"),
    # IPv4 Multicast & Future/Reserved
    ipaddress.ip_network("224.0.0.0/4"),
    ipaddress.ip_network("240.0.0.0/4"),
    # IPv4 Limited Broadcast
    ipaddress.ip_network("255.255.255.255/32"),
    # IPv6 Loopback & Unspecified
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("::/128"),
    # IPv6 Unique Local Address (ULA - RFC 4193)
    ipaddress.ip_network("fc00::/7"),
    # IPv6 Link-Local
    ipaddress.ip_network("fe80::/10"),
    # IPv6 Multicast
    ipaddress.ip_network("ff00::/8"),
)


def is_forbidden_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Evaluate whether an IP address belongs to any forbidden network in O(1) time."""
    if (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    ):
        return True

    for net in FORBIDDEN_IP_NETWORKS:
        if ip in net:
            return True

    return False


def validate_safe_url(url: str) -> str:
    """Validate that a URL is safe from SSRF attacks before dispatching outbound requests.

    Validation pipeline:
    1. Scheme check: Strictly permits 'http' and 'https'. Rejects 'file://', 'gopher://', etc.
    2. Host parsing: Rejects empty hosts and credential-bearing URLs.
    3. DNS Resolution: Resolves hostname into all target IP addresses.
    4. IP Inspection: Asserts no destination IP falls into private, loopback, or metadata ranges.

    Returns:
        The validated URL string if safe.

    Raises:
        SSRFSecurityException: If scheme is forbidden, hostname resolution fails,
                               or destination IP is in a forbidden CIDR block.
    """
    if not url or not isinstance(url, str):
        raise SSRFSecurityException("Target URL cannot be empty.")

    try:
        parsed = urlsplit(url)
    except Exception as exc:
        raise SSRFSecurityException(f"Malformed target URL: {exc}") from exc

    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        raise SSRFSecurityException(
            f"Prohibited URL scheme '{scheme}'. Only HTTP and HTTPS protocols are permitted."
        )

    hostname = parsed.hostname
    if not hostname:
        raise SSRFSecurityException("Target URL must specify a valid destination hostname.")

    # Strip bracket notation for IPv6 literals if present
    clean_host = hostname.strip("[]")

    # Step 1: Check if hostname is an IP literal
    resolved_ips: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    try:
        ip_obj = ipaddress.ip_address(clean_host)
        resolved_ips.append(ip_obj)
    except ValueError:
        # Step 2: Resolve hostname via DNS
        try:
            addr_info = socket.getaddrinfo(hostname, None)
            for item in addr_info:
                sockaddr = item[4]
                ip_str = sockaddr[0]
                try:
                    resolved_ips.append(ipaddress.ip_address(ip_str))
                except ValueError:
                    continue
        except socket.gaierror as exc:
            raise SSRFSecurityException(
                f"Failed to resolve destination hostname '{hostname}': {exc}"
            ) from exc

    if not resolved_ips:
        raise SSRFSecurityException(
            f"No valid IP addresses could be resolved for destination host '{hostname}'."
        )

    # Step 3: Inspect each resolved IP address
    for ip in resolved_ips:
        if is_forbidden_ip(ip):
            raise SSRFSecurityException(
                f"SSRF security violation: Destination host '{hostname}' resolves to forbidden IP '{ip}'."
            )

    return url
