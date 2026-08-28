#!/usr/bin/env python3
"""Process-inert reprofile preparation for an existing 1.1.22 capture profile.

This adapter accepts an already-validated prior 1.1.22 capture profile and
produces a new profile reflecting exactly one permitted change:
``account_home_identity.nlink``.  It reuses derivation and bounded recovery
validation from the fixed 1.1.22 profile module, follows its publication pattern, and has no
subprocess, network, Git, environment discovery, account HOME enumeration, retry,
capture, inventory acceptance, routing, model selection, metadata update, or
activation authority.  It does not renew any prior one-call authorization.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import signal
import stat
import sys
from typing import Optional, Sequence

sys.dont_write_bytecode = True

RUNTIME_MAJOR = 3
RUNTIME_MINOR = 9
PROFILE_LIMIT = 16_384
OUTPUT_NAME = "models.capture.1.1.22.reprofile.json"
LIFECYCLE_SIGNALS = (signal.SIGHUP, signal.SIGINT, signal.SIGTERM)
NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
DIRECTORY = getattr(os, "O_DIRECTORY", 0)
CLOEXEC = getattr(os, "O_CLOEXEC", 0)

ACTIVE_REPROFILE_PATH: Optional[str] = None
ACTIVE_REPROFILE_IDENTITY: Optional[dict] = None
ACTIVE_REPROFILE_DIGEST: Optional[str] = None

# Keys for the closed prepare request.
PREPARE_KEYS = frozenset({"prior_profile_path", "prior_profile_sha256", "output_path"})
# Keys for the closed validate request.
VALIDATE_KEYS = frozenset({"prior_profile_path", "prior_profile_sha256", "output_path",
                           "profile_path", "profile_sha256"})

# Stable account-identity fields that must remain exact across reprofile.
STABLE_ACCOUNT_FIELDS = ("dev", "gid", "ino", "mode", "uid")


class ReprofileError(ValueError):
    pass


class Interrupted(SystemExit):
    def __init__(self, signum: int):
        super().__init__(128 + signum)
        self.signum = signum


def _runtime_supported() -> bool:
    return (sys.implementation.name == "cpython" and sys.version_info[:2] == (RUNTIME_MAJOR, RUNTIME_MINOR)
            and sys.flags.isolated == 1 and sys.flags.no_site == 1 and sys.flags.dont_write_bytecode == 1 and sys.flags.ignore_environment == 1)


class Signals:
    def __init__(self, owned: Sequence[signal.Signals]):
        self.owned, self.seen, self.selected = tuple(owned), set(), None
    def latch(self, signum: int, _frame: object = None) -> None:
        if signum in self.owned:
            self.seen.add(signum)
    def poll(self) -> None:
        if self.selected is None:
            self.selected = next((item for item in self.owned if item in self.seen), None)
        if self.selected is not None:
            raise Interrupted(self.selected)


class Lifecycle:
    def __init__(self, signals: Signals, mask: set, handlers: dict):
        self.signals = signals
        self.mask = mask
        self.handlers = handlers


def _acquire() -> Lifecycle:
    if not all(hasattr(signal, item) for item in ("pthread_sigmask", "sigpending", "sigwait")):
        raise ReprofileError("required signal primitives are unavailable")
    mask = signal.pthread_sigmask(signal.SIG_BLOCK, LIFECYCLE_SIGNALS)
    handlers = {item: signal.getsignal(item) for item in LIFECYCLE_SIGNALS}
    owned = tuple(item for item in LIFECYCLE_SIGNALS if item not in mask and handlers[item] is not signal.SIG_IGN)
    state = Lifecycle(Signals(owned), mask, handlers)
    try:
        for item in owned:
            signal.signal(item, state.signals.latch)
        pending = set(signal.sigpending()).intersection(owned)
        for item in owned:
            if item in pending:
                state.signals.latch(signal.sigwait({item}))
        signal.pthread_sigmask(signal.SIG_SETMASK, mask)
        state.signals.poll()
        return state
    except BaseException:
        for item in owned:
            signal.signal(item, handlers[item])
        signal.pthread_sigmask(signal.SIG_SETMASK, mask)
        raise


# ---------------------------------------------------------------------------
# Import the fixed 1.1.22 profile and runner modules for reuse of validation
# primitives. This adapter owns its publication implementation.
# ---------------------------------------------------------------------------

def _load_profile_module() -> object:
    """Load the fixed 1.1.22 profile module from the adjacent source file and validate its held source contract."""
    import importlib.util
    source_dir = os.path.dirname(os.path.abspath(__file__))
    profile_path = os.path.join(source_dir, "models_capture_1_1_22_profile.py")
    module_name = "_models_capture_1_1_22_profile"
    spec = importlib.util.spec_from_file_location(module_name, profile_path)
    if spec is None or spec.loader is None:
        raise ReprofileError("cannot locate fixed 1.1.22 profile module")
    mod = importlib.util.module_from_spec(spec)
    previous = sys.modules.get(module_name)
    sys.modules[module_name] = mod
    try:
        spec.loader.exec_module(mod)
        mod._held_source()
    except BaseException as exc:
        if previous is None:
            sys.modules.pop(module_name, None)
        else:
            sys.modules[module_name] = previous
        if isinstance(exc, (getattr(mod, "ProfileError", ()), OSError, ValueError)):
            raise ReprofileError("profile module validation failed: %s" % exc) from exc
        raise
    return mod

_profile_mod: Optional[object] = None

def _get_profile_mod() -> object:
    global _profile_mod
    if _profile_mod is None:
        _profile_mod = _load_profile_module()
    return _profile_mod


def _load_runner_module() -> object:
    """Load the fixed 1.1.22 runner module from the adjacent source file and validate its held source contract."""
    import importlib.util
    source_dir = os.path.dirname(os.path.abspath(__file__))
    runner_path = os.path.join(source_dir, "models_capture_1_1_22_runner.py")
    module_name = "_models_capture_1_1_22_runner"
    spec = importlib.util.spec_from_file_location(module_name, runner_path)
    if spec is None or spec.loader is None:
        raise ReprofileError("cannot locate fixed 1.1.22 runner module")
    mod = importlib.util.module_from_spec(spec)
    previous = sys.modules.get(module_name)
    sys.modules[module_name] = mod
    try:
        spec.loader.exec_module(mod)
        mod._held_source()
    except BaseException as exc:
        if previous is None:
            sys.modules.pop(module_name, None)
        else:
            sys.modules[module_name] = previous
        if isinstance(exc, (getattr(mod, "CaptureError", ()), OSError, ValueError)):
            raise ReprofileError("runner module validation failed: %s" % exc) from exc
        raise
    return mod

_runner_mod: Optional[object] = None

def _get_runner_mod() -> object:
    global _runner_mod
    if _runner_mod is None:
        _runner_mod = _load_runner_module()
    return _runner_mod


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii") + b"\n"


def _json(data: bytes) -> object:
    if len(data) > PROFILE_LIMIT or not data:
        raise ReprofileError("JSON input is invalid")
    def pairs(items: list) -> dict:
        result: dict = {}
        for key, value in items:
            if key in result:
                raise ReprofileError("JSON input has duplicate keys")
            result[key] = value
        return result
    try:
        value = json.loads(data.decode("utf-8", "strict"), object_pairs_hook=pairs)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReprofileError("JSON input is invalid") from exc
    return value


def _absolute(value: object) -> str:
    if not isinstance(value, str) or not value or not os.path.isabs(value) or os.path.normpath(value) != value or os.path.realpath(value) != value:
        raise ReprofileError("path is not canonical and absolute")
    return value


def _sha(value: object) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(item not in "0123456789abcdef" for item in value):
        raise ReprofileError("digest is invalid")
    return value


def _disjoint(first: str, second: str) -> bool:
    return os.path.commonpath((first, second)) not in (first, second)


def _validate_output_root(output_parent: str, prior_path: str, prior_profile: object) -> None:
    """Keep output control disjoint from every authority held by the prior profile."""
    mod = _get_profile_mod()
    authorities = (
        prior_path,
        os.path.dirname(prior_path),
        prior_profile.account_home,
        prior_profile.capture_parent,
        prior_profile.version_root,
        prior_profile.source_path,
        prior_profile.snapshot_path,
        *(os.path.join(prior_profile.version_root, name) for name in mod.RECOVERY_SCRATCH),
    )
    if any(not _disjoint(output_parent, authority) for authority in authorities):
        raise ReprofileError("output control root is not path-disjoint from held authorities")


def _open_directory(path: str, private: bool) -> tuple[int, dict]:
    _absolute(path)
    import pathlib
    fd = os.open("/", os.O_RDONLY | DIRECTORY | CLOEXEC)
    try:
        for item in pathlib.PurePosixPath(path).parts[1:]:
            next_fd = os.open(item, os.O_RDONLY | DIRECTORY | CLOEXEC | NOFOLLOW, dir_fd=fd)
            os.close(fd); fd = next_fd
        observed = os.fstat(fd)
        if not stat.S_ISDIR(observed.st_mode) or observed.st_uid != os.getuid() or (private and (stat.S_IMODE(observed.st_mode) != 0o700 or observed.st_nlink < 1)):
            raise ReprofileError("directory authority changed")
        return fd, _stat_identity(observed)
    except BaseException:
        os.close(fd)
        raise


def _read_prior_profile(path: str, expected_sha: str) -> bytes:
    """Read and validate the prior profile file with strict ownership/mode/nlink/size checks."""
    _absolute(path)
    parent, leaf = os.path.split(path)
    parent_fd, parent_stat = _open_directory(parent, True)
    try:
        fd = os.open(leaf, os.O_RDONLY | CLOEXEC | NOFOLLOW, dir_fd=parent_fd)
    finally:
        os.close(parent_fd)
    try:
        item = os.fstat(fd)
        if not stat.S_ISREG(item.st_mode):
            raise ReprofileError("prior profile is not a regular file")
        if item.st_uid != os.getuid():
            raise ReprofileError("prior profile is not owner-owned")
        if stat.S_IMODE(item.st_mode) != 0o600:
            raise ReprofileError("prior profile mode is not 0600")
        if item.st_nlink != 1:
            raise ReprofileError("prior profile has wrong link count")
        if item.st_size > PROFILE_LIMIT or item.st_size <= 0:
            raise ReprofileError("prior profile size is out of bounds")
        real = os.path.realpath(path)
        if real != path:
            raise ReprofileError("prior profile path is not canonical")
        data = os.read(fd, PROFILE_LIMIT + 1)
        if len(data) != item.st_size or len(data) > PROFILE_LIMIT:
            raise ReprofileError("prior profile read size mismatch")
        actual_sha = hashlib.sha256(data).hexdigest()
        if actual_sha != expected_sha:
            raise ReprofileError("prior profile SHA-256 mismatch")
    finally:
        os.close(fd)
    return data


def _get_account_home_identity(account_home: str) -> dict:
    """Open the account HOME for no-follow descriptor metadata only; never enumerate or read it."""
    _absolute(account_home)
    import pathlib
    fd = os.open("/", os.O_RDONLY | DIRECTORY | CLOEXEC)
    try:
        for item in pathlib.PurePosixPath(account_home).parts[1:]:
            next_fd = os.open(item, os.O_RDONLY | DIRECTORY | CLOEXEC | NOFOLLOW, dir_fd=fd)
            os.close(fd); fd = next_fd
        observed = os.fstat(fd)
        if not stat.S_ISDIR(observed.st_mode) or observed.st_uid != os.getuid() or stat.S_IMODE(observed.st_mode) != 0o700 or observed.st_nlink < 1:
            raise ReprofileError("account HOME authority changed")
        identity = {
            "dev": observed.st_dev,
            "gid": observed.st_gid,
            "ino": observed.st_ino,
            "mode": stat.S_IMODE(observed.st_mode),
            "nlink": observed.st_nlink,
            "uid": observed.st_uid,
        }
    finally:
        os.close(fd)
    return identity


def _validate_single_field_delta(prior_profile: dict, current_identity: dict) -> None:
    """Validate that exactly one permitted delta exists: account_home_identity.nlink."""
    prior_identity = prior_profile["account_home_identity"]

    # Every stable account field must be identical.
    for field in STABLE_ACCOUNT_FIELDS:
        if prior_identity[field] != current_identity[field]:
            raise ReprofileError("stable account identity field %r changed" % field)

    # nlink must be positive in both and different.
    prior_nlink = prior_identity["nlink"]
    current_nlink = current_identity["nlink"]
    if not isinstance(prior_nlink, int) or prior_nlink <= 0:
        raise ReprofileError("prior account HOME nlink is not positive")
    if not isinstance(current_nlink, int) or current_nlink <= 0:
        raise ReprofileError("current account HOME nlink is not positive")
    if prior_nlink == current_nlink:
        raise ReprofileError("account HOME nlink has not changed")


def _publish(path: str, data: bytes, signals: Optional[Signals]) -> str:
    """Publish the new profile using the repository's hard-link/fsync/signal-safe rollback pattern."""
    global ACTIVE_REPROFILE_PATH, ACTIVE_REPROFILE_IDENTITY, ACTIVE_REPROFILE_DIGEST
    parent_path, name = os.path.split(path)
    parent_fd, _ = _open_directory(parent_path, True)
    temporary = ".models.capture.reprofile." + os.urandom(16).hex()
    temporary_identity = None
    final_identity = None
    try:
        # No-overwrite check
        try:
            os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            raise ReprofileError("reprofile output already exists")
        except FileNotFoundError:
            pass
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | CLOEXEC | NOFOLLOW, 0o600, dir_fd=parent_fd)
        try:
            temporary_identity = _stat_identity(os.fstat(fd))
            if signals:
                signals.poll()
            pending = memoryview(data)
            while pending:
                if signals:
                    signals.poll()
                count = os.write(fd, pending)
                if count <= 0:
                    raise ReprofileError("reprofile publication write failed")
                pending = pending[count:]
            os.fsync(fd)
            item = os.fstat(fd)
            if stat.S_IMODE(item.st_mode) != 0o600 or item.st_nlink != 1 or item.st_size != len(data):
                raise ReprofileError("reprofile publication changed")
        finally:
            os.close(fd)
        if signals:
            signals.poll()
        os.link(temporary, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd, follow_symlinks=False)
        final = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        final_identity = _stat_identity(final)
        if final_identity["dev"] != temporary_identity["dev"] or final_identity["ino"] != temporary_identity["ino"] or final.st_nlink != 2 or stat.S_IMODE(final.st_mode) != 0o600:
            raise ReprofileError("reprofile publication changed")
        temporary_after_link = _stat_identity(os.stat(temporary, dir_fd=parent_fd, follow_symlinks=False))
        if temporary_after_link != final_identity:
            raise ReprofileError("reprofile publication changed")
        temporary_identity = temporary_after_link
        os.unlink(temporary, dir_fd=parent_fd)
        final = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        final_identity = _stat_identity(final)
        if final_identity["dev"] != temporary_identity["dev"] or final_identity["ino"] != temporary_identity["ino"] or final.st_nlink != 1:
            raise ReprofileError("reprofile publication changed")
        if signals:
            signals.poll()
        os.fsync(parent_fd)
        if signals:
            signals.poll()
        os.fsync(parent_fd)
        final_fd = os.open(name, os.O_RDONLY | CLOEXEC | NOFOLLOW, dir_fd=parent_fd)
        try:
            if _stat_identity(os.fstat(final_fd)) != final_identity or os.read(final_fd, len(data) + 1) != data:
                raise ReprofileError("reprofile publication changed")
        finally:
            os.close(final_fd)
        digest = hashlib.sha256(data).hexdigest()
        ACTIVE_REPROFILE_PATH, ACTIVE_REPROFILE_IDENTITY, ACTIVE_REPROFILE_DIGEST = path, final_identity, digest
        return digest
    except BaseException:
        # Rollback: clean up temporary and final names if they exist and match our expectations.
        if final_identity is not None and temporary_identity is not None:
            try:
                final_now = _stat_identity(os.stat(name, dir_fd=parent_fd, follow_symlinks=False))
                temporary_now = _stat_identity(os.stat(temporary, dir_fd=parent_fd, follow_symlinks=False))
                if final_now == final_identity and temporary_now == temporary_identity and os.stat(name, dir_fd=parent_fd, follow_symlinks=False).st_nlink == 2 and os.stat(temporary, dir_fd=parent_fd, follow_symlinks=False).st_nlink == 2:
                    os.unlink(name, dir_fd=parent_fd)
                    derived = _stat_identity(os.stat(temporary, dir_fd=parent_fd, follow_symlinks=False))
                    if (derived["dev"] == temporary_identity["dev"] and derived["ino"] == temporary_identity["ino"]
                            and derived["uid"] == temporary_identity["uid"] and os.stat(temporary, dir_fd=parent_fd, follow_symlinks=False).st_nlink == 1):
                        if _stat_identity(os.stat(temporary, dir_fd=parent_fd, follow_symlinks=False)) == derived:
                            os.unlink(temporary, dir_fd=parent_fd)
                    temporary_identity = None
            except FileNotFoundError:
                pass
        for leaf, expected in ((name, final_identity), (temporary, temporary_identity)):
            try:
                item = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
                observed = _stat_identity(item)
                if expected is not None and observed == expected and stat.S_ISREG(item.st_mode):
                    os.unlink(leaf, dir_fd=parent_fd)
            except FileNotFoundError:
                pass
        os.fsync(parent_fd)
        raise
    finally:
        os.close(parent_fd)


