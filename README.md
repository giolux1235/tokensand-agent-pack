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

### Review an existing Python test

Start in the later checkout with the skill installed. Use the same shell for these commands. Set the path to your earlier checkout and the test you want to review. Run each checkout with its own installed dependencies.

```sh
earlier=../earlier-service
target=tests/test_workflow.py
reports=$(mktemp -d)
(cd "$earlier" && python -m pytest -q "$target" --junitxml="$reports/before.xml")
python -m pytest -q "$target" --junitxml="$reports/after.xml"
```

A failing test still produces a report. Once both runs finish, review their results locally:

```sh
python3 .agents/skills/tokensand-agent-pack/scripts/review_pytest.py "$reports/before.xml" "$reports/after.xml" --before-root "$earlier" --after-root . --test-source "$target"
```

For Claude Code, use `.claude/skills` in the reviewer path. Pass each test file in the reports. The reviewer checks parent directories up to each checkout root for `conftest.py` and pytest configuration. Changed fixture code or pytest settings hold the repair for review. In shared config files such as `pyproject.toml`, it compares pytest settings when it can read them. A full-file hash is used otherwise.

Add imported fixture helpers, data and custom configuration with `--test-source` to compare those entire files. To locate a fixture, run `python -m pytest --fixtures-per-test "$target"` in the trusted checkout. Check the original pytest summaries for unexpected passes. Keep the XML reports local.

### Run a local check without installing a package

Save the checker for your language in your repository. Run it with your own trusted workflow file.

