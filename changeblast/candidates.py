"""Choose where agents should look, from the code graph instead of grep alone.

1. Seeds: keyword hits (screened "likely" by the cheap gate weigh more) + definitions whose names
   contain identifier-like intent terms (e.g. credit_usage -> credit_usage_ratio).
2. Blast-radius walk: reverse edges (who imports / references a seed), 2 hops, each step weighted by
   edge confidence (resolved 1.0, name_match 0.6, ambiguous 0.25) and halved per hop.
   Only references to the symbols that matter are followed (callers of get_due_date, not every importer
   of the file), and each hop continues from the function containing the call: a call chain.
   Tests are leaves: weighted down, never expanded from, capped at 30% of candidates.
3. Rank, then partition: small repos get one agent; large repos are split by module so each agent
   gets a bounded, related set of candidates instead of drowning in grep output.
Every candidate keeps the reasons it was picked, so agents (and reviewers) can see why.
"""
import re
from collections import defaultdict

from changeblast import graphdb
from changeblast.indexer.resolve import is_test

SMALL_REPO_FILES = 400
MAX_PARTITIONS_PER_REPO = 4
MAX_CANDIDATES_PER_PARTITION = 20
MAX_CANDIDATES_PER_REPO = 60
HOP_DECAY = 0.5
TEST_WEIGHT = 0.5      # tests matter (they must be updated) but never drive the walk
MAX_TEST_SHARE = 0.3


def identifier_tokens(terms: list[str]) -> list[str]:
    """Only identifier-like terms (snake_case, camelCase, dotted); plain words like 'credit' match too much."""
    out = set()
    for t in terms:
        t = t.strip()
        if re.search(r"[a-z][A-Z]|_|\.", t) and " " not in t:
            out.add(t.split(".")[-1])
    return sorted(out)


def select(repo: str, intent: dict, gated: dict) -> list[dict]:
    """Seed files + the symbols that matter in them, then follow callers of those symbols (call chains)."""
    score: dict[str, float] = defaultdict(float)
    reasons: dict[str, list[str]] = defaultdict(list)
    focus: dict[str, set[str]] = defaultdict(set)  # file -> symbols in it that the change touches

    def note(path: str, text: str) -> None:
        if len(reasons[path]) < 5 and text not in reasons[path]:
            reasons[path].append(text)

    hit_lines = []
    for weight, hits, label in ((1.0, gated.get("likely", []), "keyword hit, screened likely"),
                                (0.35, gated.get("screened", []), "keyword hit, screened unlikely")):
        for h in hits:
            w = weight * (TEST_WEIGHT if is_test(h["path"]) else 1.0)
            score[h["path"]] += w if score[h["path"]] == 0 else 0.1 * w
            note(h["path"], f"{label}: L{h['line']} `{h['text'][:80]}`")
            hit_lines.append((h["path"], h["line"]))
    for (path, _line), sym in graphdb.enclosing_symbols(repo, hit_lines).items():
        focus[path].add(sym)

    for s_ in graphdb.symbol_seeds(repo, identifier_tokens(intent.get("search_terms", []))):
        score[s_["path"]] += 0.8 if s_["name"] not in focus[s_["path"]] else 0.1
        focus[s_["path"]].add(s_["name"])
        note(s_["path"], f"defines `{s_['name']}` ({s_['kind']}) at L{s_['line']}")

    frontier = [p for p in sorted(score, key=score.get, reverse=True) if not is_test(p)][:30]
    for hop in (1, 2):
        rows = graphdb.reverse_refs(repo, frontier)
        gains: dict[str, float] = defaultdict(float)
        callers = []
        for src, dst, kind, symbol, conf, line in rows:
            wanted = focus.get(dst)
            if kind == "imports":
                continue  # a module import says little; the call/reference edges carry the symbol
            if wanted and symbol not in wanted:
                continue  # uses something else in that file: not in this blast radius
            gain = score[dst] * HOP_DECAY * graphdb.CONF_WEIGHT[conf] * (TEST_WEIGHT if is_test(src) else 1.0)
            if gain < 0.05:
                continue
            gains[src] = max(gains[src], gain)
            note(src, f"calls `{symbol}` from {dst} at L{line} ({conf}, hop {hop})")
            if not is_test(src):
                callers.append((src, line))
        for src, gain in gains.items():
            score[src] += min(gain, 1.0)
        # Next hop follows the functions that contain those calls: a call chain, not a file fan-out.
        for (path, _line), sym in graphdb.enclosing_symbols(repo, callers).items():
            focus[path].add(sym)
        frontier = [p for p in sorted(gains, key=gains.get, reverse=True) if not is_test(p)][:40]

    ranked = sorted(score.items(), key=lambda kv: kv[1], reverse=True)
    out, tests = [], 0
    for path, sc in ranked:
        if is_test(path):
            if tests >= MAX_CANDIDATES_PER_REPO * MAX_TEST_SHARE:
                continue
            tests += 1
        out.append({"path": path, "score": round(sc, 3), "reasons": reasons[path],
                    "symbols": sorted(focus.get(path, []))[:6]})
        if len(out) >= MAX_CANDIDATES_PER_REPO:
            break
    return out


def _module(path: str, depth: int) -> str:
    parts = path.split("/")[:-1]
    return "/".join(parts[:depth]) or "(root)"


def partition(repo: str, candidates: list[dict]) -> list[dict]:
    """One agent per small repo; up to MAX_PARTITIONS_PER_REPO module-scoped agents per large repo."""
    if graphdb.file_count(repo) <= SMALL_REPO_FILES or len(candidates) <= MAX_CANDIDATES_PER_PARTITION:
        return [{"agent_id": repo, "repo": repo, "scope": "", "candidates": candidates[:MAX_CANDIDATES_PER_REPO]}]
    # Pick the shallowest directory depth that separates candidates into more than one module.
    depth = next((d for d in (2, 3, 1) if len({_module(c["path"], d) for c in candidates}) > 1), 2)
    groups: dict[str, list[dict]] = defaultdict(list)
    for c in candidates:
        groups[_module(c["path"], depth)].append(c)
    ordered = sorted(groups.items(), key=lambda kv: sum(c["score"] for c in kv[1]), reverse=True)
    keep, rest = ordered[:MAX_PARTITIONS_PER_REPO - 1], ordered[MAX_PARTITIONS_PER_REPO - 1:]
    parts = [(mod, cs) for mod, cs in keep]
    if rest:  # long tail of small modules goes to one agent that sees the whole repo
        parts.append(("", [c for _, cs in rest for c in cs]))
    out = []
    for mod, cs in parts:
        cs = sorted(cs, key=lambda c: c["score"], reverse=True)[:MAX_CANDIDATES_PER_PARTITION]
        out.append({"agent_id": f"{repo}--{mod.replace('/', '.')}" if mod else f"{repo}--other",
                    "repo": repo, "scope": mod, "candidates": cs})
    return out
