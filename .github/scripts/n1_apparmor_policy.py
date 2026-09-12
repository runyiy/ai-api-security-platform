"""Approved, ephemeral hosted-CI bwrap policy; no package installation or replacement.

prepare is unprivileged and uses an isolated authenticated Ubuntu APT source.
load/verify require the hosted Ubuntu job; they never execute a sandbox payload.
"""
import argparse
import contextlib
import ctypes
import errno
import fnmatch
import hashlib
import io
import json
import os
from pathlib import Path
import re
import runpy
import stat
import subprocess
import sys
import tarfile

VERSION = '4.0.1really4.0.1-0ubuntu0.24.04.7'
DEB_SHA256 = 'bdac5b74d884643653565c52ed7483c9582e646ff72cce8d95d0eb8467a3139c'
PROFILE_SHA256 = '11d39094f044f0cda0febb3ad517b830301da6b2ce929664af09ee9e4dd264f9'
MEMBER = './usr/share/apparmor/extra-profiles/bwrap-userns-restrict'
SECURITY = Path('/sys/kernel/security/apparmor')
POLICY = Path('/etc/apparmor.d')
EXPECTED = {'bwrap': '/usr/bin/bwrap', 'unpriv_bwrap': 'unpriv_bwrap'}
PARSER = ['/usr/sbin/apparmor_parser', '--config-file', '/dev/null', '--skip-cache',
          '--base', str(POLICY), '--jobs=0']
ENV = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C.UTF-8'}
STATE = Path('/run/n1-bwrap-policy-identity')
BINARY = runpy.run_path(str(Path(__file__).with_name('n1_policy_binary.py')))


class PolicyError(RuntimeError):
    pass


def require(condition, code):
    if not condition:
        raise PolicyError('N1_APPARMOR_' + code)


def emit(label, value):
    print('N1_APPARMOR ' + json.dumps({label: value}, ensure_ascii=True), flush=True)


def read(path, limit=65536):
    with Path(path).open('rb') as source:
        value = source.read(limit + 1)
    require(len(value) <= limit, 'READ_LIMIT')
    return value


def run(args, *, timeout=10, data=None, cwd=None, env=ENV):
    # Inputs are fixed tools and the authenticated, size-limited package/profile.
    # Parser uses jobs=0; APT and privileged execution also have outer deadlines.
    try:
        result = subprocess.run(args, input=data, capture_output=True, env=env,
                                cwd=cwd, timeout=timeout, close_fds=True)
    except subprocess.TimeoutExpired:
        raise PolicyError('N1_APPARMOR_COMMAND_TIMEOUT: ' + Path(args[0]).name) from None
    require(len(result.stdout) <= 8 * 1024 * 1024, 'COMMAND_OUTPUT_LIMIT')
    if result.stderr:
        emit('tool_diagnostic', {'tool': Path(args[0]).name,
                                 'text': result.stderr.decode(errors='replace')[:2048]})
    require(result.returncode == 0, 'COMMAND_FAILED: ' + Path(args[0]).name)
    return result.stdout


def profile_bytes(path):
    require(not Path(path).is_symlink(), 'PROFILE_SYMLINK')
    value = read(path, 4096)
    require(hashlib.sha256(value).hexdigest() == PROFILE_SHA256, 'PROFILE_IDENTITY')
    return value


def extract_profile(package):
    raw = read(package, 1024 * 1024)
    require(hashlib.sha256(raw).hexdigest() == DEB_SHA256, 'PACKAGE_IDENTITY')
    metadata = run(['/usr/bin/dpkg-deb', '-f', str(package), 'Package', 'Version', 'Architecture']).decode()
    require(metadata == f'Package: apparmor-profiles\nVersion: {VERSION}\nArchitecture: all\n',
            'PACKAGE_METADATA')
    archive = run(['/usr/bin/dpkg-deb', '--fsys-tarfile', str(package)])
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        matches = [member for member in tar if member.name == MEMBER]
        require(len(matches) == 1 and matches[0].isfile() and matches[0].size <= 4096,
                'PACKAGE_MEMBER')
        value = tar.extractfile(matches[0]).read()
    require(hashlib.sha256(value).hexdigest() == PROFILE_SHA256, 'PROFILE_IDENTITY')
    return value


