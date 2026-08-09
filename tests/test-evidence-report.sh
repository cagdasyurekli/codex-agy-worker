#!/usr/bin/env bash
# Offline paired and mutation-sensitive tests for the Evidence Report renderer.
set -uo pipefail

HERE="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
ROOT="$(CDPATH= cd -- "$HERE/.." && pwd -P)"
TMP="$(mktemp -d -t agyworker-evidence-report.XXXXXX)"
trap 'rm -rf -- "$TMP"' EXIT

python3 -I -S -B - "$ROOT" "$TMP" <<'PY'
import copy
import contextlib
import hashlib
import importlib.util
import json
import os
import io
from pathlib import Path
import stat
import subprocess
import sys

ROOT = Path(sys.argv[1])
TMP = Path(sys.argv[2])
REPORT = ROOT / "evidence-report.sh"
SCRIPTS = ROOT / "skills/agy-worker/runtime/scripts"
RUNTIME = ROOT / "skills/agy-worker/runtime"
passed = 0
failed = 0


def case(name, condition):
    global passed, failed
    if condition:
        print(f"  ok   {name}")
        passed += 1
    else:
        print(f"  FAIL {name}")
        failed += 1


def receipt(exit_code=0):
    outcomes = {
        0: ("gate-passed", "gate-passed"),
        10: ("scope-violation", "rejected"),
        11: ("untrusted-worker-claim", "rejected"),
        12: ("invalid-envelope", "rejected"),
        13: ("expected-edits-missing", "rejected"),
        14: ("driver-verification-failed", "rejected"),
        15: ("worker-escalation", "routed"),
    }
    outcome, verdict = outcomes[exit_code]
    return {
        "schema_version": 1,
        "kind": "agy-worker-evidence-receipt",
        "gate_authority": "qa-gate",
        "resolved_base": "a" * 40,
        "envelope_sha256": "b" * 64,
        "path_policy_sha256": "c" * 64,
        "verifiers": [
            {"label": "verify-001", "command_sha256": "d" * 64},
            {"label": "verify-002", "command_sha256": "1" * 64},
        ],
        "initial_candidate_state_sha256": "e" * 64,
        "final_candidate_state_sha256": "f" * 64,
        "gate_exit": exit_code,
        "gate_outcome": outcome,
        "verdict": verdict,
        "recommendations_participated_in_acceptance": False,
        "integrity": {
            "signed": False,
            "tamper_evident": False,
            "statement": "Unsigned local record; schema-valid content can be rewritten and is not self-authenticating.",
        },
    }


def recommendation():
    return {
        "schema_version": 1,
        "kind": "model-tier-recommendation",
        "stage": "pre-dispatch",
        "recommendation_only": True,
        "applied": False,
        "decision": "no-escalation",
        "recommended_tier": None,
        "rationale": "The selected tier already meets or exceeds the driver-evidenced bulk task profile.",
        "cost_impact": {
            "direction": "none",
            "relative_tier_steps": 0,
            "summary": "No tier change is recommended; no incremental model cost is proposed.",
        },
        "evidence": {
            "owner": "driver",
            "code": "batched-mechanical",
            "description": "The driver identified a bounded batch of mechanical work.",
        },
        "selected_tier": "bulk",
    }


def selection():
    return {
        "schema_version": 1,
        "kind": "agy-worker-selection",
        "selection_mode": "tier",
        "selected_tier": "bulk",
        "selected_tier_source": "cli",
        "resolved_agy_model": "gemini-3.6-flash-medium",
    }


counter = 0
def write_json(value, prefix="receipt"):
    global counter
    counter += 1
    path = TMP / f"{prefix}-{counter}.json"
    path.write_bytes(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("ascii"))
    return path


def run(value, fmt="text", extra=()):
    path = write_json(value)
    return subprocess.run(
        [str(REPORT), "--receipt", str(path), "--format", fmt, *extra],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=10,
    )


