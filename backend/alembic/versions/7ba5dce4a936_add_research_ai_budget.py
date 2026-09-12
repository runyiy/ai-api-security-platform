"""Fake-only W2 reservations, postings and lifecycle acknowledgement backstop.

No seeded policies, source backfill, live transport or administrative role grants.
"""
from alembic import op

revision = '7ba5dce4a936'
down_revision = '6a94cbd3f825'
branch_labels = None
depends_on = None

TABLES = ('research_ai_budget_policy', 'research_ai_budget_balance', 'research_ai_call_core', 'research_ai_reservation', 'research_ai_admission', 'research_ai_send_marker', 'research_ai_event', 'research_ai_observation_copy', 'research_ai_settlement', 'research_ai_posting', 'research_ai_completion', 'research_ai_conflict', 'research_ai_registry', 'research_ai_registry_current', 'research_ai_deployment', 'research_ai_invalidation', 'research_ai_recovery')
IMMUTABLE = ('research_ai_budget_policy', 'research_ai_call_core', 'research_ai_send_marker', 'research_ai_event', 'research_ai_observation_copy', 'research_ai_settlement', 'research_ai_posting', 'research_ai_completion', 'research_ai_conflict', 'research_ai_registry', 'research_ai_invalidation', 'research_ai_recovery')
PROTECTED = ('research_contexts', 'research_context_versions', 'research_target_associations', 'research_observation_controls', 'research_observation_preparations', 'research_observation_records', 'research_observation_payloads', 'research_observation_events', 'research_subject_versions', 'research_knowledge_versions', 'research_knowledge_events', 'research_rule_validations', 'research_rule_feedback', 'research_rule_feedback_reviews', 'targets', 'scopes', 'authorization_profiles', 'authorization_revisions', 'research_ai_registry', 'research_ai_registry_current', 'research_ai_budget_policy', 'research_ai_deployment')