def _stat_identity(item: os.stat_result) -> dict:
    return {
        "ctime_ns": item.st_ctime_ns,
        "dev": item.st_dev,
        "gid": item.st_gid,
        "ino": item.st_ino,
        "mode": stat.S_IMODE(item.st_mode),
        "mtime_ns": item.st_mtime_ns,
        "nlink": item.st_nlink,
        "size": item.st_size,
        "uid": item.st_uid,
    }


def _validate_runner_preflight(profile_data: bytes, signals: Optional[Signals] = None) -> None:
    """Validate that the canonical profile bytes pass the unchanged runner's child-free profile preflight."""
    runner_mod = _get_runner_mod()
    runner_profile = runner_mod.Profile.from_bytes(profile_data)
    fds = runner_mod._validate_profile(runner_profile, signals)
    for fd in fds:
        os.close(fd)


def _validate_prior_sources(prior_profile: object) -> object:
    """Revalidate every source, snapshot, capture-parent, version-root, exact recovery binding,
    file identity, and self-pin through the fixed profile module, returning the rederived profile.

    Capture-parent link count is diagnostic under the fixed profile contract: a
    failed capture may leave an owned child directory without changing the
    parent's authority.  Validate the fixed stable fields, then retain the prior
    capture-parent identity so the published profile records only the requested
    account-HOME nlink change.
    """
    mod = _get_profile_mod()
    request = {
        "account_home": prior_profile.account_home,
        "capture_parent": prior_profile.capture_parent,
        "output_path": os.path.join(prior_profile.capture_parent, mod.OUTPUT_NAME),
        "snapshot_path": prior_profile.snapshot_path,
        "source_path": prior_profile.source_path,
        "version_root": prior_profile.version_root,
    }
    try:
        rederived, _ = mod._from_request(request)
    except (mod.ProfileError, OSError, ValueError) as exc:
        raise ReprofileError("prior profile source validation failed: %s" % exc) from exc

    prior_dict = dataclasses.asdict(prior_profile)
    rederived_dict = dataclasses.asdict(rederived)

    if prior_dict.keys() != rederived_dict.keys():
        raise ReprofileError("rederived profile keys differ")

    for key, rederived_value in rederived_dict.items():
        prior_value = prior_dict[key]
        if key == "account_home_identity":
            if not isinstance(prior_value, dict) or not isinstance(rederived_value, dict):
                raise ReprofileError("account_home_identity is invalid")
            _validate_single_field_delta(prior_dict, rederived_value)
        elif key == "capture_parent_identity":
            if not mod._same_capture_parent(
                    rederived.capture_parent_identity,
                    prior_profile.capture_parent_identity):
                raise ReprofileError("rederived capture_parent_identity differs")
        else:
            if prior_value != rederived_value:
                raise ReprofileError(f"rederived {key} differs")

    return dataclasses.replace(
        rederived,
        capture_parent_identity=prior_profile.capture_parent_identity,
    )