def prepare(destination):
    destination = Path(destination).absolute()
    destination.mkdir(mode=0o700)  # Never reuse a directory or stale package lists.
    for name in ('lists', 'cache', 'lists/partial', 'cache/archives', 'cache/archives/partial', 'config.d'):
        (destination / name).mkdir()
    sources = destination / 'ubuntu.sources'
    sources.write_text('Types: deb\nURIs: https://archive.ubuntu.com/ubuntu\n'
                       'Suites: noble-updates noble-security\nComponents: main\nArchitectures: amd64\n'
                       'Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg\n')
    config = destination / 'apt.conf'
    config.write_text('Dir::Etc::parts ' + json.dumps(str(destination / 'config.d'))
                      + ';\nDir::Etc::main "/dev/null";\n')
    apt_env = {**ENV, 'APT_CONFIG': str(config)}
    options = []
    for key, value in {
        'Dir::Etc::sourcelist': str(sources), 'Dir::Etc::sourceparts': str(destination / 'config.d'),
        'Dir::Etc::main': '/dev/null', 'Dir::Etc::parts': str(destination / 'config.d'),
        'Dir::State::lists': str(destination / 'lists'), 'Dir::Cache': str(destination / 'cache'),
        'Acquire::AllowInsecureRepositories': 'false',
        'Acquire::AllowDowngradeToInsecureRepositories': 'false',
        'APT::Get::AllowUnauthenticated': 'false', 'Acquire::Retries': '0',
        'Acquire::https::Timeout': '20', 'APT::Update::Error-Mode': 'any',
    }.items():
        options.extend(['-o', key + '=' + value])
    run(['/usr/bin/apt-get', *options, 'update'], timeout=60, env=apt_env)
    metadata = run(['/usr/bin/apt-cache', *options, 'show', 'apparmor-profiles=' + VERSION],
                   env=apt_env).decode()
    records = [dict(line.split(': ', 1) for line in block.splitlines() if ': ' in line)
               for block in metadata.strip().split('\n\n')]
    require(bool(records) and all(record.get('Package') == 'apparmor-profiles'
            and record.get('Version') == VERSION and record.get('SHA256') == DEB_SHA256
            for record in records), 'AUTHENTICATED_METADATA_IDENTITY')
    run(['/usr/bin/apt-get', *options, 'download', 'apparmor-profiles=' + VERSION],
        timeout=30, cwd=destination, env=apt_env)
    packages = list(destination.glob('*.deb'))
    require(len(packages) == 1, 'PACKAGE_COUNT')
    profile = extract_profile(packages[0])
    (destination / 'profile').write_bytes(profile)
    (destination / 'authenticated-package-metadata').write_text(metadata)
    emit('authenticated_package', {'package': 'apparmor-profiles', 'version': VERSION,
                                   'deb_sha256': DEB_SHA256, 'profile_sha256': PROFILE_SHA256,
                                   'member': MEMBER, 'installation': 'none'})


def attachment_may_match(pattern, target='/usr/bin/bwrap'):
    # Conservative superset of AARE: brace alternatives and globs. Unknown syntax
    # is a conflict, never permission to replace policy. '*' crossing '/' and
    # broad character classes can reject extra cases, not miss an attachment.
    if pattern.startswith(('<', '"')) or '@' in pattern or '\\' in pattern:
        return True
    if not pattern.startswith(('/', '{')):
        return False  # Named profile with no executable attachment.
    patterns = [pattern]
    while any('{' in value for value in patterns):
        expanded = []
        for value in patterns:
            match = re.search(r'\{([^{}]*)\}', value)
            if not match:
                require('{' not in value, 'ATTACHMENT_UNRESOLVED')
                expanded.append(value)
                continue
            expanded.extend(value[:match.start()] + part + value[match.end():]
                            for part in match[1].split(','))
        require(len(expanded) <= 128, 'ATTACHMENT_EXPANSION_LIMIT')
        patterns = expanded
    for value in patterns:
        value = re.sub(r'\[[^]]*\]', '?', value)
        if any(char in value for char in '[]}^'):
            return True
        if fnmatch.fnmatchcase(target, value):
            return True
    return False


