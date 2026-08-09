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
MAX_LINES = 5_000
TIMEOUT_SECONDS = 10
TOTAL_TIMEOUT_SECONDS = 30
REGULAR_MODES = {"100644", "100755"}
CONFLICT_MARKERS = (b"<<<<<<<", b"=======", b">>>>>>>")


class CheckRejected(Exception):
    pass


def _git_environment() -> dict[str, str]:
    return {
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "HOME": "/",
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": "/usr/bin:/bin",
    }


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
                os.killpg(process.pid, signal.SIGTERM)
            except (OSError, ProcessLookupError):
                pass
            try:
                process.wait(timeout=0.2)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except (OSError, ProcessLookupError):
                    pass
                try:
                    process.wait(timeout=0.8)
                except subprocess.TimeoutExpired:
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
        if len(old_sha) != 40 or len(new_sha) != 40:
            raise CheckRejected
        if status != "A" and old_mode not in REGULAR_MODES:
            raise CheckRejected
        if status != "D" and new_mode not in REGULAR_MODES:
            raise CheckRejected
        changes.append((status, old_sha, new_sha, old_mode, new_mode))
    return changes


def _blob(value: str, deadline: float) -> bytes:
    size_bytes = _git(
        "cat-file", "-s", value, limit=64, overall_deadline=deadline
    )
    try:
        size_text = size_bytes.decode("ascii").strip()
    except UnicodeDecodeError as exc:
        raise CheckRejected from exc
    if not size_text.isdigit():
        raise CheckRejected
    size = int(size_text)
    if size > MAX_BLOB_BYTES:
        raise CheckRejected
    data = _git(
        "cat-file", "blob", value, limit=MAX_BLOB_BYTES,
        overall_deadline=deadline,
    )
    if len(data) != size or b"\x00" in data:
        raise CheckRejected
    try:
        data.decode("utf-8", "strict")
    except UnicodeDecodeError as exc:
        raise CheckRejected from exc
    if any(byte < 0x20 and byte not in {0x09, 0x0A, 0x0D} for byte in data):
        raise CheckRejected
    return data


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
    total = 0
    for status, old_sha, new_sha, _old_mode, _new_mode in _raw_changes(
        comparison_base, comparison_head, deadline
    ):
        if status == "D":
            continue
        del old_sha
        new = _blob(new_sha, deadline)
        total += len(new)
        if total > MAX_TOTAL_BLOB_BYTES:
            raise CheckRejected
        _check_head_blob(new)


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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
