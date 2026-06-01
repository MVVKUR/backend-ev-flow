import pytest

pytest.importorskip("sqlalchemy")


def test_engine_uses_psycopg_driver(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/x")
    import importlib
    from api import db
    importlib.reload(db)
    assert db.engine.url.drivername == "postgresql+psycopg"
    assert db.engine.url.database == "x"
