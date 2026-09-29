#!/usr/bin/env python3
"""Lint spreadsheet-brain playbooks against dev/CONTRACTS.md section 3.

Usage:
    python dev/lint_playbooks.py [playbooks_dir] [--simulate] [--quiet]

Checks every playbooks/<id>.json for: valid JSON, required keys (full or
detection-level), role references in predicates, slots, insights, graph and
sensitivity, the predicate and recipe vocabularies, template slots, question
shape (header length, 2 to 4 options, recommend rules, why, unblocks), fact
statements and gotcha lines that read as declarative sentences, no em dashes,
no internal jargon in user-facing text, and unique ids.

--simulate also runs a small detection simulator over synthetic header sets
(written for weight sanity only; they are NOT verified export fingerprints)
and fails if a sample does not land on its own playbook with a confident score.

Exit code 0 when there are no errors, 1 otherwise. Stdlib only.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DIR = ROOT / "skills" / "spreadsheet-brain" / "playbooks"

# ---------------------------------------------------------------- vocabulary

FULL_REQUIRED = [
    "id", "version", "name", "looks_like", "summary", "full", "roles", "detect",
    "goals", "questions", "insights", "outputs", "gotchas", "graph", "freshness",
    "sensitivity",
]
LITE_REQUIRED = [
    "id", "name", "looks_like", "summary", "full", "roles", "detect", "goals",
    "insights", "graph",
]
LITE_OPTIONAL = ["version", "questions", "outputs", "gotchas", "freshness", "sensitivity"]

ROLE_TYPES = {"number", "text", "date", "id", "bool", "any"}
ROLE_KINDS = {"entity", "identifier", "attribute", "metric", "dimension", "temporal", "text", "flag"}
# level: the column holds an amount at a date (a count on hand, a balance), read on one date, never summed across dates
ROLE_KEYS = {"label", "headers", "type", "kind", "entity", "additive", "unit", "level"}

QUESTION_KINDS = {"grain", "definition", "unit", "rule", "exclusion", "coverage", "mapping", "history"}
FACT_KINDS = {"grain", "definition", "unit", "rule", "exclusion", "gotcha", "goal", "history", "coverage"}
FACT_CLASSES = {"data", "commercial"}
NO_RECOMMEND_KINDS = {"coverage"}  # human-owned by definition: never carries a default
QUESTION_KEYS = {
    "id", "header", "kind", "priority", "ask_if", "prompt", "multi", "options",
    "recommend", "recommend_if", "recommend_basis", "why", "unblocks", "fact", "stale_after", "settled_by",
    "backed_by",
}
# grain facts code states with their counts (analyze._grain) that answer a question listing them in settled_by
# (snapshot_stock: a snapshot panel with a stock column read on its latest date)
SETTLED_BY = {"snapshot", "snapshot_stock", "balanced", "wide"}
# what a model's formulas and titles settle for the whole file (analyze._sign_fact, _scale_fact: an insight's
# 'settles'), on a question of any kind
MODEL_SETTLED_BY = {"model_sign", "model_scale"}
# backed_by: {insight: recipe prefix, recommend: option id}; the insight's counted statement is the evidence and
# its option the one-tap recommendation (interview._insight_evidence)
BACKED_BY_KEYS = {"insight", "recommend"}
BACKED_BY_INSIGHTS = {"formula:actuals_boundary"}
GOTCHA_KEYS = {"if", "say", "evidence"}
# gotcha evidence (brain.Composer._evidence): '<check>:<role>' over this file's rows, or
# 'no_insight:<recipe prefix>' (no counted check settled it the other way)
EVIDENCE_CHECKS = {"times_near_midnight", "full_discount"}
FACT_KEYS = {"kind", "class", "depends", "statement"}
OPTION_KEYS = {"id", "label", "desc"}

OUTPUT_KEYS = {"id", "title", "pitch", "requires", "needs_answer", "goals", "value", "job", "external"}
OUTPUT_JOBS = {"analysis", "audit", "explain", "blueprint", "join", "context"}
GUIDE_TITLE = "Guide for the next owner"
BLUEPRINT_TITLE = "App blueprint"

GRAPH_KEYS = {"sentence", "entities", "record_cap", "size_by", "relations", "mode", "records"}
GRAPH_MODES = {"entities", "formula_flow", "schema"}

ROLE_PREDICATES = {"has_role", "no_role", "mixed_values", "has_negatives", "has_blanks", "coded", "tree_names",
                   "repeats"}
BARE_PREDICATES = {"multi_dates", "multi_files", "multi_sheets", "has_formulas", "has_derived_tab",
                   "has_totals_rows"}
GUARANTEE_PREDICATES = {"has_role", "mixed_values", "has_negatives", "has_blanks", "coded", "tree_names",
                        "repeats"}

# recipe name -> arg kinds ("number": role type number, "date": role type date, "any": any role)
RECIPES = {
    "pareto": ["number", "any"],
    "top_share": ["number", "any"],
    "product_check": ["number", "number", "number"],
    "negatives": ["number"],
    "mixed": ["any"],
    "date_range": ["date"],
    "duplicates": ["any"],
    "blank_rate": ["any"],
    "count_distinct": ["any"],
    "spread": ["number", "any", "any"],
}

ROLE_SLOTS = {"role", "values", "count", "sum", "rows"}
BARE_SLOTS = {"rows"}          # {rows} alone: the main table's rows; {rows:<role>}: rows with a value there
ANSWER_SLOTS = {"answer_labels", "answer_text"}

IMPERATIVES = {
    "ignore", "run", "send", "click", "open", "execute", "delete", "email",
    "visit", "use", "always", "never", "make", "check",
}

# Internal words that must never reach the user (design doc, section 0 table).
JARGON = [
    r"\bgrain\b", r"\bforeign key", r"\barchetype", r"\bplaybook", r"\bfingerprint",
    r"\bcardinality\b", r"\bschema\b", r"\bjoin(s|ed|ing)?\b", r"\bpredicate",
    r"\bnon[- ]additive\b",
]

ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")
STALE_RE = re.compile(r"^(\+\d+d|on-change|\d{4}-\d{2}-\d{2})$")
SLOT_RE = re.compile(r"\{([^{}]*)\}")
EM_DASH = "\u2014"  # escaped so this file has none either

LIMITS = {
    "header": 12,
    "option_label": 40,
    "option_desc": 110,
    "goal_label": 40,
    "goal_desc": 90,
    "relation_label": 30,
    "statement": 500,
    "prompt": 240,
    "why": 160,
    "role_label": 80,
    "name": 60,
    "looks_like": 60,
}


def normalize_header(text: str) -> str:
    """Contract 3 header normalization: lowercase, # kept, other punctuation to spaces, collapsed."""
    text = text.lower()
    text = re.sub(r"[^a-z0-9#\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


class Linter:
    def __init__(self, directory: Path):
        self.dir = directory
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.books: dict[str, dict] = {}
        self.paths: dict[str, Path] = {}

    # ------------------------------------------------------------ plumbing
    def err(self, where: str, msg: str) -> None:
        self.errors.append(f"{where}: {msg}")

    def warn(self, where: str, msg: str) -> None:
        self.warnings.append(f"{where}: {msg}")

    def load(self) -> None:
        files = sorted(p for p in self.dir.glob("*.json"))
        if not files:
            self.err(str(self.dir), "no playbook files found")
        for path in files:
            raw = path.read_text(encoding="utf-8")
            if EM_DASH in raw:
                line = raw[: raw.index(EM_DASH)].count("\n") + 1
                self.err(path.name, f"em dash found (line {line})")
            try:
                data = json.loads(raw)
            except json.JSONDecodeError as exc:
                self.err(path.name, f"invalid JSON: {exc}")
                continue
            if not isinstance(data, dict):
                self.err(path.name, "top level must be an object")
                continue
            pid = data.get("id")
            if pid != path.stem:
                self.err(path.name, f"id {pid!r} must match the file name {path.stem!r}")
            if pid in self.books:
                self.err(path.name, f"duplicate playbook id {pid!r}")
            self.books[str(pid)] = data
            self.paths[str(pid)] = path

    # ------------------------------------------------------------ text checks
    def check_text(self, where: str, text, *, max_len: int | None = None,
                   allow_answer: bool = False, guaranteed: set[str] | None = None,
                   roles: dict | None = None, allow_role_slots: bool = True,
                   declarative: bool = False, jargon: bool = True) -> None:
        if not isinstance(text, str) or not text.strip():
            self.err(where, "must be a non-empty string")
            return
        if max_len and len(text) > max_len:
            self.err(where, f"too long ({len(text)} > {max_len})")
        if text != text.strip():
            self.err(where, "leading or trailing whitespace")
        if "  " in text:
            self.warn(where, "double space")
        if text.count("{") != text.count("}"):
            self.err(where, "unbalanced braces")
        for slot in SLOT_RE.findall(text):
            self.check_slot(where, slot, allow_answer=allow_answer, guaranteed=guaranteed,
                            roles=roles, allow_role_slots=allow_role_slots)
        if jargon:
            low = SLOT_RE.sub(" ", text).lower()
            for pat in JARGON:
                if re.search(pat, low):
                    self.err(where, f"internal jargon ({pat.strip(chr(92) + 'b')}) in user-facing text")
        if declarative:
            stripped = text.lstrip()
            if not stripped.startswith("{"):
                m = re.match(r"[A-Za-z']+", stripped)
                if m and m.group(0).lower() in IMPERATIVES:
                    self.err(where, f"starts with an imperative verb ({m.group(0)!r}); write a declarative sentence")
            if stripped.endswith("?"):
                self.err(where, "a statement must not be a question")

    def check_slot(self, where: str, slot: str, *, allow_answer: bool, guaranteed, roles,
                   allow_role_slots: bool) -> None:
        if slot in BARE_SLOTS:
            return
        if slot in ANSWER_SLOTS:
            if not allow_answer:
                self.err(where, f"slot {{{slot}}} is only allowed in fact statements")
            return
        if ":" in slot:
            name, arg = slot.split(":", 1)
            if name in ROLE_SLOTS:
                if not allow_role_slots:
                    self.err(where, f"role slot {{{slot}}} not allowed here")
                    return
                if roles is not None and arg not in roles:
                    self.err(where, f"slot {{{slot}}} names unknown role {arg!r}")
                    return
                if name == "sum" and roles is not None and roles[arg].get("type") != "number":
                    self.err(where, f"slot {{{slot}}} needs a number role")
                if guaranteed is not None and arg not in guaranteed:
                    self.err(where, f"slot {{{slot}}} is not guaranteed by the condition (add has_role:{arg})")
                return
        self.err(where, f"unknown template slot {{{slot}}}")

    # ------------------------------------------------------------ predicates
    def check_predicates(self, where: str, preds, book: dict, *, allow_answers: bool = True) -> set[str]:
        """Validate a predicate list; return the set of roles it guarantees are matched."""
        guaranteed: set[str] = set()
        if isinstance(preds, str):
            preds = [preds]
        if not isinstance(preds, list):
            self.err(where, "predicates must be a string or a list of strings")
            return guaranteed
        roles = book.get("roles", {}) or {}
        questions = {q.get("id"): q for q in book.get("questions", []) or [] if isinstance(q, dict)}
        goals = {g.get("id") for g in book.get("goals", []) or [] if isinstance(g, dict)}
        for p in preds:
            if not isinstance(p, str):
                self.err(where, f"predicate {p!r} must be a string")
                continue
            name, _, arg = p.partition(":")
            if name in BARE_PREDICATES:
                if arg:
                    self.err(where, f"predicate {p!r} takes no argument")
            elif name in ROLE_PREDICATES:
                if arg not in roles:
                    self.err(where, f"predicate {p!r} names unknown role {arg!r}")
                    continue
                if name == "has_negatives" and roles[arg].get("type") != "number":
                    self.err(where, f"predicate {p!r} needs a number role")
                if name in GUARANTEE_PREDICATES:
                    guaranteed.add(arg)
            elif name == "rows_gt":
                if not arg.isdigit():
                    self.err(where, f"predicate {p!r} needs an integer")
            elif name == "goal":
                if arg not in goals:
                    self.err(where, f"predicate {p!r} names unknown goal {arg!r}")
            elif name in ("answered", "not_answered"):
                if not allow_answers:
                    self.err(where, f"predicate {p!r} not allowed here")
                qid, _, opt = arg.partition("=")
                if qid not in questions:
                    self.err(where, f"predicate {p!r} names unknown question {qid!r}")
                    continue
                if name == "answered":
                    opts = {o.get("id") for o in questions[qid].get("options", []) if isinstance(o, dict)}
                    if not opt:
                        self.err(where, f"predicate {p!r} needs =<option>")
                    elif opt not in opts:
                        self.err(where, f"predicate {p!r} names unknown option {opt!r}")
                elif opt:
                    self.err(where, f"predicate {p!r} takes a question id only")
            else:
                self.err(where, f"unknown predicate {p!r}")
        return guaranteed

    # ------------------------------------------------------------ sections
    def lint_book(self, pid: str, b: dict) -> None:
        full = b.get("full")
        w = f"{pid}"
        if not isinstance(full, bool):
            self.err(w, "'full' must be true or false")
            return
        required = FULL_REQUIRED if full else LITE_REQUIRED
        allowed = set(FULL_REQUIRED) | (set() if full else set(LITE_OPTIONAL))
        for k in required:
            if k not in b:
                self.err(w, f"missing required key {k!r}")
        for k in b:
            if k not in allowed:
                self.err(w, f"unknown top-level key {k!r}")
        if "version" in b and not re.match(r"^\d+\.\d+\.\d+$", str(b["version"])):
            self.err(w, "version must look like 0.1.0")
        for k in ("name", "looks_like"):
            if k in b:
                self.check_text(f"{w}.{k}", b[k], max_len=LIMITS[k], allow_role_slots=False)
        if "summary" in b:
            self.check_text(f"{w}.summary", b["summary"], allow_role_slots=False, declarative=True)

        roles = self.lint_roles(pid, b, full)
        self.lint_detect(pid, b, roles)
        goal_ids = self.lint_goals(pid, b, full)
        question_ids = self.lint_questions(pid, b, full, roles)
        output_ids = self.lint_outputs(pid, b, full, roles, goal_ids, question_ids)
        self.cross_check_questions(pid, b, output_ids)
        self.lint_insights(pid, b, roles)
        self.lint_gotchas(pid, b, roles)
        self.lint_graph(pid, b, roles)
        self.lint_freshness(pid, b, roles)
        self.lint_sensitivity(pid, b, roles)

    def lint_roles(self, pid: str, b: dict, full: bool) -> dict:
        roles = b.get("roles")
        w = f"{pid}.roles"
        if not isinstance(roles, dict) or not roles:
            self.err(w, "must be a non-empty object")
            return {}
        if full and not 10 <= len(roles) <= 20:
            self.err(w, f"full playbooks need 10 to 20 roles (has {len(roles)})")
        seen_headers: dict[str, str] = {}
        for rid, r in roles.items():
            rw = f"{w}.{rid}"
            if not ID_RE.match(rid):
                self.err(rw, "role id must be lowercase snake_case")
            if not isinstance(r, dict):
                self.err(rw, "must be an object")
                continue
            for k in r:
                if k not in ROLE_KEYS:
                    self.err(rw, f"unknown key {k!r}")
            self.check_text(f"{rw}.label", r.get("label"), max_len=LIMITS["role_label"], allow_role_slots=False, jargon=False)
            if r.get("type") not in ROLE_TYPES:
                self.err(rw, f"type {r.get('type')!r} not in {sorted(ROLE_TYPES)}")
            if r.get("kind") not in ROLE_KINDS:
                self.err(rw, f"kind {r.get('kind')!r} not in {sorted(ROLE_KINDS)}")
            if r.get("kind") == "entity" and not r.get("entity"):
                self.err(rw, "kind entity needs an 'entity' name")
            if "additive" in r and not isinstance(r["additive"], bool):
                self.err(rw, "additive must be a boolean")
            if "level" in r and (r["level"] is not True or r.get("kind") != "metric"):
                self.err(rw, "level is true, on a metric role only")
            if r.get("kind") == "metric" and r.get("type") != "number":
                self.err(rw, "metric roles must be type number")
            headers = r.get("headers")
            if not isinstance(headers, list) or not headers:
                self.err(rw, "headers must be a non-empty list")
                continue
            if full and len(headers) < 3:
                self.warn(rw, "short header lexicon (fewer than 3 entries)")
            local = set()
            for h in headers:
                if not isinstance(h, str) or not h:
                    self.err(rw, f"bad header entry {h!r}")
                    continue
                if normalize_header(h) != h:
                    self.err(rw, f"header {h!r} is not normalized (expected {normalize_header(h)!r})")
                if h in local:
                    self.err(rw, f"header {h!r} listed twice")
                local.add(h)
                if h in seen_headers and seen_headers[h] != rid:
                    self.err(rw, f"header {h!r} also listed under role {seen_headers[h]!r}")
                seen_headers[h] = rid
        return roles

    def lint_detect(self, pid: str, b: dict, roles: dict) -> None:
        d = b.get("detect")
        w = f"{pid}.detect"
        if not isinstance(d, dict):
            self.err(w, "must be an object")
            return
        for k in d:
            if k not in {"required_any", "weights", "negatives", "confident", "candidate"}:
                self.err(w, f"unknown key {k!r}")
        ra = d.get("required_any", [])
        if not isinstance(ra, list):
            self.err(w, "required_any must be a list of role lists")
        else:
            for grp in ra:
                if not isinstance(grp, list) or not grp:
                    self.err(w, f"required_any group {grp!r} must be a non-empty list")
                    continue
                for r in grp:
                    if r not in roles:
                        self.err(w, f"required_any names unknown role {r!r}")
        weights = d.get("weights")
        if not isinstance(weights, dict) or not weights:
            self.err(w, "weights must be a non-empty object")
        else:
            for r, v in weights.items():
                if r not in roles:
                    self.err(w, f"weight for unknown role {r!r}")
                if not isinstance(v, (int, float)) or v <= 0:
                    self.err(w, f"weight for {r!r} must be a positive number")
        negs = d.get("negatives", [])
        if not isinstance(negs, list):
            self.err(w, "negatives must be a list")
        else:
            for n in negs:
                if n in roles:
                    self.err(w, f"negative {n!r} is one of this playbook's own roles")
                homes = [o for o, ob in self.books.items() if o != pid and n in (ob.get("roles") or {})]
                if not homes:
                    self.err(w, f"negative {n!r} is not a role in any other playbook")
        conf, cand = d.get("confident"), d.get("candidate")
        for k, v in (("confident", conf), ("candidate", cand)):
            if not isinstance(v, (int, float)) or not 0 < v <= 1:
                self.err(w, f"{k} must be a number in (0, 1]")
        if isinstance(conf, (int, float)) and isinstance(cand, (int, float)) and cand > conf:
            self.err(w, "candidate must not exceed confident")

    def lint_goals(self, pid: str, b: dict, full: bool) -> set[str]:
        goals = b.get("goals")
        w = f"{pid}.goals"
        ids: set[str] = set()
        if not isinstance(goals, list):
            self.err(w, "must be a list")
            return ids
        lo, hi = (3, 4) if full else (2, 4)
        if not lo <= len(goals) <= hi:
            self.err(w, f"needs {lo} to {hi} goals (has {len(goals)})")
        for g in goals:
            if not isinstance(g, dict):
                self.err(w, "each goal must be an object")
                continue
            gid = g.get("id")
            gw = f"{w}.{gid}"
            if not isinstance(gid, str) or not ID_RE.match(gid):
                self.err(gw, "bad goal id")
            if gid in ids:
                self.err(gw, "duplicate goal id")
            ids.add(gid)
            for k in g:
                if k not in {"id", "label", "desc"}:
                    self.err(gw, f"unknown key {k!r}")
            self.check_text(f"{gw}.label", g.get("label"), max_len=LIMITS["goal_label"], allow_role_slots=False)
            self.check_text(f"{gw}.desc", g.get("desc"), max_len=LIMITS["goal_desc"], allow_role_slots=False)
        return ids

    def lint_questions(self, pid: str, b: dict, full: bool, roles: dict) -> set[str]:
        qs = b.get("questions", [])
        w = f"{pid}.questions"
        ids: set[str] = set()
        if not isinstance(qs, list):
            self.err(w, "must be a list")
            return ids
        lo, hi = (6, 9) if full else (0, 3)
        if not lo <= len(qs) <= hi:
            self.err(w, f"needs {lo} to {hi} questions (has {len(qs)})")
        gated = 0
        commercial = set((b.get("sensitivity") or {}).get("commercial_roles", []) or [])
        for q in qs:
            if not isinstance(q, dict):
                self.err(w, "each question must be an object")
                continue
            qid = q.get("id")
            qw = f"{w}.{qid}"
            if not isinstance(qid, str) or not ID_RE.match(qid):
                self.err(qw, "bad question id")
            if qid in ids:
                self.err(qw, "duplicate question id")
            ids.add(qid)
            for k in q:
                if k not in QUESTION_KEYS:
                    self.err(qw, f"unknown key {k!r}")
            for k in ("header", "kind", "priority", "ask_if", "prompt", "options", "why", "unblocks", "fact"):
                if k not in q:
                    self.err(qw, f"missing {k!r}")
            header = q.get("header")
            if not isinstance(header, str) or not header:
                self.err(qw, "header must be a non-empty string")
            elif len(header) > LIMITS["header"]:
                self.err(qw, f"header {header!r} is {len(header)} chars (max {LIMITS['header']})")
            if q.get("kind") not in QUESTION_KINDS:
                self.err(qw, f"kind {q.get('kind')!r} not in {sorted(QUESTION_KINDS)}")
            if q.get("priority") not in (1, 2, 3):
                self.err(qw, "priority must be 1, 2 or 3")
            sb = q.get("settled_by")
            if "settled_by" in q and not (isinstance(sb, list) and sb and set(sb) <= MODEL_SETTLED_BY) \
                    and (q.get("kind") != "grain" or not isinstance(sb, list) or not set(sb) <= SETTLED_BY):
                self.err(qw, f"settled_by is a list of {sorted(SETTLED_BY)} on a grain question, or of "
                             f"{sorted(MODEL_SETTLED_BY)} on any question")
            by = q.get("backed_by")
            if "backed_by" in q and (not isinstance(by, dict) or set(by) != BACKED_BY_KEYS
                                     or by.get("insight") not in BACKED_BY_INSIGHTS
                                     or by.get("recommend") not in {o.get("id") for o in q.get("options") or []
                                                                    if isinstance(o, dict)}):
                self.err(qw, f"backed_by is {{insight: one of {sorted(BACKED_BY_INSIGHTS)}, recommend: an option id}}")
            guaranteed = self.check_predicates(f"{qw}.ask_if", q.get("ask_if", []), b)
            if any(isinstance(p, str) and (p.startswith("goal:") or p.startswith("answered:"))
                   for p in (q.get("ask_if") or [])):
                gated += 1
            self.check_text(f"{qw}.prompt", q.get("prompt"), max_len=LIMITS["prompt"],
                            guaranteed=guaranteed, roles=roles)
            if isinstance(q.get("prompt"), str) and "?" not in q["prompt"]:
                self.err(f"{qw}.prompt", "a question prompt must ask a question")
            if "multi" in q and not isinstance(q["multi"], bool):
                self.err(qw, "multi must be a boolean")
            if q.get("multi") and isinstance(q.get("prompt"), str) and "Pick all that apply" not in q["prompt"]:
                self.err(f"{qw}.prompt", "multi-select prompts end with 'Pick all that apply.'")
            opts = q.get("options")
            opt_ids: set[str] = set()
            if not isinstance(opts, list) or not 2 <= len(opts) <= 4:
                self.err(qw, "needs 2 to 4 options (the engine adds Not sure and Other)")
                opts = opts if isinstance(opts, list) else []
            for o in opts:
                if not isinstance(o, dict):
                    self.err(qw, "each option must be an object")
                    continue
                oid = o.get("id")
                ow = f"{qw}.options.{oid}"
                for k in o:
                    if k not in OPTION_KEYS:
                        self.err(ow, f"unknown key {k!r}")
                if not isinstance(oid, str) or not ID_RE.match(oid):
                    self.err(ow, "bad option id")
                if oid in opt_ids:
                    self.err(ow, "duplicate option id")
                opt_ids.add(oid)
                self.check_text(f"{ow}.label", o.get("label"), max_len=LIMITS["option_label"],
                                guaranteed=guaranteed, roles=roles)
                if isinstance(o.get("label"), str) and o["label"].strip().lower() in {"not sure", "other", "unsure", "other..."}:
                    self.err(ow, "the engine adds Not sure and Other; do not list them")
                if "desc" in o:
                    self.check_text(f"{ow}.desc", o.get("desc"), max_len=LIMITS["option_desc"],
                                    guaranteed=guaranteed, roles=roles)
            rec, basis = q.get("recommend"), q.get("recommend_basis")
            if rec is not None:
                if not isinstance(rec, str) or rec not in opt_ids:
                    self.err(qw, f"recommend {rec!r} is not one of the option ids")
                if not basis:
                    self.err(qw, "recommend needs a recommend_basis grounded in evidence or an industry norm")
                if q.get("kind") in NO_RECOMMEND_KINDS:
                    self.err(qw, f"{q.get('kind')} questions are human-owned and never carry a recommendation")
            # a recommendation shows only when its data predicate holds; candidates() fills the
            # basis with interview.fill, so it may quote roles the conditions guarantee
            rec_guaranteed = set(guaranteed)
            if "recommend_if" in q:
                if rec is None:
                    self.err(qw, "recommend_if without recommend")
                rec_guaranteed |= self.check_predicates(f"{qw}.recommend_if", q["recommend_if"], b)
            if basis is not None:
                if rec is None:
                    self.err(qw, "recommend_basis without recommend")
                self.check_text(f"{qw}.recommend_basis", basis, max_len=200, guaranteed=rec_guaranteed,
                                roles=roles, declarative=True)
            self.check_text(f"{qw}.why", q.get("why"), max_len=LIMITS["why"], guaranteed=guaranteed,
                            roles=roles, declarative=True)
            unb = q.get("unblocks")
            if not isinstance(unb, list) or not unb:
                self.err(qw, "unblocks must be a non-empty list of output ids")
            fact = q.get("fact")
            if not isinstance(fact, dict):
                self.err(qw, "fact must be an object")
            else:
                fw = f"{qw}.fact"
                for k in fact:
                    if k not in FACT_KEYS:
                        self.err(fw, f"unknown key {k!r}")
                if fact.get("kind") not in FACT_KINDS:
                    self.err(fw, f"kind {fact.get('kind')!r} not in {sorted(FACT_KINDS)}")
                if fact.get("class") not in FACT_CLASSES:
                    self.err(fw, f"class {fact.get('class')!r} not in {sorted(FACT_CLASSES)}")
                deps = fact.get("depends", [])
                if not isinstance(deps, list):
                    self.err(fw, "depends must be a list of role ids")
                    deps = []
                for d in deps:
                    if d not in roles:
                        self.err(fw, f"depends names unknown role {d!r}")
                if set(deps) & commercial and fact.get("class") != "commercial":
                    self.warn(fw, "depends on a commercial role but class is data (fine for units, not for terms)")
                self.check_text(f"{fw}.statement", fact.get("statement"), max_len=LIMITS["statement"],
                                allow_answer=True, guaranteed=guaranteed, roles=roles, declarative=True)
                if isinstance(fact.get("statement"), str) and "{answer_" not in fact["statement"]:
                    self.err(fw, "statement must carry the answer ({answer_labels} or {answer_text})")
            sa = q.get("stale_after")
            if sa is not None and (not isinstance(sa, str) or not STALE_RE.match(sa)):
                self.err(qw, f"stale_after {sa!r} must be +Nd, on-change or an ISO date")
        if full and gated < 2:
            self.err(w, f"full playbooks need 2 or more round-two questions gated by goal: or answered: (has {gated})")
        return ids

    def lint_outputs(self, pid: str, b: dict, full: bool, roles: dict, goal_ids: set[str],
                     question_ids: set[str]) -> set[str]:
        outs = b.get("outputs", [])
        w = f"{pid}.outputs"
        ids: set[str] = set()
        if not isinstance(outs, list):
            self.err(w, "must be a list")
            return ids
        if full and not 4 <= len(outs) <= 6:
            self.err(w, f"full playbooks need 4 to 6 outputs (has {len(outs)})")
        if not full and b.get("questions") and not outs:
            self.err(w, "questions need outputs to unblock")
        served_goals: set[str] = set()
        for o in outs:
            if not isinstance(o, dict):
                self.err(w, "each output must be an object")
                continue
            oid = o.get("id")
            ow = f"{w}.{oid}"
            if not isinstance(oid, str) or not ID_RE.match(oid):
                self.err(ow, "bad output id")
            if oid in ids:
                self.err(ow, "duplicate output id")
            ids.add(oid)
            for k in o:
                if k not in OUTPUT_KEYS:
                    self.err(ow, f"unknown key {k!r}")
            for k in ("title", "pitch", "requires", "goals", "value", "job"):
                if k not in o:
                    self.err(ow, f"missing {k!r}")
            guaranteed = self.check_predicates(f"{ow}.requires", o.get("requires", []), b, allow_answers=False)
            self.check_text(f"{ow}.title", o.get("title"), max_len=40, allow_role_slots=False)
            self.check_text(f"{ow}.pitch", o.get("pitch"), max_len=300, guaranteed=guaranteed, roles=roles)
            if isinstance(o.get("pitch"), str) and isinstance(o.get("title"), str) \
                    and not o["pitch"].startswith(o["title"] + ":"):
                self.err(f"{ow}.pitch", "pitch starts with the title and a colon")
            for qid in o.get("needs_answer", []) or []:
                if qid not in question_ids:
                    self.err(ow, f"needs_answer names unknown question {qid!r}")
            gl = o.get("goals")
            if not isinstance(gl, list) or not gl:
                self.err(ow, "goals must be a non-empty list")
            else:
                for g in gl:
                    if g not in goal_ids:
                        self.err(ow, f"goals names unknown goal {g!r}")
                    served_goals.add(g)
            if o.get("value") not in (1, 2, 3):
                self.err(ow, "value must be 1, 2 or 3")
            if o.get("job") not in OUTPUT_JOBS:
                self.err(ow, f"job {o.get('job')!r} not in {sorted(OUTPUT_JOBS)}")
            if "external" in o and not isinstance(o["external"], bool):
                self.err(ow, "external must be a boolean")
        if full and outs:
            last = outs[-1] if isinstance(outs[-1], dict) else {}
            if last.get("title") != BLUEPRINT_TITLE or last.get("job") != "blueprint":
                self.err(w, f"the last output must be '{BLUEPRINT_TITLE}' with job blueprint")
            if not any(isinstance(o, dict) and o.get("title") == GUIDE_TITLE and o.get("job") == "explain" for o in outs):
                self.err(w, f"needs a '{GUIDE_TITLE}' output with job explain")
            jobs = {o.get("job") for o in outs if isinstance(o, dict)}
            if not jobs & {"audit", "analysis"}:
                self.err(w, "needs at least one analysis or audit output")
        if outs:
            for g in goal_ids - served_goals:
                self.err(w, f"goal {g!r} is served by no output")
        return ids

    def cross_check_questions(self, pid: str, b: dict, output_ids: set[str]) -> None:
        for q in b.get("questions", []) or []:
            if not isinstance(q, dict):
                continue
            for oid in q.get("unblocks", []) or []:
                if oid not in output_ids:
                    self.err(f"{pid}.questions.{q.get('id')}", f"unblocks names unknown output {oid!r}")

    def lint_insights(self, pid: str, b: dict, roles: dict) -> None:
        ins = b.get("insights")
        w = f"{pid}.insights"
        if not isinstance(ins, list) or not ins:
            self.err(w, "must be a non-empty list")
            return
        seen = set()
        for s in ins:
            if not isinstance(s, str):
                self.err(w, f"{s!r} must be a string")
                continue
            if s in seen:
                self.err(w, f"duplicate insight {s!r}")
            seen.add(s)
            name, *args = s.split(":")
            if name not in RECIPES:
                self.err(w, f"unknown recipe {name!r} in {s!r}")
                continue
            spec = RECIPES[name]
            if len(args) != len(spec):
                self.err(w, f"{s!r} needs {len(spec)} role argument(s)")
                continue
            for a, kind in zip(args, spec):
                if a not in roles:
                    self.err(w, f"{s!r} names unknown role {a!r}")
                    continue
                t = roles[a].get("type")
                if kind == "number" and t != "number":
                    self.err(w, f"{s!r}: role {a!r} must be type number")
                if kind == "date" and t != "date":
                    self.err(w, f"{s!r}: role {a!r} must be type date")

    def lint_gotchas(self, pid: str, b: dict, roles: dict) -> None:
        gs = b.get("gotchas", [])
        w = f"{pid}.gotchas"
        if not isinstance(gs, list):
            self.err(w, "must be a list")
            return
        if b.get("full") and len(gs) < 4:
            self.err(w, "full playbooks need 4 or more gotchas")
        for i, g in enumerate(gs):
            gw = f"{w}[{i}]"
            if not isinstance(g, dict):
                self.err(gw, "must be an object")
                continue
            for k in g:
                if k not in GOTCHA_KEYS:
                    self.err(gw, f"unknown key {k!r}")
            guaranteed = self.check_predicates(f"{gw}.if", g.get("if", []), b)
            if "evidence" in g:
                self.check_evidence(f"{gw}.evidence", g["evidence"], roles)
            self.check_text(f"{gw}.say", g.get("say"), max_len=300, guaranteed=guaranteed, roles=roles,
                            declarative=True)

    def check_evidence(self, where: str, preds, roles: dict) -> None:
        """A gotcha's evidence list: an unknown check never passes, so it is an error here."""
        if not isinstance(preds, list) or not preds:
            self.err(where, "evidence must be a non-empty list of strings")
            return
        for p in preds:
            name, _, arg = str(p).partition(":")
            if not isinstance(p, str) or not arg:
                self.err(where, f"evidence {p!r} must read '<check>:<role>' or 'no_insight:<recipe prefix>'")
            elif name == "no_insight":
                continue
            elif name not in EVIDENCE_CHECKS:
                self.err(where, f"unknown evidence check {name!r} (one of {sorted(EVIDENCE_CHECKS)} or no_insight)")
            elif arg not in roles:
                self.err(where, f"evidence {p!r} names unknown role {arg!r}")

    def lint_graph(self, pid: str, b: dict, roles: dict) -> None:
        g = b.get("graph")
        w = f"{pid}.graph"
        if not isinstance(g, dict):
            self.err(w, "must be an object")
            return
        for k in g:
            if k not in GRAPH_KEYS:
                self.err(w, f"unknown key {k!r}")
        self.check_text(f"{w}.sentence", g.get("sentence"), max_len=200, allow_role_slots=False,
                        declarative=True)
        if g.get("mode") not in GRAPH_MODES:
            self.err(w, f"mode {g.get('mode')!r} not in {sorted(GRAPH_MODES)}")
        ents = g.get("entities", [])
        if not isinstance(ents, list):
            self.err(w, "entities must be a list of role ids")
            ents = []
        if g.get("mode") == "entities" and not ents:
            self.err(w, "entities mode needs entity roles")
        for e in ents:
            if e not in roles:
                self.err(w, f"entity {e!r} is not a role")
        rc = g.get("record_cap", {})
        if not isinstance(rc, dict):
            self.err(w, "record_cap must be an object")
            rc = {}
        for r, n in rc.items():
            if r not in ents:
                self.err(w, f"record_cap names {r!r}, which is not in entities")
            if not isinstance(n, int) or n <= 0:
                self.err(w, f"record_cap for {r!r} must be a positive integer")
        for e in ents:
            if e not in rc:
                self.err(w, f"entity {e!r} has no record_cap")
        sb = g.get("size_by")
        if sb is not None:
            if sb not in roles:
                self.err(w, f"size_by {sb!r} is not a role")
            elif roles[sb].get("type") != "number":
                self.err(w, f"size_by {sb!r} must be a number role")
        recs = g.get("records", [])
        if not isinstance(recs, list):
            self.err(w, "records must be a list of role ids")
            recs = []
        for r in recs:
            if r not in ents:
                self.err(w, f"records names {r!r}, which is not in entities")
        rels = g.get("relations", [])
        if not isinstance(rels, list):
            self.err(w, "relations must be a list")
            rels = []
        for i, rel in enumerate(rels):
            rw = f"{w}.relations[{i}]"
            if not isinstance(rel, dict):
                self.err(rw, "must be an object")
                continue
            for k in rel:
                if k not in {"from", "to", "label"}:
                    self.err(rw, f"unknown key {k!r}")
            for end in ("from", "to"):
                if rel.get(end) not in roles:
                    self.err(rw, f"{end} {rel.get(end)!r} is not a role")
            self.check_text(f"{rw}.label", rel.get("label"), max_len=LIMITS["relation_label"],
                            allow_role_slots=False)
            if isinstance(rel.get("label"), str) and rel["label"] != rel["label"].lower():
                self.err(rw, "relation labels are lowercase")

    def lint_freshness(self, pid: str, b: dict, roles: dict) -> None:
        if "freshness" not in b:
            return
        f = b["freshness"]
        w = f"{pid}.freshness"
        if not isinstance(f, dict):
            self.err(w, "must be an object")
            return
        for k in f:
            if k not in {"default_stale_after", "rules"}:
                self.err(w, f"unknown key {k!r}")
        d = f.get("default_stale_after")
        if not isinstance(d, str) or not STALE_RE.match(d):
            self.err(w, "default_stale_after must be +Nd, on-change or an ISO date")
        for i, r in enumerate(f.get("rules", []) or []):
            rw = f"{w}.rules[{i}]"
            if not isinstance(r, dict):
                self.err(rw, "must be an object")
                continue
            for k in r:
                if k not in {"role", "stale_after", "basis"}:
                    self.err(rw, f"unknown key {k!r}")
            if r.get("role") not in roles:
                self.err(rw, f"role {r.get('role')!r} is not a role")
            if not isinstance(r.get("stale_after"), str) or not STALE_RE.match(r["stale_after"]):
                self.err(rw, "stale_after must be +Nd, on-change or an ISO date")
            self.check_text(f"{rw}.basis", r.get("basis"), max_len=240, allow_role_slots=False,
                            declarative=True)

    def lint_sensitivity(self, pid: str, b: dict, roles: dict) -> None:
        if "sensitivity" not in b:
            return
        s = b["sensitivity"]
        w = f"{pid}.sensitivity"
        if not isinstance(s, dict):
            self.err(w, "must be an object")
            return
        for k in s:
            if k not in {"commercial_roles", "private_roles"}:
                self.err(w, f"unknown key {k!r}")
        for k in ("commercial_roles", "private_roles"):
            v = s.get(k, [])
            if not isinstance(v, list):
                self.err(w, f"{k} must be a list")
                continue
            for r in v:
                if r not in roles:
                    self.err(w, f"{k} names unknown role {r!r}")

    # ------------------------------------------------------------ run
    def run(self) -> None:
        self.load()
        for pid, b in self.books.items():
            self.lint_book(pid, b)


# -------------------------------------------------------------- simulator
# Synthetic header sets, written from general knowledge of common exports to
# sanity-check detection weights. They are NOT verified export fingerprints.
SAMPLES = {
    "procurement": [
        ["Invoice Date", "Invoice #", "Vendor", "Ship-To", "Item #", "Mfr #", "Brand", "Description",
         "Pack", "UOM", "Qty Shipped", "Unit Price", "Ext Price", "Category"],
        ["SUPC", "Desc", "Pack", "Size", "Brand", "Case Qty", "Case $", "Extended Price", "Invoice Number",
         "Invoice Date", "Customer Name"],
    ],
    "financial_model": [
        ["Line Item", "Jan-26", "Feb-26", "Mar-26", "Apr-26", "FY2026", "Notes"],
        ["Assumption", "Value", "Unit", "Source", "Last Updated", "Scenario"],
    ],
    "crm_contacts": [
        ["Record ID", "First Name", "Last Name", "Email", "Phone Number", "Company Name", "Job Title",
         "Lifecycle Stage", "Lead Status", "Contact owner", "Create Date", "Last Activity Date"],
        ["Name", "Email", "Company", "Title", "City", "State", "Zip", "Source", "Tags"],
    ],
    "sales_transactions": [
        ["Name", "Email", "Financial Status", "Paid at", "Fulfillment Status", "Subtotal", "Shipping",
         "Taxes", "Total", "Discount Code", "Discount Amount", "Created at", "Lineitem quantity",
         "Lineitem name", "Lineitem price", "Lineitem sku", "Refunded Amount", "Source"],
        ["Date", "Time", "Category", "Item", "Qty", "Gross Sales", "Discounts", "Net Sales", "Tax",
         "Transaction ID", "Location", "Channel"],
    ],
    "ledger": [
        ["Date", "Transaction Type", "Num", "Name", "Memo/Description", "Account", "Split", "Amount", "Balance"],
        ["Date", "Account Code", "Account", "Description", "Reference", "Debit", "Credit", "Running Balance"],
    ],
    "ar_ap": [
        ["Date", "Transaction Type", "Num", "Customer", "Due Date", "Past Due", "Amount", "Open Balance"],
        ["Customer", "Current", "1 - 30", "31 - 60", "61 - 90", "91 and over", "Total"],
    ],
    "sales_pipeline": [
        ["Deal Name", "Deal Stage", "Amount", "Close Date", "Deal owner", "Create Date", "Pipeline",
         "Associated Company"],
        ["Opportunity Name", "Account Name", "Stage", "Amount", "Probability (%)", "Close Date",
         "Forecast Category", "Opportunity Owner", "Next Step"],
    ],
    "inventory": [
        ["Item #", "Description", "Storage Area", "Count Unit", "Par", "On Hand", "Unit Cost", "Total Value",
         "Count Date"],
    ],
    "payroll_hr": [
        ["Employee ID", "Employee Name", "Department", "Job Title", "Hire Date", "Pay Type", "Pay Rate",
         "Regular Hours", "OT Hours", "Gross Pay", "Check Date"],
    ],
    "project_tracker": [
        ["ID", "Task Name", "Owner", "Status", "Start Date", "Due Date", "% Complete", "Priority", "Phase",
         "Predecessors"],
    ],
    "marketing_performance": [
        ["Reporting starts", "Reporting ends", "Campaign name", "Ad set name", "Ad name", "Results", "Reach",
         "Impressions", "Frequency", "Amount spent (USD)", "Cost per result", "Link clicks", "CTR (link click-through rate)"],
        ["Campaign", "Ad group", "Impr.", "Clicks", "CTR", "Avg. CPC", "Cost", "Conversions", "Conv. value"],
    ],
    "survey_responses": [
        ["Timestamp", "Email Address", "How likely are you to recommend us to a friend?", "What could we do better?",
         "Which location did you visit?"],
        ["ResponseId", "StartDate", "EndDate", "Progress", "Duration (in seconds)", "Finished", "RecordedDate",
         "IPAddress", "Q1", "Q2"],
    ],
    "bookings_events": [
        ["Confirmation #", "Guest Name", "Email", "Arrival Date", "Departure Date", "Nights", "Room Type",
         "Rate", "Status", "Booked On", "Source"],
        ["Date", "Time", "Party Size", "Guest", "Status", "Table", "Booked Via", "Notes"],
    ],
    "operations_log": [
        ["WO #", "Opened", "Closed", "Asset", "Location", "Problem Code", "Priority", "Technician",
         "Downtime (hrs)", "Status"],
        ["Date", "Time", "Equipment", "Temp", "Initials", "Corrective Action"],
    ],
}


def match_roles(headers: list[str], roles: dict) -> dict[str, str]:
    """Contract 3 matching: whole-word lexicon phrases, longer wins, one column takes one role."""
    out: dict[str, str] = {}
    for h in headers:
        norm = normalize_header(h)
        best = None
        for rid, r in roles.items():
            for entry in r.get("headers", []):
                pat = r"(?<![a-z0-9#])" + re.escape(entry) + r"(?![a-z0-9#])"
                if norm == entry or re.search(pat, norm):
                    if best is None or len(entry) > best[1]:
                        best = (rid, len(entry))
        if best:
            out[h] = best[0]
    return out


def score(book: dict, headers: list[str], catalog: dict) -> float:
    roles = book["roles"]
    d = book["detect"]
    matched = match_roles(headers, roles)
    hit = set(matched.values())
    for grp in d.get("required_any", []):
        if not hit & set(grp):
            return 0.0
    weights = d["weights"]
    total = sum(weights.values())
    s = sum(v for r, v in weights.items() if r in hit) / total if total else 0.0
    unmatched = [h for h in headers if h not in matched]
    for neg in d.get("negatives", []):
        homes = [ob["roles"][neg] for oid, ob in catalog.items() if oid != book["id"] and neg in ob.get("roles", {})]
        for home in homes:
            if match_roles(unmatched, {neg: home}):
                s -= 0.3
                break
    return max(0.0, round(s, 3))


def simulate(linter: Linter) -> int:
    books = {pid: b for pid, b in linter.books.items() if pid != "generic"}
    fails = 0
    print("\nDetection simulator (synthetic header sets, not verified fingerprints)")
    for pid, samples in SAMPLES.items():
        if pid not in books:
            print(f"  {pid}: no playbook")
            fails += 1
            continue
        for i, headers in enumerate(samples):
            scores = sorted(((score(b, headers, linter.books), oid) for oid, b in books.items()), reverse=True)
            top_score, top = scores[0]
            own = next(s for s, o in scores if o == pid)
            conf = books[pid]["detect"]["confident"]
            runner = next((f"{o} {s:.2f}" for s, o in scores if o != pid), "")
            ok = top == pid and own >= conf and (len(scores) < 2 or own - max(s for s, o in scores if o != pid) >= 0.10)
            flag = "ok  " if ok else "FAIL"
            if not ok:
                fails += 1
            print(f"  {flag} {pid}[{i}]: own {own:.2f} (confident {conf}), top {top} {top_score:.2f}, next {runner}")
    return fails


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("--")]
    directory = Path(args[0]) if args else DEFAULT_DIR
    linter = Linter(directory)
    linter.run()
    quiet = "--quiet" in argv
    if not quiet:
        for wmsg in linter.warnings:
            print(f"warning: {wmsg}")
    for e in linter.errors:
        print(f"error: {e}")
    sim_fails = 0
    if "--simulate" in argv and not linter.errors:
        sim_fails = simulate(linter)
    n = len(linter.books)
    qn = sum(len(b.get("questions", []) or []) for b in linter.books.values())
    print(f"\n{n} playbooks, {qn} questions, {len(linter.errors)} errors, "
          f"{len(linter.warnings)} warnings" + (f", {sim_fails} simulator failures" if "--simulate" in argv else ""))
    return 1 if linter.errors or sim_fails else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
