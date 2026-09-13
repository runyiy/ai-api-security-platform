"""Synthetic-only internal lookup. Public retrieval is never provider input."""
from hashlib import sha256
from sqlalchemy import select

from app.ai.proposals.codec import canonical
from app.ai.proposals.contract import SHAPE
from app.db.models.research_knowledge import KnowledgeVersion as KV, KnowledgeEvent as KE
from app.schemas import research_knowledge as schema
from app.schemas.research_context import ResearchIntakeInput
from app.services import research_context as intake, research_observation as observation
from app.services import research_knowledge as knowledge, research_rule_validation as validation
from .records import make, require, PortError, stamp, utc

CLAIM = ('Sharing may allow non-owner access. This synthetic rule requires independent access facts '
         'and a complete single-resource GET JSON-object scenario. A separately authorized sharing '
         "example can permit access; a non-sharing counterexample can deny it. Neither ownership nor "
         "this rule proves a project's access policy. Human review is required; nothing was executed.")
CODES = (SHAPE, 'anonymous', 'bearer', 'independent_facts_required')
PROJECTION = {'format': 'ra-w2-projection/1', 'template': 'sharing-complete/1',
              'claim': CLAIM, 'applicability_codes': list(CODES)}
PROJECTION_DIGEST = sha256(canonical({'claim': CLAIM, 'applicability_codes': list(CODES)})).hexdigest()
TEMPLATE_DIGEST = sha256(b'ra-w2-projection/1\n' + canonical(PROJECTION)).hexdigest()


def exact_ref(source):
    # ExactSource has a format field. Never pass it into the older strict schema.
    return schema.ExactRef(**{k: source[k] for k in ('scope', 'knowledge_id', 'version', 'digest')})


