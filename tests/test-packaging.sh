#!/usr/bin/env bash
# Offline Codex package, skill-bundle, and landing-page contract tests.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$HERE/.."
TMP="$(mktemp -d -t agyworker-packaging.XXXXXX)"
trap 'rm -rf "$TMP"' EXIT
pass=0; fail=0

ok() { printf '  ok   %s\n' "$1"; pass=$((pass+1)); }
bad() { printf '  FAIL %s\n' "$1"; fail=$((fail+1)); }

echo "Codex distribution offline test suite"
echo

if python3 - "$ROOT" <<'PY'
import json
from pathlib import Path
import sys

root = Path(sys.argv[1])
manifest = json.loads((root / ".codex-plugin/plugin.json").read_text())
assert manifest["name"] == "codex-agy-worker"
assert manifest["version"] == "0.2.0"
assert manifest["skills"] == "./skills/"
assert manifest["license"] == "MIT"
assert manifest["interface"]["privacyPolicyURL"].startswith("https://")
assert manifest["interface"]["termsOfServiceURL"].startswith("https://")
assert not ({"apps", "mcpServers", "hooks"} & manifest.keys())
PY
then ok "Codex plugin is a skills-only package with public legal links"; else bad "Codex plugin is a skills-only package with public legal links"; fi

if [[ ! -e "$ROOT/.claude-plugin" ]] \
        && [[ ! -e "$ROOT/CLAUDE.md" ]] \
        && [[ ! -e "$ROOT/.agents/plugins/marketplace.json" ]] \
        && [[ ! -e "$ROOT/docs/MARKETPLACE.md" ]]; then
    ok "removed Claude and marketplace distribution surfaces stay absent"
else
    bad "removed Claude and marketplace distribution surfaces stay absent"
fi

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

if [[ -x "$ROOT/doctor.sh" ]] \
        && [[ -x "$ROOT/skills/agy-worker/runtime/doctor.sh" ]] \
        && [[ -x "$ROOT/skills/agy-worker/runtime/scripts/doctor-metadata.py" ]]; then
    ok "root and portable packages include the canonical read-only doctor"
else
    bad "root and portable packages include the canonical read-only doctor"
fi

required_runtime_dependencies=(
    agy-worker.sh
    qa-gate.sh
    model-recommendation.sh
    doctor.sh
    scripts/validate-envelope.py
    scripts/model-recommendation.py
    scripts/doctor-metadata.py
    schemas/worker-result.schema.json
    agents/bulk-test-writer.md
    agents/repo-inventory.md
    agents/diff-reviewer.md
    compat/agy-verified-version.txt
    compat/agy-last-reviewed.txt
)
for dependency in "${required_runtime_dependencies[@]}"; do
    label="${dependency//\//-}"
    dependency_copy="$TMP/missing-$label"
    cp -R "$ROOT/skills/agy-worker" "$dependency_copy"
    rm -f "$dependency_copy/runtime/$dependency"
    PATH="$TMP/no-network-bin:$PATH" NETWORK_MARKER="$TMP/missing-$label.network" \
        bash "$dependency_copy/scripts/resolve-pipeline.sh" \
        > "$TMP/missing-$label.out" 2> "$TMP/missing-$label.err"
    rc=$?
    if [[ "$rc" == 2 && ! -s "$TMP/missing-$label.out" ]] \
            && grep -Fq 'complete agy-worker skill bundle' "$TMP/missing-$label.err" \
            && [[ ! -e "$TMP/missing-$label.network" ]]; then
        ok "resolver rejects a bundle missing $dependency"
    else
        bad "resolver rejects a bundle missing $dependency"
    fi
done

if python3 - "$ROOT" <<'PY'
from pathlib import Path
import re
import sys

root = Path(sys.argv[1])
resolver = (root / "skills/agy-worker/scripts/resolve-pipeline.sh").read_text()
doctor = (root / "skills/agy-worker/runtime/doctor.sh").read_text()

def body(text: str, name: str) -> str:
    match = re.search(rf"^{name}\(\) \{{\n(.*?)^\}}$", text, re.M | re.S)
    assert match is not None
    return match.group(1)

