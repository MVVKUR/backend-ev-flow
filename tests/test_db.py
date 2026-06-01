import pytest

pytest.importorskip("sqlalchemy")


def test_engine_uses_psycopg_driver(monkeypatch):
    import importlib

    from api import db

    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/x")
    importlib.reload(db)
    try:
        assert db.engine.url.drivername == "postgresql+psycopg"
        assert db.engine.url.database == "x"
    finally:
        # Reloading db rebound the shared module-level engine to the fake URL.
        # Restore the real env and reload so later tests (e.g. test_stations_db)
        # use the genuine DATABASE_URL instead of this throwaway one.
        monkeypatch.undo()
        importlib.reload(db)
