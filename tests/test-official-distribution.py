#!/usr/bin/env python3
"""Offline adversarial tests for the fixed agy distribution canary."""

from __future__ import annotations

import contextlib
import email.message
import importlib.util
import io
import json
import os
import ssl
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, List, Optional, Tuple
from unittest import mock

sys.dont_write_bytecode = True


ROOT = Path(__file__).resolve().parent.parent
MODULE_PATH = ROOT / "scripts" / "official_distribution.py"
SPEC = importlib.util.spec_from_file_location("official_distribution", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise SystemExit("cannot load official distribution module")
distribution = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(distribution)

VERSION = "1.1.16"
SHA512 = "fa3a94a7d9d96cb367bf643ecf0da3b4d6b45f3e390ec6db1d699fdac4f7750894617152fc3c1695712a36eee926fff4f00ff4a44d372b3f604cfc9ec6fdbea6"
ARCHIVE_URL = (
    "https://storage.googleapis.com/antigravity-public/antigravity-cli/"
    "1.1.16-6607970839166976/darwin-arm/cli_mac_arm64.tar.gz"
)


def manifest_bytes(**changes: Any) -> bytes:
    value = {"version": VERSION, "url": ARCHIVE_URL, "sha512": SHA512}
    value.update(changes)
    return json.dumps(value, separators=(",", ":")).encode("utf-8")


class FakeResponse:
    def __init__(
        self,
        body: bytes,
        *,
        status: int = 200,
        content_type: str = "application/json",
        content_length: Optional[str] = None,
        extra_headers: Optional[List[Tuple[str, str]]] = None,
    ):
        self.body = body
        self.status = status
        self.headers = email.message.Message()
        if content_type:
            self.headers["Content-Type"] = content_type
        self.headers["Content-Length"] = (
            str(len(body)) if content_length is None else content_length
        )
        for name, value in extra_headers or []:
            self.headers[name] = value
        self.read_sizes: list[int] = []

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: Any) -> None:
        del args

    def getcode(self) -> int:
        return self.status

    def read(self, size: int) -> bytes:
        self.read_sizes.append(size)
        return self.body[:size]


class FakeOpener:
    def __init__(self, response: Any):
        self.response = response
        self.calls: list[tuple[urllib.request.Request, float]] = []

    def open(self, request: urllib.request.Request, *, timeout: float) -> Any:
        self.calls.append((request, timeout))
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response


def expect_error(category: str, callback: Callable[[], Any]) -> None:
    try:
        callback()
    except distribution.DistributionEvidenceError as exc:
        if exc.category != category:
            raise AssertionError(f"category {exc.category!r}, expected {category!r}")
        return
    raise AssertionError(f"expected controlled {category!r} error")


def parse(**changes: Any) -> dict[str, str]:
    return distribution.parse_manifest_bytes(manifest_bytes(**changes))


def replace_url(old: str, new: str) -> str:
    if old not in ARCHIVE_URL:
        raise AssertionError("test fixture replacement does not match")
    return ARCHIVE_URL.replace(old, new)


TESTS: list[tuple[str, Callable[[], None]]] = []


def test(name: str) -> Callable[[Callable[[], None]], Callable[[], None]]:
    def register(callback: Callable[[], None]) -> Callable[[], None]:
        TESTS.append((name, callback))
        return callback

    return register


@test("fixed production manifest URL is exact")
def _() -> None:
    assert distribution.MANIFEST_URL == (
        "https://antigravity-cli-auto-updater-974169037036.us-central1.run.app/"
        "manifests/darwin_arm64.json"
    )


@test("production opener disables environment proxies")
def _() -> None:
    real_proxy_handler = urllib.request.ProxyHandler
    constructor_inputs: list[Any] = []

    class RecordingProxyHandler(real_proxy_handler):
        def __init__(self, proxies: Any = None):
            constructor_inputs.append(proxies)
            super().__init__(proxies)

    with mock.patch.object(urllib.request, "ProxyHandler", RecordingProxyHandler):
        distribution.build_fixed_opener()
    assert constructor_inputs == [{}]