assert body(resolver, "pipeline_runtime_complete") == body(
    doctor, "doctor_runtime_complete"
)
assert "runtime-bundle.sh" not in resolver
assert "runtime-bundle.sh" not in doctor
assert not re.search(r"(?:^|[;&|()]\s*)(?:source|\.)\s+", resolver, re.M)
assert not re.search(r"(?:^|[;&|()]\s*)(?:source|\.)\s+", doctor, re.M)
PY
then ok "resolver and doctor use the same fixed non-sourced runtime predicate"; else bad "resolver and doctor use the same fixed non-sourced runtime predicate"; fi

real_parent_copy="$TMP/real-runtime-parents"
cp -R "$ROOT/skills/agy-worker" "$real_parent_copy"
real_parent_resolved="$(bash "$real_parent_copy/scripts/resolve-pipeline.sh" 2>/dev/null)"
if [[ "$real_parent_resolved" == "$(cd "$real_parent_copy/runtime" && pwd -P)" ]]; then
    ok "resolver accepts bundle-owned real runtime parent directories"
else
    bad "resolver accepts bundle-owned real runtime parent directories"
fi

for parent in scripts agents schemas compat; do
    for link_kind in absolute relative in-root; do
        parent_copy="$TMP/parent-$parent-$link_kind"
        foreign_parent="$TMP/foreign-$parent-$link_kind"
        cp -R "$ROOT/skills/agy-worker" "$parent_copy"
        if [[ "$link_kind" == in-root ]]; then
            mv "$parent_copy/runtime/$parent" \
                "$parent_copy/runtime/owned-$parent"
            ln -s "owned-$parent" "$parent_copy/runtime/$parent"
        else
            mv "$parent_copy/runtime/$parent" "$foreign_parent"
            if [[ "$link_kind" == absolute ]]; then
                ln -s "$foreign_parent" "$parent_copy/runtime/$parent"
            else
                ln -s "../../${foreign_parent##*/}" "$parent_copy/runtime/$parent"
            fi
        fi
        bash "$parent_copy/scripts/resolve-pipeline.sh" \
            > "$TMP/parent-$parent-$link_kind.out" \
            2> "$TMP/parent-$parent-$link_kind.err"
        rc=$?
        if [[ "$rc" == 2 \
                && ! -s "$TMP/parent-$parent-$link_kind.out" ]] \
                && grep -Fq 'complete agy-worker skill bundle' \
                    "$TMP/parent-$parent-$link_kind.err" \
                && ! grep -Fq "$TMP" "$TMP/parent-$parent-$link_kind.err"; then
            ok "resolver rejects $link_kind $parent parent symlink"
        else
            bad "resolver rejects $link_kind $parent parent symlink"
        fi
    done
done

for specification in \
    'qa-gate.sh:executable' \
    'scripts/validate-envelope.py:executable' \
    'schemas/worker-result.schema.json:data' \
    'agents/repo-inventory.md:data' \
    'compat/agy-verified-version.txt:data'; do
    dependency="${specification%:*}"
    dependency_class="${specification##*:}"
    for wrong_type in directory symlink-directory symlink-foreign fifo wrong-mode; do
        label="${dependency//\//-}-$wrong_type"
        dependency_copy="$TMP/wrong-$label"
        cp -R "$ROOT/skills/agy-worker" "$dependency_copy"
        dependency_path="$dependency_copy/runtime/$dependency"
        rm -f "$dependency_path"
        case "$wrong_type" in
            directory) mkdir "$dependency_path" ;;
            symlink-directory) ln -s "$TMP" "$dependency_path" ;;
            symlink-foreign) ln -s /dev/null "$dependency_path" ;;
            fifo) mkfifo "$dependency_path" ;;
            wrong-mode)
                cp "$ROOT/skills/agy-worker/runtime/$dependency" "$dependency_path"
                if [[ "$dependency_class" == executable ]]; then
                    chmod -x "$dependency_path"
                else
                    chmod +x "$dependency_path"
                fi
                ;;
        esac
        PATH="$TMP/no-network-bin:$PATH" NETWORK_MARKER="$TMP/wrong-$label.network" \
            bash "$dependency_copy/scripts/resolve-pipeline.sh" \
            > "$TMP/wrong-$label.out" 2> "$TMP/wrong-$label.err"
        rc=$?
        if [[ "$rc" == 2 && ! -s "$TMP/wrong-$label.out" ]] \
                && grep -Fq 'complete agy-worker skill bundle' "$TMP/wrong-$label.err" \
                && [[ ! -e "$TMP/wrong-$label.network" ]] \
                && ! grep -Fq "$TMP" "$TMP/wrong-$label.err"; then
            ok "resolver rejects $wrong_type for $dependency_class $dependency"
        else
            bad "resolver rejects $wrong_type for $dependency_class $dependency"
        fi
    done
