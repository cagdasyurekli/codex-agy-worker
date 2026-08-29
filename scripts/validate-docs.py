#!/usr/bin/env python3
"""Validate durable README and local documentation invariants without dependencies."""

from __future__ import annotations

import argparse
import os
import re
import sys
import urllib.parse
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path


ONBOARDING_MARKERS = (
    ("positioning", "A Codex Agent Skill for bounded Antigravity CLI delegation"),
    (
        "workflow badge",
        "[![Offline test workflow](https://github.com/cagdasyurekli/codex-agy-worker/"
        "actions/workflows/test.yml/badge.svg)](https://github.com/cagdasyurekli/"
        "codex-agy-worker/actions/workflows/test.yml)",
    ),
    (
        "license badge",
        "[![License: MIT](https://img.shields.io/badge/license-MIT-yellow.svg)](LICENSE)",
    ),
    ("quick-start heading", "## Quick start"),
    ("prerequisites", "Requires a POSIX-compatible environment"),
    ("marketplace add", "codex plugin marketplace add cagdasyurekli/codex-agy-worker"),
    ("plugin add", "codex plugin add codex-agy-worker@codex-agy-worker"),
    ("GitHub fallback", "git clone https://github.com/cagdasyurekli/codex-agy-worker.git"),
    (
        "installation authorization boundary",
        "does not authorize a provider dispatch or repository transmission",
    ),
    ("offline proof", "./proof-demo.sh"),
    ("provider privacy warning", "Before an agy-backed request"),
    ("safe natural-language task", "> Use the agy-worker skill"),
    (
        "verification tutorial",
        "[Learn how to verify an agent candidate without trusting its report]"
        "(docs/VERIFYING_AGENT_OUTPUT.md)",
    ),
)

FENCE_ALLOWED_ONBOARDING = {
    "marketplace add",
    "plugin add",
    "GitHub fallback",
    "offline proof",
}
STANDALONE_ONBOARDING_SUFFIX = {
    "workflow badge": "",
    "license badge": "",
    "quick-start heading": "",
    "verification tutorial": ".",
}

HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
HTML_COMMENT_RE = re.compile(r"<!--.*?(?:-->|$)", flags=re.DOTALL)
MARKDOWN_SUFFIXES = {".md", ".markdown", ".mdown", ".mkd", ".mkdn"}
FORBIDDEN_DOC_TOKENS = {"campaign", "draft", "drafts", "private", "report", "reports"}
DATED_PATH_RE = re.compile(r"(?:^|[^0-9])20[0-9]{2}-[0-9]{2}-[0-9]{2}(?:[^0-9]|$)")
PAGES_BASE = "https://cagdasyurekli.github.io/codex-agy-worker/"


def validate_onboarding(readme: str, max_lines: int) -> list[str]:
    """Return ordered-onboarding and line-budget violations."""

    raw_lines = readme.splitlines()
    visible_readme = HTML_COMMENT_RE.sub(
        lambda match: "\n" * match.group(0).count("\n"), readme
    )
    lines = visible_readme.splitlines()
    errors: list[str] = []
    if len(raw_lines) > max_lines:
        errors.append(f"README has {len(raw_lines)} lines; maximum is {max_lines}")

    first_screen: list[tuple[str, bool]] = []
    fence: str | None = None
    for line in lines[:120]:
        stripped = line.lstrip()
        if stripped.startswith(("```", "~~~")):
            marker = stripped[:3]
            fence = None if fence == marker else marker if fence is None else fence
            first_screen.append((line, True))
            continue
        first_screen.append((line, fence is not None))

    positions: list[int] = []
    for label, marker in ONBOARDING_MARKERS:
        standalone = STANDALONE_ONBOARDING_SUFFIX.get(label)
        matches = [
            index
            for index, (line, in_fence) in enumerate(first_screen, start=1)
            if (line.strip() == marker + standalone if standalone is not None else marker in line)
            and (not in_fence or label in FENCE_ALLOWED_ONBOARDING)
        ]
        if not matches:
            errors.append(f"README first 120 lines omit {label}: {marker!r}")
            continue
        positions.append(matches[0])

    if len(positions) == len(ONBOARDING_MARKERS) and positions != sorted(positions):
        errors.append(f"README onboarding markers are out of order: {positions}")
    return errors


def _heading_slugs(text: str) -> set[str]:
    """Approximate GitHub Markdown heading anchors, including duplicate suffixes."""

    slugs: set[str] = set()
    counts: Counter[str] = Counter()
    fence: str | None = None
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith(("```", "~~~")):
            marker = stripped[:3]
            fence = None if fence == marker else marker if fence is None else fence
            continue
        if fence is not None:
            continue
        match = HEADING_RE.match(line)
        if match is None:
            continue
        heading = re.sub(r"<[^>]+>", "", match.group(2))
        heading = re.sub(r"[`*_~]", "", heading).strip().lower()
        base = re.sub(r"[^\w\- ]", "", heading, flags=re.UNICODE)
        base = re.sub(r"\s+", "-", base)
        suffix = counts[base]
        counts[base] += 1
        slugs.add(base if suffix == 0 else f"{base}-{suffix}")
    return slugs


