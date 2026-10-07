# Tokens& Agent Pack for Cursor

Keep working on the repository you already have. Tokens& helps your coding agent find tools and docs, check credit eligibility, and reopen a saved build plan.

## Install

Use [Connect your agent](https://tokensand.com/agents/install?mode=developer) for the Cursor install link. Reload Cursor, then ask:

> Use Tokens& get_account_mode. Keep my current stack and help with the next task in this repo.

Public lookup needs no Tokens& account. Private saved plans require a scoped `DAI_TOKEN`; browser sign-in does not authenticate MCP. [Setup and credentials](https://tokensand.com/auth.md).

This plugin is prepared for website package **0.1.29**; production verification is pending. The npm registry remains at **0.1.12**; `@dev-adoption/cli@0.1.29` is not published. Existing installations need an update and restart.

[Manual setup and verification](plugins/tokensand-agent-pack/README.md) · [Live package manifest](https://tokensand.com/.well-known/tokensand-agent-pack.json)

Try the [public first-use example](examples/existing-supabase-tracker.md): keep an existing Supabase stack and get a sourced brief for a private project tracker, with the observed result and remaining implementation checks.

## Install the skill in another coding agent

The workflow instructions are also discoverable with the open [skills CLI](https://skills.sh/docs):

```bash
npx skills add giolux1235/tokensand-agent-pack --skill tokensand-agent-pack
```

Choose your supported agent when prompted. This installs the **skill instructions**, not the MCP server configuration. Connect Tokens& separately using your client's instructions on [Connect your agent](https://tokensand.com/agents/install), then verify `get_account_mode` before trying a task. The Cursor plugin bundles both components.

The skills.sh leaderboard reflects real installs; a discoverable repository is not marketplace approval or evidence of product use.

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
