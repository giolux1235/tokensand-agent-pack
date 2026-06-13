---
name: tokensand-agent-pack
description: Use when a builder or enterprise operator wants Tokens& stack, perk, Build Packet, project-proof, or session-adoption context inside Cursor.
---

# Tokens& Agent Pack

Use this skill when the user asks for AI/devtool stack choices, builder perks, Tokens& Build Packets, project proof, enterprise session insight, or adoption context.

## Public Discovery

- Agent pack: `https://tokensand.com/.well-known/tokensand-agent-pack.json`
- OpenAPI: `https://tokensand.com/openapi.json`
- Public perks: `https://tokensand.com/perks?public=1`
- Builder workbench: `https://tokensand.com/tools?mode=build`
- Enterprise dashboard: `https://tokensand.com/dashboard?mode=enterprise`

## Account Modes

Always call `get_account_mode` when the user asks whether the plugin is connected, authenticated, or using developer vs enterprise context.

Cursor does not inherit the user's Tokens& browser login. The MCP server is based on its env/config:

- Public mode: no token; can search stacks, perks, adoption rank, builder briefs, and enterprise sample/user-supplied session briefs.
- Developer mode: `DAI_TOKEN` unlocks saved stack, Build Packet, and project-proof workflows.
- Enterprise mode: `DAI_MODE=enterprise` plus `DAI_ORGANIZATION_ID` or `DAI_ENTERPRISE_PROFILE` makes enterprise context explicit. Live tenant metrics require an enterprise-scoped credential; do not ask users to paste browser cookies.

## MCP Tools

Public MCP tools work without a scoped token:

- `get_account_mode` for persona/auth/context boundary.
- `search_tools` for stack/product suggestions by build intent.
- `find_perks` for public builder credits, events, startup programs, and Tokens& starter-kit offers.
- `get_adoption_rank` for public AgentRank/adoption evidence before choosing a stack.
- `build_brief` for a combined best-API plan, cost driver summary, free credit/perk plan, adoption evidence, dashboard sync boundary, risk, and proof handoff.
- `enterprise_session_brief` for enterprise session funnel analysis, cost-per-activation/proof math, ICP segment ranking, event/session strategy, dashboard handoff links, offer strategy, activation, retention, and proof-loop next actions.
- `draft_enterprise_session_motion`, `draft_builder_invite_motion`, `draft_partner_onboarding_motion`, and `draft_adoption_proof_motion` for approval-only enterprise motion drafts.

Private saved stacks, project context, and write-like actions require a scoped workflow token generated from Tokens& after sign-in.

Use MCP only when the user has configured:

```json
{
  "mcpServers": {
    "tokensand": {
      "command": "npx",
      "args": ["-y", "@dev-adoption/cli@0.1.8", "mcp", "serve", "--api", "https://tokensand.com"],
      "env": {
        "DAI_TOKEN": "<scoped-token>"
      }
    }
  }
}
```

Enterprise context example:

```json
{
  "mcpServers": {
    "tokensand": {
      "command": "npx",
      "args": ["-y", "@dev-adoption/cli@0.1.8", "mcp", "serve", "--api", "https://tokensand.com", "--mode", "enterprise"],
      "env": {
        "DAI_MODE": "enterprise",
        "DAI_ENTERPRISE_PROFILE": "redis"
      }
    }
  }
}
```

## Operating Rules

1. Ask for `build_brief` before implementation when the user is choosing stack, backend, auth, data, deployment, cost, credits, APIs, evals, or proof path. For customer-agent builds, require best APIs, available free credits/perks, cost drivers, and dashboard sync status.
2. For enterprise operators, collapse the recommendation to one promise: create a tracked developer session, invite the right builders, onboard partner companies, and prove adoption.
3. Ask for session metrics, budget/spend if available, and any known ICP segment split; then call `enterprise_session_brief` with a profile before recommending offers, session format, ICP, or follow-up.
4. When the enterprise operator asks what to do next, call the approval-only motion draft tools. Drafts are useful in Cursor but do not create sessions, send invites, onboard partners, export accounts, change offers, or publish proof.
5. Treat perks as opportunities, not guarantees, until the user claims or verifies the offer.
6. Keep repo writes, public proof publishing, outbound customer/account actions, account exports, and offer changes approval-gated.
7. Source-label adoption claims. Do not convert self-reported proof into verified adoption without first-party, public-source, consented, or aggregate-safe evidence.
8. Prefer the user's current repo conventions over generic stack advice.
9. If no private token is configured, call `build_brief` for builders or `enterprise_session_brief` for enterprise operators so the user still receives useful public/sample guidance.

## Expected Output

For builders, return a short build decision with:

- recommended stack and alternatives
- best APIs by role, especially support-system, handoff, runtime, and memory/retrieval APIs for customer agents
- matched perks or missing supply
- free credits/perks available and claim caveats
- required env vars and docs
- cost/safety/eval risks
- whether the Codex/Cursor call persisted anything to Tokens& and what DAI_TOKEN + draft/publish path is required for the build to appear in the dashboard
- next action inside the current coding agent
- proof step that remains human-approved

For enterprise operators, return a short session/adoption decision with:

- data source: supplied metrics, public/sample model, or private workspace if explicitly credentialed
- funnel bottleneck and numeric rates
- cost source and cost per activation, retained builder, and proof project when budget is supplied or modelled
- best ICP segment and why it should receive the next cohort
- recommended event/session format and cadence
- offer/perk to seed next
- event/proof definition
- approval-only drafts for session, invite, partner onboarding, or proof motions when requested
- whether the Codex/Cursor call persisted anything to Tokens& and what token/config path is required for a build to appear in the dashboard
- activation and retention next actions
- kill condition for the next 100 builders
