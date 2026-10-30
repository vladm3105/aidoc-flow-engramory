#!/usr/bin/env python3
"""canon/sdd/sdd_index.py — index an aidoc-flow SDD corpus for the unified-test-suite canon.

Vendored as .unified-test-suite/sdd/sdd_index.py. Reads only; never writes SDD documents.
Needs python3 + PyYAML (the runtime every SDD project already has for sdd_doc_lint).

  python3 sdd_index.py --root docs/sdd [--id-pattern RE] [--profile .aidoc/profile.yaml]

Prints one JSON object on stdout:
  {version, root, activeLayers, ears[], bdd[], tdd[], mappings[], iplans[], evals[], errors[]}
Exit 0 on success (document-level problems are listed in errors[]), 2 on usage errors,
3 when PyYAML is missing.

Carriers (upstream U4): Markdown documents declare elements as headings `## <ID> <title>` (levels
2-4) and cite upstream IDs with `@ears:` / `@bdd:` tags until the next heading; YAML documents
(one or more `---` documents, merged) declare elements as mappings with an `id` key. Superseded
versions (`archive/`) and EVAL cycle reports (`reports/`) are not part of the live corpus.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

try:
    import yaml
except ImportError:  # pragma: no cover - exercised by the canon when PyYAML is absent
    print(json.dumps({"version": 1, "fatal": "PyYAML is not installed (python3 -m pip install pyyaml)"}))
    sys.exit(3)

DEFAULT_ID = r"[A-Z]+\.\d{2,}\.\d{2,}\.[a-f0-9]{4,8}"
SKIP_DIRS = {"archive", "reports", ".git", "node_modules", "__pycache__"}
STEP = re.compile(r"^(Given|When|Then|And|But)\b")
JOURNEY_ORACLES = {"ui", "url", "network", "console", "timing", "storage", "a11y"}
EARS_PATTERNS = {"event", "state", "unwanted", "optional", "ubiquitous"}


def ears_pattern(statement: str) -> str:
    """EARS pattern from the leading keyword (Mavin et al.): WHILE state, WHEN event, IF unwanted, WHERE optional."""
    head = statement.lstrip("*_ ").upper()
    for kw, pat in (("WHILE", "state"), ("WHEN", "event"), ("IF", "unwanted"), ("WHERE", "optional")):
        if head.startswith(kw + " "):
            return pat
    return "ubiquitous"


TAG = re.compile(r"@(ears|bdd|tdd|spec|adr|prd|brd|iplan)\s*:\s*([^\s|'\"`,\]]+)", re.I)


class Index:
    def __init__(self, root: str, id_pattern: str):
        self.root = root
        self.id_full = re.compile(rf"^(?:{id_pattern})$")
        self.id_any = re.compile(rf"\b(?:{id_pattern})\b")
        self.heading = re.compile(rf"^(#{{2,4}})\s+\**({id_pattern})\**\s*[—:-]?\s*(.*)$")
        self.out = {"ears": [], "bdd": [], "tdd": [], "mappings": [], "iplans": [], "evals": [], "errors": []}
        self.seen: dict[str, str] = {}

    # ------------------------------------------------------------------ helpers
    def rel(self, path: str) -> str:
        return os.path.relpath(path, os.getcwd()).replace(os.sep, "/")

    def ids_in(self, value, layer: str | None = None) -> list[str]:
        """Element IDs cited in a string / list value (tags or bare IDs), optionally by layer prefix."""
        found: list[str] = []
        items = value if isinstance(value, list) else [value]
        for item in items:
            if isinstance(item, dict):
                item = " ".join(str(v) for v in item.values())
            if item is None:
                continue
            for m in self.id_any.finditer(str(item)):
                if layer is None or m.group(0).startswith(layer + "."):
                    found.append(m.group(0))
        return list(dict.fromkeys(found))

    def add(self, kind: str, rec: dict):
        prev = self.seen.get(rec["id"])
        if prev and kind in ("ears", "bdd", "tdd"):
            self.out["errors"].append({"file": rec["file"], "message": f"duplicate element id {rec['id']} (also in {prev})"})
            return
        self.seen[rec["id"]] = rec["file"]
        self.out[kind].append(rec)

    # ------------------------------------------------------------------ Markdown carrier
    def markdown(self, path: str):
        rel = self.rel(path)
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
        current = None
        for n, line in enumerate(lines, 1):
            m = self.heading.match(line)
            if m:
                ident, title = m.group(2), m.group(3).strip()
                layer = ident.split(".")[0]
                current = None
                if layer in ("EARS", "BDD"):
                    rec = {"id": ident, "title": title, "file": rel, "line": n}
                    if layer == "BDD":
                        rec.update(kind="scenario" if re.match(r"(?i)scenario\b", title) else ("feature" if ident.split(".")[2] == "02" else "scenario"), ears=[])
                        self.add("bdd", rec)
                    else:
                        self.add("ears", rec)
                    current = rec
                continue
            if line.startswith("#"):
                current = None
                continue
            if current is not None:
                text = line.strip()
                if "ears" in current and STEP.match(text):
                    current.setdefault("steps", []).append(text)
                elif "ears" not in current and text and not text.startswith(("-", "@", "|")) and "statement" not in current:
                    current["statement"] = text
                    current["pattern"] = ears_pattern(text)
            if current is not None and "ears" in current:
                for t in TAG.finditer(line):
                    if t.group(1).lower() == "ears" and self.id_full.match(t.group(2)) and t.group(2) not in current["ears"]:
                        current["ears"].append(t.group(2))

    # ------------------------------------------------------------------ YAML carrier
    def yaml_doc(self, path: str):
        rel = self.rel(path)
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        try:
            docs = [d for d in yaml.safe_load_all(text) if isinstance(d, dict)]
        except yaml.YAMLError as err:
            self.out["errors"].append({"file": rel, "message": f"YAML parse error: {str(err).splitlines()[0]}"})
            return
        data: dict = {}
        for d in docs:
            data.update(d)
        name = os.path.basename(path).upper()
        dc = data.get("document_control") if isinstance(data.get("document_control"), dict) else {}
        meta = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
        doc_id = str(data.get("doc_id") or data.get("id") or meta.get("artifact_id") or dc.get("iplan_id") or dc.get("eval_id") or "")
        kind = doc_id.split("-")[0] if doc_id else name.split("-")[0]

        if kind == "TDD" or "test_cases" in data or "test_mapping" in data:
            self.tdd(data, rel, doc_id)
        elif kind == "IPLAN" or "file_manifest" in data:
            self.iplan(data, rel, dc, doc_id)
        elif kind == "EVAL" or "test_design" in data:
            self.eval(data, rel, dc, doc_id)
        else:
            self.walk(data, rel, doc_id)

    def walk(self, node, rel: str, doc_id: str, depth: int = 0, seen=None):
        """Generic element walk for YAML EARS / BDD carriers (any mapping with a grammar `id`).
        Aliases can make the graph cyclic; nodes are visited once and depth is capped."""
        seen = set() if seen is None else seen
        if depth > 64 or id(node) in seen:
            return
        if isinstance(node, (list, dict)):
            seen.add(id(node))
        if isinstance(node, list):
            for x in node:
                self.walk(x, rel, doc_id, depth + 1, seen)
            return
        if not isinstance(node, dict):
            return
        ident = node.get("id")
        if isinstance(ident, str) and self.id_full.match(ident):
            layer = ident.split(".")[0]
            title = str(node.get("title") or node.get("name") or node.get("statement") or "")
            if layer == "EARS":
                stmt = self.text(node.get("statement") or node.get("requirement") or node.get("text"))
                rec = {"id": ident, "title": title, "file": rel, "line": 0}
                if stmt:
                    rec.update(statement=stmt, pattern=self.text(node.get("pattern")) or ears_pattern(stmt))
                self.add("ears", rec)
            elif layer == "BDD":
                ears = self.ids_in([node.get(k) for k in ("ears", "ears_ref", "ears_refs", "traceability", "trace") if node.get(k)], "EARS")
                kind = "scenario" if ident.split(".")[2] != "02" else "feature"
                steps = [f"{k.capitalize()} {v}" for k in ("given", "when", "then") for v in ([node[k]] if isinstance(node.get(k), str) else node.get(k) or [])]
                if isinstance(node.get("steps"), list):
                    steps += [str(x) for x in node["steps"]]
                self.add("bdd", {"id": ident, "title": title, "file": rel, "line": 0, "kind": kind, "ears": ears, "steps": steps})
        for k, v in node.items():
            if k != "id":
                self.walk(v, rel, doc_id, depth + 1, seen)

    @staticmethod
    def tdd_cases(tc):
        """Cases in either layout: `test_cases.cases` / a `test_cases` list (golden chain), or the
        template's `test_cases.{unit,integration,e2e,security}_tests.cases` groups."""
        if isinstance(tc, list):
            return [(c, None) for c in tc]
        if not isinstance(tc, dict):
            return []
        out = [(c, None) for c in tc.get("cases") or [] if isinstance(tc.get("cases"), list)]
        for key, group in tc.items():
            if key.endswith("_tests") and isinstance(group, dict) and isinstance(group.get("cases"), list):
                out += [(c, key[: -len("_tests")]) for c in group["cases"]]
        return out

    @staticmethod
    def text(v):
        """A scalar SDD field as text; lists/maps are not valid test_file/test_function values."""
        return None if v is None or isinstance(v, (list, dict)) else str(v)

    def tdd(self, data: dict, rel: str, doc_id: str):
        for c, group_type in self.tdd_cases(data.get("test_cases")):
            if not isinstance(c, dict) or not isinstance(c.get("id"), str):
                continue
            for k in ("test_file", "test_function"):
                if isinstance(c.get(k), (list, dict)):
                    self.out["errors"].append({"file": rel, "message": f"{c['id']}: {k} must be a single value, got {type(c.get(k)).__name__}"})
            if not self.id_full.match(c["id"]):
                self.out["errors"].append({"file": rel, "message": f"malformed TDD case id {c['id']!r}"})
                continue
            self.add("tdd", {
                "id": c["id"], "doc": doc_id, "file": rel, "title": str(c.get("name") or c.get("target") or c.get("contract") or ""),
                "type": self.text(c.get("type")) or group_type, "testFile": self.text(c.get("test_file")), "testFunction": self.text(c.get("test_function")),
                "bdd": self.ids_in([c.get("bdd_ref"), c.get("bdd_refs"), c.get("bdd_scenario")], "BDD"),
                "ears": self.ids_in([c.get("ears_ref"), c.get("ears_refs")], "EARS"),
                "status": self.text(c.get("status")), "priority": self.text(c.get("priority")),
            })
        tm = data.get("test_mapping")
        scen = tm.get("scenarios") if isinstance(tm, dict) else None
        for s in scen or []:
            if not isinstance(s, dict):
                continue
            bdd = self.ids_in(s.get("bdd_scenario"), "BDD")
            for t in s.get("tests") or []:
                if isinstance(t, dict) and (t.get("file") or t.get("test_file")):
                    self.out["mappings"].append({
                        "doc": doc_id, "file": rel, "bdd": bdd, "type": self.text(t.get("type")),
                        "testFile": self.text(t.get("file") or t.get("test_file")), "testFunction": self.text(t.get("function") or t.get("test_function")),
                    })

    def iplan(self, data: dict, rel: str, dc: dict, doc_id: str):
        fm = data.get("file_manifest")
        files = fm.get("files") if isinstance(fm, dict) else fm
        manifest = [{"path": self.text(f.get("path")), "tdd": self.ids_in(f.get("tdd_ref"), "TDD")} for f in files or [] if isinstance(f, dict)]
        self.out["iplans"].append({"id": self.text(dc.get("iplan_id")) or doc_id, "file": rel, "status": self.text(dc.get("status")), "sourceChg": self.text(dc.get("source_chg")), "manifest": manifest})

    def eval(self, data: dict, rel: str, dc: dict, doc_id: str):
        td = data.get("test_design")
        entries = (td.get("entries") or td.get("test_cases")) if isinstance(td, dict) else td
        if isinstance(entries, dict):  # template map form (upstream U3)
            entries = [dict(v, id=k) if isinstance(v, dict) else {"id": k} for k, v in entries.items()]
        cases = [{"id": self.text(e.get("id")), "sourceId": self.text(e.get("source_id")), "testFile": self.text(e.get("test_file"))} for e in entries or [] if isinstance(e, dict)]
        scope = data.get("evaluation_scope") if isinstance(data.get("evaluation_scope"), dict) else {}
        self.out["evals"].append({"id": doc_id or self.text(dc.get("eval_id")), "file": rel, "iplan": self.text(scope.get("iplan_id")), "cases": cases})

    # ------------------------------------------------------------------ walk
    def run(self):
        for base, dirs, files in os.walk(self.root):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
            for f in sorted(files):
                path = os.path.join(base, f)
                try:
                    if f.endswith(".md"):
                        self.markdown(path)
                    elif f.endswith((".yaml", ".yml")):
                        self.yaml_doc(path)
                except Exception as err:  # one bad document never loses the whole index
                    self.out["errors"].append({"file": self.rel(path), "message": f"{type(err).__name__}: {err}"})
        return self.out


