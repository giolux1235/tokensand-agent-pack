# Tokens& Agent Pack

Cursor plugin for Tokens& builder workflows.

## What It Adds

- Tokens& MCP server configured through the production package at `https://tokensand.com/packages/dev-adoption-cli-0.1.4.tgz`
- Agent skill guidance for Build Packets, perks, stack suggestions, and proof workflows
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

## Structure

```text
.cursor-plugin/marketplace.json
plugins/tokensand-agent-pack/.cursor-plugin/plugin.json
plugins/tokensand-agent-pack/mcp.json
plugins/tokensand-agent-pack/skills/tokensand-agent-pack/SKILL.md
```

