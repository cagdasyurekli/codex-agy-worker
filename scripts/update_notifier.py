#!/usr/bin/env python3
"""Install and run a conservative local macOS daily update notifier."""

from __future__ import annotations

import fcntl
import hashlib
import hmac
import json
import os
import plistlib
import pwd
import re
import secrets
import signal
import stat
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

sys.dont_write_bytecode = True

LABEL = "com.cagdasyurekli.agy-worker.update-notifier"
STATE_RELATIVE = Path("Library/Application Support/codex-agy-worker/update-notifier")
PLIST_RELATIVE = Path("Library/LaunchAgents") / f"{LABEL}.plist"
MAX_LEDGER = 128 * 1024
MAX_RESULT = 128 * 1024
RESULT_EXIT_STATUS = {
    0: "unchanged",
    2: "evidence-unavailable",
    3: "drift-review",
}
SIGNALS = (signal.SIGHUP, signal.SIGINT, signal.SIGTERM)
SIGNAL_EXIT = {signal.SIGHUP: 129, signal.SIGINT: 130, signal.SIGTERM: 143}
SOURCE_FILES = (
    "update-notifier.sh",
    "scripts/update_notifier.py",
    "scripts/update_notifier_child.py",
    "update.sh",
    "scripts/compatibility.py",
    "skills/agy-worker/runtime/scripts/compatibility.py",
    "scripts/compatibility_probe.py",
    "scripts/official_github.py",
    "scripts/official_distribution.py",
    "compat/agy-distribution-manifest.json",
    "compat/agy-last-reviewed.txt",
    "compat/agy-model-effort-matrix.json",
    "compat/agy-model-effort-matrix.sha256",
    "compat/agy-models-inventory-binding.json",
    "compat/agy-models-inventory-binding.sha256",
    "compat/agy-upstream-head.txt",
    "compat/agy-verified-version.txt",
    "compat/codex-last-reviewed.txt",
    "compat/codex-upstream-head.txt",
    "compat/codex-verified-version.txt",
    "compat/model-effort-matrix.schema.json",
)


class NotifierError(RuntimeError):
    pass


class Interrupted(BaseException):
    def __init__(self, signum: int):
        super().__init__(signum)
        self.signum = signum


@dataclass(frozen=True)
class Layout:
    home: Path
    source: Path
    state: Path
    plist: Path
    ledger: Path
    tombstone: Path
    result: Path
    snapshot: Path
    launcher: Path
    shim: Path


def canonical_home() -> Path:
    uid = os.getuid()
    value = pwd.getpwuid(uid).pw_dir
    if not value or not Path(value).is_absolute():
        raise NotifierError("account HOME is unavailable")
    home = Path(value)
    ambient = os.environ.get("HOME")
    if ambient != value:
        raise NotifierError("ambient HOME does not match the account database")
    return home


def source_root() -> Path:
    return Path(__file__).resolve().parents[1]