def journey_cards(root: str, id_full, rel):
    """Parse journey cards (<root>/**/*.journey.yaml). Returns (cards, errors). Schema: uts.journey/1."""
    import hashlib
    cards, errors = [], []
    if not root or not os.path.isdir(root):
        return cards, errors
    for base, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for f in sorted(files):
            if not f.endswith(".journey.yaml"):
                continue
            path = os.path.join(base, f)
            r = rel(path)
            def err(msg):
                errors.append({"file": r, "message": msg})
            try:
                raw = open(path, "rb").read()
                d = yaml.safe_load(raw.decode("utf-8"))
            except Exception as e:  # noqa: BLE001 — any unreadable card is a card error
                err(f"unreadable card: {type(e).__name__}: {str(e).splitlines()[0] if str(e) else ''}")
                continue
            if not isinstance(d, dict):
                err("card is not a mapping")
                continue
            jid = d.get("journey")
            draft = bool(d.get("draft"))
            card = {"file": r, "sha256": hashlib.sha256(raw).hexdigest(), "journey": jid if isinstance(jid, str) else None,
                    "title": str(d.get("title") or ""), "tdd": d.get("tdd") if isinstance(d.get("tdd"), str) else None,
                    "draft": draft, "ears": [], "expect": [], "setup": [], "act": [],
                    "environments": [str(x) for x in d.get("environments") or []] if isinstance(d.get("environments"), list) else [],
                    "external": bool(d.get("external")), "valid": True}
            problems = []
            if d.get("schema") != "uts.journey/1":
                problems.append("schema must be uts.journey/1")
            if not (isinstance(jid, str) and id_full.match(jid) and jid.startswith("BDD.")):
                problems.append("journey must be a BDD element ID")
            elif f != f"{jid}.journey.yaml":
                problems.append(f"file name must be {jid}.journey.yaml")
            if d.get("tdd") is not None and not (isinstance(d.get("tdd"), str) and id_full.match(d["tdd"]) and d["tdd"].startswith("TDD.")):
                problems.append("tdd must be a TDD element ID or null")
            for key in ("setup", "act", "expect", "ears"):
                v = d.get(key, [])
                if not isinstance(v, list):
                    problems.append(f"{key} must be a list")
                    d[key] = []
            expect_ids = []
            for i, e in enumerate(d.get("expect") or []):
                if not isinstance(e, dict) or not e.get("id") or not e.get("assert"):
                    problems.append(f"expect[{i}] needs id and assert")
                    continue
                if e.get("oracle") not in JOURNEY_ORACLES:
                    problems.append(f"expect[{i}] oracle must be one of {sorted(JOURNEY_ORACLES)}")
                expect_ids.append(str(e["id"]))
            if len(set(expect_ids)) != len(expect_ids):
                problems.append("expect ids must be unique")
            for i, a in enumerate(d.get("act") or []):
                if not isinstance(a, dict) or not a.get("id") or not a.get("intent"):
                    problems.append(f"act[{i}] needs id and intent")
            for i, st in enumerate(d.get("setup") or []):
                if not isinstance(st, dict) or not st.get("id"):
                    problems.append(f"setup[{i}] needs an id")
            for i, e in enumerate(d.get("ears") or []):
                if not isinstance(e, dict) or not (isinstance(e.get("id"), str) and id_full.match(e["id"]) and e["id"].startswith("EARS.")):
                    problems.append(f"ears[{i}] needs an EARS element id")
                    continue
                if e.get("pattern") not in EARS_PATTERNS:
                    problems.append(f"ears[{i}] pattern must be one of {sorted(EARS_PATTERNS)}")
                if not draft and not e.get("covered_by") and str(e.get("then")) not in expect_ids:
                    problems.append(f"ears[{i}] then must name an expect id of this card (or set covered_by)")
                w = e.get("within") if isinstance(e.get("within"), dict) else None
                card["ears"].append({"id": e["id"], "pattern": e.get("pattern"), "then": e.get("then"), "coveredBy": e.get("covered_by"),
                                     "within": {"value": w.get("value"), "unit": w.get("unit"), "enforce": bool(w.get("enforce"))} if w else None})
            if not draft:
                if not d.get("ears"):
                    problems.append("ears needs at least one entry")
                if not d.get("act"):
                    problems.append("act needs at least one intent")
                if not expect_ids:
                    problems.append("expect needs at least one oracle")
            card["expect"] = expect_ids
            card["setup"] = [{"id": st.get("id"), "reach": st.get("reach"), "identity": st.get("identity")} for st in d.get("setup") or [] if isinstance(st, dict)]
            card["act"] = [a.get("id") for a in d.get("act") or [] if isinstance(a, dict)]
            for p in problems:
                err(p)
            card["valid"] = not problems
            cards.append(card)
    return cards, errors


