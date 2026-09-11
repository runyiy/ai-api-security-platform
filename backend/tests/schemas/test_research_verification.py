from copy import deepcopy
import pytest
from pydantic import ValidationError
from app.schemas import research_verification as s

EXP=dict(role='probe',object_key='record_id',object_value='94',identity_key='subject_id',identity_value='fixture_p')


@pytest.mark.parametrize('field,value',[
    ('object_key','authenticated'),('identity_key','record_id'),('object_key','nested.id'),
    ('object_key','x'*33),('object_value','1'*17),('identity_value','x'*65),('identity_value','x y'),
    ('object_value',94),('role','owner'),('passed',True),('validator','eval(response)')])
def test_exact_fields_and_limits(field,value):
    data={**EXP,field:value}
    with pytest.raises(ValidationError):s.Expectation.model_validate(data)


def test_positive_edges_and_no_caller_authority():
    assert s.Expectation.model_validate({**EXP,'object_key':'x'*32,'object_value':'1'*16,'identity_value':'a'*64})
    for extra in ('passed','health','headers','url','fixture_id'):
        with pytest.raises(ValidationError):s.PlanInput.model_validate(dict(intent=dict(number=1,version=1,digest='a'*64),plan_id=1,**{extra:True}))
