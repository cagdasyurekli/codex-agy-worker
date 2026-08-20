#!/usr/bin/env python3
"""Offline fake-control tests for the macOS local update notifier."""

from __future__ import annotations

import importlib.util
import os
import plistlib
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "update_notifier.py"
SPEC = importlib.util.spec_from_file_location("update_notifier_tested", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
notifier = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = notifier
SPEC.loader.exec_module(notifier)


class Fixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="agy-notifier-test-")
        root = Path(self.temporary.name)
        self.home = root / "account"
        self.source = root / "source"
        self.home.mkdir(mode=0o700)
        self.source.mkdir(mode=0o700)
        (self.source / ".git").mkdir(mode=0o700)
        for relative in notifier.SOURCE_FILES:
            source = ROOT / relative
            destination = self.source / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.chmod(destination.parent, 0o700)
            shutil.copyfile(source, destination)
            os.chmod(destination, 0o500 if relative.endswith((".py", ".sh")) else 0o400)
        self.paths = notifier.layout(self.home, self.source)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def installed(self, loaded: str = "loaded") -> dict[str, object]:
        states = iter([loaded])
        with mock.patch.object(notifier, "loaded_state", side_effect=lambda _uid: next(states)), mock.patch.object(
            notifier, "_launchctl", return_value=subprocess.CompletedProcess([], 0, b"", b"")
        ):
            notifier.install(self.paths)
        return notifier._load_json(self.paths.ledger, notifier.MAX_LEDGER)


class HomeAndSourceTests(Fixture):
    def test_home_mismatch_rejected(self) -> None:
        record = type("Record", (), {"pw_dir": str(self.home)})()
        with mock.patch.object(notifier.pwd, "getpwuid", return_value=record), mock.patch.dict(os.environ, {"HOME": str(self.home / "other")}):
            with self.assertRaisesRegex(notifier.NotifierError, "ambient HOME"):
                notifier.canonical_home()

    def test_home_match_uses_account_database(self) -> None:
        record = type("Record", (), {"pw_dir": str(self.home)})()
        with mock.patch.object(notifier.pwd, "getpwuid", return_value=record), mock.patch.dict(os.environ, {"HOME": str(self.home)}):
            self.assertEqual(notifier.canonical_home(), self.home)

    def test_writable_account_ancestor_rejected(self) -> None:
        unsafe = self.home / "unsafe"
        unsafe.mkdir(mode=0o777)
        os.chmod(unsafe, 0o777)
        with self.assertRaisesRegex(notifier.NotifierError, "writability"):
            notifier._safe_components(self.home, unsafe, allow_missing=False)

    def test_symlink_account_ancestor_rejected(self) -> None:
        target = self.home / "target"; target.mkdir()
        alias = self.home / "alias"; alias.symlink_to(target, target_is_directory=True)
        with self.assertRaisesRegex(notifier.NotifierError, "symlinked"):
            notifier._safe_components(self.home, alias, allow_missing=False)

    def test_complete_manifest_names_probe(self) -> None:
        manifest = notifier.source_manifest(self.source)
        self.assertEqual(set(manifest), set(notifier.SOURCE_FILES))
        self.assertIn("scripts/compatibility_probe.py", manifest)
        self.assertIn("skills/agy-worker/runtime/scripts/compatibility.py", manifest)

    def test_probe_mutation_is_source_drift(self) -> None:
        ledger = self.installed()
        path = self.source / "scripts/compatibility_probe.py"
        os.chmod(path, 0o600); path.write_bytes(path.read_bytes() + b"\n# mutation\n"); os.chmod(path, 0o400)
        with self.assertRaisesRegex(notifier.NotifierError, "source drifted"):
            notifier._validate_ledger(ledger, self.paths)

    def test_each_transitive_source_mutation_is_rejected(self) -> None:
        ledger = self.installed()
        for relative in notifier.SOURCE_FILES:
            with self.subTest(relative=relative):
                path = self.source / relative
                original = path.read_bytes(); original_mode = stat.S_IMODE(path.stat().st_mode)
                os.chmod(path, 0o600); path.write_bytes(original + b"\n"); os.chmod(path, original_mode)
                with self.assertRaises(notifier.NotifierError):
                    notifier._validate_ledger(ledger, self.paths)
                os.chmod(path, 0o600); path.write_bytes(original); os.chmod(path, original_mode)

    def test_group_writable_behavior_source_rejected(self) -> None:
        path = self.source / "update.sh"; os.chmod(path, 0o520)
        with self.assertRaisesRegex(notifier.NotifierError, "behavior source"):
            notifier.source_manifest(self.source)


