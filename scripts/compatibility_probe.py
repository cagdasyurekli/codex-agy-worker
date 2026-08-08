#!/usr/bin/env python3
"""Run fixed compatibility probes under hard process, time, and byte bounds."""

from __future__ import annotations

import os
import re
import selectors
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional, Sequence


sys.dont_write_bytecode = True

SCRIPT_DIR = Path(__file__).resolve().parent
OFFICIAL_HELPER = SCRIPT_DIR / "official_github.py"
SEMVER_PATTERN = r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
VERSION_PATTERNS = {
    "agy": re.compile(rf"(?:agy\s+)?({SEMVER_PATTERN})"),
    "codex": re.compile(rf"codex-cli\s+({SEMVER_PATTERN})"),
}
SIGNALS = (signal.SIGHUP, signal.SIGINT, signal.SIGTERM)


@dataclass(frozen=True)
class Limits:
    timeout: float
    stdout: int
    stderr: int


OFFICIAL_LIMITS = Limits(timeout=20.0, stdout=8 * 1024, stderr=8 * 1024)
VERSION_LIMITS = Limits(timeout=3.0, stdout=128, stderr=128)
TERM_GRACE_SECONDS = 0.25
KILL_GRACE_SECONDS = 0.75


class ProbeError(ValueError):
    """A compatibility probe failed without exposing child output."""


class ProbeInterrupted(BaseException):
    """A terminal signal interrupted an active probe."""

    def __init__(self, signum: int):
        super().__init__(signum)
        self.signum = signum


def scrubbed_environment(source: Optional[Mapping[str, str]] = None) -> dict[str, str]:
    """Remove ambient network, Git transport, pager, and Python startup controls."""

    environment = dict(os.environ if source is None else source)
    exact = {
        "ALL_PROXY",
        "GIT_ASKPASS",
        "GIT_CONFIG",
        "GIT_CONFIG_COUNT",
        "GIT_CONFIG_GLOBAL",
        "GIT_CONFIG_NOSYSTEM",
        "GIT_CONFIG_PARAMETERS",
        "GIT_CONFIG_SYSTEM",
        "GIT_PAGER",
        "GIT_PROXY_COMMAND",
        "GIT_SSH",
        "GIT_SSH_COMMAND",
        "GH_PAGER",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "PAGER",
        "PYTHONBREAKPOINT",
        "PYTHONHOME",
        "PYTHONINSPECT",
        "PYTHONPATH",
        "PYTHONSTARTUP",
        "SSH_ASKPASS",
        "all_proxy",
        "http_proxy",
        "https_proxy",
        "no_proxy",
    }
    prefixes = ("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_", "DYLD_", "LD_PRELOAD")
    for key in list(environment):
        if key in exact or key.startswith(prefixes):
            environment.pop(key, None)
    environment["NO_COLOR"] = "1"
    environment["TERM"] = "dumb"
    return environment


def _group_exists(pgid: int) -> bool:
    """Observe one exact process group without exposing or signalling another group."""

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