done

for helper_mode in malformed side-effect exit stale; do
    helper_copy="$TMP/helper-$helper_mode"
    cp -R "$ROOT/skills/agy-worker" "$helper_copy"
    helper="$helper_copy/runtime/scripts/runtime-bundle.sh"
    case "$helper_mode" in
        malformed) printf 'if then impossible\n' > "$helper" ;;
        side-effect) printf ': > %q\n' "$TMP/helper-side-effect.marker" > "$helper" ;;
        exit) printf 'exit 91\n' > "$helper" ;;
        stale) printf 'pipeline_runtime_complete() { return 1; }\n' > "$helper" ;;
    esac
    resolved="$(bash "$helper_copy/scripts/resolve-pipeline.sh" 2> "$TMP/helper-$helper_mode.err")"
    rc=$?
    if [[ "$rc" == 0 \
            && "$resolved" == "$(cd "$helper_copy/runtime" && pwd -P)" \
            && ! -s "$TMP/helper-$helper_mode.err" \
            && ! -e "$TMP/helper-side-effect.marker" ]]; then
        ok "resolver ignores $helper_mode candidate runtime helper"
    else
        bad "resolver ignores $helper_mode candidate runtime helper"
    fi
done

if grep -Fq 'run: ./tests/test-doctor.sh' "$ROOT/.github/workflows/test.yml" \
        && grep -Fq 'runs-on: macos-latest' "$ROOT/.github/workflows/test.yml"; then
    ok "macOS CI runs the dedicated offline doctor suite"
else
    bad "macOS CI runs the dedicated offline doctor suite"
fi

if [[ -x "$ROOT/proof-demo.sh" ]] \
        && [[ -x "$ROOT/tests/test-proof-demo.sh" ]] \
        && [[ -f "$ROOT/demo/fixtures/honest-envelope.json" ]] \
        && [[ ! -L "$ROOT/demo/fixtures/honest-envelope.json" ]] \
        && [[ -f "$ROOT/demo/fixtures/scope-mismatch-envelope.json" ]] \
        && [[ ! -L "$ROOT/demo/fixtures/scope-mismatch-envelope.json" ]] \
        && [[ ! -e "$ROOT/skills/agy-worker/runtime/proof-demo.sh" ]]; then
    ok "repository package includes the repo-only starter proof and canonical fixtures"
else
    bad "repository package includes the repo-only starter proof and canonical fixtures"
fi

if grep -Fq 'run: ./tests/test-proof-demo.sh' "$ROOT/.github/workflows/test.yml"; then
    ok "macOS CI runs the dedicated offline starter-proof suite"
else
    bad "macOS CI runs the dedicated offline starter-proof suite"
fi

if grep -Fq './proof-demo.sh' "$ROOT/README.md" \
        && grep -Fq 'starter proof' "$ROOT/README.md" \
        && grep -Fq 'proof-demo.sh' "$ROOT/docs/index.md" \
        && grep -Fq 'not human review' "$ROOT/docs/index.md"; then
    ok "public documentation links the bounded starter proof without claiming acceptance"
else
    bad "public documentation links the bounded starter proof without claiming acceptance"
fi

if grep -Fq 'gate="$script_dir/qa-gate.sh"' "$ROOT/proof-demo.sh" \
        && ! grep -Eq 'PROOF_GATE|--gate([=[:space:]]|$)' "$ROOT/proof-demo.sh" \
        && grep -Fq 'honest: gate-passed (exit 0)' "$ROOT/proof-demo.sh" \
        && grep -Fq 'mismatch: rejected (exit 10)' "$ROOT/proof-demo.sh" \
        && grep -Fq 'starter proof only; no candidate accepted because no human review occurred' \
            "$ROOT/proof-demo.sh"; then
    ok "starter proof fixes the maintained gate and its bounded success contract"
