"""Orchestrates the deterministic pass over one or more files: load, find
tables, profile columns, keys, dependencies, joins, formulas, archetype, and
the computed insights. The result is what every other module works from.
"""
from __future__ import annotations

import os
import re
from collections import Counter

from . import brainzip
from . import detect as detect_mod
from . import formulas as formulas_mod
from . import profile as profile_mod
from . import recipes as recipes_mod
from . import tables as tables_mod
from . import workbook
from .profile import _is_num
from .analyze_helpers import (  # noqa: F401 (other modules import some from here)
    _a, _ADJUST_HEADER, _AMOUNT_NOT, _AS_RECORDED, BALANCED, _blank, _BY_LABEL, _BY_LABEL_CALL,
    _CLOSING_WORDS, _CODE_WORDS, CONFORM_ALL, CONFORM_AT, CONFORM_LINES, CONFORM_MONTHS, CONFORM_OFF,
    CONTRA_LINES, CONTRA_LINES_RARE, CONTRA_MIN, CONTRA_RARE, CONTRA_SIDE, _cycle, _cycle_by_rows,
    _date_step, _day_words, _END_HEAD, _FLOW, _forms, _formula_counts, GROUP_CATS, GROUP_MAX, GROUP_MIN,
    _group_noun, _group_word, _grouping, _iso_of, _jaccard, _join_words, _LEVEL_ROW, _listed, _median,
    _metric, _mon_words, _money_col, _month, _MONTHS, _move_words, _none_worth, _NOT_LEVEL, _NOTE_HEADER,
    ODD_ROWS, ONSET_CHANCE, ONSET_LINES, ONSET_MONTHS, ONSET_ROWS, ONSET_STEADY, _onset_words,
    OPENING_TIMES, _OPENING_WORDS, _pair_why, _PERCENT_HEAD, _period_kind, _period_words, _plural,
    _prefix_families, PREFIX_SHARE, _PRICE_HEADER, _range_words, _rate_words, RATIO_GROUPS, RATIO_MAX,
    RATIO_SHARE, SNAP_PER_DATE, SNAP_REPEAT, SNAP_ROWS, SNAP_UNIQUE, _START_HEAD, _stem, _STOCK,
    _TERMS_HEAD, TERMS_NEAR_END, TERMS_SHOWN, TERMS_TEXT, _title_day, _title_period, UNIT_EXACT, UNIT_QTY,
    UNIT_RATE, UNPAID_IDS, _value_words, WINDOW_LINES, WINDOW_SHARE, _window_words, _written,
    _written_key, _year_of,
)
from .analyze_rows import RowFacts