| Download | Command |
| --- | --- |
| [Node.js checker](https://github.com/giolux1235/tokensand-agent-pack/releases/download/offline-check-0.1.10/tokensand-first.test.mjs) | `node tokensand-first.test.mjs --offline-workflow ./observe.workflow.mjs` |
| [Python checker](https://github.com/giolux1235/tokensand-agent-pack/releases/download/python-check-0.1.5/test_tokensand_first.py) | `python3 test_tokensand_first.py --offline-workflow ./my_workflow.py` |

Append `--case CASE_ID --inspect-values` to see three trials. Open the private file printed by the checker for each returned value and the expected value when the case supplies one. Delete the file after debugging.

If a hosted reservation reports 20 retained comparisons, use the command above for your trusted, offline candidate. It checks your current code immediately and keeps the result on your machine. Preserve the original run directory for hosted review.

For a first run, omit `--offline-workflow` and its path. The bundled retry example deliberately fails when the same tool call runs twice.

### Did my fix pass the original checks?

With the skill installed, ask Codex or Claude Code to review one case using your before and after run directories and the earlier service checkout. The reviewer scores the saved outputs using your original checks. It keeps inputs and outputs on your machine.

For a project-local Codex install, run this from the later service directory:

```sh
node "$(git rev-parse --show-toplevel)/.agents/skills/tokensand-agent-pack/scripts/review-repair-v1.mjs" BEFORE_RUN_DIR AFTER_RUN_DIR --case CASE_ID --original-checkout EARLIER_SERVICE
```

For Claude Code, use `.claude/skills` in the same command. You can also [download the standalone reviewer](https://github.com/giolux1235/tokensand-agent-pack/releases/download/repair-review-1.0.0/review-repair-v1.mjs) once and use that file from any service. Use two saved offline Node SDK runs with Git revisions, declared scorer files and the same case. Install the service’s pinned SDK and the earlier checkout’s dependencies first. Exit `0` confirms the selected repair under the recorded checks, `100` holds it, and `1` means setup or evidence needs attention.

### Check a Python workflow

In your project’s virtual environment, install the checker and run your trusted local workflow:

```sh
python3 -m pip install https://raw.githubusercontent.com/giolux1235/tokensand-agent-pack/0a6d1c9254d9b41a7888e1df29a358fc656952b5/packages/tokensand_workflow_check-0.1.5-py3-none-any.whl
python3 -m tokensand_check --offline-workflow ./my_workflow.py
```

Try the [SQLite sync example](examples/incremental-sync.py) as `my_workflow.py`. The initial read passes. Resuming a page drops a record when two timestamps match. Rerun with `FIX_CURSOR=1` to verify the repair. “Unscored checks” means a required scorer could not produce a boolean. Resolve it before approving the change.

### Check a Lovable project

Open your [GitHub-synced Lovable project](https://docs.lovable.dev/integrations/github) in Codex or Claude Code. A local check needs no Tokens& account, gateway key or skill install. Copy this request into the agent:

> Use Tokens& to check my change locally. Run the existing offline workflow test if it covers this change. Otherwise create observe.workflow.mjs around the changed app function with synthetic model replies or tool results, expected outcomes, named checks and a zero provider budget, then run npx --yes https://raw.githubusercontent.com/giolux1235/tokensand-agent-pack/2d6f77ae7e74aaedd9071334e3a23f0b6bbe717b/packages/tokensand-workflow-check-0.1.11.tgz --offline-workflow ./observe.workflow.mjs. Show which cases failed and why.

Before connecting a Node workflow, check the tool and step labels reached by its candidate cases:

```sh
npx --yes https://raw.githubusercontent.com/giolux1235/tokensand-agent-pack/2d6f77ae7e74aaedd9071334e3a23f0b6bbe717b/packages/tokensand-workflow-check-0.1.11.tgz --offline-workflow ./observe.workflow.mjs --require-hosted-labels
```

A label warning names the required format and keeps the label private. The optional flag exits 2 for incompatible labels or when no instrumented call ran. A connected run verifies the full experiment.


For a working example, copy the [six-case quiz check](examples/lovable-study-review.mjs) to `observe.workflow.mjs` in a checkout of [SparkSage at the tested revision](https://github.com/tahsan2544/sparksage/tree/ab0bb8fa7c134d9baae19c3ec1aaeca6a7e85737). Run the command above on Node 24. It imports the app’s TypeScript parser and checks plain, fenced, prefaced, empty and malformed model replies. Append `--case markdown-json` to repeat that case three times. The original parser passes all six cases.

### Review a pull request

Save the [starter workflow](examples/github/starter-check.yml) as `.github/workflows/tokensand-starter.yml` in your repository. Commit it, your app changes and the offline `observe.workflow.mjs` you want to check. From that checkout, run the public checker.

```sh
npx --yes https://raw.githubusercontent.com/giolux1235/tokensand-agent-pack/2d6f77ae7e74aaedd9071334e3a23f0b6bbe717b/packages/tokensand-workflow-check-0.1.11.tgz --pr-receipt
```

With the Agent Pack installed in Codex or Claude Code, you can ask “Prepare the Tokens& result for a teammate to review. Show me what failed and leave it uncommitted.”

Exit 0 means every candidate case passed. Exit 100 means some failed. Both produce `.tokensand/starter-check.json`. An unscored check exits 1 and writes no new receipt. Rerun it locally to see which evaluator needs attention. Review its four public fields (schema, tested revision, passed cases and failed cases). To share them with reviewers, commit just that file and push your branch.

```sh
git add -- .tokensand/starter-check.json
git commit --only -m "Record starter check" -- .tokensand/starter-check.json
git push
```

GitHub validates the revision and shows the author-reported counts. Private test output stays on your machine. After another code change or rebase, record a fresh result. When a case fails, the PR summary gives reviewers a command to reproduce it in a trusted checkout of the evaluated revision. This starter checks the candidate only. For SDK baseline comparisons, see the [integration guide](https://tokensand.com/docs/api#observe-workflow).

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

Run `node --test tests/starter-release.test.mjs` before releasing a new checker. It checks the archive used by the agent instructions, this guide and the PR workflow.

The reviewed source is `plugins/tokensand-cursor-plugin` in the private application repository. Sync the MCP configuration and skill after verifying the live package manifest and an isolated public MCP replay. Preserve this repository's marketplace path and license. Update the plugin version so installed clients can detect the release. Source changes alone do not refresh an installed client.
