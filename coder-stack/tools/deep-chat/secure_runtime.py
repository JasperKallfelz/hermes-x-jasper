#!/usr/bin/env python3
"""Small POSIX security runtime shared by the Deep Chat bridge and worker."""

from __future__ import annotations

from contextlib import contextmanager
import ctypes
from dataclasses import dataclass
import errno
import fcntl
import json
import math
import os
from pathlib import Path
import secrets
import select
import signal
import stat
import subprocess
import sys
import threading
import time
from typing import Any, Dict, Iterator, Mapping, Optional, Sequence, Tuple


if sys.version_info < (3, 11):  # Must run before filesystem or subprocess work.
    raise SystemExit("Deep Chat requires Python 3.11 or newer")


EXIT_USAGE = 64
EXIT_HARNESS = 70
EXIT_UNAVAILABLE = 75
EXIT_TIMEOUT = 124
MAX_CAPTURE = 4 * 1024 * 1024
LAUNCH_GRACE_SECONDS = 5.0
CLEANUP_RESERVE_SECONDS = 1.0
READY = b"R"
ACK = b"G"
CLEAN = b"C"


class SecurityError(RuntimeError):
    def __init__(self, reason_id: str, message: str, exit_code: int = EXIT_HARNESS):
        super().__init__(message)
        self.reason_id = reason_id
        self.message = message
        self.exit_code = exit_code


@dataclass
class ProcessResult:
    returncode: int
    stdout: bytes
    stderr: bytes
    launched: bool
    timed_out: bool
    interrupted: bool
    signal_number: Optional[int]
    cleanup_verified: bool
    output_complete: bool
    duration_seconds: float


_DarwinProcPidInfo = None
_darwin_unavailable = False


class _DarwinProcBSDInfo(ctypes.Structure):
    _fields_ = [
        ("pbi_flags", ctypes.c_uint32),
        ("pbi_status", ctypes.c_uint32),
        ("pbi_xstatus", ctypes.c_uint32),
        ("pbi_pid", ctypes.c_uint32),
        ("pbi_ppid", ctypes.c_uint32),
        ("pbi_uid", ctypes.c_uint32),
        ("pbi_gid", ctypes.c_uint32),
        ("pbi_ruid", ctypes.c_uint32),
        ("pbi_rgid", ctypes.c_uint32),
        ("pbi_svuid", ctypes.c_uint32),
        ("pbi_svgid", ctypes.c_uint32),
        ("rfu_1", ctypes.c_uint32),
        ("pbi_comm", ctypes.c_char * 16),
        ("pbi_name", ctypes.c_char * 32),
        ("pbi_nfiles", ctypes.c_uint32),
        ("pbi_pgid", ctypes.c_uint32),
        ("pbi_pjobc", ctypes.c_uint32),
        ("e_tdev", ctypes.c_uint32),
        ("e_tpgid", ctypes.c_uint32),
        ("pbi_nice", ctypes.c_int32),
        ("pbi_start_tvsec", ctypes.c_uint64),
        ("pbi_start_tvusec", ctypes.c_uint64),
    ]


def _darwin_proc_function() -> Any:
    global _DarwinProcPidInfo, _darwin_unavailable
    if sys.platform != "darwin" or _darwin_unavailable:
        return None
    if _DarwinProcPidInfo is not None:
        return _DarwinProcPidInfo
    try:
        library = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        function = library.proc_pidinfo
        function.argtypes = (
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_uint64,
            ctypes.c_void_p,
            ctypes.c_int,
        )
        function.restype = ctypes.c_int
    except (AttributeError, OSError):
        _darwin_unavailable = True
        return None
    _DarwinProcPidInfo = function
    return function


