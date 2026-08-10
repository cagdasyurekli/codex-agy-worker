#!/usr/bin/env python3
"""Offline adversarial tests for the process-inert capture-profile builder."""

from __future__ import annotations

import ast
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import shutil
import signal
import stat
import sys
import tempfile
from pathlib import Path
from typing import Callable


sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "models_capture_profile.py"
RUNNER_PATH = ROOT / "scripts" / "models_capture_runner.py"
SPEC = importlib.util.spec_from_file_location("models_capture_profile_tested", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)
RUNNER_SPEC = importlib.util.spec_from_file_location("models_capture_runner_parity", RUNNER_PATH)
assert RUNNER_SPEC is not None and RUNNER_SPEC.loader is not None
RUNNER = importlib.util.module_from_spec(RUNNER_SPEC)
sys.modules[RUNNER_SPEC.name] = RUNNER
RUNNER_SPEC.loader.exec_module(RUNNER)

TMP = Path(tempfile.mkdtemp(prefix="agyworker-models-capture-profile-tests.")).resolve()
TMP.chmod(0o700)
SOURCE = MODULE_PATH.read_bytes()
RUNNER_SOURCE = RUNNER_PATH.read_bytes()
passed = 0
failed = 0


def check(name: str, predicate: Callable[[], bool]) -> None:
    global passed, failed
    try:
        accepted = bool(predicate())
    except BaseException as exc:
        accepted = False
        detail = f" ({type(exc).__name__}: {exc})"
    else:
        detail = ""
    if accepted:
        passed += 1
    else:
        failed += 1
        print(f"FAIL models capture profile: {name}{detail}")


def rejects(action: Callable[[], object]) -> bool:
    try:
        action()
    except (MODULE.ModelsCaptureProfileError, OSError, ValueError):
        return True
    return False


def mutate(old: bytes, new: bytes) -> bytes:
    # Each mutation retains the reviewed normalized-AST mismatch: it must
    # fail closed before this builder can read stdin or open an authority path.
    position = SOURCE.find(old)
    if position < 0:
        raise AssertionError(f"missing mutation target {old!r}")
    return SOURCE[:position] + new + SOURCE[position + len(old):]
def write_private(path: Path, data: bytes) -> None:
    path.write_bytes(data)
    path.chmod(0o600)


def patch_expected(source_sha: str, binding_sha: str) -> tuple[str, str]:
    old_source = MODULE.EXPECTED_SOURCE_SHA256
    old_binding = MODULE.EXPECTED_VERSION_BINDING_SHA256
    MODULE.EXPECTED_SOURCE_SHA256 = source_sha
    MODULE.EXPECTED_VERSION_BINDING_SHA256 = binding_sha
    return old_source, old_binding


def restore_expected(old: tuple[str, str]) -> None:
    MODULE.EXPECTED_SOURCE_SHA256, MODULE.EXPECTED_VERSION_BINDING_SHA256 = old


def fixture(name: str) -> tuple[Path, dict[str, str], tuple[str, str]]:
    root = TMP / name
    root.mkdir(mode=0o700)
    account = root / "account"
    account.mkdir(mode=0o700)
    evidence_parent = root / "evidence-parent"
    evidence_parent.mkdir(mode=0o700)
    version_root = evidence_parent / "version-root"
    version_root.mkdir(mode=0o700)
    output_parent = root / "output-parent"
    output_parent.mkdir(mode=0o700)
    source = root / "source"
    snapshot = root / "snapshot-external"
    payload = b"synthetic-reviewed-agy-1.1.11\n"
    source.write_bytes(payload)
    snapshot.write_bytes(payload)
    source.chmod(0o755)
    snapshot.chmod(0o500)
    source_identity = MODULE.FileIdentity.from_stat(source.stat())
    snapshot_identity = MODULE.FileIdentity.from_stat(snapshot.stat())
    source_sha = hashlib.sha256(payload).hexdigest()
    binding = {
        "claim": "snapshot-version-recovery",
        "snapshot": {"pre": snapshot_identity.as_dict(), "post": snapshot_identity.as_dict(), "sha256": source_sha},
        "source": {"pre": source_identity.as_dict(), "post": source_identity.as_dict(), "sha256": source_sha},
        "version": {"exit": 0, "logical_argv": [str(source), "--version"], "observed": "1.1.11", "popen_count": 1},
    }
    binding_raw = MODULE._canonical_json(binding)
    binding_sha = hashlib.sha256(binding_raw).hexdigest()
    for directory in ("cwd", "home", "tmp", "xdg-cache", "xdg-config", "xdg-state"):
        child = version_root / directory
        child.mkdir(mode=0o700)
    write_private(version_root / "runner.py", b"runner\n")
    write_private(version_root / "runner.py.sha256", b"0" * 64 + b"\n")
    write_private(version_root / "version.binding.json", binding_raw)
    write_private(version_root / "version.binding.sha256", (binding_sha + "\n").encode("ascii"))
    write_private(version_root / "version.stdout", b"1.1.11\n")
    write_private(version_root / "version.stderr", b"")
    write_private(version_root / "version.summary.json", b"{}\n")
    request = {
        "account_home": str(account),
        "output_path": str(output_parent / MODULE.OUTPUT_BASENAME),
        "snapshot_path": str(snapshot),
        "source_path": str(source),
        "version_root": str(version_root),
    }
    return root, request, patch_expected(source_sha, binding_sha)


