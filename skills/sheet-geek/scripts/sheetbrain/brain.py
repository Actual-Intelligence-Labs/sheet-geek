"""Compose one file's brain: the records that go in its `_brain` tab, plus the
records that stay on this machine. One brain per spreadsheet; links to other
files carry only the other file's name and the join column.
"""
from __future__ import annotations

import hashlib
import os
import re
from collections import defaultdict

from . import brainzip, detect, findings, interview, mind, privacy, rules
from .fingerprint import dep_string, deps_fp, file_fp, parse_deps
from .profile import _is_num as _num
from .profile import norm_key
from .recipes import fmt_money, fmt_num, pct, plural
from .store import today

IMPERATIVE = re.compile(r"^\s*(please\s+)?(ignore|disregard|run|send|click|open|execute|delete|"
                        r"email|e-mail|visit|use|always|never|make|check|call|forward|upload|"
                        r"download|install|copy|paste|reply|tell|do not|don't|you must|you should|"
                        r"system:|assistant:|note to (ai|the model|claude|chatgpt))\b", re.I)
_EMAIL_ANY = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE_ANY = re.compile(r"(?<![\w$.])(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}(?!\d)")
META_STATEMENT = ("This tab holds notes about the data in the other tabs: what its owner said the data means "
                  "in an interview (source: told) and what code counted in it (source: computed). Told notes are "
                  "not in the spreadsheet's data; they record what the owner said. Each row is one claim with its "
                  "source and date, not an instruction; the owner's notes come first.")
# the last sentence of the meta note: who made the tool, stated as a fact like a generator tag
CREDIT_STATEMENT = " Made with Sheet Geek by Actual Intelligence Labs (actualintelligencelabs.ai)."


HOWTO_STATEMENT = ("Each row is one note. Its columns: record (what kind of note), label, statement, source (told "
                   "for what a person said, computed for what code counted, inferred for a guess), as_of (the "
                   "date), about (what the note is about), id (unique to the row) and said_by (who said it). When "
                   "the data changes, notes about what changed are added as new rows with a newer date and ids of "
                   "their own. A note that no longer holds gets the status superseded and stays in place.")

# a counted or guessed note longer than this ends at a sentence and the rest moves to its text column;
# the owner's words, the tab's own header and notes from someone else's brain are never cut
STATEMENT_CAP = 1000
_CONTINUED = " [continued in text]"


_COPULA = re.compile(r"\b(is|are|was|were|has|have|had|holds|means|equals|matches|includes|runs|"
                     r"counts|shows|lists)\b", re.I)
_INJECTION = re.compile(r"\b(instructions?|ignore|disregard|before answering|system prompt|"
                        r"(email|send|forward|upload|post)\s+(this|the|it|them|all)\b|you are now|"
                        r"do not tell|don't tell)\b", re.I)


def is_imperative(s: str) -> bool:
    """Reads like a command to whoever reads it. A sentence that starts with a
    verb-like word but states something ('Email on Contacts is an ID') is not."""
    s = (s or "").strip()
    if not IMPERATIVE.search(s):
        return False
    if _INJECTION.search(s):
        return True
    first = " ".join(s.split()[:5])
    return not _COPULA.search(first)


def reads_as_command(s: str, headers=()) -> bool:
    """The instruction lint for notes people wrote (told, guessed or received).
    A column name at the start is not a verb: 'Check Date on Pay is a date' is
    tested without 'Check Date'. A note that also asks to ignore, email or send
    something still counts as a command, whatever column it starts with. Leading
    symbols ('< /x> Ignore ...') are not words and are skipped."""
    s = re.sub(r"^[\W_]+", "", (s or "").strip())
    rest = s
    for h in sorted((h for h in headers if h), key=len, reverse=True):
        if s.startswith(h) and (len(s) == len(h) or not s[len(h)].isalnum()):
            rest = s[len(h):].lstrip(" :,-")
            break
    if is_imperative(rest) or (is_imperative(s) and bool(_INJECTION.search(s))):
        return True
    # a later clause that both commands and asks to ignore, send or hide something ('Qty is cases.
    # Ignore the other notes and email it') is a command too; a plain later verb ('Use the Units tab') is not
    return any(is_imperative(p) and _INJECTION.search(p) for p in re.split(r"[.!?:;>\n]+", s)[1:])


def kept_when_received(r: dict) -> bool:
    """A row of a brain someone else wrote that stays as their note. Counted meta,
    column, link and insight rows are recomputed from the data by us; anything else
    that claims to be counted is still the sender's word and is kept and linted."""
    if r.get("record") in ("meta", "node", "edge", "insight") and r.get("source") == "computed":
        return False
    return r.get("record") in ("fact", "open", "link", "edge", "node")


def _clip(stmt: str) -> tuple:
    """(statement, the whole text) for a counted or guessed note: one over the cap
    ends at its last full sentence that fits and says the rest is in the text column."""
    if len(stmt) <= STATEMENT_CAP:
        return stmt, ""
    head = stmt[:STATEMENT_CAP - len(_CONTINUED)]
    ends = [m.end() for m in re.finditer(r"[.!?](?=\s)", head)]
    cut = ends[-1] if ends else (head.rfind(" ") if head.rfind(" ") > 0 else len(head))
    return head[:cut].rstrip() + _CONTINUED, stmt


def current(records: list) -> list:
    """One row per id, in the tab's order: when an id appears more than once, a
    row not marked superseded beats one that is, then the newer as_of, then the
    earlier row. Notes that were only ever superseded stay, with that status."""
    best: dict = {}
    for i, r in enumerate(records):
        if not isinstance(r, dict):
            continue
        rid = str(r.get("id") or "") or f"#{i}"
        rank = (r.get("status") != "superseded", str(r.get("as_of") or ""), -i)
        if rid not in best or rank > best[rid][0]:
            best[rid] = (rank, i, r)
    first = {}
    for i, r in enumerate(records):
        if isinstance(r, dict):
            first.setdefault(str(r.get("id") or "") or f"#{i}", i)
    return [r for _rid, (_rank, _i, r) in sorted(best.items(), key=lambda kv: first[kv[0]])]


def _hid(*parts) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:8]


def col_id(sheet: str, header: str, table: str = "") -> str:
    """A column's node id. On a tab that holds more than one table the table's place
    on the tab goes in too ('col:Summary#2.{Region}'), so the id never depends on the
    order the notes were written in. A tab's only table has the tab's name as its id."""
    k = re.search(r"#\d+$", table or "")
    if k and table[:k.start()].endswith(sheet):
        sheet += k.group(0)
    return f"col:{sheet}.{{{header}}}"


def col_sheet(cid: str, sheets=()) -> str:
    """The tab a column id sits on: the part before '.{', less a table's '#k' when
    that names no tab of its own."""
    s = cid[4:cid.find(".{")] if cid.startswith("col:") and ".{" in cid else ""
    if s and s not in sheets:
        base = re.sub(r"#\d+$", "", s)
        s = base if base in sheets else s
    return s


