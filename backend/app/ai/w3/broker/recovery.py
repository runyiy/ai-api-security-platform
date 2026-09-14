"""One-call recovery lower bounds, never settlement or admission evidence."""
import re

from app.ai.proposals.bindings import RATE_CARD, USAGE_MAPPING
from app.ai.proposals.codec import canonical
from app.ai.w2.accounting import actual
from app.ai.w2.records import decode, require, MAX_N
from .protocol import validate_event, ZERO


def amount(key_digest=None, tokens=0, microusd=0):
    require(key_digest is None or (type(key_digest) is str and re.fullmatch('[0-9a-f]{64}', key_digest)))
    require(all(type(n) is int and 0 <= n <= MAX_N for n in (tokens, microusd)), 'LIMIT_EXCEEDED')
    require(key_digest is not None or tokens == microusd == 0, 'CONFLICT')
    return dict(format='ra-broker-recovery-liability/1', key_digest=key_digest, tokens=tokens, microusd=microusd)


def merge(left, right):
    """Dimension-wise maximum for the same call, including exact duplicates."""
    for value in (left, right):
        require(type(value) is dict and set(value) == {'format', 'key_digest', 'tokens', 'microusd'})
        require(value == amount(value['key_digest'], value['tokens'], value['microusd']), 'INVALID_RECORD')
    require(left['key_digest'] is None or right['key_digest'] is None
            or left['key_digest'] == right['key_digest'], 'CONFLICT')
    return amount(left['key_digest'] or right['key_digest'], max(left['tokens'], right['tokens']),
                  max(left['microusd'], right['microusd']))


def liability(events):
    """Use validated retained J/W metadata even when coverage/ACKs are missing.

    A durable local final is already a lower bound; a missing witness/local ACK
    prohibits settlement, not retention. Unknown rates, contradictory bindings
    or unrepresentable cost raise instead of returning a smaller numeric floor.
    """
    result, key, core, permit, intent, response, final = amount(), None, None, None, None, None, None
    previous, stream = ZERO, None
    for sequence, event in enumerate(events, 1):
        validate_event(event)
        require(event['sequence'] == sequence and event['previous'] == previous
                and (stream is None or event['stream'] == stream), 'CONFLICT')
        previous, stream = event['digest'], event['stream']
        data, kind = event['data'], event['kind']
        if kind == 'BINDING':
            require(key is None, 'CONFLICT')
            key, core, reserved = (decode(name, canonical(data[field])) for name, field in
                (('ReservationKey', 'key'), ('BindingCore', 'core'), ('Reservation', 'reservation')))
            require(key == reserved.key and key.core_digest == core.fingerprint()
                    and key.scope == core.scope and key.call_ref == core.call_ref
                    and core.rate_card == reserved.rate_card == RATE_CARD
                    and core.usage_mapping == reserved.usage_mapping == USAGE_MAPPING
                    and core.currency == reserved.currency == 'USD'
                    and (core.reserved_tokens, core.reserved_microusd)
                        == (reserved.reserved_tokens, reserved.reserved_microusd), 'CONFLICT')
            result = amount(key.fingerprint(), reserved.reserved_tokens, reserved.reserved_microusd)
        elif kind == 'PERMIT':
            require(key is not None and permit is None, 'CONFLICT')
            permit = decode('SendPermit', canonical(data['permit']))
            require(permit.key == key and permit.body_digest == core.body_digest, 'CONFLICT')
        elif kind == 'INTENT':
            require(permit is not None and data['permit'] == permit.document(), 'CONFLICT')
            intent = permit
        elif kind in ('PEER_RESPONSE', 'FINAL_USAGE'):
            require(intent is not None and data['exchange_id'] == permit.permit_id
                    and data['body_digest'] == core.body_digest
                    and data['measurement_kind'] == 'synthetic'
                    and data['terminal'] in ('COMPLETED', 'INCOMPLETE', 'FAILED', 'CANCELLED'), 'CONFLICT')
            evidence = {k: data[k] for k in ('exchange_id', 'body_digest', 'status', 'response_id',
                                            'terminal', 'measurement_kind', 'response_digest')}
            if kind == 'PEER_RESPONSE':
                require(response is None or response == evidence, 'CONFLICT')
                response = evidence
            else:
                require(response == evidence, 'CONFLICT')
                usage = decode('UsageView', canonical(data['usage']))
                require(usage.mapping == USAGE_MAPPING and (final is None or final == usage), 'CONFLICT')
                final = usage
                tokens, cost = actual(usage)
                result = merge(result, amount(key.fingerprint(), tokens, cost))
    return result