def cleanup(root: Path, old: tuple[str, str]) -> None:
    restore_expected(old)
    if root.exists():
        shutil.rmtree(root)


def normalized_ast_sha256(data: bytes) -> str:
    tree = ast.parse(data.decode("utf-8"))
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "MODULE_AST_SHA256"
        ):
            node.value = ast.Constant(value="PINNED-MODULE-AST")
            break
    return hashlib.sha256(ast.dump(tree, include_attributes=False).encode("utf-8")).hexdigest()


contract = MODULE.validate_source_contract(SOURCE)
check("module imports with bytecode disabled", lambda: sys.dont_write_bytecode)
check("canonical source contract is accepted", lambda: contract["sha256"] == hashlib.sha256(SOURCE).hexdigest())
check("builder has no process-capable runner imports", lambda: all(not (isinstance(node, (ast.Import, ast.ImportFrom)) and any(alias.name in {"models_capture_runner", "models_attestation_runner", "version_attestation_runner"} for alias in node.names)) for node in ast.walk(ast.parse(SOURCE.decode("utf-8")))))
check("builder has no child launch syntax", lambda: all(not (isinstance(node, ast.Attribute) and node.attr in {"Popen", "run", "call", "system", "fork", "spawn", "popen"}) for node in ast.walk(ast.parse(SOURCE.decode("utf-8")))))
check("builder accepts exactly prepare or validate", lambda: MODULE.main(["--other"]) == 64 and MODULE.main(["--prepare", "--validate"]) == 64)
check("builder profile keys match capture runner exactly", lambda: MODULE.PROFILE_KEYS == RUNNER.PROFILE_KEYS)
check("builder serialization matches capture runner exactly", lambda: MODULE._canonical_json({"z": 1, "a": 2}) == RUNNER._canonical_json({"z": 1, "a": 2}))
check("builder preserves reviewed source pin", lambda: MODULE.EXPECTED_SOURCE_SHA256 == "198ff7c3f6d173daa510b0814aa70c6ce14c94035bcd4707a3c0e79fa38a7bc3")
check("builder preserves reviewed version binding pin", lambda: MODULE.EXPECTED_VERSION_BINDING_SHA256 == "72d0bba6040b46109f6968528697579dd1dbe7fdae949e68fb22e6f058452ea2")
check("builder profile limit is fixed", lambda: MODULE.PROFILE_LIMIT == 16_384)
check(
    "exact reviewed builder identity is pinned externally",
    lambda: (
        stat.S_IMODE(MODULE_PATH.stat().st_mode),
        len(SOURCE),
        hashlib.sha256(SOURCE).hexdigest(),
        normalized_ast_sha256(SOURCE),
    ) == (
        0o755,
        41_149,
        "8da7d669d9d7b8bde3feac18e1a42ec576f4d5ef72424c00a4f7b8564da6c883",
        "08fb914a7d33cc46979e23a7741b7686345f719046d6a1325decde730ca289b0",
    ),
)


