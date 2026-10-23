import os

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from tests.conftest import ROOT_DIR


def test_alembic_upgrade_and_downgrade(tmp_path):
    url = os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{tmp_path / 'm.sqlite'}"
    config = Config(str(ROOT_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT_DIR / "alembic"))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")
    engine = create_engine(url)
    tables = set(inspect(engine).get_table_names())
    assert {"sources", "fetch_logs"} <= tables
    columns = {c["name"] for c in inspect(engine).get_columns("sources")}
    assert {"tier", "terms_url", "reviewed_at", "reviewer", "allowed_paths", "paused_at"} <= columns
    command.downgrade(config, "base")
    assert not {"sources", "fetch_logs"} & set(inspect(engine).get_table_names())
    engine.dispose()
