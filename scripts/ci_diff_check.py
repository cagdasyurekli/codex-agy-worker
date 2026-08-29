#!/usr/bin/env python3
"""Attribute-independent whitespace checks for an exact committed CI range."""

from __future__ import annotations

import os
import re
import selectors
import signal
import subprocess
import sys
import time
from pathlib import Path


GIT = "/usr/bin/git"
ZERO_SHA = "0" * 40
SHA_RE = re.compile(r"[0-9a-f]{40}\Z")
MAX_METADATA_BYTES = 1024 * 1024
MAX_PATHS = 1024
MAX_BLOB_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BLOB_BYTES = 16 * 1024 * 1024
MAX_LINES = 6_000
MAX_BATCH_HEADER_BYTES = 128
MAX_BATCH_STDERR_BYTES = 8 * 1024
TIMEOUT_SECONDS = 10
TOTAL_TIMEOUT_SECONDS = 30
REGULAR_MODES = {"100644", "100755"}
CONFLICT_MARKERS = (b"<<<<<<<", b"=======", b">>>>>>>")


class CheckRejected(Exception):
    pass


class BatchInterrupted(BaseException):
    def __init__(self, signum: int) -> None:
        self.signum = signum


def _git_environment() -> dict[str, str]:
    return {
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "HOME": "/",
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": "/usr/bin:/bin",
        "TMPDIR": "/tmp",
    }


def _terminate_group(process: subprocess.Popen[bytes]) -> None:
    if process.returncode is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except (OSError, ProcessLookupError):
        pass
    time.sleep(0.2)
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        pass
    try:
        process.wait(timeout=0.8)
    except subprocess.TimeoutExpired as exc:
        raise CheckRejected from exc


def _git(
    *arguments: str,
    limit: int = MAX_METADATA_BYTES,
    overall_deadline: float | None = None,
) -> bytes:
    process = None
    try:
        process = subprocess.Popen(
            [GIT, *arguments],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=_git_environment(),
            start_new_session=True,
        )
        if process.stdout is None:
            raise CheckRejected
        descriptor = process.stdout.fileno()
        os.set_blocking(descriptor, False)
        output = bytearray()
        deadline = time.monotonic() + TIMEOUT_SECONDS
        if overall_deadline is not None:
            deadline = min(deadline, overall_deadline)
        with selectors.DefaultSelector() as selector:
            selector.register(descriptor, selectors.EVENT_READ)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise CheckRejected
                events = selector.select(min(remaining, 0.1))
                for key, _mask in events:
                    block = os.read(key.fd, min(65536, limit + 1 - len(output)))
                    if not block:
                        selector.unregister(key.fd)
                        process.stdout.close()
                        continue
                    output.extend(block)
                    if len(output) > limit:
                        raise CheckRejected
        remaining = deadline - time.monotonic()
        if remaining <= 0 or process.wait(timeout=remaining) != 0:
            raise CheckRejected
        return bytes(output)
    except (OSError, subprocess.SubprocessError, CheckRejected) as exc:
        if process is not None and process.returncode is None:
            try:
                _terminate_group(process)
            except CheckRejected:
                pass
        raise CheckRejected from exc


def _commit_exists(value: str, deadline: float) -> None:
    _git(
        "cat-file", "-e", value + "^{commit}", limit=0,
        overall_deadline=deadline,
    )


def _one_sha(data: bytes) -> str:
    try:
        value = data.decode("ascii").strip()
    except UnicodeDecodeError as exc:
        raise CheckRejected from exc
    if not SHA_RE.fullmatch(value):
        raise CheckRejected
    return value


