#!/usr/bin/env python3
"""Offline unit and adversary tests for agy 1.1.22 models capture failure classifier."""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import runpy
import stat
import sys
import tempfile
import unittest
from typing import Any


ROOT = pathlib.Path(__file__).resolve().parents[1]
CLASSIFIER_PATH = ROOT / "scripts" / "models_capture_1_1_22_classifier.py"
RUNNER_SOURCE_PATH = ROOT / "scripts" / "models_capture_1_1_22_runner.py"

classifier_module = runpy.run_path(str(CLASSIFIER_PATH))
classify_evidence_root = classifier_module["classify_evidence_root"]
classify_stderr_bytes = classifier_module["classify_stderr_bytes"]
ClassificationError = classifier_module["ClassificationError"]
EXPECTED_RUNNER_SHA256 = classifier_module["EXPECTED_RUNNER_SHA256"]
EXPECTED_SOURCE_SHA256 = classifier_module["EXPECTED_SOURCE_SHA256"]
EXPECTED_RECOVERY_BINDING_SHA256 = classifier_module["EXPECTED_RECOVERY_BINDING_SHA256"]
OUTPUT_PROFILE_NAME = classifier_module["OUTPUT_PROFILE_NAME"]
OUTPUT_CLASSIFICATION_NAME = classifier_module["OUTPUT_CLASSIFICATION_NAME"]
SCRATCH_NAMES = classifier_module["SCRATCH_NAMES"]
RULESET_VERSION = classifier_module["RULESET_VERSION"]
RULESET_SHA256 = classifier_module["RULESET_SHA256"]


def canonical(value: object) -> bytes:
    return (
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("ascii")
        + b"\n"
    )


