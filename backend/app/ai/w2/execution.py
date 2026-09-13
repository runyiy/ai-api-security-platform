"""Explicit fake-only execution in the mandatory N1 process boundary.

This internal helper has no route, automatic task loop or default registration.
Only the bounded W1 projection enters the child. The controller and all SQL,
call core, registry, journal and witness state remain in the parent.
"""
from dataclasses import asdict
from hashlib import sha256
from pathlib import Path
import shutil
import tempfile

from app.ai.proposals.adapter import ProposalOutcome, Usage
from app.ai.proposals.bindings import AuthorizationSnapshot, PERMISSIONS
from app.ai.proposals.codec import bounded_json, canonical, CODES
from app.ai.proposals.contract import Display
from .records import require, stamp, decode, usage_view
from .ipc import CallServer, snapshot_document
from .sandbox import Sandbox, IsolationUnavailable


def child_projection(root, runtime, response):
    require(type(response) is bytes and len(response) <= 70000, 'LIMIT_EXCEEDED')
    root = Path(root)
    root.mkdir(mode=0o700, exist_ok=False)
    code, database, call = (root/name for name in ('code', 'database', 'call'))
    for path in (code, database, call):
        path.mkdir(mode=0o700)
    source = Path(__file__).parent
    for package in ('app', 'app/ai', 'app/ai/proposals', 'app/ai/w2'):
        path = code/package
        path.mkdir(exist_ok=True)
        (path/'__init__.py').write_text('')
    for path in (source.parent/'proposals').glob('*.py'):
        shutil.copyfile(path, code/'app/ai/proposals'/path.name)
    for name in ('records.py', 'ipc.py', 'w1_bridge.py'):
        shutil.copyfile(source/name, code/'app/ai/w2'/name)
    shutil.copyfile(source/'child.py', code/'child.py')
    p = runtime.prepared
    snapshot = snapshot_document(AuthorizationSnapshot(p.prepared.registry, p.config, PERMISSIONS,
        p.prepared.registry.valid_from, p.prepared.registry.expires_at, 'active'))
    receipt = asdict(runtime.receipt)
    receipt['expires_at'] = stamp(runtime.receipt.expires_at)
    value = dict(key_digest=runtime.key.fingerprint(), body_digest=p.core.body_digest,
        payload=p.prepared.payload.decode(), registry=snapshot['registry'], config=snapshot['config'],
        receipt=receipt, wall=stamp(runtime.deadline.check()), remaining=runtime.deadline.remaining(), response=response.decode())
    raw = canonical(value)
    require(len(raw) <= 131072, 'LIMIT_EXCEEDED')
    (code/'call.json').write_bytes(raw)
    return Sandbox(code, database, call)


def child_outcome(raw, runtime):
    doc = bounded_json(raw, maximum=8192, depth=6, nodes=1024)
    require(set(doc) == {'code', 'delivery', 'usage', 'display', 'refusal_code', 'measurement_kind'})
    require(doc['measurement_kind'] == 'synthetic' and (doc['code'] is None or doc['code'] in CODES))
    require(doc['delivery'] in ('not_sent', 'unknown', 'responded'))
    usage = decode('UsageView', canonical({'format': 'ra-w2-usage-view/1', **doc['usage']}))
    require(type(doc['display']) is list and len(doc['display']) <= 4, 'LIMIT_EXCEEDED')
    display = []
    seen = set()
    for index, item in enumerate(doc['display'], 1):
        require(set(item) == {'suggestion_ref', 'text', 'references', 'uncertainty_codes'})
        require(item['suggestion_ref'] == 's_'+str(index) and item['uncertainty_codes'] == ['NOT_EXECUTED'])
        refs = tuple(item['references'])
        if item['text'] == 'Suggested for human review. Not executed.':
            require(refs in runtime.prepared.prepared.registry.allowed_pairs)
        else:
            require(item['text'] == 'Review the referenced general rule. No project conclusion.'
                and len(refs) == 1 and any(refs[0] == pair[1] for pair in runtime.prepared.prepared.registry.allowed_pairs))
        require(refs not in seen)
        seen.add(refs)
        display.append(Display(item['suggestion_ref'], item['text'], refs, ('NOT_EXECUTED',)))
    require((doc['code'] is None and doc['refusal_code'] is None and display)
        or (not display and (doc['refusal_code'] is None or
            (doc['code'] == 'PROVIDER_REFUSAL' and doc['refusal_code'] == 'SAFETY_REFUSAL'))))
    return ProposalOutcome(doc['code'], doc['delivery'], Usage(**{k:v for k,v in usage.document().items() if k != 'format'}),
        tuple(display), doc['refusal_code'])


def _analysis_identity(runtime, completion, outcome):
    # Parent-only, volatile provenance, never a provider/accounting receipt.
    # The child cannot import this module or write the parent's runtime object.
    return ('ra-w3-origin/1', runtime.key.encode(), runtime.run.encode(),
            runtime.permit.encode(), completion.encode(),
            sha256(b'ra-w3-origin-analysis/1\n' + canonical(asdict(outcome))).digest())


def execute_fake(runtime, response, *, owned_directory=None):
    """Run one already reserved call. No retry or implicit gate reopening."""
    runtime._analysis_receipt = None
    with tempfile.TemporaryDirectory(prefix='w2-call-', dir=owned_directory) as temporary:
        sandbox = child_projection(Path(temporary)/'isolated', runtime, response)
        server = CallServer(sandbox.call_socket/'endpoint', runtime).start()
        try:
            try:
                result = child_outcome(sandbox.run(timeout=runtime.deadline.remaining(), maximum=8192), runtime)
            except Exception:
                result = ProposalOutcome('AUDIT_UNAVAILABLE', 'unknown', Usage())
        finally:
            server.close()
        completion, outcome = runtime.complete_v1(result)
        if completion.display_state == 'ELIGIBLE_NOW' and outcome.code is None:
            runtime._analysis_receipt = _analysis_identity(runtime, completion, outcome)
        return completion, outcome
