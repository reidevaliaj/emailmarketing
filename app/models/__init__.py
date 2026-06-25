"""SQLAlchemy models.

Importing this package registers every model on ``Base.metadata`` (needed for
Alembic autogenerate and for ``create_all`` in tests).
"""

from app.models.app_setting import ApiKey, AppSetting
from app.models.base import Base
from app.models.campaign import Campaign, CampaignRecipient, EmailEvent
from app.models.contact import Contact
from app.models.contact_list import ContactList
from app.models.suppression import Suppression
from app.models.template import Template
from app.models.user import User

__all__ = [
    "Base",
    "User",
    "ContactList",
    "Contact",
    "Suppression",
    "Template",
    "Campaign",
    "CampaignRecipient",
    "EmailEvent",
    "AppSetting",
    "ApiKey",
]
