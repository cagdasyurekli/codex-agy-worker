#!/usr/bin/env python3
"""Fixed-profile, snapshot-backed ``agy models`` inventory attestation.

The production interface accepts only ``--attest-models`` and a bounded strict
JSON profile on stdin.  It executes exactly one logical ``[source, "models"]``
call with the already version-attested snapshot as the actual executable.  The
offline interface uses synthetic evidence only.
"""

from __future__ import annotations

import ast
import dataclasses
import hashlib
import importlib.util
import json
import os
import selectors
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence


sys.dont_write_bytecode = True

VERSION_RUNNER_BYTES = 69_242
VERSION_RUNNER_SHA256 = "0e2632c2de1dc2651693dce942429b3219d551eb5a979aa2d8d273ee0aa95d6b"
INVENTORY_PARSER_BYTES = 3_652
INVENTORY_PARSER_SHA256 = "824fc35b7c87df61a437b5c93e508b6caf5759626b004e0f82acd8f903eadd18"
EXPECTED_NORMALIZED_SHA256 = "8d46bcac6b8f27995635d91dc6f5a0e549d351e707efe11a82d8b6593fe12daf"
EXPECTED_VERSION_BINDING_SHA256 = "72d0bba6040b46109f6968528697579dd1dbe7fdae949e68fb22e6f058452ea2"
EXPECTED_STDERR_BYTES = 29
EXPECTED_STDERR_SHA256 = "53f588bc9a928f4a66908deacaca57dddc7e7ce177a0cc3586b5a501be26e1e8"
PROFILE_LIMIT = 16_384
STREAM_LIMIT = 64 * 1024
WALL_SECONDS = 25.0
PRIVATE_DIRECTORY_NAMES = (
    "cwd",
    "home",
    "tmp",
    "xdg-config",
    "xdg-cache",
    "xdg-state",
)
PRODUCTION_AST_SHA256 = {
    "ModelsProfile": "819b2a1e9acc5953bc89db8e17a479cec748e13c67faa1feb912b9af944e98ce",
    "_canonical_json": "fabcd67b48b36dd92128417c318ccecdd1afe85e1373ef80f1e51657032a3255",
    "_source_bytes": "73af25ae16ae24028b6735807f8d4d09d4755f46eed29e952dd069e46f1811c5",
    "_canonical_sources": "23327104eef19d6ba010b62a144fa6336d5fcce65a8c25877bf6bc1c2bf2f792",
    "_validate_production_profile": "d604722e6d0c518b46a47c49af9c9008c75bb27096447ba1d7aab4ddfba6c268",
    "_validate_version_evidence": "cc1a4f0d89d28badbdfed02e88f47592af0938f2ca8e06611697d56d36984fef",
    "_capture": "49719973bcd0647fa8ec6ebb90a9934827b99ec4464fbbe9a1aabc71ecf0e657",
    "_validate_stderr": "0abacd437c8dc19669339c2c3273342fd0e9a2d0b20fd7a0817e69d971d64eff",
    "_private_directory_identity": "4a19c9e4b92eabdced7ca4d79fad544cc2c532964d8ad28ba19d87927fadb31d",
    "_revalidate_private_directories": "d50498ba403904036956f13335864ec1a38b0ca5a0b0eb0a4881c487edefa20a",
    "run_attestation": "7dadb4f0d3c5472e14c9b5e153b7c292eb1afd6594afab5cc4e6da3cdea4317e",
    "main": "0057f238185514dd4fb7238d0348031109f54da61ddbbed2297e3a073723ced4",
}
PROFILE_KEYS = frozenset(
    {
        "inventory_normalized_sha256",
        "snapshot_identity",
        "snapshot_path",
        "source_identity",
        "source_path",
        "source_sha256",
        "temp_parent",
        "version_binding_sha256",
        "version_root",
    }
)
VERSION_ROOT_FILES = frozenset(
    {
        "cwd",
        "home",
        "models-placeholder-never-present",
        "runner.py",
        "runner.py.sha256",
        "tmp",
        "version.binding.json",
        "version.binding.sha256",
        "version.stderr",
        "version.stdout",
        "version.summary.json",
        "xdg-cache",
        "xdg-config",
        "xdg-state",
    }
) - {"models-placeholder-never-present"}
OUTPUT_FILES = (
    "models_runner.py",
    "models_runner.py.sha256",
    "agy_inventory.py",
    "agy_inventory.py.sha256",
    "models.profile.json",
    "models.stdout",
    "models.stderr",
    "models.summary.json",
    "models.binding.json",
    "models.binding.sha256",
)


def _load_pinned_dependency(
    module_name: str, filename: str, expected_size: int, expected_sha256: str
) -> object:
    path = Path(__file__).resolve(strict=True).with_name(filename)
    value = path.lstat()
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    cloexec = getattr(os, "O_CLOEXEC", 0)
    if (
        path.parent != Path(__file__).resolve(strict=True).parent
        or not stat.S_ISREG(value.st_mode)
        or value.st_size != expected_size
        or stat.S_IMODE(value.st_mode) & 0o022
    ):
        raise RuntimeError("canonical dependency identity changed")
    descriptor = os.open(str(path), os.O_RDONLY | nofollow | cloexec)
    try:
        data = os.read(descriptor, expected_size + 1)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if (
        len(data) != expected_size
        or hashlib.sha256(data).hexdigest() != expected_sha256
        or (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns)
        != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    ):
        raise RuntimeError("canonical dependency bytes changed")
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("canonical dependency cannot be loaded")
    loaded = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = loaded
    spec.loader.exec_module(loaded)
    return loaded


version = _load_pinned_dependency(
    "_agy_models_version_runner",
    "version_attestation_runner.py",
    VERSION_RUNNER_BYTES,
    VERSION_RUNNER_SHA256,
)
inventory = _load_pinned_dependency(
    "_agy_models_inventory_parser",
    "agy_inventory.py",
    INVENTORY_PARSER_BYTES,
    INVENTORY_PARSER_SHA256,
)


class ModelsAttestationError(ValueError):
    """A fixed-profile models observation failed closed."""


class ModelsAttestationInterrupted(BaseException):
    def __init__(self, signum: int):
        super().__init__(signum)
        self.signum = signum