@contextlib.contextmanager
def evidence(operation, field):
    # Only fixed operation/field labels and numeric errno leave this boundary.
    # Never expose filenames, link text, evidence bytes or OS exception text.
    try:
        yield
    except (PolicyError, OSError, ValueError) as error:
        if isinstance(error, PolicyError) and hasattr(error, 'diagnostic'):
            raise
        number = error.errno if isinstance(error, OSError) else None
        category = ('denied_access' if number in (errno.EACCES, errno.EPERM) else
                    'missing_interface' if number == errno.ENOENT else
                    'traversal_failure' if number in (errno.ELOOP, errno.ENOTDIR, errno.EXDEV) else
                    'io_failure' if number is not None else 'malformed_evidence')
        failure = PolicyError(str(error) if isinstance(error, PolicyError)
                              else 'N1_APPARMOR_EVIDENCE_UNAVAILABLE')
        failure.diagnostic = {'operation': operation, 'field': field,
                              'category': category, 'errno': number}
        raise failure from None


def object_identity(info):
    return info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode)


def verify_filesystem(fd, expected):
    # Linux statfs is at most 120 bytes on the supported hosted amd64 ABI;
    # f_type is its first native long. An oversized aligned buffer avoids
    # depending on the remaining libc struct fields.
    buffer = (ctypes.c_long * 32)()
    libc = ctypes.CDLL(None, use_errno=True)
    call = libc.fstatfs
    call.argtypes = [ctypes.c_int, ctypes.c_void_p]
    call.restype = ctypes.c_int
    if call(fd, ctypes.byref(buffer)) != 0:
        raise OSError(ctypes.get_errno(), 'fstatfs')
    require(buffer[0] == expected, 'POLICY_FILESYSTEM_IDENTITY')


@contextlib.contextmanager
def opened(path, flags, field, *, parent=None, follow=False):
    with evidence('open', field):
        fd = os.open(path, flags | os.O_CLOEXEC | (0 if follow else os.O_NOFOLLOW),
                     dir_fd=parent)
    try:
        yield fd
    finally:
        os.close(fd)


@contextlib.contextmanager
def policy_namespace(security):
    # Walk the ordinary absolute securityfs path one component at a time.
    # Only the fixed final policy component may use kernel magic traversal.
    with contextlib.ExitStack() as stack:
        current = stack.enter_context(opened('/', os.O_RDONLY | os.O_DIRECTORY, 'security'))
        parts = Path(security).absolute().parts[1:]
        with evidence('validate', 'security'):
            require(len(parts) <= 64 and all(part not in ('.', '..') for part in parts),
                    'POLICY_ROOT_LAYOUT')
        for part in parts:
            current = stack.enter_context(opened(part, os.O_RDONLY | os.O_DIRECTORY,
                                                 'security', parent=current))
        with evidence('fstatfs', 'security'):
            verify_filesystem(current, 0x73636673)  # SECURITYFS_MAGIC
        namespace = stack.enter_context(opened('policy', os.O_RDONLY | os.O_DIRECTORY,
                                                'policy', parent=current, follow=True))
        with evidence('fstatfs', 'policy'):
            verify_filesystem(namespace, 0x5a3c69f0)  # AAFS_MAGIC
        with evidence('stat', 'policy'):
            require(object_identity(os.fstat(namespace)) == object_identity(
                os.stat('policy', dir_fd=current)), 'POLICY_NAMESPACE_IDENTITY')
        yield namespace
        with evidence('stat', 'policy'):
            require(object_identity(os.fstat(namespace)) == object_identity(
                os.stat('policy', dir_fd=current)), 'POLICY_NAMESPACE_IDENTITY')