def resolve_git_dir(source: Path) -> Path:
    marker = source / ".git"
    info = os.lstat(marker)
    if stat.S_ISDIR(info.st_mode):
        result = marker
    elif stat.S_ISREG(info.st_mode) and info.st_size <= 4096:
        descriptor = os.open(marker, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            raw = os.read(descriptor, 4097)
        finally:
            os.close(descriptor)
        if len(raw) > 4096 or not raw.endswith(b"\n") or raw.count(b"\n") != 1:
            raise NotifierError("Git authority marker is malformed")
        try:
            line = raw[:-1].decode("utf-8", "strict")
        except UnicodeDecodeError:
            raise NotifierError("Git authority marker is malformed") from None
        if not line.startswith("gitdir: ") or not line[8:]:
            raise NotifierError("Git authority marker is malformed")
        candidate = Path(line[8:])
        result = candidate if candidate.is_absolute() else source / candidate
    else:
        raise NotifierError("Git authority marker is unsafe")
    result = Path(os.path.abspath(result))
    target = os.stat(result, follow_symlinks=False)
    if not stat.S_ISDIR(target.st_mode) or target.st_uid != os.getuid() or target.st_mode & 0o022:
        raise NotifierError("Git authority directory is unsafe")
    return result


def layout(home: Optional[Path] = None, source: Optional[Path] = None) -> Layout:
    account_home = canonical_home() if home is None else home
    repository = source_root() if source is None else source
    state_root = account_home / STATE_RELATIVE
    snapshot = state_root / "source"
    return Layout(
        home=account_home,
        source=repository,
        state=state_root,
        plist=account_home / PLIST_RELATIVE,
        ledger=state_root / "state.json",
        tombstone=state_root / "uninstall.json",
        result=state_root / "last-result.json",
        snapshot=snapshot,
        launcher=state_root / "launcher.py",
        shim=state_root / "shim" / "python3",
    )


def _safe_components(base: Path, target: Path, *, allow_missing: bool) -> None:
    uid = os.getuid()
    try:
        relative = target.relative_to(base)
    except ValueError:
        raise NotifierError("path escapes account HOME") from None
    current = base
    components = [None, *relative.parts]
    for index, component in enumerate(components):
        if component is not None:
            current = current / component
        try:
            info = os.lstat(current)
        except FileNotFoundError:
            if allow_missing:
                return
            raise NotifierError("required path is missing") from None
        if stat.S_ISLNK(info.st_mode):
            raise NotifierError("symlinked account path is refused")
        if index < len(components) - 1 and not stat.S_ISDIR(info.st_mode):
            raise NotifierError("account path ancestor is not a directory")
        if info.st_uid != uid or info.st_mode & 0o022:
            raise NotifierError("account path ownership or writability is unsafe")


def _mkdir_private(path: Path, home: Path) -> None:
    _safe_components(home, path.parent, allow_missing=True)
    pending: list[Path] = []
    current = path
    while not current.exists():
        pending.append(current)
        current = current.parent
    _safe_components(home, current, allow_missing=False)
    for item in reversed(pending):
        item.mkdir(mode=0o700)
        os.chmod(item, 0o700)
        _fsync_dir(item.parent)
    _safe_components(home, path, allow_missing=False)


def _fsync_dir(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _read_bounded(path: Path, limit: int) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise NotifierError("private state is unavailable or unsafe") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise NotifierError("private state ownership or mode is unsafe")
        if info.st_size > limit:
            raise NotifierError("private state exceeds its bound")
        chunks = bytearray()
        while len(chunks) <= limit:
            chunk = os.read(descriptor, min(8192, limit + 1 - len(chunks)))
            if not chunk:
                break
            chunks.extend(chunk)
        if len(chunks) > limit:
            raise NotifierError("private state exceeds its bound")
        return bytes(chunks)
    finally:
        os.close(descriptor)


def _atomic_write(path: Path, payload: bytes, mode: int = 0o600) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _fsync_dir(path.parent)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _canonical_json(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _load_json(path: Path, limit: int) -> dict[str, object]:
    def no_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result
    try:
        value = json.loads(
            _read_bounded(path, limit).decode("utf-8", "strict"),
            object_pairs_hook=no_duplicates,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise NotifierError("private state is malformed") from exc
    if not isinstance(value, dict):
        raise NotifierError("private state is malformed")
    return value


def _hash_file(path: Path) -> str:
    info = os.lstat(path)
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_mode & 0o022
        or info.st_size > 2 * 1024 * 1024
    ):
        raise NotifierError("behavior source is unsafe")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino, opened.st_size) != (info.st_dev, info.st_ino, info.st_size):
            raise NotifierError("behavior source changed while opening")
        chunks = bytearray()
        while len(chunks) < info.st_size:
            chunk = os.read(descriptor, min(8192, info.st_size - len(chunks)))
            if not chunk:
                break
            chunks.extend(chunk)
        raw = bytes(chunks)
    finally:
        os.close(descriptor)
    if len(raw) != info.st_size:
        raise NotifierError("behavior source changed while reading")
    return hashlib.sha256(raw).hexdigest()


def source_manifest(source: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    source_info = os.lstat(source)
    if not stat.S_ISDIR(source_info.st_mode) or source_info.st_uid != os.getuid():
        raise NotifierError("source root is unsafe")
    for relative in SOURCE_FILES:
        path = source / relative
        parent = path.parent
        while parent != source.parent:
            info = os.lstat(parent)
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise NotifierError("behavior source ancestor is unsafe")
            if info.st_uid != os.getuid() or info.st_mode & 0o022:
                raise NotifierError("behavior source ancestor is writable or foreign")
            if parent == source:
                break
            parent = parent.parent
        result[relative] = _hash_file(path)
    return result


def _shim_payload(launcher_payload: bytes) -> bytes:
    first, separator, remainder = launcher_payload.partition(b"\n")
    if separator != b"\n" or not first.startswith(b"#!"):
        raise NotifierError("launcher source lacks a canonical shebang")
    return b"#!/usr/bin/python3\n" + remainder


def _copy_snapshot(paths: Layout, manifest: dict[str, str]) -> str:
    _mkdir_private(paths.snapshot, paths.home)
    for relative, digest in manifest.items():
        destination = paths.snapshot / relative
        _mkdir_private(destination.parent, paths.home)
        payload = (paths.source / relative).read_bytes()
        if hashlib.sha256(payload).hexdigest() != digest:
            raise NotifierError("behavior source changed during installation")
        descriptor = os.open(
            destination,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o500 if relative.endswith((".py", ".sh")) else 0o400,
        )
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        _fsync_dir(destination.parent)
    launcher_payload = (paths.source / "scripts/update_notifier_child.py").read_bytes()
    shim_payload = _shim_payload(launcher_payload)
    for destination, payload in ((paths.launcher, launcher_payload), (paths.shim, shim_payload)):
        _mkdir_private(destination.parent, paths.home)
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o500)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        _fsync_dir(destination.parent)
    return hashlib.sha256(shim_payload).hexdigest()


@contextmanager
def lifecycle_lock(paths: Layout) -> Iterator[None]:
    _mkdir_private(paths.state, paths.home)
    lock_path = paths.state / "lock"
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise NotifierError("notifier lock is unsafe")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise NotifierError("another notifier lifecycle command is active") from None
        yield
    finally:
        os.close(descriptor)


def _launchctl(arguments: list[str]) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["/bin/launchctl", *arguments],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=15.0,
        check=False,
    )


def loaded_state(uid: int) -> str:
    completed = _launchctl(["print", f"gui/{uid}/{LABEL}"])
    if completed.returncode == 0:
        return "loaded"
    if completed.returncode in (3, 113):
        return "unloaded"
    return "unknown"


def _plist(paths: Layout) -> bytes:
    value = {
        "Label": LABEL,
        "ProgramArguments": [
            "/usr/bin/python3", "-I", "-S", "-B",
            str(paths.launcher), "--scheduled",
            str(paths.snapshot / "scripts/update_notifier.py"), str(paths.source),
        ],
        "StartCalendarInterval": {"Hour": 10, "Minute": 0},
        "ProcessType": "Background",
    }
    return plistlib.dumps(value, fmt=plistlib.FMT_XML, sort_keys=True)


def _validate_ledger_shape(value: dict[str, object]) -> dict[str, object]:
    expected = {"schema", "label", "uid", "source", "git_dir", "manifest", "plist_sha256", "shim_sha256", "secret", "phase"}
    if set(value) != expected or value.get("schema") != 1 or value.get("label") != LABEL:
        raise NotifierError("installed state is malformed")
    manifest = value.get("manifest")
    if (
        value.get("uid") != os.getuid()
        or value.get("phase") not in {"preparing", "bootstrapping", "loaded", "uninstalled"}
        or not isinstance(value.get("source"), str)
        or not Path(str(value["source"])).is_absolute()
        or not isinstance(value.get("git_dir"), str)
        or not Path(str(value["git_dir"])).is_absolute()
        or not isinstance(manifest, dict)
        or set(manifest) != set(SOURCE_FILES)
        or any(not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None for digest in manifest.values())
        or not isinstance(value.get("plist_sha256"), str)
        or re.fullmatch(r"[0-9a-f]{64}", str(value["plist_sha256"])) is None
        or not isinstance(value.get("shim_sha256"), str)
        or re.fullmatch(r"[0-9a-f]{64}", str(value["shim_sha256"])) is None
        or not isinstance(value.get("secret"), str)
        or re.fullmatch(r"[0-9a-f]{64}", str(value["secret"])) is None
    ):
        raise NotifierError("installed state is malformed")
    return value


def _validate_ledger(value: dict[str, object], paths: Layout) -> dict[str, object]:
    _validate_ledger_binding(value, paths)
    manifest = value.get("manifest")
    if not isinstance(manifest, dict) or manifest != source_manifest(paths.source):
        raise NotifierError("notifier behavior source drifted")
    return value


def _validate_ledger_binding(value: dict[str, object], paths: Layout) -> dict[str, object]:
    """Validate installed authority without requiring live source byte equality."""
    _validate_ledger_shape(value)
    if value.get("uid") != os.getuid() or value.get("source") != str(paths.source):
        raise NotifierError("installed state does not bind this account and source")
    if value.get("git_dir") != str(resolve_git_dir(paths.source)):
        raise NotifierError("installed state does not bind this Git authority")
    return value


def _live_source_state(ledger: dict[str, object], paths: Layout) -> str:
    """Classify only a safe, complete live-source digest mismatch as maintenance."""
    state, _current = _live_source_manifest_state(ledger, paths)
    return state


def _live_source_manifest_state(
    ledger: dict[str, object], paths: Layout
) -> tuple[str, dict[str, str]]:
    _validate_ledger_binding(ledger, paths)
    current = source_manifest(paths.source)
    state = "unchanged" if current == ledger.get("manifest") else "maintenance-required"
    return state, current


def _validate_plist(paths: Layout, ledger: dict[str, object]) -> bool:
    try:
        return hashlib.sha256(_read_bounded(paths.plist, 64 * 1024)).hexdigest() == ledger["plist_sha256"]
    except NotifierError:
        return False


def _validate_installed_sources(paths: Layout, ledger: dict[str, object]) -> None:
    manifest = ledger.get("manifest")
    if not isinstance(manifest, dict) or set(manifest) != set(SOURCE_FILES):
        raise NotifierError("installed behavior manifest is malformed")
    for relative, digest in manifest.items():
        if not isinstance(relative, str) or not isinstance(digest, str):
            raise NotifierError("installed behavior manifest is malformed")
        if _hash_file(paths.snapshot / relative) != digest:
            raise NotifierError("installed behavior source drifted")
    child_digest = manifest.get("scripts/update_notifier_child.py")
    if _hash_file(paths.launcher) != child_digest or _hash_file(paths.shim) != ledger.get("shim_sha256"):
        raise NotifierError("installed launcher source drifted")


def install(paths: Layout) -> None:
    with lifecycle_lock(paths):
        _install_locked(paths)


def _install_locked(paths: Layout) -> None:
    if paths.ledger.exists():
        ledger = _load_json(paths.ledger, MAX_LEDGER)
        _validate_ledger_shape(ledger)
        state = loaded_state(os.getuid())
        if ledger.get("phase") == "uninstalled":
            if ledger.get("source") != str(paths.source) or ledger.get("git_dir") != str(resolve_git_dir(paths.source)):
                raise NotifierError("installed state does not bind this source and Git authority")
            if state != "unloaded":
                raise NotifierError("prior uninstall cannot be reconciled with launchd")
            tombstone = _load_json(paths.tombstone, MAX_LEDGER)
            _validate_tombstone(tombstone, ledger)
            if tombstone.get("phase") != "completed":
                raise NotifierError("prior uninstall recovery is incomplete")
            os.unlink(paths.tombstone)
            _fsync_dir(paths.state)
        else:
            _validate_ledger(ledger, paths)
        if ledger.get("phase") != "uninstalled" and state == "loaded" and _validate_plist(paths, ledger):
            _validate_installed_sources(paths, ledger)
            print("update notifier: already installed and loaded")
            return
        if ledger.get("phase") != "uninstalled" and state != "unloaded":
            raise NotifierError("launchd state is loaded or unknown; retained installed state")
        if ledger.get("phase") != "uninstalled":
            raise NotifierError("an incomplete notifier installation requires uninstall")
    manifest = source_manifest(paths.source)
    plist_payload = _plist(paths)
    launcher_payload = (paths.source / "scripts/update_notifier_child.py").read_bytes()
    shim_digest = hashlib.sha256(_shim_payload(launcher_payload)).hexdigest()
    ledger = {
        "schema": 1,
        "label": LABEL,
        "uid": os.getuid(),
        "source": str(paths.source),
        "git_dir": str(resolve_git_dir(paths.source)),
        "manifest": manifest,
        "plist_sha256": hashlib.sha256(plist_payload).hexdigest(),
        "shim_sha256": shim_digest,
        "secret": secrets.token_hex(32),
        "phase": "preparing",
    }
    # Ledger authority exists before the first installed-source write, making
    # every later install failure removable through authenticated uninstall.
    _atomic_write(paths.ledger, _canonical_json(ledger))
    if _copy_snapshot(paths, manifest) != shim_digest:
        raise NotifierError("installed shim binding changed during installation")
    _mkdir_private(paths.plist.parent, paths.home)
    descriptor = os.open(paths.plist, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(plist_payload)
        stream.flush()
        os.fsync(stream.fileno())
    _fsync_dir(paths.plist.parent)
    ledger["phase"] = "bootstrapping"
    _atomic_write(paths.ledger, _canonical_json(ledger))
    if source_manifest(paths.source) != manifest:
        raise NotifierError("behavior source changed before bootstrap")
    _validate_installed_sources(paths, ledger)
    completed = _launchctl(["bootstrap", f"gui/{os.getuid()}", str(paths.plist)])
    state = loaded_state(os.getuid())
    if state == "loaded":
        ledger["phase"] = "loaded"
        _atomic_write(paths.ledger, _canonical_json(ledger))
        print("update notifier: installed and loaded")
        return
    if state == "unknown":
        raise NotifierError("launchctl bootstrap outcome is unknown; retained recovery state")
    detail = "bootstrap failed" if completed.returncode else "bootstrap side effect was not observed"
    raise NotifierError(f"launchctl {detail}; retained recovery state")


def status(paths: Layout) -> None:
    with lifecycle_lock(paths):
        if not paths.ledger.exists():
            print("update notifier: not installed")
            return
        ledger = _load_json(paths.ledger, MAX_LEDGER)
        if ledger.get("phase") == "uninstalled":
            _validate_ledger_shape(ledger)
            if ledger.get("uid") != os.getuid() or ledger.get("label") != LABEL:
                raise NotifierError("uninstalled recovery ledger is malformed")
            tombstone = _load_json(paths.tombstone, MAX_LEDGER)
            _validate_tombstone(tombstone, ledger)
            state = loaded_state(os.getuid())
            if state != "unloaded":
                raise NotifierError("uninstalled recovery conflicts with launchd state")
            print("update notifier: not installed; authenticated recovery record retained")
            return
        try:
            _validate_ledger_binding(ledger, paths)
            if not _validate_plist(paths, ledger):
                raise NotifierError("installed plist drifted")
            _validate_installed_sources(paths, ledger)
            source = _live_source_state(ledger, paths)
        except (NotifierError, OSError):
            source = "drifted-or-invalid"
        print(f"update notifier: {loaded_state(os.getuid())}; source {source}")


def _tombstone_payload(ledger: dict[str, object], phase: str, replacements: list[str]) -> dict[str, object]:
    body = {"schema": 1, "label": LABEL, "phase": phase, "replacements": replacements}
    body_bytes = _canonical_json(body)
    body["authentication"] = hmac.new(
        bytes.fromhex(str(ledger["secret"])), body_bytes, hashlib.sha256
    ).hexdigest()
    return body


def _validate_tombstone(value: dict[str, object], ledger: dict[str, object]) -> None:
    if set(value) != {"schema", "label", "phase", "replacements", "authentication"}:
        raise NotifierError("uninstall recovery record is malformed")
    if (
        value.get("schema") != 1
        or value.get("label") != LABEL
        or value.get("phase") not in {"started", "unloaded", "plist-processed", "files-processed", "completed"}
        or not isinstance(value.get("replacements"), list)
        or any(not isinstance(item, str) for item in value["replacements"])
    ):
        raise NotifierError("uninstall recovery record is malformed")
    authentication = value.pop("authentication", None)
    expected = hmac.new(
        bytes.fromhex(str(ledger["secret"])), _canonical_json(value), hashlib.sha256
    ).hexdigest()
    value["authentication"] = authentication
    if not isinstance(authentication, str) or not hmac.compare_digest(authentication, expected):
        raise NotifierError("uninstall recovery record is unauthenticated")


def _unlink_if_exact(path: Path, digest: str, replacements: list[str], label: str) -> None:
    try:
        if _hash_file(path) != digest:
            replacements.append(label)
            return
        os.unlink(path)
        _fsync_dir(path.parent)
    except FileNotFoundError:
        return


def uninstall(paths: Layout) -> None:
    with lifecycle_lock(paths):
        _uninstall_locked(paths)


def _uninstall_locked(paths: Layout) -> None:
    if not paths.ledger.exists():
        print("update notifier: not installed")
        return
    ledger = _load_json(paths.ledger, MAX_LEDGER)
    _validate_ledger_shape(ledger)
    replacements: list[str] = []
    phase = "started"
    if paths.tombstone.exists():
        tombstone = _load_json(paths.tombstone, MAX_LEDGER)
        _validate_tombstone(tombstone, ledger)
        phase = str(tombstone.get("phase"))
        stored = tombstone.get("replacements")
        replacements = list(stored) if isinstance(stored, list) and all(isinstance(x, str) for x in stored) else []
    else:
        _atomic_write(paths.tombstone, _canonical_json(_tombstone_payload(ledger, phase, replacements)))
    state = loaded_state(os.getuid())
    # A previously unloaded label may be loaded again after login/reboot.
    # Reconcile it before every resumed deletion phase, not only on the first
    # invocation that created the tombstone.
    if state == "loaded":
        _launchctl(["bootout", f"gui/{os.getuid()}/{LABEL}"])
        state = loaded_state(os.getuid())
    if state != "unloaded":
        raise NotifierError("launchd state is loaded or unknown; uninstall remains resumable")
    if phase == "started":
        phase = "unloaded"
        _atomic_write(paths.tombstone, _canonical_json(_tombstone_payload(ledger, phase, replacements)))
    if phase == "unloaded":
        _unlink_if_exact(paths.plist, str(ledger["plist_sha256"]), replacements, "plist")
        phase = "plist-processed"
        _atomic_write(paths.tombstone, _canonical_json(_tombstone_payload(ledger, phase, replacements)))
    if phase == "plist-processed":
        manifest = ledger.get("manifest")
        if not isinstance(manifest, dict):
            raise NotifierError("installed state is malformed")
        for relative, digest in manifest.items():
            if isinstance(relative, str) and isinstance(digest, str):
                _unlink_if_exact(paths.snapshot / relative, digest, replacements, f"source:{relative}")
        child_digest = str(manifest.get("scripts/update_notifier_child.py", ""))
        _unlink_if_exact(paths.launcher, child_digest, replacements, "launcher")
        _unlink_if_exact(paths.shim, str(ledger.get("shim_sha256", "")), replacements, "shim")
        phase = "files-processed"
        _atomic_write(paths.tombstone, _canonical_json(_tombstone_payload(ledger, phase, replacements)))
    # Installed authority changes only after launchd absence and all durable
    # phases.  Keep an authenticated completed tombstone and inert ledger so a
    # directory-fsync failure never destroys the only resumable authority.
    if loaded_state(os.getuid()) != "unloaded":
        raise NotifierError("launchd absence could not be reconfirmed; retained recovery state")
    phase = "completed"
    _atomic_write(paths.tombstone, _canonical_json(_tombstone_payload(ledger, phase, replacements)))
    ledger["phase"] = "uninstalled"
    _atomic_write(paths.ledger, _canonical_json(ledger))
    if replacements:
        print("update notifier: uninstalled; preserved replacement files")
    else:
        print("update notifier: uninstalled")


def refresh(paths: Layout) -> None:
    """Explicitly replace a bound installation from the current reviewed source."""
    with lifecycle_lock(paths):
        if not paths.ledger.exists():
            raise NotifierError("notifier is not installed; use install")
        prior = _load_json(paths.ledger, MAX_LEDGER)
        _validate_ledger_binding(prior, paths)
        # A digest mismatch is expected here, but every current source component
        # must still pass the normal ownership, mode, size, and no-symlink checks.
        source_manifest(paths.source)
        _uninstall_locked(paths)
        retained = _load_json(paths.tombstone, MAX_LEDGER)
        _validate_tombstone(retained, prior)
        replacements = retained.get("replacements")
        if replacements:
            raise NotifierError(
                "refresh stopped after uninstall; preserved replacement files require review"
            )
        _install_locked(paths)
        print("update notifier: refreshed from current source")


def _timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _prior_result(paths: Layout) -> Optional[dict[str, object]]:
    if not paths.result.exists() and not paths.result.is_symlink():
        return None
    value = _load_json(paths.result, MAX_RESULT)
    required = {"schema", "timestamp", "status", "update_exit", "fingerprint", "notification_attempted"}
    update_exit = value.get("update_exit")
    if (
        set(value) != required
        or value.get("schema") != 1
        or not isinstance(value.get("timestamp"), str)
        or re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value["timestamp"]) is None
        or value.get("status") not in set(RESULT_EXIT_STATUS.values())
        or type(update_exit) is not int
        or update_exit not in RESULT_EXIT_STATUS
        or RESULT_EXIT_STATUS[update_exit] != value["status"]
        or not isinstance(value.get("fingerprint"), str)
        or re.fullmatch(r"[0-9a-f]{64}", value["fingerprint"]) is None
        or not isinstance(value.get("notification_attempted"), bool)
    ):
        raise NotifierError("prior notifier result is malformed")
    return value


def _terminate_group(process: subprocess.Popen[bytes]) -> None:
    pgid = process.pid
    try:
        os.killpg(pgid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        process.wait(timeout=1.0)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(pgid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        process.wait(timeout=1.0)


def _group_exists(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _wait_group_absent(pgid: int, seconds: float = 1.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not _group_exists(pgid):
            return True
        time.sleep(0.01)
    return not _group_exists(pgid)


def _run_child(paths: Layout) -> tuple[int, bytes, bytes]:
    sentinel_read, sentinel_write = os.pipe()
    ack_read, ack_write = os.pipe()
    work = Path(tempfile.mkdtemp(prefix="run-", dir=paths.state))
    os.chmod(work, 0o700)
    output, status_file = work / "output", work / "status"
    environment = {"HOME": str(paths.home), "LANG": "C", "LC_ALL": "C", "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"}
    process: Optional[subprocess.Popen[bytes]] = None
    interrupted: Optional[BaseException] = None
    try:
        process = subprocess.Popen(
            [
                "/usr/bin/python3", "-I", "-S", "-B", str(paths.launcher), "--run",
                str(paths.snapshot / "update.sh"), str(paths.shim.parent), str(paths.source),
                str(resolve_git_dir(paths.source)), str(sentinel_read), str(ack_write), str(output), str(status_file),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=environment,
            pass_fds=(sentinel_read, ack_write),
            start_new_session=True,
        )
        os.close(sentinel_read); sentinel_read = -1
        os.close(ack_write); ack_write = -1
        returncode = process.wait(timeout=100.0)
        if not _wait_group_absent(process.pid):
            raise NotifierError("update child process group did not close")
        os.close(sentinel_write); sentinel_write = -1
        acknowledgements = bytearray()
        while len(acknowledgements) <= 128:
            chunk = os.read(ack_read, 129 - len(acknowledgements))
            if not chunk:
                break
            acknowledgements.extend(chunk)
        if (
            returncode != 0
            or len(acknowledgements) > 128
            or acknowledgements.count(b"S") == 0
            or acknowledgements.count(b"S") != acknowledgements.count(b"A")
            or any(value not in b"SA" for value in acknowledgements)
        ):
            raise NotifierError("update child or nested cleanup acknowledgement failed")
        status_raw = _read_bounded(status_file, 16)
        stdout = _read_bounded(output, 64 * 1024)
        if status_raw not in (b"0\n", b"2\n", b"3\n"):
            raise NotifierError("update child returned an invalid result")
        return int(status_raw.strip()), stdout, bytes(acknowledgements)
    except subprocess.TimeoutExpired:
        if process is not None:
            _terminate_group(process)
        raise NotifierError("update child timed out") from None
    except BaseException as exc:
        interrupted = exc
        if sentinel_write >= 0:
            os.close(sentinel_write); sentinel_write = -1
        if process is not None and process.poll() is None:
            try:
                process.wait(timeout=10.0)
            except subprocess.TimeoutExpired:
                _terminate_group(process)
                raise NotifierError("interrupted child did not prove nested cleanup") from exc
        if process is not None and not _wait_group_absent(process.pid):
            raise NotifierError("interrupted child process group did not close") from exc
        acknowledgements = bytearray()
        while len(acknowledgements) <= 128:
            chunk = os.read(ack_read, 129 - len(acknowledgements))
            if not chunk:
                break
            acknowledgements.extend(chunk)
        if (
            acknowledgements.count(b"S") == 0
            or acknowledgements.count(b"S") != acknowledgements.count(b"A")
            or any(value not in b"SA" for value in acknowledgements)
        ):
            raise NotifierError("interrupted child lacked nested cleanup acknowledgement") from exc
        raise interrupted
    finally:
        for descriptor in (sentinel_read, sentinel_write, ack_read, ack_write):
            if descriptor >= 0:
                try: os.close(descriptor)
                except OSError: pass
        for path in (output, status_file):
            try: os.unlink(path)
            except OSError: pass
        try: os.rmdir(work)
        except OSError: pass


def run(paths: Layout) -> None:
    with lifecycle_lock(paths):
        ledger = _validate_ledger_binding(_load_json(paths.ledger, MAX_LEDGER), paths)
        if ledger.get("phase") != "loaded" or loaded_state(os.getuid()) != "loaded":
            raise NotifierError("notifier is not proven loaded")
        if not _validate_plist(paths, ledger):
            raise NotifierError("installed plist drifted")
        _validate_installed_sources(paths, ledger)
        prior = _prior_result(paths)
        source_state, current_manifest = _live_source_manifest_state(ledger, paths)
        if source_state == "maintenance-required":
            # Preserve the v1 result vocabulary for downgrade-safe dedup state.
            # The public run/status output and fixed notification carry the more
            # precise maintenance classification without migrating private state.
            update_exit = 3
            status_name = "maintenance-required"
            result_status = "drift-review"
            fingerprint = hashlib.sha256(
                b"maintenance-required\0"
                + _canonical_json(
                    {"installed": ledger.get("manifest"), "current": current_manifest}
                )
            ).hexdigest()
            acknowledgements = b""
        else:
            update_exit, stdout, acknowledgements = _run_child(paths)
            if b"\x00" in stdout or len(stdout) > 64 * 1024:
                raise NotifierError("update result is malformed")
            status_name = RESULT_EXIT_STATUS[update_exit]
            result_status = status_name
            fingerprint = hashlib.sha256(str(update_exit).encode("ascii") + b"\0" + stdout).hexdigest()
        attempted = status_name in {"drift-review", "maintenance-required"} and not (
            prior is not None
            and prior.get("fingerprint") == fingerprint
            and prior.get("notification_attempted") is True
        )
        result = {
            "schema": 1,
            "timestamp": _timestamp(),
            "status": result_status,
            "update_exit": update_exit,
            "fingerprint": fingerprint,
            "notification_attempted": attempted,
        }
        # Commit the attempt before the irreversible final side effect. Recovery
        # conservatively suppresses duplicates; this records an attempt, not delivery.
        _atomic_write(paths.result, _canonical_json(result))
        if attempted:
            if _run_notification(paths, status_name) != 0:
                raise NotifierError("notification request failed; its side effect cannot be retracted")
        del acknowledgements
        print(f"update notifier: {status_name}; notification {'attempted' if attempted else 'suppressed'}")


def _run_notification(paths: Layout, status_name: str) -> int:
    messages = {
        "drift-review": (
            "codex-agy-worker update review",
            "Update or compatibility drift changed; run update.sh check.",
        ),
        "maintenance-required": (
            "codex-agy-worker notifier maintenance",
            "Monitoring paused after the bound source changed; run update-notifier.sh refresh.",
        ),
    }
    if status_name not in messages:
        raise NotifierError("notification status is invalid")
    title, message = messages[status_name]
    process = subprocess.Popen(
        [
            "/usr/bin/python3", "-I", "-S", "-B", str(paths.launcher),
            "--notify", title, message,
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        return process.wait(timeout=15.0)
    except subprocess.TimeoutExpired:
        _terminate_group(process)
        return 2
    except BaseException:
        _terminate_group(process)
        raise


def usage() -> int:
    print("usage: update-notifier.sh {install|refresh|uninstall|status|run}", file=sys.stderr)
    return 64


def _complete(exitcode: int, latched: list[int]) -> None:
    if hasattr(signal, "pthread_sigmask"):
        signal.pthread_sigmask(signal.SIG_BLOCK, SIGNALS)
    sys.stdout.flush(); sys.stderr.flush()
    pending = {number for number in SIGNALS if number in signal.sigpending()} if hasattr(signal, "sigpending") else set()
    chosen = next((number for number in SIGNALS if number in pending or number in latched), None)
    os._exit(exitcode if chosen is None else SIGNAL_EXIT[chosen])


def main(argv: list[str]) -> int:
    internal_source: Optional[Path] = None
    if len(argv) == 4 and argv[1:3] == ["run", "--source"] and Path(argv[3]).is_absolute():
        command = "run"
        internal_source = Path(argv[3])
    elif len(argv) == 2 and argv[1] in {"install", "refresh", "uninstall", "status", "run"}:
        command = argv[1]
    else:
        return usage()
    latched: list[int] = []
    previous = {number: signal.getsignal(number) for number in SIGNALS}
    def handle(number: int, _frame: object) -> None:
        latched.append(number)
        raise Interrupted(number)
    try:
        for number in SIGNALS:
            signal.signal(number, handle)
        paths = layout(source=internal_source)
        {
            "install": install,
            "refresh": refresh,
            "uninstall": uninstall,
            "status": status,
            "run": run,
        }[command](paths)
        if latched:
            raise Interrupted(next(number for number in SIGNALS if number in latched))
        # One process-owned completion snapshot for both success and failure.
        _complete(0, latched)
    except Interrupted as exc:
        latched.append(exc.signum)
        _complete(SIGNAL_EXIT[exc.signum], latched)
    except (NotifierError, OSError, subprocess.SubprocessError, ValueError) as exc:
        print(f"update notifier: {exc}", file=sys.stderr)
        _complete(2, latched)
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