def process_identity(pid: int) -> Optional[str]:
    if os.name != "posix" or pid <= 0:
        return None
    if sys.platform.startswith("linux"):
        try:
            raw = Path("/proc/{}/stat".format(pid)).read_bytes()[: 16 * 1024]
        except OSError:
            return None
        fields = raw.rsplit(b")", 1)[-1].split()
        if len(fields) < 20 or not fields[19].isdigit():
            return None
        return "linux:" + fields[19].decode("ascii")
    function = _darwin_proc_function()
    if function is not None:
        info = _DarwinProcBSDInfo()
        size = function(pid, 3, 0, ctypes.byref(info), ctypes.sizeof(info))
        if size < ctypes.sizeof(info) or info.pbi_pid != pid:
            return None
        return "darwin:{}:{}".format(info.pbi_start_tvsec, info.pbi_start_tvusec)
    return None


SUPERVISOR_SOURCE = r'''import errno,os,select,signal,sys,time
FAIL=70
READY=b"R"; ACK=b"G"; CLEAN=b"C"
def clock(): return time.clock_gettime(time.CLOCK_MONOTONIC)
def send(fd,value,deadline):
 try:
  left=deadline-clock()
  if left>0 and select.select([],[fd],[],left)[1]: return os.write(fd,value)==len(value)
 except OSError: pass
 return False
def stop():
 try: os.killpg(os.getpgrp(),signal.SIGKILL)
 except OSError: pass
 os._exit(FAIL)
def main(a):
 if os.name!="posix" or len(a)<5: return FAIL
 try: control=int(a[0]); status=int(a[1]); launch=float(a[2]); hard=float(a[3])
 except (ValueError,OverflowError): return FAIL
 command=a[4:]
 if control<=2 or status<=2 or control==status or not command or hard<launch or launch<=clock(): return FAIL
 try:
  if os.getsid(0)!=os.getpid() or os.getpgrp()!=os.getpid(): return FAIL
  os.set_inheritable(control,False); os.set_inheritable(status,False)
  for sig in (signal.SIGHUP,signal.SIGINT,signal.SIGTERM): signal.signal(sig,signal.SIG_IGN)
  if not send(status,READY,launch): return FAIL
  left=launch-clock()
  if left<=0 or not select.select([control],[],[],left)[0] or os.read(control,2)!=ACK: return FAIL
  child=os.fork()
  if child==0:
   try:
    os.close(control)
    for sig in (signal.SIGHUP,signal.SIGINT,signal.SIGTERM): signal.signal(sig,signal.SIG_DFL)
    os.execvpe(command[0],command,os.environ)
   except OSError as error:
    send(status,b"E %d\n"%(error.errno or errno.EIO),hard)
    os._exit(127 if error.errno==errno.ENOENT else 126)
  while True:
   try: waited,value=os.waitpid(child,os.WNOHANG)
   except InterruptedError: waited=0
   except ChildProcessError: stop()
   if waited==child:
    rc=os.WEXITSTATUS(value) if os.WIFEXITED(value) else -os.WTERMSIG(value) if os.WIFSIGNALED(value) else FAIL
    if not send(status,b"D %d\n"%rc,hard): stop()
    break
   left=hard-clock()
   if left<=0: stop()
   if select.select([control],[],[],min(.05,left))[0]: os.read(control,2); stop()
  left=hard-clock()
  if left<=0 or not select.select([control],[],[],left)[0]: stop()
  if os.read(control,2)==CLEAN: stop()
  stop()
 except OSError: stop()
if __name__=="__main__": raise SystemExit(main(sys.argv[1:]))
'''


