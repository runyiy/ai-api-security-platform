"""Test-only N1 process boundary. No permissive subprocess fallback.

The controller supplies freshly copied code and a call-specific socket directory;
neither journal storage nor the control endpoint is mounted in the child.
"""
from dataclasses import dataclass
import os
from pathlib import Path
import selectors
import shutil
import subprocess
import sys
import sysconfig
import time


class IsolationUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class Sandbox:
    code: Path
    database_socket: Path
    call_socket: Path

    def command(self, entry: str):
        executable = shutil.which('bwrap')
        if executable is None:
            raise IsolationUnavailable('N1_BUBBLEWRAP_REQUIRED')
        if entry != 'child.py':
            raise IsolationUnavailable('N1_ENTRY_DENIED')
        paths = (self.code, self.database_socket, self.call_socket)
        if any(not p.is_absolute() or p.is_symlink() or not p.is_dir() for p in paths):
            raise IsolationUnavailable('N1_MOUNT_DENIED')
        if any(p == q or p in q.parents for i, p in enumerate(paths)
               for j, q in enumerate(paths) if i != j):
            raise IsolationUnavailable('N1_MOUNT_OVERLAP')
        count = total = 0
        for path in self.code.rglob('*'):
            count += 1
            if count > 512 or path.is_symlink() or path.name.startswith('.env') or path.name in ('.git', '.codex'):
                raise IsolationUnavailable('N1_CODE_PROJECTION_DENIED')
            if path.is_file():
                total += path.stat().st_size
                if total > 32 * 1024 * 1024:
                    raise IsolationUnavailable('N1_CODE_PROJECTION_DENIED')
        # The Python standard library and installed dependencies contain no task
        # configuration. Do not mount the repository, home, /tmp, /run or /etc.
        version = f'python{sys.version_info.major}.{sys.version_info.minor}'
        stdlib = Path(sysconfig.get_path('stdlib')).resolve()
        packages = Path(sysconfig.get_path('purelib')).resolve()
        command = [executable, '--unshare-user', '--unshare-pid', '--unshare-net',
                   '--unshare-ipc', '--unshare-uts', '--disable-userns',
                   '--assert-userns-disabled', '--new-session', '--die-with-parent',
                   '--cap-drop', 'ALL', '--clearenv', '--hostname', 'n1-fake',
                   '--proc', '/proc', '--dev', '/dev', '--tmpfs', '/tmp',
                   '--dir', '/runtime/bin', '--dir', '/runtime/lib',
                   '--ro-bind', str(Path(sys.executable).resolve()), '/runtime/bin/python3',
                   '--ro-bind', str(stdlib), f'/runtime/lib/{version}',
                   '--ro-bind', str(packages), '/packages',
                   '--ro-bind', str(self.code), '/code',
                   '--ro-bind', str(self.database_socket), '/database',
                   '--ro-bind', str(self.call_socket), '/call',
                   '--setenv', 'PYTHONHOME', '/runtime', '--setenv', 'LANG', 'C.UTF-8',
                   '--setenv', 'PYTHONDONTWRITEBYTECODE', '1', '--chdir', '/tmp']
        # setup-python's shared interpreter can live outside /usr. Bind its
        # declared library directory too; never inherit ambient LD_* settings.
        native = Path(sysconfig.get_config_var('LIBDIR')).resolve()
        if not native.is_dir():
            raise IsolationUnavailable('N1_PYTHON_RUNTIME_REQUIRED')
        command.extend(['--ro-bind', str(native), '/runtime/native',
                        '--setenv', 'LD_LIBRARY_PATH', '/runtime/native'])
        # Dynamic-loader dependencies only. Symlink targets are mounted at their
        # normal names, with the whole mount tree read-only.
        for library in ('/lib', '/lib64', '/usr/lib'):
            path = Path(library)
            if path.exists():
                command.extend(['--ro-bind', str(path.resolve()), library])
        command.extend(['--', '/runtime/bin/python3', '-I', '-S', '/code/child.py'])
        return command

    def run(self, *, timeout=5.0, maximum=8192):
        if type(timeout) not in (float, int) or not 0 < timeout <= 30:
            raise ValueError('N1_DEADLINE')
        if type(maximum) is not int or not 1 <= maximum <= 40960:
            raise ValueError('N1_LIMIT')
        # exec starts a fresh interpreter; no parent authority state or inherited
        # journal/control descriptors enter the child. No ambient env survives.
        process = subprocess.Popen(self.command('child.py'), stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   close_fds=True, env={'PATH': '/usr/bin:/bin'})
        output, errors = bytearray(), bytearray()
        deadline = time.monotonic() + timeout
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ, output)
                selector.register(process.stderr, selectors.EVENT_READ, errors)
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise IsolationUnavailable('N1_CHILD_TIMEOUT')
                    for key, _ in selector.select(remaining):
                        chunk = os.read(key.fd, min(4096, maximum + 1))
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        key.data.extend(chunk)
                        if len(output) + len(errors) > maximum:
                            raise IsolationUnavailable('N1_CHILD_OUTPUT_LIMIT')
            process.wait(timeout=max(.001, deadline - time.monotonic()))
            if time.monotonic() >= deadline:
                raise IsolationUnavailable('N1_CHILD_TIMEOUT')
            if process.returncode != 0:
                # Never surface raw child data, database errors or stderr.
                raise IsolationUnavailable('N1_CHILD_FAILED')
            return bytes(output)
        except subprocess.TimeoutExpired:
            raise IsolationUnavailable('N1_CHILD_TIMEOUT') from None
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=3)
            process.stdout.close()
            process.stderr.close()
