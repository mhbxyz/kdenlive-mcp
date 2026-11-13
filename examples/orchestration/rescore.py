"""Recompute the scores of finished bench runs (after a change to score.py).

python examples/orchestration/rescore.py RUNS_DIR
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import score

runs = Path(sys.argv[1]).resolve()
records = []
for result in sorted(runs.glob("*/result.json")):
    record = json.loads(result.read_text(encoding="utf-8"))
    answer = "\n".join(r.get("answer") or "" for r in record["runs"])
    record["score"] = score.score(record["demo"], Path(record["work"]), answer)
    result.write_text(json.dumps(record, indent=1, default=str), encoding="utf-8")
    records.append(record)
with (runs / "results.jsonl").open("w", encoding="utf-8") as fh:
    for record in records:
        fh.write(json.dumps(record, default=str) + "\n")
print(f"{len(records)} runs rescored")
