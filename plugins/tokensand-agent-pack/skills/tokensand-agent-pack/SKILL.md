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
- `build_brief` for a combined stack, perk, adoption, risk, and proof handoff.
- `enterprise_session_brief` for enterprise session funnel analysis, offer strategy, activation, retention, and proof-loop next actions.

Private saved stacks, project context, and write-like actions require a scoped workflow token generated from Tokens& after sign-in.

Use MCP only when the user has configured:

```json
{
  "mcpServers": {
    "tokensand": {
      "command": "npx",
      "args": ["-y", "https://tokensand.com/packages/dev-adoption-cli-0.1.5.tgz", "mcp", "serve", "--api", "https://tokensand.com"],
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
      "args": ["-y", "https://tokensand.com/packages/dev-adoption-cli-0.1.5.tgz", "mcp", "serve", "--api", "https://tokensand.com", "--mode", "enterprise"],
      "env": {
        "DAI_MODE": "enterprise",
        "DAI_ENTERPRISE_PROFILE": "redis"
      }
    }
  }
}
```

## Operating Rules

1. Ask for a Build Packet before implementation when the user is choosing stack, backend, auth, data, deployment, cost, evals, or proof path.
2. For enterprise operators, ask for session metrics or call `enterprise_session_brief` with a profile before recommending offers or follow-up.
3. Treat perks as opportunities, not guarantees, until the user claims or verifies the offer.
4. Keep repo writes, public proof publishing, outbound customer/account actions, account exports, and offer changes approval-gated.
5. Source-label adoption claims. Do not convert self-reported proof into verified adoption without first-party, public-source, consented, or aggregate-safe evidence.
6. Prefer the user's current repo conventions over generic stack advice.
7. If no private token is configured, call `build_brief` for builders or `enterprise_session_brief` for enterprise operators so the user still receives useful public/sample guidance.

## Expected Output

For builders, return a short build decision with:

- recommended stack and alternatives
- matched perks or missing supply
- required env vars and docs
- cost/safety/eval risks
- next action inside the current coding agent
- proof step that remains human-approved

For enterprise operators, return a short session/adoption decision with:

- data source: supplied metrics, public/sample model, or private workspace if explicitly credentialed
- funnel bottleneck and numeric rates
- offer/perk to seed next
- event/proof definition
- activation and retention next actions
- kill condition for the next 100 builders
