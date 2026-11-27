# Tokens& Agent Pack

Check an AI workflow change in your repository and see which cases failed.

## Install automatic workflow checks

With your npm project open in Codex or Claude Code, paste this once.

```text
Set up Tokens& once in this npm project. Reuse observe.workflow.mjs or create it around our actual workflow with named checks, synthetic tool responses and a zero provider budget. Run npx --yes https://github.com/giolux1235/tokensand-agent-pack/releases/download/workflow-check-0.1.19/tokensand-workflow-check-0.1.19.tgz --install-test. Append --github for a self-contained workflow at a GitHub repo root. Run npm test and show any failed or unscored checks. If the Git working tree started clean, commit only the setup locally, including a check that found a real bug. If Git needs my approval or the tree started dirty, leave my edits intact and list the setup files I need to commit. Keep results local unless I connect hosted reporting.
```

To install from a terminal, run this from the GitHub repository root with a self-contained offline `observe.workflow.mjs` that exercises your app.

```sh
npx --yes https://github.com/giolux1235/tokensand-agent-pack/releases/download/workflow-check-0.1.19/tokensand-workflow-check-0.1.19.tgz --install-test --github
```

Run the same install command to update an existing generated setup. Commit the setup files so teammates get the same checker. `npm test` runs it alongside the project’s tests. `npm run test:tokensand:watch` rechecks imported project files when you save, including after you fix a syntax or import error. Stop watching with Ctrl+C.

When a case fails, copy the replay command printed by the checker. It runs that case at least three times and shows which instrumented tools returned, rejected, or were still running at the deadline. An unscored check needs attention even when another check already failed. Fix the failure, restore the missing score and rerun the full suite.

GitHub runs self-contained cases on pull requests and default-branch pushes. The job has read-only repository access, with networking disabled during evaluation. Its summary shows case counts and the tested revision. Omit `--github` for a local-only install or a service inside a monorepo. To disconnect, revert the setup changes.


## Check a change with Codex or Claude Code

To make the skill available in every local project, install it once for both hosts:

```sh
npx --yes skills@1.9.0 add giolux1235/tokensand-agent-pack --skill tokensand-agent-pack --agent codex claude-code --global --yes
```

To limit the skill to one repository, run this from that project directory.

```sh
npx --yes skills@1.9.0 add giolux1235/tokensand-agent-pack --skill tokensand-agent-pack --agent codex -y
```

For Claude Code, replace `--agent codex` with `--agent claude-code`. Open a new agent session in the repository and ask:

> Use Tokens& to check this change and show me what failed.

