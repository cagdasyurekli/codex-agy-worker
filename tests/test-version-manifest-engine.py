#!/usr/bin/env python3
"""Offline parity, self-healing, and adversary tests for version manifest binding."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import runpy
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]
ENGINE_PATH = ROOT / "scripts" / "version_manifest_engine.py"
GUARD_PATH = ROOT / "scripts" / "version_copy_guard.py"
MANIFEST_PATH = ROOT / "compat" / "agy-version-manifest.json"
PORTABLE = ROOT / "skills" / "agy-worker" / "runtime"


def load_module(name: str, path: Path) -> object:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


engine = load_module("version_manifest_engine_tested", ENGINE_PATH)
guard = load_module("version_copy_guard_tested", GUARD_PATH)


def write_manifest(root: Path, data: dict[str, object]) -> Path:
    path = root / "agy-version-manifest.json"
    raw = (json.dumps(data, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    path.write_bytes(raw)
    path.with_suffix(".sha256").write_text(hashlib.sha256(raw).hexdigest() + "\n", encoding="ascii")
    return path


class VersionManifestEngineTests(unittest.TestCase):
    def test_01_manifest_and_digest_are_exact(self) -> None:
        raw = MANIFEST_PATH.read_bytes()
        expected = MANIFEST_PATH.with_suffix(".sha256").read_text(encoding="ascii").strip()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), expected)
        self.assertEqual(set(engine.load_manifest(MANIFEST_PATH)), {"1.1.12", "1.1.16", "1.1.22", "1.1.24"})

    def test_02_portable_artifacts_are_byte_identical(self) -> None:
        for relative in (
            "compat/agy-version-manifest.json", "compat/agy-version-manifest.sha256",
            "compat/version-manifest.schema.json", "scripts/version_manifest_engine.py",
        ):
            self.assertEqual((ROOT / relative).read_bytes(), (PORTABLE / relative).read_bytes(), relative)

    def test_03_current_spec_is_exact(self) -> None:
        spec = engine.get_version_spec("1.1.24", MANIFEST_PATH)
        self.assertEqual(spec.version, "1.1.24")
        self.assertEqual(spec.support_tier, "current")
        self.assertEqual(
            spec.allowed_operations,
            ("activation", "capture", "classifier", "profile", "reprofile", "version-evidence"),
        )
        self.assertEqual(spec.expected_stdout, b"1.1.24\n")
        self.assertEqual(spec.source_sha256, "4d1138b2dbde56127969fd307281494d4a7dcc22759ce9adb44d36247df86151")
        self.assertEqual(spec.release_commit, "bf27ce1134b4ead2f7bfa0a4fb3cb5fcbebcaa5a")
        self.assertEqual(spec.slug_count, 14)
        self.assertEqual(spec.capture_snapshot_policy, "macos-readonly-mount")

        legacy = engine.get_version_spec("1.1.22", MANIFEST_PATH)
        self.assertEqual(legacy.support_tier, "legacy")
        self.assertEqual(
            legacy.allowed_operations,
            ("capture", "classifier", "profile", "reprofile", "version-evidence"),
        )

        previous = engine.get_version_spec("1.1.16", MANIFEST_PATH)
        self.assertEqual(previous.support_tier, "previous")
        self.assertEqual(previous.allowed_operations, ("capture", "profile", "version-evidence"))
        self.assertEqual(
            previous.recovery_binding_sha256,
            "facf6adc18afc85ed5c232e3e1f9ad0fbcac7d62f1f98866cabb615d43069a57",
        )
        self.assertEqual(
            previous.recovery_runner_sha256,
            "9c1a9d35c0db9fe137ed4490b47d2b11443fe7cfaf1e552eca7adf575b048d4c",
        )

        historical = engine.get_version_spec("1.1.12", MANIFEST_PATH)
        self.assertEqual(historical.support_tier, "historical")
        self.assertEqual(historical.allowed_operations, ())

    def test_04_operation_constants_match_version_evidence_adapter(self) -> None:
        module = runpy.run_path(str(ROOT / "scripts/version_manifest_version_evidence.py"))
        expected = engine.operation_constants(engine.get_version_spec("1.1.22"), "version-evidence")
        for key, value in expected.items():
            self.assertEqual(module[key], value, key)

    def test_05_operation_constants_match_profile_adapter(self) -> None:
        module = runpy.run_path(str(ROOT / "scripts/version_manifest_capture_profile.py"))
        expected = engine.operation_constants(engine.get_version_spec("1.1.22"), "profile")
        for key, value in expected.items():
            self.assertEqual(module[key], value, key)

    def test_06_operation_constants_match_capture_adapter(self) -> None:
        module = runpy.run_path(str(ROOT / "scripts/version_manifest_capture_runner.py"))
        expected = engine.operation_constants(engine.get_version_spec("1.1.22"), "capture")
        for key, value in expected.items():
            self.assertEqual(module[key], value, key)

    def test_07_operation_constants_match_classifier_adapter(self) -> None:
        module = runpy.run_path(str(ROOT / "scripts/version_manifest_capture_classifier.py"))
        expected = engine.operation_constants(engine.get_version_spec("1.1.22"), "classifier")
        for key, value in expected.items():
            self.assertEqual(module[key], value, key)

    def test_08_operation_constants_match_reprofile_adapter(self) -> None:
        module = runpy.run_path(str(ROOT / "scripts/version_manifest_reprofile.py"))
        expected = engine.operation_constants(engine.get_version_spec("1.1.22"), "reprofile")
        for key, value in expected.items():
            self.assertEqual(module[key], value, key)

    def test_09_data_only_new_version_self_heals_all_bindings(self) -> None:
        data = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        new = copy.deepcopy(data["versions"]["1.1.24"])
        new.update({
            "version": "1.1.25", "expected_stdout": "1.1.25\n",
            "source_sha256": "1" * 64, "source_size": 180_000_000,
            "release_commit": "2" * 40, "distribution_sha512": "3" * 128,
            "recovery_binding_sha256": "4" * 64, "recovery_stdout": "1.1.25\n",
            "recovery_runner_sha256": "5" * 64,
            "output_profile_name": "models.capture.1.1.25.profile.json",
            "prior_name": "agy-models-capture-1.1.25.version",
            "reprofile_output_name": "models.capture.1.1.25.reprofile.json",
            "failure_ruleset_version": "agy-1.1.25-failure-rules-v1",
            "capture_snapshot_policy": "macos-readonly-mount",
            "capture_runner_source_sha256": hashlib.sha256((ROOT / "scripts" / "version_manifest_capture_runner.py").read_bytes()).hexdigest(),
        })
        data["versions"]["1.1.25"] = new
        with tempfile.TemporaryDirectory() as directory:
            path = write_manifest(Path(directory), data)
            spec = engine.get_version_spec("1.1.25", path)
            self.assertEqual(engine.operation_constants(spec, "version-evidence")["EXPECTED_VERSION"], "1.1.25")
            self.assertEqual(engine.operation_constants(spec, "profile")["OUTPUT_NAME"], new["output_profile_name"])
            self.assertEqual(engine.operation_constants(spec, "capture")["OUTPUT_PROFILE_NAME"], new["output_profile_name"])
            self.assertEqual(engine.operation_constants(spec, "capture")["CAPTURE_SNAPSHOT_POLICY"], "macos-readonly-mount")
            self.assertEqual(engine.operation_constants(spec, "capture")["EXPECTED_CAPTURE_RUNNER_SOURCE_SHA256"], new["capture_runner_source_sha256"])
            self.assertEqual(engine.operation_constants(spec, "classifier")["RULESET_VERSION"], new["failure_ruleset_version"])
            self.assertEqual(engine.operation_constants(spec, "reprofile")["OUTPUT_NAME"], new["reprofile_output_name"])

            scripts = {
                "version-evidence": ("version_manifest_version_evidence.py", ["--validate-source-contract"], True),
                "profile": ("version_manifest_capture_profile.py", ["--validate-source-contract"], True),
                "capture": ("version_manifest_capture_runner.py", ["--validate-source-contract"], True),
                "classifier": ("version_manifest_capture_classifier.py", ["--validate-ruleset"], False),
                "reprofile": ("version_manifest_reprofile.py", ["--validate-source-contract"], True),
            }
            for operation, (name, operation_args, sends_source) in scripts.items():
                script = ROOT / "scripts" / name
                result = subprocess.run(
                    [
                        sys.executable, "-I", "-S", "-B", str(script),
                        "--manifest-version", "1.1.25", "--manifest", str(path),
                        *operation_args,
                    ],
                    input=script.read_bytes() if sends_source else None,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, (operation, result.stderr))
                if operation == "classifier":
                    self.assertIn(b"ruleset_version=agy-1.1.25-failure-rules-v1", result.stdout)
                else:
                    self.assertIn(json.loads(result.stdout)["status"], {"accepted", "valid-source"})

    def test_09b_legacy_1_1_22_adapters_reach_shared_entrypoints(self) -> None:
        scripts = {
            "models_capture_1_1_22_version_evidence.py": ("version_manifest_version_evidence.py", ["--validate-source-contract"]),
            "models_capture_1_1_22_profile.py": ("version_manifest_capture_profile.py", ["--validate-source-contract"]),
            "models_capture_1_1_22_runner.py": ("version_manifest_capture_runner.py", ["--validate-source-contract"]),
            "models_capture_1_1_22_classifier.py": ("version_manifest_capture_classifier.py", ["--validate-ruleset"]),
            "models_capture_1_1_22_reprofile.py": ("version_manifest_reprofile.py", ["--validate-source-contract"]),
        }
        for adapter_name, (shared_name, operation_args) in scripts.items():
            shared = ROOT / "scripts" / shared_name
            result = subprocess.run(
                [sys.executable, "-I", "-S", "-B", str(ROOT / "scripts" / adapter_name), *operation_args],
                input=None if operation_args == ["--validate-ruleset"] else shared.read_bytes(),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(result.returncode, 0, (adapter_name, result.stderr))

    def test_09c_previous_version_uses_only_shared_common_entrypoints(self) -> None:
        scripts = {
            "version-evidence": "version_manifest_version_evidence.py",
            "profile": "version_manifest_capture_profile.py",
            "capture": "version_manifest_capture_runner.py",
        }
        for operation, name in scripts.items():
            script = ROOT / "scripts" / name
            result = subprocess.run(
                [
                    sys.executable, "-I", "-S", "-B", str(script),
                    "--manifest-version", "1.1.16", "--validate-source-contract",
                ],
                input=script.read_bytes(),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(result.returncode, 0, (operation, result.stderr))
            self.assertIn(json.loads(result.stdout)["status"], {"accepted", "valid-source"})

    def test_09d_previous_and_historical_unsupported_operations_fail_closed(self) -> None:
        previous = engine.get_version_spec("1.1.16")
        for operation in ("activation", "classifier", "reprofile"):
            with self.subTest(version=previous.version, operation=operation):
                with self.assertRaises(engine.EngineError):
                    if operation == "activation":
                        engine.validate_activation_binding({}, previous)
                    else:
                        engine.operation_constants(previous, operation)

        historical = engine.get_version_spec("1.1.12")
        for operation in (
            "activation", "capture", "classifier", "profile", "reprofile",
            "version-evidence",
        ):
            with self.subTest(version=historical.version, operation=operation):
                with self.assertRaises(engine.EngineError):
                    if operation == "activation":
                        engine.validate_activation_binding({}, historical)
                    else:
                        engine.operation_constants(historical, operation)

        scripts = (
            "version_manifest_version_evidence.py",
            "version_manifest_capture_profile.py",
            "version_manifest_capture_runner.py",
            "version_manifest_capture_classifier.py",
            "version_manifest_reprofile.py",
        )
        for name in scripts:
            script = ROOT / "scripts" / name
            operation_args = ["--validate-ruleset"] if "classifier" in name else ["--validate-source-contract"]
            result = subprocess.run(
                [
                    sys.executable, "-I", "-S", "-B", str(script),
                    "--manifest-version", "1.1.12", *operation_args,
                ],
                input=None if "classifier" in name else script.read_bytes(),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0, name)
            self.assertEqual(result.stdout, b"", name)

    def test_09e_support_policy_is_closed_and_digest_bound(self) -> None:
        data = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        cases = (
            ("1.1.16", "support_tier", "current"),
            ("1.1.16", "allowed_operations", ["capture", "profile"]),
            ("1.1.12", "allowed_operations", ["version-evidence"]),
            ("1.1.24", "capture_snapshot_policy", "network-block"),
        )
        for version, field, value in cases:
            with self.subTest(version=version, field=field):
                changed = copy.deepcopy(data)
                changed["versions"][version][field] = value
                with tempfile.TemporaryDirectory() as directory:
                    path = write_manifest(Path(directory), changed)
                    with self.assertRaises(engine.EngineError):
                        engine.load_manifest(path)

    def test_10_exact_activation_binding_passes(self) -> None:
        binding = json.loads((ROOT / "compat/agy-models-inventory-binding.json").read_text(encoding="utf-8"))
        engine.validate_activation_binding(binding, engine.get_version_spec("1.1.24"))

    def test_11_activation_drift_fails_closed(self) -> None:
        binding = json.loads((ROOT / "compat/agy-models-inventory-binding.json").read_text(encoding="utf-8"))
        for key in ("source_sha256", "capture_record_sha256", "inventory_normalized_sha256"):
            changed = copy.deepcopy(binding)
            changed[key] = "0" * 64
            with self.assertRaises(engine.EngineError):
                engine.validate_activation_binding(changed, engine.get_version_spec("1.1.24"))

    def test_12_stale_digest_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "agy-version-manifest.json"
            path.write_bytes(MANIFEST_PATH.read_bytes())
            path.with_suffix(".sha256").write_text("0" * 64 + "\n", encoding="ascii")
            with self.assertRaises(engine.EngineError):
                engine.load_manifest(path)

    def test_13_duplicate_keys_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = b'{"schema_version":1,"schema_version":1,"kind":"agy-version-manifest","versions":{}}\n'
            path = root / "agy-version-manifest.json"
            path.write_bytes(raw)
            path.with_suffix(".sha256").write_text(hashlib.sha256(raw).hexdigest(), encoding="ascii")
            with self.assertRaises(engine.EngineError):
                engine.load_manifest(path)

    def test_14_symlink_manifest_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "agy-version-manifest.json"
            path.symlink_to(MANIFEST_PATH)
            path.with_suffix(".sha256").write_text(MANIFEST_PATH.with_suffix(".sha256").read_text(), encoding="ascii")
            with self.assertRaises(engine.EngineError):
                engine.load_manifest(path)

    def test_15_unknown_version_and_operation_fail_closed(self) -> None:
        with self.assertRaises(engine.EngineError):
            engine.get_version_spec("9.9.9")
        with self.assertRaises(engine.EngineError):
            engine.operation_constants(engine.get_version_spec("1.1.24"), "unknown")

    def test_16_reprofile_transition_accepts_only_nlink_drift(self) -> None:
        current = os.stat_result((stat.S_IFDIR | 0o700, 3, 2, 9, os.getuid(), 4, 0, 0, 0, 0))
        prior = {"account_home_identity": {"dev": 2, "gid": 4, "ino": 3, "mode": 0o700, "nlink": 8, "uid": os.getuid()}}
        engine.verify_reprofile_transition(prior, current)
        prior["account_home_identity"]["ino"] = 99
        with self.assertRaises(engine.EngineError):
            engine.verify_reprofile_transition(prior, current)

    def test_17_copy_guard_rejects_only_present_retired_copies_during_deletion_handoff(self) -> None:
        ok, violations = guard.audit_version_copies(ROOT)
        retired = {
            "scripts/version_recovery_1_1_12_runner.py",
            "scripts/models_capture_1_1_12_profile.py",
            "scripts/models_capture_1_1_12_runner.py",
            "scripts/models_capture_1_1_16_version_evidence.py",
            "scripts/models_capture_1_1_16_profile.py",
            "scripts/models_capture_1_1_16_runner.py",
        }
        present = {path for path in retired if (ROOT / path).exists()}
        observed = {
            item.split(": ", 1)[1].split(" ", 1)[0]
            for item in violations
        }
        self.assertEqual(observed, present)
        self.assertEqual(ok, not present)
        self.assertEqual(
            guard.CURRENT_ADAPTER_ALLOWLIST,
            {
                "scripts/models_capture_1_1_22_classifier.py",
                "scripts/models_capture_1_1_22_profile.py",
                "scripts/models_capture_1_1_22_reprofile.py",
                "scripts/models_capture_1_1_22_runner.py",
                "scripts/models_capture_1_1_22_version_evidence.py",
            },
        )

    def test_18_copy_guard_rejects_new_version_script(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "scripts/models_capture_1_1_24_runner.py"
            path.parent.mkdir()
            path.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
            manifest = root / "compat" / "agy-version-manifest.json"
            manifest.parent.mkdir()
            manifest.write_text(
                '{"versions":{"1.1.24":{"support_tier":"current"}}}\n',
                encoding="utf-8",
            )
            ok, violations = guard.audit_version_copies(root)
            self.assertFalse(ok)
            self.assertTrue(any("1_1_24" in item for item in violations))

    def test_19_engine_cli_audits_bound_manifest(self) -> None:
        result = subprocess.run(
            [sys.executable, "-I", "-S", "-B", str(ENGINE_PATH), "--audit-manifest"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, b"manifest valid: 4 versions loaded\n")

    def test_20_test_is_read_only_for_production_artifacts(self) -> None:
        paths = [MANIFEST_PATH, ENGINE_PATH, ROOT / "scripts/version_manifest_capture_runner.py"]
        before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
        engine.get_version_spec("1.1.24")
        after = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