def prepare(data: bytes, signals: Optional[Signals] = None) -> dict:
    """Prepare a new reprofiled 1.1.22 capture profile."""
    value = _json(data)
    if not isinstance(value, dict) or set(value) != PREPARE_KEYS:
        raise ReprofileError("prepare request is invalid")

    prior_path = _absolute(value["prior_profile_path"])
    prior_sha = _sha(value["prior_profile_sha256"])
    output = _absolute(value["output_path"])

    # Validate output basename
    if os.path.basename(output) != OUTPUT_NAME:
        raise ReprofileError("output basename must be %r" % OUTPUT_NAME)

    output_parent = os.path.dirname(output)
    # Read and validate the prior profile bytes
    prior_data = _read_prior_profile(prior_path, prior_sha)

    # Validate prior profile satisfies 12-field schemas
    mod = _get_profile_mod()
    runner_mod = _get_runner_mod()
    prior_profile = mod.CaptureProfile.from_bytes(prior_data)
    runner_mod.Profile.from_bytes(prior_data)

    # Validate output control root is path-disjoint from all held authorities.
    _validate_output_root(output_parent, prior_path, prior_profile)

    # Rederive current profile through fixed profile module and validate all fields
    rederived = _validate_prior_sources(prior_profile)

    # Build the new profile canonical bytes from the rederived profile
    new_data = _canonical(dataclasses.asdict(rederived))

    # Verify new profile satisfies schemas and passes runner's child-free preflight
    mod.CaptureProfile.from_bytes(new_data)
    _validate_runner_preflight(new_data, signals)

    # Revalidate the prior profile is unchanged after our work
    recheck = _read_prior_profile(prior_path, prior_sha)
    if recheck != prior_data:
        raise ReprofileError("prior profile changed during reprofile")

    # Revalidate current account identity is unchanged
    recheck_identity = _get_account_home_identity(prior_profile.account_home)
    if recheck_identity != dataclasses.asdict(rederived.account_home_identity):
        raise ReprofileError("account HOME identity changed during reprofile")

    # Publish the new profile
    new_sha = _publish(output, new_data, signals)

    # Post-publication verification wrapped with rollback protection
    try:
        if signals:
            signals.poll()
        output_parent_path = os.path.dirname(output)
        output_leaf = os.path.basename(output)
        out_parent_fd, _ = _open_directory(output_parent_path, True)
        try:
            out_fd = os.open(output_leaf, os.O_RDONLY | CLOEXEC | NOFOLLOW, dir_fd=out_parent_fd)
            try:
                out_stat = os.fstat(out_fd)
                if not stat.S_ISREG(out_stat.st_mode) or out_stat.st_uid != os.getuid() or stat.S_IMODE(out_stat.st_mode) != 0o600 or out_stat.st_nlink != 1:
                    raise ReprofileError("output verification failed")
                readback = os.read(out_fd, len(new_data) + 1)
                if readback != new_data:
                    raise ReprofileError("output verification failed")
            finally:
                os.close(out_fd)
        finally:
            os.close(out_parent_fd)

        # Rerun runner preflight after publication on verified readback bytes
        _validate_runner_preflight(readback, signals)

        # Revalidate the prior profile one final time
        final_check = _read_prior_profile(prior_path, prior_sha)
        if final_check != prior_data:
            raise ReprofileError("prior profile changed after publication")
        if signals:
            signals.poll()
    except BaseException:
        _rollback_active_reprofile()
        raise

    return {
        "activation_authorized": False,
        "capture_authorized": False,
        "changed_fields": ["account_home_identity.nlink"],
        "models_called": False,
        "new_profile_sha256": new_sha,
        "prior_profile_sha256": prior_sha,
        "provider_contacted": False,
        "retry_authorized": False,
        "status": "reprofiled",
    }


