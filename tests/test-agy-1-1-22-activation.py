#!/usr/bin/env python3
"""Focused offline tests for the active agy 1.1.22 compatibility binding."""

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
SPEC = importlib.util.spec_from_file_location("agy_1_1_22_compatibility", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise SystemExit("cannot load compatibility module")
compatibility = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(compatibility)

VERSION = "1.1.22"
REVISION = "556846a4bb94117222f53846896c7eb0d645307e"
SOURCE_SHA256 = "7b1317779085913d338bde0e9b39b72323d9083a879525f944fd469c8ecca906"
VERSION_BINDING_SHA256 = "d9d830e65d3a5c76df6d9e07e6ea7e14e14f290ab4036bdbae8cb33502e29f2a"
CAPTURE_SHA256 = "626623c2c7b3b126efc2161c36554ecfa7fad3ce46e9dfcee8419c685ccaf2e3"
STDOUT_SHA256 = "b75bd15381574af9ff1d9891dee36cc88a811c2abc86ef202c86c6b79077251c"
RESPONSE_SHA256 = "a7463eafad52e693c6d4890ed329f16aa60b1dfa9b058c051a13c0f0553efec1"
NORMALIZED_SHA256 = "db2a3529568b1ce4bb112d4cb9a0c31a4f3d1b32bd787728d224894ec6db133c"
MATRIX_SHA256 = "5a363dee8acb35e91b60405e705e8afaf155989dd755027cc5fa16741e42436c"
BINDING_SHA256 = "e544ce0c8ac2fb11481b0590720ec3474122ec95238a02d6d3a13db833ed94e5"
HELP_SHA256 = "c26943c81bf16cf55fb35e6152eda42de30f6e09cd671e29dcbc22bc5517fde6"
CAPABILITIES_SHA256 = "a08e143034f0cef4bd06b5de372b5e6b4a53e2e13db89ad26b0ea2c790bec293"
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
    assert canonical("agy-last-reviewed.txt").read_text() == "2026-08-28\n"


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
    activation = canonical("reviews/agy-1.1.22-activation.md").read_text()
    observation = canonical("reviews/agy-1.1.22.md").read_text()
    assert "activates the exact agy `1.1.22`" in activation
    assert "object has `success: true`, `command.name: models`, and a `response` string" in activation
    assert "`command.data.models` contains the model records" in activation
    assert "does **not** activate 1.1.22" in observation


@test("activation binds exact reviewed help evidence")
def _() -> None:
    activation = canonical("reviews/agy-1.1.22-activation.md").read_text()
    assert HELP_SHA256 in activation
    assert CAPABILITIES_SHA256 in activation
    assert "`--output-format` is a root" in activation


@test("activation preserves controller retry and closed-binary residual")
def _() -> None:
    activation = canonical("reviews/agy-1.1.22-activation.md").read_text()
    assert "does not automatically relaunch, restart, or begin a fresh provider" in activation
    assert "retry count and backoff are unknown" in activation


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
    print(f"AGY_1_1_22_ACTIVATION_TEST_RESULT passed={passed} failed={failed}")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