@test("production opener installs a redirect rejector")
def _() -> None:
    opener = distribution.build_fixed_opener()
    assert any(
        isinstance(handler, distribution.RejectRedirects)
        for handler in opener.handlers
    )


@test("bounded fetch accepts exact response and makes one request")
def _() -> None:
    body = manifest_bytes()
    response = FakeResponse(body)
    opener = FakeOpener(response)
    assert distribution.fetch_manifest_bytes(opener) == body
    assert len(opener.calls) == 1
    request, timeout = opener.calls[0]
    assert request.full_url == distribution.MANIFEST_URL
    assert request.get_method() == "GET"
    assert request.headers["Accept"] == "application/json"
    assert timeout == distribution.FETCH_TIMEOUT_SECONDS
    assert response.read_sizes == [distribution.MAX_RESPONSE_BYTES + 1]


@test("response exactly at byte limit is accepted")
def _() -> None:
    body = b"x" * 32
    opener = FakeOpener(FakeResponse(body))
    assert distribution.fetch_manifest_bytes(opener, limit=32) == body


@test("streamed body over byte limit is rejected")
def _() -> None:
    body = b"x" * 33
    opener = FakeOpener(FakeResponse(body, content_length="32"))
    expect_error(
        "response too large",
        lambda: distribution.fetch_manifest_bytes(opener, limit=32),
    )


@test("declared body over byte limit is rejected before reading")
def _() -> None:
    response = FakeResponse(b"x", content_length="33")
    expect_error(
        "response too large",
        lambda: distribution.fetch_manifest_bytes(FakeOpener(response), limit=32),
    )
    assert response.read_sizes == []


@test("missing Content-Length is rejected")
def _() -> None:
    response = FakeResponse(b"x")
    del response.headers["Content-Length"]
    expect_error(
        "invalid response metadata",
        lambda: distribution.fetch_manifest_bytes(FakeOpener(response)),
    )


@test("duplicate Content-Length is rejected")
def _() -> None:
    response = FakeResponse(b"x", extra_headers=[("Content-Length", "1")])
    expect_error(
        "invalid response metadata",
        lambda: distribution.fetch_manifest_bytes(FakeOpener(response)),
    )


@test("noncanonical Content-Length is rejected")
def _() -> None:
    response = FakeResponse(b"x", content_length="01")
    expect_error(
        "invalid response metadata",
        lambda: distribution.fetch_manifest_bytes(FakeOpener(response)),
    )


@test("truncated body is rejected")
def _() -> None:
    response = FakeResponse(b"x", content_length="2")
    expect_error(
        "invalid response metadata",
        lambda: distribution.fetch_manifest_bytes(FakeOpener(response)),
    )


@test("wrong content type is rejected")
def _() -> None:
    response = FakeResponse(b"x", content_type="text/plain")
    expect_error(
        "invalid response metadata",
        lambda: distribution.fetch_manifest_bytes(FakeOpener(response)),
    )


@test("UTF-8 JSON content type is accepted")
def _() -> None:
    response = FakeResponse(b"x", content_type="application/json; charset=utf-8")
    assert distribution.fetch_manifest_bytes(FakeOpener(response)) == b"x"


@test("non-UTF-8 JSON charset is rejected")
def _() -> None:
    response = FakeResponse(b"x", content_type="application/json; charset=latin1")
    expect_error(
        "invalid response metadata",
        lambda: distribution.fetch_manifest_bytes(FakeOpener(response)),
    )


@test("unknown content-type parameter is rejected")
def _() -> None:
    response = FakeResponse(b"x", content_type="application/json; profile=test")
    expect_error(
        "invalid response metadata",
        lambda: distribution.fetch_manifest_bytes(FakeOpener(response)),
    )


@test("non-200 response is rejected")
def _() -> None:
    response = FakeResponse(b"x", status=503)
    expect_error(
        "HTTP evidence unavailable",
        lambda: distribution.fetch_manifest_bytes(FakeOpener(response)),
    )


