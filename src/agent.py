from .models import FINISH_ACTIONS, TOOLS


def tool_arguments(action, request, results, last_slot):
    material_id = request["material_id"]
    if action == "read_material":
        return {"material_id": material_id}
    metadata = results.get("read_material", {}).get("metadata")
    if action == "check_rules" and metadata is not None:
        return {"metadata": metadata}
    if metadata is None or results.get("check_rules", {}).get("state") != "pass":
        return None
    target = {"material_id": material_id, "channel": metadata["channel"], "slot": request["requested_at"]}
    if action == "check_slot" or (action == "save_draft" and last_slot == "available"):
        return target
    if action == "find_alternatives" and last_slot == "conflict":
        return {"material_id": material_id, "channel": metadata["channel"], "after_slot": target["slot"]}
    return None


def finish_reason(outcome, results, observations, last_slot):
    read_state = results.get("read_material", {}).get("state")
    review_state = results.get("check_rules", {}).get("state")
    save_state = results.get("save_draft", {}).get("state")
    alternatives = results.get("find_alternatives", {}).get("state")
    if outcome == "done" and save_state in ("saved", "reused"):
        return save_state
    if outcome == "needs_info" and (read_state == "missing_material" or review_state == "missing_information"):
        return "missing_material" if read_state == "missing_material" else "missing_information"
    if outcome == "blocked" and review_state == "risk":
        return "rule_risk"
    if outcome == "needs_confirmation" and last_slot == "conflict" and alternatives == "found":
        return "alternative_requires_confirmation"
    if outcome == "incomplete" and observations:
        last = observations[-1]["result"]["state"]
        return {"no_alternative": "no_alternative", "tool_error": "tool_failure"}.get(last)
    return None


def validate_decision(decision):
    if not isinstance(decision, dict):
        raise ValueError("decision is not an object")
    action = decision.get("action")
    reason = decision.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("missing public reason")
    if not isinstance(action, str) or (action not in FINISH_ACTIONS and action not in TOOLS):
        raise ValueError("invalid action")
    return action, reason.strip()[:200]


def run_item(request, model, business, max_steps):
    observations = []
    trace = []
    results = {}
    last_slot = None
    conflict = None
    saved = None
    status, reason_code = "incomplete", "step_limit"

    for step in range(1, max_steps + 1):
        try:
            action, reason = validate_decision(model.decide(request, observations))
        except (OSError, ValueError, KeyError, TypeError) as error:
            reason_code = "model_error"
            trace.append({"step": step, "action": "model_error", "arguments": {},
                          "reason": "模型调用失败或返回了无效动作。",
                          "observation": {"state": "model_error", "code": type(error).__name__}})
            break

        entry = {"step": step, "action": action, "reason": reason}
        trace.append(entry)
        if action in FINISH_ACTIONS:
            outcome = FINISH_ACTIONS[action]
            accepted = finish_reason(outcome, results, observations, last_slot)
            entry["arguments"] = {"outcome": outcome}
            if accepted:
                status, reason_code = outcome, accepted
                entry["observation"] = {"state": status, "code": reason_code}
                break
            result = {"state": "guard_rejected", "code": "unsupported_finish"}
            entry["observation"] = result
            observations.append({"action": action, "result": result})
            continue

        args = tool_arguments(action, request, results, last_slot)
        if args is None:
            result = {"state": "guard_rejected", "code": "missing_tool_precondition"}
        else:
            result = business.invoke(action, args)
            results[action] = result
            if action == "check_slot":
                last_slot = result["state"]
                conflict = result if last_slot == "conflict" else None
            if action in ("check_slot", "save_draft") and result["state"] == "conflict":
                last_slot, conflict = "conflict", result
            if action == "save_draft" and result["state"] in ("saved", "reused"):
                saved = saved or result

        entry.update(arguments=args or {}, observation=result)
        observations.append({"action": action, "result": result})

    for entry, next_entry in zip(trace, trace[1:]):
        entry["next_action"] = next_entry["action"]
    if trace:
        trace[-1]["next_action"] = None

    review = results.get("check_rules", {})
    read = results.get("read_material", {})
    audit_status = review.get("state", "not_reviewed")
    if read.get("state") == "missing_material":
        audit_status = "missing_material"
    missing = review.get("missing_fields", [])
    if audit_status == "missing_material":
        missing = ["material"]

    draft = saved or results.get("save_draft", {"state": "not_attempted"})
    if draft["state"] == "tool_error":
        draft = {"state": "failed", "code": draft["code"]}
    alternatives = results.get("find_alternatives", {}).get("slots", []) if last_slot == "conflict" else []
    return {
        **request,
        "status": status,
        "reason": reason_code,
        "audit_status": audit_status,
        "risks": review.get("risks", []),
        "missing_information": missing,
        "conflict": conflict,
        "suggested_slots": [request["requested_at"]] if saved or last_slot == "available" else alternatives,
        "pending_confirmation": {"slots": alternatives, "reason": "requested_slot_conflict"}
        if alternatives and not saved else None,
        "draft": draft,
    }, trace
