"""Pure explanation execution over authored synthetic cases, without oracle access."""
from app.schemas.research_knowledge import Content


def evaluate(content, case):
    from app.services.research_knowledge import _applicable
    content = Content.model_validate(content.model_dump())
    _, actor, relationships, accesses, shape, baseline, instruction = case
    result = {'state': 'rejected', 'matched': False, 'relationship': 'unspecified',
              'expected_access': 'unspecified', 'missing_inputs': ['untrusted_material'],
              'execution_authorized': False}
    if instruction:
        return result  # Never interpret or return untrusted instructions/canaries.
    relationships, accesses = set(relationships), set(accesses)
    gaps = set()
    if len(relationships) > 1 or len(accesses) > 1:
        gaps.add('facts_conflict')
    elif not accesses:
        gaps.add('facts_missing')
    if actor == 'unknown':
        gaps.add('identity_missing')
    if actor == 'bearer':
        gaps.add('session_health_unverified')
    if not baseline:
        gaps.add('baseline_missing')
    if shape != 'path':
        gaps.add('preview_only_shape')
    matched = _applicable(content, {'proposal': {'identity_choice': actor}}, gaps) is not None
    state = 'explanation' if matched else 'no_match'
    if gaps:
        state = 'unsupported' if shape != 'path' else 'needs_input'
    result.update(state=state, matched=matched,
        relationship=next(iter(relationships)) if len(relationships) == 1 else 'unspecified',
        expected_access=next(iter(accesses)) if len(accesses) == 1 else 'unspecified',
        missing_inputs=sorted(gaps))
    return result