@dataclass(frozen=True)
class ModelsProfile:
    inventory_normalized_sha256: str
    snapshot_identity: version.FileIdentity
    snapshot_path: str
    source_identity: version.FileIdentity
    source_path: str
    source_sha256: str
    temp_parent: str
    version_binding_sha256: str
    version_root: str

    @classmethod
    def from_bytes(cls, data: bytes) -> "ModelsProfile":
        value = version._strict_json(data)
        if not isinstance(value, dict) or set(value) != PROFILE_KEYS:
            raise ModelsAttestationError("invalid models evidence profile")
        for key in ("snapshot_path", "source_path", "temp_parent", "version_root"):
            version._require_canonical_absolute(value[key])
        for key in (
            "inventory_normalized_sha256",
            "source_sha256",
            "version_binding_sha256",
        ):
            if not isinstance(value[key], str) or not version._is_sha256(value[key]):
                raise ModelsAttestationError("invalid models evidence profile")
        if value["inventory_normalized_sha256"] != EXPECTED_NORMALIZED_SHA256:
            raise ModelsAttestationError("inventory baseline is not reviewed")
        return cls(
            inventory_normalized_sha256=value["inventory_normalized_sha256"],
            snapshot_identity=version.FileIdentity.from_mapping(value["snapshot_identity"]),
            snapshot_path=value["snapshot_path"],
            source_identity=version.FileIdentity.from_mapping(value["source_identity"]),
            source_path=value["source_path"],
            source_sha256=value["source_sha256"],
            temp_parent=value["temp_parent"],
            version_binding_sha256=value["version_binding_sha256"],
            version_root=value["version_root"],
        )


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii") + b"\n"


def _source_bytes(path: Path, expected_size: int, expected_sha: str) -> bytes:
    canonical = path.resolve(strict=True)
    scripts = Path(__file__).resolve(strict=True).parent
    if canonical.parent != scripts or canonical != path:
        raise ModelsAttestationError("canonical dependency path changed")
    value = canonical.lstat()
    if (
        not stat.S_ISREG(value.st_mode)
        or value.st_size != expected_size
        or stat.S_IMODE(value.st_mode) & 0o022
    ):
        raise ModelsAttestationError("canonical dependency identity changed")
    descriptor = os.open(str(canonical), os.O_RDONLY | version.CLOEXEC | version.NOFOLLOW)
    try:
        data = os.read(descriptor, expected_size + 1)
        if len(data) != expected_size or os.read(descriptor, 1) != b"":
            raise ModelsAttestationError("canonical dependency size changed")
        if version.FileIdentity.from_stat(os.fstat(descriptor)) != version.FileIdentity.from_stat(value):
            raise ModelsAttestationError("canonical dependency changed while read")
    finally:
        os.close(descriptor)
    if hashlib.sha256(data).hexdigest() != expected_sha:
        raise ModelsAttestationError("canonical dependency digest changed")
    return data


def _canonical_sources(module_source: Optional[bytes] = None) -> tuple[bytes, bytes, bytes]:
    scripts = Path(__file__).resolve(strict=True).parent
    runner = Path(__file__).resolve(strict=True).read_bytes() if module_source is None else module_source
    if not runner or len(runner) > 128 * 1024 or b"\x00" in runner:
        raise ModelsAttestationError("models runner source is invalid")
    version_source = _source_bytes(
        scripts / "version_attestation_runner.py",
        VERSION_RUNNER_BYTES,
        VERSION_RUNNER_SHA256,
    )
    inventory_source = _source_bytes(
        scripts / "agy_inventory.py",
        INVENTORY_PARSER_BYTES,
        INVENTORY_PARSER_SHA256,
    )
    if Path(version.__file__).resolve(strict=True) != scripts / "version_attestation_runner.py":
        raise ModelsAttestationError("loaded version runner path changed")
    if Path(inventory.__file__).resolve(strict=True) != scripts / "agy_inventory.py":
        raise ModelsAttestationError("loaded inventory parser path changed")
    return runner, version_source, inventory_source


