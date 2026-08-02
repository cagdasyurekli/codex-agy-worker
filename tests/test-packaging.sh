#!/usr/bin/env bash
# Offline plugin, marketplace, skill-bundle, and landing-page contract tests.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$HERE/.."
TMP="$(mktemp -d -t agyworker-packaging.XXXXXX)"
trap 'rm -rf "$TMP"' EXIT
pass=0; fail=0

ok() { printf '  ok   %s\n' "$1"; pass=$((pass+1)); }
bad() { printf '  FAIL %s\n' "$1"; fail=$((fail+1)); }

echo "marketplace packaging offline test suite"
echo

if python3 - "$ROOT" <<'PY'
import json
from pathlib import Path
import sys

root = Path(sys.argv[1])
manifest = json.loads((root / ".codex-plugin/plugin.json").read_text())
assert manifest["name"] == "codex-agy-worker"
assert manifest["skills"] == "./skills/"
assert manifest["license"] == "MIT"
assert manifest["interface"]["privacyPolicyURL"].startswith("https://")
assert manifest["interface"]["termsOfServiceURL"].startswith("https://")
assert not ({"apps", "mcpServers", "hooks"} & manifest.keys())
PY
then ok "Codex plugin is a skills-only package with public legal links"; else bad "Codex plugin is a skills-only package with public legal links"; fi

if python3 - "$ROOT" <<'PY'
import json
from pathlib import Path
import sys

manifest = json.loads((Path(sys.argv[1]) / ".claude-plugin/plugin.json").read_text())
assert manifest["name"] == "codex-agy-worker"
assert manifest["skills"] == "./skills/"
assert manifest["agents"] == []
assert manifest["version"] == "0.1.0"
PY
then ok "Claude plugin versions the shared skill and suppresses incompatible agy personas"; else bad "Claude plugin versions the shared skill and suppresses incompatible agy personas"; fi

if python3 - "$ROOT" <<'PY'
import json
from pathlib import Path
import sys

catalog = json.loads((Path(sys.argv[1]) / ".agents/plugins/marketplace.json").read_text())
entry = catalog["plugins"][0]
assert catalog["name"] == "codex-agy-worker"
assert entry["name"] == "codex-agy-worker"
assert entry["source"] == {
    "source": "url",
    "url": "https://github.com/cagdasyurekli/codex-agy-worker.git",
    "ref": "main",
}
assert entry["policy"] == {"installation": "AVAILABLE", "authentication": "ON_INSTALL"}
PY
then ok "Codex marketplace entry is explicit and remotely installable"; else bad "Codex marketplace entry is explicit and remotely installable"; fi

if python3 - "$ROOT" <<'PY'
import json
from pathlib import Path
import sys

catalog = json.loads((Path(sys.argv[1]) / ".claude-plugin/marketplace.json").read_text())
entry = catalog["plugins"][0]
assert catalog["name"] == "codex-agy-worker"
assert entry["name"] == "codex-agy-worker"
assert entry["source"] == {"source": "github", "repo": "cagdasyurekli/codex-agy-worker"}
PY
then ok "Claude marketplace entry resolves the public repository"; else bad "Claude marketplace entry resolves the public repository"; fi

if python3 - "$ROOT" <<'PY'
from pathlib import Path
import re
import sys

root = Path(sys.argv[1])
skill = (root / "skills/agy-worker/SKILL.md").read_text()
metadata = (root / "skills/agy-worker/agents/openai.yaml").read_text()
assert skill.startswith("---\nname: agy-worker\ndescription:")
assert len(re.search(r"^description: (.+)$", skill, re.M).group(1)) <= 1024
assert 'display_name: "Verified agy Worker"' in metadata
assert "$agy-worker" in metadata
PY
then ok "canonical Agent Skill has matching OpenAI UI metadata"; else bad "canonical Agent Skill has matching OpenAI UI metadata"; fi

if ! grep -R -Fq '__REPO_ROOT__' "$ROOT/skills/agy-worker" \
        && ! grep -R -Fq '/Users/' "$ROOT/skills/agy-worker" \
        && [[ ! -e "$ROOT/skills/agy-worker/.pipeline-root" ]]; then
    ok "public skill bundle contains no checkout placeholder or local path marker"
else
    bad "public skill bundle contains no checkout placeholder or local path marker"
fi

