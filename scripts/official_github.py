#!/usr/bin/env python3
"""Fetch bounded compatibility evidence from fixed official GitHub API paths."""

from __future__ import annotations

import json
import re
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Iterable, Optional


API_ORIGIN = "https://api.github.com"
API_HOST = "api.github.com"
MAX_RESPONSE_BYTES = 256 * 1024
MAX_RELEASE_RESPONSE_BYTES = 512 * 1024
FETCH_TIMEOUT_SECONDS = 6.0
SEMVER_PATTERN = r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
SEMVER_RE = re.compile(SEMVER_PATTERN)
REVISION_RE = re.compile(r"[0-9a-f]{40}")
FIXED_PATH_RE = re.compile(
    rf"(?:"
    rf"/repos/cagdasyurekli/codex-agy-worker/"
    rf"(?:releases/latest|git/ref/tags/v{SEMVER_PATTERN})"
    rf"|/repos/google-antigravity/antigravity-cli/"
    rf"(?:releases/latest|git/ref/heads/main)"
    rf"|/repos/openai/codex/(?:releases/latest|git/ref/heads/main)"
    rf")"
)


@dataclass(frozen=True)
class ToolPolicy:
    owner: str
    repository: str
    tag_pattern: re.Pattern[str]
    main_branch: Optional[str]


POLICIES = {
    "project": ToolPolicy(
        owner="cagdasyurekli",
        repository="codex-agy-worker",
        tag_pattern=re.compile(rf"v({SEMVER_PATTERN})"),
        main_branch=None,
    ),
    "agy": ToolPolicy(
        owner="google-antigravity",
        repository="antigravity-cli",
        tag_pattern=re.compile(rf"v?({SEMVER_PATTERN})"),
        main_branch="main",
    ),
    "codex": ToolPolicy(
        owner="openai",
        repository="codex",
        tag_pattern=re.compile(rf"rust-v({SEMVER_PATTERN})"),
        main_branch="main",
    ),
}


class OfficialEvidenceError(ValueError):
    """A sanitized fixed-source evidence failure."""

    def __init__(self, category: str):
        super().__init__(category)
        self.category = category


class DuplicateKeyError(OfficialEvidenceError):
    """Duplicate JSON keys make official evidence ambiguous."""


