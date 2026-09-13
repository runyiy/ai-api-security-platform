"""Owned local TLS fixture capabilities; no host/port, DNS or operator secrets.

These co-located, same-UID processes prove protocol mechanics only. They cannot
attest the separate Linux service UID/netns or the independently administered
off-host witness required for operation. There is no production mode.
"""
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import multiprocessing
import ctypes
import os
from pathlib import Path
import socket
import signal
import select
import ssl
import tempfile
import threading
import time

from app.ai.proposals.codec import canonical
from app.ai.w2.records import PortError, require
from .journal import Journal, Witness
from .protocol import peer_identity, receive, send, socket_identity


@dataclass(frozen=True)
class Peer:
    path: str
    inode: tuple
    pid: int
    uid: int
    ca: str
    certificate: str
    key: str
    name: str
    identity: str
    write_limit: int | None = None

    def connect(self, timeout=3):
        require(0 < timeout <= 3 and socket_identity(self.path) == self.inode, 'AUTHORITY_UNAVAILABLE')
        wire = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            wire.settimeout(timeout)
            wire.connect(self.path)
            peer_identity(wire, self.uid, self.pid)
            context = ssl.create_default_context(cafile=self.ca)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.load_cert_chain(self.certificate, self.key)
            wrapped = context.wrap_socket(wire, server_hostname=self.name)
            wire = wrapped
            require(sha256(wire.getpeercert(binary_form=True)).hexdigest() == self.identity, 'AUTHORITY_UNAVAILABLE')
            return wire
        except BaseException:
            wire.close()
            raise


class WitnessClient:
    def __init__(self, peer):
        require(type(peer) is Peer)
        self.peer, self.identity = peer, peer.identity
        # Startup completes before the controller can acquire G or submit a
        # ticket. No TLS/credential preparation occurs inside a G/A operation.
        self.wire = peer.connect()
        self.lock = threading.Lock()

    def _call(self, op, data):
        require(self.lock.acquire(timeout=3), 'OBSERVER_UNAVAILABLE')
        try:
            require(self.wire is not None, 'OBSERVER_UNAVAILABLE')
            self.wire.settimeout(3)
            send(self.wire, dict(format='ra-broker-witness-control/1', op=op, data=data))
            reply = receive(self.wire)
            require(set(reply) == {'ok', 'data'} and reply['ok'] is True, 'OBSERVER_UNAVAILABLE')
            return reply['data']
        except BaseException:
            self.close()
            raise
        finally:
            self.lock.release()

    def close(self):
        if self.wire is not None:
            self.wire.close(); self.wire = None

    def append(self, value): return self._call('append', value)
    def status(self): return self._call('status', {})


def _certificates(root):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
    now = datetime.now(timezone.utc)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'owned-bjw-synthetic-ca')])
    ca = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(ca_key.public_key())
          .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=1))
          .not_valid_after(now + timedelta(hours=2)).add_extension(x509.BasicConstraints(ca=True, path_length=0), True)
          .sign(ca_key, hashes.SHA256()))
    ca_path = root / 'ca.pem'; ca_path.write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    result = {}
    for label, server in (('broker', False), ('witness', True), ('provider', True)):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, label + '.synthetic.invalid')])
        cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=1))
            .not_valid_after(now + timedelta(hours=2))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(label + '.synthetic.invalid')]), False)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH if server else ExtendedKeyUsageOID.CLIENT_AUTH]), False)
            .sign(ca_key, hashes.SHA256()))
        key_path, cert_path = root / (label + '.key'), root / (label + '.pem')
        key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                               serialization.NoEncryption()))
        key_path.chmod(0o600)
        cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        result[label] = (str(cert_path), str(key_path), sha256(cert.public_bytes(serialization.Encoding.DER)).hexdigest())
    return str(ca_path), result


def _listener(path, packet=False):
    require(len(os.fsencode(path)) <= 107, 'LIMIT_EXCEEDED')
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET if packet else socket.SOCK_STREAM)
    listener.bind(str(path)); os.chmod(path, 0o600)
    listener.listen(4); listener.settimeout(.1)
    return listener


def _parent_fence():
    # Linux fixture process lifetime only, not a host security-policy change.
    # An interrupted pytest owner cannot strand synthetic listeners.
    parent = os.getppid()
    require(ctypes.CDLL(None, use_errno=True).prctl(1, signal.SIGKILL, 0, 0, 0) == 0,
            'AUTHORITY_UNAVAILABLE')
    if os.getppid() != parent: os._exit(72)
    # Spawn bootstraps only these configuration-free modules. Drop even the
    # outer runner's generated TEST DSN before constructing any peer service.
    os.environ.clear()
    os.environ.update(PATH='/usr/bin:/bin', LANG='C.UTF-8', PYTHONDONTWRITEBYTECODE='1')


