# codex

**Second opinions and parallel sweeps from OpenAI's Codex CLI.** Route hard debugging, adversarial diff reviews, security audits, N-unit migrations, and `$imagegen` asset generation to `gpt-6.1-sol` at `xhigh` reasoning while keeping individual bounded changes and large net-new code on Claude.

## Install

```text
/plugin marketplace add yasyf/cc-skills
/plugin install codex@skills
```

- The `codex` CLI on `PATH`.
- Depends on the `captain-hook` plugin at `>=12.71.1` from the `captain-hook` marketplace.
- Image generation requires `codex` signed in with a ChatGPT plan, which you can verify with `codex login status`; with API-key auth the hosted image tool never mounts.
- On Linux amd64, async owner wakes need `codex-ask supervise` running in the foreground; any process of the same user can reach its daemon, so run it only on a single-user machine.

## Use it

### Investigate a production spike across telemetry and chat

Tying an alert across logs, error trackers, and chat takes too many manual hops during an outage. With `codex` installed, ask:

```text
Ask codex why checkout-service's error rate spiked at 14:32 UTC: check Datadog, Sentry, and #eng-checkout in Slack
```

`codex:codex` runs `codex-ask` with the `recon` lane and mounts the `slack` server over MCP, the Model Context Protocol, while Datadog and Sentry mount automatically when `~/.codex/config.toml` enables them. The call prints `REPLY_FILE:`, `LOG_FILE:`, and `AWAIT:` lines first, and blocks until the reply completes. If the Bash call is killed or times out, the run survives on disk under `${XDG_CACHE_HOME:-~/.cache}/codex-ask/runs/` and recovers with `codex-ask --await <run-dir>`. Add `--incident` for the fast service tier during active production alerts, or `-m astra` to select `gpt-6-astra` for exceptional cases.

### Audit a diff with structured findings and adversarial refutation

Self-reviewing your own changes misses subtle auth, input validation, crypto, and secrets bugs. With `codex` installed, ask:

```text
Run a security and adversarial review on the current working tree diff with codex.
```

`codex:codex` runs inline from the main conversation, subagents, or workflows without a context fork. The review runs through `codex-ask` with the `review`, `refute`, or `security` lane contracts, and `--schema findings`, `--schema verdict`, or `--schema refutations` returns structured JSON. Parallel sweeps fan out across `codex:codex-wrapper-async` owner agents and `codex-ask --collect` grades each lane from disk.

### Generate a project mascot or banner

With `codex` installed and signed in with a ChatGPT plan, ask:

```text
Use codex to generate a mascot logo for this repo.
```

`codex:codex` invokes `codex-ask --image`, routing the prompt to Codex's `$imagegen` skill while preventing Codex from drawing the image itself with local tools. Generated images stay in `$CODEX_HOME/generated_images/`.

## What's inside

| Kind | Name | What it does |
| --- | --- | --- |
| Skill | `codex:codex` | Routes debugging, code/diff review, security audits, parallel bounded sweeps, rote throwaway work, and `$imagegen` image generation to OpenAI's Codex CLI running `gpt-6.1-sol` at `xhigh` reasoning through `codex-ask`. |
| Agent | `codex:codex-wrapper` | Relay lane on `sonnet` at low effort for workflow stages and subagents that only accept Claude models; runs one foreground `codex-ask` call and returns Codex's answer verbatim while keeping big context gathers out of the caller's window. |
| Agent | `codex:codex-wrapper-async` | Async owner lane that dispatches with `codex-ask --dispatch --owner`, parks on the `await` tool, and returns the on-disk reply on wake. |
| MCP server | `codex-ask-channel` | Steering channel on `codex-ask` that provides the `await` tool and delivers completion wakes to owner subagents. |
| Hook | `hooks/hooks.json` | `SessionStart` hook that runs `codex-ask --version` to pre-warm and `codex-ask --sweep` asynchronously. |
| Hook | `captain-hook` guard pack | Warns once when `codex-ask` is backgrounded or detached with `run_in_background`, trailing `&`, `nohup`, `setsid`, or `disown`, or when a hand-rolled `codex exec` runs, steering back to foreground `codex-ask` or `--dispatch`. |
| CLI | `bin/codex-ask` | Bundled executable on the Bash tool's `PATH` that resolves a version-pinned release from `bin/codex-ask.binrun`, pins Codex CLI flags, and manages `--lane`, `--schema`, `--mcp`, `--image`, `--incident`, `--dispatch`, `--await`, `--watch`, `--mint-run`, `--collect`, `--ps`, `--cancel`, and `--reap`. |

Part of [cc-skills](../../README.md).
