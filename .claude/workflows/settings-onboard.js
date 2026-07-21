export const meta = {
  name: 'settings-onboard',
  description: 'Onboard one repo to cc-guides fragment-rendered .claude/settings.json',
  phases: [{ title: 'Onboard', detail: 'render settings.json from cc-guides fragments, one CI-gated commit' }],
}

// args may arrive as a JS value or a JSON string (Workflow stringifies objects).
// Accept either a bare repo name ("yclaw") or {repo, custom, note}.
let input = args
if (typeof input === 'string') {
  try { input = JSON.parse(input) } catch { /* bare, non-JSON repo name */ }
}
const r = typeof input === 'string' ? { repo: input } : input
if (!r || !r.repo) {
  throw new Error('settings-onboard needs a repo name — pass a bare string or {repo, custom, note}')
}

// custom overlay may pin the dev repo's own plugin off — step 7 then expects false
const pinsPluginOff = !!(r.custom && String(r.custom).includes('captain-hook@captain-hook') && String(r.custom).includes('false'))

const mkPrompt = (r) => `Migrate the repo yasyf/${r.repo} to cc-guides fragment-rendered .claude/settings.json. You work autonomously through transient friction, but if the task's SHAPE surprises you (residue keys not covered below, a semantic conflict with the base fragment, the repo not actually consuming cc-guides, CI red for reasons unrelated to your change), STOP and return findings plus 2-4 concrete options instead of improvising.

WORKING COPY: if /Users/yasyf/Code/${r.repo} exists locally it may be shared with concurrent sessions — run jj st (or git status) first, commit ONLY your files (fileset-scoped jj commit or git add of exact paths), never git stash; on push reject, fetch + rebase onto main@origin. If not cloned locally, git clone https://github.com/yasyf/${r.repo} into your scratchpad and work there with plain git.

CANONICAL SHAPE (mirror exactly): cc-skills commit 3a4d7b8b did this migration — fetch it as reference: gh api repos/yasyf/cc-skills/contents/.claude/fragments/.claude/settings.json/layout.toml --jq .content | base64 -d. The layout lives at .claude/fragments/.claude/settings.json/layout.toml; overlay file settings-overrides.fragment.json sits next to it; lock is .claude/fragments/cc-guides.lock; fragments + rendered settings.json + lock land in ONE commit.

STEPS:
1. Save the current settings for diffing: git show HEAD:.claude/settings.json > /tmp/old-settings-${r.repo}.json 2>/dev/null || echo '{}' > /tmp/old-settings-${r.repo}.json. ${r.note && r.note.startsWith('NO existing') ? '(This repo has no settings.json — expect the empty-object fallback.)' : ''}
2. Detect language: go.mod → variant "cc-skills:settings-go"; pyproject.toml → "cc-skills:settings-python"; Package.swift or *.xcodeproj → "cc-skills:settings-swift"; none → no variant. ${r.note ? 'Note: ' + r.note : ''}
3. Create .claude/fragments/.claude/settings.json/layout.toml: fragments = ["cc-skills:settings-base"<, variant if any>, "settings-overrides"], then [sources.cc-skills] source = "github:yasyf/cc-skills@main" AFTER the fragments array (match the cc-skills reference exactly).
4. Overlay settings-overrides.fragment.json: ${r.custom ? r.custom + '. Express these as a JSON object matching settings.json structure.' : 'start with {} — the residue diff in step 7 decides if anything must move in.'}
5. Ensure the .claude/capt-hook.toml layout exists — capt-hook 10.x loads ZERO packs without a rendered .claude/capt-hook.toml (the legacy .claude/hooks/packs.toml is dead). If missing, create .claude/fragments/.claude/capt-hook.toml/layout.toml with fragments = ["cc-skills:capt-hook-base"<, "cc-skills:capt-hook-python" | "cc-skills:capt-hook-go" per language>, "cc-skills:capt-hook-ccx", "cc-skills:capt-hook-cc-present"] then [sources.cc-skills] source = "github:yasyf/cc-skills@main" — mirror plugins/repo-bootstrap/skills/repo-bootstrap/templates/{python|go|base}/claude/fragments/capt-hook.toml/layout.toml (base flavor for swift/other; the cc-guides render in step 6 composes the rendered .claude/capt-hook.toml). Also ensure .claude/jj-config.toml exists (base template, author from git log -1 --format='%an|%ae' of THIS repo); for python repos also .claude/ty-quiet.toml (python template).
6. Install cc-guides if needed (brew list cc-guides || brew install yasyf/tap/cc-guides) and run cc-guides render at repo root. This onboarding render is the ONE sanctioned local render — steady-state fragment edits never render locally; CI's Guides workflow renders on push. First render over a pre-existing hand-written settings.json will refuse with "refusing to overwrite a handwritten file" — that is the expected one-time onboarding guard; re-run with --force ONLY after step 1 saved the old file.
7. RESIDUE GATE: diff <(jq -S . /tmp/old-settings-${r.repo}.json) <(jq -S . .claude/settings.json). Every only-in-old line must be either (a) explicitly covered by the overlay instruction above, (b) a stale "hooks" block or a stray cc-context@skills entry (deliberately dropped), or (c) a value identical to what base provides. ANYTHING ELSE → move it into the overlay and re-render, or if it semantically conflicts with base, STOP and check back. Rendered file must have enabledPlugins["captain-hook@captain-hook"] == ${pinsPluginOff ? 'false — this overlay deliberately pins the dev repo\'s own plugin off; verify the pin survived the render' : 'true'} and extraKnownMarketplaces.captain-hook.
8. Verify cc-guides check --diff green. ONE commit that includes EVERY artifact this render refreshed — layout.toml + overlay + rendered .claude/settings.json + lock + any re-rendered AGENTS.md/CLAUDE.md (render is unscoped and advances the shared cc-guides pin, so those artifacts move even when settings.json is the target) + any added .claude/capt-hook.toml (with its layout dir)/jj-config.toml/ty-quiet.toml — message "chore: render .claude/settings.json from cc-guides fragments". Committing selectively (settings.json + lock but not the re-rendered AGENTS.md/CLAUDE.md) is self-healing (the next push's render commit reconciles it) but pointlessly noisy — land the render whole. HOW you commit depends on the clone: for a LOCAL shared clone at /Users/yasyf/Code/${r.repo}, commit exactly those named files fileset-scoped (never \`ccx vcs ship\` — its unscoped commit would sweep a concurrent session's files), push, then watch guides.yml; on push reject fetch + rebase onto main@origin and re-push. Only in a fresh scratchpad clone (nothing else touching the tree) is \`ccx vcs ship -m "<msg>"\` fine — it commits + pushes + watches in one. Watch: gh run list -R yasyf/${r.repo} --workflow guides.yml -L 3 then gh run watch <id> --exit-status.
9. Return compact JSON-ish report: repo, language/variant, overlay contents, residue-diff summary (what was dropped/preserved), captain-hook enabled (bool), CI conclusion, commit sha, check-back items (empty if none).`

phase('Onboard')
const res = await agent(mkPrompt(r), { label: `onboard:${r.repo}`, phase: 'Onboard', model: 'opus', effort: 'xhigh' })
return { repo: r.repo, res }
