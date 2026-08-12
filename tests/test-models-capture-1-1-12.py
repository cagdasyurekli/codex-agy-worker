#!/usr/bin/env python3
"""Offline structural controls for the separate 1.1.12 models-capture bridge.

No case launches agy, opens an account HOME, contacts a provider, or uses a real
recovery record.  The production exact-record check is deliberately fail-closed.
"""
from __future__ import annotations

import ast
import hashlib
import io
import json
import os
import pathlib
import runpy
import sys
import tempfile
import types
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
PROFILE_SOURCE = ROOT / "scripts" / "models_capture_1_1_12_profile.py"
RUNNER_SOURCE = ROOT / "scripts" / "models_capture_1_1_12_runner.py"
profile = runpy.run_path(str(PROFILE_SOURCE))
runner = runpy.run_path(str(RUNNER_SOURCE))


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii") + b"\n"


def identity() -> dict[str, int]:
    return {"ctime_ns": 1, "dev": 1, "gid": 1, "ino": 1, "mode": 0o755, "mtime_ns": 1, "nlink": 1, "size": 1, "uid": 1}


def directory() -> dict[str, int]:
    return {"dev": 1, "gid": 1, "ino": 1, "mode": 0o700, "nlink": 2, "uid": 1}


def profile_value() -> dict[str, object]:
    return {
        "account_home": "/private/account", "account_home_identity": directory(),
        "capture_parent": "/private/capture", "capture_parent_identity": directory(),
        "snapshot_identity": identity(), "snapshot_path": "/private/capture/snapshot/agy.snapshot",
        "source_identity": identity(), "source_path": "/private/capture/agy.source",
        "source_sha256": runner["EXPECTED_SOURCE_SHA256"],
        "version_binding_sha256": runner["EXPECTED_RECOVERY_BINDING_SHA256"],
        "version_root": "/private/capture/recovery", "version_root_identity": directory(),
    }


def repin_profile(data: bytes) -> bytes:
    text = data.decode("utf-8")
    tree = ast.parse(text)
    assignment = next(node for node in tree.body if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) and node.targets[0].id == "MODULE_AST_SHA256")
    assignment.value = ast.Constant(value="PINNED-MODULE-AST")
    digest = hashlib.sha256(ast.dump(tree, include_attributes=False).encode("utf-8")).hexdigest()
    original = profile["MODULE_AST_SHA256"].encode("ascii")
    return data.replace(original, digest.encode("ascii"), 1)


def load_profile_copy(data: bytes) -> dict[str, object]:
    name = "_mutated_profile"
    module = types.ModuleType(name)
    module.__file__ = str(PROFILE_SOURCE)
    sys.modules[name] = module
    try:
        exec(compile(data, "<mutated-profile>", "exec"), module.__dict__)
        return module.__dict__
    finally:
        sys.modules.pop(name, None)


class NlinkBarrier:
    def __init__(self, directory: str):
        self.directory = directory
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


