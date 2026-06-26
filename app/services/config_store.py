"""Runtime-editable configuration (free-provider blocklist, Section 8).

Stored in ``app_settings`` so it can be edited from the UI without a redeploy.
The consumer/free-mail filter defaults ON: the owner only targets business
domains and wants these excluded to protect sender reputation.

Pattern matching:
  * ``"gmail.com"``  -> exact domain match
  * ``"hotmail."``   -> prefix match (hotmail.com, hotmail.co.uk, ...)
  * ``"gmx.*"``      -> prefix match on ``gmx.``
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.config import settings
from app.models.app_setting import AppSetting

KEY_ENABLED = "free_provider_filter_enabled"
KEY_DOMAINS = "free_provider_domains"

# Seed/default consumer domains the owner never gets B2B business from.
DEFAULT_FREE_PROVIDER_DOMAINS: list[str] = [
    "gmail.com", "googlemail.com",
    "hotmail.", "outlook.", "live.", "msn.com",
    "yahoo.", "ymail.com",
    "aol.com", "icloud.com", "me.com",
    "gmx.*", "proton.me", "protonmail.com",
]


def is_free_provider(domain: str, patterns: list[str]) -> bool:
    domain = domain.lower()
    for raw in patterns:
        p = raw.strip().lower()
        if not p:
            continue
        if p.endswith(".*"):
            if domain.startswith(p[:-1]):  # "gmx.*" -> startswith "gmx."
                return True
        elif p.endswith("."):
            if domain.startswith(p):       # "hotmail." -> startswith "hotmail."
                return True
        elif domain == p:
            return True
    return False


# --- sync (CSV import task) ------------------------------------------------

def get_free_provider_config_sync(session: Session) -> tuple[bool, list[str]]:
    enabled = session.get(AppSetting, KEY_ENABLED)
    domains = session.get(AppSetting, KEY_DOMAINS)
    enabled_val = (
        bool(enabled.value) if enabled is not None
        else settings.free_provider_filter_default
    )
    domains_val = list(domains.value) if domains is not None else list(DEFAULT_FREE_PROVIDER_DOMAINS)
    return enabled_val, domains_val


# --- async (UI) ------------------------------------------------------------

async def get_free_provider_config(session: AsyncSession) -> tuple[bool, list[str]]:
    enabled = await session.get(AppSetting, KEY_ENABLED)
    domains = await session.get(AppSetting, KEY_DOMAINS)
    enabled_val = (
        bool(enabled.value) if enabled is not None
        else settings.free_provider_filter_default
    )
    domains_val = list(domains.value) if domains is not None else list(DEFAULT_FREE_PROVIDER_DOMAINS)
    return enabled_val, domains_val


async def set_free_provider_config(
    session: AsyncSession, enabled: bool, domains: list[str]
) -> None:
    await _upsert(session, KEY_ENABLED, bool(enabled))
    cleaned = [d.strip().lower() for d in domains if d.strip()]
    await _upsert(session, KEY_DOMAINS, cleaned)


async def _upsert(session: AsyncSession, key: str, value) -> None:
    row = await session.get(AppSetting, key)
    if row is None:
        session.add(AppSetting(key=key, value=value))
    else:
        row.value = value
    await session.flush()
