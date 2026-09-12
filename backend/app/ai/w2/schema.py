"""Additive, content-free W2 persistence metadata.

Authority remains in N1; database closure copies cannot issue acceptance.
No source FK obstructs DATA deletion. No seeded approvals or default budgets.
"""
from sqlalchemy import (Table, Column, String, BigInteger, Integer, Boolean, DateTime,
                        LargeBinary, CheckConstraint, UniqueConstraint, Index, ForeignKey, text)
from sqlalchemy.dialects.postgresql import JSONB
from app.db.base import Base


def c(name, kind=String(64), **kwargs):
    return Column(name, kind, nullable=False, **kwargs)


policy = Table('research_ai_budget_policy', Base.metadata,
    c('balance_id', primary_key=True), c('scope_kind', String(8)), c('account_ref'), c('task_id'),
    c('policy_id'), c('policy_version', Integer), c('scope', JSONB), c('manifest_digest'),
    c('decision_ref'), c('rate_card', String(96)), c('currency', String(3)),
    Column('token_cap', BigInteger), Column('cost_cap', BigInteger),
    Column('call_cap', BigInteger), Column('wall_cap_ns', BigInteger),
    c('valid_from', DateTime(timezone=True)), c('expires_at', DateTime(timezone=True)),
    UniqueConstraint('scope_kind', 'account_ref', 'task_id', 'policy_id', 'policy_version'),
    CheckConstraint("scope_kind IN ('account','task') AND currency='USD' AND policy_version BETWEEN 1 AND 10000", name='ck_ai_budget_policy_1'),
    CheckConstraint('token_cap>=0 AND cost_cap>=0 AND call_cap>=0 AND wall_cap_ns>=0 AND valid_from<expires_at', name='ck_ai_budget_policy_2'))
balance = Table('research_ai_budget_balance', Base.metadata,
    c('balance_id', ForeignKey(policy.c.balance_id, ondelete='RESTRICT'), primary_key=True),
    c('settled_tokens', BigInteger), c('settled_microusd', BigInteger),
    c('held_tokens', BigInteger), c('held_microusd', BigInteger), c('calls', BigInteger), c('wall_ns', BigInteger),
    c('state', String(24)), c('cancel_generation', Integer),
    CheckConstraint('settled_tokens>=0 AND settled_microusd>=0 AND held_tokens>=0 AND held_microusd>=0 AND calls>=0 AND wall_ns>=0', name='ck_ai_budget_balance_1'),
    CheckConstraint("state IN ('ACTIVE','PAUSED_UNKNOWN','CANCELLED','CLOSED') AND cancel_generation BETWEEN 1 AND 2147483647", name='ck_ai_budget_balance_2'))
core = Table('research_ai_call_core', Base.metadata,
    c('key_digest', primary_key=True), c('key_bytes', LargeBinary), c('core_bytes', LargeBinary),
    c('task_id'), c('case_id'), c('question_id'), c('call_ref'), c('account_ref'), c('deployment_ref'),
    c('account_balance', ForeignKey(policy.c.balance_id, ondelete='RESTRICT')),
    c('task_balance', ForeignKey(policy.c.balance_id, ondelete='RESTRICT')),
    UniqueConstraint('task_id', 'case_id', 'question_id'), UniqueConstraint('call_ref'),
    CheckConstraint("octet_length(key_bytes)<=32768 AND octet_length(core_bytes)<=32768 AND key_digest=encode(sha256(convert_to('ra-w2-reservation-key/1'||chr(10),'UTF8')||key_bytes),'hex')", name='ck_ai_call_core_1'))
reservation = Table('research_ai_reservation', Base.metadata,
    c('key_digest', ForeignKey(core.c.key_digest, ondelete='RESTRICT'), primary_key=True),
    c('reservation_id', unique=True), c('receipt', LargeBinary), c('owner_id'), c('owner_generation', Integer),
    c('state', String(24)), c('held_tokens', BigInteger), c('held_microusd', BigInteger),
    c('actual_tokens', BigInteger), c('actual_microusd', BigInteger), c('event_count', Integer),
    c('closed', Boolean), c('cancelled', Boolean), c('revision', Integer),
    CheckConstraint('owner_generation BETWEEN 1 AND 2147483647 AND held_tokens>=0 AND held_microusd>=0 AND actual_tokens>=0 AND actual_microusd>=0 AND event_count BETWEEN 0 AND 64 AND revision>=0', name='ck_ai_reservation_1'),
    CheckConstraint("state IN ('RESERVED','ADMITTED','SEND_INTENT','IN_DOUBT','SETTLED','CONFLICT') AND octet_length(receipt)<=32768", name='ck_ai_reservation_2'))
admission = Table('research_ai_admission', Base.metadata,
    c('key_digest', ForeignKey(core.c.key_digest, ondelete='RESTRICT'), primary_key=True),
    c('admission_id', unique=True), c('account_ref'), c('deployment_ref'), c('owner_generation', Integer),
    c('admitted_at', DateTime(timezone=True)), c('expires_at', DateTime(timezone=True)), c('closed', Boolean))
