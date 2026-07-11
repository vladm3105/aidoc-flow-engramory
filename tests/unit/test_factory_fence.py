"""ADR-10 dev-tier fence at the factory layer (PLAN-002 Phase 2.4)."""
from __future__ import annotations

import pytest

from engramory.adapters.factory import require_dev_profile


def test_dev_profile_passes() -> None:
    assert require_dev_profile("cli", profile="dev") == "dev"


@pytest.mark.parametrize("profile", ["gcp", "azure", "prod", "staging"])
def test_non_dev_profiles_are_refused(profile: str) -> None:
    with pytest.raises(PermissionError, match="ADR-10"):
        require_dev_profile("cli", profile=profile)


def test_env_default_is_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ENGRAMORY_PROFILE", raising=False)
    assert require_dev_profile("cli") == "dev"


def test_env_non_dev_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENGRAMORY_PROFILE", "gcp")
    with pytest.raises(PermissionError, match="ADR-10"):
        require_dev_profile("cli")
