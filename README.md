# Tokens& Agent Pack

Check an AI workflow change in your repository and see which cases failed.

## Check a change with Codex or Claude Code

From your project directory, install the skill for the agent you use.

```sh
npx --yes skills@1.9.0 add giolux1235/tokensand-agent-pack --skill tokensand-agent-pack --agent codex -y
```

For Claude Code, replace `--agent codex` with `--agent claude-code`. Open a new agent session in the repository and ask:

> Use Tokens& to check this change and show me what failed.

Local checks run without an account, provider key or MCP connection. If you have no workflow tests, try the challenge below or run the [first local check](https://tokensand.com/agents/install#first-local-check).

### Check a Lovable project

Open your [GitHub-synced Lovable project](https://docs.lovable.dev/integrations/github) in Codex or Claude Code. A local check needs no Tokens& account, gateway key or skill install. Copy this request into the agent:

> Use Tokens& to check my change locally. Run the existing offline workflow test if it covers this change. Otherwise create observe.workflow.mjs around the changed app function with synthetic model replies or tool results, expected outcomes, named checks and a zero provider budget, then run npx --yes https://raw.githubusercontent.com/giolux1235/tokensand-agent-pack/7df0a16bfe09d0ec15bb97ffa3922334170e6d47/packages/tokensand-workflow-check-0.1.7.tgz --offline-workflow ./observe.workflow.mjs. Show which cases failed and why.

For a working example, copy the [six-case quiz check](examples/lovable-study-review.mjs) to `observe.workflow.mjs` in a checkout of [SparkSage at the tested revision](https://github.com/tahsan2544/sparksage/tree/ab0bb8fa7c134d9baae19c3ec1aaeca6a7e85737). Run the command above on Node 24. It imports the app’s TypeScript parser and checks plain, fenced, prefaced, empty and malformed model replies. Append `--case markdown-json` to repeat that case three times. The original parser passes all six cases.

### Review a pull request

Save the [starter workflow](https://raw.githubusercontent.com/giolux1235/tokensand-agent-pack/cd5bf9a3632cb9f594780cbb3bf93fcfd16e94e1/examples/github/starter-check.yml) as `.github/workflows/tokensand-starter.yml` in your repository. Commit it, your app changes and the offline `observe.workflow.mjs` you want to check. From that checkout, run the public checker.

```sh
npx --yes https://raw.githubusercontent.com/giolux1235/tokensand-agent-pack/7df0a16bfe09d0ec15bb97ffa3922334170e6d47/packages/tokensand-workflow-check-0.1.7.tgz --pr-receipt
```

With the Agent Pack installed in Codex or Claude Code, you can ask “Prepare the Tokens& result for a teammate to review. Show me what failed and leave it uncommitted.”

Exit 0 means every candidate case passed. Exit 100 means some failed. Both produce `.tokensand/starter-check.json`. Review its four public fields (schema, tested revision, passed cases and failed cases). To share them with reviewers, commit just that file and push your branch.

```sh
git add -- .tokensand/starter-check.json
git commit --only -m "Record starter check" -- .tokensand/starter-check.json
git push
```

GitHub validates the revision and shows the author-reported counts. Private test output stays on your machine. After another code change or rebase, record a fresh result. To see case names and diagnose a failure locally, replace `--pr-receipt` with `--offline-workflow ./observe.workflow.mjs`. This starter checks the candidate only. For SDK baseline comparisons, see the [integration guide](https://tokensand.com/docs/api#observe-workflow).

### Try a partner challenge in your repository

Run this in your checkout. For Claude Code, replace `.agents/` with `.claude/`. It downloads the synthetic cases for the Tinybird practice challenge the first time. The cases cover late events, duplicate deliveries, and latency reporting.

```sh
(test -e release-telemetry-v1.mjs || curl -fsSL --remove-on-error -o release-telemetry-v1.mjs https://tokensand.com/agent-pack/release-telemetry-v1.mjs) &&
node .agents/skills/tokensand-agent-pack/scripts/check_workflow.mjs --offline-workflow ./release-telemetry-v1.mjs
```

The example has three failing cases. Connect the candidate to your app’s reporting function, then rerun. The check runs locally without a Tinybird account. [Challenge and partner docs](https://tokensand.com/challenges#idea-partner-tinybird-agent-telemetry).

## Connect Cursor or access saved work

Install the Cursor plugin, reload Cursor and ask Tokens& to check your change. The plugin includes the workflow skill and MCP. [Connect your agent](https://tokensand.com/agents/install?mode=developer)

Public tool lookup works without signing in. Private saved projects require a scoped `DAI_TOKEN`. Use the published archive for the MCP server version listed in the live manifest. [Manual Cursor setup](plugins/tokensand-agent-pack/README.md) · [Live manifest](https://tokensand.com/.well-known/tokensand-agent-pack.json) · [Credentials](https://tokensand.com/auth.md)

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