class ModelsCapture1122ClassifierTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="agy-test-classifier-")
        self.root = pathlib.Path(os.path.realpath(self.temp_dir.name))
        os.chmod(str(self.root), 0o700)
        self.runner_bytes = RUNNER_SOURCE_PATH.read_bytes()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _build_valid_evidence_root(
        self,
        stderr_content: bytes = b"",
        stdout_content: bytes = b"",
        exit_code: int = 1,
    ) -> pathlib.Path:
        root = self.root / "evidence"
        root.mkdir(mode=0o700)

        for name in SCRATCH_NAMES:
            (root / name).mkdir(mode=0o700)

        # Runner and its hash file
        runner_file = root / "models_capture_1_1_22_runner.py"
        runner_file.write_bytes(self.runner_bytes)
        os.chmod(str(runner_file), 0o600)

        runner_sha_file = root / "models_capture_1_1_22_runner.py.sha256"
        runner_sha_file.write_bytes((EXPECTED_RUNNER_SHA256 + "\n").encode("ascii"))
        os.chmod(str(runner_sha_file), 0o600)

        # Profile
        profile_data = canonical({
            "account_home": "/private/account",
            "account_home_identity": {"dev": 1, "gid": 1, "ino": 1, "mode": 0o700, "nlink": 2, "uid": os.getuid()},
            "capture_parent": "/private/capture",
            "capture_parent_identity": {"dev": 1, "gid": 1, "ino": 1, "mode": 0o700, "nlink": 2, "uid": os.getuid()},
            "snapshot_identity": {"ctime_ns": 1, "dev": 1, "gid": 1, "ino": 1, "mode": 0o500, "mtime_ns": 1, "nlink": 1, "size": 1, "uid": os.getuid()},
            "snapshot_path": "/private/capture/recovery/agy.snapshot",
            "source_identity": {"ctime_ns": 1, "dev": 1, "gid": 1, "ino": 1, "mode": 0o755, "mtime_ns": 1, "nlink": 1, "size": 1, "uid": os.getuid()},
            "source_path": "/private/capture/agy.source",
            "source_sha256": EXPECTED_SOURCE_SHA256,
            "version_binding_sha256": EXPECTED_RECOVERY_BINDING_SHA256,
            "version_root": "/private/capture/recovery",
            "version_root_identity": {"dev": 1, "gid": 1, "ino": 1, "mode": 0o700, "nlink": 2, "uid": os.getuid()},
        })
        profile_file = root / OUTPUT_PROFILE_NAME
        profile_file.write_bytes(profile_data)
        os.chmod(str(profile_file), 0o600)
        profile_sha = hashlib.sha256(profile_data).hexdigest()

        # Stdout & Stderr
        stdout_file = root / "models.stdout"
        stdout_file.write_bytes(stdout_content)
        os.chmod(str(stdout_file), 0o600)
        stdout_sha = hashlib.sha256(stdout_content).hexdigest()

        stderr_file = root / "models.stderr"
        stderr_file.write_bytes(stderr_content)
        os.chmod(str(stderr_file), 0o600)
        stderr_sha = hashlib.sha256(stderr_content).hexdigest()

        # Failure record
        failure_record: dict[str, Any] = {
            "artifacts": {
                "models.stderr": stderr_sha,
                "models.stdout": stdout_sha,
            },
            "bounds": {
                "stream_bytes": 65536,
                "wall_seconds": 25.0,
            },
            "claim": "models-capture-failure",
            "input_profile_sha256": profile_sha,
            "limitations": {
                "accepted_inventory": False,
                "failure_classified": False,
                "inventory_interpreted": False,
                "metadata_advance_authorized": False,
                "metadata_updated": False,
                "provider_backend_proven": False,
                "routing_authority": False,
                "routing_authorized": False,
            },
            "observation": {
                "exit": exit_code,
                "popen_count": 1,
            },
            "runner_sha256": EXPECTED_RUNNER_SHA256,
            "source_sha256": EXPECTED_SOURCE_SHA256,
            "status": "child-failed",
            "version_binding_sha256": EXPECTED_RECOVERY_BINDING_SHA256,
        }
        failure_file = root / "models.capture.failure.json"
        failure_file.write_bytes(canonical(failure_record))
        os.chmod(str(failure_file), 0o600)

        return root

    def test_01_static_ruleset_validation(self) -> None:
        self.assertEqual(RULESET_VERSION, "agy-1.1.22-failure-rules-v1")
        self.assertEqual(len(RULESET_SHA256), 64)

    def test_02_classify_real_retained_failure_evidence_as_local_environment(self) -> None:
        # Real campaign form: local log/crash open and loopback bind were not permitted.
        stderr = (
            b"2026/08/27 22:15:00 open /tmp/agy.log: operation not permitted\n"
            b"listen tcp 127.0.0.1:0: bind: operation not permitted\n"
        )
        root = self._build_valid_evidence_root(stderr_content=stderr)
        record = classify_evidence_root(str(root))
        self.assertEqual(record["category"], "local_environment")
        self.assertEqual(record["origin"], "agy-1.1.22-models-capture")
        self.assertEqual(record["status"], "classified")
        self.assertEqual(record["ruleset_version"], RULESET_VERSION)
        self.assertEqual(record["ruleset_sha256"], RULESET_SHA256)

        out_file = root / OUTPUT_CLASSIFICATION_NAME
        self.assertTrue(out_file.exists())
        st = os.stat(str(out_file))
        self.assertEqual(stat.S_IMODE(st.st_mode), 0o600)

    def test_03_classify_authentication_failure(self) -> None:
        stderr = b"Error: Unauthenticated. Please run agy login to authenticate.\n"
        root = self._build_valid_evidence_root(stderr_content=stderr)
        record = classify_evidence_root(str(root))
        self.assertEqual(record["category"], "authentication")

    def test_04_classify_provider_permission_failure(self) -> None:
        stderr = b"rpc error: code = PermissionDenied desc = User does not have permission on project.\n"
        root = self._build_valid_evidence_root(stderr_content=stderr)
        record = classify_evidence_root(str(root))
        self.assertEqual(record["category"], "provider_permission")

    def test_05_classify_quota_failure(self) -> None:
        stderr = b"rpc error: code = ResourceExhausted desc = Quota exceeded for models API. HTTP 429\n"
        root = self._build_valid_evidence_root(stderr_content=stderr)
        record = classify_evidence_root(str(root))
        self.assertEqual(record["category"], "quota")

    def test_06_classify_service_failure(self) -> None:
        stderr = b"HTTP/2 503 Service Unavailable: backend_error connecting to upstream server\n"
        root = self._build_valid_evidence_root(stderr_content=stderr)
        record = classify_evidence_root(str(root))
        self.assertEqual(record["category"], "service")

    def test_07_classify_timeout_failure(self) -> None:
        stderr = b"Client.Timeout exceeded while awaiting headers: request timeout\n"
        root = self._build_valid_evidence_root(stderr_content=stderr)
        record = classify_evidence_root(str(root))
        self.assertEqual(record["category"], "timeout")

    def test_08_classify_unknown_when_no_rules_match(self) -> None:
        stderr = b"Some completely unrecognized custom error string without category keywords\n"
        root = self._build_valid_evidence_root(stderr_content=stderr)
        record = classify_evidence_root(str(root))
        self.assertEqual(record["category"], "unknown")

    def test_09_classify_mixed_matches_yields_unknown(self) -> None:
        # Mixed: unauthenticated (auth) AND Quota exceeded (quota)
        stderr = b"Error: Unauthenticated. Also Quota exceeded.\n"
        root = self._build_valid_evidence_root(stderr_content=stderr)
        record = classify_evidence_root(str(root))
        self.assertEqual(record["category"], "unknown")

    def test_10_explicit_output_destination(self) -> None:
        stderr = b"open /tmp/test.log: permission denied\n"
        root = self._build_valid_evidence_root(stderr_content=stderr)
        custom_out = self.root / "custom_classification.json"
        record = classify_evidence_root(str(root), str(custom_out))
        self.assertEqual(record["category"], "local_environment")
        self.assertTrue(custom_out.exists())
        self.assertEqual(stat.S_IMODE(os.stat(str(custom_out)).st_mode), 0o600)

    def test_11_rejects_relative_or_symlinked_evidence_root(self) -> None:
        with self.assertRaises(ClassificationError):
            classify_evidence_root("relative/path/evidence")

        root = self._build_valid_evidence_root(stderr_content=b"test\n")
        link = self.root / "link_evidence"
        os.symlink(str(root), str(link))
        with self.assertRaises(ClassificationError):
            classify_evidence_root(str(link))

    def test_12_rejects_non_directory_or_missing_root(self) -> None:
        with self.assertRaises(ClassificationError):
            classify_evidence_root(str(self.root / "nonexistent"))

        file_target = self.root / "regular_file"
        file_target.write_bytes(b"hello")
        with self.assertRaises(ClassificationError):
            classify_evidence_root(str(file_target))

    def test_13_rejects_wrong_mode_root(self) -> None:
        root = self._build_valid_evidence_root(stderr_content=b"test\n")
        os.chmod(str(root), 0o755)
        with self.assertRaises(ClassificationError):
            classify_evidence_root(str(root))

    def test_14_rejects_unexpected_files_in_evidence_root(self) -> None:
        root = self._build_valid_evidence_root(stderr_content=b"test\n")
        (root / "unauthorized_file.txt").write_bytes(b"extra")
        with self.assertRaises(ClassificationError):
            classify_evidence_root(str(root))

    def test_15_rejects_missing_required_files(self) -> None:
        root = self._build_valid_evidence_root(stderr_content=b"test\n")
        (root / "models.stderr").unlink()
        with self.assertRaises(ClassificationError):
            classify_evidence_root(str(root))

    def test_16_rejects_corrupted_runner_hash(self) -> None:
        root = self._build_valid_evidence_root(stderr_content=b"test\n")
        (root / "models_capture_1_1_22_runner.py").write_bytes(b"# tampered\n")
        with self.assertRaises(ClassificationError):
            classify_evidence_root(str(root))

    def test_17_rejects_corrupted_runner_sha_file(self) -> None:
        root = self._build_valid_evidence_root(stderr_content=b"test\n")
        (root / "models_capture_1_1_22_runner.py.sha256").write_bytes(b"0" * 64 + b"\n")
        with self.assertRaises(ClassificationError):
            classify_evidence_root(str(root))

    def test_18_rejects_corrupted_failure_record_status(self) -> None:
        root = self._build_valid_evidence_root(stderr_content=b"test\n")
        record_path = root / "models.capture.failure.json"
        data = json.loads(record_path.read_bytes())
        data["status"] = "captured"  # Wrong status
        record_path.write_bytes(canonical(data))
        with self.assertRaises(ClassificationError):
            classify_evidence_root(str(root))

    def test_19_rejects_mismatched_artifact_hashes(self) -> None:
        root = self._build_valid_evidence_root(stderr_content=b"test\n")
        record_path = root / "models.capture.failure.json"
        data = json.loads(record_path.read_bytes())
        data["artifacts"]["models.stderr"] = "0" * 64  # Wrong hash
        record_path.write_bytes(canonical(data))
        with self.assertRaises(ClassificationError):
            classify_evidence_root(str(root))

    def test_20_rejects_mismatched_profile_hash(self) -> None:
        root = self._build_valid_evidence_root(stderr_content=b"test\n")
        record_path = root / "models.capture.failure.json"
        data = json.loads(record_path.read_bytes())
        data["input_profile_sha256"] = "0" * 64  # Wrong hash
        record_path.write_bytes(canonical(data))
        with self.assertRaises(ClassificationError):
            classify_evidence_root(str(root))

    def test_21_rejects_non_empty_or_wrong_mode_scratch(self) -> None:
        root = self._build_valid_evidence_root(stderr_content=b"test\n")
        (root / "tmp" / "leak.txt").write_bytes(b"leak")
        with self.assertRaises(ClassificationError):
            classify_evidence_root(str(root))

    def test_24_rejects_non_private_runner_or_output_parent(self) -> None:
        root = self._build_valid_evidence_root(stderr_content=b"request timeout\n")
        runner = root / "models_capture_1_1_22_runner.py"
        os.chmod(str(runner), 0o644)
        with self.assertRaises(ClassificationError):
            classify_evidence_root(str(root))

        os.chmod(str(runner), 0o600)
        public_parent = self.root / "public-output"
        public_parent.mkdir(mode=0o755)
        with self.assertRaises(ClassificationError):
            classify_evidence_root(str(root), str(public_parent / "classification.json"))

    def test_22_rejects_existing_output_file_no_overwrite(self) -> None:
        root = self._build_valid_evidence_root(stderr_content=b"test\n")
        (root / OUTPUT_CLASSIFICATION_NAME).write_bytes(b"existing")
        with self.assertRaises(ClassificationError):
            classify_evidence_root(str(root))

    def test_23_output_contains_no_prose_or_paths_or_authority(self) -> None:
        stderr = b"open /private/tmp/secret_file.log: permission denied\n"
        root = self._build_valid_evidence_root(stderr_content=stderr)
        record = classify_evidence_root(str(root))
        raw_json = canonical(record).decode("utf-8")

        self.assertNotIn("secret_file.log", raw_json)
        self.assertNotIn("/private/tmp", raw_json)
        self.assertNotIn("permission denied", raw_json)
        self.assertFalse(record["limitations"]["activation_authorized"])
        self.assertFalse(record["limitations"]["retry_authorized"])
        self.assertFalse(record["limitations"]["routing_authority"])
        self.assertFalse(record["limitations"]["accepted_inventory"])


if __name__ == "__main__":
    unittest.main()
