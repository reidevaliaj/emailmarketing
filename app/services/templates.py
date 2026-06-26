"""Template CRUD + validation (async, UI side).

Templates support a small merge-tag set and MUST contain {{unsubscribe_url}}
(compliance, Section 9). Preview renders with sample data so the admin can see
the result before sending.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import TemplateType
from app.models.template import Template
from app.services.merge import BASE_TAGS, build_context, has_unsubscribe, render, unknown_tags

SAMPLE_CONTEXT = build_context(
    email="jane.doe@example-corp.com",
    first_name="Jane",
    last_name="Doe",
    unsubscribe_url="https://dashboard.cod-st.com/u/SAMPLE",
    custom_fields={"company": "Example Corp"},
)


@dataclass
class TemplateValidation:
    has_unsubscribe: bool
    unknown_tags: list[str]

    @property
    def ok(self) -> bool:
        return self.has_unsubscribe


def validate_template(body: str, allowed_custom: set[str] | None = None) -> TemplateValidation:
    allowed = BASE_TAGS | (allowed_custom or set())
    return TemplateValidation(
        has_unsubscribe=has_unsubscribe(body),
        unknown_tags=sorted(unknown_tags(body, allowed)),
    )


def preview(body: str, *, html: bool) -> str:
    return render(body, SAMPLE_CONTEXT, html=html)


async def list_templates(session: AsyncSession) -> list[Template]:
    return list(await session.scalars(select(Template).order_by(Template.updated_at.desc())))


async def get_template(session: AsyncSession, template_id: int) -> Template | None:
    return await session.get(Template, template_id)


async def create_template(
    session: AsyncSession, name: str, type_: str, subject_default: str | None, body: str
) -> Template:
    tmpl = Template(
        name=name.strip() or "Untitled",
        type=TemplateType(type_).value if type_ in (t.value for t in TemplateType) else "plain",
        subject_default=subject_default,
        body=body or "",
    )
    session.add(tmpl)
    await session.flush()
    return tmpl


async def update_template(
    session: AsyncSession, template_id: int, **fields
) -> Template | None:
    tmpl = await session.get(Template, template_id)
    if tmpl is None:
        return None
    for key in ("name", "type", "subject_default", "body"):
        if key in fields and fields[key] is not None:
            setattr(tmpl, key, fields[key])
    await session.flush()
    return tmpl


async def delete_template(session: AsyncSession, template_id: int) -> bool:
    tmpl = await session.get(Template, template_id)
    if tmpl is None:
        return False
    await session.delete(tmpl)
    return True