def validate(data: bytes) -> dict:
    """Validate a prior+new profile pair for nlink-only reprofile ancestry."""
    value = _json(data)
    if not isinstance(value, dict) or set(value) != VALIDATE_KEYS:
        raise ReprofileError("validate request is invalid")

    prior_path = _absolute(value["prior_profile_path"])
    prior_sha = _sha(value["prior_profile_sha256"])
    output = _absolute(value["output_path"])
    profile_path = _absolute(value["profile_path"])
    profile_sha = _sha(value["profile_sha256"])

    # Validate basenames
    if os.path.basename(profile_path) != OUTPUT_NAME:
        raise ReprofileError("profile basename must be %r" % OUTPUT_NAME)

    # Validate output path matches profile path
    if output != profile_path:
        raise ReprofileError("output_path must match profile_path")

    output_parent = os.path.dirname(output)
    # Read and validate prior profile
    prior_data = _read_prior_profile(prior_path, prior_sha)
    mod = _get_profile_mod()
    runner_mod = _get_runner_mod()
    prior_profile = mod.CaptureProfile.from_bytes(prior_data)
    runner_mod.Profile.from_bytes(prior_data)

    # Validate output control root is path-disjoint from all held authorities.
    _validate_output_root(output_parent, prior_path, prior_profile)

    # Read the new profile
    new_parent_fd, _ = _open_directory(output_parent, True)
    try:
        new_fd = os.open(os.path.basename(profile_path), os.O_RDONLY | CLOEXEC | NOFOLLOW, dir_fd=new_parent_fd)
        try:
            new_stat = os.fstat(new_fd)
            if not stat.S_ISREG(new_stat.st_mode) or new_stat.st_uid != os.getuid() or stat.S_IMODE(new_stat.st_mode) != 0o600 or new_stat.st_nlink != 1 or new_stat.st_size > PROFILE_LIMIT:
                raise ReprofileError("new profile authority changed")
            new_data = os.read(new_fd, PROFILE_LIMIT + 1)
            if len(new_data) != new_stat.st_size or len(new_data) > PROFILE_LIMIT:
                raise ReprofileError("new profile read size mismatch")
        finally:
            os.close(new_fd)
    finally:
        os.close(new_parent_fd)

    actual_sha = hashlib.sha256(new_data).hexdigest()
    if actual_sha != profile_sha:
        raise ReprofileError("new profile SHA-256 mismatch")

    # Parse new profile and validate runner preflight
    new_profile = mod.CaptureProfile.from_bytes(new_data)
    _validate_runner_preflight(new_data, None)

    # Rederive and validate all sources through profile module
    rederived = _validate_prior_sources(prior_profile)

    # Verify new profile matches rederived exactly
    if dataclasses.asdict(new_profile) != dataclasses.asdict(rederived):
        raise ReprofileError("new profile does not match rederived profile")

    return {
        "activation_authorized": False,
        "capture_authorized": False,
        "changed_fields": ["account_home_identity.nlink"],
        "models_called": False,
        "new_profile_sha256": profile_sha,
        "prior_profile_sha256": prior_sha,
        "provider_contacted": False,
        "retry_authorized": False,
        "status": "valid",
    }


