#!/usr/bin/env python3
"""Validate one fixed, observational agy distribution-manifest canary."""

from __future__ import annotations

import json
import re
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Iterable, Optional


MANIFEST_URL = (
    "https://antigravity-cli-auto-updater-974169037036.us-central1.run.app/"
    "manifests/darwin_arm64.json"
)
ARCHIVE_HOST = "storage.googleapis.com"
MAX_RESPONSE_BYTES = 4096
FETCH_TIMEOUT_SECONDS = 10.0
SEMVER_RE = re.compile(
    r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
)
SHA512_RE = re.compile(r"[0-9a-f]{128}")
ARCHIVE_PATH_RE = re.compile(
    r"/antigravity-public/antigravity-cli/"
    r"((?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*))"
    r"-([0-9]+)/darwin-arm/cli_mac_arm64\.tar\.gz"
)


class DistributionEvidenceError(ValueError):
    """A sanitized fixed-source evidence failure."""

    def __init__(self, category: str):
        super().__init__(category)
        self.category = category


class DuplicateKeyError(DistributionEvidenceError):
    """A duplicate JSON key makes the document ambiguous."""


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
        raise DistributionEvidenceError("redirect rejected")


def no_duplicate_object(pairs: Iterable[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateKeyError("invalid manifest document")
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


def _single_header(headers: Any, name: str) -> str:
    values = headers.get_all(name) if hasattr(headers, "get_all") else None
    if values is None:
        value = headers.get(name) if hasattr(headers, "get") else None
        values = [] if value is None else [value]
    if len(values) != 1 or not isinstance(values[0], str):
        raise DistributionEvidenceError("invalid response metadata")
    return values[0]


def _validate_content_type(raw: str) -> None:
    parts = [part.strip() for part in raw.split(";")]
    if not parts or parts[0].lower() != "application/json":
        raise DistributionEvidenceError("invalid response metadata")
    for parameter in parts[1:]:
        if parameter.lower() != "charset=utf-8":
            raise DistributionEvidenceError("invalid response metadata")


def fetch_manifest_bytes(
    opener: Any,
    *,
    url: str = MANIFEST_URL,
    timeout: float = FETCH_TIMEOUT_SECONDS,
    limit: int = MAX_RESPONSE_BYTES,
) -> bytes:
    """Fetch one bounded manifest response through an injected opener."""
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "codex-agy-worker/compat"},
        method="GET",
    )
    try:
        response = opener.open(request, timeout=timeout)
    except DistributionEvidenceError:
        raise
    except urllib.error.HTTPError as exc:
        if 300 <= exc.code < 400:
            raise DistributionEvidenceError("redirect rejected") from None
        raise DistributionEvidenceError("HTTP evidence unavailable") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise DistributionEvidenceError("network evidence unavailable") from None
    except Exception:
        raise DistributionEvidenceError("network evidence unavailable") from None

    try:
        with response:
            status = getattr(response, "status", None)
            if status is None and hasattr(response, "getcode"):
                status = response.getcode()
            if status != 200:
                raise DistributionEvidenceError("HTTP evidence unavailable")

            _validate_content_type(_single_header(response.headers, "Content-Type"))
            length_text = _single_header(response.headers, "Content-Length")
            if re.fullmatch(r"[1-9][0-9]*", length_text) is None:
                raise DistributionEvidenceError("invalid response metadata")
            expected_length = int(length_text)
            if expected_length > limit:
                raise DistributionEvidenceError("response too large")
            body = response.read(limit + 1)
    except DistributionEvidenceError:
        raise
    except (TimeoutError, OSError):
        raise DistributionEvidenceError("network evidence unavailable") from None
    except Exception:
        raise DistributionEvidenceError("network evidence unavailable") from None

    if len(body) > limit:
        raise DistributionEvidenceError("response too large")
    if len(body) != expected_length:
        raise DistributionEvidenceError("invalid response metadata")
    return body


def _validate_string(value: Any) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise DistributionEvidenceError("invalid manifest policy")
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
        raise DistributionEvidenceError("invalid manifest policy")
    return value