print("Evidence Report offline test suite")
print()
print("stable gate outcome rendering:")
for exit_code, expected in (
    (0, "gate-passed"),
    (10, "rejected"),
    (11, "rejected"),
    (12, "rejected"),
    (13, "rejected"),
    (14, "rejected"),
    (15, "routed"),
):
    result = run(receipt(exit_code))
    case(
        f"exit {exit_code} renders exact {expected} verdict",
        result.returncode == 0
        and result.stderr == b""
        and f"Verdict: {expected}\n".encode() in result.stdout,
    )
base = receipt()
first = run(base)
second = run(base)
case("text rendering is byte-stable", first.returncode == 0 and first.stdout == second.stdout)
expected_text = (
    "Evidence Report v1\n"
    "Verdict: gate-passed\n"
    "Gate outcome: gate-passed (exit 0)\n"
    "Gate authority: qa-gate\n"
    f"Resolved base: {'a'*40}\n"
    f"Envelope SHA-256: {'b'*64}\n"
    f"Path policy SHA-256: {'c'*64}\n"
    f"Initial candidate state SHA-256: {'e'*64}\n"
    f"Final candidate state SHA-256: {'f'*64}\n"
    "Verification labels (2): verify-001, verify-002\n"
    "Caller selection bound: no\n"
    "Pre-dispatch recommendation bound: no\n"
    "Recommendations participated in acceptance: no\n"
    "Integrity: unsigned and not tamper-evident\n"
    "Human review: required before calling a gate-passed candidate accepted.\n"
).encode("ascii")
case("text renderer matches the exact v1 byte contract", first.stdout == expected_text)
markdown = run(base, "markdown")
case(
    "Markdown rendering is stable and bounded",
    markdown.returncode == 0
    and markdown.stdout.startswith(b"# Evidence Report v1\n")
    and len(markdown.stdout) < 65536,
)
case(
    "verification labels render without verifier commands",
    b"`verify-001`, `verify-002`" in markdown.stdout and b"d" * 64 not in markdown.stdout,
)
case(
    "report states unsigned and human-review limits",
    b"unsigned and not tamper-evident" in first.stdout
    and b"Human review: required" in first.stdout,
)

print()
print("explicit private output publication:")
output = TMP / "report.txt"
published = run(base, extra=("--output", str(output)))
case(
    "explicit output publishes exact mode 0600 and no stdout",
    published.returncode == 0
    and published.stdout == b""
    and output.read_bytes() == first.stdout
    and stat.S_IMODE(output.stat().st_mode) == 0o600,
)
before = output.read_bytes()
overwrite = run(base, extra=("--output", str(output)))
case("existing output is never overwritten", overwrite.returncode == 64 and output.read_bytes() == before)
link = TMP / "report-link"
link.symlink_to(output)
linked = run(base, extra=("--output", str(link)))
case("symlink output is rejected", linked.returncode == 64 and link.is_symlink())
relative = run(base, extra=("--output", "relative-report.txt"))
case("relative output is rejected", relative.returncode == 64)
case(
    "successful publication leaves no temporary",
    not any(path.name.startswith(".report.txt.evidence-report.") for path in TMP.iterdir()),
)

