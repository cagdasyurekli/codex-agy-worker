#!/usr/bin/env python3
"""Declarative canonical stage manifest for offline CI execution, timing, and sharding."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Any, NamedTuple, Sequence

sys.dont_write_bytecode = True


class Stage(NamedTuple):
    id: str
    announcement: str
    shard: str
    argv: tuple[str, ...]
    receipt_metadata: dict[str, Any]


STAGES: tuple[Stage, ...] = (
    Stage(
        "diff-hygiene",
        "working-tree diff hygiene",
        "other-a",
        ("bash", "scripts/ci-worktree-check.sh"),
        {"receipt_id": "diff-hygiene"},
    ),
    Stage(
        "shell-syntax",
        "shell syntax",
        "other-a",
        (
            "bash",
            "-c",
            "for file in ./*.sh conformance/*.sh scripts/*.sh tests/*.sh skills/*/scripts/*.sh skills/*/runtime/*.sh; do bash -n \"$file\" || exit 1; done",
        ),
        {"receipt_id": "shell-syntax"},
    ),
    Stage(
        "python-syntax",
        "Python syntax",
        "other-a",
        (
            "/usr/bin/python3",
            "-I",
            "-S",
            "-B",
            "-c",
            "import glob, os, py_compile, sys\n"
            "sys.pycache_prefix = os.environ['AGY_WORKER_CI_PYCACHE_DIR']\n"
            "[py_compile.compile(f, doraise=True) for f in sorted(set(glob.glob('conformance/v1/*.py') + glob.glob('scripts/*.py') + glob.glob('skills/*/runtime/scripts/*.py')))]",
        ),
        {"receipt_id": "python-syntax"},
    ),
    Stage(
        "qa-gate",
        "qa-gate suite",
        "other-b",
        ("./tests/test-qa-gate.sh",),
        {"receipt_id": "qa-gate"},
    ),
    Stage(
        "evidence-receipt",
        "Evidence Receipt v1 suite",
        "other-b",
        ("./tests/test-evidence-receipt.sh",),
        {"receipt_id": "evidence-receipt"},
    ),
    Stage(
        "evidence-report",
        "Evidence Report suite",
        "other-b",
        ("./tests/test-evidence-report.sh",),
        {"receipt_id": "evidence-report"},
    ),
    Stage(
        "offline-benchmark",
        "offline benchmark suite",
        "other-b",
        ("/usr/bin/python3", "-I", "-S", "-B", "tests/test-benchmark.py"),
        {"receipt_id": "offline-benchmark"},
    ),
    Stage(
        "swebench-workflow-study",
        "SWE-bench workflow study suite",
        "other-b",
        ("/usr/bin/python3", "-I", "-S", "-B", "tests/test-swebench-workflow-study.py"),
        {"receipt_id": "swebench-workflow-study"},
    ),
    Stage(
        "persona-evidence",
        "persona evidence registry suite",
        "other-b",
        ("/usr/bin/python3", "-I", "-S", "-B", "tests/test-persona-evidence.py"),
        {"receipt_id": "persona-evidence"},
    ),
    Stage(
        "job-lifecycle",
        "local job lifecycle suite",
        "other-a",
        ("/usr/bin/python3", "-I", "-S", "-B", "tests/test-job-lifecycle.py"),
        {"receipt_id": "job-lifecycle"},
    ),
    Stage(
        "workload-profiles",
        "data-only workload profiles suite",
        "other-b",
        ("/usr/bin/python3", "-I", "-S", "-B", "tests/test-workload-profiles.py"),
        {"receipt_id": "workload-profiles"},
    ),
    Stage(
        "dispatcher",
        "dispatcher suite",
        "dispatcher",
        ("./tests/test-agy-worker.sh",),
        {"receipt_id": "dispatcher"},
    ),
    Stage(
        "dispatcher-remediation",
        "dispatcher remediation suite",
        "dispatcher-remediation",
        ("/usr/bin/python3", "-I", "-S", "-B", "tests/test-agy-worker-remediation.py"),
        {"receipt_id": "dispatcher-remediation"},
    ),
    Stage(
        "updater",
        "updater suite",
        "other-a",
        ("./tests/test-update.sh",),
        {"receipt_id": "updater"},
    ),
    Stage(
        "adoption-measurement",
        "adoption measurement suite",
        "other-b",
        ("/usr/bin/python3", "-I", "-S", "-B", "tests/test-adoption-measurement.py"),
        {"receipt_id": "adoption-measurement"},
    ),
    Stage(
        "update-notifier",
        "local update notifier suite",
        "other-b",
        ("/usr/bin/python3", "-I", "-S", "-B", "tests/test-update-notifier.py"),
        {"receipt_id": "update-notifier"},
    ),
    Stage(
        "version-attestation-runner",
        "canonical version attestation runner",
        "other-b",
        ("/usr/bin/python3", "-I", "-S", "-B", "tests/test-version-attestation-runner.py"),
        {"receipt_id": "version-attestation-runner"},
    ),
    Stage(
        "version-bootstrap-preflight",
        "repository-only version bootstrap runtime preflight",
        "other-b",
        (
            "/usr/bin/python3",
            "-I",
            "-S",
            "-B",
            "-c",
            "import sys\nif not (\n    sys.implementation.name == \"cpython\"\n    and sys.version_info[:2] == (3, 9)\n    and sys.flags.isolated == 1\n    and sys.flags.no_site == 1\n    and sys.flags.dont_write_bytecode == 1\n    and sys.flags.ignore_environment == 1\n):\n    raise SystemExit('repository-only version bootstrap requires CPython 3.9 with -I -S -B')\n",
        ),
        {"receipt_id": "version-bootstrap-preflight"},
    ),
    Stage(
        "version-bootstrap-runner",
        "repository-only version bootstrap runner",
        "other-a",
        ("/usr/bin/python3", "-I", "-S", "-B", "tests/test-version-bootstrap-runner.py"),
        {"receipt_id": "version-bootstrap-runner"},
    ),
    Stage(
        "version-initial-bootstrap-runner",
        "repository-only version initial bootstrap runner",
        "other-a",
        ("/usr/bin/python3", "-I", "-S", "-B", "tests/test-version-initial-bootstrap-runner.py"),
        {"receipt_id": "version-initial-bootstrap-runner"},
    ),
    Stage(
        "version-attestation-harness",
        "version attestation mutation harness",
        "other-b",
        ("/usr/bin/python3", "-I", "-S", "-B", "tests/test-version-attestation-harness.py"),
        {"receipt_id": "version-attestation-harness"},
    ),
    Stage(
        "models-attestation-runner",
        "canonical models inventory attestation runner",
        "other-a",
        ("/usr/bin/python3", "-I", "-S", "-B", "tests/test-models-attestation-runner.py"),
        {"receipt_id": "models-attestation-runner"},
    ),
    Stage(
        "models-capture-runner",
        "explicit-account models capture runner",
        "other-b",
        ("/usr/bin/python3", "-I", "-S", "-B", "tests/test-models-capture-runner.py"),
        {"receipt_id": "models-capture-runner"},
    ),
    Stage(
        "models-capture-profile",
        "explicit-account models capture profile builder",
        "other-a",
        ("/usr/bin/python3", "-I", "-S", "-B", "tests/test-models-capture-profile.py"),
        {"receipt_id": "models-capture-profile"},
    ),
    Stage(
        "models-capture-1-1-22-version-evidence",
        "fixed 1.1.22 models capture version evidence",
        "other-a",
        ("/usr/bin/python3", "-I", "-S", "-B", "tests/test-models-capture-1-1-22-version-evidence.py"),
        {"receipt_id": "models-capture-1-1-22-version-evidence"},
    ),
    Stage(
        "models-capture-1-1-22-profile",
        "fixed 1.1.22 models capture profile builder",
        "other-a",
        ("/usr/bin/python3", "-I", "-S", "-B", "tests/test-models-capture-1-1-22-profile.py"),
        {"receipt_id": "models-capture-1-1-22-profile"},
    ),
    Stage(
        "models-capture-1-1-22-runner",
        "fixed 1.1.22 models capture runner",
        "other-a",
        ("/usr/bin/python3", "-I", "-S", "-B", "tests/test-models-capture-1-1-22-runner.py"),
        {"receipt_id": "models-capture-1-1-22-runner"},
    ),
    Stage(
        "models-capture-1-1-22-reprofile",
        "fixed 1.1.22 models capture reprofile adapter",
        "other-b",
        ("/usr/bin/python3", "-I", "-S", "-B", "tests/test-models-capture-1-1-22-reprofile.py"),
        {"receipt_id": "models-capture-1-1-22-reprofile"},
    ),
    Stage(
        "models-capture-1-1-22-classifier",
        "fixed 1.1.22 models capture failure classifier",
        "other-b",
        ("/usr/bin/python3", "-I", "-S", "-B", "tests/test-models-capture-1-1-22-classifier.py"),
        {"receipt_id": "models-capture-1-1-22-classifier"},
    ),
    Stage(
        "agy-1-1-22-activation",
        "1.1.22 activation binding",
        "other-a",
        ("/usr/bin/python3", "-I", "-S", "-B", "tests/test-agy-1-1-22-activation.py"),
        {"receipt_id": "agy-1-1-22-activation"},
    ),
    Stage(
        "reporting",
        "reporting suite",
        "other-a",
        ("./tests/test-reporting.sh",),
        {"receipt_id": "reporting"},
    ),
    Stage(
        "feedback-triage",
        "feedback triage suite",
        "other-a",
        ("/usr/bin/python3", "-I", "-S", "-B", "tests/test-feedback-triage.py"),
        {"receipt_id": "feedback-triage"},
    ),
    Stage(
        "model-intelligence",
        "Model Intelligence v1 suite",
        "other-a",
        ("/usr/bin/python3", "-I", "-S", "-B", "tests/test-model-intelligence.py"),
        {"receipt_id": "model-intelligence"},
    ),
    Stage(
        "model-evidence-campaign",
        "Model Evidence Campaign suite",
        "other-a",
        (
            "/usr/bin/python3",
            "-I",
            "-S",
            "-B",
            "tests/test-model-evidence-campaign.py",
        ),
        {"receipt_id": "model-evidence-campaign"},
    ),
    Stage(
        "codex-usage-report",
        "Codex usage observation suite",
        "other-b",
        ("/usr/bin/python3", "-I", "-S", "-B", "tests/test-codex-usage-report.py"),
        {"receipt_id": "codex-usage-report"},
    ),
    Stage(
        "delegation-policy",
        "delegation policy suite",
        "other-b",
        ("/usr/bin/python3", "-I", "-S", "-B", "tests/test-delegation-policy.py"),
        {"receipt_id": "delegation-policy"},
    ),
    Stage(
        "workflow",
        "workflow facade suite",
        "other-a",
        ("/usr/bin/python3", "-I", "-S", "-B", "tests/test-workflow.py"),
        {"receipt_id": "workflow"},
    ),
    Stage(
        "packaging",
        "Codex distribution suite",
        "other-a",
        ("./tests/test-packaging.sh",),
        {"receipt_id": "packaging"},
    ),
    Stage(
        "doctor",
        "read-only doctor suite",
        "other-a",
        ("./tests/test-doctor.sh",),
        {"receipt_id": "doctor"},
    ),
    Stage(
        "conformance",
        "public gate conformance suite",
        "other-b",
        ("/usr/bin/python3", "-I", "-S", "-B", "tests/test-conformance.py"),
        {"receipt_id": "conformance"},
    ),
    Stage(
        "proof-demo",
        "starter proof suite",
        "other-a",
        ("./tests/test-proof-demo.sh",),
        {"receipt_id": "proof-demo"},
    ),
    Stage(
        "bytecode-hygiene",
        "repository bytecode hygiene",
        "other-b",
        (
            "/usr/bin/python3",
            "-I",
            "-S",
            "-B",
            "-c",
            "import os, sys\nfor root, dirs, files in os.walk('.'):\n    if '__pycache__' in dirs:\n        sys.stderr.write('ci offline: repository bytecode detected\\n')\n        sys.exit(1)\n    for f in files:\n        if f.endswith('.pyc') or f.endswith('.pyo'):\n            sys.stderr.write('ci offline: repository bytecode detected\\n')\n            sys.exit(1)\n",
        ),
        {"receipt_id": "bytecode-hygiene"},
    ),
)

STAGE_MAP: dict[str, str] = {stage.id: stage.announcement for stage in STAGES}
ANNOUNCE_MAP: dict[str, str] = {stage.announcement: stage.id for stage in STAGES}

SHARDS: dict[str, tuple[str, ...]] = {
    "dispatcher": tuple(s.id for s in STAGES if s.shard == "dispatcher"),
    "dispatcher-remediation": tuple(
        s.id for s in STAGES if s.shard == "dispatcher-remediation"
    ),
    "other-a": tuple(s.id for s in STAGES if s.shard == "other-a"),
    "other-b": tuple(s.id for s in STAGES if s.shard == "other-b"),
}


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def inventory_digest() -> str:
    serialized = [
        (s.id, s.announcement, s.shard, s.argv, s.receipt_metadata) for s in STAGES
    ]
    return hashlib.sha256(canonical_bytes(serialized)).hexdigest()


RECEIPT_V1_INVENTORY_DIGEST = "452ecfe85d9b5896280341aaef06bf54a58448d824af69329ab924cfcd2d240d"


def receipt_v1_inventory_digest() -> str:
    """Return the historical receipt-v1 digest over public stage identity only."""
    return RECEIPT_V1_INVENTORY_DIGEST


def stage_environment(
    base_env: dict[str, str], stage: Stage, pycache: Path
) -> dict[str, str]:
    """Isolate runner imports without changing ordinary suite bytecode probes."""
    env = dict(base_env)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.pop("PYTHONPYCACHEPREFIX", None)
    env.pop("AGY_WORKER_CI_PYCACHE_DIR", None)
    if stage.id == "python-syntax":
        env["AGY_WORKER_CI_PYCACHE_DIR"] = str(pycache)
    return env


def execute_stage(stage: Stage, repo_root: Path, env: dict[str, str]) -> int:
    return subprocess.run(
        stage.argv,
        cwd=str(repo_root),
        env=env,
        stdin=subprocess.DEVNULL,
        check=False,
    ).returncode


def run_stages(
    repo_root: Path,
    target_shard: str | None = None,
    timing_nonce: str | None = None,
    shard_nonce: str | None = None,
) -> int:
    if target_shard is not None and target_shard not in SHARDS:
        sys.stderr.write("ci offline: rejected arguments\n")
        return 2

    matching_stages = [
        stage for stage in STAGES if target_shard is None or stage.shard == target_shard
    ]

    pycache = Path(tempfile.mkdtemp(prefix="agyworker-ci-pycache."))
    try:
        base_env = dict(os.environ)

        for stage in matching_stages:
            sys.stdout.write(f"==> {stage.announcement}\n")
            if timing_nonce:
                sys.stdout.write(
                    f"@@agy-worker-ci-timing:{timing_nonce}:{stage.announcement}\n"
                )
            if shard_nonce:
                sys.stdout.write(
                    f"@@agy-worker-ci-shard:{shard_nonce}:{stage.announcement}\n"
                )
            sys.stdout.flush()

            returncode = execute_stage(
                stage, repo_root, stage_environment(base_env, stage, pycache)
            )
            if returncode != 0:
                return returncode
        return 0
    finally:
        shutil.rmtree(pycache, ignore_errors=True)


def main(argv: Sequence[str]) -> int:
    repo_root = Path(__file__).resolve().parent.parent
    target_shard: str | None = None
    timing_nonce: str | None = None
    shard_nonce: str | None = None

    idx = 1
    while idx < len(argv):
        arg = argv[idx]
        if arg == "--shard" and idx + 1 < len(argv):
            target_shard = argv[idx + 1]
            idx += 2
        elif arg.startswith("--shard="):
            target_shard = arg.partition("=")[2]
            idx += 1
        elif arg == "--shard-child" and idx + 2 < len(argv):
            shard_nonce = argv[idx + 1]
            target_shard = argv[idx + 2]
            idx += 3
        elif arg == "--timing-child" and idx + 1 < len(argv):
            timing_nonce = argv[idx + 1]
            idx += 2
        elif arg in ("run", "run-stages"):
            idx += 1
        else:
            sys.stderr.write("ci offline: rejected arguments\n")
            return 2

    return run_stages(repo_root, target_shard, timing_nonce, shard_nonce)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