Local checks run without an account, provider key or MCP connection. If you have no workflow tests, try the challenge below or run the [first local check](https://tokensand.com/agents/install#first-local-check).

### Run a shared Tokens& project

With the skill installed, give your coding agent the public `tokensand.com/p/…` link and ask:

> Run this shared project’s example locally. Show me which cases fail and whether the documented repair passes.

The agent follows the project’s **Repo** link and tests the edition linked from its page in a separate checkout. If the page has no pinned edition, it reports the revision it ran from the default branch. **Check your own project** starts setup for your own app.

### Review a release with a teammate

Use the same pinned private Node SDK for both runs. The reviewer recognizes 0.7.7 and 0.7.8. From a clean candidate checkout, choose the earlier release tag and its saved run. Declare the app code in `sourceFiles`. If the scorer changed, the reviewer flags the saved result for review.

```sh
npx --yes https://github.com/giolux1235/tokensand-agent-pack/releases/download/workflow-check-0.1.19/tokensand-workflow-check-0.1.19.tgz --review-release REFERENCE_RUN CANDIDATE_RUN --reference RELEASE_TAG --all-cases --note review.html
```

Open `review.html` to see the blocking cases, their scores and both Git revisions. Review the case names before sharing the note. Keep your run directories and private inputs and outputs on your machine.

A teammate can save the note as `review.html` and run this in the trusted candidate service checkout with both Git revisions available locally.

```sh
npx --yes https://github.com/giolux1235/tokensand-agent-pack/releases/download/workflow-check-0.1.19/tokensand-workflow-check-0.1.19.tgz --recheck-release ./review.html --all-cases --run
```

This rechecks every case **in the note**, three times per revision, using the earlier scorer. To narrow the selection, replace `--all-cases` with one or more `--case ID` flags. Self-contained workflows need no private SDK or account for replay. For offline use, install the pinned archive once and use `tokensand-check` in place of `npx --yes URL`.

Release review and replay exit `100` for a verified regression, `2` for inconclusive evidence and `0` when all selected cases pass. Omit `--run` to inspect a received note without executing project code. That preview also exits `2`. Three fresh passing trials leave a case inconclusive if the original check used more.

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
| [Node.js checker](https://github.com/giolux1235/tokensand-agent-pack/releases/download/workflow-check-0.1.19/tokensand-first.test.mjs) | `node tokensand-first.test.mjs --offline-workflow ./observe.workflow.mjs` |
| [Python checker](https://github.com/giolux1235/tokensand-agent-pack/releases/download/python-check-0.1.8/test_tokensand_first.py) | `python3 test_tokensand_first.py --offline-workflow ./my_workflow.py` |

Set `repetitions` to an integer from 1 to 10 to run every case that many times. Omit it to run each case once. Append `--case ID_A --case ID_B` to repeat only those cases, at least three times each. An ordinary local check exits `0` when its checks pass and `1` on a failed or unscored check. An invalid case selection or repetition count exits `2`. For expected and actual values, select one case with `--case CASE_ID --inspect-values` and open the private file it prints. Delete it after debugging.

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
python3 -m pip install https://github.com/giolux1235/tokensand-agent-pack/releases/download/python-check-0.1.8/tokensand_workflow_check-0.1.8-py3-none-any.whl
python3 -m tokensand_check --offline-workflow ./my_workflow.py
```

Try the [SQLite sync example](examples/incremental-sync.py) as `my_workflow.py`. The initial read passes. Resuming a page drops a record when two timestamps match. Rerun with `FIX_CURSOR=1` to verify the repair. The summary shows how many trials failed and how many lacked a required score. An affected trial can appear in both counts.

### Check a Lovable project

Open your [GitHub-synced Lovable project](https://docs.lovable.dev/integrations/github) in Codex or Claude Code. A local check needs no Tokens& account, gateway key or skill install. Copy this request into the agent:

> Use Tokens& to check my change locally. Run the existing offline workflow test if it covers this change. Otherwise create observe.workflow.mjs around the changed app function with synthetic model replies or tool results, expected outcomes, named checks and a zero provider budget, then run npx --yes https://github.com/giolux1235/tokensand-agent-pack/releases/download/workflow-check-0.1.19/tokensand-workflow-check-0.1.19.tgz --offline-workflow ./observe.workflow.mjs. Show which cases failed and why.

Before connecting a Node workflow, check the tool and step labels reached by its candidate cases:

```sh
npx --yes https://github.com/giolux1235/tokensand-agent-pack/releases/download/workflow-check-0.1.19/tokensand-workflow-check-0.1.19.tgz --offline-workflow ./observe.workflow.mjs --require-hosted-labels
```

A label warning names the required format and keeps the label private. The optional flag exits 2 for incompatible labels or when no instrumented call ran. A connected run verifies the full experiment.


For a working example, copy the [six-case quiz check](examples/lovable-study-review.mjs) to `observe.workflow.mjs` in a checkout of [SparkSage at the tested revision](https://github.com/tahsan2544/sparksage/tree/ab0bb8fa7c134d9baae19c3ec1aaeca6a7e85737). Run the command above on Node 24. It imports the app’s TypeScript parser and checks plain, fenced, prefaced, empty and malformed model replies. Append `--case markdown-json` to repeat that case three times. The original parser passes all six cases.

### Review a pull request

The automatic setup above runs self-contained Node cases on each PR. For workflows that need locally installed dependencies, you can share a revision-bound receipt instead. Save the [starter workflow](examples/github/starter-check.yml) as `.github/workflows/tokensand-starter.yml` in your repository. Commit it, your app changes and the offline `observe.workflow.mjs` you want to check. From that checkout, run the public checker.

```sh
npx --yes https://github.com/giolux1235/tokensand-agent-pack/releases/download/workflow-check-0.1.19/tokensand-workflow-check-0.1.19.tgz --pr-receipt
```

With the Agent Pack installed in Codex or Claude Code, you can ask “Prepare the Tokens& result for a teammate to review. Show me what failed and leave it uncommitted.”

Exit 0 means every case passed. Exit 100 means at least one case failed, including a case where the workflow itself threw an error. Both write `.tokensand/starter-check.json`. If a scorer throws or omits a required score, the command exits 1 and writes no fresh receipt. It still reports known failures. Fix them and restore the unscored checks before sharing. Review the receipt’s four public fields (schema, tested revision, passed cases and failed cases), then commit only the receipt to share those counts.

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