for index, (old, new) in enumerate((
    (b'request = _canonical_request(data, REQUEST_KEYS, "prepare")', b"request = _strict_json(data)"),
    (b"_validate_version_evidence(version_root, source_path, source_identity, snapshot_identity)", b"pass"),
    (b"_verify_regular(source_parent, source_fd, source_identity, source_leaf, EXPECTED_SOURCE_SHA256)", b"pass"),
    (b"_verify_regular(snapshot_parent, snapshot_fd, snapshot_identity, snapshot_leaf, EXPECTED_SOURCE_SHA256)", b"pass"),
    (b"os.O_WRONLY | os.O_CREAT | os.O_EXCL | CLOEXEC | NOFOLLOW", b"os.O_WRONLY | os.O_CREAT | CLOEXEC | NOFOLLOW"),
    (b"os.link(\n            temporary_name, final_name, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False\n        )", b"os.rename(temporary_name, final_name, src_dir_fd=parent, dst_dir_fd=parent)"),
    (b"os.fsync(parent)\n        if not _remove_owned(parent, temporary_name, temporary_identity):", b"if not _remove_owned(parent, temporary_name, temporary_identity):"),
    (b"if _canonical_json(profile.as_mapping()) != data:", b"if False:"),
    (b"or value.st_nlink != 1\n            or value.st_size > maximum", b"or False"),
    (b"or stat.S_IMODE(value.st_mode) != 0o600\n            or value.st_nlink != 1", b"or False"),
    (b"or value.st_uid != os.getuid()\n            or stat.S_IMODE(value.st_mode) != 0o600", b"or False"),
    (b"leaf.st_uid != os.getuid() or stat.S_IMODE(leaf.st_mode) != 0o700", b"False"),
    (b"if set(os.listdir(descriptor)) != VERSION_ROOT_FILES:", b"if False:"),
    (b"or observed.get(\"logical_argv\") != [source_path, \"--version\"]", b"or False"),
    (b"or _read_at(descriptor, \"version.stdout\", 128) != b\"1.1.11\\n\"", b"or False"),
    (b"or _read_at(descriptor, \"version.stderr\", 128) != b\"\"", b"or False"),
    (b"import os\n", b"import os as os_alias\n"),
    (b"if list(argv) not in ([\"--prepare\"], [\"--validate\"]):", b"if False:"),
), 1):
    check(f"reviewed-source mutation {index} is killed", lambda old=old, new=new: rejects(lambda: MODULE.validate_source_contract(mutate(old, new))))


hidden_subprocess = SOURCE.replace(
    b"import sys\n",
    b"import sys\nfrom subprocess import Popen\n\n\ndef hidden_launch():\n    return Popen([])\n",
    1,
)
check("hidden subprocess ImportFrom helper changes reviewed identity", lambda: rejects(lambda: MODULE.validate_source_contract(hidden_subprocess)))
hidden_os = SOURCE.replace(
    b"import sys\n",
    b"import sys\nfrom os import system as hidden_system\n\n\ndef hidden_launch():\n    return hidden_system(\"x\")\n",
    1,
)
check("hidden os launch alias changes reviewed identity", lambda: rejects(lambda: MODULE.validate_source_contract(hidden_os)))
hidden_name = SOURCE.replace(
    b"class ModelsCaptureProfileError(ValueError):\n",
    b"def hidden_launch():\n    return Popen([])\n\n\nclass ModelsCaptureProfileError(ValueError):\n",
    1,
)
check("hidden launch name call changes reviewed identity", lambda: rejects(lambda: MODULE.validate_source_contract(hidden_name)))


def helper_source(import_line: bytes, body: bytes) -> bytes:
    return SOURCE.replace(
        b"import sys\n",
        b"import sys\n" + import_line + b"\n\n\ndef hidden_authority():\n    " + body + b"\n",
        1,
    )


for helper_name, helper_import, helper_body in (
    ("direct __import__", b"", b'return __import__("subprocess")'),
    ("aliased __import__", b"from builtins import __import__ as hidden_import", b'return hidden_import("subprocess")'),
    ("direct importlib import_module", b"import importlib", b'return importlib.import_module("subprocess")'),
    ("aliased import_module", b"from importlib import import_module as hidden_import_module", b'return hidden_import_module("subprocess")'),
    ("direct os.environ", b"", b'return os.environ.get("HOME")'),
    ("aliased os.environ", b"from os import environ as hidden_environ", b'return hidden_environ.get("HOME")'),
    ("extra os.listdir", b"", b'return os.listdir("/")'),
    ("aliased os.listdir", b"from os import listdir as hidden_listdir", b'return hidden_listdir("/")'),
    ("direct Path.home", b"from pathlib import Path", b"return Path.home()"),
    ("aliased Path.home", b"from pathlib import Path as HiddenPath", b"return HiddenPath.home()"),
):
    check(
        f"hidden {helper_name} helper changes reviewed identity",
        lambda helper_import=helper_import, helper_body=helper_body: rejects(
            lambda: MODULE.validate_source_contract(helper_source(helper_import, helper_body))
        ),
    )