Index('uq_research_ai_active_account', admission.c.account_ref, unique=True, postgresql_where=text('NOT closed'))
Index('uq_research_ai_active_deployment', admission.c.deployment_ref, unique=True, postgresql_where=text('NOT closed'))
marker = Table('research_ai_send_marker', Base.metadata,
    c('key_digest', ForeignKey(core.c.key_digest, ondelete='RESTRICT'), primary_key=True),
    c('event_id', unique=True), c('owner_generation', Integer), c('ticket', LargeBinary), c('body_digest'),
    CheckConstraint('octet_length(ticket)<=4096', name='ck_ai_send_marker_1'))
event = Table('research_ai_event', Base.metadata,
    c('event_id', primary_key=True), c('key_digest', ForeignKey(core.c.key_digest, ondelete='RESTRICT')),
    c('sequence', Integer), c('kind', String(24)), c('digest'), c('record', LargeBinary),
    UniqueConstraint('key_digest', 'sequence'), CheckConstraint('sequence BETWEEN 1 AND 64 AND octet_length(record)<=4096', name='ck_ai_event_1'))
observation_copy = Table('research_ai_observation_copy', Base.metadata,
    c('event_id', primary_key=True), c('key_digest', ForeignKey(core.c.key_digest, ondelete='RESTRICT')),
    c('observer_epoch'), c('sequence', BigInteger), c('digest'), c('record', LargeBinary),
    UniqueConstraint('observer_epoch', 'sequence'), CheckConstraint('sequence>0 AND octet_length(record)<=4096', name='ck_ai_observation_copy_1'))
settlement = Table('research_ai_settlement', Base.metadata,
    c('settlement_id', primary_key=True), c('key_digest', ForeignKey(core.c.key_digest, ondelete='RESTRICT')),
    c('final_event_id'), c('revision', Integer), c('record', LargeBinary),
    UniqueConstraint('key_digest', 'final_event_id', 'revision'), UniqueConstraint('key_digest', 'revision'),
    CheckConstraint('revision BETWEEN 1 AND 2147483647 AND octet_length(record)<=4096', name='ck_ai_settlement_1'))
posting = Table('research_ai_posting', Base.metadata,
    c('settlement_id', ForeignKey(settlement.c.settlement_id, ondelete='RESTRICT'), primary_key=True),
    c('scope_kind', String(8), primary_key=True), c('dimension', String(8), primary_key=True),
    c('balance_id', ForeignKey(balance.c.balance_id, ondelete='RESTRICT')),
    c('settled_delta', BigInteger), c('held_delta', BigInteger),
    CheckConstraint("scope_kind IN ('task','account') AND dimension IN ('tokens','microusd')", name='ck_ai_posting_1'))
completion = Table('research_ai_completion', Base.metadata,
    c('completion_id', primary_key=True), c('key_digest', ForeignKey(core.c.key_digest, ondelete='RESTRICT')),
    c('digest', unique=True), c('record', LargeBinary), CheckConstraint('octet_length(record)<=4096', name='ck_ai_completion_1'))
conflict = Table('research_ai_conflict', Base.metadata,
    c('conflict_id', primary_key=True), c('key_digest', ForeignKey(core.c.key_digest, ondelete='RESTRICT')),
    c('existing_digest'), c('incoming_digest'), c('reason', String(24)),
    UniqueConstraint('key_digest', 'existing_digest', 'incoming_digest', 'reason'))
registry = Table('research_ai_registry', Base.metadata,
    c('entry_id'), c('version', Integer), c('kind', String(40)), c('digest', primary_key=True),
    c('record', LargeBinary), c('recorded_at', DateTime(timezone=True)),
    UniqueConstraint('entry_id', 'version'), CheckConstraint('version BETWEEN 1 AND 10000 AND octet_length(record)<=32768', name='ck_ai_registry_1'))
registry_current = Table('research_ai_registry_current', Base.metadata,
    c('entry_id', primary_key=True), c('digest', ForeignKey(registry.c.digest, ondelete='RESTRICT')),
    c('generation', Integer), c('state', String(16)), CheckConstraint("generation BETWEEN 1 AND 2147483647 AND state IN ('ACTIVE','REVOKED')", name='ck_ai_registry_current_1'))
deployment = Table('research_ai_deployment', Base.metadata,
    c('deployment_ref', primary_key=True), c('process_epoch'), c('state', String(24)),
    CheckConstraint("state IN ('SUSPENDED','FAKE_ACTIVE','CLOSED')", name='ck_ai_deployment_1'))
invalidation = Table('research_ai_invalidation', Base.metadata,
    c('operation_id', primary_key=True), c('operation_digest'), c('deployment_ref'),
    c('ack', LargeBinary), c('transaction_id', BigInteger), c('backend_pid', Integer),
    CheckConstraint('octet_length(ack)<=4096', name='ck_ai_invalidation_1'))
recovery = Table('research_ai_recovery', Base.metadata,
    c('decision_id', primary_key=True), c('deployment_ref'), c('key_digest'),
    c('owner_generation', Integer), c('evidence_digest'), c('record', LargeBinary),
    CheckConstraint('octet_length(record)<=32768 AND owner_generation BETWEEN 1 AND 2147483647', name='ck_ai_recovery_1'))

TABLES = (policy, balance, core, reservation, admission, marker, event, observation_copy, settlement,
          posting, completion, conflict, registry, registry_current, deployment, invalidation, recovery)
IMMUTABLE = (policy, core, marker, event, observation_copy, settlement, posting, completion, conflict, registry, invalidation, recovery)
