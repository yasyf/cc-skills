#!/bin/sh
set -eu
out=$(cd "$(dirname "$0")/../lib/drive/site" && pwd)/vendor
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
cd "$work"

npm pack --silent @highlightjs/cdn-assets@11.12.0 mermaid@12.3.0 @viz-js/viz@3.31.0 >/dev/null
for pack in *.tgz; do mkdir "${pack%.tgz}" && tar xzf "$pack" -C "${pack%.tgz}"; done
npm install --silent --no-save --no-package-lock vega@6.4.0 vega-lite@6.4.3 vega-embed@7.3.0 esbuild@0.28.2 >/dev/null

rm -rf "$out"
hljs="$out/highlight.js@11.12.0" mermaid="$out/mermaid@12.3.0" viz="$out/viz@3.31.0" vega="$out/vega-embed@7.3.0" arch="$out/cc-present@v0.60.2"
mkdir -p "$hljs/languages" "$mermaid/chunks/mermaid.esm.min" "$viz" "$vega" "$arch"

from=highlightjs-cdn-assets-11.12.0/package
cp "$from/es/highlight.min.js" "$from/styles/github.min.css" "$from/LICENSE" "$hljs/"
cp "$from"/es/languages/*.min.js "$hljs/languages/"

from=mermaid-12.3.0/package
cp "$from/dist/mermaid.esm.min.mjs" "$from/LICENSE" "$mermaid/"
cp "$from"/dist/chunks/mermaid.esm.min/*.mjs "$mermaid/chunks/mermaid.esm.min/"

cp viz-js-viz-3.31.0/package/dist/viz.js "$viz/"

echo 'export { default } from "vega-embed";' >embed.mjs
npx --no-install esbuild embed.mjs --bundle --format=esm --minify --legal-comments=none --outfile="$vega/vega-embed.min.mjs" --log-level=warning
cat node_modules/vega/LICENSE node_modules/vega-lite/LICENSE node_modules/vega-embed/LICENSE >"$vega/LICENSE"

for file in arch-map.js system-map.js; do
  curl -fsSL "https://raw.githubusercontent.com/yasyf/cc-present/v0.60.2/plugin/.claude/components/display/src/$file" -o "$arch/$file"
done