print()
print("receipt and injection rejection:")
bad_json = TMP / "malformed.json"
bad_json.write_bytes(b"{")
malformed = subprocess.run(
    [str(REPORT), "--receipt", str(bad_json), "--format", "text"],
    stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
)
case("malformed JSON produces no report", malformed.returncode == 1 and malformed.stdout == b"")
duplicate = TMP / "duplicate.json"
raw = json.dumps(base).replace('"schema_version": 1', '"schema_version": 1, "schema_version": 1')
duplicate.write_text(raw, encoding="ascii")
dup = subprocess.run([str(REPORT), "--receipt", str(duplicate), "--format", "text"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
case("duplicate JSON key produces no report", dup.returncode == 1 and dup.stdout == b"")
private = copy.deepcopy(base); private["prompt"] = "/Users/private/secret"
case("forbidden private field produces no report", run(private).returncode == 1)
wrong = copy.deepcopy(base); wrong["verdict"] = "rejected"
case("internally inconsistent outcome produces no report", run(wrong).returncode == 1)
version = copy.deepcopy(base); version["schema_version"] = 2
case("unsupported receipt version produces no report", run(version).returncode == 1)
control = copy.deepcopy(base); control["verifiers"][0]["label"] = "verify-001\nsecret"
case("control-character label produces no report", run(control).returncode == 1)
injection = copy.deepcopy(base); injection["verifiers"][0]["label"] = "[x](file:///secret)"
case("Markdown-link injection produces no report", run(injection, "markdown").returncode == 1)
missing = copy.deepcopy(base); missing["verifiers"] = []
case("receipt without verification evidence produces no report", run(missing).returncode == 1)

print()
print("separately trusted bindings:")
envelope = TMP / "envelope.json"; envelope.write_bytes(b"bound-envelope\n")
bound = copy.deepcopy(base); bound["envelope_sha256"] = hashlib.sha256(envelope.read_bytes()).hexdigest()
match = run(bound, extra=("--envelope", str(envelope)))
case("matching envelope digest renders", match.returncode == 0)
other = TMP / "other-envelope"; other.write_bytes(b"other\n")
case("mismatched envelope digest renders nothing", run(bound, extra=("--envelope", str(other))).returncode == 1)
selected = selection(); selected_path = write_json(selected, "selection")
selected_receipt = copy.deepcopy(base); selected_receipt["caller_selection"] = selected
case("matching selection artifact renders", run(selected_receipt, extra=("--selection", str(selected_path))).returncode == 0)
different_selection = copy.deepcopy(selected); different_selection["selected_tier"] = "hard"; different_selection["resolved_agy_model"] = "gemini-3.1-pro-high"
different_path = write_json(different_selection, "selection")
case("mismatched selection artifact renders nothing", run(selected_receipt, extra=("--selection", str(different_path))).returncode == 1)
advisory = recommendation(); advisory_path = write_json(advisory, "recommendation")
advisory_receipt = copy.deepcopy(base); advisory_receipt["pre_dispatch_recommendation"] = advisory
case("matching recommendation artifact renders", run(advisory_receipt, extra=("--pre-recommendation", str(advisory_path))).returncode == 0)
changed_advisory = copy.deepcopy(advisory); changed_advisory["applied"] = True
changed_path = write_json(changed_advisory, "recommendation")
case("mismatched or applied recommendation renders nothing", run(advisory_receipt, extra=("--pre-recommendation", str(changed_path))).returncode == 1)
case("matching trusted candidate digests render", run(base, extra=("--initial-state-digest", "e"*64, "--final-state-digest", "f"*64)).returncode == 0)
case("mismatched trusted candidate digest renders nothing", run(base, extra=("--final-state-digest", "0"*64)).returncode == 1)

print()
print("pure validation and mutation authority:")
sys.path.insert(0, str(SCRIPTS))
import evidence_receipt
import evidence_report
import recommendation_record

original_run = evidence_receipt.subprocess.run
def forbidden_subprocess(*args, **kwargs):
    raise AssertionError("receipt-only validation invoked a subprocess")
evidence_receipt.subprocess.run = forbidden_subprocess
try:
    schema = evidence_receipt.load_schema(RUNTIME / "schemas/evidence-receipt.schema.json")
    evidence_receipt.validate_receipt(advisory_receipt, schema)
    pure = True
except BaseException:
    pure = False
finally:
    evidence_receipt.subprocess.run = original_run
case("receipt-only recommendation validation invokes no subprocess", pure)

direct = copy.deepcopy(base)
direct["caller_selection"] = {
    "schema_version": 1,
    "kind": "agy-worker-selection",
    "selection_mode": "exact-model",
    "user_model": "gemini-3.1-pro-high",
    "user_model_source": "cli",
    "resolved_agy_model": "gemini-3.1-pro-high",
    "installed_agy_version": "1.1.11",
    "matrix_sha256": "2" * 64,
    "matrix_agy_version": "1.1.11",
    "matrix_source_revision": "3" * 40,
}
import model_selection
original_policy = model_selection.load_policy
model_selection.load_policy = lambda: (_ for _ in ()).throw(AssertionError("policy read"))
try:
    evidence_receipt.validate_receipt(direct, schema)
    pure_selection = True
except BaseException:
    pure_selection = False
finally:
    model_selection.load_policy = original_policy
case("receipt-only direct selection validation reads no routing policy", pure_selection)

direct_mismatch = copy.deepcopy(direct)
direct_mismatch["caller_selection"]["resolved_agy_model"] = "claude-sonnet-4-6"
try:
    evidence_receipt.validate_receipt(direct_mismatch, schema)
    exact_selection_rejected = False
except evidence_receipt.ValidationFailure:
    exact_selection_rejected = True
case("pure selection validator rejects exact-model resolution mismatch", exact_selection_rejected)

selection_source = (SCRIPTS / "model_selection.py").read_text(encoding="utf-8")
selection_mutated = selection_source.replace(
    'elif resolved_model != user_model:', 'elif False:', 1
)
selection_namespace = {
    "__name__": "model_selection_mutation",
    "__file__": str(SCRIPTS / "model_selection.py"),
}
exec(compile(selection_mutated, "<selection-mutation>", "exec"), selection_namespace, selection_namespace)
try:
    selection_namespace["validate_selection_record_shape"](
        direct_mismatch["caller_selection"]
    )
    selection_mutation_exposed = True
except selection_namespace["CallerError"]:
    selection_mutation_exposed = False
case("exact-selection equality weakening mutation is detected", selection_mutation_exposed)

invalid_advisory = copy.deepcopy(advisory); invalid_advisory["applied"] = True
try:
    recommendation_record.validate_recommendation_record(invalid_advisory)
    secure_rejected = False
except recommendation_record.RecommendationRecordError:
    secure_rejected = True
case("pure validator rejects an applied advisory", secure_rejected)

source = (SCRIPTS / "recommendation_record.py").read_text(encoding="utf-8")
needle = 'if value.get("recommendation_only") is not True or value.get("applied") is not False:'
mutated = source.replace(needle, 'if value.get("recommendation_only") is not True:', 1)
namespace = {"__name__": "recommendation_record_mutation", "__file__": str(SCRIPTS / "recommendation_record.py")}
exec(compile(mutated, "<recommendation-mutation>", "exec"), namespace, namespace)
try:
    namespace["validate_recommendation_record"](invalid_advisory)
    mutation_exposed = True
except namespace["RecommendationRecordError"]:
    mutation_exposed = False
case("applied-guard weakening mutation is detected", mutation_exposed)

direct_advisory = copy.deepcopy(advisory)
direct_advisory.pop("selected_tier")
direct_advisory["user_model"] = "claude-sonnet-4-6"
direct_advisory["resolved_agy_model"] = "claude-sonnet-4-6"
direct_advisory["matrix_sha256"] = "2" * 64
direct_advisory["matrix_agy_version"] = "1.1.11"
direct_advisory["matrix_source_revision"] = "3" * 40
direct_advisory["rationale"] = (
    "An explicit model/effort selection is caller-owned and unranked; "
    "this advisory cannot change or redispatch it."
)
recommendation_record.validate_recommendation_record(direct_advisory)
direct_advisory_bad = copy.deepcopy(direct_advisory)
direct_advisory_bad["resolved_agy_model"] = "gpt-oss-120b-medium"
try:
    recommendation_record.validate_recommendation_record(direct_advisory_bad)
    exact_advisory_rejected = False
except recommendation_record.RecommendationRecordError:
    exact_advisory_rejected = True
case("pure recommendation validator rejects exact-model resolution mismatch", exact_advisory_rejected)

equality_mutated = source.replace(
    'if "user_effort" not in value and value["resolved_agy_model"] != value["user_model"]:',
    'if False:',
    1,
)
equality_namespace = {
    "__name__": "recommendation_equality_mutation",
    "__file__": str(SCRIPTS / "recommendation_record.py"),
}
exec(compile(equality_mutated, "<recommendation-equality-mutation>", "exec"), equality_namespace, equality_namespace)
try:
    equality_namespace["validate_recommendation_record"](direct_advisory_bad)
    equality_mutation_exposed = True
except equality_namespace["RecommendationRecordError"]:
    equality_mutation_exposed = False
case("exact-advisory equality weakening mutation is detected", equality_mutation_exposed)

receipt_source = (SCRIPTS / "evidence_receipt.py").read_text(encoding="utf-8")
case(
    "receipt validator source contains no recommendation subprocess call",
    "validate_recommendation_for_publication" in receipt_source
    and "validate_receipt(candidate, schema)" in receipt_source,
)
recommender_source = (SCRIPTS / "model-recommendation.py").read_text(encoding="utf-8")
case(
    "recommender binds its output to the shared v1 validator",
    "validate_recommendation_record(result)" in recommender_source,
)
report_source = (SCRIPTS / "evidence_report.py").read_text(encoding="utf-8")
case(
    "renderer has no command, routing, gate, git, agy, or network execution surface",
    "import subprocess" not in report_source
    and "subprocess." not in report_source
    and "os.system" not in report_source
    and "exec(" not in report_source,
)
case(
    "renderer validation occurs before either output surface",
    report_source.index("receipt = validate_receipt(") < report_source.index("payload = render_text(")
    < report_source.index("publish_new(Path(output_path), payload)"),
)

fault_target = TMP / "fault-report.txt"
real_stat = evidence_report.os.stat
stat_calls = 0
def fail_post_link(path, *args, **kwargs):
    global stat_calls
    if path == fault_target.name and kwargs.get("dir_fd") is not None and stat_calls == 0:
        stat_calls += 1
        raise OSError("injected post-link stat failure")
    return real_stat(path, *args, **kwargs)
evidence_report.os.stat = fail_post_link
try:
    try:
        evidence_report.publish_new(fault_target, b"report\n")
    except OSError:
        pass
finally:
    evidence_report.os.stat = real_stat
case(
    "post-link validation failure removes final and temporary by registered inode",
    stat_calls == 1
    and not fault_target.exists()
    and not any("fault-report.txt.evidence-report" in path.name for path in TMP.iterdir()),
)

signal_receipt = write_json(base, "signal-receipt")
def signal_args(target):
    return ["--receipt", str(signal_receipt), "--format", "text", "--output", str(target)]

before_target = TMP / "signal-before-link.txt"
import signal
real_open = evidence_report.os.open
open_signalled = False
def hup_after_temp_open(path, *args, **kwargs):
    global open_signalled
    descriptor = real_open(path, *args, **kwargs)
    if isinstance(path, str) and ".evidence-report." in path and not open_signalled:
        open_signalled = True
        os.kill(os.getpid(), signal.SIGHUP)
    return descriptor
evidence_report.os.open = hup_after_temp_open
try:
    with contextlib.redirect_stderr(io.StringIO()):
        before_rc = evidence_report.main(signal_args(before_target))
finally:
    evidence_report.os.open = real_open
case(
    "HUP after temp open before return exits 129 with no final or temporary",
    before_rc == 129
    and open_signalled
    and not before_target.exists()
    and not any("signal-before-link.txt.evidence-report" in path.name for path in TMP.iterdir()),
)

completion_target = TMP / "signal-completion.txt"
real_checkpoint = evidence_report._completion_checkpoint
def term_before_disarm():
    os.kill(os.getpid(), signal.SIGTERM)
evidence_report._completion_checkpoint = term_before_disarm
try:
    with contextlib.redirect_stderr(io.StringIO()):
        completion_rc = evidence_report.main(signal_args(completion_target))
finally:
    evidence_report._completion_checkpoint = real_checkpoint
case(
    "TERM after publication before main disarm exits 143 with no durable final",
    completion_rc == 143
    and not completion_target.exists()
    and not any("signal-completion.txt.evidence-report" in path.name for path in TMP.iterdir()),
)

class SimulatedSuccess(BaseException):
    pass

real_atomic_exit = evidence_report._atomic_success_exit
real_exit_checkpoint = evidence_report._before_atomic_exit_checkpoint
for signum, expected_exit, label in (
    (signal.SIGHUP, 129, "HUP"),
    (signal.SIGINT, 130, "INT"),
    (signal.SIGTERM, 143, "TERM"),
):
    transition_target = TMP / f"signal-before-exit-{signum}.txt"
    evidence_report._before_atomic_exit_checkpoint = (
        lambda selected=signum: os.kill(os.getpid(), selected)
    )
    evidence_report._atomic_success_exit = lambda: (_ for _ in ()).throw(SimulatedSuccess())
    try:
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                transition_rc = evidence_report.main(signal_args(transition_target))
        except SimulatedSuccess:
            transition_rc = 0
    finally:
        evidence_report._before_atomic_exit_checkpoint = real_exit_checkpoint
        evidence_report._atomic_success_exit = real_atomic_exit
    case(
        f"{label} after success unmask but before process exit rolls back exact inode",
        transition_rc == expected_exit
        and not transition_target.exists()
        and not any(
            f"signal-before-exit-{signum}.txt.evidence-report" in path.name
            for path in TMP.iterdir()
        ),
    )

simulated_target = TMP / "simulated-atomic-success.txt"
evidence_report._atomic_success_exit = lambda: (_ for _ in ()).throw(SimulatedSuccess())
try:
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            evidence_report.main(signal_args(simulated_target))
    except SimulatedSuccess:
        simulated_exit = True
    else:
        simulated_exit = False
finally:
    evidence_report._atomic_success_exit = real_atomic_exit
case(
    "published success reaches only the non-returning atomic exit boundary",
    simulated_exit and simulated_target.is_file(),
)
simulated_target.unlink()

weak_transition_target = TMP / "signal-before-exit-weak.txt"
real_complete_success = evidence_report._complete_published_success
def weakened_complete_success(publication):
    for number in evidence_report.SIGNALS:
        signal.signal(number, signal.SIG_IGN)
    signal.pthread_sigmask(signal.SIG_SETMASK, publication.prior_mask)
    os.kill(os.getpid(), signal.SIGTERM)
    raise SimulatedSuccess()
evidence_report._complete_published_success = weakened_complete_success
try:
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            evidence_report.main(signal_args(weak_transition_target))
    except SimulatedSuccess:
        weak_transition_exit = True
    else:
        weak_transition_exit = False
finally:
    evidence_report._complete_published_success = real_complete_success
case(
    "pre-exit SIG_IGN weakening mutation exposes a durable lost-signal final",
    weak_transition_exit and weak_transition_target.is_file(),
)
weak_transition_target.unlink()

success_source = report_source[
    report_source.index("def _complete_published_success("):
    report_source.index("\ndef publish_new(")
]
case(
    "success transition retains handlers and ownership through exact os._exit",
    "SIG_IGN" not in success_source
    and success_source.index("SIG_SETMASK")
        < success_source.index("_before_atomic_exit_checkpoint()")
        < success_source.index("_atomic_success_exit()")
    and "os._exit(0)" in report_source,
)

after_target = TMP / "signal-after-link.txt"
real_stat = evidence_report.os.stat
after_sent = False
def int_after_link(path, *args, **kwargs):
    global after_sent
    if path == after_target.name and kwargs.get("dir_fd") is not None and not after_sent:
        after_sent = True
        os.kill(os.getpid(), signal.SIGINT)
    return real_stat(path, *args, **kwargs)
evidence_report.os.stat = int_after_link
try:
    with contextlib.redirect_stderr(io.StringIO()):
        after_rc = evidence_report.main(signal_args(after_target))
finally:
    evidence_report.os.stat = real_stat
case(
    "INT after link exits 130 and rolls back final and temporary",
    after_rc == 130
    and after_sent
    and not after_target.exists()
    and not any("signal-after-link.txt.evidence-report" in path.name for path in TMP.iterdir()),
)

double_target = TMP / "signal-double.txt"
real_owned_unlink = evidence_report._owned_unlink
double_first_sent = False
double_second_sent = False
def hup_after_link(path, *args, **kwargs):
    global double_first_sent
    if path == double_target.name and kwargs.get("dir_fd") is not None and not double_first_sent:
        double_first_sent = True
        os.kill(os.getpid(), signal.SIGHUP)
    return real_stat(path, *args, **kwargs)
def term_during_cleanup(*args, **kwargs):
    global double_second_sent
    if not double_second_sent:
        double_second_sent = True
        os.kill(os.getpid(), signal.SIGTERM)
    return real_owned_unlink(*args, **kwargs)
evidence_report.os.stat = hup_after_link
evidence_report._owned_unlink = term_during_cleanup
try:
    with contextlib.redirect_stderr(io.StringIO()):
        double_rc = evidence_report.main(signal_args(double_target))
finally:
    evidence_report.os.stat = real_stat
    evidence_report._owned_unlink = real_owned_unlink
case(
    "distinct second signal cannot interrupt rollback or replace first exit",
    double_rc == 129
    and double_first_sent and double_second_sent
    and not double_target.exists()
    and not any("signal-double.txt.evidence-report" in path.name for path in TMP.iterdir()),
)

attacker_target = TMP / "signal-attacker.txt"
attacker_first_sent = False
attacker_replaced = False
def attacker_hup_after_link(path, *args, **kwargs):
    global attacker_first_sent
    if path == attacker_target.name and kwargs.get("dir_fd") is not None and not attacker_first_sent:
        attacker_first_sent = True
        os.kill(os.getpid(), signal.SIGHUP)
    return real_stat(path, *args, **kwargs)
def replace_before_owned_unlink(parent_fd, name, identity):
    global attacker_replaced
    if name == attacker_target.name and not attacker_replaced:
        attacker_replaced = True
        os.unlink(name, dir_fd=parent_fd)
        descriptor = os.open(
            name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=parent_fd
        )
        os.write(descriptor, b"attacker\n")
        os.close(descriptor)
    return real_owned_unlink(parent_fd, name, identity)
evidence_report.os.stat = attacker_hup_after_link
evidence_report._owned_unlink = replace_before_owned_unlink
try:
    with contextlib.redirect_stderr(io.StringIO()):
        attacker_rc = evidence_report.main(signal_args(attacker_target))
finally:
    evidence_report.os.stat = real_stat
    evidence_report._owned_unlink = real_owned_unlink
case(
    "signal rollback preserves a raced attacker replacement by pinned inode",
    attacker_rc == 129
    and attacker_first_sent and attacker_replaced
    and attacker_target.read_bytes() == b"attacker\n",
)
attacker_target.unlink()

weak_target = TMP / "signal-weak.txt"
real_mask = evidence_report.signal.pthread_sigmask
weak_first_sent = False
weak_second_sent = False
def weak_hup_after_link(path, *args, **kwargs):
    global weak_first_sent
    if path == weak_target.name and kwargs.get("dir_fd") is not None and not weak_first_sent:
        weak_first_sent = True
        os.kill(os.getpid(), signal.SIGHUP)
    return real_stat(path, *args, **kwargs)
def weak_term_during_cleanup(*args, **kwargs):
    global weak_second_sent
    if not weak_second_sent:
        weak_second_sent = True
        os.kill(os.getpid(), signal.SIGTERM)
    return real_owned_unlink(*args, **kwargs)
evidence_report.os.stat = weak_hup_after_link
evidence_report._owned_unlink = weak_term_during_cleanup
evidence_report.signal.pthread_sigmask = lambda how, mask: set()
try:
    with contextlib.redirect_stderr(io.StringIO()):
        weak_rc = evidence_report.main(signal_args(weak_target))
finally:
    evidence_report.os.stat = real_stat
    evidence_report._owned_unlink = real_owned_unlink
    evidence_report.signal.pthread_sigmask = real_mask
weak_artifact = weak_target.exists() or any(
    "signal-weak.txt.evidence-report" in path.name for path in TMP.iterdir()
)
for path in list(TMP.iterdir()):
    if path == weak_target or "signal-weak.txt.evidence-report" in path.name:
        path.unlink()
case(
    "cleanup-mask weakening mutation is detected before test-owned cleanup",
    weak_first_sent and weak_second_sent and (weak_rc != 129 or weak_artifact),
)

usage = subprocess.run([str(REPORT), "--format", "text"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
case("missing receipt is usage failure with no report", usage.returncode == 64 and usage.stdout == b"")
duplicate_option = subprocess.run([str(REPORT), "--receipt", str(write_json(base)), "--format", "text", "--format", "markdown"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
case("singleton options reject repetition", duplicate_option.returncode == 64 and duplicate_option.stdout == b"")

print()
if failed:
    print(f"FAILED: {failed} failed, {passed} passed")
    raise SystemExit(1)
print(f"PASSED: {passed} tests")
PY