@test("HTTP error is sanitized")
def _() -> None:
    error = urllib.error.HTTPError(
        distribution.MANIFEST_URL, 503, "secret upstream body", {}, None
    )
    expect_error(
        "HTTP evidence unavailable",
        lambda: distribution.fetch_manifest_bytes(FakeOpener(error)),
    )


def redirect_is_rejected(location: str) -> None:
    handler = distribution.RejectRedirects()
    request = urllib.request.Request(distribution.MANIFEST_URL)
    expect_error(
        "redirect rejected",
        lambda: handler.redirect_request(
            request, None, 302, "redirect", {"Location": location}, location
        ),
    )


@test("same-host redirect is rejected before following")
def _() -> None:
    redirect_is_rejected(distribution.MANIFEST_URL + "?next=1")


@test("cross-host redirect is rejected before following")
def _() -> None:
    redirect_is_rejected("https://example.invalid/credential")


@test("redirect HTTP error is classified without Location disclosure")
def _() -> None:
    error = urllib.error.HTTPError(
        distribution.MANIFEST_URL,
        302,
        "redirect",
        {"Location": "https://example.invalid/secret"},
        None,
    )
    expect_error(
        "redirect rejected",
        lambda: distribution.fetch_manifest_bytes(FakeOpener(error)),
    )


@test("TLS failure is inconclusive and sanitized")
def _() -> None:
    expect_error(
        "network evidence unavailable",
        lambda: distribution.fetch_manifest_bytes(
            FakeOpener(ssl.SSLError("certificate secret"))
        ),
    )


@test("DNS failure is inconclusive and sanitized")
def _() -> None:
    expect_error(
        "network evidence unavailable",
        lambda: distribution.fetch_manifest_bytes(
            FakeOpener(urllib.error.URLError("dns secret"))
        ),
    )


@test("timeout is inconclusive and sanitized")
def _() -> None:
    expect_error(
        "network evidence unavailable",
        lambda: distribution.fetch_manifest_bytes(FakeOpener(TimeoutError("secret"))),
    )


@test("unexpected opener failure is inconclusive and sanitized")
def _() -> None:
    expect_error(
        "network evidence unavailable",
        lambda: distribution.fetch_manifest_bytes(
            FakeOpener(RuntimeError("raw exception credential-secret"))
        ),
    )


@test("exact manifest schema and tuple are accepted")
def _() -> None:
    assert parse() == {"version": VERSION, "url": ARCHIVE_URL, "sha512": SHA512}


@test("duplicate manifest key is rejected")
def _() -> None:
    raw = manifest_bytes()[:-1] + b',"version":"1.1.10"}'
    expect_error("invalid manifest document", lambda: distribution.parse_manifest_bytes(raw))


@test("missing manifest key is rejected")
def _() -> None:
    value = {"version": VERSION, "url": ARCHIVE_URL}
    expect_error(
        "invalid manifest schema",
        lambda: distribution.parse_manifest_bytes(json.dumps(value).encode()),
    )


@test("extra manifest key is rejected")
def _() -> None:
    expect_error("invalid manifest schema", lambda: parse(extra="no"))


@test("non-string version is rejected")
def _() -> None:
    expect_error("invalid manifest policy", lambda: parse(version=110))


@test("non-string URL is rejected")
def _() -> None:
    expect_error("invalid manifest policy", lambda: parse(url=[]))


@test("non-string SHA-512 is rejected")
def _() -> None:
    expect_error("invalid manifest policy", lambda: parse(sha512=None))


@test("padded value is rejected")
def _() -> None:
    expect_error("invalid manifest policy", lambda: parse(version=" 1.1.10"))


@test("escaped control character is rejected")
def _() -> None:
    expect_error("invalid manifest policy", lambda: parse(sha512=SHA512 + "\u0000"))


@test("invalid UTF-8 is rejected")
def _() -> None:
    expect_error(
        "invalid manifest document",
        lambda: distribution.parse_manifest_bytes(b"{\xff}"),
    )


@test("trailing second JSON document is rejected")
def _() -> None:
    expect_error(
        "invalid manifest document",
        lambda: distribution.parse_manifest_bytes(manifest_bytes() + b"{}"),
    )


