"""Markdown export of a finished report."""


def to_markdown(r: dict) -> str:
    n = r.get("narrative") or {}
    by_id = {f["finding_id"]: f for f in r["findings"]}

    def cite(ids: list[str]) -> str:
        return ", ".join(f"`{i}`" for i in ids) or "_no evidence_"

    out = [f"# Change impact report `{r['investigation_id']}`", "",
           f"**Change request:** {r['change_request']}", "",
           f"**Status:** {r['status']} · **Confidence:** {n.get('confidence', 'n/a')} · "
           f"verified {r['stats']['verified']}, inferred {r['stats']['inferred']}, "
           f"unverified {r['stats']['unverified']}", "",
           "**Pinned commits:** " + ", ".join(f"{x['name']}@{x['commit'][:7]}" for x in r["repositories"]), "",
           "## Summary", "", r["summary"], ""]
    if n.get("confidence_rationale"):
        out += [f"_Confidence rationale:_ {n['confidence_rationale']}", ""]
    if r["missing_coverage"]:
        out += ["## Missing coverage", ""] + [f"- {m['repo']} ({m['task_id']}): {m['error']}"
                                              for m in r["missing_coverage"]] + [""]
    out += ["## Findings", "", "| ID | Evidence | Level | Location | Impact | Claim |", "|---|---|---|---|---|---|"]
    for f in r["findings"]:
        claim = f["claim"].replace("|", "\\|").replace("\n", " ")
        out.append(f"| `{f['finding_id']}` | {f['evidence_status']} | {f.get('impact_level', '')} | "
                   f"`{f['repository']}/{f['path']}:{f['line_start']}-{f['line_end']}` | {f['impact_type']} | {claim} |")
    out.append("")
    if n:
        out += ["## Risks", ""]
        for x in n["risks"]:
            out.append(f"- **[{x['severity']}] {x['title']}**{' _(speculative)_' if x.get('speculative') else ''}: "
                       f"{x['description']} ({cite(x['finding_ids'])})")
        out += ["", "## Implementation plan", ""]
        for x in sorted(n["plan"], key=lambda s: s["order"]):
            dep = f" (after {', '.join(map(str, x['depends_on']))})" if x["depends_on"] else ""
            out.append(f"{x['order']}. **{x['service']}** [{x['effort_band']}]{dep}: {x['change']} "
                       f"({cite(x['finding_ids'])})")
        out += ["", "## Test plan", ""]
        for x in n["tests"]:
            out.append(f"- **{x['name']}** ({x['service']}, {x['kind']}): {x['description']} "
                       f"({cite(x['finding_ids'])})")
        out += ["", "## Open questions", ""] + [f"- {q}" for q in n["open_questions"]]
    out += ["", "## Evidence", ""]
    for fid, f in by_id.items():
        out += [f"**`{fid}`** {f['repository']}@{f['commit_sha'][:7]} `{f['path']}:{f['line_start']}-"
                f"{f['line_end']}` ({f['evidence_status']})", "", "```", f["evidence_excerpt"], "```", ""]
    return "\n".join(out)
