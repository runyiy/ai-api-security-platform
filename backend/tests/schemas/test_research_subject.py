import pytest
from app.schemas.research_subject import SubjectInput, SubjectCorrection, SubjectError, validate
from tests.research_subject_fixtures import proposal, NOW, REF


def value():return proposal({"target":1,"anonymous":2,"resource":3,"endpoint":4,"slot":5})


@pytest.mark.parametrize("field,limit",[("assertion_ids",16),("sources",8)])
def test_independent_list_limits(field,limit):
    p=value()
    make=lambda i:i if field=="assertion_ids" else {"observation_id":i,"source_entry_index":0}
    p[field]=[make(i+1) for i in range(limit)]
    validate(SubjectInput,p)
    p[field].append(make(limit+1))
    with pytest.raises(SubjectError):validate(SubjectInput,p)


@pytest.mark.parametrize("field",["target_id","test_identity_id","resource_id","owner_identity_id","endpoint_id","binding_id","credential_binding_id"])
def test_strict_id_bounds(field):
    p=value()
    if field=="credential_binding_id":p.update(identity_choice="bearer",session_state="unknown",credential_update="needed")
    for bad in (0,-1,2147483648,True,1.0,"1"):
        p[field]=bad
        with pytest.raises(SubjectError):validate(SubjectInput,p)
    p[field]=2147483647
    validate(SubjectInput,p)


@pytest.mark.parametrize("stamp",["2031-04-03T12:00:00Z","2031-04-03T05:00:00-07:00"])
def test_aware_session_facts_no_ttl(stamp):
    p=value();p.update(session_state="expired",session_reported_at=stamp,session_reference=REF)
    assert validate(SubjectInput,p).session_reported_at==stamp
    p["session_reported_at"]="2031-04-03T12:00:00"
    with pytest.raises(SubjectError):validate(SubjectInput,p)


def test_constructed_model_extra_invalid_values_revalidated():
    p=SubjectInput.model_construct(**(value()|{"target_id":True}))
    with pytest.raises(SubjectError):validate(SubjectInput,p)


def test_duplicate_and_missing_fields():
    p=value();p["assertion_ids"]=[1,1]
    with pytest.raises(SubjectError):validate(SubjectInput,p)
    for key in value():
        p=value();p.pop(key)
        with pytest.raises(SubjectError):validate(SubjectInput,p)
    validate(SubjectCorrection,{"expected_version":1,"correction_reference":REF,"proposal":value()})
