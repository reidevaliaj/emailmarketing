"""External verification-API adapter — STUB (Section 7b).

Deliberately unimplemented. This is the clearly-marked slot where a paid
provider (ZeroBounce / NeverBounce / Kickbox / etc.) could be plugged in later to
add deeper Layer-4 (mailbox) verification WITHOUT running SMTP probes from our
own sending IPs.

To enable in the future:
  1. Add the provider's API key to settings/.env.
  2. Implement ``verify_batch`` to call the provider's bulk endpoint and map its
     statuses onto VerificationResult (valid / invalid / unknown).
  3. Set ``VERIFY_PROVIDER=external``.

No paid API is integrated now; selecting this provider raises clearly.
"""

from __future__ import annotations

from .base import EmailVerification


class ExternalVerificationProvider:
    name = "external"

    def __init__(self, *args, **kwargs) -> None:  # pragma: no cover - stub
        pass

    async def verify_batch(self, emails: list[str]) -> list[EmailVerification]:  # pragma: no cover
        raise NotImplementedError(
            "External verification provider is a stub. Implement an adapter and "
            "set VERIFY_PROVIDER=external to enable Layer-4 verification."
        )
