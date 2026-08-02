# agy compatibility sources

Review these sources when `update.sh check` reports version, date, or upstream drift:

- Official CLI documentation: https://antigravity.google/docs/cli-overview
- Official CLI usage documentation: https://antigravity.google/docs/cli-using
- Official source repository: https://github.com/google-antigravity/antigravity-cli
- Live installed interface: `./ground-truth.sh`

Update `agy-verified-version.txt`, `agy-upstream-head.txt`, and `last-reviewed.txt`
only after reconciling current official guidance with live CLI output and rerunning
the offline suites plus a bounded real-job check where behavior changed. The updater
uses these official sources and a fixed 30-day review interval; production callers
cannot replace them with environment variables, and a future review date is invalid.
