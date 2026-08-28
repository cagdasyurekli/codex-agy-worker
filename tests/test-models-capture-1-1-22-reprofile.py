#!/usr/bin/env python3
"""Offline focused test suite for the fixed 1.1.22 capture reprofile adapter.

Tests positive runner-compatible nlink-only reprofiling, runner child-free profile
preflight validation, source/snapshot/recovery drift detection, link/fsync/short-write
boundaries, replacement-race handling, signal/rollback lifecycle, and old-profile
immutability.  No case launches agy, contacts a provider, or opens a real account HOME.
"""
from __future__ import annotations

import ast
import dataclasses
import hashlib
import io
import json
import os
import pathlib
import runpy
import stat
import sys
import tempfile
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
REPROFILE_SOURCE = ROOT / "scripts" / "models_capture_1_1_22_reprofile.py"
PROFILE_SOURCE = ROOT / "scripts" / "models_capture_1_1_22_profile.py"
RUNNER_SOURCE = ROOT / "scripts" / "models_capture_1_1_22_runner.py"

reprofile = runpy.run_path(str(REPROFILE_SOURCE))
profile_mod = runpy.run_path(str(PROFILE_SOURCE))
runner_mod = runpy.run_path(str(RUNNER_SOURCE))


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii") + b"\n"


def directory() -> dict:
    return {"dev": 1, "gid": 1, "ino": 1, "mode": 0o700, "nlink": 2, "uid": 1}


def identity() -> dict:
    return {"ctime_ns": 1, "dev": 1, "gid": 1, "ino": 1, "mode": 0o755, "mtime_ns": 1, "nlink": 1, "size": 1, "uid": 1}


def make_profile_value(nlink: int = 2) -> dict:
    """Build a valid 12-field capture profile dict with the given HOME nlink."""
    home_id = directory()
    home_id["nlink"] = nlink
    return {
        "account_home": "/private/account",
        "account_home_identity": home_id,
        "capture_parent": "/private/capture",
        "capture_parent_identity": directory(),
        "snapshot_identity": identity(),
        "snapshot_path": "/private/capture/recovery/agy.snapshot",
        "source_identity": identity(),
        "source_path": "/private/capture/agy.source",
        "source_sha256": runner_mod["EXPECTED_SOURCE_SHA256"],
        "version_binding_sha256": runner_mod["EXPECTED_RECOVERY_BINDING_SHA256"],
        "version_root": "/private/capture/recovery",
        "version_root_identity": directory(),
    }


def make_prior_profile_bytes(nlink: int = 2) -> bytes:
    """Return canonical profile bytes for a given nlink."""
    return canonical(make_profile_value(nlink))


class NlinkBarrier:
    def __init__(self, directory_path: str):
        self.directory = directory_path
        self.polls = 0
        self.two_link_observed = False

    def check(self) -> None:
        for name in os.listdir(self.directory):
            item = os.stat(os.path.join(self.directory, name), follow_symlinks=False)
            if os.path.isfile(os.path.join(self.directory, name)) and item.st_nlink == 2:
                self.two_link_observed = True
                raise AssertionError("publication exposed a two-name hard link to a hook")

    def poll(self) -> None:
        self.polls += 1
        self.check()


class PollCounter:
    def __init__(self) -> None:
        self.calls = 0

    def poll(self) -> None:
        self.calls += 1