def helper_mediated_source(body: bytes) -> bytes:
    value = helper_source(b"", body)
    return value.replace(
        b'request = _canonical_request(data, REQUEST_KEYS, "prepare")',
        b'hidden_authority()\n    request = _canonical_request(data, REQUEST_KEYS, "prepare")',
        1,
    )


for helper_name, helper_data in (
    ("getattr environment", helper_source(b"", b'return getattr(os, "environ")')),
    ("imported-module dictionary lookup", helper_source(b"", b'return os.__dict__["listdir"]')),
    ("globals builtins import process", helper_source(b"", b'return globals()["__builtins__"]["__import__"]("subprocess")')),
    ("helper-mediated getattr listdir", helper_mediated_source(b'return getattr(os, "listdir")')),
):
    check(
        f"hidden {helper_name} validator-only mutation changes reviewed identity",
        lambda helper_data=helper_data: rejects(lambda: MODULE.validate_source_contract(helper_data)),
    )


def positive_prepare() -> bool:
    root, request, old = fixture("positive")
    try:
        raw = MODULE._canonical_json(request)
        result = MODULE.prepare(raw)
        profile_path = Path(request["output_path"])
        profile_raw = profile_path.read_bytes()
        profile = MODULE.CaptureProfile.from_bytes(profile_raw)
        runner_profile = RUNNER.CaptureProfile.from_bytes(profile_raw)
        original_runner = (
            RUNNER.EXPECTED_SOURCE_SHA256,
            RUNNER.EXPECTED_VERSION_BINDING_SHA256,
            RUNNER.EXPECTED_SNAPSHOT_INODE,
            RUNNER.EXPECTED_SNAPSHOT_SIZE,
        )
        try:
            RUNNER.EXPECTED_SOURCE_SHA256 = profile.source_sha256
            RUNNER.EXPECTED_VERSION_BINDING_SHA256 = profile.version_binding_sha256
            RUNNER.EXPECTED_SNAPSHOT_INODE = profile.snapshot_identity.ino
            RUNNER.EXPECTED_SNAPSHOT_SIZE = profile.snapshot_identity.size
            production_accepts = RUNNER._validate_production_profile(runner_profile) is None
        finally:
            (
                RUNNER.EXPECTED_SOURCE_SHA256,
                RUNNER.EXPECTED_VERSION_BINDING_SHA256,
                RUNNER.EXPECTED_SNAPSHOT_INODE,
                RUNNER.EXPECTED_SNAPSHOT_SIZE,
            ) = original_runner
        return (
            result == {"profile_sha256": hashlib.sha256(profile_raw).hexdigest(), "status": "prepared"}
            and profile == MODULE.CaptureProfile.from_bytes(profile_raw)
            and runner_profile.as_mapping() == profile.as_mapping()
            and production_accepts
            and profile.snapshot_path == request["snapshot_path"]
            and profile.snapshot_path != profile.version_root + "/agy.snapshot"
            and stat.S_IMODE(profile_path.stat().st_mode) == 0o600
            and profile_path.stat().st_nlink == 1
        )
    finally:
        cleanup(root, old)


check("external snapshot profile prepares canonical paired runner bytes", positive_prepare)


def positive_validate() -> bool:
    root, request, old = fixture("validate")
    try:
        prepared = MODULE.prepare(MODULE._canonical_json(request))
        result = MODULE.validate(MODULE._canonical_json({"profile_path": request["output_path"]}))
        return result == {"profile_sha256": prepared["profile_sha256"], "status": "valid"}
    finally:
        cleanup(root, old)


check("prepared profile validates against held external evidence", positive_validate)


def noncanonical_request_rejects(mode: str, kind: str) -> bool:
    root, request, old = fixture("noncanonical-request-" + mode + "-" + kind)
    original_open = MODULE._open_directory
    try:
        canonical = MODULE._canonical_json(
            request if mode == "prepare" else {"profile_path": request["output_path"]}
        )
        value = json.loads(canonical.decode("ascii"))
        if kind == "indent":
            raw = json.dumps(value, sort_keys=True, indent=1).encode("ascii") + b"\n"
        elif kind == "order":
            if mode == "validate":
                # The single validate key cannot be reordered; an equivalent JSON
                # escape proves the raw-byte canonicality gate is still enforced.
                raw = canonical.replace(b'":"/', b'":"\\u002f', 1)
            else:
                raw = json.dumps(
                    {key: value[key] for key in reversed(tuple(value))}, separators=(",", ":")
                ).encode("ascii") + b"\n"
        elif kind == "missing-newline":
            raw = canonical.rstrip(b"\n")
        elif kind == "double-newline":
            raw = canonical + b"\n"
        else:
            raw = canonical.replace(b"{", b'{"profile_path":"x",', 1)
        MODULE._open_directory = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("authority opened")
        )
        action = MODULE.prepare if mode == "prepare" else MODULE.validate
        return rejects(lambda: action(raw)) and not Path(request["output_path"]).exists()
    finally:
        MODULE._open_directory = original_open
        cleanup(root, old)


