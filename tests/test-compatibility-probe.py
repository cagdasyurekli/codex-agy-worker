#!/usr/bin/env python3
"""Offline adversarial tests for bounded compatibility subprocess profiles."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import signal
import shutil
import stat
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable


sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "compatibility_probe.py"
SPEC = importlib.util.spec_from_file_location("compatibility_probe_tested", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

TMP = Path(tempfile.mkdtemp(prefix="agyworker-probe-tests."))
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
        print(f"FAIL compatibility probe: {name}{detail}")


def rejects(action: Callable[[], Any]) -> bool:
    try:
        action()
    except MODULE.ProbeError:
        return True
    return False


def command(code: str) -> list[str]:
    return [sys.executable, "-I", "-B", "-c", code]


def process_gone(pid: int) -> bool:
    for _ in range(40):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.025)
    return False


check(
    "bounded capture returns separate exact streams",
    lambda: MODULE.run_bounded(
        command("import sys; sys.stdout.write('out'); sys.stderr.write('err')"),
        MODULE.Limits(1.0, 3, 3),
    )
    == (b"out", b"err"),
)
check(
    "exact stdout and stderr limits are accepted",
    lambda: MODULE.run_bounded(
        command("import sys; sys.stdout.write('1234'); sys.stderr.write('5678')"),
        MODULE.Limits(1.0, 4, 4),
    )
    == (b"1234", b"5678"),
)
check(
    "stdout overflow kills the probe",
    lambda: rejects(
        lambda: MODULE.run_bounded(
            command("print('x' * 9, end='')"), MODULE.Limits(1.0, 8, 8)
        )
    ),
)
check(
    "stderr overflow kills the probe",
    lambda: rejects(
        lambda: MODULE.run_bounded(
            command("import sys; sys.stderr.write('x' * 9)"),
            MODULE.Limits(1.0, 8, 8),
        )
    ),
)
check(
    "hard wall timeout kills the probe",
    lambda: rejects(
        lambda: MODULE.run_bounded(
            command("import time; time.sleep(5)"), MODULE.Limits(0.15, 8, 8)
        )
    ),
)
check(
    "nonzero child is rejected without returning partial output",
    lambda: rejects(
        lambda: MODULE.run_bounded(
            command("print('secret'); raise SystemExit(7)"), MODULE.Limits(1.0, 128, 128)
        )
    ),
)
check(
    "missing executable is controlled",
    lambda: rejects(
        lambda: MODULE.run_bounded(
            [str(TMP / "does-not-exist")], MODULE.Limits(1.0, 8, 8)
        )
    ),
)


def hostile_descendant_code(pid_marker: Path, late_marker: Path, stream: str) -> str:
    stream_write = {
        "none": "",
        "stdout": "os.write(1, b'x' * 4096)",
        "stderr": "os.write(2, b'x' * 4096)",
    }[stream]
    child_code = "\n".join(
        (
            "import os, signal, time",
            "signal.signal(signal.SIGHUP, signal.SIG_IGN)",
            "signal.signal(signal.SIGINT, signal.SIG_IGN)",
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)",
            f"with open({str(pid_marker)!r}, 'w') as marker:",
            "    marker.write(str(os.getpid()))",
            stream_write,
            "time.sleep(0.7)",
            f"with open({str(late_marker)!r}, 'w') as marker:",
            "    marker.write('late side effect')",
            "time.sleep(9)",
        )
    )
    return (
        "import subprocess,sys; "
        f"subprocess.Popen([sys.executable,'-I','-B','-c',{child_code!r}]); "
        "raise SystemExit(0)"
    )


def hostile_descendant_failure(label: str, stream: str) -> bool:
    pid_marker = TMP / f"{label}.pid"
    late_marker = TMP / f"{label}.late"
    limits = MODULE.Limits(0.35 if stream == "none" else 2.0, 128, 128)
    started = time.monotonic()
    rejected = rejects(
        lambda: MODULE.run_bounded(
            command(hostile_descendant_code(pid_marker, late_marker, stream)), limits
        )
    )
    elapsed = time.monotonic() - started
    if not pid_marker.exists():
        return False
    descendant = int(pid_marker.read_text(encoding="ascii"))
    gone = process_gone(descendant)
    time.sleep(0.8)
    return rejected and elapsed < 1.8 and gone and not late_marker.exists()


check(
    "timeout kills a TERM-ignoring descendant after its leader exits",
    lambda: hostile_descendant_failure("hostile-timeout", "none"),
)
check(
    "stdout overflow kills a TERM-ignoring descendant after its leader exits",
    lambda: hostile_descendant_failure("hostile-stdout", "stdout"),
)
check(
    "stderr overflow kills a TERM-ignoring descendant after its leader exits",
    lambda: hostile_descendant_failure("hostile-stderr", "stderr"),
)


def signal_cleanup(signum: int) -> bool:
    marker = TMP / f"signal-{signum}.pid"
    late_marker = TMP / f"signal-{signum}.late"
    runner = os.fork()
    if runner == 0:
        try:
            MODULE.run_bounded(
                command(hostile_descendant_code(marker, late_marker, "none")),
                MODULE.Limits(8.0, 128, 128),
            )
        except MODULE.ProbeInterrupted as exc:
            os._exit(0 if exc.signum == signum else 4)
        except BaseException:
            os._exit(5)
        os._exit(6)
    deadline = time.monotonic() + 2.0
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    if not marker.exists():
        os.kill(runner, signal.SIGKILL)
        os.waitpid(runner, 0)
        return False
    child = int(marker.read_text(encoding="ascii"))
    os.kill(runner, signum)
    _pid, status = os.waitpid(runner, 0)
    gone = process_gone(child)
    time.sleep(0.8)
    return (
        os.waitstatus_to_exitcode(status) == 0
        and gone
        and not late_marker.exists()
    )


check("HUP cleans a leader-exited TERM-ignoring group", lambda: signal_cleanup(signal.SIGHUP))
check("INT cleans a leader-exited TERM-ignoring group", lambda: signal_cleanup(signal.SIGINT))
check("TERM cleans a leader-exited TERM-ignoring group", lambda: signal_cleanup(signal.SIGTERM))


def completed_reap_signal(mask_signals: bool) -> tuple[bool, bool]:
    real_popen = MODULE.subprocess.Popen
    real_terminate = MODULE.terminate_group
    real_mask = MODULE.signal.pthread_sigmask
    initial_mask = real_mask(signal.SIG_BLOCK, [])
    cleanup_after_reap: list[bool] = []

    class SignallingWaitProcess:
        def __init__(self, *args: Any, **kwargs: Any):
            self.child = real_popen(*args, **kwargs)

        def __getattr__(self, name: str) -> Any:
            return getattr(self.child, name)

        def wait(self, *args: Any, **kwargs: Any) -> int:
            result = self.child.wait(*args, **kwargs)
            os.kill(os.getpid(), signal.SIGTERM)
            return result

    def record_cleanup(process: Any) -> None:
        cleanup_after_reap.append(process.child.returncode is not None)

    MODULE.subprocess.Popen = SignallingWaitProcess
    MODULE.terminate_group = record_cleanup
    if not mask_signals:
        MODULE.signal.pthread_sigmask = lambda *_args: set()
    interrupted = False
    try:
        MODULE.run_bounded(command("pass"), MODULE.Limits(1.0, 8, 8))
    except MODULE.ProbeInterrupted as exc:
        interrupted = exc.signum == signal.SIGTERM
    finally:
        MODULE.subprocess.Popen = real_popen
        MODULE.terminate_group = real_terminate
        MODULE.signal.pthread_sigmask = real_mask
        real_mask(signal.SIG_SETMASK, initial_mask)
    return interrupted, bool(cleanup_after_reap)


check(
    "signal after completed reap propagates without process-group cleanup",
    lambda: completed_reap_signal(True) == (True, False),
)
check(
    "removing atomic wait masking reintroduces cleanup after reap",
    lambda: completed_reap_signal(False) == (True, True),
)


def environment_policy() -> bool:
    source = {
        "PATH": "/bin",
        "HOME": "/private/example",
        "HTTP_PROXY": "secret",
        "https_proxy": "secret",
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "url.bad.insteadOf",
        "GIT_CONFIG_VALUE_0": "https://github.com/",
        "GIT_ASKPASS": "secret",
        "GIT_SSH_COMMAND": "secret",
        "PYTHONPATH": "secret",
        "PYTHONSTARTUP": "secret",
        "DYLD_INSERT_LIBRARIES": "secret",
        "LD_PRELOAD": "secret",
        "PAGER": "secret",
    }
    result = MODULE.scrubbed_environment(source)
    return (
        result["PATH"] == "/bin"
        and result["HOME"] == "/private/example"
        and result["TERM"] == "dumb"
        and result["NO_COLOR"] == "1"
        and not any("secret" == value for value in result.values())
        and not any(key.startswith("GIT_CONFIG_") for key in result)
    )


check("ambient transport and startup controls are stripped", environment_policy)


check(
    "agy version parser accepts documented bare output",
    lambda: MODULE._parse_version("agy", b"1.1.11\n") == "1.1.11",
)
check(
    "agy version parser accepts documented prefix",
    lambda: MODULE._parse_version("agy", b"agy 1.1.11\n") == "1.1.11",
)
check(
    "codex version parser accepts exact documented output",
    lambda: MODULE._parse_version("codex", b"codex-cli 0.147.0\n") == "0.147.0",
)
for index, raw in enumerate(
    (
        b"",
        b"1.1.11",
        b"version 1.1.11\n",
        b"1.1.11\nextra\n",
        b"1.1.11\x00\n",
        b"01.1.11\n",
        b"1.1.11-rc.1\n",
        b"\xff\n",
    ),
    1,
):
    check(
        f"malformed agy version form {index} is rejected",
        lambda raw=raw: rejects(lambda: MODULE._parse_version("agy", raw)),
    )
for index, raw in enumerate(
    (b"0.147.0\n", b"codex 0.147.0\n", b"codex-cli 0.147.0", b"codex-cli 0.147.0\nextra\n"),
    1,
):
    check(
        f"malformed codex version form {index} is rejected",
        lambda raw=raw: rejects(lambda: MODULE._parse_version("codex", raw)),
    )


def write_tool(name: str, output: str, marker: Path) -> None:
    path = TMP / name
    path.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$*\" > {str(marker)!r}\n"
        f"printf '%b' {output!r}\n",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def version_profile(tool: str, output: str, expected: bytes) -> bool:
    marker = TMP / f"{tool}.argv"
    write_tool(tool, output, marker)
    previous = os.environ.get("PATH")
    os.environ["PATH"] = f"{TMP}:/usr/bin:/bin"
    try:
        result = MODULE.capture_profile(f"{tool}-version")
    finally:
        if previous is None:
            os.environ.pop("PATH", None)
        else:
            os.environ["PATH"] = previous
    return result == expected and marker.read_text(encoding="utf-8") == "--version\n"


check(
    "agy profile invokes only exact --version and normalizes output",
    lambda: version_profile("agy", "agy 1.1.11\n", b"1.1.11\n"),
)
check(
    "codex profile invokes only exact --version and normalizes output",
    lambda: version_profile("codex", "codex-cli 0.147.0\n", b"0.147.0\n"),
)
check(
    "version profile rejects an extra caller argument",
    lambda: rejects(lambda: MODULE.capture_profile("agy-version", "extra")),
)


def official_profile(profile: str, argument: Any, expected_tail: list[str], payload: bytes) -> bool:
    captured: list[Any] = []
    original = MODULE.run_bounded

    def fake(argv: Any, limits: Any, **_kwargs: Any) -> tuple[bytes, bytes]:
        captured.extend([list(argv), limits])
        return payload, b"private ignored stderr"

    MODULE.run_bounded = fake
    try:
        result = MODULE.capture_profile(profile, argument)
    finally:
        MODULE.run_bounded = original
    argv, limits = captured
    return (
        result == payload
        and argv[:3] == [sys.executable, "-I", "-B"]
        and argv[-len(expected_tail) :] == expected_tail
        and limits == MODULE.OFFICIAL_LIMITS
    )


check(
    "agy official profile binds the fixed helper and exact tool",
    lambda: official_profile(
        "official-agy", None, ["--latest", "agy"], b"agy\t1.1.11\t" + b"a" * 40 + b"\n"
    ),
)
check(
    "project release profile accepts one stable tag",
    lambda: official_profile(
        "official-project-release",
        "v1.2.3",
        ["--project-release", "v1.2.3"],
        b"project\tv1.2.3\t" + b"b" * 40 + b"\n",
    ),
)
check(
    "unstable project release profile is rejected before child start",
    lambda: rejects(
        lambda: MODULE.capture_profile("official-project-release", "v1.2.3-rc.1")
    ),
)
check(
    "unknown production profile is rejected",
    lambda: rejects(lambda: MODULE.capture_profile("run-arbitrary", "/bin/sh")),
)
check(
    "official profile rejects multiline helper output",
    lambda: rejects(
        lambda: official_profile(
            "official-codex",
            None,
            ["--latest", "codex"],
            b"codex\t0.147.0\t" + b"c" * 40 + b"\nextra\n",
        )
    ),
)


def sanitized_main() -> bool:
    original = MODULE.capture_profile
    MODULE.capture_profile = lambda *_args: (_ for _ in ()).throw(
        MODULE.ProbeError("credential-bearing private child bytes")
    )
    stderr = io.StringIO()
    try:
        with contextlib.redirect_stderr(stderr):
            status = MODULE.main([str(MODULE_PATH), "agy-version"])
    finally:
        MODULE.capture_profile = original
    return (
        status == 2
        and stderr.getvalue() == "compatibility probe: evidence unavailable\n"
        and "credential" not in stderr.getvalue()
    )


check("CLI failures expose one sanitized category only", sanitized_main)
check(
    "CLI rejects extra invocation fields",
    lambda: MODULE.main([str(MODULE_PATH), "agy-version", "x", "y"]) == 2,
)

shutil.rmtree(TMP)
print(f"COMPATIBILITY_PROBE_TEST_RESULT passed={passed} failed={failed}")
raise SystemExit(1 if failed else 0)
