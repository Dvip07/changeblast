"""Deterministic 0-100 index quality score: how far the dependency graph can be trusted.

Per language:
  parse_coverage     files parsed without an exception
  error_free         parsed files whose syntax tree has no ERROR nodes
  import_resolution  internal imports resolved to a file (external packages excluded)
  reference_linking  references to in-repo names linked to one definition (resolved or unique name)
Overall = file-weighted mean of language scores x share of code files we can graph at all.
"""
from collections import defaultdict

# Reference linking dominates: it decides whether "who uses this?" has a trustworthy answer.
# (First version weighted parsing 45% and scored ERPNext 86 with only 44% of Python references linked.)
WEIGHTS = {"parse_coverage": 0.15, "error_free": 0.10, "import_resolution": 0.30, "reference_linking": 0.45}


def _ratio(num: int, den: int) -> float | None:
    return round(num / den, 3) if den else None


def quality_score(files: list[dict], ref_stats: dict, categories: dict[str, str], dynamic_sites: int = 0) -> dict:
    by_lang = defaultdict(list)
    for f in files:
        by_lang[f["language"]].append(f)

    languages = {}
    for lang, fs in sorted(by_lang.items()):
        s = ref_stats.get(lang, {})
        parsed = [f for f in fs if not f["error"]]
        linked = s.get("refs_resolved", 0) + s.get("refs_name_match", 0)
        components = {
            "parse_coverage": _ratio(len(parsed), len(fs)),
            "error_free": _ratio(sum(1 for f in parsed if not f["has_error"]), len(parsed)),
            "import_resolution": _ratio(s.get("imports_resolved", 0),
                                        s.get("imports_resolved", 0) + s.get("imports_unresolved", 0)),
            "reference_linking": _ratio(linked, linked + s.get("refs_ambiguous", 0) + s.get("refs_too_generic", 0)),
        }
        available = {k: v for k, v in components.items() if v is not None}
        weight = sum(WEIGHTS[k] for k in available)
        score = round(100 * sum(WEIGHTS[k] * v for k, v in available.items()) / weight) if weight else 0
        languages[lang] = {"files": len(fs), "score": score, **components,
                           "not_measured": [k for k, v in components.items() if v is None],
                           "counts": {k: v for k, v in s.items()}}

    code_files = sum(1 for c in categories.values() if c == "code")
    unsupported = sum(1 for c in categories.values() if c == "unsupported_code")
    graphable = _ratio(code_files, code_files + unsupported) or 0.0
    weighted = sum(l["score"] * l["files"] for l in languages.values()) / max(sum(l["files"] for l in languages.values()), 1)
    overall = round(weighted * graphable) if code_files else None  # no code: not applicable, not zero

    notes = []
    for lang, l in languages.items():
        weakest = min(((k, l[k]) for k in WEIGHTS if l[k] is not None), key=lambda kv: kv[1], default=None)
        if weakest and weakest[1] < 0.8:
            notes.append(f"{lang}: {weakest[0].replace('_', ' ')} {weakest[1]:.0%}")
        if l["not_measured"]:
            notes.append(f"{lang}: {', '.join(n.replace('_', ' ') for n in l['not_measured'])} not measured")
    if not code_files:
        notes.append("no source code to graph (contracts, docs or config only); text search applies")
    if unsupported:
        notes.append(f"{unsupported} code files in languages without graph support (text search only)")
    if dynamic_sites:
        notes.append(f"{dynamic_sites} calls pick their target from a string at runtime (e.g. getattr, "
                     f"frappe.get_doc(\"Sales Invoice\")); those links are not in the graph, text search covers them")
    return {"overall": overall, "graphable_share": graphable, "languages": languages, "notes": notes,
            "dynamic_dispatch_sites": dynamic_sites}
