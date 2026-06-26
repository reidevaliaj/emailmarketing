"""Best-effort deliverability preflight checks (Section 9).

Checks SPF / DKIM / DMARC / MX (and PTR for any configured sending IPs) for the
sending domain and surfaces warnings. Never hard-blocks — misconfigured DNS is
common during setup; we warn loudly instead. Results are logged at startup and
shown on the dashboard.
"""

from __future__ import annotations

from dataclasses import dataclass

import dns.resolver
import dns.reversename

from app.config import settings
from app.logging import get_logger

logger = get_logger(__name__)


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


def _resolver() -> dns.resolver.Resolver:
    """Resolver pinned to public DNS — the host's own resolver can serve stale
    cache (e.g. an SPF record that was just changed)."""
    r = dns.resolver.Resolver(configure=False)
    r.nameservers = settings.dns_resolvers_list or ["1.1.1.1", "8.8.8.8"]
    r.lifetime = 5.0
    r.timeout = 5.0
    return r


def _txt(domain: str) -> list[str]:
    out: list[str] = []
    answer = _resolver().resolve(domain, "TXT")
    for rec in answer:
        out.append(b"".join(rec.strings).decode(errors="replace"))
    return out


def _check_spf(domain: str) -> Check:
    try:
        records = _txt(domain)
        spf = [r for r in records if r.lower().startswith("v=spf1")]
        if spf:
            return Check("SPF", True, spf[0][:120])
        return Check("SPF", False, "no v=spf1 TXT record found")
    except Exception as exc:  # noqa: BLE001
        return Check("SPF", False, f"lookup failed: {type(exc).__name__}")


def _check_dmarc(domain: str) -> Check:
    try:
        records = _txt(f"_dmarc.{domain}")
        dmarc = [r for r in records if r.lower().startswith("v=dmarc1")]
        if dmarc:
            return Check("DMARC", True, dmarc[0][:120])
        return Check("DMARC", False, "no v=DMARC1 record at _dmarc")
    except Exception as exc:  # noqa: BLE001
        return Check("DMARC", False, f"lookup failed: {type(exc).__name__}")


def _check_dkim(domain: str, selector: str) -> Check:
    name = f"{selector}._domainkey.{domain}"
    try:
        records = _txt(name)
        dkim = [r for r in records if "p=" in r]
        if dkim:
            return Check("DKIM", True, f"{name} present")
        return Check("DKIM", False, f"no key at {name}")
    except Exception as exc:  # noqa: BLE001
        return Check("DKIM", False, f"{name}: {type(exc).__name__}")


def _check_mx(domain: str) -> Check:
    # A dedicated SEND-ONLY domain intentionally has no MX (bounces return via
    # the Postal return-path, not the domain's own MX). So "no MX" is fine here.
    try:
        answer = _resolver().resolve(domain, "MX")
        return Check("MX", True, f"{len(answer)} record(s) (optional for send-only)")
    except Exception:  # noqa: BLE001
        return Check("MX", True, "none — expected for a send-only domain")


def _check_ptr(ip: str) -> Check:
    try:
        rev = dns.reversename.from_address(ip)
        answer = _resolver().resolve(rev, "PTR")
        host = str(answer[0]).rstrip(".")
        return Check(f"PTR {ip}", True, host)
    except Exception as exc:  # noqa: BLE001
        return Check(f"PTR {ip}", False, f"no/failed PTR: {type(exc).__name__}")


def run_preflight() -> list[Check]:
    domain = settings.sending_domain
    checks = [
        _check_spf(domain),
        _check_dkim(domain, settings.dkim_selector),
        _check_dmarc(domain),
        _check_mx(domain),
    ]
    for ip in settings.sending_ips_list:
        checks.append(_check_ptr(ip))
    for c in checks:
        (logger.info if c.ok else logger.warning)("preflight %s: %s", c.name, c.detail)
    return checks
