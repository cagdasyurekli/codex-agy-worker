#!/usr/bin/env python3
"""Focused offline tests for macOS native launch containment."""

from __future__ import annotations

import errno
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
from typing import Callable


sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
SUBJECT = ROOT / "skills" / "agy-worker" / "runtime" / "scripts" / "agy_dispatch_containment.py"
SCRIPTS = SUBJECT.parent
CLT_PYTHON = "/Library/Developer/CommandLineTools/usr/bin/python3"
CLT_PYTHON_EXECUTABLE = str(Path(os.path.realpath(CLT_PYTHON)))
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location("agy_dispatch_containment_tested", SUBJECT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)
DISPATCH_SPEC = importlib.util.spec_from_file_location(
    "agy_dispatch_containment_integration", SCRIPTS / "agy_dispatch.py",
)
assert DISPATCH_SPEC is not None and DISPATCH_SPEC.loader is not None
DISPATCH = importlib.util.module_from_spec(DISPATCH_SPEC)
sys.modules[DISPATCH_SPEC.name] = DISPATCH
DISPATCH_SPEC.loader.exec_module(DISPATCH)

passed = 0
failed = 0
skipped = 0


def check(name: str, action: Callable[[], bool]) -> None:
    global passed, failed, skipped
    try:
        outcome = action()
    except BaseException as exc:
        outcome = False
        detail = f" ({type(exc).__name__}: {exc})"
    else:
        detail = ""
    if outcome is None:
        skipped += 1
        print(f"  skip {name} (requires macOS native containment)")
        return
    okay = bool(outcome)
    if okay:
        passed += 1
        print(f"  ok   {name}")
    else:
        failed += 1
        print(f"  FAIL {name}{detail}")


def rejects(action: Callable[[], object]) -> bool:
    try:
        action()
    except (MODULE.ContainmentError, DISPATCH.DispatchError, OSError, subprocess.SubprocessError):
        return True
    return False


def fixture(name: str) -> tuple[Path, Path, Path, Path, Path]:
    root = Path(tempfile.mkdtemp(prefix=f"agyworker-containment-{name}.")).resolve()
    root.chmod(0o700)
    job = root / "job"
    job.mkdir(mode=0o700)
    stage = job / "stage-001"
    stage.mkdir(mode=0o700)
    checkout = root / "checkout"
    checkout.mkdir(mode=0o700)
    ambient_home = root / "ambient-home"
    ambient_home.mkdir(mode=0o700)
    return root, job, stage, checkout, ambient_home


def prepare(
    job: Path,
    stage: Path,
    executable: Path | str,
    argv: list[str],
    environment: dict[str, str],
    *,
    role: str = MODULE.ROLE_PROVIDER,
    allow_keychain: bool = False,
    network_policy: str = MODULE.NETWORK_DENY_ALL,
    read_only_inputs: tuple[Path, ...] = (),
) -> object:
    original_discovery = MODULE._discover_default_keychain
    if allow_keychain:
        synthetic = job.parent / "synthetic-default.keychain-db"
        synthetic.write_bytes(b"synthetic keychain metadata fixture\n")
        synthetic.chmod(0o600)
        binding = MODULE._bind_keychain(synthetic)
        MODULE._discover_default_keychain = lambda: binding
    try:
        return MODULE.prepare_contained_launch(
            role=role,
            network_policy=network_policy,
            job_dir=job,
            attempt=1,
            stage_dir=stage,
            target_executable=executable,
            target_argv=argv,
            child_environment=environment,
            allow_keychain=allow_keychain,
            read_only_inputs=read_only_inputs,
            provider_max_cycles=1 if allow_keychain else None,
            provider_write_selectors=(
                ({"kind": "file", "path": "candidate.py"},)
                if allow_keychain else ()
            ),
        )
    finally:
        MODULE._discover_default_keychain = original_discovery


def shell_probe(stage: Path) -> Path:
    child = stage / "child.sh"
    child.write_text(
        "#!/bin/sh\n/usr/bin/printf child >\"$PROBE_CHILD_OUT\"\n"
        "/usr/bin/printf escape >\"$PROBE_HOME\"\n",
        encoding="utf-8",
    )
    child.chmod(0o700)
    grand = stage / "grand.sh"
    grand.write_text(
        "#!/bin/sh\n/usr/bin/printf grand >\"$PROBE_GRAND_OUT\"\n"
        "/usr/bin/printf escape >\"$PROBE_HOME\"\n",
        encoding="utf-8",
    )
    grand.chmod(0o700)
    probe = stage / "probe.sh"
    probe.write_text(
        "#!/bin/sh\n"
        "set +e\n"
        "result=\"$PROBE_STAGE/result.txt\"\n"
        ": >\"$result\"\n"
        "probe_one() {\n"
        "  label=$1; target=$2\n"
        "  if /bin/cat \"$target\" >/dev/null 2>&1; then echo \"${label}_read=ALLOWED\" >>\"$result\"; else echo \"${label}_read=DENIED\" >>\"$result\"; fi\n"
        "  if /usr/bin/printf changed >\"$target\" 2>/dev/null; then echo \"${label}_write=ALLOWED\" >>\"$result\"; else echo \"${label}_write=DENIED\" >>\"$result\"; fi\n"
        "}\n"
        "probe_one checkout \"$PROBE_CHECKOUT\"\n"
        "probe_one git \"$PROBE_GIT\"\n"
        "probe_one home \"$PROBE_HOME\"\n"
        "probe_one sibling \"$PROBE_SIBLING\"\n"
        "/bin/ln -s \"$PROBE_CHECKOUT\" \"$PROBE_STAGE/checkout-link\"\n"
        "probe_one symlink \"$PROBE_STAGE/checkout-link\"\n"
        "/usr/bin/printf allowed >\"$PROBE_STAGE/allowed.txt\"\n"
        "\"$PROBE_CHILD\"\n"
        "echo \"child_rc=$?\" >>\"$result\"\n"
        "/bin/sh -c '\"$PROBE_GRAND\"'\n"
        "echo \"grand_rc=$?\" >>\"$result\"\n"
        "\"$PROBE_CONTROL\" >>\"$result\"\n"
        "for address in \"$PROBE_V4\" \"$PROBE_V6\" \"$PROBE_HOST\"; do\n"
        "  if /usr/bin/nc -z -w 1 \"$address\" \"$PROBE_PORT\" >/dev/null 2>&1; then echo net=ALLOWED >>\"$result\"; else echo net=DENIED >>\"$result\"; fi\n"
        "done\n"
        "/bin/cat \"$result\"\n",
        encoding="utf-8",
    )
    probe.chmod(0o700)
    return probe


