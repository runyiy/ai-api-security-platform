"""CI policy guards; real compilation, simulated kernel exports, never loaded."""
import contextlib
import errno
import os
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import n1_apparmor_policy as policy


def compile_policy(body, *options):
    return policy.run([*policy.PARSER, '--kernel-features', '/etc/apparmor.d/abi/4.0',
                       *options, '--stdout'], data=('abi <abi/4.0>,\n' + body).encode())


def exported_blob(root, data, slot='one'):
    records = policy.binary_identity(data)
    raw = root / 'policy/raw_data' / slot
    raw.mkdir(parents=True)
    (raw / 'raw_data').write_bytes(data)
    (raw / 'sha256').write_text(hashlib.sha256(data).hexdigest())
    (raw / 'abi').write_text(str(next(iter(records.values()))['abi']))
    (root / 'policy/revision').write_text('1\n')
    for name, entry in records.items():
        directory = root / 'policy/profiles'
        for part in name.split('//'):
            directory = directory / part
            directory.mkdir(parents=True, exist_ok=True)
            if part != name.split('//')[-1]:
                directory = directory / 'profiles'
        for field in ('attach', 'mode', 'sha256'):
            (directory / field).write_text(entry[field] + '\n')
        (directory / 'name').write_text(name.split('//')[-1] + '\n')
        for field, target in (('raw_data', 'raw_data'), ('raw_sha256', 'sha256'), ('raw_abi', 'abi')):
            (directory / field).symlink_to(os.path.relpath(raw / target, directory))
    return records


class ExportFixtureTests(unittest.TestCase):
    def setUp(self):
        # Ordinary fixtures are deliberately not securityfs/apparmorfs. Only
        # this filesystem-type assertion is replaced; all traversal is real.
        check = patch.object(policy, 'verify_filesystem')
        self.filesystem_check = check.start()
        self.addCleanup(check.stop)


