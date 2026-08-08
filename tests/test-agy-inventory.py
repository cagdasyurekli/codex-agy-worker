#!/usr/bin/env python3
"""Offline adversarial tests for semantic ``agy models`` inventory parsing."""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from typing import Callable

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parent.parent
MODULE_PATH = ROOT / "scripts" / "agy_inventory.py"
SPEC = importlib.util.spec_from_file_location("agy_inventory", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise SystemExit("cannot load agy inventory module")
inventory = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = inventory
SPEC.loader.exec_module(inventory)

EXPECTED_HASH = "8d46bcac6b8f27995635d91dc6f5a0e549d351e707efe11a82d8b6593fe12daf"


def fixture_lines() -> list[str]:
    lines = [f"available model\t{slug}" for slug in inventory.EXPECTED_SLUGS]
    index = inventory.EXPECTED_SLUGS.index("gpt-oss-120b-medium")
    lines[index] = "gpt-oss display\tgpt-oss-120b-medium"
    return lines


def encode(lines: list[str]) -> bytes:
    return ("\n".join(lines) + "\n").encode("utf-8")


def replace_line(lines: list[str], needle: str, replacement: str) -> list[str]:
    result = list(lines)
    index = next(i for i, line in enumerate(result) if needle in line)
    result[index] = replacement
    return result


def expect_error(raw: bytes) -> None:
    try:
        inventory.parse_inventory_bytes(raw)
    except inventory.InventoryEvidenceError as exc:
        assert str(exc) == "invalid agy inventory evidence"
        return
    raise AssertionError("expected controlled inventory rejection")


TESTS: list[tuple[str, Callable[[], None]]] = []


def test(name: str) -> Callable[[Callable[[], None]], Callable[[], None]]:
    def register(callback: Callable[[], None]) -> Callable[[], None]:
        TESTS.append((name, callback))
        return callback

    return register


@test("reviewed canonical inventory and colocated display alias are accepted")
def _() -> None:
    result = inventory.parse_inventory_bytes(encode(fixture_lines()))
    assert result.slugs == tuple(sorted(inventory.EXPECTED_SLUGS))
    assert result.line_count == 11


@test("corrected canonical normalized hash is pinned")
def _() -> None:
    result = inventory.parse_inventory_bytes(encode(fixture_lines()))
    assert result.normalized_sha256 == EXPECTED_HASH


@test("inventory ordering does not change canonical hash")
def _() -> None:
    result = inventory.parse_inventory_bytes(encode(list(reversed(fixture_lines()))))
    assert result.normalized_sha256 == EXPECTED_HASH


@test("standalone display alias line is rejected")
def _() -> None:
    expect_error(encode(fixture_lines() + ["gpt-oss"]))


@test("display alias without its canonical slug is rejected")
def _() -> None:
    lines = replace_line(fixture_lines(), "gpt-oss-120b-medium", "gpt-oss display")
    expect_error(encode(lines))


@test("display alias beside a different canonical slug is rejected")
def _() -> None:
    lines = replace_line(
        fixture_lines(),
        "gemini-3.6-flash-low",
        "gpt-oss display\tgemini-3.6-flash-low",
    )
    expect_error(encode(lines))


@test("repeated display alias is rejected")
def _() -> None:
    lines = replace_line(
        fixture_lines(),
        "gpt-oss-120b-medium",
        "gpt-oss gpt-oss\tgpt-oss-120b-medium",
    )
    expect_error(encode(lines))


@test("missing canonical line is rejected")
def _() -> None:
    expect_error(encode(fixture_lines()[:-1]))


@test("duplicate canonical line is rejected")
def _() -> None:
    lines = fixture_lines()
    lines[-1] = lines[0]
    expect_error(encode(lines))


@test("extra line is rejected")
def _() -> None:
    expect_error(encode(fixture_lines() + ["safe display only"]))


@test("two canonical slugs on one line are rejected")
def _() -> None:
    lines = replace_line(
        fixture_lines(),
        "gemini-3.6-flash-low",
        "gemini-3.6-flash-low gemini-3.6-flash-medium",
    )
    expect_error(encode(lines))


@test("same canonical slug twice on one line is rejected")
def _() -> None:
    lines = replace_line(
        fixture_lines(),
        "gemini-3.6-flash-low",
        "gemini-3.6-flash-low gemini-3.6-flash-low",
    )
    expect_error(encode(lines))


@test("unknown slug-shaped token beside a canonical slug is rejected")
def _() -> None:
    lines = replace_line(
        fixture_lines(),
        "gemini-3.6-flash-low",
        "gemini-9.9-unknown gemini-3.6-flash-low",
    )
    expect_error(encode(lines))


@test("ordinary one-hyphen display label is accepted")
def _() -> None:
    lines = replace_line(
        fixture_lines(),
        "gemini-3.6-flash-low",
        "vendor-label display\tgemini-3.6-flash-low",
    )
    result = inventory.parse_inventory_bytes(encode(lines))
    assert result.normalized_sha256 == EXPECTED_HASH


@test("nearby non-provider namespace is not overclassified")
def _() -> None:
    lines = replace_line(
        fixture_lines(),
        "gemini-3.6-flash-low",
        "geminix-unknown display\tgemini-3.6-flash-low",
    )
    result = inventory.parse_inventory_bytes(encode(lines))
    assert result.normalized_sha256 == EXPECTED_HASH


@test("unknown one-hyphen Gemini model token is rejected")
def _() -> None:
    lines = replace_line(
        fixture_lines(),
        "gemini-3.6-flash-low",
        "gemini-unknown gemini-3.6-flash-low",
    )
    expect_error(encode(lines))


@test("unknown one-hyphen Claude model token is rejected")
def _() -> None:
    lines = replace_line(
        fixture_lines(),
        "claude-sonnet-4-6",
        "claude-unknown claude-sonnet-4-6",
    )
    expect_error(encode(lines))


@test("unknown one-hyphen GPT model token is rejected")
def _() -> None:
    lines = replace_line(
        fixture_lines(),
        "gpt-oss-120b-medium",
        "gpt-unknown gpt-oss display\tgpt-oss-120b-medium",
    )
    expect_error(encode(lines))


@test("reserved-namespace mutation reintroduces one-hyphen ambiguity")
def _() -> None:
    lines = replace_line(
        fixture_lines(),
        "gemini-3.6-flash-low",
        "gemini-unknown gemini-3.6-flash-low",
    )
    expect_error(encode(lines))
    original = inventory.RESERVED_MODEL_NAMESPACES
    try:
        inventory.RESERVED_MODEL_NAMESPACES = ()
        result = inventory.parse_inventory_bytes(encode(lines))
    finally:
        inventory.RESERVED_MODEL_NAMESPACES = original
    assert result.normalized_sha256 == EXPECTED_HASH


@test("unknown slug-shaped extra line is rejected")
def _() -> None:
    lines = fixture_lines()
    lines[-1] = "vendor-next-model-high"
    expect_error(encode(lines))


@test("canonical prefix extended into a longer token is rejected")
def _() -> None:
    lines = replace_line(
        fixture_lines(),
        "gemini-3.6-flash-low",
        "gemini-3.6-flash-low-preview",
    )
    expect_error(encode(lines))


@test("exact token matching catches the naive longest-prefix mutation")
def _() -> None:
    mutated = "gemini-3.6-flash-low-preview"
    lines = replace_line(fixture_lines(), "gemini-3.6-flash-low", mutated)
    expect_error(encode(lines))
    alternatives = "|".join(
        re.escape(slug)
        for slug in sorted(inventory.EXPECTED_SLUGS, key=len, reverse=True)
    )
    prefix_only = re.compile(
        rf"(?<![A-Za-z0-9._-])({alternatives})"
    )
    original = inventory.TOKEN_RE
    try:
        inventory.TOKEN_RE = prefix_only
        result = inventory.parse_inventory_bytes(encode(lines))
    finally:
        inventory.TOKEN_RE = original
    assert result.normalized_sha256 == EXPECTED_HASH


@test("malformed UTF-8 is rejected")
def _() -> None:
    expect_error(encode(fixture_lines())[:-1] + b"\xff\n")


@test("NUL control byte is rejected")
def _() -> None:
    expect_error(encode(fixture_lines()).replace(b"available", b"available\x00", 1))


@test("DEL control byte is rejected")
def _() -> None:
    expect_error(encode(fixture_lines()).replace(b"available", b"available\x7f", 1))


@test("CRLF evidence is rejected")
def _() -> None:
    expect_error(encode(fixture_lines()).replace(b"\n", b"\r\n"))


@test("missing final newline is rejected")
def _() -> None:
    expect_error(encode(fixture_lines())[:-1])


@test("blank line is rejected")
def _() -> None:
    lines = fixture_lines()
    lines[0] = ""
    expect_error(encode(lines))


@test("empty evidence is rejected")
def _() -> None:
    expect_error(b"")


@test("evidence exactly at byte limit is accepted")
def _() -> None:
    raw = encode(fixture_lines())
    padding = inventory.MAX_INVENTORY_BYTES - len(raw)
    lines = fixture_lines()
    lines[0] = (" " * padding) + lines[0]
    assert len(encode(lines)) == inventory.MAX_INVENTORY_BYTES
    inventory.parse_inventory_bytes(encode(lines))


@test("evidence over byte limit is rejected")
def _() -> None:
    raw = encode(fixture_lines())
    padding = inventory.MAX_INVENTORY_BYTES + 1 - len(raw)
    lines = fixture_lines()
    lines[0] = (" " * padding) + lines[0]
    expect_error(encode(lines))


@test("non-bytes input fails closed")
def _() -> None:
    expect_error("not bytes")  # type: ignore[arg-type]


passed = 0
failed = 0
for name, callback in TESTS:
    try:
        callback()
    except Exception as exc:
        failed += 1
        print(f"FAIL {name}: {exc}")
    else:
        passed += 1
        print(f"ok   {name}")

print(f"AGY_INVENTORY_TEST_RESULT passed={passed} failed={failed}")
raise SystemExit(1 if failed else 0)