else
    bad "starter proof fixes the maintained gate and its bounded success contract"
fi

if cmp -s "$ROOT/compat/agy-verified-version.txt" \
        "$ROOT/skills/agy-worker/runtime/compat/agy-verified-version.txt" \
        && cmp -s "$ROOT/compat/agy-last-reviewed.txt" \
        "$ROOT/skills/agy-worker/runtime/compat/agy-last-reviewed.txt"; then
    ok "portable doctor metadata is byte-synchronized with canonical compatibility records"
else
    bad "portable doctor metadata is byte-synchronized with canonical compatibility records"
fi

resolved="$(bash "$ROOT/skills/agy-worker/scripts/resolve-pipeline.sh" 2>/dev/null)"
if [[ "$resolved" == "$(cd "$ROOT" && pwd -P)" ]]; then
    ok "Codex package resolver finds the adjacent canonical runtime"
else
    bad "Codex package resolver finds the adjacent canonical runtime"
fi

mkdir -p "$TMP/legacy-claude-only/.claude-plugin" \
    "$TMP/legacy-claude-only/skills"
cp "$ROOT/agy-worker.sh" "$ROOT/qa-gate.sh" \
    "$ROOT/model-recommendation.sh" "$TMP/legacy-claude-only/"
cp -R "$ROOT/skills/agy-worker" "$TMP/legacy-claude-only/skills/agy-worker"
printf '{}\n' > "$TMP/legacy-claude-only/.claude-plugin/plugin.json"
legacy_resolved="$(bash "$TMP/legacy-claude-only/skills/agy-worker/scripts/resolve-pipeline.sh" 2>/dev/null)"
if [[ "$legacy_resolved" == "$(cd "$TMP/legacy-claude-only/skills/agy-worker/runtime" && pwd -P)" ]]; then
    ok "resolver ignores a removed Claude-only package marker"
else
    bad "resolver ignores a removed Claude-only package marker"
fi

mkdir -p "$TMP/skill-folder-copy" "$TMP/no-network-bin"
cp -R "$ROOT/skills/agy-worker" "$TMP/skill-folder-copy/agy-worker"
for command_name in agy curl wget git npm npx; do
    printf '#!/usr/bin/env bash\n: > "$NETWORK_MARKER"\nexit 99\n' \
        > "$TMP/no-network-bin/$command_name"
    chmod +x "$TMP/no-network-bin/$command_name"
done
copied_pipeline="$(PATH="$TMP/no-network-bin:$PATH" \
    NETWORK_MARKER="$TMP/network-called" \
    bash "$TMP/skill-folder-copy/agy-worker/scripts/resolve-pipeline.sh" 2>/dev/null)"
PATH="$TMP/no-network-bin:$PATH" NETWORK_MARKER="$TMP/network-called" \
    "$copied_pipeline/model-recommendation.sh" --stage pre-dispatch \
    --selected-tier cheap --evidence bounded-routine \
    > "$TMP/copied-recommendation.json" 2> "$TMP/copied-recommendation.err"
rc=$?
if [[ "$rc" == "0" ]] \
        && [[ "$copied_pipeline" == "$(cd "$TMP/skill-folder-copy/agy-worker/runtime" && pwd -P)" ]] \
        && grep -Fq '"recommendation_only": true' "$TMP/copied-recommendation.json" \
        && grep -Fq '"applied": false' "$TMP/copied-recommendation.json" \
        && [[ ! -e "$TMP/network-called" ]]; then
    ok "skill-folder-only copy resolves and runs a bounded offline advisory"
else
    bad "skill-folder-only copy resolves and runs a bounded offline advisory"
fi

mkdir -p "$TMP/incomplete-skill/agy-worker/agents" \
    "$TMP/incomplete-skill/agy-worker/scripts"
cp "$ROOT/skills/agy-worker/SKILL.md" "$TMP/incomplete-skill/agy-worker/SKILL.md"
cp "$ROOT/skills/agy-worker/agents/openai.yaml" \
    "$TMP/incomplete-skill/agy-worker/agents/openai.yaml"
cp "$ROOT/skills/agy-worker/scripts/resolve-pipeline.sh" \
    "$TMP/incomplete-skill/agy-worker/scripts/resolve-pipeline.sh"