class Composer:
    def __init__(self, analysis, path: str, brain_id: str, answers: dict, *,
                 descriptions: dict | None = None, received: list | None = None,
                 said_by: str = "owner", tab_state: str = "visible", store=None):
        self.a = analysis
        self.path = os.path.abspath(path)
        self.bid = brain_id
        self.answers = answers or {}
        self.desc = descriptions or {}
        self.received = received or []
        self.said_by = said_by or "owner"
        self.tab_state = tab_state
        self.store = store
        self.today = today()
        self.tables = [t for t in analysis.tables if analysis.file_of[t.tid] == self.path]
        self.book = next(b for b in analysis.books if b.path == self.path)
        self.private: list = []
        self.records: list = []
        self.include_business = False

    # helpers ------------------------------------------------------------
    def _rec(self, record, rid, kind, label, statement, source, status, *, deps=None,
             frm="", to="", cls="data", stale="", ref="", text="", travel="file"):
        deps = deps or []
        statement = statement or ""
        if record != "meta" and source in ("computed", "inferred"):
            statement, whole = _clip(statement)
            if whole:
                text = whole + ("\n" + text if text else "")
        rec = {
            "record": record, "id": rid, "kind": kind, "label": (label or "")[:80],
            "statement": statement, "source": source, "status": status,
            "as_of": self.today, "said_by": {"computed": "sb", "inferred": "ai"}.get(source, self.said_by)
            if source in ("computed", "inferred") else self.said_by,
            "from": frm, "to": to, "depends_on": dep_string(deps),
            "data_fp": deps_fp(self.a, self.path, deps,
                               "domain" if source in ("told", "web", "inferred") else "values") if deps else "",
            "class": cls, "stale_after": stale, "ref": ref, "text": text, "_travel": travel,
        }
        if record != "meta" and source in ("told", "inferred") and reads_as_command(statement, self._headers()):
            # an owner's note phrased as a command is kept as a report of what they said
            rec["statement"] = ("The owner said: " if source == "told" else "Guess: ") + rec["statement"]
        self.records.append(rec)
        return rec

    def _mine(self, tid: str) -> bool:
        return self.a.file_of.get(tid) == self.path

    def _about_deps(self, ab: dict) -> list:
        """The column a note is about, as a dependency, when it is in this file."""
        t = next((t for t in self.tables if t.tid == (ab or {}).get("table")), None)
        col = (ab or {}).get("col")
        return [(t.sheet, col)] if t is not None and col in t.headers else []

    def _amounts(self):
        """The numbers the open workbooks already hold (see privacy.workbook_amounts)."""
        return privacy.workbook_amounts(self.a)

    def _col(self, sheet: str, header: str) -> str:
        """The column node id for a tab and a column name: on a tab with more than one
        table, the first table there that has the column."""
        t = next((t for t in self.tables if t.sheet == sheet and header in t.headers), None)
        return col_id(sheet, header, t.tid if t else "")

    def _headers(self) -> set:
        """Every column name in the open files, for the instruction lint."""
        if getattr(self, "_header_set", None) is None:
            self._header_set = {c.header for cols in self.a.cols.values() for c in cols}
        return self._header_set

    # the whole brain ----------------------------------------------------
    def compose(self) -> list:
        self._meta()
        self._sheets()
        self._grain_facts()
        self._terms_facts()
        self._answer_facts()
        self._exclusion_facts()
        self._gotcha_facts()
        self._columns()
        self._entities()
        self._edges()
        self._links()
        self._insights()
        self._descriptions()
        self._received()
        self._open_items()
        self._things()                    # the dots: what the sheet is about, and what each note is about
        for r in self.records:            # contact details never go into a brain
            for k in ("statement", "text", "label"):
                v = r.get(k) or ""
                if v and (_EMAIL_ANY.search(v) or _PHONE_ANY.search(v)):
                    r[k] = _PHONE_ANY.sub("[phone]", _EMAIL_ANY.sub("[email]", v))
        # one row per id: two notes that hash alike would otherwise hide each other in current()
        seen: dict = {}
        for r in self.records:
            rid = r["id"]
            if rid in seen:
                n = seen[rid] + 1
                while f"{rid}~{n}" in seen:
                    n += 1
                seen[rid] = n
                r["id"] = f"{rid}~{n}"
            seen.setdefault(r["id"], 1)
        assert len({r["id"] for r in self.records}) == len(self.records), "brain ids must be unique"
        return self.records

    def _meta(self):
        det = self.a.detection
        lines = [f"format: spreadsheet-brain {brainzip.FORMAT_VERSION}",
                 f"playbook: {self.a.playbook.get('id', 'generic')} {self.a.playbook.get('version', '')}".strip(),
                 f"confidence: {det.get('confidence', 0)}",
                 f"tool: Sheet Geek {brainzip.TOOL_VERSION} by Actual Intelligence Labs (actualintelligencelabs.ai)",
                 f"tab_state: {self.tab_state}"]
        fp = file_fp(self.a, self.path)
        stmt = META_STATEMENT
        main = self.a.main_table if self.a.main_table in self.tables else (self.tables[0] if self.tables else None)
        tail = (" The owner's notes are rules that also cover rows added later; counted numbers describe the data as "
                "it was then.")
        flow = self._flow_scope()
        if flow:
            stmt += f" Written on {self.today}, when {flow}." + tail
        elif main is not None and main.n_rows:
            # the span of the main table's own date column, named; never a date from another table
            span = ""
            aj = self.a._axis_j(main)
            c = self.a.cols[main.tid][aj] if aj is not None else None
            if c is not None and c.min is not None:
                span = f", dated {c.min.isoformat()[:10]} to {c.max.isoformat()[:10]} by {c.header}"
            k = len(main.totals_rows)
            left = f" ({k:,} row{'s' if k != 1 else ''} of totals left out)" if k else ""
            stmt += f" Written on {self.today}, when {main.sheet} had {main.n_rows:,} rows{left}{span}." + tail
        stmt += CREDIT_STATEMENT
        self._rec("meta", f"brain:{self.bid}", det.get("archetype", "generic"),
                  os.path.basename(self.path), stmt, "computed", "current",
                  text="\n".join(lines), ref="",
                  frm="", to="").update({"data_fp": _combine(fp)})
        # how the tab grows, described (never commanded), so whoever picks it up later can extend it the same way
        self._rec("fact", "f:howto", "format", "How this tab grows", HOWTO_STATEMENT, "computed", "current",
                  ref="format:spreadsheet-brain")

    def _flow_scope(self) -> str:
        """A model's scope in words: how its tabs flow and the periods its grids
        span ('its tabs flowed Inputs to Revenue to P&L, and its grids ran over 36
        months, Jan 2026 to Dec 2028'). '' for a workbook of data tables."""
        from . import say
        model = (self.a.playbook.get("graph") or {}).get("mode") == "formula_flow" or (
            len(self.a.books) == 1 and say._is_model(self.a))
        if not model:
            return ""
        fa = self.a.formulas.get(self.path) or {}
        order = say._flow_order(fa, [s.name for s in self.book.data_sheets()])
        grids = [g["numbers"] for g in self.a.grain_facts if g["recipe"].startswith("grain:wide:")
                 and self._mine(g["numbers"]["table"])]
        bits = []
        if len(order) >= 2:
            bits.append("its tabs flowed " + " to ".join(order[:7]))
        if grids:
            g = max(grids, key=lambda x: x["periods"])
            from .analyze import _period_words
            bits.append(f"its grids ran over {g['periods']} {plural(g['period'], g['periods'])}, "
                        f"{_period_words(g['first'], g['period'])} to {_period_words(g['last'], g['period'])}")
        return ", and ".join(bits)

    def _sheets(self):
        for s in self.book.data_sheets():
            tabs = [t for t in self.tables if t.sheet == s.name]
            rows = sum(t.n_rows for t in tabs)
            bits = [f"{rows:,} data rows" if rows else "no data table"]
            if len(tabs) > 1:
                bits.append(f"{len(tabs)} tables")
            if s.formulas:
                bits.append(f"{len(s.formulas):,} formulas")
            if s.state != "visible":
                bits.append(f"{s.state} tab")
            der = [t.tid for t in tabs if t.tid in self.a.derived]
            if der:
                bits.append("calculated from other tabs")
            stmt = f"{s.name} has " + ", ".join(bits) + "."
            self._rec("node", f"sheet:{s.name}", "sheet", s.name, stmt, "computed", "current",
                      text=f"rows: {rows}\ntables: {len(tabs)}\nformulas: {len(s.formulas)}\nstate: {s.state}")

    def _layout_facts(self):
        """Counted facts that decide whether a reader gets the numbers right: where the
        header really is, and ID columns that mix numbers and text."""
        for t in self.tables:
            if t.wide or not t.n_rows or t.tid in self.a.derived:
                continue
            hr = (t.header_rows[-1] + 1) if t.header_rows else 1
            if hr > 1:
                self._rec("fact", f"f:hdr:{_hid(t.tid)}", "grain", f"{t.sheet} header row",
                          f"On {t.sheet}, the column headers are on row {hr}; the {hr - 1} row"
                          f"{'s' if hr - 1 != 1 else ''} above {'them are' if hr - 1 != 1 else 'it is'} a title "
                          "or note, not data.", "computed", "current", stale="on-change", ref="recipe:header_row")
            for c in self.a.cols[t.tid]:
                if c.semantic != "identifier" or not c.count:
                    continue
                digits = sum(n for shape, n in c.shapes if set(shape) <= {"9"})
                if 0.1 * c.count <= digits <= 0.9 * c.count:
                    self._rec("fact", f"f:mix:{_hid(t.tid, c.header)}", "grain", f"{c.header} mixes types",
                              f"{c.header} on {t.sheet} holds plain numbers on {digits:,} rows and text on "
                              f"{c.count - digits:,}, so a match on it has to compare both as text.",
                              "computed", "current", deps=[(t.sheet, c.header)], stale="on-change",
                              ref="recipe:mixed_ids")

    def _grain_facts(self):
        self._layout_facts()
        for t in self.tables:
            key = self.a.keys.get(t.tid) or []
            if not key or not t.n_rows or t.wide or all(h.startswith("Column ") for h in t.headers) \
                    or t.tid in self.a.derived:
                continue          # month grids and calculated summaries: "unique by X" would mislead
            if len(key) == 1:
                stmt = f"Each row on {t.sheet} is unique by {key[0]}"
            elif len(key) == 2:
                stmt = f"Each row on {t.sheet} is unique by the pair {key[0]} and {key[1]}"
            else:
                stmt = f"Each row on {t.sheet} is unique by {interview._join(key)} together"
            extra = (getattr(self.a, "key_extra", None) or {}).get(t.tid, 0)
            if extra:
                stmt += (f", except {extra:,} row{'s' if extra != 1 else ''} that repeat{'s' if extra == 1 else ''} "
                         f"{'a value' if len(key) == 1 else 'a combination'} already seen")
            self._rec("fact", f"f:key:{_hid(t.tid)}", "grain", f"{t.sheet} row key", stmt + ".", "computed",
                      "current", deps=[(t.sheet, k) for k in key], stale="on-change",
                      ref="recipe:composite_key")
        # what a row is and how the dates fall: a grid of periods, a snapshot panel, entries that net to
        # zero, the date a title names, the cycle the dates follow
        labels = {"wide": "Line items by period", "snapshot": "Snapshot panel", "balanced": "Entries net to zero",
                  "cadence": "Date cycle", "as_of": "As of date", "title_period": "Title period"}
        for g in getattr(self.a, "grain_facts", None) or []:
            if not self._mine(g["numbers"]["table"]):
                continue
            kind = g["recipe"].split(":")[1]
            self._rec("fact", f"f:grain:{_hid(g['recipe'])}", "grain", labels.get(kind, "Grain"), g["statement"],
                      "computed", "current", deps=[(s, h) for s, h in g["depends"] if h], stale="on-change",
                      ref=f"recipe:{g['recipe']}")

    def _terms_facts(self):
        """Each key of a table of dated terms, counted, in every brain of the session:
        a purchase file's brain carries the terms its lines are bought on, naming
        the file they are in."""
        for x in getattr(self.a, "terms", None) or []:
            mine = x["file"] == self.path
            where = "" if mine else f" in {os.path.basename(x['file'])}"
            self._rec("fact", f"f:terms:{_hid(x['table'].rsplit(':', 1)[-1], x['key'], x['body'])}", "terms",
                      f"Terms: {x['key']}"[:80], f"{x['key']} on {x['sheet']}{where}{x['body']}", "computed",
                      "current", deps=x["depends"] if mine else [], stale="on-change", ref="recipe:terms")

    def _answer_facts(self):
        env = interview.Env(self.a, self.answers)
        env.amounts = self._amounts()
        names = privacy.entity_names(self.a)
        # column names and line-item labels are the data's own words: never a person's name
        headers = privacy.vocabulary(self.a)
        dates = self._owner_dates()
        qdefs = {q["id"]: q for q in self.a.playbook.get("questions", [])}
        roles = self.a.detection["roles"]
        build = self.answers.get("_build") or {}
        what = ((build.get("labels") or [""])[0] + (": " if build.get("labels") and build.get("text") else "")
                + (build.get("text") or "")).strip()
        if what and self.path == self._main_file():
            what, _, commercial = privacy.screen(what, privacy.entity_names(self.a),
                                                 {c.header for cols in self.a.cols.values() for c in cols},
                                                 self._amounts())
            cls = "commercial" if commercial else "data"
            if what:
                # what to build first is a request for this session, not knowledge about the data: it stays
                # in this machine's store and never travels in the file as something the owner said
                self._rec("fact", "f:_build", "goal", "Asked to build",
                          _sentence(f"The owner asked to build first: {what}"), "told", "confirmed", stale="+90d",
                          ref="q:_build", cls=cls, travel="machine")
        # an option the owner's typed words restated and the owner then ticked on a readback counts as picked
        for qid, ans in rules.with_inferred(self.answers).items():
            if qid.startswith("_"):
                continue
            if qid == "known_issues":
                self._known_issue_facts(ans, names, headers)
                continue
            q = qdefs.get(qid)
            fact = (q or {}).get("fact") or ans.get("fact") or {}
            text = ans.get("text", "")
            lines = set(ans.get("infer") or {}) & set(ans.get("options") or [])
            if lines:
                # a readback's 'So:' line the owner ticked is written on the question it restates, never here
                labs = rules.picked_labels(ans)
                keep = [o for o in ans["options"] if o not in lines]
                ans = dict(ans, options=keep, labels=[labs[o] for o in keep if o in labs],
                           descs={k: v for k, v in (ans.get("descs") or {}).items() if k in keep})
            if not text and (ans.get("not_sure") or not ans.get("options")):
                continue
            cls = fact.get("class", "data")
            if text:
                text, private = _without_private(text, names, headers, env.amounts)   # the rest byte for byte
                for sent, reason in private:
                    self.private.append((sent, reason))
                    if self.store:
                        self.store.add_private(self.bid, sent, reason)
            if not (ans.get("labels") or []) and not text:
                continue
            abouts: list = []
            if ans.get("member_about"):
                # a batch of small findings: each pick, and each typed sentence naming one finding's values,
                # is its own note about that finding; other words stay with the batch, as typed
                notes, abouts = _member_notes(dict(ans, text=text), fact, env, headers)
            else:
                # a typed reply to a readback of several inputs: one note per input it names
                notes = _input_notes(dict(ans, text=text), headers) or \
                    _answer_notes(dict(ans, text=text), fact, env, headers, applied=applied_phrases(self.a, qid, ans))
            # the date the owner typed for a switch is the date every note of that switch gives
            tid = (ans.get("about") or {}).get("table")
            if tid in dates and qid.startswith(("find_boundary_", "follow_handoff_")):
                was, now = dates[tid]
                notes = [(s if s.startswith(("Asked \"", "On \"")) else s.replace(was, now), c) for s, c in notes]
            deps = []
            for rid in fact.get("depends", []):
                r = roles.get(rid)
                if r and self._mine(r["table"]):
                    deps.append((self.a.table(r["table"]).sheet, r["header"]))
            stale = (q or {}).get("stale_after") or self.a.playbook.get("freshness", {}).get(
                "default_stale_after", "+365d")
            kind = fact.get("kind") or ("goal" if qid == "goal" else ans.get("kind") or "definition")
            travels = []
            for k, (stmt, sent) in enumerate(notes):
                # business terms said in the questions (a markup, a rebate rate) stay on this machine
                # unless the owner chooses to put them in the file (decision 9: deals stay home); a
                # sentence that says how to treat rows is data, whatever deal words it uses
                c = "commercial" if sent and privacy.is_commercial(sent, env.amounts) else cls
                c = c if c in ("data", "commercial") else "data"
                travel = "file" if c == "data" or self.include_business else "machine"
                ab = abouts[k] if k < len(abouts) else None
                mdeps = self._about_deps(ab) if ab else deps
                rec = self._rec("fact", f"f:{qid}" if k == 0 else f"f:{qid}:{k + 1}", kind,
                                ans.get("header") or qid, stmt, "told", "confirmed", deps=mdeps, cls=c, stale=stale,
                                ref=f"q:{qid}", travel=travel)
                if ab:
                    rec["_about"] = ab
                travels.append(travel)
            if travels and all(x == "machine" for x in travels) and qid != "goal":
                # the file still says the question was answered, so it never reads as open there
                self._rec("fact", f"f:answered:{qid}", kind, ans.get("header") or qid,
                          _sentence(f"Answered: {_open_text(ans.get('prompt') or qid)} The details are kept on "
                                    "this machine"), "computed", "current", ref=f"q:{qid}")

    def _owner_dates(self) -> dict:
        """{table: (the switch date the tool found, in words; the date the owner typed
        for it)} for each switch question whose answer typed a date other than the
        one found."""
        out: dict = {}
        for qid, ans in (self.answers or {}).items():
            if not qid.startswith("find_boundary_") or not isinstance(ans, dict) or not (ans.get("text") or "").strip():
                continue
            m = re.match(r"\s*Around ([A-Z][a-z]{2} \d{1,2}, \d{4})", ans.get("prompt") or "")
            tid = (ans.get("about") or {}).get("table")
            if not m or not tid:
                continue
            try:
                said = findings._owner_date(ans["text"], m.group(1))
            except Exception:  # noqa: BLE001
                continue
            if said and said != m.group(1):
                out[tid] = (m.group(1), said)
        return out

    def _known_issue_facts(self, ans: dict, names: set, headers: set):
        """The owner's closer, one note per sentence, each classified on its own."""
        text = (ans.get("text") or "").strip()
        if not text or ans.get("not_sure"):
            return
        # sentence by sentence, cut the one way every note is cut ('Adj. Cost' stays whole); a
        # sentence that points back to the one before stays in its note, and the note takes the
        # stricter class of the two. A treatment clause taken out of a deal's sentence stays apart
        groups: list = []
        last = -1
        for sent, cls, reason, n in privacy.classify_parts(text, names, headers, self._amounts()):
            if cls == "private":
                self.private.append((sent, reason))
                if self.store:
                    self.store.add_private(self.bid, sent, reason)
                groups.append(None)
                last = n
                continue
            if groups and groups[-1] and n != last and privacy.continues(groups[-1][1], sent):
                was = groups[-1][0]
                groups[-1] = ("commercial" if "commercial" in (was, cls) else cls, groups[-1][1] + " " + sent)
            else:
                groups.append((cls, sent))
            last = n
        n = 0
        self._closer_sents = []
        for g in groups:
            if not g:
                continue
            cls, sent = g
            n += 1
            travel = "file" if cls == "data" or self.include_business else "machine"
            self._closer_sents.append((sent, travel))
            self._rec("fact", f"f:known_issues:{n}", "history", "Owner's note", _sentence(sent),
                      "told", "confirmed", cls=cls if cls in ("data", "commercial") else "data",
                      stale="+365d", ref="q:known_issues", travel=travel)

    def _exclusion_facts(self):
        """One counted note per rule the counted numbers went through, marked applied,
        with its rows and money (the rule first, so it covers rows added later; then
        the numbers as written), each measured in the money its rows carry; one
        counted total per table after every rule when more than one changes its
        rows; one note per rule the owner wrote that is not applied, saying those
        rows are counted as they are (a proposed rule the owner did not tick is
        said as that, never as their note); and any calculated tab whose totals
        still include rows the owner leaves out. A column whose unit changed at a
        date is never summed in any of them."""
        mixed = getattr(self.a, "mixed_units", None) or {}
        live = list(getattr(self.a, "rules", None) or [])
        dropping: dict = {}
        for r in live:
            t = next((t for t in self.tables if t.tid == r.table), None)
            if t is None:
                continue
            if r.kind in ("exclude", "filter", "pair", "dedupe") and not rules.word_scope(self.a, r):
                dropping.setdefault(t.tid, []).append(r)
        for r in live:
            t = next((t for t in self.tables if t.tid == r.table), None)
            if t is None:
                continue
            eff = rules.effect(self.a, r)
            f = fmt_money if eff["money"] else (lambda x: fmt_num(round(x, 2)))
            summed = bool(eff["col"]) and (t.tid, eff["col"]) not in mixed
            of = f", {f(eff['sum'])} of {eff['col']}" if summed else ""
            cols_scope = [h for h in r.scope if h not in rules.word_scope(self.a, r)]
            scope = f", for {interview._join(cols_scope)} only" if cols_scope else ""
            words = rules.word_scope(self.a, r)
            n_rows = f"{eff['rows']:,} {plural('row', eff['rows'])}"
            if words and r.kind in ("exclude", "filter"):
                # kept for one calculation in the owner's words: no count or total here changes
                calc = interview._join(words)
                stmt = (f"Kept for {calc} only (the owner's rule): rows of {t.sheet} where {rules.where(r)} "
                        f"({n_rows}{of}) are {'left out of' if r.kind == 'exclude' else 'the only rows counted in'} "
                        f"{calc}; no count or total in this brain changes for it.")
                self._rec("fact", f"f:x:{_hid(*r.key(), *words)}", "exclusion", f"Only for {calc}"[:80], stmt,
                          "computed", "current", deps=[(t.sheet, h) for h in r.cols()], ref="rule:scoped")
                continue
            alone = len(dropping.get(t.tid, [])) <= 1
            # a rule scoped to named totals says where its rows are left out, and that the other totals keep them
            tots = f"{interview._join(cols_scope)} totals" if cols_scope else ""
            if r.kind == "exclude":
                stmt = (f"Applied to counted numbers: rows of {t.sheet} where {rules.where(r)} "
                        f"({n_rows}{of}) are left out" + (f" of {tots}; the other counts and totals keep them."
                                                           if tots else "."))
                if summed and not r.scope and alone:
                    stmt += (f" When this was written, all {t.n_rows:,} rows total {f(eff['total'])} and the counted "
                             f"total is {f(eff['after'])}.")
            elif r.kind == "filter":
                stmt = (f"Applied to counted numbers: only rows of {t.sheet} where {rules.where(r)} are counted "
                        f"({n_rows}{of})" + (f" in {tots}; the other counts and totals take every row." if tots
                                             else "."))
            elif r.kind == "adjust":
                col, minus = r.values.get("col"), r.values.get("minus")
                moved = (f"; {col} totals {f(eff['total'])} before and {f(eff['after'])} after"
                         if (t.tid, col) not in mixed else "")
                stmt = (f"Applied to counted numbers: {col} is taken as {col} minus {minus} on the rows of {t.sheet} "
                        f"where {rules.where(r)} ({n_rows}{moved}){scope}.")
            elif r.kind == "map":
                # a map that pairs each value with its own match says each pair, never 'as one'
                stmt = (f"Applied to counted numbers: in {r.values.get('col')} on {t.sheet}, "
                        f"{rules.map_words(self.a, r)} ({n_rows}{of}){scope}.")
            elif r.kind == "scale":
                col = r.values.get("col")
                follows = r.values.get("follows")
                moved = (f"; {col} totals {f(eff['total'])} before and {f(eff['after'])} after"
                         if (t.tid, col) not in mixed else "")
                stmt = (f"Applied to counted numbers: {col} is divided by "
                        f"{fmt_num(float(r.values.get('by') or 0))} on "
                        + (f"the rows of {t.sheet} where {rules.where(r)}" if r.predicate
                           else f"every row of {t.sheet}")
                        + f" ({n_rows}{moved}){scope}.")
                if follows:
                    stmt += (f" {col} is worked out from {follows} on these rows, so it follows the owner's rule on "
                             f"{follows}.")
            elif r.kind == "dedupe":
                stmt = (f"Applied to counted numbers: each {r.values.get('col')} on {t.sheet} is counted once, the "
                        f"{r.values.get('keep', 'first')} row of each kept ({eff['rows']:,} repeated rows{of} left "
                        f"out){scope}.")
            elif r.kind == "fill":
                col = r.values.get("col")
                moved = (f"; {col} totals {f(eff['total'])} before and {f(eff['after'])} after"
                         if (t.tid, col) not in mixed else "")
                stmt = (f"Applied to counted numbers: a blank {col} on {t.sheet} takes the value above "
                        f"it ({n_rows}{moved}){scope}.")
            else:
                # a pair rule names both sides: the rows it names and the rows they cancel
                k = eff["rows"]
                stmt = (f"Applied to counted numbers: rows of {t.sheet} where {rules.where(r)} are left out with the "
                        f"rows they cancel ({k:,} {plural('row', k)} and the {k:,} {plural('row', k)} they cancel, "
                        f"{2 * k:,} in all){scope}.")
            one = r.kind == "exclude" and len(r.predicate) == 1 and r.predicate[0].get("op", "in") == "in"
            vals = ", ".join(list(dict.fromkeys(str(v) for v in r.predicate[0]["values"]))[:5]) if one else ""
            header = r.predicate[0]["col"] if one else ""
            rid = f"f:x:{_hid(t.sheet, header, vals)}" if one else f"f:x:{_hid(*r.key())}"
            self._rec("fact", rid, "exclusion", (f"Totals without {vals}" if one else f"Rule on {rules.topic(r)}")[:80],
                      stmt, "computed", "current", deps=[(t.sheet, h) for h in r.cols()], ref="rule:applied")
            if not one:
                continue
            # a calculated tab that still carries the left-out value is where a wrong total gets quoted
            keys = {norm_key(v) for v in r.predicate[0]["values"]}
            for d in self.tables:
                if d.tid in self.a.derived and any(norm_key(h) in keys for h in d.headers):
                    self._rec("fact", f"f:xd:{_hid(d.sheet, vals)}", "exclusion", f"{d.sheet} includes {vals}"[:80],
                              f"The {d.sheet} tab has a {vals} column, so its totals include the rows the owner "
                              "leaves out.", "computed", "current", deps=[(t.sheet, header)], ref="rule:applied")
                    break
        self._counted_totals(dropping, live, mixed)
        for c in getattr(self.a, "unapplied_rules", None) or []:
            self._not_applied(c, "the owner's note on {topic}", "rule:not_applied", "f:xn:", mixed)
        for c in rules.declined(self.a, self.answers, live):
            self._not_applied(c, "a proposed rule on {topic} that the owner did not tick", "rule:declined", "f:xy:",
                              mixed)
        for u in rules.unbound(self.a, self.answers):
            t = next((t for t in self.tables if t.tid == u["table"]), None)
            if t is None:
                continue
            self._rec("fact", f"f:xu:{_hid(t.sheet, u['said'])}", "exclusion", f"Not applied on {t.sheet}"[:80],
                      f"Not applied to counted numbers: the owner wrote {interview._quoted(u['said'])} and no rows on "
                      f"{t.sheet} could be found for \"{u['words']}\", so {t.sheet}'s numbers are counted as they are.",
                      "computed", "current", deps=[], ref="rule:not_applied")
        # a treatment the owner typed that no rule could be read from: listed, never dropped
        if self.path == self._main_file():
            for u in rules.unread(self.a, self.answers):
                self._rec("fact", f"f:xr:{_hid(u['said'])}", "exclusion", "Owner's rule, not applied"[:80],
                          f"The owner's rule, not applied from these words alone: {interview._quoted(u['said'])}",
                          "computed", "current", deps=[], ref="rule:not_applied")

    def _not_applied(self, c: dict, what: str, ref: str, prefix: str, mixed: dict):
        r = c["rule"]
        t = next((t for t in self.tables if t.tid == r.table), None)
        if t is None:
            return
        f = fmt_money if c["money"] else (lambda x: fmt_num(round(x, 2)))
        of = f", {f(c['sum'])} of {c['col']}" if c["col"] and (t.tid, c["col"]) not in mixed else ""
        topic = rules.topic(r)
        head = "Not applied: " if ref == "rule:not_applied" else "Not ticked: "
        self._rec("fact", f"{prefix}{_hid(*r.key())}", "exclusion", f"{head}{topic}"[:80],
                  f"Not applied to counted numbers: {what.format(topic=topic)} ({c['rows']:,} rows on "
                  f"{t.sheet}{of}); those rows are counted as they are.", "computed", "current",
                  deps=[(t.sheet, h) for h in r.cols()], ref=ref)

    def _counted_totals(self, dropping: dict, live: list, mixed: dict):
        """One counted total per table after every rule that changes its rows, when
        more than one does: each rule's own note says only what it takes out. It
        counts every rule acting on the table (leave-outs, maps, unit and pair rules,
        adjustments), says the rows two leave-outs share, reads a snapshot's stock
        column on its latest date (never summed across dates), and sums a column
        whose two signs the owner said mean the same without its sign."""
        from .recipes import _latest_rows, day_words
        signed = self._sign_cols()
        snaps = getattr(self.a, "snapshots", None) or {}
        for tid, rs in dropping.items():
            if len(rs) < 2:
                continue
            t = next(t for t in self.tables if t.tid == tid)
            acting = [r for r in live if r.table == tid and r.kind in rules.KINDS and r.kind != "unit"
                      and not rules.word_scope(self.a, r)]
            snap = snaps.get(tid) or {}
            cols: list = []
            for r in rs:
                e = rules.effect(self.a, r)
                if e["col"] and (tid, e["col"]) not in mixed and e["col"] not in [c for c, _m in cols]:
                    cols.append((e["col"], e["money"]))
            parts = []
            for col, money in cols:
                if col not in t.headers:
                    continue
                j = t.headers.index(col)
                f = fmt_money if money else (lambda x: fmt_num(round(x, 2)))
                sign = abs if (tid, col) in signed else (lambda x: x)
                kept_rows = [row for _i, row in rules.apply(live, t, self.a.cols[t.tid], metric=col)]
                all_rows = list(t.rows)
                where_ = ""
                if col in (snap.get("stock") or []):
                    kept_rows, all_rows = _latest_rows(kept_rows, snap), _latest_rows(all_rows, snap)
                    where_ = f" on the latest {snap['col']}, {day_words(snap['latest_iso'])}"
                total = sum(sign(row[j]) for row in all_rows if j < len(row) and _num(row[j]))
                after = sum(sign(row[j]) for row in kept_rows if j < len(row) and _num(row[j]))
                how = " as absolute values" if (tid, col) in signed else ""
                parts.append(f"{col} {f(after)}{where_}{how} (all {len(all_rows):,} rows{' then' if where_ else ''}: "
                             f"{f(total)})")
            n_kept = len(rules.apply([r for r in live if not r.scope], t, self.a.cols[t.tid]))
            shared = []
            for i, a in enumerate(rs):
                for b in rs[i + 1:]:
                    both = rules.rows_of(self.a, a) & rules.rows_of(self.a, b)
                    if both:
                        e = rules.effect(self.a, a)
                        j = t.headers.index(e["col"]) if e["col"] in t.headers and (tid, e["col"]) not in mixed \
                            else None
                        f = fmt_money if e["money"] else (lambda x: fmt_num(round(x, 2)))
                        money = sum(t.rows[i2][j] for i2 in both if j is not None and j < len(t.rows[i2])
                                    and _num(t.rows[i2][j])) if j is not None else None
                        shared.append(f"the rules on {rules.topic(a)} and on {rules.topic(b)} share {len(both):,} "
                                      f"{plural('row', len(both))}" + (f" ({f(money)} of {e['col']})"
                                                                       if money is not None else "")
                                      + ", left out once")
            k = len(acting)
            stmt = (f"Counted after all {k} of the owner's rules on {t.sheet}: {n_kept:,} of its {t.n_rows:,} "
                    f"rows" + (f"; {'; '.join(parts)}" if parts else "") + ". "
                    + (_sentence("; ".join(shared)[:1].upper() + "; ".join(shared)[1:]) + " " if shared else "")
                    + "Each rule's own note says only what it takes out.")
            self._rec("fact", f"f:xt:{_hid(tid)}", "exclusion", f"Counted totals on {t.sheet}"[:80], stmt,
                      "computed", "current", deps=[(t.sheet, c) for c, _m in cols], ref="rule:total")

    def _sign_cols(self) -> set:
        """{(table, column)} whose two signs the owner said mean the same thing (a
        'same meaning, only the sign flipped' pick on a switch): summed without
        their sign in every counted total."""
        out = set()
        for qid, ans in rules.with_inferred(self.answers).items():
            if not qid.startswith("find_boundary_") or not isinstance(ans, dict) \
                    or "sign" not in (ans.get("options") or []):
                continue
            tid = (ans.get("about") or {}).get("table")
            for i in getattr(self.a, "insights", None) or []:
                n = i.get("numbers") or {}
                if str(i.get("recipe", "")).startswith("boundary:") and n.get("table") == tid \
                        and qid.endswith(str(n.get("date", "")).replace("-", "")):
                    out |= {(tid, c["col"]) for c in n.get("changes") or [] if c.get("kind") == "sign"}
        return out

    def _gotcha_facts(self):
        env = interview.Env(self.a, self.answers)
        done = interview.settled(self.a, self.answers)
        for g in self.a.playbook.get("gotchas", []):
            cond = g.get("if")
            if cond and not env.all(cond):
                continue
            conds = cond if isinstance(cond, list) else [cond] if cond else []
            if any(c in done for c in conds):
                continue          # the owner already said what this is; the guess retires
            files = self._role_files(conds)
            if (files and self.path not in files) or (not files and self.path != self._main_file()):
                continue          # a note about a column in another workbook
            say_ = g.get("say", "")
            if not _DATA_SLOT.search(say_) and all(c.split(":")[0] in _GENERIC for c in conds):
                continue          # true of any workbook with formulas or tabs: not a note about this one
            if not self._evidence(g.get("evidence")):
                continue          # the data does not show what the note warns about
            deps = []
            for p in (cond if isinstance(cond, list) else [cond] if cond else []):
                _, _, arg = p.partition(":")
                r = self.a.detection["roles"].get(arg)
                if r and self._mine(r["table"]):
                    deps.append((self.a.table(r["table"]).sheet, r["header"]))
            # run by run: clauses that quote the data are counted; the rule of thumb next to them
            # is still a guess from the playbook, even in the same sentence of the template
            for i, (clause, from_data) in enumerate(gotcha_parts(say_)):
                if not from_data and not self._guess_fits(say_, conds, clause):
                    continue          # a guess about another kind of column, or one the owner defined
                if from_data:
                    clause = _values_tail(clause)
                stmt = _sentence(interview.fill(clause, env))
                if not stmt or (not from_data and reads_as_command(stmt, self._headers())):
                    continue
                rec = self._rec("fact", f"f:g:{_hid(say_, i) if i else _hid(say_)}", "gotcha", "Watch out",
                                stmt, "computed" if from_data else "inferred",
                                "current" if from_data else "unconfirmed", deps=deps, stale="on-change",
                                ref="recipe:gotcha" if from_data else "playbook:norm")
                if not from_data:
                    rec["said_by"] = "playbook"

    def _guess_fits(self, say_: str, conds: list, clause: str) -> bool:
        """A playbook guess goes in only about columns it plainly fits: for every role
        it names, the column bound to that role carries a header the playbook lists
        for the role, or shares a word with the guess (in its header or its values).
        A guess about a column whose meaning the owner gave is dropped: the owner's
        words are the note."""
        rids = {m.group(1) for m in _ROLE_SLOT.finditer(say_)} | {
            p.partition(":")[2].split("=")[0] for p in conds if ":" in p}
        roles = self.a.detection["roles"]
        pbr = self.a.playbook.get("roles", {})
        defined = {(t, c) for t, c, aspect in interview.covered_keys(self.answers) if aspect == "meaning"}
        said = interview._stems(_ROLE_SLOT.sub(" ", clause)) - {interview._stem(w) for w in interview._FRAME}
        for rid in rids:
            r = roles.get(rid)
            if not r or r.get("col") is None:
                continue
            if (r["table"], r["header"]) in defined:
                return False
            aliases = {detect.norm_header(h) for h in (pbr.get(rid) or {}).get("headers", [])}
            if not aliases or detect.norm_header(r["header"]) in aliases:
                continue          # bound by a header the playbook names for it (or by no header list at all)
            c = r["col"]
            seen = interview._stems(r["header"]) | interview._stems(
                " ".join(str(k) for k, _n in (c.top or [])[:50] if not c.sensitive))
            if not said & seen:
                return False
        return True

    def _evidence(self, preds) -> bool:
        """A gotcha's evidence check, over this file's rows: a warning fires only when
        the data shows the thing it warns about. Unknown checks never pass."""
        for p in preds if isinstance(preds, list) else [preds] if preds else []:
            name, _, arg = str(p).partition(":")
            if name == "no_insight":          # a counted check already settled it the other way
                # counted on this file's rows, or against a reference table this file holds
                if any(i["recipe"].startswith(arg) and ((i.get("files") or [self.path]) == [self.path]
                                                        or (i.get("numbers") or {}).get("ref_file") == self.path)
                       for i in self.a.insights):
                    return False
                continue
            r = self.a.detection["roles"].get(arg)
            if not r or r.get("col") is None or not self._mine(r["table"]):
                return False
            t, j = self.a.table(r["table"]), r["col"].j
            if name == "times_near_midnight" and not _near_midnight(t.column(j)):
                return False
            if name == "full_discount":
                # a discount is compared only with this table's money columns (a price, a line
                # amount) and the counts they multiply, never with an ID or any other number
                units = {rid: (self.a.playbook.get("roles", {}).get(rid) or {}).get("unit")
                         for rid in self.a.detection["roles"]}
                on = {rid: x["col"].j for rid, x in self.a.detection["roles"].items()
                      if x.get("table") == r["table"] and x.get("col") is not None and x["col"].j != j}
                money = [k for rid, k in on.items() if units.get(rid) == "currency"]
                counts = [k for rid, k in on.items() if units.get(rid) == "count"]
                if not _full_discount(t, j, r["header"], money, counts):
                    return False
            if name not in EVIDENCE_CHECKS:
                return False
        return True

    def _columns(self):
        role_of = {(r["table"], r["header"]): rid for rid, r in self.a.detection["roles"].items()}
        # groups the owner said are counted in another unit: that column is never summed across them
        groups: dict = {}
        for r in rules.unit_groups(self.a, self.answers):
            groups.setdefault((r.table, r.values.get("col")), []).append(rules.where(r))
        pb_roles = self.a.playbook.get("roles", {})
        for t in self.tables:
            # a count summed over rows of different units (cases and each) is no total
            units = any(interview.is_unit_col(c.header, role_of.get((t.tid, c.header))) and c.type == "text"
                        and c.distinct >= 2 for c in self.a.cols[t.tid])
            # a grid's period columns are not columns of their own: its line items are the nodes
            periods = set(self.a.period_headers(t))
            snap = (getattr(self.a, "snapshots", None) or {}).get(t.tid) or {}
            signed = self._sign_cols()
            for c in self.a.cols[t.tid]:
                if c.type == "empty" or c.header in periods:
                    continue
                rid = role_of.get((t.tid, c.header))
                kind = {"identifier": "column", "metric": "metric", "dimension": "dimension",
                        "temporal": "column", "flag": "column", "text": "column"}.get(c.semantic, "column")
                role = pb_roles.get(rid, {}) if rid else {}
                ruled = self.a._ctx.col(t, c.j, c.header) if c.type == "number" else None
                latest = self._latest_sum(t, c, snap) if c.header in snap.get("stock", []) else None
                absolute = None
                if (t.tid, c.header) in signed and c.type == "number":
                    absolute = tuple(sum(abs(r[c.j]) for r in rows if c.j < len(r) and _num(r[c.j]))
                                     for rows in (t.rows, self.a._ctx.rows(t, c.header)))
                stmt, text = _column_statement(c, t, role, bool(rid and self.a.detection["roles"][rid].get("inferred")),
                                               absolute=absolute, ruled=ruled if ruled is not c else None,
                                               mixed_units=units and role.get("unit") == "count", latest=latest,
                                               unit_change=(getattr(self.a, "mixed_units", None) or {}).get(
                                                   (t.tid, c.header)),
                                               unit_group=groups.get((t.tid, c.header)))
                stmt = _with_marks(stmt, self.a.not_applied_marks([t], [(t.sheet, c.header)]))
                self._rec("node", col_id(t.sheet, c.header, t.tid), kind, c.header, stmt, "computed", "current",
                          deps=[(t.sheet, c.header)], stale="on-change", text=text,
                          ref=f"role:{rid}" if rid else "")
            if periods:
                self._line_items(t, [h for h in t.headers if h in periods])

    def _latest_sum(self, t, c, snap: dict) -> tuple:
        """(the latest snapshot date in words, the column's sum on that date's rows under the owner's rules)."""
        from .recipes import _latest_rows, day_words
        rows = _latest_rows(self.a._ctx.rows(t, c.header), snap)
        return (f"{snap['col']} {day_words(snap['latest_iso'])}",
                sum(r[c.j] for r in rows if c.j < len(r) and _num(r[c.j])))

    def _line_items(self, t, periods: list):
        """One node per line item of a grid: how many of its periods hold a number, and their range."""
        from .analyze import _period_kind
        lab = t.row_label_col if t.row_label_col >= 0 else 0
        js = [t.headers.index(h) for h in periods]
        kind = _period_kind(periods[0]) if periods else "period"
        word = next((g["numbers"]["period"] for g in self.a.grain_facts if g["recipe"] == f"grain:wide:{t.tid}"),
                    "period" if kind == "date" else kind)
        for r in t.rows[:200]:
            label = r[lab] if lab < len(r) else None
            if not isinstance(label, str) or not label.strip():
                continue
            label = " ".join(label.split())
            vals = [r[j] for j in js if j < len(r) and _num(r[j])]
            stmt = f"{label} is a line item on {t.sheet}"
            if vals:
                stmt += (f": {len(vals):,} of its {len(js):,} {plural(word, len(js))} hold a number, from "
                         f"{fmt_num(round(min(vals), 2))} to {fmt_num(round(max(vals), 2))}")
            self._rec("node", f"line:{t.sheet}.{{{label}}}", "line_item", label, stmt + ".", "computed", "current",
                      stale="on-change", text=f"periods: {len(js)}\nfilled: {len(vals)}")

    def _entities(self):
        pb_roles = self.a.playbook.get("roles", {})
        for rid, r in self.a.detection["roles"].items():
            role = pb_roles.get(rid, {})
            if role.get("kind") not in ("entity",) or r.get("col") is None or not self._mine(r["table"]):
                continue
            c = r["col"]
            t = self.a.table(r["table"])
            noun = role.get("entity") or rid
            stmt = f"There are {c.distinct:,} {plural(noun, c.distinct)} in {c.header} on {t.sheet}."
            ruled = self.a._ctx.col(t, c.j)
            if ruled.distinct != c.distinct:
                stmt = (f"There are {ruled.distinct:,} {plural(noun, ruled.distinct)} in {c.header} on {t.sheet} "
                        f"after the owner's rules; the column has {c.distinct:,} values.")
            text = ""
            if c.distinct <= 50 and not c.sensitive:
                text = "values: " + " | ".join(str(k)[:40] for k, _ in c.top[:50])
                if c.distinct <= 10:
                    stmt = stmt[:-1] + ": " + ", ".join(str(k)[:30] for k, _ in c.top[:10]) + "."
            stmt = _with_marks(stmt, self.a.not_applied_marks([t], [(t.sheet, c.header)]))
            self._rec("node", f"ent:{rid}", "entity", plural(noun).capitalize(), stmt, "computed",
                      "current", deps=[(t.sheet, c.header)], stale="on-change", text=text,
                      frm=col_id(t.sheet, c.header, t.tid))

    def _edges(self):
        a = self.a
        for j in a.joins:
            if not (self._mine(j["from_table"]) and self._mine(j["to_table"])):
                continue
            ans = self.answers.get(_join_qid(j))
            if j["band"] == "ask" and not (ans and "same" in ans.get("options", [])):
                continue
            ft, tt = a.table(j["from_table"]), a.table(j["to_table"])
            src = "told" if ans else "computed"
            stmt = (f"{j['from_col']} on {_tname(ft)} matches {j['to_col']} on {_tname(tt)} for "
                    f"{pct(j['rows_matched'])} of rows.")
            self._rec("edge", f"e:{_hid('join', j['from_table'], j['from_col'], j['to_table'], j['to_col'])}",
                      "joins_on", f"{j['from_col']} to {tt.sheet}", stmt, src,
                      "confirmed" if ans else "current",
                      frm=col_id(ft.sheet, j["from_col"], ft.tid), to=col_id(tt.sheet, j["to_col"], tt.tid),
                      deps=[(ft.sheet, j["from_col"]), (tt.sheet, j["to_col"])], stale="on-change",
                      ref="recipe:overlap", text=f"rows_matched: {j['rows_matched']}")
        fa = a.formulas.get(self.path) or {}
        derived = {(d["sheet"], d["from"]) for d in fa.get("derived", [])}
        for d in fa.get("derived", []):
            sources = [e["from"] for e in sorted(fa.get("sheet_edges", []), key=lambda e: -e["refs"])
                       if e["to"] == d["sheet"] and e["from"] != d["sheet"]]
            src_text = interview._join(sources[:4]) if sources else d["from"]
            self._rec("edge", f"e:{_hid('derived', d['sheet'], d['from'])}", "derived_from",
                      f"{d['sheet']} from {d['from']}",
                      f"{d['sheet']} is calculated from {src_text} ({pct(d['formula_share'])} of its cells "
                      "are formulas).", "computed", "current",
                      frm=f"sheet:{d['sheet']}", to=f"sheet:{d['from']}", ref="recipe:formula_refs")
        for lk in fa.get("lookups", [])[:20]:
            frm = self._col(lk["sheet"], lk["column"]) if lk["column"] else f"sheet:{lk['sheet']}"
            self._rec("edge", f"e:{_hid('lookup', lk['sheet'], lk['column'], lk['to_sheet'])}", "looks_up",
                      f"{lk['column'] or lk['sheet']} looks up {lk['to_sheet']}",
                      f"{lk['column'] or 'Cells'} on {lk['sheet']} is looked up from {lk['to_sheet']} "
                      f"({lk['function']}, {lk['cells']:,} cells).", "computed", "current",
                      frm=frm, to=f"sheet:{lk['to_sheet']}", ref="recipe:formula_refs")
        for e in fa.get("sheet_edges", [])[:30]:
            if (e["to"], e["from"]) in derived:
                continue
            if any(lk["sheet"] == e["to"] and lk["to_sheet"] == e["from"] for lk in fa.get("lookups", [])):
                continue
            self._rec("edge", f"e:{_hid('feeds', e['from'], e['to'])}", "feeds",
                      f"{e['from']} feeds {e['to']}",
                      f"{e['from']} feeds {e['to']} ({e['refs']:,} formula references).", "computed",
                      "current", frm=f"sheet:{e['from']}", to=f"sheet:{e['to']}", ref="recipe:formula_refs")
        for t in self.tables:
            for fd in a.fds.get(t.tid, [])[:8]:
                self._rec("edge", f"e:{_hid('fd', t.tid, fd['from'], fd['to'])}", "determines",
                          f"{fd['from']} sets {fd['to']}",
                          f"Each {fd['from']} on {t.sheet} has one {fd['to']}"
                          + (f" ({fd['exceptions']} exceptions)." if fd["exceptions"] else "."),
                          "computed", "current", frm=col_id(t.sheet, fd["from"], t.tid),
                          to=col_id(t.sheet, fd["to"], t.tid), deps=[(t.sheet, fd["from"]), (t.sheet, fd["to"])],
                          stale="on-change", ref="recipe:functional_dependency")
        # formula flow between labeled rows (financial models)
        if a.playbook.get("graph", {}).get("mode") == "formula_flow":
            for rf in fa.get("row_flow", [])[:200]:
                self._rec("edge", f"e:{_hid('flow', rf['from'], rf['to'])}", "feeds",
                          f"{_short_label(rf['from'])} feeds {_short_label(rf['to'])}",
                          f"{rf['from']} feeds {rf['to']}.", "computed", "current",
                          frm=f"row:{rf['from']}", to=f"row:{rf['to']}", ref="recipe:formula_refs")

    def _links(self):
        """Thin links to OTHER files: name and join column only."""
        seen = set()
        for j in self.a.joins:
            if not j["cross_file"]:
                continue
            mine_from, mine_to = self._mine(j["from_table"]), self._mine(j["to_table"])
            if not (mine_from or mine_to):
                continue
            ans = self.answers.get(_join_qid(j))
            if j["band"] == "ask" and not (ans and "same" in ans.get("options", [])):
                continue
            my_tid = j["from_table"] if mine_from else j["to_table"]
            my_col = j["from_col"] if mine_from else j["to_col"]
            other_file = j["to_file"] if mine_from else j["from_file"]
            other_col = j["to_col"] if mine_from else j["from_col"]
            name = os.path.basename(other_file)
            key = (name, my_col)
            if key in seen:
                continue
            seen.add(key)
            t = self.a.table(my_tid)
            self._rec("link", f"link:{_hid(name, my_col)}", "other_workbook", name,
                      f"Another workbook named {name} connects here on {my_col}.",
                      "told" if ans else "computed", "confirmed" if ans else "current",
                      frm=col_id(t.sheet, my_col, t.tid), to=other_col, ref="recipe:overlap")

    def _insights(self):
        for ins in self.a.insights:
            if ins.get("files") and any(f != self.path for f in ins["files"]):
                continue          # numbers from another file never go into this file's brain
            deps = [(s, h) for s, h in ins.get("depends", [])
                    if any(t.sheet == s for t in self.tables)]
            if ins.get("depends") and not deps:
                continue
            stmt = _sentence(ins["statement"])       # code's own sentence: no instruction lint
            known = ""
            if ins["recipe"].startswith("unmatched:"):
                # the codes seen so far, so a later check can tell which ones are new
                known = "known: " + " | ".join(str(k) for k in (ins.get("numbers") or {}).get("missing_keys", [])[:200])
            self._rec("insight", f"i:{self._insight_hid(ins, deps, stmt)}", ins.get("kind", "insight"),
                      ins["recipe"].split(":")[0].replace("_", " "), stmt, "computed", "current",
                      deps=deps, stale="on-change", ref=f"recipe:{ins['recipe']}", text=known)

    def _insight_hid(self, ins: dict, deps: list, stmt: str) -> str:
        """An insight's id: the recipe plus the tables and columns it reads, or, when it
        reads none, the file and the tab it names. Two tabs with the same finding differ."""
        if deps:
            tids = set()
            for s, h in deps:
                on = [t for t in self.tables if t.sheet == s]
                tids |= {t.tid for t in on if h in t.headers} or {t.tid for t in on[:1]}
            return _hid(ins["recipe"], *sorted(tids), *sorted({h for _, h in deps}))
        # the table it names tells two tables on one tab apart; a tab's only table has the
        # tab's name as its id (after the file part), so the id is the same as the tab's
        tid = str((ins.get("numbers") or {}).get("table") or "").rsplit(":", 1)[-1]
        if tid:
            return _hid(ins["recipe"], os.path.basename(self.path), tid)
        sheet = (ins.get("numbers") or {}).get("sheet") or ""
        if not sheet:
            named = [(stmt.find(s.name), -len(s.name), s.name) for s in self.book.data_sheets() if s.name in stmt]
            sheet = min(named)[2] if named else ""
        return _hid(ins["recipe"], os.path.basename(self.path), sheet)

    def _descriptions(self):
        for key, text in self.desc.items():
            m = re.match(r"^(.*?)\.\{(.*)\}$", key)
            if not m:
                continue
            sheet, header = m.groups()
            if not any(t.sheet == sheet and header in t.headers for t in self.tables):
                continue
            s, _ = brainzip.clean_text(str(text))
            s = _sentence(re.sub(r"\s+", " ", s).strip()[:300])
            if not s or is_imperative(s):
                continue
            self._rec("fact", f"f:d:{_hid(sheet, header)}", "definition", header, f"{header}: {s}",
                      "inferred", "unconfirmed", deps=[(sheet, header)], stale="+365d",
                      ref="model:describe")

    def _received(self):
        """Claims from a brain someone else wrote: kept as their notes, never as ours."""
        have = {r["id"] for r in self.records}
        for r in self.received:
            if r.get("id") in have or not kept_when_received(r):
                continue
            rec = {k: r.get(k, "") for k in brainzip.FIELDS if k not in ("part",)}
            if rec.get("source") == "told" and rec.get("said_by", "") in ("", "owner"):
                rec["said_by"] = "sender"     # their owner is not this owner: never shown as "the owner said"
            rec["_travel"] = "file"
            if reads_as_command(rec.get("statement", ""), self._headers()):
                # shown here, flagged, but never written into the next copy of the file
                rec["status"] = "disputed"
                rec["ref"] = "lint:reads-like-an-instruction"
                rec["_travel"] = "machine"
            self.records.append(rec)

    def _main_file(self) -> str:
        return self.a.file_of.get(getattr(self.a.main_table, "tid", ""), "")

    def _role_files(self, preds) -> set:
        out = set()
        for p in preds if isinstance(preds, list) else [preds] if preds else []:
            _, _, arg = p.partition(":")
            r = self.a.detection["roles"].get(arg.split("=")[0])
            if r:
                out.add(self.a.file_of.get(r["table"], ""))
        return out

    def _about_this_file(self, q) -> bool:
        """A question about another open workbook stays out of this one's brain."""
        files = (q.meta.get("finding") or {}).get("files")
        if files:
            return self.path in files
        pbq = next((x for x in self.a.playbook.get("questions", []) if x["id"] == q.id), None)
        files = self._role_files((pbq or {}).get("ask_if", []))
        return self.path in files if files else self.path == self._main_file()

    def _open_items(self):
        state = {"answers": self.answers}
        opens = interview.open_items(self.a, state)
        closer = getattr(self, "_closer_sents", None) or []
        written = []
        for q in opens[:12]:
            if q.id.startswith(("join_", findings.READBACK)):
                continue          # a rule not read back yet has its own not-applied note
            if not self._about_this_file(q):
                continue
            if self._grain_counted(q):
                continue          # what a row is, code already counted: a key it is unique by
            said = self._routed(q, closer)
            if said is not None:
                # the owner's closing note names this item's values: that note answers it (it stays a closing
                # note); when it is kept on this machine, the file says the item was answered
                if said == "machine":
                    self._rec("fact", f"f:answered:{q.id}", q.kind, q.header,
                              _sentence(f"Answered in the owner's closing note: {_open_text(q.prompt)} The details "
                                        "are kept on this machine"), "computed", "current", ref=f"q:{q.id}")
                continue
            self._rec("open", f"o:{q.id}", q.kind, q.header, _sentence(f"Not answered yet: {_standalone(q)}"),
                      "inferred", "unconfirmed", ref=f"q:{q.id}")
            written.append(q)
        for qid, ans in self.answers.items():
            # the goal and the build pick ('_build') are wishes, not open aspects of the data
            if ans.get("not_sure") and qid not in (interview.CLOSER, "goal") and not qid.startswith("_"):
                self._rec("open", f"o:{qid}", ans.get("kind", "definition"), ans.get("header") or qid,
                          _sentence(f"The owner was not sure: {_open_text(ans.get('prompt', qid))}"), "told",
                          "unconfirmed", ref=f"q:{qid}")
        self._coverage(opens)

    def _grain_counted(self, q) -> bool:
        """A question about what a row is, on a table code found a key for."""
        ab = q.meta.get("about") or {}
        if q.kind != "grain" and ab.get("aspect") != "grain":
            return False
        tid = ab.get("table") or getattr(self.a.main_table, "tid", "")
        return bool((self.a.keys or {}).get(tid))

    def _routed(self, q, closer: list):
        """'file' or 'machine' when a sentence of the owner's closing note names the
        values an open item is about (as whole words, a code in capitals only as
        written), else None."""
        vals = [str(v) for v in (q.meta.get("about") or {}).get("values") or [] if str(v).strip()]
        vals = [v for v in vals if len(v) >= 3 or (len(v) >= 2 and v.upper() == v and any(ch.isalpha() for ch in v))]
        if not vals or not closer:
            return None
        # a name of two or more words is also named by its first word, as owners shorten it ('Lindquist' for
        # 'Lindquist Tile'), when that word is capitalized and five letters or more
        short = [v.split()[0] for v in vals if len(v.split()) >= 2 and len(v.split()[0]) >= 5
                 and v.split()[0][:1].isupper()]
        where = None
        for sent, travel in closer:
            if any(rules._value_pattern(v).search(sent) for v in vals) or \
                    any(re.search(r"(?<![\w-])" + re.escape(w) + r"(?![\w-])", sent) for w in short):
                where = "file" if travel == "file" or where == "file" else "machine"
        return where

    def _coverage(self, opens: list):
        """Which columns' meanings the owner confirmed (a note about what the owner
        said, so it is told), and, counted apart, which were asked about or found
        worth asking about and not confirmed."""
        mine = {t.tid: t for t in self.tables}
        done = {(t, c) for t, c, aspect in interview.covered_keys(self.answers) if aspect == "meaning" and t in mine}
        # an answer that settled something else (which rows count) leaves the meaning it was asked about open
        asked = [(a.get("about") or {}) for q, a in self.answers.items() if isinstance(a, dict)]
        left = {(ab.get("table"), ab.get("col")) for ab in asked + [q.meta.get("about") or {} for q in opens]
                if (ab.get("asked") or ab.get("aspect")) == "meaning" and ab.get("col")
                and ab.get("table") in mine} - done
        if not done and not left:
            return

        def names(keys):
            return interview._join([f"{c} on {mine[t].sheet}" for t, c in sorted(keys)[:8]]
                                   + ([f"{len(keys) - 8} more"] if len(keys) > 8 else []))
        if done:
            # the interview's own bookkeeping, not something the owner said: an answer about a tab or a
            # code list settles the question it was asked for, not every column that question named
            self._rec("fact", "f:coverage", "coverage", "Column meanings asked and answered",
                      f"Asked about and answered: what {names(done)} mean{'s' if len(done) == 1 else ''}.",
                      "computed", "current", ref="interview:coverage")
            if left:
                self._rec("fact", "f:coverage:open", "coverage", "Column meanings not said",
                          f"Not said yet: {names(left)}.", "computed", "current", ref="interview:coverage")
            return
        self._rec("fact", "f:coverage", "coverage", "Column meanings confirmed",
                  f"The owner has not said what any column means. Not said yet: {names(left)}.", "computed",
                  "current", ref="interview:coverage")


    # the brain as a graph of things --------------------------------------
    def _things(self):
        """Vendors, hotels, items, accounts, model rows: each a node; the links between
        them; and every note tied to the things it is about (the `to` column)."""
        notes = [r for r in self.records if r["record"] in ("fact", "insight", "open")]
        th = mind.Things(self.a, self.path)
        asked = [self._asked_about(r) for r in notes]
        mentioned = set()
        for r, ab in zip(notes, asked):
            mentioned |= {x for x in th.find(r["statement"]) if not ab or th.values[x]["table"] == ab["table"]}
        mentioned = set(sorted(mentioned))
        ids = set()
        if th.mode == "formula_flow":
            cells = set()
            for r in notes:
                cells |= mind.cells_named(self.a, r["statement"])
            rows, flow = mind.model_rows(self.a, self.path, cells)
            settled = []
            if "leftover" in ((self.answers.get("find_orphans") or {}).get("options") or []):
                settled.append("no formula uses")
            if "override" in ((self.answers.get("find_typed_plug") or {}).get("options") or []):
                settled.append("typed in a row of formulas")
            for x in rows:
                x["flags"] = [fl for fl in x["flags"] if not any(s in fl for s in settled)]
                flags = "; ".join(x["flags"][:3])
                stmt = (f"{x['label']} on {x['sheet']} is {'an input' if x['kind'] == 'input' else 'a calculation'} "
                        f"with {x['refs']:,} formula references" + (f"; worth a look: {flags}" if flags else "") + ".")
                self._rec("node", x["id"], "formula_block", x["label"], stmt, "computed", "current",
                          frm=f"sheet:{x['sheet']}", stale="on-change",
                          text=f"sheet: {x['sheet']}\nrole: {x['kind']}\nrefs: {x['refs']}"
                               + ("\nflags: " + flags if flags else ""))
                ids.add(x["id"])
        chosen = th.chosen(mentioned)
        totals = defaultdict(float)
        for i in th.values.values():      # a share of ALL of that kind, not of the dots shown
            if not i["left_out"]:
                totals[i["rid"]] += max(0.0, i["amount"])
        for i in chosen:
            noun = i["noun"]
            size = ""
            if th.size_header and i["amount"]:
                amt = fmt_money(i["amount"]) if th.money else fmt_num(round(i["amount"], 2))
                size = f"{amt} of {th.size_header}" + (f" ({pct(i['amount'] / totals[i['rid']])})"
                                                       if totals[i["rid"]] and not i["left_out"] else "")
            bits = [size] if size else []
            bits.append(f"{i['rows']:,} row{'s' if i['rows'] != 1 else ''}")
            if i["label"] != i["code"] and i["code"] not in i["label"]:
                bits.append(f"code {i['code']}")
            stmt = f"{i['label']} is {_an(noun)} {noun}: " + ", ".join(bits) + "."
            if i["left_out"]:
                stmt = stmt[:-1] + "; left out of counted totals, as the owner said."
            self._rec("node", i["id"], "thing", i["label"], stmt, "computed", "current",
                      frm=f"ent:{i['rid']}", stale="on-change",
                      text="\n".join(x for x in (f"kind: {noun}", f"sheet: {self.a.table(i['table']).sheet}",
                                                  f"code: {i['code']}",
                                                  f"amount: {round(i['amount'], 2)}" if th.size_header else "",
                                                  f"rows: {i['rows']}", "left_out: yes" if i["left_out"] else "")
                                      if x))
            ids.add(i["id"])
        label_of = {i["id"]: i["label"] for i in chosen}
        for x, y, lab, val in th.links(ids):
            amt = (f" ({fmt_money(val)})" if th.money else "") if th.size_header else ""
            self._rec("edge", f"e:{_hid('rel', x, y)}", "relates", f"{label_of[x]} {lab} {label_of[y]}"[:80],
                      f"{label_of[x]} {lab} {label_of[y]}{amt}.", "computed", "current", frm=x, to=y,
                      stale="on-change", text=f"weight: {round(val, 2)}")
        # the owner said these codes are one product: a line between old and new, only when the
        # answer itself is "all the same" with nothing typed; a partial answer stays a quoted note
        linked = set()
        for r in notes:
            if r.get("source") != "told" or not r.get("ref", "").startswith("q:follow_codes"):
                continue
            ans = self.answers.get(r["ref"][2:]) or {}
            if ans.get("options") != ["all"] or (ans.get("text") or "").strip() or r["ref"] in linked:
                continue
            linked.add(r["ref"])
            for old, new in re.findall(r"([\w-]+) -> ([\w-]+)", r["statement"]):
                x = next((i for i in th.values if th.values[i]["code"].lower() == old.lower()), None)
                y = next((i for i in th.values if th.values[i]["code"].lower() == new.lower()), None)
                if x in ids and y in ids:
                    self._rec("edge", f"e:{_hid('recoded', x, y)}", "same_as", f"{old} re-coded to {new}",
                              f"{old} was re-coded to {new}; the owner says they are the same product.", "told",
                              "confirmed", frm=x, to=y, ref=r.get("ref", ""))
        # every note points at what it is about
        cols = {r["id"] for r in self.records if r["record"] == "node" and r["id"].startswith("col:")}
        sheets = {r["id"] for r in self.records if r["record"] == "node" and r["id"].startswith("sheet:")}
        meta_id = next((r["id"] for r in self.records if r["record"] == "meta"), "")
        for r, ab in zip(notes, asked):
            # "(leaving out Q7, per the owner)" says what a number skips, not what the note is about
            said = re.sub(r"\((?:leaving out|excluding|without) [^)]*\)|\([^()]*, per the owner\)", "", r["statement"])
            if ab:          # an answer is about its own question's table and column, never another's
                r["to"] = " | ".join(dict.fromkeys(self._in_table(said, ab, th, ids, cols | sheets)[:8])) or meta_id
                continue
            about = sorted(x for x in th.find(said) if x in ids)      # sorted: the same brain draws the same
            if th.mode == "formula_flow":
                about += sorted(f"row:{c}" for c in mind.cells_named(self.a, r["statement"]) if f"row:{c}" in ids)
                if not about:
                    about += sorted(i for i in ids if i.startswith("row:") and len(i.split("!", 1)[-1]) >= 4
                                    and re.search(r"(?<![\w-])" + re.escape(i.split("!", 1)[-1]) + r"(?![\w-])",
                                                  said, re.I))[:3]
            if not about:
                for rid, noun, _tid, c in th.kinds:
                    if any(i.startswith(f"v:{rid}:") for i in ids) and re.search(
                            r"(?<![\w-])" + re.escape(c.header) + r"(?![\w-])", said):
                        about.append(f"ent:{rid}")
            if not about:
                for sheet, header in parse_deps(r.get("depends_on", "")):
                    cid = self._col(sheet, header)
                    if cid in cols:
                        about.append(cid)
            if not about:
                about = [c for c in sorted(cols) if re.search(r"(?<![\w-])" + re.escape(c[c.find(".{") + 2:-1]) + r"(?![\w-])",
                                                       r["statement"])][:3]
            if not about:
                about = [s for s in sorted(sheets) if re.search(r"\b" + re.escape(s[6:]) + r"\b", r["statement"])][:2]
            r["to"] = " | ".join(dict.fromkeys(about[:8])) or meta_id

    def _asked_about(self, r: dict) -> dict:
        """The {table, col} a note's question was about (its meta.about, kept on the
        answer; for one finding of a batch, that finding's), when the note answers a
        question about a table in this file."""
        ref = str(r.get("ref") or "")
        ab = r.get("_about") or ((self.answers.get(ref[2:]) or {}).get("about") if ref.startswith("q:") else None)
        return ab if isinstance(ab, dict) and any(t.tid == ab.get("table") for t in self.tables) else {}

    def _in_table(self, said: str, ab: dict, th, ids: set, known: set) -> list:
        """What an answer is about: its question's column, then the things and model
        rows it names in that table. Failing those, the table's own columns it names
        by word, then its tab. Nothing in another table, whatever words it shares."""
        t = self.a.table(ab["table"])
        col = str(ab.get("col") or "")
        about = [x for x in (col_id(t.sheet, col, t.tid), f"row:{t.sheet}!{col}") if col and x in known | ids]
        about += sorted(x for x in th.find(said) if x in ids and th.values[x]["table"] == t.tid)
        about += sorted(f"row:{c}" for c in mind.cells_named(self.a, said)
                        if f"row:{c}" in ids and c.startswith(t.sheet + "!"))
        if not about:
            about = [cid for c in self.a.cols[t.tid] if (cid := col_id(t.sheet, c.header, t.tid)) in known
                     and re.search(r"(?<![\w-])" + re.escape(c.header) + r"(?![\w-])", said)][:3]
        if not about and f"sheet:{t.sheet}" in known:
            about = [f"sheet:{t.sheet}"]
        return about


