"""Merge-tag rendering for templates (Section 8).

Supported tags: ``{{first_name}}``, ``{{last_name}}``, ``{{email}}``,
``{{unsubscribe_url}}`` plus any per-contact custom field.

SAFETY (Section 9): template bodies are admin-authored (trusted), but merge
VALUES come from imported CSV data (untrusted). For HTML templates we therefore
HTML-escape every substituted value to prevent injection. We do NOT use a full
template engine on email bodies — substitution is limited to this controlled tag
set, so admin content can't trigger template-injection or arbitrary execution.
"""

from __future__ import annotations

import re

from markupsafe import escape

# {{ tag }} with optional surrounding whitespace; tag is a simple identifier.
_TAG_RE = re.compile(r"{{\s*([a-zA-Z0-9_]+)\s*}}")

BASE_TAGS = {"first_name", "last_name", "email", "unsubscribe_url"}
UNSUBSCRIBE_TAG = "unsubscribe_url"


def find_tags(body: str) -> set[str]:
    """All merge tag names referenced in the body."""
    return {m.group(1) for m in _TAG_RE.finditer(body or "")}


def has_unsubscribe(body: str) -> bool:
    return UNSUBSCRIBE_TAG in find_tags(body)


def unknown_tags(body: str, allowed: set[str] | None = None) -> set[str]:
    """Tags referenced that aren't in ``allowed`` (defaults to BASE_TAGS).

    Custom-field tags are dynamic, so callers that know a list's custom fields
    can pass an expanded ``allowed`` set for stricter validation.
    """
    allowed = BASE_TAGS if allowed is None else allowed
    return find_tags(body) - allowed


def render(body: str, context: dict, *, html: bool) -> str:
    """Substitute known tags from ``context``. Unknown tags render as empty.

    Values are HTML-escaped when ``html`` is True.
    """
    if not body:
        return ""

    def repl(match: re.Match) -> str:
        tag = match.group(1)
        value = context.get(tag, "")
        if value is None:
            value = ""
        value = str(value)
        return str(escape(value)) if html else value

    return _TAG_RE.sub(repl, body)


def build_context(
    *,
    email: str,
    first_name: str | None,
    last_name: str | None,
    unsubscribe_url: str,
    custom_fields: dict | None = None,
) -> dict:
    """Assemble the substitution context for one recipient.

    Custom fields are included but never override the base tags.
    """
    ctx: dict = dict(custom_fields or {})
    ctx.update(
        {
            "email": email,
            "first_name": first_name or "",
            "last_name": last_name or "",
            "unsubscribe_url": unsubscribe_url,
        }
    )
    return ctx