DDL = r"""
CREATE TABLE research_ai_budget_policy (
	balance_id VARCHAR(64) NOT NULL,
	scope_kind VARCHAR(8) NOT NULL,
	account_ref VARCHAR(64) NOT NULL,
	task_id VARCHAR(64) NOT NULL,
	policy_id VARCHAR(64) NOT NULL,
	policy_version INTEGER NOT NULL,
	scope JSONB NOT NULL,
	manifest_digest VARCHAR(64) NOT NULL,
	decision_ref VARCHAR(64) NOT NULL,
	rate_card VARCHAR(96) NOT NULL,
	currency VARCHAR(3) NOT NULL,
	token_cap BIGINT,
	cost_cap BIGINT,
	call_cap BIGINT,
	wall_cap_ns BIGINT,
	valid_from TIMESTAMP WITH TIME ZONE NOT NULL,
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (balance_id),
	UNIQUE (scope_kind, account_ref, task_id, policy_id, policy_version),
	CONSTRAINT ck_ai_budget_policy_1 CHECK (scope_kind IN ('account','task') AND currency='USD' AND policy_version BETWEEN 1 AND 10000),
	CONSTRAINT ck_ai_budget_policy_2 CHECK (token_cap>=0 AND cost_cap>=0 AND call_cap>=0 AND wall_cap_ns>=0 AND valid_from<expires_at)
)

;


CREATE TABLE research_ai_budget_balance (
	balance_id VARCHAR(64) NOT NULL,
	settled_tokens BIGINT NOT NULL,
	settled_microusd BIGINT NOT NULL,
	held_tokens BIGINT NOT NULL,
	held_microusd BIGINT NOT NULL,
	calls BIGINT NOT NULL,
	wall_ns BIGINT NOT NULL,
	state VARCHAR(24) NOT NULL,
	cancel_generation INTEGER NOT NULL,
	PRIMARY KEY (balance_id),
	CONSTRAINT ck_ai_budget_balance_1 CHECK (settled_tokens>=0 AND settled_microusd>=0 AND held_tokens>=0 AND held_microusd>=0 AND calls>=0 AND wall_ns>=0),
	CONSTRAINT ck_ai_budget_balance_2 CHECK (state IN ('ACTIVE','PAUSED_UNKNOWN','CANCELLED','CLOSED') AND cancel_generation BETWEEN 1 AND 2147483647),
	FOREIGN KEY(balance_id) REFERENCES research_ai_budget_policy (balance_id) ON DELETE RESTRICT
)

;


CREATE TABLE research_ai_call_core (
	key_digest VARCHAR(64) NOT NULL,
	key_bytes BYTEA NOT NULL,
	core_bytes BYTEA NOT NULL,
	task_id VARCHAR(64) NOT NULL,
	case_id VARCHAR(64) NOT NULL,
	question_id VARCHAR(64) NOT NULL,
	call_ref VARCHAR(64) NOT NULL,
	account_ref VARCHAR(64) NOT NULL,
	deployment_ref VARCHAR(64) NOT NULL,
	account_balance VARCHAR(64) NOT NULL,
	task_balance VARCHAR(64) NOT NULL,
	PRIMARY KEY (key_digest),
	UNIQUE (task_id, case_id, question_id),
	UNIQUE (call_ref),
	CONSTRAINT ck_ai_call_core_1 CHECK (octet_length(key_bytes)<=32768 AND octet_length(core_bytes)<=32768 AND key_digest=encode(sha256(convert_to('ra-w2-reservation-key/1'||chr(10),'UTF8')||key_bytes),'hex')),
	FOREIGN KEY(account_balance) REFERENCES research_ai_budget_policy (balance_id) ON DELETE RESTRICT,
	FOREIGN KEY(task_balance) REFERENCES research_ai_budget_policy (balance_id) ON DELETE RESTRICT
)

;


CREATE TABLE research_ai_reservation (
	key_digest VARCHAR(64) NOT NULL,
	reservation_id VARCHAR(64) NOT NULL,
	receipt BYTEA NOT NULL,
	owner_id VARCHAR(64) NOT NULL,
	owner_generation INTEGER NOT NULL,
	state VARCHAR(24) NOT NULL,
	held_tokens BIGINT NOT NULL,
	held_microusd BIGINT NOT NULL,
	actual_tokens BIGINT NOT NULL,
	actual_microusd BIGINT NOT NULL,
	event_count INTEGER NOT NULL,
	closed BOOLEAN NOT NULL,
	cancelled BOOLEAN NOT NULL,
	revision INTEGER NOT NULL,
	PRIMARY KEY (key_digest),
	CONSTRAINT ck_ai_reservation_1 CHECK (owner_generation BETWEEN 1 AND 2147483647 AND held_tokens>=0 AND held_microusd>=0 AND actual_tokens>=0 AND actual_microusd>=0 AND event_count BETWEEN 0 AND 64 AND revision>=0),
	CONSTRAINT ck_ai_reservation_2 CHECK (state IN ('RESERVED','ADMITTED','SEND_INTENT','IN_DOUBT','SETTLED','CONFLICT') AND octet_length(receipt)<=32768),
	FOREIGN KEY(key_digest) REFERENCES research_ai_call_core (key_digest) ON DELETE RESTRICT,
	UNIQUE (reservation_id)
)

;


CREATE TABLE research_ai_admission (
	key_digest VARCHAR(64) NOT NULL,
	admission_id VARCHAR(64) NOT NULL,
	account_ref VARCHAR(64) NOT NULL,
	deployment_ref VARCHAR(64) NOT NULL,
	owner_generation INTEGER NOT NULL,
	admitted_at TIMESTAMP WITH TIME ZONE NOT NULL,
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
	closed BOOLEAN NOT NULL,
	PRIMARY KEY (key_digest),
	FOREIGN KEY(key_digest) REFERENCES research_ai_call_core (key_digest) ON DELETE RESTRICT,
	UNIQUE (admission_id)
)

;
CREATE UNIQUE INDEX uq_research_ai_active_account ON research_ai_admission (account_ref) WHERE NOT closed;
CREATE UNIQUE INDEX uq_research_ai_active_deployment ON research_ai_admission (deployment_ref) WHERE NOT closed;

CREATE TABLE research_ai_send_marker (
	key_digest VARCHAR(64) NOT NULL,
	event_id VARCHAR(64) NOT NULL,
	owner_generation INTEGER NOT NULL,
	ticket BYTEA NOT NULL,
	body_digest VARCHAR(64) NOT NULL,
	PRIMARY KEY (key_digest),
	CONSTRAINT ck_ai_send_marker_1 CHECK (octet_length(ticket)<=4096),
	FOREIGN KEY(key_digest) REFERENCES research_ai_call_core (key_digest) ON DELETE RESTRICT,
	UNIQUE (event_id)
)

;


CREATE TABLE research_ai_event (
	event_id VARCHAR(64) NOT NULL,
	key_digest VARCHAR(64) NOT NULL,
	sequence INTEGER NOT NULL,
	kind VARCHAR(24) NOT NULL,
	digest VARCHAR(64) NOT NULL,
	record BYTEA NOT NULL,
	PRIMARY KEY (event_id),
	UNIQUE (key_digest, sequence),
	CONSTRAINT ck_ai_event_1 CHECK (sequence BETWEEN 1 AND 64 AND octet_length(record)<=4096),
	FOREIGN KEY(key_digest) REFERENCES research_ai_call_core (key_digest) ON DELETE RESTRICT
)

;


CREATE TABLE research_ai_observation_copy (
	event_id VARCHAR(64) NOT NULL,
	key_digest VARCHAR(64) NOT NULL,
	observer_epoch VARCHAR(64) NOT NULL,
	sequence BIGINT NOT NULL,
	digest VARCHAR(64) NOT NULL,
	record BYTEA NOT NULL,
	PRIMARY KEY (event_id),
	UNIQUE (observer_epoch, sequence),
	CONSTRAINT ck_ai_observation_copy_1 CHECK (sequence>0 AND octet_length(record)<=4096),
	FOREIGN KEY(key_digest) REFERENCES research_ai_call_core (key_digest) ON DELETE RESTRICT
)

;


CREATE TABLE research_ai_settlement (
	settlement_id VARCHAR(64) NOT NULL,
	key_digest VARCHAR(64) NOT NULL,
	final_event_id VARCHAR(64) NOT NULL,
	revision INTEGER NOT NULL,
	record BYTEA NOT NULL,
	PRIMARY KEY (settlement_id),
	UNIQUE (key_digest, final_event_id, revision),
	UNIQUE (key_digest, revision),
	CONSTRAINT ck_ai_settlement_1 CHECK (revision BETWEEN 1 AND 2147483647 AND octet_length(record)<=4096),
	FOREIGN KEY(key_digest) REFERENCES research_ai_call_core (key_digest) ON DELETE RESTRICT
)

;


CREATE TABLE research_ai_posting (
	settlement_id VARCHAR(64) NOT NULL,
	scope_kind VARCHAR(8) NOT NULL,
	dimension VARCHAR(8) NOT NULL,
	balance_id VARCHAR(64) NOT NULL,
	settled_delta BIGINT NOT NULL,
	held_delta BIGINT NOT NULL,
	PRIMARY KEY (settlement_id, scope_kind, dimension),
	CONSTRAINT ck_ai_posting_1 CHECK (scope_kind IN ('task','account') AND dimension IN ('tokens','microusd')),
	FOREIGN KEY(settlement_id) REFERENCES research_ai_settlement (settlement_id) ON DELETE RESTRICT,
	FOREIGN KEY(balance_id) REFERENCES research_ai_budget_balance (balance_id) ON DELETE RESTRICT
)

;


CREATE TABLE research_ai_completion (
	completion_id VARCHAR(64) NOT NULL,
	key_digest VARCHAR(64) NOT NULL,
	digest VARCHAR(64) NOT NULL,
	record BYTEA NOT NULL,
	PRIMARY KEY (completion_id),
	CONSTRAINT ck_ai_completion_1 CHECK (octet_length(record)<=4096),
	FOREIGN KEY(key_digest) REFERENCES research_ai_call_core (key_digest) ON DELETE RESTRICT,
	UNIQUE (digest)
)

;


CREATE TABLE research_ai_conflict (
	conflict_id VARCHAR(64) NOT NULL,
	key_digest VARCHAR(64) NOT NULL,
	existing_digest VARCHAR(64) NOT NULL,
	incoming_digest VARCHAR(64) NOT NULL,
	reason VARCHAR(24) NOT NULL,
	PRIMARY KEY (conflict_id),
	UNIQUE (key_digest, existing_digest, incoming_digest, reason),
	FOREIGN KEY(key_digest) REFERENCES research_ai_call_core (key_digest) ON DELETE RESTRICT
)

;


CREATE TABLE research_ai_registry (
	entry_id VARCHAR(64) NOT NULL,
	version INTEGER NOT NULL,
	kind VARCHAR(40) NOT NULL,
	digest VARCHAR(64) NOT NULL,
	record BYTEA NOT NULL,
	recorded_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (digest),
	UNIQUE (entry_id, version),
	CONSTRAINT ck_ai_registry_1 CHECK (version BETWEEN 1 AND 10000 AND octet_length(record)<=32768)
)

;


CREATE TABLE research_ai_registry_current (
	entry_id VARCHAR(64) NOT NULL,
	digest VARCHAR(64) NOT NULL,
	generation INTEGER NOT NULL,
	state VARCHAR(16) NOT NULL,
	PRIMARY KEY (entry_id),
	CONSTRAINT ck_ai_registry_current_1 CHECK (generation BETWEEN 1 AND 2147483647 AND state IN ('ACTIVE','REVOKED')),
	FOREIGN KEY(digest) REFERENCES research_ai_registry (digest) ON DELETE RESTRICT
)

;


CREATE TABLE research_ai_deployment (
	deployment_ref VARCHAR(64) NOT NULL,
	process_epoch VARCHAR(64) NOT NULL,
	state VARCHAR(24) NOT NULL,
	PRIMARY KEY (deployment_ref),
	CONSTRAINT ck_ai_deployment_1 CHECK (state IN ('SUSPENDED','FAKE_ACTIVE','CLOSED'))
)

;


CREATE TABLE research_ai_invalidation (
	operation_id VARCHAR(64) NOT NULL,
	operation_digest VARCHAR(64) NOT NULL,
	deployment_ref VARCHAR(64) NOT NULL,
	ack BYTEA NOT NULL,
	transaction_id BIGINT NOT NULL,
	backend_pid INTEGER NOT NULL,
	PRIMARY KEY (operation_id),
	CONSTRAINT ck_ai_invalidation_1 CHECK (octet_length(ack)<=4096)
)

;


CREATE TABLE research_ai_recovery (
	decision_id VARCHAR(64) NOT NULL,
	deployment_ref VARCHAR(64) NOT NULL,
	key_digest VARCHAR(64) NOT NULL,
	owner_generation INTEGER NOT NULL,
	evidence_digest VARCHAR(64) NOT NULL,
	record BYTEA NOT NULL,
	PRIMARY KEY (decision_id),
	CONSTRAINT ck_ai_recovery_1 CHECK (octet_length(record)<=32768 AND owner_generation BETWEEN 1 AND 2147483647)
)

;
"""