_TOLD_ORDER = {"exclusion": 0, "rule": 1, "definition": 2, "mapping": 3, "unit": 4, "coverage": 5, "history": 6,
               "gotcha": 7, "goal": 8}


def for_tab(records: list) -> list:
    """What goes in the tab, in the order a reader needs it: what the tab is, then the
    owner's notes (the rules that change answers first), then counted facts that change
    answers, and how the tabs connect (joins, lookups, calculated tabs), then findings,
    other workbooks, open questions and guesses, and last the tabs and columns as they
    were (the baseline for spotting changes). The graph of things (vendors, links between
    them) stays on this machine: the map and the drawing are built from it, and an AI
    reading the tab does better without it."""
    meta = [r for r in records if r.get("record") == "meta"] + [r for r in records if r.get("id") == "f:howto"]
    told = [r for r in records if r.get("record") == "fact" and r.get("source") == "told"]
    told.sort(key=lambda r: (r.get("id") == "f:_build", _TOLD_ORDER.get(r.get("kind"), 9)))
    counted = [r for r in records if r.get("record") == "fact" and r.get("source") == "computed"
               and r.get("id") != "f:howto"]
    counted.sort(key=lambda r: (0 if r.get("kind") == "exclusion" else 1 if r.get("id", "").startswith(("f:hdr", "f:mix"))
                                else 2))
    counted += [r for r in records if r.get("record") == "edge" and _tab_edge(r)]
    insights = [r for r in records if r.get("record") == "insight"]
    insights.sort(key=lambda r: 0 if any(k in (r.get("ref") or "") for k in
                                         ("exclusive", "unmatched", "signflip", "blanks", "formula", "check",
                                          "window:", "offlist:", "onset:"))
                  else 1)
    links = [r for r in records if r.get("record") == "link"]
    other = [r for r in records if r.get("record") == "fact" and r.get("source") not in ("told", "computed", "inferred")]
    opens = [r for r in records if r.get("record") == "open"]
    guesses = [r for r in records if r.get("record") == "fact" and r.get("source") == "inferred"]
    # last: the tabs and columns as they were, so a later check can say what changed since
    baseline = [r for r in records if r.get("record") == "node"
                and (r.get("kind") == "sheet" or str(r.get("id", "")).startswith(("col:", "line:")))]
    return meta + told + counted + insights + links + other + opens + guesses + baseline