def _comparison(
    event: str, base: str, head: str, deadline: float
) -> tuple[str, str]:
    if not SHA_RE.fullmatch(base) or not SHA_RE.fullmatch(head) or head == ZERO_SHA:
        raise CheckRejected
    _commit_exists(head, deadline)
    if event == "pull_request":
        if base == ZERO_SHA:
            raise CheckRejected
        _commit_exists(base, deadline)
        _git(
            "diff", "--check", "--no-ext-diff", "--no-textconv",
            base + "..." + head, "--", overall_deadline=deadline
        )
        merge_base = _one_sha(
            _git("merge-base", base, head, limit=128, overall_deadline=deadline)
        )
        return merge_base, head
    if event == "push":
        if base == ZERO_SHA:
            empty_tree = _one_sha(
                _git(
                    "hash-object", "-t", "tree", "/dev/null", limit=128,
                    overall_deadline=deadline,
                )
            )
            _git(
                "diff", "--check", "--no-ext-diff", "--no-textconv",
                empty_tree + ".." + head, "--", overall_deadline=deadline
            )
            return empty_tree, head
        _commit_exists(base, deadline)
        _git(
            "diff", "--check", "--no-ext-diff", "--no-textconv",
            base + ".." + head, "--", overall_deadline=deadline
        )
        return base, head
    raise CheckRejected


def _raw_changes(
    base: str, head: str, deadline: float
) -> list[tuple[str, str, str, str, str]]:
    data = _git(
        "diff-tree",
        "--no-commit-id",
        "-r",
        "--raw",
        "-z",
        "--full-index",
        "--no-renames",
        base,
        head,
        "--",
        overall_deadline=deadline,
    )
    fields = data.split(b"\0")
    if fields[-1] != b"":
        raise CheckRejected
    fields.pop()
    if len(fields) % 2 or len(fields) // 2 > MAX_PATHS:
        raise CheckRejected
    changes: list[tuple[str, str, str, str, str]] = []
    for offset in range(0, len(fields), 2):
        header, path_bytes = fields[offset : offset + 2]
        try:
            header_text = header.decode("ascii")
            path = path_bytes.decode("utf-8", "strict")
        except UnicodeDecodeError as exc:
            raise CheckRejected from exc
        parts = header_text.split(" ")
        if (
            len(parts) != 5
            or not parts[0].startswith(":")
            or parts[4] not in {"A", "D", "M", "T"}
            or not path
            or "\x00" in path
        ):
            raise CheckRejected
        old_mode = parts[0][1:]
        new_mode, old_sha, new_sha, status = parts[1:]
        if not SHA_RE.fullmatch(old_sha) or not SHA_RE.fullmatch(new_sha):
            raise CheckRejected
        if status != "A" and old_mode not in REGULAR_MODES:
            raise CheckRejected
        if status != "D" and new_mode not in REGULAR_MODES:
            raise CheckRejected
        changes.append((status, old_sha, new_sha, old_mode, new_mode))
    return changes


def _validate_blob(data: bytes) -> None:
    if b"\x00" in data:
        raise CheckRejected
    try:
        data.decode("utf-8", "strict")
    except UnicodeDecodeError as exc:
        raise CheckRejected from exc
    if any(byte < 0x20 and byte not in {0x09, 0x0A, 0x0D} for byte in data):
        raise CheckRejected