def validate_source_contract(data: bytes) -> dict:
    """Validate the reprofile adapter source contract."""
    import ast
    try:
        tree = ast.parse(data.decode("utf-8", "strict"))
    except (UnicodeDecodeError, SyntaxError) as exc:
        raise ReprofileError("reprofile source invalid") from exc

    forbidden_modules = {
        "subprocess", "socket", "urllib", "http", "asyncio", "selectors",
        "ftplib", "smtplib", "xmlrpc", "multiprocessing", "threading",
    }

    glob_aliases = set()
    os_aliases = set()
    pathlib_aliases = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in forbidden_modules:
                    raise ReprofileError("reprofile source gained process or network authority")
                if root == "os":
                    os_aliases.add(alias.asname or root)
                elif root == "glob":
                    glob_aliases.add(alias.asname or root)
                elif root == "pathlib":
                    pathlib_aliases.add(alias.asname or root)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                root = node.module.split(".")[0]
                if root in forbidden_modules:
                    raise ReprofileError("reprofile source gained process or network authority")
            allowed_from = {
                ("__future__", "annotations"),
                ("typing", "Optional"),
                ("typing", "Sequence"),
            }
            for alias in node.names:
                if (node.module, alias.name) not in allowed_from:
                    raise ReprofileError("reprofile source gained imported authority")

    forbidden_direct = {
        "eval", "exec", "compile", "setattr", "delattr", "globals", "locals",
        "vars", "__import__", "open_code", "scandir", "listdir", "walk",
        "glob", "iglob", "iterdir", "rglob",
        "Popen", "popen", "system", "spawn", "spawnl", "spawnv", "execl", "execv", "fork", "kill",
    }
    if any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in forbidden_direct for node in ast.walk(tree)):
        raise ReprofileError("reprofile source gained dynamic, discovery, or process authority")

    forbidden_attr = {
        "scandir", "listdir", "walk", "Popen", "popen", "system", "run", "call",
        "check_call", "check_output", "spawn", "spawnl", "spawnv", "execl", "execv", "fork", "kill",
    }

    def enumerates_filesystem(node: object) -> bool:
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            return False
        receiver = node.func.value
        if isinstance(receiver, ast.Name) and receiver.id in os_aliases:
            return node.func.attr in {"scandir", "listdir", "walk"}
        if isinstance(receiver, ast.Name) and receiver.id in glob_aliases:
            return node.func.attr in {"glob", "iglob"}
        if node.func.attr not in {"iterdir", "glob", "rglob"} or not isinstance(receiver, ast.Call):
            return False
        constructor = receiver.func
        return (isinstance(constructor, ast.Attribute) and constructor.attr == "Path"
                and isinstance(constructor.value, ast.Name)
                and constructor.value.id in pathlib_aliases)

    functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    account_identity = functions.get("_get_account_home_identity")
    if account_identity is not None and any(enumerates_filesystem(node) for node in ast.walk(account_identity)):
        raise ReprofileError("reprofile source enumerates account HOME")

    for node in ast.walk(tree):
        if enumerates_filesystem(node):
            raise ReprofileError("reprofile source gained filesystem enumeration authority")
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        receiver = node.func.value
        process_prefix = node.func.attr.startswith(("exec", "spawn", "fork"))
        if (isinstance(receiver, ast.Name) and receiver.id in os_aliases
                and (node.func.attr in forbidden_attr or process_prefix or node.func.attr == "killpg")):
            raise ReprofileError("reprofile source gained discovery or process authority")

    return {"status": "valid-source"}


