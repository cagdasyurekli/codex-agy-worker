#!/usr/bin/env python3
"""Offline tests for the canonical fixed-profile version attestation runner."""

from __future__ import annotations

import contextlib
import dataclasses
import hashlib
import importlib.util
import io
import json
import os
import pathlib
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable


sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "version_attestation_runner.py"
SPEC = importlib.util.spec_from_file_location("version_attestation_runner_tested", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

TMP = Path(tempfile.mkdtemp(prefix="agyworker-version-runner-tests.")).resolve()
os.chmod(TMP, 0o700)
passed = 0
failed = 0


def check(name: str, predicate: Callable[[], bool]) -> None:
    global passed, failed
    try:
        result = bool(predicate())
    except BaseException as exc:
        result = False
        detail = f" ({type(exc).__name__}: {exc})"
    else:
        detail = ""
    if result:
        passed += 1
    else:
        failed += 1
        print(f"FAIL version attestation runner: {name}{detail}")


def rejects(action: Callable[[], object]) -> bool:
    try:
        action()
    except (MODULE.AttestationError, OSError, subprocess.SubprocessError):
        return True
    return False


def profile_bytes(profile: object) -> bytes:
    return MODULE._canonical_json(dataclasses.asdict(profile))


def fresh_profile(label: str, executable: bytes = MODULE.FAKE_EXECUTABLE):
    root = TMP / label
    root.mkdir(mode=0o700)
    return root, MODULE._offline_fixture(root, executable)


def cleanup_result(root: Path, result: dict[str, object] | None) -> None:
    if result is not None:
        artifact = Path(str(result.get("artifact_root", "")))
        if artifact.is_dir() and artifact.parent == root:
            shutil.rmtree(artifact)
    if root.exists():
        shutil.rmtree(root)


source = MODULE_PATH.read_bytes()
contract = MODULE.validate_source_contract(source)
check("module imports with bytecode disabled", lambda: sys.dont_write_bytecode)
check("fixed version contract is 1.1.11", lambda: MODULE.EXPECTED_VERSION == "1.1.11" and MODULE.EXPECTED_STDOUT == b"1.1.11\n")
check("fixed bounds are three seconds and 128 bytes", lambda: MODULE.WALL_SECONDS == 3.0 and MODULE.STREAM_LIMIT == 128)
check("canonical source contract is accepted", lambda: contract["status"] == "accepted")
check("canonical source digest is exact", lambda: contract["sha256"] == hashlib.sha256(source).hexdigest())
check("runner source has no model or effort command", lambda: not re.search(rb"--(?:model|effort)|\bmodels\b|/effort", source))
check("runner source imports no network client", lambda: not re.search(rb"\b(?:socket|urllib|requests|httpx)\b", source))
check("runner has exactly one production Popen call", lambda: source.count(b"calls.popen(") == 1)
check("runner binds executable to snapshot", lambda: b"executable=profile.snapshot_path" in source)
check("runner logical argv is version-only", lambda: b'argv = [profile.source_path, "--version"]' in source)

mutated = source.replace(b"                executable=profile.snapshot_path,\n", b"", 1)
check("source validator rejects executable override removal", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = source.replace(b"            process = calls.popen(\n", b"            calls.popen(argv)\n            process = calls.popen(\n", 1)
check("source validator rejects an extra Popen", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = source.replace(b'argv = [profile.source_path, "--version"]', b'argv = [profile.source_path, "--help"]', 1)
check("source validator rejects logical argv drift", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = source.replace(b"            signal.signal(item, signal.SIG_IGN)\n", b"", 1)
check("source validator rejects terminal signal disarm removal", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = source.replace(
    b"    entry_mask = signal.pthread_sigmask(signal.SIG_BLOCK, LIFECYCLE_SIGNALS)\n",
    b"    entry_mask = signal.pthread_sigmask(signal.SIG_BLOCK, [])\n",
    1,
)
check("source validator rejects early lifecycle masking removal", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = source.replace(
    b"        blocked = signal.pthread_sigmask(signal.SIG_BLOCK, LIFECYCLE_SIGNALS)\n",
    b"        blocked = signal.pthread_sigmask(signal.SIG_BLOCK, [])\n",
    1,
)
check("source validator rejects publication signal masking removal", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = source.replace(b"            exit_code = _close_reserved_group(process, calls)\n", b"            exit_code = process.wait()\n", 1)
check("source validator rejects pre-reap group closure removal", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = source.replace(
    b"        return process.wait(timeout=0.75)\n",
    b"        result = process.wait(timeout=0.75)\n        calls.killpg(pgid, 0)\n        return result\n",
    1,
)
check("source validator rejects any post-reap group probe", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = source.replace(
    b"    startup = _production_startup_evaluation()\n",
    b"    startup = None\n",
    1,
)
check("source validator rejects isolated startup enforcement removal", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = source.replace(b"and isolated == 1\n", b"and True\n", 1)
check("source validator rejects isolated-flag enforcement removal", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = source.replace(b"and no_site == 1\n", b"and True\n", 1)
check("source validator rejects no-site enforcement removal", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = source.replace(b"and dont_write_bytecode == 1\n", b"and True\n", 1)
check("source validator rejects no-bytecode enforcement removal", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = source.replace(
    b"        if not os.path.isabs(path) or os.path.normpath(path) != path:\n",
    b"        if False:\n",
    1,
)
check("source validator rejects canonical-path enforcement removal", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = source.replace(b'        if family == "unreviewed":\n', b"        if False:\n", 1)
check("source validator rejects reviewed-family enforcement removal", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = source.replace(
    b"        if not nodes or len(nodes) != len(parts):\n",
    b"        if False:\n",
    1,
)
check("source validator rejects component-completeness removal", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = source.replace(b'            if node.kind != "directory":\n', b"            if False:\n", 1)
check("source validator rejects ancestor-directory enforcement removal", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = source.replace(b"            if node.mode & 0o002:\n", b"            if False:\n", 1)
check("source validator rejects interpreter ancestor world-write enforcement removal", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = source.replace(
    b'            if node.kind != "directory":\n',
    b'            if node.kind != "directory":\n                pass\n            if node.uid != 0:\n                add(side, "ancestor-directory", index, node)\n',
    1,
)
check("source validator rejects uid becoming interpreter trust authority", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = source.replace(b"            if node.mode & 0o002:\n", b"            if node.mode & 0o022:\n", 1)
check("source validator rejects group-write becoming interpreter trust authority", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = source.replace(b'            if leaf.kind != "symlink":\n', b'            if False:\n', 1)
check("source validator rejects alias-symlink enforcement removal", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = source.replace(b'            if family == "usr-bin":\n', b"            if False:\n", 1)
check("source validator rejects family-specific alias-kind selection removal", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = source.replace(b"            if leaf != target:\n", b"            if False:\n", 1)
check("source validator rejects regular-alias identity enforcement removal", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = source.replace(b'            if leaf.kind != "regular":\n', b'            if False:\n', 1)
check("source validator rejects regular-alias enforcement removal", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = source.replace(
    b'        else:\n            if leaf != target:\n                add(side, "leaf-identity", leaf_index, leaf)\n',
    b'        else:\n            if False:\n                add(side, "leaf-identity", leaf_index, leaf)\n',
    1,
)
check("source validator rejects descriptor identity enforcement removal", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = source.replace(
    b'            if leaf.kind != "regular":\n                add(side, "leaf-regular", leaf_index, leaf)\n            if leaf.mode & 0o002:\n',
    b'            if False:\n                add(side, "leaf-regular", leaf_index, leaf)\n            if leaf.mode & 0o002:\n',
    1,
)
check("source validator rejects regular resolved-interpreter enforcement removal", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = source.replace(b"            if leaf.mode & 0o002:\n", b"            if False:\n", 1)
check("source validator rejects interpreter leaf world-write enforcement removal", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = source.replace(b"            if leaf.mode & 0o6000:\n", b"            if False:\n", 1)
check("source validator rejects setid-interpreter enforcement removal", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = source.replace(b"            if not leaf.mode & 0o111:\n", b"            if False:\n", 1)
check("source validator rejects executable-interpreter enforcement removal", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = source.replace(
    b"    if facts.alias_target != facts.resolved_target:\n",
    b"    if False:\n",
    1,
)
check("source validator rejects alias-target identity enforcement removal", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = source.replace(
    b"    descriptor = os.open(resolved, os.O_RDONLY | CLOEXEC | NOFOLLOW)\n",
    b"    descriptor = os.open(resolved, os.O_RDONLY | CLOEXEC)\n",
    1,
)
check("source validator rejects descriptor nofollow removal", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = source.replace(b"    if parts == (\n", b"    if parts[-7:] == (\n", 1)
check("source validator rejects CLT path suffix matching", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = source.replace(
    b'    if xcode_root and parts[5:] == ("usr", "bin", "python3"):\n',
    b'    if xcode_root and parts[-3:] == ("usr", "bin", "python3"):\n',
    1,
)
check("source validator rejects Xcode path suffix matching", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = source.replace(b"    if len(tail) != 3 or", b"    if len(tail) < 3 or", 1)
check("source validator rejects framework extra-component weakening", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = source.replace(b"not _numeric_python_version(tail[0])", b"not tail[0]", 1)
check("source validator rejects numeric framework-version weakening", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = source.replace(
    b'    if family == "unreviewed" or component_index < 0:\n',
    b"    if component_index < 0:\n",
    1,
)
check("source validator rejects private basename redaction weakening", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = source.replace(
    b"        if len(failures) >= STARTUP_FAILURE_LIMIT:\n",
    b"        if False:\n",
    1,
)
check("source validator rejects diagnostic failure cap removal", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = source.replace(
    b"    if not startup.accepted:\n",
    b"    if False:\n",
    1,
)
check("source validator rejects live startup condition bypass", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = source.replace(
    b"        sys.stderr.buffer.write(_startup_diagnostic(startup))\n        return 64\n    data = sys.stdin.buffer.read(PROFILE_LIMIT + 1)\n",
    b"        sys.stderr.buffer.write(_startup_diagnostic(startup))\n        pass\n    data = sys.stdin.buffer.read(PROFILE_LIMIT + 1)\n",
    1,
)
check("source validator rejects live startup fallthrough", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = source.replace(
    b"    startup = _production_startup_evaluation()\n    if not startup.accepted:\n        sys.stderr.buffer.write(_startup_diagnostic(startup))\n        return 64\n    data = sys.stdin.buffer.read(PROFILE_LIMIT + 1)\n",
    b"    startup = _production_startup_evaluation()\n    data = sys.stdin.buffer.read(PROFILE_LIMIT + 1)\n    if not startup.accepted:\n        sys.stderr.buffer.write(_startup_diagnostic(startup))\n        return 64\n",
    1,
)
check("source validator rejects profile read before startup guard", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = source.replace(
    b"    startup = _production_startup_evaluation()\n",
    b"    ignored = sys.stdin.buffer.read(1)\n    startup = _production_startup_evaluation()\n",
    1,
)
check("source validator rejects an extra hidden profile read", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = source.replace(
    b"    data = sys.stdin.buffer.read(PROFILE_LIMIT + 1)\n",
    b"    data = sys.stdout.buffer.read(PROFILE_LIMIT + 1)\n",
    1,
)
check("source validator rejects profile read receiver drift", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = source.replace(
    b"    data = sys.stdin.buffer.read(PROFILE_LIMIT + 1)\n",
    b"    data = sys.stdin.buffer.read(PROFILE_LIMIT)\n",
    1,
)
check("source validator rejects profile read cap drift", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = source.replace(
    b"        sys.stderr.buffer.write(_startup_diagnostic(startup))\n",
    b"        pass\n",
    1,
)
check("source validator rejects startup diagnostic removal", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
mutated = source.replace(
    b'        return _collection_failure("permission", **flags)\n',
    b'        return _collection_failure("os-error", **flags)\n',
    1,
)
check("source validator rejects collection classification collapse", lambda: rejects(lambda: MODULE.validate_source_contract(mutated)))
check("source hash changes under a one-byte drift", lambda: hashlib.sha256(source + b"\n").hexdigest() != contract["sha256"])

profile_root, profile = fresh_profile("profile")
encoded = profile_bytes(profile)
parsed = MODULE.AttestationProfile.from_bytes(encoded)
check("strict profile round-trips exact fields", lambda: parsed == profile)
value = json.loads(encoded)
value["extra"] = True
check("profile rejects extra fields", lambda: rejects(lambda: MODULE.AttestationProfile.from_bytes(MODULE._canonical_json(value))))
value = json.loads(encoded)
value.pop("snapshot_path")
check("profile rejects missing fields", lambda: rejects(lambda: MODULE.AttestationProfile.from_bytes(MODULE._canonical_json(value))))
duplicate = encoded[:-2] + b',"source_path":"/tmp/other"}\n'
check("profile rejects duplicate fields", lambda: rejects(lambda: MODULE.AttestationProfile.from_bytes(duplicate)))
value = json.loads(encoded)
value["source_sha256"] = value["source_sha256"].upper()
check("profile rejects noncanonical SHA", lambda: rejects(lambda: MODULE.AttestationProfile.from_bytes(MODULE._canonical_json(value))))
value = json.loads(encoded)
value["source_path"] = "relative/agy"
check("profile rejects relative paths", lambda: rejects(lambda: MODULE.AttestationProfile.from_bytes(MODULE._canonical_json(value))))
symlink = profile_root / "source-link"
symlink.symlink_to(Path(profile.source_path))
value = json.loads(encoded)
value["source_path"] = str(symlink)
check("profile rejects symlink path aliases", lambda: rejects(lambda: MODULE.AttestationProfile.from_bytes(MODULE._canonical_json(value))))
value = json.loads(encoded)
value["source_identity"]["size"] = True
check("profile rejects boolean identity integers", lambda: rejects(lambda: MODULE.AttestationProfile.from_bytes(MODULE._canonical_json(value))))

unrelated_snapshot = dataclasses.replace(profile, snapshot_path=str(profile_root / "unrelated.snapshot"))
check("authority rejects a snapshot outside the prior root", lambda: rejects(lambda: MODULE._validate_profile_authority(unrelated_snapshot)))
repo_root_profile = dataclasses.replace(
    profile,
    temp_parent=str(ROOT),
    prior_root=str(ROOT / "agy-version-attestation.synthetic"),
    snapshot_path=str(ROOT / "agy-version-attestation.synthetic" / "agy.snapshot"),
)
check("authority rejects an evidence parent inside the repository", lambda: rejects(lambda: MODULE._validate_profile_authority(repo_root_profile)))
unsafe_source = dataclasses.replace(profile.source_identity, mode=0o700)
check("authority rejects a caller-attested unsafe source mode", lambda: rejects(lambda: MODULE._validate_profile_authority(dataclasses.replace(profile, source_identity=unsafe_source))))
unsafe_snapshot = dataclasses.replace(profile.snapshot_identity, nlink=2)
check("authority rejects a caller-attested linked snapshot", lambda: rejects(lambda: MODULE._validate_profile_authority(dataclasses.replace(profile, snapshot_identity=unsafe_snapshot))))
swapped_source = profile_root / "agy-copy"
swapped_source.write_bytes(Path(profile.source_path).read_bytes())
swapped_source.chmod(0o755)
source_swap_profile = dataclasses.replace(
    profile,
    source_path=str(swapped_source),
    source_identity=MODULE._identity(swapped_source),
)
check("prior binding rejects a source path and identity swap", lambda: rejects(lambda: MODULE._validate_prior(source_swap_profile)))
snapshot_swap_identity = dataclasses.replace(profile.snapshot_identity, ino=profile.snapshot_identity.ino + 1)
check("prior binding rejects a snapshot identity swap", lambda: rejects(lambda: MODULE._validate_prior(dataclasses.replace(profile, snapshot_identity=snapshot_swap_identity))))
check("prior binding rejects a reviewed SHA swap", lambda: rejects(lambda: MODULE._validate_prior(dataclasses.replace(profile, source_sha256="0" * 64))))

directory_source = profile_root / "directory-agy"
directory_source.mkdir(mode=0o755)
check(
    "attested executable must be a regular file",
    lambda: rejects(
        lambda: MODULE._open_attested(
            str(directory_source), MODULE._identity(directory_source), "0" * 64, 0o755
        )
    ),
)
cleanup_result(profile_root, None)


def invalid_cli() -> bool:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = MODULE.main([])
    return code == 64 and stdout.getvalue() == "" and stderr.getvalue() == "version attestation runner: invalid invocation\n"


check("invalid CLI is sanitized", invalid_cli)
check("CLI exposes only self-test and attest-version", lambda: b'["--self-test"]' in source and b'["--attest-version"]' in source)


def nonisolated_production_rejected() -> bool:
    result = subprocess.run(
        ["/usr/bin/python3", str(MODULE_PATH), "--attest-version"],
        input=b"{}\n",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 64 or result.stdout != b"":
        return False
    value = json.loads(result.stderr)
    return (
        value["schema_version"] == 1
        and value["status"] == "rejected"
        and value["isolated"] is False
        and value["failures"][0]["predicate"] == "isolated"
        and len(result.stderr) <= MODULE.STARTUP_DIAGNOSTIC_LIMIT
    )


check("production CLI rejects a nonisolated interpreter", nonisolated_production_rejected)


def isolated_startup_ignores_python_hooks() -> bool:
    hooks = TMP / "startup-hooks"
    hooks.mkdir(mode=0o700)
    marker = TMP / "startup.marker"
    (hooks / "sitecustomize.py").write_text(
        f"open({str(marker)!r}, 'wb').write(b'hook\\n')\n", encoding="utf-8"
    )
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(hooks)
    result = subprocess.run(
        ["/usr/bin/python3", "-I", "-S", "-B", str(MODULE_PATH), "--attest-version"],
        input=b"{}\n",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=environment,
        check=False,
    )
    return (
        result.returncode == 2
        and result.stdout == b""
        and result.stderr == b"version attestation runner: rejected\n"
        and not marker.exists()
    )


check("production CLI requires isolated no-site no-bytecode startup", isolated_startup_ignores_python_hooks)


def interpreter_node(
    kind: str, *, uid: int = 0, gid: int = 0, mode: int = 0o755, ino: int = 1
) -> object:
    return MODULE.InterpreterNode(
        dev=1, gid=gid, ino=ino, kind=kind, mode=mode, uid=uid
    )


def interpreter_facts(alias: str, resolved: str) -> object:
    target = interpreter_node("regular", ino=99)
    alias_parts = pathlib.PurePosixPath(alias).parts
    resolved_parts = pathlib.PurePosixPath(resolved).parts
    return MODULE.InterpreterTrustFacts(
        alias_path=alias,
        alias_nodes=tuple(
            [interpreter_node("directory", ino=index + 2) for index in range(len(alias_parts) - 1)]
            + [interpreter_node("symlink", ino=90)]
        ),
        alias_target=target,
        resolved_path=resolved,
        resolved_nodes=tuple(
            [interpreter_node("directory", ino=index + 102) for index in range(len(resolved_parts) - 1)]
            + [target]
        ),
        resolved_target=target,
    )


def facts_accepted(facts: object, **flag_overrides: int) -> bool:
    flags = {"isolated": 1, "no_site": 1, "dont_write_bytecode": 1}
    flags.update(flag_overrides)
    return MODULE._trusted_interpreter_facts(facts, **flags)


clt_facts = interpreter_facts(
    "/Library/Developer/CommandLineTools/usr/bin/python3",
    "/Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.9/bin/python3.9",
)
xcode_facts = interpreter_facts(
    "/Applications/Xcode_26.0.app/Contents/Developer/usr/bin/python3",
    "/Applications/Xcode_26.0.app/Contents/Developer/Library/Frameworks/Python3.framework/Versions/3.13/bin/python3.13",
)
usr_target = interpreter_node("regular", ino=299)
usr_facts = MODULE.InterpreterTrustFacts(
    alias_path="/usr/bin/python3",
    alias_nodes=(
        interpreter_node("directory", ino=201),
        interpreter_node("directory", ino=202),
        interpreter_node("directory", ino=203),
        usr_target,
    ),
    alias_target=usr_target,
    resolved_path="/usr/bin/python3",
    resolved_nodes=(
        interpreter_node("directory", ino=201),
        interpreter_node("directory", ino=202),
        interpreter_node("directory", ino=203),
        usr_target,
    ),
    resolved_target=usr_target,
)
check("trusted-host regular usr-bin interpreter facts are accepted", lambda: facts_accepted(usr_facts))
check("trusted-host CLT interpreter facts are accepted", lambda: facts_accepted(clt_facts))
check("trusted-host versioned Xcode interpreter facts are accepted", lambda: facts_accepted(xcode_facts))


def github_macos_xcode_facts() -> object:
    target = dataclasses.replace(xcode_facts.resolved_target, uid=501, gid=20)

    def hosted_nodes(nodes: tuple[object, ...], *, alias: bool) -> tuple[object, ...]:
        changed = []
        for index, node in enumerate(nodes):
            if index == 0:
                changed.append(node)
            elif index == 1:
                changed.append(dataclasses.replace(node, uid=0, gid=80, mode=0o775))
            elif index == len(nodes) - 1:
                changed.append(
                    dataclasses.replace(
                        node,
                        uid=501,
                        gid=20,
                        mode=0o755,
                        kind="symlink" if alias else "regular",
                        dev=target.dev,
                        ino=target.ino if not alias else node.ino,
                    )
                )
            else:
                changed.append(dataclasses.replace(node, uid=501, gid=20, mode=0o755))
        return tuple(changed)

    return dataclasses.replace(
        xcode_facts,
        alias_nodes=hosted_nodes(xcode_facts.alias_nodes, alias=True),
        alias_target=target,
        resolved_nodes=hosted_nodes(xcode_facts.resolved_nodes, alias=False),
        resolved_target=target,
    )


ci_xcode_facts = github_macos_xcode_facts()
check(
    "GitHub macOS Xcode ownership and group-write facts are accepted",
    lambda: facts_accepted(ci_xcode_facts),
)
usr_symlink = dataclasses.replace(usr_target, kind="symlink")
check(
    "usr-bin symlink alias facts are rejected",
    lambda: not facts_accepted(
        dataclasses.replace(
            usr_facts,
            alias_nodes=usr_facts.alias_nodes[:-1] + (usr_symlink,),
        )
    ),
)
check(
    "CLT regular alias facts are rejected",
    lambda: not facts_accepted(
        dataclasses.replace(
            clt_facts,
            alias_nodes=clt_facts.alias_nodes[:-1] + (clt_facts.alias_target,),
        )
    ),
)


def current_startup_accepted() -> bool:
    evaluation = MODULE._production_startup_evaluation()
    if not evaluation.accepted:
        sys.stderr.buffer.write(MODULE._startup_diagnostic(evaluation))
    return evaluation.accepted


check("current isolated system interpreter facts are accepted", current_startup_accepted)
check(
    "diagnostic family classifier covers every fixed family",
    lambda: {
        MODULE._apple_interpreter_family("/usr/bin/python3"),
        MODULE._apple_interpreter_family(
            "/Library/Developer/CommandLineTools/usr/bin/python3"
        ),
        MODULE._apple_interpreter_family(
            "/Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.9/bin/python3.9"
        ),
        MODULE._apple_interpreter_family(
            "/Applications/Xcode.app/Contents/Developer/usr/bin/python3"
        ),
        MODULE._apple_interpreter_family(
            "/Applications/Xcode_26.0.app/Contents/Developer/Library/Frameworks/Python3.framework/Versions/3.13/bin/python3.13"
        ),
        MODULE._apple_interpreter_family("/private/secret/python3"),
    }
    == {
        "usr-bin",
        "clt-usr-bin",
        "clt-framework",
        "xcode-usr-bin",
        "xcode-framework",
        "unreviewed",
    },
)
check(
    "diagnostic resolved filename classes are fixed",
    lambda: {
        MODULE._resolved_filename_class("/usr/bin/python3"),
        MODULE._resolved_filename_class("/fixed/python3.14"),
        MODULE._resolved_filename_class("/fixed/private-python"),
    }
    == {"python3", "python-major-minor", "other"},
)
check(
    "incomplete interpreter component facts are rejected",
    lambda: not facts_accepted(
        dataclasses.replace(clt_facts, resolved_nodes=clt_facts.resolved_nodes[1:])
    ),
)

user_target = dataclasses.replace(clt_facts.resolved_target, uid=501, gid=20)
user_owned = dataclasses.replace(
    clt_facts,
    alias_target=user_target,
    resolved_target=user_target,
    resolved_nodes=clt_facts.resolved_nodes[:-1] + (user_target,),
)
check("trusted-host user-owned interpreter facts are accepted", lambda: facts_accepted(user_owned))

user_alias_leaf = dataclasses.replace(clt_facts.alias_nodes[-1], uid=os.getuid() or 501)
user_alias = dataclasses.replace(
    clt_facts, alias_nodes=clt_facts.alias_nodes[:-1] + (user_alias_leaf,)
)
check("trusted-host user-owned interpreter alias facts are accepted", lambda: facts_accepted(user_alias))

writable_parent = dataclasses.replace(clt_facts.resolved_nodes[2], mode=0o775)
writable_ancestor = dataclasses.replace(
    clt_facts,
    resolved_nodes=clt_facts.resolved_nodes[:2]
    + (writable_parent,)
    + clt_facts.resolved_nodes[3:],
)
check("group-writable interpreter ancestor facts are accepted", lambda: facts_accepted(writable_ancestor))

world_writable_parent = dataclasses.replace(clt_facts.resolved_nodes[2], mode=0o777)
world_writable_ancestor = dataclasses.replace(
    clt_facts,
    resolved_nodes=clt_facts.resolved_nodes[:2]
    + (world_writable_parent,)
    + clt_facts.resolved_nodes[3:],
)
check(
    "world-writable interpreter ancestor facts are rejected",
    lambda: not facts_accepted(world_writable_ancestor),
)

world_writable_alias_parent = dataclasses.replace(clt_facts.alias_nodes[2], mode=0o777)
world_writable_alias_ancestor = dataclasses.replace(
    clt_facts,
    alias_nodes=clt_facts.alias_nodes[:2]
    + (world_writable_alias_parent,)
    + clt_facts.alias_nodes[3:],
)
check(
    "world-writable interpreter alias ancestor facts are rejected",
    lambda: not facts_accepted(world_writable_alias_ancestor),
)

writable_target = dataclasses.replace(clt_facts.resolved_target, mode=0o777)
writable_executable = dataclasses.replace(
    clt_facts,
    alias_target=writable_target,
    resolved_target=writable_target,
    resolved_nodes=clt_facts.resolved_nodes[:-1] + (writable_target,),
)
check("world-writable interpreter executable facts are rejected", lambda: not facts_accepted(writable_executable))

group_writable_target = dataclasses.replace(clt_facts.resolved_target, mode=0o775)
group_writable_executable = dataclasses.replace(
    clt_facts,
    alias_target=group_writable_target,
    resolved_target=group_writable_target,
    resolved_nodes=clt_facts.resolved_nodes[:-1] + (group_writable_target,),
)
check(
    "group-writable interpreter executable facts are accepted",
    lambda: facts_accepted(group_writable_executable),
)

setid_target = dataclasses.replace(clt_facts.resolved_target, mode=0o6755)
setid_executable = dataclasses.replace(
    clt_facts,
    alias_target=setid_target,
    resolved_target=setid_target,
    resolved_nodes=clt_facts.resolved_nodes[:-1] + (setid_target,),
)
check("setid interpreter executable facts are rejected", lambda: not facts_accepted(setid_executable))

nonexecutable_target = dataclasses.replace(clt_facts.resolved_target, mode=0o644)
nonexecutable = dataclasses.replace(
    clt_facts,
    alias_target=nonexecutable_target,
    resolved_target=nonexecutable_target,
    resolved_nodes=clt_facts.resolved_nodes[:-1] + (nonexecutable_target,),
)
check("non-executable interpreter facts are rejected", lambda: not facts_accepted(nonexecutable))

symlink_parent = dataclasses.replace(clt_facts.alias_nodes[2], kind="symlink")
symlink_ancestor = dataclasses.replace(
    clt_facts,
    alias_nodes=clt_facts.alias_nodes[:2]
    + (symlink_parent,)
    + clt_facts.alias_nodes[3:],
)
check("intermediate symlink facts are rejected", lambda: not facts_accepted(symlink_ancestor))

resolved_symlink = dataclasses.replace(clt_facts.resolved_nodes[-1], kind="symlink")
symlink_leaf = dataclasses.replace(
    clt_facts, resolved_nodes=clt_facts.resolved_nodes[:-1] + (resolved_symlink,)
)
check("resolved symlink leaf facts are rejected", lambda: not facts_accepted(symlink_leaf))

regular_alias_mismatch = dataclasses.replace(
    clt_facts.alias_nodes[-1], kind="regular", ino=1234
)
mismatched_alias = dataclasses.replace(
    clt_facts, alias_nodes=clt_facts.alias_nodes[:-1] + (regular_alias_mismatch,)
)
check("regular interpreter alias leaf is rejected", lambda: not facts_accepted(mismatched_alias))

nonregular = dataclasses.replace(clt_facts.resolved_target, kind="other")
nonregular_facts = dataclasses.replace(
    clt_facts,
    alias_target=nonregular,
    resolved_target=nonregular,
    resolved_nodes=clt_facts.resolved_nodes[:-1] + (nonregular,),
)
check("nonregular interpreter facts are rejected", lambda: not facts_accepted(nonregular_facts))
check(
    "relative interpreter facts are rejected",
    lambda: not facts_accepted(dataclasses.replace(clt_facts, alias_path="relative/python3")),
)
check(
    "nonnormalized interpreter facts are rejected",
    lambda: not facts_accepted(
        dataclasses.replace(
            clt_facts,
            alias_path="/Library/Developer/CommandLineTools/usr/../usr/bin/python3",
        )
    ),
)
arbitrary_facts = interpreter_facts(
    "/opt/unreviewed/python3", "/opt/unreviewed/python3.13"
)
check("arbitrary interpreter families are rejected", lambda: not facts_accepted(arbitrary_facts))
clt_unreviewed = dataclasses.replace(
    clt_facts,
    alias_path="/Library/Developer/CommandLineTools/unreviewed/python3",
)
check("unreviewed CLT subpaths are rejected", lambda: not facts_accepted(clt_unreviewed))
xcode_toolchain = dataclasses.replace(
    xcode_facts,
    alias_path="/Applications/Xcode_26.0.app/Contents/Developer/Toolchains/bin/python3",
)
check("alternate Xcode toolchain paths are rejected", lambda: not facts_accepted(xcode_toolchain))
nonnumeric_framework = dataclasses.replace(
    clt_facts,
    resolved_path="/Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/Current/bin/python3",
)
check("nonnumeric framework versions are rejected", lambda: not facts_accepted(nonnumeric_framework))
extra_framework_component = dataclasses.replace(
    clt_facts,
    resolved_path="/Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.9/extra/bin/python3.9",
    resolved_nodes=clt_facts.resolved_nodes[:-1]
    + (interpreter_node("directory", ino=999), clt_facts.resolved_nodes[-1]),
)
check("extra framework path components are rejected", lambda: not facts_accepted(extra_framework_component))
wrong_framework_leaf = dataclasses.replace(
    clt_facts,
    resolved_path="/Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.9/bin/python",
)
check("unreviewed framework executable names are rejected", lambda: not facts_accepted(wrong_framework_leaf))
fake_xcode = interpreter_facts(
    "/Applications/XcodeMalware.app/Contents/Developer/usr/bin/python3",
    "/Applications/XcodeMalware.app/Contents/Developer/bin/python3.13",
)
check("unreviewed Xcode-like interpreter family is rejected", lambda: not facts_accepted(fake_xcode))
check("isolated flag drift is rejected", lambda: not facts_accepted(clt_facts, isolated=0))
check("no-site flag drift is rejected", lambda: not facts_accepted(clt_facts, no_site=0))
check(
    "no-bytecode flag drift is rejected",
    lambda: not facts_accepted(clt_facts, dont_write_bytecode=0),
)
check("boolean startup flags are rejected", lambda: not facts_accepted(clt_facts, isolated=True))


def multi_failure_diagnostic() -> bool:
    alias_bad = dataclasses.replace(
        xcode_facts.alias_nodes[1], kind="other", uid=501, gid=80, mode=0o777
    )
    resolved_bad = dataclasses.replace(
        xcode_facts.resolved_nodes[1], kind="other", uid=501, gid=80, mode=0o777
    )
    facts = dataclasses.replace(
        xcode_facts,
        alias_nodes=xcode_facts.alias_nodes[:1]
        + (alias_bad,)
        + xcode_facts.alias_nodes[2:],
        resolved_nodes=xcode_facts.resolved_nodes[:1]
        + (resolved_bad,)
        + xcode_facts.resolved_nodes[2:],
    )
    evaluation = MODULE._evaluate_interpreter_trust(
        facts, isolated=0, no_site=0, dont_write_bytecode=0
    )
    expected = [
        ("flags", "isolated", -1),
        ("flags", "no-site", -1),
        ("flags", "no-bytecode", -1),
        ("alias", "ancestor-directory", 1),
        ("alias", "ancestor-not-world-writable", 1),
        ("resolved", "ancestor-directory", 1),
        ("resolved", "ancestor-not-world-writable", 1),
    ]
    observed = [
        (item.side, item.predicate, item.component_index)
        for item in evaluation.failures
    ]
    node_failures = evaluation.failures[3:]
    return (
        not evaluation.accepted
        and not evaluation.truncated
        and observed == expected
        and all(item.basename == "Applications" for item in node_failures)
        and all(item.kind == "other" for item in node_failures)
        and all(item.uid == 501 and item.gid == 80 for item in node_failures)
        and all(item.mode == "0777" for item in node_failures)
    )


check("diagnostic preserves every failure in deterministic order", multi_failure_diagnostic)


def ownership_and_group_write_remain_diagnostic_only() -> bool:
    accepted = MODULE._evaluate_interpreter_trust(
        ci_xcode_facts, isolated=1, no_site=1, dont_write_bytecode=1
    )
    bad_target = dataclasses.replace(
        ci_xcode_facts.resolved_target, mode=0o777
    )
    rejected_facts = dataclasses.replace(
        ci_xcode_facts,
        alias_target=bad_target,
        resolved_nodes=ci_xcode_facts.resolved_nodes[:-1] + (bad_target,),
        resolved_target=bad_target,
    )
    diagnostic = json.loads(
        MODULE._startup_diagnostic(
            MODULE._evaluate_interpreter_trust(
                rejected_facts, isolated=1, no_site=1, dont_write_bytecode=1
            )
        )
    )
    leaf = diagnostic["failures"][0]
    return (
        accepted.accepted
        and not accepted.failures
        and leaf["predicate"] == "leaf-not-world-writable"
        and leaf["uid"] == 501
        and leaf["gid"] == 20
        and leaf["mode"] == "0777"
    )


check(
    "uid gid and group-write are diagnostic facts rather than trust authority",
    ownership_and_group_write_remain_diagnostic_only,
)


def diagnostic_schema_is_bounded() -> bool:
    target = dataclasses.replace(clt_facts.resolved_target, mode=0o777)
    facts = dataclasses.replace(
        clt_facts,
        alias_target=target,
        resolved_target=target,
        resolved_nodes=clt_facts.resolved_nodes[:-1] + (target,),
    )
    evaluation = MODULE._evaluate_interpreter_trust(
        facts, isolated=1, no_site=1, dont_write_bytecode=1
    )
    encoded = MODULE._startup_diagnostic(evaluation)
    value = json.loads(encoded)
    recoded = (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        + "\n"
    ).encode("ascii")
    return (
        encoded == recoded
        and encoded.count(b"\n") == 1
        and len(encoded) <= MODULE.STARTUP_DIAGNOSTIC_LIMIT
        and set(value)
        == {
            "alias_family",
            "collection_error",
            "dont_write_bytecode",
            "failures",
            "isolated",
            "no_site",
            "resolved_family",
            "resolved_filename",
            "schema_version",
            "status",
            "truncated",
        }
        and set(value["failures"][0])
        == {
            "basename",
            "component_index",
            "gid",
            "kind",
            "mode",
            "predicate",
            "side",
            "uid",
        }
    )


check("diagnostic is one bounded canonical line with exact schema", diagnostic_schema_is_bounded)


def private_paths_are_redacted() -> bool:
    facts = interpreter_facts(
        "/Users/private-owner/secret/python3",
        "/private/var/hidden/token/python3.14",
    )
    bad = dataclasses.replace(facts.alias_nodes[1], uid=501, gid=20, mode=0o777)
    facts = dataclasses.replace(
        facts,
        alias_nodes=facts.alias_nodes[:1] + (bad,) + facts.alias_nodes[2:],
    )
    evaluation = MODULE._evaluate_interpreter_trust(
        facts, isolated=1, no_site=1, dont_write_bytecode=1
    )
    encoded = MODULE._startup_diagnostic(evaluation)
    forbidden = (b"Users", b"private-owner", b"secret", b"hidden", b"token", b"/private", b"/var")
    return (
        evaluation.alias_family == "unreviewed"
        and evaluation.resolved_family == "unreviewed"
        and all(item.basename == "redacted" for item in evaluation.failures)
        and not any(fragment in encoded for fragment in forbidden)
        and b"/" not in encoded
        and b'"uid":501' in encoded
        and b'"gid":20' in encoded
        and b'"mode":"0777"' in encoded
    )


check("diagnostic redacts hostile private path fragments", private_paths_are_redacted)


def failure_collection_is_capped() -> bool:
    parts = tuple("private" + str(index) for index in range(40))
    path = "/" + "/".join(parts)
    bad_nodes = tuple(
        interpreter_node("directory", uid=501, gid=501, mode=0o777, ino=index + 1)
        for index in range(len(pathlib.PurePosixPath(path).parts))
    )
    facts = dataclasses.replace(
        clt_facts,
        alias_path=path,
        alias_nodes=bad_nodes,
    )
    evaluation = MODULE._evaluate_interpreter_trust(
        facts, isolated=0, no_site=0, dont_write_bytecode=0
    )
    encoded = MODULE._startup_diagnostic(evaluation)
    return (
        not evaluation.accepted
        and evaluation.truncated
        and len(evaluation.failures) == MODULE.STARTUP_FAILURE_LIMIT
        and len(encoded) <= MODULE.STARTUP_DIAGNOSTIC_LIMIT
        and all(item.basename == "redacted" for item in evaluation.failures[3:])
    )


check("diagnostic failure collection truncates and fails closed", failure_collection_is_capped)


def diagnostic_overflow_falls_back() -> bool:
    evaluation = MODULE._collection_failure(
        "os-error", isolated=1, no_site=1, dont_write_bytecode=1
    )
    hostile = dataclasses.replace(evaluation, collection_error="x" * 9000)
    encoded = MODULE._startup_diagnostic(hostile)
    value = json.loads(encoded)
    return (
        len(encoded) <= MODULE.STARTUP_DIAGNOSTIC_LIMIT
        and value["collection_error"] == "diagnostic-overflow"
        and value["failures"] == []
        and value["truncated"] is True
        and value["status"] == "rejected"
    )


check("diagnostic hard cap uses a fixed fail-closed fallback", diagnostic_overflow_falls_back)
check(
    "collection errors use only fixed sanitized enums",
    lambda: {
        MODULE._collection_failure(
            item, isolated=1, no_site=1, dont_write_bytecode=1
        ).collection_error
        for item in ("invalid-path", "missing", "permission", "os-error", "invalid-data")
    }
    == {"invalid-path", "missing", "permission", "os-error", "invalid-data"},
)
check(
    "unknown collection error text is rejected before serialization",
    lambda: rejects(
        lambda: MODULE._collection_failure(
            "/Users/private/error text",
            isolated=1,
            no_site=1,
            dont_write_bytecode=1,
        )
    ),
)


def diagnostic_enums_are_closed() -> bool:
    evaluation = MODULE._evaluate_interpreter_trust(
        dataclasses.replace(
            xcode_facts,
            alias_nodes=xcode_facts.alias_nodes[:1]
            + (dataclasses.replace(xcode_facts.alias_nodes[1], kind="hostile"),)
            + xcode_facts.alias_nodes[2:],
        ),
        isolated=0,
        no_site=0,
        dont_write_bytecode=0,
    )
    allowed_sides = {"flags", "alias", "resolved", "collection"}
    allowed_kinds = {"directory", "regular", "symlink", "other", "not-applicable"}
    return all(
        item.side in allowed_sides
        and item.kind in allowed_kinds
        and len(item.mode) == 4
        and all(char in "01234567" for char in item.mode)
        for item in evaluation.failures
    )


check("diagnostic side kind and mode enums are closed", diagnostic_enums_are_closed)


def every_diagnostic_predicate_and_node_kind_is_exercised() -> bool:
    evaluations = []
    evaluations.append(
        MODULE._evaluate_interpreter_trust(
            dataclasses.replace(
                clt_facts,
                alias_path="relative/private/python3",
                alias_nodes=(),
            ),
            isolated=0,
            no_site=0,
            dont_write_bytecode=0,
        )
    )
    bad_ancestor = dataclasses.replace(
        xcode_facts.alias_nodes[1], kind="symlink", uid=501, gid=80, mode=0o777
    )
    evaluations.append(
        MODULE._evaluate_interpreter_trust(
            dataclasses.replace(
                xcode_facts,
                alias_nodes=xcode_facts.alias_nodes[:1]
                + (bad_ancestor,)
                + xcode_facts.alias_nodes[2:],
            ),
            isolated=1,
            no_site=1,
            dont_write_bytecode=1,
        )
    )
    world_writable_directory = dataclasses.replace(
        clt_facts.resolved_nodes[2], kind="directory", uid=501, gid=20, mode=0o777
    )
    evaluations.append(
        MODULE._evaluate_interpreter_trust(
            dataclasses.replace(
                clt_facts,
                resolved_nodes=clt_facts.resolved_nodes[:2]
                + (world_writable_directory,)
                + clt_facts.resolved_nodes[3:],
            ),
            isolated=1,
            no_site=1,
            dont_write_bytecode=1,
        )
    )
    bad_alias_leaf = dataclasses.replace(
        clt_facts.alias_nodes[-1], kind="other", uid=501, gid=20, mode=0o755
    )
    evaluations.append(
        MODULE._evaluate_interpreter_trust(
            dataclasses.replace(
                clt_facts,
                alias_nodes=clt_facts.alias_nodes[:-1] + (bad_alias_leaf,),
            ),
            isolated=1,
            no_site=1,
            dont_write_bytecode=1,
        )
    )
    regular_mismatch = dataclasses.replace(
        clt_facts.alias_nodes[-1], kind="regular", ino=444
    )
    evaluations.append(
        MODULE._evaluate_interpreter_trust(
            dataclasses.replace(
                clt_facts,
                alias_nodes=clt_facts.alias_nodes[:-1] + (regular_mismatch,),
            ),
            isolated=1,
            no_site=1,
            dont_write_bytecode=1,
        )
    )
    bad_target = dataclasses.replace(
        clt_facts.resolved_target,
        ino=555,
        kind="other",
        uid=501,
        gid=20,
        mode=0o666,
    )
    evaluations.append(
        MODULE._evaluate_interpreter_trust(
            dataclasses.replace(
                clt_facts,
                resolved_nodes=clt_facts.resolved_nodes[:-1] + (bad_target,),
                resolved_target=bad_target,
            ),
            isolated=1,
            no_site=1,
            dont_write_bytecode=1,
        )
    )
    mismatched_target_node = dataclasses.replace(
        clt_facts.resolved_target, ino=777
    )
    evaluations.append(
        MODULE._evaluate_interpreter_trust(
            dataclasses.replace(
                clt_facts,
                resolved_nodes=clt_facts.resolved_nodes[:-1]
                + (mismatched_target_node,),
            ),
            isolated=1,
            no_site=1,
            dont_write_bytecode=1,
        )
    )
    setid = dataclasses.replace(clt_facts.resolved_target, mode=0o6755)
    evaluations.append(
        MODULE._evaluate_interpreter_trust(
            dataclasses.replace(
                clt_facts,
                alias_target=setid,
                resolved_nodes=clt_facts.resolved_nodes[:-1] + (setid,),
                resolved_target=setid,
            ),
            isolated=1,
            no_site=1,
            dont_write_bytecode=1,
        )
    )
    evaluations.append(
        MODULE._collection_failure(
            "os-error", isolated=1, no_site=1, dont_write_bytecode=1
        )
    )
    failures = [item for evaluation in evaluations for item in evaluation.failures]
    predicates = {item.predicate for item in failures}
    kinds = {item.kind for item in failures}
    expected_predicates = {
        "isolated",
        "no-site",
        "no-bytecode",
        "path-canonical",
        "family-reviewed",
        "components-complete",
        "ancestor-directory",
        "ancestor-not-world-writable",
        "leaf-symlink",
        "leaf-identity",
        "leaf-regular",
        "leaf-not-world-writable",
        "leaf-no-setid",
        "leaf-executable",
        "alias-target-identity",
        "collection-error",
    }
    expected_kinds = {"directory", "regular", "symlink", "other", "not-applicable"}
    if predicates != expected_predicates or kinds != expected_kinds:
        print(
            "FAIL diagnostic enum delta:",
            sorted(predicates ^ expected_predicates),
            sorted(kinds ^ expected_kinds),
        )
    return (
        predicates == expected_predicates
        and kinds == expected_kinds
        and all(-1 <= item.component_index < 64 for item in failures)
        and all(len(item.mode) == 4 for item in failures)
    )


check(
    "every diagnostic predicate and node kind has reject evidence",
    every_diagnostic_predicate_and_node_kind_is_exercised,
)


def self_test_accepts() -> bool:
    result = MODULE.run_offline_self_test()
    return result == {
        "call_count": 1,
        "claim": "synthetic-version-attestation",
        "schema_version": 1,
        "status": "accepted",
    }


check("offline self-test uses the production function", self_test_accepts)


def exact_popen_contract() -> bool:
    root, candidate = fresh_profile("popen-contract")
    records: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def observed(*args, **kwargs):
        records.append((args, kwargs.copy()))
        return subprocess.Popen(*args, **kwargs)

    result = None
    try:
        result = MODULE.run_attestation(candidate, calls=MODULE.RunnerCalls(popen=observed), module_source=source)
        args, kwargs = records[0]
        return (
            len(records) == 1
            and args == ([candidate.source_path, "--version"],)
            and kwargs.get("executable") == candidate.snapshot_path
            and kwargs.get("stdin") is subprocess.DEVNULL
            and kwargs.get("stdout") is subprocess.PIPE
            and kwargs.get("stderr") is subprocess.PIPE
            and kwargs.get("start_new_session") is True
            and result["call_count"] == 1
        )
    finally:
        cleanup_result(root, result)


check("production function sends one exact snapshot-backed Popen", exact_popen_contract)


def result_rejected(label: str, executable: bytes) -> bool:
    root, candidate = fresh_profile(label, executable)
    try:
        return rejects(lambda: MODULE.run_attestation(candidate, module_source=source))
    finally:
        cleanup_result(root, None)


check("wrong stdout is rejected", lambda: result_rejected("wrong-stdout", b"#!/usr/bin/python3\nprint('wrong')\n"))
check("nonempty stderr is rejected", lambda: result_rejected("stderr", b"#!/usr/bin/python3\nimport sys\nsys.stderr.write('error')\nsys.stdout.write('1.1.11\\n')\n"))
check("stdout overflow is rejected", lambda: result_rejected("overflow", b"#!/usr/bin/python3\nprint('x'*129)\n"))


def timeout_closes_process_group() -> bool:
    script = b"#!/usr/bin/python3\nimport os,signal,time\nsignal.signal(signal.SIGTERM,signal.SIG_IGN)\npid=os.fork()\nif pid==0:\n time.sleep(3.4)\n open('../late.marker','wb').write(b'late\\n')\n time.sleep(5)\ntime.sleep(9)\n"
    root, candidate = fresh_profile("timeout", script)
    try:
        rejected = rejects(lambda: MODULE.run_attestation(candidate, module_source=source))
        time.sleep(0.5)
        return rejected and not any(root.glob("agy-version-recovery.*/late.marker"))
    finally:
        cleanup_result(root, None)


check("timeout kills the whole process group with no late side effect", timeout_closes_process_group)


def close_pipe_descendant_is_reaped() -> bool:
    script = b"#!/usr/bin/python3\nimport os,signal,sys,time\npid=os.fork()\nif pid==0:\n os.close(1);os.close(2);signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(.8);open('../late.marker','wb').write(b'late\\n');os._exit(0)\nsys.stdout.write('1.1.11\\n')\n"
    root, candidate = fresh_profile("close-pipe-descendant", script)
    result = None
    try:
        result = MODULE.run_attestation(candidate, module_source=source)
        time.sleep(0.9)
        return result["status"] == "accepted" and not any(
            root.glob("agy-version-recovery.*/late.marker")
        )
    finally:
        cleanup_result(root, result)


check("reserved group cleanup kills a pipe-closing TERM-ignoring descendant", close_pipe_descendant_is_reaped)


def signal_after_reap_never_reenters_group_cleanup(signum: int) -> bool:
    root, candidate = fresh_profile(f"post-reap-signal-{signum}")
    calls: list[tuple[int, int, int | None]] = []
    observed: list[subprocess.Popen[bytes]] = []

    def observed_popen(*args, **kwargs):
        process = subprocess.Popen(*args, **kwargs)
        observed.append(process)
        return process

    def signaling_killpg(group: int, sent: int) -> None:
        calls.append((group, sent, observed[0].returncode))
        try:
            os.killpg(group, sent)
        finally:
            if sent == signal.SIGKILL:
                os.kill(os.getpid(), signum)

    try:
        try:
            MODULE.run_attestation(
                candidate,
                calls=MODULE.RunnerCalls(
                    popen=observed_popen, killpg=signaling_killpg
                ),
                module_source=source,
            )
        except SystemExit as exc:
            outcome = (
                exc.code == 128 + signum
                and [(sent, returncode) for _group, sent, returncode in calls]
                == [(signal.SIGTERM, None), (signal.SIGKILL, None)]
                and not any(root.glob("agy-version-recovery.*/version.binding.sha256"))
            )
            return outcome
        return False
    finally:
        cleanup_result(root, None)


for lifecycle_signal in MODULE.LIFECYCLE_SIGNALS:
    check(
        f"post-reap signal {lifecycle_signal} never reuses group cleanup authority",
        lambda lifecycle_signal=lifecycle_signal: signal_after_reap_never_reenters_group_cleanup(
            lifecycle_signal
        ),
    )


def changed_mode_rejected() -> bool:
    root, candidate = fresh_profile("mode-drift")
    Path(candidate.snapshot_path).chmod(0o700)
    try:
        return rejects(lambda: MODULE.run_attestation(candidate, module_source=source))
    finally:
        cleanup_result(root, None)


check("snapshot mode drift is rejected before Popen", changed_mode_rejected)


def source_drift_rejected() -> bool:
    root, candidate = fresh_profile("source-drift")
    Path(candidate.source_path).write_bytes(b"changed\n")
    try:
        return rejects(lambda: MODULE.run_attestation(candidate, module_source=source))
    finally:
        cleanup_result(root, None)


check("source byte drift is rejected before Popen", source_drift_rejected)


def prior_claim_rejected() -> bool:
    root, candidate = fresh_profile("prior-claim")
    binding = Path(candidate.prior_root) / "version.binding.json"
    binding.write_bytes(MODULE._canonical_json({"claim": "wrong", "inventory": {"executable_version_bound": False}}))
    digest = hashlib.sha256(binding.read_bytes()).hexdigest()
    (Path(candidate.prior_root) / "version.binding.sha256").write_bytes((digest + "\n").encode("ascii"))
    mutated = dataclasses.replace(candidate, prior_binding_sha256=digest)
    try:
        return rejects(lambda: MODULE.run_attestation(mutated, module_source=source))
    finally:
        cleanup_result(root, None)


check("incompatible prior claim is rejected", prior_claim_rejected)


def fsync_sequence() -> bool:
    root, candidate = fresh_profile("fsync-sequence")
    real = os.fsync
    completed: list[tuple[str, int]] = []

    def traced(descriptor: int) -> None:
        value = os.fstat(descriptor)
        real(descriptor)
        completed.append(("dir" if stat.S_ISDIR(value.st_mode) else "file", descriptor))

    result = None
    try:
        result = MODULE.run_attestation(candidate, calls=MODULE.RunnerCalls(fsync=traced), module_source=source)
        roles = [role for role, _descriptor in completed]
        return roles.count("file") >= 6 and roles.count("dir") >= 12 and roles[0] == "file"
    finally:
        cleanup_result(root, result)


check("runner completes actual staged-file and parent fsync calls", fsync_sequence)


def publisher_fsync_authority(category: str, suppress: bool) -> bool:
    root = TMP / f"publisher-fsync-{category}-{'weak' if suppress else 'secure'}"
    root.mkdir(mode=0o700)
    real = os.fsync
    completed: list[tuple[str, int]] = []
    ordinals = {"file": 0, "dir": 0}
    target = {
        "staged-file": ("file", 1),
        "post-link-parent": ("dir", 1),
        "post-temp-parent": ("dir", 2),
        "rollback-parent": ("dir", 1),
        "failure-cleanup-parent": ("dir", 1),
    }[category]

    def traced(descriptor: int) -> None:
        value = os.fstat(descriptor)
        role = "dir" if stat.S_ISDIR(value.st_mode) else "file"
        ordinals[role] += 1
        if suppress and (role, ordinals[role]) == target:
            return
        real(descriptor)
        completed.append((role, descriptor))

    publisher = MODULE.Publisher(root, MODULE.RunnerCalls(fsync=traced))
    try:
        if category in {"staged-file", "post-link-parent", "post-temp-parent"}:
            publisher.publish("binding.json", b"binding\n")
            expected = [("file", completed[0][1] if completed and completed[0][0] == "file" else -1), ("dir", publisher.root_fd), ("dir", publisher.root_fd)]
            accepted = completed == expected
            publisher.rollback()
        elif category == "rollback-parent":
            publisher.calls = MODULE.REAL_CALLS
            publisher.publish("binding.json", b"binding\n")
            publisher.calls = MODULE.RunnerCalls(fsync=traced)
            completed.clear()
            ordinals.update(file=0, dir=0)
            publisher.rollback()
            accepted = completed == [("dir", publisher.root_fd)]
            real(publisher.root_fd)
        else:
            sentinel = root / "binding.json"
            sentinel.write_bytes(b"existing\n")
            sentinel.chmod(0o600)
            rejected = rejects(lambda: publisher.publish("binding.json", b"replacement\n"))
            expected = [("file", completed[0][1] if completed and completed[0][0] == "file" else -1), ("dir", publisher.root_fd)]
            accepted = rejected and completed == expected
            sentinel.unlink()
            real(publisher.root_fd)
        return accepted if not suppress else not accepted
    finally:
        try:
            publisher.rollback()
        except BaseException:
            pass
        publisher.close()
        shutil.rmtree(root)


for fsync_category in (
    "staged-file",
    "post-link-parent",
    "post-temp-parent",
    "rollback-parent",
    "failure-cleanup-parent",
):
    check(
        f"{fsync_category} completes the real fsync syscall",
        lambda fsync_category=fsync_category: publisher_fsync_authority(fsync_category, False),
    )
    check(
        f"{fsync_category} syscall omission mutation is killed",
        lambda fsync_category=fsync_category: publisher_fsync_authority(fsync_category, True),
    )


def interrupted_during_preflight(signum: int) -> bool:
    root, candidate = fresh_profile(f"preflight-signal-{signum}")
    original = MODULE._validate_prior

    def signaling(profile) -> None:
        os.kill(os.getpid(), signum)
        original(profile)

    MODULE._validate_prior = signaling
    try:
        try:
            MODULE.run_attestation(candidate, module_source=source)
        except SystemExit as exc:
            return exc.code == 128 + signum and not any(
                root.glob("agy-version-recovery.*")
            )
        return False
    finally:
        MODULE._validate_prior = original
        cleanup_result(root, None)


for lifecycle_signal in MODULE.LIFECYCLE_SIGNALS:
    check(
        f"preflight signal {lifecycle_signal} exits exactly before root creation",
        lambda lifecycle_signal=lifecycle_signal: interrupted_during_preflight(
            lifecycle_signal
        ),
    )


def double_signal_during_intermediate_publication(first: int) -> bool:
    second = signal.SIGTERM if first != signal.SIGTERM else signal.SIGHUP
    root, candidate = fresh_profile(f"publication-signal-{first}-{second}")
    real = os.fsync
    file_calls = 0
    directory_calls = 0

    def signaling_fsync(descriptor: int) -> None:
        nonlocal file_calls, directory_calls
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            directory_calls += 1
            if directory_calls == 3:
                os.kill(os.getpid(), second)
        else:
            file_calls += 1
            if file_calls == 1:
                os.kill(os.getpid(), first)
        real(descriptor)

    try:
        try:
            MODULE.run_attestation(
                candidate,
                calls=MODULE.RunnerCalls(fsync=signaling_fsync),
                module_source=source,
            )
        except SystemExit as exc:
            artifact_roots = list(root.glob("agy-version-recovery.*"))
            expected_dirs = {"cwd", "home", "tmp", "xdg-config", "xdg-cache", "xdg-state"}
            return (
                exc.code == 128 + first
                and file_calls >= 1
                and directory_calls >= 3
                and len(artifact_roots) == 1
                and {item.name for item in artifact_roots[0].iterdir()} == expected_dirs
                and all(not any((artifact_roots[0] / name).iterdir()) for name in expected_dirs)
            )
        return False
    finally:
        cleanup_result(root, None)


for lifecycle_signal in MODULE.LIFECYCLE_SIGNALS:
    check(
        f"distinct double signal beginning {lifecycle_signal} preserves the first during publication cleanup",
        lambda lifecycle_signal=lifecycle_signal: double_signal_during_intermediate_publication(
            lifecycle_signal
        ),
    )


def interrupted_during_popen(signum: int) -> bool:
    root, candidate = fresh_profile(f"signal-{signum}")

    def signaling(*args, **kwargs):
        process = subprocess.Popen(*args, **kwargs)
        os.kill(os.getpid(), signum)
        return process

    try:
        try:
            MODULE.run_attestation(candidate, calls=MODULE.RunnerCalls(popen=signaling), module_source=source)
        except SystemExit as exc:
            return exc.code == 128 + signum and not any(root.glob("agy-version-recovery.*/version.binding.sha256"))
        return False
    finally:
        cleanup_result(root, None)


for lifecycle_signal in MODULE.LIFECYCLE_SIGNALS:
    check(
        f"signal {lifecycle_signal} exits exactly and publishes no completion marker",
        lambda lifecycle_signal=lifecycle_signal: interrupted_during_popen(lifecycle_signal),
    )


def double_signal_during_completion(signum: int) -> bool:
    root, candidate = fresh_profile(f"double-signal-{signum}")
    real = os.fsync
    directory_calls = 0

    def signaling_fsync(descriptor: int) -> None:
        nonlocal directory_calls
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            directory_calls += 1
            if directory_calls in (13, 15):
                os.kill(os.getpid(), signum)
        real(descriptor)

    try:
        try:
            MODULE.run_attestation(
                candidate,
                calls=MODULE.RunnerCalls(fsync=signaling_fsync),
                module_source=source,
            )
        except SystemExit as exc:
            return (
                exc.code == 128 + signum
                and directory_calls >= 15
                and not any(root.glob("agy-version-recovery.*/version.binding.sha256"))
            )
        return False
    finally:
        cleanup_result(root, None)


for lifecycle_signal in MODULE.LIFECYCLE_SIGNALS:
    check(
        f"double signal {lifecycle_signal} during marker rollback preserves exact exit",
        lambda lifecycle_signal=lifecycle_signal: double_signal_during_completion(lifecycle_signal),
    )

shutil.rmtree(TMP)
print(f"version attestation runner offline tests: {passed} passed, {failed} failed")
raise SystemExit(0 if failed == 0 else 1)
