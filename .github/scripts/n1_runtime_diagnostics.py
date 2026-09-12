"""Failure-only, read-only evidence for the existing N1 capability probe.

No application imports, namespace experiments, policy writes or probe retries.
Kernel records are limited to the probe's time window and comm=bwrap; a matching
name/window is supporting evidence, not proof of the exact failing process.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import shutil
import stat
import subprocess
import time


def emit(label, value):
    # JSON escaping prevents control characters/newlines becoming CI commands.
    print('N1_DIAGNOSTIC ' + json.dumps({label: value}, ensure_ascii=True), flush=True)


def read(path, limit=4096):
    try:
        with open(path, 'rb') as source:
            value = source.read(limit + 1)
        if len(value) > limit:
            return 'UNAVAILABLE: size limit'
        return value.decode('utf-8', errors='replace').strip().rstrip('\0')
    except OSError as error:
        return 'UNAVAILABLE: errno=' + str(error.errno)


def command(args):
    """Three seconds and 64 KiB per fixed read-only command; no raw stderr."""
    try:
        process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, close_fds=True,
                                   env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'},
                                   start_new_session=True)
    except OSError:
        return 'unavailable', ''
    output = bytearray()
    deadline = time.monotonic() + 3
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return 'timeout', ''
                for key, _ in selector.select(remaining):
                    chunk = os.read(key.fd, 4096)
                    if not chunk:
                        selector.unregister(key.fileobj)
                    output.extend(chunk)
                    if len(output) > 65536:
                        return 'output_limit', ''
        code = process.wait(timeout=max(.001, deadline - time.monotonic()))
        return 'exit=' + str(code), output.decode('utf-8', errors='replace')
    except subprocess.TimeoutExpired:
        return 'timeout', ''
    finally:
        # Only this newly owned read-only command group; never a runner service.
        if process.returncode is None:
            try:
                os.killpg(process.pid, 9)
            except ProcessLookupError:
                pass
        process.wait(timeout=1)
        process.stdout.close()


def audit_records(output):
    records = []
    allowed = {'apparmor', 'operation', 'class', 'profile', 'target', 'pid', 'comm',
               'capability', 'capname', 'family', 'sock_type', 'protocol',
               'requested_mask', 'denied_mask', 'error', 'arch', 'syscall', 'code'}
    for line in output.splitlines()[:80]:
        try:
            entry = json.loads(line)
        except (ValueError, TypeError):
            continue
        if not isinstance(entry, dict) or not isinstance(entry.get('MESSAGE'), str):
            continue
        message = entry['MESSAGE']
        fields = {key: quoted or plain for key, quoted, plain in
                  re.findall(r'\b(\w+)=(?:"([^"\n]*)"|([^\s]+))', message)}
        if fields.get('comm') != 'bwrap' or not (
                'apparmor' in fields or re.search(r'\btype=(?:SECCOMP|1326)\b', message)):
            continue
        # No raw MESSAGE, command line, environment, filenames or journal metadata.
        record = {key: value for key, value in fields.items() if key in allowed
                  and re.fullmatch(r'[A-Za-z0-9_./:+() -]{1,160}', value)}
        timestamp = entry.get('__REALTIME_TIMESTAMP', '')
        if isinstance(timestamp, str) and timestamp.isdigit():
            record['timestamp_us'] = timestamp[:20]
        records.append(record)
    return records


def report(started, ended):
    emit('scope', 'parent state and bwrap kernel records; absence/unavailability is not proof of no denial')
    executable = shutil.which('bwrap')
    emit('executable', executable)
    if executable:
        resolved = Path(executable).resolve()
        metadata = resolved.stat()
        identity = {'resolved': str(resolved), 'mode': oct(stat.S_IMODE(metadata.st_mode)),
                    'uid': metadata.st_uid, 'gid': metadata.st_gid}
        if stat.S_ISREG(metadata.st_mode) and metadata.st_size <= 4 * 1024 * 1024:
            identity['sha256'] = hashlib.sha256(resolved.read_bytes()).hexdigest()
        try:
            identity['file_capabilities_hex'] = os.getxattr(resolved, 'security.capability').hex()
        except OSError as error:
            identity['file_capabilities_errno'] = error.errno
        emit('executable_identity', identity)
        state, output = command(['/usr/bin/dpkg-query', '-S', str(resolved)])
        emit('package_owner', {'state': state, 'value': output.strip()[:512]})
    state, output = command(['/usr/bin/dpkg-query', '-W', '-f=${Package} ${Version} ${Architecture}\n',
                             'bubblewrap', 'apparmor'])
    emit('packages', {'state': state, 'value': output.strip()[:1024]})
    state, output = command(['/usr/bin/dpkg', '--verify', 'bubblewrap'])
    emit('package_verification', {'state': state, 'reported_differences': bool(output.strip())})
    emit('kernel_release', os.uname().release)
    emit('parent_lsm_label', read('/proc/self/attr/current'))
    status = dict(line.split(':', 1) for line in read('/proc/self/status').splitlines() if ':' in line)
    emit('parent_status', {key: status[key].strip() for key in (
        'Uid', 'Gid', 'CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb',
        'NoNewPrivs', 'Seccomp', 'Seccomp_filters') if key in status})
    for suffix in ('uid_map', 'gid_map'):
        emit('parent_' + suffix, read('/proc/self/' + suffix))
    for name in ('user', 'pid', 'net', 'ipc', 'uts', 'mnt'):
        try:
            emit('parent_namespace_' + name, os.readlink('/proc/self/ns/' + name))
        except OSError:
            emit('parent_namespace_' + name, 'UNAVAILABLE')
    for path in ('/sys/module/apparmor/parameters/enabled', '/sys/kernel/security/lsm',
                 '/proc/sys/kernel/apparmor_restrict_unprivileged_userns',
                 '/proc/sys/kernel/apparmor_restrict_unprivileged_unconfined',
                 '/proc/sys/kernel/unprivileged_userns_clone',
                 '/proc/sys/user/max_user_namespaces', '/proc/sys/user/max_net_namespaces'):
        emit(path, read(path))
    # sudo is used only for these two fixed reads, never the sandbox probe.
    # A root-side deadline also bounds queries an unprivileged parent cannot kill.
    privileged_read = ['/usr/bin/sudo', '-n', '/usr/bin/timeout', '--kill-after=1s', '1s']
    state, output = command([*privileged_read, '/usr/bin/grep', '-E',
                             r'^(bwrap|bubblewrap|unprivileged_userns)([- (]|$)',
                             '/sys/kernel/security/apparmor/profiles'])
    emit('relevant_loaded_profiles', {'state': state, 'value': output.strip()[:2048]})
    state, output = command([*privileged_read, '/usr/bin/journalctl', '--kernel',
                             '--since=@' + str(int(started)), '--until=@' + str(int(ended) + 1),
                             '--no-pager', '--output=json', '--lines=80', '--grep=comm="bwrap"'])
    emit('kernel_evidence', {'state': state, 'window_start': int(started),
                             'window_end': int(ended) + 1, 'records': audit_records(output)})
