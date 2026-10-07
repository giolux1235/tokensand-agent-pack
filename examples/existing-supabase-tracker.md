# Keep an existing stack: private project tracker

Use this public lookup when your repository already uses Supabase Auth and Postgres. It produces a sourced brief for your coding agent; implementing and testing the tracker happens in your repository.

## Connect

Follow [the install instructions](https://tokensand.com/agents/install). The tested website package is CLI **0.1.27**; the npm package is still **0.1.12**. For a public-only MCP session, set or replace the command's mode argument with `--mode public` and restart it. This disables private credentials, including previously saved tokens.

Call `get_account_mode` to confirm the connection is public before continuing. Installing the skill alone does not configure this MCP connection.

## Ask your coding agent

> Use Tokens& to brief this task: build a private project tracker using our existing Supabase Auth and Postgres. Each signed-in user can read and edit only their own projects. No AI features and no additional auth provider. Keep our current stack, show the provider docs, and explain what still needs implementing and testing.

The equivalent `build_brief` arguments are:

```json
{
  "intent": "Build a private project tracker using existing Supabase Auth and Postgres. Each signed-in user can read and edit only their own projects. No AI features and no additional auth provider.",
  "query": "Supabase Auth Postgres private project tracker",
  "useCase": "internal_tool",
  "limit": 5
}
```

## Check the result

The public MCP replay on October 7, 2026 returned:

- Supabase as the single selected tool and a link to [its official documentation](https://supabase.com/docs).
- Instructions to reuse the repository's existing tools and test signed-out access and isolation between two users.
- A public free-plan reference with provider-controlled limits and terms. Estimated cost remained unknown; no credit redemption was performed.
- Suppressed adoption evidence because the minimum evidence threshold was not met. Missing evidence does not establish zero usage or a product ranking.
- `publicOnly: true` and `persisted: false`.

This was a synthetic, read-only technical check using the unmodified CLI archive, with telemetry disabled. It verified a returned brief, not a completed application, customer adoption, a saved Build Packet or provider eligibility. Catalog results can change.

## Continue in your repository

Have your coding agent inspect the existing authentication, schema and tests, then implement the smallest tracker flow. Verify signed-in access, signed-out rejection and isolation between two distinct users before calling it complete. Follow the provider's current documentation and your repository instructions.

The brief is not saved automatically. To resume a private plan later, save it in the [workbench](https://tokensand.com/dashboard?mode=developer#build-workbench) and configure a scoped token using [auth.md](https://tokensand.com/auth.md). No public project submission is required.