def read_evidence(parent, filename, limit, field=None):
    field = field or filename
    with opened(filename, os.O_RDONLY | os.O_NONBLOCK, field, parent=parent) as fd:
        return read_descriptor(fd, limit, field)


def read_descriptor(fd, limit, field):
    with evidence('fstat', field):
        require(stat.S_ISREG(os.fstat(fd).st_mode), 'POLICY_OBJECT_TYPE')
    with evidence('read', field):
        # revision is pollable and must only receive one read.
        if field == 'revision':
            value = os.read(fd, limit + 1)
        else:
            chunks, count = [], 0
            while count <= limit:
                chunk = os.read(fd, min(65536, limit + 1 - count))
                if not chunk:
                    break
                chunks.append(chunk)
                count += len(chunk)
            value = b''.join(chunks)
        require(len(value) <= limit, 'READ_LIMIT')
        return value


def text_evidence(parent, filename, limit, field=None):
    field = field or filename
    value = read_evidence(parent, filename, limit, field)
    with evidence('decode', field):
        return value.decode().strip()


def revision(security=SECURITY, *, namespace=None):
    if namespace is None:
        with policy_namespace(security) as fd:
            return revision(security, namespace=fd)
    value = text_evidence(namespace, 'revision', 128)
    with evidence('validate', 'revision'):
        require(value.isascii() and value.isdecimal(), 'POLICY_REVISION_UNAVAILABLE')
    return value


def binary_identity(data):
    try:
        return BINARY['parse'](data)
    except (ValueError, IndexError, OverflowError):
        raise PolicyError('N1_APPARMOR_BINARY_EVIDENCE_UNRESOLVED') from None


def loaded_entry(directory, depth, name, entry, cache, namespace):
    # Parse the kernel's bounded relative link layout, then independently open
    # canonical objects from the pinned namespace without following ANY links.
    # Link text chooses a candidate; kernel object identity authorizes it.
    fields = {'raw_data': 'raw_data', 'raw_sha256': 'sha256', 'raw_abi': 'abi'}
    slots = []
    for field, filename in fields.items():
        with evidence('readlink', field):
            target = os.readlink(field, dir_fd=directory)
        with evidence('validate_link', field):
            parts = target.split('/')
            require(len(target) <= 4096 and len(parts) == depth * 2 + 3
                    and parts[:depth * 2] == ['..'] * (depth * 2)
                    and parts[-3] == 'raw_data' and parts[-1] == filename
                    and parts[-2] not in ('', '.', '..'), 'RAW_POLICY_LINK_IDENTITY')
            slots.append(parts[-2])
    with evidence('validate_link', 'raw_abi'):
        require(len(set(slots)) == 1, 'RAW_POLICY_LINK_IDENTITY')
    with contextlib.ExitStack() as stack:
        raw_root = stack.enter_context(opened('raw_data', os.O_RDONLY | os.O_DIRECTORY,
                                              'raw_root', parent=namespace))
        blob = stack.enter_context(opened(slots[0], os.O_RDONLY | os.O_DIRECTORY,
                                          'raw_blob', parent=raw_root))
        descriptors, identities = {}, {}
        for field, filename in fields.items():
            fd = stack.enter_context(opened(filename, os.O_RDONLY | os.O_NONBLOCK,
                                            field, parent=blob))
            with evidence('stat_identity', field):
                info = os.fstat(fd)
                require(stat.S_ISREG(info.st_mode)
                        and info.st_dev == os.fstat(namespace).st_dev
                        and object_identity(info) == object_identity(os.stat(field, dir_fd=directory)),
                        'RAW_POLICY_LINK_IDENTITY')
                identities[field] = object_identity(info)
            descriptors[field] = fd
        key = tuple(identities.values())
        if key not in cache:
            remaining = 64 * 1024 * 1024 - cache['bytes']
            with evidence('validate', 'raw_data'):
                require(remaining > 0, 'INVENTORY_BYTE_LIMIT')
            data = read_descriptor(descriptors['raw_data'], min(BINARY['MAX_BLOB'], remaining), 'raw_data')
            cache['bytes'] += len(data)
            with evidence('validate', 'raw_sha256'):
                digest = hashlib.sha256(data).hexdigest()
                require(read_descriptor(descriptors['raw_sha256'], 128, 'raw_sha256').decode().strip()
                        == digest, 'RAW_POLICY_HASH_MISMATCH')
            with evidence('parse', 'raw_data'):
                records = binary_identity(data)
            with evidence('validate', 'raw_abi'):
                abi = int(read_descriptor(descriptors['raw_abi'], 32, 'raw_abi').decode().strip())
                # The export describes the LAST header of the COMPLETE blob.
                require(list(records.values())[-1]['abi'] == abi, 'RAW_POLICY_ABI_MISMATCH')
            cache[key] = records
        with evidence('validate', 'raw_data'):
            record = cache[key].get(name)
            require(record is not None, 'LOADED_PROFILE_MISSING_FROM_BLOB')
        digest = text_evidence(directory, 'sha256', 128)
        with evidence('validate', 'sha256'):
            require(digest == record['sha256'], 'LOADED_PROFILE_HASH_MISMATCH')
        for field in ('attach', 'mode'):
            with evidence('validate', field):
                require(record[field] == entry[field], 'LOADED_PROFILE_EXPORT_MISMATCH')
        for field in fields:
            with evidence('stat_identity', field):
                require(object_identity(os.stat(field, dir_fd=directory)) == identities[field],
                        'RAW_POLICY_LINK_IDENTITY')
        return record