def validate_source_contract(data: bytes) -> dict[str, object]:
    try:
        text = data.decode("utf-8", "strict")
        tree = ast.parse(text, filename="<models-attestation-runner>", mode="exec")
    except (UnicodeDecodeError, SyntaxError) as exc:
        raise ModelsAttestationError("models runner source is invalid") from exc
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    classes = {node.name: node for node in tree.body if isinstance(node, ast.ClassDef)}
    production_nodes: dict[str, ast.AST] = {
        name: classes[name] if name == "ModelsProfile" else functions[name]
        for name in PRODUCTION_AST_SHA256
        if (name == "ModelsProfile" and name in classes)
        or (name != "ModelsProfile" and name in functions)
    }
    if set(production_nodes) != set(PRODUCTION_AST_SHA256):
        raise ModelsAttestationError("models production call graph is incomplete")
    for name, expected_sha256 in PRODUCTION_AST_SHA256.items():
        observed_sha256 = hashlib.sha256(
            ast.dump(production_nodes[name], include_attributes=False).encode("utf-8")
        ).hexdigest()
        if observed_sha256 != expected_sha256:
            raise ModelsAttestationError("models production call graph changed")
    run_node = functions.get("run_attestation")
    capture_node = functions.get("_capture")
    version_node = functions.get("_validate_version_evidence")
    production_node = functions.get("_validate_production_profile")
    private_directory_node = functions.get("_private_directory_identity")
    revalidate_directories_node = functions.get("_revalidate_private_directories")
    main_node = functions.get("main")
    if (
        run_node is None
        or capture_node is None
        or version_node is None
        or production_node is None
        or private_directory_node is None
        or revalidate_directories_node is None
        or main_node is None
    ):
        raise ModelsAttestationError("models source authority is incomplete")
    subprocess_launch_attributes = {
        "Popen",
        "call",
        "check_call",
        "check_output",
        "getoutput",
        "getstatusoutput",
        "run",
    }
    os_launch_attributes = {
        "fork",
        "forkpty",
        "popen",
        "posix_spawn",
        "posix_spawnp",
        "system",
    }
    def process_module_reference(node: ast.AST) -> tuple[str, str] | None:
        if not isinstance(node, ast.Attribute) or not isinstance(node.value, ast.Name):
            return None
        module = node.value.id
        attribute = node.attr
        if module == "subprocess" and attribute in subprocess_launch_attributes:
            return module, attribute
        if module == "os" and (
            attribute in os_launch_attributes
            or attribute.startswith(("exec", "spawn"))
        ):
            return module, attribute
        if module == "asyncio" and attribute.startswith("create_subprocess"):
            return module, attribute
        if module == "calls" and attribute == "popen":
            return module, attribute
        return None

    launch_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and process_module_reference(node.func) is not None
    ]
    if len(launch_calls) != 1:
        raise ModelsAttestationError("models runner must contain one child-launch authority")
    call = launch_calls[0]
    if call not in tuple(ast.walk(run_node)):
        raise ModelsAttestationError("models child launch moved outside its owner")
    process_module_imports = [
        item
        for node in tree.body
        if isinstance(node, ast.Import)
        for item in node.names
        if item.name in {"asyncio", "os", "subprocess"}
    ]
    if sorted((item.name, item.asname) for item in process_module_imports) != [
        ("os", None),
        ("subprocess", None),
    ]:
        raise ModelsAttestationError("models process module authority changed")
    parents = {
        id(child): parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id in {"os", "subprocess"}
            and node.attr in {"__dict__", "__getattribute__"}
        ):
            raise ModelsAttestationError("models dynamic process lookup changed")
        if isinstance(node, ast.ImportFrom) and node.module in {"os", "subprocess"}:
            if any(
                item.name in subprocess_launch_attributes
                or item.name in os_launch_attributes
                or item.name.startswith(("exec", "spawn"))
                for item in node.names
            ):
                raise ModelsAttestationError("models process alias authority changed")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in {"__import__", "eval", "exec"}:
                raise ModelsAttestationError("models dynamic launch authority changed")
            if (
                node.func.id == "getattr"
                and len(node.args) >= 2
                and isinstance(node.args[0], ast.Name)
                and node.args[0].id in {"os", "subprocess"}
                and isinstance(node.args[1], ast.Constant)
                and isinstance(node.args[1].value, str)
                and (
                    node.args[1].value in subprocess_launch_attributes
                    or node.args[1].value in os_launch_attributes
                    or node.args[1].value.startswith(("exec", "spawn"))
                )
            ):
                raise ModelsAttestationError("models dynamic launch authority changed")
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "import_module"
        ):
            raise ModelsAttestationError("models dynamic process import changed")
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Name)
            and isinstance(node.ctx, ast.Load)
            and node.id in {"os", "subprocess"}
        ):
            continue
        parent = parents[id(node)]
        if isinstance(parent, ast.Attribute) and parent.value is node:
            continue
        if (
            node.id == "os"
            and isinstance(parent, ast.Call)
            and isinstance(parent.func, ast.Name)
            and parent.func.id == "getattr"
            and len(parent.args) >= 2
            and parent.args[0] is node
            and isinstance(parent.args[1], ast.Constant)
            and parent.args[1].value in {"O_CLOEXEC", "O_NOFOLLOW"}
        ):
            continue
        raise ModelsAttestationError("models process module alias changed")
    capture_process_args = [
        item for item in capture_node.args.args if item.arg == "process"
    ]
    run_process_annotations = [
        node
        for node in ast.walk(run_node)
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id == "process"
    ]
    if len(capture_process_args) != 1 or len(run_process_annotations) != 1:
        raise ModelsAttestationError("models process typing authority changed")
    expected_popen_references = [
        node
        for annotation in (
            capture_process_args[0].annotation,
            run_process_annotations[0].annotation,
        )
        for node in ast.walk(annotation)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "subprocess"
        and node.attr == "Popen"
    ]
    observed_launch_references = [
        node
        for node in ast.walk(tree)
        if process_module_reference(node) is not None
    ]
    expected_launch_references = [*expected_popen_references, call.func]
    if (
        len(observed_launch_references) != len(expected_launch_references)
        or {id(node) for node in observed_launch_references}
        != {id(node) for node in expected_launch_references}
    ):
        raise ModelsAttestationError("models process alias authority changed")
    calls_popen_references = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "calls"
        and node.attr == "popen"
    ]
    if len(calls_popen_references) != 1 or calls_popen_references[0] is not call.func:
        raise ModelsAttestationError("models injected Popen authority changed")
    calls_loads = [
        node
        for node in ast.walk(run_node)
        if isinstance(node, ast.Name)
        and isinstance(node.ctx, ast.Load)
        and node.id == "calls"
    ]
    if len(calls_loads) != 4:
        raise ModelsAttestationError("models injected call authority changed")
    filesystem_mutation_attributes = {
        "chmod",
        "chown",
        "copy",
        "copy2",
        "copyfile",
        "link",
        "mkdir",
        "mkfifo",
        "mknod",
        "move",
        "open",
        "remove",
        "rename",
        "renames",
        "replace",
        "rmdir",
        "rmtree",
        "symlink",
        "symlink_to",
        "touch",
        "truncate",
        "unlink",
        "write_bytes",
        "write_text",
    }
    filesystem_mutations = [
        node
        for node in ast.walk(run_node)
        if isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Name) and node.func.id == "open")
            or (
                isinstance(node.func, ast.Attribute)
                and (
                    node.func.attr in filesystem_mutation_attributes
                    or (
                        isinstance(node.func.value, ast.Name)
                        and node.func.value.id == "shutil"
                    )
                )
            )
        )
    ]
    expected_filesystem_mutations = {
        "Call(func=Attribute(value=Name(id='os', ctx=Load()), attr='chmod', "
        "ctx=Load()), args=[Name(id='root', ctx=Load()), Constant(value=448)], "
        "keywords=[])",
        "Call(func=Attribute(value=Name(id='child', ctx=Load()), attr='mkdir', "
        "ctx=Load()), args=[], keywords=[keyword(arg='mode', "
        "value=Constant(value=448))])",
    }
    if (
        len(filesystem_mutations) != 2
        or {ast.dump(node) for node in filesystem_mutations}
        != expected_filesystem_mutations
    ):
        raise ModelsAttestationError("models filesystem mutation authority changed")
    launch_statements = [
        node
        for node in ast.walk(run_node)
        if isinstance(node, ast.Assign) and call in tuple(ast.walk(node))
    ]
    launch_owners = [
        node
        for node in ast.walk(run_node)
        if isinstance(node, ast.Try)
        and len(launch_statements) == 1
        and launch_statements[0] in node.body
    ]
    if len(launch_owners) != 1:
        raise ModelsAttestationError("models pre-spawn authority changed")
    launch_body = launch_owners[0].body
    launch_indexes = [
        index
        for index, statement in enumerate(launch_body)
        if call in tuple(ast.walk(statement))
    ]
    expected_revalidation = (
        "Call(func=Name(id='_revalidate_private_directories', ctx=Load()), "
        "args=[Name(id='root', ctx=Load()), "
        "Name(id='private_directory_identities', ctx=Load())], keywords=[])"
    )
    if (
        len(launch_indexes) != 1
        or not any(
            isinstance(statement, ast.Expr)
            and ast.dump(statement.value) == expected_revalidation
            and index < launch_indexes[0]
            for index, statement in enumerate(launch_body)
        )
        or "process_active = True" not in (ast.get_source_segment(text, launch_owners[0]) or "")
    ):
        raise ModelsAttestationError("models private directory revalidation moved")
    keywords = {item.arg: item.value for item in call.keywords if item.arg is not None}
    if not (
        isinstance(call.func, ast.Attribute)
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id == "calls"
        and call.func.attr == "popen"
        and len(call.args) == 1
        and len(call.keywords) == 7
        and set(keywords)
        == {
            "cwd",
            "env",
            "executable",
            "start_new_session",
            "stderr",
            "stdin",
            "stdout",
        }
        and isinstance(call.args[0], ast.Name)
        and call.args[0].id == "argv"
        and ast.dump(keywords.get("executable"))
        == "Attribute(value=Name(id='profile', ctx=Load()), attr='snapshot_path', ctx=Load())"
        and ast.dump(keywords.get("stdin"))
        == "Attribute(value=Name(id='subprocess', ctx=Load()), attr='DEVNULL', ctx=Load())"
        and ast.dump(keywords.get("stdout"))
        == "Attribute(value=Name(id='subprocess', ctx=Load()), attr='PIPE', ctx=Load())"
        and ast.dump(keywords.get("stderr"))
        == "Attribute(value=Name(id='subprocess', ctx=Load()), attr='PIPE', ctx=Load())"
        and ast.dump(keywords.get("cwd"))
        == "Call(func=Name(id='str', ctx=Load()), args=[BinOp(left=Name(id='root', ctx=Load()), op=Div(), right=Constant(value='cwd'))], keywords=[])"
        and isinstance(keywords.get("env"), ast.Name)
        and keywords["env"].id == "environment"
        and isinstance(keywords.get("start_new_session"), ast.Constant)
        and keywords["start_new_session"].value is True
    ):
        raise ModelsAttestationError("models Popen contract changed")
    environment_assignments = [
        node
        for node in ast.walk(run_node)
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == "environment"
    ]
    environment_loads = [
        node
        for node in ast.walk(run_node)
        if isinstance(node, ast.Name)
        and node.id == "environment"
        and isinstance(node.ctx, ast.Load)
    ]
    if len(environment_assignments) != 1 or environment_loads != [keywords["env"]]:
        raise ModelsAttestationError("models environment authority changed")
    environment_value = environment_assignments[0].value
    if not isinstance(environment_value, ast.Dict):
        raise ModelsAttestationError("models environment authority changed")
    environment_keys = (
        "HOME",
        "TMPDIR",
        "XDG_CONFIG_HOME",
        "XDG_CACHE_HOME",
        "XDG_STATE_HOME",
        "LANG",
        "LC_ALL",
        "NO_COLOR",
        "TERM",
        "PATH",
    )
    if tuple(
        item.value if isinstance(item, ast.Constant) and isinstance(item.value, str) else None
        for item in environment_value.keys
    ) != environment_keys:
        raise ModelsAttestationError("models environment authority changed")
    expected_private_values = {
        "HOME": "home",
        "TMPDIR": "tmp",
        "XDG_CONFIG_HOME": "xdg-config",
        "XDG_CACHE_HOME": "xdg-cache",
        "XDG_STATE_HOME": "xdg-state",
    }
    expected_literals = {
        "LANG": "C",
        "LC_ALL": "C",
        "NO_COLOR": "1",
        "TERM": "dumb",
        "PATH": "/usr/bin:/bin",
    }
    for key, value in zip(environment_keys, environment_value.values):
        if key in expected_private_values:
            expected = (
                "Call(func=Name(id='str', ctx=Load()), "
                "args=[BinOp(left=Name(id='root', ctx=Load()), op=Div(), "
                f"right=Constant(value={expected_private_values[key]!r}))], keywords=[])"
            )
            if ast.dump(value) != expected:
                raise ModelsAttestationError("models environment authority changed")
        elif not (
            isinstance(value, ast.Constant)
            and value.value == expected_literals[key]
        ):
            raise ModelsAttestationError("models environment authority changed")
    argv_assignments = [
        node
        for node in ast.walk(run_node)
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == "argv"
    ]
    expected_argv = "List(elts=[Attribute(value=Name(id='profile', ctx=Load()), attr='source_path', ctx=Load()), Constant(value='models')], ctx=Load())"
    if len(argv_assignments) != 1 or ast.dump(argv_assignments[0].value) != expected_argv:
        raise ModelsAttestationError("models logical argv changed")
    run_text = ast.get_source_segment(text, run_node) or ""
    capture_text = ast.get_source_segment(text, capture_node) or ""
    version_text = ast.get_source_segment(text, version_node) or ""
    production_text = ast.get_source_segment(text, production_node) or ""
    private_directory_text = ast.get_source_segment(text, private_directory_node) or ""
    revalidate_directories_text = (
        ast.get_source_segment(text, revalidate_directories_node) or ""
    )
    main_text = ast.get_source_segment(text, main_node) or ""
    required_run = (
        "deadline = started + WALL_SECONDS",
        "evidence = inventory.parse_inventory_bytes(stdout)",
        'profile_sha = publisher.publish("models.profile.json", exact_profile)',
        "stderr_sha = _validate_stderr(stderr, stderr_contract)",
        'publisher.publish("models.binding.sha256",',
    )
    if (
        any(marker not in run_text for marker in required_run)
        or "STREAM_LIMIT + 1 - len(captured)" not in capture_text
        or "hashlib.sha256(binding_bytes).hexdigest() != profile.version_binding_sha256"
        not in version_text
        or "or os.listdir(child)" not in version_text
        or "profile.version_binding_sha256 != EXPECTED_VERSION_BINDING_SHA256"
        not in production_text
        or "version.DIRECTORY | version.CLOEXEC | version.NOFOLLOW"
        not in private_directory_text
        or "observed.st_uid != os.getuid()" not in private_directory_text
        or "stat.S_IMODE(observed.st_mode) != 0o700" not in private_directory_text
        or "or os.listdir(descriptor)" not in private_directory_text
        or "return version.FileIdentity.from_stat(observed)" not in private_directory_text
        or "tuple(expected) != PRIVATE_DIRECTORY_NAMES"
        not in revalidate_directories_text
        or "_private_directory_identity(root / name) != expected[name]"
        not in revalidate_directories_text
        or "_validate_production_profile(profile)\n        result = run_attestation(profile)"
        in main_text
    ):
        raise ModelsAttestationError("models source authority changed")
    required_main = (
        'if list(argv) != ["--attest-models"]:',
        "lifecycle = version._acquire_lifecycle()",
        "version._activate_lifecycle(lifecycle)",
        "startup = version._production_startup_evaluation()",
        "diagnostic = version._startup_diagnostic(startup)",
        "data = version._read_stdin(lifecycle.controller)",
        "profile = ModelsProfile.from_bytes(data)",
        "_validate_production_profile(profile)",
        "process_owned=True",
    )
    if any(marker not in main_text for marker in required_main):
        raise ModelsAttestationError("models production main authority changed")
    assignment_nodes = {
        node.targets[0].id: node.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
    }
    if (
        ast.dump(assignment_nodes.get("EXPECTED_STDERR_BYTES"))
        != "Constant(value=29)"
        or ast.dump(assignment_nodes.get("EXPECTED_STDERR_SHA256"))
        != "Constant(value='53f588bc9a928f4a66908deacaca57dddc7e7ce177a0cc3586b5a501be26e1e8')"
        or ast.dump(assignment_nodes.get("STREAM_LIMIT"))
        != "BinOp(left=Constant(value=64), op=Mult(), right=Constant(value=1024))"
        or ast.dump(assignment_nodes.get("WALL_SECONDS")) != "Constant(value=25.0)"
    ):
        raise ModelsAttestationError("models fixed evidence constants changed")
    if not (
        main_text.index("if not startup.accepted:")
        < main_text.index("data = version._read_stdin(lifecycle.controller)")
        < main_text.index("profile = ModelsProfile.from_bytes(data)")
        < main_text.index("run_attestation(")
    ):
        raise ModelsAttestationError("models production main ordering changed")
    return {"byte_count": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def _validate_production_profile(profile: ModelsProfile) -> None:
    if profile.version_binding_sha256 != EXPECTED_VERSION_BINDING_SHA256:
        raise ModelsAttestationError("version binding is not the accepted production evidence")


def _validate_authority(profile: ModelsProfile) -> None:
    if os.path.dirname(profile.version_root) != profile.temp_parent:
        raise ModelsAttestationError("version evidence is outside its private parent")
    if not os.path.basename(profile.version_root).startswith("agy-version-recovery."):
        raise ModelsAttestationError("version evidence root name is invalid")
    repository = str(Path(__file__).resolve(strict=True).parents[1])
    if os.path.commonpath((profile.temp_parent, repository)) == repository:
        raise ModelsAttestationError("private evidence parent is inside the repository")
    parent = version._open_dir(profile.temp_parent)
    try:
        value = os.fstat(parent)
        if (
            not stat.S_ISDIR(value.st_mode)
            or value.st_uid != os.getuid()
            or stat.S_IMODE(value.st_mode) != 0o700
        ):
            raise ModelsAttestationError("private evidence parent is not owner-private")
    finally:
        os.close(parent)
    for identity, mode in (
        (profile.source_identity, 0o755),
        (profile.snapshot_identity, 0o500),
    ):
        if (
            identity.uid != os.getuid()
            or identity.mode != mode
            or identity.nlink != 1
            or identity.size <= 0
        ):
            raise ModelsAttestationError("executable identity policy is invalid")


def _validate_version_evidence(profile: ModelsProfile) -> None:
    _validate_authority(profile)
    descriptor = version._open_dir(profile.version_root)
    try:
        value = os.fstat(descriptor)
        if value.st_uid != os.getuid() or stat.S_IMODE(value.st_mode) != 0o700:
            raise ModelsAttestationError("version evidence root is not owner-private")
        if set(os.listdir(descriptor)) != VERSION_ROOT_FILES:
            raise ModelsAttestationError("version evidence root has an unexpected shape")
        for name in ("cwd", "home", "tmp", "xdg-cache", "xdg-config", "xdg-state"):
            child = os.open(
                name,
                os.O_RDONLY | version.DIRECTORY | version.CLOEXEC | version.NOFOLLOW,
                dir_fd=descriptor,
            )
            try:
                child_stat = os.fstat(child)
                if (
                    not stat.S_ISDIR(child_stat.st_mode)
                    or child_stat.st_uid != os.getuid()
                    or stat.S_IMODE(child_stat.st_mode) != 0o700
                    or os.listdir(child)
                ):
                    raise ModelsAttestationError("version evidence directory is invalid")
            finally:
                os.close(child)
        binding_bytes = version._read_at(descriptor, "version.binding.json", PROFILE_LIMIT)
        detached = version._read_at(descriptor, "version.binding.sha256", 128)
        if (
            hashlib.sha256(binding_bytes).hexdigest() != profile.version_binding_sha256
            or detached != (profile.version_binding_sha256 + "\n").encode("ascii")
        ):
            raise ModelsAttestationError("version binding digest changed")
        binding = version._strict_json(binding_bytes)
        if not isinstance(binding, dict):
            raise ModelsAttestationError("version binding is invalid")
        runner = binding.get("runner")
        snapshot = binding.get("snapshot")
        source = binding.get("source")
        observed = binding.get("version")
        limitations = binding.get("limitations")
        if (
            binding.get("claim") != "snapshot-version-recovery"
            or not isinstance(runner, dict)
            or runner.get("byte_count") != VERSION_RUNNER_BYTES
            or runner.get("sha256") != VERSION_RUNNER_SHA256
            or not isinstance(snapshot, dict)
            or snapshot.get("pre") != profile.snapshot_identity.as_dict()
            or snapshot.get("post") != profile.snapshot_identity.as_dict()
            or snapshot.get("sha256") != profile.source_sha256
            or not isinstance(source, dict)
            or source.get("pre") != profile.source_identity.as_dict()
            or source.get("post") != profile.source_identity.as_dict()
            or source.get("sha256") != profile.source_sha256
            or not isinstance(observed, dict)
            or observed.get("exit") != 0
            or observed.get("logical_argv") != [profile.source_path, "--version"]
            or observed.get("observed") != "1.1.11"
            or observed.get("popen_count") != 1
            or not isinstance(limitations, dict)
            or limitations.get("prior_inventory_executable_version_bound") is not False
        ):
            raise ModelsAttestationError("version binding claim is incompatible")
        runner_bytes = version._read_at(descriptor, "runner.py", 128 * 1024)
        runner_digest = version._read_at(descriptor, "runner.py.sha256", 128)
        if (
            len(runner_bytes) != VERSION_RUNNER_BYTES
            or hashlib.sha256(runner_bytes).hexdigest() != VERSION_RUNNER_SHA256
            or runner_digest != (VERSION_RUNNER_SHA256 + "\n").encode("ascii")
        ):
            raise ModelsAttestationError("persisted version runner changed")
        if version._read_at(descriptor, "version.stdout", 128) != b"1.1.11\n":
            raise ModelsAttestationError("version observation changed")
        if version._read_at(descriptor, "version.stderr", 128) != b"":
            raise ModelsAttestationError("version stderr changed")
        version._read_at(descriptor, "version.summary.json", PROFILE_LIMIT)
    finally:
        os.close(descriptor)


def _capture(
    process: subprocess.Popen[bytes],
    deadline: float,
    controller: Optional[version.SignalController] = None,
) -> tuple[bytes, bytes]:
    if process.stdout is None or process.stderr is None:
        raise ModelsAttestationError("models process did not expose bounded streams")
    stdout_descriptor = process.stdout.fileno()
    stderr_descriptor = process.stderr.fileno()
    buffers = {
        stdout_descriptor: (process.stdout, bytearray()),
        stderr_descriptor: (process.stderr, bytearray()),
    }
    for descriptor in buffers:
        os.set_blocking(descriptor, False)
    with selectors.DefaultSelector() as selector:
        for descriptor in buffers:
            selector.register(descriptor, selectors.EVENT_READ)
        while selector.get_map():
            if controller is not None:
                controller.poll()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ModelsAttestationError("models process timed out")
            for key, _mask in selector.select(min(remaining, 0.05)):
                if controller is not None:
                    controller.poll()
                stream, captured = buffers[key.fd]
                block = os.read(key.fd, min(8192, STREAM_LIMIT + 1 - len(captured)))
                if controller is not None:
                    controller.poll()
                if not block:
                    selector.unregister(key.fd)
                    stream.close()
                    continue
                captured.extend(block)
                if len(captured) > STREAM_LIMIT:
                    raise ModelsAttestationError("models output exceeded its bound")
    if controller is not None:
        controller.poll()
    return bytes(buffers[stdout_descriptor][1]), bytes(buffers[stderr_descriptor][1])


def _validate_stderr(
    raw: bytes,
    expected: tuple[int, str] = (EXPECTED_STDERR_BYTES, EXPECTED_STDERR_SHA256),
) -> str:
    expected_bytes, expected_sha256 = expected
    observed_sha256 = hashlib.sha256(raw).hexdigest()
    if (
        type(expected_bytes) is not int
        or expected_bytes < 0
        or not isinstance(expected_sha256, str)
        or not version._is_sha256(expected_sha256)
        or len(raw) != expected_bytes
        or observed_sha256 != expected_sha256
    ):
        raise ModelsAttestationError("models stderr differs from reviewed evidence")
    return observed_sha256


def _private_directory_identity(path: Path) -> version.FileIdentity:
    descriptor = os.open(
        str(path),
        os.O_RDONLY | version.DIRECTORY | version.CLOEXEC | version.NOFOLLOW,
    )
    try:
        observed = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(observed.st_mode)
            or observed.st_uid != os.getuid()
            or stat.S_IMODE(observed.st_mode) != 0o700
            or os.listdir(descriptor)
        ):
            raise ModelsAttestationError("private models directory changed")
        return version.FileIdentity.from_stat(observed)
    finally:
        os.close(descriptor)