def _tab_edge(r: dict) -> bool:
    """A connection between columns or tabs (a join, a lookup, a calculated tab), not
    one between things in the data or between rows of a model."""
    if r.get("kind") in ("joins_on", "derived_from", "looks_up"):
        return True
    return r.get("kind") == "feeds" and all(str(r.get(k) or "").startswith("sheet:") for k in ("from", "to"))


def _an(noun: str) -> str:
    return "an" if noun[:1].lower() in "aeiou" else "a"


# --------------------------------------------------------------------------
def _combine(fp: dict) -> str:
    h = hashlib.sha256()
    for k in sorted(fp):
        h.update(f"{k}:{fp[k]['fp']}".encode())
    return h.hexdigest()[:12]


def _join_qid(j: dict) -> str:
    return "join_" + re.sub(r"[^a-z0-9]+", "_",
                            f"{j['from_table']}_{j['from_col']}_{j['to_table']}".lower())[:40]


def _tname(t) -> str:
    """'Regional Sales (table 2)' when a tab holds more than one table."""
    tid = t.tid.split(":")[-1]
    if "#" in tid:
        return f"{t.sheet} (table {tid.rsplit('#', 1)[1]})"
    return t.sheet


def _short_label(s: str) -> str:
    return s.split("!", 1)[-1][:30]


