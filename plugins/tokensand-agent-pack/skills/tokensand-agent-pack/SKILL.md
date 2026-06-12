---
name: tokensand-agent-pack
description: Use when a builder wants Tokens& stack, perk, build-packet, or project-proof context inside Cursor.
---

# Tokens& Agent Pack

Use this skill when the user asks for AI/devtool stack choices, builder perks, Tokens& Build Packets, project proof, or developer-adoption context.

## Public Discovery

- Agent pack: `https://tokensand.com/.well-known/tokensand-agent-pack.json`
- OpenAPI: `https://tokensand.com/openapi.json`
- Public perks: `https://tokensand.com/perks?public=1`
- Builder workbench: `https://tokensand.com/tools?mode=build`

## Private Workflow Context

Public MCP tools work without a scoped token:

- `search_tools` for stack/product suggestions by build intent.
- `find_perks` for public builder credits, events, startup programs, and Tokens& starter-kit offers.
- `get_adoption_rank` for public AgentRank/adoption evidence before choosing a stack.
- `build_brief` for a combined stack, perk, adoption, risk, and proof handoff.

Private saved stacks, project context, and write-like actions require a scoped workflow token generated from Tokens& after sign-in.

Use MCP only when the user has configured:

```json
{
  "mcpServers": {
    "tokensand": {
      "command": "npx",
      "args": ["-y", "https://tokensand.com/packages/dev-adoption-cli-0.1.4.tgz", "mcp", "serve", "--api", "https://tokensand.com"],
      "env": {
        "DAI_TOKEN": "<scoped-token>"
      }
    }
  }
}
```

## Operating Rules

1. Ask for a Build Packet before implementation when the user is choosing stack, backend, auth, data, deployment, cost, evals, or proof path.
2. Treat perks as opportunities, not guarantees, until the user claims or verifies the offer.
3. Keep repo writes, public proof publishing, outbound customer/account actions, and offer changes approval-gated.
4. Source-label adoption claims. Do not convert self-reported proof into verified adoption without first-party, public-source, consented, or aggregate-safe evidence.
5. Prefer the user's current repo conventions over generic stack advice.
6. If no private token is configured, call `build_brief` first so the builder still receives stack options, perks, adoption evidence, and next steps.

## Expected Output

Return a short build decision with:

- recommended stack and alternatives
- matched perks or missing supply
- required env vars and docs
- cost/safety/eval risks
- next action inside the current coding agent
- proof step that remains human-approved
