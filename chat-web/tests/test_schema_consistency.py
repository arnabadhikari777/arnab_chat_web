"""Keeps tools/db_inspect.py (which cannot import the app) in step with the models."""
import importlib.util
from pathlib import Path

from app.extensions import db

TOOL = Path(__file__).resolve().parent.parent / "tools" / "db_inspect.py"


def load_tool():
    spec = importlib.util.spec_from_file_location("db_inspect", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_inspector_expected_schema_matches_the_models(app):
    expected = load_tool().EXPECTED
    assert set(expected) == set(db.metadata.tables)
    for table, cols in expected.items():
        assert set(cols) == {c.name for c in db.metadata.tables[table].columns}, table


def test_inspector_knows_the_current_head():
    from tests.test_migrations import HEAD
    assert load_tool().HEAD == HEAD
