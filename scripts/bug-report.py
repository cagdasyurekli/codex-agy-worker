#!/usr/bin/env python3
"""Sanitized, review-bound bug drafting and optional GitHub submission."""
from __future__ import annotations

import argparse
import hashlib
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


MARKER = "<!-- agy-worker-sanitized-bug-draft:v1 -->"
MAX_DRAFT_BYTES = 20_000
DEFAULT_REPO = "cagdasyurekli/codex-agy-worker"

PEM_BLOCK = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?"
    r"(?:-----END [A-Z0-9 ]*PRIVATE KEY-----|\Z)",
    re.DOTALL,
)
SECRET_PATTERNS = (
    # Redact the complete remainder of credential-bearing lines. Matching only the
    # first non-space token leaks Authorization payloads and quoted passphrases.
    re.compile(r"(?im)\b(?:authorization|api[_ -]?key|token|password|secret)\b\s*[:=]\s*[^\r\n]*"),
    re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{12,}"),
    re.compile(r"(?i)\bBasic\s+[A-Za-z0-9+/=]{8,}"),
)
PATH_PATTERNS = (
    # Quoted paths may contain spaces; remove them before the unquoted patterns.
    re.compile(r'''(?<![A-Za-z0-9:/])(?:"/[^"\r\n]+"|'/[^'\r\n]+')'''),
    re.compile(r'''(?<![A-Za-z0-9:/.])/(?!/)[^\s`<>\[\](){}"']+'''),
    re.compile(r'''(?<![A-Za-z0-9])(?:"[A-Za-z]:\\[^"\r\n]+"|'[A-Za-z]:\\[^'\r\n]+')'''),
    re.compile(r'''\b[A-Za-z]:\\[^\s`<>\[\](){}"']+'''),
    re.compile(r'''(?<![A-Za-z0-9\\])\\\\[^\\\s`<>\[\](){}]+\\[^\s`<>\[\](){}"']+'''),
)
FORBIDDEN_ARTIFACTS = re.compile(
    r"(?i)(?:\b(?:stream\.ndjson|prompt\.txt|task\.txt|full-prompt\.txt|"
    r"stderr\.txt|envelope\.json)\b|/logs/|\\logs\\)"
)
CODE_FENCE = re.compile(r"(?:`{3,}|~{3,}).*?(?:(?:`{3,}|~{3,})|\Z)", re.DOTALL)
INDENTED_CODE = re.compile(r"(?m)(?:^(?: {4}|\t).*(?:\n|$))+")


class DraftError(ValueError):
    pass


def sanitize(text: str, *, limit: int) -> str:
    text = text.replace("\x00", "")[:limit]
    text = CODE_FENCE.sub("<redacted-code-block>", text)
    text = INDENTED_CODE.sub("<redacted-code-block>\n", text)
    text = PEM_BLOCK.sub("<redacted-secret>", text)
    for pattern in SECRET_PATTERNS:
        text = pattern.sub("<redacted-secret>", text)
    for pattern in PATH_PATTERNS:
        text = pattern.sub("<redacted-path>", text)
    text = FORBIDDEN_ARTIFACTS.sub("<redacted-artifact>", text)
    return text.strip()


def safe_title(text: str) -> str:
    title = " ".join(sanitize(text, limit=200).split())
    if not title:
        raise DraftError("title is empty after sanitization")
    return title.replace("#", "")


def command_output(command: list[str], fallback: str) -> str:
    try:
        result = subprocess.run(
            command, check=True, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, timeout=10,
        )
        return " ".join(result.stdout.strip().split()) or fallback
    except (OSError, subprocess.SubprocessError):
        return fallback


def runtime_facts() -> tuple[str, str, str]:
    root = Path(__file__).resolve().parent.parent
    tool = command_output(
        ["git", "-C", str(root), "describe", "--tags", "--always", "--dirty"],
        "unavailable",
    )
    agy = command_output(["agy", "--version"], "unavailable")
    system = f"{platform.system()} {platform.machine()}".strip()
    return sanitize(tool, limit=200), sanitize(agy, limit=200), sanitize(system, limit=200)


def render_draft(args: argparse.Namespace) -> str:
    title = safe_title(args.title)
    component = safe_title(args.component)
    summary = sanitize(args.summary, limit=4_000)
    steps = sanitize(args.steps, limit=6_000)
    expected = sanitize(args.expected, limit=3_000)
    actual = sanitize(args.actual, limit=3_000)
    if not all((summary, steps, expected, actual)):
        raise DraftError("summary, steps, expected, and actual must be non-empty")
    tool, agy, system = runtime_facts()
    return f"""{MARKER}
# Bug: {title}

## Component

{component}

## Summary

{summary}

## Minimal reproduction

{steps}

## Expected behavior

{expected}

## Actual behavior

{actual}

## Sanitized environment

- codex-agy-worker: {tool}
- agy: {agy}
- OS/architecture: {system}

## Privacy boundary

This draft was generated without reading or attaching prompts, source files,
envelopes, credentials, absolute paths, or raw logs. Review the exact body with
`./bug-report.sh preview <file>` before authorizing submission.
"""