@test("non-object JSON is rejected")
def _() -> None:
    expect_error("invalid manifest schema", lambda: distribution.parse_manifest_bytes(b"[]"))


@test("semantic version prerelease is rejected")
def _() -> None:
    expect_error("invalid manifest policy", lambda: parse(version="1.1.10-rc.1"))


@test("semantic version leading zero is rejected")
def _() -> None:
    expect_error("invalid manifest policy", lambda: parse(version="01.1.10"))


@test("uppercase SHA-512 is rejected")
def _() -> None:
    expect_error("invalid manifest policy", lambda: parse(sha512="A" * 128))


@test("short SHA-512 is rejected")
def _() -> None:
    expect_error("invalid manifest policy", lambda: parse(sha512="a" * 127))


@test("HTTP archive scheme is rejected")
def _() -> None:
    expect_error("invalid archive policy", lambda: parse(url=replace_url("https://", "http://")))


@test("wrong archive host is rejected")
def _() -> None:
    expect_error(
        "invalid archive policy",
        lambda: parse(url=replace_url("storage.googleapis.com", "example.invalid")),
    )


@test("archive URL userinfo is rejected")
def _() -> None:
    expect_error(
        "invalid archive policy",
        lambda: parse(url=replace_url("https://", "https://user:secret@")),
    )


@test("archive URL nondefault port is rejected")
def _() -> None:
    expect_error(
        "invalid archive policy",
        lambda: parse(url=replace_url("storage.googleapis.com", "storage.googleapis.com:444")),
    )


@test("archive URL query is rejected")
def _() -> None:
    expect_error("invalid archive policy", lambda: parse(url=ARCHIVE_URL + "?token=secret"))


@test("archive URL fragment is rejected")
def _() -> None:
    expect_error("invalid archive policy", lambda: parse(url=ARCHIVE_URL + "#secret"))


@test("encoded archive separator is rejected")
def _() -> None:
    expect_error(
        "invalid archive policy",
        lambda: parse(url=replace_url("/darwin-arm/", "%2fdarwin-arm/")),
    )


@test("encoded archive backslash is rejected")
def _() -> None:
    expect_error(
        "invalid archive policy",
        lambda: parse(url=replace_url("/darwin-arm/", "%5cdarwin-arm/")),
    )


@test("archive path traversal is rejected")
def _() -> None:
    expect_error(
        "invalid archive policy",
        lambda: parse(url=replace_url("/darwin-arm/", "/../darwin-arm/")),
    )


@test("encoded archive traversal is rejected")
def _() -> None:
    expect_error(
        "invalid archive policy",
        lambda: parse(url=replace_url("/darwin-arm/", "/%2e%2e/darwin-arm/")),
    )


@test("archive version mismatch is rejected")
def _() -> None:
    expect_error(
        "invalid archive policy",
        lambda: parse(url=replace_url("1.1.16-", "1.1.9-")),
    )


@test("nonnumeric archive build is rejected")
def _() -> None:
    expect_error(
        "invalid archive policy",
        lambda: parse(url=replace_url("6607970839166976", "build-secret")),
    )


@test("wrong archive platform directory is rejected")
def _() -> None:
    expect_error(
        "invalid archive policy",
        lambda: parse(url=replace_url("darwin-arm", "linux-x64")),
    )


@test("wrong archive filename is rejected")
def _() -> None:
    expect_error(
        "invalid archive policy",
        lambda: parse(url=replace_url("cli_mac_arm64.tar.gz", "cli_linux.tar.gz")),
    )


@test("exact trusted tuple matching baseline is unchanged")
def _() -> None:
    value = parse()
    assert distribution.evaluate_manifest(value, VERSION, value) == (0, VERSION)


@test("distribution version beyond baseline is drift-review")
def _() -> None:
    value = parse()
    status, detail = distribution.evaluate_manifest(value, "1.1.9", value)
    assert status == 3 and "1.1.16" in detail and "1.1.9" in detail