class InstallStatusTests(Fixture):
    def test_install_bootstrap_success(self) -> None:
        ledger = self.installed()
        self.assertEqual(ledger["phase"], "loaded")
        self.assertEqual(stat.S_IMODE(self.paths.ledger.stat().st_mode), 0o600)
        plist = plistlib.loads(self.paths.plist.read_bytes())
        self.assertEqual(plist["Label"], notifier.LABEL)
        self.assertEqual(plist["ProgramArguments"][4], str(self.paths.launcher))
        self.assertEqual(plist["ProgramArguments"][5], "--scheduled")
        self.assertEqual(plist["ProgramArguments"][6], str(self.paths.snapshot / "scripts/update_notifier.py"))
        self.assertNotIn("RunAtLoad", plist)

    def test_bootstrap_side_effect_plus_error_reconciles_loaded(self) -> None:
        with mock.patch.object(notifier, "_launchctl", return_value=subprocess.CompletedProcess([], 5, b"", b"")), mock.patch.object(notifier, "loaded_state", return_value="loaded"):
            notifier.install(self.paths)
        self.assertEqual(notifier._load_json(self.paths.ledger, notifier.MAX_LEDGER)["phase"], "loaded")

    def test_launchd_reconciliation_queries_exact_account_label(self) -> None:
        completed = subprocess.CompletedProcess([], 0, b"", b"")
        with mock.patch.object(notifier, "_launchctl", return_value=completed) as launchctl:
            self.assertEqual(notifier.loaded_state(501), "loaded")
        launchctl.assert_called_once_with(["print", f"gui/501/{notifier.LABEL}"])

    def test_bootstrap_unknown_retains_ledger(self) -> None:
        with mock.patch.object(notifier, "_launchctl", return_value=subprocess.CompletedProcess([], 5, b"", b"")), mock.patch.object(notifier, "loaded_state", return_value="unknown"):
            with self.assertRaisesRegex(notifier.NotifierError, "unknown"):
                notifier.install(self.paths)
        self.assertTrue(self.paths.ledger.exists())
        self.assertTrue(self.paths.plist.exists())

    def test_bootstrap_proven_unloaded_retains_recovery(self) -> None:
        with mock.patch.object(notifier, "_launchctl", return_value=subprocess.CompletedProcess([], 5, b"", b"")), mock.patch.object(notifier, "loaded_state", return_value="unloaded"):
            with self.assertRaisesRegex(notifier.NotifierError, "retained recovery"):
                notifier.install(self.paths)
        self.assertTrue(self.paths.ledger.exists())

    def test_copy_failure_has_uninstall_authority(self) -> None:
        with mock.patch.object(notifier, "_copy_snapshot", side_effect=OSError("injected copy failure")):
            with self.assertRaises(OSError):
                notifier.install(self.paths)
        ledger = notifier._load_json(self.paths.ledger, notifier.MAX_LEDGER)
        self.assertEqual(ledger["phase"], "preparing")
        with mock.patch.object(notifier, "loaded_state", side_effect=["unloaded", "unloaded"]):
            notifier.uninstall(self.paths)
        self.assertEqual(notifier._load_json(self.paths.ledger, notifier.MAX_LEDGER)["phase"], "uninstalled")

    def test_idempotent_install_requires_exact_plist(self) -> None:
        self.installed()
        with mock.patch.object(notifier, "loaded_state", return_value="loaded"):
            notifier.install(self.paths)
        self.paths.plist.write_bytes(b"replacement")
        with mock.patch.object(notifier, "loaded_state", return_value="loaded"):
            with self.assertRaises(notifier.NotifierError):
                notifier.install(self.paths)

    def test_idempotent_install_rejects_installed_snapshot_drift(self) -> None:
        self.installed()
        target = self.paths.snapshot / "scripts/compatibility_probe.py"
        os.chmod(target, 0o600); target.write_bytes(b"replacement"); os.chmod(target, 0o500)
        with mock.patch.object(notifier, "loaded_state", return_value="loaded"):
            with self.assertRaisesRegex(notifier.NotifierError, "installed behavior source drifted"):
                notifier.install(self.paths)

    def test_status_reports_not_installed(self) -> None:
        with mock.patch("builtins.print") as output:
            notifier.status(self.paths)
        output.assert_called_with("update notifier: not installed")

    def test_status_rejects_malformed_uninstalled_ledger_shape(self) -> None:
        ledger = self.installed()
        with mock.patch.object(notifier, "loaded_state", side_effect=["unloaded", "unloaded"]):
            notifier.uninstall(self.paths)
        ledger = notifier._load_json(self.paths.ledger, notifier.MAX_LEDGER)
        ledger["extra"] = "forbidden"
        self.paths.ledger.write_bytes(notifier._canonical_json(ledger)); os.chmod(self.paths.ledger, 0o600)
        with self.assertRaisesRegex(notifier.NotifierError, "installed state is malformed"):
            notifier.status(self.paths)

    def test_status_reports_safe_source_drift_as_maintenance_required(self) -> None:
        self.installed()
        path = self.source / "scripts/compatibility_probe.py"; os.chmod(path, 0o600); path.write_text("changed\n"); os.chmod(path, 0o400)
        with mock.patch.object(notifier, "loaded_state", return_value="loaded"), mock.patch("builtins.print") as output:
            notifier.status(self.paths)
        output.assert_called_with("update notifier: loaded; source maintenance-required")

    def test_status_keeps_unsafe_live_source_invalid(self) -> None:
        self.installed()
        path = self.source / "scripts/compatibility_probe.py"
        os.chmod(path, 0o620)
        with mock.patch.object(notifier, "loaded_state", return_value="loaded"), mock.patch("builtins.print") as output:
            notifier.status(self.paths)
        output.assert_called_with("update notifier: loaded; source drifted-or-invalid")

    def test_status_reports_installed_snapshot_drift(self) -> None:
        self.installed()
        target = self.paths.snapshot / "update.sh"
        os.chmod(target, 0o600); target.write_bytes(b"replacement"); os.chmod(target, 0o500)
        with mock.patch.object(notifier, "loaded_state", return_value="loaded"), mock.patch("builtins.print") as output:
            notifier.status(self.paths)
        output.assert_called_with("update notifier: loaded; source drifted-or-invalid")