def _revalidate_private_directories(
    root: Path, expected: dict[str, version.FileIdentity]
) -> None:
    if tuple(expected) != PRIVATE_DIRECTORY_NAMES:
        raise ModelsAttestationError("private models directory inventory changed")
    for name in PRIVATE_DIRECTORY_NAMES:
        if _private_directory_identity(root / name) != expected[name]:
            raise ModelsAttestationError("private models directory identity changed")


def run_attestation(
    profile: ModelsProfile,
    *,
    calls: version.RunnerCalls = version.REAL_CALLS,
    module_source: Optional[bytes] = None,
    profile_source: Optional[bytes] = None,
    stderr_contract: tuple[int, str] = (EXPECTED_STDERR_BYTES, EXPECTED_STDERR_SHA256),
    lifecycle: Optional[version.LifecycleState] = None,
    process_owned: bool = False,
) -> dict[str, object]:
    """Run exactly one snapshot-backed models inventory observation."""

    if lifecycle is None:
        lifecycle = version._acquire_lifecycle()
        try:
            version._activate_lifecycle(lifecycle)
        except BaseException:
            signal.pthread_sigmask(signal.SIG_BLOCK, lifecycle.controller.owned)
            lifecycle.controller.merge_pending()
            for item in reversed(lifecycle.installed_handlers):
                signal.signal(item, lifecycle.old_handlers[item])
            lifecycle.controller.merge_pending()
            signal.pthread_sigmask(signal.SIG_SETMASK, lifecycle.entry_mask)
            raise
    controller = lifecycle.controller
    root: Optional[Path] = None
    publisher: Optional[version.Publisher] = None
    source_parent = source_fd = snapshot_parent = snapshot_fd = None
    process: Optional[subprocess.Popen[bytes]] = None
    process_active = False
    completion_linearized = False
    original_error: Optional[BaseException] = None
    result: Optional[dict[str, object]] = None
    try:
        controller.poll()
        runner_source, _version_source, inventory_source = _canonical_sources(module_source)
        controller.poll()
        source_contract = validate_source_contract(runner_source)
        exact_profile = (
            _canonical_json(dataclasses.asdict(profile))
            if profile_source is None
            else profile_source
        )
        if ModelsProfile.from_bytes(exact_profile) != profile:
            raise ModelsAttestationError("exact profile bytes do not match the parsed profile")
        controller.poll()
        _validate_version_evidence(profile)
        controller.poll()
        blocked = signal.pthread_sigmask(signal.SIG_BLOCK, controller.owned)
        try:
            controller.merge_pending()
            controller.poll()
            root = Path(tempfile.mkdtemp(prefix="agy-models-attestation.", dir=profile.temp_parent))
        finally:
            controller.merge_pending()
            signal.pthread_sigmask(signal.SIG_SETMASK, blocked)
        controller.poll()
        os.chmod(root, 0o700)
        publisher = version.Publisher(root, calls, controller)
        private_directory_identities: dict[str, version.FileIdentity] = {}
        for name in PRIVATE_DIRECTORY_NAMES:
            controller.poll()
            child = root / name
            child.mkdir(mode=0o700)
            private_directory_identities[name] = _private_directory_identity(child)
        runner_sha = publisher.publish("models_runner.py", runner_source)
        publisher.publish("models_runner.py.sha256", (runner_sha + "\n").encode("ascii"))
        parser_sha = publisher.publish("agy_inventory.py", inventory_source)
        publisher.publish("agy_inventory.py.sha256", (parser_sha + "\n").encode("ascii"))
        profile_sha = publisher.publish("models.profile.json", exact_profile)
        if runner_sha != source_contract["sha256"] or parser_sha != INVENTORY_PARSER_SHA256:
            raise ModelsAttestationError("persisted canonical source changed")
        source_parent, source_fd = version._open_attested(
            profile.source_path, profile.source_identity, profile.source_sha256, 0o755, controller
        )
        snapshot_parent, snapshot_fd = version._open_attested(
            profile.snapshot_path, profile.snapshot_identity, profile.source_sha256, 0o500, controller
        )
        argv = [profile.source_path, "models"]
        environment = {
            "HOME": str(root / "home"),
            "TMPDIR": str(root / "tmp"),
            "XDG_CONFIG_HOME": str(root / "xdg-config"),
            "XDG_CACHE_HOME": str(root / "xdg-cache"),
            "XDG_STATE_HOME": str(root / "xdg-state"),
            "LANG": "C",
            "LC_ALL": "C",
            "NO_COLOR": "1",
            "TERM": "dumb",
            "PATH": "/usr/bin:/bin",
        }
        blocked = signal.pthread_sigmask(signal.SIG_BLOCK, controller.owned)
        try:
            controller.merge_pending()
            controller.poll()
            _revalidate_private_directories(root, private_directory_identities)
            process = calls.popen(
                argv,
                executable=profile.snapshot_path,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(root / "cwd"),
                env=environment,
                start_new_session=True,
            )
            if type(process.pid) is not int or process.pid <= 1 or process.pid == os.getpgrp():
                raise ModelsAttestationError("models process group is unsafe")
            process_active = True
        finally:
            controller.merge_pending()
            signal.pthread_sigmask(signal.SIG_SETMASK, blocked)
        controller.poll()
        started = time.monotonic()
        deadline = started + WALL_SECONDS
        stdout, stderr = _capture(process, deadline, controller)
        blocked = signal.pthread_sigmask(signal.SIG_BLOCK, controller.owned)
        try:
            controller.merge_pending()
            exit_code = version._close_reserved_group(process, calls)
            process_active = False
        finally:
            controller.merge_pending()
            signal.pthread_sigmask(signal.SIG_SETMASK, blocked)
        controller.poll()
        if exit_code != 0:
            raise ModelsAttestationError("models process failed")
        evidence = inventory.parse_inventory_bytes(stdout)
        if (
            evidence.normalized_sha256 != profile.inventory_normalized_sha256
            or evidence.slugs != tuple(sorted(inventory.EXPECTED_SLUGS))
            or evidence.line_count != 11
        ):
            raise ModelsAttestationError("models inventory changed")
        stderr_sha = _validate_stderr(stderr, stderr_contract)
        source_post = version._verify_attested_path(
            source_parent, profile.source_path, source_fd, profile.source_identity, profile.source_sha256, controller
        )
        snapshot_post = version._verify_attested_path(
            snapshot_parent, profile.snapshot_path, snapshot_fd, profile.snapshot_identity, profile.source_sha256, controller
        )
        stdout_sha = publisher.publish("models.stdout", stdout)
        published_stderr_sha = publisher.publish("models.stderr", stderr)
        if published_stderr_sha != stderr_sha:
            raise ModelsAttestationError("persisted stderr digest changed")
        logical_sha = hashlib.sha256(json.dumps(argv, separators=(",", ":")).encode("ascii")).hexdigest()
        elapsed_ms = int((time.monotonic() - started) * 1000)
        summary = {
            "call_count": 1,
            "child_exit": exit_code,
            "claim": "snapshot-models-inventory",
            "elapsed_ms": elapsed_ms,
            "line_count": evidence.line_count,
            "logical_argv_sha256": logical_sha,
            "normalized_sha256": evidence.normalized_sha256,
            "profile_bytes": len(exact_profile),
            "profile_sha256": profile_sha,
            "schema_version": 1,
            "status": "accepted",
            "stderr_bytes": len(stderr),
            "stdout_bytes": len(stdout),
            "timeout": False,
        }
        summary_sha = publisher.publish("models.summary.json", _canonical_json(summary))
        binding = {
            "artifacts": {
                "agy_inventory.py": parser_sha,
                "models.stderr": stderr_sha,
                "models.stdout": stdout_sha,
                "models.summary.json": summary_sha,
                "models_runner.py": runner_sha,
                "models.profile.json": profile_sha,
            },
            "claim": "snapshot-models-inventory",
            "inventory": {
                "line_count": evidence.line_count,
                "normalized_sha256": evidence.normalized_sha256,
                "parser": {"byte_count": len(inventory_source), "sha256": parser_sha},
                "slugs": list(evidence.slugs),
            },
            "limitations": {
                "cost_and_quota_unknown": True,
                "metadata_advance_authorized": False,
                "network_absence_os_enforced": False,
                "provider_backend_proven": False,
                "retry_behavior_proven": False,
            },
            "models": {
                "exit": exit_code,
                "logical_argv": argv,
                "logical_argv_sha256": logical_sha,
                "popen_count": 1,
                "stderr_limit": STREAM_LIMIT,
                "stdout_limit": STREAM_LIMIT,
                "timeout_seconds": WALL_SECONDS,
            },
            "profile": {"byte_count": len(exact_profile), "sha256": profile_sha},
            "runner": {"byte_count": len(runner_source), "sha256": runner_sha},
            "schema_version": 1,
            "snapshot": {
                "post": snapshot_post.as_dict(),
                "pre": profile.snapshot_identity.as_dict(),
                "sha256": profile.source_sha256,
            },
            "source": {
                "post": source_post.as_dict(),
                "pre": profile.source_identity.as_dict(),
                "sha256": profile.source_sha256,
            },
            "version": {"binding_sha256": profile.version_binding_sha256},
        }
        binding_sha = publisher.publish("models.binding.json", _canonical_json(binding))
        publisher.publish("models.binding.sha256", (binding_sha + "\n").encode("ascii"))
        controller.poll()
        result = {
            "artifact_root": str(root),
            "binding_sha256": binding_sha,
            "call_count": 1,
            "claim": "snapshot-models-inventory",
            "line_count": evidence.line_count,
            "normalized_sha256": evidence.normalized_sha256,
            "runner_sha256": runner_sha,
            "snapshot_sha256": profile.source_sha256,
            "status": "accepted",
            "stderr_sha256": published_stderr_sha,
            "stdout_sha256": stdout_sha,
        }
        if process_owned:
            sys.stdout.buffer.flush()
            version._write_all(
                sys.stdout.buffer.fileno(), _canonical_json(result), controller
            )
            sys.stdout.buffer.flush()
        signal.pthread_sigmask(signal.SIG_BLOCK, controller.owned)
        controller.merge_pending()
        controller.poll()
        completion_linearized = True
    except BaseException as exc:
        original_error = exc
    finally:
        signal.pthread_sigmask(signal.SIG_BLOCK, controller.owned)
        if process_owned and completion_linearized:
            os._exit(0)
        cleanup_failure: Optional[BaseException] = None
        if not completion_linearized:
            controller.merge_pending()
            if process is not None and process_active:
                try:
                    version._terminate_group(process, calls)
                    process_active = False
                except BaseException as exc:
                    cleanup_failure = exc
            if publisher is not None:
                try:
                    publisher.rollback()
                except BaseException as exc:
                    if cleanup_failure is None:
                        cleanup_failure = exc
            controller.merge_pending()
        for descriptor in (snapshot_fd, snapshot_parent, source_fd, source_parent):
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
        if publisher is not None:
            try:
                publisher.close()
            except BaseException as exc:
                if cleanup_failure is None:
                    cleanup_failure = exc
        if process_owned:
            controller.merge_pending()
            selected = controller.choose()
            version._atomic_exit(
                128 + selected if selected is not None else 2,
                sys.stderr.buffer.fileno(),
                b"models attestation runner: interrupted\n"
                if selected is not None
                else b"models attestation runner: rejected\n",
            )
        for item in reversed(lifecycle.installed_handlers):
            try:
                signal.signal(item, lifecycle.old_handlers[item])
            except BaseException as exc:
                if cleanup_failure is None:
                    cleanup_failure = exc
        if not completion_linearized:
            controller.merge_pending()
        try:
            signal.pthread_sigmask(signal.SIG_SETMASK, lifecycle.entry_mask)
        except BaseException as exc:
            if cleanup_failure is None:
                cleanup_failure = exc
        if original_error is None and cleanup_failure is not None:
            original_error = cleanup_failure
    selected = controller.choose()
    if selected is not None and not completion_linearized:
        raise version.AttestationInterrupted(selected)
    if original_error is not None:
        raise original_error
    if result is None:
        raise ModelsAttestationError("models attestation did not produce a result")
    return result