def _held_source() -> bytes:
    path = _absolute(os.path.realpath(__file__))
    parent, name = os.path.split(path)
    parent_fd, _ = _open_directory(parent, False)
    try:
        fd = os.open(name, os.O_RDONLY | CLOEXEC | NOFOLLOW, dir_fd=parent_fd)
    finally:
        os.close(parent_fd)
    try:
        item = os.fstat(fd)
        if not stat.S_ISREG(item.st_mode) or item.st_uid != os.getuid() or stat.S_IMODE(item.st_mode) & 0o022 or item.st_size <= 0 or item.st_size > 128 * 1024:
            raise ReprofileError("reprofile source authority changed")
        data = os.read(fd, item.st_size + 1)
        if len(data) != item.st_size or os.fstat(fd).st_ino != item.st_ino:
            raise ReprofileError("reprofile source authority changed")
    finally:
        os.close(fd)
    validate_source_contract(data)
    return data


def _rollback_active_reprofile() -> None:
    global ACTIVE_REPROFILE_PATH, ACTIVE_REPROFILE_IDENTITY, ACTIVE_REPROFILE_DIGEST
    if ACTIVE_REPROFILE_PATH is None or ACTIVE_REPROFILE_IDENTITY is None or ACTIVE_REPROFILE_DIGEST is None:
        return
    path = ACTIVE_REPROFILE_PATH
    expected_identity = ACTIVE_REPROFILE_IDENTITY
    expected_digest = ACTIVE_REPROFILE_DIGEST
    ACTIVE_REPROFILE_PATH, ACTIVE_REPROFILE_IDENTITY, ACTIVE_REPROFILE_DIGEST = None, None, None
    parent, leaf = os.path.split(path)
    try:
        fd, _ = _open_directory(parent, True)
    except (ReprofileError, OSError):
        return
    try:
        try:
            held = os.open(leaf, os.O_RDONLY | CLOEXEC | NOFOLLOW, dir_fd=fd)
            try:
                item = os.fstat(held)
                current = _stat_identity(item)
                path_identity = _stat_identity(os.stat(leaf, dir_fd=fd, follow_symlinks=False))
                data = os.read(held, item.st_size + 1)
                if (stat.S_ISREG(item.st_mode) and current == expected_identity and path_identity == current
                        and len(data) == item.st_size and hashlib.sha256(data).hexdigest() == expected_digest):
                    os.unlink(leaf, dir_fd=fd)
                    os.fsync(fd)
            finally:
                os.close(held)
        except FileNotFoundError:
            pass
    finally:
        os.close(fd)


