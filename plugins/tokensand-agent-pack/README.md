# Tokens& for Cursor

Use [Connect your agent](https://tokensand.com/agents/install?mode=developer), reload Cursor, then call `get_account_mode`. The plugin already includes MCP configuration; add it manually only if the tools are missing.

## Manual setup

```json
{
  "mcpServers": {
    "tokensand": {
      "command": "npx",
      "args": ["-y", "https://tokensand.com/packages/dev-adoption-cli-0.1.34.tgz", "mcp", "serve", "--api", "https://tokensand.com"]
    }
  }
}
```

Website package **0.1.34** is pinned by archive URL; check the live manifest before upgrading. npm remains **0.1.12**; do not substitute `@dev-adoption/cli@0.1.34` for the archive URL. Update the package and restart existing MCP sessions to use this release.

## Check one useful task

Ask: **“Keep Next.js, Supabase auth and pgvector. Add streaming answers. Find only what's missing, with docs.”**

The result should preserve the existing stack and identify the streaming integration. It is a build plan; model access, cost and a working implementation still need checking in your repo.

For `build_brief`, put the complete task in `intent`; `query` is optional. Returned implementation examples are optional source code to inspect and test, not automatic execution.

For a reproduced public lookup with exact tool arguments, see [the existing-stack tracker example](../../examples/existing-supabase-tracker.md). It records what the returned brief established and what still needs implementation.

Public lookups need no account. For private saved plans, configure a scoped `DAI_TOKEN` using [auth.md](https://tokensand.com/auth.md). Browser login is separate. Keep credentials out of committed configuration.

Use `--mode public` for public-only access, including when credentials were saved previously. Switch an explicitly public command to `--mode developer` before using private context. Project-scoped tokens are read-only.

For live enterprise reads, follow the [included skill](skills/tokensand-agent-pack/SKILL.md). A sample company profile does not authenticate a workspace or prove live outcomes.

## Local plugin check

From the repository root, copy the plugin into Cursor's local plugin directory:

```bash
mkdir -p ~/.cursor/plugins/local/tokensand-agent-pack
cp -R plugins/tokensand-agent-pack/. ~/.cursor/plugins/local/tokensand-agent-pack/
```

Use a real directory: [Cursor skips symlinks whose targets are outside its local plugin folder](https://cursor.com/docs/plugins#test-plugins-locally). Reload Cursor and verify that both the skill and MCP server appear in Customize, then call `get_account_mode`. A marketplace installation with the same name takes precedence over this local test copy. Refresh the copy when testing later changes.

A repository or install link is not marketplace approval.