def _inventory_bytes() -> bytes:
    lines = []
    for slug in inventory.EXPECTED_SLUGS:
        if slug == "gpt-oss-120b-medium":
            lines.append("gpt-oss display gpt-oss-120b-medium")
        else:
            lines.append(f"available {slug}")
    return ("\n".join(lines) + "\n").encode("ascii")


def _fake_executable(stdout: bytes, stderr: bytes = b"", exit_code: int = 0) -> bytes:
    return (
        b"#!/usr/bin/python3\nimport os\n"
        + b"os.write(1," + repr(stdout).encode("ascii") + b")\n"
        + b"os.write(2," + repr(stderr).encode("ascii") + b")\n"
        + f"raise SystemExit({exit_code})\n".encode("ascii")
    )


def _synthetic_profile(
    root: Path,
    *,
    models_stdout: Optional[bytes] = None,
    models_stderr: bytes = b"",
    models_exit: int = 0,
    models_delay: float = 0.0,
    models_require_session: bool = False,
) -> ModelsProfile:
    source = root / "agy"
    selected_stdout = _inventory_bytes() if models_stdout is None else models_stdout
    session_guard = b""
    if models_require_session:
        caller_home = root / "caller-home"
        caller_home.mkdir(mode=0o700)
        session_marker = caller_home / "session.marker"
        session_marker.write_bytes(b"synthetic\n")
        session_marker.chmod(0o600)
        session_guard = (
            b" if not os.path.isfile(os.path.join(os.environ['HOME'],'session.marker')):\n"
            b"  os.write(2,b'login-state-unavailable\\n')\n"
            b"  raise SystemExit(0)\n"
        )
    dual = (
        b"#!/usr/bin/python3\nimport os,sys,time\n"
        b"if sys.argv[1:] == ['--version']: os.write(1,b'1.1.11\\n')\n"
        b"elif sys.argv[1:] == ['models']:\n"
        + f" time.sleep({models_delay!r})\n".encode("ascii")
        + session_guard
        + b" os.write(1," + repr(selected_stdout).encode("ascii") + b")\n"
        + b" os.write(2," + repr(models_stderr).encode("ascii") + b")\n"
        + f" raise SystemExit({models_exit})\n".encode("ascii")
        + b"else: raise SystemExit(2)\n"
    )
    source.write_bytes(dual)
    source.chmod(0o755)
    old = root / "agy-version-attestation.synthetic"
    old.mkdir(mode=0o700)
    snapshot = old / "agy.snapshot"
    snapshot.write_bytes(dual)
    snapshot.chmod(0o500)
    sha = hashlib.sha256(dual).hexdigest()
    prior_profile = version.AttestationProfile(
        prior_binding_sha256="0" * 64,
        prior_root=str(old),
        snapshot_identity=version.FileIdentity.from_stat(snapshot.stat()),
        snapshot_path=str(snapshot),
        source_identity=version.FileIdentity.from_stat(source.stat()),
        source_path=str(source),
        source_sha256=sha,
        temp_parent=str(root),
    )
    for name in version.PRIOR_FILES:
        path = old / name
        if name == "agy.snapshot":
            continue
        if name in {"cwd", "home", "tmp", "xdg-cache", "xdg-config", "xdg-state"}:
            path.mkdir(mode=0o700)
        else:
            path.write_bytes(b"")
            path.chmod(0o600)
    prior_value = {
        "claim": "snapshot-version-only",
        "inventory": {"executable_version_bound": False},
        "snapshot": {"pre": prior_profile.snapshot_identity.as_dict(), "sha256": sha},
        "source": {"pre": prior_profile.source_identity.as_dict(), "sha256": sha},
        "version": {"logical_argv": [str(source), "--version"]},
    }
    prior_bytes = _canonical_json(prior_value)
    prior_sha = hashlib.sha256(prior_bytes).hexdigest()
    (old / "version.binding.json").write_bytes(prior_bytes)
    (old / "version.binding.sha256").write_bytes((prior_sha + "\n").encode("ascii"))
    (old / "version.binding.json").chmod(0o600)
    (old / "version.binding.sha256").chmod(0o600)
    prior_profile = dataclasses.replace(prior_profile, prior_binding_sha256=prior_sha)
    second = version.run_attestation(prior_profile)
    version_root = Path(str(second["artifact_root"]))
    for name in ("cwd", "home", "tmp", "xdg-config", "xdg-cache", "xdg-state"):
        directory = version_root / name
        for child in tuple(directory.iterdir()):
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()
    return ModelsProfile(
        inventory_normalized_sha256=EXPECTED_NORMALIZED_SHA256,
        snapshot_identity=prior_profile.snapshot_identity,
        snapshot_path=str(snapshot),
        source_identity=prior_profile.source_identity,
        source_path=str(source),
        source_sha256=sha,
        temp_parent=str(root),
        version_binding_sha256=str(second["binding_sha256"]),
        version_root=str(second["artifact_root"]),
    )