def _read_stdin(limit: int = PROFILE_LIMIT, signals: Optional[Signals] = None) -> bytes:
    chunks = bytearray()
    while len(chunks) <= limit:
        if signals:
            signals.poll()
        block = os.read(sys.stdin.buffer.fileno(), min(4096, limit + 1 - len(chunks)))
        if not block:
            return bytes(chunks)
        chunks.extend(block)
    raise ReprofileError("stdin exceeds bound")


def _finish_success(state: Lifecycle, result: dict) -> None:
    try:
        payload = _canonical(result)
        if sys.stdout.buffer.write(payload) != len(payload):
            raise OSError("completion output write failed")
        sys.stdout.buffer.flush()
        signal.pthread_sigmask(signal.SIG_BLOCK, state.signals.owned)
        pending = set(signal.sigpending()).intersection(state.signals.owned)
        for item in state.signals.owned:
            if item in pending:
                state.signals.latch(signal.sigwait({item}))
        state.signals.poll()
    except Interrupted as exc:
        try:
            _rollback_active_reprofile()
        except BaseException:
            pass
        os._exit(exc.code)
    except BaseException:
        try:
            signal.pthread_sigmask(signal.SIG_BLOCK, state.signals.owned)
        except BaseException:
            pass
        try:
            pending = set(signal.sigpending()).intersection(state.signals.owned)
            for item in state.signals.owned:
                if item in pending:
                    state.signals.latch(signal.sigwait({item}))
        except BaseException:
            pass
        try:
            _rollback_active_reprofile()
        except BaseException:
            pass
        try:
            pending = set(signal.sigpending()).intersection(state.signals.owned)
            for item in state.signals.owned:
                if item in pending:
                    state.signals.latch(signal.sigwait({item}))
        except BaseException:
            pass
        try:
            state.signals.poll()
        except Interrupted as exc:
            os._exit(exc.code)
        os._exit(1)
    os._exit(0)


def main(argv: Sequence[str]) -> int:
    if not _runtime_supported():
        return 64
    if len(argv) != 1 or argv[0] not in ("--prepare", "--validate", "--validate-source-contract"):
        return 64
    state = _acquire()
    try:
        _held_source()
        raw = _read_stdin(128 * 1024 if argv[0] == "--validate-source-contract" else PROFILE_LIMIT, state.signals)
        if argv[0] == "--validate-source-contract":
            result = validate_source_contract(raw)
        elif argv[0] == "--prepare":
            result = prepare(raw, state.signals)
        else:
            result = validate(raw)
        _finish_success(state, result)
    except Interrupted as exc:
        return exc.code
    except (OSError, ReprofileError, ValueError):
        return 1


if __name__ == "__main__":
    if not _runtime_supported():
        os._exit(64)
    os._exit(main(sys.argv[1:]))