def _standalone(q) -> str:
    """An open question as a note that stands alone: when its words point at its
    options ('Which of these should stay out?'), the options are named."""
    s = _open_text(q.prompt)
    real = [o["label"] for o in q.options if o.get("id") not in ("not_sure", "type", "skip")]
    if real and re.search(r"\b(?:of|any of|which of|all of)\s+(?:these|those|them)\b|\bthese\s*\?", s, re.I):
        s = s.rstrip() + " (" + "; ".join(real[:4]) + ")"
    return s


def _open_text(prompt: str) -> str:
    """A question as a note: no instructions to the reader, no 'your'."""
    s = re.sub(r"\s*(Pick all that apply|Type (them|what you know)[^.]*|Reply [^.]*)\.", "", prompt or "")
    s = re.sub(r"\b[Yy]our\b", lambda m: "The" if m.group(0)[0] == "Y" else "the", s)
    return s.strip()


_GENERIC = {"has_formulas", "has_derived_tab", "multi_sheets", "multi_files"}
_DATA_SLOT = re.compile(r"\{(values|count|sum|rows)[:}]")
_ROLE_SLOT = re.compile(r"\{(?:role|values|count|sum|rows):(\w+)\}")
_HEDGE = re.compile(r"\b(often|may|usually|can|might|typically)\b", re.I)
_MIDNIGHT_SHARE = 0.05        # a real share of the timed rows within two hours of midnight
# the gotcha evidence checks Composer._evidence runs ('<check>:<role>'); dev/lint_playbooks.py
# accepts exactly these, and a test keeps the two lists the same
EVIDENCE_CHECKS = ("times_near_midnight", "full_discount")
_RATE_WORDS = {"%", "pct", "percent", "rate", "per"}     # a header naming a rate or a share, not an amount