for request_mode in ("prepare", "validate"):
    for request_kind in ("indent", "order", "missing-newline", "double-newline", "duplicate"):
        check(
            f"{request_mode} rejects canonical-request {request_kind} before authority open",
            lambda request_mode=request_mode, request_kind=request_kind: noncanonical_request_rejects(request_mode, request_kind),
        )


def stored_profile_field_drift_rejects(field: str) -> bool:
    root, request, old = fixture("stored-field-" + field)
    try:
        MODULE.prepare(MODULE._canonical_json(request))
        path = Path(request["output_path"])
        value = json.loads(path.read_text("ascii"))
        if field == "account_home":
            value[field] = value[field] + "-other"
        elif field == "account_home_identity":
            value[field]["ino"] += 1
        elif field in {"snapshot_identity", "source_identity"}:
            value[field]["ino"] += 1
        elif field == "snapshot_path":
            value[field] = value["source_path"]
        elif field == "source_path":
            value[field] = value["snapshot_path"]
        elif field in {"source_sha256", "version_binding_sha256"}:
            value[field] = "0" * 64
        elif field == "temp_parent":
            value[field] = value[field] + "-other"
        elif field == "version_root":
            value[field] = value[field] + "-other"
        else:
            raise AssertionError(field)
        write_private(path, MODULE._canonical_json(value))
        return rejects(lambda: MODULE.validate(MODULE._canonical_json({"profile_path": str(path)})))
    finally:
        cleanup(root, old)


for profile_field in (
    "account_home",
    "account_home_identity",
    "snapshot_identity",
    "snapshot_path",
    "source_identity",
    "source_path",
    "source_sha256",
    "temp_parent",
    "version_binding_sha256",
    "version_root",
):
    check(
        f"validate rederives and rejects stored {profile_field} drift",
        lambda profile_field=profile_field: stored_profile_field_drift_rejects(profile_field),
    )


def live_profile_field_drift_rejects(field: str) -> bool:
    root, request, old = fixture("live-field-" + field)
    try:
        MODULE.prepare(MODULE._canonical_json(request))
        if field in {"account_home", "account_home_identity"}:
            Path(request["account_home"]).chmod(0o755)
        elif field in {"snapshot_identity", "snapshot_path"}:
            snapshot = Path(request["snapshot_path"])
            snapshot.chmod(0o600)
            snapshot.write_bytes(b"replacement-snapshot\n")
            snapshot.chmod(0o500)
        elif field in {"source_identity", "source_path", "source_sha256"}:
            Path(request["source_path"]).write_bytes(b"replacement-source\n")
            Path(request["source_path"]).chmod(0o755)
        elif field == "temp_parent":
            Path(request["output_path"]).parent.chmod(0o755)
        elif field == "version_binding_sha256":
            write_private(Path(request["version_root"]) / "version.binding.json", b"{}\n")
        elif field == "version_root":
            write_private(Path(request["version_root"]) / "unexpected", b"x\n")
        else:
            raise AssertionError(field)
        return rejects(lambda: MODULE.validate(MODULE._canonical_json({"profile_path": request["output_path"]})))
    finally:
        cleanup(root, old)


for profile_field in (
    "account_home",
    "account_home_identity",
    "snapshot_identity",
    "snapshot_path",
    "source_identity",
    "source_path",
    "source_sha256",
    "temp_parent",
    "version_binding_sha256",
    "version_root",
):
    check(
        f"validate rederives and rejects live {profile_field} drift",
        lambda profile_field=profile_field: live_profile_field_drift_rejects(profile_field),
    )


def request_reject(kind: str) -> bool:
    root, request, old = fixture("request-" + kind)
    try:
        value: object = dict(request)
        if kind == "extra":
            value["unexpected"] = "x"
        elif kind == "missing":
            del value["version_root"]
        elif kind == "relative":
            value["account_home"] = "relative"
        elif kind == "output-name":
            value["output_path"] = str(Path(request["output_path"]).with_name("profile.json"))
        elif kind == "output-overlap":
            value["output_path"] = str(Path(request["version_root"]) / MODULE.OUTPUT_BASENAME)
        elif kind == "account-overlap":
            value["account_home"] = request["version_root"]
        return rejects(lambda: MODULE.prepare(MODULE._canonical_json(value)))
    finally:
        cleanup(root, old)


