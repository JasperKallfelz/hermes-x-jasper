#!/usr/bin/env python3
"""Deterministic Claude JSON-envelope fake; never contacts a live model."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time


mode = sys.argv[1] if len(sys.argv) > 1 else "success"
prompt = sys.stdin.read()

if mode == "slow-light" and " LIGHT\n" in prompt:
    time.sleep(2)

if mode == "failure" or (mode == "deep-failure" and " DEEP\n" in prompt):
    print("temporary model failure", file=sys.stderr)
    raise SystemExit(3)
if mode == "policy":
    print("request not allowed by policy", file=sys.stderr)
    raise SystemExit(4)
if mode == "invalid-json":
    print("not-json")
    raise SystemExit(0)
if mode == "missing-envelope":
    print(json.dumps({"subtype": "success", "result": "ok"}))
    raise SystemExit(0)
if mode == "oversize":
    print(json.dumps({"structured_output": {"padding": "x" * 100000}}))
    raise SystemExit(0)
if mode == "oversize-stderr":
    sys.stderr.write("x" * 100000)
    raise SystemExit(0)
if mode == "descendant-timeout":
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    Path = __import__("pathlib").Path
    Path(os.environ["DREAM_FAKE_PID_PATH"]).write_text(str(child.pid))
    time.sleep(30)

if mode == "pipe-descendant":
    # Exit the leader normally while a SIGTERM-ignoring descendant retains
    # both inherited output pipes. The adapter must track and kill the group.
    code = (
        "import pathlib,signal,sys,time;"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN);"
        "pathlib.Path(sys.argv[1]).write_text('ready');"
        "time.sleep(30)"
    )
    Path = __import__("pathlib").Path
    pid_path = Path(os.environ["DREAM_FAKE_PID_PATH"])
    ready_path = pid_path.with_suffix(".ready")
    child = subprocess.Popen([sys.executable, "-c", code, str(ready_path)])
    pid_path.write_text(str(child.pid))
    for _ in range(100):
        if ready_path.exists():
            break
        time.sleep(0.01)

marker = "UNTRUSTED_DATA (JSON):\n"
data = json.loads(prompt.split(marker, 1)[1])

if " LIGHT\n" in prompt:
    refs = []
    roles = {}
    quotes = {}
    for session in data["sessions"]:
        for message in session["messages"]:
            refs.append(message["ref"])
            roles[message["ref"]] = message["role"]
            quotes[message["ref"]] = message["text"]
        for summary in session["lcm_summaries"]:
            refs.append(summary["ref"])
            roles[summary["ref"]] = summary["role"]
            quotes[summary["ref"]] = summary["text"]
    if mode == "missing-evidence":
        refs = ["unknown:session#m999"]
        roles[refs[0]] = "user"
        quotes[refs[0]] = "unknown"
    evidence = [
        {"ref": ref, "role": "assistant" if mode == "role-spoof" else roles[ref],
         "quote": "fabricated quote" if mode == "mismatched-quote" else quotes[ref][:80]}
        for ref in refs[:12]
    ]
    output = {
        "candidates": [{
            "kind": "open_loop" if mode == "open-loop" else "project",
            "claim": ("The moon is made of green cheese" if mode == "unrelated-supported"
                      else "Robin plans a grounded Second Brain consolidation"),
            "claim_identity": (
                {
                    "subject": "moon", "predicate": "made of", "object": "green cheese",
                    "polarity": "positive", "scope": "global",
                }
                if mode == "unrelated-supported"
                else {
                    "subject": "Robin", "predicate": "plans",
                    "object": "grounded Second Brain consolidation",
                    "polarity": "positive", "scope": "personal projects",
                }
            ),
            "detail": "Repeated across independent sessions.",
            "evidence": evidence,
            "confidence": 0.95,
            "durability": "durable",
            "actionability": "act" if mode == "open-loop" else "watch",
            "tags": ["second-brain"],
        }] if evidence else [],
        "themes": [{"name": "Second Brain", "summary": "Consolidation", "refs": refs[:2]}] if refs else [],
        "queries": ["Second Brain consolidation"],
    }
elif " REM\n" in prompt:
    refs = data["allowed_evidence_refs"]
    ids = [item["id"] for item in data["candidates"]]
    chosen = []
    sessions = set()
    ordered_refs = refs
    if mode in {"canonical-link", "canonical-missing-insight", "canonical-unsupported-insight", "canonical-unrelated-link"}:
        ordered_refs = [ref for ref in refs if ref.startswith("ov:")] + [ref for ref in refs if not ref.startswith("ov:")]
    for ref in ordered_refs:
        session = ref.split("#", 1)[0]
        if session not in sessions:
            chosen.append(ref)
            sessions.add(session)
        if len(chosen) == 2:
            break
    output = {
        "insights": [{
            "kind": "contradiction" if mode == "contradiction" else "connection",
            "claim": ("The moon is made of green cheese" if mode in {"unrelated-insight-supported", "canonical-unrelated-link"}
                      else "Second Brain consolidation connects repeated work across profiles."),
            "detail": "Grounded reflection.",
            "evidence": [{"ref": ref, "role": data["evidence_roles"][ref],
                          "quote": data["evidence_catalog"][ref]["quote"][:80]} for ref in chosen],
            "candidate_ids": ids[:1],
            "confidence": 0.8,
            "contradiction_sides": ["old", "current"] if mode == "contradiction" else [],
        }] if refs and ids else [],
        "notes": "bounded",
    }
else:
    all_items = list(data["candidates"]) + list(data.get("insights", []))
    output = {
        "reviews": [{"id": item.get("id") or item.get("insight_id"),
                     "supported": mode not in {"deep-unsupported", "unrelated-ref", "mismatched-quote"}
                                  and not (mode == "canonical-unsupported-insight" and "insight_id" in item),
                     "verdict": ("reject" if mode == "insight-reject" and "insight_id" in item
                                 else ("inbox" if mode == "open-loop" and "id" in item else "promote")),
                     "rationale": "Evidence semantically supports the item." if mode not in {"deep-unsupported", "unrelated-ref", "mismatched-quote"} else "Evidence is unrelated.",
                     "concerns": []}
                    for item in all_items
                    if not (mode in {"missing-deep-insight", "canonical-missing-insight"}
                            and "insight_id" in item)],
        "summary": "Grounded deep review complete.",
    }

print(json.dumps({"subtype": "success", "structured_output": output}))