def terminate_group(process: subprocess.Popen[bytes], signum: int = signal.SIGTERM) -> None:
    """Terminate the exact reserved child group, then boundedly prove its absence."""

    pgid = process.pid

    try:
        os.killpg(pgid, signum)
    except (ProcessLookupError, PermissionError):
        pass
    grace_deadline = time.monotonic() + TERM_GRACE_SECONDS
    while _group_exists(pgid) and time.monotonic() < grace_deadline:
        time.sleep(0.01)
    if _group_exists(pgid):
        try:
            os.killpg(pgid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    try:
        process.wait(timeout=KILL_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        raise ProbeError("probe process group cleanup failed") from None
    # All signals happen before reaping the reserved group leader. After reap, only
    # observe: never signal a PGID that the kernel could subsequently reuse.
    if not _wait_group_absent(pgid, KILL_GRACE_SECONDS):
        raise ProbeError("probe process group cleanup failed")


def run_bounded(
    argv: Sequence[str],
    limits: Limits,
    *,
    environment: Optional[Mapping[str, str]] = None,
    cwd: Optional[Path] = None,
) -> tuple[bytes, bytes]:
    """Capture both streams incrementally and fail closed on any exceeded bound."""

    process: Optional[subprocess.Popen[bytes]] = None
    cleanup_required = False
    previous_handlers = {signum: signal.getsignal(signum) for signum in SIGNALS}
    entry_mask = (
        signal.pthread_sigmask(signal.SIG_BLOCK, [])
        if hasattr(signal, "pthread_sigmask")
        else None
    )

    def handle_signal(signum: int, _frame: object) -> None:
        raise ProbeInterrupted(signum)

    try:
        for signum in SIGNALS:
            signal.signal(signum, handle_signal)
        if hasattr(signal, "pthread_sigmask"):
            popen_mask = signal.pthread_sigmask(signal.SIG_BLOCK, SIGNALS)
        try:
            process = subprocess.Popen(
                list(argv),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=None if cwd is None else str(cwd),
                env=scrubbed_environment(environment),
                start_new_session=True,
            )
            cleanup_required = True
        except OSError as exc:
            raise ProbeError("probe could not start") from exc
        finally:
            if hasattr(signal, "pthread_sigmask"):
                signal.pthread_sigmask(signal.SIG_SETMASK, popen_mask)

        assert process.stdout is not None and process.stderr is not None
        stdout_descriptor = process.stdout.fileno()
        stderr_descriptor = process.stderr.fileno()
        streams = {
            stdout_descriptor: (process.stdout, limits.stdout, bytearray()),
            stderr_descriptor: (process.stderr, limits.stderr, bytearray()),
        }
        for descriptor in streams:
            os.set_blocking(descriptor, False)
        deadline = time.monotonic() + limits.timeout
        with selectors.DefaultSelector() as selector:
            for descriptor in streams:
                selector.register(descriptor, selectors.EVENT_READ)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ProbeError("probe timed out")
                events = selector.select(min(remaining, 0.10))
                for key, _mask in events:
                    descriptor = key.fd
                    stream, limit, captured = streams[descriptor]
                    try:
                        chunk = os.read(descriptor, min(8192, limit + 1 - len(captured)))
                    except BlockingIOError:
                        continue
                    if not chunk:
                        selector.unregister(descriptor)
                        stream.close()
                        continue
                    captured.extend(chunk)
                    if len(captured) > limit:
                        raise ProbeError("probe output exceeded its bound")
        if hasattr(signal, "pthread_sigmask"):
            wait_mask = signal.pthread_sigmask(signal.SIG_BLOCK, SIGNALS)
        try:
            try:
                returncode = process.wait(timeout=max(0.0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired as exc:
                raise ProbeError("probe timed out") from exc
            # Keep terminal signals blocked until the reaped leader can no longer
            # authorize cleanup of its former (and now reusable) process-group ID.
            cleanup_required = False
        finally:
            if hasattr(signal, "pthread_sigmask"):
                signal.pthread_sigmask(signal.SIG_SETMASK, wait_mask)
        if returncode != 0:
            raise ProbeError("probe command failed")
        return bytes(streams[stdout_descriptor][2]), bytes(streams[stderr_descriptor][2])
    except ProbeInterrupted:
        if process is not None and cleanup_required:
            terminate_group(process)
        raise
    except BaseException:
        if process is not None and cleanup_required:
            terminate_group(process)
        raise
    finally:
        if hasattr(signal, "pthread_sigmask") and entry_mask is not None:
            signal.pthread_sigmask(signal.SIG_SETMASK, entry_mask)
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)


def _version_argv(tool: str) -> list[str]:
    executable = shutil.which(tool)
    if executable is None:
        raise ProbeError("installed tool is unavailable")
    return [executable, "--version"]


def _parse_version(tool: str, raw: bytes) -> str:
    if not raw.endswith(b"\n") or raw.count(b"\n") != 1 or b"\x00" in raw:
        raise ProbeError("version output is malformed")
    try:
        line = raw[:-1].decode("ascii", "strict")
    except UnicodeDecodeError as exc:
        raise ProbeError("version output is malformed") from exc
    match = VERSION_PATTERNS[tool].fullmatch(line)
    if match is None:
        raise ProbeError("version output lacks documented semantic content")
    return match.group(1)


def capture_profile(profile: str, argument: Optional[str] = None) -> bytes:
    """Run one fixed production profile and return only validated canonical stdout."""

    if profile in ("agy-version", "codex-version"):
        if argument is not None:
            raise ProbeError("version profile does not accept an argument")
        tool = profile[:-8]
        stdout, _stderr = run_bounded(_version_argv(tool), VERSION_LIMITS)
        return (_parse_version(tool, stdout) + "\n").encode("ascii")

    if profile in ("official-project", "official-agy", "official-codex"):
        if argument is not None:
            raise ProbeError("latest-evidence profile does not accept an argument")
        tool = profile[len("official-") :]
        argv = [
            sys.executable,
            "-I",
            "-B",
            str(OFFICIAL_HELPER),
            "--latest",
            tool,
        ]
    elif profile == "official-project-release":
        if argument is None or re.fullmatch(rf"v{SEMVER_PATTERN}", argument) is None:
            raise ProbeError("project release profile requires one stable tag")
        argv = [
            sys.executable,
            "-I",
            "-B",
            str(OFFICIAL_HELPER),
            "--project-release",
            argument,
        ]
    else:
        raise ProbeError("unknown probe profile")
    stdout, _stderr = run_bounded(argv, OFFICIAL_LIMITS)
    if not stdout.endswith(b"\n") or stdout.count(b"\n") != 1 or b"\x00" in stdout:
        raise ProbeError("official evidence output is malformed")
    try:
        fields = stdout[:-1].decode("ascii", "strict").split("\t")
    except UnicodeDecodeError as exc:
        raise ProbeError("official evidence output is malformed") from exc
    if len(fields) != 3 or any(not field for field in fields):
        raise ProbeError("official evidence output is malformed")
    return stdout


def main(argv: list[str]) -> int:
    if len(argv) not in (2, 3):
        print("compatibility probe: evidence unavailable", file=sys.stderr)
        return 2
    try:
        payload = capture_profile(argv[1], argv[2] if len(argv) == 3 else None)
    except ProbeInterrupted as exc:
        return 128 + exc.signum
    except (ProbeError, OSError, subprocess.SubprocessError):
        print("compatibility probe: evidence unavailable", file=sys.stderr)
        return 2
    sys.stdout.buffer.write(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