@test("same-version archive build drift is drift-review")
def _() -> None:
    observed = parse()
    snapshot = parse(url=replace_url("6607970839166976", "6607970839166975"))
    assert distribution.evaluate_manifest(observed, VERSION, snapshot)[0] == 3


@test("same-version archive hash drift is drift-review")
def _() -> None:
    observed = parse()
    snapshot = parse(sha512="b" * 128)
    assert distribution.evaluate_manifest(observed, VERSION, snapshot)[0] == 3


@test("production check makes no archive request")
def _() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        (root / "compat").mkdir()
        (root / "compat" / "agy-verified-version.txt").write_text(
            VERSION + "\n", encoding="ascii"
        )
        (root / "compat" / "agy-distribution-manifest.json").write_bytes(
            manifest_bytes()
        )
        opener = FakeOpener(FakeResponse(manifest_bytes()))
        assert distribution.check_production_manifest(root=root, opener=opener) == (
            0,
            VERSION,
        )
        assert [call[0].full_url for call in opener.calls] == [distribution.MANIFEST_URL]


@test("malformed local snapshot fails closed without bytes")
def _() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "snapshot.json"
        path.write_bytes(b'{"url":"credential-secret"}')
        try:
            distribution.read_snapshot(path)
        except distribution.DistributionEvidenceError as exc:
            assert exc.category == "invalid observational snapshot"
            assert "credential-secret" not in str(exc)
            return
    raise AssertionError("malformed snapshot was accepted")


@test("malformed verified baseline fails closed without bytes")
def _() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "baseline.txt"
        path.write_bytes(b"credential-secret\n")
        try:
            distribution.read_verified_version(path)
        except distribution.DistributionEvidenceError as exc:
            assert exc.category == "invalid verified baseline"
            assert "credential-secret" not in str(exc)
            return
    raise AssertionError("malformed baseline was accepted")


@test("top-level failure output leaks no body exception URL or credential")
def _() -> None:
    error = distribution.DistributionEvidenceError("network evidence unavailable")
    output = io.StringIO()
    with mock.patch.object(distribution, "check_production_manifest", side_effect=error):
        with mock.patch.object(sys, "argv", [str(MODULE_PATH)]):
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                try:
                    distribution.main()
                except SystemExit as exc:
                    assert exc.code == 2
                else:
                    raise AssertionError("main did not fail")
    rendered = output.getvalue()
    assert "evidence-unavailable" in rendered
    for forbidden in (
        distribution.MANIFEST_URL,
        ARCHIVE_URL,
        "credential",
        "Traceback",
        "secret",
    ):
        assert forbidden not in rendered


@test("unexpected top-level exception is sanitized without traceback")
def _() -> None:
    output = io.StringIO()
    error = RuntimeError("raw exception credential-secret https://example.invalid")
    with mock.patch.object(distribution, "check_production_manifest", side_effect=error):
        with mock.patch.object(sys, "argv", [str(MODULE_PATH)]):
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                try:
                    distribution.main()
                except SystemExit as exc:
                    assert exc.code == 2
                else:
                    raise AssertionError("main did not fail")
    rendered = output.getvalue()
    assert rendered == (
        "  distribution manifest: evidence-unavailable "
        "(internal evidence failure)\n"
    )


@test("direct CLI URL override is rejected without echo")
def _() -> None:
    output = io.StringIO()
    with mock.patch.object(
        sys, "argv", [str(MODULE_PATH), "--url", "https://example.invalid/secret"]
    ):
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            try:
                distribution.main()
            except SystemExit as exc:
                assert exc.code == 64
            else:
                raise AssertionError("invalid invocation was accepted")
    assert "example.invalid" not in output.getvalue()
    assert "secret" not in output.getvalue()


def main() -> None:
    passed = 0
    failed = 0
    for name, callback in TESTS:
        try:
            callback()
        except Exception as exc:  # test-only diagnostic boundary
            failed += 1
            print(f"  FAIL manifest: {name}: {type(exc).__name__}: {exc}")
        else:
            passed += 1
            print(f"  ok   manifest: {name}")
    print(f"MANIFEST_TEST_RESULT passed={passed} failed={failed}")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
