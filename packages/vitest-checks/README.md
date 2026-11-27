# Connect an existing Vitest workflow

This reporter sends native pass/fail counts for one workflow test file to a private Tokens& app. Select a trusted file whose ordinary test cases are your acceptance checks. Vitest defines a passing case, including any case its author wrote without assertions.

From your application checkout, install the versioned GitHub release as a development dependency.

```sh
npm install --save-dev https://github.com/giolux1235/tokensand-agent-pack/releases/download/vitest-checks-0.1.0/tokensand-vitest-checks-0.1.0.tgz
```

To build the same asset locally, run `mkdir -p dist && python3 packages/vitest-checks/build.py dist` from the Agent Pack source checkout and install `dist/tokensand-vitest-checks-0.1.0.tgz` by local path. No npm registry release is required.

Append `'@tokensand/vitest-checks'` to the existing `test.reporters` in your Vitest configuration. If you were using Vitest’s implicit default reporter, use this setting to keep your usual terminal output:

```js
test: { reporters: ['default', '@tokensand/vitest-checks'] }
```

Enable **Repository checks** in your private Tokens& app. From the same Git checkout, enroll once. Use the repository-check app ID and its existing ingestion key in an owner-only file outside the checkout. This command stores a reference to the file; the integration never creates or copies a key. Keep any existing private account or ingestion keys outside your test environment.

```sh
TOKENS_OBSERVE_INGEST_URL=https://tokensand.com/api/observability/v1/events \
  npx --no-install tokensand-vitest connect APP_ID tests/orders.test.ts \
  --key-file /absolute/path/to/existing-owner-only-key --queue-offline
npm test
```

For a named Vitest workspace project add `--vitest-project NAME`. Tests retain their usual reporter and exit behavior. The private receipt contains a random ID, completion time, app and connection identifiers, and native case counts. Pending receipts reuse their original ID when delivered again. A new full run is a different receipt. With the offline queue enabled, a native run gives the collector up to 600 ms total to acknowledge its pending batch. An unconfirmed response keeps its original receipt pending. Disk and lock time are additional. Explicit sync uses the normal transport timeout. With `--queue-offline`, the next full run or `npx --no-install tokensand-vitest sync` retries the local outbox. `npx --no-install tokensand-vitest disconnect` stops this Vitest connection and deletes only its pending counts.

This version supports Node 20.19+, 22.12+, or 24+, Vitest 4.1.x, Git and Python 3.10+ on POSIX. Normal tests need neither a Tokens& key nor an endpoint environment variable. Use trusted checkouts only. Test code running under your OS account can open files your account can read, including a known key-file path. Keep untrusted forks disconnected. Watch, incomplete or name-filtered runs, skipped/expected-failure/retried cases, suite errors, and files containing a failed case that ran a beforeEach or afterEach hook do not send a receipt. That includes a genuine failed assertion following a successful hook. Projects using globalSetup are excluded because its teardown can report errors after the results callback. Completed results from an ordinary full selected file can also be included in a larger native test run. Explicit CLI reporter overrides are honored. Remove any other reporting for the same native workflow before enabling this reporter.