def snapshot(security=SECURITY):
    with policy_namespace(security) as namespace:
        return namespace_snapshot(namespace, security)


def namespace_snapshot(namespace, security):
    original_revision = revision(security, namespace=namespace)
    result = {}
    cache, failures, unknowns = {'bytes': 0}, [], []

    def visit(parent, prefix, depth):
        with evidence('listdir', 'profiles'):
            # scandir is streamed; do not allocate an unbounded directory list.
            with os.scandir(parent) as entries:
                names = []
                for item in entries:
                    require(len(names) < 4096, 'POLICY_INVENTORY_LIMIT')
                    names.append(item.name)
        for basename in names:
            with evidence('validate', 'profiles'):
                require(depth <= 64 and len(result) < 4096, 'POLICY_INVENTORY_LIMIT')
            with opened(basename, os.O_RDONLY | os.O_DIRECTORY, 'profile', parent=parent) as directory:
                name = prefix + text_evidence(directory, 'name', 4096)
                with evidence('validate', 'name'):
                    require(name and len(name) <= 4096 and name not in result,
                            'POLICY_INVENTORY_AMBIGUOUS')
                entry = {}
                try:
                    entry['attach'] = text_evidence(directory, 'attach', 4096)
                    entry['mode'] = text_evidence(directory, 'mode', 128)
                    result[name] = loaded_entry(directory, depth, name, entry, cache, namespace)
                    status = {'name': name[:256], 'result': 'resolved',
                              'matches': result[name]['matches'], 'sha256': result[name]['sha256'],
                              'raw_sha256': result[name]['raw_sha256']}
                except PolicyError as error:
                    result[name] = entry
                    status = {'name': name[:256], 'result': str(error),
                              **getattr(error, 'diagnostic', {})}
                    failures.append(status)
                if entry.get('attach') == '<unknown>':
                    unknowns.append(status)
                with evidence('stat', 'profiles'):
                    try:
                        os.stat('profiles', dir_fd=directory, follow_symlinks=False)
                    except FileNotFoundError:
                        continue
                with opened('profiles', os.O_RDONLY | os.O_DIRECTORY, 'profiles', parent=directory) as children:
                    visit(children, name + '//', depth + 1)

    with opened('profiles', os.O_RDONLY | os.O_DIRECTORY, 'profiles', parent=namespace) as root:
        visit(root, '', 1)
    emit('unknown_attachments', unknowns)
    emit('inventory', {'profiles': len(result), 'raw_bytes': cache['bytes'],
                       'revision': original_revision, 'failures': failures})
    require(revision(security, namespace=namespace) == original_revision, 'POLICY_CHANGED_DURING_INVENTORY')
    require(not failures, 'POLICY_INVENTORY_UNRESOLVED')
    return result


