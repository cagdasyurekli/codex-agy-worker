#!/usr/bin/env python3
"""Parse bounded, owner-captured ``agy models`` inventory evidence.

This module is deliberately offline.  It recognizes only the reviewed canonical
slugs below; display text is never promoted into model-selection metadata.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Iterable, Tuple


MAX_INVENTORY_BYTES = 64 * 1024
EXPECTED_SLUGS: Tuple[str, ...] = (
    "gemini-3.6-flash-low",
    "gemini-3.6-flash-medium",
    "gemini-3.6-flash-high",
    "gemini-3.5-flash-low",
    "gemini-3.5-flash-medium",
    "gemini-3.5-flash-high",
    "gemini-3.1-pro-low",
    "gemini-3.1-pro-high",
    "claude-sonnet-4-6",
    "claude-opus-4-6-thinking",
    "gpt-oss-120b-medium",
)
DISPLAY_ALIASES = {"gpt-oss": "gpt-oss-120b-medium"}
RESERVED_MODEL_NAMESPACES = ("gemini", "claude", "gpt")
TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9._-])([a-z0-9][a-z0-9._-]*[a-z0-9])"
    r"(?![A-Za-z0-9._-])"
)


class InventoryEvidenceError(ValueError):
    """The inventory cannot be interpreted without ambiguity."""


@dataclass(frozen=True)
class InventoryEvidence:
    """Sanitized semantic fields derived from one complete inventory."""

    slugs: Tuple[str, ...]
    normalized_sha256: str
    line_count: int


def _error() -> InventoryEvidenceError:
    return InventoryEvidenceError("invalid agy inventory evidence")


def _tokens(line: str) -> Tuple[str, ...]:
    return tuple(match.group(1) for match in TOKEN_RE.finditer(line))


def _canonical_for_line(line: str) -> str:
    tokens = _tokens(line)
    slug_candidates = tuple(
        token
        for token in tokens
        if token not in DISPLAY_ALIASES
        and (
            token.count("-") >= 2
            or token.partition("-")[0] in RESERVED_MODEL_NAMESPACES
        )
    )
    canonical = tuple(token for token in slug_candidates if token in EXPECTED_SLUGS)
    unknown = tuple(token for token in slug_candidates if token not in EXPECTED_SLUGS)
    if unknown or len(canonical) != 1:
        raise _error()

    selected = canonical[0]
    if slug_candidates.count(selected) != 1:
        raise _error()

    for alias, target in DISPLAY_ALIASES.items():
        alias_count = tokens.count(alias)
        if alias_count and (alias_count != 1 or selected != target):
            raise _error()
    return selected


def _normalized_bytes(slugs: Iterable[str]) -> bytes:
    return "".join(f"{slug}\n" for slug in sorted(slugs)).encode("ascii")


def parse_inventory_bytes(raw: bytes) -> InventoryEvidence:
    """Return sanitized semantics for one complete bounded inventory document."""
    if not isinstance(raw, bytes) or not raw or len(raw) > MAX_INVENTORY_BYTES:
        raise _error()
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise _error() from None
    if not text.endswith("\n") or "\r" in text:
        raise _error()
    if any(
        (ord(character) < 0x20 and character not in ("\t", "\n"))
        or ord(character) == 0x7F
        for character in text
    ):
        raise _error()

    lines = text[:-1].split("\n")
    if len(lines) != len(EXPECTED_SLUGS) or any(not line.strip() for line in lines):
        raise _error()

    observed = tuple(_canonical_for_line(line) for line in lines)
    if len(set(observed)) != len(observed) or set(observed) != set(EXPECTED_SLUGS):
        raise _error()

    normalized = _normalized_bytes(observed)
    return InventoryEvidence(
        slugs=tuple(sorted(observed)),
        normalized_sha256=hashlib.sha256(normalized).hexdigest(),
        line_count=len(lines),
    )
