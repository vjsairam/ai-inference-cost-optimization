"""Bearer API-key authentication (FR-002).

Team identity derives only from the authenticated key. Keys are CSPRNG-generated
with >=128 bits of entropy; configuration stores SHA-256 lookup digests, never raw
keys. Comparison is constant-time across every configured entry. The optional
X-Gateway-Team header is an assertion: a mismatch is rejected and the header is
never used for identity or attribution.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

_KEY_PREFIX = "plab_"
_KEY_ENTROPY_BYTES = 32


class ApiKeyEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    team: str = Field(min_length=1, max_length=64)


class AuthConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    keys: list[ApiKeyEntry] = Field(min_length=1)


class AuthenticatedTeam(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    team: str


class AuthenticationFailed(Exception):
    """The bearer key is missing or does not match any configured digest."""


class TeamAssertionMismatch(Exception):
    """X-Gateway-Team was present and differs from the key-derived team."""

    def __init__(self, asserted: str, authenticated: str) -> None:
        super().__init__("team assertion does not match authenticated identity")
        self.asserted = asserted
        self.authenticated = authenticated


def generate_api_key() -> str:
    """Create a lab API key with 256 bits of CSPRNG entropy."""
    return _KEY_PREFIX + secrets.token_urlsafe(_KEY_ENTROPY_BYTES)


def hash_api_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def load_auth_config(path: str | Path) -> AuthConfig:
    config_path = Path(path)
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"cannot load auth configuration {config_path}: {exc}") from exc
    try:
        return AuthConfig.model_validate(raw)
    except ValidationError as exc:
        raise ValueError(f"invalid auth configuration {config_path}:\n{exc}") from exc


def authenticate(
    config: AuthConfig,
    bearer_key: str | None,
    asserted_team: str | None = None,
) -> AuthenticatedTeam:
    """Resolve the caller's team from the bearer key alone.

    Every configured digest is compared in constant time with no early exit so
    timing does not reveal which entry (if any) matched.
    """
    if not bearer_key:
        raise AuthenticationFailed("missing bearer key")

    presented = hash_api_key(bearer_key)
    matched_team: str | None = None
    for entry in config.keys:
        if hmac.compare_digest(presented, entry.sha256):
            matched_team = entry.team
    if matched_team is None:
        raise AuthenticationFailed("unknown API key")

    if asserted_team is not None and asserted_team != matched_team:
        raise TeamAssertionMismatch(asserted=asserted_team, authenticated=matched_team)
    return AuthenticatedTeam(team=matched_team)
