#!/usr/bin/env python3
"""Offline mutation harness for version-attestation lifecycle primitives.

This module never invokes agy, reads compatibility evidence, or accesses a network.
It exercises private-file publication and bounded process-group cleanup against a
fixed synthetic Python child.  Mutation policies are Python-callable test fixtures;
they are not accepted from the command line or environment.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import selectors
import signal
import stat
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Sequence


sys.dont_write_bytecode = True

LIFECYCLE_SIGNALS = (signal.SIGHUP, signal.SIGINT, signal.SIGTERM)
TERM_GRACE_SECONDS = 0.20
KILL_GRACE_SECONDS = 0.75
HANDSHAKE_SECONDS = 1.50
HANDSHAKE_LIMIT = 64


class HarnessError(ValueError):
    """A fail-closed offline harness condition was not satisfied."""


class HarnessInterrupted(BaseException):
    """A lifecycle signal interrupted a controlled operation."""

    def __init__(self, signum: int):
        super().__init__(signum)
        self.signum = signum


@dataclass(frozen=True)
class MutationPolicy:
    """Fixed, copy-based test mutation points; never user-configurable."""

    block_spawn_signals: bool = True
    block_cleanup_signals: bool = True
    block_completion_signals: bool = True
    local_publication_rollback: bool = True
    register_before_parent_fsync: bool = True
    fsync_temp_cleanup: bool = True
    rollback_completion_exceptions: bool = True


SECURE_POLICY = MutationPolicy()


@dataclass(frozen=True)
class FsyncMutation:
    """One fixed test-only omission of an actual fsync syscall."""

    role: str
    ordinal: int


def _require_signal_primitives() -> None:
    required = ("pthread_sigmask", "sigpending", "sigwait")
    if not all(hasattr(signal, name) for name in required):
        raise HarnessError("required lifecycle signal primitives are unavailable")


def _mode(value: os.stat_result) -> int:
    return stat.S_IMODE(value.st_mode)


def _write_all(descriptor: int, data: bytes) -> None:
    remaining = memoryview(data)
    while remaining:
        written = os.write(descriptor, remaining)
        if written <= 0:
            raise HarnessError("private artifact write failed")
        remaining = remaining[written:]


def _group_exists(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _wait_group_absent(pgid: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _group_exists(pgid):
            return True
        time.sleep(0.01)
    return not _group_exists(pgid)


def _exact_unlink(parent_fd: int, name: str, inode: int) -> bool:
    try:
        current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return True
    if (
        stat.S_ISREG(current.st_mode)
        and current.st_uid == os.getuid()
        and current.st_ino == inode
    ):
        os.unlink(name, dir_fd=parent_fd)
        return True
    return False


class PublicationOps:
    """Real operations plus fixed one-shot faults used only by the harness."""

    def __init__(self, *, fault: Optional[str] = None) -> None:
        self.fault = fault
        self.linked = False
        self.fired = False
        self.cleanup_fsyncs = 0

    def link(self, source: str, target: str, parent_fd: int) -> None:
        os.link(
            source,
            target,
            src_dir_fd=parent_fd,
            dst_dir_fd=parent_fd,
            follow_symlinks=False,
        )
        self.linked = True

    def stat(self, name: str, parent_fd: int) -> os.stat_result:
        if self.fault == "post-link-stat" and self.linked and not self.fired:
            self.fired = True
            raise OSError("injected post-link stat failure")
        return os.stat(name, dir_fd=parent_fd, follow_symlinks=False)

    def fsync_parent(self, parent_fd: int) -> None:
        if self.fault == "parent-fsync" and self.linked and not self.fired:
            self.fired = True
            raise OSError("injected parent fsync failure")
        os.fsync(parent_fd)

    def fsync_cleanup(self, parent_fd: int) -> None:
        self.cleanup_fsyncs += 1
        os.fsync(parent_fd)


class DurablePublisher:
    """Publish owner-private bytes without overwrite and with inode rollback."""

    def __init__(self, parent: Path) -> None:
        self.parent = parent.resolve(strict=True)
        parent_stat = self.parent.stat()
        if (
            not self.parent.is_dir()
            or parent_stat.st_uid != os.getuid()
            or _mode(parent_stat) != 0o700
        ):
            raise HarnessError("publication parent is not owner-private")
        self.parent_fd = os.open(
            self.parent,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        self.owned: dict[str, int] = {}

    def close(self) -> None:
        os.close(self.parent_fd)

    def rollback(
        self,
        *,
        before_unlink: Optional[Callable[[], None]] = None,
        before_fsync: Optional[Callable[[], None]] = None,
    ) -> None:
        for name, inode in tuple(self.owned.items()):
            if before_unlink is not None:
                before_unlink()
            if _exact_unlink(self.parent_fd, name, inode):
                self.owned.pop(name, None)
        if before_fsync is not None:
            before_fsync()
        os.fsync(self.parent_fd)

    def publish(
        self,
        name: str,
        data: bytes,
        *,
        ops: Optional[PublicationOps] = None,
        policy: MutationPolicy = SECURE_POLICY,
    ) -> str:
        if not name or "/" in name or name in (".", ".."):
            raise HarnessError("invalid publication name")
        operations = PublicationOps() if ops is None else ops
        temporary = f".{name}.{os.getpid()}.{time.monotonic_ns()}.tmp"
        descriptor = os.open(
            temporary,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=self.parent_fd,
        )
        inode: Optional[int] = None
        linked = False
        try:
            os.fchmod(descriptor, 0o600)
            _write_all(descriptor, data)
            os.fsync(descriptor)
            staged = os.fstat(descriptor)
            inode = staged.st_ino
            if staged.st_uid != os.getuid() or _mode(staged) != 0o600:
                raise HarnessError("staged artifact is not owner-private")
            os.close(descriptor)
            descriptor = -1
            try:
                os.stat(name, dir_fd=self.parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise HarnessError("publication target already exists")
            operations.link(temporary, name, self.parent_fd)
            linked = True
            published = operations.stat(name, self.parent_fd)
            if (
                published.st_ino != inode
                or published.st_uid != os.getuid()
                or _mode(published) != 0o600
            ):
                raise HarnessError("published inode identity changed")
            if policy.register_before_parent_fsync:
                self.owned[name] = inode
            operations.fsync_parent(self.parent_fd)
            if not policy.register_before_parent_fsync:
                self.owned[name] = inode
            os.unlink(temporary, dir_fd=self.parent_fd)
            os.fsync(self.parent_fd)
            return hashlib.sha256(data).hexdigest()
        except BaseException:
            if descriptor >= 0:
                os.close(descriptor)
            if linked and inode is not None and policy.local_publication_rollback:
                if _exact_unlink(self.parent_fd, name, inode):
                    self.owned.pop(name, None)
                try:
                    os.fsync(self.parent_fd)
                except OSError:
                    pass
            raise
        finally:
            temporary_removed = False
            try:
                os.unlink(temporary, dir_fd=self.parent_fd)
                temporary_removed = True
            except FileNotFoundError:
                pass
            if temporary_removed and policy.fsync_temp_cleanup:
                operations.fsync_cleanup(self.parent_fd)


def _read_exact_handshake(
    process: subprocess.Popen[bytes], timeout: float = HANDSHAKE_SECONDS
) -> bytes:
    assert process.stdout is not None and process.stderr is not None
    streams = {
        process.stdout.fileno(): (process.stdout, bytearray()),
        process.stderr.fileno(): (process.stderr, bytearray()),
    }
    for descriptor in streams:
        os.set_blocking(descriptor, False)
    deadline = time.monotonic() + timeout
    with selectors.DefaultSelector() as selector:
        for descriptor in streams:
            selector.register(descriptor, selectors.EVENT_READ)
        stdout_descriptor = process.stdout.fileno()
        stderr_descriptor = process.stderr.fileno()
        while stdout_descriptor in selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise HarnessError("controller handshake timed out")
            for key, _mask in selector.select(min(remaining, 0.05)):
                stream, captured = streams[key.fd]
                try:
                    chunk = os.read(
                        key.fd, min(64, HANDSHAKE_LIMIT + 1 - len(captured))
                    )
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(key.fd)
                    stream.close()
                    continue
                captured.extend(chunk)
                if len(captured) > HANDSHAKE_LIMIT:
                    raise HarnessError("controller handshake exceeded its bound")
                if key.fd == stdout_descriptor and captured.endswith(b"READY\n"):
                    selector.unregister(key.fd)
                    stream.close()
                if key.fd == stderr_descriptor and captured:
                    raise HarnessError("controller emitted stderr")
        try:
            trailing_stderr = os.read(stderr_descriptor, HANDSHAKE_LIMIT + 1)
        except BlockingIOError:
            trailing_stderr = b""
        streams[stderr_descriptor][1].extend(trailing_stderr)
        if stderr_descriptor in selector.get_map():
            selector.unregister(stderr_descriptor)
        process.stderr.close()
    stderr = bytes(streams[stderr_descriptor][1])
    if stderr:
        raise HarnessError("controller emitted stderr")
    return bytes(streams[stdout_descriptor][1])


class ControllerSupervisor:
    """One bounded process-group owner for every synthetic controller case."""

    def __init__(self) -> None:
        _require_signal_primitives()
        self.process: Optional[subprocess.Popen[bytes]] = None
        self.pgid: Optional[int] = None
        self.registered = False

    def start(
        self,
        argv: Sequence[str],
        cwd: Path,
        *,
        policy: MutationPolicy = SECURE_POLICY,
        after_popen: Optional[Callable[[], None]] = None,
    ) -> None:
        if self.process is not None:
            raise HarnessError("controller already started")
        previous_mask = None
        if policy.block_spawn_signals:
            previous_mask = signal.pthread_sigmask(
                signal.SIG_BLOCK, LIFECYCLE_SIGNALS
            )
        local_process: Optional[subprocess.Popen[bytes]] = None
        local_pgid: Optional[int] = None
        try:
            local_process = subprocess.Popen(
                list(argv),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(cwd),
                env={
                    "HOME": str(cwd),
                    "TMPDIR": str(cwd),
                    "LANG": "C",
                    "LC_ALL": "C",
                    "NO_COLOR": "1",
                    "PATH": "/usr/bin:/bin",
                },
                start_new_session=True,
            )
            local_pgid = local_process.pid
            if after_popen is not None:
                after_popen()
            if os.getpgid(local_process.pid) != local_process.pid:
                local_process.kill()
                local_process.wait(timeout=KILL_GRACE_SECONDS)
                raise HarnessError("controller did not reserve its own process group")
            self.process = local_process
            self.pgid = local_pgid
            self.registered = True
            handshake = _read_exact_handshake(local_process)
            expected = f"PGID {local_process.pid}\nREADY\n".encode("ascii")
            if handshake != expected:
                raise HarnessError("controller handshake was malformed")
        except BaseException:
            if local_process is not None and local_pgid is not None:
                try:
                    safe_group = os.getpgid(local_process.pid) == local_pgid
                except ProcessLookupError:
                    safe_group = False
                if safe_group:
                    self.process = local_process
                    self.pgid = local_pgid
                    self.registered = True
                    self.terminate(policy=SECURE_POLICY)
                else:
                    local_process.kill()
                    local_process.wait(timeout=KILL_GRACE_SECONDS)
            raise
        finally:
            if policy.block_spawn_signals and previous_mask is not None:
                signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)

    def terminate(
        self,
        *,
        policy: MutationPolicy = SECURE_POLICY,
        during_grace: Optional[Callable[[], None]] = None,
        during_reap: Optional[Callable[[], None]] = None,
    ) -> None:
        if self.process is None or self.pgid is None or not self.registered:
            return
        process = self.process
        pgid = self.pgid
        previous_mask = None
        if policy.block_cleanup_signals:
            previous_mask = signal.pthread_sigmask(
                signal.SIG_BLOCK, LIFECYCLE_SIGNALS
            )
        try:
            try:
                os.killpg(pgid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
            if during_grace is not None:
                during_grace()
            deadline = time.monotonic() + TERM_GRACE_SECONDS
            while _group_exists(pgid) and time.monotonic() < deadline:
                time.sleep(0.01)
            if _group_exists(pgid):
                try:
                    os.killpg(pgid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
            if during_reap is not None:
                during_reap()
            try:
                process.wait(timeout=KILL_GRACE_SECONDS)
            except subprocess.TimeoutExpired as exc:
                raise HarnessError("controller leader could not be reaped") from exc
            if not _wait_group_absent(pgid, KILL_GRACE_SECONDS):
                raise HarnessError("controller process group survived cleanup")
            self.process = None
            self.pgid = None
            self.registered = False
        finally:
            if policy.block_cleanup_signals and previous_mask is not None:
                signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)


def atomic_completion(
    marker: Callable[[], None],
    rollback: Callable[[], None],
    disarm: Callable[[], None],
    *,
    policy: MutationPolicy = SECURE_POLICY,
) -> None:
    """Linearize final publication while lifecycle signals remain blocked."""

    _require_signal_primitives()
    previous_mask = None
    if policy.block_completion_signals:
        previous_mask = signal.pthread_sigmask(
            signal.SIG_BLOCK, LIFECYCLE_SIGNALS
        )
    rollback_started = False
    try:
        marker()
        pending = set(signal.sigpending()).intersection(LIFECYCLE_SIGNALS)
        if pending:
            first = signal.sigwait(pending)
            rollback_started = True
            rollback()
            raise HarnessInterrupted(first)
        disarm()
    except BaseException:
        if policy.rollback_completion_exceptions and not rollback_started:
            rollback_started = True
            rollback()
        raise
    finally:
        if policy.block_completion_signals and previous_mask is not None:
            signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)


FAKE_CONTROLLER_SOURCE = r'''import os,signal,sys,time
root=sys.argv[1]
for item in (signal.SIGHUP,signal.SIGINT,signal.SIGTERM):
 signal.pthread_sigmask(signal.SIG_UNBLOCK,(item,))
def marker(name,data):
 path=os.path.join(root,name)
 fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
 try:
  os.fchmod(fd,0o600); os.write(fd,data); os.fsync(fd)
 finally: os.close(fd)
def term(_n,_f):
 try: marker("term.marker",b"term\n")
 except FileExistsError: pass
signal.signal(signal.SIGTERM,term)
os.write(1,("PGID %d\n"%os.getpid()).encode("ascii"))
pid=os.fork()
if pid==0:
 signal.signal(signal.SIGTERM,signal.SIG_IGN)
 marker("descendant.ready",b"ready\n")
 time.sleep(.40)
 marker("late.marker",b"late\n")
 time.sleep(9)
 raise SystemExit(0)
while not os.path.exists(os.path.join(root,"descendant.ready")): time.sleep(.005)
os.write(1,b"READY\n")
while True: signal.pause()
'''


def fake_controller_argv(root: Path) -> list[str]:
    return [sys.executable, "-I", "-S", "-B", "-c", FAKE_CONTROLLER_SOURCE, str(root)]


def _wait_marker(path: Path, timeout: float = 1.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            value = path.stat()
        except FileNotFoundError:
            time.sleep(0.005)
            continue
        return (
            stat.S_ISREG(value.st_mode)
            and value.st_uid == os.getuid()
            and _mode(value) == 0o600
        )
    return False


def _private_case(parent: Path, name: str) -> Path:
    case = parent / name
    case.mkdir(mode=0o700)
    value = case.stat()
    if value.st_uid != os.getuid() or _mode(value) != 0o700:
        raise HarnessError("case directory is not owner-private")
    return case


def run_publication_cases(root: Path) -> list[dict[str, object]]:
    evidence: list[dict[str, object]] = []
    for final_name in ("version.binding.json", "version.binding.sha256"):
        for fault in ("post-link-stat", "parent-fsync"):
            secure_case = _private_case(root, f"publication-secure-{final_name}-{fault}")
            publisher = DurablePublisher(secure_case)
            operations = PublicationOps(fault=fault)
            rejected = False
            try:
                publisher.publish(final_name, b"evidence\n", ops=operations)
            except OSError:
                rejected = True
            publisher.rollback()
            secure_absent = not (secure_case / final_name).exists()
            publisher.close()
            if not (rejected and operations.fired and secure_absent):
                raise HarnessError("secure publication fault case failed")
            evidence.append(
                {"case_id": f"secure-{final_name}-{fault}", "status": "accepted"}
            )

            mutation = (
                dataclasses.replace(SECURE_POLICY, local_publication_rollback=False)
                if fault == "post-link-stat"
                else dataclasses.replace(
                    SECURE_POLICY,
                    local_publication_rollback=False,
                    register_before_parent_fsync=False,
                )
            )
            weak_case = _private_case(root, f"publication-mutation-{final_name}-{fault}")
            weak = DurablePublisher(weak_case)
            weak_ops = PublicationOps(fault=fault)
            try:
                weak.publish(final_name, b"evidence\n", ops=weak_ops, policy=mutation)
            except OSError:
                pass
            leaked_before_cleanup = (weak_case / final_name).exists()
            if leaked_before_cleanup:
                value = (weak_case / final_name).stat()
                _exact_unlink(weak.parent_fd, final_name, value.st_ino)
                os.fsync(weak.parent_fd)
            weak.close()
            if not (weak_ops.fired and leaked_before_cleanup):
                raise HarnessError("publication mutation was not killed")
            evidence.append(
                {"case_id": f"mutation-{final_name}-{fault}", "status": "killed"}
            )

        for label, policy, expected_fsyncs, status in (
            ("secure", SECURE_POLICY, 1, "accepted"),
            (
                "mutation",
                dataclasses.replace(SECURE_POLICY, fsync_temp_cleanup=False),
                0,
                "killed",
            ),
        ):
            cleanup_case = _private_case(
                root, f"publication-{label}-{final_name}-temp-cleanup-fsync"
            )
            sentinel = cleanup_case / final_name
            descriptor = os.open(
                sentinel,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            try:
                os.fchmod(descriptor, 0o600)
                _write_all(descriptor, b"existing\n")
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            cleanup_publisher = DurablePublisher(cleanup_case)
            cleanup_ops = PublicationOps()
            rejected = False
            try:
                cleanup_publisher.publish(
                    final_name,
                    b"replacement\n",
                    ops=cleanup_ops,
                    policy=policy,
                )
            except HarnessError:
                rejected = True
            no_temp = not any(
                path.name.endswith(".tmp") for path in cleanup_case.iterdir()
            )
            sentinel_unchanged = sentinel.read_bytes() == b"existing\n"
            sentinel_stat = sentinel.stat()
            os.unlink(sentinel)
            os.fsync(cleanup_publisher.parent_fd)
            cleanup_publisher.close()
            if not (
                rejected
                and no_temp
                and sentinel_unchanged
                and _mode(sentinel_stat) == 0o600
                and cleanup_ops.cleanup_fsyncs == expected_fsyncs
            ):
                raise HarnessError("temporary cleanup fsync control failed")
            evidence.append(
                {
                    "case_id": f"{label}-{final_name}-temp-cleanup-fsync",
                    "status": status,
                }
            )
    return evidence


def _capture_actual_fsyncs(
    parent_fd: int,
    action: Callable[[], None],
    mutation: Optional[FsyncMutation] = None,
) -> list[tuple[str, int]]:
    """Record only completed real fsync calls, with exact descriptor order."""

    real_fsync = os.fsync
    calls: list[tuple[str, int]] = []
    ordinals = {"staged": 0, "parent": 0}

    def traced_fsync(descriptor: int) -> None:
        value = os.fstat(descriptor)
        if descriptor == parent_fd and stat.S_ISDIR(value.st_mode):
            role = "parent"
        elif descriptor != parent_fd and stat.S_ISREG(value.st_mode):
            role = "staged"
        else:
            raise HarnessError("fsync used an unexpected descriptor")
        ordinals[role] += 1
        if mutation == FsyncMutation(role, ordinals[role]):
            return
        real_fsync(descriptor)
        calls.append((role, descriptor))

    os.fsync = traced_fsync
    try:
        action()
    finally:
        os.fsync = real_fsync
    return calls


def _fsync_authority_case(
    root: Path,
    category: str,
    mutation: Optional[FsyncMutation],
) -> bool:
    label = "secure" if mutation is None else "mutation"
    case = _private_case(root, f"fsync-{label}-{category}")
    publisher = DurablePublisher(case)
    expected: list[tuple[str, int]]

    if category in ("staged-file", "post-link-parent", "post-temp-parent"):
        suppression = {
            "staged-file": FsyncMutation("staged", 1),
            "post-link-parent": FsyncMutation("parent", 1),
            "post-temp-parent": FsyncMutation("parent", 2),
        }[category]
        if mutation is not None and mutation != suppression:
            raise HarnessError("fsync mutation does not match its category")
        calls = _capture_actual_fsyncs(
            publisher.parent_fd,
            lambda: publisher.publish("version.binding.json", b"binding\n"),
            mutation,
        )
        expected = [
            ("staged", calls[0][1] if calls and calls[0][0] == "staged" else -1),
            ("parent", publisher.parent_fd),
            ("parent", publisher.parent_fd),
        ]
        accepted = calls == expected
        publisher.rollback()
    elif category == "rollback-parent":
        publisher.publish("version.binding.json", b"binding\n")
        calls = _capture_actual_fsyncs(
            publisher.parent_fd,
            publisher.rollback,
            mutation,
        )
        expected = [("parent", publisher.parent_fd)]
        accepted = calls == expected
        os.fsync(publisher.parent_fd)
    elif category == "failure-cleanup-parent":
        sentinel = case / "version.binding.json"
        descriptor = os.open(
            sentinel,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        try:
            os.fchmod(descriptor, 0o600)
            _write_all(descriptor, b"existing\n")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

        def reject_existing_target() -> None:
            try:
                publisher.publish("version.binding.json", b"replacement\n")
            except HarnessError:
                return
            raise HarnessError("existing publication target was accepted")

        calls = _capture_actual_fsyncs(
            publisher.parent_fd,
            reject_existing_target,
            mutation,
        )
        expected = [
            ("staged", calls[0][1] if calls and calls[0][0] == "staged" else -1),
            ("parent", publisher.parent_fd),
        ]
        accepted = calls == expected
        os.unlink(sentinel)
        os.fsync(publisher.parent_fd)
    else:
        publisher.close()
        raise HarnessError("unknown fsync authority category")

    no_final = not (case / "version.binding.json").exists()
    no_temp = not any(path.name.endswith(".tmp") for path in case.iterdir())
    publisher.close()
    return accepted and no_final and no_temp


def run_fsync_authority_cases(root: Path) -> list[dict[str, object]]:
    evidence: list[dict[str, object]] = []
    categories = {
        "staged-file": FsyncMutation("staged", 1),
        "post-link-parent": FsyncMutation("parent", 1),
        "post-temp-parent": FsyncMutation("parent", 2),
        "rollback-parent": FsyncMutation("parent", 1),
        "failure-cleanup-parent": FsyncMutation("parent", 1),
    }
    for category, mutation in categories.items():
        if not _fsync_authority_case(root, category, None):
            raise HarnessError("secure fsync authority case failed")
        evidence.append(
            {"case_id": f"secure-fsync-{category}", "status": "accepted"}
        )
        if _fsync_authority_case(root, category, mutation):
            raise HarnessError("fsync omission mutation was not killed")
        evidence.append(
            {"case_id": f"mutation-fsync-{category}", "status": "killed"}
        )
    return evidence


def _run_supervisor_case(
    root: Path,
    signum: int,
    phase: str,
    policy: MutationPolicy,
) -> tuple[int, bool, bool]:
    case = _private_case(root, f"signal-{signum}-{phase}-{hash(policy)}")
    supervisor = ControllerSupervisor()
    previous = signal.getsignal(signum)
    delivered_after_registration = False

    def interrupt(received: int, _frame: object) -> None:
        nonlocal delivered_after_registration
        delivered_after_registration = supervisor.registered
        raise HarnessInterrupted(received)

    signal.signal(signum, interrupt)
    try:
        if phase == "fork-pgid":
            supervisor.start(
                fake_controller_argv(case),
                case,
                policy=policy,
                after_popen=lambda: os.kill(os.getpid(), signum),
            )
        else:
            supervisor.start(fake_controller_argv(case), case, policy=SECURE_POLICY)
            os.kill(os.getpid(), signum)
    except HarnessInterrupted as exc:
        try:
            if phase == "group-grace":
                supervisor.terminate(
                    policy=policy,
                    during_grace=lambda: os.kill(os.getpid(), signum),
                )
            else:
                supervisor.terminate(policy=SECURE_POLICY)
        except HarnessInterrupted:
            pass
        return 128 + exc.signum, delivered_after_registration, supervisor.registered
    finally:
        signal.signal(signum, previous)
        if supervisor.registered:
            try:
                supervisor.terminate(policy=SECURE_POLICY)
            except BaseException:
                pass
    raise HarnessError("supervisor case was not interrupted")


def run_signal_cases(root: Path) -> list[dict[str, object]]:
    evidence: list[dict[str, object]] = []
    for signum in LIFECYCLE_SIGNALS:
        secure_exit, after_registration, still_active = _run_supervisor_case(
            root, signum, "fork-pgid", SECURE_POLICY
        )
        if secure_exit != 128 + signum or not after_registration or still_active:
            raise HarnessError("secure fork-to-PGID case failed")
        evidence.append(
            {"case_id": f"secure-fork-pgid-{signum}", "status": "accepted"}
        )
        weak = dataclasses.replace(SECURE_POLICY, block_spawn_signals=False)
        weak_exit, weak_after, weak_active = _run_supervisor_case(
            root, signum, "fork-pgid", weak
        )
        if weak_exit != 128 + signum or weak_after or weak_active:
            raise HarnessError("fork-to-PGID mutation was not killed")
        evidence.append(
            {"case_id": f"mutation-fork-pgid-{signum}", "status": "killed"}
        )

        secure_exit, _after, still_active = _run_supervisor_case(
            root, signum, "group-grace", SECURE_POLICY
        )
        if secure_exit != 128 + signum or still_active:
            raise HarnessError("secure double-signal cleanup case failed")
        evidence.append(
            {"case_id": f"secure-group-grace-{signum}", "status": "accepted"}
        )
        weak_cleanup = dataclasses.replace(
            SECURE_POLICY, block_cleanup_signals=False
        )
        weak_exit, _weak_after, weak_active = _run_supervisor_case(
            root, signum, "group-grace", weak_cleanup
        )
        if weak_exit != 128 + signum or not weak_active:
            raise HarnessError("double-signal cleanup mutation was not killed")
        evidence.append(
            {"case_id": f"mutation-group-grace-{signum}", "status": "killed"}
        )
    time.sleep(0.45)
    if any(root.rglob("late.marker")):
        raise HarnessError("a cleaned controller produced a late side effect")
    return evidence


def _publisher_cleanup_signal_case(
    root: Path,
    signum: int,
    phase: str,
    policy: MutationPolicy,
) -> tuple[int, bool, bool]:
    case = _private_case(root, f"publisher-signal-{signum}-{phase}-{hash(policy)}")
    publisher = DurablePublisher(case)
    publisher.publish("tracked.final", b"evidence\n")
    previous = signal.getsignal(signum)
    entry_mask = signal.pthread_sigmask(signal.SIG_BLOCK, [])
    nested = False

    def interrupt(received: int, _frame: object) -> None:
        raise HarnessInterrupted(received)

    def second_signal() -> None:
        os.kill(os.getpid(), signum)

    signal.signal(signum, interrupt)
    try:
        try:
            os.kill(os.getpid(), signum)
        except HarnessInterrupted as first:
            if policy.block_cleanup_signals:
                signal.pthread_sigmask(signal.SIG_BLOCK, LIFECYCLE_SIGNALS)
            try:
                publisher.rollback(
                    before_unlink=second_signal if phase == "unlink" else None,
                    before_fsync=second_signal if phase == "parent-fsync" else None,
                )
            except HarnessInterrupted:
                nested = True
            return (
                128 + first.signum,
                nested,
                (case / "tracked.final").exists(),
            )
    finally:
        signal.pthread_sigmask(signal.SIG_BLOCK, LIFECYCLE_SIGNALS)
        publisher.rollback()
        pending = set(signal.sigpending()).intersection(LIFECYCLE_SIGNALS)
        while pending:
            signal.sigwait(pending)
            pending = set(signal.sigpending()).intersection(LIFECYCLE_SIGNALS)
        signal.signal(signum, previous)
        signal.pthread_sigmask(signal.SIG_SETMASK, entry_mask)
        publisher.close()
    raise HarnessError("publisher cleanup case was not interrupted")


def run_publisher_cleanup_signal_cases(root: Path) -> list[dict[str, object]]:
    evidence: list[dict[str, object]] = []
    for signum in LIFECYCLE_SIGNALS:
        for phase in ("unlink", "parent-fsync"):
            secure_exit, secure_nested, secure_final = _publisher_cleanup_signal_case(
                root, signum, phase, SECURE_POLICY
            )
            if secure_exit != 128 + signum or secure_nested or secure_final:
                raise HarnessError("secure publisher signal cleanup failed")
            evidence.append(
                {
                    "case_id": f"secure-publisher-{phase}-{signum}",
                    "status": "accepted",
                }
            )
            weak = dataclasses.replace(SECURE_POLICY, block_cleanup_signals=False)
            weak_exit, weak_nested, _weak_final = _publisher_cleanup_signal_case(
                root, signum, phase, weak
            )
            if weak_exit != 128 + signum or not weak_nested:
                raise HarnessError("publisher signal cleanup mutation was not killed")
            evidence.append(
                {
                    "case_id": f"mutation-publisher-{phase}-{signum}",
                    "status": "killed",
                }
            )
    return evidence


def _completion_case(root: Path, signum: int, policy: MutationPolicy) -> tuple[int, bool]:
    case = _private_case(root, f"completion-{signum}-{hash(policy)}")
    publisher = DurablePublisher(case)
    previous = signal.getsignal(signum)
    entry_mask = signal.pthread_sigmask(signal.SIG_BLOCK, [])

    def interrupt(received: int, _frame: object) -> None:
        raise HarnessInterrupted(received)

    signal.signal(signum, interrupt)
    try:
        try:
            atomic_completion(
                lambda: (
                    publisher.publish("version.binding.sha256", b"digest\n"),
                    os.kill(os.getpid(), signum),
                ),
                publisher.rollback,
                lambda: None,
                policy=policy,
            )
        except HarnessInterrupted as exc:
            return 128 + exc.signum, (case / "version.binding.sha256").exists()
    finally:
        signal.pthread_sigmask(signal.SIG_BLOCK, LIFECYCLE_SIGNALS)
        publisher.rollback()
        signal.signal(signum, previous)
        signal.pthread_sigmask(signal.SIG_SETMASK, entry_mask)
        publisher.close()
    raise HarnessError("completion case was not interrupted")


def run_completion_cases(root: Path) -> list[dict[str, object]]:
    evidence: list[dict[str, object]] = []
    for signum in LIFECYCLE_SIGNALS:
        secure_exit, secure_final = _completion_case(root, signum, SECURE_POLICY)
        if secure_exit != 128 + signum or secure_final:
            raise HarnessError("secure completion case failed")
        evidence.append(
            {"case_id": f"secure-completion-{signum}", "status": "accepted"}
        )
        weak = dataclasses.replace(
            SECURE_POLICY,
            block_completion_signals=False,
            rollback_completion_exceptions=False,
        )
        weak_exit, weak_final = _completion_case(root, signum, weak)
        if weak_exit != 128 + signum or not weak_final:
            raise HarnessError("completion mutation was not killed")
        evidence.append(
            {"case_id": f"mutation-completion-{signum}", "status": "killed"}
        )
    return evidence


def run_completion_failure_cases(root: Path) -> list[dict[str, object]]:
    evidence: list[dict[str, object]] = []
    for failure_point in ("marker", "disarm"):
        for label, policy, status in (
            ("secure", SECURE_POLICY, "accepted"),
            (
                "mutation",
                dataclasses.replace(
                    SECURE_POLICY, rollback_completion_exceptions=False
                ),
                "killed",
            ),
        ):
            case = _private_case(root, f"completion-{label}-{failure_point}")
            publisher = DurablePublisher(case)

            def marker() -> None:
                publisher.publish("version.binding.sha256", b"digest\n")
                if failure_point == "marker":
                    raise HarnessError("injected marker failure")

            def disarm() -> None:
                if failure_point == "disarm":
                    raise HarnessError("injected disarm failure")

            rejected = False
            try:
                atomic_completion(marker, publisher.rollback, disarm, policy=policy)
            except HarnessError:
                rejected = True
            final_exists = (case / "version.binding.sha256").exists()
            if final_exists:
                publisher.rollback()
            publisher.close()
            expected_final = label == "mutation"
            if not rejected or final_exists != expected_final:
                raise HarnessError("completion failure mutation control failed")
            evidence.append(
                {
                    "case_id": f"{label}-completion-{failure_point}",
                    "status": status,
                }
            )
    return evidence


def run_offline_harness() -> dict[str, object]:
    """Run all fixed synthetic cases and return a path-free result."""

    _require_signal_primitives()
    private_root = Path(tempfile.mkdtemp(prefix="agy-version-attestation-harness."))
    os.chmod(private_root, 0o700)
    try:
        evidence = []
        evidence.extend(run_publication_cases(private_root))
        evidence.extend(run_fsync_authority_cases(private_root))
        evidence.extend(run_signal_cases(private_root))
        evidence.extend(run_publisher_cleanup_signal_cases(private_root))
        evidence.extend(run_completion_cases(private_root))
        evidence.extend(run_completion_failure_cases(private_root))
        secure = sum(item["status"] == "accepted" for item in evidence)
        killed = sum(item["status"] == "killed" for item in evidence)
        return {
            "case_count": len(evidence),
            "failed": 0,
            "mutations_killed": killed,
            "schema_version": 1,
            "secure": secure,
            "status": "accepted",
        }
    finally:
        import shutil

        shutil.rmtree(private_root)


def main(argv: list[str]) -> int:
    if argv != ["--self-test"]:
        print("version attestation harness: invalid invocation", file=sys.stderr)
        return 64
    try:
        result = run_offline_harness()
    except (HarnessError, OSError, subprocess.SubprocessError):
        print("version attestation harness: rejected", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
