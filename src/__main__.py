import argparse
import json
from datetime import datetime
from pathlib import Path

from .agent import run_item
from .business import BusinessTools
from .models import TOOLS, MockModel, OllamaModel


ROOT = Path(__file__).resolve().parents[1]
SLOT_FORMAT = "%Y-%m-%dT%H:%M"


def load(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def parse_request(payload):
    materials = payload.get("materials") if isinstance(payload, dict) else None
    if not isinstance(materials, list) or not materials:
        raise ValueError("materials must be a non-empty list")
    requests = []
    for item in materials:
        if not isinstance(item, dict) or not isinstance(item.get("material_id"), str) or not item["material_id"].strip():
            raise ValueError("each material needs a non-empty material_id")
        slot = item.get("requested_at")
        if not isinstance(slot, str) or datetime.strptime(slot, SLOT_FORMAT).strftime(SLOT_FORMAT) != slot:
            raise ValueError("requested_at must be YYYY-MM-DDTHH:MM in Asia/Shanghai")
        requests.append({"material_id": item["material_id"].strip(), "requested_at": slot})
    return requests


def serialize(value):
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def main():
    parser = argparse.ArgumentParser(description="素材审核与发布排期草稿 Agent")
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--mode", choices=("ollama", "mock"), default="ollama")
    parser.add_argument("--catalog", type=Path, default=ROOT / "data/materials.json")
    parser.add_argument("--schedule", type=Path, default=ROOT / "data/schedule_open.json")
    parser.add_argument("--db", type=Path, default=ROOT / ".runtime/drafts.sqlite3")
    parser.add_argument("--trace", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--fail", choices=TOOLS, help="inject a tool failure before execution")
    parser.add_argument("--max-steps", type=int, default=8, help="model decisions per material, including finish")
    args = parser.parse_args()
    if args.max_steps < 1:
        parser.error("--max-steps must be at least 1")

    try:
        requests = parse_request(load(args.request))
        business = BusinessTools(load(args.catalog), load(args.schedule), args.db, fail=args.fail)
        args.db.parent.mkdir(parents=True, exist_ok=True)
    except (OSError, ValueError, KeyError) as error:
        parser.error(str(error))

    model = OllamaModel() if args.mode == "ollama" else MockModel()
    items, traces = [], []
    for request in requests:
        item, steps = run_item(request, model, business, args.max_steps)
        items.append(item)
        traces.append({"request": request, "status": item["status"], "reason": item["reason"], "steps": steps})

    output = {"status": "complete" if all(item["status"] == "done" for item in items) else "incomplete", "items": items}
    trace = {"mode": args.mode, "model": model.name, "max_steps_per_material": args.max_steps,
             "injected_failure": args.fail, "items": traces}
    if args.output:
        args.output.write_text(serialize(output), encoding="utf-8")
    if args.trace:
        args.trace.write_text(serialize(trace), encoding="utf-8")
    print(serialize(output), end="")


if __name__ == "__main__":
    main()
