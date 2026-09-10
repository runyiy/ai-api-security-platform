from datetime import timedelta
import pytest
from app.schemas import research_knowledge as s, research_rule_validation as v
from tests.research_knowledge_fixtures import content, NOW, REF


def request():
    c = content()
    return {'reference': {k: c[k] for k in ('scope', 'knowledge_id', 'version')} | {'digest': s.digest(c)},
            'valid_until': (NOW+timedelta(hours=1)).isoformat()}


@pytest.mark.parametrize('raw', [b'{"passed":true,"passed":false}', b'\xff', b'{"code":"__import__(os)"}',
    b'{"heldout":"W3-ISOLATION-CANARY"}', b'{"$ref":"https://example.invalid"}'])
def test_no_instructions_private_values_or_external_references(raw):
    with pytest.raises(s.KnowledgeError):s.validate(v.ValidateInput, raw)


@pytest.mark.parametrize('size', [32768, 32769])
def test_exact_actual_input_byte_boundary(size):
    raw = s.canonical(request())
    raw += b' '*(size-len(raw))
    if size == 32768:assert s.validate(v.ValidateInput, raw)
    else:
        with pytest.raises(s.KnowledgeError, match='knowledge_input_limit'):s.validate(v.ValidateInput, raw)


@pytest.mark.parametrize('value', [True, 1.0, '1', 0, 2147483648])
def test_proof_ids_are_strict_and_bounded(value):
    with pytest.raises(s.KnowledgeError):
        s.validate(v.ValidationReadInput, {'reference': request()['reference'],
            'validation_ref': {'validation_id': value, 'digest': '0'*64}})


@pytest.mark.parametrize('value', ['2031-04-03T12:00:00', 'not-a-time', '2031-04-03T12:00:00-00:00'])
def test_naive_unknown_offset_and_invalid_time_rejected(value):
    with pytest.raises(s.KnowledgeError):s.validate(v.ValidateInput, {**request(), 'valid_until': value})