def check_conflicts(inventory, policy=POLICY):
    conflicts = []
    for name, entry in inventory.items():
        name_conflict = any(part in EXPECTED for part in name.split('//'))
        if entry['attach'] == '<unknown>':
            require('sha256' in entry and 'matches' in entry, 'ATTACHMENT_UNRESOLVED')
            attachment_conflict = bool(entry['matches'])
        else:
            attachment_conflict = bool(entry.get('matches')) or any(
                attachment_may_match(entry['attach'], path) for path in ('/usr/bin/bwrap', '/bin/bwrap'))
        if name_conflict or attachment_conflict:
            emit('conflicting_profile', {'name': name[:256], 'attach': entry['attach'][:256]})
            conflicts.append('PROFILE_NAME_CONFLICT' if name_conflict else 'ATTACHMENT_CONFLICT')
    require(not conflicts, ','.join(conflicts))
    for name in ('bwrap-userns-restrict', 'bwrap', 'usr.bin.bwrap', 'unpriv_bwrap',
                 'local/bwrap-userns-restrict', 'local/unpriv_bwrap',
                 'disable/bwrap-userns-restrict', 'force-complain/bwrap-userns-restrict'):
        require(not os.path.lexists(policy / name), 'EXISTING_POLICY_OR_OVERRIDE: ' + name)


def global_restrictions():
    paths = {'enabled': '/sys/module/apparmor/parameters/enabled',
             'userns': '/proc/sys/kernel/apparmor_restrict_unprivileged_userns',
             'unconfined': '/proc/sys/kernel/apparmor_restrict_unprivileged_unconfined'}
    state = {key: read(path, 32).decode().strip() for key, path in paths.items()}
    # Preserve the observed hosted baseline. The distinct unconfined-transition
    # control was 0 in run 34670509334; do not silently enable or relax it.
    require(state == {'enabled': 'Y', 'userns': '1', 'unconfined': '0'}, 'GLOBAL_RESTRICTIONS_REQUIRED')
    emit('global_restrictions', state)
    return state


def hosted_only():
    require(os.getuid() == 0 and os.environ.get('GITHUB_ACTIONS') == 'true'
            and os.environ.get('RUNNER_ENVIRONMENT') == 'github-hosted', 'HOSTED_JOB_REQUIRED')
    release = read('/etc/os-release').decode()
    require('ID=ubuntu\n' in release and 'VERSION_ID="24.04"\n' in release, 'UBUNTU_24_04_REQUIRED')
    require(Path('/usr/bin/bwrap').resolve() == Path('/usr/bin/bwrap'), 'EXECUTABLE_ATTACHMENT')


def verify_loaded(inventory, compiled):
    require(set(compiled) == set(EXPECTED), 'COMPILED_PROFILE_NAMES')
    for name in EXPECTED:
        require(compiled[name]['mode'] == 'enforce' and inventory.get(name) == compiled[name],
                'LOADED_POLICY_IDENTITY')
    emit('loaded_profiles', {name: inventory[name] for name in EXPECTED})