for label in ("extra", "missing", "relative", "output-name", "output-overlap", "account-overlap"):
    check(f"prepare rejects {label} request authority", lambda label=label: request_reject(label))


def drift_reject(kind: str) -> bool:
    root, request, old = fixture("drift-" + kind)
    try:
        path = Path(request["source_path"] if kind.startswith("source") else request["snapshot_path"])
        if kind == "source-bytes":
            path.write_bytes(b"changed\n")
        elif kind == "snapshot-bytes":
            path.chmod(0o700)
            path.write_bytes(b"changed\n")
            path.chmod(0o500)
        elif kind == "source-mode":
            path.chmod(0o700)
        elif kind == "snapshot-mode":
            path.chmod(0o700)
        elif kind == "source-links":
            os.link(path, root / "source-link")
        elif kind == "snapshot-links":
            os.link(path, root / "snapshot-link")
        return rejects(lambda: MODULE.prepare(MODULE._canonical_json(request)))
    finally:
        cleanup(root, old)


for label in ("source-bytes", "snapshot-bytes", "source-mode", "snapshot-mode", "source-links", "snapshot-links"):
    check(f"prepare rejects {label} drift", lambda label=label: drift_reject(label))


def evidence_reject(kind: str) -> bool:
    root, request, old = fixture("evidence-" + kind)
    try:
        version_root = Path(request["version_root"])
        if kind == "extra":
            write_private(version_root / "unexpected", b"x")
        elif kind == "stdout":
            write_private(version_root / "version.stdout", b"1.1.10\n")
        elif kind == "stderr":
            write_private(version_root / "version.stderr", b"x")
        elif kind == "binding":
            write_private(version_root / "version.binding.sha256", b"0" * 64 + b"\n")
        elif kind == "mode":
            (version_root / "version.binding.json").chmod(0o644)
        return rejects(lambda: MODULE.prepare(MODULE._canonical_json(request)))
    finally:
        cleanup(root, old)


for label in ("extra", "stdout", "stderr", "binding", "mode"):
    check(f"prepare rejects {label} version evidence drift", lambda label=label: evidence_reject(label))


def account_reject(kind: str) -> bool:
    root, request, old = fixture("account-" + kind)
    try:
        account = Path(request["account_home"])
        if kind == "mode":
            account.chmod(0o755)
        elif kind == "symlink":
            target = root / "target"
            target.mkdir(mode=0o700)
            account.rmdir()
            account.symlink_to(target, target_is_directory=True)
        elif kind == "parent-mode":
            root.chmod(0o720)
        return rejects(lambda: MODULE.prepare(MODULE._canonical_json(request)))
    finally:
        root.chmod(0o700)
        cleanup(root, old)


for label in ("mode", "symlink", "parent-mode"):
    check(f"prepare rejects account HOME {label} drift without enumerating it", lambda label=label: account_reject(label))


def publication_reject(kind: str) -> bool:
    root, request, old = fixture("publication-" + kind)
    try:
        output = Path(request["output_path"])
        if kind == "final-collision":
            write_private(output, b"existing\n")
        elif kind == "temp-collision":
            fixed = b"a" * 16
            temporary = output.parent / (".models.capture.profile." + fixed.hex() + ".tmp")
            write_private(temporary, b"collision\n")
            original = MODULE.os.urandom
            MODULE.os.urandom = lambda _count: fixed
            try:
                return rejects(lambda: MODULE.prepare(MODULE._canonical_json(request))) and not output.exists()
            finally:
                MODULE.os.urandom = original
        elif kind == "link-failure":
            original = MODULE.os.link
            MODULE.os.link = lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("link"))
            try:
                return rejects(lambda: MODULE.prepare(MODULE._canonical_json(request))) and not output.exists()
            finally:
                MODULE.os.link = original
        return rejects(lambda: MODULE.prepare(MODULE._canonical_json(request)))
    finally:
        cleanup(root, old)


for label in ("final-collision", "temp-collision", "link-failure"):
    check(f"no-overwrite publication rejects {label}", lambda label=label: publication_reject(label))


def no_publication_residual(root: Path, output: Path) -> bool:
    return not output.exists() and not list(root.glob("output-parent/.models.capture.profile.*.tmp"))


