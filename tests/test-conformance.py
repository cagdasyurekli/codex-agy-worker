#!/usr/bin/env python3
"""Offline adversarial tests for the public qa-gate v1 conformance kit."""

from __future__ import annotations

import hashlib
import importlib.util
import contextlib
import io
import os
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from typing import Callable, Iterable, Tuple


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "conformance" / "run.sh"
GATE = ROOT / "qa-gate.sh"
PYTHON_RUNNER = ROOT / "conformance" / "v1" / "run.py"
EXPECTED = b"CONFORMANCE_RESULT version=v1 fixtures=11 status=passed\n"
TMP = Path(tempfile.mkdtemp(prefix="agyworker-conformance-tests.")).resolve()
TMP.chmod(0o700)
passed = 0
failed = 0


def load_runner():
    specification = importlib.util.spec_from_file_location(
        "agy_worker_conformance_runner_tested", PYTHON_RUNNER
    )
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


MODULE = load_runner()


def check(name: str, action: Callable[[], bool]) -> None:
    global passed, failed
    try:
        accepted = bool(action())
    except BaseException as exc:
        accepted = False
        detail = f" ({type(exc).__name__}: {exc})"
    else:
        detail = ""
    if accepted:
        passed += 1
    else:
        failed += 1
        print(f"FAIL conformance: {name}{detail}")


def run(
    gate: Path = GATE,
    *,
    runner: Path = RUNNER,
    extra: Iterable[str] = (),
    timeout: float = 25.0,
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [str(runner), "--gate", str(gate), *extra],
        cwd=str(ROOT),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )


def copy_kit(label: str) -> Path:
    target = TMP / label
    shutil.copytree(ROOT / "conformance", target)
    return target


def write_gate(label: str, body: str) -> Path:
    path = TMP / f"{label}.sh"
    path.write_text("#!/usr/bin/env bash\nset -u\n" + body, encoding="utf-8")
    path.chmod(0o700)
    return path


def roots() -> Tuple[str, ...]:
    seen = set()
    values = []
    for raw in ("/private/tmp", "/tmp"):
        try:
            base = Path(raw).resolve(strict=True)
        except OSError:
            continue
        if base in seen:
            continue
        seen.add(base)
        values.extend(str(path) for path in base.glob("agy-worker-conformance.*"))
    return tuple(sorted(values))


def mutant_gate(label: str, condition: str) -> Path:
    gate = str(GATE)
    body = f"""
envelope=''; repo=''; base=''; verifier=''
args=("$@")
while [[ $# -gt 0 ]]; do
  case "$1" in
    --envelope) envelope="$2"; shift 2 ;;
    --repo) repo="$2"; shift 2 ;;
    --base) base="$2"; shift 2 ;;
    --verify) verifier="$2"; shift 2 ;;
    --allow|--only) shift 2 ;;
    --expect-edits) shift ;;
    *) shift ;;
  esac
done
if {condition}; then exit 0; fi
exec {gate!r} "${{args[@]}}"
"""
    return write_gate(label, body)


before_roots = roots()

result = run()
check(
    "maintained gate passes every exact v1 fixture",
    lambda: result.returncode == 0 and result.stdout == EXPECTED and result.stderr == b"",
)
check("success output is one bounded canonical line", lambda: len(result.stdout) < 128 and result.stdout.count(b"\n") == 1)
check("success output discloses no private path", lambda: b"/private/" not in result.stdout and b"/tmp/" not in result.stdout)