def load(path, state=STATE):
    hosted_only()
    profile = profile_bytes(path)
    original_controls = global_restrictions()
    original_revision = revision()
    before = snapshot()
    check_conflicts(before)
    # Offline compilation first; load only these bytes with add semantics, never
    # replace or activate a policy directory. No files are installed into /etc.
    compiled = run([*PARSER, '--stdout'], data=profile)
    identity = binary_identity(compiled)
    require(set(identity) == set(EXPECTED) and all(entry['mode'] == 'enforce' for entry in identity.values()),
            'COMPILED_POLICY_IDENTITY')
    require(identity['bwrap']['matches'] == ['/usr/bin/bwrap']
            and identity['unpriv_bwrap']['matches'] == [], 'COMPILED_ATTACHMENTS')
    require(snapshot() == before, 'POLICY_CHANGED_BEFORE_LOAD')
    check_conflicts(before)
    require(revision() == original_revision, 'POLICY_CHANGED_BEFORE_LOAD')
    # Exclusive root-owned state survives only for this ephemeral job. A later
    # verification must use this actual compilation and full unrelated baseline.
    state.mkdir(mode=0o700)
    run([*PARSER, '--binary', '--add'], data=compiled)
    loaded_revision = revision()
    after = snapshot()
    verify_loaded(after, identity)
    require({name: entry for name, entry in after.items() if name not in EXPECTED} == before,
            'UNRELATED_POLICY_CHANGED')
    require(global_restrictions() == original_controls, 'GLOBAL_RESTRICTIONS_CHANGED')
    require(revision() == loaded_revision, 'POLICY_CHANGED_AFTER_LOAD')
    evidence = {'source_sha256': PROFILE_SHA256, 'compiled': identity, 'before': before,
                'controls': original_controls, 'revision': loaded_revision,
                'compiler_sha256': hashlib.sha256(read(PARSER[0], 8 * 1024 * 1024)).hexdigest(),
                'compiler_version': run([PARSER[0], '--version']).decode().strip()}
    with (state / 'identity.json').open('x') as output:
        json.dump(evidence, output)
    emit('loaded_policy_identity', {key: value for key, value in evidence.items() if key != 'before'})


def verify(state=STATE):
    hosted_only()
    for path in (state, state / 'identity.json'):
        info = path.lstat()
        require(not stat.S_ISLNK(info.st_mode) and info.st_uid == 0
                and info.st_mode & 0o022 == 0, 'SAVED_IDENTITY_OWNERSHIP')
    evidence = json.loads(read(state / 'identity.json', 4 * 1024 * 1024))
    require(evidence['source_sha256'] == PROFILE_SHA256, 'SAVED_SOURCE_IDENTITY')
    require(revision() == evidence['revision'], 'POLICY_REVISION_DRIFT')
    after = snapshot()
    verify_loaded(after, evidence['compiled'])
    require({name: entry for name, entry in after.items() if name not in EXPECTED} == evidence['before'],
            'UNRELATED_POLICY_CHANGED')
    require(global_restrictions() == evidence['controls'], 'GLOBAL_RESTRICTIONS_CHANGED')
    require(revision() == evidence['revision'], 'POLICY_REVISION_DRIFT')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'load', 'verify', 'offline'))
    parser.add_argument('path', nargs='?')
    args = parser.parse_args()
    try:
        if args.action == 'prepare':
            prepare(args.path)
        elif args.action == 'load':
            load(args.path)
        elif args.action == 'verify':
            verify()
        else:
            run([*PARSER, '--skip-kernel-load', '--kernel-features', str(POLICY / 'abi/4.0')],
                data=profile_bytes(args.path))
            emit('offline_syntax', 'passed; no kernel policy loaded')
    except (PolicyError, OSError, ValueError, KeyError, TypeError, tarfile.TarError) as error:
        message = str(error) if isinstance(error, PolicyError) else 'N1_APPARMOR_EVIDENCE_UNAVAILABLE'
        if hasattr(error, 'diagnostic'):
            emit('evidence_failure', error.diagnostic)
        print('::error::' + message, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