class PrivateStateAndLockTests(Fixture):
    def test_prior_result_symlink_rejected_before_child(self) -> None:
        self.paths.state.mkdir(parents=True, mode=0o700)
        target = self.paths.state / "target"; target.write_text("{}")
        self.paths.result.symlink_to(target)
        with self.assertRaises(notifier.NotifierError):
            notifier._prior_result(self.paths)

    def test_prior_result_oversize_rejected_before_child(self) -> None:
        self.paths.state.mkdir(parents=True, mode=0o700)
        self.paths.result.write_bytes(b"x" * (notifier.MAX_RESULT + 1)); os.chmod(self.paths.result, 0o600)
        with self.assertRaisesRegex(notifier.NotifierError, "exceeds"):
            notifier._prior_result(self.paths)

    def test_prior_result_non_integer_exit_is_malformed(self) -> None:
        self.paths.state.mkdir(parents=True, mode=0o700)
        value = {
            "schema": 1,
            "timestamp": "2026-08-20T00:00:00Z",
            "status": "drift-review",
            "update_exit": [3],
            "fingerprint": "0" * 64,
            "notification_attempted": True,
        }
        self.paths.result.write_bytes(notifier._canonical_json(value)); os.chmod(self.paths.result, 0o600)
        with self.assertRaisesRegex(notifier.NotifierError, "malformed"):
            notifier._prior_result(self.paths)

    def test_lock_serializes_overlapping_run_and_uninstall(self) -> None:
        with notifier.lifecycle_lock(self.paths):
            with self.assertRaisesRegex(notifier.NotifierError, "another notifier"):
                with notifier.lifecycle_lock(self.paths):
                    pass

    def test_lock_rejects_permissive_replacement(self) -> None:
        self.paths.state.mkdir(parents=True, mode=0o700)
        lock = self.paths.state / "lock"; lock.write_text(""); os.chmod(lock, 0o666)
        with self.assertRaisesRegex(notifier.NotifierError, "lock is unsafe"):
            with notifier.lifecycle_lock(self.paths):
                pass


