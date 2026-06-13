# Tokens& Agent Pack

Cursor plugin for Tokens& builder and enterprise operator workflows.

## What It Adds

- Tokens& MCP server configured through the published npm package `@dev-adoption/cli@0.1.8`
- Public builder tools for stack search, public perks, AgentRank/adoption evidence, and Build Briefs with best APIs, cost drivers, free credits/perks, and dashboard sync status
- Enterprise session briefs for supplied or sample session metrics, budget/cost-per-proof math, ICP segment ranking, event/session strategy, dashboard handoff, activation bottlenecks, offer strategy, retention, and proof loops
- Approval-only enterprise motion drafts for tracked sessions, builder invites, partner onboarding, and adoption proof
- Private builder context through `DAI_TOKEN` for saved stacks, Build Packets, project drafts, and proof publishing
- Explicit account-mode guidance so Cursor users know browser login is not automatically shared with MCP
- Marketplace metadata for Cursor plugin review

## Install

Use the Tokens& install page:

```text
https://tokensand.com/agents/install
```

Or install the MCP server directly from Cursor with:

```text
https://tokensand.com/.well-known/tokensand-agent-pack.json
```

Enterprise mode can be configured with:

```text
DAI_MODE=enterprise
DAI_ENTERPRISE_PROFILE=redis
```

Private live enterprise tenant metrics require an enterprise-scoped token when Tokens& enables that issuer. Do not paste browser cookies into MCP config.

## Structure

```text
.cursor-plugin/marketplace.json
plugins/tokensand-agent-pack/.cursor-plugin/plugin.json
plugins/tokensand-agent-pack/mcp.json
plugins/tokensand-agent-pack/skills/tokensand-agent-pack/SKILL.md
```
