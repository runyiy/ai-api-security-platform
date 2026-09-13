"""Real call IPC rejects child authority minting and unrelated call selection."""
import json
import socket
import struct

import pytest
from sqlalchemy import select
from app.db.session import SessionLocal
from app.ai.w2 import schema as t
from app.ai.w2.execution import child_projection
from app.ai.w2.ipc import CallServer,receive_frame
from app.ai.w2.records import PortError
from tests.ai.test_w2_execution import response,balances
from tests.ai.w2_fixtures import w2,rule_pair,knowledge_pair,subject_pair,two_intake_targets,zero_capabilities  # noqa: F401


def test_t12_real_child_cannot_mint_authority_or_bypass_call_acceptance(w2,tmp_path):
    w2.reopen();rt=w2.runtime()
    sandbox=child_projection(tmp_path/'forgery',rt,response(rt))
    (sandbox.code/'child.py').write_text('''import sys
sys.path.insert(0,'/code')
import json,socket
from pathlib import Path
from app.ai.w2.ipc import send_frame,receive_frame
from app.ai.proposals.adapter import request_body
from app.ai.proposals.contract import input_document
value=json.loads(Path('/code/call.json').read_text())
assert not Path('/code/app/ai/w2/fake_authority.py').exists()
assert not Path('/code/app/ai/w2/accounting.py').exists()
assert not Path('/home').exists() and list(Path('/database').iterdir())==[]
denied=[]
with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as peer:
    peer.settimeout(3);peer.connect('/call/endpoint')
    for action in ('issue','invalidate','reopen','script_final','observe','register_call','restore_liability'):
        send_frame(peer,dict(action=action,key_digest=value['key_digest'],arguments={}))
        reply,body=receive_frame(peer)
        assert reply['ok'] is False and not body
        denied.append(action)
    send_frame(peer,dict(action='admit',key_digest='f'*64,arguments={}))
    assert receive_frame(peer)[0]['ok'] is False
    body=request_body(input_document(value['payload'].encode()))
    send_frame(peer,dict(action='write',key_digest=value['key_digest'],arguments={'timeout':1}),body)
    assert receive_frame(peer)[0]['ok'] is False
print(json.dumps(denied))
''')
    server=CallServer(sandbox.call_socket/'endpoint',rt).start()
    try:
        denied=json.loads(sandbox.run(timeout=10))
        assert len(denied)==7
    finally:server.close()
    assert w2.witness.accepted[rt.key.fingerprint()]==0 and w2.authority.coverage()
    assert not w2.authority.permits and not w2.authority.endpoint_bodies
    with SessionLocal() as db:
        assert db.scalar(select(t.admission.c.key_digest)) is None
        assert db.scalar(select(t.marker.c.key_digest)) is None
    assert rt.reconcile().state=='ZERO'
    assert all(b['settled_tokens']==b['held_tokens']==0 and b['calls']==1 for b in balances(rt.key))


@pytest.mark.parametrize('header',[(8193,0),(4097,1),(1,32769),(0,1)])
def test_t2_ipc_rejects_oversized_header_before_loading_body(header):
    left,right=socket.socketpair()
    try:
        right.settimeout(.5)
        left.sendall(struct.pack('!II',*header))
        with pytest.raises(PortError,match='LIMIT_EXCEEDED'):receive_frame(right)
    finally:
        left.close();right.close()