bash "$TMP/incomplete-skill/agy-worker/scripts/resolve-pipeline.sh" \
    > "$TMP/incomplete.out" 2> "$TMP/incomplete.err"
rc=$?
if [[ "$rc" == "2" && ! -s "$TMP/incomplete.out" ]] \
        && grep -Fq 'complete agy-worker skill bundle' "$TMP/incomplete.err"; then
    ok "skill-folder-only resolver rejects an incomplete runtime bundle"
else
    bad "skill-folder-only resolver rejects an incomplete runtime bundle"
fi

cp -R "$ROOT/skills/agy-worker" "$TMP/missing-doctor-skill"
rm -f "$TMP/missing-doctor-skill/runtime/doctor.sh"
PATH="$TMP/no-network-bin:$PATH" NETWORK_MARKER="$TMP/missing-doctor-network" \
    bash "$TMP/missing-doctor-skill/scripts/resolve-pipeline.sh" \
    > "$TMP/missing-doctor.out" 2> "$TMP/missing-doctor.err"
rc=$?
if [[ "$rc" == "2" && ! -s "$TMP/missing-doctor.out" ]] \
        && grep -Fq 'complete agy-worker skill bundle' "$TMP/missing-doctor.err" \
        && [[ ! -e "$TMP/missing-doctor-network" ]]; then
    ok "resolver rejects a doctor-less bundle without fallback or network"
else
    bad "resolver rejects a doctor-less bundle without fallback or network"
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
if [[ -x "$TMP/installed/agy-worker/runtime/doctor.sh" ]] \
        && [[ -x "$TMP/installed/agy-worker/runtime/scripts/doctor-metadata.py" ]] \
        && cmp -s "$ROOT/compat/agy-verified-version.txt" \
            "$TMP/installed/agy-worker/runtime/compat/agy-verified-version.txt" \
        && cmp -s "$ROOT/compat/agy-last-reviewed.txt" \
            "$TMP/installed/agy-worker/runtime/compat/agy-last-reviewed.txt"; then
    ok "standalone install preserves the doctor and its reviewed metadata bytes"
else
    bad "standalone install preserves the doctor and its reviewed metadata bytes"
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

required_suite_paths=(
    tests/test-qa-gate.sh
    tests/test-agy-worker.sh
    tests/test-update.sh
    tests/test-reporting.sh
    tests/test-packaging.sh
    tests/test-doctor.sh
    tests/test-proof-demo.sh
)
governance_lists_all_suites=1
for suite in "${required_suite_paths[@]}"; do
    if ! grep -Fq "./$suite" "$ROOT/CONTRIBUTING.md" \
            || ! grep -Fq "./$suite" "$ROOT/.github/pull_request_template.md"; then
        governance_lists_all_suites=0
    fi
done

if [[ "$governance_lists_all_suites" == "1" ]] \
        && grep -Fq 'Google/Gemini' "$ROOT/PRIVACY.md" \
        && grep -Fq 'logs/' "$ROOT/PRIVACY.md" \
        && grep -Fq 'GitHub Issues' "$ROOT/SUPPORT.md" \
        && grep -Fq 'not legal advice' "$ROOT/TERMS.md"; then
    ok "governance docs require all seven suites and disclose public policy boundaries"
else
    bad "governance docs require all seven suites and disclose public policy boundaries"
fi

python3 "$ROOT/scripts/validate-brand-assets.py" "$ROOT/docs/assets/brand" \
    > "$TMP/brand-valid.out" 2> "$TMP/brand-valid.err"
