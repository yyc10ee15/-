"""Application identities for Google accounts and temporary guests."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class UserIdentity:
    """A stable internal key plus the public name shown in the UI."""

    user_key: str
    display_name: str
    auth_type: str
    email: str = ""
    picture_url: str = ""

    @property
    def is_google(self) -> bool:
        return self.auth_type == "google"
