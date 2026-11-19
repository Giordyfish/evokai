"""Migrations: they build exactly the models' schema, and the latest one can be undone."""

from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext

from evokai.core.config import Settings
from evokai.db.models import Base

ROOT = Path(__file__).resolve().parents[1]


def _config(db: Path) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.attributes["settings"] = Settings(database_url=f"sqlite+aiosqlite:///{db}")
    return cfg


def _diff(db: Path) -> list[object]:
    engine = sa.create_engine(f"sqlite:///{db}")
    try:
        with engine.connect() as c:
            ctx = MigrationContext.configure(c, opts={"compare_type": True})
            return [
                d
                for d in compare_metadata(ctx, Base.metadata)
                if not (isinstance(d, tuple) and "alembic_version" in str(d))
            ]
    finally:
        engine.dispose()


def test_migrations_build_the_models_schema_and_the_latest_is_reversible(tmp_path: Path) -> None:
    db = tmp_path / "migrated.db"
    cfg = _config(db)
    command.upgrade(cfg, "head")
    assert _diff(db) == []
    command.downgrade(cfg, "-1")
    command.upgrade(cfg, "head")
    assert _diff(db) == []
