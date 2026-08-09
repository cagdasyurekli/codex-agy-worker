#!/usr/bin/env python3
"""Bounded repository-only runner for the public qa-gate v1 fixture contract."""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import selectors
import shlex
import signal
import stat
import subprocess
import sys
import tempfile
import time
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


MANIFEST_SHA256 = "9741584060f5391e5a79df1022c9cd574c28fdddefc75006b8b6e7ff0e5e36a0"
MANIFEST_MAX_BYTES = 65536
FIXTURE_IDS = (
    "honest-edit",
    "scope-undeclared",
    "ignored-untracked",
    "untrusted-worker-command",
    "malformed-envelope",
    "expected-edits-missing",
    "verifier-failure",
    "verifier-mutation",
    "human-required",
    "mutable-base",
    "missing-verifier",
)
EXPECTED_EXITS = (0, 10, 10, 11, 12, 13, 14, 14, 15, 64, 64)
LIFECYCLE_SIGNALS = (signal.SIGHUP, signal.SIGINT, signal.SIGTERM)
GIT_TIMEOUT_SECONDS = 5.0
GIT_OUTPUT_LIMIT = 8192
GIT = "/usr/bin/git"
TERM_GRACE_SECONDS = 0.05
CLEANUP_MAX_ENTRIES = 4096
CLEANUP_MAX_DEPTH = 32
CLEANUP_MAX_BYTES = 16 * 1024 * 1024
CLEANUP_TIMEOUT_SECONDS = 2.0
OPEN_DIRECTORY_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
)
try:
    _WAITID = ctypes.CDLL(None, use_errno=True).waitid
except (AttributeError, OSError):
    _WAITID = None


class ConformanceError(Exception):
    pass


class Interrupted(BaseException):
    def __init__(self, signum: int) -> None:
        super().__init__(signum)
        self.signum = signum


ACTIVE_PROCESS: Optional[subprocess.Popen[bytes]] = None
ACTIVE_WORKSPACE: Optional[Path] = None
ACTIVE_WORKSPACE_IDENTITY: Optional[Tuple[int, int, int, int]] = None
ACTIVE_WORKSPACE_PARENT_IDENTITY: Optional[Tuple[int, int, int, int]] = None
ACTIVE_WORKSPACE_FD: Optional[int] = None
ACTIVE_WORKSPACE_PARENT_FD: Optional[int] = None
FIRST_SIGNAL: Optional[int] = None


def _signal_handler(signum: int, _frame: Any) -> None:
    global FIRST_SIGNAL
    if FIRST_SIGNAL is None:
        FIRST_SIGNAL = signum
    raise Interrupted(FIRST_SIGNAL)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _strict_json(data: bytes) -> Any:
    def unique(pairs: Iterable[Tuple[str, Any]]) -> Dict[str, Any]:
        value: Dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise ConformanceError("duplicate JSON key")
            value[key] = item
        return value

    try:
        return json.loads(data.decode("ascii"), object_pairs_hook=unique)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConformanceError("invalid JSON") from exc


