from datetime import timedelta
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.api.routes import research_knowledge as route
from app.services import research_observation as observation
from app.schemas import research_knowledge as s
from tests.research_knowledge_fixtures import knowledge_pair, subject_pair, two_intake_targets, content, record, published, query, NOW, REF, zero_capabilities  # noqa: F401
from tests.research_intake_fixtures import snapshot
from tests.research_knowledge_fixtures import permission_knowledge_pair, future_assertion  # noqa: F401

client=TestClient(app)


@pytest.fixture
def api(knowledge_pair,monkeypatch):
    monkeypatch.setattr(observation,'_time',lambda clock: NOW if clock is None else clock)
    g,b=knowledge_pair
    return f"/api/research-projects/{g['project']}/contexts/{g['ctx']}/knowledge",g,b


def test_operator_candidate_and_publication_refusal(api):
    root,g,_=api
    response=client.post(root+'/versions',json={'context_version':1,'target_id':g['target'],'content':content(),'review':REF})
    assert response.status_code==200 and response.json()['state']=='candidate'
    ref=response.json()['reference'];before=snapshot()
    response=client.post(root+'/decisions',json={'reference':ref,'expected_sequence':0,'action':'publish','review':REF,'valid_from':NOW.isoformat(),'valid_until':'2031-04-04T12:00:00Z'})
    assert response.status_code==409 and response.json()['code']=='knowledge_publication_closed'
    assert snapshot()==before
    response=client.post(root+'/query',json=query())
    assert response.status_code==200 and response.json()['matches']==[]


def test_test_only_retrieval_exact_provenance_and_no_authority(api):
    root,g,_=api;ref=published(g)
    response=client.post(root+'/query',json=query(selected=[ref,ref]))
    assert response.status_code==200 and response.headers['cache-control']=='no-store'
    value=response.json();assert value['matches'][0]['reference']==ref
    assert value['matches'][0]['publication_evidence']=='synthetic_test_only'
    assert value['matches'][0]['review_actor']=='local_operator'
    assert value['matches'][0]['publication_actor']=='synthetic_test_reviewer'
    assert not value['execution_authorized'] and not value['ordinary_publication_allowed']
    assert len(value['matches'])==1


@pytest.mark.parametrize('size,expected',[(8192,200),(8193,413)])
def test_api_actual_bytes(api,size,expected):
    root,_,_=api;raw=s.canonical(query());raw+=b' '*(size-len(raw))
    r=client.post(root+'/query',content=raw,headers={'content-type':'application/json'})
    assert r.status_code==expected


@pytest.mark.parametrize('raw',[b'{"top_k":1,"top_k":2}',b'{"secret":"SYNTHETIC-CANARY"}',b'\xff',b'{"$ref":"http://synthetic.invalid"}',b'{"validation_ref":"synthetic_test_only"}'])
def test_api_sanitized_invalid_input(api,raw,caplog):
    root,_,_=api;before=snapshot()
    r=client.post(root+'/query',content=raw,headers={'content-type':'application/json'})
    assert r.status_code==422 and len(r.content)<256
    assert b'CANARY' not in r.content and 'CANARY' not in caplog.text and snapshot()==before


def test_foreign_and_missing_context_are_indistinguishable(api):
    root,a,b=api
    values=[]
    for context in (b['ctx'],2147483647):
        response=client.post(f"/api/research-projects/{a['project']}/contexts/{context}/knowledge/query",json=query())
        values.append((response.status_code,response.json()))
    assert values[0]==values[1]==(409,{'status':'rejected','code':'knowledge_unavailable'})


@pytest.mark.parametrize('failure',['serialization','size','database_audit'])
def test_response_or_genuine_audit_failure_rolls_back(api,monkeypatch,failure):
    root,g,_=api;before=snapshot()
    if failure=='serialization':
        monkeypatch.setattr(route,'encoded',lambda value: (_ for _ in ()).throw(ValueError('synthetic serialize failure')))
    elif failure=='size':monkeypatch.setattr(s,'MAX_RESPONSE',1)
    else:
        from app.services import research_knowledge as service
        from sqlalchemy import text
        def fail(db,*args,**kwargs):db.execute(text('INSERT INTO research_knowledge_audit (context_id,code,recorded_at) VALUES (NULL,NULL,NULL)'))
        monkeypatch.setattr(service,'_audit',fail)
    r=client.post(root+'/versions',json={'context_version':1,'target_id':g['target'],'content':content(),'review':REF})
    assert r.status_code==500 and snapshot()==before


@pytest.mark.parametrize('size,expected',[(32768,200),(32769,413)])
def test_candidate_actual_byte_boundary(api,size,expected):
    root,g,_=api
    raw=s.canonical({'context_version':1,'target_id':g['target'],'content':content(),'review':REF})
    raw+=b' '*(size-len(raw))
    before=snapshot()
    response=client.post(root+'/versions',content=raw,headers={'content-type':'application/json'})
    assert response.status_code==expected
    if expected==413:assert snapshot()==before


@pytest.mark.parametrize('kind', ['permission', 'valid_from', 'asserted_at'])
@pytest.mark.parametrize('offset', [-1, 0, 1])
def test_eligibility_boundary_during_response_encoding(request, monkeypatch, kind, offset):
    pair = request.getfixturevalue('permission_knowledge_pair' if kind == 'permission' else 'knowledge_pair')
    g, _ = pair
    end = NOW + timedelta(seconds=1)
    if kind != 'permission':
        future_assertion(g, end, field=kind)
    published(g)
    clock = [NOW]
    monkeypatch.setattr(observation, '_time', lambda now: clock[0])
    canonical = s.canonical
    encoded_values = []

    def advance(value):
        raw = canonical(value)
        if isinstance(value, dict) and value.get('format') == 'ra-knowledge-retrieval/1':
            encoded_values.append(value)
            clock[0] = end + timedelta(microseconds=offset)
        return raw

    monkeypatch.setattr(s, 'canonical', advance)
    before = snapshot()
    response = client.post(f"/api/research-projects/{g['project']}/contexts/{g['ctx']}/knowledge/query", json=query())
    assert len(encoded_values) == 1 and encoded_values[0]['matches']
    assert s.timestamp(encoded_values[0]['eligibility_until']) == end
    assert response.headers['cache-control'] == 'no-store'
    if offset < 0:
        assert response.status_code == 200 and response.json()['matches']
    else:
        assert response.status_code == 409
        assert response.json() == {'status': 'rejected', 'code': 'knowledge_unavailable'}
        assert snapshot() == before


def test_mechanism_actor_filter_applies_to_api(api):
    root, g, _ = api
    c = content(category='mechanism')
    c['applicability']['actors'] = ['bearer']
    published(g, c)
    response = client.post(root+'/query', json=query())
    assert response.status_code == 200 and response.json()['matches'] == []