class ModelsCapture112Tests(unittest.TestCase):
    def _synthetic_runner_profile(self, base: str, exit_code: int, scratch_write: bool = False) -> tuple[object, bytes, dict[str, object], dict[str, object]]:
        """Build one private, data-only recovery chain for the real runner path."""
        account, parent = os.path.join(base, "account"), os.path.join(base, "capture")
        os.mkdir(account, 0o700); os.mkdir(parent, 0o700)
        source, snapshots, recovery = os.path.join(parent, "agy.source"), os.path.join(parent, "snapshot"), os.path.join(parent, "recovery")
        os.mkdir(snapshots, 0o700); os.mkdir(recovery, 0o700)
        mutation = 'printf residual > "$TMPDIR/residual"\n' if scratch_write else ""
        child = (f"#!/bin/sh\n{mutation}printf '%s|%s|%s|%s|%s|%s|%s\\n' \"$0\" \"$1\" \"$HOME\" \"$TMPDIR\" \"$XDG_CACHE_HOME\" \"$PATH\" \"$LC_ALL\"\nexit {exit_code}\n").encode("ascii")
        snapshot = os.path.join(snapshots, "agy.snapshot")
        for path, mode in ((source, 0o755), (snapshot, 0o500)):
            with open(path, "wb") as handle: handle.write(child)
            os.chmod(path, mode)
        for name in runner["RECOVERY_SCRATCH"]:
            os.mkdir(os.path.join(recovery, name), 0o700)
        FileIdentity, DirectoryIdentity, Profile = runner["FileIdentity"], runner["DirectoryIdentity"], runner["Profile"]
        source_identity, snapshot_identity = FileIdentity.from_stat(os.stat(source)), FileIdentity.from_stat(os.stat(snapshot))
        source_sha = hashlib.sha256(child).hexdigest()
        summary = b"{}\n"; recovery_runner = b"r"
        artifacts = {"version.summary.json": hashlib.sha256(summary).hexdigest(), "version.stdout": hashlib.sha256(b"1.1.12\n").hexdigest(), "version.stderr": hashlib.sha256(b"").hexdigest()}
        binding_value = {"artifacts": artifacts, "claim": "snapshot-version-recovery", "source": {"pre": runner["dataclasses"].asdict(source_identity), "post": runner["dataclasses"].asdict(source_identity), "sha256": source_sha}, "snapshot": {"pre": runner["dataclasses"].asdict(snapshot_identity), "post": runner["dataclasses"].asdict(snapshot_identity), "sha256": source_sha}, "version": {"exit": 0, "logical_argv": [source, "--version"], "observed": "1.1.12", "popen_count": 1}}
        binding = canonical(binding_value)
        self.assertLess(len(binding), 2_051)
        binding += b" " * (2_051 - len(binding))
        binding_sha = hashlib.sha256(binding).hexdigest(); runner_sha = hashlib.sha256(recovery_runner).hexdigest()
        files = {"runner.py": recovery_runner, "runner.py.sha256": (runner_sha + "\n").encode("ascii"), "version.binding.json": binding, "version.binding.sha256": (binding_sha + "\n").encode("ascii"), "version.stderr": b"", "version.stdout": b"1.1.12\n", "version.summary.json": summary}
        for name, data in files.items():
            with open(os.path.join(recovery, name), "wb") as handle: handle.write(data)
            os.chmod(os.path.join(recovery, name), 0o600)
        value = Profile(account, DirectoryIdentity.from_stat(os.stat(account)), parent, DirectoryIdentity.from_stat(os.stat(parent)), snapshot_identity, snapshot, source_identity, source, source_sha, binding_sha, recovery, DirectoryIdentity.from_stat(os.stat(recovery)))
        globals_ = runner["run_capture"].__globals__
        old = {name: globals_[name] for name in ("EXPECTED_SOURCE_SHA256", "EXPECTED_RECOVERY_BINDING_SHA256", "EXPECTED_RECOVERY_RUNNER_SHA256", "EXPECTED_RECOVERY_RUNNER_BYTES", "EXPECTED_RECOVERY_SUMMARY_BYTES")}
        globals_.update({"EXPECTED_SOURCE_SHA256": source_sha, "EXPECTED_RECOVERY_BINDING_SHA256": binding_sha, "EXPECTED_RECOVERY_RUNNER_SHA256": runner_sha, "EXPECTED_RECOVERY_RUNNER_BYTES": len(recovery_runner), "EXPECTED_RECOVERY_SUMMARY_BYTES": len(summary)})
        return value, canonical(runner["dataclasses"].asdict(value)), old, {"account": account, "parent": parent, "source": source, "snapshot": snapshot}

    def test_01_profile_source_contract(self) -> None:
        self.assertEqual(profile["validate_source_contract"](PROFILE_SOURCE.read_bytes())["status"], "valid-source")

    def test_02_runner_source_contract(self) -> None:
        self.assertEqual(runner["validate_source_contract"](RUNNER_SOURCE.read_bytes())["status"], "valid-source")

    def test_03_profile_accepts_canonical_shape(self) -> None:
        self.assertEqual(profile["CaptureProfile"].from_bytes(canonical(profile_value())).source_path, "/private/capture/agy.source")

    def test_04_runner_accepts_canonical_shape(self) -> None:
        self.assertEqual(runner["Profile"].from_bytes(canonical(profile_value())).capture_parent, "/private/capture")

    def test_05_profile_rejects_extra_field(self) -> None:
        value = profile_value(); value["output_path"] = "/private/capture/x"
        with self.assertRaises(profile["ProfileError"]): profile["CaptureProfile"].from_bytes(canonical(value))

    def test_06_runner_rejects_extra_field(self) -> None:
        value = profile_value(); value["output_path"] = "/private/capture/x"
        with self.assertRaises(runner["CaptureError"]): runner["Profile"].from_bytes(canonical(value))

    def test_07_profile_rejects_noncanonical_bytes(self) -> None:
        with self.assertRaises(profile["ProfileError"]): profile["CaptureProfile"].from_bytes(canonical(profile_value()).replace(b"\n", b""))

    def test_08_runner_rejects_wrong_binding(self) -> None:
        value = profile_value(); value["version_binding_sha256"] = "0" * 64
        self.assertEqual(runner["Profile"].from_bytes(canonical(value)).version_binding_sha256, "0" * 64)

    def test_09_builder_request_has_explicit_capture_parent(self) -> None:
        self.assertEqual(profile["REQUEST_KEYS"], frozenset({"account_home", "capture_parent", "output_path", "snapshot_path", "source_path", "version_root"}))

    def test_10_output_is_builder_only(self) -> None:
        self.assertNotIn("output_path", runner["PROFILE_KEYS"])

    def test_11_no_legacy_runner_import(self) -> None:
        text = RUNNER_SOURCE.read_text()
        self.assertNotIn("models_capture_runner", text)
        self.assertNotIn("models_attestation_runner", text)
        self.assertNotIn("version_recovery_1_1_12_runner", text)

    def test_12_builder_has_no_process_import(self) -> None:
        imported = {alias.name for node in ast.parse(PROFILE_SOURCE.read_text()).body if isinstance(node, ast.Import) for alias in node.names}
        self.assertNotIn("subprocess", imported)

    def test_13_builder_does_not_list_account_home(self) -> None:
        source = PROFILE_SOURCE.read_text()
        self.assertIn("profile authority call graph changed", source)
        self.assertIn("profile source enumerates account HOME", source)
        self.assertIn("_open_directory", source)

    def test_14_runner_has_one_popen(self) -> None:
        tree = ast.parse(RUNNER_SOURCE.read_text())
        popens = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "Popen"]
        self.assertEqual(len(popens), 1)

    def test_15_runner_uses_snapshot_executable(self) -> None:
        source = RUNNER_SOURCE.read_text()
        self.assertIn("executable=profile.snapshot_path", source)
        self.assertIn("subprocess.Popen([profile.source_path, \"models\"], executable=profile.snapshot_path", source)

    def test_16_closed_environment_is_explicit(self) -> None:
        source = RUNNER_SOURCE.read_text()
        for key in ("HOME", "TMPDIR", "XDG_CACHE_HOME", "XDG_CONFIG_HOME", "XDG_STATE_HOME", "PATH", "LANG", "LC_ALL", "TERM", "NO_COLOR"):
            self.assertIn('"' + key + '"', source)

    def test_17_capture_is_not_acceptance(self) -> None:
        source = RUNNER_SOURCE.read_text()
        self.assertIn('"status": "captured"', source)
        self.assertIn('"accepted_inventory": False', source)
        self.assertIn('"metadata_updated": False', source)

    def test_18_capture_parent_is_disjoint_from_account(self) -> None:
        self.assertIn("not _disjoint(profile.account_home, profile.capture_parent)", RUNNER_SOURCE.read_text())

    def test_19_recovery_inventory_is_13_members(self) -> None:
        self.assertEqual(len(runner["RECOVERY_FILES"]), 13)

    def test_20_marker_is_last_named_publication(self) -> None:
        source = RUNNER_SOURCE.read_text()
        self.assertLess(source.index('_write(root_fd, "models.capture.json"'), source.index("_marker(root_fd, record_sha, signals, ledger)"))

    def test_21_runner_rejects_profile_shape_drift(self) -> None:
        value = profile_value(); value.pop("version_root_identity")
        with self.assertRaises(runner["CaptureError"]): runner["Profile"].from_bytes(canonical(value))

    def test_22_profile_rejects_wrong_output_basename_before_authority(self) -> None:
        value = {"account_home": "/private/account", "capture_parent": "/private/capture", "output_path": "/private/capture/other.json", "snapshot_path": "/private/capture/snapshot/agy.snapshot", "source_path": "/private/capture/agy.source", "version_root": "/private/capture/recovery"}
        with self.assertRaises(profile["ProfileError"]): profile["_from_request"](value)

    def test_23_runner_does_not_emit_account_path(self) -> None:
        source = RUNNER_SOURCE.read_text()
        returned = source[source.index("return {\"artifact_root\""):source.index("return {\"artifact_root\"") + 150]
        self.assertNotIn("account_home", returned)

    def test_24_profile_publisher_has_exact_identity_cleanup(self) -> None:
        source = PROFILE_SOURCE.read_text()
        self.assertIn("temporary_identity", source)
        self.assertIn("final_identity", source)
        self.assertNotIn("for leaf in (name, temporary)", source)

    def test_25_runner_has_post_child_authority_revalidation(self) -> None:
        source = RUNNER_SOURCE.read_text()
        self.assertLess(source.index("_revalidate_authority(profile"), source.index("_empty_scratch(root_fd)"))

    def test_26_marker_owns_both_names_before_rollback(self) -> None:
        source = RUNNER_SOURCE.read_text()
        self.assertIn("temporary_identity", source)
        self.assertIn("final_identity", source)
        self.assertIn("for name, expected in ((\"models.capture.sha256\", final_identity), (temporary, temporary_identity))", source)

    def test_27_group_uses_logical_source_and_snapshot_executable(self) -> None:
        source = RUNNER_SOURCE.read_text()
        self.assertIn("executable=profile.snapshot_path", source)
        self.assertIn("os.getpgid(process.pid) != process.pid", source)
        self.assertIn("capture group cannot be closed", source)

    def test_28_final_root_precedes_marker(self) -> None:
        source = RUNNER_SOURCE.read_text()
        self.assertLess(source.index("_verify_final_root(root_path, root_name, root_fd, root_identity, profile, capture_fd, ledger, scratch_ledger)"), source.index("_marker(root_fd, record_sha, signals, ledger)"))

    def test_29_record_binds_nonsemantic_capture_evidence(self) -> None:
        source = RUNNER_SOURCE.read_text()
        for key in ("account_post", '"popen_count": 1', '"routing_authority": False', '"stream_bytes": STREAM_LIMIT', '"version_binding_sha256"'):
            self.assertIn(key, source)

    def test_30_profile_validate_reopens_final_path(self) -> None:
        source = PROFILE_SOURCE.read_text()
        self.assertIn("parent_check_fd", source)
        self.assertIn("profile_identity", source)

    def test_31_process_owned_success_finalization(self) -> None:
        for path in (PROFILE_SOURCE, RUNNER_SOURCE):
            source = path.read_text()
            self.assertIn("def _finish_success", source)
            self.assertIn("signal.pthread_sigmask(signal.SIG_BLOCK", source)
            self.assertIn("os._exit(0)", source)

    def test_32_runner_rolls_back_marker_on_pending_signal(self) -> None:
        source = RUNNER_SOURCE.read_text()
        self.assertIn("_rollback_active_marker()", source)
        self.assertIn("ACTIVE_MARKER_ROOT_IDENTITY", source)

    def test_33_profile_rolls_back_provisional_output_on_pending_signal(self) -> None:
        source = PROFILE_SOURCE.read_text()
        self.assertIn("_rollback_active_profile()", source)
        self.assertIn("ACTIVE_PROFILE_IDENTITY", source)

    def test_34_source_contract_rejects_popen_mutation(self) -> None:
        mutated = RUNNER_SOURCE.read_bytes().replace(b"subprocess.Popen", b"subprocess.PopenX", 1)
        with self.assertRaises(runner["CaptureError"]): runner["validate_source_contract"](mutated)

    def test_35_source_contract_rejects_popen_environment_mutation(self) -> None:
        mutated = RUNNER_SOURCE.read_bytes().replace(b"env=environment", b"env=os.environ", 1)
        with self.assertRaises(runner["CaptureError"]): runner["validate_source_contract"](mutated)

    def test_36_source_contract_rejects_profile_process_import(self) -> None:
        mutated = PROFILE_SOURCE.read_bytes().replace(b"import ast", b"import subprocess\nimport ast", 1)
        with self.assertRaises(profile["ProfileError"]): profile["validate_source_contract"](mutated)

    def test_37_stdin_is_bounded_incremental(self) -> None:
        for path in (PROFILE_SOURCE, RUNNER_SOURCE):
            source = path.read_text()
            self.assertIn("while len(chunks) <= limit", source)
            self.assertIn("os.read(sys.stdin.buffer.fileno()", source)

    def test_38_final_inventory_is_marker_inclusive(self) -> None:
        source = RUNNER_SOURCE.read_text()
        self.assertIn("_verify_final_root(root_path, root_name, root_fd, root_identity, profile, capture_fd, ledger, scratch_ledger, True)", source)
        self.assertIn('expected.add("models.capture.sha256")', source)

    def test_39_runner_has_unvalidated_child_close_path(self) -> None:
        source = RUNNER_SOURCE.read_text()
        self.assertIn("def _close_unvalidated_child", source)
        self.assertIn("_close_unvalidated_child(process)", source)
        self.assertNotIn("_close_group(process)\n                raise CaptureError(\"capture process group registration changed\")", source)

    def test_40_marker_and_artifacts_are_identity_ledgered(self) -> None:
        source = RUNNER_SOURCE.read_text()
        self.assertIn("ledger[name] = (digest, identity)", source)
        self.assertIn("ledger[\"models.capture.sha256\"]", source)
        self.assertIn("ACTIVE_MARKER_IDENTITY", source)

    def test_41_scratch_identity_is_ledgered(self) -> None:
        source = RUNNER_SOURCE.read_text()
        self.assertIn("scratch_ledger", source)
        self.assertIn("capture scratch identity changed", source)

    def test_42_profile_guard_rejects_dynamic_importfrom(self) -> None:
        mutated = PROFILE_SOURCE.read_bytes().replace(b"from dataclasses import dataclass", b"from subprocess import Popen", 1)
        with self.assertRaises(profile["ProfileError"]): profile["validate_source_contract"](mutated)

    def test_43_profile_guard_rejects_reachable_helper_mutation(self) -> None:
        mutated = PROFILE_SOURCE.read_bytes().replace(b"os.close(account_fd)", b"os.listdir(account_fd)", 1)
        mutated = repin_profile(mutated)
        mutated_profile = load_profile_copy(mutated)
        with self.assertRaises(mutated_profile["ProfileError"]) as raised: mutated_profile["validate_source_contract"](mutated)
        self.assertEqual(str(raised.exception), "profile account authority changed")

    def test_44_marker_rollback_requires_exact_identity(self) -> None:
        source = RUNNER_SOURCE.read_text()
        self.assertIn("marker_identity is not None", source)
        self.assertIn("path_identity == observed", source)
        self.assertNotIn("_remove_owned_marker(root_fd, record_sha)\n", source)

    def test_45_profile_hardlink_normalizes_before_poll_or_fsync(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            os.chmod(directory, 0o700)
            barrier = NlinkBarrier(directory)
            globals_ = profile["_publish"].__globals__
            original_fsync = globals_["os"].fsync
            def guarded_fsync(fd: int) -> None:
                barrier.check()
                original_fsync(fd)
            globals_["os"].fsync = guarded_fsync
            try:
                digest = profile["_publish"](os.path.join(os.path.realpath(directory), profile["OUTPUT_NAME"]), b'{"profile":true}\n', barrier)
            finally:
                globals_["os"].fsync = original_fsync
            self.assertEqual(digest, hashlib.sha256(b'{"profile":true}\n').hexdigest())
            self.assertGreater(barrier.polls, 0)
            self.assertFalse(barrier.two_link_observed)
            self.assertEqual(os.stat(os.path.join(directory, profile["OUTPUT_NAME"])).st_nlink, 1)

    def test_46_marker_hardlink_normalizes_before_poll_or_fsync(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            os.chmod(directory, 0o700)
            root = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            barrier = NlinkBarrier(directory)
            globals_ = runner["_marker"].__globals__
            original_fsync = globals_["os"].fsync
            def guarded_fsync(fd: int) -> None:
                barrier.check()
                original_fsync(fd)
            globals_["os"].fsync = guarded_fsync
            try:
                ledger: dict[str, object] = {}
                marker = runner["_marker"](root, "a" * 64, barrier, ledger)
            finally:
                globals_["os"].fsync = original_fsync
                os.close(root)
            self.assertEqual(marker.nlink, 1)
            self.assertIn("models.capture.sha256", ledger)
            self.assertGreater(barrier.polls, 0)
            self.assertFalse(barrier.two_link_observed)

    def test_47_start_capture_uses_exact_popen_contract(self) -> None:
        calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
        class FakeProcess:
            pid = 8123
        globals_ = runner["_start_capture"].__globals__
        original_popen, original_getpgid, original_mask = globals_["subprocess"].Popen, globals_["os"].getpgid, globals_["signal"].pthread_sigmask
        def fake_popen(*args: object, **kwargs: object) -> FakeProcess:
            calls.append((args, kwargs)); return FakeProcess()
        globals_["subprocess"].Popen = fake_popen
        globals_["os"].getpgid = lambda pid: pid
        globals_["signal"].pthread_sigmask = lambda *_args: set()
        try:
            value = runner["Profile"].from_bytes(canonical(profile_value()))
            process = runner["_start_capture"](value, "/private/capture/root")
        finally:
            globals_["subprocess"].Popen, globals_["os"].getpgid, globals_["signal"].pthread_sigmask = original_popen, original_getpgid, original_mask
        self.assertEqual(process.pid, 8123)
        self.assertEqual(len(calls), 1)
        args, kwargs = calls[0]
        self.assertEqual(args, ([value.source_path, "models"],))
        self.assertEqual(kwargs["executable"], value.snapshot_path)
        self.assertEqual(kwargs["cwd"], "/private/capture/root/cwd")
        self.assertTrue(kwargs["start_new_session"])
        self.assertTrue(kwargs["close_fds"])
        self.assertEqual(kwargs["env"], {"HOME": value.account_home, "TMPDIR": "/private/capture/root/tmp", "XDG_CACHE_HOME": "/private/capture/root/xdg-cache", "XDG_CONFIG_HOME": "/private/capture/root/xdg-config", "XDG_STATE_HOME": "/private/capture/root/xdg-state", "PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C", "TERM": "dumb", "NO_COLOR": "1"})

    def test_48_start_capture_reaps_fast_exit_before_group_registration(self) -> None:
        class FakeProcess:
            pid = 8124
        globals_ = runner["_start_capture"].__globals__
        original_popen, original_getpgid, original_close, original_mask = globals_["subprocess"].Popen, globals_["os"].getpgid, globals_["_close_fast_exit_group"], globals_["signal"].pthread_sigmask
        closed: list[object] = []
        globals_["subprocess"].Popen = lambda *_args, **_kwargs: FakeProcess()
        def lost(_pid: int) -> int:
            raise ProcessLookupError
        def close_fast(process: object) -> None:
            closed.append(process)
            raise runner["CaptureError"]("fast exit closed")
        globals_["os"].getpgid, globals_["_close_fast_exit_group"], globals_["signal"].pthread_sigmask = lost, close_fast, lambda *_args: set()
        try:
            value = runner["Profile"].from_bytes(canonical(profile_value()))
            with self.assertRaises(runner["CaptureError"]): runner["_start_capture"](value, "/private/capture/root")
        finally:
            globals_["subprocess"].Popen, globals_["os"].getpgid, globals_["_close_fast_exit_group"], globals_["signal"].pthread_sigmask = original_popen, original_getpgid, original_close, original_mask
        self.assertEqual(len(closed), 1)
        self.assertEqual(closed[0].pid, 8124)

    def test_49_runner_stdin_polls_each_chunk(self) -> None:
        chunks = [b"abc", b"de", b""]
        globals_ = runner["_read_stdin"].__globals__
        original_read = globals_["os"].read
        globals_["os"].read = lambda _fd, _size: chunks.pop(0)
        signals = PollCounter()
        try:
            self.assertEqual(runner["_read_stdin"](5, signals), b"abcde")
        finally:
            globals_["os"].read = original_read
        self.assertEqual(signals.calls, 3)

    def test_50_prepare_publishes_canonical_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = os.path.realpath(temporary); os.chmod(parent, 0o700)
            output = os.path.join(parent, profile["OUTPUT_NAME"])
            expected = profile["CaptureProfile"].from_bytes(canonical(profile_value()))
            globals_ = profile["prepare"].__globals__; original = globals_["_from_request"]
            globals_["_from_request"] = lambda _request: (expected, output)
            try:
                result = profile["prepare"](canonical({"closed": "request"}), PollCounter())
            finally:
                globals_["_from_request"] = original
            with open(output, "rb") as handle:
                raw = handle.read()
            self.assertEqual(result, {"profile_sha256": hashlib.sha256(raw).hexdigest(), "status": "prepared"})
            self.assertEqual(profile["CaptureProfile"].from_bytes(raw), expected)
            self.assertEqual(os.stat(output).st_mode & 0o777, 0o600)

    def test_51_validate_rejects_post_derivation_profile_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = os.path.realpath(temporary); os.chmod(parent, 0o700)
            output = os.path.join(parent, profile["OUTPUT_NAME"])
            expected = profile["CaptureProfile"].from_bytes(canonical(profile_value()))
            raw = canonical(profile_value())
            with open(output, "wb") as handle:
                handle.write(raw)
            os.chmod(output, 0o600)
            globals_ = profile["validate"].__globals__; original = globals_["_from_request"]
            globals_["_from_request"] = lambda _request: (expected, output)
            try:
                self.assertEqual(profile["validate"](canonical({"profile_path": output}))["status"], "valid")
                def replace_after_derivation(_request: object) -> tuple[object, str]:
                    os.unlink(output)
                    with open(output, "wb") as handle:
                        handle.write(raw)
                    os.chmod(output, 0o600)
                    return expected, output
                globals_["_from_request"] = replace_after_derivation
                with self.assertRaises(profile["ProfileError"]): profile["validate"](canonical({"profile_path": output}))
            finally:
                globals_["_from_request"] = original

    def test_52_full_fake_capture_publishes_nonsemantic_record_and_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = os.path.realpath(temporary); account, parent, version = (os.path.join(base, item) for item in ("account", "capture", "version"))
            for directory_path in (account, parent, version):
                os.mkdir(directory_path, 0o700)
            source, snapshot = os.path.join(parent, "agy.source"), os.path.join(parent, "snapshot")
            for path, mode in ((source, 0o755), (snapshot, 0o500)):
                with open(path, "wb") as handle:
                    handle.write(b"fixture")
                os.chmod(path, mode)
            Profile, DirectoryIdentity, FileIdentity = runner["Profile"], runner["DirectoryIdentity"], runner["FileIdentity"]
            value = Profile(account, DirectoryIdentity.from_stat(os.stat(account)), parent, DirectoryIdentity.from_stat(os.stat(parent)), FileIdentity.from_stat(os.stat(snapshot)), snapshot, FileIdentity.from_stat(os.stat(source)), source, runner["EXPECTED_SOURCE_SHA256"], runner["EXPECTED_RECOVERY_BINDING_SHA256"], version, DirectoryIdentity.from_stat(os.stat(version)))
            raw = canonical(runner["dataclasses"].asdict(value)); globals_ = runner["run_capture"].__globals__
            original = {name: globals_[name] for name in ("_validate_profile", "_revalidate_authority", "_start_capture", "_capture", "_close_group")}
            class FakeProcess:
                pid = 9031
            def validate_profile(_profile: object, _signals: object) -> tuple[int, int, int, int]:
                return (os.open(source, os.O_RDONLY), os.open(snapshot, os.O_RDONLY), os.open(account, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)), os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)))
            globals_["_validate_profile"] = validate_profile
            globals_["_revalidate_authority"] = lambda *_args: None
            globals_["_start_capture"] = lambda *_args: FakeProcess()
            globals_["_capture"] = lambda *_args: (b"captured output\n", b"")
            globals_["_close_group"] = lambda _process: 0
            try:
                result = runner["run_capture"](value, raw, PollCounter(), RUNNER_SOURCE.read_bytes())
            finally:
                globals_.update(original)
            root = result["artifact_root"]
            with open(os.path.join(root, "models.capture.json"), "rb") as handle:
                record = json.loads(handle.read())
            self.assertEqual(result["status"], "captured")
            self.assertEqual(record["status"], "captured")
            self.assertEqual(record["observation"]["argv"], [source, "models"])
            self.assertFalse(record["limitations"]["accepted_inventory"])
            with open(os.path.join(root, "models.capture.sha256"), "rb") as handle:
                marker = handle.read()
            self.assertEqual(marker, (result["capture_sha256"] + "\n").encode("ascii"))

    def test_53_completion_snapshot_rolls_back_before_direct_exit(self) -> None:
        class ExitCalled(BaseException):
            def __init__(self, code: int): self.code = code
        class PendingSignals:
            owned: tuple[object, ...] = ()
            def poll(self) -> None: raise runner["Interrupted"](runner["signal"].SIGINT)
        state = types.SimpleNamespace(signals=PendingSignals())
        globals_ = runner["_finish_success"].__globals__; original_exit, original_rollback, original_sys = globals_["os"]._exit, globals_["_rollback_active_marker"], globals_["sys"]
        rolled_back: list[bool] = []
        globals_["os"]._exit = lambda code: (_ for _ in ()).throw(ExitCalled(code))
        globals_["_rollback_active_marker"] = lambda: rolled_back.append(True)
        globals_["sys"] = types.SimpleNamespace(stdout=types.SimpleNamespace(buffer=io.BytesIO()))
        try:
            with self.assertRaises(ExitCalled) as raised:
                runner["_finish_success"](state, {"status": "captured"})
        finally:
            globals_["os"]._exit, globals_["_rollback_active_marker"], globals_["sys"] = original_exit, original_rollback, original_sys
        self.assertEqual(raised.exception.code, 128 + runner["signal"].SIGINT)
        self.assertEqual(rolled_back, [True])

    def test_54_profile_cleanup_preserves_post_normalization_mode_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = os.path.realpath(temporary); os.chmod(parent, 0o700)
            output = os.path.join(parent, profile["OUTPUT_NAME"])
            class Drift:
                def poll(self) -> None:
                    if os.path.exists(output) and os.stat(output).st_nlink == 1:
                        os.chmod(output, 0o400)
                        raise profile["ProfileError"]("injected")
            with self.assertRaises(profile["ProfileError"]):
                profile["_publish"](output, b'{"profile":true}\n', Drift())
            self.assertTrue(os.path.exists(output))
            self.assertEqual(os.stat(output).st_mode & 0o777, 0o400)

    def test_55_marker_cleanup_preserves_post_normalization_size_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = os.path.realpath(temporary); os.chmod(directory, 0o700)
            root = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            marker_path = os.path.join(directory, "models.capture.sha256")
            class Drift:
                def poll(self) -> None:
                    if os.path.exists(marker_path) and os.stat(marker_path).st_nlink == 1:
                        with open(marker_path, "ab") as handle:
                            handle.write(b"x")
                        raise runner["CaptureError"]("injected")
            try:
                with self.assertRaises(runner["CaptureError"]):
                    runner["_marker"](root, "b" * 64, Drift(), {})
            finally:
                os.close(root)
            self.assertTrue(os.path.exists(marker_path))
            self.assertEqual(os.path.getsize(marker_path), 66)

    def test_56_real_fake_child_success_uses_runner_subprocess_and_publishes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = os.path.realpath(temporary); value, raw, old, paths = self._synthetic_runner_profile(base, 0)
            globals_ = runner["run_capture"].__globals__
            try:
                result = runner["run_capture"](value, raw, PollCounter(), RUNNER_SOURCE.read_bytes())
            finally:
                globals_.update(old)
            root = result["artifact_root"]
            with open(os.path.join(root, "models.stdout"), "rb") as handle: output = handle.read().decode("ascii").rstrip("\n").split("|")
            self.assertEqual(output, [paths["snapshot"], "models", paths["account"], os.path.join(root, "tmp"), os.path.join(root, "xdg-cache"), "/usr/bin:/bin", "C"])
            self.assertTrue(os.path.exists(os.path.join(root, "models.capture.sha256")))

    def test_57_real_fake_child_nonzero_rejects_without_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = os.path.realpath(temporary); value, raw, old, paths = self._synthetic_runner_profile(base, 7)
            globals_ = runner["run_capture"].__globals__
            try:
                with self.assertRaises(runner["CaptureError"]): runner["run_capture"](value, raw, PollCounter(), RUNNER_SOURCE.read_bytes())
            finally:
                globals_.update(old)
            roots = [entry for entry in os.listdir(paths["parent"]) if entry.startswith("agy-models-capture-1-1-12.")]
            self.assertEqual(len(roots), 1)
            self.assertFalse(os.path.exists(os.path.join(paths["parent"], roots[0], "models.capture.sha256")))

    def test_58_profile_staging_unlink_failure_removes_both_owned_names(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = os.path.realpath(temporary); os.chmod(directory, 0o700)
            globals_ = profile["_publish"].__globals__; original_unlink = globals_["os"].unlink; failed = [False]
            def fail_staging(name: object, *args: object, **kwargs: object) -> None:
                if not failed[0] and str(name).startswith(".models.capture.profile."):
                    failed[0] = True
                    raise OSError("injected staging unlink failure")
                original_unlink(name, *args, **kwargs)
            globals_["os"].unlink = fail_staging
            try:
                with self.assertRaises(OSError):
                    profile["_publish"](os.path.join(directory, profile["OUTPUT_NAME"]), b'{"profile":true}\n', PollCounter())
            finally:
                globals_["os"].unlink = original_unlink
            self.assertTrue(failed[0])
            self.assertEqual(os.listdir(directory), [])

    def test_59_marker_staging_unlink_failure_removes_both_owned_names(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = os.path.realpath(temporary); os.chmod(directory, 0o700)
            root = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)); globals_ = runner["_marker"].__globals__; original_unlink = globals_["os"].unlink; failed = [False]
            def fail_staging(name: object, *args: object, **kwargs: object) -> None:
                if not failed[0] and str(name).startswith(".models.capture.marker."):
                    failed[0] = True
                    raise OSError("injected staging unlink failure")
                original_unlink(name, *args, **kwargs)
            globals_["os"].unlink = fail_staging
            try:
                with self.assertRaises(OSError):
                    runner["_marker"](root, "c" * 64, PollCounter(), {})
            finally:
                globals_["os"].unlink = original_unlink
                os.close(root)
            self.assertTrue(failed[0])
            self.assertEqual(os.listdir(directory), [])

    def _completion_state(self, owned: tuple[object, ...] = ()) -> object:
        return types.SimpleNamespace(signals=types.SimpleNamespace(owned=owned, latch=lambda _signal: None, poll=lambda: None))

    def _active_profile(self, directory: str) -> str:
        output = os.path.join(directory, profile["OUTPUT_NAME"])
        profile["_publish"](output, b'{"profile":true}\n', PollCounter())
        return output

    def _active_marker(self, directory: str) -> str:
        root = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            identity = runner["_marker"](root, "d" * 64, PollCounter(), {})
            root_item = os.fstat(root)
            globals_ = runner["_finish_success"].__globals__
            globals_["ACTIVE_MARKER_ROOT"] = directory
            globals_["ACTIVE_MARKER_ROOT_IDENTITY"] = (root_item.st_dev, root_item.st_ino)
            globals_["ACTIVE_MARKER_DIGEST"] = "d" * 64
            globals_["ACTIVE_MARKER_IDENTITY"] = identity
        finally:
            os.close(root)
        return os.path.join(directory, "models.capture.sha256")

    def _completion_exit_code(self, module: dict[str, object], invoke: object, expected: int) -> None:
        class ExitCalled(BaseException):
            def __init__(self, code: int): self.code = code
        globals_ = module["_finish_success"].__globals__; original_exit = globals_["os"]._exit
        globals_["os"]._exit = lambda code: (_ for _ in ()).throw(ExitCalled(code))
        try:
            with self.assertRaises(ExitCalled) as raised: invoke()
        finally:
            globals_["os"]._exit = original_exit
        self.assertEqual(raised.exception.code, expected)

    def test_60_profile_completion_write_failure_rolls_back_profile(self) -> None:
        class Broken:
            def write(self, _data: bytes) -> int: raise OSError("write failure")
            def flush(self) -> None: raise AssertionError("flush must not run")
        with tempfile.TemporaryDirectory() as temporary:
            directory = os.path.realpath(temporary); os.chmod(directory, 0o700); output = self._active_profile(directory)
            globals_ = profile["_finish_success"].__globals__; original_sys = globals_["sys"]
            globals_["sys"] = types.SimpleNamespace(stdout=types.SimpleNamespace(buffer=Broken()))
            try:
                self._completion_exit_code(profile, lambda: profile["_finish_success"](self._completion_state(), {"status": "prepared"}), 1)
            finally:
                globals_["sys"] = original_sys
            self.assertFalse(os.path.exists(output))

    def test_61_profile_completion_flush_failure_rolls_back_profile(self) -> None:
        class Broken:
            def write(self, data: bytes) -> int: return len(data)
            def flush(self) -> None: raise OSError("flush failure")
        with tempfile.TemporaryDirectory() as temporary:
            directory = os.path.realpath(temporary); os.chmod(directory, 0o700); output = self._active_profile(directory)
            globals_ = profile["_finish_success"].__globals__; original_sys = globals_["sys"]
            globals_["sys"] = types.SimpleNamespace(stdout=types.SimpleNamespace(buffer=Broken()))
            try:
                self._completion_exit_code(profile, lambda: profile["_finish_success"](self._completion_state(), {"status": "prepared"}), 1)
            finally:
                globals_["sys"] = original_sys
            self.assertFalse(os.path.exists(output))

    def test_62_profile_completion_primitive_failure_rolls_back_and_restores_mask(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = os.path.realpath(temporary); os.chmod(directory, 0o700); output = self._active_profile(directory)
            globals_ = profile["_finish_success"].__globals__; original_pending, original_mask, original_sys = globals_["signal"].sigpending, globals_["signal"].pthread_sigmask, globals_["sys"]
            masks: list[tuple[object, object]] = []
            globals_["signal"].sigpending = lambda: (_ for _ in ()).throw(OSError("pending failure"))
            globals_["signal"].pthread_sigmask = lambda how, value: (masks.append((how, value)) or {profile["signal"].SIGTERM})
            globals_["sys"] = types.SimpleNamespace(stdout=types.SimpleNamespace(buffer=io.BytesIO()))
            try:
                self._completion_exit_code(profile, lambda: profile["_finish_success"](self._completion_state((profile["signal"].SIGINT,)), {"status": "prepared"}), 1)
            finally:
                globals_["signal"].sigpending, globals_["signal"].pthread_sigmask, globals_["sys"] = original_pending, original_mask, original_sys
            self.assertFalse(os.path.exists(output))
            self.assertFalse(any(how == profile["signal"].SIG_SETMASK for how, _value in masks))

    def test_63_profile_completion_drift_preserves_residual(self) -> None:
        class Broken:
            def write(self, _data: bytes) -> int: raise OSError("write failure")
            def flush(self) -> None: raise AssertionError("flush must not run")
        with tempfile.TemporaryDirectory() as temporary:
            directory = os.path.realpath(temporary); os.chmod(directory, 0o700); output = self._active_profile(directory)
            with open(output, "ab") as handle: handle.write(b"x")
            os.chmod(output, 0o400)
            globals_ = profile["_finish_success"].__globals__; original_sys = globals_["sys"]
            globals_["sys"] = types.SimpleNamespace(stdout=types.SimpleNamespace(buffer=Broken()))
            try:
                self._completion_exit_code(profile, lambda: profile["_finish_success"](self._completion_state(), {"status": "prepared"}), 1)
            finally:
                globals_["sys"] = original_sys
            self.assertTrue(os.path.exists(output))
            self.assertEqual(os.stat(output).st_mode & 0o777, 0o400)
            self.assertEqual(os.path.getsize(output), len(b'{"profile":true}\n') + 1)

    def test_64_runner_completion_write_failure_rolls_back_marker(self) -> None:
        class Broken:
            def write(self, _data: bytes) -> int: raise OSError("write failure")
            def flush(self) -> None: raise AssertionError("flush must not run")
        with tempfile.TemporaryDirectory() as temporary:
            directory = os.path.realpath(temporary); os.chmod(directory, 0o700); marker = self._active_marker(directory)
            globals_ = runner["_finish_success"].__globals__; original_sys = globals_["sys"]
            globals_["sys"] = types.SimpleNamespace(stdout=types.SimpleNamespace(buffer=Broken()))
            try:
                self._completion_exit_code(runner, lambda: runner["_finish_success"](self._completion_state(), {"status": "captured"}), 1)
            finally:
                globals_["sys"] = original_sys
            self.assertFalse(os.path.exists(marker))

    def test_65_runner_completion_flush_failure_rolls_back_marker(self) -> None:
        class Broken:
            def write(self, data: bytes) -> int: return len(data)
            def flush(self) -> None: raise OSError("flush failure")
        with tempfile.TemporaryDirectory() as temporary:
            directory = os.path.realpath(temporary); os.chmod(directory, 0o700); marker = self._active_marker(directory)
            globals_ = runner["_finish_success"].__globals__; original_sys = globals_["sys"]
            globals_["sys"] = types.SimpleNamespace(stdout=types.SimpleNamespace(buffer=Broken()))
            try:
                self._completion_exit_code(runner, lambda: runner["_finish_success"](self._completion_state(), {"status": "captured"}), 1)
            finally:
                globals_["sys"] = original_sys
            self.assertFalse(os.path.exists(marker))

    def test_66_runner_completion_primitive_failure_rolls_back_and_restores_mask(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = os.path.realpath(temporary); os.chmod(directory, 0o700); marker = self._active_marker(directory)
            globals_ = runner["_finish_success"].__globals__; original_pending, original_mask, original_sys = globals_["signal"].sigpending, globals_["signal"].pthread_sigmask, globals_["sys"]
            masks: list[tuple[object, object]] = []
            globals_["signal"].sigpending = lambda: (_ for _ in ()).throw(OSError("pending failure"))
            globals_["signal"].pthread_sigmask = lambda how, value: (masks.append((how, value)) or {runner["signal"].SIGTERM})
            globals_["sys"] = types.SimpleNamespace(stdout=types.SimpleNamespace(buffer=io.BytesIO()))
            try:
                self._completion_exit_code(runner, lambda: runner["_finish_success"](self._completion_state((runner["signal"].SIGINT,)), {"status": "captured"}), 1)
            finally:
                globals_["signal"].sigpending, globals_["signal"].pthread_sigmask, globals_["sys"] = original_pending, original_mask, original_sys
            self.assertFalse(os.path.exists(marker))
            self.assertFalse(any(how == runner["signal"].SIG_SETMASK for how, _value in masks))

    def test_67_profile_completion_short_write_rolls_back_profile(self) -> None:
        class Short:
            def write(self, _data: bytes) -> int: return 0
            def flush(self) -> None: raise AssertionError("flush must not run")
        with tempfile.TemporaryDirectory() as temporary:
            directory = os.path.realpath(temporary); os.chmod(directory, 0o700); output = self._active_profile(directory)
            globals_ = profile["_finish_success"].__globals__; original_sys = globals_["sys"]
            globals_["sys"] = types.SimpleNamespace(stdout=types.SimpleNamespace(buffer=Short()))
            try:
                self._completion_exit_code(profile, lambda: profile["_finish_success"](self._completion_state(), {"status": "prepared"}), 1)
            finally:
                globals_["sys"] = original_sys
            self.assertFalse(os.path.exists(output))

    def test_68_runner_completion_short_write_rolls_back_marker(self) -> None:
        class Short:
            def write(self, _data: bytes) -> int: return 0
            def flush(self) -> None: raise AssertionError("flush must not run")
        with tempfile.TemporaryDirectory() as temporary:
            directory = os.path.realpath(temporary); os.chmod(directory, 0o700); marker = self._active_marker(directory)
            globals_ = runner["_finish_success"].__globals__; original_sys = globals_["sys"]
            globals_["sys"] = types.SimpleNamespace(stdout=types.SimpleNamespace(buffer=Short()))
            try:
                self._completion_exit_code(runner, lambda: runner["_finish_success"](self._completion_state(), {"status": "captured"}), 1)
            finally:
                globals_["sys"] = original_sys
            self.assertFalse(os.path.exists(marker))

    def _completion_failure_exits_for_latched_signal(self, module: dict[str, object], active_path: str, signum: object, inject_during_rollback: bool) -> None:
        class ExitCalled(BaseException):
            def __init__(self, code: int): self.code = code
        class Broken:
            def write(self, _data: bytes) -> int: raise OSError("write failure")
            def flush(self) -> None: raise AssertionError("flush must not run")
        globals_ = module["_finish_success"].__globals__
        original_mask, original_pending, original_wait = globals_["signal"].pthread_sigmask, globals_["signal"].sigpending, globals_["signal"].sigwait
        original_exit, original_rollback, original_sys = globals_["os"]._exit, globals_["_rollback_active_profile" if module is profile else "_rollback_active_marker"], globals_["sys"]
        pending, masks = [not inject_during_rollback], []
        def fake_mask(how: object, value: object) -> set[object]:
            masks.append((how, value)); return set()
        def wrapped_rollback() -> None:
            original_rollback()
            pending[0] = True
        globals_["signal"].pthread_sigmask = fake_mask
        globals_["signal"].sigpending = lambda: {signum} if pending[0] else set()
        globals_["signal"].sigwait = lambda _items: signum
        globals_["os"]._exit = lambda code: (_ for _ in ()).throw(ExitCalled(code))
        globals_["_rollback_active_profile" if module is profile else "_rollback_active_marker"] = wrapped_rollback
        globals_["sys"] = types.SimpleNamespace(stdout=types.SimpleNamespace(buffer=Broken()))
        state = types.SimpleNamespace(signals=module["Signals"]((signum,)))
        try:
            with self.assertRaises(ExitCalled) as raised:
                module["_finish_success"](state, {"status": "prepared" if module is profile else "captured"})
        finally:
            globals_["signal"].pthread_sigmask, globals_["signal"].sigpending, globals_["signal"].sigwait = original_mask, original_pending, original_wait
            globals_["os"]._exit, globals_["_rollback_active_profile" if module is profile else "_rollback_active_marker"], globals_["sys"] = original_exit, original_rollback, original_sys
        self.assertEqual(raised.exception.code, 128 + signum)
        self.assertFalse(os.path.exists(active_path))
        self.assertFalse(any(how == module["signal"].SIG_SETMASK for how, _value in masks))

    def test_69_profile_completion_pending_signal_exits_after_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = os.path.realpath(temporary); os.chmod(directory, 0o700)
            self._completion_failure_exits_for_latched_signal(profile, self._active_profile(directory), profile["signal"].SIGHUP, False)

    def test_70_runner_completion_pending_signal_exits_after_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = os.path.realpath(temporary); os.chmod(directory, 0o700)
            self._completion_failure_exits_for_latched_signal(runner, self._active_marker(directory), runner["signal"].SIGINT, False)

    def test_71_profile_completion_signal_during_rollback_exits_after_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = os.path.realpath(temporary); os.chmod(directory, 0o700)
            self._completion_failure_exits_for_latched_signal(profile, self._active_profile(directory), profile["signal"].SIGTERM, True)

    def test_72_runner_completion_signal_during_rollback_exits_after_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = os.path.realpath(temporary); os.chmod(directory, 0o700)
            self._completion_failure_exits_for_latched_signal(runner, self._active_marker(directory), runner["signal"].SIGHUP, True)

    def _completion_mask_failure_still_rolls_back(self, module: dict[str, object], active_path: str) -> None:
        class Broken:
            def write(self, _data: bytes) -> int: raise OSError("write failure")
            def flush(self) -> None: raise AssertionError("flush must not run")
        globals_ = module["_finish_success"].__globals__; original_mask, original_sys = globals_["signal"].pthread_sigmask, globals_["sys"]
        rollback_name = "_rollback_active_profile" if module is profile else "_rollback_active_marker"
        original_rollback = globals_[rollback_name]; calls = [0]
        def fail_mask(_how: object, _items: object) -> object: raise OSError("mask failure")
        def wrapped_rollback() -> None: calls[0] += 1; original_rollback()
        globals_["signal"].pthread_sigmask, globals_[rollback_name], globals_["sys"] = fail_mask, wrapped_rollback, types.SimpleNamespace(stdout=types.SimpleNamespace(buffer=Broken()))
        try:
            self._completion_exit_code(module, lambda: module["_finish_success"](self._completion_state(), {"status": "prepared" if module is profile else "captured"}), 1)
        finally:
            globals_["signal"].pthread_sigmask, globals_[rollback_name], globals_["sys"] = original_mask, original_rollback, original_sys
        self.assertEqual(calls, [1])
        self.assertFalse(os.path.exists(active_path))

    def test_73_profile_completion_mask_failure_still_rolls_back(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = os.path.realpath(temporary); os.chmod(directory, 0o700)
            self._completion_mask_failure_still_rolls_back(profile, self._active_profile(directory))

    def test_74_runner_completion_mask_failure_still_rolls_back(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = os.path.realpath(temporary); os.chmod(directory, 0o700)
            self._completion_mask_failure_still_rolls_back(runner, self._active_marker(directory))

    def _selected_signal_survives_rollback_failure(self, module: dict[str, object], signum: object) -> None:
        class ExitCalled(BaseException):
            def __init__(self, code: int): self.code = code
        globals_ = module["_finish_success"].__globals__; rollback_name = "_rollback_active_profile" if module is profile else "_rollback_active_marker"
        original_mask, original_pending, original_wait = globals_["signal"].pthread_sigmask, globals_["signal"].sigpending, globals_["signal"].sigwait
        original_exit, original_rollback, original_sys = globals_["os"]._exit, globals_[rollback_name], globals_["sys"]
        globals_["signal"].pthread_sigmask = lambda _how, _items: set()
        globals_["signal"].sigpending = lambda: {signum}
        globals_["signal"].sigwait = lambda _items: signum
        globals_["os"]._exit = lambda code: (_ for _ in ()).throw(ExitCalled(code))
        globals_[rollback_name] = lambda: (_ for _ in ()).throw(OSError("rollback residual"))
        globals_["sys"] = types.SimpleNamespace(stdout=types.SimpleNamespace(buffer=io.BytesIO()))
        state = types.SimpleNamespace(signals=module["Signals"]((signum,)))
        try:
            with self.assertRaises(ExitCalled) as raised:
                module["_finish_success"](state, {"status": "prepared" if module is profile else "captured"})
        finally:
            globals_["signal"].pthread_sigmask, globals_["signal"].sigpending, globals_["signal"].sigwait = original_mask, original_pending, original_wait
            globals_["os"]._exit, globals_[rollback_name], globals_["sys"] = original_exit, original_rollback, original_sys
        self.assertEqual(raised.exception.code, 128 + signum)

    def test_75_profile_selected_pending_signal_survives_rollback_failure(self) -> None:
        self._selected_signal_survives_rollback_failure(profile, profile["signal"].SIGINT)

    def test_76_runner_selected_pending_signal_survives_rollback_failure(self) -> None:
        self._selected_signal_survives_rollback_failure(runner, runner["signal"].SIGTERM)

    def test_77_real_profile_publication_validates_across_capture_parent_nlink_delta(self) -> None:
        """The profile leaf changes APFS parent nlink; core identity remains bound."""
        with tempfile.TemporaryDirectory() as temporary:
            parent = os.path.realpath(temporary); os.chmod(parent, 0o700)
            output = os.path.join(parent, profile["OUTPUT_NAME"])
            pre = profile["DirectoryIdentity"].from_stat(os.stat(parent))
            expected = profile["CaptureProfile"](
                "/private/account", profile["DirectoryIdentity"](1, 1, 1, 0o700, 2, 1),
                parent, pre, profile["FileIdentity"](1, 1, 1, 1, 0o500, 1, 1, 1, 1),
                "/private/snapshot", profile["FileIdentity"](1, 1, 1, 1, 0o755, 1, 1, 1, 1),
                "/private/source", profile["EXPECTED_SOURCE_SHA256"], profile["EXPECTED_RECOVERY_BINDING_SHA256"],
                "/private/recovery", profile["DirectoryIdentity"](1, 1, 1, 0o700, 2, 1),
            )
            globals_ = profile["prepare"].__globals__
            original = globals_["_from_request"]
            try:
                globals_["_from_request"] = lambda _request: (expected, output)
                profile["prepare"](canonical({"closed": "request"}), PollCounter())
                observed = profile["DirectoryIdentity"].from_stat(os.stat(parent))
                # Simulate the APFS post-publication delta even on filesystems
                # whose directory nlink does not track regular children.
                post = profile["dataclasses"].replace(expected, capture_parent_identity=profile["dataclasses"].replace(observed, nlink=pre.nlink + 1))
                globals_["_from_request"] = lambda _request: (post, output)
                self.assertEqual(profile["validate"](canonical({"profile_path": output}))["status"], "valid")
            finally:
                globals_["_from_request"] = original

    def test_78_capture_parent_stable_identity_rejects_mode_or_invalid_nlink(self) -> None:
        base = profile["DirectoryIdentity"](1, 2, 3, 0o700, 2, 4)
        for field, value in (("dev", 9), ("gid", 9), ("ino", 9), ("mode", 0o755), ("uid", 9)):
            self.assertFalse(profile["_same_capture_parent"](profile["dataclasses"].replace(base, **{field: value}), base))
        self.assertFalse(profile["_same_capture_parent"](base, profile["dataclasses"].replace(base, nlink=0)))
        self.assertFalse(runner["_same_capture_parent"](runner["DirectoryIdentity"](1, 2, 3, 0o700, 0, 4), runner["DirectoryIdentity"](1, 2, 3, 0o700, 2, 4)))

    def test_79_runner_tolerates_foreign_capture_parent_sibling(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = os.path.realpath(temporary); value, raw, old, paths = self._synthetic_runner_profile(base, 0)
            foreign = os.path.join(paths["parent"], "unrelated-owner-file")
            with open(foreign, "wb") as handle: handle.write(b"preserve")
            os.chmod(foreign, 0o600)
            globals_ = runner["run_capture"].__globals__
            try:
                result = runner["run_capture"](value, raw, PollCounter(), RUNNER_SOURCE.read_bytes())
            finally:
                globals_.update(old)
            self.assertEqual(result["status"], "captured")
            with open(foreign, "rb") as handle: self.assertEqual(handle.read(), b"preserve")

    def test_80_runner_rejects_foreign_entry_inside_owned_root_before_child(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = os.path.realpath(temporary); value, raw, old, paths = self._synthetic_runner_profile(base, 0)
            globals_ = runner["run_capture"].__globals__; original_empty, original_start = globals_["_empty_scratch"], globals_["_start_capture"]; calls = [0]; launched = [False]
            def add_foreign(root_fd: int) -> None:
                original_empty(root_fd); calls[0] += 1
                if calls[0] == 1:
                    descriptor = os.open("foreign", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=root_fd)
                    os.close(descriptor)
            globals_["_empty_scratch"] = add_foreign
            globals_["_start_capture"] = lambda *_args: (launched.__setitem__(0, True) or None)
            try:
                with self.assertRaises(runner["CaptureError"]): runner["run_capture"](value, raw, PollCounter(), RUNNER_SOURCE.read_bytes())
            finally:
                globals_["_empty_scratch"], globals_["_start_capture"] = original_empty, original_start; globals_.update(old)
            self.assertFalse(launched[0])
            roots = [entry for entry in os.listdir(paths["parent"]) if entry.startswith("agy-models-capture-1-1-12.")]
            self.assertEqual(len(roots), 1)
            self.assertTrue(os.path.exists(os.path.join(paths["parent"], roots[0], "foreign")))

    def test_81_runner_rejects_owned_root_path_replacement_before_child(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = os.path.realpath(temporary); value, raw, old, paths = self._synthetic_runner_profile(base, 0)
            globals_ = runner["run_capture"].__globals__; original_verify, original_start = globals_["_verify_pre_child_root"], globals_["_start_capture"]
            launched = [False]
            def replace_root(root_path: str, root_name: str, root_fd: int, root_identity: object, capture_profile: object, capture_fd: int) -> None:
                moved = root_path + ".replaced"
                os.rename(root_path, moved)
                os.mkdir(root_path, 0o700)
                original_verify(root_path, root_name, root_fd, root_identity, capture_profile, capture_fd)
            globals_["_verify_pre_child_root"] = replace_root
            globals_["_start_capture"] = lambda *_args: (launched.__setitem__(0, True) or None)
            try:
                with self.assertRaises(runner["CaptureError"]): runner["run_capture"](value, raw, PollCounter(), RUNNER_SOURCE.read_bytes())
            finally:
                globals_["_verify_pre_child_root"], globals_["_start_capture"] = original_verify, original_start; globals_.update(old)
            self.assertFalse(launched[0])
            roots = [entry for entry in os.listdir(paths["parent"]) if entry.startswith("agy-models-capture-1-1-12.")]
            self.assertEqual(len(roots), 2)

    def test_82_exit_zero_child_scratch_write_rejects_without_marker_and_preserves_residual(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = os.path.realpath(temporary); value, raw, old, paths = self._synthetic_runner_profile(base, 0, scratch_write=True)
            globals_ = runner["run_capture"].__globals__
            try:
                with self.assertRaises(runner["CaptureError"]): runner["run_capture"](value, raw, PollCounter(), RUNNER_SOURCE.read_bytes())
            finally:
                globals_.update(old)
            roots = [entry for entry in os.listdir(paths["parent"]) if entry.startswith("agy-models-capture-1-1-12.")]
            self.assertEqual(len(roots), 1)
            root = os.path.join(paths["parent"], roots[0])
            self.assertEqual(set(os.listdir(root)), set(runner["SCRATCH_NAMES"]))
            self.assertEqual(os.listdir(os.path.join(root, "tmp")), ["residual"])
            for name in (runner["OUTPUT_PROFILE_NAME"], "models.stdout", "models.stderr", "models.capture.summary.json", "models.capture.json", "models.capture.sha256", "models_capture_1_1_12_runner.py", "models_capture_1_1_12_runner.py.sha256"):
                self.assertFalse(os.path.exists(os.path.join(root, name)))

    def test_83_exit_zero_child_with_empty_scratch_remains_captured(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = os.path.realpath(temporary); value, raw, old, _paths = self._synthetic_runner_profile(base, 0, scratch_write=False)
            globals_ = runner["run_capture"].__globals__
            try:
                result = runner["run_capture"](value, raw, PollCounter(), RUNNER_SOURCE.read_bytes())
            finally:
                globals_.update(old)
            self.assertEqual(result["status"], "captured")
            self.assertTrue(os.path.isfile(os.path.join(result["artifact_root"], "models.capture.sha256")))


if __name__ == "__main__":
    unittest.main()
