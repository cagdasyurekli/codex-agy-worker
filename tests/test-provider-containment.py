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
    )


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
    """Return the kernel image path used by the Apple-shipped Python launcher."""
    script = (
        "import ctypes, os; b=ctypes.create_string_buffer(4096); "
        "p=ctypes.CDLL('/usr/lib/libproc.dylib').proc_pidpath(os.getpid(),b,len(b)); "
        "print(b.value.decode() if p > 0 else '')"
    )
    result = subprocess.run(
        ["/usr/bin/python3", "-I", "-S", "-B", "-c", script],
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
    ).decode("utf-8")
    helper = """(with-filter (process-path \"/usr/bin/security\")
  (allow mach-lookup
    (global-name \"com.apple.SecurityServer\")
    (global-name \"com.apple.securityd.xpc\")
    (global-name \"com.apple.securityd.general\")
    (global-name \"com.apple.trustd\")
    (global-name \"com.apple.trustd.agent\")))
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
            "/usr/bin/python3", "-I", "-S", "-B", "-c",
            f"from pathlib import Path; Path({str(output)!r}).write_text('ok')",
        ]
        prepared = prepare(
            job, stage, "/usr/bin/python3", argv,
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
        python = "/usr/bin/python3"
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
        if "inherited_fd" in locals() and inherited_fd >= 0:
            os.close(inherited_fd)
        shutil.rmtree(root)


check("profile, role, HOME, TMP, and exact target are launch-bound", profile_and_environment_are_private)
check("invalid network and self-verification provider authority fail closed", role_and_network_policy_fail_closed)
check("Keychain helper exception is exact, provider-only, and network-free", security_helper_keychain_exception_is_exact_and_provider_only)
check("profile identity drift fails before native launch", binding_drift_fails_closed)
check("exact read-only runtime input drift fails before native launch", runtime_input_drift_fails_closed)
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

print(f"provider containment: {passed} passed, {failed} failed, {skipped} skipped")
raise SystemExit(1 if failed else 0)
