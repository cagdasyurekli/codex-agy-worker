#!/usr/bin/env python3
"""Offline tests for the explicit-account, capture-only models runner."""

from __future__ import annotations

import dataclasses
import ast
import contextlib
import hashlib
import importlib.util
import inspect
import io
import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import types
from pathlib import Path
from typing import Callable, Optional


sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "models_capture_runner.py"
ISOLATED_PATH = ROOT / "scripts" / "models_attestation_runner.py"
SPEC = importlib.util.spec_from_file_location("models_capture_runner_tested", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

TMP = Path(tempfile.mkdtemp(prefix="agyworker-models-capture-tests.")).resolve()
TMP.chmod(0o700)
SOURCE = MODULE_PATH.read_bytes()
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
        print(f"FAIL models capture runner: {name}{detail}")


def rejects(action: Callable[[], object]) -> bool:
    try:
        action()
    except (
        MODULE.ModelsCaptureError,
        MODULE.models.ModelsAttestationError,
        MODULE.models.inventory.InventoryEvidenceError,
        MODULE.version.AttestationError,
        OSError,
        subprocess.SubprocessError,
    ):
        return True
    return False


def mutate(old: bytes, new: bytes) -> bytes:
    position = SOURCE.rfind(old)
    if position < 0:
        raise AssertionError(f"mutation target missing: {old!r}")
    return SOURCE[:position] + new + SOURCE[position + len(old):]


def repin_module_ast(data: bytes) -> bytes:
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
    digest = hashlib.sha256(ast.dump(tree, include_attributes=False).encode("utf-8")).hexdigest()
    return data.replace(MODULE.MODULE_AST_SHA256.encode("ascii"), digest.encode("ascii"), 1)


def fixture(name: str, **kwargs: object) -> tuple[Path, object, object]:
    root = TMP / name
    root.mkdir(mode=0o700)
    work = root / "work"
    work.mkdir(mode=0o700)
    account = root / "account"
    account.mkdir(mode=0o700)
    account.chmod(0o700)
    base = MODULE.models._synthetic_profile(work, **kwargs)
    profile = profile_from_base(account, base)
    return root, profile, base


def profile_from_base(account: Path, base: object) -> object:
    return MODULE.CaptureProfile(
        account_home=str(account),
        account_home_identity=MODULE.DirectoryIdentity.from_stat(account.stat()),
        snapshot_identity=base.snapshot_identity,
        snapshot_path=base.snapshot_path,
        source_identity=base.source_identity,
        source_path=base.source_path,
        source_sha256=base.source_sha256,
        temp_parent=base.temp_parent,
        version_binding_sha256=base.version_binding_sha256,
        version_root=base.version_root,
    )


def cleanup(root: Path, result: Optional[dict[str, object]] = None) -> None:
    if result is not None:
        capture_root = Path(str(result.get("artifact_root", "")))
        if capture_root.is_dir() and capture_root.parent.parent == root:
            shutil.rmtree(capture_root)
    if root.exists():
        shutil.rmtree(root)


class CleaningProcess:
    """Remove only Apple's test-interpreter TMP xcrun cache after leader reap."""

    def __init__(self, process: subprocess.Popen[bytes], tmpdir: Path):
        self.process = process
        self.tmpdir = tmpdir
        self.stdout = process.stdout
        self.stderr = process.stderr
        self.pid = process.pid

    @property
    def returncode(self) -> Optional[int]:
        return self.process.returncode

    def poll(self) -> Optional[int]:
        return self.process.poll()

    def wait(self, *args: object, **kwargs: object) -> int:
        result = self.process.wait(*args, **kwargs)
        cache = self.tmpdir / "xcrun_db"
        if cache.exists() and cache.is_file() and not cache.is_symlink():
            cache.unlink()
        return result


def cleaning_popen(*args: object, **kwargs: object) -> CleaningProcess:
    process = subprocess.Popen(*args, **kwargs)
    return CleaningProcess(process, Path(kwargs["env"]["TMPDIR"]))


def run_synthetic(profile: object, **kwargs: object) -> dict[str, object]:
    kwargs.setdefault("calls", MODULE.version.RunnerCalls(popen=cleaning_popen))
    return MODULE.run_capture(
        profile,
        module_source=SOURCE,
        profile_source=MODULE._canonical_json(profile.as_mapping()),
        profile_validator=MODULE._validate_account_policy,
        **kwargs,
    )


def capture_cli_command(
    profile: object,
    *,
    delivered: tuple[int, ...] = (),
    late_path: Path | None = None,
) -> tuple[list[str], bytes]:
    delivered_values = tuple(int(item) for item in delivered)
    child = (
        "import importlib.util,os,pathlib,signal,subprocess,sys,types;"
        f"p={str(MODULE_PATH)!r};"
        "s=importlib.util.spec_from_file_location('capture_cli_test',p);"
        "m=importlib.util.module_from_spec(s);sys.modules[s.name]=m;s.loader.exec_module(m);"
        "m.version._production_startup_evaluation=lambda:types.SimpleNamespace(accepted=True);"
        "m._validate_production_profile=lambda _profile:None;real=m.run_capture;"
        f"delivered={delivered_values!r};"
        "exec(\"def cleaning(*args,**kwargs):\\n"
        " process=subprocess.Popen(*args,**kwargs)\\n"
        " for item in delivered: os.kill(os.getpid(),item)\\n"
        " wait=process.wait\\n"
        " def cleaned_wait(*a,**k):\\n"
        "  result=wait(*a,**k)\\n"
        "  cache=pathlib.Path(kwargs['env']['TMPDIR'])/'xcrun_db'\\n"
        "  if cache.is_file() and not cache.is_symlink(): cache.unlink()\\n"
        "  return result\\n"
        " process.wait=cleaned_wait\\n"
        " return process\\n"
        "def wrapped(*args,**kwargs):\\n"
        " kwargs['calls']=m.version.RunnerCalls(popen=cleaning)\\n"
        " kwargs['profile_validator']=lambda _profile:None\\n"
        " return real(*args,**kwargs)\");"
        "m.run_capture=wrapped;m.main(['--capture-models']);"
    )
    if late_path is not None:
        child += f"pathlib.Path({str(late_path)!r}).write_bytes(b'returned\\n')"
    return ["/usr/bin/python3", "-I", "-S", "-B", "-c", child], MODULE._canonical_json(profile.as_mapping())


contract = MODULE.validate_source_contract(SOURCE)
check("module imports with bytecode disabled", lambda: sys.dont_write_bytecode)
check("capture wall is exactly 25 seconds", lambda: MODULE.WALL_SECONDS == 25.0)
check("streams are independently capped at 64 KiB", lambda: MODULE.STREAM_LIMIT == 64 * 1024)
check("reviewed source digest is pinned", lambda: MODULE.EXPECTED_SOURCE_SHA256 == "198ff7c3f6d173daa510b0814aa70c6ce14c94035bcd4707a3c0e79fa38a7bc3")
check("accepted version binding is pinned", lambda: MODULE.EXPECTED_VERSION_BINDING_SHA256 == "72d0bba6040b46109f6968528697579dd1dbe7fdae949e68fb22e6f058452ea2")
check("reviewed snapshot identity is pinned", lambda: (MODULE.EXPECTED_SNAPSHOT_INODE, MODULE.EXPECTED_SNAPSHOT_SIZE) == (26545304, 169718336))
check("isolated accepting runner remains byte identical", lambda: len(ISOLATED_PATH.read_bytes()) == MODULE.MODELS_RUNNER_BYTES and hashlib.sha256(ISOLATED_PATH.read_bytes()).hexdigest() == MODULE.MODELS_RUNNER_SHA256)
check("canonical capture source contract is accepted", lambda: contract["sha256"] == hashlib.sha256(SOURCE).hexdigest())
check("completion marker is capture-only and last", lambda: MODULE.OUTPUT_FILES[-1] == "models.capture.sha256")
check("capture names never claim accepted binding", lambda: all("accepted" not in item and "binding" not in item for item in MODULE.OUTPUT_FILES))
check("runner has exactly one child launch site", lambda: SOURCE.count(b"calls.popen(") == 1)
check("production CLI is capture-models only", lambda: b'["--capture-models"]' in SOURCE and b'["--attest-models"]' not in SOURCE)
check(
    "public capture source contains no personal absolute path",
    lambda: b"/Users/" + b"cagdasyurekli/" not in SOURCE,
)
check("capture source contains no inventory or stderr interpretation", lambda: b"parse_inventory_bytes" not in SOURCE and b"_validate_stderr" not in SOURCE and b"inventory_normalized_sha256" not in SOURCE)
check("full normalized module AST is pinned", lambda: len(MODULE.MODULE_AST_SHA256) == 64)


mutation_cases = (
    (b'argv = [base.source_path, "models"]', b'argv = [base.source_path, "models", "--login"]'),
    (b"                executable=base.snapshot_path,", b"                executable=base.source_path,"),
    (b"                stdin=subprocess.DEVNULL,", b"                stdin=None,"),
    (b"                stdout=subprocess.PIPE,", b"                stdout=None,"),
    (b"                stderr=subprocess.PIPE,", b"                stderr=None,"),
    (b'                cwd=str(root / "cwd"),', b"                cwd=profile.account_home,"),
    (b"                env=environment,", b"                env=os.environ,"),
    (b"                start_new_session=True,", b"                start_new_session=False,"),
    (b'            "TMPDIR": str(root / "tmp"),\n', b""),
    (b'            "XDG_CONFIG_HOME": str(root / "xdg-config"),\n', b""),
    (b'            "XDG_CACHE_HOME": str(root / "xdg-cache"),\n', b""),
    (b'            "XDG_STATE_HOME": str(root / "xdg-state"),\n', b""),
    (b'            "HOME": profile.account_home,', b'            "HOME": os.environ["HOME"],'),
    (b"            process = calls.popen(\n", b"            calls.popen(argv)\n            process = calls.popen(\n"),
    (b"            process = calls.popen(\n", b"            subprocess.run(argv, check=False)\n            process = calls.popen(\n"),
    (b'publisher.publish("models.capture.sha256"', b'publisher.publish("models.binding.sha256"'),
    (b'"status": "captured",', b'"status": "accepted",'),
    (b"        _verify_account_descriptor(profile, account_fd)\n", b""),
    (b"        account_post = _verify_account_descriptor(profile, account_fd)\n", b"        account_post = profile.account_home_identity\n"),
    (b"        _revalidate_private_directories(root, private_identities)\n        account_post", b"        account_post"),
    (b"            version._require_canonical_absolute(value[key])", b"            pass"),
    (b"        if _canonical_json(profile.as_mapping()) != data:\n            raise ModelsCaptureError(\"models capture profile is not canonical\")\n", b""),
    (b"        or base.source_sha256 != EXPECTED_SOURCE_SHA256\n", b"        or base.source_sha256 != EXPECTED_SOURCE_SHA256\n        or base.snapshot_path != base.version_root + \"/agy.snapshot\"\n"),
)
for index, (old, new) in enumerate(mutation_cases, 1):
    check(
        f"source authority mutation {index} is killed",
        lambda old=old, new=new: rejects(lambda: MODULE.validate_source_contract(mutate(old, new))),
    )

dynamic = SOURCE.replace(
    b"class ModelsCaptureError(ValueError):\n",
    b'def hidden_run(argv):\n    return subprocess.__dict__["run"](argv)\n\n\nclass ModelsCaptureError(ValueError):\n',
    1,
).replace(
    b"        stdout, stderr = models._capture(process, deadline)\n",
    b"        hidden_run(argv)\n        stdout, stderr = models._capture(process, deadline)\n",
    1,
)
check(
    "repinned dynamic subprocess lookup is independently rejected",
    lambda: rejects(lambda: MODULE.validate_source_contract(repin_module_ast(dynamic))),
)


def profile_round_trip() -> bool:
    root, profile, _base = fixture("profile-round-trip")
    try:
        encoded = MODULE._canonical_json(profile.as_mapping())
        return MODULE.CaptureProfile.from_bytes(encoded) == profile
    finally:
        cleanup(root)


check("strict canonical profile round trips", profile_round_trip)


def noncanonical_profile_rejects_before_capture() -> bool:
    root, profile, _base = fixture("noncanonical-profile")
    value = profile.as_mapping()
    reordered = json.dumps(
        {key: value[key] for key in reversed(tuple(value))},
        sort_keys=False,
        separators=(",", ":"),
    ).encode("ascii") + b"\n"
    spaced = json.dumps(value, sort_keys=True, indent=1).encode("ascii") + b"\n"
    doubled = MODULE._canonical_json(value) + b"\n"
    try:
        return all(
            rejects(lambda raw=raw: MODULE.CaptureProfile.from_bytes(raw))
            for raw in (reordered, spaced, doubled, MODULE._canonical_json(value).rstrip(b"\n"))
        )
    finally:
        cleanup(root)


check("noncanonical profile encodings reject before capture authority", noncanonical_profile_rejects_before_capture)


def profile_reject(change: str) -> bool:
    root, profile, _base = fixture(f"profile-{change}")
    try:
        value = profile.as_mapping()
        if change == "extra":
            value["model"] = "forbidden"
        elif change == "missing":
            del value["account_home_identity"]
        elif change == "relative":
            value["account_home"] = "relative/home"
        elif change == "identity":
            value["account_home_identity"]["ino"] += 1
        return rejects(lambda: MODULE.CaptureProfile.from_bytes(MODULE._canonical_json(value)))
    finally:
        cleanup(root)


for label in ("extra", "missing", "relative"):
    check(f"profile rejects {label} authority", lambda label=label: profile_reject(label))


def positive_capture() -> bool:
    root, profile, _base = fixture("positive")
    seen: list[tuple[tuple[object, ...], dict[str, object]]] = []
    result = None

    def tracking(*args: object, **kwargs: object) -> subprocess.Popen[bytes]:
        seen.append((args, kwargs))
        return cleaning_popen(*args, **kwargs)

    try:
        result = run_synthetic(
            profile, calls=MODULE.version.RunnerCalls(popen=tracking)
        )
        artifact = Path(str(result["artifact_root"]))
        record_bytes = (artifact / "models.capture.json").read_bytes()
        record = json.loads(record_bytes)
        profile_bytes = MODULE._canonical_json(profile.as_mapping())
        expected_env = {
            "HOME": profile.account_home,
            "TMPDIR": str(artifact / "tmp"),
            "XDG_CONFIG_HOME": str(artifact / "xdg-config"),
            "XDG_CACHE_HOME": str(artifact / "xdg-cache"),
            "XDG_STATE_HOME": str(artifact / "xdg-state"),
            "LANG": "C",
            "LC_ALL": "C",
            "NO_COLOR": "1",
            "TERM": "dumb",
            "PATH": "/usr/bin:/bin",
        }
        args, kwargs = seen[0]
        names = {item.name for item in artifact.iterdir()}
        files_private = all(
            stat.S_IMODE(item.stat().st_mode) == 0o600
            for item in artifact.iterdir() if item.is_file()
        )
        dirs_private = all(
            stat.S_IMODE(item.stat().st_mode) == 0o700
            for item in artifact.iterdir() if item.is_dir()
        )
        return (
            len(seen) == 1
            and args == ([profile.models_profile.source_path, "models"],)
            and kwargs == {
                "executable": profile.models_profile.snapshot_path,
                "stdin": subprocess.DEVNULL,
                "stdout": subprocess.PIPE,
                "stderr": subprocess.PIPE,
                "cwd": str(artifact / "cwd"),
                "env": expected_env,
                "start_new_session": True,
            }
            and names == set(MODULE.OUTPUT_FILES) | set(MODULE.PRIVATE_DIRECTORY_NAMES)
            and files_private and dirs_private
            and (artifact / "models.capture.profile.json").read_bytes() == profile_bytes
            and (artifact / "models.capture.sha256").read_text("ascii").strip() == result["capture_sha256"]
            and hashlib.sha256(record_bytes).hexdigest() == result["capture_sha256"]
            and record["status"] == "captured"
            and record["limitations"]["accepted_inventory"] is False
            and record["limitations"]["inventory_interpreted"] is False
            and record["limitations"]["metadata_advance_authorized"] is False
            and "inventory" not in record
            and not any("binding" in name or "accepted" in name for name in names)
        )
    finally:
        cleanup(root, result)


check("one synthetic account call captures exact private evidence", positive_capture)


def version_root_snapshot_extra_rejects() -> bool:
    root, profile, _base = fixture("version-root-snapshot-extra")
    extra = Path(profile.version_root) / "agy.snapshot"
    try:
        extra.write_bytes(b"unexpected\n")
        return rejects(lambda: MODULE.models._validate_version_evidence(profile.models_profile))
    finally:
        cleanup(root)


check("version evidence rejects a co-located snapshot extra while external snapshot remains valid", version_root_snapshot_extra_rejects)


def credential_dependent_capture() -> bool:
    root = TMP / "credential-dependent"
    root.mkdir(mode=0o700)
    work = root / "work"
    work.mkdir(mode=0o700)
    base = MODULE.models._synthetic_profile(work, models_require_session=True)
    account = work / "caller-home"
    profile = profile_from_base(account, base)
    seen: list[dict[str, object]] = []
    result = None

    def tracking(*args: object, **kwargs: object) -> subprocess.Popen[bytes]:
        seen.append(kwargs)
        return cleaning_popen(*args, **kwargs)

    try:
        result = MODULE.run_capture(
            profile,
            calls=MODULE.version.RunnerCalls(popen=tracking),
            module_source=SOURCE,
            profile_source=MODULE._canonical_json(profile.as_mapping()),
            profile_validator=lambda _profile: None,
        )
        return len(seen) == 1 and seen[0]["env"]["HOME"] == str(account) and result["status"] == "captured"
    finally:
        cleanup(root, result)


check("synthetic credential-dependent fake uses only explicit account HOME", credential_dependent_capture)


def rejected_execution(label: str, **kwargs: object) -> bool:
    root, profile, _base = fixture(label, **kwargs)
    try:
        rejected = rejects(lambda: run_synthetic(profile))
        return rejected and not any(root.glob("work/agy-models-account-capture.*/models.capture.sha256"))
    finally:
        cleanup(root)


check("nonzero process rejects without capture marker", lambda: rejected_execution("nonzero", models_exit=7))
check("stdout overflow rejects without capture marker", lambda: rejected_execution("stdout-overflow", models_stdout=b"x" * (MODULE.STREAM_LIMIT + 1)))
check("stderr overflow rejects without capture marker", lambda: rejected_execution("stderr-overflow", models_stderr=b"x" * (MODULE.STREAM_LIMIT + 1)))


def arbitrary_bounded_bytes_are_captured() -> bool:
    raw_stdout = b"new-1.1.11-model-shape\x00\n"
    raw_stderr = b"authentication permission quota rate-limit interactive drift\n"
    root, profile, _base = fixture(
        "arbitrary-bounded", models_stdout=raw_stdout, models_stderr=raw_stderr
    )
    result = None
    try:
        result = run_synthetic(profile)
        artifact = Path(str(result["artifact_root"]))
        record = json.loads((artifact / "models.capture.json").read_text("ascii"))
        return (
            (artifact / "models.stdout").read_bytes() == raw_stdout
            and (artifact / "models.stderr").read_bytes() == raw_stderr
            and record["artifacts"]["models.stdout"] == hashlib.sha256(raw_stdout).hexdigest()
            and record["artifacts"]["models.stderr"] == hashlib.sha256(raw_stderr).hexdigest()
            and record["limitations"]["inventory_interpreted"] is False
            and result["status"] == "captured"
        )
    finally:
        cleanup(root, result)


check("arbitrary bounded exit-zero stdout and stderr are captured uninterpreted", arbitrary_bounded_bytes_are_captured)


def timeout_rejects() -> bool:
    root, profile, _base = fixture("timeout", models_delay=0.25)
    old = MODULE.WALL_SECONDS
    MODULE.WALL_SECONDS = 0.05
    try:
        return rejects(lambda: run_synthetic(profile)) and not any(
            root.glob("work/agy-models-account-capture.*/models.capture.sha256")
        )
    finally:
        MODULE.WALL_SECONDS = old
        cleanup(root)


check("timeout closes the process group without a marker", timeout_rejects)


def account_drift(kind: str) -> bool:
    root, profile, _base = fixture(f"account-{kind}")
    path = Path(profile.account_home)
    try:
        if kind == "mode":
            path.chmod(0o755)
        elif kind == "identity":
            replacement = root / "replacement"
            replacement.mkdir(mode=0o700)
            path.rename(root / "old-account")
            replacement.rename(path)
        elif kind == "symlink":
            target = root / "target"
            target.mkdir(mode=0o700)
            path.rmdir()
            path.symlink_to(target, target_is_directory=True)
        return rejects(lambda: run_synthetic(profile))
    finally:
        cleanup(root)


for label in ("mode", "identity", "symlink"):
    check(f"account HOME {label} drift is rejected", lambda label=label: account_drift(label))


def account_overlap() -> bool:
    root, profile, base = fixture("overlap")
    candidate = dataclasses.replace(
        profile,
        account_home=base.temp_parent,
        account_home_identity=MODULE.DirectoryIdentity.from_stat(os.stat(base.temp_parent)),
    )
    try:
        return rejects(lambda: MODULE._validate_account_policy(candidate))
    finally:
        cleanup(root)


check("account HOME cannot overlap capture evidence authority", account_overlap)


def account_executable_overlap(kind: str, relation: str) -> bool:
    root, profile, _base = fixture("account-executable-" + kind + "-" + relation)
    try:
        if relation == "under":
            path = Path(profile.account_home) / ("agy.source" if kind == "source" else "agy.snapshot")
            path.write_bytes(b"synthetic\n")
            path.chmod(0o755 if kind == "source" else 0o500)
        elif relation == "equal":
            path = Path(profile.account_home)
        elif relation == "ancestor":
            path = Path(profile.account_home).parent
        else:
            raise AssertionError(relation)
        candidate = dataclasses.replace(
            profile,
            source_path=str(path) if kind == "source" else profile.source_path,
            snapshot_path=str(path) if kind == "snapshot" else profile.snapshot_path,
        )
        return rejects(lambda: MODULE._validate_account_policy(candidate))
    finally:
        cleanup(root)


for executable_kind in ("source", "snapshot"):
    for containment in ("under", "equal", "ancestor"):
        check(
            "production consumer rejects " + executable_kind + " " + containment + " account HOME",
            lambda executable_kind=executable_kind, containment=containment: account_executable_overlap(
                executable_kind, containment
            ),
        )


guard = (
    b"    if any(\n"
    b"        os.path.commonpath((path, profile.account_home)) in {path, profile.account_home}\n"
    b"        for path in (base.source_path, base.snapshot_path)\n"
    b"    ):\n"
    b"        raise ModelsCaptureError(\"attested executable overlaps account HOME\")\n"
)
check(
    "source authority rejects removal of the independent executable-account guard",
    lambda: rejects(lambda: MODULE.validate_source_contract(repin_module_ast(mutate(guard, b"")))),
)
check(
    "source authority rejects weakening account-contains-executable direction",
    lambda: rejects(
        lambda: MODULE.validate_source_contract(
            repin_module_ast(
                mutate(
                    b"os.path.commonpath((path, profile.account_home)) in {path, profile.account_home}",
                    b"os.path.commonpath((path, profile.account_home)) == path",
                )
            )
        )
    ),
)
check(
    "source authority rejects weakening executable-contains-account direction",
    lambda: rejects(
        lambda: MODULE.validate_source_contract(
            repin_module_ast(
                mutate(
                    b"os.path.commonpath((path, profile.account_home)) in {path, profile.account_home}",
                    b"os.path.commonpath((path, profile.account_home)) == profile.account_home",
                )
            )
        )
    ),
)


def writable_component_rejects() -> bool:
    root, profile, _base = fixture("writable-component")
    root.chmod(0o720)
    try:
        return rejects(lambda: run_synthetic(profile))
    finally:
        root.chmod(0o700)
        cleanup(root)


check("group-writable account path component is rejected", writable_component_rejects)


def post_launch_account_drift() -> bool:
    root, profile, _base = fixture("post-launch-account-drift")

    def drifting(*args: object, **kwargs: object) -> subprocess.Popen[bytes]:
        process = subprocess.Popen(*args, **kwargs)
        Path(profile.account_home).chmod(0o755)
        return process

    try:
        return rejects(
            lambda: run_synthetic(
                profile, calls=MODULE.version.RunnerCalls(popen=drifting)
            )
        ) and not any(root.glob("work/agy-models-account-capture.*/models.capture.sha256"))
    finally:
        Path(profile.account_home).chmod(0o700)
        cleanup(root)


check("post-launch account HOME mode drift rejects without marker", post_launch_account_drift)


def post_child_scratch_write_rejects() -> bool:
    root, profile, _base = fixture("post-child-scratch")

    def writing(*args: object, **kwargs: object) -> subprocess.Popen[bytes]:
        process = subprocess.Popen(*args, **kwargs)
        scratch = Path(kwargs["env"]["XDG_CACHE_HOME"])
        (scratch / "uncontrolled-secret").write_bytes(b"private\n")
        return process

    try:
        return rejects(
            lambda: run_synthetic(
                profile, calls=MODULE.version.RunnerCalls(popen=writing)
            )
        ) and not any(root.glob("work/agy-models-account-capture.*/models.capture.sha256"))
    finally:
        cleanup(root)


check("post-child scratch content rejects and publishes no marker", post_child_scratch_write_rejects)


def signal_during_launch(signum: int) -> bool:
    root, profile, _base = fixture(f"signal-{signum}")

    def signaling(*args: object, **kwargs: object) -> subprocess.Popen[bytes]:
        process = subprocess.Popen(*args, **kwargs)
        os.kill(os.getpid(), signum)
        return process

    try:
        try:
            run_synthetic(profile, calls=MODULE.version.RunnerCalls(popen=signaling))
        except SystemExit as exc:
            return exc.code == 128 + signum and not any(
                root.glob("work/agy-models-account-capture.*/models.capture.sha256")
            )
        return False
    finally:
        cleanup(root)


for lifecycle_signal in MODULE.version.LIFECYCLE_SIGNALS:
    check(
        f"signal {lifecycle_signal} preserves first exit and no marker",
        lambda lifecycle_signal=lifecycle_signal: signal_during_launch(lifecycle_signal),
    )


def signal_during_completion(signum: int, second: int | None = None) -> bool:
    root, profile, _base = fixture(f"completion-signal-{signum}-{second}")
    real_fsync = os.fsync
    directory_calls = 0

    def signaling_fsync(descriptor: int) -> None:
        nonlocal directory_calls
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            directory_calls += 1
            if directory_calls == 15:
                os.kill(os.getpid(), signum)
            elif second is not None and directory_calls == 16:
                os.kill(os.getpid(), second)
        real_fsync(descriptor)

    try:
        try:
            run_synthetic(
                profile,
                calls=MODULE.version.RunnerCalls(
                    popen=cleaning_popen, fsync=signaling_fsync
                ),
            )
        except SystemExit as exc:
            return (
                exc.code == 128 + signum
                and directory_calls >= 15
                and not any(
                    root.glob("work/agy-models-account-capture.*/models.capture.sha256")
                )
            )
        return False
    finally:
        cleanup(root)


for lifecycle_signal in MODULE.version.LIFECYCLE_SIGNALS:
    check(
        f"completion signal {lifecycle_signal} rolls back the final capture marker",
        lambda lifecycle_signal=lifecycle_signal: signal_during_completion(lifecycle_signal),
    )

check(
    "distinct second completion signal cannot replace first exit or preserve marker",
    lambda: signal_during_completion(signal.SIGHUP, signal.SIGTERM),
)


def group_calls_precede_sole_reap() -> bool:
    source = inspect.getsource(MODULE.version._close_reserved_group)
    tree = ast.parse(source)
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    waits = [
        node
        for node in calls
        if isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "process"
        and node.func.attr == "wait"
    ]
    group_calls = [
        node
        for node in calls
        if isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "calls"
        and node.func.attr == "killpg"
    ]
    return (
        len(waits) == 1
        and bool(group_calls)
        and all((node.lineno, node.col_offset) < (waits[0].lineno, waits[0].col_offset) for node in group_calls)
    )


check("all reserved-group calls precede the sole leader reap", group_calls_precede_sole_reap)


def success_restores_signal_state() -> bool:
    root, profile, _base = fixture("success-signal-state")
    entry_mask = signal.pthread_sigmask(signal.SIG_BLOCK, ())
    entry_handlers = {
        item: signal.getsignal(item) for item in MODULE.version.LIFECYCLE_SIGNALS
    }

    def caller_handler(_signum: int, _frame: object) -> None:
        return None

    for item in MODULE.version.LIFECYCLE_SIGNALS:
        signal.signal(item, caller_handler)
    expected_mask = signal.pthread_sigmask(signal.SIG_BLOCK, ())
    result: Optional[dict[str, object]] = None
    try:
        result = run_synthetic(profile)
        observed_mask = signal.pthread_sigmask(signal.SIG_BLOCK, ())
        return (
            observed_mask == expected_mask
            and all(
                signal.getsignal(item) is caller_handler
                for item in MODULE.version.LIFECYCLE_SIGNALS
            )
        )
    finally:
        signal.pthread_sigmask(signal.SIG_BLOCK, MODULE.version.LIFECYCLE_SIGNALS)
        for item, handler in entry_handlers.items():
            signal.signal(item, handler)
        signal.pthread_sigmask(signal.SIG_SETMASK, entry_mask)
        cleanup(root, result)


check("successful capture restores caller lifecycle handlers and mask", success_restores_signal_state)

handler_restore_mutation = (
    b"        for item in reversed(lifecycle.installed_handlers):\n"
    b"            try:\n"
    b"                signal.signal(item, lifecycle.old_handlers[item])\n"
    b"            except BaseException as cleanup_error:\n"
    b"                if cleanup_failure is None:\n"
    b"                    cleanup_failure = cleanup_error\n"
)
check(
    "source authority kills embedded handler-restoration removal",
    lambda: rejects(
        lambda: MODULE.validate_source_contract(mutate(handler_restore_mutation, b""))
    ),
)


def no_raw_console() -> bool:
    command = ["/usr/bin/python3", "-I", "-S", "-B", str(MODULE_PATH), "--capture-models"]
    completed = subprocess.run(
        command,
        input=b'{"private":"/secret/account"}\n',
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    combined = completed.stdout + completed.stderr
    return completed.returncode == 2 and b"/secret" not in combined and b"private" not in combined and b"traceback" not in combined.lower()


check("rejected CLI emits one sanitized category without raw profile", no_raw_console)


def success_console_locates_capture_without_account_leak() -> bool:
    root, profile, _base = fixture("success-console")
    late = root / "late.return"
    command, encoded = capture_cli_command(profile, late_path=late)
    try:
        completed = subprocess.run(
            command,
            input=encoded,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if not completed.stdout:
            raise AssertionError(
                f"production child exited {completed.returncode}: "
                f"{completed.stderr.decode('utf-8', errors='replace')}"
            )
        value = json.loads(completed.stdout)
        artifact = Path(value["artifact_root"])
        return (
            completed.returncode == 0
            and value["status"] == "captured"
            and len(value["capture_sha256"]) == 64
            and profile.account_home.encode() not in completed.stdout
            and (artifact / "models.capture.sha256").is_file()
            and not late.exists()
        )
    finally:
        cleanup(root)


check("success console locates capture without exposing account HOME", success_console_locates_capture_without_account_leak)


def production_capture_reverse_pending_priority() -> bool:
    root, profile, _base = fixture("production-reverse-pending")
    command, encoded = capture_cli_command(
        profile, delivered=(signal.SIGTERM, signal.SIGHUP)
    )
    try:
        completed = subprocess.run(
            command,
            input=encoded,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        return (
            completed.returncode == 128 + signal.SIGHUP
            and completed.stdout == b""
            and completed.stderr == b"models account capture: interrupted\n"
            and not any(root.glob("work/agy-models-account-capture.*/models.capture.sha256"))
        )
    finally:
        cleanup(root)


check("capture CLI reverse pending delivery selects fixed HUP priority", production_capture_reverse_pending_priority)


def production_capture_broken_stdout_rolls_back() -> bool:
    root, profile, _base = fixture("production-broken-stdout")
    command, encoded = capture_cli_command(profile)
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert process.stdin is not None and process.stdout is not None and process.stderr is not None
    process.stdin.write(encoded)
    process.stdin.close()
    process.stdout.close()
    stderr = process.stderr.read()
    returncode = process.wait(timeout=15)
    try:
        return (
            returncode == 2
            and stderr == b"models account capture: rejected\n"
            and not any(root.glob("work/agy-models-account-capture.*/models.capture.sha256"))
        )
    finally:
        cleanup(root)


check("capture CLI broken stdout rolls back its provisional marker", production_capture_broken_stdout_rolls_back)
check("invalid CLI is usage failure", lambda: MODULE.main(["models"]) == 64)
check("auth-isolated runner is not modified by capture tests", lambda: hashlib.sha256(ISOLATED_PATH.read_bytes()).hexdigest() == MODULE.MODELS_RUNNER_SHA256)


shutil.rmtree(TMP)
print(f"models capture runner offline tests: {passed} passed, {failed} failed")
raise SystemExit(0 if failed == 0 else 1)
