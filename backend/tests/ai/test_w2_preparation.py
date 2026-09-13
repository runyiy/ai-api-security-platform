from copy import deepcopy
from hashlib import sha256
import pytest
from app.ai.proposals.codec import canonical
from app.ai.w2.records import make, decode, PortError
from app.ai.w2.retrieval import CLAIM, CODES, PROJECTION_DIGEST
from app.ai.w2.registry import ref_id
from tests.ai.w2_fixtures import w2, rule_pair, knowledge_pair, subject_pair, two_intake_targets, zero_capabilities  # noqa: F401


@pytest.mark.parametrize('entry,state,retrievals', [('Q1','RULES_SUFFICIENT',0),('Q2','RETRIEVAL_SUFFICIENT',1),
                                                  ('Q3','RULES_SUFFICIENT',0),('Q4','PRIORITY_UNRESOLVED',1)])
def test_t1_fixed_necessity_and_complete_projection(w2, entry,state,retrievals):
    calls=[]
    original=w2.retrieval.retrieve_projected_v1
    def lookup(*args):
        calls.append(args[1])
        return original(*args)
    w2.retrieval.retrieve_projected_v1=lookup
    result,_=w2.prepare(entry)
    assert result.state==state and len(calls)==retrievals
    assert not w2.authority.endpoint_bodies and not w2.authority.calls
    if entry=='Q4':
        prepared=w2.preparation.prepared[result.prepared_ref]
        from app.ai.proposals.contract import input_document
        payload=input_document(prepared.prepared.payload)
        assert payload['task']=='prioritize_review' and payload['gaps']==[]
        assert len(payload['candidates'])==2 and len(payload['rules'])==1
        assert payload['rules'][0]['claim']==CLAIM
        assert payload['rules'][0]['applicability_codes']==list(CODES)
        assert sha256(canonical({k:v for k,v in payload['rules'][0].items() if k!='ref'})).hexdigest()==PROJECTION_DIGEST
        assert len(prepared.prepared.registry.allowed_pairs)==2
        assert result.core_digest==prepared.key.core_digest
    else:
        assert result.prepared_ref is result.core_digest is None


@pytest.mark.parametrize('mutation', ['extra','boolean','fraction','version','duplicate','bom','trailing','nonfinite','surrogate'])
def test_t2_strict_records_no_coercion(w2,mutation):
    value=w2.scope.document()
    if mutation=='extra':value['unknown']='synthetic_canary'
    if mutation=='boolean':value['context_version']=True
    if mutation=='fraction':value['context_version']=1.0
    if mutation=='version':value['format']='ra-w2-scope/2'
    raw=canonical(value)
    if mutation=='duplicate':raw=raw[:-1]+b',"task_id":"other"}'
    if mutation=='bom':raw=b'\xef\xbb\xbf'+raw
    if mutation=='trailing':raw+=b' {}'
    if mutation=='nonfinite':raw=raw.replace(b'"context_version":1',b'"context_version":NaN')
    if mutation=='surrogate':raw=raw.replace(b'"case_1"',b'"\\ud800"')
    with pytest.raises(PortError):decode('Scope',raw)
    assert not w2.authority.endpoint_bodies


def test_t2_fixed_projection_digests_match_w1_and_records_are_immutable(w2):
    from app.ai.w2.retrieval import PROJECTION,TEMPLATE_DIGEST
    assert len(CLAIM.encode())<=512
    assert TEMPLATE_DIGEST==sha256(b'ra-w2-projection/1\n'+canonical(PROJECTION)).hexdigest()
    with pytest.raises(TypeError):w2.scope.task_id='other'
    with pytest.raises(TypeError):w2.scope._values['task_id']='other'
    assert decode('Scope',w2.scope.encode())==w2.scope


@pytest.mark.parametrize('kind', ['publication','projection','context','candidate','manifest'])
def test_t3_exact_dependency_failures_never_escalate(w2,kind):
    from sqlalchemy import text
    from app.db.session import SessionLocal
    from app.services import research_knowledge, research_context
    from tests.research_rule_fixtures import call,REF
    if kind=='publication':
        call(research_knowledge.decide,w2.g['ctx'],dict(reference={k:w2.source[k] for k in ('scope','knowledge_id','version','digest')},
            action='withdraw',expected_sequence=3,review=REF,valid_from=None,valid_until=None),project=w2.g['project'])
    elif kind=='context':
        call(research_context.close_context,w2.g['ctx'],dict(expected_version=1,closure_reference=REF),project=w2.g['project'])
    else:
        entry={'projection':ref_id('projection',w2.source),'candidate':ref_id('candidate',w2.candidates[0]),
               'manifest':ref_id('manifest',w2.scope.task_id)}[kind]
        record=w2.registry.get(entry,{'projection':'ProjectionBinding','candidate':'SyntheticCandidateCertificate','manifest':'TaskManifest'}[kind],w2.deadline())
        w2.registry.revoke(entry,record.fingerprint(),w2.deadline())
    with pytest.raises(Exception):w2.prepare()
    assert not w2.authority.endpoint_bodies and not w2.authority.calls


def test_t6_completed_retrieval_expiry_rejects(w2):
    original=w2.retrieval._qualify
    def expired(*args):
        result=original(*args)
        w2.clock.advance(30)
        return result
    w2.retrieval._qualify=expired
    with pytest.raises(PortError):w2.prepare()
    assert not w2.authority.endpoint_bodies


def test_t1_every_local_missing_code_stays_out_of_provider_gaps(w2):
    from app.ai.w2.records import MISSING
    for version,code in enumerate(MISSING,2):
        question=make('QuestionManifest',**{**w2.questions['Q4'].document(),'missing':[code]})
        w2.registry.install(ref_id('question',[w2.scope.task_id,w2.scope.case_id,question.question_id]),version,question,w2.deadline())
        manifest=make('TaskManifest',**{**w2.manifest.document(),
            'questions':[question if q.question_id==question.question_id else q for q in w2.manifest.questions]})
        w2.registry.install(ref_id('manifest',w2.scope.task_id),version,manifest,w2.deadline())
        read=w2.read()
        result=w2.preparation.prepare_v1(read,question,w2.deadline(read))
        assert result.state=='NEEDS_INPUT' and result.missing==(code,)
        assert result.prepared_ref is None and not w2.preparation.prepared
    assert not w2.authority.calls and not w2.witness.accepted
