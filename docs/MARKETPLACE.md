# Codex marketplace contract

This repository carries a repo-scoped Codex marketplace descriptor at
`.agents/plugins/marketplace.json`. It describes the existing
`.codex-plugin/plugin.json` package; it does not install the plugin, change a user
configuration or cache, publish a listing, or prove that any external marketplace is
enabled.

The sole entry is `codex-agy-worker`. Its `source.source` is `local` and its
`source.path` is exactly `.`. The package is therefore the repository root: its name
must equal `.codex-plugin/plugin.json`'s `name`, and it uses the one canonical
`skills/agy-worker/` bundle and its bundled `runtime/`. Do not introduce a
`plugins/` copy, a second skill source, or a second runtime to satisfy marketplace
layout conventions.

The packaging contract rejects a missing or symlinked plugin manifest, a mismatched
name, alternate or escaping source paths, duplicate skill/runtime trees, and changed
installed runtime bytes. `install.sh` remains the explicit local skill-installation
path; its copied skill bundle must remain byte-identical to the source bundle (apart
from the local `.pipeline-root` marker).

Validate the package manifest before a release-oriented review with the installed
plugin-creator skill's `scripts/validate_plugin.py`, passing this repository root as
its argument. For example, from that skill directory:

```bash
python3 scripts/validate_plugin.py <repository-root>
```

That validation is local package-shape evidence only. Adding this repository as a
marketplace, installing it into Codex, or publishing it remains a separately approved
external action.