def publication_fault_cleans_exact_owned_inode(kind: str) -> bool:
    root, request, old = fixture("publication-fault-" + kind)
    output = Path(request["output_path"])
    original_write = MODULE.os.write
    original_fsync = MODULE.os.fsync
    original_link = MODULE.os.link
    original_stat = MODULE.os.stat
    try:
        if kind == "write":
            MODULE.os.write = lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("write"))
        elif kind == "file-fsync":
            MODULE.os.fsync = lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("fsync"))
        elif kind == "link":
            MODULE.os.link = lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("link"))
        elif kind == "postlink-stat":
            linked = {"done": False}

            def link_then_mark(*args: object, **kwargs: object) -> object:
                result = original_link(*args, **kwargs)
                linked["done"] = True
                return result

            def stat_after_link(path: object, *args: object, **kwargs: object) -> os.stat_result:
                if linked["done"] and path == MODULE.OUTPUT_BASENAME:
                    linked["done"] = False
                    raise OSError("postlink")
                return original_stat(path, *args, **kwargs)

            MODULE.os.link = link_then_mark
            MODULE.os.stat = stat_after_link
        elif kind == "parent-fsync":
            calls = {"dirs": 0}

            def parent_fsync(descriptor: int) -> None:
                if stat.S_ISDIR(original_stat(descriptor).st_mode):
                    calls["dirs"] += 1
                    if calls["dirs"] == 1:
                        raise OSError("parent-fsync")
                original_fsync(descriptor)

            MODULE.os.fsync = parent_fsync
        return rejects(lambda: MODULE.prepare(MODULE._canonical_json(request))) and no_publication_residual(root, output)
    finally:
        MODULE.os.write = original_write
        MODULE.os.fsync = original_fsync
        MODULE.os.link = original_link
        MODULE.os.stat = original_stat
        cleanup(root, old)


for label in ("write", "file-fsync", "link", "postlink-stat", "parent-fsync"):
    check(f"publication {label} failure removes only owned temporary/final inode", lambda label=label: publication_fault_cleans_exact_owned_inode(label))


def foreign_final_race_survives_failure() -> bool:
    root, request, old = fixture("foreign-final-race")
    output = Path(request["output_path"])
    original_link = MODULE.os.link
    foreign = b"foreign-final\n"

    def racing_link(*args: object, **kwargs: object) -> object:
        write_private(output, foreign)
        return original_link(*args, **kwargs)

    MODULE.os.link = racing_link
    try:
        return (
            rejects(lambda: MODULE.prepare(MODULE._canonical_json(request)))
            and output.read_bytes() == foreign
            and not list(root.glob("output-parent/.models.capture.profile.*.tmp"))
        )
    finally:
        MODULE.os.link = original_link
        cleanup(root, old)


check("foreign final race survives failure and owned temporary is removed", foreign_final_race_survives_failure)


def postlink_foreign_replacement_survives_failure() -> bool:
    root, request, old = fixture("postlink-foreign-replacement")
    output = Path(request["output_path"])
    foreign = b"postlink-foreign\n"
    original_link = MODULE.os.link

    def replace_linked_final(*args: object, **kwargs: object) -> object:
        result = original_link(*args, **kwargs)
        os.unlink(output)
        write_private(output, foreign)
        return result

    MODULE.os.link = replace_linked_final
    try:
        return (
            rejects(lambda: MODULE.prepare(MODULE._canonical_json(request)))
            and output.read_bytes() == foreign
            and not list(root.glob("output-parent/.models.capture.profile.*.tmp"))
        )
    finally:
        MODULE.os.link = original_link
        cleanup(root, old)


check(
    "post-link foreign replacement survives and publisher-owned temporary is removed",
    postlink_foreign_replacement_survives_failure,
)


def foreign_final_survives_signal_cleanup() -> bool:
    root, request, old = fixture("foreign-final-signal")
    output = Path(request["output_path"])
    foreign = b"foreign-signal\n"
    original_link = MODULE.os.link
    old_stdin = MODULE.sys.stdin
    old_stdout = MODULE.sys.stdout

    def racing_link(*args: object, **kwargs: object) -> object:
        write_private(output, foreign)
        os.kill(os.getpid(), signal.SIGHUP)
        return original_link(*args, **kwargs)

    MODULE.os.link = racing_link
    MODULE.sys.stdin = type("Input", (), {"buffer": io.BytesIO(MODULE._canonical_json(request))})()
    MODULE.sys.stdout = io.StringIO()
    try:
        return (
            MODULE.main(["--prepare"]) == 128 + signal.SIGHUP
            and output.read_bytes() == foreign
            and not list(root.glob("output-parent/.models.capture.profile.*.tmp"))
        )
    finally:
        MODULE.os.link = original_link
        MODULE.sys.stdin = old_stdin
        MODULE.sys.stdout = old_stdout
        cleanup(root, old)