class RejectRedirects(urllib.request.HTTPRedirectHandler):
    """Reject redirects before urllib can make a second request."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        del req, fp, code, msg, headers, newurl
        raise OfficialEvidenceError("redirect rejected")


def no_duplicate_object(pairs: Iterable[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateKeyError("invalid JSON document")
        result[key] = value
    return result


def build_fixed_opener() -> urllib.request.OpenerDirector:
    """Build the production opener: default TLS, no proxies, no redirects."""

    context = ssl.create_default_context()
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        RejectRedirects(),
        urllib.request.HTTPSHandler(context=context),
    )


def _single_header(headers: Any, name: str, *, required: bool = True) -> Optional[str]:
    values = headers.get_all(name) if hasattr(headers, "get_all") else None
    if values is None:
        value = headers.get(name) if hasattr(headers, "get") else None
        values = [] if value is None else [value]
    if not values and not required:
        return None
    if len(values) != 1 or not isinstance(values[0], str):
        raise OfficialEvidenceError("invalid response metadata")
    return values[0]


def _validate_content_type(raw: str) -> None:
    parts = [part.strip() for part in raw.split(";")]
    if not parts or parts[0].lower() not in {
        "application/json",
        "application/vnd.github+json",
    }:
        raise OfficialEvidenceError("invalid response metadata")
    for parameter in parts[1:]:
        if parameter.lower() != "charset=utf-8":
            raise OfficialEvidenceError("invalid response metadata")


def _validate_fixed_url(url: str) -> None:
    if not url.isascii() or "%" in url or "\\" in url:
        raise OfficialEvidenceError("invalid fixed endpoint")
    try:
        parsed = urllib.parse.urlsplit(url)
        port = parsed.port
    except ValueError:
        raise OfficialEvidenceError("invalid fixed endpoint") from None
    if (
        parsed.scheme != "https"
        or parsed.netloc != API_HOST
        or parsed.hostname != API_HOST
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.query
        or parsed.fragment
        or FIXED_PATH_RE.fullmatch(parsed.path) is None
    ):
        raise OfficialEvidenceError("invalid fixed endpoint")


def fetch_json(
    opener: Any,
    url: str,
    *,
    timeout: float = FETCH_TIMEOUT_SECONDS,
    limit: int = MAX_RESPONSE_BYTES,
) -> Any:
    """Fetch one strict, incrementally bounded JSON response."""

    _validate_fixed_url(url)
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "codex-agy-worker/compat",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        method="GET",
    )
    try:
        response = opener.open(request, timeout=timeout)
    except OfficialEvidenceError:
        raise
    except urllib.error.HTTPError as exc:
        if 300 <= exc.code < 400:
            raise OfficialEvidenceError("redirect rejected") from None
        raise OfficialEvidenceError("HTTP evidence unavailable") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise OfficialEvidenceError("network evidence unavailable") from None
    except Exception:
        raise OfficialEvidenceError("network evidence unavailable") from None

    try:
        with response:
            status = getattr(response, "status", None)
            if status is None and hasattr(response, "getcode"):
                status = response.getcode()
            if status != 200:
                raise OfficialEvidenceError("HTTP evidence unavailable")
            content_type = _single_header(response.headers, "Content-Type")
            assert content_type is not None
            _validate_content_type(content_type)
            if _single_header(response.headers, "Content-Encoding", required=False) not in (
                None,
                "identity",
            ):
                raise OfficialEvidenceError("invalid response metadata")
            transfer_encoding = _single_header(response.headers, "Transfer-Encoding", required=False)
            if transfer_encoding is not None:
                transfer_encoding = transfer_encoding.strip().lower()
            if transfer_encoding not in (None, "chunked"):
                raise OfficialEvidenceError("invalid response metadata")
            # HTTP/1.1 chunked responses legitimately omit Content-Length.  The
            # incremental byte ceiling below is the authority in that case; when a
            # length is supplied it remains one unambiguous bounded declaration.
            length_text = _single_header(response.headers, "Content-Length", required=False)
            expected_length: Optional[int] = None
            if length_text is not None:
                if transfer_encoding is not None:
                    raise OfficialEvidenceError("invalid response metadata")
                if re.fullmatch(r"[1-9][0-9]*", length_text) is None:
                    raise OfficialEvidenceError("invalid response metadata")
                expected_length = int(length_text)
                if expected_length > limit:
                    raise OfficialEvidenceError("response too large")

            body = bytearray()
            while len(body) <= limit:
                chunk = response.read(min(8192, limit + 1 - len(body)))
                if not chunk:
                    break
                body.extend(chunk)
                if len(body) > limit:
                    raise OfficialEvidenceError("response too large")
    except OfficialEvidenceError:
        raise
    except (TimeoutError, OSError):
        raise OfficialEvidenceError("network evidence unavailable") from None
    except Exception:
        raise OfficialEvidenceError("network evidence unavailable") from None

    if expected_length is not None and len(body) != expected_length:
        raise OfficialEvidenceError("invalid response metadata")
    try:
        return json.loads(
            bytes(body).decode("utf-8", "strict"),
            object_pairs_hook=no_duplicate_object,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, DuplicateKeyError):
        raise OfficialEvidenceError("invalid JSON document") from None


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise OfficialEvidenceError(f"invalid {label} evidence")
    return value


def _release(value: Any, policy: ToolPolicy) -> tuple[str, str]:
    document = _object(value, "release")
    tag = document.get("tag_name")
    if (
        not isinstance(tag, str)
        or policy.tag_pattern.fullmatch(tag) is None
        or document.get("draft") is not False
        or document.get("prerelease") is not False
    ):
        raise OfficialEvidenceError("invalid stable release evidence")
    match = policy.tag_pattern.fullmatch(tag)
    assert match is not None
    return tag, match.group(1)


def _source_ref(value: Any, branch: str) -> str:
    document = _object(value, "source")
    target = _object(document.get("object"), "source")
    revision = target.get("sha")
    if (
        document.get("ref") != f"refs/heads/{branch}"
        or target.get("type") != "commit"
        or not isinstance(revision, str)
        or REVISION_RE.fullmatch(revision) is None
    ):
        raise OfficialEvidenceError("invalid source evidence")
    return revision


def _tag_ref(value: Any, tag: str) -> str:
    document = _object(value, "release tag")
    target = _object(document.get("object"), "release tag")
    revision = target.get("sha")
    if (
        document.get("ref") != f"refs/tags/{tag}"
        or target.get("type") != "commit"
        or not isinstance(revision, str)
        or REVISION_RE.fullmatch(revision) is None
    ):
        raise OfficialEvidenceError("invalid release tag evidence")
    return revision


def _repository_url(policy: ToolPolicy, suffix: str) -> str:
    return f"{API_ORIGIN}/repos/{policy.owner}/{policy.repository}/{suffix}"


def latest_evidence(tool: str, *, opener: Optional[Any] = None) -> tuple[str, str, str]:
    """Return a sanitized tool, stable version/tag, and source or release revision."""

    policy = POLICIES.get(tool)
    if policy is None:
        raise OfficialEvidenceError("invalid tool policy")
    fixed_opener = opener if opener is not None else build_fixed_opener()
    tag, version = _release(
        fetch_json(
            fixed_opener,
            _repository_url(policy, "releases/latest"),
            limit=MAX_RELEASE_RESPONSE_BYTES,
        ),
        policy,
    )
    if policy.main_branch is None:
        revision = _tag_ref(
            fetch_json(fixed_opener, _repository_url(policy, f"git/ref/tags/{tag}")),
            tag,
        )
        return tool, tag, revision
    revision = _source_ref(
        fetch_json(
            fixed_opener,
            _repository_url(policy, f"git/ref/heads/{policy.main_branch}"),
        ),
        policy.main_branch,
    )
    return tool, version, revision


def project_release_evidence(tag: str, *, opener: Optional[Any] = None) -> tuple[str, str, str]:
    """Resolve one strictly validated project release tag to an official commit."""

    policy = POLICIES["project"]
    if policy.tag_pattern.fullmatch(tag) is None:
        raise OfficialEvidenceError("invalid project release tag")
    fixed_opener = opener if opener is not None else build_fixed_opener()
    revision = _tag_ref(
        fetch_json(fixed_opener, _repository_url(policy, f"git/ref/tags/{tag}")),
        tag,
    )
    return "project", tag, revision


def main(argv: list[str]) -> int:
    try:
        if len(argv) == 3 and argv[1] == "--latest" and argv[2] in POLICIES:
            row = latest_evidence(argv[2])
        elif len(argv) == 3 and argv[1] == "--project-release":
            row = project_release_evidence(argv[2])
        else:
            raise OfficialEvidenceError("invalid invocation")
    except OfficialEvidenceError as exc:
        print(f"official github: evidence unavailable ({exc.category})", file=sys.stderr)
        return 2
    print("\t".join(row))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
