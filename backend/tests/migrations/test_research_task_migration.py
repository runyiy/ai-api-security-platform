"""Additive task schema and fail-closed preservation of decisions and liability."""
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, select, text, update

from app.core.config import settings
from app.db.base import Base
from app.db.session import SessionLocal, engine
from app.db.models.research_task import TABLES, ResearchTaskVersion, ResearchTaskAllocation
from tests.research_task_fixtures import (task_graph, intent_graph, subject_pair, two_intake_targets,
    original_targets, zero_capabilities, create, decision, call, tasks)
from tests.finding_evidence_fixtures import evidence_pair
from tests.api.test_finding_evidence_fingerprints import old_evidence
from tests.api.test_finding_structured_evidence import analyze
from tests.migrations.test_research_intent_migration import test_populated_legacy_evidence_review_report_survive as legacy_cycle

HEAD = '8cb6edf5ba47'
PARENT = '7ba5dce4a936'
NAMES = {m.__tablename__ for m in TABLES}


def previous():
    with engine.connect() as db:
        return {t.name: list(db.execute(select(t).order_by(*t.primary_key.columns)).mappings())
                for t in Base.metadata.sorted_tables if t.name not in NAMES}


def test_fresh_empty_migration_exact_schema_and_safe_rollback(monkeypatch):
    config = Config('alembic.ini')
    scripts = ScriptDirectory.from_config(config)
    assert scripts.get_heads() == [HEAD] and scripts.get_revision(HEAD).down_revision == PARENT
    name = 'task_' + uuid4().hex
    with engine.begin() as db:
        db.execute(text('CREATE SCHEMA ' + name))
    url = engine.url.update_query_dict({'options': '-csearch_path=' + name})
    isolated = create_engine(url)
    try:
        monkeypatch.setattr(settings, 'database_url', url.render_as_string(hide_password=False).replace('%', '%%'))
        command.upgrade(config, PARENT)
        before = set(inspect(isolated).get_table_names())
        command.upgrade(config, HEAD)
        inspector = inspect(isolated)
        assert set(inspector.get_table_names()) == before | NAMES
        with isolated.connect() as db:
            assert all(db.scalar(select(text('count(*)')).select_from(m)) == 0 for m in TABLES)
        for model in TABLES:
            columns = {c['name']: c for c in inspector.get_columns(model.__tablename__)}
            for column in model.__table__.columns:
                assert column.type.compile(dialect=engine.dialect) == columns[column.name]['type'].compile(dialect=engine.dialect)
                assert column.nullable == columns[column.name]['nullable']
            assert all(f['options'] == {'ondelete': 'RESTRICT'} for f in inspector.get_foreign_keys(model.__tablename__))
        command.check(config)
        command.downgrade(config, PARENT)
        assert set(inspect(isolated).get_table_names()) == before
        command.upgrade(config, HEAD)
    finally:
        isolated.dispose()
        with engine.begin() as db:
            db.execute(text('DROP SCHEMA ' + name + ' CASCADE'))


def test_populated_existing_intent_and_verifier_are_unchanged(task_graph):
    # task_graph has real RA-04 plans/manifest decisions, but no task rows yet.
    config = Config('alembic.ini')
    before = previous()
    try:
        command.downgrade(config, PARENT)
        assert previous() == before
        command.upgrade(config, HEAD)
        assert previous() == before
    finally:
        command.upgrade(config, HEAD)


def test_populated_legacy_exact_approvals_m8_run_and_m13_history_survive(subject_pair, evidence_pair):
    # Reuse the existing actual plan/digest/approval/run/evidence/review/report
    # comparisons across this new migration, not a newly invented legacy oracle.
    import tests.migrations.test_research_intent_migration as legacy
    parent, revision = legacy.PARENT, legacy.REVISION
    try:
        legacy.PARENT, legacy.REVISION = PARENT, HEAD
        legacy_cycle(subject_pair, evidence_pair)
    finally:
        legacy.PARENT, legacy.REVISION = parent, revision


def test_populated_task_downgrade_and_budget_rewrite_refused(task_graph):
    g = task_graph
    approved = call(tasks.decide_budget, g, decision(create(g)))
    with SessionLocal() as db:
        before = list(db.execute(select(ResearchTaskAllocation.__table__)).mappings())
        for statement in (update(ResearchTaskVersion).values(body={}),
                          ResearchTaskAllocation.__table__.delete()):
            with pytest.raises(Exception, match='research_task_immutable'):
                with db.begin_nested():
                    db.execute(statement)
        db.rollback()
    with pytest.raises(RuntimeError, match='research_task_evidence_retained'):
        command.downgrade(Config('alembic.ini'), PARENT)
    with engine.connect() as db:
        assert MigrationContext.configure(db).get_current_revision() == HEAD
        assert list(db.execute(select(ResearchTaskAllocation.__table__)).mappings()) == before
    assert call(tasks.inspect_task, g, approved['reference'])['held_requests'] == 2
