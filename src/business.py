import sqlite3
from contextlib import closing
from uuid import uuid4


class Drafts:
    def __init__(self, path):
        self.path = path
        with closing(self.connect()) as db, db:
            db.execute("""CREATE TABLE IF NOT EXISTS drafts (
                id TEXT PRIMARY KEY, material_id TEXT NOT NULL,
                channel TEXT NOT NULL, slot TEXT NOT NULL, UNIQUE(channel, slot)
            )""")

    def connect(self):
        return sqlite3.connect(self.path, timeout=5)

    @staticmethod
    def lookup(db, channel, slot):
        return db.execute(
            "SELECT id, material_id FROM drafts WHERE channel = ? AND slot = ?",
            (channel, slot),
        ).fetchone()

    def occupancy(self, channel, slot):
        with closing(self.connect()) as db:
            return self.lookup(db, channel, slot)

    def save(self, material_id, channel, slot):
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            existing = self.lookup(db, channel, slot)
            if existing:
                if existing[1] == material_id:
                    return {"state": "reused", "draft_id": existing[0]}
                return {"state": "conflict", "source": "draft"}
            draft_id = uuid4().hex
            db.execute("INSERT INTO drafts VALUES (?, ?, ?, ?)", (draft_id, material_id, channel, slot))
        return {"state": "saved", "draft_id": draft_id}


class BusinessTools:
    def __init__(self, catalog, schedule, draft_path, fail=None):
        self.materials = catalog["materials"]
        self.rules = catalog["rules"]
        self.schedule = schedule
        self.draft_path = draft_path
        self.drafts = None
        self.fail = fail

    def draft_store(self):
        if self.drafts is None:
            self.drafts = Drafts(self.draft_path)
        return self.drafts

    def invoke(self, name, args):
        if self.fail == name:
            return {"state": "tool_error", "code": "injected_failure"}
        try:
            return getattr(self, name)(**args)
        except sqlite3.Error as error:
            return {"state": "tool_error", "code": getattr(error, "sqlite_errorname", type(error).__name__)}

    def read_material(self, material_id):
        material = self.materials.get(material_id)
        if material is None:
            return {"state": "missing_material"}
        return {"state": "found", "metadata": material}

    def check_rules(self, metadata):
        required = dict.fromkeys([*self.rules["required_fields"], "channel"])
        missing = []
        for field in required:
            value = metadata.get(field)
            if field == "license_confirmed":
                usable = isinstance(value, bool)
            elif field == "channel":
                usable = isinstance(value, str) and bool(value.strip())
            else:
                usable = value is not None and (not isinstance(value, str) or bool(value.strip()))
            if not usable:
                missing.append(field)
        risks = [{"id": rule["id"], "reason": rule["reason"]} for rule in self.rules["blocked_if"]
                 if rule["field"] not in missing and rule["field"] in metadata
                 and metadata[rule["field"]] == rule["value"]]
        if risks:
            state = "risk"
        elif missing:
            state = "missing_information"
        else:
            state = "pass"
        return {"state": state, "missing_fields": missing, "risks": risks}

    def static_slot(self, channel, slot):
        if slot in self.schedule["occupied"].get(channel, []):
            return {"state": "conflict", "source": "existing_schedule"}
        if slot not in self.schedule["slots"].get(channel, []):
            return {"state": "conflict", "source": "outside_publish_window"}
        return {"state": "available"}

    def check_slot(self, material_id, channel, slot):
        result = self.static_slot(channel, slot)
        if result["state"] != "available":
            return result
        existing = self.draft_store().occupancy(channel, slot)
        if existing:
            if existing[1] == material_id:
                return {"state": "available", "existing_draft_id": existing[0]}
            return {"state": "conflict", "source": "draft"}
        return result

    def find_alternatives(self, material_id, channel, after_slot):
        candidates = []
        for slot in sorted(set(self.schedule["slots"].get(channel, []))):
            if slot[:10] == after_slot[:10] and slot > after_slot:
                result = self.check_slot(material_id, channel, slot)
                if result["state"] == "available" and "existing_draft_id" not in result:
                    candidates.append(slot)
        return {"state": "found" if candidates else "no_alternative", "slots": candidates[:3]}

    def save_draft(self, material_id, channel, slot):
        result = self.static_slot(channel, slot)
        if result["state"] != "available":
            return result
        return self.draft_store().save(material_id, channel, slot)
