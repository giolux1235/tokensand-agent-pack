# Tokens& Agent Pack for Cursor

Keep working on the repository you already have. Tokens& helps your coding agent find tools and docs, check credit eligibility, and reopen a saved build plan.

## Install

Use [Connect your agent](https://tokensand.com/agents/install?mode=developer) for the Cursor install link. Reload Cursor, then ask:

> Use Tokens& get_account_mode. Keep my current stack and help with the next task in this repo.

Public lookup needs no Tokens& account. Private saved plans require a scoped `DAI_TOKEN`; browser sign-in does not authenticate MCP. [Setup and credentials](https://tokensand.com/auth.md).

This plugin uses the verified website package **0.1.27**. The npm registry remains at **0.1.12**; `@dev-adoption/cli@0.1.27` is not published. Existing installations need an update and restart.

[Manual setup and verification](plugins/tokensand-agent-pack/README.md) · [Live package manifest](https://tokensand.com/.well-known/tokensand-agent-pack.json)

## What it does

- Find a missing tool and its docs without requiring a new stack.
- Check offers and eligibility; lookup does not redeem credits.
- Read an exact saved Build Packet after sign-in. Public project proof is optional.
- Read authorized enterprise context separately; sample briefs do not establish live customer outcomes.

## Maintaining this package

The reviewed source is `plugins/tokensand-cursor-plugin` in the private application repository. Sync the MCP configuration and skill after verifying the live package manifest and an isolated public MCP replay. Preserve this repository's marketplace path and license. Update the plugin version so installed clients can detect the release. Source changes alone do not refresh an installed client.