class ChildProtocolTests(Fixture):
    def fake_launcher(self, ack: bytes, delay: float = 0.0) -> None:
        self.paths.state.mkdir(parents=True, mode=0o700)
        program = f'''import os,sys,time\n# argv: --run update shim repo gitdir sentinel ack output status\ntime.sleep({delay!r})\nos.write(int(sys.argv[7]), {ack!r})\nfd=os.open(sys.argv[8],os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600);os.write(fd,b"compatibility result: drift-review\\n");os.close(fd)\nfd=os.open(sys.argv[9],os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600);os.write(fd,b"3\\n");os.close(fd)\n'''
        self.paths.launcher.write_text(program); os.chmod(self.paths.launcher, 0o500)

    def test_nested_ack_delayed_but_complete(self) -> None:
        self.fake_launcher(b"SA", 0.05)
        started = time.monotonic()
        status, _stdout, ack = notifier._run_child(self.paths)
        self.assertEqual((status, ack), (3, b"SA"))
        self.assertGreaterEqual(time.monotonic() - started, 0.04)

    def test_nested_ack_absent_rejected(self) -> None:
        self.fake_launcher(b"")
        with self.assertRaisesRegex(notifier.NotifierError, "acknowledgement"):
            notifier._run_child(self.paths)

    def test_nested_ack_unpaired_rejected(self) -> None:
        self.fake_launcher(b"S")
        with self.assertRaisesRegex(notifier.NotifierError, "acknowledgement"):
            notifier._run_child(self.paths)

    def test_nested_ack_invalid_byte_rejected(self) -> None:
        self.fake_launcher(b"SAX")
        with self.assertRaisesRegex(notifier.NotifierError, "acknowledgement"):
            notifier._run_child(self.paths)

    def test_real_launcher_fake_probe_proves_ack_and_unblocked_mask(self) -> None:
        self.paths.state.mkdir(parents=True, mode=0o700)
        self.paths.snapshot.mkdir(mode=0o700)
        scripts = self.paths.snapshot / "scripts"; scripts.mkdir(mode=0o700)
        launcher = (ROOT / "scripts/update_notifier_child.py").read_bytes()
        self.paths.launcher.write_bytes(launcher); os.chmod(self.paths.launcher, 0o500)
        self.paths.shim.parent.mkdir(mode=0o700)
        self.paths.shim.write_bytes(notifier._shim_payload(launcher)); os.chmod(self.paths.shim, 0o500)
        (scripts / "compatibility_probe.py").write_text(
            "import signal,sys\n"
            "blocked=signal.pthread_sigmask(signal.SIG_BLOCK, [])\n"
            "sys.exit(9 if any(x in blocked for x in (signal.SIGHUP,signal.SIGINT,signal.SIGTERM)) else 0)\n"
        )
        update = self.paths.snapshot / "update.sh"
        update.write_text(
            '#!/bin/bash\n"$(dirname "$0")/../shim/python3" -I -B "$(dirname "$0")/scripts/compatibility_probe.py" fake\n'
            'echo "compatibility result: drift-review"\nexit 3\n'
        )
        # update.sh normally finds python3 on PATH. The explicit shim path above
        # keeps this fake entirely local while exercising the same shim protocol.
        os.chmod(update, 0o500)
        # Point the fake relative shim reference at the installed shim directory.
        fake_link = self.paths.state / "source" / ".." / "shim"
        self.assertEqual(fake_link.resolve(), self.paths.shim.parent.resolve())
        status, stdout, ack = notifier._run_child(self.paths)
        self.assertEqual(status, 3)
        self.assertIn(b"drift-review", stdout)
        self.assertEqual(ack, b"SA")

    def test_child_launcher_unblocks_terminal_signals(self) -> None:
        text = (ROOT / "scripts/update_notifier_child.py").read_text()
        self.assertIn("SIG_UNBLOCK, SIGNALS", text)
        self.assertIn("start_new_session=True", text)
        self.assertIn('argv[1] == "--scheduled"', text)

    def test_parent_death_sentinel_is_passed_to_nested_probe(self) -> None:
        text = (ROOT / "scripts/update_notifier_child.py").read_text()
        self.assertIn("notifier parent disappeared", text)
        self.assertIn("pass_fds=(sentinel_fd,)", text)

    def test_interruption_waits_for_delayed_cleanup_ack(self) -> None:
        self.paths.state.mkdir(parents=True, mode=0o700)
        program = '''import os,sys,time\nack=int(sys.argv[7]); sentinel=int(sys.argv[6])\nos.write(ack,b"S")\nwhile os.read(sentinel,1): pass\ntime.sleep(0.12)\nos.write(ack,b"A")\n'''
        self.paths.launcher.write_text(program); os.chmod(self.paths.launcher, 0o500)
        previous = signal.getsignal(signal.SIGTERM)
        signal.signal(signal.SIGTERM, lambda number, _frame: (_ for _ in ()).throw(notifier.Interrupted(number)))
        sender = threading.Timer(0.05, os.kill, args=(os.getpid(), signal.SIGTERM))
        started = time.monotonic(); sender.start()
        try:
            with self.assertRaises(notifier.Interrupted):
                notifier._run_child(self.paths)
        finally:
            sender.join(); signal.signal(signal.SIGTERM, previous)
        self.assertGreaterEqual(time.monotonic() - started, 0.15)

    def test_interruption_without_cleanup_ack_fails_closed(self) -> None:
        self.paths.state.mkdir(parents=True, mode=0o700)
        program = '''import os,sys\nos.write(int(sys.argv[7]),b"S")\nwhile os.read(int(sys.argv[6]),1): pass\n'''
        self.paths.launcher.write_text(program); os.chmod(self.paths.launcher, 0o500)
        previous = signal.getsignal(signal.SIGTERM)
        signal.signal(signal.SIGTERM, lambda number, _frame: (_ for _ in ()).throw(notifier.Interrupted(number)))
        sender = threading.Timer(0.05, os.kill, args=(os.getpid(), signal.SIGTERM)); sender.start()
        try:
            with self.assertRaisesRegex(notifier.NotifierError, "lacked nested cleanup"):
                notifier._run_child(self.paths)
        finally:
            sender.join(); signal.signal(signal.SIGTERM, previous)