class ProjectedRetrieval:
    def __init__(self, sessions, registry, guard):
        self.sessions, self.registry, self.guard = sessions, registry, guard

    def context(self, db, read, deadline):
        mapping = self.registry.scope_mapping(read.scope, deadline)
        context = knowledge._locked(db,mapping.project_number,mapping.context_id)
        self._eligible_consumer(db,context,read.scope.context_version,deadline)
        return context

    def _eligible_consumer(self,db,context,version_number,deadline):
        observation._eligible_context(db, context, version_number)
        observation._control(db, context, deadline.check())
        version = intake._latest(db, context.id)
        for item in ResearchIntakeInput.model_validate(version.intake).targets:
            observation._target(db, context, item.target_id)
            target, revision, scopes, digest = intake._authorization_metadata(db, context, item)
            require(digest == version.permission_snapshots.get(str(item.target_id)), 'CONTEXT_CHANGED')
            require(intake._permission_status(target,revision,scopes,item,True,deadline.check())=='referenced_current',
                    'SOURCE_UNAVAILABLE')
            deadline.check((stamp(revision['valid_from']) if revision['valid_from'] else stamp(deadline.last_wall),
                stamp(revision['valid_until']) if revision['valid_until'] else deadline.context.deadline_at))

    def _metadata(self, db):
        # The scan bounds metadata only; excluded/project bodies never load.
        metadata = list(db.execute(select(KV.id, KV.scope, KV.knowledge_id, KV.version, KV.digest,
                        KV.content['data_class'].astext.label('data_class'),
                        KV.content['purpose'].astext.label('purpose'))
                        .where(KV.scope == 'reusable_synthetic').order_by(KV.id).limit(257)))
        require(len(metadata) <= 256, 'LIMIT_EXCEEDED')
        return metadata

    def _qualify(self, db, context, source, deadline, metadata=None):
        metadata = self._metadata(db) if metadata is None else metadata
        catalog, disabled, seen = {}, set(), set()
        current, selected, deadlines = source, None, []
        metadata_keys = {(r.scope, r.knowledge_id, r.version, r.digest): r for r in metadata}
        while current is not None:
            key = (current.scope, current.knowledge_id, current.version, current.digest)
            require(key in metadata_keys and key not in seen and len(seen) < 128, 'SOURCE_UNAVAILABLE')
            seen.add(key)
            meta=metadata_keys[key]
            require(meta.data_class=='synthetic_authored' and meta.purpose=='offline_context_explanation','SOURCE_UNAVAILABLE')
            # Check public authority metadata before loading the card. The old
            # public helper deliberately accepts test-only publication, so it
            # cannot establish this filter by itself.
            events=list(db.scalars(select(KE).where(KE.version_id==meta.id).order_by(KE.sequence).limit(17)))
            require(events and len(events)<=16 and [e.sequence for e in events]==list(range(1,len(events)+1))
                and events[-1].action=='publish' and not any(e.action in ('withdraw','disable') for e in events),'SOURCE_UNAVAILABLE')
            publication_meta=schema.validate(schema.EventBody,events[-1].body)
            require(publication_meta.evidence=='operator_recorded' and publication_meta.actor=='local_operator'
                and type(publication_meta.validation_ref) is schema.ValidationRef
                and publication_meta.digest==current.digest and knowledge._window(publication_meta,deadline.check()),'SOURCE_UNAVAILABLE')
            binding=self.registry.projection(current,deadline)
            require(binding.source==current,'SOURCE_UNAVAILABLE')
            row = knowledge._exact(db, context, exact_ref(current))
            content = validation._content(row)
            require(content.data_class == 'synthetic_authored' and content.scope == 'reusable_synthetic'
                    and content.purpose == 'offline_context_explanation'
                    and all(s.kind == 'synthetic_authored' for s in content.source_refs), 'SOURCE_UNAVAILABLE')
            withdrawn = db.scalar(select(KE.id).where(KE.version_id == row.id,
                                   KE.action.in_(('withdraw', 'disable'))).limit(1))
            require(withdrawn is None, 'SOURCE_UNAVAILABLE')
            at = deadline.check()
            publication = knowledge._publication(db, row, at)
            require(publication is not None, 'SOURCE_UNAVAILABLE')
            pub, body, until, _ = publication
            require(body.evidence == 'operator_recorded' and body.actor == 'local_operator'
                    and type(body.validation_ref) is schema.ValidationRef, 'SOURCE_UNAVAILABLE')
            proof = validation.qualified(db, row, body.validation_ref, deadline.check())
            deadlines.extend([until, proof.valid_until])
            catalog[schema.canonical(knowledge._ref(row))] = row
            if selected is None:
                selected = row, content, pub, body, proof
            current = (make('ExactSource', **content.supersedes.model_dump())
                       if content.supersedes is not None else None)
        row, content, pub, body, proof = selected
        require(knowledge._lineage_eligible(content, catalog, disabled), 'SOURCE_UNAVAILABLE')
        require(content.claim == 'Sharing may allow non-owner access.'
                and content.applicability.shape == SHAPE and content.applicability.requires_facts is True
                and set(content.applicability.actors) == {'anonymous', 'bearer'}, 'SOURCE_UNAVAILABLE')
        binding = self.registry.projection(source, deadline)
        require(binding.source == source and binding.projection_digest == PROJECTION_DIGEST
                and binding.template_digest == TEMPLATE_DIGEST
                and binding.validation_id == proof.id and binding.validation_digest == proof.digest
                and binding.publication_event_id == pub.id and binding.review_event_id == body.review_event_id
                and binding.reuse_event_id == body.reuse_event_id, 'SOURCE_UNAVAILABLE')
        # Registry approval binds the complete card digest and its example refs,
        # separately from genuine W3 proof/publication in the database.
        self.registry.review_projection(binding, content.model_dump(), deadline)
        deadline.check((binding.valid_from, binding.expires_at))
        deadline.check((binding.valid_from,stamp(min(deadlines))))
        return binding, content, min([*deadlines, utc(binding.expires_at)])

    def qualified_selected(self, read, question, deadline, token=None):
        with self.guard.read(deadline, token), self.sessions() as db, db.begin():
            deadline.sql_timeout(db)
            context = self.context(db, read, deadline)
            result = self._qualify(db, context, question.rule, deadline)
            self.context(db, read, deadline)
            deadline.check((result[0].valid_from, stamp(result[2])))
            return result[0]

    def retrieve_projected_v1(self, read, request, deadline):
        request = make('RetrievalRequest', **{k: v for k, v in request.items() if k != 'format'})
        try:
            with self.guard.read(deadline), self.sessions() as db, db.begin():
                deadline.sql_timeout(db)
                context = self.context(db, read, deadline)
                metadata = self._metadata(db)
                matches = []
                for source in request.selected:
                    binding, content, until = self._qualify(db, context, source, deadline, metadata)
                    score = knowledge._rank(content, request)
                    if score > 0:
                        matches.append((binding, score, until))
                matches.sort(key=lambda v: (-v[1], v[0].source.scope, v[0].source.knowledge_id,
                                           v[0].source.version, v[0].source.digest))
                matches = matches[:request.top_k]
                for binding, _, _ in matches:
                    require(self._qualify(db, context, binding.source, deadline, metadata)[0] == binding, 'CONTEXT_CHANGED')
                self.context(db, read, deadline)
                at = deadline.check()
                audit = knowledge._audit(db, context, 'query', at)
                until = min([utc(read.deadline_at), *[m[2] for m in matches]])
                result = make('RetrievalResult', question_digest=request.question_digest,
                    bindings=[m[0] for m in matches], projection_refs=[m[0].fingerprint() for m in matches],
                    scores=[m[1] for m in matches], evaluated_at=stamp(at), expires_at=stamp(until), audit_id=str(audit))
                deadline.check((stamp(at), stamp(until)))
            # Commit acknowledgement time is part of the lookup too.
            deadline.check((result.evaluated_at, result.expires_at))
            return result
        except (schema.KnowledgeError, observation.ObservationError, intake.ResearchContextError):
            raise PortError('SOURCE_UNAVAILABLE') from None