class Analysis(RowFacts):
    def __init__(self, paths: list, playbooks: dict | None = None):
        self.paths = [os.path.abspath(p) for p in paths]
        self.playbooks = playbooks if playbooks is not None else detect_mod.load_playbooks()
        self.books = [workbook.load(p) for p in self.paths]
        self.tables: list = []
        self.tables_by_book: dict = {}
        self.cols: dict = {}
        self.file_of: dict = {}
        self.keys: dict = {}
        self.fds: dict = {}
        self.formulas: dict = {}
        for b in self.books:
            by_sheet = {}
            for s in b.data_sheets():
                ts = tables_mod.find_tables(s)
                if len(self.books) > 1:
                    for t in ts:
                        t.tid = f"{_stem(b.path)}:{t.tid}"
                by_sheet[s.name] = ts
                for t in ts:
                    self.tables.append(t)
                    self.file_of[t.tid] = b.path
                    fcount = _formula_counts(s, t)
                    self.cols[t.tid] = [profile_mod.profile_column(t, j, fcount.get(j, 0))
                                        for j in range(len(t.headers))]
            self.tables_by_book[b.path] = by_sheet
            self.formulas[b.path] = formulas_mod.analyze(b, by_sheet) if b.kind == "xlsx" else {}
        self.derived = set()
        # (table, column) -> the old values another value takes over from at one date, see _boundaries
        self.handoffs: dict = {}
        for path, fa in self.formulas.items():
            for d in fa.get("derived", []):
                for t in self.tables:
                    if self.file_of[t.tid] == path and t.sheet == d["sheet"]:
                        self.derived.add(t.tid)
        self.key_extra: dict = {}          # table -> rows over one per value of a key that holds on 99% of rows
        for t in self.tables:
            ki = profile_mod.key_info(t, self.cols[t.tid])
            self.keys[t.tid] = ki["cols"]
            if ki["extra"]:
                self.key_extra[t.tid] = ki["extra"]
            self.fds[t.tid] = profile_mod.functional_deps(t, self.cols[t.tid])
        # a tab calculated from another tab repeats its values; matching into it says nothing new
        wide = {t.tid for t in self.tables if t.wide}
        self.joins = [j for j in profile_mod.find_joins(self.tables, self.cols, self.file_of)
                      if j["from_table"] not in self.derived and j["to_table"] not in self.derived
                      and j["from_table"] not in wide and j["to_table"] not in wide]
        for j in self.joins:          # ID columns before descriptions, everywhere joins are listed
            fc = self.col(j["from_table"], j["from_col"])
            j["id_like"] = bool(fc and fc.semantic == "identifier")
        self.joins.sort(key=lambda j: (not j["id_like"], -j["rows_matched"]))
        self.main_table = self._pick_main()
        self.detection = detect_mod.detect(self, self.playbooks)
        self.playbook = self.playbooks.get(self.detection["archetype"], {"id": "generic", "roles": {}})
        # what a row is and how the dates fall, settled by code as counted facts (see _grain)
        self.grain_facts: list = []
        self.snapshots: dict = {}          # table -> its snapshot date column, latest date and stock columns
        self.balanced: dict = {}           # table -> the ID whose lines net to zero, and how
        self.cadence: dict = {}            # (table, date column) -> the cycle its dates follow
        self.title_dates: dict = {}        # table -> the as-of date or period end its title names
        self.title_period: dict = {}       # table -> {said, end, kind}: the period a title names, rows past it or not
        self._grain()
        # per-key counted facts from a table of dated terms, written into every brain (see _terms_facts)
        self.terms: list = []
        ctx = recipes_mod.Ctx(self, self.detection, self.playbook)
        self.exclusions: dict = {}
        self.rules: list = []              # the owner's confirmed rules every counted number goes through
        self.unapplied_rules: list = []    # rules the owner wrote that are not applied, with their counts
        self.net_prices: set = set()       # tables where the owner said price comparisons are after credits
        self.mixed_units: dict = {}        # (table, column) -> the date the owner said its unit changed at
        self._ctx = ctx
        self._structural = self._structural_insights()
        self.insights = self._dedupe(recipes_mod.run_all(ctx, self.playbook.get("insights", []))
                                     + self._structural)

    @staticmethod
    def _dedupe(insights: list) -> list:
        from .findings import one_is_one
        seen, out = set(), []
        for i in insights:
            k = one_is_one(i["statement"])          # '1 row', never '1 rows'
            if k in seen:
                continue
            seen.add(k)
            out.append(i if k == i["statement"] else dict(i, statement=k))
        return out

    def apply_answers(self, answers: dict) -> list:
        """The owner's confirmed rules ('Q7 transfers never count') re-scope every
        counted number: the recipes run again over the ruled rows, and findings
        counted from the whole table say they came before the rules. A rule the
        owner wrote but did not confirm marks each number it would change. Returns
        the values left out of every total, (sheet, column, values), for the lines
        that say what was applied."""
        from . import rules
        from .findings import unit_changed
        # a money column the owner said changed unit at a date is never summed across it (see recipes._mixed_note)
        self.mixed_units = unit_changed(self, answers)
        applied = rules.confirmed(self, answers)
        fees = rules.not_items(self, answers)
        self.rules = applied
        self.unapplied_rules = rules.unapplied(self, answers, applied)
        # 'net them' everywhere: price comparisons still read the lines before credits, and say so
        self.net_prices = {(a.get("about") or {}).get("table") for k, a in (answers or {}).items()
                           if k.startswith("follow_net_") and isinstance(a, dict) and "net" in (a.get("options") or [])}
        self.exclusions = rules.exclusions_of(self, applied)
        self._ctx.set_rules(applied, fees)
        recomputed = self._retire_repeats(recipes_mod.run_all(self._ctx, self.playbook.get("insights", [])), answers)
        structural = [self._before_rules(i, applied) for i in self._structural]
        # the problem-cell ledger counts what the owner has answered; a row the owner called a plan is shown
        # with and without it
        structural = [(self._ledger(i["files"][0], self.formulas.get(i["files"][0]) or {}, answers) or i)
                      if i.get("recipe") == "model:ledger" and i.get("files") else i for i in structural]
        structural += self._plan_effects(answers)
        self.insights = [self._not_applied(i) for i in self._dedupe(recomputed + structural
                                                                    + self._derived_totals(answers))]
        out = []
        for (tid, j), vals in self.exclusions.items():
            t = self.table(tid)
            shown = {}
            for v in t.column(j):          # the value as written, not its lowercase key
                k = profile_mod.norm_key(v)
                if k in vals and k not in shown:
                    shown[k] = str(v).strip()
            out.append((t.sheet, t.headers[j], sorted(shown.get(k, k) for k in vals)))
        return out

    # answers that say the rows sharing a value are separate things, by the question they answer
    _SEPARATE = {"find_dupes_": "different", "find_copies_": "both", "find_pairs_": "separate",
                 "find_upload_": "both"}

    def _retire_repeats(self, insights: list, answers: dict) -> list:
        """Once the owner said repeated rows are separate things (keep both), the
        finding that the ID column repeats is no longer an open question: it is
        left out of what is found. Nothing is ever dropped from the rows."""
        done = set()
        for qid, a in (answers or {}).items():
            pick = next((v for k, v in self._SEPARATE.items() if qid.startswith(k)), None)
            ab = (a.get("about") or {}) if isinstance(a, dict) else {}
            if pick and pick in (a.get("options") or []) and ab.get("table"):
                # the column asked about, and the ID column a twin rule matches on
                for h in [ab.get("col")] + [m for d in a.get("rules") or [] for m in (d.get("values") or {})
                                            .get("match") or []]:
                    done.add((ab["table"], h))
        out = []
        for i in insights:
            if i.get("recipe", "").startswith("duplicates:"):
                r = self.detection["roles"].get(i["recipe"].split(":", 1)[1]) or {}
                if (r.get("table"), r.get("header")) in done:
                    continue
            out.append(i)
        return out

    def _insight_tables(self, ins: dict) -> list:
        """The tables a finding counted: the one its numbers name, else those its columns are on."""
        n = ins.get("numbers") or {}
        tid = n.get("table") or n.get("from_table") if isinstance(n, dict) else None
        if tid and any(t.tid == tid for t in self.tables):
            return [self.table(tid)]
        files = set(ins.get("files") or [])
        return [t for t in self.tables if (not files or self.file_of[t.tid] in files)
                and any(s == t.sheet and (h in t.headers or not t.headers) for s, h in ins.get("depends") or [])]

    @staticmethod
    def _touches(rule, t, ins: dict) -> bool:
        """A rule that drops rows changes every number counted on its table; one that
        re-labels or divides a column changes the numbers that read that column,
        except a check that reads the values as recorded (a product check)."""
        if rule.kind in ("exclude", "filter", "pair", "dedupe"):
            return True
        if rule.kind == "scale" and str(ins.get("recipe", "")).startswith(_AS_RECORDED):
            return False
        return any(s == t.sheet and h == rule.values.get("col") for s, h in ins.get("depends") or [])

    def not_applied_marks(self, tables: list, depends: list, recipe: str = "") -> list:
        """The sentences that say an owner's rule would change a number counted on
        these tables from these columns and is not applied, at most 2. Where a
        confirmed rule already applies on the table, the mark does not say the
        number is counted over every row."""
        from .rules import topic
        ins = {"depends": depends, "recipe": recipe}
        marks = []
        for c in self.unapplied_rules:
            r = c["rule"]
            t = next((t for t in tables if t.tid == r.table), None)
            if t is None or not self._touches(r, t, ins):
                continue
            ruled = any(x.table == t.tid and self._touches(x, t, ins) for x in self.rules)
            mark = (f"The owner's note on {topic(r)} would change this number and is not applied here." if ruled
                    else f"Counted over every row; the owner's note on {topic(r)} changes this number and is not "
                         "applied here.")
            if mark not in marks:
                marks.append(mark)
        if str(recipe).startswith("spread:") and any(t.tid in self.net_prices for t in tables):
            marks.insert(0, "The owner's note on credits in price comparisons is not applied here: the prices "
                            "compared are the lines before credits.")
        return marks[:2]

    def _before_rules(self, ins: dict, applied: list) -> dict:
        """A finding counted over the whole table, labeled as such when rules now apply there."""
        for t in self._insight_tables(ins):
            if any(r.table == t.tid and not r.scope and self._touches(r, t, ins) for r in applied):
                return dict(ins, statement=ins["statement"].rstrip(". ")
                            + f" (counted over all {t.n_rows:,} rows, before the owner's rules).")
        return ins

    def _not_applied(self, ins: dict) -> dict:
        """Each rule the owner wrote and did not confirm marks the numbers it would change."""
        marks = self.not_applied_marks(self._insight_tables(ins), ins.get("depends") or [], ins.get("recipe", ""))
        if not marks:
            return ins
        return dict(ins, statement=ins["statement"].rstrip(". ") + ". " + " ".join(marks))

    # ------------------------------------------------------------------
    def table(self, tid: str):
        return next(t for t in self.tables if t.tid == tid)

    def row_labels(self, t) -> list:
        """Labels down the first column: month-grid tables, and label/value input
        lists ('Growth | 0.04') whose rows are the things that have meaning."""
        if t.wide and t.row_label_col >= 0:
            return [str(v).strip() for v in t.column(t.row_label_col) if isinstance(v, str) and v.strip()]
        if len(t.headers) >= 2 and t.rows:
            pairs = sum(1 for r in t.rows if len(r) >= 2 and isinstance(r[0], str) and r[0].strip()
                        and isinstance(r[1], (int, float)) and not isinstance(r[1], bool))
            if pairs >= max(2, 0.6 * len(t.rows)):
                return [r[0].strip() for r in t.rows if isinstance(r[0], str) and r[0].strip()]
        return []

    def is_derived(self, tid: str) -> bool:
        return tid in self.derived

    def reach_share(self, cells: list, path: str = "") -> float:
        """The largest share of a workbook's formula cells that one of these cells
        ('Sheet!A1') feeds, directly or down the chain: how much of a model an
        answer about that cell can move."""
        graphs = self.__dict__.setdefault("_dep_graphs", {})
        best = 0.0
        for b in self.books:
            if b.kind != "xlsx" or (path and b.path != path):
                continue
            if b.path not in graphs:
                graphs[b.path] = formulas_mod.dependents(b)
            g = graphs[b.path]
            names = {s.name for s in b.data_sheets()}
            for x in cells or []:
                sheet, _, cell = str(x).rpartition("!")
                if g["total"] and sheet in names:
                    best = max(best, formulas_mod.reach(g, sheet, cell) / g["total"])
        return best

    def book_of(self, tid: str):
        path = self.file_of[tid]
        return next(b for b in self.books if b.path == path)

    def col(self, tid: str, header: str):
        return next((c for c in self.cols.get(tid, []) if c.header == header), None)

    def _pick_main(self):
        cands = [t for t in self.tables if t.tid not in self.derived and t.rows]
        if not cands:
            cands = [t for t in self.tables if t.rows]
        if not cands:
            return None
        return max(cands, key=lambda t: (t.n_rows * max(1, len(t.headers))))

    @property
    def total_rows(self) -> int:
        return sum(t.n_rows for t in self.tables)

    # ------------------------------------------------------------------
    def _structural_insights(self) -> list:
        out = []
        for b in self.books:
            start = len(out)
            self._book_insights(b, out)
            for ins in out[start:]:
                ins["files"] = [b.path]
        for t in self.tables:
            start = len(out)
            self._table_insights(t, out)
            for ins in out[start:]:
                ins["files"] = [self.file_of[t.tid]]
        out += self._unmatched_insights()
        out += self._reference_insights()
        out += self._join_variants()
        out += self._derived_labels()
        return out

    def _unmatched_insights(self) -> list:
        """Rows whose ID has no partner in the table it looks up: often the most
        telling finding in a sheet (retired codes, fee lines, typos)."""
        out = []
        for j in self.joins:
            if j["band"] != "auto":
                continue
            ft, tt = self.table(j["from_table"]), self.table(j["to_table"])
            fc, tc = self.col(ft.tid, j["from_col"]), self.col(tt.tid, j["to_col"])
            if fc is None or tc is None:
                continue
            if j["cross_file"] and ft.n_rows < tt.n_rows:
                # report from the bigger (transaction) side: which of its values have no match
                ft, tt, fc, tc = tt, ft, tc, fc
                j = dict(j, from_file=j["to_file"], to_file=j["from_file"])
            elif not j["cross_file"] and (j["rows_matched"] >= 0.995 or not j["to_unique"]
                                          or fc.semantic != "identifier"):
                continue
            if fc.sensitive:
                continue
            missing = [(k, n) for k, n in fc.counter.most_common() if k not in tc.counter]
            rows = sum(n for _, n in missing)
            if not rows:
                continue
            first = {}
            for v in ft.column(fc.j):
                k = profile_mod.norm_key(v)
                if k is not None and k not in first:
                    first[k] = v
            ex = ", ".join(str(first.get(k, k))[:40] for k, _ in missing[:3])
            where_t = tt.sheet if not j["cross_file"] else os.path.basename(j["to_file"])
            out.append({"recipe": f"unmatched:{ft.tid}:{fc.header}", "kind": "gotcha", "oddity": True,
                        "weight": 2,
                        "statement": f"{rows:,} rows on {ft.sheet} have {_a(fc.header)} {fc.header} that is "
                                     f"not in {where_t} " + (f"({len(missing):,} distinct values, for example {ex})."
                                                             if len(missing) != 1 else f"(always {ex})."),
                        "depends": [(ft.sheet, fc.header), (tt.sheet, tc.header)],
                        "numbers": {"rows": rows, "values": len(missing),
                                    "examples": [str(first.get(k, k))[:40] for k, _ in missing[:6]],
                                    "missing_keys": [k for k, _ in missing[:200]],
                                    "from_table": ft.tid, "from_col": fc.header, "to_table": tt.tid,
                                    "to_col": tc.header, "where": where_t},
                        "files": [j["from_file"]]})
        return out

    def _book_insights(self, b, out: list):
        fa = self.formulas.get(b.path) or {}
        every = fa.get("_all") or fa           # the uncapped lists: counts are of every cell, not a shown few
        name = b.name
        plugs = every.get("typed_in_formula_rows") or []
        for chk in fa.get("checks", []):
            if chk["failing_count"]:
                # a typed number in a formula row in the very months the check fails explains it
                fails = {_month(f) for f in chk["failing"]}
                why = [p for p in plugs if self.cell_label(p["sheet"], p["cell"]).rpartition(", ")[2] in fails]
                chk = dict(chk, explained_by=[f"{p['sheet']}!{p['cell']}" for p in why])
                out.append({
                    "recipe": "formula:check", "kind": "check", "oddity": True, "weight": 3,
                    "statement": f"The {chk['row_label']} row on {chk['sheet']} is not zero in "
                                 f"{chk['failing_count']} of {chk['periods']} periods "
                                 f"({', '.join(_month(f) for f in chk['failing'][:3])}); "
                                 + ("the largest gap is" if chk["failing_count"] != 1 else "the gap is")
                                 + f" {chk['max_abs']:,.2f}"
                                 + (f"; in {'that month' if chk['failing_count'] == 1 else 'a month it fails'} "
                                    f"{_join_words([self._named(p) for p in why[:3]])} "
                                    + ("is a typed number in a row of formulas" if len(why) == 1 else
                                       "are typed numbers in rows of formulas") if why else "") + ".",
                    "depends": [(chk["sheet"], chk["row_label"])],
                    "numbers": chk})
            else:
                out.append({
                    "recipe": "formula:check", "kind": "check", "weight": 1,
                    "statement": f"The {chk['row_label']} row on {chk['sheet']} is zero in all "
                                 f"{chk['periods']} periods.",
                    "depends": [(chk["sheet"], chk["row_label"])], "numbers": chk})
        # a link times a typed number is one problem (the amount it adds), not also a broken pattern
        times = {(x["sheet"], x["cell"]) for x in every.get("hardcoded") or [] if x.get("multiplier")}
        br = [x for x in every.get("pattern_breaks") or [] if (x["sheet"], x["cell"]) not in times]
        if br:
            cells = ", ".join(self._named(x) for x in br[:4])
            out.append({"recipe": "formula:pattern_breaks", "kind": "gotcha", "oddity": True,
                        "weight": 3,
                        "statement": f"{len(br)} formula{'s break the pattern of their' if len(br) != 1 else ' breaks the pattern of its'} "
                                     "row or column "
                                     f"({cells}{', ...' if len(br) > 4 else ''}).",
                        "depends": [(x["sheet"], x["row_label"] or x["cell"]) for x in br[:6]],
                        "numbers": {"count": len(br), "cells": [f"{x['sheet']}!{x['cell']}"
                                                                for x in br[:20]],
                                    "named": [self._named(x) for x in br[:20]],
                                    "diffs": ["; ".join(x.get("diff") or []) for x in br[:20]],
                                    "formulas": [x["formula"] for x in br[:20]],
                                    "expected": [x["expected_like"] for x in br[:20]],
                                    "expected_at": [x.get("expected_at") for x in br[:20]]}})
        hc = fa.get("hardcoded_total", 0)
        if hc:
            ex = fa["hardcoded"][0]
            adds = (f", a typed multiplier on a link that adds {ex['added']:,.2f}" if ex.get("multiplier")
                    else "")
            out.append({"recipe": "formula:hardcoded", "kind": "gotcha", "oddity": True,
                        "weight": 2,
                        "statement": (f"{hc} formulas contain a typed number (for example "
                                      f"{self._named(ex)} uses {ex['constant']}{adds})." if hc != 1 else
                                      f"1 formula contains a typed number: {self._named(ex)} uses "
                                      f"{ex['constant']}{adds}."),
                        "depends": [(x["sheet"], x["row_label"] or x["cell"])
                                    for x in fa["hardcoded"][:6]],
                        "numbers": {"count": hc, "cells": [f"{x['sheet']}!{x['cell']}"
                                                           for x in fa["hardcoded"][:20]],
                                    "named": [self._named(x) for x in fa["hardcoded"][:20]],
                                    "constants": [x["constant"] for x in fa["hardcoded"][:20]],
                                    "added": [x.get("added") for x in fa["hardcoded"][:20]]}})
        if plugs:
            ex = plugs[0]
            out.append({"recipe": "formula:typed_in_formula_row", "kind": "gotcha", "oddity": True,
                        "weight": 3,
                        "statement": (f"{len(plugs)} numbers are typed into rows that are otherwise formulas "
                                      f"(for example {self._named(ex)}), so they will not update when inputs "
                                      "change." if len(plugs) != 1 else
                                      f"1 number is typed into a row that is otherwise formulas "
                                      f"({self._named(ex)}), so it will not update when inputs change."),
                        "depends": [(x["sheet"], x["row_label"] or x["cell"]) for x in plugs[:6]],
                        "numbers": {"count": len(plugs), "cells": [f"{x['sheet']}!{x['cell']}"
                                                                   for x in plugs[:20]],
                                    "named": [self._named(x) for x in plugs[:20]]}})
        blocks = fa.get("plan_blocks") or []
        in_block = {(p["sheet"], ri) for p in blocks for ri in p["row_index"]}
        for tr in [x for x in fa.get("typed_rows_on_calculated_tabs") or []
                   if (x["sheet"], x["header_row"]) not in in_block][:3]:
            big = tr["biggest"]
            big_s = f"{big:,.0f}" if isinstance(big, (int, float)) and abs(big) >= 100 else f"{big}"
            out.append({"recipe": f"formula:typed_row:{tr['sheet']}:{tr['row_label']}", "kind": "gotcha",
                        "oddity": True, "weight": 2,
                        "statement": f"The {tr['row_label']} row on {tr['sheet']} is typed, not calculated, "
                                     "on a tab that is otherwise formulas "
                                     + (f"(the largest is {big_s} in {self._named(tr)})." if tr["cells"] != 1
                                        else f"(its only number is {big_s}, in {self._named(tr)})."),
                        "depends": [(tr["sheet"], tr["row_label"])], "numbers": tr})
        orph = every.get("orphan_inputs") or []
        if orph:
            ex = orph[0]
            # the same value typed elsewhere, where the model really takes it from
            twin = [self._named({"sheet": x.partition("!")[0], "cell": x.partition("!")[2]})
                    for x in ex.get("twins") or []]
            also = (f"; the same {_value_words(ex['value'])} is typed in {twin[0]}"
                    + (f" and {ex['twin_count'] - 1} other cell{'s' if ex['twin_count'] != 2 else ''}"
                       if ex["twin_count"] > 1 else "") + ", and nothing links them" if twin else "")
            out.append({"recipe": "formula:orphan_inputs", "kind": "gotcha", "oddity": True,
                        "weight": 2,
                        "statement": (f"{len(orph)} inputs on {ex['sheet']} are never used by any formula "
                                      f"(for example {ex['row_label']} in {ex['cell']}{also})." if len(orph) != 1
                                      else f"1 input on {ex['sheet']} is never used by any formula: "
                                           f"{ex['row_label']} in {ex['cell']}{also}."),
                        "depends": [(x["sheet"], x["row_label"]) for x in orph[:6]],
                        "numbers": {"count": len(orph), "cells": [f"{x['sheet']}!{x['cell']}"
                                                                  for x in orph[:20]],
                                    "named": [self._named(x) for x in orph[:20]], "twin": twin[:1],
                                    "value": ex["value"]}})
        self._model_insights(b, fa, blocks, out)
        for sr in (fa.get("short_ranges") or [])[:3]:
            out.append({"recipe": f"formula:short_range:{sr['sheet']}:{sr['reads']}", "kind": "gotcha",
                        "oddity": True, "weight": 5,
                        "statement": f"The formulas on {sr['sheet']} read {sr['reads']} only down to row "
                                     f"{sr['range_end']:,}, but {sr['reads']} now goes to row {sr['last_row']:,}, "
                                     + (f"so its last {sr['rows_left_out']:,} rows are left out of "
                                        if sr["rows_left_out"] != 1 else "so its last row is left out of ")
                                     + f"{sr['sheet']}'s totals.",
                        "depends": [], "numbers": sr})
        bd = fa.get("actuals_boundary") or {}
        if bd:
            out.append({"recipe": "formula:actuals_boundary", "kind": "scope", "weight": 2,
                        "statement": f"On {_join_words(bd.get('sheets') or [bd['sheet']])}, months through "
                                     f"{_month(bd['last_actual'])} are typed numbers and later months are formulas "
                                     f"on {bd['rows']} rows, which looks like the end of actuals.",
                        "depends": [(bd["sheet"], bd["last_actual"])], "numbers": bd})
        if fa.get("calc_manual"):
            out.append({"recipe": "formula:calc_manual", "kind": "gotcha", "oddity": True,
                        "weight": 2, "statement": f"{name} is set to manual calculation, so "
                                                  "cached numbers may be out of date.",
                        "depends": [], "numbers": {}})
        vol = fa.get("volatile") or {}
        if vol:
            out.append({"recipe": "formula:volatile", "kind": "gotcha", "weight": 1,
                        "statement": f"Some results in {name} can change without any cell "
                                     f"changing (it uses {', '.join(sorted(vol))}).",
                        "depends": [], "numbers": vol})
        if fa.get("missing_cached"):
            out.append({"recipe": "formula:missing_cached", "kind": "gotcha", "oddity": True,
                        "weight": 2,
                        "statement": (f"{fa['missing_cached']} formulas in {name} have no saved result, so "
                                      "their values are unknown" if fa["missing_cached"] != 1 else
                                      f"1 formula in {name} has no saved result, so its value is unknown")
                                     + " until the file is opened and recalculated in a spreadsheet app.",
                        "depends": [], "numbers": {}})
        if fa.get("external_links"):
            out.append({"recipe": "formula:external_links", "kind": "gotcha", "weight": 1,
                        "statement": f"{name} links to "
                                     + (f"{fa['external_links']} other workbooks; those numbers update only when "
                                        "the links refresh." if fa["external_links"] != 1 else
                                        "1 other workbook; those numbers update only when the link refreshes."),
                        "depends": [], "numbers": {}})
        hidden = [s.name for s in b.data_sheets() if s.state != "visible"]
        if hidden:
            out.append({"recipe": "structure:hidden_tabs", "kind": "gotcha", "weight": 1,
                        "statement": f"{name} has hidden tabs: {', '.join(hidden[:6])}.",
                        "depends": [], "numbers": {"hidden": hidden}})
        if any(s.is_rules for s in b.sheets):
            out.append({"recipe": "structure:rules_sheet", "kind": "gotcha", "weight": 2,
                        "oddity": True,
                        "statement": f"{name} has a .Rules tab. Copilot in Excel treats that tab "
                                     "as instructions from whoever wrote the file.",
                        "depends": [], "numbers": {}})

    # ------------------------------------------------------------------
    # a formula model in words: inputs, drivers, signs, scale, plan blocks, tie-outs, balances
    # ------------------------------------------------------------------
    def _model_insights(self, b, fa: dict, blocks: list, out: list):
        """What code can state about a formula model from the formulas alone: each
        input with its note (or none), each row that reads an input in words, how
        subtotals treat costs, the money scale titles and notes name, the balance
        rows' lowest point and yearly totals, and one ledger of the problem cells.
        A typed block that feeds the model and a row whose typed periods do not tie
        to the row it links to later are asked, typed actuals at the tab's switch
        to formulas included: the gap is money the link leaves out, timing or a
        mistake, which only the owner can say."""
        if not fa.get("count") or not any(t.wide for t in self.tables if self.file_of[t.tid] == b.path):
            return
        for x in fa.get("inputs") or []:
            note = f": \"{x['note']}\"" if x["note"] else ", with no note"
            out.append({"recipe": f"model:input:{x['sheet']}:{x['cell']}", "kind": "structure", "weight": 0,
                        "statement": f"In the file, {x['label']} ({x['sheet']}!{x['cell']}) = "
                                     f"{_value_words(x['value'])}{note}.",
                        "depends": [(x["sheet"], x["label"])],
                        "numbers": {k: x[k] for k in ("sheet", "cell", "label", "value", "note", "read")}})
        for d in fa.get("drivers") or []:
            a, z = _month(d["first"]), _month(d["last"])
            span = f" in {a} to {z}" if a and z and a != z else f" in {a}" if a else ""
            part = f" ({d['cells']} of its {d['formulas']} formula cells)" if d["cells"] < d["formulas"] else ""
            out.append({"recipe": f"model:driver:{d['sheet']}:{d['row_label']}", "kind": "structure", "weight": 0,
                        "statement": f"On {d['sheet']}, {d['row_label']} = {d['words']}{span}{part}.",
                        "depends": [(d["sheet"], d["row_label"])], "numbers": d})
        self._sign_fact(fa.get("sign") or {}, out)
        self._scale_fact(b, fa.get("scale") or {}, out)
        for p in blocks:
            rows = _join_words(p["rows"])
            many = len(p["rows"]) != 1
            out.append({"recipe": f"formula:plan_block:{p['sheet']}:{p['rows'][0]}", "kind": "gotcha", "oddity": True,
                        "weight": 3,
                        "statement": f"The {rows} row{'s' if many else ''} on {p['sheet']} {'are' if many else 'is'} "
                                     f"typed, not calculated, in {p['periods']} periods ({_month(p['first'])} to "
                                     f"{_month(p['last'])}, from {_value_words(p['lo'])} to {_value_words(p['hi'])}) "
                                     f"and feed{'' if many else 's'} {p['feeds']:,} formula cells "
                                     f"({p['share'] * 100:.0f}% of the model).",
                        "depends": [(p["sheet"], r) for r in p["rows"]], "numbers": p})
        for x in fa.get("tieouts") or []:
            on = f" on {x['link_sheet']}" if x["link_sheet"] != x["sheet"] else ""
            gap = x["gap"]
            share = f", {abs(gap) / abs(x['linked_sum']) * 100:.1f}%" if x["linked_sum"] else ""
            when = "the same periods" if x["offset"] == 0 else "the periods its link reads"
            # typed actuals up to the tab's switch to formulas: the usual case, and still a gap only the owner knows
            after = f"after that, where {x['sheet']} switches from typed numbers to formulas" if x["at_switch"] \
                else "after that"
            stmt = (f"{x['row_label']} on {x['sheet']} is typed in {x['typed']} periods ({_month(x['first'])} to "
                    f"{_month(x['last'])}) and links to {x['link_label']}{on} {after}; the typed periods come to "
                    f"{x['typed_sum']:,.2f}, {abs(gap):,.2f} {'above' if gap > 0 else 'below'} {x['link_label']} "
                    f"in {when}{share}, and {x['off']} of the {x['typed']} differ.")
            out.append({"recipe": f"formula:tieout:{x['sheet']}:{x['row_label']}", "kind": "gotcha", "oddity": True,
                        "weight": 2, "statement": stmt, "depends": [(x["sheet"], x["row_label"])], "numbers": x})
        self._series_facts(b, out)
        led = self._ledger(b.path, fa, {})
        if led:
            out.append(led)

    def _sign_fact(self, sg: dict, out: list):
        """How subtotals treat costs, from the rows they subtract and add: settled
        by code when every subtotal agrees, stated as a conflict when they do not."""
        pos, neg = sg.get("positive_subtracted") or [], sg.get("negative_added") or []
        if not pos and not neg:
            return
        labels = [x.partition("!")[2] for x in (pos or neg)]
        shown = _join_words(labels[:4] + ([f"{len(labels) - 4} more"] if len(labels) > 4 else []))
        ex = f"for example {sg['example']} on {sg['example_sheet']}"
        if pos and neg:
            stmt = (f"Subtotals disagree on signs: they subtract {len(pos)} row{'s' if len(pos) != 1 else ''} of "
                    f"positive numbers ({shown}) and add {len(neg)} row{'s' if len(neg) != 1 else ''} of negative "
                    f"numbers ({_join_words([x.partition('!')[2] for x in neg[:4]])}).")
        elif pos:
            stmt = (f"Subtotals subtract {len(pos)} row{'s' if len(pos) != 1 else ''} that hold positive numbers "
                    f"({shown}), {ex}; no subtotal adds a row of negative numbers.")
        else:
            stmt = (f"Subtotals add {len(neg)} row{'s' if len(neg) != 1 else ''} that hold negative numbers "
                    f"({shown}), {ex}; no subtotal subtracts a row of positive numbers.")
        settled = not (pos and neg) and len(pos or neg) >= 2
        out.append({"recipe": "model:sign", "kind": "structure", "weight": 0, "statement": stmt,
                    "depends": [], "numbers": dict(sg, conflict=bool(pos and neg)),
                    "settles": ["model_sign"] if settled else []})

    def _scale_fact(self, b, sc: dict, out: list):
        """The money scale the file's titles and input notes name: settled by code
        when they name one, stated as a conflict when they name two."""
        if not sc:
            return
        order = [s.name for s in b.data_sheets()]
        parts = []
        for kind in sc["kinds"]:
            said = sc["said"][kind]
            titles = sorted({s for s, w, _ in said if w == "title"}, key=lambda s: order.index(s) if s in order else 99)
            notes = [s for s, w, _ in said if w == "note"]
            where = []
            if titles:
                where.append(f"the title{'s' if len(titles) != 1 else ''} on {_join_words(titles)}")
            if notes:
                where.append(f"{len(notes)} input note{'s' if len(notes) != 1 else ''} on "
                             f"{_join_words(sorted(set(notes)))}")
            parts.append((kind, said[0][2], " and ".join(where)))
        if sc["conflict"]:
            stmt = ("The file names more than one money scale: "
                    + "; ".join(f"{w} say{'s' if w.startswith('the title ') else ''} \"{tok}\" ({kind})"
                                for kind, tok, w in parts) + ".")
        else:
            kind, tok, w = parts[0]
            what = {"as shown": "a currency and no thousands or millions", "thousands": "thousands",
                    "millions": "millions"}[kind]
            stmt = (f"{w[:1].upper()}{w[1:]} say{'s' if w.startswith('the title ') else ''} \"{tok}\" and nothing "
                    f"else in the file names a scale, so money figures are "
                    + ("as shown" if kind == "as shown" else f"in {kind}") + f" ({what}).")
        out.append({"recipe": "model:scale", "kind": "structure", "weight": 0, "statement": stmt, "depends": [],
                    "numbers": sc, "settles": [] if sc["conflict"] else ["model_scale"]})

    def _series_rows(self, path: str) -> list:
        """The balance rows of a model's grids (cash, a bank or loan balance, a
        closing figure; not a flow in the period):
        [(table, sheet row, label, [(sheet column, period header)])]."""
        out = []
        for t in self.tables:
            if self.file_of[t.tid] != path or not t.wide or t.row_label_col < 0:
                continue
            per = [(j, c) for j, c in enumerate(t.cols) if formulas_mod._is_period(t.headers[j])]
            if len(per) < 6:
                continue
            for ri, row in zip(t.row_index, t.rows):
                lab = row[t.row_label_col] if t.row_label_col < len(row) else None
                if isinstance(lab, str) and _LEVEL_ROW.search(lab) and not _NOT_LEVEL.search(lab) \
                        and not formulas_mod._CHECK_LABEL.search(lab):
                    out.append((t, ri, lab.strip(), [(c, t.headers[j]) for j, c in per]))
        return out

    def _series_facts(self, b, out: list):
        """A balance row's lowest point and its period, where it ends, and the first
        period below zero; a revenue or profit row's total in each whole year."""
        sheets = {s.name: s for s in b.data_sheets()}
        model = formulas_mod.Model(b)        # a formula saved without its value is read again
        for t, ri, lab, per in self._series_rows(b.path):
            vals = [(h, v) for h, v in ((h, model.number((t.sheet, ri, c))) for c, h in per) if _is_num(v)]
            if len(vals) < 6:
                continue
            out.append(self._series_insight(t.sheet, ri, lab, vals))
        roles = self.detection["roles"]
        for rid in ("revenue", "profit"):
            r = roles.get(rid) or {}
            t = next((t for t in self.tables if t.tid == r.get("table")), None)
            if t is None or not t.wide or self.file_of[t.tid] != b.path or not r.get("row_label"):
                continue
            s = sheets[t.sheet]
            ri = next((i for i, row in zip(t.row_index, t.rows) if t.row_label_col >= 0
                       and str(row[t.row_label_col]).strip() == r["header"]), None)
            if ri is None or not any(k[0] == ri for k in s.formulas):
                continue
            years: dict = {}
            for j, c in enumerate(t.cols):
                y = _year_of(t.headers[j]) if formulas_mod._is_period(t.headers[j]) else None
                v = model.number((t.sheet, ri, c)) if y else None
                if _is_num(v):
                    years.setdefault(y, []).append(v)
            whole = [(y, sum(vs)) for y, vs in sorted(years.items()) if len(vs) == 12]
            if not whole:
                continue
            out.append({"recipe": f"model:totals:{t.sheet}:{r['header']}", "kind": "structure", "weight": 0,
                        "statement": f"{r['header']} on {t.sheet} totals "
                                     + _join_words([f"{v:,.2f} in {y}" for y, v in whole]) + ".",
                        "depends": [(t.sheet, r["header"])], "numbers": {"years": whole}})

    def _series_insight(self, sheet: str, ri: int, lab: str, vals: list, recipe: str = "model:series",
                        lead: str = "", kept: str = "") -> dict:
        low = min(vals, key=lambda hv: hv[1])
        below = next((h for h, v in vals if v < 0), None)
        end = (f"at its end, {low[1]:,.2f} in {_month(low[0])}" if low[0] == vals[-1][0] else
               f"at {low[1]:,.2f} in {_month(low[0])} and is {vals[-1][1]:,.2f} in {_month(vals[-1][0])}")
        stmt = (f"{lead}{lab} on {sheet}{kept} is lowest {end}"
                + (f"; it first goes below zero in {_month(below)}" if below else "") + ".")
        return {"recipe": f"{recipe}:{sheet}:{lab}", "kind": "structure", "weight": 0, "statement": stmt,
                "depends": [(sheet, lab)],
                "numbers": {"sheet": sheet, "row": ri, "row_label": lab, "min": low[1], "min_at": low[0],
                            "last": vals[-1][1], "last_at": vals[-1][0], "below": below}}

    # answers that call a typed row a plan or a test number: the picks on a typed row, and on a typed block
    _PLAN_PICKS = {"find_typed_row_": {"plan", "test"}, "find_plan_block_": {"approved", "estimate"}}

    def _plan_effects(self, answers: dict) -> list:
        """A typed row the owner called a plan (or a test number), taken out: each
        balance row read again with that row at zero (lowest point and period, first
        period below zero) beside the same row as the file has it. A number typed
        into the balance row itself after its formulas start stays as typed, and the
        reading says so. Nothing when the model uses a function this reading does
        not cover."""
        out = []
        for qid, a in (answers or {}).items():
            picks = next((v for k, v in self._PLAN_PICKS.items() if qid.startswith(k)), None)
            ab = (a.get("about") or {}) if isinstance(a, dict) else {}
            if not picks or not set(a.get("options") or []) & picks or not ab.get("table"):
                continue
            t = next((t for t in self.tables if t.tid == ab["table"]), None)
            if t is None:
                continue
            b = self.book_of(t.tid)
            fa = self.formulas.get(b.path) or {}
            rows = [x["header_row"] for x in fa.get("typed_rows_on_calculated_tabs") or []
                    if x["sheet"] == t.sheet and x["row_label"] == ab.get("col")]
            rows += [ri for p in fa.get("plan_blocks") or [] if p["sheet"] == t.sheet and ab.get("col") in p["rows"]
                     for ri in p["row_index"]]
            s = next((s for s in b.data_sheets() if s.name == t.sheet), None)
            if not rows or s is None:
                continue
            zero = {(s.name, r, c) for r in rows for c, v in enumerate(s.values[r]) if _is_num(v)
                    and (r, c) not in s.formulas}
            model = formulas_mod.Model(b, zero)
            for tt, ri, lab, per in self._series_rows(b.path):
                try:
                    vals = [(h, model.value((tt.sheet, ri, c))) for c, h in per]
                except formulas_mod.Unsupported:
                    break
                if len(vals) < 6:
                    continue
                own = model.sheets[tt.sheet]
                row = own.values[ri] if ri < len(own.values) else []
                fcols = [c for c, _h in per if (ri, c) in own.formulas]
                typed = [f"{brainzip.col_letter(c)}{ri + 1}" for c, _h in per if fcols and c > fcols[0]
                         and (ri, c) not in own.formulas and c < len(row) and _is_num(row[c])]
                shown = typed[:3] + ([f"{len(typed) - 3} more"] if len(typed) > 3 else [])
                kept = f", with its typed {_join_words(shown)} kept as typed," if typed else ""
                ins = self._series_insight(tt.sheet, ri, lab, vals, recipe=f"model:without:{t.sheet}:{ab['col']}",
                                           lead=f"Without the {ab['col']} row on {t.sheet} (the owner's pick: "
                                                f"{_join_words(a.get('labels') or [])}), ", kept=kept)
                ins["numbers"]["kept"] = typed
                ins["numbers"]["without"] = ab["col"]
                ins["files"] = [b.path]
                out.append(ins)
        return out

    def _ledger(self, path: str, fa: dict, answers: dict) -> dict | None:
        """One ledger of the cells the formula checks found, each once with all its
        reasons: a broken pattern in words, a typed number inside a formula (and what
        a typed multiplier adds), a typed number in a row of formulas (and the check
        it makes fail), an input nothing reads (and its typed twin). Cells the owner
        has answered about are counted apart. None when fewer than two cells. Built
        from the uncapped lists, so its count is of every such cell."""
        cells: dict = {}
        every = fa.get("_all") or fa

        def add(x, why):
            k = f"{x['sheet']}!{x['cell']}"
            cells.setdefault(k, {"named": self._named(x), "why": []})["why"].append(why)
        times = {(x["sheet"], x["cell"]) for x in every.get("hardcoded") or [] if x.get("multiplier")}
        for x in every.get("pattern_breaks") or []:
            if (x["sheet"], x["cell"]) not in times:
                add(x, "; ".join(x.get("diff") or []) or "breaks its row's pattern")
        for x in every.get("hardcoded") or []:
            add(x, f"multiplies a link by a typed {x['multiplier']}, adding {x['added']:,.2f}" if x.get("multiplier")
                else f"has a typed {x['constant']} inside its formula")
        checks = [c for c in fa.get("checks") or [] if c["failing_count"]]
        for x in every.get("typed_in_formula_rows") or []:
            month = self.cell_label(x["sheet"], x["cell"]).rpartition(", ")[2]
            fails = [c["row_label"] for c in checks if month in {_month(f) for f in c["failing"]}]
            add(x, "is a typed number in a row of formulas"
                + (f", the month the {fails[0]} row fails" if fails else ""))
        for x in every.get("orphan_inputs") or []:
            add(x, "is an input no formula uses" + (f", and the same value is typed in {x['twins'][0]}"
                                                     if x.get("twins") else ""))
        if len(cells) < 2:
            return None
        told = set()
        for a in (answers or {}).values():
            if isinstance(a, dict) and not a.get("not_sure"):
                told |= {v for v in (a.get("about") or {}).get("values") or []}
        shown = [f"{v['named']} {' and '.join(v['why'])}" for v in cells.values()]
        done = [k for k, v in cells.items() if v["named"] in told]
        stmt = (f"{len(cells)} cells hold the formula problems found, each counted once: " + "; ".join(shown[:8])
                + (f"; and {len(shown) - 8} more" if len(shown) > 8 else "")
                + (f". The owner has answered about {len(done)} of them." if done else "."))
        return {"recipe": "model:ledger", "kind": "structure", "weight": 0, "statement": stmt, "depends": [],
                "numbers": {"cells": list(cells)[:200], "count": len(cells), "answered": done},
                "files": [path]}

    def _table_insights(self, t, out: list):
        n_text_nums = [c for c in self.cols[t.tid] if c.numeric_text >= max(5, 0.5 * c.count)
                       and c.type == "text" and c.semantic != "identifier" and not c.codes]
        for c in n_text_nums[:2]:
            out.append({"recipe": "structure:numbers_as_text", "kind": "gotcha", "weight": 1,
                        "oddity": True,
                        "statement": f"{c.header} on {t.sheet} holds numbers stored as text "
                                     f"({c.numeric_text:,} cells), so sums skip them.",
                        "depends": [(t.sheet, c.header)], "numbers": {}})
        if t.totals_rows:
            n = len(t.totals_rows)
            rows = [r + 1 for r in sorted(t.totals_rows)]
            shown = ", ".join(f"{r:,}" for r in rows[:6]) + (", ..." if n > 6 else "")
            out.append({"recipe": "structure:totals_rows", "kind": "structure", "weight": 0,
                        "statement": f"{t.sheet} has {n} total row{'s' if n != 1 else ''} inside the data "
                                     f"(row{'s' if n != 1 else ''} {shown}), left out of every count.",
                        "depends": [], "numbers": {"rows": n, "row_numbers": rows[:200], "sheet": t.sheet,
                                                   "table": t.tid}})
        self._structure_facts(t, out)
        if not t.wide and t.tid not in self.derived:
            self._nonstock(t, out)          # a lookup list of any size: a category with no cost at all
        if t.wide or t.tid in self.derived or t.n_rows < 50:
            return out
        self._blank_groups(t, out)
        self._measure_blanks(t, out)
        # copies first: rows a second system loaded again are set aside when a date's old and new values are read
        self._copies(t, out)
        self._boundaries(t, out)
        self._near_key_blocks(t, out)
        self._exclusive_prefixes(t, out)
        self._odd_groups(t, out)
        self._entity_outliers(t, out)
        self._unpaid(t, out)
        self._unit_groups(t, out)
        self._period_end(t, out)
        self._derived_measure(t, out)
        self._group_arithmetic(t, out)
        self._carried_in(t, out)
        return out

    # ------------------------------------------------------------------
    # group arithmetic: a companion line at a fixed ratio, lines on the unusual side, an opening entry
    # ------------------------------------------------------------------
    def _group_col(self, t):
        """The column whose repeated values gather a table's rows into groups (an
        entry, an order, an invoice): the ID whose lines net to zero, else an ID
        column whose values repeat in groups of 2 to GROUP_MAX lines, GROUP_MIN
        groups or more. None when no column does."""
        bal = (getattr(self, "balanced", None) or {}).get(t.tid)
        if bal:
            return self.col(t.tid, bal["col"])
        best = None
        for c in self.cols[t.tid]:
            if c.type not in ("text", "number") or c.sensitive or c.distinct_capped or c.distinct < GROUP_MIN \
                    or (_metric(c) and not profile_mod._numbers_as_ids(c)) \
                    or not (c.semantic == "identifier" or profile_mod.is_id_header(c.header)):
                continue
            multi = sum(1 for _, n in c.counter.items() if n > 1)
            if multi < GROUP_MIN or not 1.5 <= c.count / c.distinct <= GROUP_MAX:
                continue
            if best is None or multi > best[0]:
                best = (multi, c)
        return best[1] if best else None

    def _dr_cr(self, t):
        """(debit column, credit column) when the headers name both sides, else None."""
        lex = detect_mod._lexicons()
        nums = [c for c in self.cols[t.tid] if c.type == "number" and c.semantic == "metric" and not c.sensitive]

        def side(c, name):
            hn = detect_mod.norm_header(c.header)
            other = "credit" if name == "debit" else "debit"
            return detect_mod._lex_match(hn, lex[name]) > detect_mod._lex_match(hn, lex[other])
        dr = [c for c in nums if side(c, "debit")]
        cr = [c for c in nums if side(c, "credit")]
        return (dr[0], cr[0]) if len(dr) == 1 and len(cr) == 1 else None

    def _group_arithmetic(self, t, out: list):
        """For rows in groups under a repeated ID: a line category that is a fixed
        share of the rest of its side of the group (a tax, a fee), lines posted on
        the side opposite their category's usual side (marked by a shared text
        prefix, or by a lookup that gives each key its side), and a first group that
        carries opening balances in (its text says so, or it holds every money
        column's largest amount, far above the rest)."""
        from .findings import _NOTES
        gc = self._group_col(t)
        if gc is None:
            return
        sides = self._dr_cr(t)
        money = None if sides else _money_col(self, t)
        if not sides and money is None:
            return

        def at(r, j):
            return r[j] if j < len(r) else None

        def amount(r):
            """(amount with its sign, side or None) of a row; None when it has no amount."""
            if sides:
                d, k = at(r, sides[0].j), at(r, sides[1].j)
                if _is_num(d) and d:
                    return d, "debit"
                if _is_num(k) and k:
                    return k, "credit"
                return None
            v = at(r, money.j)
            return (v, None) if _is_num(v) and v else None
        groups: dict = {}
        for i, r in enumerate(t.rows):
            k = profile_mod.norm_key(at(r, gc.j))
            a = amount(r)
            if k is not None and a is not None:
                groups.setdefault(k, []).append((i, a[0], a[1]))
        groups = {k: v for k, v in groups.items() if len(v) >= 2}
        if len(groups) < GROUP_MIN:
            return
        cats = [c for c in self.cols[t.tid] if c.type == "text" and c.header != gc.header and not c.sensitive
                and not c.distinct_capped and 2 <= c.distinct <= GROUP_CATS and c.distinct <= 0.2 * c.count
                and not _NOTES.search(str(c.header))]
        found = [f for c in cats for f in [self._fixed_ratio(t, gc, c, groups, sides)] if f]
        if found:
            out.append(max(found, key=lambda f: f["numbers"]["hits"]))
        if sides:
            contra = next((f for c in cats for f in [self._contra_side(t, gc, c, sides)] if f), None)
            if contra:
                out.append(contra)
        if sides:
            moves = self._balance_moves(t, gc, cats, groups, sides)
            if moves:
                out.append(moves)
        opening = self._opening_group(t, gc, groups, sides, money)
        if opening:
            out.append(opening)
        closing = self._opening_group(t, gc, groups, sides, money, last=True)
        if closing:
            out.append(closing)

    def _fixed_ratio(self, t, gc, c, groups: dict, sides) -> dict | None:
        """A category B whose line is one fixed share r (at most RATIO_MAX) of the rest
        of its side of the group, or of one other category's lines there, to the
        cent in RATIO_SHARE of RATIO_GROUPS or more groups. Only lines of the same
        sign are compared: a negative line at a share of positive ones (a discount)
        is a negative, which the negatives question already asks about."""
        cat = {i: profile_mod.norm_key(r[c.j] if c.j < len(r) else None) for i, r in enumerate(t.rows)}
        shown = {}
        for i, r in enumerate(t.rows):
            if cat[i] is not None and cat[i] not in shown:
                shown[cat[i]] = str(r[c.j]).strip()
        pairs: dict = {}
        for lines in groups.values():
            keys = Counter(cat[i] for i, _, _ in lines)
            for i, amt, side in lines:
                b = cat[i]
                if b is None or keys[b] != 1:
                    continue
                rest = [(cat[x], abs(a)) for x, a, s in lines if x != i and s == side and (a > 0) == (amt > 0)]
                if not rest:
                    continue
                amt = abs(amt)
                pairs.setdefault((b, None), []).append((amt, sum(a for _, a in rest)))
                for a_key in {k for k, _ in rest if k is not None and k != b}:
                    pairs.setdefault((b, a_key), []).append((amt, sum(a for k, a in rest if k == a_key)))
        best = None
        for (b, base), xs in pairs.items():
            xs = [(v, d) for v, d in xs if d > 0]
            if len(xs) < RATIO_GROUPS:
                continue
            med = _median([v / d for v, d in xs])
            for r in {round(med, 4), round(med, 5)}:
                if not 0 < r <= RATIO_MAX:
                    continue
                hits = sum(1 for v, d in xs if abs(v - round(d * r, 2)) <= 0.011)
                if hits >= RATIO_SHARE * len(xs) and (best is None or hits > best[3]):
                    best = (b, base, r, hits, len(xs))
        if best is None:
            return None
        b, base, r, hits, n = best
        noun = _group_noun(gc.header)
        pct = f"{r * 100:.4g}%"
        side = next((s for lines in groups.values() for i, _, s in lines if cat[i] == b), None)
        word = _group_word(gc.header)
        of = (f"the {shown[base]} line" if base is not None else
              f"the other {side} lines of its {word}" if side else f"the rest of its {word}")
        rows = sum(1 for i in cat if cat[i] == b)
        return {"recipe": f"ratio:{t.tid}:{c.header}", "kind": "gotcha", "oddity": True, "weight": 3,
                "statement": f"In {hits:,} of {n:,} {noun} on {t.sheet} that have one, the {shown[b]} line in "
                             f"{c.header} is exactly {pct} of {of}.",
                "depends": [(t.sheet, c.header), (t.sheet, gc.header)],
                "numbers": {"table": t.tid, "col": c.header, "value": shown[b], "base": shown.get(base),
                            "rate": r, "pct": pct, "hits": hits, "groups": n, "group_col": gc.header,
                            "noun": noun, "of": of, "rows": rows}}

    def _contra_side(self, t, gc, c, sides) -> dict | None:
        """Lines on the side opposite their category's usual side: the usual side from
        a lookup that gives each key Debit or Credit, else from the category's own
        lines. Only a category with CONTRA_SIDE of its lines on its usual side counts
        (a bank account paid in and out holds both sides by nature). Found when a
        text prefix marks some of those lines (a prefix family), and only the
        marked lines are asked about. With no prefix, a lookup's normal side is
        enough only for categories whose own lines keep to it all but rarely
        (CONTRA_RARE of their lines or fewer on the other side, CONTRA_LINES_RARE
        lines or more): a side alone also flags the routine lines of accounts
        that move both ways, which a bank account's in and out lines never pass."""
        lookup = self._side_lookup(t, c)
        by: dict = {}
        for i, r in enumerate(t.rows):
            k = profile_mod.norm_key(r[c.j] if c.j < len(r) else None)
            d = r[sides[0].j] if sides[0].j < len(r) else None
            cr = r[sides[1].j] if sides[1].j < len(r) else None
            s = "debit" if _is_num(d) and d else "credit" if _is_num(cr) and cr else None
            if k is not None and s:
                by.setdefault(k, []).append((i, s))
        opposite, rare = [], []
        for k, lines in by.items():
            usual = lookup.get(k) if lookup else Counter(s for _, s in lines).most_common(1)[0][0]
            n = sum(1 for _, s in lines if s == usual)
            if usual and n >= CONTRA_SIDE * len(lines) and len(lines) >= CONTRA_LINES:
                opposite += [(i, k, s) for i, s in lines if s != usual]
                if lookup and len(lines) >= CONTRA_LINES_RARE and len(lines) - n <= CONTRA_RARE * len(lines):
                    rare += [(i, k, s) for i, s in lines if s != usual]
        if not opposite:
            return None
        prefix, marked = self._prefix_mark(t, [i for i, _, _ in opposite])
        if prefix is not None:
            opposite = [x for x in opposite if x[0] in marked]
        elif lookup:
            opposite = rare          # a lookup's side alone: only the rare lines of accounts that keep to it
        else:
            return None
        # with a list that gives each key a category, the lines split by what they give back: credits on cost
        # accounts, debits on income accounts; the balancing legs on balance-sheet accounts (a bank, a card) of
        # the same entries are left out
        groups = self._contra_groups(t, c, opposite, lookup, prefix)
        if groups is not None:
            opposite = [x for g in groups for x in g["lines"]]
        if len(opposite) < CONTRA_MIN:
            return None
        on = {s for _, _, s in opposite}
        side = Counter(s for _, _, s in opposite).most_common(1)[0][0]
        col = sides[0] if side == "debit" else sides[1]
        names = []
        for i, k, _ in opposite:
            v = str(t.rows[i][c.j]).strip()
            if v not in names:
                names.append(v)
        n = len(opposite)
        stmt = (f"{n:,} line{'s' if n != 1 else ''} on {t.sheet} post{'s' if n == 1 else ''} on the side opposite "
                f"{'its' if n == 1 else 'their'} {c.header}'s usual side ("
                + (f"{col.header} on " if len(on) == 1 else "") + f"{_listed(names)})")
        if groups and len(groups) > 1:
            stmt += ": " + " and ".join(f"{len(g['lines']):,} {g['side']}{'s' if len(g['lines']) != 1 else ''} on "
                                         f"{g['noun']} ({_listed(g['accounts'])})" for g in groups)
        if prefix is not None:
            pj, pword = prefix
            stmt += f", and all of them have {_a(t.headers[pj])} {t.headers[pj]} starting \"{pword}\""
        else:
            pj, pword = None, None
            ref = self._side_lookup_sheet(t, c)
            stmt += (f"; {ref} gives each {c.header} its side, and "
                     f"{'this one keeps' if len(names) == 1 else 'these keep'} to it on every other line")
        return {"recipe": f"contra:{t.tid}:{c.header}", "kind": "gotcha", "oddity": True, "weight": 3,
                "statement": stmt + ".",
                "depends": [(t.sheet, c.header), (t.sheet, col.header)],
                "numbers": {"table": t.tid, "col": c.header, "side_col": col.header, "rows": n,
                            "row_ids": [i for i, _, _ in opposite][:200], "values": names[:12],
                            "prefix": pword, "prefix_col": t.headers[pj] if pj is not None else None,
                            "lookup": bool(lookup),
                            "groups": [{"kind": g["kind"], "side": g["side"], "noun": g["noun"],
                                        "accounts": g["accounts"], "rows": len(g["lines"]),
                                        "row_ids": [i for i, _k, _s in g["lines"]][:200],
                                        "memo": self._memo_words(t, g["lines"], pj, pword)}
                                       for g in groups or []]}}

    # a category that names where a key sits on the balance sheet, never income or a cost
    _BALANCE = re.compile(r"\b(bank|cash|card|credit card|liabilit\w*|assets?|equity|payable|receivable|loans?|"
                          r"deposits?|inventory|capital|accrued|prepaid|clearing|suspense|fixed)\b", re.I)
    _INCOME = re.compile(r"\b(income|revenue|sales?)\b", re.I)
    _COST = re.compile(r"\b(expenses?|costs?|cogs|cost of goods|purchases?|overhead)\b", re.I)

    def _contra_groups(self, t, c, opposite: list, lookup: dict, prefix):
        """[{kind, side, noun, accounts, lines}] of lines on the unusual side split by
        the category a list gives each key: credits on cost accounts and debits on
        income accounts, the lines on balance-sheet accounts left out. None when no
        list gives a category (or every line is on one kind of account)."""
        cats = self._key_categories(t, c)
        if not cats or not opposite:
            return None
        out = {}
        for i, k, s in opposite:
            cat = cats.get(k) or ""
            if self._BALANCE.search(cat):
                continue
            if self._INCOME.search(cat) or (not self._COST.search(cat) and lookup.get(k) == "credit"):
                kind = "income"
            elif self._COST.search(cat) or lookup.get(k) == "debit":
                kind = "cost"
            else:
                continue
            out.setdefault((kind, s), []).append((i, k, s))
        if not out:
            return []
        groups = []
        for (kind, s), lines in sorted(out.items(), key=lambda kv: (kv[0][0] != "cost", kv[0][1])):
            names = list(dict.fromkeys(str(t.rows[i][c.j]).strip() for i, _k, _s in lines))
            groups.append({"kind": kind, "side": s, "noun": f"{kind} accounts" if kind == "income" else "cost accounts",
                           "accounts": names, "lines": lines})
        return groups

    def _balance_moves(self, t, gc, cats: list, groups: dict, sides) -> dict | None:
        """Entries whose every line sits on a balance-sheet account of the list the
        account column looks up: money moved from one bank account to another, a
        card or loan paid from the bank, the owner's money taken out to equity or
        put in from it. Grouped by the accounts, with how many entries and their
        commonest memo. None when no list gives each account a category, or no
        group has 2 entries or more."""
        acct = next((c for c in cats if self._key_categories(t, c)), None)
        if acct is None:
            return None
        kinds = self._key_categories(t, acct)
        memo = next((c for c in self.cols[t.tid] if c.type == "text" and c is not acct and c is not gc
                     and _NOTE_HEADER.search(str(c.header))), None)
        bank_money = 0.0
        found: dict = {}
        for _k, lines in groups.items():
            cls = []
            for i, amt, side in lines:
                a = profile_mod.norm_key(t.rows[i][acct.j] if acct.j < len(t.rows[i]) else None)
                cat = kinds.get(a) or ""
                cls.append((i, a, side, abs(amt), cat))
                if re.search(r"\b(bank|cash|checking|savings)\b", cat, re.I):
                    bank_money += abs(amt)
            if not cls or not all(self._BALANCE.search(c[4]) for c in cls):
                continue
            banks = [c for c in cls if re.search(r"\b(bank|cash|checking|savings)\b", c[4], re.I)]
            other = [c for c in cls if c not in banks]
            debit = [c for c in cls if c[2] == "debit"]
            credit = [c for c in cls if c[2] == "credit"]
            if not debit or not credit:
                continue
            if not other:
                kind = "transfer"
            elif all(re.search(r"\b(card|liabilit\w*|loans?|payable)\b", c[4], re.I) for c in other) and banks:
                kind = "payment"
            elif all(re.search(r"\b(equity|capital|draws?)\b", c[4], re.I) for c in other) and banks:
                kind = "draw" if all(c[2] == "debit" for c in other) else "put_in"
            else:
                continue
            frm = tuple(sorted({str(t.rows[c[0]][acct.j]).strip() for c in credit}))
            to = tuple(sorted({str(t.rows[c[0]][acct.j]).strip() for c in debit}))
            g = found.setdefault((kind, frm, to), {"entries": 0, "rows": [], "money": 0.0, "memos": Counter()})
            g["entries"] += 1
            g["rows"] += [c[0] for c in cls]
            g["money"] += sum(c[3] for c in debit)
            if memo is not None:
                m = str(t.rows[cls[0][0]][memo.j] if memo.j < len(t.rows[cls[0][0]]) else "").strip()
                if m:
                    g["memos"][m] += 1
        got = [(k, g) for k, g in found.items() if g["entries"] >= 2]
        if not got:
            return None
        got.sort(key=lambda kg: (-kg[1]["money"], kg[0]))
        out = []
        for (kind, frm, to), g in got[:3]:
            m = g["memos"].most_common(1)[0][0] if g["memos"] else ""
            out.append({"kind": kind, "from": list(frm), "to": list(to), "entries": g["entries"],
                        "rows": len(g["rows"]), "row_ids": g["rows"][:400], "money": round(g["money"], 2), "memo": m})
        said = "; ".join(_move_words(x) for x in out)
        return {"recipe": f"moves:{t.tid}:{acct.header}", "kind": "structure", "weight": 1,
                "statement": f"On {t.sheet}, entries that only move money between balance-sheet accounts: {said}.",
                "depends": [(t.sheet, acct.header)],
                "numbers": {"table": t.tid, "col": acct.header, "groups": out,
                            "memo_col": memo.header if memo is not None else "",
                            "share": round(sum(x["money"] for x in out) / bank_money, 4) if bank_money else 0.0}}

    def _key_categories(self, t, c) -> dict:
        """{key: its category} from the list this column looks up: a text column
        there (not the key, not the side) with 2 to 15 values."""
        got = self._side_ref(t, c)
        ref, kc = (got[0], got[1]) if got else (None, None)
        if ref is None:
            j = next((j for j in self.joins if j["from_table"] == t.tid and j["from_col"] == c.header
                      and j["band"] == "auto"), None)
            if j is None:
                return {}
            ref, kc = self.table(j["to_table"]), self.col(j["to_table"], j["to_col"])
        if kc is None:
            return {}
        side = got[2] if got else None
        cat = next((x for x in self.cols[ref.tid] if x.type == "text" and x is not kc and x is not side
                    and 2 <= x.distinct <= 15 and not x.distinct_capped), None)
        if cat is None:
            return {}
        out = {}
        for r in ref.rows:
            k = profile_mod.norm_key(r[kc.j] if kc.j < len(r) else None)
            v = r[cat.j] if cat.j < len(r) else None
            if k is not None and v is not None:
                out[k] = str(v).strip()
        return out

    def _memo_words(self, t, lines: list, pj, pword) -> str:
        """The commonest words after a prefix on these lines ('Refund: late
        booking' gives 'late booking'), up to 3 words; '' without a
        prefix."""
        if pj is None or not pword:
            return ""
        got = Counter()
        for i, _k, _s in lines:
            v = str(t.rows[i][pj] if pj < len(t.rows[i]) else "")
            rest = re.sub(r"^\s*" + re.escape(pword) + r"\s*[:\-]?\s*", "", v, flags=re.I).strip()
            if rest:
                got[" ".join(rest.split()[:3])] += 1
        if not got:
            return ""
        word, n = got.most_common(1)[0]
        return word if n >= 2 or len(got) == 1 else ""

    def _side_lookup(self, t, c) -> dict:
        """{key: 'debit' | 'credit'} from a table this column looks up whose column
        holds only Debit and Credit (or Dr and Cr): each key's normal side."""
        got = self._side_ref(t, c)
        if got is None:
            return {}
        ref, kc, sc = got
        out = {}
        for r in ref.rows:
            k = profile_mod.norm_key(r[kc.j] if kc.j < len(r) else None)
            s = str(r[sc.j] if sc.j < len(r) else "").strip().lower()
            if k is not None and s:
                out[k] = "debit" if s.startswith("d") else "credit"
        return out

    def _side_ref(self, t, c):
        """(lookup table, its key column, its Debit or Credit column), or None."""
        for j in self.joins:
            if j["from_table"] != t.tid or j["from_col"] != c.header:
                continue
            ref = self.table(j["to_table"])
            kc = self.col(ref.tid, j["to_col"])
            for sc in self.cols[ref.tid]:
                vals = {str(k).lower() for k in sc.counter} if sc.type == "text" else set()
                if vals and vals <= {"debit", "credit", "dr", "cr"} and kc is not None:
                    return ref, kc, sc
        return None

    def _side_lookup_sheet(self, t, c) -> str:
        """'Normal Side on Chart': where the lookup of each key's side is."""
        got = self._side_ref(t, c)
        return f"{got[2].header} on {got[0].sheet}" if got else "a lookup"

    def _prefix_mark(self, t, rows: list) -> tuple:
        """((column, prefix), rows it marks) when a text prefix family ('Refund:',
        'Return -') marks some of these rows: CONTRA_MIN or more of them start with
        it, and PREFIX_SHARE of the table's rows with it are among them (a category
        that sits on both sides by nature, like cash, adds rows no prefix marks).
        (None, set()) else."""
        best = (None, set())
        mine = set(rows)
        for c in self.cols[t.tid]:
            if c.type != "text" or c.sensitive:
                continue
            fams = _prefix_families(t, c.j, CONTRA_MIN)
            for word, idx in fams.items():
                hit = idx & mine
                if len(hit) >= CONTRA_MIN and len(hit) >= PREFIX_SHARE * len(idx) and len(hit) > len(best[1]):
                    best = ((c.j, word), hit)
        return best

    def _opening_group(self, t, gc, groups: dict, sides, money, last: bool = False) -> dict | None:
        """The group on the earliest date that carries balances in: its text says so
        (an opening or beginning balance, brought or carried forward; a bare 'opening'
        is an ordinary first entry), or it holds the largest amount of every money
        column and that amount is OPENING_TIMES the largest outside it or more.
        last: the group on the latest date that closes the period out (a closing
        entry, a year-end close, balances carried forward or down; a bare 'closing'
        is an ordinary last entry), found by its amounts alone only where the table
        has a debit and credit pair (a big last order is only a big order)."""
        aj = self._axis_j(t)
        if aj is None:
            return None
        day = {}
        for k, lines in groups.items():
            ds = [t.rows[i][aj] for i, _, _ in lines if aj < len(t.rows[i]) and hasattr(t.rows[i][aj], "year")]
            if ds:
                day[k] = max(ds) if last else min(ds)
        if not day:
            return None
        first = max(day.values()) if last else min(day.values())
        cands = [k for k, d in day.items() if d == first]
        texts = [c for c in self.cols[t.tid] if c.type == "text" and not c.sensitive and c.header != gc.header]
        words = _CLOSING_WORDS if last else _OPENING_WORDS
        cols = list(sides) if sides else [money]

        def reading(g):
            """(what its text says, whether it holds every money column's maxima, the maxima)."""
            mine = [i for i, _, _ in groups[g]]
            said = next((str(t.rows[i][c.j]).strip() for i in mine for c in texts
                         if c.j < len(t.rows[i]) and isinstance(t.rows[i][c.j], str)
                         and words.search(t.rows[i][c.j])), "")
            biggest = []
            own = set(mine)
            for c in cols:
                inside = max((abs(t.rows[i][c.j]) for i in mine if c.j < len(t.rows[i]) and _is_num(t.rows[i][c.j])),
                             default=0.0)
                outside = max((abs(r[c.j]) for i, r in enumerate(t.rows) if i not in own and c.j < len(r)
                               and _is_num(r[c.j])), default=0.0)
                biggest.append((c.header, inside, outside))
            maxima = all(i > 0 and i >= OPENING_TIMES * o for _, i, o in biggest) and (bool(sides) or not last)
            return mine, said, maxima, biggest
        # several entries on the first (last) date: the one whose text says so, else the one holding every money
        # column's maxima; none, or more than one, and nothing is said
        read = {g: reading(g) for g in cands}
        said_by = [g for g in cands if read[g][1]]
        big_by = [g for g in cands if read[g][2]]
        pick = said_by if said_by else big_by
        if len(pick) != 1:
            return None
        g = pick[0]
        mine, said, maxima, biggest = read[g]
        if not said and not maxima:
            return None
        shown = str(next(t.rows[i][gc.j] for i in mine)).strip()
        # the totals of what happened in the period; a level (a running balance) keeps what was carried in
        stock = self._stock_cols(t)
        flows = [c.header for c in self.cols[t.tid] if _metric(c) and not c.sensitive and c not in stock]
        why = []
        if said:
            why.append(f"its text says \"{said[:60]}\"")
        if maxima:
            why.append("it holds the largest " + _join_words([f"{h} ({v:,.2f})" for h, v, _ in biggest]))
        return {"recipe": f"{'closing' if last else 'opening'}:{t.tid}:{gc.header}", "kind": "gotcha",
                "oddity": True, "weight": 3,
                "statement": f"{gc.header} {shown} on {t.sheet} is the {'last' if last else 'first'} "
                             f"{_group_word(gc.header)} ({_day_words(first.toordinal())}, {len(mine)} lines) and "
                             + " and ".join(why) + ".",
                "depends": [(t.sheet, gc.header)],
                "numbers": {"table": t.tid, "col": gc.header, "value": shown, "lines": len(mine),
                            "row_ids": mine, "date": first.isoformat()[:10], "said": said[:60], "maxima": maxima,
                            "flows": flows}}

    # ------------------------------------------------------------------
    # balances carried in by shape, a charge that is never paid, a group counted in another unit
    # ------------------------------------------------------------------
    def _carried_in(self, t, out: list):
        """Rows that carry balances in from before the data's dates, found by their
        shape when no entry number gathers them: a code on a few rows, all on the
        table's first date, whose text names a date before that day or says it is a
        balance brought forward, and that either leave a column blank that the other
        rows fill or sit on one side of a debit and credit pair only. Stated and asked
        as an opening entry (it is left out of the period's activity on that pick)."""
        if any(i.get("recipe", "").startswith(f"opening:{t.tid}:") for i in out):
            return
        aj = self._axis_j(t)
        if aj is None:
            return
        days = [r[aj].toordinal() for r in t.rows if aj < len(r) and hasattr(r[aj], "year")]
        if not days:
            return
        first = min(days)
        pair = [self.col(t.tid, h) for h in ((self.detection.get("pairs") or {}).get(t.tid) or [])]
        pair = [c for c in pair if c is not None]
        texts = [c for c in self.cols[t.tid] if c.type == "text" and not c.sensitive]
        for c in self.cols[t.tid]:
            if c.type != "text" or c.sensitive or not (2 <= c.distinct <= 60) or c.distinct_capped:
                continue
            rows_of: dict = {}
            for i, r in enumerate(t.rows):
                k = profile_mod.norm_key(r[c.j] if c.j < len(r) else None)
                if k is not None:
                    rows_of.setdefault(k, []).append(i)
            for k, rs in rows_of.items():
                if not 2 <= len(rs) <= max(3, 0.05 * t.n_rows):
                    continue
                if not all(aj < len(t.rows[i]) and hasattr(t.rows[i][aj], "year")
                           and t.rows[i][aj].toordinal() == first for i in rs):
                    continue
                said = ""
                # a text on the rows (never the code itself) naming a date before the first, else a carry-forward
                for want_date in (True, False):
                    for i in rs:
                        for x in texts:
                            v = t.rows[i][x.j] if x.j < len(t.rows[i]) else None
                            if x is c or not isinstance(v, str):
                                continue
                            said_days = [_title_day(m) for m in
                                         re.findall(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b|\b\d{4}-\d{2}-\d{2}\b", v)]
                            before = any(d is not None and d.toordinal() < first for d in said_days)
                            if before if want_date else _OPENING_WORDS.search(v):
                                said = v.strip()
                                break
                        if said:
                            break
                    if said:
                        break
                if not said:
                    continue
                mine = set(rs)
                blank = [x for x in self.cols[t.tid] if x is not c and all(
                    _blank(t.rows[i][x.j] if x.j < len(t.rows[i]) else None) for i in rs)
                    and x.count >= 0.9 * (t.n_rows - len(rs))]
                one_side = len(pair) == 2 and any(
                    all(_is_num(t.rows[i][p.j] if p.j < len(t.rows[i]) else None) and t.rows[i][p.j]
                        for i in rs)
                    and not any(_is_num(t.rows[i][o.j] if o.j < len(t.rows[i]) else None) and t.rows[i][o.j]
                                for i in rs)
                    for p, o in (pair, pair[::-1]))
                if not blank and not one_side:
                    continue
                shown = _written_key(t, c, k)
                stock = self._stock_cols(t)
                flows = [x.header for x in self.cols[t.tid] if _metric(x) and not x.sensitive and x not in stock]
                why = [f"its text says \"{said[:60]}\""]
                if blank:
                    why.append(f"{_join_words([x.header for x in blank[:2]])} {'is' if len(blank[:2]) == 1 else 'are'} "
                               "blank on them")
                if one_side:
                    why.append("they sit on one side only")
                out.append({"recipe": f"opening:{t.tid}:{c.header}", "kind": "gotcha", "oddity": True, "weight": 3,
                            "statement": f"{c.header} {shown} on {t.sheet} is on {len(rs):,} rows, all on the first date "
                                         f"({_day_words(first)}), and " + " and ".join(why) + ".",
                            "depends": [(t.sheet, c.header)],
                            "numbers": {"table": t.tid, "col": c.header, "value": shown, "lines": len(rs),
                                        "row_ids": sorted(mine), "date": _iso_of(first), "said": said[:60],
                                        "maxima": False, "flows": flows, "carried": True}})
                return

    def _nonstock(self, lt, out: list):
        """On a list keyed by a unique column (items), a category whose rows all leave
        a number blank (no cost) while every other category fills it: things that are
        not stock (gift cards, fees). Carried through the join to the lines that look
        the list up, with their rows, money and whether they ever carry the lines'
        adjustment (never discounted). Stated on the lines' table."""
        from .findings import money_column, money_fmt
        cols = self.cols[lt.tid]
        keys = [c for c in cols if c.unique and c.type in ("text", "number") and c.count >= 5 and not c.sensitive]
        if not keys:
            return
        joins = [j for j in self.joins if j["band"] == "auto" and j["to_table"] == lt.tid
                 and any(j["to_col"] == k.header for k in keys)]
        if not joins:
            return
        j = joins[0]
        ft = self.table(j["from_table"])
        if ft.n_rows <= lt.n_rows or j["from_col"] not in ft.headers:
            return
        key = self.col(lt.tid, j["to_col"])
        cats = [c for c in cols if c.type == "text" and not c.unique and not c.sensitive and 2 <= c.distinct <= 30]
        nums = [c for c in cols if c.type == "number" and c.semantic == "metric" and not c.sensitive]
        for cat in cats:
            rows_of: dict = {}
            for i, r in enumerate(lt.rows):
                k = profile_mod.norm_key(r[cat.j] if cat.j < len(r) else None)
                if k is not None:
                    rows_of.setdefault(k, []).append(i)
            if len(rows_of) < 3:
                continue
            for x in nums:
                empty = [k for k, rs in rows_of.items() if len(rs) >= 2 and all(
                    _blank(lt.rows[i][x.j] if x.j < len(lt.rows[i]) else None) for i in rs)]
                filled = all(sum(1 for i in rs if not _blank(lt.rows[i][x.j] if x.j < len(lt.rows[i]) else None))
                             >= 0.95 * len(rs) for k, rs in rows_of.items() if k not in empty)
                if len(empty) != 1 or not filled:
                    continue
                k = empty[0]
                mine = {profile_mod.norm_key(lt.rows[i][key.j] if key.j < len(lt.rows[i]) else None)
                        for i in rows_of[k]} - {None}
                fj = ft.headers.index(j["from_col"])
                lines = [i for i, r in enumerate(ft.rows) if fj < len(r) and profile_mod.norm_key(r[fj]) in mine]
                if len(lines) < 5:
                    continue
                m = money_column(self, ft)
                if m is not None and _ADJUST_HEADER.search(str(m.header)):
                    m = None          # an amount taken off is never what the lines are worth
                worth = self._line_worth(ft, m)
                tot = sum(worth(r) for r in ft.rows)
                part = sum(worth(ft.rows[i]) for i in lines)
                label_m = m.header if m is not None else ("Net" if "Net" not in ft.headers else "Net Amount") \
                    if worth is not _none_worth else ""
                adj = next((a for a in self.cols[ft.tid] if _metric(a) and _ADJUST_HEADER.search(str(a.header))), None)
                never = ""
                if adj is not None:
                    own = set(lines)
                    on = sum(1 for i in lines if adj.j < len(ft.rows[i]) and _is_num(ft.rows[i][adj.j])
                             and ft.rows[i][adj.j])
                    rest = [i for i in range(ft.n_rows) if i not in own]
                    others = sum(1 for i in rest if adj.j < len(ft.rows[i]) and _is_num(ft.rows[i][adj.j])
                                 and ft.rows[i][adj.j])
                    if not on and rest and others >= 0.1 * len(rest):
                        never = adj.header
                ex = sorted(_written_key(lt, key, v) for v in mine)[:3]
                label = _written_key(lt, cat, k)
                fmt = money_fmt(self, ft, m) if m is not None else (lambda v: f"{v:,.2f}")
                out.append({"recipe": f"nonstock:{ft.tid}:{lt.tid}:{cat.header}:{label}", "kind": "gotcha",
                            "oddity": True, "weight": 3,
                            "statement": f"{label} in {cat.header} on {lt.sheet} ({len(rows_of[k])} of {lt.n_rows} "
                                         f"{_plural(key.header, lt.n_rows)}) has no {x.header} on any row, where every "
                                         f"other {cat.header} has one; its {len(lines):,} lines on {ft.sheet} carry "
                                         + (f"{fmt(part)} of {label_m}" if label_m else "no amount")
                                         + (f" and never a {never}" if never else "") + ".",
                            "depends": [(lt.sheet, cat.header), (lt.sheet, x.header), (ft.sheet, j["from_col"])],
                            "numbers": {"table": ft.tid, "col": j["from_col"], "lookup": lt.tid, "category": label,
                                        "cat_col": cat.header, "blank_col": x.header, "keys": ex,
                                        "values": sorted(_written_key(lt, key, v) for v in mine),
                                        "rows": len(lines), "row_ids": lines[:5000], "money": label_m,
                                        "derived": m is None and bool(label_m),
                                        "share": round(part / tot, 6) if tot else 0.0, "adj": never,
                                        "items": len(rows_of[k])},
                            "files": [self.file_of[ft.tid]]})
                return

    def _line_worth(self, t, m):
        """What one row of t is worth: its money column, else its quantity times its
        price (a table whose money has to be worked out), else nothing."""
        from .findings import _COUNT_WORDS
        if m is not None:
            return lambda r: abs(r[m.j]) if m.j < len(r) and _is_num(r[m.j]) else 0.0
        price = self._price_col(t)
        qty = next((c for c in self.cols[t.tid] if _metric(c) and c.integers and c is not price
                    and _COUNT_WORDS.search(str(c.header))), None)
        if price is None or qty is None:
            return _none_worth
        return lambda r: abs(r[qty.j] * r[price.j]) if max(qty.j, price.j) < len(r) and _is_num(r[qty.j]) \
            and _is_num(r[price.j]) else 0.0

    def _unpaid(self, t, out: list):
        """On a table of charges and payments (a debit and credit pair), an ID charged
        in half the months or more that never pays, where the other IDs pay: summed
        per ID over the whole period, each side on its own."""
        from .detect import pair_sides
        pair = [self.col(t.tid, h) for h in (self.detection.get("pairs") or {}).get(t.tid) or []]
        sides = pair_sides(pair) if len(pair) == 2 and None not in pair else None
        if not sides:
            return
        dc, cc = sides
        aj = self._axis_j(t)
        if dc is None or cc is None or aj is None:
            return
        ids = [c for c in self.cols[t.tid] if c.semantic == "identifier" and c.type in ("text", "number")
               and not c.codes and not c.distinct_capped and c.distinct >= UNPAID_IDS and c.count
               and c.distinct / c.count < 0.5]
        if not ids:
            return
        idc = ids[0]
        months = {(r[aj].year, r[aj].month) for r in t.rows if aj < len(r) and hasattr(r[aj], "year")}
        per: dict = {}
        for i, r in enumerate(t.rows):
            k = profile_mod.norm_key(r[idc.j] if idc.j < len(r) else None)
            if k is None:
                continue
            d, cr = (r[dc.j] if dc.j < len(r) else None), (r[cc.j] if cc.j < len(r) else None)
            x = per.setdefault(k, {"dr": 0.0, "cr": 0.0, "months": set(), "rows": []})
            x["rows"].append(i)
            if _is_num(d) and d:
                x["dr"] += abs(d)
                if hasattr(r[aj], "year"):
                    x["months"].add((r[aj].year, r[aj].month))
            if _is_num(cr) and cr:
                x["cr"] += abs(cr)
        charged = {k: x for k, x in per.items() if x["dr"] > 0}
        never = [k for k, x in charged.items() if x["cr"] == 0 and len(x["months"]) >= 0.5 * len(months)
                 and len(x["months"]) >= 3]
        peers = sorted(x["cr"] / x["dr"] for k, x in charged.items() if k not in never)
        if not never or len(never) > 3 or len(peers) < 10 or sum(1 for p in peers if p >= 0.3) < 0.9 * len(peers):
            return
        from .recipes import pct
        lo, hi = peers[int(0.05 * len(peers))], peers[int(0.95 * len(peers)) - 1]
        k = max(never, key=lambda k: charged[k]["dr"])
        x = charged[k]
        who = _written_key(t, idc, k)
        where = self._placed(t, idc, x["rows"])
        loc = f" ({where})" if where else ""
        from .findings import money_fmt
        fmt = money_fmt(self, t, dc)
        side = sum(x2["dr"] for x2 in charged.values())
        out.append({"recipe": f"unpaid:{t.tid}:{idc.header}", "kind": "gotcha", "oddity": True, "weight": 3,
                    "statement": f"{who}{loc} on {t.sheet} is charged in {len(x['months'])} of {len(months)} months "
                                 f"({len(x['rows']):,} rows, {fmt(x['dr'])} of {dc.header}) and never pays, where every "
                                 f"other {idc.header} pays {pct(lo)} to {pct(hi)} of its charges.",
                    "depends": [(t.sheet, idc.header), (t.sheet, dc.header), (t.sheet, cc.header)],
                    "numbers": {"table": t.tid, "col": idc.header, "value": who, "where": where,
                                "rows": len(x["rows"]), "row_ids": x["rows"][:2000], "debit": dc.header,
                                "credit": cc.header, "charged": round(x["dr"], 2), "months": len(x["months"]),
                                "of": len(months), "lo": lo, "hi": hi,
                                "share": round(x["dr"] / side, 6) if side else 0.0}})

    def _placed(self, t, idc, rows: list) -> str:
        """Where an ID's rows are: up to two columns holding one value on all of them
        ('12, ELM WAY'), in the table's order; '' when none does."""
        bits = []
        for c in self.cols[t.tid]:
            # a place: words or a unit number written as text, never a code (an account) or a date
            if c is idc or c.sensitive or c.type != "text" or c.codes or c.semantic == "temporal" or c.distinct < 3:
                continue
            vals = {profile_mod.norm_key(t.rows[i][c.j] if c.j < len(t.rows[i]) else None) for i in rows}
            if len(vals) == 1 and None not in vals and (c.distinct >= 3 and c.distinct < 0.8 * t.n_rows):
                bits.append(_written_key(t, c, vals.pop()))
            if len(bits) == 2:
                break
        return ", ".join(bits)

    def _unit_groups(self, t, out: list):
        """A group whose quantity is in another unit (visits where the rest count
        hours, cases where the rest count pieces): on a table where quantity x rate =
        money, a group whose quantity median is a third of every peer's or less while
        its rate median is 1.5 times every peer's or more, so the money stays in
        range. Whole numbers only, where the peers are not, and a secondary measure
        at zero on every row, where the peers use it, add weight."""
        from .findings import money_column
        price, m = self._price_col(t), money_column(self, t)
        if price is None or m is None or price is m:
            return
        from .findings import _COUNT_WORDS
        nums = [c for c in self.cols[t.tid] if _metric(c) and not c.sensitive and c not in (price, m)]
        rows = t.rows
        for q in nums:
            # the rows where no other count adds to the money (overtime hours paid on top): there quantity x rate
            # is the money, to the cent, on UNIT_EXACT of them
            extra = [c for c in nums if c is not q and _COUNT_WORDS.search(str(c.header))]
            base = [i for i, r in enumerate(rows) if max(q.j, price.j, m.j) < len(r) and _is_num(r[q.j])
                    and _is_num(r[price.j]) and _is_num(r[m.j])
                    and not any(x.j < len(r) and _is_num(r[x.j]) and r[x.j] for x in extra)]
            ok = [i for i in base if abs(rows[i][q.j] * rows[i][price.j] - rows[i][m.j]) <= 0.0051]
            if len(ok) < UNIT_EXACT * len(base) or len(ok) < 20:
                continue
            exact, plain = set(ok), set(base)
            for g in [c for c in self.cols[t.tid] if _grouping(c) and 3 <= c.distinct <= 30]:
                rows_of: dict = {}
                for i, r in enumerate(rows):
                    k = profile_mod.norm_key(r[g.j] if g.j < len(r) else None)
                    if k is not None and _is_num(r[q.j] if q.j < len(r) else None) \
                            and _is_num(r[price.j] if price.j < len(r) else None):
                        rows_of.setdefault(k, []).append(i)
                big = {k: rs for k, rs in rows_of.items() if len(rs) >= ODD_ROWS}
                if len(big) < 3:
                    continue
                qmed = {k: _median([rows[i][q.j] for i in rs]) for k, rs in big.items()}
                pmed = {k: _median([rows[i][price.j] for i in rs]) for k, rs in big.items()}
                for k, rs in big.items():
                    peers = [x for x in big if x != k]
                    if not all(qmed[k] > 0 and qmed[k] <= UNIT_QTY * qmed[x] and pmed[k] >= UNIT_RATE * pmed[x]
                               for x in peers):
                        continue
                    own = [i for i in rs if i in plain]
                    if len(own) < 0.5 * len(rs) or sum(1 for i in own if i in exact) < UNIT_EXACT * len(own):
                        continue          # the product holds on the group's own rows: the unit, not a slip
                    self._unit_fact(t, g, k, rs, big, q, price, m, nums, out)
                    return

    def _unit_fact(self, t, g, k, rs: list, big: dict, q, price, m, nums: list, out: list):
        from .findings import money_fmt
        from .recipes import fmt_num, pct
        rows = t.rows
        mine = [rows[i][q.j] for i in rs]
        theirs = [rows[i][q.j] for x, v in big.items() if x != k for i in v]
        pm = [rows[i][price.j] for i in rs]
        pt = [rows[i][price.j] for x, v in big.items() if x != k for i in v]
        whole = all(float(v).is_integer() for v in mine) and not all(float(v).is_integer() for v in theirs)
        idle = []
        for s in nums:
            if s is q:
                continue
            on_mine = sum(1 for i in rs if s.j < len(rows[i]) and _is_num(rows[i][s.j]) and rows[i][s.j])
            on_theirs = sum(1 for x, v in big.items() if x != k for i in v
                            if s.j < len(rows[i]) and _is_num(rows[i][s.j]) and rows[i][s.j])
            if not on_mine and on_theirs >= 0.1 * len(theirs):
                idle.append(s.header)
        tot = sum(abs(r[m.j]) for r in rows if m.j < len(r) and _is_num(r[m.j]))
        share = sum(abs(rows[i][m.j]) for i in rs if m.j < len(rows[i]) and _is_num(rows[i][m.j])) / tot if tot else 0
        f = money_fmt(self, t, price)
        val = _written_key(t, g, k)
        rng = lambda xs: f"{fmt_num(round(min(xs), 2))} to {fmt_num(round(max(xs), 2))}"  # noqa: E731
        bits = [f"{q.header} runs {rng(mine)}" + (", whole numbers" if whole else "")
                + f", where the other {g.header} values run {rng(theirs)}",
                f"{price.header} is {f(min(pm))} to {f(max(pm))} there, against {f(min(pt))} to {f(max(pt))}"]
        if idle:
            bits.append(f"no {idle[0]} on any of its rows")
        out.append({"recipe": f"unitgroup:{t.tid}:{q.header}:{g.header}:{val}", "kind": "gotcha", "oddity": True,
                    "weight": 3 + whole + bool(idle),
                    "statement": f"On {g.header} {val} rows on {t.sheet} ({len(rs):,} rows, {pct(share)} of {m.header}), "
                                 + "; ".join(bits) + ".",
                    "depends": [(t.sheet, q.header), (t.sheet, g.header), (t.sheet, price.header)],
                    "numbers": {"table": t.tid, "col": q.header, "group_col": g.header, "value": val,
                                "rows": len(rs), "row_ids": rs[:5000], "share": round(share, 6), "price": price.header,
                                "money": m.header, "whole": whole, "idle": idle[:2], "text": "; ".join(bits)}})

    # ------------------------------------------------------------------
    # what a row is and how the dates fall: counted facts, never questions
    # ------------------------------------------------------------------
    def _grain(self):
        """What a row is and how its dates fall, settled by code and stated with
        its counts: a grid whose columns are periods, the date or period a title
        names, the cycle the dates follow, a snapshot panel whose stock columns
        are read on one date, and entries whose lines net to zero."""
        for t in self.tables:
            if t.wide:
                self._wide_fact(t)
            elif t.n_rows and t.tid not in self.derived:
                self._title_facts(t)
                self._cadence_fact(t)
                self._snapshot_fact(t)
                self._balanced_fact(t)

    def _grain_fact(self, t, kind: str, stmt: str, depends: list, numbers: dict):
        self.grain_facts.append({"recipe": f"grain:{kind}:{t.tid}", "kind": "grain", "statement": stmt,
                                 "depends": depends, "numbers": dict(numbers, table=t.tid),
                                 "files": [self.file_of[t.tid]]})

    def period_headers(self, t) -> list:
        """A grid's period columns ('Jan 2025', 'Q1 2025', '2025-01-01'), in order; [] on other tables."""
        return [h for h in t.headers if _period_kind(h)] if t.wide else []

    def _wide_fact(self, t):
        """A grid with its periods across: each row is a line item and each column
        a month, quarter or year, first to last, with how many."""
        per = self.period_headers(t)
        if len(per) < 4:
            return
        kind = Counter(_period_kind(h) for h in per).most_common(1)[0][0]
        if kind == "date":
            kind = _date_step(per)
        lines = len(self.row_labels(t)) or t.n_rows
        stmt = (f"On {t.sheet}, each row is a line item and each column is a {kind}, {_period_words(per[0], kind)} "
                f"to {_period_words(per[-1], kind)} ({len(per)} periods, {lines:,} line item{'s' if lines != 1 else ''}).")
        self._grain_fact(t, "wide", stmt, [], {"sheet": t.sheet, "period": kind, "periods": len(per),
                                               "first": per[0], "last": per[-1], "lines": lines})

    def _title_facts(self, t):
        """The date a title or a run note names: 'as of' a date is a snapshot, and a
        period (a month, a quarter, a year, two dates) or a run date that the rows
        run past is a counted fact whose end is a candidate boundary."""
        text = " . ".join(x for x in [t.title] + list(t.notes) if x)
        aj = self._axis_j(t)
        stamps = [r[aj] for r in t.rows if aj is not None and aj < len(r) and hasattr(r[aj], "year")]
        got = _title_period(text, {d.year for d in stamps}) if text else None
        if not got:
            return
        days = [d.toordinal() for d in stamps]
        end = got["end"].toordinal()
        after = sum(1 for d in days if d > end)
        self.title_period[t.tid] = {"said": got["said"], "end": end, "kind": got["kind"]}
        axis = self.cols[t.tid][aj].header if aj is not None else ""
        numbers = {"sheet": t.sheet, "said": got["said"], "date": got["end"].isoformat(), "after": after,
                   "rows": len(days), "col": axis}
        if got["kind"] == "as_of":
            self.title_dates[t.tid] = end
            stmt = f"{t.sheet}'s title says \"{got['said']}\", so its rows are a snapshot as of {_day_words(end)}"
            if after:
                stmt += (f"; {after:,} of {len(days):,} rows are dated after it (the last on "
                         f"{_day_words(max(days))})")
            self._grain_fact(t, "as_of", stmt + ".", [(t.sheet, axis)] if axis else [], numbers)
            return
        if not after:
            return
        self.title_dates[t.tid] = end
        self._grain_fact(t, "title_period",
                         f"{t.sheet}'s title says \"{got['said']}\", but {after:,} of {len(days):,} rows are dated "
                         f"after {_day_words(end)} (the last on {_day_words(max(days))}).",
                         [(t.sheet, axis)], numbers)

    def _cadence_fact(self, t):
        """The cycle a table's dates follow: every k days on one weekday, or one day
        of each month, with the last regular date and the dates off that cycle."""
        aj = self._axis_j(t)
        if aj is None:
            return
        c = self.cols[t.tid][aj]
        per_day = Counter(v.toordinal() for v in t.column(aj) if hasattr(v, "year"))
        days = sorted(per_day)
        # read on the dates, else on the rows: a few rows on many off-cycle dates (manual checks between paydays)
        # never hide the cycle most rows keep to
        got = _cycle(days) or _cycle_by_rows(per_day)
        if not got:
            return
        on, off = got["on"], got["off"]
        if off:
            got["off_code"] = self._off_code(t, aj, set(off), set(on))
        self.cadence[(t.tid, c.header)] = dict(got, col=c.header)
        if got["step"] == "month":
            when = "on the last day of each month" if got["day"] == "last" else f"on day {got['day']} of each month"
        else:
            import datetime as dt
            when = f"every {got['step']} days on a {dt.date.fromordinal(on[0]).strftime('%A')}"
        stmt = f"Dates in {c.header} on {t.sheet} fall {when}: {len(on):,} dates, the last on {_day_words(on[-1])}"
        oc = got.get("off_code")
        if off and oc:
            # the rows off the cycle share one code: said by it ('the 38 M rows fall on Tuesdays in between')
            stmt += (f"; the {oc['rows']:,} {oc['value']} rows in {oc['col']} fall off that cycle, mostly on a "
                     f"{oc['weekday']}")
        elif off:
            import datetime as dt
            ex = dt.date.fromordinal(off[0])
            stmt += (f"; {len(off):,} date{'s are' if len(off) != 1 else ' is'} off that cycle (for example "
                     f"{ex.strftime('%a')} {_day_words(off[0])})")
        self._grain_fact(t, "cadence", stmt + ".", [(t.sheet, c.header)],
                         {"col": c.header, "step": got["step"], "day": got.get("day"),
                          "weekday": got.get("weekday"), "dates": len(on), "last": _iso_of(on[-1]),
                          "off": len(off), "off_dates": [_iso_of(d) for d in off[:20]], "off_code": oc,
                          "clause": stmt})

    def _period_end(self, t, out: list):
        """Dates on a cycle of a week or more whose last date falls short of the end of
        the period the title names: whether that period is complete, a question only
        the owner can answer (a payday can be late, or the export early)."""
        import datetime as dt
        tp = self.title_period.get(t.tid)
        cyc = next((c for (tid, _h), c in self.cadence.items() if tid == t.tid and c.get("step") != "month"
                    and int(c.get("step") or 0) >= 7), None)
        if not tp or not cyc or tp["kind"] == "as_of":
            return
        last = cyc["on"][-1]
        days = [r[self._axis_j(t)].toordinal() for r in t.rows if hasattr(r[self._axis_j(t)], "year")]
        if not days or max(days) >= tp["end"] or last >= tp["end"]:
            return
        nxt = last + int(cyc["step"])
        wd = dt.date.fromordinal(cyc["on"][0]).strftime("%A")
        g = next(x for x in self.grain_facts if x["recipe"] == f"grain:cadence:{t.tid}")
        out.append({"recipe": f"period:{t.tid}:{cyc['col']}", "kind": "gotcha", "oddity": True, "weight": 3,
                    "statement": f"{t.sheet}'s title says \"{tp['said']}\", to {_day_words(tp['end'])}, and the last "
                                 f"{cyc['col']} is {_day_words(last)}; {cyc['col']} falls every {cyc['step']} days on a "
                                 f"{wd}.",
                    "depends": [(t.sheet, cyc["col"])],
                    "numbers": {"table": t.tid, "col": cyc["col"], "said": tp["said"], "end": _iso_of(tp["end"]),
                                "last": _iso_of(last), "next": _iso_of(nxt), "step": cyc["step"], "weekday": wd,
                                "dates": len(cyc["on"]), "cycle": g["statement"], "complete": nxt > tp["end"],
                                "off_code": cyc.get("off_code")}})

    def _off_code(self, t, aj: int, off: set, on: set) -> dict | None:
        """The code (a column of 2 to 6 values) that the off-cycle rows share, 90% of
        them or more, and that is rare on the cycle's own rows: {col, value, weekday,
        rows}; None when no code marks them."""
        import datetime as dt
        rows = [r for r in t.rows if aj < len(r) and hasattr(r[aj], "year") and r[aj].toordinal() in off]
        if not rows:
            return None
        for c in self.cols[t.tid]:
            if c.j == aj or c.sensitive or c.distinct_capped or not (2 <= c.distinct <= 6):
                continue
            got = Counter(profile_mod.norm_key(r[c.j] if c.j < len(r) else None) for r in rows)
            v, k = got.most_common(1)[0]
            if v is None or k < 0.9 * len(rows):
                continue
            regular = sum(1 for r in t.rows if aj < len(r) and hasattr(r[aj], "year") and r[aj].toordinal() in on
                          and profile_mod.norm_key(r[c.j] if c.j < len(r) else None) == v)
            if regular > 0.1 * k:
                continue
            wd = Counter(dt.date.fromordinal(r[aj].toordinal()).strftime("%A") for r in rows).most_common(1)[0][0]
            return {"col": c.header, "value": _written_key(t, c, v), "weekday": wd, "rows": k}
        return None

    def _snapshot_fact(self, t):
        """A snapshot panel: a date column with few values where the date and one
        or two columns (a site, an item) name each row, 99% of rows or more, and
        the same set of those repeats from one date to the next. Its stock columns
        (a count on hand, a balance, or a money column that is one of them times a
        price) are read on the latest date, never summed across dates."""
        import itertools
        cols, n = self.cols[t.tid], t.n_rows
        if n < SNAP_ROWS:
            return
        # a column of names still says what a row is about; only its header is ever said
        keys = sorted([c for c in cols if c.type in ("text", "number") and not _metric(c) and not c.distinct_capped
                       and c.distinct >= 2 and c.semantic in ("identifier", "dimension")],
                      key=lambda c: -c.distinct)[:6]
        dates = sorted([c for c in cols if c.type == "date" and not profile_mod.is_audit(t, c)
                        and 3 <= c.distinct <= n / SNAP_PER_DATE], key=lambda c: c.distinct)
        for d in dates:
            by: dict = {}
            for i, r in enumerate(t.rows):
                v = r[d.j] if d.j < len(r) else None
                if hasattr(v, "year"):
                    by.setdefault(v.toordinal(), []).append(i)
            sizes = sorted(len(v) for v in by.values())
            if len(by) < 3 or sizes[len(sizes) // 2] < SNAP_PER_DATE:
                continue
            order = sorted(by)
            for size in (1, 2):
                best = None
                for combo in itertools.combinations(keys, size):
                    sets = {dk: [tuple(profile_mod.norm_key(t.rows[i][c.j] if c.j < len(t.rows[i]) else None)
                                       for c in combo) for i in rows] for dk, rows in by.items()}
                    dated = sum(len(v) for v in sets.values())
                    if sum(len(set(v)) for v in sets.values()) < SNAP_UNIQUE * dated:
                        continue
                    jac = [_jaccard(set(sets[a]), set(sets[b])) for a, b in zip(order, order[1:])]
                    rep = sum(jac) / len(jac)
                    if rep >= SNAP_REPEAT and (best is None or rep > best[0]):
                        best = (rep, combo)
                if best:
                    self._snapshot_found(t, d, best[1], by, sizes)
                    return

    def _snapshot_found(self, t, d, combo: tuple, by: dict, sizes: list):
        stock = self._stock_cols(t)
        latest = max(by)
        names = [c.header for c in combo]
        self.snapshots[t.tid] = {"col": d.header, "j": d.j, "latest": latest, "latest_iso": _iso_of(latest),
                                 "dates": len(by), "keys": names, "stock": [c.header for c in stock]}
        lo, hi = sizes[0], sizes[-1]
        stmt = (f"On {t.sheet}, each row is one {_join_words(names)} on one {d.header}: {len(by):,} dates from "
                f"{_day_words(min(by))} to {_day_words(latest)}, " + (f"{lo:,} rows each" if lo == hi
                                                                     else f"{lo:,} to {hi:,} rows each"))
        if stock:
            one = len(stock) == 1
            stmt += (f"; {_join_words([c.header for c in stock])} {'is a count' if one else 'are counts'} at each "
                     f"date, so {'it is' if one else 'they are'} read on the latest {d.header} ({_day_words(latest)}), "
                     "never summed across dates")
        self._grain_fact(t, "snapshot", stmt + ".", [(t.sheet, d.header)] + [(t.sheet, h) for h in names],
                         {"col": d.header, "keys": names, "dates": len(by), "latest": _iso_of(latest),
                          "stock": [c.header for c in stock]})

    def _stock_cols(self, t) -> list:
        """Number columns that hold a level at a date, not an amount that happened:
        a column bound to a role the playbook calls a level (a count on hand, a
        balance), a header that names a count on hand, a balance or a stock, never
        one that names a movement, or a money column that is one of them times a price."""
        nums = [c for c in self.cols[t.tid] if _metric(c) and not c.sensitive]
        pb_roles = (self.playbook or {}).get("roles") or {}
        level = {r["header"] for rid, r in (self.detection.get("roles") or {}).items()
                 if r.get("table") == t.tid and not r.get("row_label") and (pb_roles.get(rid) or {}).get("level")}
        stock = [c for c in nums if (c.header in level or _STOCK.search(profile_mod.split_camel(str(c.header))))
                 and not _FLOW.search(profile_mod.split_camel(str(c.header)))]
        rows = t.rows[:2000]
        for m in nums:
            if m in stock or _FLOW.search(profile_mod.split_camel(str(m.header))):
                continue
            for s in list(stock):
                for p in nums:
                    if p.j in (s.j, m.j):
                        continue
                    trio = [(r[s.j], r[p.j], r[m.j]) for r in rows if max(s.j, p.j, m.j) < len(r)
                            and _is_num(r[s.j]) and _is_num(r[p.j]) and _is_num(r[m.j])]
                    if len(trio) >= 5 and sum(1 for a, b, x in trio if abs(a * b - x) <= 0.0051) >= 0.9 * len(trio):
                        stock.append(m)
                        break
                if m in stock:
                    break
        return sorted(stock, key=lambda c: c.j)

    def _balanced_fact(self, t):
        """A repeated ID whose lines net to zero under one sign rule (a signed amount
        summed, or a debit minus the credit counted without its sign): each value is
        one entry of k to m lines, stated with how many of them net to zero."""
        from .findings import _NOTES
        rule = self._net_rule(t)
        if rule is None:
            return
        net, words, sides = rule
        best = None
        for c in self.cols[t.tid]:
            if c.type not in ("text", "number") or c.header in sides or c.sensitive or c.distinct_capped \
                    or c.distinct < 3 or c.distinct >= c.count or _NOTES.search(str(c.header)) \
                    or (_metric(c) and not profile_mod._numbers_as_ids(c)):
                continue
            sums: dict = {}
            sizes: Counter = Counter()
            for r in t.rows:
                k = profile_mod.norm_key(r[c.j] if c.j < len(r) else None)
                if k is not None:
                    sums[k] = sums.get(k, 0.0) + net(r)
                    sizes[k] += 1
            multi = [k for k, m in sizes.items() if m > 1]
            if len(multi) < 3:
                continue
            ok = sum(1 for k in multi if round(sums[k], 2) == 0)
            if ok < BALANCED * len(multi):
                continue
            score = (c.semantic == "identifier" or profile_mod.is_id_header(c.header), len(multi))
            if best is None or score > best[0]:
                best = (score, c, ok, multi, sizes)
        if best is None:
            return
        _s, c, ok, multi, sizes = best
        lo, hi = min(sizes[k] for k in multi), max(sizes[k] for k in multi)
        single = len(sizes) - len(multi)
        self.balanced[t.tid] = {"col": c.header, "rule": words, "entries": len(multi), "netted": ok}
        lines = f"{lo} line{'s' if lo != 1 else ''}" if lo == hi else f"{lo} to {hi} lines"
        stmt = (f"Each {c.header} on {t.sheet}{' that repeats' if single else ''} is one entry of {lines} that nets "
                f"to zero under {words}: {ok:,} of {len(multi):,}")
        if single:
            stmt += f"; {single:,} {c.header} value{'s have' if single != 1 else ' has'} one line"
        self._grain_fact(t, "balanced", stmt + ".", [(t.sheet, c.header)] + [(t.sheet, h) for h in sides],
                         {"col": c.header, "rule": words, "entries": len(multi), "netted": ok, "lo": lo, "hi": hi,
                          "single": single})

    def _net_rule(self, t):
        """(net of a row, the rule in words, the columns it reads): a debit minus the
        credit counted without its sign, when the headers name both sides (or the
        values show a pair whose headers say which is which); else the table's
        signed money column summed. None when neither exists."""
        from .findings import money_column
        lex = detect_mod._lexicons()
        nums = [c for c in self.cols[t.tid] if c.type == "number" and c.semantic == "metric" and not c.sensitive]

        def side(c, name):
            hn = detect_mod.norm_header(c.header)
            other = "credit" if name == "debit" else "debit"
            return detect_mod._lex_match(hn, lex[name]) > detect_mod._lex_match(hn, lex[other])
        dr = [c for c in nums if side(c, "debit")]
        cr = [c for c in nums if side(c, "credit")]
        if len(dr) == 1 and len(cr) == 1:
            d, k = dr[0], cr[0]
            jd, jc = d.j, k.j

            def net(r):
                a = r[jd] if jd < len(r) else None
                b = r[jc] if jc < len(r) else None
                return (a if _is_num(a) else 0.0) - (abs(b) if _is_num(b) else 0.0)
            how = f"{d.header} minus the absolute {k.header}" if k.negatives else f"{d.header} minus {k.header}"
            return net, how, [d.header, k.header]
        m = money_column(self, t)
        if m is None or not m.negatives:
            return None
        jm = m.j
        return (lambda r: r[jm] if jm < len(r) and _is_num(r[jm]) else 0.0), f"{m.header} summed", [m.header]

    # ------------------------------------------------------------------
    # reference and partner tables: dated windows, versions, list prices, onsets, terms
    # ------------------------------------------------------------------
    def _reference_links(self) -> list:
        """(transactions, reference, their key columns) for each auto join whose
        smaller side is a reference the larger side's dated lines look up: the
        larger table has a date column of its own. One per table pair and key."""
        out, seen = [], set()
        for j in self.joins:
            if j["band"] != "auto":
                continue
            a, b = self.table(j["from_table"]), self.table(j["to_table"])
            ca, cb = j["from_col"], j["to_col"]
            if a.n_rows < b.n_rows:
                a, b, ca, cb = b, a, cb, ca
            # joins come ID columns first, then the most rows matched: the first one per table pair is the key
            if a.wide or b.wide or self._axis_j(a) is None or a.n_rows < WINDOW_LINES or (a.tid, b.tid) in seen \
                    or a.tid in self.derived or b.tid in self.derived:
                continue
            seen.add((a.tid, b.tid))
            out.append({"t": a, "r": b, "tcol": self.col(a.tid, ca), "rcol": self.col(b.tid, cb)})
        return out

    def _window_cols(self, r):
        """(start, end) date columns of a reference table: dates on the same rows
        where the end is on or after the start on 95% of rows or more, named as a
        start and an end (or the table's only two dates). None when there are none."""
        dated = [c for c in self.cols[r.tid] if c.type == "date" and not profile_mod.is_audit(r, c)]
        best = None
        for s in dated:
            for e in dated:
                if s is e:
                    continue
                pairs = [(x[s.j], x[e.j]) for x in r.rows if max(s.j, e.j) < len(x) and hasattr(x[s.j], "year")
                         and hasattr(x[e.j], "year")]
                if len(pairs) < 2 or sum(1 for a, b in pairs if a <= b) < 0.95 * len(pairs) \
                        or sum(1 for a, b in pairs if a < b) < 0.5 * len(pairs):
                    continue
                named = bool(_START_HEAD.search(str(s.header))) + bool(_END_HEAD.search(str(e.header)))
                if named or len(dated) == 2:
                    score = (named, -abs(s.j - e.j))
                    if best is None or score > best[0]:
                        best = (score, s, e)
        return (best[1], best[2]) if best else None

    def _ranges(self, r, rcol, win) -> dict:
        """{key: [(start ordinal, end ordinal, row position)]} of a reference with
        dates; a blank start or end is open on that side."""
        s, e = win
        out: dict = {}
        for i, x in enumerate(r.rows):
            k = profile_mod.norm_key(x[rcol.j] if rcol.j < len(x) else None)
            if k is None:
                continue
            a = x[s.j] if s.j < len(x) else None
            b = x[e.j] if e.j < len(x) else None
            out.setdefault(k, []).append((a.toordinal() if hasattr(a, "year") else -10 ** 9,
                                          b.toordinal() if hasattr(b, "year") else 10 ** 9, i))
        return {k: sorted(v) for k, v in out.items()}

    def _reference_insights(self) -> list:
        out = []
        for link in self._reference_links():
            t, r = link["t"], link["r"]
            if link["tcol"] is None or link["rcol"] is None:
                continue
            win = self._window_cols(r)
            if win:
                ranges = self._ranges(r, link["rcol"], win)
                self._windows(link, win, ranges, out)
                self._versions(link, win, ranges, out)
                self._terms_facts(link, win)
            self._conformity(link, win, out)
        for t in self.tables:
            if not t.wide and t.tid not in self.derived and t.n_rows >= ONSET_LINES:
                self._onsets(t, out)
        for ins in out:
            ins.setdefault("files", [self.file_of[ins["numbers"]["table"]]])
        return out

    def _windows(self, link: dict, win: tuple, ranges: dict, out: list):
        """Lines dated before a key's first start, after its last end or between
        its dated rows, counted per key. Stated for keys with 5 lines or more
        outside (and 5% of their lines): what applies to them only the owner knows."""
        t, r, tc = link["t"], link["r"], link["tcol"]
        aj = self._axis_j(t)
        rows_of: dict = {}
        for i, x in enumerate(t.rows):
            k = profile_mod.norm_key(x[tc.j] if tc.j < len(x) else None)
            d = x[aj] if aj < len(x) else None
            if k in ranges and hasattr(d, "year"):
                rows_of.setdefault(k, []).append((i, d.toordinal()))
        per, hit = [], []
        for k, lines in rows_of.items():
            rs = ranges[k]
            first, last = min(a for a, _b, _i in rs), max(b for _a, b, _i in rs)
            got = {"before": [], "after": [], "gap": []}
            for i, d in lines:
                if any(a <= d <= b for a, b, _i in rs):
                    continue
                got["before" if d < first else "after" if d > last else "gap"].append(i)
            n = sum(len(v) for v in got.values())
            if n < WINDOW_LINES or n < WINDOW_SHARE * len(lines):
                continue
            per.append({"key": _written_key(t, tc, k), "lines": len(lines), "before": len(got["before"]),
                        "after": len(got["after"]), "gap": len(got["gap"]),
                        "start": _iso_of(first) if first > -10 ** 9 else "", "end": _iso_of(last) if last < 10 ** 9 else "",
                        "row_ids": sorted(i for v in got.values() for i in v)[:2000]})
            hit += [i for v in got.values() for i in v]
        if not per:
            return
        per.sort(key=lambda p: -(p["before"] + p["after"] + p["gap"]))
        n = len(hit)
        s, e = win
        out.append({"recipe": f"window:{t.tid}:{r.tid}:{tc.header}", "kind": "gotcha", "oddity": True, "weight": 3,
                    "statement": f"{n:,} lines on {t.sheet} are dated outside the {s.header} and {e.header} dates "
                                 f"{r.sheet} gives their {tc.header}: " + "; ".join(_window_words(p) for p in per[:3])
                                 + (f"; and {len(per) - 3:,} more" if len(per) > 3 else "") + ".",
                    "depends": [(t.sheet, tc.header), (r.sheet, s.header), (r.sheet, e.header)],
                    "numbers": {"table": t.tid, "ref": r.tid, "col": tc.header, "ref_col": link["rcol"].header,
                                "start": s.header, "end": e.header, "rows": n, "per": per[:20],
                                "row_ids": sorted(hit)[:5000]}})

    def _versions(self, link: dict, win: tuple, ranges: dict, out: list):
        """A reference key on more than one dated row whose dates never overlap: a
        line takes the row whose dates cover its date. A counted fact."""
        t, r, rc = link["t"], link["r"], link["rcol"]
        s, e = win
        keys = []
        for k, rs in ranges.items():
            if len(rs) >= 2 and all(b1 < a2 for (_a1, b1, _i), (a2, _b2, _j) in zip(rs, rs[1:])):
                keys.append(k)
        if not keys or any(f"structure:versions:{r.tid}:" in i["recipe"] for i in out):
            return
        # the lines each dated row covers, counted, so the owner can say which row applies to which lines
        tc, aj = link["tcol"], self._axis_j(t)
        per, row_ids = [], []
        want = set(keys)
        lines: dict = {}
        for i, x in enumerate(t.rows):
            k = profile_mod.norm_key(x[tc.j] if tc.j < len(x) else None)
            d = x[aj] if aj is not None and aj < len(x) else None
            if k in want and hasattr(d, "year"):
                lines.setdefault(k, []).append((i, d.toordinal()))
        for k in keys:
            got = [{"start": _iso_of(a) if a > -10 ** 9 else "", "end": _iso_of(b) if b < 10 ** 9 else "",
                    "range": _range_words(a, b), "lines": sum(1 for _i, d in lines.get(k, []) if a <= d <= b)}
                   for a, b, _j in ranges[k]]
            covered = sum(g["lines"] for g in got)
            per.append({"key": _written_key(r, rc, k), "rows": got, "outside": len(lines.get(k, [])) - covered})
            row_ids += [i for i, _d in lines.get(k, [])]
        k0 = keys[0]
        shown = " and ".join(f"{_range_words(a, b)}" for a, b, _i in ranges[k0][:3])
        n = len(keys)
        out.append({"recipe": f"structure:versions:{r.tid}:{rc.header}", "kind": "structure", "weight": 0,
                    "statement": f"On {r.sheet}, {n:,} {rc.header} value{'s have' if n != 1 else ' has'} more than one "
                                 f"dated row, and the dates never overlap (for example {_written_key(r, rc, k0)}: "
                                 f"{shown}); a line on {t.sheet} takes the row whose {s.header} and {e.header} cover "
                                 f"its date.",
                    "depends": [(r.sheet, rc.header), (r.sheet, s.header), (r.sheet, e.header)],
                    "numbers": {"table": r.tid, "col": rc.header, "keys": [_written_key(r, rc, k) for k in keys[:20]],
                                "values": n, "lines_table": t.tid, "lines_col": tc.header, "start": s.header,
                                "end": e.header, "per": per[:20], "row_ids": sorted(row_ids)[:5000]}})

    def _terms_facts(self, link: dict, win: tuple):
        """Each key of a table with dated terms, as a counted fact for every brain in
        the session: its dates, each rate with its unit and the terms in words, and
        when its end falls within 90 days of the last line. Only a table keyed by a
        partner holds terms: it carries a rate or a terms text, or its key is a
        column that splits the lines into a few groups and it carries no price (a
        dated price list keyed by item is versions, never terms). With more keys
        than can be stated one by one, one counted fact stands for them all."""
        t, r, tc, rc = link["t"], link["r"], link["tcol"], link["rcol"]
        if any(x["table"] == r.tid for x in self.terms):
            return
        s, e = win
        aj = self._axis_j(t)
        last = max((x[aj].toordinal() for x in t.rows if aj < len(x) and hasattr(x[aj], "year")), default=None)
        rates = [c for c in self.cols[r.tid] if c.type == "number" and c.j not in (rc.j, s.j, e.j) and not c.codes
                 and c.semantic != "identifier"]
        words = [c for c in self.cols[r.tid] if c.type == "text" and c.j != rc.j and not c.sensitive and c.avg_len >= 8]
        said = [c for c in words if _TERMS_HEAD.search(profile_mod.split_camel(str(c.header)))]
        pct = [c for c in rates if _PERCENT_HEAD.search(profile_mod.split_camel(str(c.header)))]
        keyed = [x for x in r.rows if profile_mod.norm_key(x[rc.j] if rc.j < len(x) else None) is not None]
        n = len({profile_mod.norm_key(x[rc.j]) for x in keyed})
        if not keyed or not (said or pct or (_grouping(tc) and self._ref_price(r, rc) is None)):
            return
        role = self._entity_role(t, tc) or self._entity_role(r, rc)
        deps = [(r.sheet, rc.header), (r.sheet, s.header), (r.sheet, e.header)]
        if n > TERMS_SHOWN or len(keyed) > 3 * TERMS_SHOWN:
            if said or pct:
                self._terms_summary(t, r, rc, win, keyed, n, last, role, deps)
            return
        pb_roles = (self.playbook or {}).get("roles") or {}
        money = {x["header"] for rid, x in (self.detection.get("roles") or {}).items()
                 if x.get("table") == r.tid and (pb_roles.get(rid) or {}).get("unit") == "currency"}
        for x in keyed:
            key = x[rc.j]
            a = x[s.j] if s.j < len(x) else None
            b = x[e.j] if e.j < len(x) else None
            span = f"{_day_words(a.toordinal()) if hasattr(a, 'year') else 'no start'} to " \
                   f"{_day_words(b.toordinal()) if hasattr(b, 'year') else 'no end'}"
            bits = [f": {span}"]
            texts = {}
            for c in rates:
                v = x[c.j] if c.j < len(x) else None
                if _is_num(v):
                    bits.append(f"{c.header} {_rate_words(c, v, c.header in money)}")
            for c in words:
                v = x[c.j] if c.j < len(x) else None
                if isinstance(v, str) and v.strip():
                    from .findings import _short
                    said = _short(v, 1000)
                    said = said if len(said) <= TERMS_TEXT else said[:TERMS_TEXT].rsplit(" ", 1)[0] + "..."
                    bits.append(f"{c.header} \"{said}\"")
                    texts[c.header] = said
            body = bits[0] + "".join(f"; {x}" for x in bits[1:])
            soon = last is not None and hasattr(b, "year") and abs(b.toordinal() - last) <= TERMS_NEAR_END
            if soon:
                gap = b.toordinal() - last
                body += (f"; it ends {abs(gap):,} day{'s' if abs(gap) != 1 else ''} "
                         f"{'after' if gap >= 0 else 'before'} the last line on {t.sheet} ({_day_words(last)})"
                         if gap else f"; it ends on the day of the last line on {t.sheet}")
            # '<key> on <sheet>' + body; another file's brain names the file after the sheet
            self.terms.append({"table": r.tid, "key": _written(key), "sheet": r.sheet, "body": body + ".",
                               "file": self.file_of[r.tid], "statement": f"{_written(key)} on {r.sheet}{body}.",
                               "depends": deps, "ends_soon": soon, "role": role, "texts": texts,
                               "terms_col": next((h for h in texts if _TERMS_HEAD.search(profile_mod.split_camel(h))),
                                                 next(iter(texts), "")),
                               "start": a.isoformat()[:10] if hasattr(a, "year") else "",
                               "end": b.isoformat()[:10] if hasattr(b, "year") else ""})

    def _terms_summary(self, t, r, rc, win, keyed: list, n: int, last, role: str, deps: list):
        """One counted fact for a terms table with too many keys to state one by
        one: how many, the span of their dates and how many end near the last line."""
        s, e = win
        starts = [x[s.j].toordinal() for x in keyed if s.j < len(x) and hasattr(x[s.j], "year")]
        ends = [x[e.j].toordinal() for x in keyed if e.j < len(x) and hasattr(x[e.j], "year")]
        body = f": {len(keyed):,} dated rows by {s.header} and {e.header}"
        if starts and ends:
            body += f", from {_day_words(min(starts))} to {_day_words(max(ends))}"
        soon = sum(1 for b in ends if last is not None and abs(b - last) <= TERMS_NEAR_END)
        if soon:
            body += (f"; {soon:,} of them end within {TERMS_NEAR_END} days of the last line on {t.sheet} "
                     f"({_day_words(last)})")
        key = f"{n:,} {_plural(rc.header, n)}" if n != 1 else f"1 {rc.header}"
        self.terms.append({"table": r.tid, "key": key, "sheet": r.sheet, "body": body + ".",
                           "file": self.file_of[r.tid], "statement": f"{key} on {r.sheet}{body}.", "depends": deps,
                           "ends_soon": bool(soon), "role": role, "summary": True})

    def _entity_role(self, t, c) -> str:
        """The playbook role of kind entity (a vendor, a party, a location) column c is bound to, or ''."""
        pb_roles = (self.playbook or {}).get("roles") or {}
        for rid, x in (self.detection.get("roles") or {}).items():
            if x.get("table") == t.tid and x.get("header") == c.header and not x.get("row_label") \
                    and (pb_roles.get(rid) or {}).get("kind") == "entity":
                return rid
        return ""

    def _ref_price(self, r, rcol):
        """A reference table's price: a number column with cents (or headed price,
        cost, rate or each) that is not a rate or a share; the one headed as a price first."""
        from .rules import _RATE
        nums = [c for c in self.cols[r.tid] if c.type == "number" and c.j != rcol.j and not c.codes
                and c.semantic == "metric" and not _RATE.search(str(c.header)) and not _AMOUNT_NOT.search(str(c.header))
                and (not c.integers or _PRICE_HEADER.search(str(c.header)))]
        named = [c for c in nums if _PRICE_HEADER.search(str(c.header)) or re.search(r"\blist\b", str(c.header), re.I)]
        return (named or nums or [None])[0] if len(named) == 1 or (not named and len(nums) == 1) else None

    def _conformity(self, link: dict, win, out: list):
        """How closely each group's lines (a vendor's) keep to the reference price,
        month by month: a group that charges the reference price exactly from one
        month on (that month at it on half its lines or more, and 90% of the lines
        since) follows the list from that month, a counted fact. Lines off the list inside
        that run are a finding when they are few (10% of the run at most). Prices
        that float around the list say nothing."""
        t, r, tc, rc = link["t"], link["r"], link["tcol"], link["rcol"]
        tp, rp = self._price_col(t), self._ref_price(r, rc)
        aj = self._axis_j(t)
        if tp is None or rp is None or aj is None or tp.j == tc.j:
            return
        ranges = self._ranges(r, rc, win) if win else {}
        price_of: dict = {}
        for i, x in enumerate(r.rows):
            k = profile_mod.norm_key(x[rc.j] if rc.j < len(x) else None)
            v = x[rp.j] if rp.j < len(x) else None
            if k is not None and _is_num(v):
                price_of.setdefault(k, {})[i] = v
        lines = []          # (row position, key, month, day, at the list, above it)
        for i, x in enumerate(t.rows):
            k = profile_mod.norm_key(x[tc.j] if tc.j < len(x) else None)
            p, d = (x[tp.j] if tp.j < len(x) else None), (x[aj] if aj < len(x) else None)
            if k not in price_of or not _is_num(p) or p <= 0 or not hasattr(d, "year"):
                continue
            ref = self._ref_for(k, d.toordinal(), price_of[k], ranges.get(k))
            if ref is None:
                continue
            lines.append((i, (d.year, d.month), abs(p - ref) <= 0.005, p > ref))
        if len(lines) < CONFORM_LINES:
            return
        best = None
        for g in [None] + [c for c in self.cols[t.tid] if _grouping(c) and c.j != tc.j and c.type == "text"]:
            got = self._runs(t, g, lines)
            if got and (best is None or got[0] > best[0]):
                best = got
        if not best:
            return
        _p, g, runs = best
        rows_word = lambda n: f"{n:,} line{'s' if n != 1 else ''}"  # noqa: E731
        parts = [f"{x['value'] or 'every line'} " + (f"from {_mon_words(x['from'])}" if x["before"] else
                                                      f"in every month from {_mon_words(x['from'])}")
                 + f" ({x['at']:,} of {rows_word(x['lines'])} at the list)" for x in runs]
        who = f"by {g.header}, " if g is not None else ""
        # runs that hold most lines with a reference price say the list is what is charged (listprice_all); a few
        # groups at the list while the rest float say only that those groups follow it (listprice)
        held = sum(x["lines"] for x in runs)
        name = "listprice_all" if held >= CONFORM_ALL * len(lines) else "listprice"
        out.append({"recipe": f"{name}:{t.tid}:{r.tid}", "kind": "structure", "weight": 0,
                    "statement": f"{tp.header} on {t.sheet} matches {rp.header} on {r.sheet} exactly, {who}"
                                 + "; ".join(parts[:4]) + (f"; and {len(parts) - 4:,} more" if len(parts) > 4 else "")
                                 + ".",
                    "depends": [(t.sheet, tp.header), (r.sheet, rp.header)],
                    "numbers": {"table": t.tid, "ref": r.tid, "col": tp.header, "ref_col": rp.header,
                                "group": g.header if g is not None else "", "runs": runs,
                                "held": held, "priced": len(lines), "ref_file": self.file_of[r.tid]}})
        for x in runs:
            if x["off"] and x["off"] <= CONFORM_OFF * x["lines"] and x["clean_months"] >= 2:
                self._offlist_fact(t, r, tp, rp, g, x, out)

    def _ref_for(self, k, day: int, prices: dict, ranges):
        """The reference price for a key on a day: its only row, or the row whose
        dates cover the day. None when the key has several rows and none covers it."""
        if len(prices) == 1:
            return next(iter(prices.values()))
        for a, b, i in ranges or []:
            if a <= day <= b and i in prices:
                return prices[i]
        return None

    def _runs(self, t, g, lines: list):
        """(how cleanly the groups split, the group column, [runs]) for one way of
        grouping the lines; None when no group follows the list."""
        by: dict = {}
        for i, month, at, above in lines:
            k = profile_mod.norm_key(t.rows[i][g.j] if g is not None and g.j < len(t.rows[i]) else None) \
                if g is not None else ""
            if k is None:
                continue
            by.setdefault(k, {}).setdefault(month, []).append((i, at, above))
        runs, pure, cells = [], 0, 0
        for k, months in by.items():
            order = sorted(months)
            for m in order:
                share = sum(1 for _i, at, _a in months[m] if at) / len(months[m])
                cells += 1
                pure += share <= 0.05 or share >= 0.95
            # the run starts at the earliest month at the list on half its lines or more from which 90% of the
            # lines to the end are at it: one month of exceptions inside the run never cuts it short
            at_of = [sum(1 for _i, a, _b in months[m] if a) for m in order]
            n_of = [len(months[m]) for m in order]
            s = next((s for s in range(len(order)) if at_of[s] >= 0.5 * n_of[s]
                      and sum(at_of[s:]) >= CONFORM_AT * sum(n_of[s:])), len(order))
            run = order[s:]
            n = sum(len(months[m]) for m in run)
            at = sum(1 for m in run for _i, a, _b in months[m] if a)
            if len(run) < CONFORM_MONTHS or n < CONFORM_LINES or at < CONFORM_AT * n:
                continue
            off = [(i, above, m) for m in run for i, a, above in months[m] if not a]
            before = sum(len(months[m]) for m in order[:s])
            runs.append({"value": _written_key(t, g, k) if g is not None else "", "from": f"{run[0][0]}-{run[0][1]:02d}",
                         "months": len(run), "lines": n, "at": at, "off": len(off), "before": before,
                         "before_at": sum(1 for m in order[:s] for _i, a, _b in months[m] if a),
                         "off_rows": [i for i, _a, _m in off][:200], "above": sum(1 for _i, a, _m in off if a),
                         "off_months": sorted({f"{m[0]}-{m[1]:02d}" for _i, _a, m in off}),
                         "clean_months": sum(1 for m in run if all(a for _i, a, _b in months[m]))})
        if not runs:
            return None
        # the grouping whose runs hold the most lines, then the one whose months split cleanest
        return (sum(x["lines"] for x in runs), pure / cells if cells else 0.0), g, runs

    def _offlist_fact(self, t, r, tp, rp, g, x: dict, out: list):
        n, up = x["off"], x["above"]
        side = "above" if up == n else "below" if not up else "off"
        when = _join_words([_mon_words(m) for m in x["off_months"]])
        who = f"{x['value']} lines" if x["value"] else "lines"
        item = self._dominant_item(t, x["off_rows"], tp)
        what = (f" ({item['item']}: charged {item['charged']} where the list says {item['list']})"
                if item and item.get("list") else f" ({item['item']})" if item else "")
        out.append({"recipe": f"offlist:{t.tid}:{g.header if g is not None else ''}:{x['value']}", "kind": "gotcha",
                    "oddity": True, "weight": 3,
                    "statement": f"{n:,} {who} on {t.sheet} in {when} are {side} the {rp.header} on {r.sheet}{what}, "
                                 f"inside a run from {_mon_words(x['from'])} where the other {x['at']:,} lines are at it.",
                    "depends": [(t.sheet, tp.header), (r.sheet, rp.header)],
                    "numbers": {"table": t.tid, "ref": r.tid, "col": tp.header, "ref_col": rp.header,
                                "group": g.header if g is not None else "", "value": x["value"], "rows": n,
                                "side": side, "months": x["off_months"], "from": x["from"], "at": x["at"],
                                "row_ids": x["off_rows"], "item": item}})

    def _dominant_item(self, t, rows: list, price=None, ref=None, rcol=None) -> dict | None:
        """{item, charged, list} for the lines named: the item (a description, else
        an item code) on most of them, the price charged there (its median), and the
        reference price when a key joins it; None when no item column names half
        of them."""
        from .recipes import fmt_num
        if not rows:
            return None
        cols = [c for c in self.cols[t.tid] if c.type == "text" and not c.sensitive and c.distinct >= 5]
        cols.sort(key=lambda c: (not re.search(r"desc|name|product|item", str(c.header), re.I), -c.avg_len))
        for c in cols[:3]:
            got = Counter(profile_mod.norm_key(t.rows[i][c.j] if c.j < len(t.rows[i]) else None) for i in rows)
            k, n = got.most_common(1)[0]
            if k is None or n < 0.5 * len(rows):
                continue
            mine = [i for i in rows if profile_mod.norm_key(t.rows[i][c.j] if c.j < len(t.rows[i]) else None) == k]
            charged = [t.rows[i][price.j] for i in mine if price is not None and price.j < len(t.rows[i])
                       and _is_num(t.rows[i][price.j])]
            out = {"item": _written(t.rows[mine[0]][c.j]), "col": c.header, "rows": n,
                   "charged": fmt_num(round(_median(sorted(charged)), 2)) if charged else ""}
            out["list"] = self._list_price(t, mine, price) if price is not None else ""
            return out
        return None

    def _list_price(self, t, rows: list, price) -> str:
        """The reference price of these lines' item, when a reference table joined by
        an item key holds a price column with the same header or a price role; ''
        otherwise."""
        from .recipes import fmt_num
        for j in self.joins:
            if j["from_table"] != t.tid or j.get("band") != "auto":
                continue
            ref = self.table(j["to_table"])
            if j["from_col"] not in t.headers or j["to_col"] not in ref.headers:
                continue
            kc, rk = t.headers.index(j["from_col"]), ref.headers.index(j["to_col"])
            rp = next((c for c in self.cols[ref.tid] if c.type == "number" and re.search(
                r"price|cost|rate", str(c.header), re.I)), None)
            if rp is None:
                continue
            keys = {profile_mod.norm_key(t.rows[i][kc] if kc < len(t.rows[i]) else None) for i in rows} - {None}
            got = [r[rp.j] for r in ref.rows if rk < len(r) and profile_mod.norm_key(r[rk]) in keys and rp.j < len(r)
                   and _is_num(r[rp.j])]
            if got:
                return fmt_num(round(_median(sorted(got)), 2))
        return ""

    def _onsets(self, t, out: list):
        """A code that first shows up for one group (a charge for one vendor) after
        that group had been active 3 months or more without it, and then carries
        on in 80% of its months or more. A code that starts for most groups at
        once is a change across the file, left to the date where the file changes."""
        aj = self._axis_j(t)
        if aj is None:
            return
        cols = self.cols[t.tid]
        # a column whose values hand over from old to new at one date is a change across the file, asked there
        cols = [c for c in cols if (t.tid, c.header) not in self.handoffs]
        groups = [c for c in cols if _grouping(c) and c.type == "text"]
        # the code is a column that names kinds of lines (a type, a charge, a status), never who entered them
        codes = [c for c in cols if _grouping(c) and (c.codes or _CODE_WORDS.search(str(c.header)))]
        found = []
        for g in groups:
            for c in codes:
                if c is g:
                    continue
                found += self._onsets_of(t, g, c, aj)
        if not found:
            return
        # one start read both ways (a fee starting for a vendor is the vendor starting in the fee's rows): the
        # code is the column that names kinds of lines, else the one with fewer values
        width = {c.header: c.distinct for c in cols}
        kept: dict = {}
        for f in found:
            k = frozenset(f["row_ids"])
            rank = (not _CODE_WORDS.search(str(f["col"])), width[f["col"]] - width[f["group_col"]])
            if k not in kept or rank < kept[k][0]:
                kept[k] = (rank, f)
        found = sorted((f for _r, f in kept.values()), key=lambda f: (-f["rows"], f["group"]))
        best = found[0]
        items = [f for f in found if (f["group_col"], f["col"]) == (best["group_col"], best["col"])]
        rows = sorted({i for f in items for i in f["row_ids"]})
        g, c = best["group_col"], best["col"]
        # what the new rows are: the item on most of them and what each one costs
        from .findings import money_column
        m = money_column(self, t)
        item = self._dominant_item(t, best["row_ids"], m)
        if item and m is not None:
            item["money_col"] = m.header
        out.append({"recipe": f"onset:{t.tid}:{c}", "kind": "history", "oddity": True, "weight": 3,
                    "statement": f"On {t.sheet}, " + "; ".join(_onset_words(f) for f in items[:3])
                                 + (f"; and {len(items) - 3:,} more" if len(items) > 3 else "") + ".",
                    "depends": [(t.sheet, c), (t.sheet, g)],
                    "numbers": {"table": t.tid, "col": c, "group_col": g, "items": [
                        {k: v for k, v in f.items() if k != "row_ids"} for f in items[:20]], "row_ids": rows[:5000],
                        "item": item}})

    def _onsets_of(self, t, g, c, aj) -> list:
        months_of: dict = {}          # group -> its months
        per_month: Counter = Counter()          # (group, month) -> rows
        code_months: dict = {}        # (group, code) -> [(month, row)]
        for i, x in enumerate(t.rows):
            d = x[aj] if aj < len(x) else None
            gk = profile_mod.norm_key(x[g.j] if g.j < len(x) else None)
            ck = profile_mod.norm_key(x[c.j] if c.j < len(x) else None)
            if not hasattr(d, "year") or gk is None or ck is None:
                continue
            m = d.year * 12 + d.month - 1
            months_of.setdefault(gk, set()).add(m)
            per_month[(gk, m)] += 1
            code_months.setdefault((gk, ck), []).append((m, i))
        if not months_of:
            return []
        first = min(min(v) for v in months_of.values())
        debut: dict = {}          # code -> its first month anywhere in the table
        held: dict = {}           # code -> the groups that have it
        for (gk, ck), got in code_months.items():
            debut[ck] = min(debut.get(ck, 10 ** 9), min(m for m, _i in got))
            held.setdefault(ck, set()).add(gk)
        out = []
        for (gk, ck), got in code_months.items():
            mine = sorted(months_of[gk])
            onset = min(m for m, _i in got)
            before = [m for m in mine if m < onset]
            after = [m for m in mine if m >= onset]
            have = {m for m, _i in got}
            if len(before) < ONSET_MONTHS or len(after) < ONSET_MONTHS or len(got) < ONSET_ROWS \
                    or sum(1 for m in after if m in have) < ONSET_STEADY * len(after):
                continue
            # at one steady share of the group's rows over all its months, the rows before would have shown the
            # code but for a 1-in-100 chance, shared out over every group and code read: a code missing early by
            # chance is no start (the share since the start alone is picked after the fact and runs high)
            rows_before = sum(per_month[(gk, m)] for m in before)
            share = len(got) / max(1, rows_before + sum(per_month[(gk, m)] for m in after))
            if (1 - min(share, 1.0)) ** rows_before > ONSET_CHANCE / len(code_months):
                continue
            if debut[ck] - first >= ONSET_MONTHS and len(held[ck]) >= 2:
                continue          # a code new to the whole table that several groups take up: a change across the file
            rows = [i for _m, i in got]
            out.append({"group_col": g.header, "col": c.header, "group": _written_key(t, g, gk),
                        "code": _written_key(t, c, ck), "month": f"{onset // 12}-{onset % 12 + 1:02d}",
                        "before": len(before), "rows": len(rows), "row_ids": rows})
        return out

    def _join_variants(self) -> list:
        """A join that matches only once values are read without capitals, extra
        spaces or leading zeros: a counted fact naming the pairs written two ways.
        The rows counted are on the side with more rows (the lines that look the
        other side up), so a rule that leaves lines out says it came before."""
        out = []
        for j in self.joins:
            if j["band"] != "auto":
                continue
            ft, tt = self.table(j["from_table"]), self.table(j["to_table"])
            fc, tc = self.col(ft.tid, j["from_col"]), self.col(tt.tid, j["to_col"])
            ff, tf = j["from_file"], j["to_file"]
            if ft.n_rows < tt.n_rows:
                ft, tt, fc, tc, ff, tf = tt, ft, tc, fc, tf, ff
            if fc is None or tc is None or fc.sensitive or tc.sensitive:
                continue
            forms_f, forms_t = _forms(ft, fc), _forms(tt, tc)
            pairs, rows = [], 0
            for k, fw in forms_f.items():
                tw = forms_t.get(k)
                if tw is None:
                    continue
                odd = {w: n for w, n in fw.items() if w not in tw}
                if odd:
                    pairs.append((max(odd, key=odd.get), max(tw, key=tw.get)))
                    rows += sum(odd.values())
            if not pairs:
                continue
            n = len(pairs)
            ex = "; ".join(f"'{a}' and '{b}'" for a, b in pairs[:3])
            out.append({"recipe": f"structure:join_variants:{ft.tid}:{fc.header}:{tt.tid}", "kind": "structure",
                        "weight": 0,
                        "statement": f"{fc.header} on {ft.sheet} matches {tc.header} on {tt.sheet} for {n:,} "
                                     f"value{'s' if n != 1 else ''} ({rows:,} row{'s' if rows != 1 else ''}) only once "
                                     f"capitals, extra spaces, a closing period or leading zeros are set aside (for "
                                     f"example {ex}).",
                        "depends": [(ft.sheet, fc.header), (tt.sheet, tc.header)],
                        "numbers": {"table": ft.tid, "col": fc.header, "to_table": tt.tid, "to_col": tc.header,
                                    "values": n, "rows": rows, "pairs": [list(p) for p in pairs[:20]],
                                    "cross_file": ff != tf},
                        "files": [ff]})
        return out

    def _derived_labels(self) -> list:
        """A calculated tab whose row or column labels are the values of a column on
        the tab it is built from, missing some of them: those values' rows can
        never reach its totals. A counted fact naming them."""
        from .findings import money_column, money_fmt
        out = []
        for path, fa in self.formulas.items():
            for d in (fa or {}).get("derived", []):
                dts = [t for t in self.tables if self.file_of[t.tid] == path and t.sheet == d["sheet"]]
                srcs = [t for t in self.tables if self.file_of[t.tid] == path and t.sheet == d["from"]
                        and not t.wide and t.tid not in self.derived]
                for dt_ in dts:
                    lab = dt_.row_label_col if dt_.row_label_col >= 0 else 0
                    axes = [("row", {profile_mod.norm_key(x[lab]) for x in dt_.rows if lab < len(x)} - {None}),
                            ("column", {profile_mod.norm_key(h) for h in dt_.headers} - {None})]
                    for s in srcs:
                        m = money_column(self, s)
                        for c in self.cols[s.tid]:
                            if c.type != "text" or c.sensitive or c.distinct_capped or not (2 <= c.distinct <= 200):
                                continue
                            vals = set(c.counter)
                            for axis, labels in axes:
                                shared = vals & labels
                                missing = sorted(vals - labels, key=lambda k: -c.counter[k])
                                if len(shared) < 3 or len(shared) < 0.5 * len(labels) or not missing \
                                        or len(missing) > len(shared):
                                    continue
                                never = self._by_label_only(path, dt_.sheet, s.sheet)
                                self._labels_fact(dt_, s, c, m, axis, shared, missing, out,
                                                  money_fmt(self, s, m), never)
        return out

    def _by_label_only(self, path: str, sheet: str, source: str) -> bool:
        """Every formula on the tab that reads the source tab looks it up by a label
        on the tab (SUMIF, SUMIFS, COUNTIF(S) or AVERAGEIF(S) with a cell of the
        tab as its criteria), and none reads the source whole (a grand total's
        SUM of its column). Only then can a value with no label never reach a total."""
        b = next((b for b in self.books if b.path == path), None)
        sh = next((x for x in (b.sheets if b else []) if x.name == sheet), None)
        if sh is None or not sh.formulas:
            return False
        src = re.compile(r"(?:'" + re.escape(source) + r"'|\b" + re.escape(source) + r")!", re.I)
        reads = [str(f) for f in sh.formulas.values() if src.search(str(f))]
        if not reads:
            return False
        for f in reads:
            if not _BY_LABEL.search(f):
                return False
            # the criteria is a cell on this tab: a reference with no sheet in front of it
            own = re.sub(r"(?:'[^']+'|[A-Za-z_][\w.]*)!\$?[A-Z]{1,3}\$?\d*(?::\$?[A-Z]{1,3}\$?\d*)?", "", f)
            if not re.search(r"(?<![\w!])\$?[A-Z]{1,3}\$?\d+\b", own.split("(", 1)[-1]):
                return False
            # anything outside the lookups that still reads the source (SUM(Data!C:C)+SUMIFS(...)) reads it whole
            if src.search(_BY_LABEL_CALL.sub("", f)):
                return False
        return True

    def _labels_fact(self, d, s, c, m, axis: str, shared: set, missing: list, out: list, fmt_money,
                     never: bool = False):
        miss = set(missing)
        rows = [x for x in s.rows if profile_mod.norm_key(x[c.j] if c.j < len(x) else None) in miss]
        money = sum(abs(x[m.j]) for x in rows if m is not None and m.j < len(x) and _is_num(x[m.j]))
        names = [_written_key(s, c, k) for k in missing]
        k = len(names)
        out.append({"recipe": f"structure:labels:{d.tid}:{s.tid}:{c.header}", "kind": "structure", "weight": 0,
                    "statement": f"{d.sheet} has a {axis} for {len(shared):,} of the {c.distinct:,} {c.header} values on "
                                 f"{s.sheet}, but none for {_listed(names)} ({len(rows):,} rows"
                                 + (f", {fmt_money(money)} of {m.header}" if m is not None else "")
                                 + (f"), so its totals never pick {'it' if k == 1 else 'them'} up." if never else ")."),
                    "depends": [(s.sheet, c.header)],
                    "numbers": {"table": d.tid, "source": s.tid, "col": c.header, "axis": axis, "missing": names[:20],
                                "rows": len(rows), "never": never},
                    "files": [self.file_of[d.tid]]})

    def _named(self, x: dict) -> str:
        label = self.cell_label(x["sheet"], x["cell"], x.get("row_label") or "")
        return f"{label} ({x['sheet']}!{x['cell']})" if label else f"{x['sheet']}!{x['cell']}"

    def cell_label(self, sheet: str, cell: str, row_label: str = "") -> str:
        """'Revenue!X5' in words: 'New seats, Nov 2027'. Row label down the side,
        period or header across the top."""
        m = re.fullmatch(r"\$?([A-Z]{1,3})\$?(\d+)", cell or "")
        if not m:
            return row_label or ""
        ci = 0
        for ch in m.group(1):
            ci = ci * 26 + ord(ch) - 64
        ci -= 1
        ri = int(m.group(2)) - 1
        head = ""
        for t in self.tables:
            if t.sheet != sheet or ci not in t.cols:
                continue
            if t.top <= ri <= t.last_row:
                head = t.headers[t.cols.index(ci)]
                lab_col = t.row_label_col if t.row_label_col >= 0 else (0 if self.row_labels(t) else -1)
                if lab_col >= 0 and ri in t.row_index:
                    if not row_label:
                        v = t.rows[t.row_index.index(ri)][lab_col]
                        row_label = str(v).strip() if isinstance(v, str) else ""
                    if lab_col == 0 and not t.wide and row_label:
                        head = ""          # 'Starting seats', not 'Starting seats, Value'
                break
        mm = re.fullmatch(r"(\d{4})-(\d{2})-\d{2}(?:[ T].*)?", str(head))
        if mm:
            head = f"{_MONTHS[int(mm.group(2)) - 1]} {mm.group(1)}"
        if head.startswith("Column "):
            head = ""
        return ", ".join(x for x in (row_label, head) if x)

    # ------------------------------------------------------------------
    def summary(self, samples: bool = True) -> dict:
        """Bounded, JSON-safe profile for profile.json and the model."""
        files = []
        for b in self.books:
            sheets = []
            for s in b.sheets:
                if s.is_brain:
                    continue
                sheets.append({"name": s.name, "state": s.state, "rows": s.n_rows, "cols": s.n_cols,
                               "formulas": len(s.formulas), "rules_sheet": s.is_rules,
                               "tables": [t.tid for t in self.tables if self.file_of[t.tid] == b.path
                                          and t.sheet == s.name]})
            files.append({"path": b.path, "name": b.name, "kind": b.kind, "size": b.size,
                          "sheets": sheets})
        tables = []
        for t in self.tables:
            tables.append({
                "table": t.tid, "file": os.path.basename(self.file_of[t.tid]), "sheet": t.sheet,
                "title": t.title, "rows": t.n_rows, "header_row": (t.header_rows[-1] + 1)
                if t.header_rows else None, "wide": t.wide, "derived": t.tid in self.derived,
                "totals_rows": len(t.totals_rows),
                "key": self.keys.get(t.tid, []), "determines": self.fds.get(t.tid, [])[:12],
                "columns": [c.to_dict(samples) for c in self.cols[t.tid]][:120],
            })
        det = dict(self.detection)
        det["roles"] = {r: {"table": v["table"], "header": v["header"]}
                        for r, v in det["roles"].items()}
        return {
            "files": files,
            "tables": tables,
            "main_table": self.main_table.tid if self.main_table else None,
            "joins": self.joins[:40],
            "formulas": {os.path.basename(p): {k: v for k, v in fa.items() if k != "row_flow" and not k.startswith("_")}
                         for p, fa in self.formulas.items() if fa},
            "detection": det,
            "insights": [{k: v for k, v in i.items() if k in ("recipe", "kind", "statement")}
                         for i in self.insights],
        }