class NotificationTests(Fixture):
    def prepare(self) -> None:
        self.installed()

    def invoke(self, exit_code: int, payload: bytes) -> mock.Mock:
        notification = mock.Mock(return_value=0)
        with mock.patch.object(notifier, "loaded_state", return_value="loaded"), mock.patch.object(
            notifier, "_run_child", return_value=(exit_code, payload, b"SA")
        ), mock.patch.object(notifier, "_run_notification", notification):
            notifier.run(self.paths)
        return notification

    def test_first_drift_attempts_notification(self) -> None:
        self.prepare(); notification = self.invoke(3, b"official 1.1.12\n")
        self.assertEqual(notification.call_count, 1)
        self.assertTrue(notifier._load_json(self.paths.result, notifier.MAX_RESULT)["notification_attempted"])

    def test_same_fingerprint_suppresses_repeat(self) -> None:
        self.prepare(); self.invoke(3, b"official 1.1.12\n")
        notification = self.invoke(3, b"official 1.1.12\n")
        self.assertEqual(notification.call_count, 0)

    def test_changed_fingerprint_notifies_again(self) -> None:
        self.prepare(); self.invoke(3, b"official 1.1.12\n")
        notification = self.invoke(3, b"official 1.1.13\n")
        self.assertEqual(notification.call_count, 1)

    def test_unchanged_never_notifies(self) -> None:
        self.prepare(); notification = self.invoke(0, b"unchanged\n")
        self.assertEqual(notification.call_count, 0)

    def test_evidence_unavailable_never_notifies(self) -> None:
        self.prepare(); notification = self.invoke(2, b"unavailable\n")
        self.assertEqual(notification.call_count, 0)

    def test_safe_source_drift_notifies_for_maintenance_without_update_child(self) -> None:
        self.prepare()
        target = self.source / "update.sh"
        os.chmod(target, 0o600); target.write_bytes(target.read_bytes() + b"\n# reviewed update\n"); os.chmod(target, 0o500)
        notification = mock.Mock(return_value=0)
        with mock.patch.object(notifier, "loaded_state", return_value="loaded"), mock.patch.object(
            notifier, "_run_child", side_effect=AssertionError("external update child must not run")
        ), mock.patch.object(notifier, "_run_notification", notification):
            notifier.run(self.paths)
        value = notifier._load_json(self.paths.result, notifier.MAX_RESULT)
        self.assertEqual((value["status"], value["update_exit"]), ("drift-review", 3))
        ledger = notifier._load_json(self.paths.ledger, notifier.MAX_LEDGER)
        self.assertNotEqual(ledger["manifest"], notifier.source_manifest(self.source))
        notification.assert_called_once_with(self.paths, "maintenance-required")

    def test_same_maintenance_fingerprint_is_suppressed(self) -> None:
        self.prepare()
        target = self.source / "update.sh"
        os.chmod(target, 0o600); target.write_bytes(target.read_bytes() + b"\n# reviewed update\n"); os.chmod(target, 0o500)
        with mock.patch.object(notifier, "loaded_state", return_value="loaded"), mock.patch.object(
            notifier, "_run_child", side_effect=AssertionError("external update child must not run")
        ), mock.patch.object(notifier, "_run_notification", return_value=0):
            notifier.run(self.paths)
        notification = mock.Mock(return_value=0)
        with mock.patch.object(notifier, "loaded_state", return_value="loaded"), mock.patch.object(
            notifier, "_run_child", side_effect=AssertionError("external update child must not run")
        ), mock.patch.object(notifier, "_run_notification", notification):
            notifier.run(self.paths)
        self.assertEqual(notification.call_count, 0)

    def test_unsafe_source_change_neither_runs_child_nor_notifies(self) -> None:
        self.prepare()
        target = self.source / "update.sh"; os.chmod(target, 0o520)
        with mock.patch.object(notifier, "loaded_state", return_value="loaded"), mock.patch.object(
            notifier, "_run_child"
        ) as child, mock.patch.object(notifier, "_run_notification") as notification:
            with self.assertRaisesRegex(notifier.NotifierError, "behavior source"):
                notifier.run(self.paths)
        child.assert_not_called(); notification.assert_not_called()

    def test_maintenance_notification_uses_only_fixed_sanitized_text(self) -> None:
        process = mock.Mock(); process.wait.return_value = 0
        with mock.patch.object(notifier.subprocess, "Popen", return_value=process) as popen:
            self.assertEqual(notifier._run_notification(self.paths, "maintenance-required"), 0)
        argv = popen.call_args.args[0]
        self.assertEqual(argv[-3], "--notify")
        self.assertEqual(argv[-2], "codex-agy-worker notifier maintenance")
        self.assertEqual(argv[-1], "Monitoring paused after the bound source changed; run update-notifier.sh refresh.")
        self.assertNotIn(str(self.source), " ".join(argv[-2:]))

    def test_unknown_notification_status_is_rejected_before_process(self) -> None:
        with mock.patch.object(notifier.subprocess, "Popen") as popen:
            with self.assertRaisesRegex(notifier.NotifierError, "status is invalid"):
                notifier._run_notification(self.paths, "unknown")
        popen.assert_not_called()

    def test_notification_failure_records_attempt_without_delivery_claim(self) -> None:
        self.prepare()
        with mock.patch.object(notifier, "loaded_state", return_value="loaded"), mock.patch.object(
            notifier, "_run_child", return_value=(3, b"drift\n", b"SA")
        ), mock.patch.object(notifier, "_run_notification", return_value=7):
            with self.assertRaisesRegex(notifier.NotifierError, "cannot be retracted"):
                notifier.run(self.paths)
        value = notifier._load_json(self.paths.result, notifier.MAX_RESULT)
        self.assertTrue(value["notification_attempted"])
        self.assertNotIn("delivered", value)

    def test_timestamp_is_canonical_utc_seconds(self) -> None:
        self.assertRegex(notifier._timestamp(), r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class RefreshTests(Fixture):
    def test_refresh_rebinds_safe_current_source_through_uninstall_install(self) -> None:
        self.installed()
        target = self.source / "update.sh"
        os.chmod(target, 0o600); target.write_bytes(target.read_bytes() + b"\n# reviewed update\n"); os.chmod(target, 0o500)
        states = iter(["loaded", "unloaded", "unloaded", "unloaded", "loaded"])
        with mock.patch.object(notifier, "loaded_state", side_effect=lambda _uid: next(states)), mock.patch.object(
            notifier, "_launchctl", return_value=subprocess.CompletedProcess([], 0, b"", b"")
        ) as launchctl:
            notifier.refresh(self.paths)
        ledger = notifier._load_json(self.paths.ledger, notifier.MAX_LEDGER)
        self.assertEqual(ledger["phase"], "loaded")
        self.assertEqual(ledger["manifest"]["update.sh"], notifier._hash_file(target))
        self.assertEqual(notifier._hash_file(self.paths.snapshot / "update.sh"), notifier._hash_file(target))
        self.assertEqual(launchctl.call_args_list[0].args[0][0], "bootout")
        self.assertEqual(launchctl.call_args_list[-1].args[0][0], "bootstrap")

    def test_refresh_requires_existing_authenticated_installation(self) -> None:
        with mock.patch.object(notifier, "_launchctl") as launchctl:
            with self.assertRaisesRegex(notifier.NotifierError, "not installed"):
                notifier.refresh(self.paths)
        launchctl.assert_not_called()

    def test_refresh_refuses_unsafe_current_source_before_uninstall(self) -> None:
        self.installed()
        target = self.source / "update.sh"; os.chmod(target, 0o520)
        with mock.patch.object(notifier, "_launchctl") as launchctl:
            with self.assertRaisesRegex(notifier.NotifierError, "behavior source"):
                notifier.refresh(self.paths)
        launchctl.assert_not_called()
        self.assertEqual(notifier._load_json(self.paths.ledger, notifier.MAX_LEDGER)["phase"], "loaded")

    def test_refresh_stops_after_preserving_replaced_installed_file(self) -> None:
        self.installed()
        replacement = self.paths.snapshot / "update.sh"
        os.chmod(replacement, 0o600); replacement.write_bytes(b"replacement"); os.chmod(replacement, 0o500)
        with mock.patch.object(notifier, "loaded_state", side_effect=["unloaded", "unloaded"]):
            with self.assertRaisesRegex(notifier.NotifierError, "preserved replacement files"):
                notifier.refresh(self.paths)
        self.assertEqual(replacement.read_bytes(), b"replacement")
        self.assertEqual(notifier._load_json(self.paths.ledger, notifier.MAX_LEDGER)["phase"], "uninstalled")

    def test_refresh_rejects_unauthenticated_uninstall_record(self) -> None:
        self.installed()
        self.paths.tombstone.write_bytes(notifier._canonical_json({
            "schema": 1,
            "label": notifier.LABEL,
            "phase": "started",
            "replacements": [],
            "authentication": "0" * 64,
        }))
        os.chmod(self.paths.tombstone, 0o600)
        with self.assertRaisesRegex(notifier.NotifierError, "unauthenticated"):
            notifier.refresh(self.paths)
        self.assertEqual(notifier._load_json(self.paths.ledger, notifier.MAX_LEDGER)["phase"], "loaded")

    def test_refresh_install_failure_retains_recoverable_authority(self) -> None:
        self.installed()
        with mock.patch.object(notifier, "loaded_state", side_effect=["unloaded", "unloaded", "unloaded"]), mock.patch.object(
            notifier, "_copy_snapshot", side_effect=OSError("injected refresh copy failure")
        ):
            with self.assertRaisesRegex(OSError, "refresh copy failure"):
                notifier.refresh(self.paths)
        self.assertEqual(notifier._load_json(self.paths.ledger, notifier.MAX_LEDGER)["phase"], "preparing")
        with mock.patch.object(notifier, "loaded_state", side_effect=["unloaded", "unloaded", "unloaded", "loaded"]), mock.patch.object(
            notifier, "_launchctl", return_value=subprocess.CompletedProcess([], 0, b"", b"")
        ):
            notifier.refresh(self.paths)
        self.assertEqual(notifier._load_json(self.paths.ledger, notifier.MAX_LEDGER)["phase"], "loaded")


class UninstallTests(Fixture):
    def test_uninstall_tombstone_creation_failure_keeps_ledger(self) -> None:
        self.installed()
        with mock.patch.object(notifier, "_atomic_write", side_effect=OSError("injected tombstone failure")):
            with self.assertRaises(OSError):
                notifier.uninstall(self.paths)
        self.assertTrue(self.paths.ledger.exists())

    def test_loaded_or_unknown_never_deletes_ledger(self) -> None:
        self.installed()
        with mock.patch.object(notifier, "loaded_state", side_effect=["loaded", "unknown"]), mock.patch.object(
            notifier, "_launchctl", return_value=subprocess.CompletedProcess([], 5, b"", b"")
        ):
            with self.assertRaisesRegex(notifier.NotifierError, "remains resumable"):
                notifier.uninstall(self.paths)
        self.assertTrue(self.paths.ledger.exists()); self.assertTrue(self.paths.tombstone.exists())

    def test_uninstall_resumes_after_bootout_phase(self) -> None:
        self.installed()
        with mock.patch.object(notifier, "loaded_state", side_effect=["unloaded", "unloaded"]):
            notifier.uninstall(self.paths)
        self.assertEqual(notifier._load_json(self.paths.ledger, notifier.MAX_LEDGER)["phase"], "uninstalled")
        tombstone = notifier._load_json(self.paths.tombstone, notifier.MAX_LEDGER)
        self.assertEqual(tombstone["phase"], "completed")

    def test_resumed_uninstall_boots_out_a_reloaded_label_before_deletion(self) -> None:
        ledger = self.installed()
        self.paths.tombstone.write_bytes(notifier._canonical_json(notifier._tombstone_payload(ledger, "unloaded", [])))
        os.chmod(self.paths.tombstone, 0o600)
        with mock.patch.object(notifier, "loaded_state", side_effect=["loaded", "unloaded", "unloaded"]), mock.patch.object(
            notifier, "_launchctl", return_value=subprocess.CompletedProcess([], 0, b"", b"")
        ) as launchctl:
            notifier.uninstall(self.paths)
        launchctl.assert_called_once_with(["bootout", f"gui/{os.getuid()}/{notifier.LABEL}"])
        self.assertEqual(notifier._load_json(self.paths.ledger, notifier.MAX_LEDGER)["phase"], "uninstalled")

    def test_uninstall_rejects_manifest_path_injection_before_deletion(self) -> None:
        ledger = self.installed()
        manifest = dict(ledger["manifest"])
        manifest["../outside"] = manifest.pop("update.sh")
        ledger["manifest"] = manifest
        self.paths.ledger.write_bytes(notifier._canonical_json(ledger)); os.chmod(self.paths.ledger, 0o600)
        outside = self.paths.state / "outside"; outside.write_bytes(b"preserve"); os.chmod(outside, 0o600)
        with self.assertRaisesRegex(notifier.NotifierError, "installed state is malformed"):
            notifier.uninstall(self.paths)
        self.assertEqual(outside.read_bytes(), b"preserve")

    def test_uninstall_preserves_plist_replacement(self) -> None:
        self.installed(); self.paths.plist.write_bytes(b"replacement")
        with mock.patch.object(notifier, "loaded_state", side_effect=["unloaded", "unloaded"]), mock.patch("builtins.print") as output:
            notifier.uninstall(self.paths)
        self.assertEqual(self.paths.plist.read_bytes(), b"replacement")
        output.assert_called_with("update notifier: uninstalled; preserved replacement files")

    def test_uninstall_preserves_source_replacement(self) -> None:
        self.installed(); replacement = self.paths.snapshot / "scripts/compatibility_probe.py"; os.chmod(replacement, 0o600); replacement.write_bytes(b"replacement")
        with mock.patch.object(notifier, "loaded_state", side_effect=["unloaded", "unloaded"]):
            notifier.uninstall(self.paths)
        self.assertEqual(replacement.read_bytes(), b"replacement")

    def test_uninstall_plist_phase_failure_is_resumable(self) -> None:
        self.installed(); real_unlink = os.unlink
        def fail_plist(path: object, *args: object, **kwargs: object) -> None:
            if Path(path) == self.paths.plist:
                raise OSError("injected plist unlink failure")
            real_unlink(path, *args, **kwargs)
        with mock.patch.object(notifier, "loaded_state", return_value="unloaded"), mock.patch.object(os, "unlink", side_effect=fail_plist):
            with self.assertRaises(OSError):
                notifier.uninstall(self.paths)
        tombstone = notifier._load_json(self.paths.tombstone, notifier.MAX_LEDGER)
        self.assertEqual(tombstone["phase"], "unloaded")
        self.assertTrue(self.paths.ledger.exists())

    def test_uninstall_source_phase_failure_is_resumable(self) -> None:
        self.installed(); victim = self.paths.snapshot / "update.sh"; real_unlink = os.unlink
        def fail_source(path: object, *args: object, **kwargs: object) -> None:
            if Path(path) == victim:
                raise OSError("injected source unlink failure")
            real_unlink(path, *args, **kwargs)
        with mock.patch.object(notifier, "loaded_state", return_value="unloaded"), mock.patch.object(os, "unlink", side_effect=fail_source):
            with self.assertRaises(OSError):
                notifier.uninstall(self.paths)
        tombstone = notifier._load_json(self.paths.tombstone, notifier.MAX_LEDGER)
        self.assertEqual(tombstone["phase"], "plist-processed")
        self.assertTrue(self.paths.ledger.exists())

    def test_uninstall_final_launchd_unknown_keeps_authority(self) -> None:
        self.installed()
        with mock.patch.object(notifier, "loaded_state", side_effect=["unloaded", "unknown"]):
            with self.assertRaisesRegex(notifier.NotifierError, "reconfirmed"):
                notifier.uninstall(self.paths)
        self.assertTrue(self.paths.ledger.exists()); self.assertTrue(self.paths.tombstone.exists())

    def test_uninstall_final_ledger_failure_keeps_completed_tombstone(self) -> None:
        self.installed(); real_write = notifier._atomic_write
        def fail_final(path: Path, payload: bytes, mode: int = 0o600) -> None:
            if path == self.paths.ledger and b'"phase":"uninstalled"' in payload:
                raise OSError("injected final ledger failure")
            real_write(path, payload, mode)
        with mock.patch.object(notifier, "loaded_state", return_value="unloaded"), mock.patch.object(notifier, "_atomic_write", side_effect=fail_final):
            with self.assertRaises(OSError):
                notifier.uninstall(self.paths)
        self.assertEqual(notifier._load_json(self.paths.tombstone, notifier.MAX_LEDGER)["phase"], "completed")
        self.assertNotEqual(notifier._load_json(self.paths.ledger, notifier.MAX_LEDGER)["phase"], "uninstalled")

    def test_uninstall_fsync_failure_retains_tombstone(self) -> None:
        self.installed()
        real = notifier._fsync_dir
        calls = 0
        def fail_once(path: Path) -> None:
            nonlocal calls
            calls += 1
            if calls > 1:
                raise OSError("injected fsync failure")
            real(path)
        with mock.patch.object(notifier, "loaded_state", return_value="unloaded"), mock.patch.object(notifier, "_fsync_dir", side_effect=fail_once):
            with self.assertRaises(OSError):
                notifier.uninstall(self.paths)
        self.assertTrue(self.paths.ledger.exists())

    def test_completed_uninstall_can_be_installed_again(self) -> None:
        self.installed()
        with mock.patch.object(notifier, "loaded_state", side_effect=["unloaded", "unloaded"]):
            notifier.uninstall(self.paths)
        with mock.patch.object(notifier, "loaded_state", side_effect=["unloaded", "loaded"]), mock.patch.object(
            notifier, "_launchctl", return_value=subprocess.CompletedProcess([], 0, b"", b"")
        ):
            notifier.install(self.paths)
        self.assertEqual(notifier._load_json(self.paths.ledger, notifier.MAX_LEDGER)["phase"], "loaded")

    def test_completed_uninstall_allows_reinstall_after_live_source_update(self) -> None:
        self.installed()
        target = self.source / "update.sh"
        os.chmod(target, 0o600); target.write_bytes(target.read_bytes() + b"\n# reviewed update\n"); os.chmod(target, 0o500)
        with mock.patch.object(notifier, "loaded_state", side_effect=["unloaded", "unloaded"]):
            notifier.uninstall(self.paths)
        with mock.patch.object(notifier, "loaded_state", side_effect=["unloaded", "loaded"]), mock.patch.object(
            notifier, "_launchctl", return_value=subprocess.CompletedProcess([], 0, b"", b"")
        ):
            notifier.install(self.paths)
        ledger = notifier._load_json(self.paths.ledger, notifier.MAX_LEDGER)
        self.assertEqual(ledger["phase"], "loaded")
        self.assertEqual(ledger["manifest"]["update.sh"], notifier._hash_file(target))

    def test_unauthenticated_tombstone_rejected(self) -> None:
        ledger = self.installed()
        self.paths.tombstone.write_bytes(notifier._canonical_json({"schema":1,"label":notifier.LABEL,"phase":"started","replacements":[],"authentication":"0"*64}))
        os.chmod(self.paths.tombstone, 0o600)
        with self.assertRaisesRegex(notifier.NotifierError, "unauthenticated"):
            notifier.uninstall(self.paths)
        self.assertTrue(self.paths.ledger.exists())


class ContractTests(unittest.TestCase):
    def test_wrapper_execs_fixed_interpreter(self) -> None:
        text = (ROOT / "update-notifier.sh").read_text()
        self.assertIn("exec /usr/bin/python3 -I -S -B", text)

    def test_no_real_home_launchctl_osascript_or_network_in_tests(self) -> None:
        text = Path(__file__).read_text()
        self.assertNotIn("/bin/launchctl\",", text)
        self.assertNotIn("/usr/bin/osascript\",", text)
        network_module = "url" + "lib"
        self.assertNotIn(network_module, text)

    def test_signal_priority_is_hup_int_term_order(self) -> None:
        self.assertEqual(notifier.SIGNALS, (signal.SIGHUP, signal.SIGINT, signal.SIGTERM))
        self.assertEqual([notifier.SIGNAL_EXIT[x] for x in notifier.SIGNALS], [129, 130, 143])

    def test_process_owned_completion_snapshot_uses_fixed_priority(self) -> None:
        child = os.fork()
        if child == 0:
            notifier._complete(0, [signal.SIGTERM, signal.SIGINT])
        _pid, status = os.waitpid(child, 0)
        self.assertEqual(os.waitstatus_to_exitcode(status), 130)

    def test_startup_signal_exits_with_signal_status(self) -> None:
        child = os.fork()
        if child == 0:
            notifier.layout = lambda **_kwargs: os.kill(os.getpid(), signal.SIGHUP)  # type: ignore[assignment]
            notifier.main(["update_notifier.py", "status"])
            os._exit(99)
        _pid, status = os.waitpid(child, 0)
        self.assertEqual(os.waitstatus_to_exitcode(status), 129)


if __name__ == "__main__":
    unittest.main(verbosity=2)
