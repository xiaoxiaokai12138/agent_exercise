import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

from src.__main__ import load
from src.agent import run_item
from src.business import BusinessTools, Drafts
from src.models import MockModel


ROOT = Path(__file__).resolve().parents[1]
REQUEST = {"material_id": "M01", "requested_at": "2026-11-09T18:00"}
ALTERNATIVE = "2026-11-09T19:00"


class AgentTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.db = self.root / "drafts.sqlite3"
        self.model = MockModel()

    def business(self, schedule="open", fail=None):
        return BusinessTools(load(ROOT / "data/materials.json"), load(ROOT / f"data/schedule_{schedule}.json"),
                             self.db, fail=fail)

    def row_count(self):
        with closing(sqlite3.connect(self.db)) as db:
            return db.execute("SELECT count(*) FROM drafts").fetchone()[0]

    def test_cli_normal_and_duplicate_across_processes(self):
        output_path = self.root / "result.json"
        trace_path = self.root / "trace.json"
        command = [sys.executable, "-X", "utf8", "-m", "src", "--mode", "mock", "--request",
                   "examples/inputs/schedule.json", "--db", str(self.db), "--output", str(output_path),
                   "--trace", str(trace_path)]
        results = []
        for _ in range(2):
            process = subprocess.run(command, cwd=ROOT, capture_output=True, encoding="utf-8", check=True)
            output = json.loads(process.stdout)
            self.assertEqual(load(output_path), output)
            self.assertEqual(output["status"], "complete")
            results.append(output["items"][0])
        first, second = results
        self.assertEqual(first["audit_status"], "pass")
        self.assertEqual(first["suggested_slots"], [REQUEST["requested_at"]])
        self.assertIsNone(first["pending_confirmation"])
        self.assertEqual(first["draft"]["state"], "saved")
        self.assertEqual(second["draft"]["state"], "reused")
        self.assertEqual(first["draft"]["draft_id"], second["draft"]["draft_id"])
        self.assertEqual(self.row_count(), 1)
        trace = load(trace_path)
        self.assertEqual(trace["mode"], "mock")
        self.assertEqual(trace["items"][0]["steps"][2]["next_action"], "save_draft")

    def test_missing_material_and_metadata(self):
        for material_id, expected in (("M99", ["material"]), ("M02", ["owner"])):
            with self.subTest(material_id=material_id):
                item, trace = run_item({**REQUEST, "material_id": material_id}, self.model, self.business(), 8)
                self.assertEqual(item["status"], "needs_info")
                self.assertEqual(item["missing_information"], expected)
                self.assertEqual(trace[-1]["action"], "finish_needs_info")
        self.assertFalse(self.db.exists())

    def test_risk_blocks_scheduling(self):
        item, trace = run_item({**REQUEST, "material_id": "M03"}, self.model, self.business(), 8)
        self.assertEqual(item["status"], "blocked")
        self.assertEqual(item["risks"], [{"id": "R01", "reason": "素材授权未通过审核。"}])
        self.assertNotIn("check_slot", [step["action"] for step in trace])
        self.assertFalse(self.db.exists())

    def test_unusable_metadata_cannot_be_scheduled(self):
        for field, value in (("license_confirmed", "false"), ("license_confirmed", 0), ("channel", None)):
            with self.subTest(field=field, value=value):
                business = self.business()
                business.materials["M01"][field] = value
                if field == "channel":
                    business.rules["required_fields"].remove("channel")
                item, trace = run_item(REQUEST, self.model, business, 8)
                self.assertEqual(item["status"], "needs_info")
                self.assertEqual(item["missing_information"], [field])
                self.assertNotIn("save_draft", [step["action"] for step in trace])
        self.assertFalse(self.db.exists())

    def test_conflict_requires_explicit_resubmission(self):
        business = self.business("busy")
        item, trace = run_item(REQUEST, self.model, business, 8)
        self.assertEqual(item["status"], "needs_confirmation")
        self.assertEqual(item["requested_at"], REQUEST["requested_at"])
        self.assertEqual(item["pending_confirmation"]["slots"], [ALTERNATIVE])
        self.assertEqual(item["draft"]["state"], "not_attempted")
        self.assertEqual(trace[2]["next_action"], "find_alternatives")
        self.assertEqual(self.row_count(), 0)
        confirmed, _ = run_item({**REQUEST, "requested_at": ALTERNATIVE}, self.model, business, 8)
        self.assertEqual(confirmed["draft"]["state"], "saved")
        self.assertEqual(self.row_count(), 1)

    def test_injected_save_failure_is_reported(self):
        item, trace = run_item(REQUEST, self.model, self.business(fail="save_draft"), 8)
        self.assertEqual(item["status"], "incomplete")
        self.assertEqual(item["reason"], "tool_failure")
        self.assertEqual(item["suggested_slots"], [REQUEST["requested_at"]])
        self.assertEqual(item["draft"], {"state": "failed", "code": "injected_failure"})
        self.assertEqual(trace[-2]["observation"]["state"], "tool_error")
        self.assertEqual(self.row_count(), 0)

    def test_sqlite_write_failure_is_reported(self):
        Drafts(self.db)
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute("""CREATE TRIGGER reject_insert BEFORE INSERT ON drafts
                          BEGIN SELECT RAISE(ABORT, 'write denied'); END""")
        item, _ = run_item(REQUEST, self.model, self.business(), 8)
        self.assertEqual(item["reason"], "tool_failure")
        self.assertEqual(item["draft"]["state"], "failed")
        self.assertEqual(self.row_count(), 0)

    def test_concurrent_writes_deduplicate_and_reject_other_material(self):
        drafts = Drafts(self.db)
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(drafts.save, "M01", "short_video", REQUEST["requested_at"]) for _ in range(4)]
            results = [future.result() for future in futures]
        self.assertCountEqual([result["state"] for result in results], ["saved", "reused", "reused", "reused"])
        self.assertEqual(len({result["draft_id"] for result in results}), 1)
        self.assertEqual(drafts.save("M04", "short_video", REQUEST["requested_at"])["state"], "conflict")
        self.assertEqual(self.row_count(), 1)

    def test_guard_rejects_save_after_conflict_and_unbacked_success(self):
        actions = iter(["read_material", "check_rules", "check_slot", "find_alternatives", "save_draft",
                        "finish_done", "finish_needs_confirmation"])

        class UnsafeModel:
            def decide(self, request, observations):
                return {"action": next(actions), "reason": "测试不安全模型动作。"}

        item, trace = run_item(REQUEST, UnsafeModel(), self.business("busy"), 8)
        self.assertEqual(trace[-3]["observation"]["state"], "guard_rejected")
        self.assertEqual(trace[-2]["observation"]["code"], "unsupported_finish")
        self.assertEqual(item["status"], "needs_confirmation")
        self.assertEqual(item["draft"]["state"], "not_attempted")
        self.assertEqual(self.row_count(), 0)

    def test_publish_action_is_unavailable(self):
        class PublishModel:
            def decide(self, request, observations):
                return {"action": "publish", "reason": "测试发布动作。"}

        item, trace = run_item(REQUEST, PublishModel(), self.business(), 8)
        self.assertEqual(item["status"], "incomplete")
        self.assertEqual(item["reason"], "model_error")
        self.assertEqual(item["draft"]["state"], "not_attempted")
        self.assertEqual([step["action"] for step in trace], ["model_error"])
        self.assertFalse(self.db.exists())

    def test_step_limit_keeps_persisted_state_visible(self):
        item, trace = run_item(REQUEST, self.model, self.business(), 4)
        self.assertEqual(item["status"], "incomplete")
        self.assertEqual(item["reason"], "step_limit")
        self.assertEqual(item["draft"]["state"], "saved")
        self.assertEqual(len(trace), 4)
        self.assertEqual(self.row_count(), 1)


if __name__ == "__main__":
    unittest.main()
