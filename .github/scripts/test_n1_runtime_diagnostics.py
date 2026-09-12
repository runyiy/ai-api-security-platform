"""Standard-library diagnostics checks; no application or database imports."""
import json
import os
from pathlib import Path
import select
import sys
import tempfile
import time
import unittest

import n1_runtime_diagnostics as diagnostics


class DiagnosticsTests(unittest.TestCase):
    def test_only_bwrap_security_fields_are_retained(self):
        def event(message):
            return json.dumps({'MESSAGE': message, '__REALTIME_TIMESTAMP': '123456',
                               '_CMDLINE': 'synthetic-private-command'})

        output = '\n'.join([
            'invalid JSON', '[]', event('apparmor="DENIED" comm="unrelated" pid=2'),
            event('comm="bwrap" ordinary="synthetic-private-data"'),
            event('apparmor="AUDIT" operation="userns_create" profile="unconfined" '
                  'comm="bwrap" target="unprivileged_userns" pid=7'),
            event('apparmor="DENIED" operation="capable" profile="unprivileged_userns" '
                  'pid=7 comm="bwrap" capability=12 capname="net_admin" '
                  'name="/synthetic/private/file" info="synthetic-private-data"'),
            event('type=SECCOMP comm="bwrap" pid=7 syscall=44 code=0x50001 '
                  'exe="/synthetic/private/executable"'),
        ])
        records = diagnostics.audit_records(output)
        self.assertEqual(len(records), 3)
        self.assertEqual(records[0]['target'], 'unprivileged_userns')
        self.assertEqual(records[1]['capname'], 'net_admin')
        self.assertEqual(records[2]['syscall'], '44')
        self.assertNotIn('synthetic', json.dumps(records))

    def test_command_errors_do_not_expose_stderr(self):
        state, output = diagnostics.command([
            sys.executable, '-I', '-c',
            'import sys; print("synthetic-secret", file=sys.stderr); sys.exit(9)'])
        self.assertEqual((state, output), ('exit=9', ''))

    def test_output_limit_discards_partial_output(self):
        state, output = diagnostics.command([
            sys.executable, '-I', '-c', 'print("synthetic-secret" * 10000)'])
        self.assertEqual((state, output), ('output_limit', ''))

    def test_timeout_kills_owned_descendant_with_inherited_pipe(self):
        with tempfile.TemporaryDirectory(prefix='n1-diagnostic-test-') as directory:
            pid_file = Path(directory) / 'pid'
            script = ('import os, pathlib, time\n'
                      'pid = os.fork()\n'
                      'if pid:\n'
                      ' pathlib.Path(' + repr(str(pid_file)) + ').write_text(str(pid))\n'
                      ' os._exit(0)\n'
                      'time.sleep(30)\n')
            started = time.monotonic()
            state, output = diagnostics.command([sys.executable, '-I', '-c', script])
            self.assertEqual((state, output), ('timeout', ''))
            self.assertLess(time.monotonic() - started, 5)
            # Signal delivery is asynchronous; wait on the owned process identity.
            try:
                descriptor = os.pidfd_open(int(pid_file.read_text()))
            except ProcessLookupError:
                return
            try:
                self.assertEqual(select.select([descriptor], [], [], 2)[0], [descriptor])
            finally:
                os.close(descriptor)


if __name__ == '__main__':
    unittest.main()
