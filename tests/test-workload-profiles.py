#!/usr/bin/env python3
"""Offline authority tests for fixed data-only workload profiles."""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
from typing import Any, Callable


ROOT = Path(__file__).resolve().parent.parent
RUNTIME = ROOT / "skills" / "agy-worker" / "runtime"
SCRIPT = RUNTIME / "scripts" / "workload_profiles.py"
WRAPPER = ROOT / "profile.sh"
NAMES = ["bounded-test-backfill", "diff-review", "repository-inventory"]
passed = 0
failed = 0


def check(label: str, predicate: Callable[[], bool] | bool) -> None:
    global passed, failed
    try:
        result = predicate() if callable(predicate) else predicate
    except Exception:
        result = False
    if result:
        passed += 1
        print(f"  ok   {label}")
    else:
        failed += 1
        print(f"  FAIL {label}")


def run(*args: str, runtime: Path = RUNTIME, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[bytes]:
    command = ["/usr/bin/python3", "-I", "-S", "-B", str(runtime / "scripts" / "workload_profiles.py"), *args]
    return subprocess.run(command, input=b"", stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, timeout=4)


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii") + b"\n"


def load_module(path: Path = SCRIPT):
    spec = importlib.util.spec_from_file_location("workload_profiles_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def bundle() -> tuple[tempfile.TemporaryDirectory[str], Path]:
    holder = tempfile.TemporaryDirectory(prefix="agy-profile-test.")
    target = Path(holder.name) / "runtime"
    shutil.copytree(RUNTIME, target)
    return holder, target


def rejects_bundle(change: Callable[[Path], None]) -> bool:
    holder, runtime = bundle()
    try:
        change(runtime)
        result = run("list", runtime=runtime)
        return result.returncode == 2 and result.stdout == b"" and result.stderr == b"profile: bundled profiles are invalid\n"
    finally:
        holder.cleanup()


def source_contract(source: str) -> bool:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    markers = (
        'value["suggested_mode"] != expected["mode"]',
        'value["suggested_persona"] != expected["persona"]',
        'value["path_policy_shape"] != expected["path_policy_shape"]',
        'value["caller_required"] != REQUIRED_INPUTS',
        'value["non_executable"] is not True',
        'value["authority"] != NO_AUTHORITY',
        'set(value) != PROFILE_FIELDS',
        'actual != allowed',
        'stat.S_IMODE(metadata.st_mode) != 0o644',
        'stat.S_IMODE(metadata.st_mode) & 0o022',
        'O_NOFOLLOW',
    )
    if not all(source.count(marker) >= 1 for marker in markers):
        return False
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    forbidden = {"Popen", "run", "call", "check_call", "check_output", "system"}
    return not any(isinstance(call.func, ast.Attribute) and call.func.attr in forbidden for call in calls)


print("data-only workload profiles offline test suite")
print()

listed = run("list")
check("list succeeds with one canonical JSON line", listed.returncode == 0 and listed.stderr == b"" and listed.stdout.endswith(b"\n") and listed.stdout.count(b"\n") == 1)
list_value = json.loads(listed.stdout)
check("list has exact stable top-level keys", list(list_value) == ["kind", "profiles", "schema_version"])
check("list is manifest ordered and complete", [item["name"] for item in list_value["profiles"]] == NAMES)
check("list output is canonical", listed.stdout == canonical(list_value))

shown: dict[str, dict[str, Any]] = {}
for name in NAMES:
    result = run("show", name)
    check(f"show {name} succeeds without stderr", result.returncode == 0 and result.stderr == b"")
    value = json.loads(result.stdout)
    shown[name] = value
    check(f"show {name} is canonical", result.stdout == canonical(value))
    check(f"show {name} remains non-executable", value["non_executable"] is True and not any(value["authority"].values()))
    check(f"show {name} requires all caller-owned inputs", value["caller_required"] == ["approval", "exact-repository", "path-policy", "selected-tier", "verification-commands"])

check("read-only personas stay in plan mode", shown["diff-review"]["suggested_mode"] == shown["repository-inventory"]["suggested_mode"] == "plan")
check("bounded test backfill alone suggests accept-edits", shown["bounded-test-backfill"]["suggested_mode"] == "accept-edits")
check("persona suggestions use only maintained names", {value["suggested_persona"] for value in shown.values()} == {"bulk-test-writer", "diff-reviewer", "repo-inventory"})
check("path policy shapes contain no caller path", all(value["path_policy_shape"].startswith("caller-declared-repo-relative-") for value in shown.values()))

for argv in ((), ("show",), ("show", "unknown"), ("show", "../diff-review"), ("show", "/tmp/x"), ("list", "extra"), ("LIST",)):
    result = run(*argv)
    check(f"invalid invocation {argv!r} is sanitized", result.returncode == 64 and result.stdout == b"" and result.stderr == b"profile: invalid arguments\n")

root_wrapper = subprocess.run([str(WRAPPER), "list"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=4)
check("root wrapper delegates to canonical runtime", root_wrapper.returncode == 0 and root_wrapper.stdout == listed.stdout and root_wrapper.stderr == b"")

holder, portable = bundle()
try:
    portable_list = run("list", runtime=portable)
    check("folder-only runtime has exact list parity", portable_list.returncode == 0 and portable_list.stdout == listed.stdout)
    check("folder-only runtime has exact show parity", run("show", "diff-review", runtime=portable).stdout == canonical(shown["diff-review"]))
finally:
    holder.cleanup()

with tempfile.TemporaryDirectory(prefix="agy-profile-hidden.") as directory:
    hidden = Path(directory)
    (hidden / "profiles").mkdir()
    (hidden / "profiles" / "diff-review.json").write_text('{"dispatch":true}\n', encoding="utf-8")
    environment = dict(os.environ)
    environment.update({
        "HOME": str(hidden), "PWD": str(hidden), "AGY_WORKER_PROFILE": str(hidden),
        "AGY_WORKER_TIER": "hardest", "AGY_WORKER_MODEL": "forbidden",
        "GIT_CONFIG_GLOBAL": str(hidden / "gitconfig"),
    })
    hidden_result = run("list", env=environment)
    check("environment and target-style profile hints are ignored", hidden_result.returncode == 0 and hidden_result.stdout == listed.stdout)

check("runtime source has no subprocess or shell execution", source_contract(SCRIPT.read_text(encoding="utf-8")))
source = SCRIPT.read_text(encoding="utf-8")
check("runtime source never imports git network or agy helpers", all(token not in source for token in ("import subprocess", "import socket", "import urllib", "import requests", "os.environ", "getenv(")))

check("extra profile inventory entry is rejected", lambda: rejects_bundle(lambda runtime: (runtime / "profiles" / "v1" / "extra.json").write_text("{}\n", encoding="utf-8")))
check("missing profile inventory entry is rejected", lambda: rejects_bundle(lambda runtime: (runtime / "profiles" / "v1" / "diff-review.json").unlink()))

def symlink_profile(runtime: Path) -> None:
    target = runtime / "profiles" / "v1" / "diff-review.json"
    saved = target.with_name("saved.json")
    target.rename(saved)
    target.symlink_to(saved.name)

check("symlinked profile leaf is rejected", lambda: rejects_bundle(symlink_profile))

def writable_profile(runtime: Path) -> None:
    os.chmod(runtime / "profiles" / "v1" / "diff-review.json", 0o664)

check("group-writable profile data is rejected", lambda: rejects_bundle(writable_profile))

def executable_profile(runtime: Path) -> None:
    os.chmod(runtime / "profiles" / "v1" / "diff-review.json", 0o755)

check("executable profile data is rejected", lambda: rejects_bundle(executable_profile))

def writable_parent(runtime: Path) -> None:
    os.chmod(runtime / "profiles" / "v1", 0o775)

check("writable profile directory is rejected", lambda: rejects_bundle(writable_parent))

def symlink_parent(runtime: Path) -> None:
    profiles = runtime / "profiles"
    saved = runtime / "profiles-real"
    profiles.rename(saved)
    profiles.symlink_to(saved.name)

check("symlinked profile ancestor is rejected", lambda: rejects_bundle(symlink_parent))

def drift_schema(runtime: Path) -> None:
    path = runtime / "schemas" / "workload-profile.schema.json"
    path.write_bytes(path.read_bytes() + b" ")

check("profile schema byte drift is rejected", lambda: rejects_bundle(drift_schema))

def drift_manifest(runtime: Path) -> None:
    path = runtime / "profiles" / "v1" / "manifest.json"
    value = json.loads(path.read_bytes())
    value["source_revision"] = "other"
    path.write_bytes(canonical(value))

check("manifest source revision drift is rejected", lambda: rejects_bundle(drift_manifest))

def reverse_manifest(runtime: Path) -> None:
    path = runtime / "profiles" / "v1" / "manifest.json"
    value = json.loads(path.read_bytes())
    value["profiles"].reverse()
    path.write_bytes(canonical(value))

check("manifest order drift is rejected", lambda: rejects_bundle(reverse_manifest))

def malformed_manifest(runtime: Path) -> None:
    path = runtime / "profiles" / "v1" / "manifest.json"
    path.write_bytes(b'{"kind":"x","kind":"y"}\n')

check("duplicate manifest keys are rejected", lambda: rejects_bundle(malformed_manifest))

def oversized_manifest(runtime: Path) -> None:
    path = runtime / "profiles" / "v1" / "manifest.json"
    path.write_bytes(b"x" * (16 * 1024 + 1))

check("oversized profile artifact is rejected", lambda: rejects_bundle(oversized_manifest))

module = load_module()
base = dict(shown["diff-review"])


def _rejects(action: Callable[[], Any]) -> bool:
    try:
        action()
    except Exception:
        return True
    return False


bad_fields = {
    "model": "x", "tier": "hard", "effort": "high", "thinking_level": "high",
    "verify": "pytest", "shell_command": "echo x", "add_dir": "/tmp",
    "repository": "/tmp/repo", "path": "src", "git_action": "commit",
    "auto_dispatch": True,
}
for field, value in bad_fields.items():
    mutated = dict(base)
    mutated[field] = value
    check(f"semantic validator rejects forbidden {field} field", lambda mutated=mutated: _rejects(lambda: module._validate_profile(mutated, "diff-review")))

# The following direct checks prove the exact policy gates independently of hashes.
for label, mutate in (
    ("authorization authority", lambda value: value["authority"].update({"authorization": True})),
    ("dispatch authority", lambda value: value["authority"].update({"dispatch": True})),
    ("routing authority", lambda value: value["authority"].update({"routing": True})),
    ("acceptance authority", lambda value: value["authority"].update({"acceptance": True})),
    ("executable plan", lambda value: value.update({"non_executable": False})),
    ("missing caller tier", lambda value: value["caller_required"].remove("selected-tier")),
    ("missing caller verifier", lambda value: value["caller_required"].remove("verification-commands")),
    ("mode drift", lambda value: value.update({"suggested_mode": "accept-edits"})),
    ("persona drift", lambda value: value.update({"suggested_persona": "repo-inventory"})),
    ("path shape drift", lambda value: value.update({"path_policy_shape": "outside-workdir"})),
):
    value = json.loads(canonical(base))
    mutate(value)
    check(f"semantic validator rejects {label}", lambda value=value: _rejects(lambda: module._validate_profile(value, "diff-review")))

mutations = {
    "profile extra-field gate": ('set(value) != PROFILE_FIELDS', 'False'),
    "caller-required gate": ('value["caller_required"] != REQUIRED_INPUTS', 'False'),
    "non-executable gate": ('value["non_executable"] is not True', 'False'),
    "authority gate": ('value["authority"] != NO_AUTHORITY', 'False'),
    "mode gate": ('value["suggested_mode"] != expected["mode"]', 'False'),
    "persona gate": ('value["suggested_persona"] != expected["persona"]', 'False'),
    "path-shape gate": ('value["path_policy_shape"] != expected["path_policy_shape"]', 'False'),
    "inventory allowlist gate": ('actual != allowed', 'False'),
    "profile mode gate": ('stat.S_IMODE(metadata.st_mode) != 0o644', 'False'),
    "directory writable gate": ('stat.S_IMODE(metadata.st_mode) & 0o022', 'False'),
}
for label, (old, new) in mutations.items():
    assert source.count(old) >= 1
    mutant = source.replace(old, new, 1)
    check(f"source contract kills {label} weakening", not source_contract(mutant))

schema = json.loads((RUNTIME / "schemas" / "workload-profile.schema.json").read_bytes())
check("schema is draft-07 and closes top-level extras", schema["$schema"].endswith("draft-07/schema#") and schema["additionalProperties"] is False)
check("schema requires every profile field", set(schema["required"]) == module.PROFILE_FIELDS)
check("schema closes authority extras", schema["properties"]["authority"]["additionalProperties"] is False)
check("schema fixes every authority flag false", all(item["enum"] == [False] for item in schema["properties"]["authority"]["properties"].values()))
check("schema requires all five caller-owned inputs", schema["properties"]["caller_required"]["minItems"] == schema["properties"]["caller_required"]["maxItems"] == 5)
check("schema binds exact maintained name mode persona and path-shape tuples", len(schema["oneOf"]) == 3 and {item["properties"]["name"]["const"] for item in schema["oneOf"]} == set(NAMES))

root_profiles = ROOT / "profiles" / "v1"
portable_profiles = RUNTIME / "profiles" / "v1"
check("root and portable manifests are byte-identical", (root_profiles / "manifest.json").read_bytes() == (portable_profiles / "manifest.json").read_bytes())
for name in NAMES:
    check(f"root and portable {name} bytes are identical", (root_profiles / f"{name}.json").read_bytes() == (portable_profiles / f"{name}.json").read_bytes())
    expected_hash = next(item["sha256"] for item in json.loads((root_profiles / "manifest.json").read_bytes())["profiles"] if item["name"] == name)
    check(f"manifest binds exact {name} bytes", hashlib.sha256((root_profiles / f"{name}.json").read_bytes()).hexdigest() == expected_hash)

print()
print(f"PASSED: {passed} tests")
if failed:
    print(f"FAILED: {failed} tests")
    raise SystemExit(1)