def _batch_blobs(values: list[str], overall_deadline: float) -> dict[str, bytes]:
    requested = list(dict.fromkeys(values))
    if (
        not requested
        or len(requested) > MAX_PATHS
        or any(not SHA_RE.fullmatch(value) for value in requested)
    ):
        raise CheckRejected
    stdin_data = "".join(value + "\n" for value in requested).encode("ascii")
    stdout_limit = (
        MAX_TOTAL_BLOB_BYTES
        + len(requested) * (MAX_BATCH_HEADER_BYTES + 1)
    )
    process = None
    lifecycle_signals = (signal.SIGHUP, signal.SIGINT, signal.SIGTERM)
    old_mask = None
    if hasattr(signal, "pthread_sigmask"):
        old_mask = signal.pthread_sigmask(signal.SIG_BLOCK, lifecycle_signals)
    old_handlers = {
        signum: signal.getsignal(signum)
        for signum in lifecycle_signals
    }

    def interrupted(signum: int, _frame: object) -> None:
        raise BatchInterrupted(signum)

    for signum in old_handlers:
        signal.signal(signum, interrupted)
    try:
        process = subprocess.Popen(
            [GIT, "cat-file", "--batch"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=_git_environment(),
            start_new_session=True,
        )
        if process.stdin is None or process.stdout is None or process.stderr is None:
            raise CheckRejected
        if hasattr(signal, "pthread_sigmask") and old_mask is not None:
            signal.pthread_sigmask(signal.SIG_SETMASK, old_mask)
        batch_deadline = min(
            time.monotonic() + TIMEOUT_SECONDS, overall_deadline
        )
        stdin_fd = process.stdin.fileno()
        stdout_fd = process.stdout.fileno()
        stderr_fd = process.stderr.fileno()
        for descriptor in (stdin_fd, stdout_fd, stderr_fd):
            os.set_blocking(descriptor, False)
        input_offset = 0
        output_seen = 0
        stdout_buffer = bytearray()
        stderr_buffer = bytearray()
        result: dict[str, bytes] = {}
        index = 0
        body_size = None

        def parse_available() -> None:
            nonlocal index, body_size
            while True:
                if body_size is None:
                    newline = stdout_buffer.find(b"\n")
                    if newline < 0:
                        if len(stdout_buffer) > MAX_BATCH_HEADER_BYTES:
                            raise CheckRejected
                        return
                    if newline > MAX_BATCH_HEADER_BYTES or index >= len(requested):
                        raise CheckRejected
                    header = bytes(stdout_buffer[:newline])
                    del stdout_buffer[: newline + 1]
                    fields = header.split(b" ")
                    if len(fields) != 3:
                        raise CheckRejected
                    try:
                        object_id = fields[0].decode("ascii")
                        object_type = fields[1].decode("ascii")
                        size_text = fields[2].decode("ascii")
                    except UnicodeDecodeError as exc:
                        raise CheckRejected from exc
                    if (
                        object_id != requested[index]
                        or object_type != "blob"
                        or not size_text.isdigit()
                        or str(int(size_text)) != size_text
                    ):
                        raise CheckRejected
                    body_size = int(size_text)
                    if body_size > MAX_BLOB_BYTES:
                        raise CheckRejected
                if len(stdout_buffer) < body_size + 1:
                    return
                if stdout_buffer[body_size] != 0x0A:
                    raise CheckRejected
                body = bytes(stdout_buffer[:body_size])
                del stdout_buffer[: body_size + 1]
                _validate_blob(body)
                result[requested[index]] = body
                index += 1
                body_size = None

        with selectors.DefaultSelector() as selector:
            selector.register(stdin_fd, selectors.EVENT_WRITE, "stdin")
            selector.register(stdout_fd, selectors.EVENT_READ, "stdout")
            selector.register(stderr_fd, selectors.EVENT_READ, "stderr")
            while selector.get_map():
                remaining = batch_deadline - time.monotonic()
                if remaining <= 0:
                    raise CheckRejected
                events = selector.select(min(remaining, 0.1))
                for key, _mask in events:
                    if key.data == "stdin":
                        try:
                            written = os.write(
                                stdin_fd, stdin_data[input_offset:]
                            )
                        except BrokenPipeError as exc:
                            raise CheckRejected from exc
                        if written <= 0:
                            raise CheckRejected
                        input_offset += written
                        if input_offset == len(stdin_data):
                            selector.unregister(stdin_fd)
                            process.stdin.close()
                    elif key.data == "stdout":
                        capacity = stdout_limit + 1 - output_seen
                        if capacity <= 0:
                            raise CheckRejected
                        block = os.read(stdout_fd, min(65536, capacity))
                        if not block:
                            selector.unregister(stdout_fd)
                            process.stdout.close()
                            continue
                        output_seen += len(block)
                        if output_seen > stdout_limit:
                            raise CheckRejected
                        stdout_buffer.extend(block)
                        parse_available()
                    else:
                        capacity = MAX_BATCH_STDERR_BYTES + 1 - len(stderr_buffer)
                        if capacity <= 0:
                            raise CheckRejected
                        block = os.read(stderr_fd, min(8192, capacity))
                        if not block:
                            selector.unregister(stderr_fd)
                            process.stderr.close()
                            continue
                        stderr_buffer.extend(block)
                        if len(stderr_buffer) > MAX_BATCH_STDERR_BYTES:
                            raise CheckRejected
        remaining = batch_deadline - time.monotonic()
        if (
            remaining <= 0
            or process.wait(timeout=remaining) != 0
            or stderr_buffer
            or input_offset != len(stdin_data)
            or index != len(requested)
            or body_size is not None
            or stdout_buffer
        ):
            raise CheckRejected
        return result
    except BatchInterrupted:
        if hasattr(signal, "pthread_sigmask"):
            signal.pthread_sigmask(signal.SIG_BLOCK, old_handlers)
        if process is not None and process.returncode is None:
            _terminate_group(process)
        raise
    except (OSError, subprocess.SubprocessError, CheckRejected) as exc:
        if hasattr(signal, "pthread_sigmask"):
            signal.pthread_sigmask(signal.SIG_BLOCK, old_handlers)
        if process is not None and process.returncode is None:
            try:
                _terminate_group(process)
            except CheckRejected:
                pass
        raise CheckRejected from exc
    finally:
        for signum, handler in old_handlers.items():
            signal.signal(signum, handler)
        if hasattr(signal, "pthread_sigmask") and old_mask is not None:
            signal.pthread_sigmask(signal.SIG_SETMASK, old_mask)


def _bad_line(line: bytes) -> bool:
    body = line.rstrip(b"\r\n")
    if body.endswith((b" ", b"\t")) or line.endswith(b"\r\n"):
        return True
    if any(body.startswith(marker) for marker in CONFLICT_MARKERS):
        return True
    prefix = body[: len(body) - len(body.lstrip(b" \t"))]
    return b" \t" in prefix


def _check_head_blob(data: bytes) -> None:
    lines = data.splitlines(keepends=True)
    if len(lines) > MAX_LINES:
        raise CheckRejected
    if any(_bad_line(line) for line in lines):
        raise CheckRejected
    if lines and not lines[-1].rstrip(b"\r\n"):
        raise CheckRejected


def check_committed_range(event: str, base: str, head: str) -> None:
    deadline = time.monotonic() + TOTAL_TIMEOUT_SECONDS
    comparison_base, comparison_head = _comparison(event, base, head, deadline)
    changes = _raw_changes(
        comparison_base, comparison_head, deadline
    )
    object_ids = [new_sha for status, _old_sha, new_sha, _old_mode, _new_mode in changes if status != "D"]
    if not object_ids:
        return
    blobs = _batch_blobs(object_ids, deadline)
    total = sum(len(blob) for blob in blobs.values())
    if total > MAX_TOTAL_BLOB_BYTES:
        raise CheckRejected
    for blob in blobs.values():
        _check_head_blob(blob)


def main() -> int:
    if len(sys.argv) != 1 or not Path.cwd().is_dir():
        return 2
    try:
        check_committed_range(
            os.environ.get("AGY_WORKER_CI_EVENT_NAME", ""),
            os.environ.get("AGY_WORKER_CI_BASE_SHA", ""),
            os.environ.get("AGY_WORKER_CI_HEAD_SHA", ""),
        )
    except CheckRejected:
        print("ci diff check: rejected", file=sys.stderr)
        return 2
    except BatchInterrupted as exc:
        return 128 + exc.signum
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
