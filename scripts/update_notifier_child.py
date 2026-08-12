#!/usr/bin/env python3
"""Private child launcher and Python shim for the local update notifier.

The installed copy is also named ``python3``.  In that mode it interposes only on
the repository's compatibility probe so a notifier-owned parent-death pipe and an
explicit completion acknowledgement surround every nested process-group owner.
"""

from __future__ import annotations

import os
import selectors
import signal
import subprocess
import sys
import time
from pathlib import Path

SIGNALS = (signal.SIGHUP, signal.SIGINT, signal.SIGTERM)
REAL_PYTHON = "/usr/bin/python3"
MAX_STREAM = 64 * 1024
WAIT_SECONDS = 90.0
GRACE_SECONDS = 1.0


def unblock_terminal_signals() -> None:
    if hasattr(signal, "pthread_sigmask"):
        signal.pthread_sigmask(signal.SIG_UNBLOCK, SIGNALS)


def _fd_from_environment(name: str) -> int:
    raw = os.environ.get(name, "")
    if not raw.isascii() or not raw.isdigit():
        raise ValueError("missing notifier descriptor")
    descriptor = int(raw)
    os.fstat(descriptor)
    return descriptor


def _group_exists(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _terminate_group(process: subprocess.Popen[bytes]) -> None:
    pgid = process.pid
    try:
        os.killpg(pgid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass
    deadline = time.monotonic() + GRACE_SECONDS
    while _group_exists(pgid) and time.monotonic() < deadline:
        time.sleep(0.01)
    if _group_exists(pgid):
        try:
            os.killpg(pgid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    try:
        process.wait(timeout=GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        raise RuntimeError("nested process group did not close") from None


def _capture_preserved(
    process: subprocess.Popen[bytes], sentinel_fd: int, timeout: float
) -> tuple[int, bytes, bytes]:
    """Bounded capture retaining buffers after descriptors reach EOF."""
    assert process.stdout is not None and process.stderr is not None
    stdout_fd, stderr_fd = process.stdout.fileno(), process.stderr.fileno()
    buffers = {stdout_fd: bytearray(), stderr_fd: bytearray()}
    streams = {stdout_fd: process.stdout, stderr_fd: process.stderr}
    live = set(streams)
    for descriptor in live:
        os.set_blocking(descriptor, False)
    os.set_blocking(sentinel_fd, False)
    deadline = time.monotonic() + timeout
    with selectors.DefaultSelector() as selector:
        selector.register(sentinel_fd, selectors.EVENT_READ, "sentinel")
        for descriptor in live:
            selector.register(descriptor, selectors.EVENT_READ, "stream")
        while live or process.poll() is None:
            if time.monotonic() >= deadline:
                _terminate_group(process)
                raise RuntimeError("child timed out")
            for key, _mask in selector.select(0.10):
                if key.data == "sentinel":
                    try:
                        data = os.read(sentinel_fd, 1)
                    except BlockingIOError:
                        continue
                    if data == b"":
                        _terminate_group(process)
                        raise RuntimeError("notifier parent disappeared")
                    raise RuntimeError("invalid notifier sentinel")
                descriptor = key.fd
                try:
                    chunk = os.read(descriptor, min(8192, MAX_STREAM + 1 - len(buffers[descriptor])))
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(descriptor)
                    streams[descriptor].close()
                    live.remove(descriptor)
                    continue
                buffers[descriptor].extend(chunk)
                if len(buffers[descriptor]) > MAX_STREAM:
                    _terminate_group(process)
                    raise RuntimeError("child output exceeded its bound")
    return process.wait(timeout=GRACE_SECONDS), bytes(buffers[stdout_fd]), bytes(buffers[stderr_fd])


def _capture_without_sentinel(
    process: subprocess.Popen[bytes], timeout: float
) -> tuple[int, bytes, bytes]:
    """Capture the update wrapper; nested shims exclusively own sentinel cleanup."""
    read_fd, write_fd = os.pipe()
    try:
        # A private open writer prevents the generic capture loop from observing EOF.
        return _capture_preserved(process, read_fd, timeout)
    finally:
        os.close(read_fd)
        os.close(write_fd)


def _probe_invocation(argv: list[str]) -> bool:
    if len(argv) < 3 or argv[1] not in ("-I", "-B", "-S"):
        return False
    return any(Path(argument).name == "compatibility_probe.py" for argument in argv[1:5])


def python_shim(argv: list[str]) -> int:
    unblock_terminal_signals()
    if not _probe_invocation(argv):
        os.execve(REAL_PYTHON, [REAL_PYTHON, *argv[1:]], os.environ)
        raise AssertionError("unreachable")

    sentinel_fd = _fd_from_environment("AGY_NOTIFIER_SENTINEL_FD")
    ack_fd = _fd_from_environment("AGY_NOTIFIER_ACK_FD")
    os.write(ack_fd, b"S")
    process = subprocess.Popen(
        [REAL_PYTHON, *argv[1:]],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=os.environ,
        start_new_session=True,
        pass_fds=(sentinel_fd,),
    )
    try:
        returncode, stdout, stderr = _capture_preserved(process, sentinel_fd, WAIT_SECONDS)
    except BaseException:
        if process.poll() is None:
            _terminate_group(process)
        # A means the nested group is closed even on the parent-death path.
        os.write(ack_fd, b"A")
        raise
    # compatibility_probe.py does not return until its own nested group is closed.
    os.write(ack_fd, b"A")
    sys.stdout.buffer.write(stdout)
    sys.stderr.buffer.write(stderr)
    return returncode


def run_update(argv: list[str]) -> int:
    if len(argv) != 10 or argv[1] != "--run":
        return 64
    update, shim_dir, repository, git_dir, sentinel_text, ack_text, output_path, status_path = argv[2:]
    if not all(Path(value).is_absolute() for value in (update, shim_dir, repository, git_dir, output_path, status_path)):
        return 64
    sentinel_fd, ack_fd = int(sentinel_text), int(ack_text)
    unblock_terminal_signals()
    environment = {
        "HOME": os.environ["HOME"],
        "LANG": "C",
        "LC_ALL": "C",
        "NO_COLOR": "1",
        "PATH": f"{shim_dir}:/usr/bin:/bin:/usr/sbin:/sbin",
        "TERM": "dumb",
        "AGY_NOTIFIER_SENTINEL_FD": str(sentinel_fd),
        "AGY_NOTIFIER_ACK_FD": str(ack_fd),
        "GIT_DIR": git_dir,
        "GIT_WORK_TREE": repository,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_ATTR_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
    }
    process = subprocess.Popen(
        ["/bin/bash", update, "check", "--watch"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=environment,
        pass_fds=(sentinel_fd, ack_fd),
    )
    try:
        returncode, stdout, stderr = _capture_without_sentinel(process, WAIT_SECONDS)
    except BaseException:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=GRACE_SECONDS)
        return 70
    for path, payload in ((output_path, stdout), (status_path, str(returncode).encode("ascii") + b"\n")):
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    return 0


def notify(argv: list[str]) -> int:
    if len(argv) != 4 or argv[1] != "--notify":
        return 64
    title, message = argv[2:]
    unblock_terminal_signals()
    script = "on run argv\n display notification (item 2 of argv) with title (item 1 of argv)\nend run"
    completed = subprocess.run(
        ["/usr/bin/osascript", "-e", script, title, message],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
        timeout=10.0,
    )
    return completed.returncode


def scheduled(argv: list[str]) -> int:
    if len(argv) != 4 or argv[1] != "--scheduled":
        return 64
    controller, repository = argv[2:]
    if not Path(controller).is_absolute() or not Path(repository).is_absolute():
        return 64
    unblock_terminal_signals()
    environment = {
        "HOME": os.environ["HOME"],
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "TERM": "dumb",
    }
    os.execve(
        REAL_PYTHON,
        [REAL_PYTHON, "-I", "-S", "-B", controller, "run", "--source", repository],
        environment,
    )
    raise AssertionError("unreachable")


def main(argv: list[str]) -> int:
    if Path(argv[0]).name == "python3":
        return python_shim(argv)
    if len(argv) >= 2 and argv[1] == "--run":
        return run_update(argv)
    if len(argv) >= 2 and argv[1] == "--notify":
        return notify(argv)
    if len(argv) >= 2 and argv[1] == "--scheduled":
        return scheduled(argv)
    return 64


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
