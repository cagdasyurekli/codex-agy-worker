#!/usr/bin/env python3
"""Historical assertions for the superseded agy 1.1.16 activation record."""

from __future__ import annotations

from pathlib import Path
from typing import Callable


ROOT = Path(__file__).resolve().parent.parent
REVIEW = ROOT / "compat" / "reviews" / "agy-1.1.16.md"
ACTIVATION = ROOT / "compat" / "reviews" / "agy-1.1.22-activation.md"
VERSION = "1.1.16"
REVISION = "efa16f096dc02fb654b7e86958d268195284d014"
SOURCE_SHA256 = "095705beb4e4591c8ee7f8b6261473e15228f0f4b1bec58c62c966a6d4bfab30"
CAPTURE_SHA256 = "04f9cf2d18c14635689630c7bb50437151f2b0eb1d414d0d943212fe12c7a20e"
MATRIX_SHA256 = "a586927552d90295529f3059989a2a8c36c234d41b8f79d61c1c89edbf829e00"
BINDING_SHA256 = "3f34e6f6bfcf7b7e65951e02f92580c2858f32016f115866160f279d2d3a2747"

TESTS: list[tuple[str, Callable[[], None]]] = []


def test(name: str) -> Callable[[Callable[[], None]], Callable[[], None]]:
    def register(callback: Callable[[], None]) -> Callable[[], None]:
        TESTS.append((name, callback))
        return callback

    return register


@test("historical version and release revision remain exact")
def _() -> None:
    text = REVIEW.read_text()
    assert VERSION in text and REVISION in text


@test("historical source and capture hashes remain exact")
def _() -> None:
    text = REVIEW.read_text()
    assert SOURCE_SHA256 in text and CAPTURE_SHA256 in text


@test("historical matrix and binding digests remain exact")
def _() -> None:
    text = REVIEW.read_text()
    assert MATRIX_SHA256 in text and BINDING_SHA256 in text


@test("historical inventory remains fourteen exact slugs")
def _() -> None:
    assert "same fourteen exact slugs" in REVIEW.read_text()


@test("historical record retains its activation decision")
def _() -> None:
    assert "Human reconciliation accepted the exact inventory binding" in REVIEW.read_text()


@test("historical record is not rewritten as the current baseline")
def _() -> None:
    assert "This record advances the active agy baseline" in REVIEW.read_text()
    assert (ROOT / "compat" / "agy-verified-version.txt").read_text() == "1.1.22\n"


@test("new activation is a separate additive record")
def _() -> None:
    assert REVIEW.is_file() and ACTIVATION.is_file()
    assert "supplements rather than rewrites" in ACTIVATION.read_text()


@test("historical record retains its bounded limitations")
def _() -> None:
    text = REVIEW.read_text()
    assert "does not prove provider/backend identity" in text
    assert "does not authorize a provider call" in text


def main() -> None:
    passed = 0
    failed = 0
    for name, callback in TESTS:
        try:
            callback()
        except Exception as exc:
            failed += 1
            print(f"  FAIL historical activation: {name}: {type(exc).__name__}: {exc}")
        else:
            passed += 1
            print(f"  ok   historical activation: {name}")
    print(f"AGY_1_1_16_ACTIVATION_TEST_RESULT passed={passed} failed={failed}")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
