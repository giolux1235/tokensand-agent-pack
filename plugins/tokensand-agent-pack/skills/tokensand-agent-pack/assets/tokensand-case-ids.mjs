// A Node test reporter. Emit only failing case IDs from the checker's encoded trial names.
export default async function* caseIds(events) {
  const failed = new Set();
  for await (const { type, data } of events) {
    if (type !== 'test:fail') continue;
    try {
      const name = JSON.parse(data.name);
      if (Array.isArray(name) && typeof name[0] === 'string') failed.add(name[0]);
    } catch {
      // A file-load error has no case ID. The counts reporter rejects incomplete runs.
    }
  }
  yield JSON.stringify([...failed]);
}