resolved="$(bash "$ROOT/skills/agy-worker/scripts/resolve-pipeline.sh" 2>/dev/null)"
if [[ "$resolved" == "$(cd "$ROOT" && pwd -P)" ]]; then
    ok "plugin-cache resolver finds the adjacent canonical runtime"
else
    bad "plugin-cache resolver finds the adjacent canonical runtime"
fi

mkdir -p "$TMP/bin" "$TMP/installed"
printf '#!/usr/bin/env bash\nexit 0\n' > "$TMP/bin/agy"
chmod +x "$TMP/bin/agy"
PATH="$TMP/bin:$PATH" CODEX_SKILLS_DIR="$TMP/installed" "$ROOT/install.sh" \
    > "$TMP/install.out" 2> "$TMP/install.err"
rc=$?
installed_root=""
if [[ "$rc" == "0" ]]; then
    installed_root="$(bash "$TMP/installed/agy-worker/scripts/resolve-pipeline.sh" 2>/dev/null)"
fi
if [[ "$installed_root" == "$(cd "$ROOT" && pwd -P)" ]]; then
    ok "standalone install resolves the checkout without rewriting SKILL.md"
else
    bad "standalone install resolves the checkout without rewriting SKILL.md"
fi

mkdir -p "$TMP/reject-relative/agy-worker"
cp -R "$ROOT/skills/agy-worker/"* "$TMP/reject-relative/agy-worker/"
printf '../relative\n' > "$TMP/reject-relative/agy-worker/.pipeline-root"
bash "$TMP/reject-relative/agy-worker/scripts/resolve-pipeline.sh" \
    > "$TMP/relative.out" 2> "$TMP/relative.err"
rc=$?
if [[ "$rc" == "2" && ! -s "$TMP/relative.out" ]]; then
    ok "standalone resolver rejects a relative pipeline marker"
else
    bad "standalone resolver rejects a relative pipeline marker"
fi

printf '/definitely/missing/codex-agy-worker\n' > "$TMP/reject-relative/agy-worker/.pipeline-root"
bash "$TMP/reject-relative/agy-worker/scripts/resolve-pipeline.sh" \
    > "$TMP/missing.out" 2> "$TMP/missing.err"
rc=$?
if [[ "$rc" == "2" && ! -s "$TMP/missing.out" ]]; then
    ok "standalone resolver rejects a missing pipeline runtime"
else
    bad "standalone resolver rejects a missing pipeline runtime"
fi

if grep -Fq 'Google/Gemini' "$ROOT/PRIVACY.md" \
        && grep -Fq 'logs/' "$ROOT/PRIVACY.md" \
        && grep -Fq 'GitHub Issues' "$ROOT/SUPPORT.md" \
        && grep -Fq 'not legal advice' "$ROOT/TERMS.md"; then
    ok "public policy pages disclose external transfer, local artifacts, and support"
else
    bad "public policy pages disclose external transfer, local artifacts, and support"
fi

if python3 - "$ROOT/docs/MARKETPLACE.md" <<'PY'
from pathlib import Path
import re
import sys

text = Path(sys.argv[1]).read_text()
positive = re.findall(r"^### Positive [1-5]$", text, re.M)
negative = re.findall(r"^### Negative [1-3]$", text, re.M)
assert len(positive) == 5
assert len(negative) == 3
PY
then ok "submission runbook contains five positive and three negative cases"; else bad "submission runbook contains five positive and three negative cases"; fi

if grep -Fq 'https://cagdasyurekli.github.io/codex-agy-worker/' "$ROOT/docs/_config.yml" \
        && grep -Fq 'canonical' "$ROOT/docs/_layouts/default.html" \
        && grep -Fq '<loc>https://cagdasyurekli.github.io/codex-agy-worker/</loc>' "$ROOT/docs/sitemap.xml" \
        && grep -Fq 'explicitly submit' "$ROOT/docs/MARKETPLACE.md" \
        && [[ ! -e "$ROOT/docs/robots.txt" ]]; then
    ok "GitHub Pages landing exposes a canonical URL and explicitly submitted sitemap"
else
    bad "GitHub Pages landing exposes a canonical URL and explicitly submitted sitemap"
fi

if [[ ! -e "$ROOT/codex-skill/SKILL.md" ]] \
        && [[ -f "$ROOT/skills/agy-worker/SKILL.md" ]]; then
    ok "repository has one canonical skill source"
else
    bad "repository has one canonical skill source"
fi

echo
if (( fail )); then
    echo "FAILED: $fail failed, $pass passed"
    exit 1
fi
echo "PASSED: $pass tests"