def _canonical(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("ascii")


def _read_regular(path: Path, maximum: int) -> bytes:
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
        before = os.fstat(descriptor)
    except OSError as exc:
        raise ConformanceError("required fixture is unavailable") from exc
    try:
        if not stat.S_ISREG(before.st_mode) or before.st_size > maximum:
            raise ConformanceError("fixture type or size is invalid")
        chunks = bytearray()
        while len(chunks) <= maximum:
            chunk = os.read(descriptor, min(65536, maximum + 1 - len(chunks)))
            if not chunk:
                break
            chunks.extend(chunk)
        after = os.fstat(descriptor)
        if (
            len(chunks) != before.st_size
            or os.read(descriptor, 1) != b""
            or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        ):
            raise ConformanceError("fixture changed while reading")
        return bytes(chunks)
    except OSError as exc:
        raise ConformanceError("fixture cannot be read") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _exact_keys(value: Any, keys: Sequence[str]) -> None:
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ConformanceError("manifest object shape is invalid")


def _safe_relative(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > 160:
        raise ConformanceError("fixture path is invalid")
    path = PurePosixPath(value)
    if path.is_absolute() or value != path.as_posix() or any(
        part in ("", ".", "..") for part in path.parts
    ):
        raise ConformanceError("fixture path is not canonical")
    if path.parts[0] == ".git":
        raise ConformanceError("fixture path enters Git metadata")
    return value


def _safe_source(value: Any) -> str:
    path = _safe_relative(value)
    if not (path.startswith("files/") or path.startswith("envelopes/")):
        raise ConformanceError("fixture source is outside the versioned contract")
    return path


def _validate_file_entries(value: Any) -> List[Dict[str, str]]:
    if not isinstance(value, list) or len(value) > 8:
        raise ConformanceError("fixture file list is invalid")
    entries: List[Dict[str, str]] = []
    seen = set()
    for item in value:
        _exact_keys(item, ("path", "sha256", "source"))
        path = _safe_relative(item["path"])
        source = _safe_source(item["source"])
        digest = item["sha256"]
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(char not in "0123456789abcdef" for char in digest)
            or path in seen
        ):
            raise ConformanceError("fixture file binding is invalid")
        seen.add(path)
        entries.append({"path": path, "source": source, "sha256": digest})
    if entries != sorted(entries, key=lambda item: item["path"]):
        raise ConformanceError("fixture file list is not ordered")
    return entries


def _validate_manifest(root: Path) -> Dict[str, Any]:
    manifest_path = root / "manifest.json"
    raw = _read_regular(manifest_path, MANIFEST_MAX_BYTES)
    if _sha256(raw) != MANIFEST_SHA256:
        raise ConformanceError("manifest digest is not the reviewed v1 contract")
    value = _strict_json(raw)
    if _canonical(value) != raw:
        raise ConformanceError("manifest is not canonical JSON")
    _exact_keys(value, ("claim", "fixtures", "kind", "limits", "schema_version"))
    _exact_keys(value["claim"], ("certification", "receipt_conformance", "scope"))
    _exact_keys(
        value["limits"],
        (
            "fixture_count",
            "gate_stderr_bytes",
            "gate_stdout_bytes",
            "gate_timeout_seconds",
            "total_fixture_bytes",
        ),
    )
    if value["schema_version"] != 1 or value["kind"] != "codex-agy-worker-gate-conformance":
        raise ConformanceError("manifest version or kind is unsupported")
    if value["claim"] != {
        "certification": False,
        "receipt_conformance": False,
        "scope": "qa-gate-v1-synthetic-fixtures",
    }:
        raise ConformanceError("manifest claim is unsupported")
    limits = value["limits"]
    if limits != {
        "fixture_count": 11,
        "gate_stderr_bytes": 8192,
        "gate_stdout_bytes": 8192,
        "gate_timeout_seconds": 10,
        "total_fixture_bytes": 1048576,
    }:
        raise ConformanceError("manifest limits are unsupported")
    fixtures = value["fixtures"]
    if not isinstance(fixtures, list) or len(fixtures) != limits["fixture_count"]:
        raise ConformanceError("fixture count is invalid")

    used_sources: Dict[str, str] = {}
    checked: List[Dict[str, Any]] = []
    for index, fixture in enumerate(fixtures):
        _exact_keys(
            fixture,
            (
                "allow",
                "base",
                "base_argument",
                "candidate",
                "envelope",
                "envelope_sha256",
                "expect_edits",
                "expected_exit",
                "id",
                "only",
                "verifier",
            ),
        )
        if fixture["id"] != FIXTURE_IDS[index] or fixture["expected_exit"] != EXPECTED_EXITS[index]:
            raise ConformanceError("fixture identity or exit changed")
        if fixture["base_argument"] not in ("immutable", "HEAD"):
            raise ConformanceError("base argument kind is invalid")
        if fixture["verifier"] not in ("content-match", "fail", "mutate", "none"):
            raise ConformanceError("verifier kind is invalid")
        if not isinstance(fixture["expect_edits"], bool):
            raise ConformanceError("edit expectation is invalid")
        for policy_name in ("allow", "only"):
            policy = fixture[policy_name]
            if (
                not isinstance(policy, list)
                or len(policy) > 4
                or len(set(policy)) != len(policy)
                or any(not isinstance(item, str) or not item or len(item) > 160 for item in policy)
            ):
                raise ConformanceError("path policy is invalid")
        fixture["base"] = _validate_file_entries(fixture["base"])
        fixture["candidate"] = _validate_file_entries(fixture["candidate"])
        envelope = _safe_source(fixture["envelope"])
        if not envelope.startswith("envelopes/"):
            raise ConformanceError("envelope source is invalid")
        digest = fixture["envelope_sha256"]
        if not isinstance(digest, str) or len(digest) != 64 or any(
            char not in "0123456789abcdef" for char in digest
        ):
            raise ConformanceError("envelope digest is invalid")
        previous_envelope = used_sources.setdefault(envelope, digest)
        if previous_envelope != digest:
            raise ConformanceError("one envelope has conflicting digests")
        for entry in fixture["base"] + fixture["candidate"]:
            previous = used_sources.setdefault(entry["source"], entry["sha256"])
            if previous != entry["sha256"]:
                raise ConformanceError("one source has conflicting digests")
        checked.append(fixture)

    total = 0
    for source, digest in sorted(used_sources.items()):
        path = root / source
        if path.resolve(strict=False) != path:
            raise ConformanceError("fixture source path is not canonical")
        data = _read_regular(path, limits["total_fixture_bytes"])
        total += len(data)
        if total > limits["total_fixture_bytes"] or _sha256(data) != digest:
            raise ConformanceError("fixture source digest or total size is invalid")
    value["fixtures"] = checked
    return value


def _group_exists(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _leader_exited_unreaped(pid: int) -> bool:
    if _WAITID is None or not all(
        hasattr(os, name) for name in ("P_PID", "WEXITED", "WNOHANG", "WNOWAIT")
    ):
        raise ConformanceError("non-reaping child observation is unavailable")
    information = (ctypes.c_ubyte * 256)()
    ctypes.set_errno(0)
    result = _WAITID(
        os.P_PID,
        pid,
        ctypes.byref(information),
        os.WEXITED | os.WNOHANG | os.WNOWAIT,
    )
    if result != 0:
        raise ConformanceError("non-reaping child observation failed")
    return any(information)


def _close_process_group(
    process: subprocess.Popen[bytes], signum: int = signal.SIGTERM
) -> int:
    if process.returncode is not None:
        raise ConformanceError("child leader was reaped before group cleanup")
    pgid = process.pid
    try:
        os.killpg(pgid, signum)
    except (ProcessLookupError, PermissionError):
        pass
    deadline = time.monotonic() + TERM_GRACE_SECONDS
    while _group_exists(pgid) and time.monotonic() < deadline:
        time.sleep(0.01)
    if _group_exists(pgid):
        try:
            os.killpg(pgid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    # Keep the unreaped leader as the PGID reservation while SIGKILL settles.
    # The test-owned descendant PIDs provide the non-signalling postcondition;
    # never query or signal a potentially reused PGID after wait() reaps it.
    time.sleep(TERM_GRACE_SECONDS)
    try:
        process.wait(timeout=0.75)
    except subprocess.TimeoutExpired as exc:
        raise ConformanceError("child process could not be reaped") from exc
    assert process.returncode is not None
    return process.returncode


def _run_bounded(
    argv: Sequence[str],
    *,
    cwd: Path,
    env: Dict[str, str],
    timeout: float,
    stdout_limit: int,
    stderr_limit: int,
) -> Tuple[int, bytes, bytes]:
    global ACTIVE_PROCESS
    previous_mask = None
    if hasattr(signal, "pthread_sigmask"):
        previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, LIFECYCLE_SIGNALS)
    try:
        try:
            process = subprocess.Popen(
                list(argv),
                cwd=str(cwd),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
        except OSError as exc:
            raise ConformanceError("child process could not start") from exc
        ACTIVE_PROCESS = process
    finally:
        if previous_mask is not None:
            signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)
    assert process.stdout is not None and process.stderr is not None
    stdout_fd = process.stdout.fileno()
    stderr_fd = process.stderr.fileno()
    streams = {
        stdout_fd: (process.stdout, bytearray(), stdout_limit),
        stderr_fd: (process.stderr, bytearray(), stderr_limit),
    }
    for descriptor in streams:
        os.set_blocking(descriptor, False)
    deadline = time.monotonic() + timeout
    cleanup_required = True
    try:
        with selectors.DefaultSelector() as selector:
            for descriptor in streams:
                selector.register(descriptor, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ConformanceError("child process timed out")
                if _leader_exited_unreaped(process.pid):
                    break
                events = (
                    selector.select(min(remaining, 0.05))
                    if selector.get_map()
                    else ()
                )
                if not events:
                    time.sleep(min(remaining, 0.01))
                for key, _event in events:
                    stream, buffer, limit = streams[key.fd]
                    chunk = os.read(key.fd, min(8192, limit + 1 - len(buffer)))
                    if not chunk:
                        selector.unregister(key.fd)
                        stream.close()
                        continue
                    buffer.extend(chunk)
                    if len(buffer) > limit:
                        raise ConformanceError("child output exceeded its bound")
            wait_mask = None
            if hasattr(signal, "pthread_sigmask"):
                wait_mask = signal.pthread_sigmask(signal.SIG_BLOCK, LIFECYCLE_SIGNALS)
            try:
                returncode = _close_process_group(process)
                ACTIVE_PROCESS = None
                cleanup_required = False
            finally:
                if wait_mask is not None:
                    signal.pthread_sigmask(signal.SIG_SETMASK, wait_mask)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ConformanceError("child output did not close")
                events = selector.select(min(remaining, 0.05))
                if not events:
                    continue
                for key, _event in events:
                    stream, buffer, limit = streams[key.fd]
                    chunk = os.read(key.fd, min(8192, limit + 1 - len(buffer)))
                    if not chunk:
                        selector.unregister(key.fd)
                        stream.close()
                        continue
                    buffer.extend(chunk)
                    if len(buffer) > limit:
                        raise ConformanceError("child output exceeded its bound")
        return returncode, bytes(streams[stdout_fd][1]), bytes(streams[stderr_fd][1])
    except BaseException as exc:
        cleanup_mask = None
        if hasattr(signal, "pthread_sigmask"):
            cleanup_mask = signal.pthread_sigmask(signal.SIG_BLOCK, LIFECYCLE_SIGNALS)
        if cleanup_required:
            _close_process_group(process)
            ACTIVE_PROCESS = None
        if cleanup_mask is not None and not isinstance(exc, Interrupted):
            signal.pthread_sigmask(signal.SIG_SETMASK, cleanup_mask)
        raise


def _git_env(home: Path) -> Dict[str, str]:
    return {
        "HOME": str(home),
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": "/usr/bin:/bin",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_AUTHOR_NAME": "agy-worker conformance",
        "GIT_AUTHOR_EMAIL": "conformance@example.invalid",
        "GIT_COMMITTER_NAME": "agy-worker conformance",
        "GIT_COMMITTER_EMAIL": "conformance@example.invalid",
    }


def _git(repo: Path, home: Path, *args: str) -> bytes:
    returncode, stdout, _stderr = _run_bounded(
        (GIT, "-C", str(repo), *args),
        cwd=repo,
        env=_git_env(home),
        timeout=GIT_TIMEOUT_SECONDS,
        stdout_limit=GIT_OUTPUT_LIMIT,
        stderr_limit=GIT_OUTPUT_LIMIT,
    )
    if returncode != 0:
        raise ConformanceError("fixture Git setup failed")
    return stdout


def _write_bound_file(version_root: Path, repo: Path, entry: Dict[str, str]) -> None:
    source = version_root / entry["source"]
    data = _read_regular(source, 1048576)
    if _sha256(data) != entry["sha256"]:
        raise ConformanceError("fixture source changed")
    destination = repo / entry["path"]
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if destination.exists() or destination.is_symlink():
        raise ConformanceError("fixture destination collision")
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        remaining = memoryview(data)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise ConformanceError("fixture write failed")
            remaining = remaining[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _prepare_fixture(
    version_root: Path, workspace: Path, home: Path, fixture: Dict[str, Any]
) -> Tuple[Path, str, Path]:
    repo = workspace / fixture["id"]
    repo.mkdir(mode=0o700)
    _git(repo, home, "init", "-q")
    for entry in fixture["base"]:
        _write_bound_file(version_root, repo, entry)
    _git(repo, home, "add", "-A")
    _git(repo, home, "commit", "-qm", "conformance base")
    base = _git(repo, home, "rev-parse", "HEAD").decode("ascii").strip()
    if len(base) not in (40, 64) or any(char not in "0123456789abcdef" for char in base):
        raise ConformanceError("fixture base is not immutable")

    base_paths = {entry["path"] for entry in fixture["base"]}
    candidate_paths = {entry["path"] for entry in fixture["candidate"]}
    for relative in sorted(base_paths - candidate_paths, reverse=True):
        path = repo / relative
        path.unlink()
    for entry in fixture["candidate"]:
        path = repo / entry["path"]
        if path.exists() and not path.is_symlink():
            path.unlink()
        _write_bound_file(version_root, repo, entry)

    envelope_source = version_root / fixture["envelope"]
    envelope_data = _read_regular(envelope_source, 65536)
    if _sha256(envelope_data) != fixture["envelope_sha256"]:
        raise ConformanceError("fixture envelope changed")
    envelope_path = workspace / (fixture["id"] + ".envelope.json")
    descriptor = os.open(envelope_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        remaining = memoryview(envelope_data)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise ConformanceError("envelope write failed")
            remaining = remaining[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return repo, base, envelope_path


def _verifier(kind: str, repo: Path) -> Optional[str]:
    quoted = shlex.quote(str(repo / "proof.txt"))
    if kind == "none":
        return None
    if kind == "fail":
        return "/usr/bin/false"
    if kind == "mutate":
        return (
            "/usr/bin/python3 -I -S -B -c "
            + shlex.quote(
                "from pathlib import Path; import sys; p=Path(sys.argv[1]); "
                "p.write_bytes(p.read_bytes()+b'verifier mutation\\n')"
            )
            + " "
            + quoted
        )
    if kind == "content-match":
        return (
            "/usr/bin/python3 -I -S -B -c "
            + shlex.quote(
                "from pathlib import Path; import sys; "
                "raise SystemExit(0 if Path(sys.argv[1]).read_bytes() in "
                "(b'original synthetic value\\n',b'verified synthetic change\\n') else 1)"
            )
            + " "
            + quoted
        )
    raise ConformanceError("unknown verifier kind")


def _gate_argv(
    gate: Path,
    fixture: Dict[str, Any],
    repo: Path,
    base: str,
    envelope: Path,
) -> List[str]:
    argv = [
        str(gate),
        "--envelope",
        str(envelope),
        "--repo",
        str(repo),
        "--base",
        base if fixture["base_argument"] == "immutable" else "HEAD",
    ]
    for pattern in fixture["allow"]:
        argv.extend(("--allow", pattern))
    for pattern in fixture["only"]:
        argv.extend(("--only", pattern))
    if fixture["expect_edits"]:
        argv.append("--expect-edits")
    verifier = _verifier(fixture["verifier"], repo)
    if verifier is not None:
        argv.extend(("--verify", verifier))
    return argv


def _gate_env(workspace: Path) -> Dict[str, str]:
    home = workspace / "home"
    return {
        "HOME": str(home),
        "TMPDIR": str(workspace / "tmp"),
        "LANG": "C",
        "LC_ALL": "C",
        "NO_COLOR": "1",
        "PATH": "/usr/bin:/bin",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
    }


def _resolve_gate(raw: str) -> Path:
    if not raw or "\x00" in raw:
        raise ConformanceError("gate path is invalid")
    try:
        path = Path(raw).expanduser().resolve(strict=True)
        info = path.stat()
    except OSError as exc:
        raise ConformanceError("gate path is unavailable") from exc
    if not path.is_absolute() or not stat.S_ISREG(info.st_mode) or not os.access(path, os.X_OK):
        raise ConformanceError("gate path is not an executable regular file")
    return path


def _parse_args(argv: Sequence[str]) -> Path:
    if len(argv) != 2 or argv[0] != "--gate":
        raise ConformanceError("usage")
    return _resolve_gate(argv[1])


def _directory_identity(path: Path) -> Tuple[int, int, int, int]:
    information = path.lstat()
    if not stat.S_ISDIR(information.st_mode) or stat.S_ISLNK(information.st_mode):
        raise ConformanceError("workspace directory identity is invalid")
    return (
        information.st_dev,
        information.st_ino,
        information.st_uid,
        stat.S_IMODE(information.st_mode),
    )


def _require_directory_identity(
    path: Path, expected: Tuple[int, int, int, int]
) -> None:
    try:
        observed = _directory_identity(path)
    except OSError as exc:
        raise ConformanceError("workspace directory identity changed") from exc
    if observed != expected:
        raise ConformanceError("workspace directory identity changed")


def _stat_identity(information: os.stat_result) -> Tuple[int, int, int, int]:
    return (
        information.st_dev,
        information.st_ino,
        information.st_uid,
        stat.S_IMODE(information.st_mode),
    )


def _clear_directory_fd(
    descriptor: int,
    root_device: int,
    budget: Dict[str, float],
    depth: int = 0,
) -> bool:
    if depth > CLEANUP_MAX_DEPTH or time.monotonic() > budget["deadline"]:
        return False
    try:
        with os.scandir(descriptor) as entries:
            for entry in entries:
                if time.monotonic() > budget["deadline"]:
                    return False
                name = entry.name
                if not name or name in (".", "..") or "/" in name or "\x00" in name:
                    return False
                information = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
                budget["entries"] += 1
                budget["bytes"] += max(0, information.st_size)
                if (
                    budget["entries"] > CLEANUP_MAX_ENTRIES
                    or budget["bytes"] > CLEANUP_MAX_BYTES
                ):
                    return False
                expected = _stat_identity(information)
                if stat.S_ISDIR(information.st_mode):
                    if information.st_dev != root_device:
                        return False
                    child = os.open(name, OPEN_DIRECTORY_FLAGS, dir_fd=descriptor)
                    try:
                        os.set_inheritable(child, False)
                        opened = os.fstat(child)
                        if (
                            not stat.S_ISDIR(opened.st_mode)
                            or _stat_identity(opened) != expected
                            or not _clear_directory_fd(
                                child, root_device, budget, depth + 1
                            )
                        ):
                            return False
                    finally:
                        os.close(child)
                    current = os.stat(
                        name, dir_fd=descriptor, follow_symlinks=False
                    )
                    if _stat_identity(current) != expected:
                        return False
                    os.rmdir(name, dir_fd=descriptor)
                else:
                    current = os.stat(
                        name, dir_fd=descriptor, follow_symlinks=False
                    )
                    if _stat_identity(current) != expected:
                        return False
                    os.unlink(name, dir_fd=descriptor)
        os.fsync(descriptor)
    except OSError:
        return False
    return True


def _cleanup_active_workspace(test_after_precheck=None) -> bool:
    global ACTIVE_WORKSPACE
    global ACTIVE_WORKSPACE_IDENTITY
    global ACTIVE_WORKSPACE_PARENT_IDENTITY
    global ACTIVE_WORKSPACE_FD
    global ACTIVE_WORKSPACE_PARENT_FD
    if (
        ACTIVE_WORKSPACE is None
        or ACTIVE_WORKSPACE_IDENTITY is None
        or ACTIVE_WORKSPACE_PARENT_IDENTITY is None
        or ACTIVE_WORKSPACE_FD is None
        or ACTIVE_WORKSPACE_PARENT_FD is None
    ):
        return ACTIVE_WORKSPACE is None
    workspace = ACTIVE_WORKSPACE
    parent = workspace.parent
    try:
        if (
            _directory_identity(parent) != ACTIVE_WORKSPACE_PARENT_IDENTITY
            or _stat_identity(os.fstat(ACTIVE_WORKSPACE_PARENT_FD))
            != ACTIVE_WORKSPACE_PARENT_IDENTITY
            or _stat_identity(
                os.stat(
                    workspace.name,
                    dir_fd=ACTIVE_WORKSPACE_PARENT_FD,
                    follow_symlinks=False,
                )
            )
            != ACTIVE_WORKSPACE_IDENTITY
            or _stat_identity(os.fstat(ACTIVE_WORKSPACE_FD))
            != ACTIVE_WORKSPACE_IDENTITY
        ):
            return False
        if test_after_precheck is not None:
            test_after_precheck()
        budget = {
            "entries": 0.0,
            "bytes": 0.0,
            "deadline": time.monotonic() + CLEANUP_TIMEOUT_SECONDS,
        }
        if not _clear_directory_fd(
            ACTIVE_WORKSPACE_FD, ACTIVE_WORKSPACE_IDENTITY[0], budget
        ):
            return False
        if (
            _directory_identity(parent) != ACTIVE_WORKSPACE_PARENT_IDENTITY
            or _stat_identity(os.fstat(ACTIVE_WORKSPACE_PARENT_FD))
            != ACTIVE_WORKSPACE_PARENT_IDENTITY
            or _stat_identity(os.fstat(ACTIVE_WORKSPACE_FD))
            != ACTIVE_WORKSPACE_IDENTITY
            or _stat_identity(
                os.stat(
                    workspace.name,
                    dir_fd=ACTIVE_WORKSPACE_PARENT_FD,
                    follow_symlinks=False,
                )
            )
            != ACTIVE_WORKSPACE_IDENTITY
        ):
            return False
        os.rmdir(workspace.name, dir_fd=ACTIVE_WORKSPACE_PARENT_FD)
        os.fsync(ACTIVE_WORKSPACE_PARENT_FD)
    except (OSError, ConformanceError):
        return False
    os.close(ACTIVE_WORKSPACE_FD)
    os.close(ACTIVE_WORKSPACE_PARENT_FD)
    ACTIVE_WORKSPACE = None
    ACTIVE_WORKSPACE_IDENTITY = None
    ACTIVE_WORKSPACE_PARENT_IDENTITY = None
    ACTIVE_WORKSPACE_FD = None
    ACTIVE_WORKSPACE_PARENT_FD = None
    return True


def _private_workspace() -> Path:
    global ACTIVE_WORKSPACE
    global ACTIVE_WORKSPACE_IDENTITY
    global ACTIVE_WORKSPACE_PARENT_IDENTITY
    global ACTIVE_WORKSPACE_FD
    global ACTIVE_WORKSPACE_PARENT_FD
    seen = set()
    for candidate in ("/private/tmp", "/tmp"):
        try:
            base = Path(candidate).resolve(strict=True)
            info = base.stat()
        except OSError:
            continue
        if base in seen:
            continue
        seen.add(base)
        if not stat.S_ISDIR(info.st_mode) or not os.access(base, os.W_OK):
            continue
        creation_mask = None
        if hasattr(signal, "pthread_sigmask"):
            creation_mask = signal.pthread_sigmask(signal.SIG_BLOCK, LIFECYCLE_SIGNALS)
        parent_descriptor = None
        root_descriptor = None
        old_umask = os.umask(0o077)
        root = None
        try:
            parent_descriptor = os.open(base, OPEN_DIRECTORY_FLAGS)
            os.set_inheritable(parent_descriptor, False)
            if _stat_identity(os.fstat(parent_descriptor)) != _directory_identity(base):
                raise ConformanceError("workspace parent identity changed")
            root = Path(tempfile.mkdtemp(prefix="agy-worker-conformance.", dir=str(base)))
            os.chmod(root, 0o700)
            current = root.stat()
            if (
                root.resolve() != root
                or current.st_uid != os.getuid()
                or stat.S_IMODE(current.st_mode) != 0o700
            ):
                try:
                    os.rmdir(root.name, dir_fd=parent_descriptor)
                    os.fsync(parent_descriptor)
                except OSError:
                    pass
                continue
            root_descriptor = os.open(
                root.name, OPEN_DIRECTORY_FLAGS, dir_fd=parent_descriptor
            )
            os.set_inheritable(root_descriptor, False)
            if _stat_identity(os.fstat(root_descriptor)) != _directory_identity(root):
                raise ConformanceError("workspace root identity changed")
            for name in ("home", "tmp"):
                os.mkdir(name, mode=0o700, dir_fd=root_descriptor)
            ACTIVE_WORKSPACE = root
            ACTIVE_WORKSPACE_IDENTITY = _stat_identity(os.fstat(root_descriptor))
            ACTIVE_WORKSPACE_PARENT_IDENTITY = _stat_identity(
                os.fstat(parent_descriptor)
            )
            ACTIVE_WORKSPACE_FD = root_descriptor
            ACTIVE_WORKSPACE_PARENT_FD = parent_descriptor
            root_descriptor = None
            parent_descriptor = None
        except (OSError, ConformanceError):
            if root_descriptor is not None:
                try:
                    for name in ("tmp", "home"):
                        try:
                            os.rmdir(name, dir_fd=root_descriptor)
                        except OSError:
                            pass
                finally:
                    os.close(root_descriptor)
            if root is not None and parent_descriptor is not None:
                try:
                    os.rmdir(root.name, dir_fd=parent_descriptor)
                except OSError:
                    pass
            if parent_descriptor is not None:
                os.close(parent_descriptor)
            ACTIVE_WORKSPACE = None
            ACTIVE_WORKSPACE_IDENTITY = None
            ACTIVE_WORKSPACE_PARENT_IDENTITY = None
            ACTIVE_WORKSPACE_FD = None
            ACTIVE_WORKSPACE_PARENT_FD = None
            continue
        finally:
            os.umask(old_umask)
            if creation_mask is not None:
                signal.pthread_sigmask(signal.SIG_SETMASK, creation_mask)
        assert root is not None
        return root
    raise ConformanceError("private workspace is unavailable")


def _run(argv: Sequence[str]) -> int:
    global ACTIVE_WORKSPACE
    gate = _parse_args(argv)
    version_root = Path(__file__).resolve().parent
    if Path(__file__).is_symlink() or version_root.resolve() != version_root:
        raise ConformanceError("conformance runtime path is not canonical")
    manifest = _validate_manifest(version_root)
    if not Path(GIT).is_file() or not os.access(GIT, os.X_OK):
        raise ConformanceError("required Git executable is unavailable")
    workspace = _private_workspace()
    assert ACTIVE_WORKSPACE_IDENTITY is not None
    workspace_identity = ACTIVE_WORKSPACE_IDENTITY
    try:
        home = workspace / "home"
        for fixture in manifest["fixtures"]:
            repo, base, envelope = _prepare_fixture(
                version_root, workspace, home, fixture
            )
            repo_identity = _directory_identity(repo)
            returncode, _stdout, _stderr = _run_bounded(
                _gate_argv(gate, fixture, repo, base, envelope),
                cwd=workspace,
                env=_gate_env(workspace),
                timeout=float(manifest["limits"]["gate_timeout_seconds"]),
                stdout_limit=manifest["limits"]["gate_stdout_bytes"],
                stderr_limit=manifest["limits"]["gate_stderr_bytes"],
            )
            _require_directory_identity(workspace, workspace_identity)
            _require_directory_identity(repo, repo_identity)
            if returncode != fixture["expected_exit"]:
                print(
                    "conformance: fixture %s expected exit %d, observed %d"
                    % (fixture["id"], fixture["expected_exit"], returncode),
                    file=sys.stderr,
                )
                return 1
    finally:
        cleanup_mask = None
        if hasattr(signal, "pthread_sigmask"):
            cleanup_mask = signal.pthread_sigmask(signal.SIG_BLOCK, LIFECYCLE_SIGNALS)
        try:
            if not _cleanup_active_workspace():
                raise ConformanceError("workspace cleanup could not be proven")
        finally:
            if cleanup_mask is not None:
                signal.pthread_sigmask(signal.SIG_SETMASK, cleanup_mask)
    print(
        "CONFORMANCE_RESULT version=v1 fixtures=%d status=passed"
        % len(manifest["fixtures"])
    )
    return 0


def main(argv: Sequence[str]) -> int:
    global FIRST_SIGNAL
    FIRST_SIGNAL = None
    old_handlers = {item: signal.getsignal(item) for item in LIFECYCLE_SIGNALS}
    for item in LIFECYCLE_SIGNALS:
        signal.signal(item, _signal_handler)
    try:
        return _run(argv)
    except Interrupted as exc:
        if hasattr(signal, "pthread_sigmask"):
            signal.pthread_sigmask(signal.SIG_BLOCK, LIFECYCLE_SIGNALS)
        try:
            if ACTIVE_PROCESS is not None:
                _close_process_group(ACTIVE_PROCESS, exc.signum)
            if ACTIVE_WORKSPACE is not None and not _cleanup_active_workspace():
                raise ConformanceError("workspace cleanup could not be proven")
        except BaseException:
            print("conformance: failed closed", file=sys.stderr)
            return 2
        print("conformance: interrupted", file=sys.stderr)
        return 128 + exc.signum
    except ConformanceError as exc:
        if str(exc) == "usage":
            return 64
        print("conformance: failed closed", file=sys.stderr)
        return 2
    except BaseException:
        sys.stderr.write("conformance: failed closed\n")
        return 2
    finally:
        for item, handler in old_handlers.items():
            signal.signal(item, handler)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
