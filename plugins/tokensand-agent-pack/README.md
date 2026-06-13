# Tokens& Agent Pack for Cursor

This Cursor plugin packages Tokens& Build Packet guidance, builder perks, enterprise session insight, stack context, and proof workflow rules for coding-agent workflows.

## Local Test

```bash
ln -s /absolute/path/to/directory_ai_tools/plugins/tokensand-cursor-plugin ~/.cursor/plugins/local/tokensand-agent-pack
```

Restart Cursor or run `Developer: Reload Window`, then verify the skill and MCP server appear in Cursor.

## Marketplace Readiness

The repository-level Cursor marketplace manifest lives at `.cursor-plugin/marketplace.json`. Cursor's official marketplace review flow accepts Git repositories; submit from `https://cursor.com/marketplace/publish` when the public repo and package assets are ready.

## User Install

One-click install is generated at `https://tokensand.com/.well-known/tokensand-agent-pack.json` as a Cursor deeplink.

Manual MCP config:

```json
{
  "mcpServers": {
    "tokensand": {
      "command": "npx",
      "args": [
        "-y",
        "https://tokensand.com/packages/dev-adoption-cli-0.1.8.tgz",
        "mcp",
        "serve",
        "--api",
        "https://tokensand.com"
      ]
    }
  }
}
```

Enterprise MCP config:

```json
{
  "mcpServers": {
    "tokensand": {
      "command": "npx",
      "args": [
        "-y",
        "https://tokensand.com/packages/dev-adoption-cli-0.1.8.tgz",
        "mcp",
        "serve",
        "--api",
        "https://tokensand.com",
        "--mode",
        "enterprise"
      ],
      "env": {
        "DAI_MODE": "enterprise",
        "DAI_ENTERPRISE_PROFILE": "redis"
      }
    }
  }
}
```

After npm publish, use:

```bash
npx -y @dev-adoption/cli@latest mcp serve --api https://tokensand.com
```

Claude Code uses the same MCP server:

```bash
claude mcp add --transport stdio tokensand -- npx -y https://tokensand.com/packages/dev-adoption-cli-0.1.8.tgz mcp serve --api https://tokensand.com
```
