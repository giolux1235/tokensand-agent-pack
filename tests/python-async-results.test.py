"""An asynchronous return value cannot certify that a synchronous workflow ran."""
import asyncio
import runpy
import unittest
import warnings
from pathlib import Path

case_suite = runpy.run_path(str(Path(__file__).resolve().parents[1] / "plugins/tokensand-agent-pack/skills/tokensand-agent-pack/scripts/check_workflow.py"))["case_suite"]


class AsyncResultTests(unittest.TestCase):
    def run_workflow(self, candidate, evaluate):
        workflow = {
            "cases": [{"id": "current-passage", "input": "docs"}],
            "checks": [{"id": "found"}], "candidate": candidate, "evaluate": evaluate,
        }
        result = unittest.TestResult()
        case_suite(workflow).run(result)
        return result

    def test_unfinished_candidate_never_reaches_scorer(self):
        calls = []
        async def fetch(item):
            calls.append(item)
            return None
        async def stream(item):
            yield item
        class AsyncCursor:
            def __aiter__(self):
                return stream("docs")
        class Pending:
            def __await__(self):
                return fetch("docs").__await__()

        for target in (fetch, lambda item: fetch(item), stream, lambda _: AsyncCursor(), lambda _: Pending()):
            with self.subTest(target=target), warnings.catch_warnings(record=True) as observed:
                result = self.run_workflow(target, lambda *args: calls.append("scored") or {"found": True})
                self.assertEqual(calls, [])
                self.assertEqual(len(result.errors), 1)
                self.assertIn("candidate returned an unfinished async result", result.errors[0][1])
                self.assertEqual(observed, [])

    def test_suspended_coroutine_stays_under_its_owners_control(self):
        import inspect
        async def suspended():
            await asyncio.sleep(0)
        value = suspended()
        value.send(None)
        try:
            result = self.run_workflow(lambda _: value, lambda *_: {"found": True})
            self.assertEqual(len(result.errors), 1)
            self.assertEqual(inspect.getcoroutinestate(value), inspect.CORO_SUSPENDED)
        finally:
            value.close()

    def test_unfinished_evaluator_names_the_adapter_without_warning(self):
        async def evaluate(*_):
            return {"found": True}
        with warnings.catch_warnings(record=True) as observed:
            result = self.run_workflow(lambda item: item, evaluate)
            self.assertEqual(len(result.errors), 1)
            self.assertIn("evaluate returned an unfinished async result", result.errors[0][1])
            self.assertEqual(observed, [])

    def test_sync_adapter_scores_resolved_results(self):
        async def fetch(item):
            return item
        for value, expected_failures in [(None, 1), ("docs", 0)]:
            with self.subTest(value=value):
                result = self.run_workflow(lambda _: asyncio.run(fetch(value)),
                                           lambda _, output, variant: {"found": output is not None})
                self.assertEqual(len(result.failures), expected_failures)
                self.assertEqual(result.errors, [])


if __name__ == "__main__":
    unittest.main()
