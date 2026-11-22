# Tokens& Agent Pack

Check an AI workflow change in your repository and see which cases failed.

## Check a change with Codex or Claude Code

From your project directory, install the skill for the agent you use.

```sh
npx --yes skills@1.9.0 add giolux1235/tokensand-agent-pack --skill tokensand-agent-pack --agent codex -y
```

For Claude Code, replace `--agent codex` with `--agent claude-code`. Open a new agent session in the repository and ask:

> Use Tokens& to check this change and show me what failed.

Local checks run without an account, provider key or MCP connection. If you have no workflow tests, try the challenge below or run the [first local check](https://tokensand.com/agents/install#first-local-check).

### Try a partner challenge in your repository

Run this in your checkout. For Claude Code, replace `.agents/` with `.claude/`. It downloads the synthetic cases for the Tinybird practice challenge the first time. The cases cover late events, duplicate deliveries, and latency reporting.

```sh
(test -e release-telemetry-v1.mjs || curl -fsSL --remove-on-error -o release-telemetry-v1.mjs https://tokensand.com/agent-pack/release-telemetry-v1.mjs) &&
node .agents/skills/tokensand-agent-pack/scripts/check_workflow.mjs --offline-workflow ./release-telemetry-v1.mjs
```

The example has three failing cases. Connect the candidate to your app’s reporting function, then rerun. The check runs locally without a Tinybird account. [Challenge and partner docs](https://tokensand.com/challenges#idea-partner-tinybird-agent-telemetry).

## Connect Cursor or access saved work

Install the Cursor plugin, reload Cursor and ask Tokens& to check your change. The plugin includes the workflow skill and MCP. [Connect your agent](https://tokensand.com/agents/install?mode=developer)

Public tool lookup works without signing in. Private saved projects require a scoped `DAI_TOKEN`. Use the published archive for the MCP server version listed in the live manifest. [Manual Cursor setup](plugins/tokensand-agent-pack/README.md) · [Live manifest](https://tokensand.com/.well-known/tokensand-agent-pack.json) · [Credentials](https://tokensand.com/auth.md)

## What it does

- Find a missing tool and its docs without requiring a new stack.
- Check offers and eligibility; lookup does not redeem credits.
- Read an exact saved Build Packet after sign-in. Public project proof is optional.
- Read authorized enterprise context separately; sample briefs do not establish live customer outcomes.

## For DevRel teams

Tokens& combines a [developer GTM platform](https://tokensand.com/platform) with managed workshops, hackathons and builder programmes. Use a concrete product task as the starting point, then measure first successful use and repeat use separately from registrations or submissions.

The [Loop Engineering Hackathon gallery](https://loop-engineering-hackathon.devpost.com/project-gallery) lists **65 project submissions** (checked October 7, 2026). Explore the [Tokens&-hosted event](https://luma.com/loophack) and an [inspectable project submission](https://devpost.com/software/vigil-r1bpqc). Project descriptions are participant reports; these artifacts do not establish independently verified product usage, retention or sponsor ROI.

## Maintaining this package

The reviewed source is `plugins/tokensand-cursor-plugin` in the private application repository. Sync the MCP configuration and skill after verifying the live package manifest and an isolated public MCP replay. Preserve this repository's marketplace path and license. Update the plugin version so installed clients can detect the release. Source changes alone do not refresh an installed client.