brand_valid_rc=$?
if [[ "$brand_valid_rc" == "0" ]] \
        && grep -Fq '4 SVG, 7 PNG' "$TMP/brand-valid.out" \
        && grep -Fq 'https://cagdasyurekli.github.io/codex-agy-worker/' "$ROOT/docs/_config.yml" \
        && grep -Fq 'https://cagdasyurekli.github.io/codex-agy-worker/assets/brand/social-preview-1280x640.png' "$ROOT/docs/_config.yml" \
        && grep -Fq 'canonical' "$ROOT/docs/_layouts/default.html" \
        && grep -Fq 'property="og:image"' "$ROOT/docs/_layouts/default.html" \
        && grep -Fq 'name="twitter:card" content="summary_large_image"' "$ROOT/docs/_layouts/default.html" \
        && grep -Fq 'sizes="16x16"' "$ROOT/docs/_layouts/default.html" \
        && grep -Fq 'sizes="32x32"' "$ROOT/docs/_layouts/default.html" \
        && grep -Fq '<picture aria-hidden="true">' "$ROOT/docs/_layouts/default.html" \
        && grep -Fq '<loc>https://cagdasyurekli.github.io/codex-agy-worker/</loc>' "$ROOT/docs/sitemap.xml" \
        && grep -Fq 'GitHub repository as the source of truth' "$ROOT/docs/index.md" \
        && grep -Fq '<picture>' "$ROOT/README.md" \
        && grep -Fq 'srcset="docs/assets/brand/logo-dark.svg"' "$ROOT/README.md" \
        && grep -Fq 'src="docs/assets/brand/logo-light.svg" alt=""' "$ROOT/README.md" \
        && [[ ! -e "$ROOT/docs/robots.txt" ]]; then
    ok "approved brand assets and GitHub Pages wiring pass the production contract"
else
    bad "approved brand assets and GitHub Pages wiring pass the production contract"
fi

cp -R "$ROOT/docs/assets/brand" "$TMP/reject-brand-image"
python3 - "$TMP/reject-brand-image/logo-light.svg" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
path.write_text(
    text.replace("</svg>", '<image href="https://invalid.example/logo.svg"/></svg>'),
    encoding="utf-8",
)
PY
python3 "$ROOT/scripts/validate-brand-assets.py" "$TMP/reject-brand-image" \
    > "$TMP/brand-image.out" 2> "$TMP/brand-image.err"
brand_image_rc=$?
if [[ "$brand_image_rc" == "1" ]] \
        && grep -Fq 'forbidden image element' "$TMP/brand-image.err"; then
    ok "brand validator rejects an external SVG image reference"
else
    bad "brand validator rejects an external SVG image reference"
fi

cp -R "$ROOT/docs/assets/brand" "$TMP/reject-brand-onload"
python3 - "$TMP/reject-brand-onload/logo-light.svg" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
path.write_text(text.replace("<svg ", '<svg onload="alert(1)" ', 1), encoding="utf-8")
PY
python3 "$ROOT/scripts/validate-brand-assets.py" "$TMP/reject-brand-onload" \
    > "$TMP/brand-onload.out" 2> "$TMP/brand-onload.err"
brand_onload_rc=$?
if [[ "$brand_onload_rc" == "1" ]] \
        && grep -Fq 'event attributes are forbidden' "$TMP/brand-onload.err"; then
    ok "brand validator rejects an SVG root event attribute"
else
    bad "brand validator rejects an SVG root event attribute"
fi

cp -R "$ROOT/docs/assets/brand" "$TMP/reject-brand-style"
python3 - "$TMP/reject-brand-style/logo-light.svg" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
path.write_text(
    text.replace(
        "</svg>",
        '<style>@import url("https://invalid.example/brand.css");</style></svg>',
    ),
    encoding="utf-8",
)
PY
python3 "$ROOT/scripts/validate-brand-assets.py" "$TMP/reject-brand-style" \
    > "$TMP/brand-style.out" 2> "$TMP/brand-style.err"
brand_style_rc=$?
if [[ "$brand_style_rc" == "1" ]] \
        && grep -Fq 'forbidden style element' "$TMP/brand-style.err"; then
    ok "brand validator rejects SVG style imports and external CSS"
else
    bad "brand validator rejects SVG style imports and external CSS"
fi

cp -R "$ROOT/docs/assets/brand" "$TMP/reject-brand-truncated"
python3 - "$TMP/reject-brand-truncated/social-preview-1280x640.png" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
data = path.read_bytes()
path.write_bytes(data[:-5])
PY
python3 "$ROOT/scripts/validate-brand-assets.py" "$TMP/reject-brand-truncated" \
    > "$TMP/brand-truncated.out" 2> "$TMP/brand-truncated.err"
brand_truncated_rc=$?
if [[ "$brand_truncated_rc" == "1" ]] \
        && grep -Fq 'truncated PNG chunk' "$TMP/brand-truncated.err"; then
    ok "brand validator rejects a truncated social-preview PNG"
