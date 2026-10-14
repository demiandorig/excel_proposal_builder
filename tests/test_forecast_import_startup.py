from app.services.forecast_import import service


def test_published_startup_skips_schema_migrations(monkeypatch):
    from app.services import migrations

    monkeypatch.setenv("REPLIT_DEPLOYMENT", "1")

    def unexpected_migration_call():
        raise AssertionError("published startup must leave schema DDL to Publish")

    monkeypatch.setattr(migrations, "apply_pending", unexpected_migration_call)
    assert service._apply_startup_migrations() == []


def test_development_startup_still_applies_schema_migrations(monkeypatch):
    from app.services import migrations

    monkeypatch.delenv("REPLIT_DEPLOYMENT", raising=False)
    monkeypatch.setattr(migrations, "apply_pending", lambda: ["0001"])
    assert service._apply_startup_migrations() == ["0001"]
