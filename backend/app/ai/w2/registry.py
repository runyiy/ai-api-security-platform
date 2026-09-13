"""Exact, current synthetic registry; decisions are separate immutable evidence.

Only the trusted fake harness installs reviewed records. Nothing in this module
derives scenarios, questions or egress decisions from project facts. Persistence
contains bounded metadata only and does not become an approval issuer by itself.
"""
from contextlib import nullcontext
from hashlib import sha256
from sqlalchemy import insert, select, update

from app.ai.proposals.codec import canonical
from . import schema as t
from .records import Record, decode, require, PortError, utc, stamp

ROLES = ('possession', 'use', 'reuse', 'review', 'validation', 'publish', 'egress')


def ref_id(kind, value):
    raw = value.encode() if type(value) is Record else canonical(value)
    return kind + '_' + sha256(raw).hexdigest()[:32]


class Registry:
    def __init__(self, sessions, writer):
        self.sessions, self.writer = sessions, writer

    def install(self, entry_id, version, record, deadline):
        return self.install_many([(entry_id,version,record)],deadline)

    def install_many(self, entries, deadline):
        # No caller/model port exposes this administrative test-fixture operation.
        from .records import _scalar
        require(type(entries) in (tuple,list) and 1<=len(entries)<=64,'LIMIT_EXCEEDED')
        values=[]
        for entry_id,version,record in entries:
            require(_scalar('Id',entry_id) and _scalar('Ver',version))
            values.append((entry_id,version,decode(record._name,record.encode())))
        require(len({v[0] for v in values})==len(values),'CONFLICT')
        require(sum(len(v[2].encode()) for v in values)<=65536,'LIMIT_EXCEEDED')
        values.sort(key=lambda v:v[0])
        digest=sha256(canonical([(entry,version,record.fingerprint()) for entry,version,record in values])).hexdigest()
        with self.writer.mutation('CONFIGURATION','registry_batch',digest,deadline) as ticket:
            with self.sessions() as db, db.begin():
                deadline.sql_timeout(db)
                ticket.bind(db)
                for entry_id,version,record in values:
                    old = db.execute(select(t.registry_current).where(t.registry_current.c.entry_id == entry_id)
                                     .with_for_update()).mappings().one_or_none()
                    existing = db.execute(select(t.registry).where(t.registry.c.entry_id == entry_id,
                                                 t.registry.c.version == version)).mappings().one_or_none()
                    if existing is not None:
                        require(bytes(existing['record']) == record.encode(), 'CONFLICT')
                        require(old is not None and old['digest'] == record.fingerprint() and old['state'] == 'ACTIVE', 'CONFLICT')
                    else:
                        require(old is None or old['generation'] < 2147483647, 'LIMIT_EXCEEDED')
                        if old is not None:
                            prior_version=db.scalar(select(t.registry.c.version).where(t.registry.c.digest==old['digest']))
                            require(prior_version is not None and version>prior_version,'CONFLICT')
                        db.execute(insert(t.registry).values(entry_id=entry_id, version=version, kind=record._name,
                            digest=record.fingerprint(), record=record.encode(), recorded_at=deadline.check()))
                        if old is None:
                            db.execute(insert(t.registry_current).values(entry_id=entry_id, digest=record.fingerprint(),
                                                                       generation=1, state='ACTIVE'))
                        else:
                            db.execute(update(t.registry_current).where(t.registry_current.c.entry_id == entry_id)
                                .values(digest=record.fingerprint(), generation=old['generation']+1, state='ACTIVE'))
            ticket.committed()
        deadline.check()

    def revoke(self, entry_id, expected_digest, deadline):
        with self.writer.mutation('CONFIGURATION', entry_id, expected_digest, deadline) as ticket:
            with self.sessions() as db, db.begin():
                deadline.sql_timeout(db)
                ticket.bind(db)
                row = db.execute(select(t.registry_current).where(t.registry_current.c.entry_id == entry_id)
                                 .with_for_update()).mappings().one()
                require(row['digest'] == expected_digest and row['generation'] < 2147483647, 'CONFLICT')
                db.execute(update(t.registry_current).where(t.registry_current.c.entry_id == entry_id)
                           .values(state='REVOKED', generation=row['generation']+1))
            ticket.committed()

    def get(self, entry_id, name, deadline, *, expected=None):
        with self.sessions() as db, db.begin():
            deadline.sql_timeout(db)
            row = db.execute(select(t.registry.c.record, t.registry.c.kind, t.registry.c.digest,
                    t.registry_current.c.state).join(t.registry_current,
                    t.registry_current.c.digest == t.registry.c.digest)
                    .where(t.registry_current.c.entry_id == entry_id)).mappings().one_or_none()
            require(row is not None and row['state'] == 'ACTIVE' and row['kind'] == name, 'SOURCE_UNAVAILABLE')
            value = decode(name, bytes(row['record']))
            require(value.fingerprint() == row['digest'] and (expected is None or value.fingerprint() == expected), 'CONTEXT_CHANGED')
        deadline.check(*([(value.valid_from, value.expires_at)] if 'valid_from' in value else []))
        return value

    def scope_mapping(self, scope, deadline):
        value = self.get(ref_id('scope', scope), 'ScopeMapping', deadline)
        require(value.scope == scope, 'SOURCE_UNAVAILABLE')
        return value

    def question(self, question, deadline):
        value = self.get(ref_id('question', [question.scope.task_id, question.scope.case_id, question.question_id]),
                         'QuestionManifest', deadline, expected=question.fingerprint())
        manifest = self.get(ref_id('manifest', question.scope.task_id), 'TaskManifest', deadline)
        require(any(q == value for q in manifest.questions), 'SOURCE_UNAVAILABLE')
        require(all(all(q.scope[k]==manifest.scope[k] for k in manifest.scope if k!='case_id')
            for q in manifest.questions),'SOURCE_UNAVAILABLE')
        require(len({q.scope.case_id for q in manifest.questions}) <= 32, 'LIMIT_EXCEEDED')
        require(len({(q.scope.case_id, q.question_id) for q in manifest.questions}) == len(manifest.questions))
        # The reviewed manifest is the selection authority, never caller text or
        # any previously generated model result.
        return value, manifest

    def decisions(self, refs, source_id, version, digest, projection, deadline):
        require(len(refs) == 7 and len(set(refs)) == 7, 'SOURCE_UNAVAILABLE')
        windows = []
        for ref, role in zip(refs, ROLES):
            value = self.get(ref, 'Decision', deadline)
            require((value.decision_id, value.role, value.source_id, value.source_version,
                     value.source_digest, value.projection_digest) ==
                    (ref, role, source_id, version, digest, projection), 'SOURCE_UNAVAILABLE')
            windows.append((value.valid_from, value.expires_at))
        deadline.check(*windows)

    def projection(self, source, deadline):
        binding = self.get(ref_id('projection', source), 'ProjectionBinding', deadline)
        require(binding.source == source, 'SOURCE_UNAVAILABLE')
        self.decisions(binding.decision_refs, source.knowledge_id, source.version,
                       source.digest, binding.projection_digest, deadline)
        return binding

    def review_projection(self, binding, content, deadline):
        review = self.get(ref_id('review', binding.source), 'ProjectionReview', deadline)
        require(review.binding == binding and review.content_digest == binding.source.digest
            and review.reviewer_decision == binding.decision_refs[3]
            and review.examples_digest == sha256(canonical(content['example_refs'])).hexdigest()
            and review.counterexamples_digest == sha256(canonical(content['counterexample_refs'])).hexdigest(), 'SOURCE_UNAVAILABLE')

    def candidate(self, reference, review_ref, deadline):
        cert = self.get(ref_id('candidate', reference), 'SyntheticCandidateCertificate', deadline)
        require((cert.source_id, cert.source_version, cert.source_digest) ==
                (reference.source_id, reference.source_version, reference.source_digest), 'SOURCE_UNAVAILABLE')
        scenario = self.get(ref_id('scenario', [reference.source_id, reference.source_version]), 'SyntheticScenario', deadline)
        require(scenario.fingerprint() == cert.scenario_manifest_digest == cert.source_digest
                and (scenario.source_id, scenario.source_version, scenario.shape, scenario.actor_mode) ==
                    (cert.source_id, cert.source_version, cert.shape, cert.actor_mode)
                and review_ref == cert.decision_refs[3], 'SOURCE_UNAVAILABLE')
        projection = sha256(canonical({'shape': cert.shape, 'actor_mode': cert.actor_mode})).hexdigest()
        self.decisions(cert.decision_refs, cert.source_id, cert.source_version, cert.source_digest, projection, deadline)
        missing = set(cert.missing)
        if scenario.facts_ref is None or scenario.relationship == 'unspecified':
            missing.add('INDEPENDENT_FACTS_MISSING')
        if scenario.expectation_ref is None or scenario.expected_access == 'unspecified':
            missing.add('INDEPENDENT_EXPECTATION_MISSING')
        return cert, missing

    def model_permission(self, scope, deadline):
        permission = self.get(ref_id('permission', scope), 'ModelPermission', deadline)
        require(permission.scope == scope and permission.account_ref == scope.account_ref, 'CONTEXT_CHANGED')
        require(permission.enabled is True, 'AUTHORITY_UNAVAILABLE')
        certificate = self.get(permission.token_bound_evidence_ref, 'TokenCertificate', deadline)
        from .preparation import PROMPT_DIGEST, SCHEMA_DIGEST
        from .retrieval import PROJECTION_DIGEST
        require(certificate.evidence_id == permission.token_bound_evidence_ref
            and certificate.config_revision == permission.config_revision
            and (certificate.prompt_digest, certificate.schema_digest, certificate.projection_digest) ==
                (PROMPT_DIGEST, SCHEMA_DIGEST, PROJECTION_DIGEST), 'AUTHORITY_UNAVAILABLE')
        return permission, certificate

    def task_cancellation(self, scope, deadline):
        from .accounting import balance_id
        with self.sessions() as db, db.begin():
            deadline.sql_timeout(db)
            row=db.execute(select(t.balance).where(t.balance.c.balance_id==balance_id(scope,'task'))).mappings().one_or_none()
            require(row is None or row['state']!='CANCELLED','CANCELLED')
        deadline.check()
        return row['cancel_generation'] if row is not None else 1

    def budget_decisions(self, scope, manifest, deadline):
        from .accounting import balance_id, scope_document
        with self.sessions() as db, db.begin():
            deadline.sql_timeout(db)
            for kind in ('account','task'):
                row=db.execute(select(t.policy).where(t.policy.c.balance_id==balance_id(scope,kind))).mappings().one_or_none()
                require(row is not None and row['scope']==scope_document(scope,kind)
                    and all(row[k] is not None and row[k]>0 for k in ('token_cap','cost_cap','call_cap','wall_cap_ns'))
                    and (kind=='account' or row['manifest_digest']==manifest.fingerprint()),'AUTHORITY_UNAVAILABLE')
                deadline.check((stamp(row['valid_from']),stamp(row['expires_at'])))
        deadline.check()
