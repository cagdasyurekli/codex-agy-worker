#!/usr/bin/env python3
"""Focused offline tests for the active agy 1.1.24 compatibility binding."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import tempfile
from pathlib import Path
from typing import Callable


ROOT = Path(__file__).resolve().parent.parent
RUNTIME = ROOT / "skills" / "agy-worker" / "runtime"
MODULE_PATH = RUNTIME / "scripts" / "compatibility.py"
SPEC = importlib.util.spec_from_file_location("agy_1_1_24_compatibility", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise SystemExit("cannot load compatibility module")
compatibility = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(compatibility)

VERSION = "1.1.24"
REVISION = "bf27ce1134b4ead2f7bfa0a4fb3cb5fcbebcaa5a"
SOURCE_SHA256 = "4d1138b2dbde56127969fd307281494d4a7dcc22759ce9adb44d36247df86151"
VERSION_BINDING_SHA256 = "8d67b9e301c7fa117c44d0cc35ecb23602dcd940814e4569a5a4eb5e54dadb74"
CAPTURE_SHA256 = "03b97e0266acf0f162f06e9da3857f75078dc3e2506d5964d1a09e044ad3403a"
STDOUT_SHA256 = "d02970e6b6b4e0910461999afca8fb99d757e9094ab2874b557dad18fc75464a"
RESPONSE_SHA256 = "b1cc011310435afa07b1e132a5b7f3e22297aa21427177461c858bcbd6a58794"
NORMALIZED_SHA256 = "d5e58ab55e91ebd4a2cd23841c76cbe12b47d607c62cd8c834fc8f6b9f078ad7"
MATRIX_SHA256 = "e3768004b4685754ba5bfd72e75724a2c78b0b9ed78391b0363b5f3d3ff191f1"
BINDING_SHA256 = "0173be39149bfceac7dbbafae6335f2e95d60b2e482bcd25a822f0b29d34f7a5"
OLD_VERSION = "1.1.16"
OLD_REVISION = "efa16f096dc02fb654b7e86958d268195284d014"

TESTS: list[tuple[str, Callable[[], None]]] = []


def test(name: str) -> Callable[[Callable[[], None]], Callable[[], None]]:
    def register(callback: Callable[[], None]) -> Callable[[], None]:
        TESTS.append((name, callback))
        return callback

    return register


def canonical(path: str) -> Path:
    return ROOT / "compat" / path


def portable(path: str) -> Path:
    return RUNTIME / "compat" / path


def matrix() -> dict[str, object]:
    return compatibility.validate_matrix_structure(
        canonical("agy-model-effort-matrix.json"),
        canonical("model-effort-matrix.schema.json"),
    )


def validate(binding: Path, digest: Path) -> None:
    compatibility.validate_inventory_binding(binding, digest, VERSION, REVISION, matrix())


def rejects(mutator: Callable[[dict[str, object]], None], *, digest: str | None = None) -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        binding = json.loads(canonical("agy-models-inventory-binding.json").read_text())
        mutator(binding)
        binding_path = root / "binding.json"
        binding_path.write_text(json.dumps(binding, indent=2) + "\n", encoding="utf-8")
        digest_path = root / "binding.sha256"
        digest_path.write_text(
            (digest or hashlib.sha256(binding_path.read_bytes()).hexdigest()) + "\n",
            encoding="ascii",
        )
        try:
            validate(binding_path, digest_path)
        except compatibility.CompatibilityError:
            return
    raise AssertionError("mutated inventory binding was accepted")


@test("active version source and review records are exact")
def _() -> None:
    assert canonical("agy-verified-version.txt").read_text() == VERSION + "\n"
    assert canonical("agy-upstream-head.txt").read_text() == REVISION + "\n"
    assert canonical("agy-last-reviewed.txt").read_text() == "2026-09-03\n"


@test("portable active records are byte synchronized")
def _() -> None:
    for name in (
        "agy-verified-version.txt",
        "agy-upstream-head.txt",
        "agy-last-reviewed.txt",
        "agy-model-effort-matrix.json",
        "agy-model-effort-matrix.sha256",
        "agy-models-inventory-binding.json",
        "agy-models-inventory-binding.sha256",
        "agy-version-manifest.json",
        "agy-version-manifest.sha256",
    ):
        assert canonical(name).read_bytes() == portable(name).read_bytes()


@test("active review is additive and prior observation remains historical")
def _() -> None:
    activation = canonical("reviews/agy-1.1.24-activation.md").read_text()
    observation = canonical("reviews/agy-1.1.22.md").read_text()
    assert "activates the exact agy `1.1.24`" in activation
    assert "`command.name: models` and a `response` string" in activation
    assert "`command.data.models` contains" in activation
    assert "does **not** activate 1.1.22" in observation


@test("activation preserves the sanitized structured-capture boundary")
def _() -> None:
    activation = canonical("reviews/agy-1.1.24-activation.md").read_text()
    assert RESPONSE_SHA256 in activation
    assert "contains no account identifier" in activation
    assert "does not claim a `success` field" in activation


@test("activation preserves controller retry and closed-binary residual")
def _() -> None:
    activation = canonical("reviews/agy-1.1.24-activation.md").read_text()
    assert "automatically relaunch, restart, or begin a fresh provider" in activation
    assert "internal retry behavior remains outside" in activation


@test("matrix byte digest is exact")
def _() -> None:
    raw = canonical("agy-model-effort-matrix.json").read_bytes()
    assert hashlib.sha256(raw).hexdigest() == MATRIX_SHA256
    assert canonical("agy-model-effort-matrix.sha256").read_text() == MATRIX_SHA256 + "\n"


@test("active matrix is version and source bound")
def _() -> None:
    assert compatibility.matrix_binding_state(matrix(), VERSION, REVISION) == (
        True,
        "active and bound",
    )


@test("inventory binding byte digest is exact")
def _() -> None:
    raw = canonical("agy-models-inventory-binding.json").read_bytes()
    assert hashlib.sha256(raw).hexdigest() == BINDING_SHA256
    assert canonical("agy-models-inventory-binding.sha256").read_text() == BINDING_SHA256 + "\n"


@test("exact accepted inventory binding validates")
def _() -> None:
    validate(
        canonical("agy-models-inventory-binding.json"),
        canonical("agy-models-inventory-binding.sha256"),
    )


@test("inventory binding rejects a missing mandatory version manifest")
def _() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        binding_path = root / "agy-models-inventory-binding.json"
        digest_path = root / "agy-models-inventory-binding.sha256"
        binding_path.write_bytes(canonical("agy-models-inventory-binding.json").read_bytes())
        digest_path.write_bytes(canonical("agy-models-inventory-binding.sha256").read_bytes())
        try:
            validate(binding_path, digest_path)
        except compatibility.CompatibilityError as exc:
            assert "manifest" in str(exc)
            return
    raise AssertionError("inventory binding without its version manifest was accepted")


@test("capture and inventory hashes are exact")
def _() -> None:
    value = json.loads(canonical("agy-models-inventory-binding.json").read_text())
    assert value["source_sha256"] == SOURCE_SHA256
    assert value["version_binding_sha256"] == VERSION_BINDING_SHA256
    assert value["capture_record_sha256"] == CAPTURE_SHA256
    assert value["capture_stdout_sha256"] == STDOUT_SHA256
    assert value["capture_response_sha256"] == RESPONSE_SHA256
    assert value["inventory_normalized_sha256"] == NORMALIZED_SHA256


@test("inventory contains exactly the active matrix slugs")
def _() -> None:
    value = json.loads(canonical("agy-models-inventory-binding.json").read_text())
    assert value["slug_count"] == 14
    assert value["slugs"] == compatibility.matrix_slugs(matrix())


@test("historical 1.1.16 version binding is rejected")
def _() -> None:
    rejects(lambda value: value.__setitem__("agy_version", OLD_VERSION))


@test("historical 1.1.16 source binding is rejected")
def _() -> None:
    rejects(lambda value: value.__setitem__("reviewed_source_revision", OLD_REVISION))


@test("binding digest mismatch is rejected")
def _() -> None:
    rejects(lambda value: None, digest="0" * 64)


@test("unknown binding key is rejected")
def _() -> None:
    rejects(lambda value: value.__setitem__("unknown", True))


@test("capture record drift is rejected")
def _() -> None:
    rejects(lambda value: value.__setitem__("capture_record_sha256", "0" * 64))


@test("capture stdout drift is rejected")
def _() -> None:
    rejects(lambda value: value.__setitem__("capture_stdout_sha256", "0" * 64))


@test("capture response drift is rejected")
def _() -> None:
    rejects(lambda value: value.__setitem__("capture_response_sha256", "0" * 64))


@test("version binding drift is rejected")
def _() -> None:
    rejects(lambda value: value.__setitem__("version_binding_sha256", "0" * 64))


@test("source byte drift is rejected")
def _() -> None:
    rejects(lambda value: value.__setitem__("source_sha256", "0" * 64))


@test("normalized inventory drift is rejected")
def _() -> None:
    rejects(lambda value: value.__setitem__("inventory_normalized_sha256", "0" * 64))


@test("slug removal is rejected")
def _() -> None:
    rejects(lambda value: value["slugs"].pop())  # type: ignore[union-attr]


@test("slug substitution is rejected")
def _() -> None:
    rejects(lambda value: value["slugs"].__setitem__(0, "unknown-model"))  # type: ignore[union-attr]


@test("coordinated historical version and source drift is rejected")
def _() -> None:
    def mutate(value: dict[str, object]) -> None:
        value["agy_version"] = OLD_VERSION
        value["reviewed_source_revision"] = OLD_REVISION

    rejects(mutate)


def main() -> None:
    passed = 0
    failed = 0
    for name, callback in TESTS:
        try:
            callback()
        except Exception as exc:
            failed += 1
            print(f"  FAIL activation: {name}: {type(exc).__name__}: {exc}")
        else:
            passed += 1
            print(f"  ok   activation: {name}")
    print(f"AGY_1_1_24_ACTIVATION_TEST_RESULT passed={passed} failed={failed}")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