else
    bad "brand validator rejects a truncated social-preview PNG"
fi

cp -R "$ROOT/docs/assets/brand" "$TMP/reject-brand-geometry"
python3 - "$TMP/reject-brand-geometry/logo-dark.svg" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
path.write_text(text.replace("M160 224", "M161 224", 1), encoding="utf-8")
PY
python3 "$ROOT/scripts/validate-brand-assets.py" "$TMP/reject-brand-geometry" \
    > "$TMP/brand-geometry.out" 2> "$TMP/brand-geometry.err"
brand_geometry_rc=$?
if [[ "$brand_geometry_rc" == "1" ]] \
        && grep -Fq 'ordered geometry diverged' "$TMP/brand-geometry.err"; then
    ok "brand validator rejects light and dark SVG geometry divergence"
else
    bad "brand validator rejects light and dark SVG geometry divergence"
fi

cp -R "$ROOT/docs/assets/brand" "$TMP/reject-brand-trns"
python3 - "$TMP/reject-brand-trns/social-preview-1280x640.png" <<'PY'
from pathlib import Path
import struct
import sys
import zlib

path = Path(sys.argv[1])
data = path.read_bytes()
output = bytearray(data[:8])
cursor = 8
inserted = False
while cursor < len(data):
    length = struct.unpack(">I", data[cursor : cursor + 4])[0]
    chunk_end = cursor + 12 + length
    chunk_type = data[cursor + 4 : cursor + 8]
    if chunk_type == b"IDAT" and not inserted:
        transparent_color = b"\x00\x00\x00\x00\x00\x00"
        trns_type = b"tRNS"
        output.extend(struct.pack(">I", len(transparent_color)))
        output.extend(trns_type)
        output.extend(transparent_color)
        output.extend(
            struct.pack(">I", zlib.crc32(trns_type + transparent_color) & 0xFFFFFFFF)
        )
        inserted = True
    output.extend(data[cursor:chunk_end])
    cursor = chunk_end
assert inserted
path.write_bytes(output)
PY
python3 "$ROOT/scripts/validate-brand-assets.py" "$TMP/reject-brand-trns" \
    > "$TMP/brand-trns.out" 2> "$TMP/brand-trns.err"
brand_trns_rc=$?
if [[ "$brand_trns_rc" == "1" ]] \
        && grep -Fq 'tRNS is forbidden' "$TMP/brand-trns.err"; then
    ok "brand validator rejects a valid-CRC tRNS transparency chunk"
else
    bad "brand validator rejects a valid-CRC tRNS transparency chunk"
fi

cp -R "$ROOT/docs/assets/brand" "$TMP/reject-brand-idat"
python3 - "$TMP/reject-brand-idat/social-preview-1280x640.png" <<'PY'
from pathlib import Path
import struct
import sys
import zlib

path = Path(sys.argv[1])
data = path.read_bytes()
output = bytearray(data[:8])
cursor = 8
replaced = False
while cursor < len(data):
    length = struct.unpack(">I", data[cursor : cursor + 4])[0]
    chunk_end = cursor + 12 + length
    chunk_type = data[cursor + 4 : cursor + 8]
    if chunk_type == b"IDAT" and not replaced:
        payload = b"\x00" * length
        output.extend(struct.pack(">I", length))
        output.extend(chunk_type)
        output.extend(payload)
        output.extend(struct.pack(">I", zlib.crc32(chunk_type + payload) & 0xFFFFFFFF))
        replaced = True
    else:
        output.extend(data[cursor:chunk_end])
    cursor = chunk_end
assert replaced
path.write_bytes(output)
PY
python3 "$ROOT/scripts/validate-brand-assets.py" "$TMP/reject-brand-idat" \
    > "$TMP/brand-idat.out" 2> "$TMP/brand-idat.err"
brand_idat_rc=$?
if [[ "$brand_idat_rc" == "1" ]] \
        && grep -Fq 'invalid IDAT zlib stream' "$TMP/brand-idat.err"; then
    ok "brand validator rejects a re-CRCed invalid IDAT zlib stream"
else
    bad "brand validator rejects a re-CRCed invalid IDAT zlib stream"
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
