#!/usr/bin/env python3
"""Recovery-focused cases loaded by the canonical remediation suite."""
from __future__ import annotations

import base64


def run(context: dict[str, object]) -> None:
    """Run the retained tail with the canonical suite's exact context."""
    globals().update(context)
    def linked_preview_authorizes_v11_initial_and_prelaunch_binding() -> None:
        """The public preview digest is the exact V11 launch authorization."""
        fixture = root / "linked-preview-v11"; fixture.mkdir()
        source_repo = fixture / "source"; source_repo.mkdir()
        linked = fixture / "linked"
        try:
            subprocess.run(["git", "init", "-q", str(source_repo)], check=True)
            subprocess.run(["git", "-C", str(source_repo), "config", "user.email", "fixture@example.invalid"], check=True)
            subprocess.run(["git", "-C", str(source_repo), "config", "user.name", "Fixture"], check=True)
            (source_repo / "selected.txt").write_text("selected\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(source_repo), "add", "selected.txt"], check=True)
            subprocess.run(["git", "-C", str(source_repo), "commit", "-qm", "base"], check=True)
            subprocess.run(
                ["git", "-C", str(source_repo), "worktree", "add", "-q", "-b", "linked-preview-v11", str(linked)],
                check=True,
            )
            linked = linked.resolve()
            scope_path = fixture / "scope.json"
            scope = {
                "schema_version": 1, "kind": "agy-worker-provider-scope",
                "read": [{"path": "selected.txt", "kind": "file"}],
                "write": [{"path": "selected.txt", "kind": "file"}],
            }
            scope_raw = json.dumps(scope, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii") + b"\n"
            scope_path.write_bytes(scope_raw); scope_path.chmod(0o600)
            preview = subprocess.run(
                [
                    str(ROOT / "skills/agy-worker/runtime/agy-worker.sh"),
                    "transmission-preview", "--workdir", str(linked),
                    "--provider-scope", str(scope_path), "--format", "json",
                ],
                check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            approved = json.loads(preview.stdout)["transmission_sha256"]
            schema = fixture / "provider.json"; provider_schema(schema)
            scope_info = scope_path.stat()
            command = {
                "schema_version": 6, "kind": "agy-worker-dispatch-command", "job_id": "linked-preview-v11",
                "workdir": str(linked), "argv": ["agy", "--json-schema", str(schema), "--print", "task"],
                "agy_version": "1.1.22", "agy_version_observed": True,
                "idle_seconds": 2, "hard_seconds": 3, "max_seconds": 20, "notice_seconds": 3,
                "stage_dir": None, "stage_file": None, "child_umask": "022", "workflow": "task",
                "max_cycles": 2, "resume_prompt": "resume", "continue_prompt": "continue",
                "selection_path": None, "selection_sha256": None, "selection_identity": None,
                "provider_env": [], "provider_scope_path": str(scope_path),
                "provider_scope_sha256": hashlib.sha256(scope_raw).hexdigest(),
                "provider_scope_identity": list(MODULE._identity(scope_info)),
                "approved_transmission_sha256": approved,
            }
            job = fixture / "job"; job.mkdir(mode=0o700)
            state = MODULE.initial_state(
                command, "initial", 1, command_sha="0" * 64,
                command_identity=(1, 2, 3, 4, 5), stage_sha=None, stage_identity=None,
                schema_bindings=MODULE._schema_bindings(command),
            )
            assert state["transmission_sha256"] == approved
            rebound_command, rebound_state = MODULE._bound_lifecycle_inputs(job, state, command)
            assert rebound_command is command and rebound_state is state

            mismatched = dict(command); mismatched["approved_transmission_sha256"] = "0" * 64
            try:
                MODULE.initial_state(
                    mismatched, "initial", 1, command_sha="0" * 64,
                    command_identity=(1, 2, 3, 4, 5), stage_sha=None, stage_identity=None,
                    schema_bindings=MODULE._schema_bindings(mismatched),
                )
            except MODULE.DispatchError as exc:
                assert str(exc) == "approved transmission SHA does not match current worktree scope"
            else:
                raise AssertionError("V11 initial state accepted a mismatched preview approval")
            drifted = dict(state); drifted["transmission_sha256"] = "0" * 64
            try:
                MODULE._bound_lifecycle_inputs(job, drifted, command)
            except MODULE.DispatchError as exc:
                assert str(exc) == "worktree scope transmission binding changed"
            else:
                raise AssertionError("prelaunch lifecycle accepted a mismatched transmission binding")
        finally:
            if linked.exists():
                subprocess.run(["git", "-C", str(source_repo), "worktree", "remove", "--force", str(linked)], check=True)
            shutil.rmtree(fixture)

    check("linked preview SHA authorizes exact V11 initial and prelaunch scope binding", linked_preview_authorizes_v11_initial_and_prelaunch_binding)

    def materialization_failure_removes_partial_private_stage() -> None:
        """A mid-copy failure cannot leave selected bytes in a partial stage."""
        fixture = root / "materialize-mid-write"; fixture.mkdir()
        source = fixture / "source"; source.mkdir()
        (source / "selected.txt").write_bytes(b"selected-private-bytes\n")
        stage = fixture / "stage"
        scope = {
            "schema_version": 1, "kind": "agy-worker-provider-scope",
            "read": [{"path": "selected.txt", "kind": "file"}],
            "write": [{"path": "selected.txt", "kind": "file"}],
        }
        selected = MODULE._build_selected_content_manifest(source, scope)
        original_write = MODULE.os.write
        writes = 0
        def fail_after_partial_write(descriptor, payload):
            nonlocal writes
            writes += 1
            if writes == 1:
                return original_write(descriptor, bytes(payload[:3]))
            raise OSError("injected mid-write failure")
        MODULE.os.write = fail_after_partial_write
        try:
            try:
                MODULE._materialize_stage(source, stage, scope, selected)
            except OSError as exc:
                assert str(exc) == "injected mid-write failure"
            else:
                raise AssertionError("materialization accepted an injected mid-write failure")
        finally:
            MODULE.os.write = original_write
        assert writes == 2
        assert not stage.exists()
        assert list(fixture.rglob("selected-private-bytes")) == []
        shutil.rmtree(fixture)

    check("mid-write materialization failure removes the partial private stage", materialization_failure_removes_partial_private_stage)

    def pre_chmod_failure_removes_bound_stage() -> None:
        """The first post-identity chmod failure still removes the exact stage."""
        fixture = root / "materialize-pre-chmod"; fixture.mkdir()
        source = fixture / "source"; source.mkdir()
        (source / "selected.txt").write_bytes(b"selected-private-bytes\n")
        stage = fixture / "stage"
        scope = {
            "schema_version": 1, "kind": "agy-worker-provider-scope",
            "read": [{"path": "selected.txt", "kind": "file"}],
            "write": [{"path": "selected.txt", "kind": "file"}],
        }
        selected = MODULE._build_selected_content_manifest(source, scope)
        original_fchmod = MODULE.os.fchmod
        def fail_stage_chmod(_descriptor, _mode):
            raise OSError("injected pre-chmod failure")
        MODULE.os.fchmod = fail_stage_chmod
        try:
            try:
                MODULE._materialize_stage(source, stage, scope, selected)
            except OSError as exc:
                assert str(exc) == "injected pre-chmod failure"
            else:
                raise AssertionError("materialization accepted an injected pre-chmod failure")
        finally:
            MODULE.os.fchmod = original_fchmod
        assert not stage.exists()
        shutil.rmtree(fixture)

    check("pre-chmod failure removes only the bound own-created stage", pre_chmod_failure_removes_bound_stage)

    def pre_lstat_failure_and_replacement_are_fail_closed() -> None:
        """A bound stage is cleaned, but a replacement at its path is preserved."""
        fixture = root / "materialize-pre-lstat"; fixture.mkdir()
        source = fixture / "source"; source.mkdir()
        (source / "selected.txt").write_bytes(b"selected-private-bytes\n")
        scope = {
            "schema_version": 1, "kind": "agy-worker-provider-scope",
            "read": [{"path": "selected.txt", "kind": "file"}],
            "write": [{"path": "selected.txt", "kind": "file"}],
        }
        selected = MODULE._build_selected_content_manifest(source, scope)
        original_lstat = MODULE.os.lstat

        stage = fixture / "stage-lstat-failure"
        failed_once = False
        def fail_first_stage_lstat(path):
            nonlocal failed_once
            if str(path) == str(stage) and not failed_once:
                failed_once = True
                raise OSError("injected pre-lstat failure")
            return original_lstat(path)
        MODULE.os.lstat = fail_first_stage_lstat
        try:
            try:
                MODULE._materialize_stage(source, stage, scope, selected)
            except OSError as exc:
                assert str(exc) == "injected pre-lstat failure"
            else:
                raise AssertionError("materialization accepted an injected pre-lstat failure")
        finally:
            MODULE.os.lstat = original_lstat
        assert failed_once and not stage.exists()

        stage = fixture / "stage-replaced"
        displaced = fixture / "displaced-own-stage"
        replacement_created = False
        def replace_before_identity_validation(path):
            nonlocal replacement_created
            if str(path) == str(stage) and not replacement_created:
                replacement_created = True
                os.rename(stage, displaced)
                stage.mkdir(mode=0o700)
                (stage / "user-owned.txt").write_text("preserve\n", encoding="utf-8")
            return original_lstat(path)
        MODULE.os.lstat = replace_before_identity_validation
        try:
            try:
                MODULE._materialize_stage(source, stage, scope, selected)
            except MODULE.DispatchError as exc:
                assert str(exc) == "stage materialization failed and cleanup is uncertain"
                assert isinstance(exc.__cause__, MODULE.DispatchError)
                assert str(exc.__cause__) == "stage directory identity changed; cleanup refused"
            else:
                raise AssertionError("materialization removed or accepted a replacement stage path")
        finally:
            MODULE.os.lstat = original_lstat
        assert replacement_created
        assert (stage / "user-owned.txt").read_text(encoding="utf-8") == "preserve\n"
        assert displaced.is_dir()
        shutil.rmtree(fixture)

    check("pre-lstat cleanup is exact and preserves a replacement path", pre_lstat_failure_and_replacement_are_fail_closed)

    def cleanup_failure_is_visible_and_fail_closed() -> None:
        """An uncertain cleanup cannot be hidden behind the materialization error."""
        fixture = root / "materialize-cleanup-failure"; fixture.mkdir()
        source = fixture / "source"; source.mkdir()
        (source / "selected.txt").write_bytes(b"selected-private-bytes\n")
        stage = fixture / "stage"
        scope = {
            "schema_version": 1, "kind": "agy-worker-provider-scope",
            "read": [{"path": "selected.txt", "kind": "file"}],
            "write": [{"path": "selected.txt", "kind": "file"}],
        }
        selected = MODULE._build_selected_content_manifest(source, scope)
        original_fchmod = MODULE.os.fchmod
        original_cleanup = MODULE._cleanup_stage
        def fail_stage_chmod(_descriptor, _mode):
            raise OSError("injected pre-chmod failure")
        def fail_cleanup(_stage, _identity):
            raise OSError("injected cleanup failure")
        MODULE.os.fchmod = fail_stage_chmod
        MODULE._cleanup_stage = fail_cleanup
        try:
            try:
                MODULE._materialize_stage(source, stage, scope, selected)
            except MODULE.DispatchError as exc:
                assert str(exc) == "stage materialization failed and cleanup is uncertain"
                assert isinstance(exc.__cause__, OSError)
                assert str(exc.__cause__) == "injected cleanup failure"
            else:
                raise AssertionError("materialization hid an injected cleanup failure")
        finally:
            MODULE.os.fchmod = original_fchmod
            MODULE._cleanup_stage = original_cleanup
        assert stage.is_dir()
        shutil.rmtree(fixture)

    check("cleanup failure is visible and fail closed", cleanup_failure_is_visible_and_fail_closed)

    def narrow_stage_authorization_and_reconciliation_are_end_to_end() -> None:
        """Authorized stage edits reconcile; an unselected write fails closed."""
        fixture = root / "narrow-stage-reconcile"; fixture.mkdir()
        source = fixture / "source"; source.mkdir()
        (source / "selected.txt").write_text("before\n", encoding="utf-8")
        stage = fixture / "stage"
        job = fixture / "job"; job.mkdir(mode=0o700)
        scope = {
            "schema_version": 1, "kind": "agy-worker-provider-scope",
            "read": [{"path": "selected.txt", "kind": "file"}],
            "write": [{"path": "selected.txt", "kind": "file"}],
        }
        selected = MODULE._build_selected_content_manifest(source, scope)
        stage_identity, _stage_sha = MODULE._materialize_stage(source, stage, scope, selected)
        (stage / "selected.txt").write_text("after\n", encoding="utf-8")
        (stage / "unauthorized.txt").write_text("outside scope\n", encoding="utf-8")
        try:
            MODULE._scan_stage_mutations(stage, scope, selected)
        except MODULE.DispatchError as exc:
            assert str(exc) == "unauthorized creation of path: unauthorized.txt"
        else:
            raise AssertionError("stage mutation scan accepted an unselected write")
        (stage / "unauthorized.txt").unlink()
        operations, _operation_sha = MODULE._scan_stage_mutations(stage, scope, selected)
        assert [item["op"] for item in operations] == ["replace"]
        reconciliation_sha = MODULE._reconcile_stage_to_source(source, stage, operations, job)
        assert MODULE.SHA_RE.fullmatch(reconciliation_sha)
        assert (source / "selected.txt").read_text(encoding="utf-8") == "after\n"
        assert not list(job.iterdir())
        MODULE._cleanup_stage(stage, stage_identity)
        assert not stage.exists()
        shutil.rmtree(fixture)

    check("narrow stage rejects unselected writes and reconciles an authorized replacement", narrow_stage_authorization_and_reconciliation_are_end_to_end)

    def scoped_controller_fixture(label: str, behavior: str):
        """Create one exact V11 narrow-scope controller fixture with a fake provider."""
        source_repo = (root / f"scope-acceptance-source-{label}").resolve(); source_repo.mkdir()
        repo = (root / f"scope-acceptance-repo-{label}").resolve()
        subprocess.run(["git", "init", "-q", str(source_repo)], check=True)
        subprocess.run(["git", "-C", str(source_repo), "config", "user.email", "fixture@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(source_repo), "config", "user.name", "Fixture"], check=True)
        initial_payload = b"\x00initial-binary\xff\n"
        initial_tool = b"#!/bin/sh\nprintf 'initial\\n'\n"
        (source_repo / "payload.bin").write_bytes(initial_payload); (source_repo / "payload.bin").chmod(0o644)
        (source_repo / "tool.sh").write_bytes(initial_tool); (source_repo / "tool.sh").chmod(0o755)
        (source_repo / "denied-secret.txt").write_text("denied-private\n", encoding="utf-8")
        (source_repo / "omitted-private.txt").write_text("omitted-private\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(source_repo), "add", "."], check=True)
        subprocess.run(["git", "-C", str(source_repo), "commit", "-qm", "base"], check=True)
        subprocess.run([
            "git", "-C", str(source_repo), "worktree", "add", "-q", "-b",
            f"scope-acceptance-{label}", str(repo),
        ], check=True)

        scope = {
            "schema_version": 1, "kind": "agy-worker-provider-scope",
            "read": [
                {"path": "payload.bin", "kind": "file"},
                {"path": "tool.sh", "kind": "file"},
            ],
            "write": [
                {"path": "payload.bin", "kind": "file"},
                {"path": "tool.sh", "kind": "file"},
            ],
        }
        scope_raw = json.dumps(
            scope, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
        ).encode("ascii") + b"\n"
        scope_path = root / f"scope-acceptance-{label}.json"
        scope_path.write_bytes(scope_raw); scope_path.chmod(0o600)
        parsed_scope = MODULE._parse_provider_scope(scope_raw)
        readable_manifest = MODULE._scan_readable_worktree(str(repo))
        MODULE._validate_scope_against_worktree(parsed_scope, str(repo), readable_manifest)
        selected_manifest = MODULE._build_selected_content_manifest(repo, parsed_scope)
        approved = MODULE._compute_transmission_sha256(
            MODULE._canonical_digest(parsed_scope),
            MODULE._manifest_digest(readable_manifest),
            MODULE._selected_content_digest(selected_manifest),
        )

        job = (root / f"scope-acceptance-job-{label}").resolve(); job.mkdir(mode=0o700)
        bin_dir = root / f"scope-acceptance-bin-{label}"; bin_dir.mkdir()
        sentinel = root / f"scope-acceptance-provider-{label}.json"
        schema = root / f"scope-acceptance-provider-schema-{label}.json"; provider_schema(schema)
        events = [
            {"event": "init", "init": {}, "conversation_id": "scope-acceptance"},
            {
                "event": "result",
                "result": {
                    "conversation_id": "scope-acceptance", "status": "SUCCESS",
                    "structured_output": report(summary=f"scope-{behavior}"),
                },
            },
        ]
        fake = bin_dir / "agy"
        fake.write_text(
            "#!/usr/bin/env python3\n"
            "import base64, json, os, stat\n"
            "from pathlib import Path\n"
            f"sentinel = Path({str(sentinel)!r})\n"
            "cwd = Path.cwd()\n"
            "observation = {\n"
            "    'cwd': str(cwd),\n"
            "    'cwd_mode': stat.S_IMODE(cwd.stat().st_mode),\n"
            "    'git_marker_present': (cwd / '.git').exists(),\n"
            "    'gitmodules_present': (cwd / '.gitmodules').exists(),\n"
            "    'denied_present': (cwd / 'denied-secret.txt').exists(),\n"
            "    'omitted_present': (cwd / 'omitted-private.txt').exists(),\n"
            "    'payload_b64': base64.b64encode((cwd / 'payload.bin').read_bytes()).decode('ascii'),\n"
            "    'payload_mode': stat.S_IMODE((cwd / 'payload.bin').stat().st_mode),\n"
            "    'tool_b64': base64.b64encode((cwd / 'tool.sh').read_bytes()).decode('ascii'),\n"
            "    'tool_mode': stat.S_IMODE((cwd / 'tool.sh').stat().st_mode),\n"
            "}\n"
            "sentinel.write_text(json.dumps(observation, sort_keys=True), encoding='utf-8')\n"
            f"behavior = {behavior!r}\n"
            "if behavior == 'positive':\n"
            "    Path('payload.bin').write_bytes(b'\\x00reconciled-binary\\xfe\\xff\\n')\n"
            "    os.chmod('payload.bin', 0o700)\n"
            "    Path('tool.sh').write_bytes(b'#!/bin/sh\\nprintf \\\'reconciled\\\\n\\\'\\n')\n"
            "    os.chmod('tool.sh', 0o600)\n"
            "elif behavior == 'unauthorized':\n"
            "    Path('unauthorized.bin').write_bytes(b'not-authorized\\n')\n"
            "elif behavior == 'symlink':\n"
            "    os.symlink('payload.bin', 'unauthorized-link')\n"
            "elif behavior == 'fifo':\n"
            "    os.mkfifo('unauthorized-fifo', 0o600)\n"
            "elif behavior == 'source-drift':\n"
            f"    Path({str(repo / 'payload.bin')!r}).write_bytes(b'external-source-drift\\n')\n"
            "    Path('payload.bin').write_bytes(b'untrusted-stage-replacement\\n')\n"
            f"events = {events!r}\n"
            "for event in events:\n"
            "    print(json.dumps(event, ensure_ascii=True, separators=(',', ':')), flush=True)\n",
            encoding="utf-8",
        )
        fake.chmod(0o755)
        scope_info = scope_path.stat()
        command = {
            "schema_version": 6, "kind": "agy-worker-dispatch-command",
            "job_id": f"scope-acceptance-{label}", "workdir": str(repo),
            "argv": ["agy", "--json-schema", str(schema), "--print", "task"],
            "agy_version": "1.1.22", "agy_version_observed": True,
            "idle_seconds": 2, "hard_seconds": 5, "max_seconds": 20, "notice_seconds": 3,
            "stage_dir": None, "stage_file": None, "child_umask": "022", "workflow": "task",
            "max_cycles": 2, "resume_prompt": "resume", "continue_prompt": "continue",
            "selection_path": None, "selection_sha256": None, "selection_identity": None,
            "provider_env": [], "provider_scope_path": str(scope_path),
            "provider_scope_sha256": hashlib.sha256(scope_raw).hexdigest(),
            "provider_scope_identity": list(MODULE._identity(scope_info)),
            "approved_transmission_sha256": approved,
        }
        MODULE.write_atomic(job, MODULE.COMMAND_NAME, command)
        MODULE.create_state(job, "initial", resume=False)
        return repo, job, bin_dir, sentinel, initial_payload, initial_tool

    def narrow_provider_launch_is_gitless_and_reconciles_exact_bytes_and_modes() -> None:
        """The provider sees only selected bytes in a Gitless stage and exact edits reconcile."""
        repo, job, bin_dir, sentinel, initial_payload, initial_tool = scoped_controller_fixture(
            "positive", "positive",
        )
        assert run_controller(job, bin_dir) == 0
        observed = json.loads(sentinel.read_text(encoding="utf-8"))
        stage_path = Path(observed["cwd"])
        assert stage_path.parent == job and stage_path.name == "stage-001"
        assert observed["cwd"] != str(repo) and observed["cwd_mode"] == 0o700
        assert not observed["git_marker_present"] and not observed["gitmodules_present"]
        assert not observed["denied_present"] and not observed["omitted_present"]
        assert base64.b64decode(observed["payload_b64"]) == initial_payload
        assert base64.b64decode(observed["tool_b64"]) == initial_tool
        assert observed["payload_mode"] == 0o600 and observed["tool_mode"] == 0o700
        assert (repo / "payload.bin").read_bytes() == b"\x00reconciled-binary\xfe\xff\n"
        assert (repo / "tool.sh").read_bytes() == b"#!/bin/sh\nprintf 'reconciled\\n'\n"
        assert stat.S_IMODE((repo / "payload.bin").stat().st_mode) == 0o700
        assert stat.S_IMODE((repo / "tool.sh").stat().st_mode) == 0o600
        state, _raw, _sha = MODULE.load_state(job)
        assert state["status"] == "succeeded" and state["reconciliation_manifest_sha256"] is not None
        assert state["provider_stage_path"] == str(stage_path) and not stage_path.exists()

    check("narrow provider launch uses a Gitless selected-only stage and reconciles exact binary modes", narrow_provider_launch_is_gitless_and_reconciles_exact_bytes_and_modes)

    def narrow_postlaunch_unauthorized_symlink_and_source_drift_fail_closed() -> None:
        """Unapproved stage paths and concurrent source changes cannot reconcile."""
        for behavior in ("unauthorized", "symlink", "fifo"):
            repo, job, bin_dir, sentinel, initial_payload, initial_tool = scoped_controller_fixture(
                behavior, behavior,
            )
            assert run_controller(job, bin_dir) == MODULE.EXIT_BY_REASON["status_unavailable"]
            assert sentinel.exists(), f"{behavior} fixture did not reach the provider"
            state, _raw, _sha = MODULE.load_state(job)
            assert (state["status"], state["reason"], state["failure_stage"]) == (
                "failed", "status_unavailable", "binding_failure",
            )
            assert (repo / "payload.bin").read_bytes() == initial_payload
            assert (repo / "tool.sh").read_bytes() == initial_tool
            assert not os.path.lexists(repo / "unauthorized.bin")
            assert not os.path.lexists(repo / "unauthorized-link")
            assert not os.path.lexists(repo / "unauthorized-fifo")
            stage_path = Path(state["provider_stage_path"])
            if behavior == "symlink":
                retained = stage_path / "unauthorized-link"
                assert stage_path.is_dir() and os.path.lexists(retained)
                assert retained.is_symlink()
            else:
                assert not stage_path.exists()

        repo, job, bin_dir, sentinel, _initial_payload, initial_tool = scoped_controller_fixture(
            "source-drift", "source-drift",
        )
        assert run_controller(job, bin_dir) == MODULE.EXIT_BY_REASON["status_unavailable"]
        assert sentinel.exists()
        state, _raw, _sha = MODULE.load_state(job)
        assert (state["status"], state["reason"], state["failure_stage"]) == (
            "failed", "status_unavailable", "binding_failure",
        )
        assert (repo / "payload.bin").read_bytes() == b"external-source-drift\n"
        assert (repo / "payload.bin").read_bytes() != b"untrusted-stage-replacement\n"
        assert (repo / "tool.sh").read_bytes() == initial_tool
        assert not Path(state["provider_stage_path"]).exists()

    check("narrow reconciliation rejects unauthorized paths symlinks and concurrent source drift", narrow_postlaunch_unauthorized_symlink_and_source_drift_fail_closed)

    def narrow_preflight_symlink_race_and_copy_drift_never_launch_provider() -> None:
        """Every scoped preflight failure leaves the provider sentinel untouched."""
        repo, job, bin_dir, sentinel, _initial_payload, _initial_tool = scoped_controller_fixture(
            "preflight-symlink", "positive",
        )
        (repo / "denied-secret.txt").unlink()
        (repo / "denied-secret.txt").symlink_to(repo / "omitted-private.txt")
        assert run_controller(job, bin_dir) == MODULE.EXIT_BY_REASON["status_unavailable"]
        assert not sentinel.exists()

        repo, job, bin_dir, sentinel, _initial_payload, _initial_tool = scoped_controller_fixture(
            "stage-race", "positive",
        )
        attacker_stage = job / "stage-001"; attacker_stage.mkdir(mode=0o700)
        (attacker_stage / "attacker-owned.txt").write_text("preserve\n", encoding="utf-8")
        assert run_controller(job, bin_dir) == MODULE.EXIT_BY_REASON["status_unavailable"]
        assert not sentinel.exists()
        assert (attacker_stage / "attacker-owned.txt").read_text(encoding="utf-8") == "preserve\n"

        repo, job, bin_dir, sentinel, _initial_payload, _initial_tool = scoped_controller_fixture(
            "copy-drift", "positive",
        )
        original_materialize = MODULE._materialize_stage
        injected = False
        def drift_immediately_before_copy(source_root, stage_dir, scope, selected_manifest):
            nonlocal injected
            injected = True
            (Path(source_root) / "payload.bin").write_bytes(b"copy-window-source-drift\n")
            return original_materialize(source_root, stage_dir, scope, selected_manifest)
        MODULE._materialize_stage = drift_immediately_before_copy
        try:
            assert run_controller(job, bin_dir) == MODULE.EXIT_BY_REASON["status_unavailable"]
        finally:
            MODULE._materialize_stage = original_materialize
        assert injected and not sentinel.exists()
        assert (repo / "payload.bin").read_bytes() == b"copy-window-source-drift\n"
        state, _raw, _sha = MODULE.load_state(job)
        assert (state["status"], state["reason"]) == ("failed", "status_unavailable")
        assert not (job / "stage-001").exists()

    check("narrow preflight symlink stage-race and copy-drift failures never launch provider", narrow_preflight_symlink_race_and_copy_drift_never_launch_provider)

    def deleted_nested_directories_are_restored_after_later_failure() -> None:
        """Deleting an empty child before failure cannot produce a false rollback."""
        fixture = root / "nested-directory-rollback"; fixture.mkdir()
        source = fixture / "source"; source.mkdir()
        nested = source / "empty" / "inner"; nested.mkdir(parents=True)
        (source / "selected.txt").write_text("before rollback\n", encoding="utf-8")
        (source / "empty").chmod(0o750); nested.chmod(0o710)
        stage = fixture / "stage"
        job = fixture / "job"; job.mkdir(mode=0o700)
        scope = {
            "schema_version": 1, "kind": "agy-worker-provider-scope",
            "read": [
                {"path": "empty", "kind": "tree"},
                {"path": "selected.txt", "kind": "file"},
            ],
            "write": [
                {"path": "empty", "kind": "tree"},
                {"path": "selected.txt", "kind": "file"},
            ],
        }
        selected = MODULE._build_selected_content_manifest(source, scope)
        MODULE._materialize_stage(source, stage, scope, selected)
        (stage / "selected.txt").write_text("must roll back\n", encoding="utf-8")
        (stage / "empty" / "inner").rmdir(); (stage / "empty").rmdir()
        operations, _operation_sha = MODULE._scan_stage_mutations(stage, scope, selected)
        assert [item["path"] for item in operations] == ["selected.txt", "empty/inner", "empty"]
        original_rmdir = MODULE.os.rmdir
        injected = False
        def fail_after_child_delete(name, *args, **kwargs):
            nonlocal injected
            result = original_rmdir(name, *args, **kwargs)
            if name == "inner" and not injected:
                injected = True
                raise OSError("injected after child directory deletion")
            return result
        MODULE.os.rmdir = fail_after_child_delete
        try:
            try:
                MODULE._reconcile_stage_to_source(source, stage, operations, job)
            except MODULE.DispatchError as exc:
                assert str(exc).startswith("reconciliation failed and was rolled back:")
            else:
                raise AssertionError("injected post-delete failure was accepted")
        finally:
            MODULE.os.rmdir = original_rmdir
        assert injected and (source / "empty" / "inner").is_dir()
        assert stat.S_IMODE((source / "empty").stat().st_mode) == 0o750
        assert stat.S_IMODE((source / "empty" / "inner").stat().st_mode) == 0o710
        assert (source / "selected.txt").read_text(encoding="utf-8") == "before rollback\n"
        assert not list(job.iterdir())
        assert MODULE._recover_reconciliation(source, job) is False
        shutil.rmtree(fixture)

    check("later failure recreates deleted nested empty directories before clearing the ledger", deleted_nested_directories_are_restored_after_later_failure)

    def interrupted_empty_directory_recovery_is_durable_and_idempotent() -> None:
        """A crash after rmdir leaves a bound ledger that one recovery consumes."""
        fixture = root / "interrupted-directory-recovery"; fixture.mkdir()
        source = fixture / "source"; source.mkdir()
        empty = source / "empty"; empty.mkdir(); empty.chmod(0o751)
        stage = fixture / "stage"
        job = fixture / "job"; job.mkdir(mode=0o700)
        scope = {
            "schema_version": 1, "kind": "agy-worker-provider-scope",
            "read": [{"path": "empty", "kind": "tree"}],
            "write": [{"path": "empty", "kind": "tree"}],
        }
        selected = MODULE._build_selected_content_manifest(source, scope)
        MODULE._materialize_stage(source, stage, scope, selected)
        (stage / "empty").rmdir()
        operations, _operation_sha = MODULE._scan_stage_mutations(stage, scope, selected)
        original_rmdir = MODULE.os.rmdir
        interrupted = False
        def interrupt_after_delete(name, *args, **kwargs):
            nonlocal interrupted
            result = original_rmdir(name, *args, **kwargs)
            if name == "empty" and not interrupted:
                interrupted = True
                raise KeyboardInterrupt("injected reconciliation interruption")
            return result
        MODULE.os.rmdir = interrupt_after_delete
        try:
            try:
                MODULE._reconcile_stage_to_source(source, stage, operations, job)
            except KeyboardInterrupt:
                pass
            else:
                raise AssertionError("injected interruption did not escape reconciliation")
        finally:
            MODULE.os.rmdir = original_rmdir
        assert interrupted and not empty.exists()
        marker = json.loads((job / "reconciliation-in-progress.json").read_text(encoding="utf-8"))
        assert marker["schema_version"] == 1
        assert marker["directory_backups"]["empty"]["mode"] == 0o751
        assert len(marker["directory_backups"]["empty"]["prior_identity"]) == 5
        assert MODULE._recover_reconciliation(source, job) is True
        assert empty.is_dir() and stat.S_IMODE(empty.stat().st_mode) == 0o751
        assert not list(job.iterdir())
        assert MODULE._recover_reconciliation(source, job) is False
        shutil.rmtree(fixture)

    check("interrupted empty-directory reconciliation recovers durably and idempotently", interrupted_empty_directory_recovery_is_durable_and_idempotent)

    def normal_standard_and_linked_worktrees_create_bound_v11_state() -> None:
        fixture = root / "bound-positive-controls"; fixture.mkdir()
        source_repo = fixture / "source"; source_repo.mkdir()
        linked = fixture / "linked-worktree"
        try:
            subprocess.run(["git", "init", "-q", str(source_repo)], check=True)
            subprocess.run(["git", "-C", str(source_repo), "config", "user.email", "fixture@example.invalid"], check=True)
            subprocess.run(["git", "-C", str(source_repo), "config", "user.name", "Fixture"], check=True)
            subprocess.run(["git", "-C", str(source_repo), "commit", "--allow-empty", "-qm", "base"], check=True)
            subprocess.run(
                ["git", "-C", str(source_repo), "worktree", "add", "-q", "-b", "bound-linked-worktree", str(linked)],
                check=True,
            )
            linked = linked.resolve()

            for label, worktree in (("standard", source_repo), ("linked", linked)):
                snapshot = MODULE._worktree_snapshot(str(worktree))
                assert snapshot is not None and snapshot["entries"] == 0, label
                job = fixture / f"{label}-job"; job.mkdir(mode=0o700)
                schema = fixture / f"{label}-provider.json"; provider_schema(schema)
                command = {
                    "schema_version": 3, "kind": "agy-worker-dispatch-command", "job_id": f"bound-{label}",
                    "workdir": str(worktree), "argv": ["agy", "--json-schema", str(schema), "--print", "task"],
                    "agy_version": "1.1.16", "agy_version_observed": True,
                    "idle_seconds": 2, "hard_seconds": 3, "max_seconds": 20, "notice_seconds": 3,
                    "stage_dir": None, "stage_file": None, "child_umask": "022", "workflow": "task",
                    "max_cycles": 2, "resume_prompt": "resume", "continue_prompt": "continue",
                }
                MODULE.write_atomic(job, MODULE.COMMAND_NAME, command)
                state, _state_sha = MODULE.create_state(job, "initial", resume=False)
                persisted = json.loads((job / MODULE.STATE_NAME).read_text(encoding="utf-8"))
                assert state["schema_version"] == persisted["schema_version"] == MODULE.CURRENT_STATE_SCHEMA == 11, label
                assert state["worktree_snapshot_algorithm"] == MODULE.CURRENT_WORKTREE_SNAPSHOT_ALGORITHM, label
                assert state["worktree_baseline"] is not None, label
                assert state["worktree_root_identity"] is not None, label
                assert (job / MODULE.STATE_NAME).is_file(), label
        finally:
            if linked.exists():
                subprocess.run(["git", "-C", str(source_repo), "worktree", "remove", "--force", str(linked)], check=True)
            shutil.rmtree(fixture)
        git_plumbing_var_aliases_preserve_only_direct_standard_and_linked_boundaries()

    def git_plumbing_var_aliases_preserve_only_direct_standard_and_linked_boundaries() -> None:
        """A documented /var spelling cannot widen Git-directory authority."""
        fixture = root / "git-plumbing-var-aliases"; fixture.mkdir()
        source_repo = fixture / "source"; source_repo.mkdir()
        linked = fixture / "linked"
        other = fixture / "other"; other.mkdir()
        try:
            for repo in (source_repo, other):
                subprocess.run(["git", "init", "-q", str(repo)], check=True)
                subprocess.run(["git", "-C", str(repo), "config", "user.email", "fixture@example.invalid"], check=True)
                subprocess.run(["git", "-C", str(repo), "config", "user.name", "Fixture"], check=True)
                subprocess.run(["git", "-C", str(repo), "commit", "--allow-empty", "-qm", "base"], check=True)
            subprocess.run(
                ["git", "-C", str(source_repo), "worktree", "add", "-q", "-b", "var-alias-linked", str(linked)],
                check=True,
            )
            linked = linked.resolve()
            direct_reader = MODULE._WORKTREE_HELPER._IMPLEMENTATION_DEFAULTS["_bounded_git_read"]
            facade_reader = MODULE._bounded_git_read

            def with_reader(reader, action):
                MODULE._bounded_git_read = reader
                try:
                    return action()
                finally:
                    MODULE._bounded_git_read = facade_reader

            aliases: set[tuple[str, ...]] = set()
            def var_alias_reader(*args, **kwargs):
                result = direct_reader(*args, **kwargs)
                if result is not None and result[1].startswith(b"/private/var/"):
                    aliases.add(tuple(args[3]))
                    return result[0], b"/var/" + result[1][len(b"/private/var/"):]
                return result

            for label, worktree in (("standard", source_repo), ("linked", linked)):
                assert with_reader(var_alias_reader, lambda: MODULE._git_boundary_identity(str(worktree))) is not None, label
                assert with_reader(var_alias_reader, lambda: MODULE._worktree_snapshot(str(worktree))) is not None, label
            assert {("rev-parse", "--show-toplevel"), ("rev-parse", "--absolute-git-dir")} <= aliases
            assert ("rev-parse", "--git-common-dir") in aliases

            git_link = fixture / "git-dir-link"; git_link.symlink_to(source_repo / ".git", target_is_directory=True)
            common_link = fixture / "common-dir-link"; common_link.symlink_to(source_repo / ".git", target_is_directory=True)
            def replaced_reader(arguments, replacement):
                def reader(*args, **kwargs):
                    result = direct_reader(*args, **kwargs)
                    return (result[0], replacement) if result is not None and tuple(args[3]) == arguments else result
                return reader

            for worktree, arguments, replacement in (
                (source_repo, ("rev-parse", "--absolute-git-dir"), os.fsencode(git_link) + b"\n"),
                (linked, ("rev-parse", "--git-common-dir"), os.fsencode(common_link) + b"\n"),
                (source_repo, ("rev-parse", "--show-toplevel"), os.fsencode(other) + b"\n"),
                (source_repo, ("rev-parse", "--absolute-git-dir"), b"/tmp/has\0nul\n"),
            ):
                reader = replaced_reader(arguments, replacement)
                assert with_reader(reader, lambda: MODULE._git_boundary_identity(str(worktree))) is None
                assert with_reader(reader, lambda: MODULE._worktree_snapshot(str(worktree))) is None
        finally:
            if linked.exists():
                subprocess.run(["git", "-C", str(source_repo), "worktree", "remove", "--force", str(linked)], check=True)
            shutil.rmtree(fixture)

    # Keep the established current dispatch-state case as the inventory owner: this
    # is its plumbing-alias integration branch, not a new suite count.
    check("normal standard and linked worktrees persist a bound V11 dispatch state", normal_standard_and_linked_worktrees_create_bound_v11_state)

    def extracted_worktree_facade_preserves_all_signatures_and_patch_seams() -> None:
        """The split keeps the old module surface and its intentional test seams."""
        import ast
        import inspect

        extracted = (
            "_marker_only_preflight",
            "_resolved_path_is_git_administration",
            "_worktree_symlink_boundary",
            "_worktree_git_admin_alias_boundary",
            "_project_boundary",
            "_safe_git_owner_mode",
            "_safe_git_executable",
            "_confirm_safe_git_executable",
            "_safe_git_is_outside_worktree",
            "_stable_git_authority",
            "_full_stat_binding",
            "_bound_git_worktree_root",
            "_fixed_git_read_argv",
            "_bounded_git_read",
            "_git_boundary_identity",
            "_worktree_snapshot",
        )
        facade_tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        helper_tree = ast.parse(WORKTREE_SOURCE.read_text(encoding="utf-8"))
        facade_nodes = {
            node.name: node for node in facade_tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        helper_nodes = {
            node.name: node for node in helper_tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        assert set(extracted) <= set(facade_nodes) & set(helper_nodes)
        assert len(extracted) == 16
        for name in extracted:
            assert ast.dump(facade_nodes[name].args, include_attributes=False) == ast.dump(
                helper_nodes[name].args, include_attributes=False,
            ), name
            assert inspect.signature(getattr(MODULE, name)) == inspect.signature(
                getattr(MODULE._WORKTREE_HELPER, name),
            ), name

        repo = root / "extracted-facade-seams"; repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.email", "fixture@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "Fixture"], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "--allow-empty", "-qm", "base"], check=True)
        baseline = MODULE._worktree_snapshot(str(repo))
        assert baseline is not None
        original_marker = MODULE._marker_only_preflight
        original_safe_git = MODULE._safe_git_executable
        try:
            MODULE._marker_only_preflight = lambda *_args, **_kwargs: False
            assert MODULE._worktree_snapshot(str(repo)) is None
            MODULE._marker_only_preflight = original_marker
            MODULE._safe_git_executable = lambda: None
            assert MODULE._worktree_snapshot(str(repo)) is None
        finally:
            MODULE._marker_only_preflight = original_marker
            MODULE._safe_git_executable = original_safe_git

    def nested_git_entries_fail_closed_without_opening_them() -> None:
        """Only the root marker may be bound; nested markers are never content."""
        source_repo = root / "nested-git-source"; source_repo.mkdir()
        subprocess.run(["git", "init", "-q", str(source_repo)], check=True)
        subprocess.run(["git", "-C", str(source_repo), "config", "user.email", "fixture@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(source_repo), "config", "user.name", "Fixture"], check=True)
        subprocess.run(["git", "-C", str(source_repo), "commit", "--allow-empty", "-qm", "base"], check=True)
        linked = root / "nested-git-linked"
        subprocess.run(["git", "-C", str(source_repo), "worktree", "add", "-q", "-b", "nested-git-linked", str(linked)], check=True)
        linked = linked.resolve()
        # A normal checkout binds its directory marker; the project-only
        # linked checkout separately binds its marker file.
        assert MODULE._worktree_snapshot(str(source_repo)) is not None
        assert MODULE._worktree_snapshot(str(linked)) is not None
        assert MODULE._project_boundary(str(linked))["kind"] == "file"

        for kind in ("file", "directory", "symlink", "special"):
            nested = linked / f"nested-{kind}"; nested.mkdir()
            marker = nested / ".git"
            if kind == "file":
                marker.write_text("not a root marker\n", encoding="utf-8")
            elif kind == "directory":
                marker.mkdir(); (marker / "secret").write_text("do not read\n", encoding="utf-8")
            elif kind == "symlink":
                marker.symlink_to(root / "outside-nested-git-marker")
            else:
                os.mkfifo(marker)

            parent_info = os.stat(nested)
            denied: list[str] = []
            original_open = MODULE.os.open
            def no_nested_marker_open(name, flags, mode=0o777, *, dir_fd=None):
                if (
                    name == ".git" and dir_fd is not None
                    and os.fstat(dir_fd).st_dev == parent_info.st_dev
                    and os.fstat(dir_fd).st_ino == parent_info.st_ino
                ):
                    denied.append(kind)
                    raise AssertionError("nested Git marker was opened")
                return original_open(name, flags, mode, dir_fd=dir_fd)
            MODULE.os.open = no_nested_marker_open
            try:
                assert MODULE._worktree_snapshot(str(linked)) is None, kind
            finally:
                MODULE.os.open = original_open
            assert not denied, kind
            try:
                MODULE._project_boundary(str(linked))
            except MODULE.DispatchError as exc:
                assert str(exc) == "project worktree has nested Git administration"
            else:
                raise AssertionError(f"nested {kind} marker passed the project boundary")
            if marker.is_dir() and not marker.is_symlink():
                shutil.rmtree(marker)
            else:
                marker.unlink()
            nested.rmdir()

        job, state, _sha, _envelope = current_candidate_fixture("nested-git-actions")
        candidate_repo = Path(json.loads((job / MODULE.COMMAND_NAME).read_text(encoding="utf-8"))["workdir"])
        nested = candidate_repo / "nested"; nested.mkdir()
        (nested / ".git").write_text("nested authority\n", encoding="utf-8")
        candidate_snapshot_actions_reject(
            job, state, "nested-git",
            continuation_error="dispatch worktree root binding changed",
        )

        preflight_repo = root / "nested-git-preflight"; preflight_repo.mkdir()
        subprocess.run(["git", "init", "-q", str(preflight_repo)], check=True)
        (preflight_repo / "nested").mkdir()
        (preflight_repo / "nested" / ".git").write_text("nested authority\n", encoding="utf-8")
        preflight_job = root / "nested-git-preflight-job"; preflight_job.mkdir(mode=0o700)
        schema = root / "nested-git-preflight-provider.json"; provider_schema(schema)
        command = {
            "schema_version": 3, "kind": "agy-worker-dispatch-command", "job_id": "nested-git-preflight",
            "workdir": str(preflight_repo), "argv": ["agy", "--json-schema", str(schema), "--print", "task"],
            "agy_version": "1.1.16", "agy_version_observed": True,
            "idle_seconds": 2, "hard_seconds": 3, "max_seconds": 20, "notice_seconds": 3,
            "stage_dir": None, "stage_file": None, "child_umask": "022", "workflow": "task",
            "max_cycles": 2, "resume_prompt": "resume", "continue_prompt": "continue",
        }
        MODULE.write_atomic(preflight_job, MODULE.COMMAND_NAME, command)
        try:
            MODULE.create_state(preflight_job, "initial", resume=False)
        except MODULE.DispatchError as exc:
            assert str(exc) == "dispatch worktree root cannot be bound"
        else:
            raise AssertionError("nested Git marker created a dispatch state")
        assert not (preflight_job / MODULE.STATE_NAME).exists()
        stream_path, stderr_path, envelope_path = MODULE._attempt_paths(preflight_job, 1)
        assert not stream_path.exists() and not stderr_path.exists() and not envelope_path.exists()
        popen_calls: list[object] = []
        original_popen = MODULE.subprocess.Popen
        def no_controller_or_provider_process(*arguments, **keywords):
            del keywords
            popen_calls.append(arguments[0] if arguments else None)
            raise AssertionError("nested Git preflight reached a controller/provider process")
        MODULE.subprocess.Popen = no_controller_or_provider_process
        try:
            try:
                run_controller(preflight_job, root)
            except MODULE.DispatchError as exc:
                assert str(exc) == "dispatch state is unavailable"
            else:
                raise AssertionError("nested Git preflight unexpectedly entered the controller")
        finally:
            MODULE.subprocess.Popen = original_popen
        assert not popen_calls
        assert not stream_path.exists() and not stderr_path.exists() and not envelope_path.exists()

    check("nested .git file directory symlink and special entries fail before snapshot, boundary, actions, or provider", nested_git_entries_fail_closed_without_opening_them)

    def casefold_nested_git_aliases_fail_before_git_or_project_actions() -> None:
        """A Darwin-style ``.GIT`` alias is an authority marker, never content."""
        source_repo = root / "casefold-nested-git-source"; source_repo.mkdir()
        subprocess.run(["git", "init", "-q", str(source_repo)], check=True)
        subprocess.run(["git", "-C", str(source_repo), "config", "user.email", "fixture@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(source_repo), "config", "user.name", "Fixture"], check=True)
        subprocess.run(["git", "-C", str(source_repo), "commit", "--allow-empty", "-qm", "base"], check=True)
        linked = root / "casefold-nested-git-linked"
        subprocess.run(
            ["git", "-C", str(source_repo), "worktree", "add", "-q", "-b", "casefold-nested-git-linked", str(linked)],
            check=True,
        )
        linked = linked.resolve()
        # Exact normal and linked root markers remain positive controls.
        assert MODULE._worktree_snapshot(str(source_repo)) is not None
        assert MODULE._worktree_snapshot(str(linked)) is not None
        assert MODULE._project_boundary(str(linked))["kind"] == "file"

        nested = linked / "nested"; nested.mkdir()
        alias = nested / ".GIT"; alias.write_text("nested authority\n", encoding="utf-8")
        calls: list[object] = []
        original_popen = MODULE.subprocess.Popen
        def no_git_process(*arguments, **keywords):
            del keywords
            calls.append(arguments[0] if arguments else None)
            raise AssertionError("casefold nested marker reached a Git subprocess")
        MODULE.subprocess.Popen = no_git_process
        try:
            assert MODULE._worktree_snapshot(str(linked)) is None
        finally:
            MODULE.subprocess.Popen = original_popen
        assert not calls
        try:
            MODULE._project_boundary(str(linked))
        except MODULE.DispatchError as exc:
            assert str(exc) == "project worktree has nested Git administration"
        else:
            raise AssertionError("casefold nested Git marker passed the project boundary")

        # Result, continue, and finalize share the snapshot guard.  Under the
        # vulnerable exact-name implementation this first observation succeeds,
        # so a forged candidate can authorize all three actions.
        job, state, _sha, _envelope = current_candidate_fixture("casefold-nested-git-actions")
        candidate_repo = Path(json.loads((job / MODULE.COMMAND_NAME).read_text(encoding="utf-8"))["workdir"])
        candidate_nested = candidate_repo / "nested"; candidate_nested.mkdir()
        (candidate_nested / ".GIT").write_text("nested authority\n", encoding="utf-8")
        vulnerable_snapshot = MODULE._worktree_snapshot(str(candidate_repo))
        if vulnerable_snapshot is not None:
            state.update({
                "worktree_baseline": vulnerable_snapshot,
                "candidate_worktree_sha256": vulnerable_snapshot["sha256"],
                "candidate_worktree_entries": vulnerable_snapshot["entries"],
            })
        candidate_snapshot_actions_reject(
            job, state, "casefold-nested-git",
            continuation_error="dispatch worktree root binding changed",
        )

        # The initial controller path must reject before the fake provider can
        # run; this covers shell-created state as well as status/action reads.
        preflight_repo = root / "casefold-nested-git-preflight"; preflight_repo.mkdir()
        subprocess.run(["git", "init", "-q", str(preflight_repo)], check=True)
        (preflight_repo / "nested").mkdir()
        (preflight_repo / "nested" / ".GIT").write_text("nested authority\n", encoding="utf-8")
        preflight_job = root / "casefold-nested-git-preflight-job"; preflight_job.mkdir(mode=0o700)
        schema = root / "casefold-nested-git-preflight-provider.json"; provider_schema(schema)
        command = {
            "schema_version": 3, "kind": "agy-worker-dispatch-command", "job_id": "casefold-nested-git-preflight",
            "workdir": str(preflight_repo), "argv": ["agy", "--json-schema", str(schema), "--print", "task"],
            "agy_version": "1.1.16", "agy_version_observed": True,
            "idle_seconds": 2, "hard_seconds": 3, "max_seconds": 20, "notice_seconds": 3,
            "stage_dir": None, "stage_file": None, "child_umask": "022", "workflow": "task",
            "max_cycles": 2, "resume_prompt": "resume", "continue_prompt": "continue",
        }
        MODULE.write_atomic(preflight_job, MODULE.COMMAND_NAME, command)
        try:
            MODULE.create_state(preflight_job, "initial", resume=False)
        except MODULE.DispatchError as exc:
            assert str(exc) == "dispatch worktree root cannot be bound"
        else:
            raise AssertionError("casefold nested Git marker created a dispatch state")
        assert not (preflight_job / MODULE.STATE_NAME).exists()
        stream_path, stderr_path, envelope_path = MODULE._attempt_paths(preflight_job, 1)
        assert not stream_path.exists() and not stderr_path.exists() and not envelope_path.exists()
        popen_calls: list[object] = []
        original_popen = MODULE.subprocess.Popen
        def no_controller_or_provider_process(*arguments, **keywords):
            del keywords
            popen_calls.append(arguments[0] if arguments else None)
            raise AssertionError("casefold nested Git preflight reached a controller/provider process")
        MODULE.subprocess.Popen = no_controller_or_provider_process
        try:
            try:
                run_controller(preflight_job, root)
            except MODULE.DispatchError as exc:
                assert str(exc) == "dispatch state is unavailable"
            else:
                raise AssertionError("casefold nested Git preflight unexpectedly entered the controller")
        finally:
            MODULE.subprocess.Popen = original_popen
        assert not popen_calls
        assert not stream_path.exists() and not stderr_path.exists() and not envelope_path.exists()

    check("casefold nested .GIT aliases fail before Git, project, result, continue, finalize, or provider actions", casefold_nested_git_aliases_fail_before_git_or_project_actions)

    def resolve_undo_semantics_fail_closed_and_block_candidate_actions() -> None:
        """REUC is semantic index state even after a conflict is resolved and added."""
        def leave_resolve_undo(repo: Path) -> bytes:
            subprocess.run(["git", "-C", str(repo), "config", "user.email", "fixture@example.invalid"], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.name", "Fixture"], check=True)
            tracked = repo / "tracked.txt"; tracked.write_text("base\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "add", "tracked.txt"], check=True)
            subprocess.run(["git", "-C", str(repo), "commit", "-qm", "base"], check=True)
            primary = subprocess.run(
                ["git", "-C", str(repo), "branch", "--show-current"], check=True, stdout=subprocess.PIPE,
            ).stdout.decode("utf-8", "strict").strip()
            subprocess.run(["git", "-C", str(repo), "checkout", "-qb", "resolve-undo-side"], check=True)
            tracked.write_text("side\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "commit", "-am", "side", "-q"], check=True)
            subprocess.run(["git", "-C", str(repo), "checkout", "-q", primary], check=True)
            tracked.write_text("primary\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "commit", "-am", "primary", "-q"], check=True)
            merged = subprocess.run(
                ["git", "-C", str(repo), "merge", "resolve-undo-side"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            assert merged.returncode == 1
            tracked.write_text("resolved\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "add", "tracked.txt"], check=True)
            raw = subprocess.run(
                ["git", "-C", str(repo), "ls-files", "--resolve-undo", "-z"],
                check=True, stdout=subprocess.PIPE,
            ).stdout
            assert len(raw.split(b"\0")[:-1]) == 3, raw
            return raw

        repo = root / "resolve-undo-repro"; repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        raw = leave_resolve_undo(repo)
        object_length = len(raw.split(b" ", 2)[1])
        parsed = MODULE._parse_resolve_undo(raw, object_length)
        assert parsed is not None and len(parsed) == 3
        legacy_before = MODULE._worktree_snapshot(str(repo), legacy=True)
        assert legacy_before is not None
        assert MODULE._worktree_snapshot(str(repo)) is None
        staged_before = subprocess.run(
            ["git", "-C", str(repo), "ls-files", "--stage", "-z"], check=True, stdout=subprocess.PIPE,
        ).stdout
        bytes_before = (repo / "tracked.txt").read_bytes()
        subprocess.run(["git", "-C", str(repo), "update-index", "--clear-resolve-undo"], check=True)
        assert subprocess.run(
            ["git", "-C", str(repo), "ls-files", "--resolve-undo", "-z"], check=True, stdout=subprocess.PIPE,
        ).stdout == b""
        assert subprocess.run(
            ["git", "-C", str(repo), "ls-files", "--stage", "-z"], check=True, stdout=subprocess.PIPE,
        ).stdout == staged_before
        assert (repo / "tracked.txt").read_bytes() == bytes_before
        legacy_after = MODULE._worktree_snapshot(str(repo), legacy=True)
        semantic_after = MODULE._worktree_snapshot(str(repo))
        assert legacy_after is not None and legacy_after["sha256"] != legacy_before["sha256"]
        assert semantic_after is not None

        job, state, _sha, _envelope = current_candidate_fixture("resolve-undo")
        command = json.loads((job / MODULE.COMMAND_NAME).read_text(encoding="utf-8"))
        candidate_repo = Path(command["workdir"])
        leave_resolve_undo(candidate_repo)
        candidate_snapshot_actions_reject(job, state, "resolve-undo")

    check("resolve-undo state fails closed for v7 and result continue finalize parity while v6 remains exact", resolve_undo_semantics_fail_closed_and_block_candidate_actions)

    def malformed_duplicate_and_racing_resolve_undo_fail_closed() -> None:
        repo = root / "resolve-undo-shape-repo"; repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.email", "fixture@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "Fixture"], check=True)
        tracked = repo / "tracked.txt"; tracked.write_text("base\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "tracked.txt"], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-qm", "base"], check=True)
        oid = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", ":tracked.txt"], check=True, stdout=subprocess.PIPE,
        ).stdout.decode("ascii", "strict").strip()
        valid = f"100644 {oid} 1\ttracked.txt\0".encode("ascii")
        duplicate = valid + valid
        assert MODULE._parse_resolve_undo(b"", len(oid)) == {}
        assert MODULE._parse_resolve_undo(valid, len(oid)) == {(b"tracked.txt", 1): (0o100644, oid.encode("ascii"))}
        assert MODULE._parse_resolve_undo(b"malformed\0", len(oid)) is None
        assert MODULE._parse_resolve_undo(duplicate, len(oid)) is None

        real_git = shutil.which("git"); assert real_git is not None
        bin_dir = root / "resolve-undo-shape-bin"; bin_dir.mkdir()
        mode_file = root / "resolve-undo-mode"
        count_file = root / "resolve-undo-count"
        fake = bin_dir / "git"
        fake.write_text(
            "#!/bin/sh\n"
            "mode=$(cat " + shlex.quote(str(mode_file)) + ")\n"
            "case \" $* \" in *\" ls-files --resolve-undo -z \"*)\n"
            "  case \"$mode\" in\n"
            "    malformed) printf 'malformed\\0'; exit 0;;\n"
            "    duplicate) printf '100644 " + oid + " 1\\ttracked.txt\\0'; "
            "printf '100644 " + oid + " 1\\ttracked.txt\\0'; exit 0;;\n"
            "    race) count=0; if [ -r " + shlex.quote(str(count_file)) + " ]; then count=$(cat " + shlex.quote(str(count_file)) + "); fi; "
            "count=$((count + 1)); printf '%s\\n' \"$count\" > " + shlex.quote(str(count_file)) + "; "
            "if [ \"$count\" -gt 1 ]; then printf '100644 " + oid + " 1\\ttracked.txt\\0'; fi; exit 0;;\n"
            "  esac;;\n"
            "esac\n"
            "exec " + shlex.quote(real_git) + " \"$@\"\n",
            encoding="utf-8",
        )
        fake.chmod(0o755)
        previous_path = os.environ.get("PATH", "")
        os.environ["PATH"] = f"{bin_dir}{os.pathsep}{previous_path}"
        try:
            for mode in ("malformed", "duplicate", "race"):
                mode_file.write_text(mode, encoding="ascii")
                if count_file.exists(): count_file.unlink()
                assert MODULE._worktree_snapshot(str(repo)) is None, mode
                if mode == "race":
                    assert int(count_file.read_text(encoding="ascii")) > 1
        finally:
            os.environ["PATH"] = previous_path

    check("malformed duplicate and racing resolve-undo output fails closed", malformed_duplicate_and_racing_resolve_undo_fail_closed)

    def resolve_undo_observation_fails_closed_before_provider_launch() -> None:
        """A valid non-empty REUC observation immediately before launch yields resolve_undo_present."""
        repo = root / "resolve-undo-preflight-repo"; repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.email", "fixture@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "Fixture"], check=True)
        tracked = repo / "tracked.txt"; tracked.write_text("base\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "tracked.txt"], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-qm", "base"], check=True)
        primary = subprocess.run(
            ["git", "-C", str(repo), "branch", "--show-current"], check=True, stdout=subprocess.PIPE,
        ).stdout.decode("utf-8", "strict").strip()
        subprocess.run(["git", "-C", str(repo), "checkout", "-qb", "resolve-undo-side"], check=True)
        tracked.write_text("side\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "commit", "-am", "side", "-q"], check=True)
        subprocess.run(["git", "-C", str(repo), "checkout", "-q", primary], check=True)
        tracked.write_text("primary\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "commit", "-am", "primary", "-q"], check=True)
        merged = subprocess.run(
            ["git", "-C", str(repo), "merge", "resolve-undo-side"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        assert merged.returncode == 1
        tracked.write_text("resolved\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "tracked.txt"], check=True)
        raw_reuc = subprocess.run(
            ["git", "-C", str(repo), "ls-files", "--resolve-undo", "-z"],
            check=True, stdout=subprocess.PIPE,
        ).stdout
        assert len(raw_reuc.split(b"\0")[:-1]) == 3, raw_reuc

        # Positive case: valid non-empty REUC observation immediately before provider launch
        job = root / "resolve-undo-preflight-job"; job.mkdir(mode=0o700); job = job.resolve()
        bin_dir = root / "resolve-undo-preflight-bin"; bin_dir.mkdir()
        bound_provider = root / "resolve-undo-preflight-provider.json"; provider_schema(bound_provider)
        calls_log = root / "resolve-undo-calls.log"
        fake_agy = bin_dir / "agy"
        fake_agy.write_text(
            "#!/bin/sh\n"
            "printf 'provider called\\n' >> " + shlex.quote(str(calls_log)) + "\n"
            "exit 0\n",
            encoding="utf-8",
        )
        fake_agy.chmod(0o755)
        command = {
            "schema_version": 3, "kind": "agy-worker-dispatch-command", "job_id": "resolve-undo-preflight",
            "workdir": str(repo), "argv": ["agy", "--json-schema", str(bound_provider), "--print", "task"],
            "agy_version": "1.1.16", "agy_version_observed": True,
            "idle_seconds": 2, "hard_seconds": 3, "max_seconds": 20, "notice_seconds": 3,
            "stage_dir": None, "stage_file": None, "child_umask": "022", "workflow": "task",
            "max_cycles": 2, "resume_prompt": "resume", "continue_prompt": "continue",
        }
        MODULE.write_atomic(job, MODULE.COMMAND_NAME, command)
        state_init, _init_sha = MODULE.create_state(job, "initial", resume=False)
        assert state_init["status"] == "queued"
        assert state_init["worktree_baseline"] is None

        # Run controller immediately before provider launch
        exit_code = run_controller(job, bin_dir)
        assert exit_code == 20
        assert exit_code == MODULE.EXIT_BY_REASON["resolve_undo_present"]

        # Zero provider calls
        assert not calls_log.exists()

        # Terminal state validation
        terminal_state, raw_state, sha = MODULE.load_state(job)
        assert terminal_state["status"] == "failed"
        assert terminal_state["reason"] == "resolve_undo_present"
        assert terminal_state["exit_code"] == 20
        assert terminal_state["failure_stage"] == "binding_failure"
        assert terminal_state["agy_returncode"] == 20
        assert not terminal_state["candidate_recognized"]
        assert not terminal_state["result_available"]
        assert terminal_state["driver_disposition"] == "not_applicable"

        # Privacy verification: no task, path, OID, raw Git output, REUC bytes, or count exposed
        raw_state_text = raw_state.decode("utf-8", "surrogateescape")
        assert "tracked.txt" not in raw_state_text
        assert "resolve-undo-side" not in raw_state_text
        oid = raw_reuc.split(b" ", 2)[1].decode("ascii")
        assert oid not in raw_state_text
        public = MODULE.public_status(terminal_state, sha, job=job)
        assert public["reason"] == "resolve_undo_present"
        assert public["failure_stage"] == "binding_failure"
        assert public["exit_code"] == 20

        # Read-only verification: controller never mutates or clears Git resolve-undo metadata
        raw_after = subprocess.run(
            ["git", "-C", str(repo), "ls-files", "--resolve-undo", "-z"],
            check=True, stdout=subprocess.PIPE,
        ).stdout
        assert raw_after == raw_reuc

        # Negative cases: malformed, duplicate, and racing REUC fail closed as status_unavailable
        real_git = shutil.which("git"); assert real_git is not None
        neg_bin_dir = root / "resolve-undo-neg-bin"; neg_bin_dir.mkdir()
        neg_mode_file = root / "resolve-undo-neg-mode"
        neg_count_file = root / "resolve-undo-neg-count"
        fake_git = neg_bin_dir / "git"
        fake_git.write_text(
            "#!/bin/sh\n"
            "mode=$(cat " + shlex.quote(str(neg_mode_file)) + ")\n"
            "case \" $* \" in *\" ls-files --resolve-undo -z \"*)\n"
            "  case \"$mode\" in\n"
            "    malformed) printf 'malformed\\0'; exit 0;;\n"
            "    duplicate) printf '100644 " + oid + " 1\\ttracked.txt\\0'; "
            "printf '100644 " + oid + " 1\\ttracked.txt\\0'; exit 0;;\n"
            "    unavailable) exit 1;;\n"
            "    race) count=0; if [ -r " + shlex.quote(str(neg_count_file)) + " ]; then count=$(cat " + shlex.quote(str(neg_count_file)) + "); fi; "
            "count=$((count + 1)); printf '%s\\n' \"$count\" > " + shlex.quote(str(neg_count_file)) + "; "
            "if [ $((count % 2)) -eq 1 ]; then "
            "printf '100644 " + oid + " 1\\ttracked.txt\\0'; fi; exit 0;;\n"
            "  esac;;\n"
            "esac\n"
            "exec " + shlex.quote(real_git) + " \"$@\"\n",
            encoding="utf-8",
        )
        fake_git.chmod(0o755)
        previous_path = os.environ.get("PATH", "")
        os.environ["PATH"] = f"{neg_bin_dir}{os.pathsep}{previous_path}"
        try:
            for mode in ("malformed", "duplicate", "unavailable", "race"):
                neg_mode_file.write_text(mode, encoding="ascii")
                if neg_count_file.exists(): neg_count_file.unlink()
                neg_job = root / f"resolve-undo-neg-{mode}-job"; neg_job.mkdir(mode=0o700); neg_job = neg_job.resolve()
                neg_command = dict(command); neg_command["job_id"] = f"resolve-undo-neg-{mode}"
                MODULE.write_atomic(neg_job, MODULE.COMMAND_NAME, neg_command)
                MODULE.create_state(neg_job, "initial", resume=False)
                neg_exit = run_controller(neg_job, bin_dir)
                assert neg_exit == MODULE.EXIT_BY_REASON["status_unavailable"]
                neg_state, _raw, _sha = MODULE.load_state(neg_job)
                assert neg_state["status"] == "failed" and neg_state["reason"] == "status_unavailable"
                assert neg_state["failure_stage"] == "binding_failure"
        finally:
            os.environ["PATH"] = previous_path

        # Explicit owner recovery on disposable fixture
        subprocess.run(["git", "-C", str(repo), "update-index", "--clear-resolve-undo"], check=True)
        assert subprocess.run(
            ["git", "-C", str(repo), "ls-files", "--resolve-undo", "-z"],
            check=True, stdout=subprocess.PIPE,
        ).stdout == b""
        recovery_job = root / "resolve-undo-recovery-job"; recovery_job.mkdir(mode=0o700); recovery_job = recovery_job.resolve()
        recovery_command = dict(command); recovery_command["job_id"] = "resolve-undo-recovery"
        MODULE.write_atomic(recovery_job, MODULE.COMMAND_NAME, recovery_command)
        rec_state, _ = MODULE.create_state(recovery_job, "initial", resume=False)
        assert rec_state["worktree_baseline"] is not None
        rec_events = [
            {"event": "init", "init": {}, "conversation_id": "conv-recovery"},
            {"event": "result", "result": {
                "conversation_id": "conv-recovery", "status": "SUCCESS",
                "structured_output": report(summary="recovery success"),
            }},
        ]
        fake_agy.write_text(
            "#!/bin/sh\nprintf '%s\\n' " + " ".join(shlex.quote(json.dumps(item)) for item in rec_events) + "\n",
            encoding="utf-8",
        )
        assert run_controller(recovery_job, bin_dir) == 0
        final_rec, _, _ = MODULE.load_state(recovery_job)
        assert final_rec["status"] == "succeeded" and final_rec["reason"] is None

    check("resolve-undo observation immediately before launch fails closed with bounded reason and zero provider calls", resolve_undo_observation_fails_closed_before_provider_launch)

    def malformed_debug_stat_cache_fails_closed_and_blocks_candidate_actions() -> None:
        """Only documented numeric ls-files --debug cache fields are eligible."""
        job, state, _sha, _envelope = current_candidate_fixture("debug-stat-cache")
        command = json.loads((job / MODULE.COMMAND_NAME).read_text(encoding="utf-8"))
        repo = Path(command["workdir"])
        tracked = repo / "tracked.txt"; tracked.write_text("base\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "tracked.txt"], check=True)
        baseline = MODULE._worktree_snapshot(str(repo)); assert baseline is not None
        state.update({
            "worktree_baseline": baseline,
            "candidate_worktree_sha256": baseline["sha256"],
            "candidate_worktree_entries": baseline["entries"],
            "continue_available": True,
        })

        real_git = shutil.which("git"); assert real_git is not None
        bin_dir = root / "debug-stat-cache-bin"; bin_dir.mkdir()
        mode_file = root / "debug-stat-cache-mode"
        fake = bin_dir / "git"
        fake.write_text(
            "#!/bin/sh\n"
            "mode=$(cat " + shlex.quote(str(mode_file)) + ")\n"
            "case \" $* \" in *\" ls-files --debug -z \"*)\n"
            "  case \"$mode\" in\n"
            "    garbage) printf 'tracked.txt\\0  ctime: garbage\\n  mtime: garbage\\n  dev: garbage\\n  uid: garbage\\n  size: 5\\tflags: 0\\n'; exit 0;;\n"
            "    missing) printf 'tracked.txt\\0  ctime: 1:\\n  mtime: 3:4\\n  dev: 5\\tino: 6\\n  uid: 7\\tgid: 8\\n  size: 5\\tflags: 0\\n'; exit 0;;\n"
            "    trailing) printf 'tracked.txt\\0  ctime: 1:2 junk\\n  mtime: 3:4\\n  dev: 5\\tino: 6\\n  uid: 7\\tgid: 8\\n  size: 5\\tflags: 0\\n'; exit 0;;\n"
            "    valid) printf 'tracked.txt\\0  ctime: 1:2\\n  mtime: 3:4\\n  dev: 5\\tino: 6\\n  uid: 7\\tgid: 8\\n  size: 5\\tflags: 0\\n'; exit 0;;\n"
            "  esac;;\n"
            "esac\n"
            "exec " + shlex.quote(real_git) + " \"$@\"\n",
            encoding="utf-8",
        )
        fake.chmod(0o755)
        previous_path = os.environ.get("PATH", "")
        os.environ["PATH"] = f"{bin_dir}{os.pathsep}{previous_path}"
        try:
            # This exact safe-Git output was accepted by the vulnerable parser.
            mode_file.write_text("garbage", encoding="ascii")
            assert MODULE._worktree_snapshot(str(repo)) is None
            candidate_snapshot_actions_reject(job, state, "debug-stat-cache-garbage")
            for mode in ("missing", "trailing"):
                mode_file.write_text(mode, encoding="ascii")
                assert MODULE._worktree_snapshot(str(repo)) is None, mode
                candidate_snapshot_actions_reject(job, state, f"debug-stat-cache-{mode}")
            mode_file.write_text("valid", encoding="ascii")
            assert MODULE._worktree_snapshot(str(repo)) == baseline
            _raw, valid_sha = MODULE.write_atomic(job, MODULE.STATE_NAME, state)
            actions = {item["action"] for item in MODULE.public_status(state, valid_sha, job=job)["available_actions"]}
            assert {"result", "continue", "finalize"} <= actions
        finally:
            os.environ["PATH"] = previous_path

    check("malformed debug stat cache fails closed while numeric zero-flag records preserve candidate parity", malformed_debug_stat_cache_fails_closed_and_blocks_candidate_actions)

    def intent_to_add_flags_fail_closed_and_cannot_authorize_candidate_actions() -> None:
        """CE_INTENT_TO_ADD is semantic even when --stage reports the empty OID."""
        job, state, _sha, _envelope = current_candidate_fixture("intent-to-add")
        command = json.loads((job / MODULE.COMMAND_NAME).read_text(encoding="utf-8"))
        repo = Path(command["workdir"])
        empty = repo / "empty"; empty.write_bytes(b"")
        subprocess.run(["git", "-C", str(repo), "add", "-N", "empty"], check=True)
        intent_snapshot = MODULE._worktree_snapshot(str(repo))
        # The vulnerable v7 observation accepted the intent entry.  Keep this
        # branch so the red test also proves add-N -> add action parity instead
        # of merely testing a synthetic flag parser.
        if intent_snapshot is not None:
            state.update({
                "worktree_baseline": intent_snapshot,
                "candidate_worktree_sha256": intent_snapshot["sha256"],
                "candidate_worktree_entries": intent_snapshot["entries"],
            })
            subprocess.run(["git", "-C", str(repo), "add", "empty"], check=True)
            ordinary = MODULE._worktree_snapshot(str(repo))
            assert ordinary == intent_snapshot, "intent-to-add changed a semantic flag without changing v7"
        candidate_snapshot_actions_reject(job, state, "intent-to-add")

    check("intent-to-add fails closed for snapshots and result continue finalize parity", intent_to_add_flags_fail_closed_and_cannot_authorize_candidate_actions)

    def v6_snapshot_readback_uses_legacy_digest_and_rejects_semantic_substitution() -> None:
        """Persisted v6 candidates must never be silently reinterpreted as v7."""
        job, state, _sha, _envelope = current_candidate_fixture("v6-snapshot")
        command = json.loads((job / MODULE.COMMAND_NAME).read_text(encoding="utf-8"))
        legacy = MODULE._worktree_snapshot(command["workdir"], legacy=True)
        semantic = MODULE._worktree_snapshot(command["workdir"])
        assert legacy is not None and semantic is not None and legacy["sha256"] != semantic["sha256"]
        state.update({
            "schema_version": 6,
            "worktree_baseline": legacy,
            "candidate_worktree_sha256": legacy["sha256"],
            "candidate_worktree_entries": legacy["entries"],
            "continue_available": True,
        })
        state.pop("worktree_snapshot_algorithm")
        state.pop("provider_terminal_status")
        for field in MODULE.STATE_V11_FIELDS:
            state.pop(field)
        root_identity = state.pop("worktree_root_identity")
        legacy_raw, legacy_sha = MODULE.write_atomic(job, MODULE.STATE_NAME, state)
        loaded, _raw, loaded_sha = MODULE.load_state(job)
        assert loaded_sha == legacy_sha
        assert "worktree_root_identity" not in loaded
        hybrid = dict(loaded)
        hybrid["worktree_root_identity"] = root_identity
        try:
            MODULE.validate_state(hybrid)
        except MODULE.DispatchError as exc:
            assert str(exc) == "dispatch state fields are invalid"
        else:
            raise AssertionError("v6 state accepted a v9 root identity")
        assert (job / MODULE.STATE_NAME).read_bytes() == legacy_raw
        MODULE._bound_candidate_worktree(loaded, command)
        actions = {item["action"] for item in MODULE.public_status(loaded, loaded_sha, job=job)["available_actions"]}
        assert {"result", "continue", "finalize"} <= actions
        substituted = dict(loaded)
        substituted["candidate_worktree_sha256"] = semantic["sha256"]
        substituted_raw, substituted_sha = MODULE.write_atomic(job, MODULE.STATE_NAME, substituted)
        stale = MODULE.public_status(substituted, substituted_sha, job=job)
        assert {item["action"] for item in stale["available_actions"]} == {"result"}
        assert stale["result_available"] is True
        assert stale["candidate_sha256"] == substituted["result_sha256"]
        assert stale["continue_available"] is False
        try:
            MODULE._bound_candidate_worktree(substituted, command)
        except MODULE.DispatchError as exc:
            assert str(exc) == "candidate worktree binding changed"
        else:
            raise AssertionError("v6 candidate silently accepted a v7 semantic digest")
        delivered = subprocess.run(
            [sys.executable, str(SOURCE), "result", "--job-dir", str(job)],
            check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        assert delivered.returncode == 0
        assert delivered.stdout == _envelope.read_bytes() and delivered.stderr == b""
        assert (job / MODULE.STATE_NAME).read_bytes() == substituted_raw

        bin_dir = root / "v6-substituted-provider-bin"; bin_dir.mkdir()
        provider_called = root / "v6-substituted-provider-called"
        fake = bin_dir / "agy"
        fake.write_text("#!/bin/sh\nprintf called > " + shlex.quote(str(provider_called)) + "\n", encoding="utf-8")
        fake.chmod(0o755)
        verification = {
            "schema_version": 2, "summary": "legacy driver repair", "passed_checks": [],
            "failed_checks": ["fixture"], "advisory_checks": 0, "missing_checks": 0,
            "candidate_sha256": substituted["result_sha256"], "coverage": "partial",
            "verified_findings": 1, "unresolved_gaps": 1, "diff_review_complete": True,
        }
        for arguments in (
            ("restart", "--job-dir", str(job), "--approve-state-sha", substituted_sha),
            ("continue", "--job-dir", str(job), "--approve-state-sha", substituted_sha),
            ("finalize", "--job-dir", str(job), "--approve-state-sha", substituted_sha,
             "--assurance", "partially_verified"),
        ):
            rejected = subprocess.run(
                [sys.executable, str(SOURCE), *arguments], input=json.dumps(verification).encode("utf-8"),
                env={**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"},
                check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            assert rejected.returncode != 0 and rejected.stdout == b"", arguments
            assert (job / MODULE.STATE_NAME).read_bytes() == substituted_raw, arguments
            assert not provider_called.exists(), arguments
            assert not (job / "continue-staged").exists(), arguments
            assert not list(job.glob("*stream.ndjson")), arguments
        _raw, loaded_sha = MODULE.write_atomic(job, MODULE.STATE_NAME, loaded)
        verification = {
            "schema_version": 2, "summary": "legacy driver repair", "passed_checks": [],
            "failed_checks": ["fixture"], "advisory_checks": 0, "missing_checks": 0,
            "candidate_sha256": loaded["result_sha256"], "coverage": "partial",
            "verified_findings": 1, "unresolved_gaps": 1, "diff_review_complete": True,
        }
        queued, _queued_sha = MODULE.create_state(
            job, "conversation-continue", resume=True,
            approve_sha=loaded_sha, verification=verification,
        )
        assert queued["schema_version"] == MODULE.CURRENT_STATE_SCHEMA
        assert queued["worktree_baseline"] == semantic
        assert queued["candidate_worktree_sha256"] == semantic["sha256"]
        assert queued["candidate_worktree_entries"] == semantic["entries"]
        assert queued["worktree_root_identity"] == MODULE._dispatch_root_identity(command["workdir"])

    check("v6 candidate readback retains its legacy digest then atomically migrates to a fresh v9 semantic binding", v6_snapshot_readback_uses_legacy_digest_and_rejects_semantic_substitution)

    def v5_through_v9_status_parity_and_migration_are_exact() -> None:
        """Legacy status is read-only; eligible writes acquire one V11 binding."""
        for version in (5, 6, 7, 8, 9):
            job, state, _sha, _envelope = current_candidate_fixture(
                f"v{version}-migration", selection=version == 9,
            )
            command = json.loads((job / MODULE.COMMAND_NAME).read_text(encoding="utf-8"))
            legacy = MODULE._worktree_snapshot(command["workdir"], legacy=True)
            semantic = MODULE._worktree_snapshot(command["workdir"])
            assert legacy is not None and semantic is not None
            state["schema_version"] = version
            if version in {5, 6}:
                expected = legacy
            else:
                expected = semantic
            state.update({
                "worktree_baseline": expected,
                "candidate_worktree_sha256": expected["sha256"],
                "candidate_worktree_entries": expected["entries"],
                "continue_available": True,
            })
            if version == 5:
                state.pop("selection_sha256"); state.pop("selection_identity")
            if version < 8:
                state.pop("worktree_snapshot_algorithm")
            if version < 9:
                state.pop("worktree_root_identity")
            if version < MODULE.CURRENT_STATE_SCHEMA:
                state.pop("provider_terminal_status")
                for field in MODULE.STATE_V11_FIELDS:
                    state.pop(field)
            _raw, legacy_sha = MODULE.write_atomic(job, MODULE.STATE_NAME, state)
            loaded, _raw, loaded_sha = MODULE.load_state(job)
            assert loaded_sha == legacy_sha and loaded["schema_version"] == version
            actions = {
                item["action"] for item in MODULE.public_status(loaded, loaded_sha, job=job)["available_actions"]
            }
            assert {"result", "continue", "finalize"} <= actions, version
            verification = {
                "schema_version": 2, "summary": f"v{version} migration", "passed_checks": [],
                "failed_checks": ["fixture"], "advisory_checks": 0, "missing_checks": 0,
                "candidate_sha256": loaded["result_sha256"], "coverage": "partial",
                "verified_findings": 1, "unresolved_gaps": 1, "diff_review_complete": True,
            }
            queued, _queued_sha = MODULE.create_state(
                job, "conversation-continue", resume=True,
                approve_sha=loaded_sha, verification=verification,
            )
            assert queued["schema_version"] == MODULE.CURRENT_STATE_SCHEMA, version
            assert queued["worktree_snapshot_algorithm"] == MODULE.WORKTREE_SNAPSHOT_SEMANTIC_V1
            assert queued["worktree_baseline"] == semantic
            assert queued["candidate_worktree_sha256"] == semantic["sha256"]
            assert queued["candidate_worktree_entries"] == semantic["entries"]
            assert queued["worktree_root_identity"] == MODULE._dispatch_root_identity(command["workdir"])
            assert queued["provider_terminal_status"] == "unknown"

        # A V9 state cannot acquire V11 authority from a different selection
        # binding, even when the command, root, schemas, and worktree remain valid.
        _job, v9_state, _sha, _envelope = current_candidate_fixture(
            "v9-selection-drift", selection=True,
        )
        v9_command = json.loads((_job / MODULE.COMMAND_NAME).read_text(encoding="utf-8"))
        v9_state["schema_version"] = 9
        v9_state.pop("provider_terminal_status")
        for field in MODULE.STATE_V11_FIELDS:
            v9_state.pop(field)
        v9_state["selection_sha256"] = "0" * 64
        MODULE.validate_state(v9_state)
        try:
            MODULE._upgrade_legacy_state(v9_state, v9_command)
        except MODULE.DispatchError as exc:
            assert str(exc) == "dispatch selection binding changed"
        else:
            raise AssertionError("V9 selection drift acquired V11 authority")

    def v1_candidate_status_is_read_only_and_all_mutations_fail_without_writes() -> None:
        """V1 result evidence remains readable but has no lifecycle authority."""
        fake_bin = root / "legacy-mutation-fake-bin"; fake_bin.mkdir(mode=0o700)
        provider_marker = root / "legacy-mutation-provider-called"
        fake_agy = fake_bin / "agy"
        fake_agy.write_text(
            "#!/bin/sh\nprintf called > " + shlex.quote(str(provider_marker)) + "\nexit 99\n",
            encoding="utf-8",
        )
        fake_agy.chmod(0o700)
        environment = {**os.environ, "PATH": f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}"}

        for version in (1,):
            for workflow in ("task", "project"):
                job, state, _sha, envelope = current_candidate_fixture(
                    f"v{version}-{workflow}-readonly",
                    workflow=workflow, linked=workflow == "project",
                )
                state["schema_version"] = version
                removed = {*MODULE.STATE_V5_FIELDS, *MODULE.STATE_V6_FIELDS, *MODULE.STATE_V8_FIELDS, *MODULE.STATE_V9_FIELDS, *MODULE.STATE_V10_FIELDS, *MODULE.STATE_V11_FIELDS}
                if version == 1:
                    removed.update(MODULE.STATE_PROJECT_FIELDS)
                    removed.update({"provider_retry_after_seconds", "provider_retry_observed_epoch"})
                for key in removed:
                    state.pop(key, None)
                old_raw, old_sha = MODULE.write_atomic(job, MODULE.STATE_NAME, state)
                loaded, loaded_raw, loaded_sha = MODULE.load_state(job)
                assert loaded_raw == old_raw and loaded_sha == old_sha

                direct_public = MODULE.public_status(loaded, loaded_sha, job=job)
                assert {item["action"] for item in direct_public["available_actions"]} == {"result"}, (
                    version, workflow, direct_public["available_actions"],
                )
                commands = (
                    [sys.executable, str(SOURCE), "status", "--job-dir", str(job)],
                    [sys.executable, str(SOURCE), "wait", "--job-dir", str(job),
                     "--after-state-sha", loaded_sha, "--timeout", "1s"],
                )
                projected = []
                for command_line in commands:
                    outcome = subprocess.run(
                        command_line, env=environment, stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE, check=False,
                    )
                    assert outcome.returncode == 0 and not outcome.stderr
                    projected.append(json.loads(outcome.stdout))
                assert projected[0]["available_actions"] == projected[1]["available_actions"]
                assert {item["action"] for item in projected[0]["available_actions"]} == {"result"}
                delivered = subprocess.run(
                    [sys.executable, str(SOURCE), "result", "--job-dir", str(job)],
                    env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    check=False,
                )
                assert delivered.returncode == 0 and delivered.stdout == envelope.read_bytes()
                assert not delivered.stderr and (job / MODULE.STATE_NAME).read_bytes() == old_raw

                verification = {
                    "schema_version": 2, "summary": "historical candidate cannot mutate",
                    "passed_checks": [], "failed_checks": ["legacy-authority"],
                    "advisory_checks": 0, "missing_checks": 0,
                    "candidate_sha256": loaded["result_sha256"], "coverage": "partial",
                    "verified_findings": 1, "unresolved_gaps": 1,
                    "diff_review_complete": True,
                }
                attempts = (
                    ("resume", 21, []),
                    ("restart", 64, []),
                    ("continue", 64, []),
                    ("finalize", 64, ["--assurance", "partially_verified"]),
                )
                for action, expected_exit, suffix in attempts:
                    command_line = [
                        sys.executable, str(SOURCE), action, "--job-dir", str(job),
                        "--approve-state-sha", loaded_sha, *suffix,
                    ]
                    outcome = subprocess.run(
                        command_line, env=environment,
                        input=(json.dumps(verification).encode("utf-8")
                               if action in {"continue", "finalize"} else None),
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
                    )
                    assert outcome.returncode == expected_exit and not outcome.stdout, (
                        version, workflow, action, outcome.returncode, outcome.stderr,
                    )
                    assert (job / MODULE.STATE_NAME).read_bytes() == old_raw
                    assert not list(job.glob("attempt-*.stream.ndjson"))
                copy_parent = root / f"v{version}-{workflow}-copy-parent"; copy_parent.mkdir(mode=0o700)
                copied = subprocess.run(
                    [sys.executable, str(SOURCE), "verification-copy", "--job-dir", str(job),
                     "--destination", str(copy_parent.resolve() / "candidate")],
                    env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
                )
                assert copied.returncode == MODULE.EXIT_BY_REASON["status_unavailable"] and not copied.stdout
                assert not (copy_parent / "candidate").exists()
                assert (job / MODULE.STATE_NAME).read_bytes() == old_raw
                assert not provider_marker.exists()

    def v3_v4_migration_requires_state_command_agreement_before_any_write() -> None:
        """Only an internally coherent V3/V4 state may obtain a migration capability."""

        verification_template = {
            "schema_version": 2, "summary": "legacy migration binding",
            "passed_checks": [], "failed_checks": ["fixture"],
            "advisory_checks": 0, "missing_checks": 0,
            "coverage": "partial", "verified_findings": 1,
            "unresolved_gaps": 1, "diff_review_complete": True,
        }

        def downgrade(
            label: str, version: int, *, patch: dict | None = None, linked: bool = False,
            inside_worktree: bool = False,
        ) -> tuple[Path, dict, bytes, str]:
            job, state, _sha, _envelope = current_candidate_fixture(
                f"v{version}-{label}", workflow="task", linked=linked,
                inside_worktree=inside_worktree,
            )
            command = json.loads((job / MODULE.COMMAND_NAME).read_text(encoding="utf-8"))
            state["schema_version"] = version
            for key in {
                *MODULE.STATE_V5_FIELDS, *MODULE.STATE_V6_FIELDS,
                *MODULE.STATE_V8_FIELDS, *MODULE.STATE_V9_FIELDS,
                *MODULE.STATE_V10_FIELDS, *MODULE.STATE_V11_FIELDS,
            }:
                state.pop(key, None)
            if version == 3:
                state.pop("provider_retry_after_seconds", None)
                state.pop("provider_retry_observed_epoch", None)
            state.update({
                "phase": None, "assurance": None, "continue_available": False,
                "project_boundary": None,
            })
            if patch is not None:
                state.update(patch)
            if state["workflow"] == "project":
                state.update({
                    "phase": "awaiting-verification", "assurance": "pending",
                    "project_boundary": MODULE._project_boundary(command["workdir"]),
                })
            raw, sha = MODULE.write_atomic(job, MODULE.STATE_NAME, state)
            loaded, loaded_raw, loaded_sha = MODULE.load_state(job)
            assert loaded_raw == raw and loaded_sha == sha
            return job, loaded, raw, sha

        # Both V3 and V4 retain a bounded positive path.  It needs the separate
        # status-time migration digest, and its first write upgrades atomically.
        for version in (3, 4):
            job, loaded, raw, sha = downgrade("positive", version)
            public = MODULE.public_status(loaded, sha, job=job)
            migration_sha = public["migration_binding_sha256"]
            assert isinstance(migration_sha, str) and len(migration_sha) == 64
            actions = {item["action"] for item in public["available_actions"]}
            assert {"result", "restart", "finalize"} <= actions, (version, actions)
            assert "verification-copy" not in actions, (version, actions)
            copy_parent = root / f"legacy-v{version}-copy-parent"; copy_parent.mkdir(mode=0o700)
            copied = subprocess.run(
                [sys.executable, str(SOURCE), "verification-copy", "--job-dir", str(job),
                 "--destination", str(copy_parent.resolve() / "candidate")],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
            )
            assert copied.returncode == MODULE.EXIT_BY_REASON["status_unavailable"] and not copied.stdout
            assert not (copy_parent / "candidate").exists()
            assert (job / MODULE.STATE_NAME).read_bytes() == raw
            verification = dict(verification_template, candidate_sha256=loaded["result_sha256"])
            missing = subprocess.run(
                [sys.executable, str(SOURCE), "finalize", "--job-dir", str(job),
                 "--approve-state-sha", sha, "--assurance", "partially_verified"],
                input=json.dumps(verification).encode("utf-8"),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
            )
            assert missing.returncode == 64 and not missing.stdout
            assert b"legacy migration approval" in missing.stderr
            assert (job / MODULE.STATE_NAME).read_bytes() == raw
            approved = subprocess.run(
                [sys.executable, str(SOURCE), "finalize", "--job-dir", str(job),
                 "--approve-state-sha", sha, "--approve-migration-sha", migration_sha,
                 "--assurance", "partially_verified"],
                input=json.dumps(verification).encode("utf-8"),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
            )
            assert approved.returncode == 0 and not approved.stderr
            current, _current_raw, _current_sha = MODULE.load_state(job)
            assert current["schema_version"] == MODULE.CURRENT_STATE_SCHEMA
            assert current["driver_disposition"] == "partially_verified"

        # A V3/V4 record may be syntactically valid after a same-UID state
        # substitution.  It still must not advertise or use any migration
        # route unless it agrees with the frozen command's immutable lifecycle
        # contract.  ``hard_seconds`` is deliberately absent: extend may alter
        # it while every listed field remains immutable.
        mismatch_cases = (
            ("job-id", {"job_id": "different-legacy-job"}, False),
            ("workflow-explore", {"workflow": "explore"}, False),
            ("workflow-project", {"workflow": "project"}, True),
            ("max-cycles", {"max_cycles": 1}, False),
            ("idle-budget", {"idle_seconds": 1.0}, False),
            ("max-budget", {"max_seconds": 19.0}, False),
        )
        fake_bin = root / "legacy-migration-binding-bin"; fake_bin.mkdir(mode=0o700)
        provider_marker = root / "legacy-migration-binding-provider-called"
        fake_agy = fake_bin / "agy"
        fake_agy.write_text(
            "#!/bin/sh\nprintf called > " + shlex.quote(str(provider_marker)) + "\nexit 99\n",
            encoding="utf-8",
        )
        fake_agy.chmod(0o700)
        environment = {**os.environ, "PATH": f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}"}
        for version in (3, 4):
            for label, patch, linked in mismatch_cases:
                job, loaded, raw, sha = downgrade(label, version, patch=patch, linked=linked)
                public = MODULE.public_status(loaded, sha, job=job)
                actions = {item["action"] for item in public["available_actions"]}
                assert public["migration_binding_sha256"] is None, (version, label, public)
                assert not ({"resume", "restart", "continue", "finalize"} & actions), (
                    version, label, public["available_actions"],
                )
                verification = dict(verification_template, candidate_sha256=loaded["result_sha256"])
                for action, suffix in (
                    ("continue", ()),
                    ("finalize", ("--assurance", "partially_verified")),
                ):
                    rejected = subprocess.run(
                        [sys.executable, str(SOURCE), action, "--job-dir", str(job),
                         "--approve-state-sha", sha, "--approve-migration-sha", "0" * 64,
                         *suffix],
                        env=environment, input=json.dumps(verification).encode("utf-8"),
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
                    )
                    assert rejected.returncode == 64 and not rejected.stdout, (
                        version, label, action, rejected.returncode, rejected.stderr,
                    )
                    assert b"immutable lifecycle binding changed" in rejected.stderr
                    assert (job / MODULE.STATE_NAME).read_bytes() == raw
                    assert not (job / "continue-staged").exists()
                    assert not list(job.glob("attempt-*.stream.ndjson"))
                assert not provider_marker.exists()

        for version in (3, 4):
            unsafe_job, loaded, raw, sha = downgrade("unsafe-worktree", version, inside_worktree=True)
            public = MODULE.public_status(loaded, sha, job=unsafe_job)
            assert public["migration_binding_sha256"] is None
            actions = {item["action"] for item in public["available_actions"]}
            assert "result" in actions
            assert not ({"restart", "continue", "resume", "finalize"} & actions)

            status_res = subprocess.run(
                [sys.executable, str(SOURCE), "status", "--job-dir", str(unsafe_job)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
            )
            assert status_res.returncode == 0
            assert json.loads(status_res.stdout)["migration_binding_sha256"] is None

            result_res = subprocess.run(
                [sys.executable, str(SOURCE), "result", "--job-dir", str(unsafe_job)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
            )
            assert result_res.returncode == 0 and json.loads(result_res.stdout)["status"] == "completed"

            restart_res = subprocess.run(
                [sys.executable, str(SOURCE), "restart", "--job-dir", str(unsafe_job),
                 "--approve-state-sha", sha, "--approve-migration-sha", "0" * 64],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
            )
            assert restart_res.returncode != 0
            assert (unsafe_job / MODULE.STATE_NAME).read_bytes() == raw

    def v5_through_v8_status_commands_project_only_proved_actions_and_finalize_to_v11() -> None:
        """Every migratable legacy generation can prove and use its actions."""
        for version in (5, 6, 7, 8):
            job, state, _sha, _envelope = current_candidate_fixture(f"v{version}-status-positive")
            command = json.loads((job / MODULE.COMMAND_NAME).read_text(encoding="utf-8"))
            legacy = MODULE._worktree_snapshot(command["workdir"], legacy=True)
            semantic = MODULE._worktree_snapshot(command["workdir"])
            assert legacy is not None and semantic is not None
            expected = legacy if version in {5, 6} else semantic
            state.update({
                "schema_version": version,
                "worktree_baseline": expected,
                "candidate_worktree_sha256": expected["sha256"],
                "candidate_worktree_entries": expected["entries"],
                "continue_available": True,
            })
            if version == 5:
                state.pop("selection_sha256"); state.pop("selection_identity")
            if version < 8:
                state.pop("worktree_snapshot_algorithm")
            state.pop("worktree_root_identity")
            state.pop("provider_terminal_status")
            for field in MODULE.STATE_V11_FIELDS:
                state.pop(field)
            old_raw, old_sha = MODULE.write_atomic(job, MODULE.STATE_NAME, state)
            loaded, _raw, loaded_sha = MODULE.load_state(job)
            assert loaded_sha == old_sha
            public = MODULE.public_status(loaded, loaded_sha, job=job)
            assert {item["action"] for item in public["available_actions"]} == {
                "result", "restart", "continue", "finalize",
            }, (version, public["available_actions"])
            status = subprocess.run(
                [sys.executable, str(SOURCE), "status", "--job-dir", str(job)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
            )
            wait = subprocess.run(
                [sys.executable, str(SOURCE), "wait", "--job-dir", str(job),
                 "--after-state-sha", loaded_sha, "--timeout", "1s"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
            )
            assert status.returncode == wait.returncode == 0
            assert json.loads(status.stdout)["available_actions"] == json.loads(wait.stdout)["available_actions"]
            assert (job / MODULE.STATE_NAME).read_bytes() == old_raw
            verification = {
                "schema_version": 2, "summary": f"v{version} bounded finalization",
                "passed_checks": [], "failed_checks": ["fixture"],
                "advisory_checks": 0, "missing_checks": 0,
                "candidate_sha256": loaded["result_sha256"], "coverage": "partial",
                "verified_findings": 1, "unresolved_gaps": 1,
                "diff_review_complete": True,
            }
            finalized = subprocess.run(
                [sys.executable, str(SOURCE), "finalize", "--job-dir", str(job),
                 "--approve-state-sha", loaded_sha, "--assurance", "partially_verified"],
                input=json.dumps(verification).encode("utf-8"),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
            )
            assert finalized.returncode == 0 and not finalized.stderr, (version, finalized.stderr)
            current, _raw, _sha = MODULE.load_state(job)
            assert current["schema_version"] == MODULE.CURRENT_STATE_SCHEMA
            assert current["worktree_snapshot_algorithm"] == MODULE.WORKTREE_SNAPSHOT_SEMANTIC_V1
            assert current["worktree_baseline"] == semantic
            assert current["candidate_worktree_sha256"] == semantic["sha256"]
            assert current["worktree_root_identity"] == MODULE._dispatch_root_identity(command["workdir"])

    def legacy_read_and_mutation_authority_contracts() -> None:
        v5_through_v9_status_parity_and_migration_are_exact()
        v1_candidate_status_is_read_only_and_all_mutations_fail_without_writes()
        v3_v4_migration_requires_state_command_agreement_before_any_write()
        v5_through_v8_status_commands_project_only_proved_actions_and_finalize_to_v11()

    if FOCUSED_CHECK == "V3/V4 migration state-command binding rejects before any write":
        check(FOCUSED_CHECK, v3_v4_migration_requires_state_command_agreement_before_any_write)
    elif FOCUSED_CHECK == "V1 legacy evidence remains result-only":
        check(FOCUSED_CHECK, v1_candidate_status_is_read_only_and_all_mutations_fail_without_writes)
    else:
        check("legacy status separates readback from proved v5-v9 mutation authority", legacy_read_and_mutation_authority_contracts)

    def current_v11_candidate_inside_worktree_is_driver_only() -> None:
        """Preserve current evidence without reviving provider authority.

        This is intentionally distinct from the V3/V4 migration fixture above
        and the no-candidate recovery fixture below: it is one current V11 bound
        candidate whose controller directory already exists inside its worktree.
        """
        job, state, _sha, _envelope = current_candidate_fixture(
            "v10-inside-worktree", inside_worktree=True,
        )
        assert state["schema_version"] == MODULE.CURRENT_STATE_SCHEMA == 11
        state["continue_available"] = True
        before, sha = MODULE.write_atomic(job, MODULE.STATE_NAME, state)
        state, loaded_raw, loaded_sha = MODULE.load_state(job)
        assert loaded_raw == before and loaded_sha == sha

        status = subprocess.run(
            [sys.executable, str(SOURCE), "status", "--job-dir", str(job)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        assert status.returncode == 0 and not status.stderr, status.stderr
        public = json.loads(status.stdout)
        assert public["result_available"] is True
        assert public["candidate_sha256"] == state["result_sha256"]
        assert public["continue_available"] is False
        assert {item["action"] for item in public["available_actions"]} == {
            "result", "finalize",
        }

        delivered = subprocess.run(
            [sys.executable, str(SOURCE), "result", "--job-dir", str(job)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        assert delivered.returncode == 0 and not delivered.stderr
        assert json.loads(delivered.stdout)["summary"] == "candidate-v10-inside-worktree"
        assert (job / MODULE.STATE_NAME).read_bytes() == before

        verification = {
            "schema_version": 2, "summary": "driver reviewed preserved current candidate",
            "passed_checks": [], "failed_checks": ["inside-worktree-controller-state"],
            "advisory_checks": 0, "missing_checks": 0,
            "candidate_sha256": state["result_sha256"], "coverage": "partial",
            "verified_findings": 1, "unresolved_gaps": 1,
            "diff_review_complete": True,
        }
        provider_marker = root / "v10-inside-worktree-provider-called"
        fake_bin = root / "v10-inside-worktree-bin"; fake_bin.mkdir(mode=0o700)
        fake_agy = fake_bin / "agy"
        fake_agy.write_text(
            "#!/bin/sh\nprintf called > " + shlex.quote(str(provider_marker)) + "\nexit 99\n",
            encoding="utf-8",
        )
        fake_agy.chmod(0o700)
        environment = {**os.environ, "PATH": f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}"}
        for action, payload in (("continue", verification), ("restart", None)):
            rejected = subprocess.run(
                [sys.executable, str(SOURCE), action, "--job-dir", str(job),
                 "--approve-state-sha", sha],
                env=environment,
                input=None if payload is None else json.dumps(payload).encode("utf-8"),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
            )
            assert rejected.returncode == 64 and not rejected.stdout, (
                action, rejected.returncode, rejected.stderr,
            )
            assert b"dispatch job directory cannot be inside the target workdir" in rejected.stderr
            assert (job / MODULE.STATE_NAME).read_bytes() == before
            assert not list(job.glob("verification-*.json"))
            assert not list(job.glob("attempt-*.stream.ndjson"))
            assert not provider_marker.exists()

        complete_verification = {
            **verification,
            "summary": "complete evidence cannot verify an unbound worktree",
            "passed_checks": ["driver-full-gate"],
            "failed_checks": [],
            "unresolved_gaps": 0,
            "coverage": "complete",
        }
        rejected_verified = subprocess.run(
            [sys.executable, str(SOURCE), "finalize", "--job-dir", str(job),
             "--approve-state-sha", sha, "--assurance", "verified"],
            env=environment, input=json.dumps(complete_verification).encode("utf-8"),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        assert rejected_verified.returncode == 64 and not rejected_verified.stdout
        assert b"verified finalization is unavailable for jobs inside the worktree" in rejected_verified.stderr
        assert (job / MODULE.STATE_NAME).read_bytes() == before
        assert not list(job.glob("verification-*.json"))
        assert not provider_marker.exists()

        finalized = subprocess.run(
            [sys.executable, str(SOURCE), "finalize", "--job-dir", str(job),
             "--approve-state-sha", sha, "--assurance", "partially_verified"],
            env=environment, input=json.dumps(verification).encode("utf-8"),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        assert finalized.returncode == 0 and not finalized.stderr, finalized.stderr
        final_public = json.loads(finalized.stdout)
        assert final_public["driver_disposition"] == "partially_verified"
        assert {item["action"] for item in final_public["available_actions"]} == {"result"}
        assert not provider_marker.exists()

        final_result = subprocess.run(
            [sys.executable, str(SOURCE), "result", "--job-dir", str(job)],
            env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        assert final_result.returncode == 0 and not final_result.stderr
        assert json.loads(final_result.stdout)["summary"] == "candidate-v10-inside-worktree"

    check(
        "current V11 inside-worktree candidate remains result-finalize only without provider mutation",
        current_v11_candidate_inside_worktree_is_driver_only,
    )

    def independent_nonempty_snapshot_reference_preserves_v6_v7_and_v8() -> None:
        """Freeze full per-path v6/v7 bytes without dispatcher's serializers."""
        repo = root / "snapshot-compatibility-reference"; repo.mkdir()
        subprocess.run(["git", "init", "-q", "--object-format=sha1", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.email", "fixture@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "Fixture"], check=True)

        def run_git(*arguments: str) -> bytes:
            return subprocess.run(
                ["git", "-C", str(repo), *arguments], check=True, stdout=subprocess.PIPE,
            ).stdout

        def blob_oid(payload: bytes) -> bytes:
            return hashlib.sha1(
                b"blob " + str(len(payload)).encode("ascii") + b"\0" + payload,
            ).hexdigest().encode("ascii")

        def write(relative: str, payload: bytes, *, executable: bool = False) -> None:
            path = repo / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
            if executable:
                path.chmod(0o755)

        base = {
            ".gitignore": b"ignored.txt\n",
            "deleted.txt": b"delete me\n",
            "mode-change.sh": b"#!/bin/sh\necho mode\n",
            "staged.txt": b"base staged\n",
            "tracked.txt": b"tracked base\n",
        }
        for relative, payload in base.items():
            write(relative, payload)
        (repo / "tracked-symlink").symlink_to("tracked.txt")
        subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-qm", "base"], check=True)

        staged_payload = b"staged index\n"
        write("staged.txt", staged_payload)
        (repo / "mode-change.sh").chmod(0o755)
        subprocess.run(["git", "-C", str(repo), "add", "staged.txt", "mode-change.sh"], check=True)
        (repo / "deleted.txt").unlink()
        write("untracked.txt", b"untracked\n")
        (repo / "untracked-link").symlink_to("untracked.txt")
        write("ignored.txt", b"ignored\n")
        (repo / "empty-dir").mkdir()
        os.mkfifo(repo / "special")

        head = {
            b".gitignore": (0o100644, blob_oid(base[".gitignore"])),
            b"deleted.txt": (0o100644, blob_oid(base["deleted.txt"])),
            b"mode-change.sh": (0o100644, blob_oid(base["mode-change.sh"])),
            b"staged.txt": (0o100644, blob_oid(base["staged.txt"])),
            b"tracked-symlink": (0o120000, blob_oid(b"tracked.txt")),
            b"tracked.txt": (0o100644, blob_oid(base["tracked.txt"])),
        }
        index = dict(head)
        index[b"mode-change.sh"] = (0o100755, blob_oid(base["mode-change.sh"]))
        index[b"staged.txt"] = (0o100644, blob_oid(staged_payload))
        objects = {oid: payload for _mode, oid, payload in (
            (0o100644, blob_oid(base[".gitignore"]), base[".gitignore"]),
            (0o100644, blob_oid(base["deleted.txt"]), base["deleted.txt"]),
            (0o100644, blob_oid(base["mode-change.sh"]), base["mode-change.sh"]),
            (0o100644, blob_oid(base["staged.txt"]), base["staged.txt"]),
            (0o120000, blob_oid(b"tracked.txt"), b"tracked.txt"),
            (0o100644, blob_oid(base["tracked.txt"]), base["tracked.txt"]),
            (0o100644, blob_oid(staged_payload), staged_payload),
        )}
        # Git does not report this platform's FIFO through --others.  The
        # fixture therefore proves its special type through the independent
        # directory-manifest serialization, not by inventing a Git listing.
        other = {b"untracked-link", b"untracked.txt"}
        ignored = {b"ignored.txt"}

        def parse_stage(raw: bytes) -> dict[bytes, tuple[int, bytes]]:
            result: dict[bytes, tuple[int, bytes]] = {}
            for record in raw.split(b"\0")[:-1]:
                header, name = record.split(b"\t", 1)
                mode, oid, stage = header.split(b" ")
                assert stage == b"0"
                result[name] = (int(mode, 8), oid)
            return result

        def parse_tree(raw: bytes) -> dict[bytes, tuple[int, bytes]]:
            result: dict[bytes, tuple[int, bytes]] = {}
            for record in raw.split(b"\0")[:-1]:
                header, name = record.split(b"\t", 1)
                mode, kind, oid = header.split(b" ")
                assert kind == b"blob"
                result[name] = (int(mode, 8), oid)
            return result

        assert run_git("rev-parse", "--show-object-format") == b"sha1\n"
        assert parse_tree(run_git("ls-tree", "-r", "-z", "HEAD")) == head
        assert parse_stage(run_git("ls-files", "--stage", "-z")) == index
        listed_other = set(run_git("ls-files", "-z", "--others", "--exclude-standard").split(b"\0")[:-1])
        assert listed_other == other and b"special" not in listed_other
        assert set(run_git("ls-files", "-z", "--others", "--ignored", "--exclude-standard").split(b"\0")[:-1]) == ignored

        def binding(info: os.stat_result) -> tuple[int, ...]:
            return (
                info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
                info.st_uid, info.st_gid, info.st_size, info.st_mtime_ns, info.st_ctime_ns,
            )

        def authority(info: os.stat_result) -> list[int]:
            value = binding(info)
            return [value[0], value[1], value[4], value[5], stat.S_IMODE(value[2])]

        def canonical_reference(value: object) -> bytes:
            return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii") + b"\n"

        def git_path(*arguments: str) -> Path:
            raw = run_git(*arguments).decode("utf-8", "strict").strip()
            path = Path(raw)
            return (path if path.is_absolute() else repo / path).resolve()

        root_info = os.lstat(repo)
        marker_info = os.lstat(repo / ".git")
        git_dir = git_path("rev-parse", "--absolute-git-dir")
        common_dir = git_path("rev-parse", "--git-common-dir")
        index_path = git_path("rev-parse", "--git-path", "index")
        assert stat.S_ISDIR(marker_info.st_mode) and git_dir.is_dir() and common_dir.is_dir() and index_path.is_file()

        def persistent(info: os.stat_result, *, legacy: bool) -> tuple[int, ...]:
            value = binding(info)
            return value if legacy else (stat.S_IFMT(value[2]), stat.S_IMODE(value[2]))

        def directory_reference(path: Path, *, is_root: bool, legacy: bool) -> tuple[bytes, int]:
            records: list[tuple[bytes, bytes, tuple[int, ...], bytes]] = []
            empty_directories = 0
            for entry in os.scandir(path):
                raw_name = os.fsencode(entry.name)
                if is_root and raw_name == b".git":
                    continue
                info = os.lstat(entry.path)
                if stat.S_ISDIR(info.st_mode):
                    payload, nested_empty = directory_reference(Path(entry.path), is_root=False, legacy=legacy)
                    metadata = persistent(info, legacy=legacy)
                    kind = b"directory"
                    empty_directories += nested_empty
                elif stat.S_ISLNK(info.st_mode):
                    payload = hashlib.sha256(os.fsencode(os.readlink(entry.path))).digest()
                    metadata = persistent(info, legacy=legacy)
                    kind = b"symlink"
                elif stat.S_ISREG(info.st_mode):
                    payload = b""; metadata = (); kind = b"file"
                else:
                    payload = b""; metadata = (); kind = b"special"
                records.append((raw_name, kind, metadata, payload))
            digest = hashlib.sha256(); digest.update(b"agy-worker-directory-manifest-v1\0")
            for raw_name, kind, metadata, payload in sorted(records):
                digest.update(len(raw_name).to_bytes(8, "big")); digest.update(raw_name)
                digest.update(len(kind).to_bytes(8, "big")); digest.update(kind)
                metadata_raw = canonical_reference(list(metadata))
                digest.update(len(metadata_raw).to_bytes(8, "big")); digest.update(metadata_raw)
                digest.update(len(payload).to_bytes(8, "big")); digest.update(payload)
            if not is_root and not records:
                empty_directories += 1
            return digest.digest(), empty_directories

        def reference(*, legacy: bool) -> dict[str, object]:
            digest = hashlib.sha256()
            digest.update(b"agy-worker-worktree-v5\0" if legacy else b"agy-worker-worktree-v7\0")
            root_bytes = os.fsencode(str(repo.resolve()))
            digest.update(len(root_bytes).to_bytes(8, "big")); digest.update(root_bytes)
            digest.update(canonical_reference([root_info.st_dev, root_info.st_ino]))
            digest.update(canonical_reference(["directory", authority(marker_info), hashlib.sha256(b"").hexdigest()]))
            index_raw = index_path.read_bytes()
            digest.update(canonical_reference([
                str(git_dir), authority(os.lstat(git_dir)), str(common_dir), authority(os.lstat(common_dir)),
                str(index_path.resolve()) if legacy else None,
                hashlib.sha256(index_raw).hexdigest() if legacy else None,
                authority(os.lstat(index_path)) if legacy else None,
            ]))
            changed = 0
            for relative in sorted(set(head) | set(index) | other | ignored):
                digest.update(len(relative).to_bytes(8, "big")); digest.update(relative)
                indexed = index.get(relative); head_entry = head.get(relative)
                for label, value in ((b"head", head_entry), (b"index", indexed)):
                    digest.update(label + b"\0")
                    if value is None:
                        digest.update(b"missing\0")
                    else:
                        digest.update(f"{value[0]:o}".encode("ascii") + b"\0"); digest.update(value[1])
                path = repo / os.fsdecode(relative)
                is_other = relative in other or relative in ignored
                try:
                    info = os.lstat(path)
                except FileNotFoundError:
                    digest.update(b"missing\0")
                    differs = indexed is not None
                else:
                    digest.update(canonical_reference(list(persistent(info, legacy=legacy))))
                    if stat.S_ISLNK(info.st_mode):
                        payload = os.fsencode(os.readlink(path))
                        digest.update(b"symlink\0"); digest.update(len(payload).to_bytes(8, "big")); digest.update(payload)
                        differs = indexed is None or indexed[0] != 0o120000 or payload != objects[indexed[1]]
                    elif stat.S_ISREG(info.st_mode):
                        payload = path.read_bytes(); payload_digest = hashlib.sha256(payload).digest()
                        digest.update(b"file\0"); digest.update(payload_digest)
                        differs = (
                            indexed is None or indexed[0] not in {0o100644, 0o100755}
                            or bool(info.st_mode & 0o111) != bool(indexed[0] & 0o111)
                            or info.st_size != len(objects[indexed[1]])
                            or payload_digest != hashlib.sha256(objects[indexed[1]]).digest()
                        )
                    else:
                        digest.update(b"special\0")
                        differs = True
                if is_other or indexed != head_entry or differs:
                    changed += 1
            manifest, empty_directories = directory_reference(repo, is_root=True, legacy=legacy)
            digest.update(b"directory-manifest-v1\0"); digest.update(manifest)
            digest.update(empty_directories.to_bytes(8, "big"))
            assert changed == 6 and empty_directories == 1
            return {"sha256": digest.hexdigest(), "entries": changed + empty_directories}

        expected_v6 = reference(legacy=True)
        expected_v7 = reference(legacy=False)
        expected_v8 = dict(expected_v7)
        assert expected_v6["sha256"] != expected_v7["sha256"] and expected_v7["entries"] == 7
        assert MODULE._worktree_snapshot(str(repo), legacy=True) == expected_v6
        assert MODULE._worktree_snapshot(str(repo)) == expected_v7
        assert MODULE._state_worktree_snapshot({"schema_version": 6}, str(repo)) == expected_v6
        assert MODULE._state_worktree_snapshot({"schema_version": 7}, str(repo)) == expected_v7
        assert MODULE._state_worktree_snapshot({
            "schema_version": 8, "worktree_snapshot_algorithm": "semantic-v1",
        }, str(repo)) == expected_v8

    check("independent non-empty per-path reference preserves frozen v6 v7 and named v8 snapshot digests", independent_nonempty_snapshot_reference_preserves_v6_v7_and_v8)

    def current_snapshot_algorithm_is_explicit_and_v7_remains_exact() -> None:
        extracted_worktree_facade_preserves_all_signatures_and_patch_seams()
        job, state, _sha, _envelope = current_candidate_fixture("snapshot-algorithm")
        assert state["schema_version"] == MODULE.CURRENT_STATE_SCHEMA
        assert state["worktree_snapshot_algorithm"] == MODULE.WORKTREE_SNAPSHOT_SEMANTIC_V1
        command = json.loads((job / MODULE.COMMAND_NAME).read_text(encoding="utf-8"))
        assert MODULE._state_worktree_snapshot(state, command["workdir"]) == MODULE._worktree_snapshot(command["workdir"])

        invalid = dict(state)
        invalid["worktree_snapshot_algorithm"] = "semantic-v2"
        try:
            MODULE.validate_state(invalid)
        except MODULE.DispatchError as exc:
            assert str(exc) == "dispatch worktree snapshot algorithm is invalid"
        else:
            raise AssertionError("new state accepted an unrecognized snapshot algorithm")

        v7 = dict(state)
        v7["schema_version"] = 7
        v7.pop("worktree_snapshot_algorithm")
        v7.pop("worktree_root_identity")
        v7.pop("provider_terminal_status")
        for field in MODULE.STATE_V11_FIELDS:
            v7.pop(field)
        assert MODULE.validate_state(v7)["schema_version"] == 7
        assert MODULE._state_worktree_snapshot(v7, command["workdir"]) == MODULE._worktree_snapshot(command["workdir"])

        copied_scripts = root / "copied-runtime-scripts"
        shutil.copytree(SOURCE.parent, copied_scripts)
        copied_source = copied_scripts / "agy_dispatch.py"
        copied_spec = importlib.util.spec_from_file_location("agy_dispatch_copied", copied_source)
        assert copied_spec is not None and copied_spec.loader is not None
        copied_module = importlib.util.module_from_spec(copied_spec)
        copied_spec.loader.exec_module(copied_module)
        assert Path(copied_module._WORKTREE_HELPER.__file__).resolve() == (
            copied_scripts / "agy_dispatch_worktree.py"
        ).resolve()
        assert Path(copied_module._WORKTREE_HELPER.__file__).resolve() != Path(
            MODULE._WORKTREE_HELPER.__file__
        ).resolve()

    check("new states persist one snapshot algorithm while v7 readback remains semantic-v1", current_snapshot_algorithm_is_explicit_and_v7_remains_exact)


    def privileged_agy_executable_is_never_safe() -> None:
        executable = root / "privileged-agy"
        executable.write_text("#!/bin/sh\nprintf '1.1.16\\n'\n", encoding="utf-8")
        executable.chmod(0o755)
        original = MODULE.MODEL_SELECTION.shutil.which
        MODULE.MODEL_SELECTION.shutil.which = lambda _name: str(executable)
        try:
            resolved, _binding = MODULE.MODEL_SELECTION.resolve_safe_executable()
            assert Path(resolved).resolve() == executable.resolve()
            baseline = executable.lstat()
            for privileged in (0o4000, 0o2000):
                # Some test filesystems drop set-ID bits on chmod.  Exercise the
                # pre-probe metadata guard directly with the exact lstat shape;
                # no executable or provider fixture is involved.
                synthetic = list(baseline)
                synthetic[0] = baseline.st_mode | privileged
                assert not MODULE.MODEL_SELECTION._safe_owner_mode(
                    os.stat_result(synthetic), directory=False,
                ), f"privileged executable mode {privileged:o} was accepted"
        finally:
            MODULE.MODEL_SELECTION.shutil.which = original

    check("safe agy executable rejects setuid and setgid metadata before any probe", privileged_agy_executable_is_never_safe)

    def regular_to_fifo_race_fails_before_task_or_dispatch_artifacts() -> None:
        """A pathname replacement must not block the pre-task selector."""
        repo = root / "fifo-race-repo"; repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        bin_dir = root / "fifo-race-bin"; bin_dir.mkdir()
        fake = bin_dir / "agy"
        provider_marker = root / "fifo-race-provider"
        fake.write_text(
            "#!/bin/sh\n"
            "touch " + shlex.quote(str(provider_marker)) + "\n"
            "exit 99\n",
            encoding="utf-8",
        )
        fake.chmod(0o755)

        # The injected hook runs only in the selector's Python process.  It
        # replaces the already-lstat'd regular target immediately before the
        # production descriptor open, making the historical blocking FIFO race
        # deterministic without changing the production source under test.
        hook_dir = root / "fifo-race-hook"; hook_dir.mkdir()
        (hook_dir / "sitecustomize.py").write_text(
            "import os\n"
            "_target = os.environ.get('AGY_FIFO_RACE_PATH')\n"
            "_open = os.open\n"
            "_fired = False\n"
            "def _race_open(path, flags, mode=0o777, *, dir_fd=None):\n"
            "    global _fired\n"
            "    if not _fired and _target and os.path.abspath(os.fspath(path)) == _target:\n"
            "        _fired = True\n"
            "        os.unlink(_target)\n"
            "        os.mkfifo(_target, 0o700)\n"
            "    if dir_fd is None:\n"
            "        return _open(path, flags, mode)\n"
            "    return _open(path, flags, mode, dir_fd=dir_fd)\n"
            "os.open = _race_open\n",
            encoding="utf-8",
        )
        log_dir = root / "fifo-race-logs"; log_dir.mkdir(mode=0o700)
        env = dict(os.environ)
        env.update({
            "PATH": f"{bin_dir}{os.pathsep}{env.get('PATH', '')}",
            "PYTHONPATH": str(hook_dir),
            "AGY_FIFO_RACE_PATH": str(fake.resolve()),
            "AGY_WORKER_MODE": "accept-edits",
            "AGY_WORKER_LOG_DIR": str(log_dir),
            "AGY_WORKER_JOB_ID": "fifo-race",
            "AGY_WORKER_MAX_ATTEMPTS": "1",
        })
        worker = ROOT / "skills/agy-worker/runtime/agy-worker.sh"
        started = time.monotonic()
        process = subprocess.Popen(
            [str(worker), "--workdir", str(repo), "--model", "gemini-3.6-flash", "--effort", "high"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=env, start_new_session=True,
        )
        try:
            stdout, stderr = process.communicate(
                input=b"this task must not be consumed\n", timeout=1.5,
            )
        except subprocess.TimeoutExpired as exc:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.communicate()
            raise AssertionError("regular-to-FIFO pre-task selection blocked") from exc
        assert time.monotonic() - started < 1.5
        assert process.returncode == 8 and not stdout
        assert stderr == (
            b"model-selection: evidence-unavailable - agy executable identity is unavailable\n"
        )
        job = log_dir / "fifo-race"
        assert stat.S_ISFIFO(fake.lstat().st_mode)
        assert not provider_marker.exists()
        for name in ("task.txt", "dispatch-command.json", "state.json", "selection.json"):
            assert not (job / name).exists(), name

    check("regular-to-FIFO race fails promptly before task provider state or selection", regular_to_fifo_race_fails_before_task_or_dispatch_artifacts)

    def bound_executable_reader_is_nonblocking_typed_and_size_bounded() -> None:
        import hashlib
        import socket

        selection = MODULE.MODEL_SELECTION
        unavailable = "agy executable identity is unavailable"

        def rejected(path: Path, expected: os.stat_result) -> None:
            try:
                selection._read_bound_executable_sha256(path, expected)
            except selection.EvidenceUnavailable as exc:
                assert str(exc) == unavailable
            else:
                raise AssertionError(f"unsafe executable was accepted: {path.name}")

        regular = root / "bound-executable-regular"
        regular.write_bytes(b"#!/bin/sh\nexit 0\n")
        regular.chmod(0o700)
        seen_flags: list[int] = []
        original_open = selection.os.open

        def record_open(path, flags, mode=0o777, *, dir_fd=None):
            seen_flags.append(flags)
            if dir_fd is None:
                return original_open(path, flags, mode)
            return original_open(path, flags, mode, dir_fd=dir_fd)

        selection.os.open = record_open
        try:
            assert selection._read_bound_executable_sha256(regular, regular.lstat()) == hashlib.sha256(
                regular.read_bytes(),
            ).hexdigest()
        finally:
            selection.os.open = original_open
        assert seen_flags and seen_flags[-1] & selection.os.O_NOFOLLOW
        assert seen_flags[-1] & selection.os.O_NONBLOCK

        # agy 1.1.17 is a 177,517,056-byte regular executable on the reproduced
        # host.  It must remain descriptor-bound and fully hashed before any
        # version/help probe; this fixture stays below the bounded policy cap.
        current_large_size = 177_517_056
        assert 16 * 1024 * 1024 < current_large_size <= selection.EXECUTABLE_CONTENT_LIMIT
        current_large = root / "bound-executable-current-large"
        with current_large.open("wb") as handle:
            handle.truncate(current_large_size)
        current_large.chmod(0o700)
        expected_digest = hashlib.sha256()
        zero_chunk = b"\0" * (64 * 1024)
        for _ in range(current_large_size // len(zero_chunk)):
            expected_digest.update(zero_chunk)
        expected_digest.update(b"\0" * (current_large_size % len(zero_chunk)))
        assert selection._read_bound_executable_sha256(
            current_large, current_large.lstat(),
        ) == expected_digest.hexdigest()

        oversized = root / "bound-executable-over-limit"
        with oversized.open("wb") as handle:
            handle.truncate(selection.EXECUTABLE_CONTENT_LIMIT + 1)
        oversized.chmod(0o700)
        original_read = selection.os.read
        selection.os.read = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("oversized executable content was read"),
        )
        try:
            rejected(oversized, oversized.lstat())
        finally:
            selection.os.read = original_read

        directory = root / "bound-executable-directory"; directory.mkdir(mode=0o700)
        rejected(directory, directory.lstat())

        fifo = root / "bound-executable-fifo"; os.mkfifo(fifo, 0o700)
        rejected(fifo, fifo.lstat())

        first_socket, second_socket = socket.socketpair()
        original_open = selection.os.open
        selection.os.open = lambda *_args, **_kwargs: os.dup(first_socket.fileno())
        try:
            rejected(regular, regular.lstat())
        finally:
            selection.os.open = original_open
            first_socket.close(); second_socket.close()

        device = Path("/dev/null")
        if device.exists():
            rejected(device, device.lstat())

        original_open = selection.os.open
        selection.os.open = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            PermissionError("fixture path must stay private"),
        )
        try:
            rejected(regular, regular.lstat())
        finally:
            selection.os.open = original_open

        original_nonblock = selection.os.O_NONBLOCK
        open_called = False

        def forbidden_open(*_args, **_kwargs):
            nonlocal open_called
            open_called = True
            raise AssertionError("blocking fallback was attempted")

        selection.os.O_NONBLOCK = 0
        selection.os.open = forbidden_open
        try:
            rejected(regular, regular.lstat())
        finally:
            selection.os.open = original_open
            selection.os.O_NONBLOCK = original_nonblock
        assert not open_called

    check("bound executable reader accepts regular limits and rejects nonregular or blocking fallbacks", bound_executable_reader_is_nonblocking_typed_and_size_bounded)

    def live_candidate_actions_share_the_mutation_binder() -> None:
        job, state, sha, envelope = current_candidate_fixture("actions")
        public = MODULE.public_status(state, sha, job=job)
        actions = {item["action"] for item in public["available_actions"]}
        assert {"result", "finalize"} <= actions

        # Candidate bytes are no longer current: no terminal candidate action may
        # be advertised even though the old state flag still says available.
        envelope.write_bytes(b"{}\n"); envelope.chmod(0o600)
        stale = MODULE.public_status(state, sha, job=job)
        assert not ({"result", "continue", "finalize"} & {
            item["action"] for item in stale["available_actions"]
        })

        # A repair attempt retains candidate references privately, but while it is
        # active its only public controls are active controls, never terminal ones.
        active = dict(state)
        active.update({
            "status": "running", "controller_pid": 123, "started_epoch": time.time(),
            "attempt_origin": "conversation-continue", "attempt": 2, "cycle": 2,
            "phase": "repairing", "elapsed_seconds": 1.0, "attempt_base_elapsed": 1.0,
        })
        active_actions = {item["action"] for item in MODULE.public_status(active, sha, job=job)["available_actions"]}
        assert {"result", "continue", "finalize"}.isdisjoint(active_actions)

    check("available candidate actions rebind live artifacts and disappear during an active repair", live_candidate_actions_share_the_mutation_binder)

    def stale_candidate_projection_is_live_private_and_nonmutating() -> None:
        """A stale candidate may remain recognized, but never publicly available."""
        job, state, sha, envelope = current_candidate_fixture("stale-projection")
        state["continue_available"] = True
        _raw, sha = MODULE.write_atomic(job, MODULE.STATE_NAME, state)
        before = (job / MODULE.STATE_NAME).read_bytes()
        envelope.write_bytes(b"{}\n"); envelope.chmod(0o600)

        public = MODULE.public_status(state, sha, job=job)
        assert public["candidate_recognized"] is True
        assert public["candidate_source"] == "provider_success"
        assert public["result_available"] is False
        assert public["candidate_sha256"] is None
        assert public["continue_available"] is False
        assert public["failure_stage"] == "binding_failure"
        assert not ({"result", "continue", "finalize"} & {
            item["action"] for item in public["available_actions"]
        })
        assert (job / MODULE.STATE_NAME).read_bytes() == before

        captured = io.BytesIO()
        original_stdout = MODULE.sys.stdout

        class _Stdout:
            buffer = captured

        MODULE.sys.stdout = _Stdout()
        try:
            MODULE.print_text_status(state, sha, job=job)
        finally:
            MODULE.sys.stdout = original_stdout
        lines = captured.getvalue().decode("utf-8").splitlines()
        expected_lines = [
            "Provider attempt: succeeded; reason: none; failure stage: binding_failure; bound result available: no; driver disposition: unreviewed.",
            "Driver evidence: 0 passed, 0 failed, 0 advisory, 0 missing; cycle: 1/2.",
            'Next safe action: fresh-attempt restart: "$PIPELINE/agy-worker.sh" restart --job-id ' + state["job_id"] + " --approve-state-sha "
            + sha + " --format text.",
        ]
        assert lines == expected_lines

    check("stale recognized candidates project bound availability and private binding failure", stale_candidate_projection_is_live_private_and_nonmutating)

    def action_predicates_keep_runtime_and_headroom_in_sync() -> None:
        job, state, sha, _envelope = current_candidate_fixture("predicates")
        stale_resume = dict(state)
        stale_resume.update({
            "result_path": None, "result_sha256": None, "result_identity": None,
            "candidate_recognized": False, "candidate_source": "none", "result_available": False,
            "candidate_worktree_sha256": None, "candidate_worktree_entries": None,
            "status": "failed", "conversation_id": None, "resume_available": True,
        })
        assert "resume" not in {
            item["action"] for item in MODULE.public_status(stale_resume, sha, job=job)["available_actions"]
        }
        near_deadline = dict(state)
        now = time.time()
        near_deadline.update({
            "status": "running", "controller_pid": 123, "started_epoch": now,
            "attempt_base_elapsed": 9.5, "elapsed_seconds": 9.5,
            "hard_seconds": 10.0, "max_seconds": 20.0,
            "progress_count": 1, "last_progress_epoch": now,
        })
        assert not MODULE._extend_is_eligible(near_deadline, now)
        near_deadline["hard_seconds"] = 10.0
        near_deadline["max_seconds"] = 10.5
        near_deadline["attempt_base_elapsed"] = 1.0
        near_deadline["elapsed_seconds"] = 1.0
        assert not MODULE._extend_is_eligible(near_deadline, now)

        eligible_extend = dict(near_deadline)
        eligible_extend.update({
            "hard_seconds": 10.0, "max_seconds": 20.0,
            "attempt_base_elapsed": 1.0, "elapsed_seconds": 1.0,
        })
        assert MODULE._extend_is_eligible(eligible_extend, now)
        extend = next(
            item for item in MODULE._available_actions(eligible_extend, sha, now)
            if item["action"] == "extend"
        )
        assert extend == {
            "action": "extend",
            "requires": ["--by caller-provided DURATION"],
            "guidance": "choose a positive duration that remains within the current maximum runtime",
        }

    check("resume and extend action predicates reject missing conversation and sub-second headroom", action_predicates_keep_runtime_and_headroom_in_sync)

    def codex_can_continue_on_advisory_repair_intent() -> None:
        verification = {
            "schema_version": 2, "summary": "driver requests an advisory repair", "passed_checks": [],
            "failed_checks": [], "advisory_checks": 1, "missing_checks": 0,
            "candidate_sha256": "a" * 64, "coverage": "partial",
            "verified_findings": 0, "unresolved_gaps": 1, "diff_review_complete": True,
        }
        called: list[tuple] = []
        original_read = MODULE._verification_from_stdin
        original_spawn = MODULE.spawn
        MODULE._verification_from_stdin = lambda: verification
        MODULE.spawn = lambda *args, **kwargs: (called.append((args, kwargs)) or 17)
        try:
            assert MODULE.command_continue(root / "unused-job", "b" * 64, None) == 17
        finally:
            MODULE._verification_from_stdin = original_read
            MODULE.spawn = original_spawn
        assert called and called[0][0][1] == "conversation-continue"

    check("continue accepts a bounded Codex advisory repair intent", codex_can_continue_on_advisory_repair_intent)

    def finalization_persists_the_exact_codex_declaration() -> None:
        job, state, sha, _envelope = current_candidate_fixture("declaration")
        verification = {
            "schema_version": 2, "summary": "driver explicitly rejects this candidate", "passed_checks": ["unit"],
            "failed_checks": [], "advisory_checks": 0, "missing_checks": 0,
            "candidate_sha256": state["result_sha256"], "coverage": "complete",
            "verified_findings": 0, "unresolved_gaps": 0, "diff_review_complete": True,
        }
        completed = subprocess.run(
            [sys.executable, str(SOURCE), "finalize", "--job-dir", str(job),
             "--approve-state-sha", sha, "--assurance", "rejected"],
            input=json.dumps(verification).encode("utf-8"), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        assert completed.returncode == 0, completed.stderr.decode("utf-8", "replace")
        persisted = json.loads(completed.stdout)
        assert persisted["driver_disposition"] == persisted["assurance"] == "rejected"

    check("finalize records the bounded Codex declaration without reinterpreting check counts", finalization_persists_the_exact_codex_declaration)

    def verified_finalization_requires_declared_workflow_evidence() -> None:
        """`verified` is an exact Codex declaration, but has a hard evidence floor."""
        job, state, sha, _envelope = current_candidate_fixture("verified-floor")
        insufficient = {
            "schema_version": 2, "summary": "driver incorrectly called this verified", "passed_checks": [],
            "failed_checks": [], "advisory_checks": 0, "missing_checks": 0,
            "candidate_sha256": state["result_sha256"], "coverage": "partial",
            "verified_findings": 0, "unresolved_gaps": 1, "diff_review_complete": False,
        }
        before = (job / MODULE.STATE_NAME).read_bytes()
        rejected = subprocess.run(
            [sys.executable, str(SOURCE), "finalize", "--job-dir", str(job),
             "--approve-state-sha", sha, "--assurance", "verified"],
            input=json.dumps(insufficient).encode("utf-8"), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        assert rejected.returncode == 64 and not rejected.stdout
        assert (job / MODULE.STATE_NAME).read_bytes() == before
        assert not list((job / "continue-staged").glob("final-*.json"))

        sufficient = dict(insufficient)
        sufficient.update({
            "summary": "driver verified task candidate", "passed_checks": ["unit"],
            "coverage": "complete", "unresolved_gaps": 0, "diff_review_complete": True,
        })
        accepted = subprocess.run(
            [sys.executable, str(SOURCE), "finalize", "--job-dir", str(job),
             "--approve-state-sha", sha, "--assurance", "verified"],
            input=json.dumps(sufficient).encode("utf-8"), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        assert accepted.returncode == 0, accepted.stderr.decode("utf-8", "replace")
        assert json.loads(accepted.stdout)["driver_disposition"] == "verified"

    check("verified finalization requires workflow evidence without rewriting other Codex dispositions", verified_finalization_requires_declared_workflow_evidence)

    def recovery_actions_share_their_strict_mutation_guard() -> None:
        """A command binding drift must hide recovery before it can mutate state."""
        job, state, sha, _envelope = current_candidate_fixture("action-parity")
        command = job / MODULE.COMMAND_NAME
        command.write_bytes(b"{}\n"); command.chmod(0o600)
        public = MODULE.public_status(state, sha, job=job)
        actions = {item["action"] for item in public["available_actions"]}
        assert not ({"result", "continue", "finalize", "restart"} & actions), actions
        before = (job / MODULE.STATE_NAME).read_bytes()
        try:
            MODULE.create_state(job, "fresh-restart", resume=True, approve_sha=sha)
        except MODULE.DispatchError:
            pass
        else:
            raise AssertionError("a status-hidden restart was accepted")
        assert (job / MODULE.STATE_NAME).read_bytes() == before

        # Resume has the same launch-input requirements but no candidate.  Its
        # public action and both recovery mutations must fail identically.
        resume_job, resume_state, _resume_sha, _resume_envelope = current_candidate_fixture("resume-parity")
        resume_state.update({
            "status": "failed", "reason": "agy_failed_unclassified", "exit_code": 5,
            "finished_epoch": 1.0, "conversation_id": "resume-conversation", "resume_available": True,
            "result_path": None, "result_sha256": None, "result_identity": None,
            "candidate_recognized": False, "candidate_source": "none", "result_available": False,
            "candidate_worktree_sha256": None, "candidate_worktree_entries": None,
            "driver_disposition": "not_applicable", "phase": "attempt-failed", "assurance": "pending",
            "continue_available": False,
        })
        _raw, resume_sha = MODULE.write_atomic(resume_job, MODULE.STATE_NAME, resume_state)
        (resume_job / MODULE.COMMAND_NAME).write_bytes(b"{}\n")
        (resume_job / MODULE.COMMAND_NAME).chmod(0o600)
        resume_actions = {item["action"] for item in MODULE.public_status(resume_state, resume_sha, job=resume_job)["available_actions"]}
        assert not ({"resume", "restart"} & resume_actions), resume_actions
        before_resume = (resume_job / MODULE.STATE_NAME).read_bytes()
        for origin in ("conversation-resume", "fresh-restart"):
            try:
                MODULE.create_state(resume_job, origin, resume=True, approve_sha=resume_sha)
            except MODULE.DispatchError:
                pass
            else:
                raise AssertionError(f"a status-hidden {origin} was accepted")
            assert (resume_job / MODULE.STATE_NAME).read_bytes() == before_resume

    check("available recovery actions use the same bound command/schema/root/selection guard as mutation", recovery_actions_share_their_strict_mutation_guard)

    def recovery_guard_rejects_stage_schema_and_root_drift_before_state_writes() -> None:
        for kind in ("stage", "provider-schema", "root", "job-inside-worktree"):
            job, state, _sha, _envelope = current_candidate_fixture(
                f"recovery-{kind}", staged=kind == "stage",
                inside_worktree=kind == "job-inside-worktree",
            )
            state.update({
                "status": "failed", "reason": "agy_failed_unclassified", "exit_code": 5,
                "finished_epoch": 1.0, "conversation_id": "recovery-conversation", "resume_available": True,
                "result_path": None, "result_sha256": None, "result_identity": None,
                "candidate_recognized": False, "candidate_source": "none", "result_available": False,
                "candidate_worktree_sha256": None, "candidate_worktree_entries": None,
                "driver_disposition": "not_applicable", "phase": "attempt-failed", "assurance": "pending",
                "continue_available": False,
            })
            command = json.loads((job / MODULE.COMMAND_NAME).read_text(encoding="utf-8"))
            if kind == "stage":
                Path(command["stage_file"]).write_text("tampered prompt", encoding="utf-8")
                Path(command["stage_file"]).chmod(0o600)
            elif kind == "provider-schema":
                provider_path = Path(command["argv"][command["argv"].index("--json-schema") + 1])
                provider_path.write_bytes(provider_path.read_bytes() + b"\n")
            elif kind == "root":
                original = Path(command["workdir"])
                moved = original.with_name(original.name + "-moved")
                original.rename(moved)
                original.symlink_to(moved, target_is_directory=True)
            _raw, sha = MODULE.write_atomic(job, MODULE.STATE_NAME, state)
            actions = {item["action"] for item in MODULE.public_status(state, sha, job=job)["available_actions"]}
            assert not ({"resume", "restart"} & actions), (kind, actions)
            before = (job / MODULE.STATE_NAME).read_bytes()
            for origin in ("conversation-resume", "fresh-restart"):
                try:
                    MODULE.create_state(job, origin, resume=True, approve_sha=sha)
                except MODULE.DispatchError:
                    pass
                else:
                    raise AssertionError(f"{kind} drift accepted {origin}")
                assert (job / MODULE.STATE_NAME).read_bytes() == before
        assert MODULE._job_is_inside_worktree(Path("\0invalid"), "/some/workdir") is True

    check("stage provider-schema and root drift have status-command recovery parity", recovery_guard_rejects_stage_schema_and_root_drift_before_state_writes)

    def replaced_same_path_root_hides_recovery_and_cannot_rebind() -> None:
        """Recovery must not turn a different repository at the same path into authority."""
        cases = (("task", ("conversation-resume", "fresh-restart")),
                 ("explore", ("conversation-resume", "fresh-restart")),
                 ("legacy", ("conversation-resume",)))
        for workflow, origins in cases:
            job, state, _sha, _envelope = current_candidate_fixture(
                f"same-path-{workflow}", workflow=workflow,
            )
            state.update({
                "status": "failed", "reason": "agy_failed_unclassified", "exit_code": 5,
                "finished_epoch": 1.0, "conversation_id": "same-path-conversation",
                "resume_available": True, "result_path": None, "result_sha256": None,
                "result_identity": None, "candidate_recognized": False,
                "candidate_source": "none", "result_available": False,
                "candidate_worktree_sha256": None, "candidate_worktree_entries": None,
                "driver_disposition": "not_applicable", "phase": "attempt-failed",
                "assurance": "pending", "continue_available": False,
            })
            command = json.loads((job / MODULE.COMMAND_NAME).read_text(encoding="utf-8"))
            original = Path(command["workdir"])
            moved = original.with_name(original.name + "-original")
            original.rename(moved)
            original.mkdir()
            subprocess.run(["git", "init", "-q", str(original)], check=True)
            _raw, sha = MODULE.write_atomic(job, MODULE.STATE_NAME, state)
            public = MODULE.public_status(state, sha, job=job)
            assert not ({"resume", "restart"} & {item["action"] for item in public["available_actions"]})
            before = (job / MODULE.STATE_NAME).read_bytes()
            for origin in origins:
                try:
                    MODULE.create_state(job, origin, resume=True, approve_sha=sha)
                except MODULE.DispatchError:
                    pass
                else:
                    raise AssertionError(f"{workflow} replacement authorized {origin}")
                assert (job / MODULE.STATE_NAME).read_bytes() == before
            assert not list(job.glob("stream.ndjson"))

    check("task explore and legacy same-path repository replacement hides recovery before mutation", replaced_same_path_root_hides_recovery_and_cannot_rebind)

    def selection_drift_hides_candidate_actions_before_any_mutation() -> None:
        job, state, sha, _envelope = current_candidate_fixture("selection-parity", selection=True)
        state["continue_available"] = True
        _raw, sha = MODULE.write_atomic(job, MODULE.STATE_NAME, state)
        selection_path = Path(json.loads((job / MODULE.COMMAND_NAME).read_text(encoding="utf-8"))["selection_path"])
        original_selection = selection_path.read_text(encoding="utf-8")
        selection_path.write_bytes(b"{}\n"); selection_path.chmod(0o600)
        public = MODULE.public_status(state, sha, job=job)
        actions = {item["action"] for item in public["available_actions"]}
        assert not ({"result", "continue", "finalize", "restart"} & actions), actions
        assert public["resume_available"] is False
        captured = io.BytesIO()
        original_stdout = MODULE.sys.stdout

        class _Stdout:
            buffer = captured

        MODULE.sys.stdout = _Stdout()
        try:
            MODULE.print_text_status(state, sha, job=job)
        finally:
            MODULE.sys.stdout = original_stdout
        lines = captured.getvalue().decode("utf-8").splitlines()
        assert len(lines) == 3
        assert lines[2] == (
            "Next safe action: create a fresh job using the unchanged caller selection after reviewing "
            "the current sanitized agy interface evidence. No same-job action is available."
        )
        for private in (str(job), str(selection_path), original_selection, "gemini-3.6-flash"):
            assert private not in "\n".join(lines)

        resume_only = dict(state)
        resume_only.update({
            "status": "failed", "reason": "agy_failed_unclassified", "exit_code": 5,
            "conversation_id": "selection-drift-conversation", "resume_available": True,
            "result_path": None, "result_sha256": None, "result_identity": None,
            "candidate_recognized": False, "candidate_source": "none", "result_available": False,
            "candidate_worktree_sha256": None, "candidate_worktree_entries": None,
            "driver_disposition": "not_applicable", "phase": "attempt-failed",
            "continue_available": False,
        })
        resumed_public = MODULE.public_status(resume_only, sha, job=job)
        assert "resume" not in {item["action"] for item in resumed_public["available_actions"]}
        assert resumed_public["resume_available"] is False
        verification = {
            "schema_version": 2, "summary": "driver found a bounded defect", "passed_checks": [],
            "failed_checks": ["fixture"], "advisory_checks": 0, "missing_checks": 0,
            "candidate_sha256": state["result_sha256"], "coverage": "partial",
            "verified_findings": 1, "unresolved_gaps": 1, "diff_review_complete": True,
        }
        before = (job / MODULE.STATE_NAME).read_bytes()
        commands = (
            ([sys.executable, str(SOURCE), "result", "--job-dir", str(job)], MODULE.EXIT_BY_REASON["status_unavailable"]),
            ([sys.executable, str(SOURCE), "continue", "--job-dir", str(job), "--approve-state-sha", sha], 64),
            ([sys.executable, str(SOURCE), "finalize", "--job-dir", str(job), "--approve-state-sha", sha,
              "--assurance", "partially_verified"], 64),
        )
        for command, expected_exit in commands:
            completed = subprocess.run(
                command, input=json.dumps(verification).encode("utf-8") if command[2] != "result" else None,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            assert completed.returncode == expected_exit and not completed.stdout, (command, completed.stderr)
            assert (job / MODULE.STATE_NAME).read_bytes() == before
        assert not list(job.glob("stream.ndjson"))

    check("selection tamper hides result continue and finalize and leaves candidate state untouched", selection_drift_hides_candidate_actions_before_any_mutation)

    def text_status_has_three_sanitized_lines_and_lists_exact_actions() -> None:
        job, state, sha, _envelope = current_candidate_fixture("text")

        def render(value: dict, *, bound_job: Path | None = None) -> list[str]:
            captured = io.BytesIO()
            original_stdout = MODULE.sys.stdout

            class _Stdout:
                buffer = captured

            MODULE.sys.stdout = _Stdout()
            try:
                MODULE.print_text_status(value, sha, job=bound_job)
            finally:
                MODULE.sys.stdout = original_stdout
            lines = captured.getvalue().decode("utf-8").splitlines()
            assert len(lines) == 3
            for sentinel in ("conversation-secret", "worker prose secret", str(job), "candidate-text"):
                assert sentinel not in "\n".join(lines)
            return lines

        state.update({
            "conversation_id": "conversation-secret",
            "check_summary": "worker prose secret",
        })
        current = render(state, bound_job=job)
        assert current == [
            "Provider attempt: succeeded; reason: none; failure stage: none; bound result available: yes; driver disposition: unreviewed.",
            "Driver evidence: 0 passed, 0 failed, 0 advisory, 0 missing; cycle: 1/2.",
            'Next safe action: retrieve current bound result JSON with "$PIPELINE/agy-worker.sh" result --job-id current-text --format json; review it and run driver checks, construct Verification v2, then Codex—not the controller—may choose the eligible finalize action.',
        ]

        cancelled = dict(state)
        cancelled.update({
            "status": "cancelled", "reason": "provider_terminal_cancelled", "exit_code": 22,
            "candidate_source": "provider_cancelled", "continue_available": False,
        })
        cancelled_public = MODULE.public_status(cancelled, sha, job=job)
        assert "continue" not in {item["action"] for item in cancelled_public["available_actions"]}
        assert {item["action"] for item in cancelled_public["available_actions"]} == {"result", "verification-copy", "restart", "finalize"}
        cancelled_lines = render(cancelled, bound_job=job)
        assert cancelled_lines[2] == (
            'Next safe action: retrieve current bound result JSON with "$PIPELINE/agy-worker.sh" result --job-id current-text --format json; '
            "review it and run driver checks, construct Verification v2, then Codex—not the controller—may choose the eligible finalize action. "
            'Available fresh restart command: "$PIPELINE/agy-worker.sh" restart --job-id current-text --approve-state-sha ' + sha + " --format text."
        )
        assert "continue" not in cancelled_lines[2]

        for disposition in ("verified", "partially_verified", "rejected", "blocked"):
            finalized = dict(state)
            finalized.update({
                "driver_disposition": disposition,
                "assurance": disposition,
                "phase": "blocked" if disposition == "blocked" else "completed",
                "continue_available": False,
            })
            finalized_public = MODULE.public_status(finalized, sha, job=job)
            assert {item["action"] for item in finalized_public["available_actions"]} == {"result", "restart"}
            assert render(finalized, bound_job=job) == [
                f"Provider attempt: succeeded; reason: none; failure stage: none; bound result available: yes; driver disposition: {disposition}.",
                "Driver evidence: 0 passed, 0 failed, 0 advisory, 0 missing; cycle: 1/2.",
                'Next safe action: optional finalized result JSON readback with "$PIPELINE/agy-worker.sh" result --job-id current-text --format json; driver disposition is already recorded; do not construct Verification v2, continue, or finalize. Available fresh restart command: "$PIPELINE/agy-worker.sh" restart --job-id current-text --approve-state-sha ' + sha + " --format text.",
            ]

        finalized_without_restart = dict(finalized)
        finalized_without_restart["attempt"] = finalized_without_restart["max_cycles"]
        no_restart_public = MODULE.public_status(finalized_without_restart, sha, job=job)
        assert {item["action"] for item in no_restart_public["available_actions"]} == {"result"}
        no_restart_lines = render(finalized_without_restart, bound_job=job)
        assert no_restart_lines[2] == (
            'Next safe action: optional finalized result JSON readback with "$PIPELINE/agy-worker.sh" result --job-id current-text --format json; '
            "driver disposition is already recorded; do not construct Verification v2, continue, or finalize."
        )
        assert "restart" not in no_restart_lines[2]

        active = dict(state)
        active.update({
            "status": "running", "controller_pid": 123, "started_epoch": time.time(),
            "finished_epoch": None, "exit_code": None,
        })
        active_public = MODULE.public_status(active, sha, job=job)
        assert_symbolic_action_commands(active_public["available_actions"], {"wait", "cancel"})
        assert render(active, bound_job=job) == [
            "Provider attempt: running; reason: none; failure stage: none; bound result available: no; driver disposition: unreviewed.",
            "Driver evidence: 0 passed, 0 failed, 0 advisory, 0 missing; cycle: 1/2.",
            'Next safe action: "$PIPELINE/agy-worker.sh" wait --job-id current-text --after-state-sha ' + sha + " --format text.",
        ]

        resumable = dict(state)
        resumable.update({
            "status": "failed", "reason": "agy_failed_unclassified", "exit_code": 5,
            "candidate_recognized": False, "candidate_source": "none", "result_available": False,
            "result_path": None, "result_sha256": None, "result_identity": None,
            "candidate_worktree_sha256": None, "candidate_worktree_entries": None,
            "driver_disposition": "not_applicable", "phase": "attempt-failed",
            "resume_available": True, "continue_available": False,
        })
        resumable_public = MODULE.public_status(resumable, sha)
        assert_symbolic_action_commands(resumable_public["available_actions"], {"resume", "restart"})
        assert resumable_public["next_action"] == "none"
        assert resumable_public["next_action_command"] is None
        assert render(resumable) == [
            "Provider attempt: failed; reason: agy_failed_unclassified; failure stage: none; bound result available: no; driver disposition: not_applicable.",
            "Driver evidence: 0 passed, 0 failed, 0 advisory, 0 missing; cycle: 1/2.",
            'Next safe actions: exact-conversation resume: "$PIPELINE/agy-worker.sh" resume --job-id current-text --approve-state-sha ' + sha
            + ' --format text; fresh-attempt restart: "$PIPELINE/agy-worker.sh" restart --job-id current-text --approve-state-sha '
            + sha + " --format text.",
        ]

        restart_only = dict(resumable)
        restart_only.update({"conversation_id": None, "resume_available": False})
        restart_public = MODULE.public_status(restart_only, sha)
        assert_symbolic_action_commands(restart_public["available_actions"], {"restart"})
        assert render(restart_only) == [
            "Provider attempt: failed; reason: agy_failed_unclassified; failure stage: none; bound result available: no; driver disposition: not_applicable.",
            "Driver evidence: 0 passed, 0 failed, 0 advisory, 0 missing; cycle: 1/2.",
            'Next safe action: fresh-attempt restart: "$PIPELINE/agy-worker.sh" restart --job-id current-text --approve-state-sha ' + sha + " --format text.",
        ]

        exhausted = dict(restart_only)
        exhausted.update({"attempt": exhausted["max_cycles"], "cycle": exhausted["max_cycles"]})
        exhausted_public = MODULE.public_status(exhausted, sha)
        assert exhausted_public["available_actions"] == []
        assert exhausted_public["next_action"] == "none"
        assert exhausted_public["next_action_command"] is None
        assert render(exhausted) == [
            "Provider attempt: failed; reason: agy_failed_unclassified; failure stage: none; bound result available: no; driver disposition: not_applicable.",
            "Driver evidence: 0 passed, 0 failed, 0 advisory, 0 missing; cycle: 2/2.",
            "Next safe action: none; the current attempt budget is exhausted.",
        ]

        runtime_exhausted = dict(restart_only)
        runtime_exhausted.update({
            "reason": "hard_deadline_exceeded", "limit_kind": "max-runtime",
            "elapsed_seconds": runtime_exhausted["max_seconds"],
        })
        runtime_public = MODULE.public_status(runtime_exhausted, sha)
        assert runtime_public["available_actions"] == []
        assert runtime_public["next_action"] == "none"
        assert runtime_public["next_action_command"] is None
        assert render(runtime_exhausted) == [
            "Provider attempt: failed; reason: hard_deadline_exceeded; failure stage: none; bound result available: no; driver disposition: not_applicable.",
            "Driver evidence: 0 passed, 0 failed, 0 advisory, 0 missing; cycle: 1/2.",
            "Next safe action: none; the current runtime budget is exhausted.",
        ]

        no_action = dict(restart_only)
        no_action.update({"status": "orphaned", "reason": "status_unavailable", "exit_code": 23})
        assert render(no_action) == [
            "Provider attempt: orphaned; reason: status_unavailable; failure stage: none; bound result available: no; driver disposition: not_applicable.",
            "Driver evidence: 0 passed, 0 failed, 0 advisory, 0 missing; cycle: 1/2.",
            "Next safe action: none.",
        ]

    check("text status distinguishes unreviewed, finalized, and non-result sanitized snapshots", text_status_has_three_sanitized_lines_and_lists_exact_actions)

    def public_candidate_digest_and_result_text_are_bound_and_non_directive() -> None:
        job, state, sha, envelope = current_candidate_fixture("public-candidate")
        public = MODULE.public_status(state, sha, job=job)
        assert public["result_available"] is True
        assert public["candidate_sha256"] == state["result_sha256"]
        assert public["candidate_sha256"] == hashlib.sha256(envelope.read_bytes()).hexdigest()
        assert MODULE.SHA_RE.fullmatch(public["candidate_sha256"]) is not None
        assert set(("candidate_sha256", "result_available", "next_action", "next_action_command", "has_prior_candidate", "phase")) <= set(public)
        assert type(public["result_available"]) is bool
        assert type(public["has_prior_candidate"]) is bool
        assert isinstance(public["next_action"], str)
        assert public["next_action_command"] is None or isinstance(public["next_action_command"], str)
        assert isinstance(public["controller_phase"], str)
        assert isinstance(public["legacy_result_provenance"], str)
        assert public["phase"] is None or isinstance(public["phase"], str)

        # Current public candidate identity is only the bound envelope digest.
        # Path text, worker prose, and legacy historical data are distractors.
        path_digest = hashlib.sha256(str(envelope).encode("utf-8")).hexdigest()
        prose_digest = hashlib.sha256(b"candidate-public-candidate").hexdigest()
        legacy_digest = "f" * 64
        state["last_success_path"] = "/private/legacy-result.json"
        state["last_success_sha256"] = legacy_digest
        state["last_success_identity"] = [1, 2, 3, 4, 5]
        assert public["candidate_sha256"] not in {path_digest, prose_digest, legacy_digest}

        captured = io.BytesIO()
        original_stdout = MODULE.sys.stdout

        class _Stdout:
            buffer = captured

        MODULE.sys.stdout = _Stdout()
        try:
            MODULE.print_text_status(state, sha, job=job)
        finally:
            MODULE.sys.stdout = original_stdout
        lines = captured.getvalue().decode("utf-8").splitlines()
        assert len(lines) == 3
        assert lines[2] == (
            'Next safe action: retrieve current bound result JSON with "$PIPELINE/agy-worker.sh" result --job-id '
            + state["job_id"]
            + " --format json; review it and run driver checks, construct Verification v2, then Codex—not the controller—may choose the eligible finalize action."
        )
        for sentinel in (str(job), str(envelope), "/private/legacy-result.json", "candidate-public-candidate"):
            assert sentinel not in "\n".join(lines)

        before = (job / MODULE.STATE_NAME).read_bytes()

        envelope.write_bytes(b"{}\n"); envelope.chmod(0o600)
        stale = MODULE.public_status(state, sha, job=job)
        assert stale["result_available"] is False
        assert stale["candidate_sha256"] is None
        assert type(stale["result_available"]) is bool
        assert not ({"result", "continue", "finalize"} & {
            item["action"] for item in stale["available_actions"]
        })
        assert (job / MODULE.STATE_NAME).read_bytes() == before

    check("public candidate digest follows only the bound result and result text leaves disposition to Codex", public_candidate_digest_and_result_text_are_bound_and_non_directive)

    def verification_v2_example_and_lifecycle_help_are_copyable() -> None:
        job, state, sha, envelope = current_candidate_fixture(
            "verification-example", wrapper_addressable=True,
        )
        public = MODULE.public_status(state, sha, job=job)
        assert public["result_available"] is True

        help_text = subprocess.run(
            ["bash", str(ROOT / "skills/agy-worker/runtime/agy-worker.sh"), "--help"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        ).stderr.decode("utf-8", "replace")
        assert "agy-worker.sh resume --job-id JOB --approve-state-sha SHA [--approve-migration-sha SHA] [--format json|text]" in help_text
        assert "agy-worker.sh restart --job-id JOB --approve-state-sha SHA [--approve-migration-sha SHA] [--format json|text]" in help_text
        assert "agy-worker.sh resume --job-id JOB --approve-state-sha STATE_SHA" in help_text
        assert "agy-worker.sh restart --job-id JOB --approve-state-sha STATE_SHA" in help_text
        assert "--compatibility-disposition proceed --approve-help-sha SHA256" in help_text
        assert (
            "agy-worker.sh finalize --job-id JOB --approve-state-sha SHA "
            "[--approve-migration-sha SHA] \\\n"
        ) in help_text
        assert "[--approve-migration-sha SHA] \\\\\\n" not in help_text

        def preparation_block(documentation: Path) -> str:
            text = documentation.read_text(encoding="utf-8")
            marker = ': "${PIPELINE:?set PIPELINE to the resolved skill runtime}"'
            marker_at = text.index(marker)
            start = text.rfind("```bash", 0, marker_at) + len("```bash\n")
            end = text.index("\n```", marker_at)
            block = text[start:end]
            final = '\n"$PIPELINE/agy-worker.sh" continue'
            assert final in block
            return block.split(final, 1)[0]

        def execute_preparation(documentation: Path, expected_state: dict) -> dict:
            state_dir = root / f"verification-example-{documentation.stem}"; state_dir.mkdir()
            completed = subprocess.run(
                ["bash", "-euo", "pipefail", "-c", preparation_block(documentation)],
                env={
                    **os.environ, "PIPELINE": str(ROOT / "skills/agy-worker/runtime"),
                    "JOB_ID": expected_state["job_id"], "STATE_DIR": str(state_dir),
                    "AGY_WORKER_LOG_DIR": str(job.parent),
                },
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            assert completed.returncode == 0, completed.stderr.decode("utf-8", "replace")
            example = json.loads((state_dir / "verification-v2.json").read_text(encoding="utf-8"))
            assert MODULE._validate_verification(example) == example
            MODULE._require_current_candidate_verification(example, expected_state)
            assert example["candidate_sha256"] == hashlib.sha256(envelope.read_bytes()).hexdigest()
            return example

        for documentation in (
            ROOT / "docs/PROJECT_WORKFLOW.md",
            ROOT / "skills/agy-worker/references/PROJECT_LIFECYCLE_AND_VERIFICATION.md",
        ):
            text = documentation.read_text(encoding="utf-8")
            assert "CANDIDATE_SHA" in text and "STATE_AND_CANDIDATE" in text
            assert 'candidate = status.get("candidate_sha256")' in text and "--approve-state-sha" in text
            assert "_validate_verification" in text
            example = execute_preparation(documentation, state)
            for invalid in ("A" * 64, "a" * 63, "a" * 65, "a" * 63 + "g"):
                mutated = dict(example); mutated["candidate_sha256"] = invalid
                try:
                    MODULE._validate_verification(mutated)
                except MODULE.DispatchError:
                    pass
                else:
                    raise AssertionError("Verification v2 accepted a non-lowercase-64hex candidate SHA")

        skill_text = (ROOT / "skills/agy-worker/SKILL.md").read_text(encoding="utf-8")
        assert "references/PROJECT_LIFECYCLE_AND_VERIFICATION.md" in skill_text
        assert "Verification v2" in skill_text
        assert "STATE_AND_CANDIDATE" not in skill_text and "CANDIDATE_SHA" not in skill_text

        null_job, null_state, _null_sha, _null_envelope = current_candidate_fixture(
            "verification-null", wrapper_addressable=True,
        )
        null_state.update({
            "status": "failed", "reason": "agy_failed_unclassified", "exit_code": 5,
            "candidate_recognized": False, "candidate_source": "none", "result_available": False,
            "result_path": None, "result_sha256": None, "result_identity": None,
            "candidate_worktree_sha256": None, "candidate_worktree_entries": None,
            "driver_disposition": "not_applicable", "phase": "attempt-failed",
            "resume_available": False, "continue_available": False,
        })
        _raw, null_sha = MODULE.write_atomic(null_job, MODULE.STATE_NAME, null_state)
        null_public = MODULE.public_status(null_state, null_sha, job=null_job)
        assert null_public["result_available"] is False and null_public["candidate_sha256"] is None
        state_dir = root / "verification-example-null"; state_dir.mkdir()
        null_run = subprocess.run(
            [
                "bash",
                "-euo",
                "pipefail",
                "-c",
                preparation_block(ROOT / "docs/PROJECT_WORKFLOW.md"),
            ],
            env={
                **os.environ, "PIPELINE": str(ROOT / "skills/agy-worker/runtime"),
                "JOB_ID": null_state["job_id"], "STATE_DIR": str(state_dir),
                "AGY_WORKER_LOG_DIR": str(null_job.parent),
            },
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        assert null_run.returncode != 0
        assert b"status has no current bound candidate" in null_run.stderr
        assert not (state_dir / "verification-v2.json").exists()

    check("published Verification v2 examples execute from one public snapshot and bind the current candidate", verification_v2_example_and_lifecycle_help_are_copyable)

    def v10_sanitized_outer_terminal_disposition_contracts() -> None:
        """Issue #82: Sanitized outer terminal disposition and V10 migration."""
        job, state, state_sha, _envelope = current_candidate_fixture("v10-terminal-disposition")
        assert state["schema_version"] == MODULE.CURRENT_STATE_SCHEMA == 11
        assert state["provider_terminal_status"] == "unknown"

        # 1. State validation bounds on provider_terminal_status enum
        for valid_status in ("unknown", "success", "error", "cancelled"):
            candidate_state = dict(state)
            candidate_state["schema_version"] = 10
            for field in MODULE.STATE_V11_FIELDS:
                candidate_state.pop(field)
            candidate_state["provider_terminal_status"] = valid_status
            MODULE.validate_state(candidate_state)

        for invalid_status in ("canceled", "SUCCESS", "ERROR", "CANCELLED", None, 123, "", "other"):
            candidate_state = dict(state)
            candidate_state["schema_version"] = 10
            for field in MODULE.STATE_V11_FIELDS:
                candidate_state.pop(field)
            candidate_state["provider_terminal_status"] = invalid_status
            try:
                MODULE.validate_state(candidate_state)
            except MODULE.DispatchError:
                pass
            else:
                raise AssertionError(f"validate_state accepted invalid provider_terminal_status {invalid_status!r}")

        # 2. Public status does not leak provider_terminal_status
        public = MODULE.public_status(state, state_sha, job=job)
        assert "provider_terminal_status" not in public

        # 3. Reportless terminal framing sets provider_terminal_status but never recognizes a candidate
        schema_paths = MODULE._bound_schemas(
            json.loads((job / MODULE.COMMAND_NAME).read_text(encoding="utf-8")), state,
        )
        assert schema_paths is not None

        for outer_raw in ("SUCCESS", "ERROR", "CANCELLED", "CANCELED"):
            stream_file = job / f"stream-reportless-{outer_raw.lower()}.jsonl"
            stream_file.write_text(
                json.dumps({"event": "init", "init": {"session_id": "s1"}}) + "\n" +
                json.dumps({"event": "result", "result": {"status": outer_raw}}) + "\n",
                encoding="utf-8",
            )
            binding, outer_status, failure_stage = MODULE._validate_terminal_envelope(
                stream_file, job / f"envelope-reportless-{outer_raw.lower()}.json",
                schema_paths[0], schema_paths[1],
            )
            assert binding is None
            assert outer_status == ("CANCELLED" if outer_raw in {"CANCELLED", "CANCELED"} else outer_raw)
            assert failure_stage == "missing_structured_output"

        # 4. Malformed/duplicate/unparsed framing leaves outer_status None (maps to unknown)
        malformed_stream = job / "stream-malformed.jsonl"
        malformed_stream.write_text(
            json.dumps({"event": "result", "result": {"status": "SUCCESS"}}) + "\n",
            encoding="utf-8",
        )
        binding, outer_status, failure_stage = MODULE._validate_terminal_envelope(
            malformed_stream, job / "envelope-malformed.json",
            schema_paths[0], schema_paths[1],
        )
        assert binding is None
        assert outer_status is None
        assert failure_stage == "framing"

        # 5. Invalid structured result leaves provider_terminal_status unknown
        invalid_result_stream = job / "stream-invalid-result.jsonl"
        invalid_result_stream.write_text(
            json.dumps({"event": "init", "init": {"session_id": "s1"}}) + "\n" +
            json.dumps({"event": "result", "result": {"status": "SUCCESS", "structured_output": {"invalid_key": 123}}}) + "\n",
            encoding="utf-8",
        )
        binding, outer_status, failure_stage = MODULE._validate_terminal_envelope(
            invalid_result_stream, job / "envelope-invalid-result.json",
            schema_paths[0], schema_paths[1],
        )
        assert binding is None
        assert failure_stage == "schema_rejection"

        # 6. Action parity between V9 and V10 states, including verification-copy.
        # Actual controller persistence is covered by the SUCCESS/ERROR/CANCELED
        # fake-provider integration cases in the owning remediation suite.
        candidate_state_v9 = dict(state)
        candidate_state_v9["schema_version"] = 9
        candidate_state_v9["candidate_recognized"] = True
        candidate_state_v9["result_available"] = True
        candidate_state_v9["status"] = "succeeded"
        candidate_state_v9["driver_disposition"] = "unreviewed"
        candidate_state_v9.pop("provider_terminal_status", None)
        for field in MODULE.STATE_V11_FIELDS:
            candidate_state_v9.pop(field)
        v9_actions = [item["action"] for item in MODULE.public_status(candidate_state_v9, "0" * 64, job=job)["available_actions"]]
        assert "verification-copy" in v9_actions, f"verification-copy missing in v9 actions: {v9_actions}"

        for st in ("unknown", "success", "error", "cancelled"):
            candidate_state_v10 = dict(candidate_state_v9)
            candidate_state_v10["schema_version"] = 10
            candidate_state_v10["provider_terminal_status"] = st
            v10_actions = [item["action"] for item in MODULE.public_status(candidate_state_v10, "0" * 64, job=job)["available_actions"]]
            assert "verification-copy" in v10_actions, f"verification-copy missing in v10 actions: {v10_actions}"
            assert v10_actions == v9_actions, f"Action mismatch for status {st}: {v10_actions} vs {v9_actions}"

    check("v10 state validates terminal-status schema, framing privacy, invalid unknown, and V9 action parity", v10_sanitized_outer_terminal_disposition_contracts)
