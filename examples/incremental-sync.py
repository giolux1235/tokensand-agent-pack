"""Synthetic incremental sync. Two records can share an update timestamp."""
import os
import sqlite3

ROWS = [(10, "a"), (10, "b"), (10, "c"), (11, "d")]


def sync(item):
    db = sqlite3.connect(":memory:")
    try:
        db.execute("CREATE TABLE changes (updated INTEGER, id TEXT PRIMARY KEY)")
        db.executemany("INSERT INTO changes VALUES (?, ?)", ROWS)
        timestamp, key = item["cursor"]
        if os.environ.get("FIX_CURSOR") == "1":
            rows = db.execute("SELECT id FROM changes WHERE (updated, id) > (?, ?) ORDER BY updated, id", (timestamp, key))
        else:
            rows = db.execute("SELECT id FROM changes WHERE updated > ? ORDER BY updated, id", (timestamp,))
        return [row[0] for row in rows]
    finally:
        db.close()


def evaluate(item, output, variant):
    def complete():
        if os.environ.get("ORACLE_DOWN") == "1":
            raise RuntimeError("private fixture detail")
        return output == item["expected"]
    return {"complete": complete, "unique": lambda: len(set(output)) == len(output)}


workflow = {
    "cases": [
        {"id": "initial", "input": {"cursor": [0, ""], "expected": ["a", "b", "c", "d"]}},
        {"id": "resume-tied-page", "input": {"cursor": [10, "b"], "expected": ["c", "d"]}},
    ],
    "checks": [
        {"id": "complete", "label": "Return every remaining record"},
        {"id": "unique", "label": "Return each record once"},
    ],
    "baseline": sync, "candidate": sync, "evaluate": evaluate,
}
