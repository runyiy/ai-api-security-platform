"""Fresh isolated fake adapter entry point; no database or control credentials."""
import sys
sys.path[:0] = ['/code', '/packages']

from dataclasses import asdict
import json
from pathlib import Path
from app.ai.proposals.adapter import OpenAIProposalAdapter
from app.ai.proposals.bindings import Configuration, PreparedProposal, ReservationReceipt
from app.ai.proposals.codec import bounded_json, canonical
from app.ai.proposals.transport import (MemoryWire, MemoryConnector, MemoryResolver,
    MemorySecret, ProviderTransport)
from app.ai.w2.ipc import CallClient, RemoteClock, registry_from
from app.ai.w2.records import utc
from app.ai.w2.w1_bridge import MemoryPermitPort, TerminalObservationPort


def main():
    value = bounded_json(Path('/code/call.json').read_bytes(), maximum=131072, depth=16, nodes=16384)
    clock = RemoteClock(value['wall'], value['remaining'])
    client = CallClient('/call/endpoint', value['key_digest'], clock)
    config = Configuration(**value['config'])
    prepared = PreparedProposal(value['payload'].encode(), registry_from(value['registry']))
    receipt = ReservationReceipt(**{**value['receipt'], 'expires_at': utc(value['receipt']['expires_at'])})
    wire = MemoryWire(value['response'].encode(), '8.8.8.8', w2_bound=True,
        permit_port=MemoryPermitPort(value['body_digest'], client.consume, client.close_stream, clock.remaining))
    transport = ProviderTransport(resolver=MemoryResolver(('8.8.8.8',)), connector=MemoryConnector(wire),
        secret=MemorySecret(config.secret_ref, config.secret_version, b'synthetic-provider-key-only'), execution_kind='synthetic')
    adapter = OpenAIProposalAdapter(transport=transport, authority=client, coordination=client, clock=clock,
        terminal_observer=TerminalObservationPort(client.observe_terminal))
    try:
        outcome = adapter.propose_once(prepared=prepared, config=config, receipt=receipt)
        print(canonical(asdict(outcome)).decode())
    finally:
        client.close()


if __name__ == '__main__':
    main()
