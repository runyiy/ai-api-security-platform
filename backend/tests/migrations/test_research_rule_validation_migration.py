from uuid import uuid4
import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.engine import make_url
from app.core.config import settings
from app.db.base import Base
from app.db.session import engine
from tests.research_rule_fixtures import (  # noqa: F401
    rule_pair, knowledge_pair, subject_pair, two_intake_targets, rule, validate, feedback, review,
    NOW, REF,
)
from tests.research_intake_fixtures import snapshot, RULE_VALIDATION_TABLES, INTENT_TABLES

REVISION = 'a1c3e5f7b9d0'
PARENT = 'f0b2d4e6a8c0'


def previous():
    # Compare the schema at this historical revision; the additive hold column
    # is independently checked by test_research_intent_lifecycle_migration.
    with engine.connect() as db:
        return {t.name: list(db.execute(select(*[c for c in t.columns if c.name != 'hold_generation']).order_by(*t.primary_key.columns)).mappings())
            for t in Base.metadata.sorted_tables if t.name not in RULE_VALIDATION_TABLES | INTENT_TABLES}


def test_fresh_additive_schema_and_safe_empty_downgrade(monkeypatch):
    config = Config('alembic.ini')
    scripts = ScriptDirectory.from_config(config)
    assert scripts.get_heads() == ['5f83bac2e714']
    assert scripts.get_revision(REVISION).down_revision == PARENT
    name = 'rule_validation_'+uuid4().hex
    with engine.begin() as db:db.execute(text(f'CREATE SCHEMA "{name}"'))
    url = make_url(settings.database_url).update_query_dict({'options': f'-csearch_path={name}'})
    isolated = create_engine(url)
    try:
        monkeypatch.setattr(settings, 'database_url', url.render_as_string(hide_password=False).replace('%', '%%'))
        command.upgrade(config, PARENT)
        before = set(inspect(isolated).get_table_names())
        command.upgrade(config, REVISION)
        inspector = inspect(isolated)
        assert set(inspector.get_table_names()) == before | RULE_VALIDATION_TABLES
        for name in RULE_VALIDATION_TABLES:
            columns = {c['name']: c for c in inspector.get_columns(name)}
            for c in Base.metadata.tables[name].columns:
                assert columns[c.name]['type'].compile(dialect=engine.dialect) == c.type.compile(dialect=engine.dialect)
                assert columns[c.name]['nullable'] == c.nullable
            assert all(f['options'] == {'ondelete': 'RESTRICT'} for f in inspector.get_foreign_keys(name))
            assert not any(f['referred_table'].startswith('research_observation') for f in inspector.get_foreign_keys(name))
        command.downgrade(config, PARENT)
        assert set(inspect(isolated).get_table_names()) == before
    finally:
        isolated.dispose()
        # The schema name remains from the URL, independent of loop variables.
        schema = url.query['options'].split('=')[1]
        with engine.begin() as db:db.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))


def test_populated_w2_and_legacy_upgrade_unchanged_and_history_downgrade_blocked(rule_pair):
    g, _ = rule_pair
    ref = rule(g)
    before = previous()
    config = Config('alembic.ini')
    try:
        command.downgrade(config, PARENT)
        assert previous() == before
        command.upgrade(config, 'head')
        assert previous() == before
        proof = validate(g, ref)['validation_ref']
        fid = feedback(g, ref, proof)['feedback_id']
        review(g, ref, fid)
        history = snapshot()
        with pytest.raises(RuntimeError, match='research_rule_validation_populated_downgrade_blocked'):
            command.downgrade(config, PARENT)
        assert snapshot() == history
        with engine.connect() as db:assert MigrationContext.configure(db).get_current_revision() == '5f83bac2e714'
    finally:command.upgrade(config, 'head')