def compile_process_control(stage: Path) -> Path:
    source = stage / "process-control.c"
    source.write_text(
        "#include <errno.h>\n#include <spawn.h>\n#include <stdio.h>\n#include <stdlib.h>\n"
        "#include <sys/wait.h>\n#include <unistd.h>\nextern char **environ;\n"
        "static void attempt_escape(void) {\n"
        "  const char *path = getenv(\"PROBE_HOME\"); FILE *stream = path ? fopen(path, \"w\") : NULL;\n"
        "  if (stream) { fputs(\"escape\", stream); fclose(stream); }\n"
        "}\n"
        "static void spawn_probe(const char *self, short flags, const char *label) {\n"
        "  posix_spawnattr_t attr; pid_t child = -1; posix_spawnattr_init(&attr);\n"
        "  posix_spawnattr_setflags(&attr, flags);\n"
        "  if (flags & POSIX_SPAWN_SETPGROUP) posix_spawnattr_setpgroup(&attr, 0);\n"
        "  char *const argv[] = {(char *)self, (char *)\"hold\", NULL};\n"
        "  int r = posix_spawn(&child, self, NULL, &attr, argv, environ);\n"
        "  usleep(100000);\n"
        "  printf(\"%s=%d child=%d pgrp=%d sid=%d\\n\", label, r, child, child > 0 ? getpgid(child) : -1, child > 0 ? getsid(child) : -1);\n"
        "  if (r == 0) waitpid(child, NULL, 0);\n"
        "  posix_spawnattr_destroy(&attr);\n"
        "}\n"
        "int main(int argc, char **argv) {\n"
        "  if (argc == 2) { attempt_escape(); usleep(300000); return 0; }\n"
        "  setbuf(stdout, NULL);\n"
        "  pid_t a = fork();\n"
        "  if (a == 0) { errno = 0; int r = setsid(); attempt_escape(); printf(\"setsid=%d errno=%d pid=%d pgrp=%d sid=%d\\n\", r, errno, getpid(), getpgrp(), getsid(0)); _exit(0); }\n"
        "  if (a < 0 || waitpid(a, NULL, 0) < 0) return 2;\n"
        "  pid_t b = fork();\n"
        "  if (b == 0) { errno = 0; int r = setpgid(0, 0); attempt_escape(); printf(\"setpgid=%d errno=%d pid=%d pgrp=%d sid=%d\\n\", r, errno, getpid(), getpgrp(), getsid(0)); _exit(0); }\n"
        "  if (b < 0 || waitpid(b, NULL, 0) < 0) return 3;\n"
        "  spawn_probe(argv[0], POSIX_SPAWN_SETSID, \"spawn_setsid\");\n"
        "  spawn_probe(argv[0], POSIX_SPAWN_SETPGROUP, \"spawn_setpgroup\");\n"
        "  return 0;\n}\n",
        encoding="utf-8",
    )
    output = stage / "process-control"
    subprocess.run(
        ["/usr/bin/clang", "-O0", "-Wall", "-Werror", str(source), "-o", str(output)],
        check=True, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    output.chmod(0o700)
    return output


def compile_process_path_probe(stage: Path) -> tuple[Path, Path]:
    """Build two distinct images that query policy without contacting a service."""
    source = stage / "process-path-probe.c"
    source.write_text(
        "#include <stdio.h>\n#include <stdlib.h>\n"
        "#include <sys/types.h>\n#include <sys/wait.h>\n#include <unistd.h>\n"
        "extern int sandbox_check(pid_t, const char *, int, ...);\n"
        "enum { AGY_SANDBOX_FILTER_GLOBAL_NAME = 2 };\n"
        "static const char *services[] = {\n"
        "  \"com.apple.SecurityServer\", \"com.apple.securityd.xpc\",\n"
        "  \"com.apple.securityd.general\", \"com.apple.trustd\",\n"
        "  \"com.apple.trustd.agent\", \"com.apple.mDNSResponder\",\n"
        "  \"com.example.agyworker.unlisted\", NULL\n};\n"
        "static void report(const char *label) {\n"
        "  for (int i = 0; services[i] != NULL; i++)\n"
        "    printf(\"%s %s %d\\n\", label, services[i],\n"
        "      sandbox_check(getpid(), \"mach-lookup\", AGY_SANDBOX_FILTER_GLOBAL_NAME, services[i]));\n"
        "}\n"
        "int main(int argc, char **argv) {\n"
        "  setbuf(stdout, NULL);\n"
        "  if (argc == 2 && argv[1][0] == 'c') { report(\"exec\"); return 0; }\n"
        "  if (argc != 2) return 2;\n"
        "  report(\"target\");\n"
        "  pid_t child = fork();\n"
        "  if (child == 0) { report(\"fork\"); _exit(0); }\n"
        "  if (child < 0 || waitpid(child, NULL, 0) < 0) return 3;\n"
        "  child = fork();\n"
        "  if (child == 0) { execl(argv[1], argv[1], \"child\", NULL); _exit(111); }\n"
        "  if (child < 0 || waitpid(child, NULL, 0) < 0) return 4;\n"
        "  return 0;\n}\n",
        encoding="utf-8",
    )
    target = stage / "process-path-target"
    child = stage / "process-path-child"
    compiled = subprocess.run(
        ["/usr/bin/clang", "-O0", "-Wall", "-Werror", str(source), "-o", str(target), "-lsandbox"],
        check=False, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if compiled.returncode != 0:
        raise AssertionError(f"sandbox_check probe compile failed: {compiled.stderr!r}")
    shutil.copy2(target, child)
    target.chmod(0o700)
    child.chmod(0o700)
    return target, child


def system_python_process_path() -> Path:
    """Return the kernel image path used by the qualified CLT Python image."""
    script = (
        "import ctypes, os; b=ctypes.create_string_buffer(4096); "
        "p=ctypes.CDLL('/usr/lib/libproc.dylib').proc_pidpath(os.getpid(),b,len(b)); "
        "print(b.value.decode() if p > 0 else '')"
    )
    result = subprocess.run(
        [CLT_PYTHON, "-I", "-S", "-B", "-c", script],
        check=False, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    path = Path(result.stdout.strip())
    if result.returncode != 0 or not path.is_absolute() or not path.is_file():
        raise AssertionError(
            f"cannot bind system Python process path: rc={result.returncode} "
            f"stdout={result.stdout!r} stderr={result.stderr!r}"
        )
    return path


def listener_probe(stage: Path) -> tuple[Path, Path]:
    child_source = stage / "listener-child.c"
    child_source.write_text(
        "#include <arpa/inet.h>\n#include <errno.h>\n#include <netdb.h>\n#include <stdio.h>\n"
        "#include <string.h>\n#include <sys/socket.h>\n#include <unistd.h>\n"
        "int main(void) {\n"
        "  struct addrinfo hints = {0}, *resolved = NULL; hints.ai_socktype = SOCK_STREAM;\n"
        "  int resolve_result = getaddrinfo(\"localhost\", \"0\", &hints, &resolved);\n"
        "  if (resolved != NULL) freeaddrinfo(resolved);\n"
        "  int fd = socket(AF_INET, SOCK_STREAM, 0); int saved = errno;\n"
        "  struct sockaddr_in address = {0}; address.sin_family = AF_INET;\n"
        "  address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);\n"
        "  if (fd >= 0 && bind(fd, (struct sockaddr *)&address, sizeof(address)) == 0"
        " && listen(fd, 1) == 0) { printf(\"resolve=%d bind=ALLOWED\\n\", resolve_result); close(fd); return 0; }\n"
        "  saved = errno; if (fd >= 0) close(fd); printf(\"resolve=%d bind_errno=%d:%s\\n\", resolve_result, saved, strerror(saved)); return 0;\n"
        "}\n",
        encoding="utf-8",
    )
    child = stage / "listener-child"
    compiled = subprocess.run(
        ["/usr/bin/clang", "-O0", "-Wall", "-Wextra", "-Werror", str(child_source), "-o", str(child)],
        check=False, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if compiled.returncode != 0:
        raise AssertionError(f"listener child compile failed: {compiled.stderr!r}")
    child.chmod(0o700)
    probe = stage / "listener-probe.py"
    probe.write_text(
        "import json, socket, subprocess, sys\n"
        "observed = {}\n"
        "try:\n"
        "  observed['resolved'] = sorted({row[4][0] for row in socket.getaddrinfo('localhost', 0, type=socket.SOCK_STREAM)})\n"
        "except OSError as exc:\n"
        "  observed['resolve_error'] = exc.errno\n"
        "for label, family, address in (\n"
        "  ('loopback4', socket.AF_INET, '127.0.0.1'), ('loopback6', socket.AF_INET6, '::1'),\n"
        "  ('wildcard4', socket.AF_INET, '0.0.0.0'), ('wildcard6', socket.AF_INET6, '::')):\n"
        "  sock = socket.socket(family, socket.SOCK_STREAM)\n"
        "  try:\n"
        "    sock.bind((address, 0)); sock.listen(1); observed[label] = 'ALLOWED'\n"
        "  except OSError as exc:\n"
        "    observed[label] = exc.errno\n"
        "  finally:\n"
        "    sock.close()\n"
        "child = subprocess.run([sys.argv[1]], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)\n"
        "observed['child'] = [child.returncode, child.stdout.strip(), child.stderr.strip()]\n"
        "print(json.dumps(observed, sort_keys=True))\n",
        encoding="utf-8",
    )
    probe.chmod(0o600)
    return probe, child


def local_ipv4_address() -> str:
    result = subprocess.run(
        ["/sbin/ifconfig"], check=True, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True,
    )
    candidates = re.findall(r"^\s*inet (\d+\.\d+\.\d+\.\d+) ", result.stdout, re.MULTILINE)
    return next(address for address in candidates if not address.startswith("127."))


def owned_listeners() -> tuple[int, dict[str, str], list[socket.socket]]:
    addresses = {"v4": "127.0.0.1", "v6": "::1", "host": local_ipv4_address()}
    listeners: list[socket.socket] = []
    try:
        first = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        first.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        first.bind((addresses["v4"], 0))
        port = int(first.getsockname()[1])
        first.listen(4)
        listeners.append(first)
        for address in (addresses["v6"], addresses["host"]):
            family = socket.AF_INET6 if ":" in address else socket.AF_INET
            listener = socket.socket(family, socket.SOCK_STREAM)
            if family == socket.AF_INET6:
                listener.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind((address, port))
            listener.listen(4)
            listeners.append(listener)
        for address in addresses.values():
            baseline = socket.create_connection((address, port), timeout=0.5)
            baseline.close()
        return port, addresses, listeners
    except BaseException:
        for listener in listeners:
            listener.close()
        raise


def run_confirmed(prepared: object) -> subprocess.CompletedProcess[bytes]:
    confirmed = MODULE.confirm_contained_launch(prepared)
    return subprocess.run(
        confirmed.argv, executable=confirmed.executable, cwd=confirmed.cwd,
        env=confirmed.environment, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10,
        check=False, start_new_session=True,
    )


def profile_and_environment_are_private() -> bool:
    if sys.platform != "darwin":
        return None
    root, job, stage, _checkout, ambient = fixture("private")
    try:
        prepared = prepare(
            job, stage, "/usr/bin/true", ["/usr/bin/true"],
            {"HOME": str(ambient), "PATH": "/usr/bin:/bin"},
        )
        confirmed = MODULE.confirm_contained_launch(prepared)
        verify_job = root / "verify-job"
        verify_job.mkdir(mode=0o700)
        verify_stage = verify_job / "stage-001"
        verify_stage.mkdir(mode=0o700)
        verifier = prepare(
            verify_job, verify_stage, "/usr/bin/python3", ["/usr/bin/python3", "-V"],
            {"PATH": "/usr/bin:/bin"}, role=MODULE.ROLE_SELF_VERIFY,
        )
        verifier_launch = MODULE.confirm_contained_launch(verifier)
        profile_text = Path(prepared.profile.path).read_text(encoding="utf-8")
        return (
            Path(confirmed.environment["HOME"]) == job / "provider-home"
            and Path(confirmed.environment["TMPDIR"]) == job / "provider-tmp-001"
            and Path(verifier_launch.environment["HOME"]) == verify_job / "self-verify-home"
            and stat.S_IMODE(Path(prepared.profile.path).stat().st_mode) == 0o400
            and prepared.profile.sha256 is not None
            and confirmed.argv[-1] == "/usr/bin/true"
            and verifier_launch.argv[-2:] == ("/usr/bin/python3", "-V")
            and "(deny default)" in profile_text
            and "(deny syscall-unix" in profile_text
        )
    finally:
        shutil.rmtree(root)


def role_and_network_policy_fail_closed() -> bool:
    root, job, stage, _checkout, _ambient = fixture("policy")
    try:
        bad_network = rejects(lambda: MODULE.render_profile(
            target_executable="/usr/bin/true", role=MODULE.ROLE_PROVIDER,
            network_policy="unbounded", allow_keychain=False,
        ))
        self_verify_network = rejects(lambda: MODULE.render_profile(
            target_executable="/usr/bin/true", role=MODULE.ROLE_SELF_VERIFY,
            network_policy=MODULE.NETWORK_PROVIDER_TLS, allow_keychain=False,
        ))
        bad_keychain = rejects(lambda: MODULE.prepare_contained_launch(
            role=MODULE.ROLE_SELF_VERIFY, network_policy=MODULE.NETWORK_DENY_ALL,
            job_dir=job, attempt=1, stage_dir=stage,
            target_executable="/usr/bin/true", target_argv=["/usr/bin/true"],
            child_environment={}, allow_keychain=True,
            provider_max_cycles=1,
            provider_write_selectors=({"kind": "file", "path": "candidate.py"},),
        ))
        return bad_network and self_verify_network and bad_keychain
    finally:
        shutil.rmtree(root)


def security_helper_keychain_exception_is_exact_and_provider_only() -> bool:
    """Render the helper exception without executing a Keychain helper."""
    services = (
        "com.apple.SecurityServer", "com.apple.securityd.xpc",
        "com.apple.securityd.general", "com.apple.trustd",
        "com.apple.trustd.agent",
    )
    target = "/private/tmp/agyworker-provider-image"
    profile = MODULE.render_profile(
        target_executable=target, role=MODULE.ROLE_PROVIDER,
        network_policy=MODULE.NETWORK_PROVIDER_TLS, allow_keychain=True,
        keychain_path="/private/tmp/agyworker-synthetic.keychain-db",
    ).decode("utf-8")
    helper = """(with-filter (process-path \"/usr/bin/security\")
  (allow mach-lookup
    (global-name \"com.apple.SecurityServer\")
    (global-name \"com.apple.securityd.xpc\")
    (global-name \"com.apple.securityd.general\")
    (global-name \"com.apple.trustd\")
    (global-name \"com.apple.trustd.agent\"))
  (allow file-read*
    (literal \"/private/tmp/agyworker-synthetic.keychain-db\")))
"""
    self_verify = MODULE.render_profile(
        target_executable="/usr/bin/true", role=MODULE.ROLE_SELF_VERIFY,
        network_policy=MODULE.NETWORK_DENY_ALL, allow_keychain=False,
    ).decode("utf-8")
    return (
        profile.count(helper) == 1
        and all(profile.count(f'(global-name "{service}")') == 2 for service in services)
        and "network-" not in helper
        and set(re.findall(r'\(with-filter \(process-path "([^"]+)"\)', profile))
        == {target, "/usr/bin/security"}
        and '/usr/bin/security' not in self_verify
        and all(service not in self_verify for service in services)
        and rejects(lambda: MODULE.render_profile(
            target_executable="/usr/bin/true", role=MODULE.ROLE_SELF_VERIFY,
            network_policy=MODULE.NETWORK_DENY_ALL, allow_keychain=True,
        ))
    )


def binding_drift_fails_closed() -> bool:
    if sys.platform != "darwin":
        return None
    root, job, stage, _checkout, ambient = fixture("drift")
    try:
        prepared = prepare(
            job, stage, "/usr/bin/true", ["/usr/bin/true"],
            {"HOME": str(ambient), "PATH": "/usr/bin:/bin"},
        )
        profile = Path(prepared.profile.path)
        profile.chmod(0o600)
        return rejects(lambda: MODULE.confirm_contained_launch(prepared))
    finally:
        shutil.rmtree(root)


def runtime_input_drift_fails_closed() -> bool:
    if sys.platform != "darwin":
        return None
    root, job, stage, _checkout, ambient = fixture("input-drift")
    schema = job / "provider-schema.json"
    schema.write_text('{"type":"object"}\n', encoding="utf-8")
    schema.chmod(0o600)
    try:
        prepared = prepare(
            job, stage, "/usr/bin/true", ["/usr/bin/true"],
            {"HOME": str(ambient), "PATH": "/usr/bin:/bin"},
            read_only_inputs=(schema,),
        )
        schema.write_text('{"type":"string"}\n', encoding="utf-8")
        return rejects(lambda: MODULE.confirm_contained_launch(prepared))
    finally:
        shutil.rmtree(root)


def keychain_preferences_create_once_and_reject_drift() -> bool:
    """The generated preference never replaces a changed private artifact."""
    root, job, _stage, _checkout, _ambient = fixture("keychain-preferences")
    original_payload = MODULE._default_keychain_preferences_payload
    try:
        keychain_file = root / "default.keychain-db"
        keychain_file.write_bytes(b"metadata fixture\n")
        keychain_file.chmod(0o644)
        keychain = MODULE._bind_keychain(keychain_file)
        home = MODULE._ensure_private_directory(job / "provider-home", existing_ok=True)
        MODULE._default_keychain_preferences_payload = lambda _binding: b"synthetic plist\n"
        first = MODULE._prepare_keychain_preferences(job, Path(home.path), keychain)
        repaired = MODULE._prepare_keychain_preferences(job, Path(home.path), keychain)
        preferences = Path(first.path)
        sidecar = job / ".provider-keychain-preferences.binding"
        initial = (
            first == repaired
            and preferences.read_bytes() == b"synthetic plist\n"
            and stat.S_IMODE(preferences.stat().st_mode) == 0o600
            and stat.S_IMODE(sidecar.stat().st_mode) == 0o600
        )
        preferences.write_bytes(b"changed\n")
        changed_rejected = rejects(lambda: MODULE._prepare_keychain_preferences(
            job, Path(home.path), keychain,
        ))
        preferences.unlink()
        deleted_rejected = rejects(lambda: MODULE._prepare_keychain_preferences(
            job, Path(home.path), keychain,
        ))
        preferences.symlink_to(keychain_file)
        symlink_rejected = rejects(lambda: MODULE._prepare_keychain_preferences(
            job, Path(home.path), keychain,
        ))
        return initial and changed_rejected and deleted_rejected and symlink_rejected
    finally:
        MODULE._default_keychain_preferences_payload = original_payload
        shutil.rmtree(root)


def provider_settings_are_precomputed_private_and_rebound() -> bool:
    root, job, _stage, _checkout, _ambient = fixture("provider-settings")
    try:
        home = MODULE._ensure_private_directory(job / "provider-home", existing_ok=True)
        selectors = (
            {"kind": "file", "path": "candidate.py"},
            {"kind": "tree", "path": "output"},
        )
        validated = MODULE._validate_provider_write_selectors(selectors)
        first = MODULE._prepare_provider_settings(job, Path(home.path), 2, validated)
        repaired = MODULE._prepare_provider_settings(job, Path(home.path), 2, validated)
        settings = Path(first.path)
        sidecar = job / ".provider-antigravity-settings.binding"
        expected = {
            "permissions": {"allow": [
                f"read_file({job / 'stage-001'})",
                f"write_file({job / 'stage-001' / 'candidate.py'})",
                f"write_file({job / 'stage-001' / 'output'})",
                f"read_file({job / 'stage-002'})",
                f"write_file({job / 'stage-002' / 'candidate.py'})",
                f"write_file({job / 'stage-002' / 'output'})",
            ]},
        }
        initial = (
            first == repaired
            and json.loads(settings.read_text(encoding="utf-8")) == expected
            and stat.S_IMODE(settings.stat().st_mode) == 0o600
            and stat.S_IMODE(sidecar.stat().st_mode) == 0o600
        )
        settings.write_bytes(b"tampered")
        changed_rejected = rejects(lambda: MODULE._prepare_provider_settings(
            job, Path(home.path), 2, validated,
        ))
        settings.unlink()
        deleted_rejected = rejects(lambda: MODULE._prepare_provider_settings(
            job, Path(home.path), 2, validated,
        ))
        settings.symlink_to(root / "outside-settings.json")
        symlink_rejected = rejects(lambda: MODULE._prepare_provider_settings(
            job, Path(home.path), 2, validated,
        ))
        invalid_selector_rejected = rejects(lambda: MODULE._validate_provider_write_selectors((
            {"kind": "file", "path": "../escape"},
        ))) and rejects(lambda: MODULE._validate_provider_write_selectors((
            {"kind": "tree", "path": ".git"},
        ))) and rejects(lambda: MODULE._validate_provider_write_selectors((
            {"kind": "file", "path": "literal*rule"},
        )))
        return (
            initial and changed_rejected and deleted_rejected and symlink_rejected
            and invalid_selector_rejected
        )
    finally:
        shutil.rmtree(root)


def provider_settings_leaf_is_immutable_but_home_state_stays_writable() -> bool:
    if sys.platform != "darwin":
        return None
    root, job, stage, _checkout, _ambient = fixture("provider-settings-profile")
    try:
        script = stage / "settings-probe.py"
        script.write_text(
            "from pathlib import Path\n"
            "import os,json\n"
            "home = Path(os.environ['HOME'])\n"
            "result = Path(os.environ['TMPDIR']) / 'settings-result'\n"
            "(home / 'ordinary-state').write_text('allowed')\n"
            "settings = home / '.gemini/antigravity-cli/settings.json'\n"
            "replacement = home / 'replacement-settings'\n"
            "replacement.write_text('tampered')\n"
            "observed = {'readable': bool(settings.read_bytes())}\n"
            "for name,action in [('write', lambda: settings.write_text('tampered')), ('unlink', settings.unlink), ('replace', lambda: os.replace(replacement, settings))]:\n"
            "    try: action()\n"
            "    except PermissionError: observed[name] = 'denied'\n"
            "    else: observed[name] = 'allowed'\n"
            "result.write_text(json.dumps(observed))\n",
            encoding="utf-8",
        )
        script.chmod(0o600)
        prepared = prepare(
            job, stage, CLT_PYTHON_EXECUTABLE,
            [CLT_PYTHON_EXECUTABLE, "-I", "-S", "-B", str(script)],
            {}, allow_keychain=True,
        )
        confirmed = run_confirmed(prepared)
        profile = Path(prepared.profile.path).read_text(encoding="utf-8")
        settings = Path(prepared.provider_settings.path)
        result = Path(prepared.attempt_tmp.path) / "settings-result"
        confirmed_rebinds = confirmed.returncode == 0
        settings.write_text("tampered", encoding="utf-8")
        try:
            MODULE.confirm_contained_launch(prepared)
        except MODULE.ContainmentError as exc:
            settings_error = str(exc) == "native provider permission settings are unavailable"
        else:
            settings_error = False
        return (
            confirmed.returncode == 0
            and (Path(prepared.private_home.path) / "ordinary-state").read_text() == "allowed"
            and json.loads(result.read_text()) == {
                "readable": True, "write": "denied", "unlink": "denied", "replace": "denied",
            }
            and f'(deny file-write*\n  (literal "{settings}"))' in profile
            and confirmed_rebinds
            and settings_error
        )
    finally:
        shutil.rmtree(root)


def provider_settings_rule_targets_and_failures_are_exact() -> bool:
    if sys.platform != "darwin":
        return None
    root, job, stage, _checkout, _ambient = fixture("provider-settings-boundary")
    original_discovery = MODULE._discover_default_keychain
    original_preferences = MODULE._prepare_keychain_preferences
    original_settings = MODULE._prepare_provider_settings
    try:
        selectors = MODULE._validate_provider_write_selectors((
            {"kind": "file", "path": "candidate.py"},
        ))
        grammar_job = root / "job*rule"
        grammar_job.mkdir(mode=0o700)
        grammar_rejected = rejects(lambda: MODULE._provider_settings_payload(
            grammar_job, 1, selectors,
        ))
        mismatched = job / "stage-other"
        mismatched.mkdir(mode=0o700)
        stage_rejected = rejects(lambda: MODULE.prepare_contained_launch(
            role=MODULE.ROLE_PROVIDER, network_policy=MODULE.NETWORK_DENY_ALL,
            job_dir=job, attempt=1, stage_dir=mismatched,
            target_executable="/usr/bin/true", target_argv=["/usr/bin/true"],
            child_environment={}, allow_keychain=True, provider_max_cycles=1,
            provider_write_selectors=({"kind": "file", "path": "candidate.py"},),
        ))
        verification_copy = job / "verification-copy"
        verification_copy.mkdir(mode=0o700)
        self_verify = MODULE.prepare_contained_launch(
            role=MODULE.ROLE_SELF_VERIFY, network_policy=MODULE.NETWORK_DENY_ALL,
            job_dir=job, attempt=1, stage_dir=verification_copy,
            target_executable="/usr/bin/true", target_argv=["/usr/bin/true"],
            child_environment={},
        )

        def exact_error(action: Callable[[], object], expected: str) -> bool:
            try:
                action()
            except MODULE.ContainmentError as exc:
                return str(exc) == expected
            return False

        settings_validation = exact_error(lambda: MODULE.prepare_contained_launch(
            role=MODULE.ROLE_PROVIDER, network_policy=MODULE.NETWORK_DENY_ALL,
            job_dir=job, attempt=1, stage_dir=stage,
            target_executable="/usr/bin/true", target_argv=["/usr/bin/true"],
            child_environment={}, allow_keychain=True, provider_max_cycles=1,
            provider_write_selectors=({"kind": "file", "path": "bad*selector"},),
        ), "native provider permission settings are unavailable")
        MODULE._discover_default_keychain = lambda: (_ for _ in ()).throw(
            MODULE.ContainmentError("synthetic auth failure"),
        )
        authentication = exact_error(lambda: MODULE.prepare_contained_launch(
            role=MODULE.ROLE_PROVIDER, network_policy=MODULE.NETWORK_DENY_ALL,
            job_dir=job, attempt=1, stage_dir=stage,
            target_executable="/usr/bin/true", target_argv=["/usr/bin/true"],
            child_environment={}, allow_keychain=True, provider_max_cycles=1,
            provider_write_selectors=({"kind": "file", "path": "candidate.py"},),
        ), "native provider authentication is unavailable")
        keychain_file = root / "synthetic.keychain-db"
        keychain_file.write_bytes(b"metadata only\n")
        keychain_file.chmod(0o600)
        MODULE._discover_default_keychain = lambda: MODULE._bind_keychain(keychain_file)
        MODULE._prepare_keychain_preferences = lambda *_args: object()
        MODULE._prepare_provider_settings = lambda *_args: (_ for _ in ()).throw(
            MODULE.ContainmentError("synthetic settings failure"),
        )
        settings_preparation = exact_error(lambda: MODULE.prepare_contained_launch(
            role=MODULE.ROLE_PROVIDER, network_policy=MODULE.NETWORK_DENY_ALL,
            job_dir=job, attempt=1, stage_dir=stage,
            target_executable="/usr/bin/true", target_argv=["/usr/bin/true"],
            child_environment={}, allow_keychain=True, provider_max_cycles=1,
            provider_write_selectors=({"kind": "file", "path": "candidate.py"},),
        ), "native provider permission settings are unavailable")
        return (
            grammar_rejected and stage_rejected
            and self_verify.stage.path == str(verification_copy)
            and self_verify.provider_settings is None
            and not (Path(self_verify.private_home.path) / ".gemini").exists()
            and settings_validation
            and authentication and settings_preparation
        )
    finally:
        MODULE._discover_default_keychain = original_discovery
        MODULE._prepare_keychain_preferences = original_preferences
        MODULE._prepare_provider_settings = original_settings
        shutil.rmtree(root)


def keychain_binding_is_metadata_only_and_rebinds_identity() -> bool:
    if sys.platform != "darwin":
        return None
    root, job, stage, _checkout, _ambient = fixture("keychain-binding")
    original_discovery = MODULE._discover_default_keychain
    original_payload = MODULE._default_keychain_preferences_payload
    original_read_hash = MODULE._read_hash
    original_lstat = MODULE.os.lstat
    original_canonical = MODULE._canonical_existing
    try:
        keychain_file = root / "default.keychain-db"
        keychain_file.write_bytes(b"first metadata bytes\n")
        keychain_file.chmod(0o644)
        lstat_calls = 0

        def metadata_churn(path: object) -> object:
            nonlocal lstat_calls
            result = original_lstat(path)
            if os.fspath(path) == str(keychain_file):
                lstat_calls += 1
                if lstat_calls == 1:
                    keychain_file.write_bytes(b"between-lstat content churn\n")
            return result

        MODULE._canonical_existing = lambda _path: keychain_file
        MODULE.os.lstat = metadata_churn
        churn_tolerated = MODULE._bind_keychain(keychain_file)
        MODULE.os.lstat = original_lstat
        MODULE._canonical_existing = original_canonical
        MODULE._read_hash = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("keychain database must not be hashed"),
        )
        keychain = MODULE._bind_keychain(keychain_file)
        MODULE._read_hash = original_read_hash
        MODULE._default_keychain_preferences_payload = lambda _binding: b"synthetic plist\n"
        MODULE._discover_default_keychain = lambda: keychain
        prepared = MODULE.prepare_contained_launch(
            role=MODULE.ROLE_PROVIDER, network_policy=MODULE.NETWORK_DENY_ALL,
            job_dir=job, attempt=1, stage_dir=stage,
            target_executable="/usr/bin/true", target_argv=["/usr/bin/true"],
            child_environment={}, allow_keychain=True,
            provider_max_cycles=1,
            provider_write_selectors=({"kind": "file", "path": "candidate.py"},),
        )
        keychain_file.write_bytes(b"changed content is allowed\n")
        unchanged_identity_passes = MODULE.confirm_contained_launch(prepared).argv[0] == "/usr/bin/sandbox-exec"
        replacement = root / "replacement.keychain-db"
        replacement.write_bytes(b"replacement\n")
        replacement.chmod(0o644)
        replacement.replace(keychain_file)
        identity_drift_rejected = rejects(lambda: MODULE.confirm_contained_launch(prepared))
        return (
            churn_tolerated.path == str(keychain_file)
            and lstat_calls == 2
            and unchanged_identity_passes and identity_drift_rejected
        )
    finally:
        MODULE._discover_default_keychain = original_discovery
        MODULE._default_keychain_preferences_payload = original_payload
        MODULE._read_hash = original_read_hash
        MODULE.os.lstat = original_lstat
        MODULE._canonical_existing = original_canonical
        shutil.rmtree(root)


def keychain_policy_is_helper_only_and_self_verify_never_discovers() -> bool:
    keychain = "/private/tmp/agyworker-helper-only.keychain-db"
    profile = MODULE.render_profile(
        target_executable="/usr/bin/true", role=MODULE.ROLE_PROVIDER,
        network_policy=MODULE.NETWORK_DENY_ALL, allow_keychain=True,
        keychain_path=keychain,
    ).decode("utf-8")
    helper_clause = (
        '(with-filter (process-path "/usr/bin/security")\n'
        '  (allow mach-lookup\n'
    )
    return (
        helper_clause in profile
        and profile.count(f'(literal "{keychain}")') == 1
        and f'(with-filter (process-path "/bin/cat")' not in profile
        and rejects(lambda: MODULE.render_profile(
            target_executable="/usr/bin/true", role=MODULE.ROLE_SELF_VERIFY,
            network_policy=MODULE.NETWORK_DENY_ALL, allow_keychain=False,
            keychain_path=keychain,
        ))
    )


def keychain_locator_is_bounded_closed_and_strict() -> bool:
    """Exercise only synthetic locator scripts; no owner Keychain is queried."""
    if sys.platform != "darwin":
        return None
    root, _job, _stage, _checkout, _ambient = fixture("keychain-locator")
    original_helper = MODULE.SECURITY_HELPER
    original_deadline = MODULE.KEYCHAIN_LOCATOR_DEADLINE_SECONDS
    original_grace = MODULE.KEYCHAIN_LOCATOR_TERM_GRACE_SECONDS
    try:
        database = root / "default.keychain-db"
        database.write_bytes(b"metadata only\n")
        database.chmod(0o644)

        def locator_script(name: str, body: str) -> Path:
            script = root / name
            script.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
            script.chmod(0o755)
            return script

        def discovered(script: Path) -> object:
            MODULE.SECURITY_HELPER = script
            return MODULE._discover_default_keychain()

        quoted = f"printf '\"%s\"\\n' \"{database}\""
        valid = discovered(locator_script("valid-security", quoted))
        expected = MODULE._bind_keychain(database)
        indented = locator_script(
            "indented-security", f"printf '    \"%s\"\\n' \"{database}\"",
        )
        indented_valid = discovered(indented)
        invalid = locator_script("invalid-security", "printf 'not quoted\\n'")
        multiple = locator_script("multiple-security", quoted + "; " + quoted)
        trailing = locator_script(
            "trailing-security", f"printf '\"%s\" trailing\\n' \"{database}\"",
        )
        symlink = root / "linked.keychain-db"
        symlink.symlink_to(database)
        linked = locator_script(
            "linked-security", f"printf '\"%s\"\\n' \"{symlink}\"",
        )
        directory = root / "not-a-keychain"
        directory.mkdir(mode=0o700)
        nonregular = locator_script(
            "directory-security", f"printf '\"%s\"\\n' \"{directory}\"",
        )
        writable = root / "writable.keychain-db"
        writable.write_bytes(b"metadata only\n")
        writable.chmod(0o666)
        writable_script = locator_script(
            "writable-security", f"printf '\"%s\"\\n' \"{writable}\"",
        )
        oversized = locator_script(
            "oversized-security", "while :; do printf 0123456789abcdef; done",
        )
        timeout = locator_script("timeout-security", "sleep 2")
        MODULE.KEYCHAIN_LOCATOR_DEADLINE_SECONDS = 0.05
        MODULE.KEYCHAIN_LOCATOR_TERM_GRACE_SECONDS = 0.05
        return (
            valid == expected
            and indented_valid == expected
            and rejects(lambda: discovered(invalid))
            and rejects(lambda: discovered(multiple))
            and rejects(lambda: discovered(trailing))
            and rejects(lambda: discovered(linked))
            and rejects(lambda: discovered(nonregular))
            and rejects(lambda: discovered(writable_script))
            and rejects(lambda: discovered(oversized))
            and rejects(lambda: discovered(timeout))
        )
    finally:
        MODULE.SECURITY_HELPER = original_helper
        MODULE.KEYCHAIN_LOCATOR_DEADLINE_SECONDS = original_deadline
        MODULE.KEYCHAIN_LOCATOR_TERM_GRACE_SECONDS = original_grace
        shutil.rmtree(root)


def _diagnose_locator_reap_conditions(
    *,
    holding_rejected: bool,
    holding_gone: bool,
    closed_rejected: bool,
    closed_gone: bool,
) -> tuple[bool, str]:
    conditions = {
        "holding_rejected": bool(holding_rejected),
        "holding_gone": bool(holding_gone),
        "closed_rejected": bool(closed_rejected),
        "closed_gone": bool(closed_gone),
    }
    if all(conditions.values()):
        return True, ""
    failed = [name for name, passed in conditions.items() if not passed]
    detail = f"locator conditions failed: {failed!r}; all={conditions!r}"
    return False, detail


def keychain_locator_reaps_leaderless_descendants() -> bool:
    """An exited locator leader cannot leave its fresh session running."""
    if sys.platform != "darwin":
        return None
    root, _job, _stage, _checkout, _ambient = fixture("keychain-locator-reap")
    original_helper = MODULE.SECURITY_HELPER
    original_deadline = MODULE.KEYCHAIN_LOCATOR_DEADLINE_SECONDS
    original_grace = MODULE.KEYCHAIN_LOCATOR_TERM_GRACE_SECONDS
    try:
        database = root / "default.keychain-db"
        database.write_bytes(b"metadata only\n")
        database.chmod(0o644)

        def helper(name: str, body: str) -> Path:
            script = root / name
            script.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
            script.chmod(0o755)
            return script

        def run(script: Path) -> bool:
            MODULE.SECURITY_HELPER = script
            return rejects(MODULE._discover_default_keychain)

        def gone(pid_path: Path) -> bool:
            pid = int(pid_path.read_text(encoding="ascii").strip())
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    return True
                time.sleep(0.02)
            return False

        MODULE.KEYCHAIN_LOCATOR_DEADLINE_SECONDS = 0.3
        MODULE.KEYCHAIN_LOCATOR_TERM_GRACE_SECONDS = 0.3
        holding_pid = root / "holding.pid"
        holding = helper(
            "holding-stdout-security",
            f"sleep 30 & echo $! > '{holding_pid}'; exit 0",
        )
        closes_pid = root / "closed.pid"
        closes = helper(
            "closed-stdout-security",
            f"sleep 30 >/dev/null 2>&1 & echo $! > '{closes_pid}'; "
            f"printf '\"%s\"\\n' '{database}'; exit 0",
        )
        holding_rejected = run(holding)
        holding_gone = holding_pid.exists() and gone(holding_pid)
        closed_rejected = run(closes)
        closed_gone = closes_pid.exists() and gone(closes_pid)
        passed, detail = _diagnose_locator_reap_conditions(
            holding_rejected=holding_rejected,
            holding_gone=holding_gone,
            closed_rejected=closed_rejected,
            closed_gone=closed_gone,
        )
        if not passed:
            raise AssertionError(detail)
        return True
    finally:
        MODULE.SECURITY_HELPER = original_helper
        MODULE.KEYCHAIN_LOCATOR_DEADLINE_SECONDS = original_deadline
        MODULE.KEYCHAIN_LOCATOR_TERM_GRACE_SECONDS = original_grace
        shutil.rmtree(root)


def keychain_locator_reap_diagnostics_report_sanitized_conditions() -> bool:
    """Diagnostic reporter emits sanitized labels without paths, PIDs, raw output, or credentials."""
    if sys.platform != "darwin":
        return None
    # 1. Conjunction pass returns empty detail
    passed, detail = _diagnose_locator_reap_conditions(
        holding_rejected=True, holding_gone=True, closed_rejected=True, closed_gone=True,
    )
    if not passed or detail != "":
        raise AssertionError(f"expected pass with empty detail, got passed={passed} detail={detail!r}")

    # 2. Each condition failure reports its sanitized label without secrets or paths
    for name in ("holding_rejected", "holding_gone", "closed_rejected", "closed_gone"):
        kwargs = {
            "holding_rejected": True,
            "holding_gone": True,
            "closed_rejected": True,
            "closed_gone": True,
        }
        kwargs[name] = False
        passed, detail = _diagnose_locator_reap_conditions(**kwargs)
        if passed:
            raise AssertionError(f"expected failure when {name}=False")
        if name not in detail:
            raise AssertionError(f"expected condition label {name!r} in detail {detail!r}")
        if "/" in detail or "\\" in detail:
            raise AssertionError(f"path detected in diagnostic detail: {detail!r}")
        if any(token in detail.lower() for token in ("keychain", "password", "secret", "token", "credential", "stdout", "stderr")):
            raise AssertionError(f"sensitive token detected in diagnostic detail: {detail!r}")

    # 3. All failed conjunction reports all labels
    passed, detail = _diagnose_locator_reap_conditions(
        holding_rejected=False, holding_gone=False, closed_rejected=False, closed_gone=False,
    )
    if passed:
        raise AssertionError("expected failure when all conditions are False")
    for name in ("holding_rejected", "holding_gone", "closed_rejected", "closed_gone"):
        if name not in detail:
            raise AssertionError(f"expected {name!r} in multi-failure detail {detail!r}")

    # 4. Exercise existing synthetic fixture mechanics to verify real diagnostic outcome evaluation
    root, _job, _stage, _checkout, _ambient = fixture("locator-diag-coverage")
    original_helper = MODULE.SECURITY_HELPER
    try:
        database = root / "default.keychain-db"
        database.write_bytes(b"metadata only\n")
        database.chmod(0o644)
        clean = root / "clean-security"
        clean.write_text(
            f"#!/bin/sh\nprintf '\"%s\"\\n' '{database}'; exit 0\n",
            encoding="utf-8",
        )
        clean.chmod(0o755)
        MODULE.SECURITY_HELPER = clean
        clean_rejected = rejects(MODULE._discover_default_keychain)
        passed, detail = _diagnose_locator_reap_conditions(
            holding_rejected=clean_rejected,
            holding_gone=True,
            closed_rejected=True,
            closed_gone=True,
        )
        if passed or "holding_rejected" not in detail:
            raise AssertionError(f"synthetic fixture did not trigger holding_rejected diagnostic: {detail!r}")
        return True
    finally:
        MODULE.SECURITY_HELPER = original_helper
        shutil.rmtree(root)


def private_keychain_publication_failures_are_sanitized() -> bool:
    root, _job, _stage, _checkout, _ambient = fixture("keychain-publish-failure")
    original_write = MODULE.os.write
    original_fsync = MODULE.os.fsync
    original_fchmod = MODULE.os.fchmod
    try:
        def normalized(path: Path) -> bool:
            try:
                MODULE._publish_private_file(path, b"payload\n")
            except MODULE.ContainmentError as exc:
                return str(exc) == "native provider authentication is unavailable"
            return False

        MODULE.os.write = lambda *_args: (_ for _ in ()).throw(OSError("synthetic write"))
        write_failure = normalized(root / "write.plist")
        MODULE.os.write = original_write
        MODULE.os.fsync = lambda *_args: (_ for _ in ()).throw(OSError("synthetic fsync"))
        fsync_failure = normalized(root / "fsync.plist")
        MODULE.os.fsync = original_fsync
        MODULE.os.fchmod = lambda *_args: (_ for _ in ()).throw(OSError("synthetic chmod"))
        chmod_failure = normalized(root / "chmod.plist")
        return write_failure and fsync_failure and chmod_failure
    finally:
        MODULE.os.write = original_write
        MODULE.os.fsync = original_fsync
        MODULE.os.fchmod = original_fchmod
        shutil.rmtree(root)


def self_verify_never_runs_keychain_discovery() -> bool:
    if sys.platform != "darwin":
        return None
    root, job, stage, _checkout, _ambient = fixture("no-self-verify-keychain")
    original_discovery = MODULE._discover_default_keychain
    calls = 0
    try:
        def unavailable() -> object:
            nonlocal calls
            calls += 1
            raise AssertionError("self-verification must not discover a Keychain")
        MODULE._discover_default_keychain = unavailable
        prepared = MODULE.prepare_contained_launch(
            role=MODULE.ROLE_SELF_VERIFY, network_policy=MODULE.NETWORK_DENY_ALL,
            job_dir=job, attempt=1, stage_dir=stage,
            target_executable="/usr/bin/true", target_argv=["/usr/bin/true"],
            child_environment={}, allow_keychain=False,
        )
        return calls == 0 and prepared.keychain is None and prepared.keychain_preferences is None
    finally:
        MODULE._discover_default_keychain = original_discovery
        shutil.rmtree(root)


def unsupported_host_fails_before_creation() -> bool:
    root, job, stage, _checkout, _ambient = fixture("unsupported")
    original_platform = MODULE.sys.platform
    try:
        MODULE.sys.platform = "linux"
        rejected = rejects(lambda: MODULE.prepare_contained_launch(
            role=MODULE.ROLE_PROVIDER, network_policy=MODULE.NETWORK_DENY_ALL,
            job_dir=job, attempt=1, stage_dir=stage,
            target_executable="/usr/bin/true", target_argv=["/usr/bin/true"],
            child_environment={},
        ))
        return rejected and not (job / "provider-home").exists()
    finally:
        MODULE.sys.platform = original_platform
        shutil.rmtree(root)


def native_boundary_is_enforced() -> bool:
    if sys.platform != "darwin":
        return None
    root, job, stage, checkout, ambient = fixture("native")
    sibling = root / "sibling"
    sibling.mkdir(mode=0o700)
    listeners: list[socket.socket] = []
    try:
        git = checkout / ".git"
        git.mkdir(mode=0o700)
        outside = {
            "checkout": checkout / "source.txt",
            "git": git / "config",
            "home": ambient / "secret.txt",
            "sibling": sibling / "secret.txt",
        }
        for label, path in outside.items():
            path.write_text(label, encoding="utf-8")
        probe = shell_probe(stage)
        fixture_target = Path("/bin/sh")
        control = compile_process_control(stage)
        local_port, addresses, listeners = owned_listeners()
        environment = {
            "PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C",
            "PROBE_STAGE": str(stage), "PROBE_CHECKOUT": str(outside["checkout"]),
            "PROBE_GIT": str(outside["git"]), "PROBE_HOME": str(outside["home"]),
            "PROBE_SIBLING": str(outside["sibling"]),
            "PROBE_CHILD": str(stage / "child.sh"), "PROBE_GRAND": str(stage / "grand.sh"),
            "PROBE_CHILD_OUT": str(stage / "child.txt"),
            "PROBE_GRAND_OUT": str(stage / "grand.txt"),
            "PROBE_CONTROL": str(control), "PROBE_V4": addresses["v4"],
            "PROBE_V6": addresses["v6"], "PROBE_HOST": addresses["host"],
            "PROBE_PORT": str(local_port),
        }
        prepared = prepare(
            job, stage, fixture_target, [str(fixture_target), str(probe)], environment,
        )
        result = run_confirmed(prepared)
        if result.returncode != 0:
            partial = (stage / "result.txt").read_bytes() if (stage / "result.txt").exists() else b""
            raise AssertionError(
                f"rc={result.returncode} stdout={result.stdout!r} stderr={result.stderr!r} partial={partial!r}"
            )
        observed: dict[str, list[str]] = {}
        for line in result.stdout.decode("utf-8", "strict").splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                observed.setdefault(key, []).append(value)
        denied = all(
            observed[f"{label}_{operation}"] == ["DENIED"]
            for label in outside for operation in ("read", "write")
        )
        symlink_denied = all(
            observed[f"symlink_{operation}"] == ["DENIED"]
            for operation in ("read", "write")
        )
        unchanged = all(path.read_text(encoding="utf-8") == label for label, path in outside.items())
        return (
            denied and symlink_denied
            and observed["net"] == ["DENIED", "DENIED", "DENIED"]
            and unchanged and (stage / "allowed.txt").read_text(encoding="utf-8") == "allowed"
            and (stage / "child.txt").read_text(encoding="utf-8") == "child"
            and (stage / "grand.txt").read_text(encoding="utf-8") == "grand"
            and observed["child_rc"] != ["0"] and observed["grand_rc"] != ["0"]
            and observed["setsid"][0].startswith("-1 errno=1 ")
            and observed["setpgid"][0].startswith("-1 errno=1 ")
            and observed["spawn_setsid"][0].startswith("0 ")
            and observed["spawn_setpgroup"][0].startswith("0 ")
            and prepared.private_home.path == str(job / "provider-home")
        )
    finally:
        for listener in listeners:
            listener.close()
        shutil.rmtree(root)


def localhost_predicate_blocks_owned_local_endpoints() -> bool:
    """Exercise Apple's shipped localhost token in a disposable test profile."""
    if sys.platform != "darwin":
        return None
    root = Path(tempfile.mkdtemp(prefix="agyworker-localhost-token.")).resolve()
    root.chmod(0o700)
    listeners: list[socket.socket] = []
    try:
        port, addresses, listeners = owned_listeners()
        profile = root / "localhost.sb"
        profile.write_text(
            "(version 1)\n(deny default)\n(import \"system.sb\")\n(import \"dyld-support.sb\")\n"
            "(allow syscall*)\n(allow mach-bootstrap)\n(allow process-fork)\n"
            "(allow process-info-pidinfo (target self))\n(allow sysctl-read)\n"
            "(system-network)\n"
            "(allow process-exec (literal \"/usr/bin/nc\") (literal \"/usr/bin/true\"))\n"
            "(allow file-read* file-map-executable\n"
            "  (literal \"/usr/bin/nc\") (literal \"/usr/bin/true\") (subpath \"/usr/lib\")\n"
            "  (subpath \"/usr/share\") (subpath \"/System\") (subpath \"/Library/Apple\")\n"
            "  (literal \"/dev/null\"))\n"
            "(allow network-outbound (require-all\n"
            f"  (require-any (remote tcp \"*:{port}\"))\n"
            "  (require-not (remote ip \"localhost:*\"))))\n"
            "(deny network-outbound (remote ip \"localhost:*\"))\n",
            encoding="utf-8",
        )
        profile.chmod(0o400)
        applied = subprocess.run(
            ["/usr/bin/sandbox-exec", "-f", str(profile), "/usr/bin/true"],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=5, check=False,
        )
        if applied.returncode != 0:
            raise AssertionError(
                f"allowed stage marker rc={applied.returncode} stdout={applied.stdout!r} stderr={applied.stderr!r}"
            )
        outcomes: list[tuple[int, bytes]] = []
        for address in addresses.values():
            result = subprocess.run(
                ["/usr/bin/sandbox-exec", "-f", str(profile), "/usr/bin/nc", "-v", "-z", "-w", "1", address, str(port)],
                env={"LC_ALL": "C", "PATH": "/usr/bin:/bin"},
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=5, check=False,
            )
            outcomes.append((result.returncode, result.stderr))
        if not all(rc == 1 and b"Operation not permitted" in stderr for rc, stderr in outcomes):
            raise AssertionError(f"expected network denial rc=1 and EPERM, got {outcomes!r}")
        return True
    finally:
        for listener in listeners:
            listener.close()
        shutil.rmtree(root)


def keychain_filter_profile_parses_without_keychain_access() -> bool:
    if sys.platform != "darwin":
        return None
    root, job, stage, _checkout, _ambient = fixture("keychain-parser")
    try:
        prepared = prepare(
            job, stage, "/usr/bin/true", ["/usr/bin/true"], {},
            allow_keychain=True, network_policy=MODULE.NETWORK_PROVIDER_TLS,
        )
        profile = Path(prepared.profile.path).read_text(encoding="utf-8")
        return (
            run_confirmed(prepared).returncode == 0
            and '(remote tcp "*:443")' in profile
            and '(require-not (remote ip "localhost:*"))' in profile
            and '(global-name "com.apple.mDNSResponder")' in profile
        )
    finally:
        shutil.rmtree(root)


def process_path_filters_keychain_and_mdns_without_service_access() -> bool:
    """Prove process-image filtering through policy queries only."""
    if sys.platform != "darwin":
        return None
    root, job, stage, _checkout, _ambient = fixture("process-path")
    try:
        target, child = compile_process_path_probe(stage)
        prepared = prepare(
            job, stage, target, [str(target), str(child)], {},
            allow_keychain=True, network_policy=MODULE.NETWORK_PROVIDER_TLS,
        )
        result = run_confirmed(prepared)
        if result.returncode != 0:
            raise AssertionError(
                f"rc={result.returncode} stdout={result.stdout!r} stderr={result.stderr!r}"
            )
        observed: dict[tuple[str, str], int] = {}
        for line in result.stdout.decode("utf-8", "strict").splitlines():
            label, service, raw_value = line.split(" ", 2)
            observed[(label, service)] = int(raw_value)
        reviewed = (
            "com.apple.SecurityServer", "com.apple.securityd.xpc",
            "com.apple.securityd.general", "com.apple.trustd",
            "com.apple.trustd.agent", "com.apple.mDNSResponder",
        )
        unlisted = "com.example.agyworker.unlisted"
        return (
            all(observed[("target", service)] == 0 for service in reviewed)
            and all(observed[("fork", service)] == 0 for service in reviewed)
            and all(observed[("exec", service)] != 0 for service in reviewed)
            and all(observed[(label, unlisted)] != 0 for label in ("target", "fork", "exec"))
        )
    finally:
        shutil.rmtree(root)


def provider_listener_authority_is_image_bound_and_self_verify_denied() -> bool:
    """Exercise resolver/listener authority without external network or credentials."""
    if sys.platform != "darwin":
        return None
    provider_root, provider_job, provider_stage, _checkout, _ambient = fixture("provider-listener")
    verify_root, verify_job, verify_stage, _checkout, _ambient = fixture("verify-listener")
    try:
        target = system_python_process_path()
        provider_probe, provider_child = listener_probe(provider_stage)
        provider = prepare(
            provider_job, provider_stage, target,
            [str(target), "-I", "-S", "-B", str(provider_probe), str(provider_child)],
            {}, network_policy=MODULE.NETWORK_PROVIDER_TLS,
        )
        provider_result = run_confirmed(provider)
        if provider_result.returncode != 0:
            raise AssertionError(
                f"provider listener rc={provider_result.returncode} "
                f"stdout={provider_result.stdout!r} stderr={provider_result.stderr!r}"
            )
        provider_observed = json.loads(provider_result.stdout)
        provider_profile = Path(provider.profile.path).read_text(encoding="utf-8")

        verify_probe, verify_child = listener_probe(verify_stage)
        verifier = prepare(
            verify_job, verify_stage, target,
            [str(target), "-I", "-S", "-B", str(verify_probe), str(verify_child)],
            {}, role=MODULE.ROLE_SELF_VERIFY,
        )
        verify_result = run_confirmed(verifier)
        if verify_result.returncode != 0:
            raise AssertionError(
                f"self-verify listener rc={verify_result.returncode} "
                f"stdout={verify_result.stdout!r} stderr={verify_result.stderr!r}"
            )
        verify_observed = json.loads(verify_result.stdout)
        verify_profile = Path(verifier.profile.path).read_text(encoding="utf-8")
        provider_child_output = provider_observed["child"]
        verify_child_output = verify_observed["child"]
        provider_child_fields = provider_child_output[1].split()
        verify_child_fields = verify_child_output[1].split()
        return (
            set(provider_observed["resolved"]) == {"127.0.0.1", "::1"}
            and all(provider_observed[name] == "ALLOWED" for name in (
                "loopback4", "loopback6", "wildcard4", "wildcard6",
            ))
            and provider_child_output[0] == 0
            and provider_child_fields[0].startswith("resolve=")
            and int(provider_child_fields[0].split("=", 1)[1]) != 0
            and provider_child_fields[1].startswith(f"bind_errno={errno.EPERM}:")
            and provider_child_output[2] == ""
            and "resolved" not in verify_observed
            and isinstance(verify_observed.get("resolve_error"), int)
            and all(verify_observed[name] == errno.EPERM for name in (
                "loopback4", "loopback6", "wildcard4", "wildcard6",
            ))
            and verify_child_output[0] == 0
            and verify_child_fields[0].startswith("resolve=")
            and int(verify_child_fields[0].split("=", 1)[1]) != 0
            and verify_child_fields[1].startswith(f"bind_errno={errno.EPERM}:")
            and verify_child_output[2] == ""
            and '(literal "/private/var/run/mDNSResponder")' in provider_profile
            and '(allow network-bind network-inbound (local tcp "localhost:*"))' in provider_profile
            and '(deny network-outbound (remote ip "localhost:*"))' in provider_profile
            and '/private/var/run/mDNSResponder' not in verify_profile
            and 'network-bind network-inbound' not in verify_profile
        )
    finally:
        shutil.rmtree(provider_root)
        shutil.rmtree(verify_root)


def canonical_self_verify_python_executes() -> bool:
    if sys.platform != "darwin":
        return None
    root, job, stage, _checkout, _ambient = fixture("python")
    try:
        output = stage / "python-ok"
        argv = [
            CLT_PYTHON_EXECUTABLE, "-I", "-S", "-B", "-c",
            f"from pathlib import Path; Path({str(output)!r}).write_text('ok')",
        ]
        prepared = prepare(
            job, stage, CLT_PYTHON_EXECUTABLE, argv,
            {"SHOULD_BE_REMOVED": "provider-only"}, role=MODULE.ROLE_SELF_VERIFY,
        )
        environment = MODULE.confirm_contained_launch(prepared).environment
        result = run_confirmed(prepared)
        if result.returncode != 0:
            raise AssertionError(
                f"rc={result.returncode} stdout={result.stdout!r} stderr={result.stderr!r}"
            )
        return (
            result.returncode == 0 and output.read_text(encoding="utf-8") == "ok"
            and "SHOULD_BE_REMOVED" not in environment
        )
    finally:
        shutil.rmtree(root)


def identity_safe_group_cleanup() -> bool:
    if sys.platform != "darwin":
        return None
    process = subprocess.Popen(
        ["/bin/sh", "-c", "sleep 30 & /bin/sh -c 'sleep 30 & wait' & wait"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        root = MODULE.bind_new_process_group(process.pid)
        deadline = time.monotonic() + 2
        while len(MODULE._process_group_members(root.process_group)) < 3 and time.monotonic() < deadline:
            time.sleep(0.01)
        members = MODULE.terminate_bound_process_group(root)
        process.wait(timeout=3)
        deadline = time.monotonic() + 3
        while not MODULE.process_group_is_quiescent(root) and time.monotonic() < deadline:
            time.sleep(0.02)
        return (
            len(members) >= 3 and process.returncode == -signal.SIGKILL
            and MODULE.process_group_is_quiescent(root)
        )
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()


def contained_cleanup_never_falls_back_to_unbound_group_signal() -> bool:
    process = subprocess.Popen(
        ["/bin/sleep", "30"], stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        refused = rejects(lambda: DISPATCH._terminate_provider_process(
            process, None, contained=True,
        ))
        return refused and process.poll() is None
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()


def integrated_stage_rebind_and_outside_write_denial() -> bool:
    """Exercise the dispatcher rebind and real provider profile together."""
    if sys.platform != "darwin":
        return None
    root, job, _unused_stage, checkout, _ambient = fixture("integrated")
    shutil.rmtree(_unused_stage)
    outside = root / "outside.txt"
    outside.write_text("unchanged", encoding="utf-8")
    schema = job / "provider-schema.json"
    schema.write_text('{"type":"object"}\n', encoding="utf-8")
    schema.chmod(0o600)
    unbound_sibling = job / "unbound-sibling.txt"
    unbound_sibling.write_text("private sibling", encoding="utf-8")
    unbound_sibling.chmod(0o600)
    candidate = checkout / "candidate.py"
    candidate.write_text("original\n", encoding="utf-8")
    scope = {
        "schema_version": 1,
        "kind": "agy-worker-provider-scope",
        "read": [{"path": "candidate.py", "kind": "file"}],
        "write": [{"path": "candidate.py", "kind": "file"}],
    }
    try:
        selected = DISPATCH._build_selected_content_manifest(checkout, scope)

        hardlink_stage = job / "stage-001"
        hardlink_identity, hardlink_sha = DISPATCH._materialize_stage(
            checkout, hardlink_stage, scope, selected,
        )
        (hardlink_stage / "candidate.py").unlink()
        os.link(outside, hardlink_stage / "candidate.py")
        hardlink_rejected = rejects(lambda: DISPATCH._revalidate_scoped_provider_stage(
            hardlink_stage, scope, selected, hardlink_identity, hardlink_sha,
        ))
        shutil.rmtree(hardlink_stage)

        extra_stage = job / "stage-002"
        extra_identity, extra_sha = DISPATCH._materialize_stage(
            checkout, extra_stage, scope, selected,
        )
        (extra_stage / "unapproved.txt").write_text("extra", encoding="utf-8")
        extra_rejected = rejects(lambda: DISPATCH._revalidate_scoped_provider_stage(
            extra_stage, scope, selected, extra_identity, extra_sha,
        ))
        shutil.rmtree(extra_stage)

        stage = job / "stage-003"
        stage_identity, stage_sha = DISPATCH._materialize_stage(
            checkout, stage, scope, selected,
        )
        DISPATCH._revalidate_scoped_provider_stage(
            stage, scope, selected, stage_identity, stage_sha,
        )
        target = DISPATCH.CONTAINMENT
        synthetic_keychain = root / "synthetic-default.keychain-db"
        synthetic_keychain.write_bytes(b"integration metadata fixture\n")
        synthetic_keychain.chmod(0o600)
        original_discovery = target._discover_default_keychain
        target._discover_default_keychain = lambda: target._bind_keychain(synthetic_keychain)
        python = CLT_PYTHON_EXECUTABLE
        script = (
            "import errno,os,time\n"
            "from pathlib import Path\n"
            "try:\n Path(os.sys.argv[1]).write_text('escaped')\n raise SystemExit(91)\n"
            "except OSError as exc:\n"
            " Path(os.sys.argv[4]).write_text(str(exc.errno))\n"
            "try:\n os.write(int(os.sys.argv[3]), b'fd-escaped')\n raise SystemExit(92)\n"
            "except OSError as exc:\n"
            " Path(os.sys.argv[5]).write_text(str(exc.errno))\n"
            "Path(os.sys.argv[2]).write_text('allowed')\n"
            "Path(os.sys.argv[7]).write_text(Path(os.sys.argv[6]).read_text())\n"
            "try:\n Path(os.sys.argv[6]).write_text('schema-escaped')\n raise SystemExit(93)\n"
            "except OSError as exc:\n Path(os.sys.argv[8]).write_text(str(exc.errno))\n"
            "try:\n Path(os.sys.argv[1]).read_text()\n raise SystemExit(94)\n"
            "except OSError as exc:\n Path(os.sys.argv[9]).write_text(str(exc.errno))\n"
            "try:\n Path(os.sys.argv[10]).read_text()\n raise SystemExit(95)\n"
            "except OSError as exc:\n Path(os.sys.argv[11]).write_text(str(exc.errno))\n"
            "Path(os.sys.argv[12]).write_text('complete')\n"
            "time.sleep(30)\n"
        )
        inherited_fd = os.open(outside, os.O_WRONLY)
        os.set_inheritable(inherited_fd, True)
        if not os.get_inheritable(inherited_fd):
            raise AssertionError("outside descriptor did not become inheritable")
        prepared = target.prepare_contained_launch(
            role=target.ROLE_PROVIDER,
            network_policy=target.NETWORK_PROVIDER_TLS,
            job_dir=job,
            attempt=3,
            stage_dir=stage,
            target_executable=python,
            target_argv=(
                python, "-I", "-S", "-B", "-c", script,
                str(outside), str(stage / "candidate.py"), str(inherited_fd),
                str(stage / "path-result.txt"), str(stage / "fd-result.txt"),
                str(schema), str(stage / "schema-read-result.txt"),
                str(stage / "schema-write-result.txt"),
                str(stage / "outside-read-result.txt"), str(unbound_sibling),
                str(stage / "sibling-read-result.txt"),
                str(stage / "attempts-complete.txt"),
            ),
            child_environment={"PATH": "/usr/bin:/bin", "SECRET_SHOULD_NOT_PASS": "provider-approved"},
            allow_keychain=True,
            read_only_inputs=(schema,),
            provider_max_cycles=3,
            provider_write_selectors=scope["write"],
        )
        confirmed = target.confirm_contained_launch(prepared)
        process = subprocess.Popen(
            confirmed.argv,
            executable=confirmed.executable,
            cwd=confirmed.cwd,
            env=confirmed.environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            close_fds=True,
        )
        os.close(inherited_fd)
        inherited_fd = -1
        bound_root = target.bind_new_process_group(process.pid)
        completion = stage / "attempts-complete.txt"
        deadline = time.monotonic() + 3
        complete = False
        while time.monotonic() < deadline:
            try:
                complete = completion.read_text(encoding="utf-8") == "complete"
            except FileNotFoundError:
                complete = False
            if complete or process.poll() is not None:
                break
            time.sleep(0.02)
        DISPATCH._terminate_provider_process(process, bound_root, contained=True)
        assert process.stdout is not None and process.stderr is not None
        stdout = process.stdout.read().decode("utf-8", errors="replace")
        stderr = process.stderr.read().decode("utf-8", errors="replace")
        process.stdout.close()
        process.stderr.close()

        def observed_text(path: Path) -> str:
            try:
                return path.read_text(encoding="utf-8")
            except OSError as exc:
                return f"<{type(exc).__name__}: {exc}>"

        observed = {
            "hardlink_rejected": hardlink_rejected,
            "extra_rejected": extra_rejected,
            "complete": complete,
            "outside": observed_text(outside),
            "candidate": observed_text(stage / "candidate.py"),
            "path_result": observed_text(stage / "path-result.txt"),
            "fd_result": observed_text(stage / "fd-result.txt"),
            "schema_read_result": observed_text(stage / "schema-read-result.txt"),
            "schema": observed_text(schema),
            "schema_write_result": observed_text(stage / "schema-write-result.txt"),
            "outside_read_result": observed_text(stage / "outside-read-result.txt"),
            "sibling_read_result": observed_text(stage / "sibling-read-result.txt"),
            "home": confirmed.environment["HOME"],
            "tmpdir": confirmed.environment["TMPDIR"],
        }
        expected = {
            "hardlink_rejected": True,
            "extra_rejected": True,
            "complete": True,
            "outside": "unchanged",
            "candidate": "allowed",
            "path_result": str(errno.EPERM),
            "fd_result": str(errno.EBADF),
            "schema_read_result": '{"type":"object"}\n',
            "schema": '{"type":"object"}\n',
            "schema_write_result": str(errno.EPERM),
            "outside_read_result": str(errno.EPERM),
            "sibling_read_result": str(errno.EPERM),
            "home": str(job / "provider-home"),
            "tmpdir": str(job / "provider-tmp-003"),
        }
        if observed != expected:
            raise AssertionError(
                f"integrated containment mismatch: observed={observed!r}; "
                f"returncode={process.returncode!r}; stdout={stdout!r}; stderr={stderr!r}"
            )
        return True
    finally:
        if "target" in locals() and "original_discovery" in locals():
            target._discover_default_keychain = original_discovery
        if "inherited_fd" in locals() and inherited_fd >= 0:
            os.close(inherited_fd)
        shutil.rmtree(root)


def provider_parent_metadata_rule_is_exact() -> bool:
    target = '/private/owned parent "quoted"/provider'
    provider = MODULE.render_profile(
        target_executable=target, role=MODULE.ROLE_PROVIDER,
        network_policy=MODULE.NETWORK_DENY_ALL, allow_keychain=False,
    ).decode()
    verifier = MODULE.render_profile(
        target_executable=target, role=MODULE.ROLE_SELF_VERIFY,
        network_policy=MODULE.NETWORK_DENY_ALL, allow_keychain=False,
    ).decode()
    addition = (
        f'\n(with-filter (process-path {MODULE._scheme_string(target)})\n'
        '  (allow file-read-metadata file-test-existence\n'
        f'    (literal {MODULE._scheme_string(str(Path(target).parent))}))\n'
        '  ; SQLite resolves every ancestor before opening its private conversation DB.\n'
        '  (allow file-read-metadata (path-ancestors (param "HOME"))))\n'
    )
    return provider.count(addition) == 1 and provider.replace(addition, "") == verifier


def provider_home_ancestor_metadata_is_image_bound() -> bool:
    """Exercise the SQLite-required ancestor lstat grant in a native profile."""
    if sys.platform != "darwin":
        return None
    root, _job, _stage, _checkout, _ambient = fixture("home-ancestor-metadata")
    try:
        source = root / "ancestor-probe.c"
        source.write_text(r'''
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>

static int denied(void) { return errno == EACCES || errno == EPERM; }

static int parent_path(const char *home, char path[PATH_MAX]) {
    if (strlcpy(path, home, PATH_MAX) >= PATH_MAX) return 0;
    char *slash = strrchr(path, '/');
    if (!slash || slash == path) return 0;
    *slash = '\0';
    return 1;
}

static int trim_parent(char path[PATH_MAX]) {
    char *slash = strrchr(path, '/');
    if (!slash) return 0;
    if (slash == path) {
        if (path[1] == '\0') return 0;
        path[1] = '\0';
        return 1;
    }
    *slash = '\0';
    return 1;
}

static int every_ancestor_lstat(const char *home) {
    char path[PATH_MAX]; struct stat st;
    if (!parent_path(home, path)) return 0;
    for (;;) {
        if (lstat(path, &st) != 0) return 0;
        if (!trim_parent(path)) return 1;
    }
}

static int immediate_ancestor_directory_listing_denied(const char *home) {
    char path[PATH_MAX];
    if (!parent_path(home, path)) return 0;
    errno = 0; DIR *directory = opendir(path);
    if (directory || !denied()) { if (directory) closedir(directory); return 0; }
    return 1;
}

static int immediate_parent_lstat_denied(const char *home) {
    char path[PATH_MAX]; struct stat st;
    if (!parent_path(home, path)) return 0;
    errno = 0;
    return lstat(path, &st) == -1 && denied();
}

int main(int argc, char **argv) {
    if (argc == 3 && strcmp(argv[1], "child") == 0)
        return immediate_parent_lstat_denied(argv[2]) ? 0 : 1;
    if (argc != 5) return 90;
    struct stat st;
    int ancestors_lstat = every_ancestor_lstat(argv[1]);
    int ancestors_listing_denied = immediate_ancestor_directory_listing_denied(argv[1]);
    errno = 0; int ancestor_data = open(argv[3], O_RDONLY);
    int ancestor_data_denied = ancestor_data == -1 && denied();
    if (ancestor_data >= 0) close(ancestor_data);
    errno = 0; int sibling_metadata_denied = lstat(argv[2], &st) == -1 && denied();
    errno = 0; int descriptor = open(argv[2], O_RDONLY);
    int sibling_read_denied = descriptor == -1 && denied();
    if (descriptor >= 0) close(descriptor);
    pid_t child = fork();
    if (child < 0) return 91;
    if (!child) { execl(argv[4], argv[4], "child", argv[1], (char *)NULL); _exit(92); }
    int status;
    if (waitpid(child, &status, 0) != child || !WIFEXITED(status)) return 93;
    printf("%d %d %d %d %d %d\n", ancestors_lstat, ancestors_listing_denied,
           ancestor_data_denied, sibling_metadata_denied, sibling_read_denied,
           WEXITSTATUS(status) == 0);
    return 0;
}
''', encoding="utf-8")
        for role, expected in (
            (MODULE.ROLE_PROVIDER, "1 1 1 1 1 1"),
            (MODULE.ROLE_SELF_VERIFY, "0 1 1 1 1 1"),
        ):
            job = root / f"{role}-job"
            stage = job / "stage-001"
            job.mkdir(mode=0o700)
            stage.mkdir(mode=0o700)
            target = stage / "ancestor-probe"
            child = stage / "other-image"
            sibling = job / "private-sibling"
            ancestor_data = job / "ancestor-data"
            sibling.write_text("not readable or metadata-visible\n", encoding="utf-8")
            ancestor_data.write_text("not readable from a HOME ancestor\n", encoding="utf-8")
            subprocess.run(
                ["/usr/bin/clang", "-O0", "-Wall", "-Wextra", "-Werror",
                 str(source), "-o", str(target)],
                check=True, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30,
            )
            shutil.copy2(target, child)
            target.chmod(0o700)
            child.chmod(0o700)
            prepared = prepare(
                job, stage, target,
                [str(target), str(job / "provider-home"), str(sibling), str(ancestor_data), str(child)],
                {"PATH": "/usr/bin:/bin"}, role=role,
            )
            result = run_confirmed(prepared)
            observed = result.stdout.decode("utf-8", errors="strict").strip()
            if result.returncode != 0 or observed != expected:
                raise AssertionError(
                    f"{role} ancestor metadata mismatch: rc={result.returncode} "
                    f"stdout={result.stdout!r} stderr={result.stderr!r} expected={expected!r}"
                )
        return True
    finally:
        shutil.rmtree(root)


def external_provider_bundle_preserves_metadata_boundary() -> bool:
    if sys.platform != "darwin":
        return None
    root, job, stage, _checkout, _ambient = fixture("bundle-metadata")
    try:
        image_dir = root / "external-provider"
        image_dir.mkdir(mode=0o700)
        target = image_dir / "provider"
        sibling = image_dir / "private-sentinel"
        sibling.write_text("synthetic private sibling\n", encoding="utf-8")
        source = root / "bundle-probe.c"
        source.write_text(r'''
#include <CoreFoundation/CoreFoundation.h>
#include <Security/Security.h>
#include <dirent.h>
#include <errno.h>
#include <stdio.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>

static int denied(void) { return errno == EACCES || errno == EPERM; }
int main(int argc, char **argv) {
    if (argc != 3) return 90;
    struct stat st;
    int parent_stat = stat(argv[1], &st) == 0;
    DIR *dir = opendir(argv[1]);
    int listing_denied = !dir && denied();
    if (dir) closedir(dir);
    FILE *file = fopen(argv[2], "r");
    int sibling_read_denied = !file && denied();
    if (file) fclose(file);
    int sibling_stat_denied = stat(argv[2], &st) == -1 && denied();
    pid_t child = fork();
    if (child < 0) return 91;
    if (!child) _exit(stat(argv[1], &st) == 0 ? 0 : 1);
    int status;
    if (waitpid(child, &status, 0) != child || !WIFEXITED(status)) return 92;
    int fork_stat = WEXITSTATUS(status) == 0;
    child = fork();
    if (child < 0) return 93;
    if (!child) {
        if (!freopen("/dev/null", "w", stderr)) _exit(94);
        execl("/usr/bin/stat", "stat", "-f", "%i", argv[1], (char *)NULL);
        _exit(95);
    }
    if (waitpid(child, &status, 0) != child || !WIFEXITED(status)) return 96;
    int exec_stat_denied = WEXITSTATUS(status) == 1;
    CFBundleRef bundle = CFBundleGetMainBundle();
    CFDictionaryRef info = bundle ? CFBundleGetInfoDictionary(bundle) : NULL;
    SecPolicyRef policy = SecPolicyCreateSSL(true, CFSTR("example.com"));
    printf("{\"parent_stat\":%d,\"listing_denied\":%d,"
           "\"sibling_read_denied\":%d,\"sibling_stat_denied\":%d,"
           "\"fork_stat\":%d,\"exec_stat_denied\":%d,"
           "\"bundle\":%d,\"info\":%d,\"policy\":%d}\n",
           parent_stat, listing_denied, sibling_read_denied, sibling_stat_denied,
           fork_stat, exec_stat_denied, bundle != NULL, info != NULL, policy != NULL);
    if (policy) CFRelease(policy);
    return 0;
}
''', encoding="utf-8")
        subprocess.run(
            ["/usr/bin/clang", "-Wall", "-Wextra", "-Werror", "-framework",
             "CoreFoundation", "-framework", "Security", str(source), "-o", str(target)],
            check=True, capture_output=True, timeout=30,
        )
        target.chmod(0o700)
        for role, allowed in ((MODULE.ROLE_SELF_VERIFY, 0), (MODULE.ROLE_PROVIDER, 1)):
            role_job = root / role
            role_job.mkdir(mode=0o700)
            role_stage = role_job / "stage-001"
            role_stage.mkdir(mode=0o700)
            prepared = prepare(
                role_job, role_stage, target,
                [str(target), str(image_dir), str(sibling)],
                {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}, role=role,
            )
            confirmed = MODULE.confirm_contained_launch(prepared)
            result = subprocess.run(
                confirmed.argv, cwd=confirmed.cwd, env=confirmed.environment,
                capture_output=True, text=True, timeout=15,
            )
            expected = {
                "parent_stat": allowed, "listing_denied": 1,
                "sibling_read_denied": 1, "sibling_stat_denied": 1,
                "fork_stat": allowed, "exec_stat_denied": 1,
                "bundle": allowed, "info": allowed, "policy": allowed,
            }
            if result.returncode != 0 or json.loads(result.stdout) != expected:
                raise AssertionError(f"{role}: {result!r}; expected {expected!r}")
        return True
    finally:
        shutil.rmtree(root)


def two_turn_native_boundary_enforces_stage_isolation_and_settings_reuse() -> bool:
    """Turn 2 in a repair conversation reuses settings while isolating the fresh stage."""
    if sys.platform != "darwin":
        return None
    root, job, stage1, checkout, ambient = fixture("two-turn-native")
    original_discovery = MODULE._discover_default_keychain
    try:
        stage2 = job / "stage-002"
        stage2.mkdir(mode=0o700)
        sibling = root / "sibling"
        sibling.mkdir(mode=0o700)
        git = checkout / ".git"
        git.mkdir(mode=0o700)

        # Baseline files that must remain unmodified outside current stage
        prior_candidate = stage1 / "candidate.py"
        prior_candidate.write_text("turn-1 candidate\n", encoding="utf-8")
        checkout_file = checkout / "source.txt"
        checkout_file.write_text("checkout content\n", encoding="utf-8")
        git_file = git / "config"
        git_file.write_text("git content\n", encoding="utf-8")
        sibling_file = sibling / "unrelated.txt"
        sibling_file.write_text("sibling content\n", encoding="utf-8")
        ambient_file = ambient / "secret.txt"
        ambient_file.write_text("ambient content\n", encoding="utf-8")

        current_candidate = stage2 / "candidate.py"

        # Synthetic locator and keychain fixture
        synthetic_keychain = root / "synthetic-default.keychain-db"
        synthetic_keychain.write_bytes(b"two-turn synthetic keychain\n")
        synthetic_keychain.chmod(0o600)
        MODULE._discover_default_keychain = lambda: MODULE._bind_keychain(synthetic_keychain)

        python = CLT_PYTHON_EXECUTABLE
        selectors = ({"kind": "file", "path": "candidate.py"},)
        max_cycles = 2

        # Turn 1: initial attempt in stage-001
        prepared1 = MODULE.prepare_contained_launch(
            role=MODULE.ROLE_PROVIDER,
            network_policy=MODULE.NETWORK_DENY_ALL,
            job_dir=job,
            attempt=1,
            stage_dir=stage1,
            target_executable=python,
            target_argv=[python, "-I", "-S", "-B", "-c", "pass"],
            child_environment={"PATH": "/usr/bin:/bin"},
            allow_keychain=True,
            provider_max_cycles=max_cycles,
            provider_write_selectors=selectors,
        )
        res1 = run_confirmed(prepared1)
        if res1.returncode != 0:
            raise AssertionError(f"turn 1 launch failed: rc={res1.returncode} stdout={res1.stdout!r} stderr={res1.stderr!r}")

        # Turn 2: probe current stage authorized edit and outside read/write denial
        probe = (
            "import errno, json, sys\n"
            "from pathlib import Path\n"
            "results = {}\n"
            "try:\n"
            "    Path(sys.argv[1]).write_text('turn-2 candidate\\n', encoding='utf-8')\n"
            "    results['current_stage_write'] = 'ALLOWED'\n"
            "except OSError as exc:\n"
            "    results['current_stage_write'] = f'DENIED:{exc.errno}'\n"
            "for key, idx in [('prior_stage_read', 2), ('checkout_read', 3), ('git_read', 4), ('sibling_read', 5), ('ambient_read', 6)]:\n"
            "    try:\n"
            "        Path(sys.argv[idx]).read_text(encoding='utf-8')\n"
            "        results[key] = 'ALLOWED'\n"
            "    except OSError as exc:\n"
            "        results[key] = f'DENIED:{exc.errno}'\n"
            "try:\n"
            "    Path(sys.argv[2]).write_text('tampered\\n', encoding='utf-8')\n"
            "    results['prior_stage_write'] = 'ALLOWED'\n"
            "except OSError as exc:\n"
            "    results['prior_stage_write'] = f'DENIED:{exc.errno}'\n"
            "print(json.dumps(results))\n"
        )

        prepared2 = MODULE.prepare_contained_launch(
            role=MODULE.ROLE_PROVIDER,
            network_policy=MODULE.NETWORK_DENY_ALL,
            job_dir=job,
            attempt=2,
            stage_dir=stage2,
            target_executable=python,
            target_argv=[
                python, "-I", "-S", "-B", "-c", probe,
                str(current_candidate), str(prior_candidate), str(checkout_file),
                str(git_file), str(sibling_file), str(ambient_file),
            ],
            child_environment={"PATH": "/usr/bin:/bin"},
            allow_keychain=True,
            provider_max_cycles=max_cycles,
            provider_write_selectors=selectors,
        )

        # Assert same-conversation settings/preference reuse
        if (
            prepared1.provider_settings is None
            or prepared1.provider_settings != prepared2.provider_settings
            or prepared1.keychain_preferences is None
            or prepared1.keychain_preferences != prepared2.keychain_preferences
            or prepared1.private_home.path != prepared2.private_home.path
        ):
            raise AssertionError("turn 2 did not reuse exact turn 1 provider settings and preferences")

        res2 = run_confirmed(prepared2)
        if res2.returncode != 0:
            raise AssertionError(f"turn 2 launch failed: rc={res2.returncode} stdout={res2.stdout!r} stderr={res2.stderr!r}")

        results = json.loads(res2.stdout.decode("utf-8").strip())
        expected = {
            "current_stage_write": "ALLOWED",
            "prior_stage_read": f"DENIED:{errno.EPERM}",
            "prior_stage_write": f"DENIED:{errno.EPERM}",
            "checkout_read": f"DENIED:{errno.EPERM}",
            "git_read": f"DENIED:{errno.EPERM}",
            "sibling_read": f"DENIED:{errno.EPERM}",
            "ambient_read": f"DENIED:{errno.EPERM}",
        }
        if results != expected:
            raise AssertionError(f"isolation mismatch: results={results!r}; expected={expected!r}")

        # Assert filesystem integrity
        if current_candidate.read_text(encoding="utf-8") != "turn-2 candidate\n":
            raise AssertionError("current stage authorized candidate write missing")
        if prior_candidate.read_text(encoding="utf-8") != "turn-1 candidate\n":
            raise AssertionError("prior stage candidate was modified")
        if checkout_file.read_text(encoding="utf-8") != "checkout content\n":
            raise AssertionError("checkout file was modified")
        if git_file.read_text(encoding="utf-8") != "git content\n":
            raise AssertionError("git file was modified")
        if sibling_file.read_text(encoding="utf-8") != "sibling content\n":
            raise AssertionError("sibling file was modified")
        if ambient_file.read_text(encoding="utf-8") != "ambient content\n":
            raise AssertionError("ambient file was modified")
        return True
    finally:
        MODULE._discover_default_keychain = original_discovery
        shutil.rmtree(root)


check("provider parent metadata is the only safely escaped role-specific read delta", provider_parent_metadata_rule_is_exact)
check("provider HOME ancestor metadata is exact, non-readable, and removed by another image", provider_home_ancestor_metadata_is_image_bound)
check("external provider SSL policy needs only image-bound parent metadata", external_provider_bundle_preserves_metadata_boundary)
check("profile, role, HOME, TMP, and exact target are launch-bound", profile_and_environment_are_private)
check("invalid network and self-verification provider authority fail closed", role_and_network_policy_fail_closed)
check("Keychain helper exception is exact, provider-only, and network-free", security_helper_keychain_exception_is_exact_and_provider_only)
check("profile identity drift fails before native launch", binding_drift_fails_closed)
check("exact read-only runtime input drift fails before native launch", runtime_input_drift_fails_closed)
check("private generated DefaultKeychain preferences create once and reject repair drift", keychain_preferences_create_once_and_reject_drift)
check("precomputed provider settings bind all repair stages and reject drift", provider_settings_are_precomputed_private_and_rebound)
check("provider settings leaf stays immutable while ordinary HOME state remains writable", provider_settings_leaf_is_immutable_but_home_state_stays_writable)
check("provider settings rule targets, stages, and failure labels stay bounded", provider_settings_rule_targets_and_failures_are_exact)
check("default keychain identity is metadata-only and allows content churn only", keychain_binding_is_metadata_only_and_rebinds_identity)
check("Keychain read authority is helper-only and self-verification has none", keychain_policy_is_helper_only_and_self_verify_never_discovers)
check("synthetic default-Keychain locator is closed, bounded, and strict", keychain_locator_is_bounded_closed_and_strict)
check("locator cleanup reaps descendants after leader exit on timeout and apparent success", keychain_locator_reaps_leaderless_descendants)
check("locator cleanup diagnostics report sanitized condition labels on failure", keychain_locator_reap_diagnostics_report_sanitized_conditions)
check("private Keychain preference publication failures are sanitized before launch", private_keychain_publication_failures_are_sanitized)
check("self-verification skips all Keychain discovery and preference state", self_verify_never_runs_keychain_discovery)
check("unsupported hosts fail before creating containment state", unsupported_host_fails_before_creation)
check("native stage boundary denies checkout, Git, HOME, siblings, and local deputies", native_boundary_is_enforced)
check("Apple localhost token denies IPv4, IPv6, and interface-local owned listeners", localhost_predicate_blocks_owned_local_endpoints)
check("provider TLS and keychain process-path profile parses without using credentials", keychain_filter_profile_parses_without_keychain_access)
check("process-path authority survives fork and is removed by distinct exec without service access", process_path_filters_keychain_and_mdns_without_service_access)
check("provider resolver and listener authority is image-bound while self-verification stays denied", provider_listener_authority_is_image_bound_and_self_verify_denied)
check("canonical self-verification Python runs with a fixed credential-free environment", canonical_self_verify_python_executes)
check("process-group cleanup binds and drains the exact Darwin session", identity_safe_group_cleanup)
check("contained cleanup refuses an unbound process group", contained_cleanup_never_falls_back_to_unbound_group_signal)
check("integrated scoped launch rejects stage drift, outside paths, and inherited descriptors", integrated_stage_rebind_and_outside_write_denial)
check("two-turn native boundary enforces fresh stage isolation and settings reuse", two_turn_native_boundary_enforces_stage_isolation_and_settings_reuse)

print(f"provider containment: {passed} passed, {failed} failed, {skipped} skipped")
raise SystemExit(1 if failed else 0)