def validate_archive_url(value: str, version: str) -> None:
    if not value.isascii() or "%" in value or "\\" in value:
        raise DistributionEvidenceError("invalid archive policy")
    try:
        parsed = urllib.parse.urlsplit(value)
        port = parsed.port
    except ValueError:
        raise DistributionEvidenceError("invalid archive policy") from None
    if (
        parsed.scheme != "https"
        or parsed.netloc != ARCHIVE_HOST
        or parsed.hostname != ARCHIVE_HOST
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.query
        or parsed.fragment
    ):
        raise DistributionEvidenceError("invalid archive policy")
    match = ARCHIVE_PATH_RE.fullmatch(parsed.path)
    if match is None or match.group(1) != version:
        raise DistributionEvidenceError("invalid archive policy")


def parse_manifest_bytes(raw: bytes) -> dict[str, str]:
    try:
        text = raw.decode("utf-8", "strict")
        value = json.loads(text, object_pairs_hook=no_duplicate_object)
    except (UnicodeDecodeError, json.JSONDecodeError, DuplicateKeyError):
        raise DistributionEvidenceError("invalid manifest document") from None
    if not isinstance(value, dict) or set(value) != {"version", "url", "sha512"}:
        raise DistributionEvidenceError("invalid manifest schema")

    version = _validate_string(value["version"])
    archive_url = _validate_string(value["url"])
    sha512 = _validate_string(value["sha512"])
    if SEMVER_RE.fullmatch(version) is None:
        raise DistributionEvidenceError("invalid manifest policy")
    if SHA512_RE.fullmatch(sha512) is None:
        raise DistributionEvidenceError("invalid manifest policy")
    validate_archive_url(archive_url, version)
    return {"version": version, "url": archive_url, "sha512": sha512}


def read_verified_version(path: Path) -> str:
    try:
        raw = path.read_bytes()
    except OSError:
        raise DistributionEvidenceError("invalid verified baseline") from None
    if not raw.endswith(b"\n") or raw.count(b"\n") != 1:
        raise DistributionEvidenceError("invalid verified baseline")
    try:
        value = raw[:-1].decode("ascii", "strict")
    except UnicodeDecodeError:
        raise DistributionEvidenceError("invalid verified baseline") from None
    if SEMVER_RE.fullmatch(value) is None:
        raise DistributionEvidenceError("invalid verified baseline")
    return value


def read_snapshot(path: Path) -> dict[str, str]:
    try:
        raw = path.read_bytes()
    except OSError:
        raise DistributionEvidenceError("invalid observational snapshot") from None
    try:
        return parse_manifest_bytes(raw)
    except DistributionEvidenceError:
        raise DistributionEvidenceError("invalid observational snapshot") from None


def evaluate_manifest(
    observed: dict[str, str], verified_version: str, snapshot: dict[str, str]
) -> tuple[int, str]:
    if observed != snapshot:
        return 3, "validated tuple differs from observational snapshot"
    if observed["version"] != verified_version:
        return 3, (
            f"official distribution {observed['version']}; verified {verified_version}"
        )
    return 0, f"{verified_version}"


def check_production_manifest(
    *,
    root: Optional[Path] = None,
    opener: Optional[Any] = None,
) -> tuple[int, str]:
    repository = root if root is not None else Path(__file__).resolve().parent.parent
    verified_version = read_verified_version(
        repository / "compat" / "agy-verified-version.txt"
    )
    snapshot = read_snapshot(repository / "compat" / "agy-distribution-manifest.json")
    fixed_opener = opener if opener is not None else build_fixed_opener()
    observed = parse_manifest_bytes(fetch_manifest_bytes(fixed_opener))
    return evaluate_manifest(observed, verified_version, snapshot)


def main() -> None:
    if len(sys.argv) != 1:
        print("  distribution manifest: evidence-unavailable (invalid invocation)")
        raise SystemExit(64)
    try:
        status, detail = check_production_manifest()
    except DistributionEvidenceError as exc:
        print(f"  distribution manifest: evidence-unavailable ({exc.category})")
        raise SystemExit(2) from None
    except Exception:
        print("  distribution manifest: evidence-unavailable (internal evidence failure)")
        raise SystemExit(2) from None
    if status == 0:
        print(f"  distribution manifest: unchanged ({detail})")
    else:
        print(f"  distribution manifest: drift-review ({detail})")
    raise SystemExit(status)


if __name__ == "__main__":
    main()
