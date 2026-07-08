#!/usr/bin/env bash
# One-time setup — runs on `claude --init` via the plugin's Setup hook (not every
# session). Installs cookiesync + the Clark stealth browser and points
# ~/.agent-browser/config.json at Clark, so agent-browser's local default is the
# stealth browser (no HeadlessChrome / navigator.webdriver tells).
# Idempotent; leaves an existing valid config untouched, and self-heals a stale
# Clark path after a Clark upgrade.
set -eo pipefail

command -v cookiesync    >/dev/null 2>&1 || brew install --cask yasyf/tap/cookiesync
command -v clark-browser >/dev/null 2>&1 || uv tool install clark-browser
clark-browser fetch >/dev/null 2>&1 || true

info="$(clark-browser info 2>/dev/null || true)"
bin="$(printf '%s' "$info" | python3 -c 'import sys,json;print(json.load(sys.stdin)["binary_path"])' 2>/dev/null || true)"
ver="$(printf '%s' "$info" | python3 -c 'import sys,json;print(json.load(sys.stdin)["chromium_version"].split(".")[0])' 2>/dev/null || true)"
if [ -z "$bin" ] || [ ! -x "$bin" ] || [ -z "$ver" ]; then
  echo "agent-browser-with-cookies: clark-browser not ready; skipping config" >&2
  exit 0
fi

# de-quarantine the downloaded .app so Gatekeeper doesn't block launch
xattr -dr com.apple.quarantine "${bin%/Contents/MacOS/*}" 2>/dev/null || true

cfg="$HOME/.agent-browser/config.json"
cur="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1])).get("executablePath",""))' "$cfg" 2>/dev/null || true)"
# Leave a config alone when its browser still exists (either the Clark config or a
# deliberate custom one). Only (re)write when absent or when the path is stale.
if [ -f "$cfg" ] && [ -n "$cur" ] && [ -x "$cur" ]; then
  exit 0
fi

mkdir -p "$HOME/.agent-browser"
ua="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/${ver}.0.0.0 Safari/537.36"
cat > "$cfg" <<JSON
{
  "\$schema": "https://agent-browser.dev/schema.json",
  "executablePath": "$bin",
  "userAgent": "$ua",
  "args": "--no-sandbox,--fingerprint=482913,--fingerprint-platform=mac,--fingerprint-brand=Chrome,--fingerprint-brand-version=${ver}.0.0.0,--disable-features=WebGPU,--disable-blink-features=AutomationControlled"
}
JSON
echo "agent-browser-with-cookies: pointed ~/.agent-browser/config.json at Clark ${ver}" >&2