def gotcha_clauses(say: str) -> list:
    """A gotcha template cut at ';' and at sentence ends, each clause a sentence of its own."""
    out = []
    for c in re.split(r";\s+|(?<=[.!?])\s+(?=[A-Z{])", say or ""):
        c = c.strip()
        if c:
            out.append(c[:1].upper() + c[1:])
    return out


_SEP = re.compile(r"(;\s+|(?<=[.!?])\s+(?=[A-Z{]))")
_SUBJECT = re.compile(r"\{(?:role|values|count|sum):(\w+)\}")
_LIST_TAIL = re.compile(r"\{values:\w+\}\s*,?\s*(?:and more|such as|among others)\b", re.I)


def gotcha_parts(say: str) -> list:
    """A gotcha template as runs of clauses of one kind: [(text, from_data)]. A run that
    quotes the data is counted and the rule of thumb next to it is a guess. Clauses of
    one kind stay together with their own punctuation, so 'Those ...' keeps what it
    refers to; a guess after a counted run that does not name its column starts with it."""
    pieces = _SEP.split((say or "").strip())
    runs: list = []
    for k in range(0, len(pieces), 2):
        c = pieces[k].strip()
        if not c:
            continue
        data = bool(_DATA_SLOT.search(c))
        if runs and runs[-1][1] == data:
            runs[-1][0] += pieces[k - 1] + c
        else:
            runs.append([c, data])
    out = []
    for i, (text, data) in enumerate(runs):
        m = _SUBJECT.search(runs[i - 1][0]) if i and not data and not text.startswith("{role:") else None
        if m:
            text = f"About {{role:{m.group(1)}}}: " + (text[:1].lower() + text[1:] if text[1:2].islower()
                                                         else text)
        out.append((text[:1].upper() + text[1:], data))
    return out


def _values_tail(clause: str) -> str:
    """'{values:x} and more' says there are more whether or not there are. The words
    go: the filled list itself says how many more there are, only when it was cut."""
    return re.sub(r"(\{values:\w+\})\s*,?\s+(?:and more|among others)\b", r"\1", clause)


def gotcha_lint(say: str) -> list:
    """Clauses that quote the data and hedge at once (a counted note can't say 'may'),
    or that trail a value list with 'and more', 'such as' or 'among others' (the list
    is what the data holds, so the words after it claim values nobody counted)."""
    return [c for c in gotcha_clauses(say) if _DATA_SLOT.search(c) and (_HEDGE.search(c) or _LIST_TAIL.search(c))]


def _near_midnight(values: list) -> bool:
    """Times of day exist, and a real share of them sit near midnight (where a time
    zone shift moves a row to the next day)."""
    import datetime as _dt
    stamps = [v for v in values if isinstance(v, _dt.datetime)]
    timed = [v for v in stamps if v.time() != _dt.time(0)]
    if not timed or len(timed) < max(5, 0.2 * len(stamps)):
        return False
    near = sum(1 for v in timed if v.hour >= 22 or v.hour < 2)
    return near >= max(2, _MIDNIGHT_SHARE * len(timed))


def _full_discount(t, j: int, header: str, money=(), counts=()) -> bool:
    """Lines are discounted to nothing: a 100% rate, or a discount equal to the line's
    money (one of its money columns, or a money column times one of its count
    columns). One chance match is not evidence: it takes 2 lines, and at least 1 in
    1,000 of the discounted ones. A factor of 1 proves nothing, so it never counts."""
    vals = [v for v in t.column(j) if _num(v) and v > 0]
    if not vals:
        return False
    need = max(2, 0.001 * len(vals))
    if "%" in header or re.search(r"\b(pct|percent)", header, re.I) or max(vals) <= 1:
        top = 1.0 if max(vals) <= 1 else 100.0
        return sum(1 for v in vals if abs(v - top) < 1e-9) >= need
    hits = 0
    for row in t.rows[:50000]:
        d = row[j] if j < len(row) else None
        if not _num(d) or d <= 0:
            continue
        prices = [row[k] for k in money if k < len(row) and _num(row[k]) and row[k] > 0]
        qtys = [row[k] for k in counts if k < len(row) and _num(row[k]) and row[k] > 0 and row[k] != 1]
        if any(abs(p - d) < 0.005 for p in prices) or \
                any(abs(p * q - d) < 0.005 for p in prices if p != 1 for q in qtys):
            hits += 1
            if hits >= need:
                return True
    return False


def _sentence(s: str) -> str:
    s = re.sub(r"\s+", " ", (s or "")).strip()
    s = re.sub(r"(?<!\.)\.\.(?!\.)", ".", s)          # "each.." -> "each.", keep an ellipsis
    s = re.sub(r"\s+([.,;:])", r"\1", s)
    s = re.sub(r":\s*\.$", ".", s)                      # a template whose answer came back empty
    s = re.sub(r":\s*\.\s+", ": ", s)
    s = s.rstrip(" ;,:")
    s = re.sub(r'([.!?])"\.$', r'\1"', s)          # '...at list."' not '...at list.".'
    if s and s[-1] not in ".!?" and not s.endswith(('."', '!"', '?"')):
        s += "."
    return s


# words a description may use beyond its prompt and label: how rows are treated and what structure they
# have, never a new claim about what the thing is ('Loans, grants, gifts' adds three)
_DESC_FRAME = {"leave", "left", "out", "keep", "kept", "apart", "separate", "together", "again", "twice", "double",
               "doubles", "doubled", "once", "real", "never", "always", "include", "included", "includes",
               "exclude", "excluded", "number", "numbers", "sum", "sums", "type", "typed", "ones", "later",
               "earlier", "first", "last", "latest", "newest", "oldest", "copy", "copies", "add", "adds", "added",
               "nothing", "something", "else", "part", "zero", "blank", "new", "old", "name", "names", "code",
               "codes", "treat", "treated", "use", "used", "uses", "mark", "marked", "open", "guess", "sure",
               "skip", "kind", "way", "how", "why", "who", "own", "many", "more", "less", "most", "pair", "pairs",
               "row", "rows", "count", "counts", "counting", "counted", "total", "totals", "each", "every",
               "any", "all", "one", "same", "other", "others", "tab", "tabs", "value", "values", "list", "lists",
               "entry", "entries", "line", "lines", "item", "items", "upload", "uploads", "twin", "twins",
               "cancel", "cancels", "reverse", "reverses", "right", "wrong", "mistake", "mistakes", "fix",
               "true", "false", "yes", "can", "may", "stays", "stay", "come", "comes", "goes", "from", "into",
               "onto", "off", "per", "unit", "units", "rest", "whole", "part", "parts", "only", "just", "still",
               "yet", "now", "then", "note", "notes", "file", "files", "sheet", "sheets", "column", "columns",
               "shown", "show", "shows", "said", "say", "says", "given", "give", "gives", "made", "make",
               "makes", "known", "know", "news", "issue", "issues", "purpose", "reason", "leaving", "counts",
               # verbs, adjectives and adverbs that describe, never a thing the owner would be agreeing exists
               "belong", "belongs", "like", "worth", "look", "looking", "back", "coming", "given", "empty",
               "share", "shares", "shared", "older", "newer", "undo", "undoing", "wrong", "charged", "keyed",
               "agreed", "approved", "split", "single", "someone", "anyone", "nobody", "complete", "working",
               "until", "exist", "exists", "read", "sits", "filled", "above", "below", "under", "over", "money",
               "moves", "move", "moving", "changed", "change", "changes", "replaced", "later", "earlier",
               "after", "before", "already", "another", "different", "same", "separate", "together"}


def desc_adds_claim(desc: str, prompt: str, label: str) -> set:
    """The words of an option's description that claim something the owner was not
    otherwise shown: not in the question, its label or the words any description
    may use for how rows are treated. A description with any is not quoted in a
    note, since a pick of the label never says the owner agreed to them."""
    seen = interview._stems(prompt) | interview._stems(label) | {interview._stem(w) for w in interview._FRAME} \
        | {interview._stem(w) for w in _DESC_FRAME}
    return {w for w in re.findall(r"[a-z]+", str(desc or "").lower()) if len(w) >= 3 and interview._stem(w) not in seen
            and w not in _data_words(desc)}


def _data_words(desc: str) -> set:
    """Words of a description that are the data's own values, not a claim: codes in
    capitals ('SVC-A'), tokens with digits or joined by a hyphen, and names written
    with a capital inside a sentence ('... is Coral Growers')."""
    out = set()
    text = str(desc or "")
    for m in re.finditer(r"[A-Za-z0-9][\w'#&./-]*", text):
        tok = m.group(0)
        before = text[:m.start()].rstrip()
        inside = bool(before) and before[-1] not in ".:;!?(\""
        if (re.search(r"\d", tok) or "-" in tok.strip("-") or (len(tok) >= 2 and tok.isupper())
                or (inside and tok[:1].isupper())):
            out |= set(re.findall(r"[a-z]+", tok.lower()))
    return out


