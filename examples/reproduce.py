import argparse
import json
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import closing
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def run_case(out_dir, mode, name, request, db, schedule="open", extra=()):
    output = out_dir / f"{name}.json"
    trace = out_dir / f"{name}.trace.json"
    subprocess.run(
        [sys.executable, "-X", "utf8", "-m", "src", "--mode", mode, "--request", f"examples/inputs/{request}.json",
         "--schedule", f"data/schedule_{schedule}.json", "--db", str(db), "--output", str(output),
         "--trace", str(trace), *extra],
        cwd=ROOT, check=True, stdout=subprocess.DEVNULL,
    )
    count = 0
    if db.exists():
        with closing(sqlite3.connect(db)) as connection:
            count = connection.execute("SELECT count(*) FROM drafts").fetchone()[0]
    return read_json(output)["items"], read_json(trace)["items"], count


def main():
    parser = argparse.ArgumentParser(description="复跑案例，保存真实输出并核对验收结果。")
    parser.add_argument("--mode", choices=("ollama", "mock"), default="mock")
    parser.add_argument("--out-dir", type=Path)
    args = parser.parse_args()
    out_dir = (args.out_dir or ROOT / ".runtime" / "reproduced" / args.mode).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        db = Path(directory)
        cases = {
            "normal": run_case(out_dir, args.mode, "normal", "schedule", db / "open.db"),
            "duplicate": run_case(out_dir, args.mode, "duplicate", "schedule", db / "open.db"),
            "conflict": run_case(out_dir, args.mode, "conflict", "schedule", db / "busy.db", schedule="busy"),
            "confirmed": run_case(out_dir, args.mode, "confirmed", "confirmed", db / "busy.db", schedule="busy"),
            "review": run_case(out_dir, args.mode, "review", "review", db / "review.db"),
            "save_failure": run_case(out_dir, args.mode, "save_failure", "save_failure", db / "failure.db",
                                     extra=("--fail", "save_draft")),
            "step_limit": run_case(out_dir, args.mode, "step_limit", "schedule", db / "limit.db",
                                   extra=("--max-steps", "1")),
        }

    summary = {}
    output_fields = {"audit_status", "risks", "suggested_slots", "pending_confirmation", "draft"}
    trace_fields = {"step", "action", "arguments", "observation", "next_action", "reason"}
    for name, (items, traces, count) in cases.items():
        for item in items:
            if not output_fields <= item.keys() or "state" not in item["draft"]:
                raise SystemExit(f"{name}: incomplete delivery fields. See {out_dir}.")
        for trace in traces:
            steps = trace["steps"]
            if not steps or any(not trace_fields <= step.keys() for step in steps):
                raise SystemExit(f"{name}: incomplete trace fields. See {out_dir}.")
        summary[name] = {"status": [item["status"] for item in items],
                         "draft_state": [item["draft"]["state"] for item in items],
                         "draft_rows": count, "actions": [[step["action"] for step in trace["steps"]] for trace in traces]}
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    expected = {
        "normal": (["done"], ["saved"], 1),
        "duplicate": (["done"], ["reused"], 1),
        "conflict": (["needs_confirmation"], ["not_attempted"], 0),
        "confirmed": (["done"], ["saved"], 1),
        "review": (["needs_info", "blocked"], ["not_attempted", "not_attempted"], 0),
        "save_failure": (["incomplete"], ["failed"], 0),
        "step_limit": (["incomplete"], ["not_attempted"], 0),
    }
    for name, expectation in expected.items():
        result = summary[name]
        actual = result["status"], result["draft_state"], result["draft_rows"]
        if actual != expectation:
            raise SystemExit(f"{name}: expected {expectation}, got {actual}. See {out_dir}.")

    normal, duplicate, conflict, confirmed, review, failure, limit = (cases[name][0] for name in cases)
    checks = {
        "same_draft_id": normal[0]["draft"]["draft_id"] == duplicate[0]["draft"]["draft_id"],
        "conflict_suggests_19_without_writing": conflict[0]["pending_confirmation"]["slots"] == ["2026-11-09T19:00"],
        "explicit_confirmation": confirmed[0]["suggested_slots"] == ["2026-11-09T19:00"],
        "review_missing_owner_and_rule_risk": review[0]["missing_information"] == ["owner"] and
        review[1]["risks"][0] == {"id": "R01", "reason": "素材授权未通过审核。"},
        "failure_reason": failure[0]["reason"] == "tool_failure",
        "limit_reason": limit[0]["reason"] == "step_limit",
        "different_actions_after_slot_check": summary["normal"]["actions"][0][3] == "save_draft" and
        summary["conflict"]["actions"][0][3] == "find_alternatives",
    }
    for check, passed in checks.items():
        if not passed:
            raise SystemExit(f"{check} failed. See {out_dir}.")
    print(f"{args.mode}: {len(cases)} scenarios passed. See {out_dir}.")


if __name__ == "__main__":
    main()
