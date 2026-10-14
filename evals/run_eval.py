"""Score investigations against evals/answer_key.json (which agents never see).

Usage:
  python -m evals.run_eval A B C           # start new investigations via the API and score them
  python -m evals.run_eval --score inv_id:A  # score an existing investigation

Metrics (file level): recall over required files, precision over reported files
(required + optional count as relevant; anything else, including distractors, counts against),
and citation validity (share of findings whose excerpt was re-found at the pinned commit).
"""
import json
import sys
import time
from pathlib import Path

import re

import httpx

from changeblast import repos

API = "http://localhost:8100/api"
KEY = json.loads((Path(__file__).parent / "answer_key.json").read_text())["scenarios"]
REPOS = ["billing-service", "payments-service", "notification-service", "checkout-web", "shared-contracts"]


def literal_baseline(change_request: str) -> set[tuple[str, str]]:
    """What a developer greps first: the numbers and snake_case identifiers written in the request."""
    terms = set(re.findall(r"\b\d+\b|\b[a-z]+(?:_[a-z]+)+\b", change_request))
    return {(r, h["path"]) for r in REPOS for t in terms for h in repos.search(r, t)["hits"]}


def score(report: dict, scenario: str) -> dict:
    key = KEY[scenario]
    as_set = lambda items: {(i["repo"], i["path"]) for i in items}  # noqa: E731
    required, optional, distractors = as_set(key["required"]), as_set(key["optional"]), as_set(key["distractors"])
    reported = {(f["repository"], f["path"]) for f in report["findings"]
                if f["evidence_status"] != "unverified" and f.get("impact_level") != "reviewed_no_change"}
    hit = required & reported
    relevant = reported & (required | optional)
    valid = [f for f in report["findings"] if f["evidence_status"] in ("verified", "inferred")]
    grep_files = {(r, p) for r, paths in report["stats"]["grep_baseline_files"].items() for p in paths}
    return {
        "scenario": scenario, "investigation_id": report["investigation_id"], "status": report["status"],
        "recall": round(len(hit) / len(required), 2),
        "precision": round(len(relevant) / len(reported), 2) if reported else 0.0,
        "citation_validity": round(len(valid) / len(report["findings"]), 2) if report["findings"] else 0.0,
        "literal_grep_recall": round(len(required & literal_baseline(key["change_request"])) / len(required), 2),
        "llm_expanded_grep_recall": round(len(required & grep_files) / len(required), 2),
        "missed": sorted(f"{r}/{p}" for r, p in required - reported),
        "distractors_reported": sorted(f"{r}/{p}" for r, p in distractors & reported),
        "extra": sorted(f"{r}/{p}" for r, p in reported - required - optional),
        "tool_calls": report["stats"]["tool_calls"],
    }


def run(scenario: str) -> dict:
    with httpx.Client(timeout=30) as http:
        r = http.post(f"{API}/investigations", json={"change_request": KEY[scenario]["change_request"],
                                                     "repositories": REPOS})
        inv = r.json()["investigation_id"]
        started = time.time()
        while True:
            status = http.get(f"{API}/investigations/{inv}").json()["status"]
            if status in ("COMPLETED", "PARTIAL", "FAILED"):
                break
            time.sleep(3)
        if status == "FAILED":
            return {"scenario": scenario, "investigation_id": inv, "status": "FAILED"}
        out = score(http.get(f"{API}/investigations/{inv}/report").json(), scenario)
        out["seconds"] = round(time.time() - started)
        return out


if __name__ == "__main__":
    args = sys.argv[1:] or ["A", "B", "C"]
    if args[0] == "--score":
        inv, scenario = args[1].split(":")
        report = httpx.get(f"{API}/investigations/{inv}/report", timeout=30).json()
        print(json.dumps(score(report, scenario), indent=2))
    else:
        for s in args:
            print(json.dumps(run(s), indent=2), flush=True)