def _resolve_markdown_target(
    source: Path, target: str, root: Path
) -> tuple[Path, str, bool] | None:
    target = target.strip("<>")
    parsed = urllib.parse.urlsplit(target)
    if parsed.scheme or parsed.netloc or target.startswith("//"):
        return None
    if "{" in target or "}" in target:
        return None
    relative = urllib.parse.unquote(parsed.path)
    relative = re.sub(r"\\([!\"#$%&'()*+,\-./:;<=>?@[\\\]^_`{|}~])", r"\1", relative)
    if relative.startswith("/"):
        return root, urllib.parse.unquote(parsed.fragment), True
    path = source if not relative else source.parent / relative
    return path, urllib.parse.unquote(parsed.fragment), False


def _path_case_status(path: Path, root: Path) -> str:
    """Return exact, case-mismatch, missing, or outside using directory entries."""

    normalized = Path(os.path.normpath(path))
    try:
        relative = normalized.relative_to(root)
    except ValueError:
        return "outside"

    current = root
    for part in relative.parts:
        try:
            names = [entry.name for entry in current.iterdir()]
        except OSError:
            return "missing"
        if part in names:
            current /= part
            continue
        if any(name.casefold() == part.casefold() for name in names):
            return "case-mismatch"
        return "missing"
    return "exact"


def _inline_link_targets(line: str) -> list[str]:
    """Return destinations from single-line inline links with balanced parentheses."""

    targets: list[str] = []
    cursor = 0
    while True:
        opening = line.find("](", cursor)
        if opening < 0:
            return targets
        index = opening + 2
        while index < len(line) and line[index].isspace():
            index += 1
        if index >= len(line):
            return targets
        if line[index] == "<":
            end = index + 1
            while end < len(line) and line[end] != ">":
                end += 2 if line[end] == "\\" and end + 1 < len(line) else 1
            if end < len(line):
                targets.append(line[index + 1 : end])
                cursor = end + 1
                continue
            cursor = index + 1
            continue

        start = index
        depth = 0
        while index < len(line):
            character = line[index]
            if character == "\\" and index + 1 < len(line):
                index += 2
                continue
            if character == "(":
                depth += 1
            elif character == ")":
                if depth == 0:
                    break
                depth -= 1
            elif character.isspace() and depth == 0:
                break
            index += 1
        if index > start:
            targets.append(line[start:index])
        cursor = max(index + 1, opening + 2)


def validate_markdown_links(root: Path) -> list[str]:
    """Validate inline repository-local README/docs links and Markdown anchors."""

    root = root.resolve()
    errors: list[str] = []
    docs_sources = sorted(
        path
        for path in (root / "docs").rglob("*")
        if path.is_file() and not path.is_symlink() and path.suffix.lower() in MARKDOWN_SUFFIXES
    )
    sources = [root / "README.md", *docs_sources]
    anchor_cache: dict[Path, set[str]] = {}
    for source in sources:
        text = source.read_text(encoding="utf-8")
        for line_number, line in enumerate(text.splitlines(), start=1):
            for link_target in _inline_link_targets(line):
                resolved = _resolve_markdown_target(source, link_target, root)
                if resolved is None:
                    continue
                target, fragment, root_relative = resolved
                if root_relative:
                    errors.append(
                        f"{source.relative_to(root)}:{line_number}: root-relative local link "
                        f"is unsupported: {link_target!r}"
                    )
                    continue
                resolved_target = target.resolve()
                if not resolved_target.is_relative_to(root):
                    errors.append(
                        f"{source.relative_to(root)}:{line_number}: local link escapes "
                        f"the repository: {link_target!r}"
                    )
                    continue
                case_status = _path_case_status(target, root)
                if case_status == "case-mismatch":
                    errors.append(
                        f"{source.relative_to(root)}:{line_number}: local link target uses "
                        f"non-exact path casing: {link_target!r}"
                    )
                    continue
                if case_status != "exact":
                    errors.append(
                        f"{source.relative_to(root)}:{line_number}: missing local link target "
                        f"{link_target!r}"
                    )
                    continue
                if (
                    fragment
                    and resolved_target.is_file()
                    and resolved_target.suffix.lower() in MARKDOWN_SUFFIXES
                ):
                    slugs = anchor_cache.setdefault(
                        resolved_target,
                        _heading_slugs(resolved_target.read_text(encoding="utf-8")),
                    )
                    if fragment not in slugs:
                        errors.append(
                            f"{source.relative_to(root)}:{line_number}: missing Markdown anchor "
                            f"#{fragment} in {resolved_target.relative_to(root)}"
                        )
    return errors