def upgrade():
    # Frozen SQL, independent of evolving application metadata.
    op.execute(DDL)
    op.execute("""CREATE FUNCTION research_ai_immutable() RETURNS trigger
      LANGUAGE plpgsql AS $$ BEGIN
        RAISE EXCEPTION 'research_ai_immutable';
      END $$""")
    for name in IMMUTABLE:
        op.execute(f'CREATE TRIGGER research_ai_immutable BEFORE UPDATE OR DELETE ON {name} '
                   'FOR EACH ROW EXECUTE FUNCTION research_ai_immutable()')
    op.execute("""CREATE FUNCTION research_ai_require_barrier() RETURNS trigger
      LANGUAGE plpgsql SET search_path=pg_catalog AS $$
      DECLARE missing boolean;
      BEGIN
        EXECUTE format('SELECT EXISTS (SELECT FROM %I.research_ai_deployment d WHERE NOT EXISTS (
          SELECT FROM %I.research_ai_invalidation i
          WHERE i.deployment_ref=d.deployment_ref AND i.transaction_id=txid_current()
            AND i.backend_pid=pg_backend_pid()))', TG_TABLE_SCHEMA, TG_TABLE_SCHEMA) INTO missing;
        IF missing THEN
          RAISE EXCEPTION 'research_ai_closure_ack_required';
        END IF;
        RETURN NULL;
      END $$""")
    for name in PROTECTED:
        op.execute(f'CREATE TRIGGER research_ai_closure BEFORE INSERT OR UPDATE OR DELETE ON {name} '
                   'FOR EACH STATEMENT EXECUTE FUNCTION research_ai_require_barrier()')
    # The sandboxed adapter needs no direct writes to any W2 table, and no
    # security-definer function to mint tickets, settlement or invalidation.
    for name in TABLES:
        op.execute(f'REVOKE ALL ON TABLE {name} FROM PUBLIC')
    op.execute('REVOKE ALL ON FUNCTION research_ai_immutable() FROM PUBLIC')
    op.execute('REVOKE ALL ON FUNCTION research_ai_require_barrier() FROM PUBLIC')


def downgrade():
    # Accounting and barrier evidence may not be silently erased by downgrade.
    # Empty-schema migration validation remains reversible.
    for name in TABLES:
        op.execute(f"DO $$ BEGIN IF EXISTS (SELECT FROM {name} LIMIT 1) THEN "
                   "RAISE EXCEPTION 'research_ai_evidence_retained'; END IF; END $$")
    for name in PROTECTED:
        op.execute(f'DROP TRIGGER research_ai_closure ON {name}')
    op.execute('DROP FUNCTION research_ai_require_barrier()')
    for name in IMMUTABLE:
        op.execute(f'DROP TRIGGER research_ai_immutable ON {name}')
    op.execute('DROP FUNCTION research_ai_immutable()')
    for name in reversed(TABLES):
        op.drop_table(name)
