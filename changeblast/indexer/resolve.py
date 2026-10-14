"""Turn per-file facts into graph edges, with an honest confidence on every edge.

  resolved    import path maps to a file in the repo (or the reference was imported from one)
  name_match  reference whose name is defined in exactly one other file
  ambiguous   name defined in 2..MAX_AMBIGUOUS other files (edge to each, weighted down)
Names defined in more places than that are too generic to link (counted, not drawn).
"""
import posixpath
import re
from collections import defaultdict

MAX_AMBIGUOUS = 4
GENERIC_NAMES = {"__init__", "get", "set", "run", "main", "execute", "validate", "save", "update", "delete", "load",
                 "init", "setup", "render", "handle", "process", "call", "apply", "to_dict", "toString", "constructor"}
FAMILY = {"python": "py", "javascript": "js", "typescript": "js", "tsx": "js", "java": "jvm", "php": "php",
          "go": "go", "ruby": "rb", "csharp": "cs"}
TEST_PATTERN = re.compile(r"(^|/)(tests?|__tests__|spec)/|(^|/)test_[^/]*$|_test\.\w+$|\.(test|spec)\.\w+$")


def is_test(path: str) -> bool:
    return bool(TEST_PATTERN.search(path))


JS_EXTS = ["", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", "/index.ts", "/index.tsx", "/index.js", "/index.jsx"]


def python_modules(paths: set[str]) -> dict[str, str]:
    """Dotted module name -> file, rooted at the nearest ancestor that is not a package."""
    has_init = {posixpath.dirname(p) for p in paths if p.endswith("/__init__.py") or p == "__init__.py"}
    modules = {}
    for p in paths:
        if not p.endswith(".py"):
            continue
        parts = p[:-3].split("/")
        # walk up while the parent directory is a package
        i = len(parts) - 1
        while i > 0 and "/".join(parts[:i]) in has_init:
            i -= 1
        mod_parts = parts[i:]
        if mod_parts[-1] == "__init__":
            mod_parts = mod_parts[:-1]
        if mod_parts:
            modules[".".join(mod_parts)] = p
    return modules


class Resolver:
    def __init__(self, files: list[dict]):
        self.files = {f["path"]: f for f in files}
        self.paths = set(self.files)
        self.py_modules = python_modules(self.paths)
        self.py_top = {m.split(".")[0] for m in self.py_modules}
        self.java_files = {p for p in self.paths if p.endswith(".java")}
        self.defs_by_name: dict[str, list[tuple[str, str, int]]] = defaultdict(list)
        for f in files:
            for d in f["defs"]:
                self.defs_by_name[d["name"]].append((f["path"], d["kind"], d["line_start"]))

    # ------------------------------------------------------------------ imports
    def resolve_import(self, f: dict, spec: dict) -> tuple[str, str | None]:
        """Returns (status, target): status in resolved | unresolved | external | n/a."""
        lang, mod = f["language"], spec["module"]
        if spec.get("unparsed"):
            return "n/a", None
        if lang == "python":
            if spec["level"]:
                base = f["path"].split("/")[:-1]
                base = base[: len(base) - (spec["level"] - 1)] if spec["level"] > 1 else base
                candidates = ["/".join(base + mod.split(".")) if mod else "/".join(base)]
                for c in candidates:
                    for path in (c + ".py", c + "/__init__.py"):
                        if path in self.paths:
                            return "resolved", path
                return "unresolved", None
            for name in [f"{mod}.{n}" for n in spec["names"]] + [mod]:
                if name in self.py_modules:
                    return "resolved", self.py_modules[name]
            return ("unresolved", None) if mod.split(".")[0] in self.py_top else ("external", None)
        if lang in ("javascript", "typescript", "tsx"):
            if not mod.startswith("."):
                return "external", None
            base = posixpath.normpath(posixpath.join(posixpath.dirname(f["path"]), mod))
            for ext in JS_EXTS:
                if base + ext in self.paths:
                    return "resolved", base + ext
            return "unresolved", None
        if lang == "java":
            suffix = mod.replace(".", "/") + ".java"
            hit = next((p for p in self.java_files if p.endswith(suffix)), None)
            if hit:
                return "resolved", hit
            pkg = "/".join(mod.split(".")[:-1])
            return ("unresolved", None) if any(pkg and pkg in p for p in self.java_files) else ("external", None)
        return "n/a", None

    # ------------------------------------------------------------------ edges
    def build(self) -> tuple[list[dict], dict]:
        edges, stats = [], defaultdict(lambda: defaultdict(int))
        for f in self.files.values():
            lang = f["language"]
            imported_from: dict[str, str] = {}  # name -> file it was imported from
            for spec in f["imports"]:
                status, target = self.resolve_import(f, spec)
                stats[lang][f"imports_{status}"] += 1
                if status == "resolved" and target != f["path"]:
                    edges.append({"src": f["path"], "dst": target, "kind": "imports", "symbol": spec["module"],
                                  "confidence": "resolved", "line": spec.get("line", 0)})
                    for n in spec["names"]:
                        imported_from[n] = target
                elif status in ("external", "unresolved"):
                    for n in spec["names"]:
                        imported_from.setdefault(n, "")  # imported from outside: don't name-match it
            local = {d["name"] for d in f["defs"]}
            seen = set()
            for r in f["refs"]:
                name = r["name"]
                if name in local or (name, r["line"]) in seen:
                    continue
                seen.add((name, r["line"]))
                # Link only within one language family, and never from production code into a test file
                # (spot check on ERPNext: Python calls to `on`/`submit` were linking to JS test mocks).
                family, src_is_test = FAMILY.get(lang), is_test(f["path"])
                targets = [d for d in self.defs_by_name.get(name, []) if d[0] != f["path"]
                           and FAMILY.get(self.files[d[0]]["language"]) == family
                           and (src_is_test or not is_test(d[0]))]
                if name in imported_from:
                    src_file = imported_from[name]
                    if src_file and any(t[0] == src_file for t in targets):
                        stats[lang]["refs_resolved"] += 1
                        edges.append({"src": f["path"], "dst": src_file, "kind": r["kind"], "symbol": name,
                                      "confidence": "resolved", "line": r["line"]})
                    else:
                        stats[lang]["refs_external"] += 1
                    continue
                if not targets:
                    stats[lang]["refs_external"] += 1  # builtin or third-party
                    continue
                if name in GENERIC_NAMES or len(targets) > MAX_AMBIGUOUS:
                    stats[lang]["refs_too_generic"] += 1
                    continue
                conf = "name_match" if len(targets) == 1 else "ambiguous"
                stats[lang][f"refs_{conf}"] += 1
                for t in targets:
                    edges.append({"src": f["path"], "dst": t[0], "kind": r["kind"], "symbol": name,
                                  "confidence": conf, "line": r["line"]})
        return edges, {k: dict(v) for k, v in stats.items()}