def _peer_main(path, ca, certificate, key, client_identity, broker_pid, ready, stop,
               kind, journal_path, identity, response, counts, faults):
    _parent_fence()
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certificate, key)
    context.load_verify_locations(cafile=ca)
    context.verify_mode = ssl.CERT_REQUIRED
    listener = _listener(path)
    journal = Journal(journal_path) if kind == 'witness' else None
    witness = Witness(journal, identity) if journal else None
    ready.set()
    try:
        while not stop.is_set():
            try: connection, _ = listener.accept()
            except socket.timeout: continue
            try:
                with connection:
                    connection.settimeout(3)
                    peer_identity(connection, os.getuid(), broker_pid.value)
                    with context.wrap_socket(connection, server_side=True) as wire:
                        require(sha256(wire.getpeercert(binary_form=True)).hexdigest() == client_identity, 'AUTHORITY_UNAVAILABLE')
                        if kind == 'witness':
                            messages = 0
                            while not stop.is_set():
                                if not wire.pending() and not select.select([wire], [], [], .1)[0]: continue
                                wire.settimeout(3)
                                message = receive(wire)
                                messages += 1
                                require(messages <= 4096, 'LIMIT_EXCEEDED')
                                require(set(message) == {'format', 'op', 'data'}
                                        and message['format'] == 'ra-broker-witness-control/1')
                                if message['op'] == 'append':
                                    result = witness.append(message['data'])
                                    if faults.get('drop_w_ack') == message['data']['kind']:
                                        raise PortError('OBSERVER_UNAVAILABLE')
                                else:
                                    require(message['op'] == 'status' and message['data'] == {})
                                    result = witness.status()
                                send(wire, dict(ok=True, data=result))
                        else:
                            counts['connections'].value += 1
                            data = bytearray()
                            until = time.monotonic() + 5
                            while b'\r\n\r\n' not in data:
                                require(len(data) < 45056 and time.monotonic() < until, 'LIMIT_EXCEEDED')
                                chunk = wire.recv(4096)
                                if not chunk: break
                                data.extend(chunk)
                            head, _, body = bytes(data).partition(b'\r\n\r\n')
                            counts['bytes'].value += len(data)
                            if b'\r\nAuthorization: Bearer synthetic_' not in head: continue
                            lengths = [line[16:] for line in head.split(b'\r\n') if line.startswith(b'Content-Length: ')]
                            require(len(lengths) == 1 and lengths[0].isdigit() and int(lengths[0]) <= 32768)
                            while len(body) < int(lengths[0]):
                                require(time.monotonic() < until, 'DEADLINE_EXCEEDED')
                                chunk = wire.recv(int(lengths[0]) - len(body))
                                if not chunk: break
                                body += chunk; counts['bytes'].value += len(chunk)
                            if len(body) != int(lengths[0]): continue
                            counts['requests'].value += 1
                            if faults.get('provider_timeout'):
                                stop.wait(6)
                                continue
                            wire.sendall(response)
                            # Closing this exact connection terminates W1's
                            # bounded HTTP parser, including its suffix check.
            except (OSError, ValueError, PortError):
                continue
    finally:
        listener.close()
        if journal: journal.close()


class Schedule:
    """Bootstrap-only local fault schedule. Never exposed through control IPC."""
    def __init__(self, spec, reached, resume):
        self.spec, self.reached, self.resume = spec, reached, resume
        self.kind = None

    def __call__(self, stage):
        # event-kind filtering is supplied by the broker's append wrapper.
        if stage == self.spec.get('stage') and (self.spec.get('kind') is None or self.kind == self.spec['kind']):
            self.reached.set()
            if self.spec.get('crash'): os._exit(71)
            if not self.resume.wait(10): raise PortError('OBSERVER_UNAVAILABLE')


class Signal:
    """Fixture scheduling only: a killed waiter cannot poison a shared lock.

    A single-byte shared flag has one writer. Polling observes the explicit
    schedule, never supplies authorization or substitutes elapsed time for ACK.
    """
    def __init__(self, context): self.flag = context.RawValue('b', 0)
    def set(self): self.flag.value = 1
    def is_set(self): return self.flag.value == 1
    def wait(self, timeout):
        until = time.monotonic() + timeout
        while not self.is_set() and time.monotonic() < until:
            time.sleep(.005)
        return self.is_set()