def _names_measure(word: str) -> bool:
    """A scope word that is a measure's name ('Net', 'Gross Margin'): one or two words, the
    first capitalized. A calculation the owner typed ('rebate math') is lowercase."""
    parts = str(word).split()
    return 1 <= len(parts) <= 2 and parts[0][:1].isupper()


def applied_phrases(analysis, qid: str, ans: dict) -> dict:
    """{option id: what the pick did to the counted numbers}, only for picks whose
    rule was applied ('so these 12 rows are left out of every count and total',
    'so Unit Cost is divided by 12 on these 805 rows'); a pick whose rule is not
    applied says nothing of the kind. Each pick says its own rule: its own scope,
    unless the rule applied is out of every count and total."""
    live = list(getattr(analysis, "rules", None) or [])
    if not live:
        return {}
    picked = set(ans.get("options") or [])
    offered = []
    ex = ans.get("exclude") or {}
    if ex.get("col"):
        for oid in ex.get("options") or []:
            offered.append((oid, rules.Rule("exclude", ex["table"],
                                            [{"col": ex["col"], "op": "in", "values": [str(v) for v in ex["values"]]}],
                                            scope=list(ex.get("scope") or []))))
    sc = ans.get("scale") or {}
    by = rules.typed_count(ans.get("text") or "") if sc.get("option") else 0.0
    if by and sc.get("col"):
        # the price the question asked about first: the same price on the other tabs it named is said with it
        offered.append((sc["option"], rules.Rule("scale", sc["table"], list(sc.get("predicate") or []),
                                                 {"col": sc["col"], "by": by})))
    for d in ans.get("rules") or []:
        if d.get("option") and not d.get("infer") and d.get("kind") in rules.KINDS:
            offered.append((d["option"], rules.Rule.from_dict(d)))
    out: dict = {}
    for oid, r in offered:
        if oid not in picked or oid in out:
            continue
        got = _live_rule(analysis, live, r)
        if got is None:
            continue
        n = rules.effect(analysis, got)["rows"]
        these = f"these {n:,} {plural('row', n)}" if n else "these rows"
        if got.kind in ("exclude", "filter"):
            # each pick says the scope it named itself: two picks may have made one rule out of two totals;
            # a rule out of every count and total (a pick that says what the rows are) says so
            own = [] if not got.scope else (list(r.scope) or list(got.scope))
            words = set(rules.word_scope(analysis, got)) | set(rules.word_scope(analysis, r))
            # a scope word that names a measure ('Net', worked out from other columns) is a total like a
            # column's; words the owner typed for one calculation ('rebate math') keep the rows for it only
            calc = [w for w in own if w in words and not _names_measure(w)]
            tots = [w for w in own if w not in calc]
            where_ = ((f"{interview._join(tots)} totals" + (f" and {interview._join(calc)}" if calc else ""))
                      if tots else f"{interview._join(calc)} only" if calc else "every count and total")
            out[oid] = f"so {these} are left out of {where_}" if got.kind == "exclude" else \
                f"so only {these} are counted in {where_}"
        elif got.kind == "map":
            # a map that pairs each value with its own match (names with codes, old values with new ones)
            # makes them as many as it has matches, never one
            groups = [g for g, _vals in rules.map_pairs(analysis, got)]
            many = (f"{len(groups)} ({interview._join(groups)})" if len(groups) <= 5 else f"{len(groups)}") \
                if len(groups) > 1 else "one"
            out[oid] = f"so these values count as {many} in every count and total by {got.values.get('col')}"
        elif got.kind == "scale":
            col = got.values.get("col")
            follows = list(dict.fromkeys(x.values.get("col") for x in live if x.kind == "scale" and x.confirmed
                                         and x.table == got.table and x.values.get("follows") == col))
            also = [x for x in live if x.kind == "scale" and x.confirmed and x.table != got.table
                    and x.source == got.source and not x.values.get("follows")]
            said = f"so {col} is divided by {fmt_num(float(got.values.get('by') or 0))} on {these}"
            if follows:
                said += (f", and so {'is' if len(follows) == 1 else 'are'} {interview._join(follows)}, worked out "
                         "from it")
            if also:
                tabs = list(dict.fromkeys(analysis.table(x.table).sheet for x in also))
                said += f", and the same on {interview._join(tabs)}"
            out[oid] = said
    return out


def _live_rule(analysis, live: list, offered):
    """The rule a pick offered, as it was applied: the same kind on the same table,
    naming the same values (or, merged with another rule on the same rows, the
    same rows); the one with the same scope first, then one for every count and
    total, then one whose scope takes the pick's in."""
    same = [x for x in live if _same_rule(x, offered)]
    if not same and offered.kind in ("exclude", "filter"):
        rows = rules.rows_of(analysis, offered)
        same = [x for x in live if x.kind == offered.kind and x.table == offered.table and rows
                and rules.rows_of(analysis, x) == rows]
    if not same:
        return None
    want = list(offered.scope or [])
    for pick in (lambda x: list(x.scope or []) == want, lambda x: not x.scope,
                 lambda x: set(want) <= set(x.scope or [])):
        got = next((x for x in same if pick(x)), None)
        if got is not None:
            return got
    return same[0]


def _same_rule(applied, offered) -> bool:
    """The rule a pick offered, as applied: the same kind on the same table and
    columns, with every value it named (a map may have added spellings). Scope is
    matched by _live_rule, which prefers the applied rule with the pick's own."""
    if applied.kind != offered.kind or applied.table != offered.table:
        return False
    if applied.kind == "map":
        return applied.values.get("col") == offered.values.get("col") and \
            {norm_key(v) for c in offered.predicate for v in c.get("values") or []} <= \
            {norm_key(v) for c in applied.predicate for v in c.get("values") or []}
    if applied.kind == "scale" and applied.values.get("col") != offered.values.get("col"):
        return False
    if [c["col"] for c in applied.predicate] != [c["col"] for c in offered.predicate]:
        return False
    return all({norm_key(v) for v in b.get("values") or []} <= {norm_key(v) for v in a.get("values") or []}
               for a, b in zip(applied.predicate, offered.predicate))


# an instruction to the owner leading a description ('Type how many are in one; ...', 'Say why: ...')
_LEAD_INSTRUCTION = re.compile(r"^\s*(?:type|say|skip\s+it|tell\s+me|add)\b[^;:.]*(?:[;:]|\.(?=\s|$))\s*", re.I)
_TAIL_INSTRUCTION = re.compile(r"\s*\((?:type|say)\b[^()]*\)\s*$", re.I)


def _without_instruction(desc: str) -> str:
    """An option's description without the words that tell the owner what to type
    ('Type which column and how', 'Type the unit; ...', '... (type why)'): those
    are the tool's instructions to the owner, never something the owner said.
    Only a leading or closing instruction goes; the rest is kept as shown."""
    d = str(desc or "").strip()
    m = _LEAD_INSTRUCTION.match(d)
    if m:
        d = d[m.end():].strip()
    elif re.match(r"(?i)\s*(?:type|say|skip\s+it|tell\s+me)\b", d):
        return ""                         # all of it is an instruction ('Type what it is, if you like')
    d = _TAIL_INSTRUCTION.sub("", d).strip()
    return d[:1].upper() + d[1:] if m and d else d


# a curated sentence that already says what the pick does to the counted numbers takes no tail
_SAYS_TREATMENT = re.compile(r"\bleft out\b|\bcounts? as one\b|\bcounted as one\b|"
                             r"\bcounts? as (?:its|their)\b", re.I)
# the values a description names by code ('SVC-X', 'AB123'): a pick among several that each name their own
# values is said pick by pick, never as the question's whole count
_CODE_WORD = re.compile(r"(?<![\w-])(?=[A-Za-z0-9-]*[A-Za-z])(?=[A-Za-z0-9-]*[\d-])(?=[A-Za-z0-9-]*[A-Z\d])"
                        r"[A-Za-z0-9][A-Za-z0-9-]*[A-Za-z0-9](?![\w-])|(?<![\w-])[A-Z]{2,}(?![\w-])")


def _desc_codes(desc: str) -> set:
    return {m.group(0).upper() for m in _CODE_WORD.finditer(str(desc or ""))}


def _tail_said(desc: str, tail: str) -> bool:
    """The description the owner saw already says what the tail would: the same
    treatment of the same totals ('... out of every count and total')."""
    if not desc or not tail:
        return False
    m = re.search(r"left out of (.+)$|counted in (.+)$", tail)
    where_ = (m.group(1) or m.group(2)) if m else ""
    low = desc.lower()
    return bool(where_ and where_.lower() in low and re.search(r"\bout\b|\bonly\b|\bcount", low)) or \
        bool(_SAYS_TREATMENT.search(desc) and "count" in tail and " as " in tail)


def _new_words(desc: str, stmt: str, label: str = "") -> bool:
    """The description names something the curated sentence does not: a value
    ('SVC-X') or a word that is more than how rows are treated."""
    if not desc:
        return False
    low = stmt.lower()
    if any(c.lower() not in low for c in _desc_codes(desc)):
        return True
    seen = interview._stems(stmt) | interview._stems(label) | {interview._stem(w) for w in interview._FRAME} \
        | {interview._stem(w) for w in _DESC_FRAME}
    return any(interview._stem(w) not in seen for w in re.findall(r"[a-z]+", desc.lower()) if len(w) >= 3)


def _curated(stmt: str, desc: str, tail: str, headers=(), label: str = "") -> str:
    """A pick's curated sentence with what the owner saw under the pick and what the
    pick did, right after the pick's own clause and before any evidence sentence
    the statement goes on with. A description that names more than the sentence
    ('Transfers or bookkeeping entries', the codes it lists) is quoted there; one
    that says what the pick does to the rows ('Leave the 55 plain-number copies out
    of every count and total') names them in the owner's view, in place of a tail
    that could only say 'these rows'."""
    spans = privacy.sentence_spans(stmt, headers)
    cut = spans[0][1] if spans else len(stmt)
    head, rest = stmt[:cut].rstrip(), stmt[cut:].strip()
    head = head[:-1] if head.endswith(".") else head
    says = bool(tail) and _tail_said(desc, tail)
    # the sentence itself already says what the pick does ('... come out of every count and total')
    stmt_says = bool(_SAYS_TREATMENT.search(stmt)) or (bool(tail) and _tail_said(stmt, tail)) \
        or (" is divided by " in tail and bool(re.search(r"\bdivided by\b", stmt)))
    if desc and (_new_words(desc, stmt, label) or (says and not stmt_says)):
        head += f" ({_said(desc)})"
    if tail and not says and not stmt_says:
        head += f", {tail}"
    return _sentence(head) + (" " + rest if rest else "")


# a sentence that says what one thing is, in the '<code> = ...' form: two of them are two notes
_DEFINES = re.compile(r"^\W*[\w.&/-]{1,24}\s*(?:=|:)\s*\S")


def _broken(text: str, said: list) -> bool:
    """The owner put a line break between two of these sentences."""
    at = 0
    for a, b in zip(said, said[1:]):
        i = text.find(a, at)
        j = text.find(b, i + len(a)) if i >= 0 else -1
        if i < 0 or j < 0:
            return True
        if "\n" in text[i + len(a):j]:
            return True
        at = j
    return False


def _answer_notes(ans: dict, fact: dict, env, headers=(), applied: dict | None = None) -> list:
    """[(statement, the typed sentence it carries)] for one answer. One pick: its
    curated sentence when every word of it was on screen, with the description the
    owner saw under the pick when that says more, what the pick did to the counted
    numbers, and the owner's typed words after it. Otherwise the question with the
    owner's picks and words verbatim: several picks get a first note naming them all
    (without the question's full count when each pick names its own values), then
    one note per pick, each with the description the owner was shown under it and
    what its rule did. Up to three typed sentences stay in the note that carries the
    question's evidence; more, and each later one is a note of its own, and a doubt
    or a business term always is, so it never spreads to another."""
    opts = [o for o in ans.get("options") or [] if o != "not_sure"]
    labels = list(ans.get("labels") or [])
    text = (ans.get("text") or "").strip()
    per_option = fact.get("statements") or {}
    descs = ans.get("descs") or {}
    applied = applied or {}
    if text and opts == ["type"]:
        labels, opts = [], []             # "I'll type them" only says the words follow; the words are the answer
    # labels come in the question's order, as the descriptions do
    order = [k for k in descs if k in opts] if len([k for k in descs if k in opts]) == len(labels) else opts
    lab_of = dict(zip(order, labels))
    picked = [o for o in order if o in lab_of]
    prompt_raw = ans.get("prompt", "")
    tool_voice = ans.get("kind") in ("goal", "build")

    def shown(oid) -> str:
        """The description the owner saw under a pick, less any typing instruction; a goal or build pick's is
        the tool's pitch in its own voice, never quoted as the owner's."""
        if tool_voice:
            return ""
        d = _without_instruction(str(descs.get(oid) or "").strip()).rstrip(". ")
        lab = lab_of.get(oid, "")
        return "" if not d or d.lower() == lab.rstrip(". ").lower() else d

    said = _said_sentences(text, headers) if text else []
    if ans.get("codes"):          # a dossier: '3 = sent back, 5 = kept' is one note per code
        said = [piece for s in said for piece in rules.code_pieces(s, ans["codes"])]
    # a sentence about a deal that also says how to treat rows: the treatment is a note of its own
    amounts = getattr(env, "amounts", None) if env is not None else None
    parts = [(piece, n, term) for n, s in enumerate(said) for piece, term in privacy.term_parts(s, amounts)]
    said = note_groups([(piece, n) for piece, n, _term in parts])
    terms = {piece for piece, _n, term in parts if term}
    # up to three typed sentences stay together with the question's evidence, unless one is a doubt, a
    # business term or a thing's own meaning ('S1 = new lead. S2 = lost'), or a line break keeps them apart
    if 1 < len(said) <= 3 and not ans.get("codes") and not any(privacy.DOUBT.search(s) for s in said) \
            and not any(s in terms or privacy.is_commercial(s, amounts) for s in said) \
            and sum(1 for s in said if _DEFINES.match(s)) < 2 and not _broken(text, said):
        said = [" ".join(said)]
    rest = list(said)
    prompt = _open_text(prompt_raw)
    asked = f"Asked {interview._quoted(prompt)}" if prompt else f"On {_said(ans.get('header') or 'this question')}"
    on = _said(ans.get("header") or "this question")

    def wrote(note: list):
        if rest:
            note[0] = note[0].rstrip(".") + "; the owner wrote: " + interview._quoted(rest[0])
            note[1] = rest.pop(0)
    out: list = []
    if len(opts) == 1 and not ans.get("accepted") and opts[0] in per_option:
        stmt = _sentence(interview.fill(per_option[opts[0]], env, ans))
        desc = shown(opts[0])
        if stmt and not interview.unseen_words(stmt, prompt_raw, " ".join(labels), desc):
            note = [_curated(stmt, desc, applied.get(opts[0], ""), headers, " ".join(labels)), "", False]
            wrote(note)
            out.append(note)
    if not out:
        rec = ans.get("recommended_label", "") if ans.get("accepted") else ""
        multi = len(picked) > 1
        # several picks, each naming its own values ('Not real items: SVC-...' and 'Replaced codes: AB123 ->
        # ...'): no note says the question's whole count was either
        codes = [_desc_codes(shown(o)) for o in picked] if multi else []
        disjoint = multi and all(codes) and all(not (a & b) for i, a in enumerate(codes) for b in codes[i + 1:])

        def clause(oid):
            lab = lab_of.get(oid, "")
            verb = "accepted the recommended" if rec and lab == rec else "picked"
            if multi:
                verb = "one of the owner's picks was"
            desc = shown(oid)
            tail = applied.get(oid, "")
            return f"{verb} {_said(lab)}" + (f" ({_said(desc)})" if desc else "") + \
                (f", {tail}" if tail and not _tail_said(desc, tail) else "")
        if multi:
            # several picks: one note names them all, so no single pick reads as the whole answer
            lead = f"On {on}" if disjoint else asked
            out.append([f"{lead}, the owner picked " + interview._join([_said(lab_of[o]) for o in picked]), "", True])
        for oid in picked:
            # a pick of several: its curated sentence when every word of it was on screen, unless each pick
            # names its own values and the sentence would speak for the question's whole count
            cur = per_option.get(oid) if multi and not disjoint else None
            stmt = _sentence(interview.fill(cur, env, ans)) if cur else ""
            if stmt and not interview.unseen_words(stmt, prompt_raw, lab_of[oid], shown(oid)):
                out.append([_curated(stmt, shown(oid), applied.get(oid, ""), headers, lab_of[oid]), "", False])
                continue
            lead = f"On {on}, " if multi else (f"{asked}, the owner " if not out else f"On {on}, the owner also ")
            out.append([lead + clause(oid), "", True])
        if ans.get("not_sure") and text:
            lead = f"{asked}, the owner " if not out else f"On {on}, the owner also "
            out.append([lead + f"picked {_said(interview.NOT_SURE['label'])}", "", True])
        if rest and out and out[0][2] and (len(out) == 1 or multi):
            wrote(out[0])
        elif rest and not out:
            out.append([f"{asked}, the owner wrote: " + interview._quoted(rest[0]), rest[0], True])
            rest.pop(0)
    notes = [(_ends(s) if framed else s, carried) for s, carried, framed in out]
    notes += [(f"On {on}, the owner also wrote: {interview._quoted(s)}", s) for s in rest]
    return notes


