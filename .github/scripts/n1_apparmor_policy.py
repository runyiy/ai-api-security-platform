"""Approved, ephemeral hosted-CI bwrap policy; no package installation or replacement.

prepare is unprivileged and uses an isolated authenticated Ubuntu APT source.
load/verify require the hosted Ubuntu job; they never execute a sandbox payload.
"""
import argparse
import fnmatch
import hashlib
import io
import json
import os
from pathlib import Path
import re
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


def snapshot(security=SECURITY):
    root = security / 'policy/profiles'
    require(root.is_dir(), 'POLICY_INVENTORY_UNAVAILABLE')
    result = {}
    pending = [(root, '')]
    while pending:
        parent, prefix = pending.pop()
        for directory in parent.iterdir():
            require(directory.is_dir() and not directory.is_symlink(), 'POLICY_INVENTORY_LAYOUT')
            name = prefix + read(directory / 'name', 4096).decode().strip()
            require(name not in result and len(result) < 4096, 'POLICY_INVENTORY_AMBIGUOUS')
            result[name] = {'attach': read(directory / 'attach', 4096).decode().strip(),
                            'mode': read(directory / 'mode', 128).decode().strip()}
            children = directory / 'profiles'
            if children.exists():
                pending.append((children, name + '//'))
    return result


def check_conflicts(inventory, policy=POLICY):
    for name, entry in inventory.items():
        name_conflict = any(part in EXPECTED for part in name.split('//'))
        attachment_conflict = any(attachment_may_match(entry['attach'], path)
                                  for path in ('/usr/bin/bwrap', '/bin/bwrap'))
        if name_conflict or attachment_conflict:
            emit('conflicting_profile', {'name': name[:256], 'attach': entry['attach'][:256]})
        require(not name_conflict, 'PROFILE_NAME_CONFLICT')
        require(not attachment_conflict, 'ATTACHMENT_CONFLICT')
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


def verify_loaded(inventory):
    for name, attachment in EXPECTED.items():
        require(inventory.get(name) == {'attach': attachment, 'mode': 'enforce'},
                'LOADED_POLICY_IDENTITY')
    emit('loaded_profiles', {name: inventory[name] for name in EXPECTED})


def load(path):
    hosted_only()
    profile = profile_bytes(path)
    original_controls = global_restrictions()
    before = snapshot()
    check_conflicts(before)
    # Offline compilation first; load only these bytes with add semantics, never
    # replace or activate a policy directory. No files are installed into /etc.
    run([*PARSER, '--skip-kernel-load'], data=profile)
    require(snapshot() == before, 'POLICY_CHANGED_BEFORE_LOAD')
    check_conflicts(before)
    run([*PARSER, '--add'], data=profile)
    after = snapshot()
    verify_loaded(after)
    require({name: entry for name, entry in after.items() if name not in EXPECTED} == before,
            'UNRELATED_POLICY_CHANGED')
    require(global_restrictions() == original_controls, 'GLOBAL_RESTRICTIONS_CHANGED')
    emit('loaded_profile_sha256', PROFILE_SHA256)


def verify():
    hosted_only()
    global_restrictions()
    verify_loaded(snapshot())


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
    except (PolicyError, OSError, ValueError, tarfile.TarError) as error:
        message = str(error) if isinstance(error, PolicyError) else 'N1_APPARMOR_EVIDENCE_UNAVAILABLE'
        print('::error::' + message, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