def _broker_main(config, provider, witness_peer, ready, stop, reached, resume, broker_pid):
    _parent_fence()
    from .authority import Broker
    until = time.monotonic() + 3
    while broker_pid.value != os.getpid():
        require(time.monotonic() < until, 'AUTHORITY_UNAVAILABLE')
        time.sleep(.005)
    journal = Journal(config['journal'])
    schedule = Schedule(config['faults'], reached, resume)
    witness = WitnessClient(witness_peer)
    broker = Broker(config['deployment'], config['stream'], journal, witness,
                    provider, config['wall'], schedule)
    original = broker._append
    def append(event_id, kind, data):
        schedule.kind = kind
        try: return original(event_id, kind, data)
        finally: schedule.kind = None
    broker._append = append
    listener = _listener(config['control'], packet=True)
    allowed = {name: getattr(broker, method) for name, method in dict(
        bootstrap='bootstrap', bind='bind', prepare='prepare', ticket='ticket_for', authorize='authorize',
        write='write', receive='receive', close='close_call', invalidate='invalidate',
        disposition='disposition', snapshot='snapshot').items()}
    slots = threading.BoundedSemaphore(4)
    workers = []
    def handle(connection):
        try:
            with connection:
                connection.settimeout(3)
                peer_identity(connection, config['controller_uid'], config['controller_pid'])
                message = receive(connection, packet=True)
                require(set(message) == {'format', 'op', 'data'}
                        and message['format'] == 'ra-broker-control/1' and message['op'] in allowed)
                try:
                    result = allowed[message['op']](message['data'])
                    if config['faults'].get('drop_control_ack') == message['op']: return
                    send(connection, dict(ok=True, data=result), packet=True)
                except Exception:
                    send(connection, dict(ok=False, data={'code': 'OBSERVER_UNAVAILABLE'}), packet=True)
        except Exception:
            pass  # No raw body/credential/error is returned or logged.
        finally:
            connection.close(); slots.release()
    ready.set()
    try:
        while not stop.is_set():
            try: connection, _ = listener.accept()
            except socket.timeout: continue
            if not slots.acquire(blocking=False): connection.close(); continue
            workers = [worker for worker in workers if worker.is_alive()]
            worker = threading.Thread(target=handle, args=(connection,), daemon=True)
            workers.append(worker); worker.start()
    finally:
        listener.close()
        for worker in workers: worker.join(.1)
        # Fixture owner will fence a stuck process; this is not an operational
        # revocation ACK, successful invalidation, or automatic takeover.
        broker._close_socket(); witness.close(); journal.close()


@contextmanager
def owned_synthetic_broker(deployment, stream, wall, response, *, faults=None, write_limit=None):
    """Mint all endpoints, identities and credentials; accepts no destination.

    The outer validation runner must already own/verify an isolated TEST DB.
    The fixture owner retains files for the test lifetime and stops only the
    multiprocessing children it created. No service, host policy or CI changes.
    """
    require(type(response) is bytes and len(response) <= 81920, 'LIMIT_EXCEEDED')
    require(write_limit is None or type(write_limit) is int and 1 <= write_limit <= 45056)
    require(faults is None or type(faults) is dict and set(faults) <= {
        'stage', 'kind', 'crash', 'drop_w_ack', 'provider_timeout', 'drop_control_ack',
        'wrong_provider_identity', 'wrong_provider_name'})
    context = multiprocessing.get_context('spawn')
    with tempfile.TemporaryDirectory(prefix='bj-', dir='/tmp') as directory:
        root = Path(directory); root.chmod(0o700)
        for name in ('b', 'w', 'tls', 'control'):
            (root / name).mkdir(mode=0o700)
        ca, certificates = _certificates(root / 'tls')
        broker_pid = context.Value('i', 0)
        counts = {name: context.Value('i', 0) for name in ('connections', 'requests', 'bytes')}
        stop, reached, resume = Signal(context), Signal(context), Signal(context)
        children = []
        peers = {}
        try:
            for name, short in (('provider', 'p'), ('witness', 'w')):
                cert, key, identity = certificates[name]
                ready = Signal(context)
                path = root / (short + '.sock')
                child = context.Process(target=_peer_main, args=(str(path), ca, cert, key,
                    certificates['broker'][2], broker_pid, ready, stop, name, str(root / 'w' / 'journal'),
                    identity, response, counts, faults or {}))
                child.start(); children.append(child)
                require(ready.wait(5) and child.is_alive(), 'AUTHORITY_UNAVAILABLE')
                peer_name = name + '.synthetic.invalid'
                if name == 'provider' and (faults or {}).get('wrong_provider_name'): peer_name = 'wrong.synthetic.invalid'
                if name == 'provider' and (faults or {}).get('wrong_provider_identity'): identity = 'f'*64
                peers[name] = Peer(str(path), socket_identity(path), child.pid, os.getuid(), ca,
                    certificates['broker'][0], certificates['broker'][1], peer_name, identity,
                    write_limit if name == 'provider' else None)
            config = dict(deployment=deployment, stream=stream, wall=wall, journal=str(root / 'b' / 'journal'),
                control=str(root / 'control' / 'endpoint'), controller_uid=os.getuid(), controller_pid=os.getpid(),
                faults=faults or {})
            ready = Signal(context)
            process = context.Process(target=_broker_main,
                args=(config, peers['provider'], peers['witness'], ready, stop, reached, resume, broker_pid))
            process.start(); children.append(process); broker_pid.value = process.pid
            require(ready.wait(5) and process.is_alive(), 'AUTHORITY_UNAVAILABLE')
            from .bridge import BrokerClient
            client = BrokerClient(config['control'], socket_identity(config['control']), process.pid, os.getuid())
            yield dict(client=client, process=process, root=root, counts=counts, reached=reached, resume=resume,
                config=config, peers=peers, broker_pid=broker_pid, stop=stop, context=context)
        finally:
            resume.set(); stop.set()
            for child in reversed(children):
                child.join(3)
                if child.is_alive(): child.kill(); child.join(3)
                require(not child.is_alive(), 'OBSERVER_UNAVAILABLE')
                child.close()