bad_args = subprocess.run([str(RUNNER)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
check("missing gate argument is usage exit 64", lambda: bad_args.returncode == 64 and not bad_args.stdout and not bad_args.stderr)
extra_args = run(extra=("unexpected",))
check("extra argument is usage exit 64", lambda: extra_args.returncode == 64 and not extra_args.stdout and not extra_args.stderr)
missing_gate = run(TMP / "missing-gate")
check("missing gate fails before fixtures", lambda: missing_gate.returncode == 2 and missing_gate.stderr == b"conformance: failed closed\n")
directory_gate = TMP / "gate-directory"
directory_gate.mkdir()
directory_result = run(directory_gate)
check("directory gate is rejected", lambda: directory_result.returncode == 2)
nonexec_gate = write_gate("nonexec", "exit 0\n")
nonexec_gate.chmod(0o600)
nonexec_result = run(nonexec_gate)
check("non-executable gate is rejected", lambda: nonexec_result.returncode == 2)
invalid_format_gate = TMP / "invalid-format-gate"
invalid_format_gate.write_bytes(b"this executable has no shebang\n")
invalid_format_gate.chmod(0o700)
invalid_format_result = run(invalid_format_gate)
check(
    "invalid executable format fails closed without traceback or path",
    lambda: (
        invalid_format_result.returncode == 2
        and invalid_format_result.stdout == b""
        and invalid_format_result.stderr == b"conformance: failed closed\n"
        and str(TMP).encode() not in invalid_format_result.stderr
        and b"Traceback" not in invalid_format_result.stderr
    ),
)

manifest_mutations = (
    ("manifest-byte", lambda path: path.write_bytes(path.read_bytes() + b" ")),
    ("manifest-malformed", lambda path: path.write_bytes(b"{not json\n")),
    ("manifest-missing", lambda path: path.unlink()),
)
for label, mutate in manifest_mutations:
    kit = copy_kit(label)
    mutate(kit / "v1" / "manifest.json")
    outcome = run(runner=kit / "run.sh")
    check(f"{label} fails closed", lambda outcome=outcome: outcome.returncode == 2 and outcome.stdout == b"")

kit = copy_kit("source-byte")
(kit / "v1" / "files" / "verified.txt").write_bytes(b"changed fixture\n")
source_result = run(runner=kit / "run.sh")
check("fixture content digest drift fails closed", lambda: source_result.returncode == 2)

kit = copy_kit("source-missing")
(kit / "v1" / "envelopes" / "honest.json").unlink()
missing_result = run(runner=kit / "run.sh")
check("missing envelope fails closed", lambda: missing_result.returncode == 2)

kit = copy_kit("source-symlink")
source_path = kit / "v1" / "files" / "verified.txt"
source_path.unlink()
source_path.symlink_to(ROOT / "conformance" / "v1" / "files" / "verified.txt")
symlink_result = run(runner=kit / "run.sh")
check("symlink fixture source fails closed", lambda: symlink_result.returncode == 2)

always_accept = write_gate("always-accept", "exit 0\n")
always_result = run(always_accept)
check("always-accept gate fails the required rejection fixtures", lambda: always_result.returncode == 1)
check("gate mismatch reports only bounded fixture and exits", lambda: always_result.stderr.startswith(b"conformance: fixture ") and b"/" not in always_result.stderr and len(always_result.stderr) < 128)

descendant_log = TMP / "normal-descendants.log"
late_marker = TMP / "normal-descendants.late"
descendant_ready_dir = TMP / "normal-descendants.ready"
descendant_ready_dir.mkdir(mode=0o700)
descendant_gate = write_gate(
    "normal-descendants",
    f"""
child_ready_case=$(/usr/bin/mktemp -d {str(descendant_ready_dir)!r}/case.XXXXXXXX) || exit 97
child_ready_path="$child_ready_case/ready"
/bin/bash -c 'trap "" HUP INT TERM; echo $$ >> "$1"; while :; do if [[ ! -e "$2" ]]; then printf "%s\\n" "$$" > "$2"; fi; /bin/sleep 1; echo late >> "$3"; done' child {str(descendant_log)!r} "$child_ready_path" {str(late_marker)!r} >/dev/null 2>&1 &
child_pid=$!
child_ready=0
for ((child_ready_attempt = 0; child_ready_attempt < 200; child_ready_attempt++)); do
  if [[ -f "$child_ready_path" ]] && /usr/bin/grep -Fxq "$child_pid" "$child_ready_path"; then
    child_ready=1
    break
  fi
  /bin/sleep 0.005
done
[[ "$child_ready" == 1 ]] || exit 97
exec {str(GATE)!r} "$@"
""",
)
descendant_gate_source = descendant_gate.read_bytes()


def descendant_barrier_contract(data: bytes) -> bool:
    required = (
        b"child_ready_case=$(/usr/bin/mktemp -d ",
        b'child_ready_path="$child_ready_case/ready"',
        b'trap "" HUP INT TERM; echo $$ >> "$1"; while :; do',
        b'printf "%s\\n" "$$" > "$2"',
        b"child_pid=$!",
        b"child_ready=0",
        b"child_ready_attempt < 200",
        b'[[ -f "$child_ready_path" ]]',
        b'/usr/bin/grep -Fxq "$child_pid" "$child_ready_path"',
        b"/bin/sleep 0.005",
        b'[[ "$child_ready" == 1 ]] || exit 97',
    )
    if not all(data.count(item) == 1 for item in required):
        return False
    ordered = (
        b'trap "" HUP INT TERM',
        b'echo $$ >> "$1"',
        b"while :; do",
        b'printf "%s\\n" "$$" > "$2"',
        b"/bin/sleep 1",
        b'echo late >> "$3"',
    )
    return tuple(data.index(item) for item in ordered) == tuple(
        sorted(data.index(item) for item in ordered)
    )


descendant_barrier_removal = descendant_gate_source.replace(
    b'printf "%s\\n" "$$" > "$2"', b":", 1
)
descendant_barrier_reorder = descendant_gate_source.replace(
    b'echo $$ >> "$1"; while :; do if [[ ! -e "$2" ]]; then printf "%s\\n" "$$" > "$2"; fi;',
    b'echo $$ >> "$1"; printf "%s\\n" "$$" > "$2"; while :; do if [[ ! -e "$2" ]]; then :; fi;',
    1,
)
check(
    "closed-stdio descendant proof requires a bounded child-ready barrier",
    lambda: (
        descendant_barrier_contract(descendant_gate_source)
        and descendant_barrier_removal != descendant_gate_source
        and not descendant_barrier_contract(descendant_barrier_removal)
        and descendant_barrier_reorder != descendant_gate_source
        and not descendant_barrier_contract(descendant_barrier_reorder)
    ),
)
descendant_result = run(descendant_gate)
descendant_pids = [int(item) for item in descendant_log.read_text().splitlines()] if descendant_log.exists() else []
try:
    descendant_ready_pids = sorted(
        int(path.read_text().strip())
        for path in descendant_ready_dir.glob("case.*/ready")
    )
except (OSError, ValueError):
    descendant_ready_pids = []
descendants_ready = (
    len(descendant_ready_pids) == 11
    and descendant_ready_pids == sorted(descendant_pids)
)
descendant_deadline = time.monotonic() + 1.5
while True:
    live_descendants = []
    for descendant_pid in descendant_pids:
        try:
            os.kill(descendant_pid, 0)
        except ProcessLookupError:
            continue
        live_descendants.append(descendant_pid)
    if not live_descendants or time.monotonic() >= descendant_deadline:
        break
    time.sleep(0.01)
descendants_gone = len(descendant_pids) == 11 and not live_descendants
for descendant_pid in live_descendants:
    try:
        os.kill(descendant_pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
check(
    "normal and nonzero fixture exits close leader-exited closed-stdio descendants",
    lambda: (
        descendant_result.returncode == 0
        and descendant_result.stdout == EXPECTED
        and descendant_result.stderr == b""
        and descendants_ready
        and descendants_gone
        and not late_marker.exists()
    ),
)

close_then_exec_gate = write_gate(
    "close-then-exec",
    f"""
exec >/dev/null 2>&1
/bin/sleep 0.25
exec {str(GATE)!r} "$@"
""",
)
close_then_exec_result = run(close_then_exec_gate)
check(
    "pipe EOF waits for unreaped leader before preserving exact gate exits",
    lambda: close_then_exec_result.returncode == 0 and close_then_exec_result.stdout == EXPECTED,
)

timeout_gate = write_gate(
    "eof-timeout",
    "exec 1>&- 2>&-\n/bin/sleep 5\nexit 0\n",
)
timeout_env = {
    "HOME": str(TMP),
    "LANG": "C",
    "LC_ALL": "C",
    "PATH": "/usr/bin:/bin",
}
try:
    MODULE._run_bounded(
        [str(timeout_gate)],
        cwd=TMP,
        env=timeout_env,
        timeout=0.15,
        stdout_limit=128,
        stderr_limit=128,
    )
except MODULE.ConformanceError:
    eof_timeout_rejected = True
else:
    eof_timeout_rejected = False
check("EOF with a still-running leader reaches the hard timeout", lambda: eof_timeout_rejected)

saved_waitid = MODULE._WAITID
MODULE._WAITID = None
try:
    try:
        MODULE._leader_exited_unreaped(os.getpid())
    except MODULE.ConformanceError:
        waitid_unavailable_rejected = True
    else:
        waitid_unavailable_rejected = False
finally:
    MODULE._WAITID = saved_waitid
check("unavailable non-reaping observation fails closed", lambda: waitid_unavailable_rejected)

MODULE._WAITID = lambda *_arguments: -1
try:
    try:
        MODULE._leader_exited_unreaped(os.getpid())
    except MODULE.ConformanceError:
        waitid_failure_rejected = True
    else:
        waitid_failure_rejected = False
finally:
    MODULE._WAITID = saved_waitid
check("fatal non-reaping observation fails closed", lambda: waitid_failure_rejected)

mutants = (
    ("ignored-files", '[[ -n "$repo" && -e "$repo/ignored.tmp" ]]'),
    ("mutable-base", '[[ "$base" == "HEAD" ]]'),
    ("skip-verification", '[[ "$verifier" == "/usr/bin/false" ]]'),
    ("verifier-mutation", '[[ "$verifier" == *"verifier mutation"* ]]'),
    ("human-required", '[[ -n "$envelope" ]] && grep -Fq \"\\\"requires_human\\\":true\" "$envelope"'),
    ("worker-claim", '[[ -n "$envelope" ]] && grep -Fq \"worker-claim-must-not-run\" "$envelope"'),
)
for label, condition in mutants:
    outcome = run(mutant_gate(label, condition))
    check(f"permissive {label} implementation is rejected", lambda outcome=outcome: outcome.returncode == 1)

overflow_gate = write_gate("overflow", "/usr/bin/python3 -c 'print(\"x\"*9000)'\nexit 0\n")
overflow_result = run(overflow_gate)
check("gate stdout overflow fails closed", lambda: overflow_result.returncode == 2 and overflow_result.stdout == b"")


def workspace_roots():
    roots = set()
    seen = set()
    for candidate in (Path("/private/tmp"), Path("/tmp")):
        try:
            parent = candidate.resolve(strict=True)
        except OSError:
            continue
        if parent in seen:
            continue
        seen.add(parent)
        roots.update(parent.glob("agy-worker-conformance.*"))
    return roots


def sanitized_cleanup_failure(result: subprocess.CompletedProcess[bytes]) -> bool:
    return (
        result.returncode == 2
        and result.stdout == b""
        and result.stderr == b"conformance: failed closed\n"
        and b"Traceback" not in result.stderr
        and b"/private/" not in result.stderr
    )


rename_before = workspace_roots()
rename_result = run(write_gate("rename-cwd", '/bin/mv "$PWD" "$PWD.moved"\nexit 0\n'))
rename_after = workspace_roots()
rename_residuals = rename_after - rename_before
rename_retained = len(rename_residuals) == 1
for residual in rename_residuals:
    shutil.rmtree(residual, ignore_errors=True)
check(
    "same-parent workspace rename fails closed and retains the unchased residual",
    lambda: sanitized_cleanup_failure(rename_result) and rename_retained,
)

remove_before = workspace_roots()
remove_result = run(write_gate("remove-cwd", '/bin/rm -rf "$PWD"\nexit 0\n'))
remove_after = workspace_roots()
check(
    "workspace removal fails closed without traceback or residual path output",
    lambda: sanitized_cleanup_failure(remove_result) and remove_after == remove_before,
)

replacement_before = workspace_roots()
replacement_result = run(
    write_gate(
        "replace-cwd",
        '/bin/mv "$PWD" "$PWD.moved"\n/bin/mkdir "$PWD"\nprintf attacker > "$PWD/attacker-sentinel"\nexit 0\n',
    )
)
replacement_after = workspace_roots()
replacement_new = replacement_after - replacement_before
replacement_preserved = (
    len(replacement_new) == 2
    and sum(
        path.joinpath("attacker-sentinel").is_file()
        and path.joinpath("attacker-sentinel").read_bytes() == b"attacker"
        for path in replacement_new
    )
    == 1
)
for residual in replacement_new:
    shutil.rmtree(residual, ignore_errors=True)
check(
    "rename drift preserves both the residual and attacker replacement",
    lambda: sanitized_cleanup_failure(replacement_result) and replacement_preserved,
)

external_root = TMP / "externally-moved-workspace"
external_result = run(
    write_gate("external-cwd", f'/bin/mv "$PWD" {str(external_root)!r}\nexit 0\n')
)
external_preserved = external_root.is_dir()
shutil.rmtree(external_root, ignore_errors=True)
check(
    "workspace moved outside its bound parent is not chased or disclosed",
    lambda: sanitized_cleanup_failure(external_result) and external_preserved,
)

symlink_target = TMP / "foreign-symlink-target"
symlink_target.mkdir(mode=0o700)
(symlink_target / "sentinel").write_bytes(b"foreign")
symlink_before = workspace_roots()
symlink_result = run(
    write_gate(
        "symlink-cwd",
        f'/bin/mv "$PWD" "$PWD.moved"\n/bin/ln -s {str(symlink_target)!r} "$PWD"\nexit 0\n',
    )
)
symlink_after = workspace_roots()
symlink_new = symlink_after - symlink_before
symlink_preserved = (
    len(symlink_new) == 2
    and (symlink_target / "sentinel").read_bytes() == b"foreign"
)
for residual in symlink_new:
    if residual.is_symlink():
        residual.unlink()
    else:
        shutil.rmtree(residual, ignore_errors=True)
check(
    "symlink replacement and its foreign target survive fail-closed cleanup",
    lambda: sanitized_cleanup_failure(symlink_result) and symlink_preserved,
)


swap_foreign = TMP / "swap-foreign"
swap_foreign.mkdir(mode=0o700)
(swap_foreign / "sentinel").write_bytes(b"foreign")
swap_root = MODULE._private_workspace()
(swap_root / "nested").mkdir(mode=0o700)
(swap_root / "nested" / "owned").write_bytes(b"owned")
os.symlink(swap_foreign, swap_root / "foreign-link", target_is_directory=True)
swap_saved = swap_root.with_name(swap_root.name + ".saved")
swap_cloexec = (
    not os.get_inheritable(MODULE.ACTIVE_WORKSPACE_FD)
    and not os.get_inheritable(MODULE.ACTIVE_WORKSPACE_PARENT_FD)
)


def swap_after_precheck():
    os.rename(swap_root, swap_saved)
    swap_root.mkdir(mode=0o700)
    (swap_root / "replacement-sentinel").write_bytes(b"replacement")


swap_cleanup = MODULE._cleanup_active_workspace(swap_after_precheck)
swap_fd_bound = (
    not swap_cleanup
    and (swap_root / "replacement-sentinel").read_bytes() == b"replacement"
    and (swap_foreign / "sentinel").read_bytes() == b"foreign"
    and swap_saved.is_dir()
    and not any(swap_saved.iterdir())
)
for descriptor_name in ("ACTIVE_WORKSPACE_FD", "ACTIVE_WORKSPACE_PARENT_FD"):
    descriptor = getattr(MODULE, descriptor_name)
    if descriptor is not None:
        os.close(descriptor)
        setattr(MODULE, descriptor_name, None)
MODULE.ACTIVE_WORKSPACE = None
MODULE.ACTIVE_WORKSPACE_IDENTITY = None
MODULE.ACTIVE_WORKSPACE_PARENT_IDENTITY = None
shutil.rmtree(swap_root, ignore_errors=True)
shutil.rmtree(swap_saved, ignore_errors=True)
check(
    "swap after precheck deletes only through the held root fd",
    lambda: swap_fd_bound,
)
check("held parent and root descriptors are close-on-exec", lambda: swap_cloexec)


def bounded_clear(label, prepare, *, entries=4096, depth=32, byte_limit=16 * 1024 * 1024, expired=False):
    directory = TMP / ("bounded-" + label)
    directory.mkdir(mode=0o700)
    prepare(directory)
    descriptor = os.open(directory, MODULE.OPEN_DIRECTORY_FLAGS)
    old_values = (
        MODULE.CLEANUP_MAX_ENTRIES,
        MODULE.CLEANUP_MAX_DEPTH,
        MODULE.CLEANUP_MAX_BYTES,
    )
    try:
        MODULE.CLEANUP_MAX_ENTRIES = entries
        MODULE.CLEANUP_MAX_DEPTH = depth
        MODULE.CLEANUP_MAX_BYTES = byte_limit
        budget = {
            "entries": 0.0,
            "bytes": 0.0,
            "deadline": time.monotonic() + (-1.0 if expired else 2.0),
        }
        accepted = MODULE._clear_directory_fd(
            descriptor, os.fstat(descriptor).st_dev, budget
        )
    finally:
        (
            MODULE.CLEANUP_MAX_ENTRIES,
            MODULE.CLEANUP_MAX_DEPTH,
            MODULE.CLEANUP_MAX_BYTES,
        ) = old_values
        os.close(descriptor)
        shutil.rmtree(directory, ignore_errors=True)
    return accepted


check(
    "fd cleanup rejects entry-count overflow",
    lambda: not bounded_clear(
        "entries",
        lambda directory: (
            (directory / "one").write_bytes(b"1"),
            (directory / "two").write_bytes(b"2"),
        ),
        entries=1,
    ),
)
check(
    "fd cleanup rejects byte-count overflow",
    lambda: not bounded_clear(
        "bytes", lambda directory: (directory / "two-bytes").write_bytes(b"12"), byte_limit=1
    ),
)
check(
    "fd cleanup rejects depth overflow",
    lambda: not bounded_clear(
        "depth", lambda directory: (directory / "child").mkdir(), depth=0
    ),
)
check(
    "fd cleanup rejects an expired deadline",
    lambda: not bounded_clear("deadline", lambda _directory: None, expired=True),
)


drift_parent = TMP / "drift-parent"
drift_parent.mkdir(mode=0o700)
drift_root = drift_parent / "root"
drift_root.mkdir(mode=0o700)
drift_parent_fd = os.open(drift_parent, MODULE.OPEN_DIRECTORY_FLAGS)
drift_root_fd = os.open(drift_root, MODULE.OPEN_DIRECTORY_FLAGS)
MODULE.ACTIVE_WORKSPACE = drift_root
MODULE.ACTIVE_WORKSPACE_IDENTITY = MODULE._stat_identity(os.fstat(drift_root_fd))
MODULE.ACTIVE_WORKSPACE_PARENT_IDENTITY = MODULE._stat_identity(
    os.fstat(drift_parent_fd)
)
MODULE.ACTIVE_WORKSPACE_FD = drift_root_fd
MODULE.ACTIVE_WORKSPACE_PARENT_FD = drift_parent_fd
drift_saved = TMP / "drift-parent-moved"
os.rename(drift_parent, drift_saved)
parent_drift_result = MODULE._cleanup_active_workspace()
parent_drift_preserved = not parent_drift_result and (drift_saved / "root").is_dir()
os.close(drift_root_fd)
os.close(drift_parent_fd)
MODULE.ACTIVE_WORKSPACE = None
MODULE.ACTIVE_WORKSPACE_IDENTITY = None
MODULE.ACTIVE_WORKSPACE_PARENT_IDENTITY = None
MODULE.ACTIVE_WORKSPACE_FD = None
MODULE.ACTIVE_WORKSPACE_PARENT_FD = None
shutil.rmtree(drift_saved, ignore_errors=True)
check(
    "parent identity drift is never scanned or chased",
    lambda: parent_drift_preserved,
)


def group_cleanup_contract(data: bytes) -> bool:
    try:
        body = data.split(b"def _close_process_group(", 1)[1].split(
            b"def _run_bounded(", 1
        )[0]
        wait_at = body.index(b"process.wait(timeout=0.75)")
    except (IndexError, ValueError):
        return False
    before_wait = body[:wait_at]
    after_wait = body[wait_at:]
    return (
        b"os.killpg(pgid, signum)" in before_wait
        and b"os.killpg(pgid, signal.SIGKILL)" in before_wait
        and b"_group_exists(pgid)" in before_wait
        and b"os.killpg(" not in after_wait
        and b"_group_exists(" not in after_wait
    )


def source_contract(data: bytes) -> bool:
    required = (
        b'MANIFEST_SHA256 = "9741584060f5391e5a79df1022c9cd574c28fdddefc75006b8b6e7ff0e5e36a0"',
        b"if _sha256(raw) != MANIFEST_SHA256:",
        b'"fixture_count": 11,',
        b'"gate_timeout_seconds": 10,',
        b'"gate_stdout_bytes": 8192,',
        b'"gate_stderr_bytes": 8192,',
        b"CLEANUP_MAX_ENTRIES = 4096",
        b"CLEANUP_MAX_DEPTH = 32",
        b"CLEANUP_MAX_BYTES = 16 * 1024 * 1024",
        b"CLEANUP_TIMEOUT_SECONDS = 2.0",
        b'OPEN_DIRECTORY_FLAGS = (\n    os.O_RDONLY\n    | getattr(os, "O_DIRECTORY", 0)\n    | getattr(os, "O_NOFOLLOW", 0)\n    | getattr(os, "O_CLOEXEC", 0)\n)',
        b"with os.scandir(descriptor) as entries:",
        b"os.unlink(name, dir_fd=descriptor)",
        b"os.set_inheritable(root_descriptor, False)",
        b"os.rmdir(workspace.name, dir_fd=ACTIVE_WORKSPACE_PARENT_FD)",
        b"start_new_session=True,",
        b"stdin=subprocess.DEVNULL,",
        b"returncode = _close_process_group(process)",
        b"if _leader_exited_unreaped(process.pid):",
        b"except OSError as exc:\n            raise ConformanceError(\"child process could not start\") from exc",
        b"if returncode != fixture[\"expected_exit\"]:",
        b"signal.signal(item, _signal_handler)",
        b"if FIRST_SIGNAL is None:\n        FIRST_SIGNAL = signum",
        b'except BaseException:\n            print("conformance: failed closed", file=sys.stderr)\n            return 2',
        b"_require_directory_identity(workspace, workspace_identity)",
        b"if not _cleanup_active_workspace():",
        b"if ACTIVE_WORKSPACE is not None and not _cleanup_active_workspace():",
        b'except BaseException:\n        sys.stderr.write("conformance: failed closed\\n")\n        return 2',
        b"except BaseException as exc:\n        cleanup_mask = None\n        if hasattr(signal, \"pthread_sigmask\"):",
    )
    return (
        all(data.count(item) == 1 for item in required)
        and data.count(b"subprocess.Popen(") == 1
        and b"shell=True" not in data
        and b"shutil.rmtree(" not in data
        and b"os.scandir(parent)" not in data
        and group_cleanup_contract(data)
    )


source = PYTHON_RUNNER.read_bytes()
check("source contract binds manifest, bounds, exact exits, signals, and cleanup", lambda: source_contract(source))
source_mutations = (
    (b"if _sha256(raw) != MANIFEST_SHA256:", b"if False:"),
    (b"start_new_session=True,", b"start_new_session=False,"),
    (b"stdin=subprocess.DEVNULL,", b"stdin=None,"),
    (b"CLEANUP_MAX_ENTRIES = 4096", b"CLEANUP_MAX_ENTRIES = 999999"),
    (b"CLEANUP_MAX_DEPTH = 32", b"CLEANUP_MAX_DEPTH = 999999"),
    (
        b"CLEANUP_MAX_BYTES = 16 * 1024 * 1024",
        b"CLEANUP_MAX_BYTES = 999999999999",
    ),
    (b"CLEANUP_TIMEOUT_SECONDS = 2.0", b"CLEANUP_TIMEOUT_SECONDS = 999999.0"),
    (b'getattr(os, "O_NOFOLLOW", 0)', b"0"),
    (b'getattr(os, "O_CLOEXEC", 0)', b"0"),
    (
        b"os.set_inheritable(root_descriptor, False)",
        b"os.set_inheritable(root_descriptor, True)",
    ),
    (b"with os.scandir(descriptor) as entries:", b"with os.scandir('.') as entries:"),
    (b"os.unlink(name, dir_fd=descriptor)", b"os.unlink(name)"),
    (
        b"os.rmdir(workspace.name, dir_fd=ACTIVE_WORKSPACE_PARENT_FD)",
        b"shutil.rmtree(workspace)",
    ),
    (b"returncode = _close_process_group(process)", b"returncode = process.wait()"),
    (
        b"if _leader_exited_unreaped(process.pid):",
        b"if not selector.get_map():",
    ),
    (
        b"if _leader_exited_unreaped(process.pid):",
        b"if process.poll() is not None:",
    ),
    (
        b"if _leader_exited_unreaped(process.pid):",
        b"if process.wait() is not None:",
    ),
    (
        b"except OSError as exc:\n            raise ConformanceError(\"child process could not start\") from exc",
        b"except OSError:\n            raise",
    ),
    (b"if returncode != fixture[\"expected_exit\"]:", b"if False:"),
    (b"signal.signal(item, _signal_handler)", b"signal.signal(item, signal.SIG_DFL)"),
    (b"if FIRST_SIGNAL is None:\n        FIRST_SIGNAL = signum", b"FIRST_SIGNAL = signum"),
    (
        b'except BaseException:\n            print("conformance: failed closed", file=sys.stderr)\n            return 2',
        b"except BaseException:\n            raise",
    ),
    (
        b"_require_directory_identity(workspace, workspace_identity)",
        b"pass  # workspace identity not checked",
    ),
    (b"if not _cleanup_active_workspace():", b"if False:"),
    (
        b"if ACTIVE_WORKSPACE is not None and not _cleanup_active_workspace():",
        b"if False:",
    ),
    (
        b'except BaseException:\n        sys.stderr.write("conformance: failed closed\\n")\n        return 2',
        b"except BaseException:\n        raise",
    ),
    (
        b"assert process.returncode is not None\n    return process.returncode",
        b"_group_exists(pgid)\n    assert process.returncode is not None\n    return process.returncode",
    ),
    (
        b"except BaseException as exc:\n        cleanup_mask = None\n        if hasattr(signal, \"pthread_sigmask\"):",
        b"except BaseException as exc:\n        cleanup_mask = None\n        if False:",
    ),
)
for old, new in source_mutations:
    mutated = source.replace(old, new, 1)
    check(f"source mutation {old[:24]!r} is killed", lambda mutated=mutated: not source_contract(mutated))


class TraceProcess:
    pid = 424242
    returncode = None

    def wait(self, timeout: float) -> int:
        del timeout
        self.returncode = 0
        return 0


trace_process = TraceProcess()
group_trace = []
real_killpg = MODULE.os.killpg
real_grace = MODULE.TERM_GRACE_SECONDS
try:
    MODULE.TERM_GRACE_SECONDS = 0.0

    def trace_killpg(pgid: int, signum: int) -> None:
        group_trace.append((pgid, signum, trace_process.returncode))

    MODULE.os.killpg = trace_killpg
    trace_returncode = MODULE._close_process_group(trace_process)
finally:
    MODULE.os.killpg = real_killpg
    MODULE.TERM_GRACE_SECONDS = real_grace
check(
    "every process-group query and signal precedes the sole leader reap",
    lambda: (
        trace_returncode == 0
        and group_trace
        and all(returncode is None for _pgid, _signum, returncode in group_trace)
        and group_trace[-1][1] == signal.SIGKILL
    ),
)


class ReapTimeoutProcess:
    pid = 434343
    returncode = None

    def wait(self, timeout: float) -> int:
        raise subprocess.TimeoutExpired("private/reap/path", timeout)


timeout_process = ReapTimeoutProcess()
cleanup_trace = []
real_run = MODULE._run
real_killpg = MODULE.os.killpg
real_grace = MODULE.TERM_GRACE_SECONDS
real_active_process = MODULE.ACTIVE_PROCESS
real_active_workspace = MODULE.ACTIVE_WORKSPACE
cleanup_stdout = io.StringIO()
cleanup_stderr = io.StringIO()
test_signal_mask = (
    signal.pthread_sigmask(signal.SIG_BLOCK, [])
    if hasattr(signal, "pthread_sigmask")
    else None
)
try:
    MODULE.TERM_GRACE_SECONDS = 0.0

    def interrupted_run(_argv):
        MODULE.ACTIVE_PROCESS = timeout_process
        raise MODULE.Interrupted(signal.SIGTERM)

    def cleanup_killpg(pgid: int, signum: int) -> None:
        cleanup_trace.append((pgid, signum, timeout_process.returncode))

    MODULE._run = interrupted_run
    MODULE.os.killpg = cleanup_killpg
    with contextlib.redirect_stdout(cleanup_stdout), contextlib.redirect_stderr(
        cleanup_stderr
    ):
        cleanup_exit = MODULE.main([])
finally:
    MODULE._run = real_run
    MODULE.os.killpg = real_killpg
    MODULE.TERM_GRACE_SECONDS = real_grace
    MODULE.ACTIVE_PROCESS = real_active_process
    MODULE.ACTIVE_WORKSPACE = real_active_workspace
    if test_signal_mask is not None:
        signal.pthread_sigmask(signal.SIG_SETMASK, test_signal_mask)
check(
    "signal cleanup reap timeout is sanitized after pre-reap SIGKILL",
    lambda: (
        cleanup_exit == 2
        and cleanup_stdout.getvalue() == ""
        and cleanup_stderr.getvalue() == "conformance: failed closed\n"
        and cleanup_trace
        and cleanup_trace[-1][1] == signal.SIGKILL
        and all(returncode is None for _pgid, _signum, returncode in cleanup_trace)
        and "private" not in cleanup_stderr.getvalue()
        and "Traceback" not in cleanup_stderr.getvalue()
    ),
)

for signal_name, expected_exit in (("HUP", 129), ("INT", 130), ("TERM", 143)):
    marker = TMP / f"signal-{signal_name}.ready"
    child_path = TMP / f"signal-{signal_name}.child"
    gate = write_gate(
        f"signal-{signal_name}",
        f"""
/bin/bash -c 'trap "" HUP INT TERM; echo $$ > {str(child_path)!r}; while :; do /bin/sleep 1; done' &
: > {str(marker)!r}
exit 0
""",
    )
    process = subprocess.Popen(
        [str(RUNNER), "--gate", str(gate)],
        cwd=str(ROOT),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    ready = False
    for _ in range(200):
        if marker.exists() and child_path.exists():
            ready = True
            break
        time.sleep(0.02)
    if ready:
        os.kill(process.pid, getattr(signal, "SIG" + signal_name))
    try:
        stdout, stderr = process.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = process.communicate()
    child_pid = int(child_path.read_text().strip()) if child_path.exists() else -1
    child_gone = True
    if child_pid > 0:
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            pass
        else:
            child_gone = False
            try:
                os.kill(child_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    check(
        f"{signal_name} returns exact status, reaps group, and leaks no success",
        lambda ready=ready, expected_exit=expected_exit, stdout=stdout, stderr=stderr, child_gone=child_gone, process=process: (
            ready
            and process.returncode == expected_exit
            and stdout == b""
            and stderr == b"conformance: interrupted\n"
            and child_gone
        ),
    )

double_marker = TMP / "double-signal.ready"
double_child_path = TMP / "double-signal.child"
double_gate = write_gate(
    "double-signal",
    f"""
/bin/bash -c 'trap "" HUP INT TERM; echo $$ > {str(double_child_path)!r}; while :; do /bin/sleep 1; done' &
: > {str(double_marker)!r}
exit 0
""",
)
double_process = subprocess.Popen(
    [str(RUNNER), "--gate", str(double_gate)],
    cwd=str(ROOT),
    stdin=subprocess.DEVNULL,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
)
double_ready = False
for _ in range(200):
    if double_marker.exists() and double_child_path.exists():
        double_ready = True
        break
    time.sleep(0.02)
if double_ready:
    os.kill(double_process.pid, signal.SIGHUP)
    time.sleep(0.02)
    try:
        os.kill(double_process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
try:
    double_stdout, double_stderr = double_process.communicate(timeout=5)
except subprocess.TimeoutExpired:
    double_process.kill()
    double_stdout, double_stderr = double_process.communicate()
double_child_pid = int(double_child_path.read_text().strip()) if double_child_path.exists() else -1
double_child_gone = True
if double_child_pid > 0:
    try:
        os.kill(double_child_pid, 0)
    except ProcessLookupError:
        pass
    else:
        double_child_gone = False
        try:
            os.kill(double_child_pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
check(
    "second lifecycle signal cannot interrupt first-signal group cleanup",
    lambda: (
        double_ready
        and double_process.returncode == 129
        and double_stdout == b""
        and double_stderr == b"conformance: interrupted\n"
        and double_child_gone
    ),
)

after_roots = roots()
check("complete suite leaves no conformance workspace", lambda: before_roots == after_roots)
check(
    "kit imports no network stack or provider client",
    lambda: all(
        token not in source
        for token in (b"import socket", b"import urllib", b"http.client", b"requests", b"curl", b"wget")
    ),
)

shutil.rmtree(TMP)
print(f"conformance v1 offline tests: {passed} passed, {failed} failed")
raise SystemExit(0 if failed == 0 else 1)
