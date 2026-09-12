"""CI policy guard tests. All kernel policy operations are mocked, never loaded."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import n1_apparmor_policy as policy


class PolicyTests(unittest.TestCase):
    def test_attachment_conflicts_include_aliases_braces_globs_and_unknowns(self):
        with tempfile.TemporaryDirectory() as root:
            for attachment in ('/usr/bin/bwrap', '/bin/bwrap', '/{usr/,}bin/bwrap',
                               '/usr/bin/*', '/usr/**/bwrap', '/usr/bin/[ab]wrap',
                               '<unknown>', '@{bin}/bwrap'):
                with self.subTest(attachment=attachment), self.assertRaisesRegex(
                        policy.PolicyError, 'ATTACHMENT_CONFLICT'):
                    policy.check_conflicts({'unrelated-name': {'attach': attachment}}, Path(root))

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
            with self.assertRaisesRegex(policy.PolicyError, 'INVENTORY_UNAVAILABLE'):
                policy.snapshot(Path(root))

    def test_snapshot_keeps_nested_profile_names_distinct(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root) / 'policy/profiles'
            for parent in ('first', 'second'):
                for directory, name, attach in ((base / parent, parent, '/' + parent),
                                               (base / parent / 'profiles/child', 'child', 'child')):
                    directory.mkdir(parents=True)
                    for field, value in {'name': name, 'attach': attach, 'mode': 'enforce'}.items():
                        (directory / field).write_text(value)
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
        after = {name: {'attach': attach, 'mode': 'enforce'} for name, attach in policy.EXPECTED.items()}
        with patch.object(policy, 'hosted_only'), patch.object(policy, 'profile_bytes', return_value=b'approved'), \
                patch.object(policy, 'global_restrictions', return_value={'enabled': 'Y'}), \
                patch.object(policy, 'snapshot', side_effect=[{}, {}, after]), \
                patch.object(policy, 'check_conflicts'), patch.object(policy, 'run') as command:
            policy.load('synthetic')
            self.assertEqual([call.args[0][-1] for call in command.call_args_list],
                             ['--skip-kernel-load', '--add'])
            self.assertTrue(all(call.kwargs['data'] == b'approved' for call in command.call_args_list))

    def test_conflict_or_compile_failure_never_reaches_kernel_load(self):
        for conflict in (True, False):
            with self.subTest(conflict=conflict), patch.object(policy, 'hosted_only'), \
                    patch.object(policy, 'profile_bytes', return_value=b'approved'), \
                    patch.object(policy, 'global_restrictions'), patch.object(policy, 'snapshot', return_value={}), \
                    patch.object(policy, 'check_conflicts') as check, patch.object(policy, 'run') as command:
                if conflict:
                    check.side_effect = policy.PolicyError('N1_APPARMOR_PROFILE_NAME_CONFLICT')
                else:
                    command.side_effect = policy.PolicyError('N1_APPARMOR_COMMAND_FAILED')
                with self.assertRaises(policy.PolicyError):
                    policy.load('synthetic')
                self.assertFalse(any('--add' in call.args[0] for call in command.call_args_list))

    def test_missing_or_complain_child_profile_fails(self):
        for child in (None, {'attach': 'unpriv_bwrap', 'mode': 'complain'}):
            inventory = {'bwrap': {'attach': '/usr/bin/bwrap', 'mode': 'enforce'}}
            if child:
                inventory['unpriv_bwrap'] = child
            with self.assertRaisesRegex(policy.PolicyError, 'LOADED_POLICY_IDENTITY'):
                policy.verify_loaded(inventory)

    def test_command_timeout_is_bounded_and_reported(self):
        with self.assertRaisesRegex(policy.PolicyError, 'COMMAND_TIMEOUT'):
            policy.run(['/usr/bin/python3', '-I', '-c', 'import time; time.sleep(30)'], timeout=.05)


if __name__ == '__main__':
    unittest.main()