def active_layers(profile: str | None):
    if not profile or not os.path.isfile(profile):
        return None
    try:
        with open(profile, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except (OSError, yaml.YAMLError):
        return None
    raw = data.get("active_layers") if isinstance(data, dict) else None
    return [str(x).upper() for x in raw] if isinstance(raw, list) else None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="sdd_index.py")
    ap.add_argument("--root", required=True)
    ap.add_argument("--id-pattern", default=DEFAULT_ID)
    ap.add_argument("--profile", default=None)
    ap.add_argument("--journeys", default=None, help="journey card directory (uts.journey/1)")
    a = ap.parse_args(argv)
    if not os.path.isdir(a.root):
        print(json.dumps({"version": 1, "fatal": f"SDD docs root {a.root!r} does not exist"}))
        return 2
    try:
        re.compile(a.id_pattern)
    except re.error as err:
        print(json.dumps({"version": 1, "fatal": f"invalid --id-pattern: {err}"}))
        return 2
    ix = Index(a.root, a.id_pattern)
    out = ix.run()
    out["journeys"], out["journeyErrors"] = journey_cards(a.journeys, ix.id_full, ix.rel)
    print(json.dumps({"version": 1, "root": a.root.rstrip("/"), "activeLayers": active_layers(a.profile), **out}, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