def _group_exists(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _wait_group(proc: subprocess.Popen[bytes], deadline: float) -> bool:
    while _group_exists(proc.pid):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        try:
            proc.wait(timeout=min(0.01, remaining))
        except subprocess.TimeoutExpired:
            pass
    return True


def _signal_group(
    proc: subprocess.Popen[bytes], identity: str, signum: int
) -> bool:
    if process_identity(proc.pid) != identity:
        return not _group_exists(proc.pid)
    try:
        os.killpg(proc.pid, signum)
    except ProcessLookupError:
        return True
    except OSError:
        return False
    return True


def _stop_group(
    proc: subprocess.Popen[bytes], identity: str, deadline: float, first: int
) -> bool:
    if not _group_exists(proc.pid):
        return True
    if not _signal_group(proc, identity, first):
        return not _group_exists(proc.pid)
    remaining = max(0.0, deadline - time.monotonic())
    polite_deadline = time.monotonic() + min(0.25, remaining / 2.0)
    if _wait_group(proc, polite_deadline):
        return True
    if process_identity(proc.pid) != identity:
        return False
    if not _signal_group(proc, identity, signal.SIGKILL):
        return not _group_exists(proc.pid)
    return _wait_group(proc, deadline)


def _clock() -> float:
    value = time.clock_gettime(time.CLOCK_MONOTONIC)
    if not math.isfinite(value):
        raise SecurityError("monotonic_clock_invalid", "monotonic clock is invalid")
    return value


def minimal_environment(
    *,
    home: Path,
    additions: Optional[Mapping[str, str]] = None,
) -> Dict[str, str]:
    """Allow only ordinary locale/path state and explicitly supplied values."""
    allow = {"PATH", "TMPDIR", "LANG", "USER", "LOGNAME", "SYSTEMROOT", "TERM"}
    result = {
        key: value
        for key, value in os.environ.items()
        if key in allow or key.startswith("LC_")
    }
    result["HOME"] = str(home)
    result["LLVM_PROFILE_FILE"] = os.devnull
    if additions:
        for key, value in additions.items():
            if "\0" in key or "\0" in value:
                raise SecurityError("child_environment_invalid", "child environment contains NUL")
            result[key] = value
    return result


def resolve_executable(value: str, *, path_env: Optional[str] = None) -> Path:
    if not value:
        raise SecurityError("executable_missing", "executable is not configured")
    if os.path.isabs(value):
        candidate = Path(value)
    else:
        import shutil

        located = shutil.which(value, path=path_env)
        if located is None:
            raise SecurityError("executable_missing", "executable was not found")
        candidate = Path(located)
    try:
        resolved = candidate.resolve(strict=True)
        info = resolved.stat()
    except (OSError, RuntimeError):
        raise SecurityError("executable_missing", "executable is unavailable")
    if not stat.S_ISREG(info.st_mode) or not os.access(resolved, os.X_OK):
        raise SecurityError("executable_unsafe", "executable is not a regular executable")
    return resolved


def run_bounded(
    command: Sequence[str],
    *,
    cwd: Path,
    environment: Mapping[str, str],
    deadline: float,
    capture_limit: int = MAX_CAPTURE,
    signal_state: Optional["SignalState"] = None,
) -> ProcessResult:
    """Run one target under a persistent, identity-bound process-group owner."""
    started = time.monotonic()
    if os.name != "posix":
        raise SecurityError("process_supervision_unsupported", "Deep Chat supervision requires POSIX")
    if process_identity(os.getpid()) is None:
        raise SecurityError("process_identity_unavailable", "stable process identity is unavailable")
    if not command or not os.path.isabs(command[0]):
        raise SecurityError("executable_path_unsafe", "supervised executable path must be absolute")
    remaining_total = deadline - started
    if remaining_total <= 0:
        return ProcessResult(
            EXIT_TIMEOUT, b"", b"", False, True, False, None, True, True, 0.0
        )
    reserve = min(CLEANUP_RESERVE_SECONDS, max(0.05, remaining_total / 4.0))
    execution_deadline = max(started, deadline - reserve)
    # ``deadline`` and ``execution_deadline`` belong to the parent clock
    # domain. Sample both domains together and carry only remaining durations
    # across the exec boundary; a parent absolute is meaningless to the
    # isolated supervisor on platforms with process-local monotonic epochs.
    parent_clock_sample = time.monotonic()
    supervisor_clock_sample = _clock()
    hard_remaining = deadline - parent_clock_sample
    launch_remaining = min(
        LAUNCH_GRACE_SECONDS,
        execution_deadline - parent_clock_sample,
    )
    if hard_remaining <= 0 or launch_remaining <= 0:
        return ProcessResult(
            EXIT_TIMEOUT,
            b"",
            b"",
            False,
            True,
            False,
            None,
            True,
            True,
            time.monotonic() - started,
        )
    supervisor_launch_deadline = supervisor_clock_sample + launch_remaining
    supervisor_hard_deadline = supervisor_clock_sample + hard_remaining
    control_read, control_write = os.pipe()
    status_read, status_write = os.pipe()
    supervisor = [
        sys.executable,
        "-I",
        "-S",
        "-B",
        "-c",
        SUPERVISOR_SOURCE,
        str(control_read),
        str(status_write),
        repr(supervisor_launch_deadline),
        repr(supervisor_hard_deadline),
        *command,
    ]
    try:
        proc = subprocess.Popen(
            supervisor,
            cwd=str(cwd),
            env=dict(environment),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            pass_fds=(control_read, status_write),
        )
    except OSError as exc:
        for fd in (control_read, control_write, status_read, status_write):
            try:
                os.close(fd)
            except OSError:
                pass
        raise SecurityError("process_launch_failed", "cannot launch process supervisor") from exc
    os.close(control_read)
    os.close(status_write)
    identity = process_identity(proc.pid)
    if identity is None:
        for fd in (control_write, status_read):
            os.close(fd)
        try:
            proc.kill()
            proc.wait(timeout=max(0.0, deadline - time.monotonic()))
        except (OSError, subprocess.TimeoutExpired):
            pass
        raise SecurityError("process_identity_unavailable", "cannot bind process identity")
    launched = False
    try:
        left = supervisor_launch_deadline - _clock()
        readable, _, _ = select.select([status_read], [], [], max(0.0, left))
        if not readable or os.read(status_read, 2) != READY:
            raise SecurityError("process_handshake_failed", "supervisor readiness failed")
        left = supervisor_launch_deadline - _clock()
        _, writable, _ = select.select([], [control_write], [], max(0.0, left))
        if not writable or os.write(control_write, ACK) != 1:
            raise SecurityError("process_handshake_failed", "supervisor acknowledgement failed")
        launched = True
    except BaseException:
        _stop_group(proc, identity, deadline, signal.SIGKILL)
        os.close(control_write)
        os.close(status_read)
        raise

    stdout = bytearray()
    stderr = bytearray()
    output_complete = True
    target_rc = None  # type: Optional[int]
    exec_errno = None  # type: Optional[int]
    status_buffer = bytearray()
    assert proc.stdout is not None and proc.stderr is not None
    for pipe in (proc.stdout, proc.stderr):
        os.set_blocking(pipe.fileno(), False)
    os.set_blocking(status_read, False)
    streams = {proc.stdout.fileno(): stdout, proc.stderr.fileno(): stderr}
    open_streams = set(streams)
    timed_out = False
    interrupted = False
    interrupted_signal = None  # type: Optional[int]

    def consume(fd: int) -> None:
        nonlocal output_complete
        destination = streams[fd]
        try:
            chunk = os.read(fd, 64 * 1024)
        except BlockingIOError:
            return
        except OSError:
            open_streams.discard(fd)
            output_complete = False
            return
        if not chunk:
            open_streams.discard(fd)
            return
        available = capture_limit - len(destination)
        if len(chunk) > available:
            if available > 0:
                destination.extend(chunk[:available])
            output_complete = False
        elif output_complete:
            destination.extend(chunk)

    while target_rc is None:
        if signal_state is not None and signal_state.signum is not None:
            interrupted = True
            interrupted_signal = signal_state.signum
            break
        remaining = execution_deadline - time.monotonic()
        if remaining <= 0:
            timed_out = True
            break
        watched = [status_read] + list(open_streams)
        try:
            readable, _, _ = select.select(watched, [], [], min(0.05, remaining))
        except InterruptedError:
            continue
        for fd in readable:
            if fd in streams:
                consume(fd)
                continue
            try:
                chunk = os.read(status_read, 128)
            except BlockingIOError:
                continue
            if not chunk:
                output_complete = False
                target_rc = EXIT_HARNESS
                break
            status_buffer.extend(chunk)
            while b"\n" in status_buffer:
                line, _sep, rest = bytes(status_buffer).partition(b"\n")
                status_buffer[:] = rest
                fields = line.split()
                if len(fields) != 2:
                    output_complete = False
                    target_rc = EXIT_HARNESS
                    break
                try:
                    value = int(fields[1])
                except ValueError:
                    output_complete = False
                    target_rc = EXIT_HARNESS
                    break
                if fields[0] == b"E":
                    exec_errno = value
                elif fields[0] == b"D":
                    target_rc = value
                else:
                    output_complete = False
                    target_rc = EXIT_HARNESS
                if target_rc is not None:
                    break

    if target_rc is not None and not timed_out and not interrupted:
        try:
            _, writable, _ = select.select(
                [], [control_write], [], max(0.0, deadline - time.monotonic())
            )
            acknowledged = bool(writable and os.write(control_write, CLEAN) == 1)
        except OSError:
            acknowledged = False
        cleaned = acknowledged and _wait_group(proc, deadline)
        if not cleaned:
            cleaned = _stop_group(proc, identity, deadline, signal.SIGKILL)
    else:
        first = interrupted_signal or signal.SIGTERM
        cleaned = _stop_group(proc, identity, deadline, first)
    for fd in list(open_streams):
        consume(fd)
    for pipe in (proc.stdout, proc.stderr):
        try:
            pipe.close()
        except OSError:
            pass
    for fd in (control_write, status_read):
        try:
            os.close(fd)
        except OSError:
            pass
    if exec_errno == errno.ENOENT:
        target_rc = 127
    if interrupted:
        returncode = 128 + (interrupted_signal or signal.SIGTERM)
    elif timed_out:
        returncode = EXIT_TIMEOUT
    elif target_rc is None:
        returncode = EXIT_HARNESS
    elif target_rc < 0:
        returncode = 128 + (-target_rc)
    else:
        returncode = target_rc
    return ProcessResult(
        returncode,
        bytes(stdout),
        bytes(stderr),
        launched,
        timed_out,
        interrupted,
        interrupted_signal,
        cleaned,
        output_complete,
        time.monotonic() - started,
    )


class SignalState:
    def __init__(self) -> None:
        self.signum = None  # type: Optional[int]
        self._previous = {}  # type: Dict[int, Any]

    def _receive(self, signum: int, _frame: Any) -> None:
        if self.signum is None:
            self.signum = signum

    def install(self) -> None:
        for signum in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
            if signum in self._previous:
                continue
            self._previous[signum] = signal.getsignal(signum)
            signal.signal(signum, self._receive)

    def restore(self) -> None:
        for signum, handler in self._previous.items():
            signal.signal(signum, handler)
        self._previous.clear()


def canonical_roots(environment: Mapping[str, str]) -> Tuple[Path, Path]:
    raw_home = environment.get("HOME", "")
    if not raw_home or not os.path.isabs(raw_home):
        raise SecurityError("home_path_unsafe", "HOME must be a non-empty absolute path", EXIT_USAGE)
    try:
        home = Path(raw_home).resolve(strict=True)
    except (OSError, RuntimeError):
        raise SecurityError("home_path_unsafe", "HOME cannot be resolved", EXIT_USAGE)
    raw_hermes = environment.get("HERMES_HOME")
    if raw_hermes is not None:
        if not raw_hermes or not os.path.isabs(raw_hermes):
            raise SecurityError("hermes_home_unsafe", "HERMES_HOME must be absolute", EXIT_USAGE)
        try:
            hermes = Path(raw_hermes).resolve(strict=False)
        except (OSError, RuntimeError):
            raise SecurityError("hermes_home_unsafe", "HERMES_HOME cannot be resolved", EXIT_USAGE)
    else:
        hermes = home / ".hermes"
    return home, hermes


def _owner_ok(info: os.stat_result) -> bool:
    return not hasattr(os, "geteuid") or info.st_uid == os.geteuid()


def ensure_private_directory(path: Path) -> None:
    """Create a directory chain without accepting symlink components."""
    absolute = Path(os.path.abspath(path))
    missing = []
    current = absolute
    while not current.exists():
        missing.append(current)
        parent = current.parent
        if parent == current:
            raise SecurityError("directory_unsafe", "cannot find an existing directory ancestor")
        current = parent
    try:
        anchor = os.lstat(current)
    except OSError as exc:
        raise SecurityError("directory_unsafe", "directory ancestor is unavailable") from exc
    if not stat.S_ISDIR(anchor.st_mode) or stat.S_ISLNK(anchor.st_mode):
        raise SecurityError("directory_unsafe", "directory ancestor is not physical")
    for component in reversed(missing):
        try:
            os.mkdir(component, 0o700)
        except FileExistsError:
            pass
        info = os.lstat(component)
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or not _owner_ok(info):
            raise SecurityError("directory_unsafe", "private directory component is unsafe")
        os.chmod(component, 0o700)
    info = os.lstat(absolute)
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or not _owner_ok(info):
        raise SecurityError("directory_unsafe", "private directory is unsafe")
    os.chmod(absolute, 0o700)


def validate_absolute_override(value: Optional[str], default: Path, label: str) -> Path:
    if value is None:
        candidate = default
    else:
        if not value or not os.path.isabs(value):
            raise SecurityError(
                "{}_path_unsafe".format(label),
                "{} override must be a non-empty absolute path".format(label),
                EXIT_USAGE,
            )
        candidate = Path(value)
    return Path(os.path.abspath(candidate))


def _secure_file_info(path: Path) -> os.stat_result:
    try:
        info = os.lstat(path)
    except OSError as exc:
        raise SecurityError("persistence_unreadable", "private file is unavailable") from exc
    if (
        not stat.S_ISREG(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or not _owner_ok(info)
        or info.st_mode & 0o077
    ):
        raise SecurityError("persistence_unsafe", "private file ownership or mode is unsafe")
    return info


def read_secure_bytes(path: Path, limit: int) -> bytes:
    before = _secure_file_info(path)
    if before.st_size > limit:
        raise SecurityError("persistence_too_large", "private file exceeds its size limit")
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NONBLOCK
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags)
    try:
        after = os.fstat(fd)
        if (
            not stat.S_ISREG(after.st_mode)
            or not _owner_ok(after)
            or after.st_mode & 0o077
            or (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino)
        ):
            raise SecurityError("persistence_unsafe", "private file changed during open")
        payload = os.read(fd, limit + 1)
    finally:
        os.close(fd)
    if len(payload) > limit:
        raise SecurityError("persistence_too_large", "private file exceeds its size limit")
    return payload


def read_secure_json(path: Path, limit: int = 4 * 1024 * 1024) -> Any:
    try:
        return json.loads(read_secure_bytes(path, limit).decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise SecurityError("persistence_invalid", "private JSON file is invalid") from exc


def atomic_write_json(path: Path, value: Any) -> None:
    ensure_private_directory(path.parent)
    if path.exists() or path.is_symlink():
        _secure_file_info(path)
    payload = (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    temporary = path.parent / (".{}.{}.tmp".format(path.name, secrets.token_hex(16)))
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(temporary, flags, 0o600)
    try:
        offset = 0
        while offset < len(payload):
            written = os.write(fd, payload[offset:])
            if written <= 0:
                raise OSError(errno.EIO, "short atomic write")
            offset += written
        os.fsync(fd)
        os.fchmod(fd, 0o600)
    finally:
        os.close(fd)
    try:
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_CLOEXEC)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


@contextmanager
def descriptor_lock(path: Path, deadline: float) -> Iterator[int]:
    ensure_private_directory(path.parent)
    if path.exists() or path.is_symlink():
        _secure_file_info(path)
    flags = os.O_RDWR | os.O_CREAT | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or not _owner_ok(info):
            raise SecurityError("lock_unsafe", "lock file is unsafe")
        os.fchmod(fd, 0o600)
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise SecurityError("lock_timeout", "lock acquisition timed out", EXIT_TIMEOUT)
                time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
        yield fd
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(fd)


def lock_is_held(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        _secure_file_info(path)
        fd = os.open(path, os.O_RDWR | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0))
    except (OSError, SecurityError):
        return True
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)
