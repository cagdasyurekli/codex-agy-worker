#!/usr/bin/env python3
"""Focused offline tests for the active agy 1.1.16 compatibility binding."""

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
SPEC = importlib.util.spec_from_file_location("agy_1_1_16_compatibility", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise SystemExit("cannot load compatibility module")
compatibility = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(compatibility)

VERSION = "1.1.16"
REVISION = "efa16f096dc02fb654b7e86958d268195284d014"
CAPTURE_SHA256 = "04f9cf2d18c14635689630c7bb50437151f2b0eb1d414d0d943212fe12c7a20e"
STDOUT_SHA256 = "b75bd15381574af9ff1d9891dee36cc88a811c2abc86ef202c86c6b79077251c"
NORMALIZED_SHA256 = "db2a3529568b1ce4bb112d4cb9a0c31a4f3d1b32bd787728d224894ec6db133c"
MATRIX_SHA256 = "a586927552d90295529f3059989a2a8c36c234d41b8f79d61c1c89edbf829e00"
BINDING_SHA256 = "3f34e6f6bfcf7b7e65951e02f92580c2858f32016f115866160f279d2d3a2747"

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
    assert canonical("agy-last-reviewed.txt").read_text() == "2026-08-20\n"


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
    ):
        assert canonical(name).read_bytes() == portable(name).read_bytes()


@test("distribution tuple is exact")
def _() -> None:
    value = json.loads(canonical("agy-distribution-manifest.json").read_text())
    assert value == {
        "version": VERSION,
        "url": "https://storage.googleapis.com/antigravity-public/antigravity-cli/1.1.16-6607970839166976/darwin-arm/cli_mac_arm64.tar.gz",
        "sha512": "fa3a94a7d9d96cb367bf643ecf0da3b4d6b45f3e390ec6db1d699fdac4f7750894617152fc3c1695712a36eee926fff4f00ff4a44d372b3f604cfc9ec6fdbea6",
    }


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


@test("capture and inventory hashes are exact")
def _() -> None:
    value = json.loads(canonical("agy-models-inventory-binding.json").read_text())
    assert value["capture_record_sha256"] == CAPTURE_SHA256
    assert value["capture_stdout_sha256"] == STDOUT_SHA256
    assert value["inventory_normalized_sha256"] == NORMALIZED_SHA256


@test("inventory contains exactly the active matrix slugs")
def _() -> None:
    value = json.loads(canonical("agy-models-inventory-binding.json").read_text())
    assert value["slug_count"] == 14
    assert value["slugs"] == compatibility.matrix_slugs(matrix())


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


@test("binding version mismatch is rejected")
def _() -> None:
    rejects(lambda value: value.__setitem__("agy_version", "1.1.15"))


@test("binding source revision mismatch is rejected")
def _() -> None:
    rejects(lambda value: value.__setitem__("reviewed_source_revision", "0" * 40))


@test("coordinated reviewed-source drift is rejected")
def _() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        drift_revision = "0" * 40
        binding = json.loads(canonical("agy-models-inventory-binding.json").read_text())
        binding["reviewed_source_revision"] = drift_revision
        binding_path = root / "binding.json"
        binding_path.write_text(json.dumps(binding, indent=2) + "\n", encoding="utf-8")
        digest_path = root / "binding.sha256"
        digest_path.write_text(
            hashlib.sha256(binding_path.read_bytes()).hexdigest() + "\n",
            encoding="ascii",
        )
        drift_matrix = matrix()
        drift_matrix["inventory"]["reviewed_source_revision"] = drift_revision
        try:
            compatibility.validate_inventory_binding(
                binding_path,
                digest_path,
                VERSION,
                drift_revision,
                drift_matrix,
            )
        except compatibility.CompatibilityError:
            return
    raise AssertionError("coordinated reviewed-source drift was accepted")


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
    print(f"AGY_1_1_16_ACTIVATION_TEST_RESULT passed={passed} failed={failed}")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
