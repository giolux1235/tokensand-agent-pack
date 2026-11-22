// Copy to observe.workflow.mjs at the root of the SparkSage checkout linked in the README.
import { isDeepStrictEqual } from "node:util";
import { parseJson } from "./src/lib/review.server.ts";

const quiz = { questions: [{ question: "Which planet has rings?", choices: ["Mars", "Saturn", "Venus", "Mercury"], answer: 1 }] };
const json = JSON.stringify(quiz);
export default {
  id: "review-quiz", scope: "synthetic", timeoutMs: 1000, maxCallsPerRun: { candidate: 0, baseline: 0 },
  cases: [
    { id: "plain", input: { reply: json, expected: quiz } },
    { id: "markdown-json", input: { reply: "```json\n" + json + "\n```", expected: quiz } },
    { id: "assistant-preface", input: { reply: "Here is your review.\n" + json, expected: quiz } },
    { id: "empty-quiz", input: { reply: '{"questions":[]}', expected: {questions:[]} } },
    { id: "malformed", input: { reply: '{"questions":', expected: null } },
    { id: "no-response", input: { reply: "", expected: null } },
  ],
  checks: [
    { id: "reply", label: "Decode the expected review response" },
    { id: "choices", label: "Keep the original answer choices" },
  ],
  candidate: input => parseJson(input.reply),
  evaluate: ({ input, output }) => ({
    reply: isDeepStrictEqual(output, input.expected),
    choices: isDeepStrictEqual(output?.questions?.map(q => q.choices), input.expected?.questions?.map(q => q.choices)),
  }),
};
