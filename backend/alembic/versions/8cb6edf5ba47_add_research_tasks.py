"""Persistent AI-disabled local tasks; no seeded approval or execution state."""
from alembic import op
import sqlalchemy as sa

revision = '8cb6edf5ba47'
down_revision = '7ba5dce4a936'
branch_labels = None
depends_on = None

TABLES = ('research_tasks', 'research_task_versions', 'research_task_members',
          'research_task_events', 'research_task_allocations')

DDL = r"""

CREATE TABLE research_tasks (
	id SERIAL NOT NULL,
	context_id INTEGER NOT NULL,
	target_id INTEGER NOT NULL,
	number INTEGER NOT NULL,
	recorded_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_task_number UNIQUE (context_id, number),
	CONSTRAINT uq_task_context_target UNIQUE (id, context_id, target_id),
	FOREIGN KEY(context_id, target_id) REFERENCES research_target_associations (context_id, target_id) ON DELETE RESTRICT,
	CONSTRAINT ck_task_number CHECK (number BETWEEN 1 AND 1024)
)


;

CREATE TABLE research_task_versions (
	id SERIAL NOT NULL,
	task_id INTEGER NOT NULL,
	context_id INTEGER NOT NULL,
	context_version INTEGER NOT NULL,
	target_id INTEGER NOT NULL,
	authorization_revision_id INTEGER NOT NULL,
	version INTEGER NOT NULL,
	body JSONB NOT NULL,
	digest VARCHAR(64) NOT NULL,
	recorded_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_task_version UNIQUE (task_id, version),
	CONSTRAINT uq_task_version_task UNIQUE (task_id, id),
	FOREIGN KEY(task_id, context_id, target_id) REFERENCES research_tasks (id, context_id, target_id) ON DELETE RESTRICT,
	FOREIGN KEY(context_id, context_version) REFERENCES research_context_versions (context_id, version_number) ON DELETE RESTRICT,
	CONSTRAINT ck_task_version CHECK (version BETWEEN 1 AND 16),
	CONSTRAINT ck_task_version_body CHECK (jsonb_typeof(body)='object' AND octet_length(body::text)<=65536 AND digest ~ '^[0-9a-f]{64}$'),
	FOREIGN KEY(authorization_revision_id) REFERENCES authorization_revisions (id) ON DELETE RESTRICT
)


;

CREATE TABLE research_task_members (
	id SERIAL NOT NULL,
	version_id INTEGER NOT NULL,
	ordinal INTEGER NOT NULL,
	plan_id INTEGER NOT NULL,
	intent_id INTEGER NOT NULL,
	manifest_id INTEGER NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_task_member_plan UNIQUE (version_id, plan_id),
	CONSTRAINT uq_task_member_ordinal UNIQUE (version_id, ordinal),
	CONSTRAINT ck_task_member_ordinal CHECK (ordinal BETWEEN 1 AND 100),
	FOREIGN KEY(version_id) REFERENCES research_task_versions (id) ON DELETE RESTRICT,
	FOREIGN KEY(plan_id) REFERENCES execution_plans (id) ON DELETE RESTRICT,
	FOREIGN KEY(intent_id) REFERENCES research_intent_versions (id) ON DELETE RESTRICT,
	FOREIGN KEY(manifest_id) REFERENCES research_intent_manifests (id) ON DELETE RESTRICT
)


;

CREATE TABLE research_task_events (
	id SERIAL NOT NULL,
	task_id INTEGER NOT NULL,
	version_id INTEGER NOT NULL,
	sequence INTEGER NOT NULL,
	kind VARCHAR(24) NOT NULL,
	body JSONB NOT NULL,
	recorded_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_task_event_sequence UNIQUE (task_id, sequence),
	FOREIGN KEY(task_id, version_id) REFERENCES research_task_versions (task_id, id) ON DELETE RESTRICT,
	CONSTRAINT ck_task_event_sequence CHECK (sequence BETWEEN 1 AND 64),
	CONSTRAINT ck_task_event_body CHECK (kind IN ('version_created','approved','revoked','paused','cancelled') AND jsonb_typeof(body)='object' AND octet_length(body::text)<=4096)
)


;

CREATE TABLE research_task_allocations (
	plan_id INTEGER NOT NULL,
	task_id INTEGER NOT NULL,
	first_version_id INTEGER NOT NULL,
	slot INTEGER NOT NULL,
	requests INTEGER NOT NULL,
	state VARCHAR(24) NOT NULL,
	recorded_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (plan_id),
	CONSTRAINT uq_task_allocation_slot UNIQUE (task_id, slot),
	FOREIGN KEY(task_id, first_version_id) REFERENCES research_task_versions (task_id, id) ON DELETE RESTRICT,
	CONSTRAINT ck_task_allocation CHECK (slot BETWEEN 1 AND 100 AND requests=1 AND state='held_unreconciled'),
	FOREIGN KEY(plan_id) REFERENCES execution_plans (id) ON DELETE RESTRICT
)

;
CREATE INDEX ix_research_tasks_context_id ON research_tasks (context_id);
CREATE INDEX ix_research_task_versions_task_id ON research_task_versions (task_id);
CREATE INDEX ix_research_task_members_plan_id ON research_task_members (plan_id);
CREATE INDEX ix_research_task_members_version_id ON research_task_members (version_id);
CREATE INDEX ix_research_task_events_task_id ON research_task_events (task_id);
CREATE INDEX ix_research_task_allocations_task_id ON research_task_allocations (task_id);
"""


def upgrade():
    # Frozen SQL does not import evolving application models.
    op.execute(DDL)
    op.execute("""CREATE FUNCTION research_task_immutable() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'research_task_immutable'; END $$""")
    for name in TABLES:
        op.execute(f'CREATE TRIGGER research_task_immutable BEFORE UPDATE OR DELETE ON {name} '
                   'FOR EACH ROW EXECUTE FUNCTION research_task_immutable()')
        op.execute(f'REVOKE ALL ON TABLE {name} FROM PUBLIC')
    op.execute('REVOKE ALL ON FUNCTION research_task_immutable() FROM PUBLIC')


def downgrade():
    db = op.get_bind()
    db.execute(sa.text('LOCK TABLE '+', '.join(TABLES)+' IN ACCESS EXCLUSIVE MODE'))
    if any(db.scalar(sa.text(f'SELECT EXISTS (SELECT FROM {name})')) for name in TABLES):
        raise RuntimeError('research_task_evidence_retained')
    for name in reversed(TABLES):
        op.drop_table(name)
    op.execute('DROP FUNCTION research_task_immutable()')