def validate_pages_sitemap(root: Path) -> list[str]:
    """Bind each owned sitemap URL to a checked-in Pages Markdown source."""

    sitemap = root / "docs" / "sitemap.xml"
    if not sitemap.is_file() or sitemap.is_symlink():
        return ["docs/sitemap.xml must be a regular file"]
    try:
        tree = ET.parse(sitemap)
    except (ET.ParseError, OSError) as exc:
        return [f"cannot parse docs/sitemap.xml: {exc}"]

    namespace = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    if tree.getroot().tag != "{http://www.sitemaps.org/schemas/sitemap/0.9}urlset":
        return ["docs/sitemap.xml must use the sitemap urlset namespace"]
    urls = [element.text or "" for element in tree.findall("s:url/s:loc", namespace)]
    errors: list[str] = []
    if not urls:
        errors.append("docs/sitemap.xml must contain at least one owned URL")
    if len(urls) != len(set(urls)):
        errors.append("docs/sitemap.xml contains duplicate URLs")
    for url in urls:
        if not url.startswith(PAGES_BASE):
            errors.append(f"sitemap URL leaves the owned Pages base: {url!r}")
            continue
        relative = url.removeprefix(PAGES_BASE)
        if relative == "":
            source = root / "docs" / "index.md"
        elif relative.endswith(".html") and "/" not in relative:
            source = root / "docs" / f"{relative[:-5]}.md"
        else:
            errors.append(f"sitemap URL has no supported Markdown mapping: {url!r}")
            continue
        case_status = _path_case_status(source, root)
        if case_status == "case-mismatch":
            errors.append(f"sitemap URL {url!r} has a source with non-exact path casing")
        elif case_status != "exact" or not source.is_file():
            errors.append(f"sitemap URL {url!r} has no source {source.relative_to(root)}")
    return errors


def _forbidden_docs_path(relative: str) -> bool:
    """Identify high-signal owner-private report paths independently of allowlisting."""

    lowered = relative.lower()
    tokens = set(re.split(r"[^a-z0-9]+", lowered))
    return bool(tokens & FORBIDDEN_DOC_TOKENS) or DATED_PATH_RE.search(lowered) is not None


def validate_public_docs_inventory(root: Path) -> list[str]:
    """Require every docs file to be deliberately public and allowlisted."""

    root = root.resolve()
    docs = root / "docs"
    allowlist_path = docs / "public-files.allowlist"
    if not allowlist_path.is_file() or allowlist_path.is_symlink():
        return ["docs/public-files.allowlist must be a regular file"]

    raw_entries = [
        line.strip()
        for line in allowlist_path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    errors: list[str] = []
    if raw_entries != sorted(raw_entries):
        errors.append("docs/public-files.allowlist must be sorted")
    if len(raw_entries) != len(set(raw_entries)):
        errors.append("docs/public-files.allowlist contains duplicate paths")

    allowed: set[str] = set()
    for entry in raw_entries:
        candidate = Path(entry)
        if (
            candidate.is_absolute()
            or not entry.startswith("docs/")
            or candidate.as_posix() != entry
            or ".." in candidate.parts
        ):
            errors.append(f"invalid public docs allowlist entry: {entry!r}")
            continue
        resolved = (root / candidate).resolve()
        if not resolved.is_relative_to(docs):
            errors.append(f"public docs allowlist entry escapes docs/: {entry!r}")
            continue
        if _forbidden_docs_path(entry):
            errors.append(f"forbidden private/report/draft/dated docs path: {entry!r}")
        allowed.add(entry)
        source = root / candidate
        if not source.is_file() or source.is_symlink():
            errors.append(f"allowlisted public docs path must be a regular file: {entry!r}")

    actual: set[str] = set()
    for source in docs.rglob("*"):
        if not source.is_file() and not source.is_symlink():
            continue
        relative = source.relative_to(root).as_posix()
        actual.add(relative)
        if source.is_symlink() or not source.is_file():
            errors.append(f"public docs path must be a regular file: {relative!r}")
        if _forbidden_docs_path(relative):
            errors.append(f"forbidden private/report/draft/dated docs path: {relative!r}")

    for extra in sorted(actual - allowed):
        errors.append(f"docs path is not public-allowlisted: {extra!r}")
    for missing in sorted(allowed - actual):
        errors.append(f"public-allowlisted docs path is missing: {missing!r}")
    return errors


def validate(root: Path, readme_max_lines: int) -> list[str]:
    root = root.resolve()
    readme = root / "README.md"
    if not readme.is_file() or readme.is_symlink():
        return ["README.md must be a regular file"]
    errors = validate_onboarding(readme.read_text(encoding="utf-8"), readme_max_lines)
    errors.extend(validate_public_docs_inventory(root))
    errors.extend(validate_markdown_links(root))
    errors.extend(validate_pages_sitemap(root))
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--readme-max-lines", type=int, required=True)
    args = parser.parse_args(argv)
    if args.readme_max_lines <= 0:
        parser.error("--readme-max-lines must be positive")
    errors = validate(args.root, args.readme_max_lines)
    if errors:
        for error in errors:
            print(f"error: {error}", file=sys.stderr)
        return 1
    print(
        "ok: complete public docs inventory, README onboarding order and line budget, inline "
        "local Markdown links/anchors, and Pages sitemap mappings are valid"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