check("foreign final survives signal cleanup before owned link", foreign_final_survives_signal_cleanup)


def noncanonical_validate(kind: str) -> bool:
    root, request, old = fixture("noncanonical-" + kind)
    try:
        MODULE.prepare(MODULE._canonical_json(request))
        path = Path(request["output_path"])
        value = json.loads(path.read_text("ascii"))
        if kind == "spacing":
            raw = json.dumps(value, sort_keys=True, indent=1).encode("ascii") + b"\n"
        elif kind == "ordering":
            raw = json.dumps({key: value[key] for key in reversed(tuple(value))}, separators=(",", ":")).encode("ascii") + b"\n"
        elif kind == "newline":
            raw = path.read_bytes().rstrip(b"\n")
        else:
            raw = path.read_bytes() + b"\n"
        path.write_bytes(raw)
        path.chmod(0o600)
        return rejects(lambda: MODULE.validate(MODULE._canonical_json({"profile_path": str(path)})))
    finally:
        cleanup(root, old)


for label in ("spacing", "ordering", "newline", "double"):
    check(f"validate rejects {label} profile encoding", lambda label=label: noncanonical_validate(label))


def redacted_console() -> bool:
    data = b'{"account_home":"/secret/account","output_path":"/secret/out/models.capture.profile.json"}\n'
    old_stdin = MODULE.sys.stdin
    old_stderr = MODULE.sys.stderr
    stderr = io.StringIO()
    MODULE.sys.stdin = type("Input", (), {"buffer": io.BytesIO(data)})()
    MODULE.sys.stderr = stderr
    try:
        return MODULE.main(["--prepare"]) == 2 and "/secret" not in stderr.getvalue() and "traceback" not in stderr.getvalue().lower()
    finally:
        MODULE.sys.stdin = old_stdin
        MODULE.sys.stderr = old_stderr


check("invalid request console is redacted", redacted_console)


def signal_console() -> bool:
    old_prepare = MODULE.prepare
    old_stdin = MODULE.sys.stdin
    MODULE.prepare = lambda _data: (_ for _ in ()).throw(MODULE.ModelsCaptureProfileInterrupted(signal.SIGTERM))
    MODULE.sys.stdin = type("Input", (), {"buffer": io.BytesIO(b"{}\n")})()
    try:
        return MODULE.main(["--prepare"]) == 128 + signal.SIGTERM
    finally:
        MODULE.prepare = old_prepare
        MODULE.sys.stdin = old_stdin


check("first lifecycle signal produces exact interrupted exit", signal_console)


def publication_signal_rolls_back_and_preserves_first() -> bool:
    root, request, old = fixture("publication-signal")
    real_fsync = MODULE.os.fsync
    old_stdin = MODULE.sys.stdin
    old_stdout = MODULE.sys.stdout
    directories = 0

    def signaling_fsync(descriptor: int) -> None:
        nonlocal directories
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            directories += 1
            if directories == 1:
                os.kill(os.getpid(), signal.SIGHUP)
            elif directories == 2:
                os.kill(os.getpid(), signal.SIGTERM)
        real_fsync(descriptor)

    MODULE.os.fsync = signaling_fsync
    MODULE.sys.stdin = type("Input", (), {"buffer": io.BytesIO(MODULE._canonical_json(request))})()
    MODULE.sys.stdout = io.StringIO()
    try:
        return (
            MODULE.main(["--prepare"]) == 128 + signal.SIGHUP
            and directories >= 2
            and not Path(request["output_path"]).exists()
        )
    finally:
        MODULE.os.fsync = real_fsync
        MODULE.sys.stdin = old_stdin
        MODULE.sys.stdout = old_stdout
        cleanup(root, old)


check("publication signal rolls back exact file and preserves first signal", publication_signal_rolls_back_and_preserves_first)
check("validate request rejects extra authority", lambda: rejects(lambda: MODULE.validate(MODULE._canonical_json({"profile_path": "/x", "extra": 1}))))
check("validate request rejects a noncanonical path", lambda: rejects(lambda: MODULE.validate(MODULE._canonical_json({"profile_path": "relative"}))))

shutil.rmtree(TMP)
print(f"models capture profile offline tests: {passed} passed, {failed} failed")
raise SystemExit(0 if failed == 0 else 1)