def validate_body(body: str) -> None:
    encoded = body.encode("utf-8")
    if len(encoded) > MAX_DRAFT_BYTES:
        raise DraftError(f"draft exceeds {MAX_DRAFT_BYTES} bytes")
    if not body.startswith(MARKER + "\n# Bug: "):
        raise DraftError("file is not a generated sanitized bug draft")
    if PEM_BLOCK.search(body):
        raise DraftError("draft still contains sensitive-looking content")
    for pattern in SECRET_PATTERNS + PATH_PATTERNS:
        if pattern.search(body):
            raise DraftError("draft still contains sensitive-looking content")
    if (FORBIDDEN_ARTIFACTS.search(body) or CODE_FENCE.search(body)
            or INDENTED_CODE.search(body)):
        raise DraftError("draft contains a forbidden raw artifact or code block")


def read_validated(path: Path) -> str:
    try:
        body = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise DraftError(f"cannot read draft: {exc}") from exc
    validate_body(body)
    return body


def digest(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def extract_title(body: str) -> str:
    for line in body.splitlines():
        if line.startswith("# Bug: "):
            return line[len("# Bug: "):].strip()
    raise DraftError("draft title is missing")


def write_private_exclusive(path: Path, body: str) -> None:
    """Publish a complete mode-0600 draft without overwriting an existing path."""
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.")
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = -1
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            # Same-directory hard linking is an atomic no-overwrite publication.
            os.link(temporary, path)
        except FileExistsError as exc:
            raise DraftError("refusing to overwrite an existing draft") from exc
        except OSError as exc:
            raise DraftError(f"cannot create draft: {exc}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def draft_command(args: argparse.Namespace) -> int:
    output = Path(args.output)
    if not output.parent.is_dir():
        raise DraftError("output parent directory must already exist")
    body = render_draft(args)
    validate_body(body)
    write_private_exclusive(output, body)
    print(f"draft: {output}")
    print(f"SHA256: {digest(body)}")
    print(f"next: ./bug-report.sh preview {output}")
    return 0


def preview_command(args: argparse.Namespace) -> int:
    body = read_validated(Path(args.file))
    print("=== EXACT GITHUB ISSUE BODY ===")
    print(body, end="" if body.endswith("\n") else "\n")
    print("=== END BODY ===")
    print(f"SHA256: {digest(body)}")
    return 0


def submit_command(args: argparse.Namespace) -> int:
    body_path = Path(args.file)
    body = read_validated(body_path)
    body_digest = digest(body)
    print("=== EXACT GITHUB ISSUE BODY ===")
    print(body, end="" if body.endswith("\n") else "\n")
    print("=== END BODY ===")
    print(f"SHA256: {body_digest}")
    if args.confirm_sha != body_digest:
        raise DraftError("confirmation hash does not match the reviewed draft")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.repo):
        raise DraftError("--repo must be OWNER/REPO")
    gh = shutil.which("gh")
    if gh is None:
        print("bug-report: gh is not installed; draft was not submitted", file=sys.stderr)
        return 69
    qualified_repo = f"github.com/{args.repo}"
    command = [
        gh, "issue", "create", "--repo", qualified_repo,
        "--title", extract_title(body), "--body-file", "-",
    ]
    environment = os.environ.copy()
    # A caller's gh enterprise default must not redirect a public GitHub report.
    environment.pop("GH_HOST", None)
    completed = subprocess.run(
        command, check=False, input=body, text=True, env=environment)
    if completed.returncode != 0:
        print("bug-report: gh issue create failed; draft remains local", file=sys.stderr)
        return completed.returncode or 1
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        description="Create, preview, and explicitly submit sanitized bug drafts")
    commands = root.add_subparsers(dest="command", required=True)
    draft = commands.add_parser("draft")
    draft.add_argument("--output", required=True)
    draft.add_argument("--title", required=True)
    draft.add_argument("--component", default="qa-gate")
    draft.add_argument("--summary", required=True)
    draft.add_argument("--steps", required=True)
    draft.add_argument("--expected", required=True)
    draft.add_argument("--actual", required=True)
    draft.set_defaults(handler=draft_command)

    preview = commands.add_parser("preview")
    preview.add_argument("file")
    preview.set_defaults(handler=preview_command)

    submit = commands.add_parser("submit")
    submit.add_argument("file")
    submit.add_argument("--confirm-sha", required=True)
    submit.add_argument("--repo", default=DEFAULT_REPO)
    submit.set_defaults(handler=submit_command)
    return root


def main() -> int:
    args = parser().parse_args()
    try:
        return args.handler(args)
    except DraftError as exc:
        print(f"bug-report: {exc}", file=sys.stderr)
        return 65


if __name__ == "__main__":
    raise SystemExit(main())
