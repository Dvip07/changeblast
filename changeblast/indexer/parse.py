"""Per-file parsing with tree-sitter (runs in worker processes). Pure: bytes in, facts out.

Definitions and references come from each language's tags query (the tags.scm files GitHub code
navigation uses); imports come from tree-sitter-language-pack's `process()`.
"""
import re

import tree_sitter as ts
import tree_sitter_language_pack as tslp

# Languages we build graph facts for: they have a tags query (defs/refs) in the language pack.
CODE_LANGS = {"python", "javascript", "typescript", "tsx", "java", "php", "go", "ruby", "csharp"}
# Code-like files we can't put in the graph yet: counted against the quality score, still text-searchable.
UNSUPPORTED_CODE_EXT = {".vue", ".svelte", ".kt", ".kts", ".scala", ".swift", ".rs", ".c", ".h", ".cpp", ".cc",
                        ".hpp", ".m", ".dart", ".lua", ".pl", ".r", ".ex", ".exs", ".erl", ".clj", ".groovy"}

# Additions to the stock tags queries. The language pack's TypeScript tags cover only TS-specific nodes
# (upstream TS tags inherit the JavaScript ones), and JS tags only treat a const as a definition when it
# holds a function. Top-level constants are where config values like REFUND_WINDOW_MS live, so add them.
_JS_EXTRA = """
(program (lexical_declaration (variable_declarator name: (identifier) @name) @definition.constant))
(program (export_statement (lexical_declaration (variable_declarator name: (identifier) @name) @definition.constant)))
"""
_TS_EXTRA = _JS_EXTRA + """
(type_alias_declaration name: (type_identifier) @name) @definition.type
"""
_QUERY_SOURCES = {
    "javascript": lambda: tslp.get_tags_query("javascript") + _JS_EXTRA,
    "typescript": lambda: tslp.get_tags_query("javascript") + tslp.get_tags_query("typescript") + _TS_EXTRA,
    "tsx": lambda: tslp.get_tags_query("javascript") + tslp.get_tags_query("tsx") + _TS_EXTRA,
}

_queries: dict[str, ts.Query] = {}


def _query(lang: str) -> ts.Query:
    if lang not in _queries:
        source = _QUERY_SOURCES.get(lang, lambda: tslp.get_tags_query(lang))()
        _queries[lang] = ts.Query(tslp.get_language(lang), source)
    return _queries[lang]


def _import_specs(lang: str, statement: str) -> list[dict]:
    """Normalise one import statement into {module, names, level} records."""
    s = " ".join(statement.split())
    if lang == "python":
        m = re.match(r"from\s+(\.*)([\w.]*)\s+import\s+(.+)", s)
        if m:
            names = [n.strip().split(" as ")[0].strip("() ") for n in m.group(3).split(",")]
            return [{"module": m.group(2), "level": len(m.group(1)), "names": [n for n in names if n and n != "*"]}]
        m = re.match(r"import\s+(.+)", s)
        if m:
            return [{"module": part.strip().split(" as ")[0], "level": 0, "names": []}
                    for part in m.group(1).split(",")]
        return []
    if lang in ("javascript", "typescript", "tsx"):
        m = re.search(r"""(?:from\s+|require\(\s*|import\s*\(\s*|^import\s+)["']([^"']+)["']""", s)
        if not m:
            return []
        names = re.findall(r"\b(?:type\s+)?(\w+)(?:\s+as\s+\w+)?\s*[,}]", s.split("from")[0]) if "{" in s else []
        default = re.match(r"import\s+(\w+)\s*(?:,|from)", s)
        return [{"module": m.group(1), "level": 0, "names": names + ([default.group(1)] if default else [])}]
    if lang == "java":
        m = re.match(r"import\s+(?:static\s+)?([\w.]+)(\.\*)?\s*;", s)
        return [{"module": m.group(1), "level": 0, "names": [], "wildcard": bool(m.group(2))}] if m else []
    return [{"module": s[:200], "level": 0, "names": [], "unparsed": True}]


def parse_file(path: str, lang: str, source: bytes) -> dict:
    """Return defs, refs, imports, and parse health for one file."""
    out = {"path": path, "language": lang, "loc": source.count(b"\n") + 1, "has_error": False,
           "defs": [], "refs": [], "imports": [], "error": None}
    try:
        tree = tslp.get_parser(lang).parse(source)
        out["has_error"] = tree.root_node.has_error
        for _, caps in ts.QueryCursor(_query(lang)).matches(tree.root_node):
            names = caps.get("name") or []
            if not names:
                continue
            name = names[0].text.decode("utf-8", "replace")
            for key, nodes in caps.items():
                if key.startswith("definition."):
                    node = nodes[0]
                    out["defs"].append({"name": name, "kind": key.split(".", 1)[1],
                                        "line_start": node.start_point[0] + 1, "line_end": node.end_point[0] + 1})
                elif key.startswith("reference."):
                    out["refs"].append({"name": name, "kind": key.split(".", 1)[1],
                                        "line": names[0].start_point[0] + 1})
        result = tslp.process(source.decode("utf-8", "replace"), tslp.ProcessConfig(lang, imports=True))
        for imp in result.imports:
            for spec in _import_specs(lang, imp.source):
                spec["line"] = imp.span.start_line + 1 if hasattr(imp.span, "start_line") else 0
                out["imports"].append(spec)
    except Exception as e:  # noqa: BLE001 - a bad file must not stop the index; it lowers the score
        out["error"] = f"{type(e).__name__}: {str(e)[:120]}"
    return out