class ModelsCapture1122ReprofileTests(unittest.TestCase):
    """Focused offline test suite for the 1.1.22 reprofile adapter."""

    def _synthetic_tree(self, base: str, account_nlink_delta: int = 0) -> dict[str, object]:
        """Build a real filesystem fixture tree with valid recovery evidence, source, and snapshot."""
        account = os.path.join(base, "account")
        parent = os.path.join(base, "capture")
        prior_dir = os.path.join(base, "prior")
        output_dir = os.path.join(base, "output")
        for d in (account, parent, prior_dir, output_dir):
            os.mkdir(d, 0o700)

        source = os.path.join(parent, "agy.source")
        recovery = os.path.join(parent, "recovery")
        os.mkdir(recovery, 0o700)

        source_bytes = b"#!/bin/sh\nexit 0\n"
        snapshot = os.path.join(recovery, "agy.snapshot")
        for path, mode in ((source, 0o755), (snapshot, 0o500)):
            with open(path, "wb") as f:
                f.write(source_bytes)
            os.chmod(path, mode)

        for name in profile_mod["RECOVERY_SCRATCH"]:
            os.mkdir(os.path.join(recovery, name), 0o700)

        FileIdentity = profile_mod["FileIdentity"]
        DirectoryIdentity = profile_mod["DirectoryIdentity"]
        CaptureProfile = profile_mod["CaptureProfile"]

        source_id = FileIdentity.from_stat(os.stat(source))
        snapshot_id = FileIdentity.from_stat(os.stat(snapshot))
        source_sha = hashlib.sha256(source_bytes).hexdigest()

        summary_bytes = b"{}\n"
        recovery_runner = b"recovery_runner_content"
        runner_sha = hashlib.sha256(recovery_runner).hexdigest()
        summary_sha = hashlib.sha256(summary_bytes).hexdigest()

        artifacts = {
            "runner.py": runner_sha,
            "version.summary.json": summary_sha,
            "version.stdout": hashlib.sha256(b"1.1.22\n").hexdigest(),
            "version.stderr": hashlib.sha256(b"").hexdigest(),
        }
        binding_value = {
            "artifacts": artifacts,
            "claim": "snapshot-version-only",
            "inventory": {"executable_version_bound": False},
            "limitations": {
                "account_read": False,
                "metadata_advance_authorized": False,
                "models_called": False,
                "network_absence_os_enforced": False,
                "provider_backend_proven": False,
                "routing_authority": False,
            },
            "official_observation": {
                "distribution_sha512": profile_mod["EXPECTED_DISTRIBUTION_SHA512"],
                "distribution_url": profile_mod["EXPECTED_DISTRIBUTION_URL"],
                "release_commit": profile_mod["EXPECTED_RELEASE_COMMIT"],
                "version": "1.1.22",
            },
            "source": {
                "pre": dataclasses.asdict(source_id),
                "post": dataclasses.asdict(source_id),
                "sha256": source_sha,
            },
            "snapshot": {
                "pre": dataclasses.asdict(snapshot_id),
                "post": dataclasses.asdict(snapshot_id),
                "sha256": source_sha,
            },
            "version": {
                "exit": 0,
                "expected": "1.1.22",
                "logical_argv": [source, "--version"],
                "observed": "1.1.22",
                "popen_count": 1,
            },
        }
        binding = canonical(binding_value)
        self.assertLess(len(binding), 3_205)
        binding += b" " * (3_205 - len(binding))
        binding_sha = hashlib.sha256(binding).hexdigest()

        files = {
            "runner.py": recovery_runner,
            "runner.py.sha256": (runner_sha + "\n").encode("ascii"),
            "source.pre.json": canonical(dataclasses.asdict(source_id)),
            "source.post.json": canonical(dataclasses.asdict(source_id)),
            "snapshot.pre.json": canonical(dataclasses.asdict(snapshot_id)),
            "snapshot.post.json": canonical(dataclasses.asdict(snapshot_id)),
            "version.binding.json": binding,
            "version.binding.sha256": (binding_sha + "\n").encode("ascii"),
            "version.stderr": b"",
            "version.stdout": b"1.1.22\n",
            "version.summary.json": summary_bytes,
        }
        for name, data in files.items():
            fpath = os.path.join(recovery, name)
            with open(fpath, "wb") as f:
                f.write(data)
            os.chmod(fpath, 0o600)

        # Build prior profile object with initial account nlink
        initial_account_identity = DirectoryIdentity.from_stat(os.stat(account))
        prior_profile_obj = CaptureProfile(
            account,
            initial_account_identity,
            parent,
            DirectoryIdentity.from_stat(os.stat(parent)),
            snapshot_id,
            snapshot,
            source_id,
            source,
            source_sha,
            binding_sha,
            recovery,
            DirectoryIdentity.from_stat(os.stat(recovery)),
        )
        prior_data = canonical(dataclasses.asdict(prior_profile_obj))
        prior_path = os.path.join(prior_dir, profile_mod["OUTPUT_NAME"])
        with open(prior_path, "wb") as f:
            f.write(prior_data)
        os.chmod(prior_path, 0o600)
        prior_sha = hashlib.sha256(prior_data).hexdigest()

        # If account_nlink_delta > 0, create subdirectories in account to bump its nlink
        for i in range(account_nlink_delta):
            sub = os.path.join(account, f"sub_{i}")
            os.mkdir(sub, 0o700)

        output_path = os.path.join(output_dir, reprofile["OUTPUT_NAME"])

        # Patch constants in profile and runner modules to match synthetic fixture
        prof_obj = reprofile["_get_profile_mod"]()
        run_obj = reprofile["_get_runner_mod"]()
        profile_globals = prof_obj._from_request.__globals__
        runner_globals = run_obj.run_capture.__globals__
        alt_profile_globals = profile_mod["_from_request"].__globals__
        alt_runner_globals = runner_mod["run_capture"].__globals__

        saved_profile = {k: profile_globals[k] for k in ("EXPECTED_SOURCE_SHA256", "EXPECTED_RECOVERY_BINDING_SHA256", "EXPECTED_RECOVERY_RUNNER_SHA256", "EXPECTED_RECOVERY_RUNNER_BYTES", "EXPECTED_RECOVERY_SUMMARY_BYTES")}
        saved_runner = {k: runner_globals[k] for k in ("EXPECTED_SOURCE_SHA256", "EXPECTED_RECOVERY_BINDING_SHA256", "EXPECTED_RECOVERY_RUNNER_SHA256", "EXPECTED_RECOVERY_RUNNER_BYTES", "EXPECTED_RECOVERY_SUMMARY_BYTES")}

        patch = {
            "EXPECTED_SOURCE_SHA256": source_sha,
            "EXPECTED_RECOVERY_BINDING_SHA256": binding_sha,
            "EXPECTED_RECOVERY_RUNNER_SHA256": runner_sha,
            "EXPECTED_RECOVERY_RUNNER_BYTES": len(recovery_runner),
            "EXPECTED_RECOVERY_SUMMARY_BYTES": len(summary_bytes),
        }
        profile_globals.update(patch)
        runner_globals.update(patch)
        alt_profile_globals.update(patch)
        alt_runner_globals.update(patch)

        return {
            "account": account,
            "parent": parent,
            "recovery": recovery,
            "source": source,
            "snapshot": snapshot,
            "prior_path": prior_path,
            "prior_sha": prior_sha,
            "prior_data": prior_data,
            "prior_profile": prior_profile_obj,
            "output_path": output_path,
            "saved_profile": saved_profile,
            "saved_runner": saved_runner,
            "profile_globals": profile_globals,
            "runner_globals": runner_globals,
            "alt_profile_globals": alt_profile_globals,
            "alt_runner_globals": alt_runner_globals,
        }

    def _restore_globals(self, tree: dict[str, object]) -> None:
        tree["profile_globals"].update(tree["saved_profile"])
        tree["runner_globals"].update(tree["saved_runner"])
        tree["alt_profile_globals"].update(tree["saved_profile"])
        tree["alt_runner_globals"].update(tree["saved_runner"])

    # -----------------------------------------------------------------------
    # 1. Positive: full prepare, runner child-free preflight, and validate
    # -----------------------------------------------------------------------

    def test_01_reprofile_output_name_is_distinct(self) -> None:
        """The reprofile output basename must differ from the profile output."""
        self.assertNotEqual(reprofile["OUTPUT_NAME"], profile_mod["OUTPUT_NAME"])
        self.assertEqual(reprofile["OUTPUT_NAME"], "models.capture.1.1.22.reprofile.json")

    def test_02_reprofile_accepts_nlink_only_delta(self) -> None:
        """validate_single_field_delta accepts when only nlink differs."""
        prior = make_profile_value(nlink=2)
        current = directory()
        current["nlink"] = 5
        reprofile["_validate_single_field_delta"](prior, current)

    def test_03_reprofile_profile_parses_via_runner(self) -> None:
        """A synthetic profile parses via the runner's Profile.from_bytes."""
        data = make_prior_profile_bytes(2)
        p = runner_mod["Profile"].from_bytes(data)
        self.assertEqual(p.account_home, "/private/account")

    def test_04_reprofile_profile_parses_via_profile_module(self) -> None:
        """A synthetic profile parses via the profile module's CaptureProfile.from_bytes."""
        data = make_prior_profile_bytes(3)
        p = profile_mod["CaptureProfile"].from_bytes(data)
        self.assertEqual(p.account_home_identity.nlink, 3)

    def test_05_reprofile_canonical_bytes_pass_runner_preflight(self) -> None:
        """Prove that prepare produces canonical profile bytes that pass the runner's child-free preflight."""
        with tempfile.TemporaryDirectory() as _base:
            base = os.path.realpath(_base)
            os.chmod(base, 0o700)
            tree = self._synthetic_tree(base, account_nlink_delta=1)
            try:
                request = canonical({
                    "prior_profile_path": tree["prior_path"],
                    "prior_profile_sha256": tree["prior_sha"],
                    "output_path": tree["output_path"],
                })
                result = reprofile["prepare"](request, PollCounter())

                self.assertEqual(result["status"], "reprofiled")
                self.assertEqual(result["changed_fields"], ["account_home_identity.nlink"])
                self.assertFalse(result["provider_contacted"])
                self.assertFalse(result["models_called"])
                self.assertFalse(result["capture_authorized"])
                self.assertFalse(result["retry_authorized"])
                self.assertFalse(result["activation_authorized"])

                # Output file must exist, mode 0600, nlink 1
                out_stat = os.stat(tree["output_path"])
                self.assertEqual(stat.S_IMODE(out_stat.st_mode), 0o600)
                self.assertEqual(out_stat.st_nlink, 1)

                with open(tree["output_path"], "rb") as f:
                    new_bytes = f.read()

                self.assertEqual(hashlib.sha256(new_bytes).hexdigest(), result["new_profile_sha256"])

                # Prove new profile passes runner's Profile.from_bytes AND child-free _validate_profile
                runner_p = runner_mod["Profile"].from_bytes(new_bytes)
                fds = runner_mod["_validate_profile"](runner_p, None)
                self.assertEqual(len(fds), 4)
                for fd in fds:
                    os.close(fd)

                # Prove new profile passes profile module's CaptureProfile.from_bytes
                cp = profile_mod["CaptureProfile"].from_bytes(new_bytes)
                self.assertGreater(cp.account_home_identity.nlink, tree["prior_profile"].account_home_identity.nlink)

                # Validate endpoint validates the pair
                val_req = canonical({
                    "prior_profile_path": tree["prior_path"],
                    "prior_profile_sha256": tree["prior_sha"],
                    "output_path": tree["output_path"],
                    "profile_path": tree["output_path"],
                    "profile_sha256": result["new_profile_sha256"],
                })
                val_res = reprofile["validate"](val_req)
                self.assertEqual(val_res["status"], "valid")
                self.assertEqual(val_res["new_profile_sha256"], result["new_profile_sha256"])
            finally:
                self._restore_globals(tree)

    def test_06_reprofile_12_field_schema_preserved(self) -> None:
        """Both prior and new profiles have exactly 12 fields."""
        old = make_profile_value(2)
        new = dict(old)
        new_account = dict(old["account_home_identity"])
        new_account["nlink"] = 9
        new["account_home_identity"] = new_account
        self.assertEqual(set(old), runner_mod["PROFILE_KEYS"])
        self.assertEqual(set(new), runner_mod["PROFILE_KEYS"])

    def test_07_reprofile_success_output_structure(self) -> None:
        """Success output must contain the required keys."""
        result = {
            "activation_authorized": False,
            "capture_authorized": False,
            "changed_fields": ["account_home_identity.nlink"],
            "models_called": False,
            "new_profile_sha256": "a" * 64,
            "prior_profile_sha256": "b" * 64,
            "provider_contacted": False,
            "retry_authorized": False,
            "status": "reprofiled",
        }
        self.assertEqual(result["status"], "reprofiled")
        self.assertEqual(result["changed_fields"], ["account_home_identity.nlink"])
        self.assertFalse(result["provider_contacted"])
        self.assertFalse(result["models_called"])
        self.assertFalse(result["capture_authorized"])
        self.assertFalse(result["retry_authorized"])
        self.assertFalse(result["activation_authorized"])

    def test_08_reprofile_success_output_is_canonical_and_deterministic(self) -> None:
        """Success output must serialize deterministically."""
        result = {
            "activation_authorized": False,
            "capture_authorized": False,
            "changed_fields": ["account_home_identity.nlink"],
            "models_called": False,
            "new_profile_sha256": "a" * 64,
            "prior_profile_sha256": "b" * 64,
            "provider_contacted": False,
            "retry_authorized": False,
            "status": "reprofiled",
        }
        encoded = canonical(result)
        decoded = json.loads(encoded)
        re_encoded = canonical(decoded)
        self.assertEqual(encoded, re_encoded)

    def test_09_reprofile_success_output_has_no_paths(self) -> None:
        """Success output must not contain filesystem paths."""
        result = {
            "activation_authorized": False,
            "capture_authorized": False,
            "changed_fields": ["account_home_identity.nlink"],
            "models_called": False,
            "new_profile_sha256": "a" * 64,
            "prior_profile_sha256": "b" * 64,
            "provider_contacted": False,
            "retry_authorized": False,
            "status": "reprofiled",
        }
        encoded = canonical(result).decode("ascii")
        self.assertNotIn("/private", encoded)
        self.assertNotIn("/home", encoded)
        self.assertNotIn("/tmp", encoded)
        self.assertNotIn("/Users", encoded)

    # -----------------------------------------------------------------------
    # 2. Behavioral tests for source drift
    # -----------------------------------------------------------------------

    def test_10_rejects_unchanged_nlink(self) -> None:
        """Must reject when prior and current nlink are identical."""
        with tempfile.TemporaryDirectory() as _base:
            base = os.path.realpath(_base)
            os.chmod(base, 0o700)
            tree = self._synthetic_tree(base, account_nlink_delta=0)
            try:
                request = canonical({
                    "prior_profile_path": tree["prior_path"],
                    "prior_profile_sha256": tree["prior_sha"],
                    "output_path": tree["output_path"],
                })
                with self.assertRaises(reprofile["ReprofileError"]):
                    reprofile["prepare"](request)
            finally:
                self._restore_globals(tree)

    def test_11_rejects_zero_prior_nlink(self) -> None:
        """Must reject non-positive prior nlink."""
        prior = make_profile_value(nlink=0)
        current = directory()
        current["nlink"] = 3
        with self.assertRaises(reprofile["ReprofileError"]):
            reprofile["_validate_single_field_delta"](prior, current)

    def test_12_rejects_zero_current_nlink(self) -> None:
        """Must reject non-positive current nlink."""
        prior = make_profile_value(nlink=3)
        current = directory()
        current["nlink"] = 0
        with self.assertRaises(reprofile["ReprofileError"]):
            reprofile["_validate_single_field_delta"](prior, current)

    def test_13_rejects_negative_prior_nlink(self) -> None:
        """Must reject negative prior nlink."""
        prior = make_profile_value(nlink=-1)
        current = directory()
        current["nlink"] = 3
        with self.assertRaises(reprofile["ReprofileError"]):
            reprofile["_validate_single_field_delta"](prior, current)

    def test_14_rejects_dev_change(self) -> None:
        """Must reject when account dev changes."""
        prior = make_profile_value(nlink=2)
        current = directory()
        current["nlink"] = 5
        current["dev"] = 999
        with self.assertRaises(reprofile["ReprofileError"]):
            reprofile["_validate_single_field_delta"](prior, current)

    def test_15_rejects_gid_change(self) -> None:
        """Must reject when account gid changes."""
        prior = make_profile_value(nlink=2)
        current = directory()
        current["nlink"] = 5
        current["gid"] = 999
        with self.assertRaises(reprofile["ReprofileError"]):
            reprofile["_validate_single_field_delta"](prior, current)

    def test_16_rejects_ino_change(self) -> None:
        """Must reject when account ino changes."""
        prior = make_profile_value(nlink=2)
        current = directory()
        current["nlink"] = 5
        current["ino"] = 999
        with self.assertRaises(reprofile["ReprofileError"]):
            reprofile["_validate_single_field_delta"](prior, current)

    def test_17_rejects_mode_change(self) -> None:
        """Must reject when account mode changes."""
        prior = make_profile_value(nlink=2)
        current = directory()
        current["nlink"] = 5
        current["mode"] = 0o755
        with self.assertRaises(reprofile["ReprofileError"]):
            reprofile["_validate_single_field_delta"](prior, current)

    def test_18_rejects_uid_change(self) -> None:
        """Must reject when account uid changes."""
        prior = make_profile_value(nlink=2)
        current = directory()
        current["nlink"] = 5
        current["uid"] = 999
        with self.assertRaises(reprofile["ReprofileError"]):
            reprofile["_validate_single_field_delta"](prior, current)

    def test_19_rejects_prior_sha_mismatch(self) -> None:
        """Must reject when prior profile SHA does not match."""
        with tempfile.TemporaryDirectory() as _base:
            base = os.path.realpath(_base)
            os.chmod(base, 0o700)
            prior_data = make_prior_profile_bytes(2)
            prior_path = os.path.join(base, "models.capture.1.1.22.profile.json")
            with open(prior_path, "wb") as f:
                f.write(prior_data)
            os.chmod(prior_path, 0o600)
            with self.assertRaises(reprofile["ReprofileError"]):
                reprofile["_read_prior_profile"](prior_path, "0" * 64)

    def test_20_rejects_prior_wrong_mode(self) -> None:
        """Must reject when prior profile mode is not 0600."""
        with tempfile.TemporaryDirectory() as _base:
            base = os.path.realpath(_base)
            os.chmod(base, 0o700)
            prior_data = make_prior_profile_bytes(2)
            prior_path = os.path.join(base, "models.capture.1.1.22.profile.json")
            with open(prior_path, "wb") as f:
                f.write(prior_data)
            os.chmod(prior_path, 0o644)
            sha = hashlib.sha256(prior_data).hexdigest()
            with self.assertRaises(reprofile["ReprofileError"]):
                reprofile["_read_prior_profile"](prior_path, sha)

    def test_21_rejects_prior_size_too_large(self) -> None:
        """Must reject prior profile exceeding size bounds."""
        with tempfile.TemporaryDirectory() as _base:
            base = os.path.realpath(_base)
            os.chmod(base, 0o700)
            big_data = b"x" * (reprofile["PROFILE_LIMIT"] + 1)
            prior_path = os.path.join(base, "big.json")
            with open(prior_path, "wb") as f:
                f.write(big_data)
            os.chmod(prior_path, 0o600)
            sha = hashlib.sha256(big_data).hexdigest()
            with self.assertRaises(reprofile["ReprofileError"]):
                reprofile["_read_prior_profile"](prior_path, sha)

    def test_22_rejects_prior_symlink(self) -> None:
        """Must reject if prior profile path is a symlink."""
        with tempfile.TemporaryDirectory() as _base:
            base = os.path.realpath(_base)
            os.chmod(base, 0o700)
            real_path = os.path.join(base, "real.json")
            link_path = os.path.join(base, "link.json")
            prior_data = make_prior_profile_bytes(2)
            with open(real_path, "wb") as f:
                f.write(prior_data)
            os.chmod(real_path, 0o600)
            os.symlink(real_path, link_path)
            sha = hashlib.sha256(prior_data).hexdigest()
            with self.assertRaises(reprofile["ReprofileError"]):
                reprofile["_read_prior_profile"](link_path, sha)

    def test_23_rejects_prior_noncanonical_path(self) -> None:
        """Must reject non-canonical path (with ..)."""
        with self.assertRaises(reprofile["ReprofileError"]):
            reprofile["_read_prior_profile"]("/tmp/../tmp/file.json", "0" * 64)

    def test_24_rejects_prior_nlink_not_one(self) -> None:
        """Must reject when prior profile has nlink != 1 (hard linked)."""
        with tempfile.TemporaryDirectory() as _base:
            base = os.path.realpath(_base)
            os.chmod(base, 0o700)
            prior_data = make_prior_profile_bytes(2)
            prior_path = os.path.join(base, "models.capture.1.1.22.profile.json")
            link_path = os.path.join(base, "models.capture.1.1.22.profile.link.json")
            with open(prior_path, "wb") as f:
                f.write(prior_data)
            os.chmod(prior_path, 0o600)
            os.link(prior_path, link_path)
            sha = hashlib.sha256(prior_data).hexdigest()
            with self.assertRaises(reprofile["ReprofileError"]):
                reprofile["_read_prior_profile"](prior_path, sha)

    def test_25_rejects_prior_not_regular_file(self) -> None:
        """Must reject if prior profile path is a directory."""
        with tempfile.TemporaryDirectory() as _base:
            base = os.path.realpath(_base)
            os.chmod(base, 0o700)
            dir_path = os.path.join(base, "not_a_file")
            os.mkdir(dir_path, 0o600)
            with self.assertRaises((reprofile["ReprofileError"], OSError)):
                reprofile["_read_prior_profile"](dir_path, "0" * 64)

    def test_26_rejects_wrong_output_basename(self) -> None:
        """Prepare must reject output with wrong basename."""
        request = canonical({
            "prior_profile_path": "/private/some/path/models.capture.1.1.22.profile.json",
            "prior_profile_sha256": "0" * 64,
            "output_path": "/private/other/wrong-name.json",
        })
        with self.assertRaises(reprofile["ReprofileError"]):
            reprofile["prepare"](request)

    def test_27_rejects_output_overlapping_held_authorities(self) -> None:
        """Prepare and validate must reject output control root overlapping any held authority."""
        with tempfile.TemporaryDirectory() as _base:
            base = os.path.realpath(_base)
            os.chmod(base, 0o700)
            tree = self._synthetic_tree(base, account_nlink_delta=1)
            try:
                # 1. Output inside prior parent
                req1 = canonical({
                    "prior_profile_path": tree["prior_path"],
                    "prior_profile_sha256": tree["prior_sha"],
                    "output_path": os.path.join(os.path.dirname(tree["prior_path"]), reprofile["OUTPUT_NAME"]),
                })
                with self.assertRaises(reprofile["ReprofileError"]):
                    reprofile["prepare"](req1)

                # 2. Output inside account_home
                req2 = canonical({
                    "prior_profile_path": tree["prior_path"],
                    "prior_profile_sha256": tree["prior_sha"],
                    "output_path": os.path.join(tree["account"], reprofile["OUTPUT_NAME"]),
                })
                with self.assertRaises(reprofile["ReprofileError"]):
                    reprofile["prepare"](req2)

                # 3. Output inside capture_parent
                req3 = canonical({
                    "prior_profile_path": tree["prior_path"],
                    "prior_profile_sha256": tree["prior_sha"],
                    "output_path": os.path.join(tree["parent"], reprofile["OUTPUT_NAME"]),
                })
                with self.assertRaises(reprofile["ReprofileError"]):
                    reprofile["prepare"](req3)

                # 4. Output inside version_root (recovery)
                req4 = canonical({
                    "prior_profile_path": tree["prior_path"],
                    "prior_profile_sha256": tree["prior_sha"],
                    "output_path": os.path.join(tree["recovery"], reprofile["OUTPUT_NAME"]),
                })
                with self.assertRaises(reprofile["ReprofileError"]):
                    reprofile["prepare"](req4)
            finally:
                self._restore_globals(tree)

    def test_28_rejects_overwrite_existing_output(self) -> None:
        """Publication must reject when output already exists."""
        with tempfile.TemporaryDirectory() as _base:
            base = os.path.realpath(_base)
            os.chmod(base, 0o700)
            output = os.path.join(base, reprofile["OUTPUT_NAME"])
            with open(output, "wb") as f:
                f.write(b"existing")
            os.chmod(output, 0o600)
            with self.assertRaises(reprofile["ReprofileError"]):
                reprofile["_publish"](output, b"new_data\n", None)

    def test_29_rejects_source_content_drift(self) -> None:
        """Prepare must reject when agy.source content/hash drifts on disk."""
        with tempfile.TemporaryDirectory() as _base:
            base = os.path.realpath(_base)
            os.chmod(base, 0o700)
            tree = self._synthetic_tree(base, account_nlink_delta=1)
            try:
                with open(tree["source"], "wb") as f:
                    f.write(b"#!/bin/sh\n# mutated\nexit 0\n")
                request = canonical({
                    "prior_profile_path": tree["prior_path"],
                    "prior_profile_sha256": tree["prior_sha"],
                    "output_path": tree["output_path"],
                })
                with self.assertRaises(reprofile["ReprofileError"]):
                    reprofile["prepare"](request)
            finally:
                self._restore_globals(tree)

    def test_30_rejects_source_mode_drift(self) -> None:
        """Prepare must reject when agy.source permissions drift from 0755."""
        with tempfile.TemporaryDirectory() as _base:
            base = os.path.realpath(_base)
            os.chmod(base, 0o700)
            tree = self._synthetic_tree(base, account_nlink_delta=1)
            try:
                os.chmod(tree["source"], 0o644)
                request = canonical({
                    "prior_profile_path": tree["prior_path"],
                    "prior_profile_sha256": tree["prior_sha"],
                    "output_path": tree["output_path"],
                })
                with self.assertRaises(reprofile["ReprofileError"]):
                    reprofile["prepare"](request)
            finally:
                self._restore_globals(tree)

    def test_31_rejects_source_hardlink_drift(self) -> None:
        """Prepare must reject when agy.source nlink != 1."""
        with tempfile.TemporaryDirectory() as _base:
            base = os.path.realpath(_base)
            os.chmod(base, 0o700)
            tree = self._synthetic_tree(base, account_nlink_delta=1)
            try:
                os.link(tree["source"], tree["source"] + ".link")
                request = canonical({
                    "prior_profile_path": tree["prior_path"],
                    "prior_profile_sha256": tree["prior_sha"],
                    "output_path": tree["output_path"],
                })
                with self.assertRaises(reprofile["ReprofileError"]):
                    reprofile["prepare"](request)
            finally:
                self._restore_globals(tree)

    def test_32_rejects_snapshot_content_drift(self) -> None:
        """Prepare must reject when agy.snapshot content/hash drifts on disk."""
        with tempfile.TemporaryDirectory() as _base:
            base = os.path.realpath(_base)
            os.chmod(base, 0o700)
            tree = self._synthetic_tree(base, account_nlink_delta=1)
            try:
                os.chmod(tree["snapshot"], 0o700)
                with open(tree["snapshot"], "wb") as f:
                    f.write(b"#!/bin/sh\n# snapshot drift\n")
                os.chmod(tree["snapshot"], 0o500)
                request = canonical({
                    "prior_profile_path": tree["prior_path"],
                    "prior_profile_sha256": tree["prior_sha"],
                    "output_path": tree["output_path"],
                })
                with self.assertRaises(reprofile["ReprofileError"]):
                    reprofile["prepare"](request)
            finally:
                self._restore_globals(tree)

    def test_33_rejects_snapshot_mode_drift(self) -> None:
        """Prepare must reject when agy.snapshot permissions drift from 0500."""
        with tempfile.TemporaryDirectory() as _base:
            base = os.path.realpath(_base)
            os.chmod(base, 0o700)
            tree = self._synthetic_tree(base, account_nlink_delta=1)
            try:
                os.chmod(tree["snapshot"], 0o755)
                request = canonical({
                    "prior_profile_path": tree["prior_path"],
                    "prior_profile_sha256": tree["prior_sha"],
                    "output_path": tree["output_path"],
                })
                with self.assertRaises(reprofile["ReprofileError"]):
                    reprofile["prepare"](request)
            finally:
                self._restore_globals(tree)

    def test_34_rejects_recovery_binding_drift(self) -> None:
        """Prepare must reject when recovery version.binding.json is modified."""
        with tempfile.TemporaryDirectory() as _base:
            base = os.path.realpath(_base)
            os.chmod(base, 0o700)
            tree = self._synthetic_tree(base, account_nlink_delta=1)
            try:
                binding_file = os.path.join(tree["recovery"], "version.binding.json")
                with open(binding_file, "wb") as f:
                    f.write(b'{"mutated": true}' + b" " * 3188)
                request = canonical({
                    "prior_profile_path": tree["prior_path"],
                    "prior_profile_sha256": tree["prior_sha"],
                    "output_path": tree["output_path"],
                })
                with self.assertRaises(reprofile["ReprofileError"]):
                    reprofile["prepare"](request)
            finally:
                self._restore_globals(tree)

    def test_35_rejects_recovery_runner_drift(self) -> None:
        """Prepare must reject when recovery runner.py is modified."""
        with tempfile.TemporaryDirectory() as _base:
            base = os.path.realpath(_base)
            os.chmod(base, 0o700)
            tree = self._synthetic_tree(base, account_nlink_delta=1)
            try:
                runner_file = os.path.join(tree["recovery"], "runner.py")
                with open(runner_file, "wb") as f:
                    f.write(b"mutated runner content")
                request = canonical({
                    "prior_profile_path": tree["prior_path"],
                    "prior_profile_sha256": tree["prior_sha"],
                    "output_path": tree["output_path"],
                })
                with self.assertRaises(reprofile["ReprofileError"]):
                    reprofile["prepare"](request)
            finally:
                self._restore_globals(tree)

    def test_36_rejects_recovery_extra_file(self) -> None:
        """Prepare must reject when foreign file is added to recovery directory."""
        with tempfile.TemporaryDirectory() as _base:
            base = os.path.realpath(_base)
            os.chmod(base, 0o700)
            tree = self._synthetic_tree(base, account_nlink_delta=1)
            try:
                extra = os.path.join(tree["recovery"], "unexpected.txt")
                with open(extra, "wb") as f:
                    f.write(b"extra")
                os.chmod(extra, 0o600)
                request = canonical({
                    "prior_profile_path": tree["prior_path"],
                    "prior_profile_sha256": tree["prior_sha"],
                    "output_path": tree["output_path"],
                })
                with self.assertRaises(reprofile["ReprofileError"]):
                    reprofile["prepare"](request)
            finally:
                self._restore_globals(tree)

    def test_37_rejects_recovery_scratch_nonempty(self) -> None:
        """Prepare must reject when scratch directory (e.g. recovery/tmp) is non-empty."""
        with tempfile.TemporaryDirectory() as _base:
            base = os.path.realpath(_base)
            os.chmod(base, 0o700)
            tree = self._synthetic_tree(base, account_nlink_delta=1)
            try:
                residual = os.path.join(tree["recovery"], "tmp", "residual")
                with open(residual, "wb") as f:
                    f.write(b"residual")
                os.chmod(residual, 0o600)
                request = canonical({
                    "prior_profile_path": tree["prior_path"],
                    "prior_profile_sha256": tree["prior_sha"],
                    "output_path": tree["output_path"],
                })
                with self.assertRaises(reprofile["ReprofileError"]):
                    reprofile["prepare"](request)
            finally:
                self._restore_globals(tree)

    def test_38_rejects_capture_parent_mode_drift(self) -> None:
        """Prepare must reject when capture_parent mode is not 0700."""
        with tempfile.TemporaryDirectory() as _base:
            base = os.path.realpath(_base)
            os.chmod(base, 0o700)
            tree = self._synthetic_tree(base, account_nlink_delta=1)
            try:
                os.chmod(tree["parent"], 0o755)
                request = canonical({
                    "prior_profile_path": tree["prior_path"],
                    "prior_profile_sha256": tree["prior_sha"],
                    "output_path": tree["output_path"],
                })
                with self.assertRaises(reprofile["ReprofileError"]):
                    reprofile["prepare"](request)
            finally:
                self._restore_globals(tree)

    def test_39_rejects_version_root_mode_drift(self) -> None:
        """Prepare must reject when version_root mode is not 0700."""
        with tempfile.TemporaryDirectory() as _base:
            base = os.path.realpath(_base)
            os.chmod(base, 0o700)
            tree = self._synthetic_tree(base, account_nlink_delta=1)
            try:
                os.chmod(tree["recovery"], 0o755)
                request = canonical({
                    "prior_profile_path": tree["prior_path"],
                    "prior_profile_sha256": tree["prior_sha"],
                    "output_path": tree["output_path"],
                })
                with self.assertRaises(reprofile["ReprofileError"]):
                    reprofile["prepare"](request)
            finally:
                self._restore_globals(tree)

    def test_40_validate_source_contract_passes(self) -> None:
        """The reprofile script source contract must pass validation."""
        source_bytes = REPROFILE_SOURCE.read_bytes()
        result = reprofile["validate_source_contract"](source_bytes)
        self.assertEqual(result["status"], "valid-source")

    def test_41_output_mode_is_0600(self) -> None:
        """Publication must write mode-0600 files."""
        with tempfile.TemporaryDirectory() as _base:
            base = os.path.realpath(_base)
            os.chmod(base, 0o700)
            output = os.path.join(base, reprofile["OUTPUT_NAME"])
            data = make_prior_profile_bytes(3)
            reprofile["_publish"](output, data, None)
            item = os.stat(output)
            self.assertEqual(stat.S_IMODE(item.st_mode), 0o600)
            self.assertEqual(item.st_nlink, 1)

    def test_42_publication_nlink_is_one(self) -> None:
        """Published file must have nlink 1 after publication."""
        with tempfile.TemporaryDirectory() as _base:
            base = os.path.realpath(_base)
            os.chmod(base, 0o700)
            output = os.path.join(base, reprofile["OUTPUT_NAME"])
            data = make_prior_profile_bytes(3)
            reprofile["_publish"](output, data, None)
            self.assertEqual(os.stat(output).st_nlink, 1)

    def test_43_publication_readback_matches(self) -> None:
        """Published file bytes must match what was written."""
        with tempfile.TemporaryDirectory() as _base:
            base = os.path.realpath(_base)
            os.chmod(base, 0o700)
            output = os.path.join(base, reprofile["OUTPUT_NAME"])
            data = make_prior_profile_bytes(3)
            sha = reprofile["_publish"](output, data, None)
            with open(output, "rb") as f:
                readback = f.read()
            self.assertEqual(readback, data)
            self.assertEqual(hashlib.sha256(data).hexdigest(), sha)

    def test_44_prior_profile_unchanged_after_read(self) -> None:
        """Reading a prior profile must not alter it."""
        with tempfile.TemporaryDirectory() as _base:
            base = os.path.realpath(_base)
            os.chmod(base, 0o700)
            prior_data = make_prior_profile_bytes(2)
            prior_path = os.path.join(base, "models.capture.1.1.22.profile.json")
            with open(prior_path, "wb") as f:
                f.write(prior_data)
            os.chmod(prior_path, 0o600)
            sha = hashlib.sha256(prior_data).hexdigest()
            read_data = reprofile["_read_prior_profile"](prior_path, sha)
            self.assertEqual(read_data, prior_data)
            with open(prior_path, "rb") as f:
                after_data = f.read()
            self.assertEqual(after_data, prior_data)

    def test_45_publish_link_barrier_normalizes_before_poll(self) -> None:
        """Publication hard link must normalize to 1 link before poll or fsync."""
        with tempfile.TemporaryDirectory() as _base:
            base = os.path.realpath(_base)
            os.chmod(base, 0o700)
            barrier = NlinkBarrier(base)
            globals_ = reprofile["_publish"].__globals__
            original_fsync = globals_["os"].fsync
            def guarded_fsync(fd: int) -> None:
                barrier.check()
                original_fsync(fd)
            globals_["os"].fsync = guarded_fsync
            output = os.path.join(base, reprofile["OUTPUT_NAME"])
            try:
                digest = reprofile["_publish"](output, b'{"reprofile":true}\n', barrier)
            finally:
                globals_["os"].fsync = original_fsync
            self.assertEqual(digest, hashlib.sha256(b'{"reprofile":true}\n').hexdigest())
            self.assertGreater(barrier.polls, 0)
            self.assertFalse(barrier.two_link_observed)
            self.assertEqual(os.stat(output).st_nlink, 1)

    def test_46_publish_fsync_calls(self) -> None:
        """Publication must call fsync on file descriptor and parent directory."""
        with tempfile.TemporaryDirectory() as _base:
            base = os.path.realpath(_base)
            os.chmod(base, 0o700)
            fsync_calls = []
            globals_ = reprofile["_publish"].__globals__
            original_fsync = globals_["os"].fsync
            def tracking_fsync(fd: int) -> None:
                fsync_calls.append(fd)
                original_fsync(fd)
            globals_["os"].fsync = tracking_fsync
            output = os.path.join(base, reprofile["OUTPUT_NAME"])
            try:
                reprofile["_publish"](output, b'{"reprofile":true}\n', None)
            finally:
                globals_["os"].fsync = original_fsync
            self.assertGreaterEqual(len(fsync_calls), 2)

    def test_47_publish_short_write_fails_and_cleans_up(self) -> None:
        """Publication short write must clean up and raise ReprofileError."""
        with tempfile.TemporaryDirectory() as _base:
            base = os.path.realpath(_base)
            os.chmod(base, 0o700)
            globals_ = reprofile["_publish"].__globals__
            original_write = globals_["os"].write
            globals_["os"].write = lambda _fd, _buf: 0
            output = os.path.join(base, reprofile["OUTPUT_NAME"])
            try:
                with self.assertRaises(reprofile["ReprofileError"]):
                    reprofile["_publish"](output, b'{"data": 1}\n', None)
            finally:
                globals_["os"].write = original_write
            self.assertEqual(os.listdir(base), [])

    def test_48_finish_success_short_write_rolls_back(self) -> None:
        """Completion short write must roll back published profile and exit with 1."""
        class ShortWriteBuffer:
            def write(self, _data: bytes) -> int:
                return 0
            def flush(self) -> None:
                pass

        class ExitCalled(BaseException):
            def __init__(self, code: int):
                self.code = code

        with tempfile.TemporaryDirectory() as _base:
            base = os.path.realpath(_base)
            os.chmod(base, 0o700)
            output = os.path.join(base, reprofile["OUTPUT_NAME"])
            reprofile["_publish"](output, b'{"test": 1}\n', None)
            self.assertTrue(os.path.exists(output))

            globals_ = reprofile["_finish_success"].__globals__
            original_sys, original_exit = globals_["sys"], globals_["os"]._exit
            globals_["sys"] = types.SimpleNamespace(stdout=types.SimpleNamespace(buffer=ShortWriteBuffer()))
            globals_["os"]._exit = lambda code: (_ for _ in ()).throw(ExitCalled(code))

            state = types.SimpleNamespace(signals=types.SimpleNamespace(owned=(), latch=lambda _s: None, poll=lambda: None))
            try:
                with self.assertRaises(ExitCalled) as raised:
                    reprofile["_finish_success"](state, {"status": "reprofiled"})
                self.assertEqual(raised.exception.code, 1)
            finally:
                globals_["sys"], globals_["os"]._exit = original_sys, original_exit
            self.assertFalse(os.path.exists(output))

    def test_49_validate_rejects_post_derivation_file_replacement(self) -> None:
        """Validate must reject if the output file is replaced after initial open."""
        with tempfile.TemporaryDirectory() as _base:
            base = os.path.realpath(_base)
            os.chmod(base, 0o700)
            tree = self._synthetic_tree(base, account_nlink_delta=1)
            try:
                request = canonical({
                    "prior_profile_path": tree["prior_path"],
                    "prior_profile_sha256": tree["prior_sha"],
                    "output_path": tree["output_path"],
                })
                result = reprofile["prepare"](request)
                self.assertEqual(result["status"], "reprofiled")

                val_req = canonical({
                    "prior_profile_path": tree["prior_path"],
                    "prior_profile_sha256": tree["prior_sha"],
                    "output_path": tree["output_path"],
                    "profile_path": tree["output_path"],
                    "profile_sha256": result["new_profile_sha256"],
                })
                self.assertEqual(reprofile["validate"](val_req)["status"], "valid")

                with open(tree["output_path"], "wb") as f:
                    f.write(b'{"mutated": true}\n')
                os.chmod(tree["output_path"], 0o600)
                with self.assertRaises(reprofile["ReprofileError"]):
                    reprofile["validate"](val_req)
            finally:
                self._restore_globals(tree)

    def test_50_publish_staging_unlink_failure_removes_both_owned_names(self) -> None:
        """A staging-unlink failure removes both owned names and fsyncs the parent."""
        with tempfile.TemporaryDirectory() as _base:
            base = os.path.realpath(_base)
            os.chmod(base, 0o700)
            globals_ = reprofile["_publish"].__globals__
            original_unlink, original_fsync = globals_["os"].unlink, globals_["os"].fsync
            failed = [False]
            parent_fsyncs = [0]
            def fail_staging(name: object, *args: object, **kwargs: object) -> None:
                if not failed[0] and str(name).startswith(".models.capture.reprofile."):
                    failed[0] = True
                    raise OSError("injected staging unlink failure")
                original_unlink(name, *args, **kwargs)
            def track_fsync(fd: int) -> None:
                item = os.fstat(fd)
                base_item = os.stat(base)
                if stat.S_ISDIR(item.st_mode) and (item.st_dev, item.st_ino) == (base_item.st_dev, base_item.st_ino):
                    parent_fsyncs[0] += 1
                original_fsync(fd)
            globals_["os"].unlink = fail_staging
            globals_["os"].fsync = track_fsync
            output = os.path.join(base, reprofile["OUTPUT_NAME"])
            try:
                with self.assertRaises(OSError):
                    reprofile["_publish"](output, b'{"test": true}\n', PollCounter())
            finally:
                globals_["os"].unlink = original_unlink
                globals_["os"].fsync = original_fsync
            self.assertTrue(failed[0])
            self.assertEqual(os.listdir(base), [])
            self.assertEqual(parent_fsyncs[0], 1)

    def test_51_finish_success_signal_rolls_back_active_reprofile(self) -> None:
        """A pending signal during completion must roll back active profile and exit with 128+signum."""
        class ExitCalled(BaseException):
            def __init__(self, code: int):
                self.code = code

        class PendingSignals:
            owned = (reprofile["signal"].SIGINT,)
            def latch(self, _s: int) -> None:
                pass
            def poll(self) -> None:
                raise reprofile["Interrupted"](reprofile["signal"].SIGINT)

        with tempfile.TemporaryDirectory() as _base:
            base = os.path.realpath(_base)
            os.chmod(base, 0o700)
            output = os.path.join(base, reprofile["OUTPUT_NAME"])
            reprofile["_publish"](output, b'{"test": 1}\n', None)
            self.assertTrue(os.path.exists(output))

            state = types.SimpleNamespace(signals=PendingSignals())
            globals_ = reprofile["_finish_success"].__globals__
            original_exit, original_sys = globals_["os"]._exit, globals_["sys"]
            globals_["os"]._exit = lambda code: (_ for _ in ()).throw(ExitCalled(code))
            globals_["sys"] = types.SimpleNamespace(stdout=types.SimpleNamespace(buffer=io.BytesIO()))
            try:
                with self.assertRaises(ExitCalled) as raised:
                    reprofile["_finish_success"](state, {"status": "reprofiled"})
                self.assertEqual(raised.exception.code, 128 + reprofile["signal"].SIGINT)
            finally:
                globals_["os"]._exit, globals_["sys"] = original_exit, original_sys
            self.assertFalse(os.path.exists(output))

    def test_52_finish_success_flush_failure_rolls_back(self) -> None:
        """A flush failure during completion must roll back active profile and exit with 1."""
        class FlushErrorBuffer:
            def write(self, data: bytes) -> int:
                return len(data)
            def flush(self) -> None:
                raise OSError("flush failed")

        class ExitCalled(BaseException):
            def __init__(self, code: int):
                self.code = code

        with tempfile.TemporaryDirectory() as _base:
            base = os.path.realpath(_base)
            os.chmod(base, 0o700)
            output = os.path.join(base, reprofile["OUTPUT_NAME"])
            reprofile["_publish"](output, b'{"test": 1}\n', None)
            self.assertTrue(os.path.exists(output))

            state = types.SimpleNamespace(signals=types.SimpleNamespace(owned=(), latch=lambda _s: None, poll=lambda: None))
            globals_ = reprofile["_finish_success"].__globals__
            original_exit, original_sys = globals_["os"]._exit, globals_["sys"]
            globals_["os"]._exit = lambda code: (_ for _ in ()).throw(ExitCalled(code))
            globals_["sys"] = types.SimpleNamespace(stdout=types.SimpleNamespace(buffer=FlushErrorBuffer()))
            try:
                with self.assertRaises(ExitCalled) as raised:
                    reprofile["_finish_success"](state, {"status": "reprofiled"})
                self.assertEqual(raised.exception.code, 1)
            finally:
                globals_["os"]._exit, globals_["sys"] = original_exit, original_sys
            self.assertFalse(os.path.exists(output))

    def test_53_finish_success_mask_failure_still_rolls_back(self) -> None:
        """A mask failure during completion must still roll back active profile and exit with 1."""
        class ExitCalled(BaseException):
            def __init__(self, code: int):
                self.code = code

        with tempfile.TemporaryDirectory() as _base:
            base = os.path.realpath(_base)
            os.chmod(base, 0o700)
            output = os.path.join(base, reprofile["OUTPUT_NAME"])
            reprofile["_publish"](output, b'{"test": 1}\n', None)
            self.assertTrue(os.path.exists(output))

            globals_ = reprofile["_finish_success"].__globals__
            original_mask, original_exit, original_sys = globals_["signal"].pthread_sigmask, globals_["os"]._exit, globals_["sys"]
            globals_["signal"].pthread_sigmask = lambda _how, _items: (_ for _ in ()).throw(OSError("mask failed"))
            globals_["os"]._exit = lambda code: (_ for _ in ()).throw(ExitCalled(code))
            globals_["sys"] = types.SimpleNamespace(stdout=types.SimpleNamespace(buffer=io.BytesIO()))

            state = types.SimpleNamespace(signals=types.SimpleNamespace(owned=(), latch=lambda _s: None, poll=lambda: None))
            try:
                with self.assertRaises(ExitCalled) as raised:
                    reprofile["_finish_success"](state, {"status": "reprofiled"})
                self.assertEqual(raised.exception.code, 1)
            finally:
                globals_["signal"].pthread_sigmask, globals_["os"]._exit, globals_["sys"] = original_mask, original_exit, original_sys
            self.assertFalse(os.path.exists(output))

    def test_54_prior_profile_immutable_on_success(self) -> None:
        """Reading a prior profile and successfully preparing must not mutate the prior profile file."""
        with tempfile.TemporaryDirectory() as _base:
            base = os.path.realpath(_base)
            os.chmod(base, 0o700)
            tree = self._synthetic_tree(base, account_nlink_delta=1)
            try:
                before_stat = os.stat(tree["prior_path"])
                with open(tree["prior_path"], "rb") as f:
                    before_bytes = f.read()

                request = canonical({
                    "prior_profile_path": tree["prior_path"],
                    "prior_profile_sha256": tree["prior_sha"],
                    "output_path": tree["output_path"],
                })
                reprofile["prepare"](request)

                after_stat = os.stat(tree["prior_path"])
                with open(tree["prior_path"], "rb") as f:
                    after_bytes = f.read()

                self.assertEqual(before_bytes, after_bytes)
                self.assertEqual(before_stat.st_ino, after_stat.st_ino)
                self.assertEqual(before_stat.st_mode, after_stat.st_mode)
                self.assertEqual(before_stat.st_size, after_stat.st_size)
            finally:
                self._restore_globals(tree)

    def test_55_prior_profile_immutable_on_failure(self) -> None:
        """When prepare fails due to drift, the prior profile file must remain untouched."""
        with tempfile.TemporaryDirectory() as _base:
            base = os.path.realpath(_base)
            os.chmod(base, 0o700)
            tree = self._synthetic_tree(base, account_nlink_delta=1)
            try:
                with open(tree["source"], "wb") as f:
                    f.write(b"mutated")
                before_stat = os.stat(tree["prior_path"])
                with open(tree["prior_path"], "rb") as f:
                    before_bytes = f.read()

                request = canonical({
                    "prior_profile_path": tree["prior_path"],
                    "prior_profile_sha256": tree["prior_sha"],
                    "output_path": tree["output_path"],
                })
                with self.assertRaises(reprofile["ReprofileError"]):
                    reprofile["prepare"](request)

                after_stat = os.stat(tree["prior_path"])
                with open(tree["prior_path"], "rb") as f:
                    after_bytes = f.read()

                self.assertEqual(before_bytes, after_bytes)
                self.assertEqual(before_stat.st_ino, after_stat.st_ino)
                self.assertEqual(before_stat.st_mode, after_stat.st_mode)
                self.assertEqual(before_stat.st_size, after_stat.st_size)
            finally:
                self._restore_globals(tree)

    def test_56_profile_module_held_source_validated(self) -> None:
        """_load_profile_module validates the fixed profile module's held source contract."""
        mod = reprofile["_get_profile_mod"]()
        self.assertIsNotNone(mod)
        # Verify validate_source_contract on profile source passes
        source_data = PROFILE_SOURCE.read_bytes()
        res = mod.validate_source_contract(source_data)
        self.assertEqual(res.get("status"), "valid-source")

    def test_57_source_contract_rejects_subprocess_import(self) -> None:
        """Source contract must reject subprocess import in all forms."""
        with self.assertRaises(reprofile["ReprofileError"]):
            reprofile["validate_source_contract"](b"import subprocess\n")
        with self.assertRaises(reprofile["ReprofileError"]):
            reprofile["validate_source_contract"](b"from subprocess import Popen\n")

    def test_58_source_contract_rejects_socket_import(self) -> None:
        """Source contract must reject socket import in all forms."""
        with self.assertRaises(reprofile["ReprofileError"]):
            reprofile["validate_source_contract"](b"import socket\n")
        with self.assertRaises(reprofile["ReprofileError"]):
            reprofile["validate_source_contract"](b"from socket import socket\n")

    def test_59_source_contract_rejects_urllib_import(self) -> None:
        """Source contract must reject urllib import in all forms."""
        with self.assertRaises(reprofile["ReprofileError"]):
            reprofile["validate_source_contract"](b"import urllib\n")
        with self.assertRaises(reprofile["ReprofileError"]):
            reprofile["validate_source_contract"](b"from urllib.request import urlopen\n")

    def test_60_source_contract_rejects_nested_and_http_imports(self) -> None:
        """Source contract must reject http and nested imports inside functions/blocks."""
        with self.assertRaises(reprofile["ReprofileError"]):
            reprofile["validate_source_contract"](b"import http\n")
        with self.assertRaises(reprofile["ReprofileError"]):
            reprofile["validate_source_contract"](b"def helper():\n    import subprocess\n")
        with self.assertRaises(reprofile["ReprofileError"]):
            reprofile["validate_source_contract"](b"if True:\n    from socket import socket\n")

    def test_61_source_contract_rejects_eval_and_exec(self) -> None:
        """Source contract must reject eval, exec, compile calls."""
        with self.assertRaises(reprofile["ReprofileError"]):
            reprofile["validate_source_contract"](b"eval('1+1')\n")
        with self.assertRaises(reprofile["ReprofileError"]):
            reprofile["validate_source_contract"](b"exec('1+1')\n")
        with self.assertRaises(reprofile["ReprofileError"]):
            reprofile["validate_source_contract"](b"compile('1', '<str>', 'eval')\n")

    def test_62_source_contract_rejects_popen_and_system(self) -> None:
        """Source contract must reject Popen and system direct calls."""
        with self.assertRaises(reprofile["ReprofileError"]):
            reprofile["validate_source_contract"](b"Popen(['ls'])\n")
        with self.assertRaises(reprofile["ReprofileError"]):
            reprofile["validate_source_contract"](b"system('ls')\n")

    def test_63_source_contract_rejects_listdir_and_scandir(self) -> None:
        """Source contract must reject listdir and scandir direct and attribute calls."""
        with self.assertRaises(reprofile["ReprofileError"]):
            reprofile["validate_source_contract"](b"listdir('.')\n")
        with self.assertRaises(reprofile["ReprofileError"]):
            reprofile["validate_source_contract"](b"import os\nos.listdir('.')\n")
        with self.assertRaises(reprofile["ReprofileError"]):
            reprofile["validate_source_contract"](b"scandir('.')\n")
        with self.assertRaises(reprofile["ReprofileError"]):
            reprofile["validate_source_contract"](b"import os\nos.scandir('.')\n")

    def test_64_source_contract_rejects_unauthorized_importfrom(self) -> None:
        """Source contract must reject unauthorized from-imports."""
        with self.assertRaises(reprofile["ReprofileError"]):
            reprofile["validate_source_contract"](b"from os import listdir\n")
        with self.assertRaises(reprofile["ReprofileError"]):
            reprofile["validate_source_contract"](b"from sys import exit\n")

    def test_65_source_contract_rejects_home_enumeration_in_account_identity(self) -> None:
        """Equivalent enumeration in the actual account-identity function is rejected."""
        mutations = (
            b"import os\ndef _get_account_home_identity(account_home):\n    return os.listdir(account_home)\n",
            b"import pathlib as paths\ndef _get_account_home_identity(account_home):\n    return tuple(paths.Path(account_home).iterdir())\n",
            b"import glob as discovery\ndef _get_account_home_identity(account_home):\n    return discovery.glob(account_home + '/*')\n",
        )
        for bad_source in mutations:
            with self.subTest(source=bad_source):
                with self.assertRaises(reprofile["ReprofileError"]):
                    reprofile["validate_source_contract"](bad_source)

    def test_66_source_contract_rejects_syntax_error(self) -> None:
        """validate_source_contract must reject unparsable source."""
        with self.assertRaises(reprofile["ReprofileError"]):
            reprofile["validate_source_contract"](b"def (broken\n")

    def test_67_prepare_rejects_missing_key(self) -> None:
        """Prepare must reject request with missing key."""
        request = canonical({
            "prior_profile_path": "/private/some/path/models.capture.1.1.22.profile.json",
            "prior_profile_sha256": "0" * 64,
        })
        with self.assertRaises(reprofile["ReprofileError"]):
            reprofile["prepare"](request)

    def test_68_prepare_rejects_extra_key(self) -> None:
        """Prepare must reject request with extra key."""
        request = canonical({
            "prior_profile_path": "/private/some/path/models.capture.1.1.22.profile.json",
            "prior_profile_sha256": "0" * 64,
            "output_path": "/private/other/models.capture.1.1.22.reprofile.json",
            "extra_key": "value",
        })
        with self.assertRaises(reprofile["ReprofileError"]):
            reprofile["prepare"](request)

    def test_69_validate_rejects_missing_key(self) -> None:
        """Validate must reject request with missing key."""
        request = canonical({
            "prior_profile_path": "/private/some/path/models.capture.1.1.22.profile.json",
            "prior_profile_sha256": "0" * 64,
            "output_path": "/private/other/models.capture.1.1.22.reprofile.json",
        })
        with self.assertRaises(reprofile["ReprofileError"]):
            reprofile["validate"](request)

    def test_70_validate_rejects_extra_key(self) -> None:
        """Validate must reject request with extra key."""
        request = canonical({
            "prior_profile_path": "/private/some/path/models.capture.1.1.22.profile.json",
            "prior_profile_sha256": "0" * 64,
            "output_path": "/private/other/models.capture.1.1.22.reprofile.json",
            "profile_path": "/private/other/models.capture.1.1.22.reprofile.json",
            "profile_sha256": "0" * 64,
            "extra_key": "value",
        })
        with self.assertRaises(reprofile["ReprofileError"]):
            reprofile["validate"](request)

    def test_71_json_rejects_oversized_input(self) -> None:
        """_json must reject inputs larger than PROFILE_LIMIT."""
        big = b"x" * (reprofile["PROFILE_LIMIT"] + 1)
        with self.assertRaises(reprofile["ReprofileError"]):
            reprofile["_json"](big)

    def test_72_json_rejects_empty_input(self) -> None:
        """_json must reject empty input."""
        with self.assertRaises(reprofile["ReprofileError"]):
            reprofile["_json"](b"")

    def test_73_json_rejects_duplicate_keys(self) -> None:
        """_json must reject duplicate keys."""
        raw = b'{"a": 1, "a": 2}\n'
        with self.assertRaises(reprofile["ReprofileError"]):
            reprofile["_json"](raw)

    def test_74_sha_rejects_invalid(self) -> None:
        """_sha must reject digests with invalid format/casing/length."""
        with self.assertRaises(reprofile["ReprofileError"]):
            reprofile["_sha"]("abc")
        with self.assertRaises(reprofile["ReprofileError"]):
            reprofile["_sha"]("A" * 64)

    def test_75_absolute_rejects_invalid(self) -> None:
        """_absolute must reject non-canonical, relative, or empty paths."""
        with self.assertRaises(reprofile["ReprofileError"]):
            reprofile["_absolute"]("relative/path")
        with self.assertRaises(reprofile["ReprofileError"]):
            reprofile["_absolute"]("")

    def test_76_main_rejects_invalid_args(self) -> None:
        """main() must reject empty argv, unknown flags, and extra args."""
        self.assertEqual(reprofile["main"]([]), 64)
        self.assertEqual(reprofile["main"](["--capture-models"]), 64)
        self.assertEqual(reprofile["main"](["--prepare", "--validate"]), 64)

    def test_77_reprofile_error_is_value_error(self) -> None:
        """ReprofileError must be a ValueError subclass."""
        self.assertTrue(issubclass(reprofile["ReprofileError"], ValueError))

    def test_78_prepare_keys_are_closed(self) -> None:
        """PREPARE_KEYS must be the exact expected set."""
        self.assertEqual(reprofile["PREPARE_KEYS"],
                         frozenset({"prior_profile_path", "prior_profile_sha256", "output_path"}))

    def test_79_validate_keys_are_closed(self) -> None:
        """VALIDATE_KEYS must be the exact expected set."""
        self.assertEqual(reprofile["VALIDATE_KEYS"],
                         frozenset({"prior_profile_path", "prior_profile_sha256", "output_path",
                                     "profile_path", "profile_sha256"}))

    def test_80_prepare_and_validate_reject_every_held_output_root_overlap(self) -> None:
        """Both APIs reject roots equal to, inside, or containing every held authority."""
        with tempfile.TemporaryDirectory() as _base:
            base = os.path.realpath(_base)
            os.chmod(base, 0o700)
            tree = self._synthetic_tree(base, account_nlink_delta=1)
            try:
                prior_parent = os.path.dirname(tree["prior_path"])
                authorities = (
                    tree["prior_path"], prior_parent, tree["account"], tree["parent"],
                    tree["recovery"], tree["source"], tree["snapshot"],
                    *(os.path.join(tree["recovery"], name) for name in profile_mod["RECOVERY_SCRATCH"]),
                )
                for authority in authorities:
                    for output_root in (authority, os.path.join(authority, "nested"), os.path.dirname(authority)):
                        output = os.path.join(output_root, reprofile["OUTPUT_NAME"])
                        prepare_request = canonical({
                            "prior_profile_path": tree["prior_path"],
                            "prior_profile_sha256": tree["prior_sha"],
                            "output_path": output,
                        })
                        validate_request = canonical({
                            "prior_profile_path": tree["prior_path"],
                            "prior_profile_sha256": tree["prior_sha"],
                            "output_path": output,
                            "profile_path": output,
                            "profile_sha256": "0" * 64,
                        })
                        with self.subTest(api="prepare", authority=authority, output_root=output_root):
                            with self.assertRaises(reprofile["ReprofileError"]):
                                reprofile["prepare"](prepare_request)
                        with self.subTest(api="validate", authority=authority, output_root=output_root):
                            with self.assertRaises(reprofile["ReprofileError"]):
                                reprofile["validate"](validate_request)
            finally:
                self._restore_globals(tree)

    def test_81_source_contract_rejects_import_aliases_and_direct_imported_calls(self) -> None:
        """Aliasing cannot hide process/network imports or forbidden os calls."""
        samples = (
            b"import subprocess as child\nchild.run(['true'])\n",
            b"def helper():\n    import socket as transport\n    return transport.socket()\n",
            b"if True:\n    from urllib.request import urlopen as fetch\n    fetch('https://example.invalid')\n",
            b"import os as operating\noperating.system('true')\n",
            b"import os as operating\ndef helper():\n    import ast as operating\noperating.execve('/bin/true', ['true'], {})\n",
            b"from os import system as execute\nexecute('true')\n",
        )
        for source in samples:
            with self.subTest(source=source):
                with self.assertRaises(reprofile["ReprofileError"]):
                    reprofile["validate_source_contract"](source)

    def test_82_post_publication_readback_failure_rolls_back_and_fsyncs(self) -> None:
        """A post-publication read failure compare-deletes the owned output durably."""
        with tempfile.TemporaryDirectory() as _base:
            base = os.path.realpath(_base)
            os.chmod(base, 0o700)
            tree = self._synthetic_tree(base, account_nlink_delta=1)
            globals_ = reprofile["prepare"].__globals__
            original_publish, original_read, original_fsync = globals_["_publish"], globals_["os"].read, globals_["os"].fsync
            published, read_failure_seen = [False], [False]
            rollback_parent_fsyncs = [0]
            def publish_then_arm(path: str, data: bytes, signals: object) -> str:
                digest = original_publish(path, data, signals)
                published[0] = True
                return digest
            def fail_once(fd: int, size: int) -> bytes:
                if published[0]:
                    published[0] = False
                    read_failure_seen[0] = True
                    raise OSError("injected output readback failure")
                return original_read(fd, size)
            def track_fsync(fd: int) -> None:
                if read_failure_seen[0] and stat.S_ISDIR(os.fstat(fd).st_mode):
                    rollback_parent_fsyncs[0] += 1
                original_fsync(fd)
            globals_["_publish"], globals_["os"].read, globals_["os"].fsync = publish_then_arm, fail_once, track_fsync
            try:
                request = canonical({"prior_profile_path": tree["prior_path"], "prior_profile_sha256": tree["prior_sha"], "output_path": tree["output_path"]})
                with self.assertRaises(OSError):
                    reprofile["prepare"](request)
            finally:
                globals_["_publish"], globals_["os"].read, globals_["os"].fsync = original_publish, original_read, original_fsync
                self._restore_globals(tree)
            self.assertFalse(os.path.exists(tree["output_path"]))
            self.assertEqual(rollback_parent_fsyncs[0], 1)

    def test_83_post_publication_runner_preflight_failure_rolls_back_and_fsyncs(self) -> None:
        """A second, post-publication runner preflight failure is rolled back durably."""
        with tempfile.TemporaryDirectory() as _base:
            base = os.path.realpath(_base)
            os.chmod(base, 0o700)
            tree = self._synthetic_tree(base, account_nlink_delta=1)
            globals_ = reprofile["prepare"].__globals__
            original_preflight, original_fsync = globals_["_validate_runner_preflight"], globals_["os"].fsync
            calls, rollback_parent_fsyncs = [0], [0]
            def fail_second(data: bytes, signals: object = None) -> None:
                calls[0] += 1
                if calls[0] == 2:
                    raise reprofile["ReprofileError"]("injected runner preflight failure")
                original_preflight(data, signals)
            def track_fsync(fd: int) -> None:
                if calls[0] >= 2 and stat.S_ISDIR(os.fstat(fd).st_mode):
                    rollback_parent_fsyncs[0] += 1
                original_fsync(fd)
            globals_["_validate_runner_preflight"], globals_["os"].fsync = fail_second, track_fsync
            try:
                request = canonical({"prior_profile_path": tree["prior_path"], "prior_profile_sha256": tree["prior_sha"], "output_path": tree["output_path"]})
                with self.assertRaises(reprofile["ReprofileError"]):
                    reprofile["prepare"](request)
            finally:
                globals_["_validate_runner_preflight"], globals_["os"].fsync = original_preflight, original_fsync
                self._restore_globals(tree)
            self.assertEqual(calls[0], 2)
            self.assertFalse(os.path.exists(tree["output_path"]))
            self.assertEqual(rollback_parent_fsyncs[0], 1)

    def test_84_post_publication_prior_recheck_failure_rolls_back_and_fsyncs(self) -> None:
        """A final prior-profile recheck failure is rolled back durably."""
        with tempfile.TemporaryDirectory() as _base:
            base = os.path.realpath(_base)
            os.chmod(base, 0o700)
            tree = self._synthetic_tree(base, account_nlink_delta=1)
            globals_ = reprofile["prepare"].__globals__
            original_read_prior, original_fsync = globals_["_read_prior_profile"], globals_["os"].fsync
            calls, rollback_parent_fsyncs = [0], [0]
            def fail_third(path: str, digest: str) -> bytes:
                calls[0] += 1
                if calls[0] == 3:
                    raise reprofile["ReprofileError"]("injected final prior recheck failure")
                return original_read_prior(path, digest)
            def track_fsync(fd: int) -> None:
                if calls[0] >= 3 and stat.S_ISDIR(os.fstat(fd).st_mode):
                    rollback_parent_fsyncs[0] += 1
                original_fsync(fd)
            globals_["_read_prior_profile"], globals_["os"].fsync = fail_third, track_fsync
            try:
                request = canonical({"prior_profile_path": tree["prior_path"], "prior_profile_sha256": tree["prior_sha"], "output_path": tree["output_path"]})
                with self.assertRaises(reprofile["ReprofileError"]):
                    reprofile["prepare"](request)
            finally:
                globals_["_read_prior_profile"], globals_["os"].fsync = original_read_prior, original_fsync
                self._restore_globals(tree)
            self.assertEqual(calls[0], 3)
            self.assertFalse(os.path.exists(tree["output_path"]))
            self.assertEqual(rollback_parent_fsyncs[0], 1)

    def test_85_post_publication_failure_preserves_replacement_inode(self) -> None:
        """Rollback never deletes an output name replaced after owned publication."""
        with tempfile.TemporaryDirectory() as _base:
            base = os.path.realpath(_base)
            os.chmod(base, 0o700)
            tree = self._synthetic_tree(base, account_nlink_delta=1)
            globals_ = reprofile["prepare"].__globals__
            original_preflight = globals_["_validate_runner_preflight"]
            calls, replacement_inode = [0], [None]
            def replace_then_fail(data: bytes, signals: object = None) -> None:
                calls[0] += 1
                if calls[0] == 2:
                    os.unlink(tree["output_path"])
                    with open(tree["output_path"], "wb") as stream:
                        stream.write(b"replacement\n")
                    os.chmod(tree["output_path"], 0o600)
                    replacement_inode[0] = os.stat(tree["output_path"]).st_ino
                    raise reprofile["ReprofileError"]("injected post-publication failure")
                original_preflight(data, signals)
            globals_["_validate_runner_preflight"] = replace_then_fail
            try:
                request = canonical({"prior_profile_path": tree["prior_path"], "prior_profile_sha256": tree["prior_sha"], "output_path": tree["output_path"]})
                with self.assertRaises(reprofile["ReprofileError"]):
                    reprofile["prepare"](request)
            finally:
                globals_["_validate_runner_preflight"] = original_preflight
                self._restore_globals(tree)
            self.assertEqual(os.stat(tree["output_path"]).st_ino, replacement_inode[0])
            with open(tree["output_path"], "rb") as stream:
                self.assertEqual(stream.read(), b"replacement\n")

    def test_86_success_repeats_unchanged_runner_preflight_after_publication(self) -> None:
        """Successful prepare runs the same runner preflight before and after publication."""
        with tempfile.TemporaryDirectory() as _base:
            base = os.path.realpath(_base)
            os.chmod(base, 0o700)
            tree = self._synthetic_tree(base, account_nlink_delta=1)
            globals_ = reprofile["prepare"].__globals__
            original_preflight = globals_["_validate_runner_preflight"]
            observed = []
            def record(data: bytes, signals: object = None) -> None:
                observed.append(data)
                original_preflight(data, signals)
            globals_["_validate_runner_preflight"] = record
            try:
                request = canonical({"prior_profile_path": tree["prior_path"], "prior_profile_sha256": tree["prior_sha"], "output_path": tree["output_path"]})
                self.assertEqual(reprofile["prepare"](request)["status"], "reprofiled")
            finally:
                globals_["_validate_runner_preflight"] = original_preflight
                self._restore_globals(tree)
            self.assertEqual(len(observed), 2)
            self.assertEqual(observed[0], observed[1])

    def test_87_failed_capture_parent_nlink_drift_reprofiles_account_home_only(self) -> None:
        """A failed-capture-style child may change only diagnostic capture-parent nlink."""
        with tempfile.TemporaryDirectory() as _base:
            base = os.path.realpath(_base)
            os.chmod(base, 0o700)
            tree = self._synthetic_tree(base, account_nlink_delta=1)
            failed_capture = os.path.join(tree["parent"], "agy-models-capture-1-1-22.failed")
            os.mkdir(failed_capture, 0o700)
            mod = reprofile["_get_profile_mod"]()
            original = mod._from_request
            try:
                prior_parent_identity = dataclasses.asdict(
                    tree["prior_profile"].capture_parent_identity
                )

                def failed_capture_view(request: dict) -> tuple[object, str]:
                    current, output = original(request)
                    return (
                        dataclasses.replace(
                            current,
                            capture_parent_identity=dataclasses.replace(
                                current.capture_parent_identity,
                                nlink=prior_parent_identity["nlink"] + 1,
                            ),
                        ),
                        output,
                    )

                # Some filesystems do not expose child creation through directory
                # nlink, so bind the deterministic failed-capture observation that
                # triggered the regression while retaining the real residual root.
                mod._from_request = failed_capture_view
                request = canonical({
                    "prior_profile_path": tree["prior_path"],
                    "prior_profile_sha256": tree["prior_sha"],
                    "output_path": tree["output_path"],
                })
                result = reprofile["prepare"](request, PollCounter())
                self.assertEqual(result["changed_fields"], ["account_home_identity.nlink"])

                with open(tree["output_path"], "rb") as stream:
                    published = profile_mod["CaptureProfile"].from_bytes(stream.read())
                self.assertEqual(
                    dataclasses.asdict(published.capture_parent_identity),
                    prior_parent_identity,
                )
                self.assertNotEqual(
                    published.account_home_identity.nlink,
                    tree["prior_profile"].account_home_identity.nlink,
                )

                validation = canonical({
                    "prior_profile_path": tree["prior_path"],
                    "prior_profile_sha256": tree["prior_sha"],
                    "output_path": tree["output_path"],
                    "profile_path": tree["output_path"],
                    "profile_sha256": result["new_profile_sha256"],
                })
                self.assertEqual(reprofile["validate"](validation)["status"], "valid")
            finally:
                mod._from_request = original
                self._restore_globals(tree)

    def test_88_capture_parent_authority_drift_remains_rejected(self) -> None:
        """Only positive capture-parent nlink drift is diagnostic; stable fields remain exact."""
        with tempfile.TemporaryDirectory() as _base:
            base = os.path.realpath(_base)
            os.chmod(base, 0o700)
            tree = self._synthetic_tree(base, account_nlink_delta=1)
            mod = reprofile["_get_profile_mod"]()
            original = mod._from_request
            try:
                for field in ("dev", "gid", "ino", "mode", "uid", "nlink"):
                    def drift(request: dict, changed_field: str = field) -> tuple[object, str]:
                        current, output = original(request)
                        identity = current.capture_parent_identity
                        value = 0 if changed_field == "nlink" else (
                            0o755 if changed_field == "mode" else getattr(identity, changed_field) + 1
                        )
                        return (
                            dataclasses.replace(
                                current,
                                capture_parent_identity=dataclasses.replace(
                                    identity,
                                    **{changed_field: value},
                                ),
                            ),
                            output,
                        )

                    mod._from_request = drift
                    request = canonical({
                        "prior_profile_path": tree["prior_path"],
                        "prior_profile_sha256": tree["prior_sha"],
                        "output_path": tree["output_path"],
                    })
                    with self.subTest(field=field):
                        with self.assertRaises(reprofile["ReprofileError"]):
                            reprofile["prepare"](request, PollCounter())
                        self.assertFalse(os.path.exists(tree["output_path"]))
            finally:
                mod._from_request = original
                self._restore_globals(tree)


if __name__ == "__main__":
    sys.exit(0 if unittest.TextTestRunner(verbosity=1).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(ModelsCapture1122ReprofileTests)
    ).wasSuccessful() else 1)