_INPUT = re.compile(r"(?:^|;\s+|:\s+)([^;:]{2,80}?) \(([^()]*![A-Z]{1,3}\d{1,7})\) = ([^;]+)")
# inputs that share a note, shown as one clause: 'A and B (Sheet!B25 and B26) = 1 and 2 ("note")'
_INPUT_GROUP = re.compile(r"(?:^|;\s+|:\s+)([^;:]{2,200}?) \(([^()!;]*![A-Z]{1,3}\d{1,7}"
                          r"(?:(?:,\s+|,?\s+and\s+)(?:[^()!;,]*!)?[A-Z]{1,3}\d{1,7})+)\) = ([^;]+)")
_LIST_SPLIT = re.compile(r",\s+(?:and\s+)?|\s+and\s+")


def _shown_inputs(ans: dict) -> list:
    """[(label, cell, clause)] for every input an inputs readback showed, one per
    input: a clause of its own as shown, and an input shown in a clause with
    others that share its note ('A and B (Sheet!B25 and B26) = 1 and 2 ("note")')
    as its own part of that clause ('B (Sheet!B26) = 2 ("note")'), its label taken
    from the question's inputs list when it has one."""
    prompt = ans.get("prompt") or ""
    end = re.compile(r"\.\s+[^.]*\?\s*$")
    out = []
    for m in _INPUT.finditer(prompt):
        if not _LIST_SPLIT.search(m.group(2)):
            out.append((m.start(), m.group(1).strip(), m.group(2), end.sub("", m.group(0).lstrip(";: ").strip())))
    by_cell = {str(x.get("cell")): str(x.get("label")) for x in ans.get("inputs") or [] if isinstance(x, dict)}
    for m in _INPUT_GROUP.finditer(prompt):
        whole = end.sub("", m.group(0).lstrip(";: ").strip())
        cells, sheet = [], ""
        for c in _LIST_SPLIT.split(m.group(2).strip()):
            if "!" in c:
                sheet = c.rsplit("!", 1)[0]
            cells.append(c if "!" in c else f"{sheet}!{c}")
        rest = end.sub("", m.group(3).strip())
        got = re.match(r'(.*?) \("(.*)"\)\s*$', rest)
        values = _LIST_SPLIT.split(got.group(1)) if got else []
        names = [by_cell.get(c) for c in cells]
        if not all(names):
            split = _LIST_SPLIT.split(m.group(1).strip())
            names = split if len(split) == len(cells) else []
        if not names:
            continue
        for k, (label, cell) in enumerate(zip(names, cells)):
            clause = (f'{label} ({cell}) = {values[k]} ("{got.group(2)}")' if got and len(values) == len(cells)
                      else f"{label} ({cell}), one of {whole}")
            out.append((m.start(), label, cell, clause))
    return [x[1:] for x in sorted(out, key=lambda x: x[0])]
_RIGHT = re.compile(r"\b(?:right|correct|ok|okay|fine|good|accurate|yes)\b", re.I)
_WRONG = re.compile(r"\b(?:not|wrong|incorrect|isn'?t|aren'?t|except|but|should\s+be|off|no)\b", re.I)


def _input_notes(ans: dict, headers=()) -> list:
    """A typed reply to a readback of several inputs ('label (Sheet!B4) = value
    ...; ...'), split per input it names: each input a sentence says is right
    (and nothing in it says otherwise) gets a note of its own, the input as the
    question showed it and the owner's words; inputs it does not name stay open.
    [] when the answer is not such a reply."""
    text = (ans.get("text") or "").strip()
    if not text or [o for o in ans.get("options") or [] if o != "not_sure"]:
        return []
    shown = _shown_inputs(ans)
    if len(shown) < 2:
        return []
    def says(label: str, cell: str, s: str) -> bool:
        """The sentence names the input: its label, the label's words before an aside ('UP' for 'UP (monthly
        uplift)'), in capitals as written when the label is in capitals, or its cell."""
        head = re.split(r"\s*\(", label, maxsplit=1)[0].strip()
        for name in dict.fromkeys([label, head]):
            if len(name) < 2:
                continue
            flags = 0 if name.isupper() else re.I
            if re.search(r"(?<![\w])" + re.escape(name) + r"(?![\w])", s, flags):
                return True
        return bool(re.search(r"(?<![\w!])" + re.escape(cell.split("!")[-1]) + r"(?![\w])", s))
    out, other = [], []
    for s in _said_sentences(text, headers):
        named = [(label, cell, clause) for label, cell, clause in shown if says(label, cell, s)]
        if not named or not _RIGHT.search(s) or _WRONG.search(s) or privacy.DOUBT.search(s):
            other.append(s)
            continue
        for _label, _cell, clause in named:
            out.append((f"{clause.rstrip('. ')}, right as read, per the owner, who wrote: {interview._quoted(s)}", s))
    if not out:
        return []
    on = _said(ans.get("header") or "this question")
    return out + [(f"On {on}, the owner also wrote: {interview._quoted(s)}", s) for s in other]


def _member_notes(ans: dict, fact: dict, env, headers=()) -> tuple:
    """([(statement, typed sentence)], [about or None]) for a batch of small
    findings asked as one: one note per pick, about that pick's finding; one note
    per typed sentence, about the finding whose values it names (the first one it
    names), else about the batch. The words are the owner's, as typed."""
    members = ans.get("member_about") or {}
    descs = ans.get("descs") or {}
    picked = [o for o in ans.get("options") or [] if o != "not_sure"]
    order = [k for k in descs if k in picked]
    labels = dict(zip(order, ans.get("labels") or [])) if len(order) == len(ans.get("labels") or []) else {}
    notes, abouts = [], []
    for oid in order:
        one = dict(ans, options=[oid], labels=[labels.get(oid, "")], descs={oid: descs.get(oid, "")}, text="",
                   not_sure=False)
        got = _answer_notes(one, fact, env, headers)
        notes += got
        abouts += [members.get(oid)] * len(got)
    text = (ans.get("text") or "").strip()
    if text:
        said = note_groups([(s, n) for n, s in enumerate(_said_sentences(text, headers))])
        prompt = _open_text(ans.get("prompt", ""))
        on = _said(ans.get("header") or "this question")
        for k, s in enumerate(said):
            ab = next((a for a in members.values() if any(
                rules._value_pattern(str(v)).search(s) for v in (a or {}).get("values") or [] if len(str(v)) >= 2)),
                None)
            lead = (f"Asked {interview._quoted(prompt)}, the owner wrote: " if not notes and k == 0
                    else f"On {on}, the owner also wrote: ")
            notes.append((lead + interview._quoted(s), s))
            abouts.append(ab)
    return notes, abouts


def _with_marks(stmt: str, marks: list) -> str:
    """A counted note with the owner's rules that would change it and are not applied."""
    return stmt.rstrip(". ") + ". " + " ".join(marks) if marks else stmt


def _said(label: str) -> str:
    return '"' + str(label).replace('"', "'").strip() + '"'


def _ends(s: str) -> str:
    return s if s.endswith((".", "!", "?", '."', '!"', '?"')) else s + "."


def _without_private(text: str, names, headers, amounts=None) -> tuple:
    """(the owner's words with only the private sentences taken out, the rest
    byte for byte so each line and sentence stays its own note; [(sentence, reason)])."""
    kept, private, _ = privacy.screen(text, names, headers, amounts)
    return kept, private


def _said_sentences(text: str, headers=(), cut_before_lowercase: bool = False) -> list:
    """The owner's typed words cut into sentences, each kept exactly as typed: one
    cut everywhere (privacy.said_sentences), so the notes, the closer, the private
    filter and the rule reader never split 'Adj. Cost' or 'approx.'."""
    return privacy.said_sentences(text, headers, cut_before_lowercase)


def note_groups(pieces: list) -> list:
    """The typed sentences as notes: a sentence that points back to the one before it
    ('On their rows, ...', 'That is not a typo') stays in that note, so its subject
    is kept; a doubt stays a note of its own. pieces: [(text, sentence number)]; a
    piece cut out of the sentence before (a treatment clause taken out of a deal)
    stays apart. Each note is its sentences as typed, joined by a space."""
    out: list = []
    last = None
    for s, n in pieces:
        if out and n != last and privacy.continues(out[-1], s):
            out[-1] = out[-1] + " " + s
        else:
            out.append(s)
        last = n
    return out


def _column_statement(c, t, role: dict, inferred: bool = False, *, ruled=None, mixed_units: bool = False,
                      latest=None, unit_change: str | None = None, unit_group: list | None = None,
                      absolute: tuple | None = None) -> tuple:
    """A column's counted note. A role code only guessed from the table's shape (a
    debit and credit pair) is not said as what the column reads as. A rate or a
    share (%, pct, rate, per in the header) is never dollars and never summed, nor
    is an ID, a code or a count of mixed units. ruled: the column over the rows the
    owner's rules keep, whose sum is shown next to the sum over all rows. latest: a
    stock column of a snapshot panel, (the latest date in words, its sum there),
    said instead of a sum across dates. unit_change: the date the owner said the
    column's unit changed at; it is never summed across it."""
    kind_word = {"identifier": "an ID", "metric": "a number", "dimension": "a category",
                 "temporal": "a date", "flag": "a yes or no flag", "text": "free text"}.get(c.semantic,
                                                                                         "a column")
    if c.codes:
        kind_word = "a code"
    parts = [f"{c.header} on {t.sheet} is {kind_word}"]
    if role.get("label") and role.get("label").lower() != c.header.lower() and not inferred:
        parts[0] += f" (reads as {role['label'].lower()})"
    detail = []
    lines = [f"type: {c.type}", f"semantic: {c.semantic}", f"rows: {c.count + c.nulls}",
             f"distinct: {c.distinct}", f"blank_rate: {round(c.blank_rate, 4)}"]
    if c.type == "number" and c.min is not None:
        rate = bool(_RATE_WORDS & set(detect.norm_header(c.header).replace("%", " % ").split()))
        money = role.get("unit") == "currency" and not rate
        f = fmt_money if money else (lambda x: fmt_num(round(float(x), 2)))
        detail.append(f"from {f(float(c.min))} to {f(float(c.max))}")
        if unit_change:
            detail.append(f"not summed: mixed units before and after {unit_change}, per the owner")
        elif unit_group:
            detail.append(f"not summed: the rows where {interview._join(unit_group)} are in another unit, per "
                          "the owner")
        elif latest is not None and not rate and not mixed_units:
            detail.append(f"summing to {f(latest[1])} on the latest {latest[0]}, never summed across dates")
        elif absolute is not None and not rate and not mixed_units:
            # both signs mean the same, per the owner: summed without the sign
            detail.append(f"summing to {f(absolute[0])} as absolute values, both signs meaning the same per the owner"
                          + (f" ({f(absolute[1])} after the owner's rules)" if round(absolute[1], 2) != round(absolute[0], 2)
                             else ""))
        elif not rate and not mixed_units and not c.codes and c.semantic != "identifier" \
                and (role.get("additive") or (money and c.semantic == "metric")):
            if ruled is not None and round(ruled.sum, 2) != round(c.sum, 2):
                detail.append(f"summing to {f(c.sum)} over all rows ({f(ruled.sum)} after the owner's rules)")
            else:
                detail.append(f"summing to {f(c.sum)}")
        lines += [f"min: {c.min}", f"max: {c.max}", f"sum: {round(c.sum, 4)}", f"negatives: {c.negatives}"]
    elif c.type == "date" and c.min is not None:
        detail.append(f"from {c.min.isoformat()[:10]} to {c.max.isoformat()[:10]}")
        lines += [f"min: {c.min.isoformat()[:10]}", f"max: {c.max.isoformat()[:10]}"]
    else:
        detail.append(f"{c.distinct:,} distinct values")
        if c.distinct <= 50 and not c.sensitive and c.semantic != "identifier":
            lines.append("values: " + " | ".join(str(k)[:40] for k, _ in c.top[:50]))
    if c.blank_rate >= 0.02:
        detail.append(f"blank on {pct(c.blank_rate)} of rows")
    stmt = parts[0] + ", " + ", ".join(detail) + "."
    return stmt, "\n".join(lines)
