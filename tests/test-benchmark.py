#!/usr/bin/env python3
"""Offline paired and mutation-sensitive tests for Benchmark v1."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
from typing import Callable

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "skills/agy-worker/runtime/scripts/benchmark.py"
SPEC = importlib.util.spec_from_file_location("benchmark_tested", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)
TMP = Path(tempfile.mkdtemp(prefix="agyworker-benchmark-tests.")).resolve()
TMP.chmod(0o700)
passed = 0
failed = 0


def check(name: str, action: Callable[[], bool]) -> None:
    global passed, failed
    try:
        okay = bool(action())
    except BaseException as exc:
        okay = False
        detail = f" ({type(exc).__name__}: {exc})"
    else:
        detail = ""
    if okay:
        passed += 1
    else:
        failed += 1
        print(f"FAIL benchmark: {name}{detail}")


def rejects(action: Callable[[], object]) -> bool:
    try:
        action()
    except (MODULE.BenchmarkError, MODULE.ValidationFailure, MODULE.Interrupted, OSError, subprocess.SubprocessError):
        return True
    return False


def clone(value: object) -> object:
    return json.loads(json.dumps(value))


def replace_last(source: bytes, old: bytes, new: bytes) -> bytes:
    position = source.rfind(old)
    if position < 0:
        raise AssertionError(f"mutation marker missing: {old!r}")
    return source[:position] + new + source[position + len(old):]


source = MODULE_PATH.read_bytes()
manifest, manifest_raw = MODULE.load_manifest()
variant_path = ROOT / "benchmarks/v1/variants/bulk.json"
variant = MODULE.load_variant(variant_path)

check("runner imports with bytecode disabled", lambda: sys.dont_write_bytecode)
check("runtime is canonical under the public skill", lambda: MODULE.RUNTIME == ROOT / "skills/agy-worker/runtime")
check("root and portable manifests are byte-identical", lambda: (ROOT / "benchmarks/v1/manifest.json").read_bytes() == manifest_raw)
check("root and portable task assets are byte-identical", lambda: all((ROOT / "benchmarks/v1" / task[key]).read_bytes() == (ROOT / "skills/agy-worker/runtime/benchmarks/v1" / task[key]).read_bytes() for task in manifest["tasks"] for key in ("initial_source", "candidate_source", "envelope_source")))
check("manifest fixes one bounded public synthetic task", lambda: len(manifest["tasks"]) == 1 and manifest["tasks"][0]["id"] == "exact-edit")
check("manifest fixes exact one-file scope and two verifiers", lambda: manifest["tasks"][0]["only"] == ["proof.txt"] and manifest["tasks"][0]["verifiers"] == ["exact-content", "diff-check"])
check("variant input binds a caller tier selection", lambda: variant["selection"]["selection_mode"] == "tier" and variant["selection"]["selected_tier"] == "bulk")
check("variant never supplies a persona implicitly", lambda: variant["persona"] is None)
check("variant source and selection hashes are distinct bindings", lambda: variant["source_sha256"] == hashlib.sha256(variant_path.read_bytes()).hexdigest() and variant["selection_sha256"] == MODULE.digest(MODULE.canonical_bytes(variant["selection"])))
check("runner source contract accepts canonical source", lambda: MODULE.validate_source_contract(source) is None)


def manifest_mutation(key: str, value: object) -> bool:
    changed = clone(manifest); assert isinstance(changed, dict)
    changed[key] = value
    return rejects(lambda: MODULE.validate_manifest(changed))


check("manifest rejects future schema", lambda: manifest_mutation("schema_version", 2))
check("manifest rejects expanded bounds", lambda: manifest_mutation("limits", {**manifest["limits"], "max_runs": 65}))
check("manifest rejects duplicate tasks", lambda: manifest_mutation("tasks", manifest["tasks"] * 2))
check("manifest rejects a missing task", lambda: manifest_mutation("tasks", []))


def changed_task(key: str, value: object) -> bool:
    changed = clone(manifest); assert isinstance(changed, dict)
    changed["tasks"][0][key] = value
    return rejects(lambda: MODULE.validate_manifest(changed))


check("manifest rejects fixture hash drift", lambda: changed_task("candidate_sha256", "0" * 64))
check("manifest rejects path traversal", lambda: changed_task("candidate_source", "../candidate.txt"))
check("manifest rejects broader path policy", lambda: changed_task("only", ["**"] ))
check("manifest rejects verifier removal", lambda: changed_task("verifiers", ["exact-content"]))


def variant_reject(mutator: Callable[[dict], None]) -> bool:
    value = json.loads(variant_path.read_text())
    mutator(value)
    path = TMP / ("variant-" + hashlib.sha256(json.dumps(value).encode()).hexdigest()[:8] + ".json")
    path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n")
    return rejects(lambda: MODULE.load_variant(path))


check("variant rejects duplicate-name grammar escape", lambda: variant_reject(lambda value: value.__setitem__("name", "../bulk")))
check("variant rejects inferred selection mode", lambda: variant_reject(lambda value: value["selection"].__setitem__("selection_mode", "auto")))
check("variant rejects a thinking flag", lambda: variant_reject(lambda value: value["selection"].__setitem__("thinking", "high")))
check("variant rejects missing resolved model", lambda: variant_reject(lambda value: value["selection"].__setitem__("resolved_agy_model", "")))
check("variant rejects noncanonical persona source", lambda: variant_reject(lambda value: value.__setitem__("persona", {"name": "reviewer", "source": "persona.txt"})))


def tier_record(source_name: str, resolved: str = "gemini-3.6-flash-medium") -> dict:
    tier = "default" if source_name == "implicit-default" else "bulk"
    resolved_model = None if source_name == "implicit-default" else resolved
    return {
        "schema_version": 1,
        "kind": "agy-worker-selection",
        "selection_mode": "tier",
        "selected_tier": tier,
        "selected_tier_source": source_name,
        "resolved_agy_model": resolved_model,
    }


def model_effort_record() -> dict:
    return {
        "schema_version": 1,
        "kind": "agy-worker-selection",
        "selection_mode": "model-effort",
        "user_model": "gemini-3.6-flash",
        "user_model_source": "cli",
        "user_effort": "high",
        "user_effort_source": "environment",
        "resolved_agy_model": "gemini-3.6-flash-high",
        "installed_agy_version": "1.1.10",
        "matrix_sha256": "0" * 64,
        "matrix_agy_version": "1.1.10",
        "matrix_source_revision": "0" * 40,
    }


check("canonical environment tier selection is accepted", lambda: MODULE.validate_selection(tier_record("environment")) == tier_record("environment"))
check("canonical implicit-default tier selection is accepted", lambda: MODULE.validate_selection(tier_record("implicit-default")) == tier_record("implicit-default"))
check("canonical model-effort selection with provenance is accepted", lambda: MODULE.validate_selection(model_effort_record()) == model_effort_record())
check("forged env tier source is rejected", lambda: rejects(lambda: MODULE.validate_selection(tier_record("env"))))
check("arbitrary tier resolution is rejected", lambda: rejects(lambda: MODULE.validate_selection(tier_record("environment", "forged-model"))))
check("model-effort selection missing provenance is rejected", lambda: rejects(lambda: MODULE.validate_selection({key: value for key, value in model_effort_record().items() if key != "user_model_source"})))


private_root = TMP / "private"
private_root.mkdir(mode=0o700)
check("external owner-0700 result root is accepted", lambda: MODULE.canonical_external_root(private_root) == private_root)
check("repository result root is rejected", lambda: rejects(lambda: MODULE.canonical_external_root(ROOT)))
mode_root = TMP / "mode-root"; mode_root.mkdir(mode=0o755)
check("non-private result root is rejected", lambda: rejects(lambda: MODULE.canonical_external_root(mode_root)))


def publication_roundtrip() -> bool:
    root = TMP / "publication"; root.mkdir(mode=0o700)
    path, sha = MODULE.publish_new(root, "facts.json", b"{}\n")
    mode = stat.S_IMODE(path.stat().st_mode)
    duplicate = rejects(lambda: MODULE.publish_new(root, "facts.json", b"changed\n"))
    MODULE.ACTIVE_PUBLICATIONS.clear()
    return path.read_bytes() == b"{}\n" and mode == 0o600 and sha == MODULE.digest(b"{}\n") and duplicate


check("publication is owner-only, durable-shaped, and no-overwrite", publication_roundtrip)


def interrupted_link_rolls_back() -> bool:
    root = TMP / "interrupt-link"; root.mkdir(mode=0o700)
    real = MODULE.os.link
    def interrupted(*args: object, **kwargs: object) -> None:
        real(*args, **kwargs)
        raise MODULE.Interrupted(signal.SIGINT)
    MODULE.os.link = interrupted
    try:
        okay = rejects(lambda: MODULE.publish_new(root, "plan.v1.json", b"{}\n"))
    finally:
        MODULE.os.link = real
        MODULE.ACTIVE_PUBLICATIONS.clear()
    return okay and list(root.iterdir()) == []


check("signal after durable link leaves no final or temp", interrupted_link_rolls_back)


supervisor_dir = TMP / "supervisor"; supervisor_dir.mkdir(mode=0o700)
SUCCESS_TIMEOUT = 5.0


def bounded(code: str, *arguments: str, timeout: float = SUCCESS_TIMEOUT) -> tuple[int, bytes, bytes]:
    return MODULE.run_bounded(["/usr/bin/python3", "-I", "-S", "-B", "-c", code, *arguments], supervisor_dir, timeout)


check("bounded supervisor preserves a clean zero exit", lambda: bounded("raise SystemExit(0)")[0] == 0)
check("bounded supervisor preserves a nonzero exit", lambda: bounded("raise SystemExit(14)")[0] == 14)
check("bounded supervisor rejects stdout overflow", lambda: rejects(lambda: bounded("import os;os.write(1,b'x'*131073)")))


def timeout_authority_pair() -> bool:
    child = "import time;time.sleep(2)"
    secure_rejects = rejects(lambda: bounded(child, timeout=0.1))
    real = MODULE.run_bounded
    MODULE.run_bounded = lambda argv, cwd, timeout: real(argv, cwd, SUCCESS_TIMEOUT)
    try:
        weakening_exposed = not rejects(lambda: bounded(child, timeout=0.1))
    finally:
        MODULE.run_bounded = real
    return secure_rejects and weakening_exposed


check("bounded supervisor rejects a wall timeout and kills timeout weakening", timeout_authority_pair)
check("bounded supervisor sanitizes launch failure", lambda: rejects(lambda: MODULE.run_bounded([str(supervisor_dir / "missing")], supervisor_dir, 0.1)))


def leader_exit_descendant_cleanup() -> bool:
    marker = supervisor_dir / "late-marker"
    code = "import os,signal,sys,time\np=os.fork()\nif p==0:\n signal.signal(signal.SIGTERM,signal.SIG_IGN)\n os.close(1);os.close(2);time.sleep(.4);open(sys.argv[1],'w').write('late')\nos._exit(0)"
    rc, out, err = bounded(code, str(marker), timeout=1.0)
    import time as _time
    _time.sleep(0.5)
    return rc == 0 and out == b"" and err == b"" and not marker.exists()


check("leader-exit TERM-ignoring descendant is killed before acceptance", leader_exit_descendant_cleanup)


for marker, replacement, label in (
    (b'"attempts_per_variant_task": 1', b'"attempts_per_variant_task": 2', "attempt authority"),
    (b'"live_execution": False', b'"live_execution": True ', "offline authority"),
    (b'argv = [str(VERIFY_JOB),', b'argv = ["agy",', "gate authority"),
    (b'os._exit(0)', b'return', "atomic process exit"),
    (b'create_mask = signal.pthread_sigmask', b'create_mask = None # removed mask', "temp-create signal mask"),
    (b'link_mask = signal.pthread_sigmask', b'link_mask = None # removed mask', "link signal mask"),
    (b'if hasattr(signal, "pthread_sigmask"): signal.pthread_sigmask(signal.SIG_BLOCK, SIGNALS)', b'if False: pass', "cleanup signal mask"),
    (b'_leader_exited_unreaped(process)', b'True', "non-reaping leader observation"),
    (b'_close_group(process)', b'process.wait()', "pre-reap group cleanup"),
    (b'validate_selection_record_shape(value)', b'pass # canonical validator removed', "canonical selection validation"),
    (b'load_portable_source()', b'({}, b"")', "portable source manifest validation"),
    (b'observed != BENCHMARK_TREE_FILES', b'False', "portable extra-file rejection"),
    (b'stat.S_IMODE(st.st_mode) != mode', b'False', "portable file-mode rejection"),
):
    check(f"source mutation removing {label} is rejected", lambda marker=marker, replacement=replacement: rejects(lambda: MODULE.validate_source_contract(replace_last(source, marker, replacement))))


# Build one clean committed disposable checkout. All subprocess work stays offline.
source_copy = TMP / "source"
shutil.copytree(ROOT, source_copy, ignore=shutil.ignore_patterns(".git", "logs", "__pycache__", "*.pyc"))
subprocess.run(["/usr/bin/git", "init", "-q", "-b", "main"], cwd=source_copy, check=True)
subprocess.run(["/usr/bin/git", "config", "user.name", "test"], cwd=source_copy, check=True)
subprocess.run(["/usr/bin/git", "config", "user.email", "test@example.invalid"], cwd=source_copy, check=True)
subprocess.run(["/usr/bin/git", "add", "-A"], cwd=source_copy, check=True)
subprocess.run(["/usr/bin/git", "commit", "-qm", "source"], cwd=source_copy, check=True)
results = TMP / "results"; results.mkdir(mode=0o700)
wrapper = source_copy / "benchmark.sh"
copy_variant = source_copy / "benchmarks/v1/variants/bulk.json"


def invoke(*args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run([str(wrapper), *args], cwd=source_copy, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=35, check=False)


prepare = invoke("prepare", "--result-root", str(results), "--variant", str(copy_variant))
check("prepare succeeds once from a clean exact checkout", lambda: prepare.returncode == 0 and not prepare.stderr and (results / "plan.v1.json").is_file())
plan_raw = (results / "plan.v1.json").read_bytes()
plan = json.loads(plan_raw)
check("plan is canonical and owner-only", lambda: plan_raw == MODULE.canonical_bytes(plan) and stat.S_IMODE((results / "plan.v1.json").stat().st_mode) == 0o600)
check("plan binds exact one attempt before run", lambda: plan["expected_runs"] == 1 and plan["policy"]["attempts_per_variant_task"] == 1)
check("plan binds clean checkout source and all tool hashes", lambda: plan["tool_authority"]["source_kind"] == "git-clean-commit" and len(plan["tool_authority"]["source_revision"]) == 40 and all(len(value) == 64 for key, value in plan["tool_authority"].items() if key not in {"source_kind", "source_revision"}))


def rejects_nonhex_source_commit() -> bool:
    changed = clone(plan); assert isinstance(changed, dict)
    changed["tool_authority"]["source_revision"] = "z" * 40
    return rejects(lambda: MODULE.validate_plan(changed, MODULE.canonical_bytes(changed)))


check("plan rejects a non-hex source commit binding", rejects_nonhex_source_commit)
check("plan preregisters variants and tasks in order", lambda: [item["name"] for item in plan["variants"]] == ["bulk"] and [item["id"] for item in plan["tasks"]] == ["exact-edit"])


def rejects_too_many_variants() -> bool:
    changed = clone(plan); assert isinstance(changed, dict)
    changed["variants"] = []
    for index in range(9):
        entry = clone(plan["variants"][0]); entry["name"] = f"bulk-{index}"
        changed["variants"].append(entry)
    changed["expected_runs"] = 9
    return rejects(lambda: MODULE.validate_plan(changed, MODULE.canonical_bytes(changed)))


check("plan rejects variants beyond the frozen bound", rejects_too_many_variants)
check("prepare refuses overwrite", lambda: invoke("prepare", "--result-root", str(results), "--variant", str(copy_variant)).returncode == 2)
check("live execution flag is not exposed", lambda: invoke("run", "--plan", str(results / "plan.v1.json"), "--live").returncode != 0)


def invalid_arguments_are_sanitized() -> bool:
    private = "/private/SECRET-BENCHMARK-PATH"
    completed = invoke("run", "--plan", private, private)
    return completed.returncode == 64 and private.encode() not in completed.stdout + completed.stderr and b"Traceback" not in completed.stderr


check("invalid arguments expose no caller path or traceback", invalid_arguments_are_sanitized)


def forged_selection_precedes_plan_publication() -> bool:
    forged = json.loads(copy_variant.read_text())
    forged["selection"]["selected_tier_source"] = "env"
    forged["selection"]["resolved_agy_model"] = "forged-model"
    forged_path = TMP / "forged-variant.json"
    forged_path.write_text(json.dumps(forged, sort_keys=True, separators=(",", ":")) + "\n")
    forged_root = TMP / "forged-results"
    forged_root.mkdir(mode=0o700)
    completed = invoke("prepare", "--result-root", str(forged_root), "--variant", str(forged_path))
    return completed.returncode == 2 and not completed.stdout and not (forged_root / "plan.v1.json").exists() and list(forged_root.iterdir()) == []


check("forged selection fails before immutable plan publication", forged_selection_precedes_plan_publication)

readme_copy = source_copy / "README.md"
readme_original = readme_copy.read_bytes(); readme_copy.write_bytes(readme_original + b"\n")
check("run rejects source worktree drift after prepare", lambda: invoke("run", "--plan", str(results / "plan.v1.json")).returncode == 2 and not list(results.glob("receipt-*.json")))
readme_copy.write_bytes(readme_original)

run = invoke("run", "--plan", str(results / "plan.v1.json"))
check("offline run succeeds with exactly one receipt", lambda: run.returncode == 0 and not run.stderr and len(list(results.glob("receipt-*.json"))) == 1)
result_raw = (results / "result.v1.json").read_bytes()
result = json.loads(result_raw)
check("result is canonical, complete, owner-only, and unsigned", lambda: result_raw == MODULE.canonical_bytes(result) and result["complete"] is True and result["integrity"] == {"signed": False, "tamper_evident": False} and stat.S_IMODE((results / "result.v1.json").stat().st_mode) == 0o600)
check("result binds raw plan and receipt bytes", lambda: result["plan_sha256"] == MODULE.digest(plan_raw) and result["runs"][0]["receipt_sha256"] == MODULE.digest((results / "receipt-001.json").read_bytes()))
check("result preserves qa-gate as sole verdict authority", lambda: result["runs"][0]["gate_exit"] == 0 and result["runs"][0]["verdict"] == "gate-passed" and result["policy"]["gate_authority"] == "qa-gate" and result["policy"]["live_execution"] is False and result["policy"]["provider"] is False)
check("run refuses result overwrite and never retries", lambda: invoke("run", "--plan", str(results / "plan.v1.json")).returncode == 2 and len(list(results.glob("receipt-*.json"))) == 1)

report = invoke("report", "--plan", str(results / "plan.v1.json"), "--result", str(results / "result.v1.json"))
report_value = json.loads(report.stdout)
check("report is pure deterministic manifest-order facts", lambda: report.returncode == 0 and report_value["facts"] == [{"attempt": 1, "gate_exit": 0, "gate_outcome": "gate-passed", "sequence": 1, "task": "exact-edit", "variant": "bulk", "verdict": "gate-passed"}])
check("report emits no winner, score, route, or recommendation", lambda: report_value["winner"] is None and report_value["ranking"] is False and report_value["recommendation"] is None and b"score" not in report.stdout and b"route" not in report.stdout)


def pure_report_uses_no_subprocess() -> bool:
    real_run, real_popen = MODULE.subprocess.run, MODULE.subprocess.Popen
    def forbidden(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise AssertionError("report attempted a subprocess")
    MODULE.subprocess.run = forbidden
    MODULE.subprocess.Popen = forbidden
    try:
        value = MODULE.command_report(type("Args", (), {"plan": str(results / "plan.v1.json"), "result": str(results / "result.v1.json")})())
        return value["complete"] is True
    finally:
        MODULE.subprocess.run, MODULE.subprocess.Popen = real_run, real_popen


check("report validation is subprocess-free", pure_report_uses_no_subprocess)


plan_schema = MODULE.load_schema(MODULE.PLAN_SCHEMA)
result_schema = MODULE.load_schema(MODULE.RESULT_SCHEMA)
check("deep plan schema accepts the canonical plan", lambda: MODULE.validate_schema(plan, plan_schema) is None)
check("deep result schema accepts the canonical result", lambda: MODULE.validate_schema(result, result_schema) is None)


def schema_rejects(schema: dict, value: dict, mutator: Callable[[dict], None]) -> bool:
    changed = clone(value); assert isinstance(changed, dict)
    mutator(changed)
    try:
        MODULE.validate_schema(changed, schema)
    except MODULE.ValidationFailure:
        return True
    return False


check("plan schema rejects a fractional run count", lambda: schema_rejects(plan_schema, plan, lambda value: value.__setitem__("expected_runs", 1.5)))
check("plan schema rejects nested policy extras", lambda: schema_rejects(plan_schema, plan, lambda value: value["policy"].__setitem__("score", False)))
check("plan schema rejects an invalid tool digest pattern", lambda: schema_rejects(plan_schema, plan, lambda value: value["tool_authority"].__setitem__("qa_gate_sha256", "z" * 64)))
check("plan schema rejects missing selection provenance", lambda: schema_rejects(plan_schema, plan, lambda value: value["variants"][0]["selection"].pop("selected_tier_source")))
check("plan schema rejects task array overflow", lambda: schema_rejects(plan_schema, plan, lambda value: value.__setitem__("tasks", value["tasks"] * 17)))
check("result schema rejects run extras", lambda: schema_rejects(result_schema, result, lambda value: value["runs"][0].__setitem__("score", 1)))
check("result schema rejects invalid receipt-name grammar", lambda: schema_rejects(result_schema, result, lambda value: value["runs"][0].__setitem__("receipt_name", "../receipt.json")))


def nested_get(value: dict, path: tuple[object, ...]) -> object:
    current: object = value
    for key in path:
        current = current[key]  # type: ignore[index]
    return current


def nested_set(value: dict, path: tuple[object, ...], replacement: object) -> None:
    current: object = value
    for key in path[:-1]:
        current = current[key]  # type: ignore[index]
    current[path[-1]] = replacement  # type: ignore[index]


def bad_full_strings(original: str) -> list[str]:
    return [original + "\n", original + "\r", original + "\r\n", original[:1] + "\x00" + original[1:]]


def patterned_fields_reject(schema: dict, value: dict, paths: list[tuple[object, ...]]) -> bool:
    for path in paths:
        original = nested_get(value, path)
        assert isinstance(original, str)
        for invalid in bad_full_strings(original):
            changed = clone(value); assert isinstance(changed, dict)
            nested_set(changed, path, invalid)
            try:
                MODULE.validate_schema(changed, schema)
            except MODULE.ValidationFailure:
                continue
            return False
    return True


plan_pattern_paths = [("manifest_sha256",), ("tool_authority", "source_revision")]
plan_pattern_paths.extend(("tool_authority", key) for key in plan["tool_authority"] if key not in {"source_kind", "source_revision"})
plan_pattern_paths.extend(("tasks", 0, key) for key in ("id", "initial_sha256", "candidate_sha256", "envelope_sha256"))
plan_pattern_paths.extend(("variants", 0, key) for key in ("name", "source_sha256", "selection_sha256"))
result_pattern_paths = [("plan_sha256",), ("manifest_sha256",)]
result_pattern_paths.extend(("runs", 0, key) for key in ("receipt_name", "receipt_sha256", "resolved_base", "initial_candidate_state_sha256", "final_candidate_state_sha256", "selection_sha256", "task", "variant"))
check("every canonical plan patterned field rejects LF CR CRLF and embedded control", lambda: patterned_fields_reject(plan_schema, plan, plan_pattern_paths))
check("every canonical result patterned field rejects LF CR CRLF and embedded control", lambda: patterned_fields_reject(result_schema, result, result_pattern_paths))

persona_plan = clone(plan); assert isinstance(persona_plan, dict)
persona_plan["variants"][0]["persona"] = {"name": "reviewer", "source_sha256": "0" * 64}
check("schema accepts a canonical bounded persona provenance shape", lambda: MODULE.validate_schema(persona_plan, plan_schema) is None)
check("persona patterned fields reject all terminal and embedded controls", lambda: patterned_fields_reject(plan_schema, persona_plan, [("variants", 0, "persona", "name"), ("variants", 0, "persona", "source_sha256")]))

model_plan = clone(plan); assert isinstance(model_plan, dict)
model_plan["variants"][0]["selection"] = model_effort_record()
model_plan["variants"][0]["selection_sha256"] = "0" * 64
check("schema accepts canonical model-effort provenance structure", lambda: MODULE.validate_schema(model_plan, plan_schema) is None)
check("model-effort version and revision patterns reject all controls", lambda: patterned_fields_reject(plan_schema, model_plan, [("variants", 0, "selection", key) for key in ("installed_agy_version", "matrix_agy_version", "matrix_sha256", "matrix_source_revision")]))


def terminal_newline_schema_weakening_exposed() -> bool:
    weakened_plan = clone(plan_schema); assert isinstance(weakened_plan, dict)
    digest_rule = weakened_plan["properties"]["manifest_sha256"]
    digest_rule.pop("maxLength")
    digest_rule["pattern"] = "^[0-9a-f]{64}$"
    invalid_plan = clone(plan); assert isinstance(invalid_plan, dict)
    invalid_plan["manifest_sha256"] += "\n"
    weakened_result = clone(result_schema); assert isinstance(weakened_result, dict)
    receipt_rule = weakened_result["properties"]["runs"]["items"]["properties"]["receipt_name"]
    receipt_rule.pop("maxLength")
    receipt_rule["pattern"] = "^receipt-[0-9]{3}\\.json$"
    invalid_result = clone(result); assert isinstance(invalid_result, dict)
    invalid_result["runs"][0]["receipt_name"] += "\n"
    try:
        MODULE.validate_schema(invalid_plan, weakened_plan)
        MODULE.validate_schema(invalid_result, weakened_result)
    except MODULE.ValidationFailure:
        return False
    return True


check("terminal-newline grammar weakening mutations are exposed", terminal_newline_schema_weakening_exposed)


def schema_weakening_is_exposed() -> bool:
    weakened = clone(plan_schema); assert isinstance(weakened, dict)
    weakened["properties"]["policy"]["additionalProperties"] = True
    invalid = clone(plan); assert isinstance(invalid, dict)
    invalid["policy"]["score"] = False
    try:
        MODULE.validate_schema(invalid, weakened)
    except MODULE.ValidationFailure:
        return False
    return True


check("schema weakening mutation is exposed by the invalid corpus", schema_weakening_is_exposed)


def portable_invoke(skill: Path, *arguments: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run([str(skill / "runtime/benchmark.sh"), *arguments], cwd=skill, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=35, check=False)


portable_skill = TMP / "portable-skill"
shutil.copytree(ROOT / "skills/agy-worker", portable_skill, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
portable_results = TMP / "portable-results"; portable_results.mkdir(mode=0o700)
portable_variant = portable_skill / "runtime/benchmarks/v1/variants/bulk.json"
portable_prepare = portable_invoke(portable_skill, "prepare", "--result-root", str(portable_results), "--variant", str(portable_variant))
portable_plan = json.loads((portable_results / "plan.v1.json").read_bytes()) if (portable_results / "plan.v1.json").is_file() else {}
check("folder-only bundle prepares without checkout or Git authority", lambda: portable_prepare.returncode == 0 and not portable_prepare.stderr and portable_plan.get("tool_authority", {}).get("source_kind") == "portable-runtime" and portable_plan["tool_authority"]["source_revision"] == "offline-benchmark-v1")
portable_run = portable_invoke(portable_skill, "run", "--plan", str(portable_results / "plan.v1.json"))
portable_report = portable_invoke(portable_skill, "report", "--plan", str(portable_results / "plan.v1.json"), "--result", str(portable_results / "result.v1.json"))
check("folder-only bundle runs and reports one offline gate receipt", lambda: portable_run.returncode == 0 and portable_report.returncode == 0 and json.loads(portable_report.stdout)["facts"][0]["gate_exit"] == 0)


def portable_reject(name: str, mutator: Callable[[Path], None]) -> bool:
    skill = TMP / ("portable-reject-" + name)
    shutil.copytree(ROOT / "skills/agy-worker", skill, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    mutator(skill)
    result_root = TMP / ("portable-reject-results-" + name); result_root.mkdir(mode=0o700)
    completed = portable_invoke(skill, "prepare", "--result-root", str(result_root), "--variant", str(skill / "runtime/benchmarks/v1/variants/bulk.json"))
    return completed.returncode == 2 and not completed.stdout and b"Traceback" not in completed.stderr and not (result_root / "plan.v1.json").exists()


check("portable authority rejects a missing file", lambda: portable_reject("missing", lambda skill: (skill / "runtime/qa-gate.sh").unlink()))
check("portable authority rejects file digest drift", lambda: portable_reject("drift", lambda skill: (skill / "runtime/qa-gate.sh").write_bytes((skill / "runtime/qa-gate.sh").read_bytes() + b"\n")))
check("portable authority rejects writable file mode", lambda: portable_reject("writable", lambda skill: os.chmod(skill / "runtime/qa-gate.sh", 0o777)))
check("portable authority rejects executable mode drift", lambda: portable_reject("mode", lambda skill: os.chmod(skill / "runtime/qa-gate.sh", 0o644)))


def symlink_portable(skill: Path) -> None:
    target = skill / "qa-copy.sh"
    shutil.copy2(skill / "runtime/qa-gate.sh", target)
    (skill / "runtime/qa-gate.sh").unlink()
    (skill / "runtime/qa-gate.sh").symlink_to(target)


check("portable authority rejects a symlinked file", lambda: portable_reject("symlink", symlink_portable))
check("portable authority rejects an extra benchmark asset", lambda: portable_reject("extra", lambda skill: (skill / "runtime/benchmarks/v1/extra.txt").write_text("extra\n")))


def mutate_portable_revision(skill: Path) -> None:
    path = skill / "runtime/benchmarks/v1/portable-source.json"
    value = json.loads(path.read_bytes()); value["source_revision"] = "offline-benchmark-v2"
    path.write_bytes(MODULE.canonical_bytes(value) + b"\n")


check("portable authority rejects source-revision drift", lambda: portable_reject("revision", mutate_portable_revision))


def tamper_then_report(target: Path, mutator: Callable[[dict], None]) -> bool:
    original = target.read_bytes(); value = json.loads(original); mutator(value); target.write_bytes(MODULE.canonical_bytes(value))
    try: return invoke("report", "--plan", str(results / "plan.v1.json"), "--result", str(results / "result.v1.json")).returncode == 2
    finally: target.write_bytes(original); os.chmod(target, 0o600)


check("report rejects partial results", lambda: tamper_then_report(results / "result.v1.json", lambda value: value.__setitem__("complete", False)))
check("report rejects reordered or duplicate sequence", lambda: tamper_then_report(results / "result.v1.json", lambda value: value["runs"][0].__setitem__("sequence", 2)))
check("report rejects receipt hash drift", lambda: tamper_then_report(results / "result.v1.json", lambda value: value["runs"][0].__setitem__("receipt_sha256", "0" * 64)))
check("report rejects plan policy drift", lambda: tamper_then_report(results / "plan.v1.json", lambda value: value["policy"].__setitem__("ranking", True)))
check("no command output exposes fixture workspace names", lambda: b"agy-benchmark." not in prepare.stdout + prepare.stderr + run.stdout + run.stderr + report.stdout + report.stderr)

shutil.rmtree(TMP)
print(f"offline benchmark tests: {passed} passed, {failed} failed")
raise SystemExit(0 if failed == 0 else 1)
