#!/usr/bin/env python3
"""Sanitized, review-bound feedback drafting and optional GitHub submission."""
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
CANONICAL_GITHUB_REPO = f"github.com/{DEFAULT_REPO}"
PRIVATE_VULNERABILITY_URL = (
    "https://github.com/cagdasyurekli/codex-agy-worker/security/advisories/new"
)

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
# This is intentionally conservative: a public issue must not be the first route for
# a possible vulnerability. It is a routing safeguard, not a vulnerability detector.
SECURITY_SENSITIVE = re.compile(
    r"(?i)\b(?:security[ -]?(?:bug|issue|report|vulnerability)|vulnerability|"
    r"exploit|cve|cvss|(?:auth(?:entication|orization)?|permission)[ -]?(?:bypass|"
    r"escalation)|privilege[ -]?escalation|(?:remote[ -]?code|command)[ -]?execution|"
    r"\brce\b|(?:sql|command)[ -]?injection|(?:cross[ -]?site|xss)[ -]?scripting|"
    r"(?:secret|credential|token)[ -]?(?:leak|exposure)|data[ -]?exposure)\b"
)


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


def is_security_sensitive(*values: str) -> bool:
    """Fail closed to the private route when feedback may be security-sensitive."""
    return any(SECURITY_SENSITIVE.search(value) is not None for value in values)


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
    security_sensitive = is_security_sensitive(
        args.title, args.component, args.summary,
        getattr(args, "steps", "") or "", getattr(args, "expected", "") or "",
        getattr(args, "actual", "") or "", getattr(args, "problem", "") or "",
        getattr(args, "proposal", "") or "", getattr(args, "benefit", "") or "",
    )
    tool, agy, system = runtime_facts()
    if args.kind == "bug":
        steps = sanitize(args.steps or "", limit=6_000)
        expected = sanitize(args.expected or "", limit=3_000)
        actual = sanitize(args.actual or "", limit=3_000)
        if not all((summary, steps, expected, actual)):
            raise DraftError("summary, steps, expected, and actual must be non-empty")
        heading = "Bug"
        fields = f"""## Minimal reproduction

{steps}

## Expected behavior

{expected}

## Actual behavior

{actual}
"""
    elif args.kind == "improvement":
        problem = sanitize(args.problem or "", limit=4_000)
        proposal = sanitize(args.proposal or "", limit=6_000)
        benefit = sanitize(args.benefit or "", limit=3_000)
        if not all((summary, problem, proposal, benefit)):
            raise DraftError("summary, problem, proposal, and benefit must be non-empty")
        heading = "Improvement"
        fields = f"""## Problem to solve

{problem}

## Proposed improvement

{proposal}

## Expected benefit

{benefit}
"""
    elif args.kind == "security":
        if any((args.steps, args.expected, args.actual,
                args.problem, args.proposal, args.benefit)):
            raise DraftError(
                "security drafts accept only title, component, and a minimal summary")
        summary = sanitize(args.summary, limit=1_000)
        if not summary:
            raise DraftError("summary must be non-empty")
        heading = "Security"
        security_sensitive = True
        fields = """Do not add exploit details, secrets, prompts, source, paths, or raw logs to this
draft. Share any necessary sensitive details only through the private route below.
"""
    else:  # argparse enforces this; retain a fail-closed library boundary.
        raise DraftError("feedback kind is invalid")

    private_route = ""
    if security_sensitive:
        private_route = f"""## Security-sensitive route

This report is not eligible for public GitHub issue submission. Do not add exploit
details, secrets, prompts, source, paths, or raw logs. Submit a minimal report through
the private vulnerability reporting form instead: {PRIVATE_VULNERABILITY_URL}

"""
    return f"""{MARKER}
# {heading}: {title}

## Component

{component}

## Summary

{summary}

{fields}{private_route}## Sanitized environment

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
    if not any(body.startswith(MARKER + f"\n# {kind}: ")
               for kind in ("Bug", "Improvement", "Security")):
        raise DraftError("file is not a generated sanitized feedback draft")
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
        for kind in ("Bug", "Improvement", "Security"):
            prefix = f"# {kind}: "
            if line.startswith(prefix):
                return line[len(prefix):].strip()
    raise DraftError("draft title is missing")


def requires_private_route(body: str) -> bool:
    # Re-check the reviewed bytes so a manually altered but otherwise sanitized
    # draft cannot turn a possible vulnerability into a public issue.
    return (body.startswith(MARKER + "\n# Security: ")
            or "## Security-sensitive route\n" in body
            or SECURITY_SENSITIVE.search(body) is not None)


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
    if args.repo != DEFAULT_REPO:
        raise DraftError(f"--repo must be the fixed canonical repository: {DEFAULT_REPO}")
    if requires_private_route(body):
        print(
            "bug-report: security-sensitive feedback is not submitted publicly; "
            f"use {PRIVATE_VULNERABILITY_URL}",
            file=sys.stderr,
        )
        return 70
    if args.confirm_public_safe_sha != body_digest:
        raise DraftError(
            "public-safety confirmation hash does not match the reviewed draft; "
            "review the exact bytes and confirm that no vulnerability is suspected")
    gh = shutil.which("gh")
    if gh is None:
        print("bug-report: gh is not installed; draft was not submitted", file=sys.stderr)
        return 69
    command = [
        gh, "issue", "create", "--repo", CANONICAL_GITHUB_REPO,
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
        description="Create, preview, and explicitly submit sanitized feedback drafts")
    commands = root.add_subparsers(dest="command", required=True)
    draft = commands.add_parser("draft")
    draft.add_argument("--output", required=True)
    draft.add_argument(
        "--kind", choices=("bug", "improvement", "security"), default="bug")
    draft.add_argument("--title", required=True)
    draft.add_argument("--component", default="qa-gate")
    draft.add_argument("--summary", required=True)
    draft.add_argument("--steps")
    draft.add_argument("--expected")
    draft.add_argument("--actual")
    draft.add_argument("--problem")
    draft.add_argument("--proposal")
    draft.add_argument("--benefit")
    draft.set_defaults(handler=draft_command)

    preview = commands.add_parser("preview")
    preview.add_argument("file")
    preview.set_defaults(handler=preview_command)

    submit = commands.add_parser("submit")
    submit.add_argument("file")
    submit.add_argument("--confirm-sha", required=True)
    submit.add_argument(
        "--confirm-public-safe-sha",
        help=("SHA-256 of the same reviewed bytes, explicitly confirming that "
              "no vulnerability is suspected; required for public submission"),
    )
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