class PolicyTests(ExportFixtureTests):
    def test_attachment_conflicts_include_aliases_braces_globs_and_unknowns(self):
        with tempfile.TemporaryDirectory() as root:
            for attachment in ('/usr/bin/bwrap', '/bin/bwrap', '/{usr/,}bin/bwrap',
                               '/usr/bin/*', '/usr/**/bwrap', '/usr/bin/[ab]wrap',
                               '@{bin}/bwrap'):
                with self.subTest(attachment=attachment), self.assertRaisesRegex(
                        policy.PolicyError, 'ATTACHMENT_CONFLICT'):
                    policy.check_conflicts({'unrelated-name': {'attach': attachment}}, Path(root))
            with self.assertRaisesRegex(policy.PolicyError, 'ATTACHMENT_UNRESOLVED'):
                policy.check_conflicts({'unrelated-name': {'attach': '<unknown>'}}, Path(root))

    def test_unrelated_literal_and_glob_attachments_are_preserved(self):
        with tempfile.TemporaryDirectory() as root:
            policy.check_conflicts({'other': {'attach': '/usr/lib/other/**'},
                                    'named': {'attach': 'named'}}, Path(root))

    def test_existing_reserved_names_and_nested_names_are_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            for name in ('bwrap', 'unpriv_bwrap', 'parent//bwrap'):
                with self.subTest(name=name), self.assertRaisesRegex(policy.PolicyError, 'NAME_CONFLICT'):
                    policy.check_conflicts({name: {'attach': '/unrelated'}}, Path(root))

    def test_empty_and_broken_symlink_local_overrides_are_rejected(self):
        for name in ('local/bwrap-userns-restrict', 'local/unpriv_bwrap', 'bwrap-userns-restrict'):
            for symlink in (False, True):
                with self.subTest(name=name, symlink=symlink), tempfile.TemporaryDirectory() as root:
                    path = Path(root) / name
                    path.parent.mkdir(exist_ok=True)
                    path.symlink_to('/nonexistent-synthetic-policy') if symlink else path.touch()
                    with self.assertRaisesRegex(policy.PolicyError, 'EXISTING_POLICY_OR_OVERRIDE'):
                        policy.check_conflicts({}, Path(root))

    def test_modified_profile_and_package_fail_before_parsing(self):
        with tempfile.TemporaryDirectory() as root, patch.object(policy, 'run') as command:
            path = Path(root) / 'tampered'
            path.write_bytes(b'synthetic-unapproved-policy')
            with self.assertRaisesRegex(policy.PolicyError, 'PROFILE_IDENTITY'):
                policy.profile_bytes(path)
            with self.assertRaisesRegex(policy.PolicyError, 'PACKAGE_IDENTITY'):
                policy.extract_profile(path)
            command.assert_not_called()

    def test_missing_authenticated_metadata_blocks_download_and_isolates_apt_configuration(self):
        with tempfile.TemporaryDirectory() as root, patch.object(policy, 'run', side_effect=[b'', b'']) as command:
            with self.assertRaisesRegex(policy.PolicyError, 'AUTHENTICATED_METADATA_IDENTITY'):
                policy.prepare(Path(root) / 'fresh')
            self.assertEqual(command.call_count, 2)
            config = Path(command.call_args_list[0].kwargs['env']['APT_CONFIG'])
            self.assertIn(str(config.parent / 'config.d'), config.read_text())
            self.assertEqual(list((config.parent / 'config.d').iterdir()), [])
            self.assertIn('Dir::Etc::main "/dev/null";', config.read_text())
            self.assertNotIn('download', command.call_args_list[-1].args[0])

    def test_inaccessible_inventory_fails_closed(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(policy.PolicyError) as failure:
                policy.snapshot(Path(root))
            self.assertEqual(failure.exception.diagnostic, {
                'operation': 'open', 'field': 'policy',
                'category': 'missing_interface', 'errno': errno.ENOENT})

    def test_snapshot_keeps_nested_profile_names_distinct(self):
        with tempfile.TemporaryDirectory() as root:
            exported_blob(Path(root), compile_policy('profile first { profile child {} }\n'
                                                     'profile second { profile child {} }'))
            self.assertEqual(set(policy.snapshot(Path(root))),
                             {'first', 'first//child', 'second', 'second//child'})

    def test_nonhosted_execution_and_disabled_restrictions_fail(self):
        with patch.object(policy.os, 'getuid', return_value=1000):
            with self.assertRaisesRegex(policy.PolicyError, 'HOSTED_JOB_REQUIRED'):
                policy.hosted_only()
        with patch.object(policy, 'read', side_effect=[b'Y', b'0', b'1']):
            with self.assertRaisesRegex(policy.PolicyError, 'GLOBAL_RESTRICTIONS_REQUIRED'):
                policy.global_restrictions()

    def test_observed_hosted_global_baseline_is_preserved(self):
        with patch.object(policy, 'read', side_effect=[b'Y', b'1', b'0']):
            self.assertEqual(policy.global_restrictions(), {'enabled': 'Y', 'userns': '1', 'unconfined': '0'})

    def test_add_only_load_changes_only_expected_profiles(self):
        compiled = compile_policy('profile bwrap /usr/bin/bwrap {}\nprofile unpriv_bwrap {}')
        after = policy.binary_identity(compiled)
        with tempfile.TemporaryDirectory() as root, patch.object(policy, 'hosted_only'), \
                patch.object(policy, 'profile_bytes', return_value=b'approved'), \
                patch.object(policy, 'global_restrictions', return_value={'enabled': 'Y'}), \
                patch.object(policy, 'revision', return_value='1'), \
                patch.object(policy, 'snapshot', side_effect=[{}, {}, after]), \
                patch.object(policy, 'check_conflicts'), \
                patch.object(policy, 'run', side_effect=[compiled, b'', b'test compiler']) as command:
            policy.load('synthetic', Path(root) / 'state')
            self.assertEqual([call.args[0][-1] for call in command.call_args_list],
                             ['--stdout', '--add', '--version'])
            self.assertEqual(command.call_args_list[0].kwargs['data'], b'approved')
            self.assertEqual(command.call_args_list[1].kwargs['data'], compiled)
            self.assertIn('--binary', command.call_args_list[1].args[0])
            evidence = json.loads((Path(root) / 'state/identity.json').read_text())
            self.assertEqual(evidence['compiled'], after)

    def test_conflict_or_compile_failure_never_reaches_kernel_load(self):
        for conflict in (True, False):
            with self.subTest(conflict=conflict), patch.object(policy, 'hosted_only'), \
                    patch.object(policy, 'profile_bytes', return_value=b'approved'), \
                    patch.object(policy, 'global_restrictions'), patch.object(policy, 'snapshot', return_value={}), \
                    patch.object(policy, 'revision', return_value='1'), \
                    patch.object(policy, 'check_conflicts') as check, patch.object(policy, 'run') as command:
                if conflict:
                    check.side_effect = policy.PolicyError('N1_APPARMOR_PROFILE_NAME_CONFLICT')
                else:
                    command.side_effect = policy.PolicyError('N1_APPARMOR_COMMAND_FAILED')
                with self.assertRaises(policy.PolicyError):
                    policy.load('synthetic')
                self.assertFalse(any('--add' in call.args[0] for call in command.call_args_list))

    def test_missing_or_complain_child_profile_fails(self):
        compiled = policy.binary_identity(compile_policy('profile bwrap /usr/bin/bwrap {}\nprofile unpriv_bwrap {}'))
        for child in (None, {'attach': 'unpriv_bwrap', 'mode': 'complain'}):
            inventory = {'bwrap': {'attach': '/usr/bin/bwrap', 'mode': 'enforce'}}
            if child:
                inventory['unpriv_bwrap'] = child
            with self.assertRaisesRegex(policy.PolicyError, 'LOADED_POLICY_IDENTITY'):
                policy.verify_loaded(inventory, compiled)

    def test_command_timeout_is_bounded_and_reported(self):
        with self.assertRaisesRegex(policy.PolicyError, 'COMMAND_TIMEOUT'):
            policy.run(['/usr/bin/python3', '-I', '-c', 'import time; time.sleep(30)'], timeout=.05)


class BinaryEvidenceTests(ExportFixtureTests):
    @classmethod
    def setUpClass(cls):
        cls.unrelated = compile_policy('profile browser /opt/browser/{bin,other} flags=(unconfined) {}\n'
                                       'profile editor /usr/lib/editor/** {}')
        cls.approved_shape = compile_policy('profile bwrap /usr/bin/bwrap {}\nprofile unpriv_bwrap {}')

    def test_real_compiler_unknowns_resolve_in_one_inventory_without_disk_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            expected = exported_blob(root, self.unrelated)
            self.assertTrue(all(entry['attach'] == '<unknown>' for entry in expected.values()))
            self.assertEqual(expected['browser']['mode'], 'unconfined')
            with contextlib.redirect_stdout(io.StringIO()) as output:
                actual = policy.snapshot(root)
            self.assertEqual(actual, expected)
            self.assertEqual(output.getvalue().count('unknown_attachments'), 1)
            for name in ('browser', 'editor'):
                self.assertIn('"name": "' + name + '"', output.getvalue())
            policy.check_conflicts(actual, root / 'no-disk-sources')

    def test_real_compiler_differently_named_conflicts_and_unrelated_patterns(self):
        for pattern in ('/usr/bin/bwrap', '/{usr/,}bin/bwrap', '/usr/**', '/usr/bin/[ab]wrap',
                        '/bin/bwrap', '/**/bwrap', '/usr/bin/bw*'):
            with self.subTest(pattern=pattern), tempfile.TemporaryDirectory() as tmp:
                records = exported_blob(Path(tmp), compile_policy('profile other ' + pattern + ' {}'))
                self.assertTrue(records['other']['matches'])
                with self.assertRaisesRegex(policy.PolicyError, 'ATTACHMENT_CONFLICT'):
                    policy.check_conflicts(policy.snapshot(Path(tmp)), Path(tmp))
        for pattern in ('/opt/**', '/usr/bin/bwrap-other', '/usr/lib/{foo,bar}/**'):
            with self.subTest(pattern=pattern):
                records = policy.binary_identity(compile_policy('profile other ' + pattern + ' {}'))
                self.assertEqual(records['other']['matches'], [])

    def test_new_bwrap_unknown_is_bound_to_actual_compilation(self):
        with tempfile.TemporaryDirectory() as tmp:
            compiled = exported_blob(Path(tmp), self.approved_shape)
            self.assertEqual(compiled['bwrap']['attach'], '<unknown>')
            self.assertEqual(compiled['bwrap']['matches'], ['/usr/bin/bwrap'])
            self.assertEqual(compiled['unpriv_bwrap']['attach'], 'unpriv_bwrap')
            policy.verify_loaded(policy.snapshot(Path(tmp)), compiled)
            changed = policy.binary_identity(compile_policy('profile bwrap /usr/bin/bwrap { file, }\n'
                                                            'profile unpriv_bwrap {}'))
            with self.assertRaisesRegex(policy.PolicyError, 'LOADED_POLICY_IDENTITY'):
                policy.verify_loaded(policy.snapshot(Path(tmp)), changed)

    def test_real_parser_binary_add_submits_complete_blob_unchanged(self):
        # Explicitly redirect the parser's interface to an owned REGULAR file,
        # never securityfs. This exercises the real parser's binary/add path
        # and detects splitting or recompiling a multi-profile submission.
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / '.load'
            destination.write_bytes(b'')
            policy.run([*policy.PARSER, '--binary', '--add', '--subdomainfs', tmp],
                       data=self.approved_shape)
            self.assertEqual(destination.read_bytes(), self.approved_shape)
            self.assertFalse((Path(tmp) / '.replace').exists())

    def test_all_unknowns_reported_even_with_multiple_missing_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            exported_blob(root, self.unrelated)
            (root / 'policy/profiles/browser/raw_data').unlink()
            (root / 'policy/profiles/editor/sha256').unlink()
            with contextlib.redirect_stdout(io.StringIO()) as output:
                with self.assertRaisesRegex(policy.PolicyError, 'INVENTORY_UNRESOLVED'):
                    policy.snapshot(root)
            reports = [json.loads(line.removeprefix('N1_APPARMOR ')) for line in output.getvalue().splitlines()]
            unknowns = reports[0]['unknown_attachments']
            self.assertEqual({entry['name'] for entry in unknowns}, {'browser', 'editor'})
            self.assertTrue(all(entry['result'] != 'resolved' for entry in unknowns))

    def test_raw_blob_profile_hash_abi_and_export_mismatches_fail(self):
        for field, value in (('raw_data/one/sha256', '0' * 64), ('profiles/browser/sha256', '0' * 64),
                             ('raw_data/one/abi', '9'), ('profiles/browser/attach', '/usr/bin/bwrap'),
                             ('profiles/browser/mode', 'enforce')):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                exported_blob(root, self.unrelated)
                (root / 'policy' / field).write_text(value)
                with self.assertRaisesRegex(policy.PolicyError, 'INVENTORY_UNRESOLVED'):
                    policy.snapshot(root)

    def test_same_named_source_or_recompiled_blob_does_not_prove_loaded_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            exported_blob(root, self.unrelated)
            changed = compile_policy('profile browser /usr/bin/bwrap flags=(unconfined) {}\n'
                                     'profile editor /usr/lib/editor/** {}')
            # Even a consistent raw blob/hash cannot substitute for the loaded
            # per-profile hash. An on-disk namesake provides no authorization.
            (root / 'browser').write_text('profile browser /opt/browser/** {}')
            raw = root / 'policy/raw_data/one'
            (raw / 'raw_data').write_bytes(changed)
            (raw / 'sha256').write_text(hashlib.sha256(changed).hexdigest())
            with self.assertRaisesRegex(policy.PolicyError, 'INVENTORY_UNRESOLVED'):
                policy.snapshot(root)

    def test_complete_multi_profile_blob_and_distinct_segments(self):
        records = policy.binary_identity(self.unrelated)
        self.assertEqual(len(records), 2)
        self.assertEqual(records['browser']['raw_sha256'], records['editor']['raw_sha256'])
        self.assertNotEqual(records['browser']['sha256'], records['editor']['sha256'])
        for broken in (self.unrelated[:-1], self.unrelated + b'bad-tail', self.unrelated * 2):
            with self.subTest(size=len(broken)), self.assertRaisesRegex(policy.PolicyError, 'BINARY_EVIDENCE'):
                policy.binary_identity(broken)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            exported_blob(root, self.unrelated)
            # A retained multi-profile blob can include a no-longer-current
            # segment. Only the current kernel entry establishes that segment.
            import shutil
            shutil.rmtree(root / 'policy/profiles/editor')
            self.assertEqual(set(policy.snapshot(root)), {'browser'})

    def test_feature_and_compiler_variants_use_actual_bytes_not_portable_hashes(self):
        body = 'profile other /opt/other { /a/** r, /b/** r, /c/** r, }'
        retained = compile_policy(body, '-O', '0')
        default = compile_policy(body)
        first, second = policy.binary_identity(retained), policy.binary_identity(default)
        self.assertEqual(first['other']['attach'], '<unknown>')
        self.assertEqual(second['other']['attach'], '<unknown>')
        self.assertNotEqual(first['other']['sha256'], second['other']['sha256'])
        self.assertEqual(first['other']['matches'], second['other']['matches'])
        legacy = policy.run([*policy.PARSER, '--kernel-features', '/etc/apparmor.d/abi/3.0', '--stdout'],
                            data=b'abi <abi/3.0>,\nprofile other /opt/other {}')
        self.assertEqual(policy.binary_identity(legacy)['other']['matches'], [])

    def test_inventory_and_post_load_revision_drift_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            exported_blob(root, self.unrelated)
            with patch.object(policy, 'revision', side_effect=['1', '2']):
                with self.assertRaisesRegex(policy.PolicyError, 'CHANGED_DURING_INVENTORY'):
                    policy.snapshot(root)
            state = root / 'state'
            state.mkdir(mode=0o700)
            (state / 'identity.json').write_text(json.dumps({'source_sha256': policy.PROFILE_SHA256,
                                                            'revision': '1'}))
            with patch.object(policy, 'hosted_only'), patch.object(policy, 'revision', return_value='2'), \
                    patch.object(policy.os, 'getuid', return_value=0), \
                    patch.object(policy, 'snapshot') as inventory:
                # lstat's ownership is real; only substitute uid for this
                # unprivileged fixture, retaining its actual mode/type checks.
                original = Path.lstat
                def fixture_stat(path):
                    import os
                    values = list(original(path))
                    values[4] = 0
                    return os.stat_result(values)
                with patch.object(Path, 'lstat', fixture_stat):
                    with self.assertRaisesRegex(policy.PolicyError, 'REVISION_DRIFT'):
                        policy.verify(state)
                inventory.assert_not_called()

    def test_post_load_saved_identity_checks_unrelated_drift_and_global_controls(self):
        compiled = policy.binary_identity(self.approved_shape)
        unrelated = policy.binary_identity(self.unrelated)
        for change, error in (('none', None), ('unrelated', 'UNRELATED_POLICY_CHANGED'),
                              ('binary', 'LOADED_POLICY_IDENTITY'), ('controls', 'GLOBAL_RESTRICTIONS_CHANGED')):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as tmp:
                state = Path(tmp)
                controls = {'enabled': 'Y', 'userns': '1', 'unconfined': '0'}
                (state / 'identity.json').write_text(json.dumps({
                    'source_sha256': policy.PROFILE_SHA256, 'revision': '1', 'controls': controls,
                    'compiled': compiled, 'before': unrelated}))
                after = json.loads(json.dumps({**compiled, **unrelated}))
                actual_controls = controls.copy()
                if change == 'unrelated':
                    after['editor']['sha256'] = '0' * 64
                if change == 'binary':
                    after['bwrap']['sha256'] = '0' * 64
                if change == 'controls':
                    actual_controls['userns'] = '0'
                original = Path.lstat
                def fixture_stat(path):
                    import os
                    values = list(original(path))
                    values[4] = 0
                    return os.stat_result(values)
                with patch.object(Path, 'lstat', fixture_stat), patch.object(policy, 'hosted_only'), \
                        patch.object(policy, 'revision', return_value='1'), \
                        patch.object(policy, 'snapshot', return_value=after), \
                        patch.object(policy, 'global_restrictions', return_value=actual_controls):
                    if error:
                        with self.assertRaisesRegex(policy.PolicyError, error):
                            policy.verify(state)
                    else:
                        policy.verify(state)

    def test_unavailable_saved_identity_and_oversize_binary_are_not_permission(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(policy, 'hosted_only'):
            with self.assertRaises((OSError, policy.PolicyError)):
                policy.verify(Path(tmp) / 'absent')
        with self.assertRaisesRegex(policy.PolicyError, 'BINARY_EVIDENCE'):
            policy.binary_identity(b'x' * (policy.BINARY['MAX_BLOB'] + 1))
        # A new DFA representation is unresolved, even if its rest is actual
        # compiler output. This prevents treating unknown feature bits as zero.
        data = bytearray(self.unrelated)
        offset = data.index(bytes.fromhex('1b5e783d'))
        data[offset + 13] |= 4
        with self.assertRaisesRegex(policy.PolicyError, 'BINARY_EVIDENCE'):
            policy.binary_identity(bytes(data))


class KernelTraversalTests(unittest.TestCase):
    def test_real_namespace_magic_link_has_nonpath_text_but_kernel_identity(self):
        link = Path('/proc/self/ns/mnt')
        self.assertRegex(os.readlink(link), r'^mnt:\[\d+\]$')
        with self.assertRaises(FileNotFoundError):
            link.resolve(strict=True)
        with policy.opened(link, os.O_RDONLY, 'policy', follow=True) as fd:
            self.assertEqual(policy.object_identity(os.fstat(fd)),
                             policy.object_identity(os.stat(link)))

    def test_real_deleted_directory_magic_link_opens_without_path_resolution(self):
        # A directory analogue of AppArmor's non-path readlink text. No mount,
        # user namespace, policy activation or privilege is needed.
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / 'namespace'
            directory.mkdir()
            with policy.opened(directory, os.O_RDONLY | os.O_DIRECTORY, 'policy') as original:
                directory.rmdir()
                link = Path('/proc/self/fd') / str(original)
                self.assertTrue(os.readlink(link).endswith(' (deleted)'))
                with self.assertRaises(FileNotFoundError):
                    link.resolve(strict=True)
                with policy.opened(link, os.O_RDONLY | os.O_DIRECTORY, 'policy', follow=True) as fd:
                    self.assertEqual(policy.object_identity(os.fstat(fd)),
                                     policy.object_identity(os.fstat(original)))
                    self.assertEqual(os.listdir(fd), [])
                (Path(tmp) / 'policy').symlink_to(link)
                with patch.object(policy, 'verify_filesystem'), policy.policy_namespace(Path(tmp)) as fd:
                    self.assertEqual(policy.object_identity(os.fstat(fd)),
                                     policy.object_identity(os.fstat(original)))

    def test_filesystem_check_uses_actual_descriptor_type_and_errno(self):
        with policy.opened('/proc', os.O_RDONLY | os.O_DIRECTORY, 'security') as fd:
            policy.verify_filesystem(fd, 0x9fa0)  # PROC_SUPER_MAGIC
            with self.assertRaisesRegex(policy.PolicyError, 'FILESYSTEM_IDENTITY'):
                policy.verify_filesystem(fd, 0x5a3c69f0)
        with self.assertRaises(OSError) as failure:
            policy.verify_filesystem(-1, 0x5a3c69f0)
        self.assertEqual(failure.exception.errno, errno.EBADF)

    def test_ordinary_filesystem_cannot_impersonate_trusted_kernel_namespace(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / 'policy').mkdir()
            with self.assertRaisesRegex(policy.PolicyError, 'FILESYSTEM_IDENTITY') as failure:
                policy.snapshot(Path(tmp))
            self.assertEqual(failure.exception.diagnostic['field'], 'security')


class AccessIdentityTests(ExportFixtureTests):
    @classmethod
    def setUpClass(cls):
        cls.data = compile_policy('profile other /opt/other/** {}')

    def failure(self, root):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            with self.assertRaisesRegex(policy.PolicyError, 'INVENTORY_UNRESOLVED'):
                policy.snapshot(root)
        reports = [json.loads(line.removeprefix('N1_APPARMOR ')) for line in output.getvalue().splitlines()]
        return reports[-1]['inventory']

    def test_complete_inventory_through_real_directory_magic_link(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            expected = exported_blob(root, self.data)
            (root / 'policy').rename(root / 'namespace')
            with policy.opened(root / 'namespace', os.O_RDONLY | os.O_DIRECTORY, 'policy') as fd:
                (root / 'policy').symlink_to('/proc/self/fd/' + str(fd))
                with patch.object(Path, 'resolve', side_effect=AssertionError('pathname resolution forbidden')):
                    self.assertEqual(policy.snapshot(root), expected)
            self.assertEqual([call.args[1] for call in self.filesystem_check.call_args_list],
                             [0x73636673, 0x5a3c69f0])

    def test_link_escape_absolute_sibling_wrong_field_and_depth_rejected(self):
        for target in ('/tmp/private/raw_data', '../../../raw_data/one/raw_data',
                       '../../raw_data-other/one/raw_data', '../../raw_data/../raw_data',
                       '../../raw_data/one/sha256', '../../raw_data/one/extra/raw_data'):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                exported_blob(root, self.data)
                link = root / 'policy/profiles/other/raw_data'
                link.unlink()
                link.symlink_to(target)
                report = self.failure(root)
                self.assertEqual(report['raw_bytes'], 0)
                self.assertEqual(report['failures'][0]['operation'], 'validate_link')
                self.assertNotIn(target, json.dumps(report))

    def test_canonical_directories_and_leaves_cannot_redirect_outside_namespace(self):
        for relative in ('raw_data', 'raw_data/one', 'raw_data/one/raw_data'):
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                exported_blob(root, self.data)
                original = root / 'policy' / relative
                outside = root / 'private'
                original.rename(outside)
                original.symlink_to(outside)
                report = self.failure(root)
                self.assertEqual(report['raw_bytes'], 0)
                failure = report['failures'][0]
                self.assertEqual(failure['category'], 'traversal_failure')
                self.assertIn(failure['errno'], (errno.ELOOP, errno.ENOTDIR))
                self.assertNotIn(str(outside), json.dumps(report))

    def test_different_blob_metadata_link_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            exported_blob(root, self.data)
            link = root / 'policy/profiles/other/raw_abi'
            link.unlink()
            link.symlink_to('../../raw_data/two/abi')
            self.assertEqual(self.failure(root)['raw_bytes'], 0)

    def test_kernel_target_identity_mismatch_is_rejected_before_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            exported_blob(root, self.data)
            foreign = root / 'private'
            foreign.write_text('unrelated-secret')
            foreign_stat = foreign.stat()
            original = os.stat
            def changed(path, *args, **kwargs):
                if path == 'raw_data' and kwargs.get('dir_fd') is not None:
                    return foreign_stat
                return original(path, *args, **kwargs)
            with patch.object(policy.os, 'stat', side_effect=changed):
                report = self.failure(root)
            self.assertEqual(report['raw_bytes'], 0)
            self.assertEqual(report['failures'][0]['operation'], 'stat_identity')
            self.assertNotIn('unrelated-secret', json.dumps(report))

    def test_namespace_change_during_snapshot_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            exported_blob(root, self.data)
            replacement = root / 'replacement'
            replacement.mkdir()
            original = policy.namespace_snapshot
            def change(*args):
                result = original(*args)
                (root / 'policy').rename(root / 'old')
                replacement.rename(root / 'policy')
                return result
            with patch.object(policy, 'namespace_snapshot', side_effect=change):
                with self.assertRaisesRegex(policy.PolicyError, 'NAMESPACE_IDENTITY'):
                    policy.snapshot(root)

    def test_link_change_after_blob_read_fails_and_closes_descriptors(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            exported_blob(root, self.data)
            original = policy.read_descriptor
            def change(fd, limit, field):
                data = original(fd, limit, field)
                if field == 'raw_data':
                    link = root / 'policy/profiles/other/raw_data'
                    link.unlink()
                    link.symlink_to('../../raw_data/one/abi')
                return data
            before = len(os.listdir('/proc/self/fd'))
            with patch.object(policy, 'read_descriptor', side_effect=change):
                report = self.failure(root)
            self.assertGreater(report['raw_bytes'], 0)
            self.assertEqual(report['failures'][0]['operation'], 'stat_identity')
            self.assertEqual(len(os.listdir('/proc/self/fd')), before)

    def test_nonregular_and_oversize_evidence_fail_without_blocking(self):
        for kind in ('fifo', 'oversize'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                exported_blob(root, self.data)
                leaf = root / 'policy/raw_data/one/raw_data'
                if kind == 'fifo':
                    leaf.unlink()
                    os.mkfifo(leaf)
                else:
                    with leaf.open('wb') as output:
                        output.truncate(policy.BINARY['MAX_BLOB'] + 1)
                report = self.failure(root)
                self.assertIn('LINK_IDENTITY' if kind == 'fifo' else 'READ_LIMIT',
                              report['failures'][0]['result'])

    def test_errno_and_malformed_diagnostics_identify_operation_and_field(self):
        for number, category in ((errno.EACCES, 'denied_access'),
                                 (errno.ENOENT, 'missing_interface'),
                                 (errno.ELOOP, 'traversal_failure'), (errno.EIO, 'io_failure')):
            with self.subTest(number=number), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                exported_blob(root, self.data)
                original = os.open
                def failing(path, *args, **kwargs):
                    if path == 'abi':
                        raise OSError(number, 'private error', '/private/path')
                    return original(path, *args, **kwargs)
                with patch.object(policy.os, 'open', side_effect=failing):
                    report = self.failure(root)
                failure = report['failures'][0]
                self.assertEqual({key: failure[key] for key in ('operation', 'field', 'category', 'errno')},
                                 {'operation': 'open', 'field': 'raw_abi', 'category': category, 'errno': number})
                self.assertNotIn('private', json.dumps(report))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            exported_blob(root, self.data)
            (root / 'policy/raw_data/one/abi').write_bytes(b'private-invalid-abi')
            failure = self.failure(root)['failures'][0]
            self.assertEqual(failure['field'], 'raw_abi')
            self.assertEqual(failure['operation'], 'validate')
            self.assertEqual(failure['category'], 'malformed_evidence')
            self.assertIsNone(failure['errno'])
            self.assertNotIn('private', json.dumps(failure))


if __name__ == '__main__':
    unittest.main()