def run_offline_self_test() -> dict[str, object]:
    root = Path(tempfile.mkdtemp(prefix="agy-models-runner-selftest.")).resolve()
    root.chmod(0o700)
    try:
        profile = _synthetic_profile(root)
        profile_source = _canonical_json(dataclasses.asdict(profile))
        result = run_attestation(
            profile,
            profile_source=profile_source,
            stderr_contract=(0, hashlib.sha256(b"").hexdigest()),
        )
        output_root = Path(str(result["artifact_root"]))
        try:
            if set(path.name for path in output_root.iterdir()) != set(OUTPUT_FILES) | {
                "cwd", "home", "tmp", "xdg-config", "xdg-cache", "xdg-state"
            }:
                raise ModelsAttestationError("self-test artifact shape changed")
            return {
                "accepted": 1,
                "claim": result["claim"],
                "mutations_killed": 1,
                "status": "accepted",
            }
        finally:
            shutil.rmtree(output_root)
    finally:
        shutil.rmtree(root)


def main(argv: Sequence[str]) -> int:
    if list(argv) == ["--self-test"]:
        try:
            result = run_offline_self_test()
        except (ModelsAttestationError, version.AttestationError, OSError, subprocess.SubprocessError):
            print("models attestation runner: rejected", file=sys.stderr)
            return 2
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    if list(argv) != ["--attest-models"]:
        print("models attestation runner: invalid invocation", file=sys.stderr)
        return 64
    try:
        lifecycle = version._acquire_lifecycle()
    except BaseException:
        version._atomic_exit(2, sys.stderr.buffer.fileno(), b"models attestation runner: rejected\n")
    usage = False
    diagnostic = b"models attestation runner: rejected\n"
    try:
        version._activate_lifecycle(lifecycle)
        startup = version._production_startup_evaluation()
        lifecycle.controller.poll()
        if not startup.accepted:
            diagnostic = version._startup_diagnostic(startup)
            usage = True
            raise ModelsAttestationError("production startup rejected")
        data = version._read_stdin(lifecycle.controller)
        profile = ModelsProfile.from_bytes(data)
        _validate_production_profile(profile)
        lifecycle.controller.poll()
    except BaseException:
        signal.pthread_sigmask(signal.SIG_BLOCK, lifecycle.controller.owned)
        lifecycle.controller.merge_pending()
        selected = lifecycle.controller.choose()
        version._atomic_exit(
            128 + selected if selected is not None else (64 if usage else 2),
            sys.stderr.buffer.fileno(),
            b"models attestation runner: interrupted\n"
            if selected is not None
            else diagnostic,
        )
    run_attestation(
        profile,
        profile_source=data,
        lifecycle=lifecycle,
        process_owned=True,
    )
    version._atomic_exit(2, sys.stderr.buffer.fileno(), b"models attestation runner: rejected\n")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
