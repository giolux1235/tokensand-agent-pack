// Synthetic offline starter for the Cognee challenge. Nothing here calls Cognee.
// Replace recall with your app's memory lookup. Give it only the query, return
// the latest value or null for an unknown fact, and keep the cases unchanged.
const first = { key: "dispatch-room", revision: 1, value: "Cedar" };
const correction = { key: "dispatch-room", revision: 2, value: "Birch" };
const other = { key: "support-shift", revision: 1, value: "evening" };

function example(id, notes, key, expected) {
  return { id, input: { query: { notes, key }, expected } };
}

function recall({ notes, key }) {
  return notes.find(note => note.key === key)?.value ?? null;
}

function reference({ notes, key }) {
  const matches = notes.filter(note => note.key === key);
  return matches.sort((a, b) => b.revision - a.revision)[0]?.value ?? null;
}

export default {
  id: "memory-refresh", title: "Agent memory after a correction", scope: "synthetic",
  checksVersion: "1", sourceFiles: [], evaluatorSourceFiles: [],
  cases: [
    example("known-before-correction", [first, other], first.key, "Cedar"),
    example("corrected-fact", [first, correction, other], first.key, "Birch"),
    example("older-note-delivered-late", [correction, first], first.key, "Birch"),
    example("unrelated-fact-unchanged", [first, other, correction], other.key, "evening"),
    example("unknown-fact", [first, correction], "handoff-time", null),
  ],
  checks: [{ id: "current_answer", label: "Return the latest known value or null for an unknown fact" }],
  repetitions: 3, timeoutMs: 1000, maxCallsPerRun: { baseline: 0, candidate: 0 },
  baseline: ({ query }) => reference(query),
  candidate: ({ query }) => recall(query),
  evaluate: ({ input, output }) => ({ current_answer: output === input.expected }),
};
