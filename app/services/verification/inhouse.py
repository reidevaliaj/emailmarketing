"""In-house verification: Layer 1 (syntax) + Layer 2 (domain/MX).

Performance (Section 7b): a 10k B2B list has only a few thousand unique domains,
so we check each domain ONCE (cached) and run DNS lookups concurrently
(I/O-bound). With caching + concurrency a 10k list verifies in well under 2 min.

Domain status mapping:
  * has MX (or A-record fallback)   -> domain can receive mail  -> VALID
  * NXDOMAIN / no MX and no A        -> domain can't receive     -> INVALID
  * DNS timeout / resolver error     -> inconclusive             -> UNKNOWN
    (short timeout; never hard-reject a flaky DNS response — re-checkable later)

NOTE: Layer 2 "valid" means the DOMAIN accepts mail, not that the mailbox
exists (that is Layer 4 / SMTP probing, intentionally NOT built — it would risk
our sending IPs).
"""

from __future__ import annotations

import asyncio

import dns.asyncresolver
import dns.resolver

from app.config import settings
from app.logging import get_logger
from app.models.enums import VerificationResult
from app.services.normalize import domain_of, validate_syntax

from .base import EmailVerification

logger = get_logger(__name__)

# Redis cache TTLs (seconds). Don't cache UNKNOWN (transient).
_TTL_VALID = 24 * 3600
_TTL_INVALID = 6 * 3600


class InHouseVerificationProvider:
    name = "inhouse"

    def __init__(
        self,
        redis=None,
        timeout: float | None = None,
        concurrency: int | None = None,
    ) -> None:
        self._redis = redis
        self._timeout = timeout if timeout is not None else settings.verify_dns_timeout_seconds
        self._concurrency = concurrency or settings.verify_dns_concurrency
        # Per-run in-memory cache (the single biggest speedup for one list).
        self._mem: dict[str, tuple[VerificationResult, str | None]] = {}

    # --- domain resolution ------------------------------------------------

    async def _check_domain(self, domain: str) -> tuple[VerificationResult, str | None]:
        """Resolve a single domain's mail-receiving status (uncached)."""
        resolver = dns.asyncresolver.Resolver(configure=False)
        resolver.nameservers = settings.dns_resolvers_list or ["1.1.1.1", "8.8.8.8"]
        resolver.timeout = self._timeout
        resolver.lifetime = self._timeout
        try:
            answer = await resolver.resolve(domain, "MX")
            if len(answer) > 0:
                return VerificationResult.VALID, "mx"
            # Empty MX set — fall through to A-record fallback.
        except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN):
            pass
        except (dns.resolver.Timeout, dns.resolver.LifetimeTimeout):
            return VerificationResult.UNKNOWN, "dns_timeout"
        except Exception as exc:  # resolver/network error — inconclusive
            return VerificationResult.UNKNOWN, f"dns_error:{type(exc).__name__}"

        # A-record fallback: a domain with an A record can still accept mail.
        try:
            await resolver.resolve(domain, "A")
            return VerificationResult.VALID, "a_fallback"
        except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN):
            return VerificationResult.INVALID, "no_mx_no_a"
        except (dns.resolver.Timeout, dns.resolver.LifetimeTimeout):
            return VerificationResult.UNKNOWN, "dns_timeout"
        except Exception as exc:
            return VerificationResult.UNKNOWN, f"dns_error:{type(exc).__name__}"

    async def _domain_status(self, domain: str) -> tuple[VerificationResult, str | None]:
        """Cached domain status: memory -> Redis -> DNS."""
        if domain in self._mem:
            return self._mem[domain]

        if self._redis is not None:
            cached = self._redis.get(f"mxcache:{domain}")
            if cached:
                res = VerificationResult(cached)
                self._mem[domain] = (res, "cache")
                return self._mem[domain]

        status, reason = await self._check_domain(domain)
        self._mem[domain] = (status, reason)

        if self._redis is not None and status != VerificationResult.UNKNOWN:
            ttl = _TTL_VALID if status == VerificationResult.VALID else _TTL_INVALID
            try:
                self._redis.set(f"mxcache:{domain}", status.value, ex=ttl)
            except Exception:  # cache is best-effort
                pass
        return status, reason

    # --- batch entry point ------------------------------------------------

    async def verify_batch(self, emails: list[str]) -> list[EmailVerification]:
        # Layer 1 — syntax (fast, in-process). Collect domains needing DNS.
        syntax: dict[str, tuple[bool, str | None]] = {}
        domains: set[str] = set()
        for email in emails:
            check = validate_syntax(email)
            syntax[email] = (check.ok, check.error)
            if check.ok and check.normalized:
                domains.add(domain_of(check.normalized))

        # Layer 2 — resolve each unique domain once, concurrently.
        sem = asyncio.Semaphore(self._concurrency)

        async def resolve(domain: str):
            async with sem:
                return domain, await self._domain_status(domain)

        domain_results: dict[str, tuple[VerificationResult, str | None]] = {}
        if domains:
            for fut in asyncio.as_completed([resolve(d) for d in domains]):
                domain, status = await fut
                domain_results[domain] = status

        # Map per-email outcomes.
        out: list[EmailVerification] = []
        for email in emails:
            ok, err = syntax[email]
            if not ok:
                out.append(
                    EmailVerification(email, VerificationResult.INVALID, f"syntax:{err}")
                )
                continue
            d = domain_of(email)
            status, reason = domain_results.get(d, (VerificationResult.UNKNOWN, "unresolved"))
            out.append(EmailVerification(email, status, reason))
        return out
