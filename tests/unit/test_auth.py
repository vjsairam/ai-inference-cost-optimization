"""FR-002: key hashing, constant-time lookup, and team assertion rules."""

from __future__ import annotations

import pytest

from prospera_gateway.security import (
    ApiKeyEntry,
    AuthConfig,
    AuthenticationFailed,
    TeamAssertionMismatch,
    authenticate,
    generate_api_key,
    hash_api_key,
    load_auth_config,
)


@pytest.fixture()
def config() -> AuthConfig:
    return AuthConfig(
        keys=[
            ApiKeyEntry(sha256=hash_api_key("plab_team_a_key_0123456789"), team="team-a"),
            ApiKeyEntry(sha256=hash_api_key("plab_team_b_key_0123456789"), team="team-b"),
        ]
    )


def test_generated_keys_are_high_entropy_and_unique() -> None:
    keys = {generate_api_key() for _ in range(64)}
    assert len(keys) == 64
    assert all(key.startswith("plab_") and len(key) >= 40 for key in keys)


def test_authenticate_resolves_team_from_key_only(config: AuthConfig) -> None:
    identity = authenticate(config, "plab_team_b_key_0123456789")
    assert identity.team == "team-b"


def test_missing_key_fails(config: AuthConfig) -> None:
    with pytest.raises(AuthenticationFailed):
        authenticate(config, None)
    with pytest.raises(AuthenticationFailed):
        authenticate(config, "")


def test_unknown_key_fails(config: AuthConfig) -> None:
    with pytest.raises(AuthenticationFailed):
        authenticate(config, "plab_not_configured")


def test_matching_team_assertion_is_accepted(config: AuthConfig) -> None:
    identity = authenticate(config, "plab_team_a_key_0123456789", asserted_team="team-a")
    assert identity.team == "team-a"


def test_mismatched_team_assertion_is_rejected(config: AuthConfig) -> None:
    """The header never overrides key-derived identity (spec §8.6)."""
    with pytest.raises(TeamAssertionMismatch) as excinfo:
        authenticate(config, "plab_team_a_key_0123456789", asserted_team="team-b")
    assert excinfo.value.authenticated == "team-a"


def test_load_auth_config_rejects_raw_keys(tmp_path) -> None:
    path = tmp_path / "auth.yaml"
    path.write_text("keys:\n  - sha256: not-a-digest\n    team: x\n")
    with pytest.raises(ValueError, match="invalid auth configuration"):
        load_auth_config(path)


def test_load_auth_config_roundtrip(tmp_path) -> None:
    digest = hash_api_key("plab_example")
    path = tmp_path / "auth.yaml"
    path.write_text(f"keys:\n  - sha256: {digest}\n    team: lab\n")
    config = load_auth_config(path)
    assert authenticate(config, "plab_example").team == "lab"
