# Data-only workload profiles

Workload Profiles v1 reduces repeated typing without becoming a dispatcher or policy
engine. The canonical command is bundled with the public skill; the repository-root
`profile.sh` is only a compatibility wrapper.

```bash
./profile.sh list
./profile.sh show repository-inventory
./profile.sh show diff-review
./profile.sh show bounded-test-backfill
```

Both commands write one canonical JSON value to stdout. `list` returns only the fixed
maintained names and summaries. `show` returns one non-executable skeleton that may
suggest a maintained `plan`/`accept-edits` mode, a maintained persona, and one closed
repo-relative path-policy shape.

Every shown profile says that these inputs remain caller-owned:

- explicit approval;
- the exact repository;
- the exact repo-relative path policy;
- the selected tier; and
- executable verification commands.

Profiles never contain a repository or filesystem path, model, tier, effort or
thinking value, verifier or shell command, external add-dir, authorization, routing,
acceptance, dispatch, or Git action. They do not read a target repository, scan the
home directory, inspect environment variables, invoke git or agy, access a provider
or network, or accept a caller-selected profile file. A shown profile is therefore
not permission to dispatch and cannot satisfy the gate or human diff review.

The v1 manifest binds the exact three canonical data files by SHA-256. The portable
runtime rejects missing, extra, reordered, changed, executable, writable, symlinked,
oversized, or non-canonical profile artifacts and schema drift. Adding or changing a
profile is a versioned source change that must update the semantic allowlist, schema,
manifest bindings, paired accept/reject tests, and documentation together.
