import apgkit


def test_installed_kit_has_insights_and_masking():
    """Database insights, column masking and the after-migrate hook arrived in agentpg-kit 0.3.0; the masking re-assert loop (`reassert_forever`) in 0.3.1.

    Uses `apgkit.__version__` (the single source in the kit) rather than the pip metadata, which stays stale in an editable install
    until it is reinstalled."""
    parts = tuple(int(p) for p in apgkit.__version__.split(".")[:3])
    assert parts >= (0, 3, 1), f"agentpg-kit {apgkit.__version__} is too old for insights, masking and after_migrate"
    from apgkit import insights, masking, migrate
    assert hasattr(insights, "insights_payload") and hasattr(masking, "mask_view")
    assert "after_migrate" in migrate.run.__code__.co_varnames
