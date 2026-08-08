#!/usr/bin/env python3
"""Offline adversarial tests for the fixed GitHub compatibility evidence client."""

from __future__ import annotations

import importlib.util
import io
import json
import sys
import urllib.error
from pathlib import Path
from typing import Any, Callable, Optional


sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "official_github.py"
SPEC = importlib.util.spec_from_file_location("official_github_tested", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class Headers:
    def __init__(self, values: Optional[dict[str, Any]] = None):
        self.values = values or {}

    def get_all(self, name: str) -> Optional[list[str]]:
        value = self.values.get(name)
        if value is None:
            return None
        return value if isinstance(value, list) else [value]

    def get(self, name: str) -> Optional[str]:
        value = self.values.get(name)
        if isinstance(value, list):
            return value[0] if value else None
        return value


class Response:
    def __init__(
        self,
        body: bytes,
        *,
        status: int = 200,
        headers: Optional[dict[str, Any]] = None,
    ):
        self.body = body
        self.position = 0
        self.status = status
        base = {
            "Content-Type": "application/json; charset=utf-8",
            "Content-Length": str(len(body)),
        }
        if headers:
            base.update(headers)
        self.headers = Headers(base)

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            size = len(self.body) - self.position
        chunk = self.body[self.position : self.position + size]
        self.position += len(chunk)
        return chunk

    def getcode(self) -> int:
        return self.status

    def __enter__(self) -> "Response":
        return self

    def __exit__(self, *_args: object) -> None:
        return None


class Opener:
    def __init__(self, *results: Any):
        self.results = list(results)
        self.calls: list[tuple[str, float, dict[str, str]]] = []

    def open(self, request: Any, timeout: float) -> Response:
        self.calls.append((request.full_url, timeout, dict(request.header_items())))
        if not self.results:
            raise AssertionError("unexpected request")
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


def encoded(value: Any) -> bytes:
    return json.dumps(value, separators=(",", ":")).encode("utf-8")


def response(value: Any, **kwargs: Any) -> Response:
    return Response(encoded(value), **kwargs)


def release(tag: str, *, draft: Any = False, prerelease: Any = False) -> dict[str, Any]:
    return {
        "tag_name": tag,
        "draft": draft,
        "prerelease": prerelease,
        "body": "ignored public release prose",
    }


def source(revision: str = "a" * 40, *, ref: str = "refs/heads/main", kind: str = "commit") -> dict[str, Any]:
    return {"ref": ref, "object": {"sha": revision, "type": kind, "url": "ignored"}}


def commit(revision: str = "b" * 40) -> dict[str, Any]:
    return {"sha": revision, "commit": {"message": "ignored"}}


passed = 0
failed = 0


def check(name: str, predicate: Callable[[], bool]) -> None:
    global passed, failed
    try:
        result = bool(predicate())
    except Exception as exc:  # test harness reports controlled failures compactly
        result = False
        detail = f" ({type(exc).__name__}: {exc})"
    else:
        detail = ""
    if result:
        passed += 1
    else:
        failed += 1
        print(f"FAIL official github: {name}{detail}")


def rejects(category: str, action: Callable[[], Any]) -> bool:
    try:
        action()
    except MODULE.OfficialEvidenceError as exc:
        return exc.category == category
    return False


def collect(tool: str, release_value: Any, second: Any) -> tuple[tuple[str, str, str], Opener]:
    opener = Opener(response(release_value), response(second))
    return MODULE.latest_evidence(tool, opener=opener), opener


check(
    "agy stable release and source are canonical",
    lambda: collect("agy", release("1.1.11"), source())[0]
    == ("agy", "1.1.11", "a" * 40),
)
check(
    "agy accepts its documented optional v tag prefix",
    lambda: collect("agy", release("v1.1.11"), source())[0]
    == ("agy", "1.1.11", "a" * 40),
)
check(
    "codex stable tag and source are canonical",
    lambda: collect("codex", release("rust-v0.147.0"), source("c" * 40))[0]
    == ("codex", "0.147.0", "c" * 40),
)
check(
    "project latest release resolves to exact commit",
    lambda: collect("project", release("v0.1.0"), commit())[0]
    == ("project", "v0.1.0", "b" * 40),
)
check(
    "explicit project release resolves to exact commit",
    lambda: MODULE.project_release_evidence(
        "v1.2.3", opener=Opener(response(commit("d" * 40)))
    )
    == ("project", "v1.2.3", "d" * 40),
)
check(
    "inert extra API keys cannot change selected evidence",
    lambda: collect(
        "agy",
        {**release("1.1.11"), "credential": "not-rendered"},
        {**source(), "extra": {"sha": "f" * 40}},
    )[0]
    == ("agy", "1.1.11", "a" * 40),
)


def request_policy() -> bool:
    result, opener = collect("agy", release("1.1.11"), source())
    first_url, timeout, headers = opener.calls[0]
    return (
        result[0] == "agy"
        and first_url
        == "https://api.github.com/repos/google-antigravity/antigravity-cli/releases/latest"
        and timeout == MODULE.FETCH_TIMEOUT_SECONDS
        and headers.get("Accept") == "application/vnd.github+json"
        and headers.get("X-github-api-version") == "2022-11-28"
        and len(opener.calls) == 2
    )


check("request path, headers, timeout, and count are fixed", request_policy)
check(
    "vendor JSON content type is accepted",
    lambda: MODULE.fetch_json(
        Opener(
            response(
                {"ok": True},
                headers={"Content-Type": "application/vnd.github+json; charset=utf-8"},
            )
        ),
        "https://api.github.com/repos/openai/codex/releases/latest",
    )
    == {"ok": True},
)
check(
    "identity content encoding is accepted",
    lambda: MODULE.fetch_json(
        Opener(response({"ok": True}, headers={"Content-Encoding": "identity"})),
        "https://api.github.com/repos/openai/codex/releases/latest",
    )
    == {"ok": True},
)


def opener_policy() -> bool:
    opener = MODULE.build_fixed_opener()
    proxy_handlers = [
        handler for handler in opener.handlers if isinstance(handler, MODULE.urllib.request.ProxyHandler)
    ]
    return (
        not proxy_handlers
        and any(isinstance(handler, MODULE.RejectRedirects) for handler in opener.handlers)
    )


check("production opener disables proxies and installs redirect rejection", opener_policy)
check(
    "redirect handler refuses a second request",
    lambda: rejects(
        "redirect rejected",
        lambda: MODULE.RejectRedirects().redirect_request(None, None, 302, "", {}, "https://x"),
    ),
)


fixed_url_rejections = [
    "http://api.github.com/repos/openai/codex/releases/latest",
    "https://github.com/repos/openai/codex/releases/latest",
    "https://user@api.github.com/repos/openai/codex/releases/latest",
    "https://api.github.com:443/repos/openai/codex/releases/latest",
    "https://api.github.com/repos/openai/codex/releases/latest?token=x",
    "https://api.github.com/repos/openai/codex/releases/latest#x",
    "https://api.github.com/other/openai/codex/releases/latest",
    "https://api.github.com/repos/attacker/codex/releases/latest",
    "https://api.github.com/repos/openai/codex/%72eleases/latest",
    "https://api.github.com/repos/openai\\codex/releases/latest",
]
for index, url in enumerate(fixed_url_rejections, 1):
    check(
        f"fixed endpoint mutation {index} is rejected",
        lambda url=url: rejects(
            "invalid fixed endpoint", lambda: MODULE.fetch_json(Opener(), url)
        ),
    )


def fetch_reject(
    category: str,
    *,
    body: bytes = b"{}",
    status: int = 200,
    headers: Optional[dict[str, Any]] = None,
    result: Optional[BaseException] = None,
    limit: int = MODULE.MAX_RESPONSE_BYTES,
) -> bool:
    item: Any = result if result is not None else Response(body, status=status, headers=headers)
    return rejects(
        category,
        lambda: MODULE.fetch_json(
            Opener(item),
            "https://api.github.com/repos/openai/codex/releases/latest",
            limit=limit,
        ),
    )


check("non-200 response is unavailable", lambda: fetch_reject("HTTP evidence unavailable", status=500))
check(
    "HTTP redirect is rejected",
    lambda: fetch_reject(
        "redirect rejected",
        result=urllib.error.HTTPError("https://api.github.com", 302, "", {}, None),
    ),
)
check(
    "network error is unavailable",
    lambda: fetch_reject(
        "network evidence unavailable", result=urllib.error.URLError("private detail")
    ),
)
check("timeout is unavailable", lambda: fetch_reject("network evidence unavailable", result=TimeoutError()))
check(
    "missing content type is rejected",
    lambda: fetch_reject("invalid response metadata", headers={"Content-Type": []}),
)
check(
    "duplicate content type is rejected",
    lambda: fetch_reject(
        "invalid response metadata",
        headers={"Content-Type": ["application/json", "application/json"]},
    ),
)
check(
    "non-JSON content type is rejected",
    lambda: fetch_reject("invalid response metadata", headers={"Content-Type": "text/plain"}),
)
check(
    "unexpected charset is rejected",
    lambda: fetch_reject(
        "invalid response metadata", headers={"Content-Type": "application/json; charset=latin1"}
    ),
)
check(
    "compressed response is rejected",
    lambda: fetch_reject("invalid response metadata", headers={"Content-Encoding": "gzip"}),
)
check(
    "transfer encoding is rejected",
    lambda: fetch_reject("invalid response metadata", headers={"Transfer-Encoding": "chunked"}),
)
check(
    "missing content length is rejected",
    lambda: fetch_reject("invalid response metadata", headers={"Content-Length": []}),
)
check(
    "duplicate content length is rejected",
    lambda: fetch_reject(
        "invalid response metadata", headers={"Content-Length": ["2", "2"]}
    ),
)
check(
    "nonnumeric content length is rejected",
    lambda: fetch_reject("invalid response metadata", headers={"Content-Length": "two"}),
)
check(
    "zero content length is rejected",
    lambda: fetch_reject("invalid response metadata", headers={"Content-Length": "0"}),
)
check(
    "declared oversize is rejected before reading",
    lambda: fetch_reject(
        "response too large", headers={"Content-Length": "3"}, body=b"{}", limit=2
    ),
)
check(
    "actual oversize is rejected incrementally",
    lambda: fetch_reject(
        "response too large", headers={"Content-Length": "2"}, body=b"{}x", limit=2
    ),
)
check(
    "short body is rejected",
    lambda: fetch_reject(
        "invalid response metadata", headers={"Content-Length": "3"}, body=b"{}"
    ),
)
check("invalid UTF-8 is rejected", lambda: fetch_reject("invalid JSON document", body=b'"\xff"'))
check("malformed JSON is rejected", lambda: fetch_reject("invalid JSON document", body=b"{"))
check(
    "duplicate JSON keys are rejected",
    lambda: fetch_reject("invalid JSON document", body=b'{"x":1,"x":2}'),
)


release_rejections = [
    (release("1.1.11", draft=True), "draft"),
    (release("1.1.11", prerelease=True), "prerelease"),
    (release("1.1.11-rc.1"), "prerelease tag"),
    (release("01.1.11"), "leading zero"),
    ({"tag_name": "1.1.11", "draft": False}, "missing prerelease"),
    ({"tag_name": 111, "draft": False, "prerelease": False}, "nonstring tag"),
]
for value, label in release_rejections:
    check(
        f"{label} release evidence is rejected",
        lambda value=value: rejects(
            "invalid stable release evidence",
            lambda: MODULE._release(value, MODULE.POLICIES["agy"]),
        ),
    )


source_rejections = [
    (source(ref="refs/heads/dev"), "wrong ref"),
    (source(kind="tag"), "wrong type"),
    (source("A" * 40), "uppercase revision"),
    (source("a" * 39), "short revision"),
    ({"ref": "refs/heads/main", "object": "bad"}, "nonobject target"),
]
for value, label in source_rejections:
    check(
        f"{label} source evidence is rejected",
        lambda value=value: rejects(
            "invalid source evidence", lambda: MODULE._source_ref(value, "main")
        ),
    )

check(
    "malformed release commit is rejected",
    lambda: rejects("invalid release commit evidence", lambda: MODULE._commit({"sha": "secret"})),
)
check(
    "unknown tool cannot select an endpoint",
    lambda: rejects("invalid tool policy", lambda: MODULE.latest_evidence("unknown", opener=Opener())),
)
check(
    "unstable explicit project tag is rejected before request",
    lambda: rejects(
        "invalid project release tag",
        lambda: MODULE.project_release_evidence("v1.2.3-rc.1", opener=Opener()),
    ),
)
check(
    "invalid CLI invocation is controlled",
    lambda: MODULE.main([str(MODULE_PATH), "--latest", "unknown"]) == 2,
)

print(f"OFFICIAL_GITHUB_TEST_RESULT passed={passed} failed={failed}")
raise SystemExit(1 if failed else 0)
