"""Orchestrates the deterministic pass over one or more files: load, find
tables, profile columns, keys, dependencies, joins, formulas, archetype, and
the computed insights. The result is what every other module works from.
"""
from __future__ import annotations

import difflib
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


_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
_AS_RECORDED = ("product_check:",)      # recipes that read values before the owner's unit rules


class Analysis:
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

    def _structure_facts(self, t, out: list):
        """How the rows are written, stated with counts: stacked blocks, cells read
        from text, a minority of another type in a column, and spellings that differ
        only in capitals or spaces. Counted facts, never questions."""
        if len(t.segments) >= 2:
            first = t.segments[0]["headers"]
            renamed = []
            for s in t.segments[1:]:
                for a, b in zip(first, s["headers"]):
                    if b and re.sub(r"\W+", "", a.lower()) != re.sub(r"\W+", "", b.lower()) \
                            and (a, b) not in renamed:
                        renamed.append((a, b))
            at = ", ".join(f"{s['header_row'] + 1:,}" for s in t.segments[1:5])
            k = len(t.segments) - 1
            stmt = (f"{t.sheet} holds {len(t.segments)} blocks stacked one under the other: another header "
                    f"row sits at row{'s' if k != 1 else ''} {at}")
            if renamed:
                stmt += (" with other column names (" + ", ".join(f"{b} where the first block has {a}"
                                                                for a, b in renamed[:3])
                         + (", and more" if len(renamed) > 3 else "") + ")")
            out.append({"recipe": f"structure:segments:{t.tid}", "kind": "structure", "weight": 0,
                        "statement": stmt + ". Header rows are left out of every count.",
                        "depends": [], "numbers": {"sheet": t.sheet, "blocks": len(t.segments),
                                                   "header_rows": [s["header_row"] + 1 for s in t.segments],
                                                   "renamed": renamed[:20]}})
        for c in self.cols[t.tid]:
            if c.retyped:
                date = c.retyped_kind == "date"
                what = ("date" if date else "number") + ("s" if c.retyped != 1 else "")
                ex = " and ".join(f"'{e}'" for e in c.retyped_examples[:2])
                reading = ("dates, " + tables_mod.date_reading(c.retyped_format)) if date and c.retyped_format \
                    else "dates" if date else "numbers"
                out.append({"recipe": f"structure:read_from_text:{t.tid}:{c.header}", "kind": "structure",
                            "weight": 0,
                            "statement": f"{c.header} on {t.sheet} has {c.retyped:,} {what} typed as text "
                                         f"(for example {ex}); {'they are' if c.retyped != 1 else 'it is'} "
                                         f"read here as {reading}.",
                            "depends": [(t.sheet, c.header)],
                            "numbers": {"table": t.tid, "col": c.header, "cells": c.retyped,
                                        "kind": c.retyped_kind, "format": c.retyped_format,
                                        "examples": c.retyped_examples}})
            if c.ambiguous_dates:
                n = c.ambiguous_dates
                ex = " and ".join(f"'{e}'" for e in c.retyped_examples[:2])
                out.append({"recipe": f"structure:ambiguous_dates:{t.tid}:{c.header}", "kind": "structure",
                            "weight": 0,
                            "statement": f"{c.header} on {t.sheet} has {n:,} date{'s' if n != 1 else ''} typed as "
                                         f"text that read{'s' if n == 1 else ''} as a date both day first and month first "
                                         f"(for example {ex}); {'they are' if n != 1 else 'it is'} left as "
                                         f"written here.",
                            "depends": [(t.sheet, c.header)],
                            "numbers": {"table": t.tid, "col": c.header, "cells": n,
                                        "examples": c.retyped_examples}})
            if c.sensitive or c.avg_len > 40 or c.type == "empty":
                continue
            kinds: Counter = Counter()
            example: dict = {}
            for v in t.column(c.j):
                if v is None or (isinstance(v, str) and not v.strip()):
                    continue
                kd = ("yes or no value" if isinstance(v, bool) else "number" if isinstance(v, (int, float))
                      else "date" if hasattr(v, "year") else "text value")
                kinds[kd] += 1
                example.setdefault(kd, v)
            if len(kinds) > 1:
                (major, m), *rest = kinds.most_common()
                for kd, n in rest:
                    if kd == "text value" and c.ambiguous_dates:
                        continue          # stated above as dates left as written
                    ex = example[kd]
                    ex = ex.isoformat()[:10] if hasattr(ex, "year") else str(ex)[:40]
                    # a grouping column whose values each keep to one of the two kinds says which rows hold which
                    only = self._kind_by_group(t, c, kd)
                    said = f"; {kd}s only for {_listed(only[1])} in {only[0]}" if only else ""
                    out.append({"recipe": f"structure:mixed_types:{t.tid}:{c.header}:{kd}", "kind": "structure",
                                "weight": 0,
                                "statement": f"{c.header} on {t.sheet} holds {n:,} {kd}{'s' if n != 1 else ''} "
                                             f"(for example '{ex}') where the other {m:,} cells are {major}s{said}.",
                                "depends": [(t.sheet, c.header)],
                                "numbers": {"table": t.tid, "col": c.header, "cells": n, "kind": kd,
                                            "majority": major, "example": ex,
                                            "by": list(only) if only else None}})
            if c.variants:
                n = sum(k for _, forms in c.variants for _, k in forms)
                usual, forms = c.variants[0]
                shown = " and ".join(f"'{v}'" for v, _ in forms[:3])
                out.append({"recipe": f"structure:spellings:{t.tid}:{c.header}", "kind": "structure", "weight": 0,
                            "statement": f"{c.header} on {t.sheet} has {n:,} cell{'s' if n != 1 else ''} that "
                                         f"differ from the usual spelling only in capitals or spaces (for example "
                                         f"{shown} for '{usual}').",
                            "depends": [(t.sheet, c.header)],
                            "numbers": {"table": t.tid, "col": c.header, "cells": n,
                                        "variants": [[u, [v for v, _ in fs]] for u, fs in c.variants[:20]]}})

    def _kind_by_group(self, t, c, kind: str):
        """(a grouping column, its values whose rows hold this kind in c) when that
        column decides the kind: each of its values keeps to one kind on 98% or more
        of its rows, with 2 to 30 values, and both kinds each have a value of their
        own. None otherwise."""
        def kd(v):
            if v is None or (isinstance(v, str) and not v.strip()):
                return None
            return ("yes or no value" if isinstance(v, bool) else "number" if isinstance(v, (int, float))
                    else "date" if hasattr(v, "year") else "text value")
        for g in self.cols[t.tid]:
            if g is c or g.type != "text" or g.sensitive or g.distinct_capped or not (2 <= g.distinct <= 30) \
                    or g.semantic != "dimension":
                continue
            by: dict = {}
            for r in t.rows:
                gk = profile_mod.norm_key(r[g.j] if g.j < len(r) else None)
                k = kd(r[c.j] if c.j < len(r) else None)
                if gk is not None and k is not None:
                    by.setdefault(gk, Counter())[k] += 1
            mine, ok = [], True
            for gk, cnt in by.items():
                top, n = cnt.most_common(1)[0]
                if n < 0.98 * sum(cnt.values()):
                    ok = False
                    break
                if top == kind:
                    mine.append(gk)
            if ok and mine and len(mine) < len(by):
                return g.header, sorted(_written_key(t, g, k) for k in mine)
        return None

    def _blank_groups(self, t, out: list):
        """A grouping column (class, department, location) blank on many rows: the
        blank usually means something (shared, overhead, all) that only the owner
        knows. Notes and free text are never asked. A blank that sits exactly where
        another column is blank, zero or one value is a counted fact, not a
        question. When the blanks bunch on some values of another column (accounts,
        or a lookup's category for them) where the column is nearly always blank,
        that is a counted fact too, and only the slice where it is filled in is
        asked about, with that slice's rows and money."""
        from .findings import _NOTES
        for c in self.cols[t.tid]:
            total = c.count + c.nulls
            if c.semantic != "dimension" or c.type != "text" or c.sensitive or not (2 <= c.distinct <= 30) \
                    or not total or c.nulls < 20 or c.nulls / total < 0.10 or _NOTES.search(str(c.header)):
                continue
            blank = {i for i, r in enumerate(t.rows) if _blank(r[c.j] if c.j < len(r) else None)}
            if self._blank_match(t, c, blank, out):
                continue
            vals = ", ".join(str(k)[:30] for k, _ in c.top[:4])
            sl = self._blank_slice(t, c, blank)
            if sl is not None:
                self._blank_slice_facts(t, c, sl, vals, out)
                continue
            out.append({"recipe": f"blanks:{t.tid}:{c.header}", "kind": "gotcha", "oddity": True, "weight": 3,
                        "statement": f"{c.nulls:,} of {total:,} rows on {t.sheet} ({c.nulls / total:.0%}) have "
                                     f"no {c.header}; the rest are {vals}.",
                        "depends": [(t.sheet, c.header)],
                        "numbers": {"rows": c.nulls, "total": total, "table": t.tid, "col": c.header,
                                    "values": [str(k)[:30] for k, _ in c.top[:4]]}})

    def _blank_match(self, t, c, blank: set, out: list, numbers: bool = True) -> bool:
        """A column blank on the same rows (99% both ways) as another column is
        blank, is zero or holds one value: said once as a counted fact, never
        asked. A single value must cover 5 rows or more to count. numbers=False:
        another number column blank on the same rows is no explanation."""
        if not blank:
            return False
        for x in self.cols[t.tid]:
            if x is c or x.type == "empty":
                continue
            unexplained = not numbers and _metric(x)
            sets: dict = {}
            for i, r in enumerate(t.rows):
                v = r[x.j] if x.j < len(r) else None
                if _blank(v):
                    sets.setdefault(("blank", ""), set()).add(i)
                    continue
                if _is_num(v) and _metric(x):          # the other side of one amount (a debit and a credit)
                    sets.setdefault(("filled", ""), set()).add(i)
                if _is_num(v) and v == 0:
                    sets.setdefault(("zero", ""), set()).add(i)
                elif x.type == "text" and x.distinct <= 60 and not x.sensitive and len(blank) >= 5:
                    sets.setdefault(("value", _written(v)), set()).add(i)
            for (how, v), rows in sets.items():
                both = len(blank & rows)
                if both < 0.99 * len(blank) or both < 0.99 * len(rows) or (unexplained and how == "blank"):
                    continue
                what = {"blank": "is blank", "zero": "is 0", "filled": "has a value"}.get(how) \
                    or f"is {_shown_value(v)}"
                pair = sorted([c.header, x.header])
                rec = f"structure:blank_match:{t.tid}:{pair[0]}:{pair[1]}"
                if not any(i.get("recipe") == rec for i in out):
                    n = len(blank)
                    out.append({"recipe": rec, "kind": "structure", "weight": 0,
                                "statement": f"{c.header} on {t.sheet} is blank on {n:,} row{'s' if n != 1 else ''}, "
                                             f"the same rows where {x.header} {what}.",
                                "depends": [(t.sheet, c.header), (t.sheet, x.header)],
                                "numbers": {"table": t.tid, "col": c.header, "with": x.header, "how": how,
                                            "value": v, "rows": n}})
                return True
        return False

    def _blank_slice(self, t, c, blank: set) -> dict | None:
        """Where a column is nearly always blank: the values of another column (an
        account, a category, or a lookup's category for them) on whose rows it is
        blank 90% of the time or more, holding at least half the blanks, while on
        the other values it is filled in on most rows. A category is tried before
        an entity. None when the blanks do not bunch that way."""
        groupings = []            # (distinct values, label, {row position: key}, {key: as written})
        for x in self.cols[t.tid]:
            if x is c or x.type not in ("text", "number") or _metric(x) or x.sensitive or x.distinct_capped \
                    or not (2 <= x.distinct <= 300):
                continue
            keys = {i: profile_mod.norm_key(r[x.j] if x.j < len(r) else None) for i, r in enumerate(t.rows)}
            shown = {}
            for r in t.rows:
                v = r[x.j] if x.j < len(r) else None
                shown.setdefault(profile_mod.norm_key(v), _written(v))
            groupings.append((x.distinct, x.header, keys, shown))
        for j in self.joins:          # a lookup's category: the account's type on a chart of accounts
            if j["from_table"] != t.tid or j["band"] != "auto":
                continue
            lt, fc, kc = self.table(j["to_table"]), self.col(t.tid, j["from_col"]), self.col(j["to_table"], j["to_col"])
            if fc is None or kc is None:
                continue
            for x in self.cols[lt.tid]:
                if x is kc or x.type != "text" or x.sensitive or not (2 <= x.distinct <= 10):
                    continue
                look, shown = {}, {}
                for r in lt.rows:
                    v = r[x.j] if x.j < len(r) else None
                    look.setdefault(profile_mod.norm_key(r[kc.j] if kc.j < len(r) else None), profile_mod.norm_key(v))
                    shown.setdefault(profile_mod.norm_key(v), _written(v))
                keys = {i: look.get(profile_mod.norm_key(r[fc.j] if fc.j < len(r) else None))
                        for i, r in enumerate(t.rows)}
                groupings.append((x.distinct, f"{x.header} on {lt.sheet}", keys, shown))
        for _d, label, keys, shown in sorted(groupings, key=lambda g: g[0]):
            rows_of: dict = {}
            for i, k in keys.items():
                if k is not None:
                    rows_of.setdefault(k, []).append(i)
            gone = {k: sum(1 for i in rs if i in blank) for k, rs in rows_of.items()}
            never = [k for k, rs in rows_of.items() if len(rs) >= 3 and gone[k] >= 0.9 * len(rs)]
            filled = [k for k, rs in rows_of.items() if gone[k] < 0.5 * len(rs)]
            n_never, n_filled = sum(len(rows_of[k]) for k in never), sum(len(rows_of[k]) for k in filled)
            if not never or not filled or n_never < 5 or n_never + n_filled < 0.95 * sum(map(len, rows_of.values())) \
                    or sum(gone[k] for k in never) < 0.5 * len(blank):
                continue
            by = lambda k: -len(rows_of[k])  # noqa: E731
            return {"col": label, "never": [shown.get(k, k) for k in sorted(never, key=by)],
                    "never_rows": n_never, "never_blank": sum(gone[k] for k in never),
                    "values": [shown.get(k, k) for k in sorted(filled, key=by)],
                    "rows": {i for k in filled for i in rows_of[k]},
                    "blank": {i for k in filled for i in rows_of[k] if i in blank}}
        return None

    def _blank_slice_facts(self, t, c, sl: dict, vals: str, out: list):
        """The blanks where the column is nearly always blank, as a counted fact;
        the blanks where it is filled in, as the finding to ask about."""
        from .findings import money_column, money_fmt
        nv, nb = sl["never_rows"], sl["never_blank"]
        out.append({"recipe": f"structure:blank_slice:{t.tid}:{c.header}", "kind": "structure", "weight": 0,
                    "statement": f"{c.header} on {t.sheet} is blank on {nb:,} of the {nv:,} rows where {sl['col']} is "
                                 f"{_listed(sl['never'])}.",
                    "depends": [(t.sheet, c.header)],
                    "numbers": {"table": t.tid, "col": c.header, "by": sl["col"], "values": sl["never"][:20],
                                "rows": nb, "total": nv}})
        k, n = len(sl["blank"]), len(sl["rows"])
        if k < max(5, 0.02 * n):
            return                  # filled in nearly everywhere it applies: nothing to ask
        m = money_column(self, t)
        tot = part = 0.0
        for i, r in enumerate(t.rows):
            v = r[m.j] if m is not None and m.j < len(r) else None
            if _is_num(v):
                tot += abs(v)
                part += abs(v) if i in sl["blank"] else 0.0
        stake = part / tot if tot else k / t.n_rows
        money = f", {money_fmt(self, t, m)(part)} of {m.header}" if m is not None and tot else ""
        out.append({"recipe": f"blanks:{t.tid}:{c.header}", "kind": "gotcha", "oddity": True, "weight": 3,
                    "statement": f"{k:,} of {n:,} rows on {t.sheet} where {sl['col']} is {_listed(sl['values'])} have no "
                                 f"{c.header}{money}; the rest are {vals}.",
                    "depends": [(t.sheet, c.header)],
                    "numbers": {"rows": k, "total": n, "table": t.tid, "col": c.header,
                                "values": [str(v)[:30] for v, _ in c.top[:4]], "money": money[2:],
                                "slice": {"col": sl["col"], "values": sl["values"][:20]}, "stake": round(stake, 6),
                                "blank_rows": sorted(sl["blank"])[:5000]}})

    def _measure_blanks(self, t, out: list):
        """The table's main measure (its money column) blank on rows that are
        otherwise filled in: zero, unknown or the last value carried on, and only
        the owner knows which. Asked whenever any exist, with where they bunch; a
        blank on the same rows where another column is blank, zero or one value
        is a counted fact instead."""
        from .findings import money_column
        m = money_column(self, t)
        if m is None or not m.nulls or m.header in ((self.detection.get("pairs") or {}).get(t.tid) or []):
            return                # a debit or a credit is blank wherever the other side holds the amount
        need = max(2, 0.5 * (len(t.headers) - 1))
        blank = {i for i, r in enumerate(t.rows) if _blank(r[m.j] if m.j < len(r) else None)
                 and sum(1 for v in r if not _blank(v)) >= need}
        # another number blank on the same rows (the count the amount is worked out from) explains nothing:
        # both are unknown there, and what a blank means is still the owner's to say
        if not blank or self._blank_match(t, m, blank, out, numbers=False):
            return
        also = [x.header for x in self.cols[t.tid] if x is not m and _metric(x) and self._same_blanks(t, x, blank)]
        k, n = len(blank), t.n_rows
        where = self._bunch(t, blank)
        with_ = f", the same rows where {_join_words(also)} {'is' if len(also) == 1 else 'are'} blank" if also else ""
        # the money is worked out from one of those columns (amount = count x price on the filled rows): the
        # blanks come from that input, so the question is about it
        made = self._made_from(t, m, [x for x in self.cols[t.tid] if x.header in also])
        out.append({"recipe": f"blankmeasure:{t.tid}:{m.header}", "kind": "gotcha", "oddity": True, "weight": 3,
                    "statement": f"{m.header} on {t.sheet} is blank on {k:,} of {n:,} row{'s' if n != 1 else ''}"
                                 + with_ + (f", mostly at {where}" if where else "") + ".",
                    "depends": [(t.sheet, m.header)] + [(t.sheet, h) for h in also],
                    "numbers": {"table": t.tid, "col": m.header, "rows": k, "total": n, "where": where,
                                "with": also, "stake": round(k / n, 6) if n else 0.0,
                                **({"input": made[0], "price": made[1]} if made else {})}})

    def _made_from(self, t, m, inputs: list):
        """(input column, price column) when m = input x price on 99% of the rows
        where all three are filled (5 or more), for one of these inputs; else None."""
        nums = [c for c in self.cols[t.tid] if _metric(c) and c is not m]
        rows = t.rows[:4000]
        for x in inputs:
            for p in nums:
                if p is x:
                    continue
                trio = [(r[x.j], r[p.j], r[m.j]) for r in rows if max(x.j, p.j, m.j) < len(r)
                        and _is_num(r[x.j]) and _is_num(r[p.j]) and _is_num(r[m.j])]
                if len(trio) >= 5 and sum(1 for a, b, c in trio if abs(a * b - c) <= 0.0051) >= 0.99 * len(trio):
                    return x.header, p.header
        return None

    def _same_blanks(self, t, x, blank: set) -> bool:
        """Column x is blank on the same rows as these (99% both ways)."""
        mine = {i for i, r in enumerate(t.rows) if _blank(r[x.j] if x.j < len(r) else None)}
        both = len(blank & mine)
        return bool(mine) and both >= 0.99 * len(blank) and both >= 0.99 * len(mine)

    def _bunch(self, t, rows: set) -> str:
        """Where rows bunch, in words: a value of a grouping column that holds 60%
        of them or more and at least twice its share of the table (at most two
        columns), and the dates when they all sit within 10% of the table's span.
        '' when they spread out."""
        parts = []
        for x in self.cols[t.tid]:
            if x.type != "text" or x.sensitive or x.semantic != "dimension" or not (2 <= x.distinct <= 60) \
                    or len(parts) >= 2:
                continue
            got = Counter(profile_mod.norm_key(t.rows[i][x.j] if x.j < len(t.rows[i]) else None) for i in rows)
            k, hits = got.most_common(1)[0]
            if k is None or hits < 0.6 * len(rows) or not x.count or hits / len(rows) < 2 * x.counter.get(k, 0) / x.count:
                continue
            parts.append(f"{_written_key(t, x, k)} in {x.header}")
        aj = self._axis_j(t)
        days = sorted(t.rows[i][aj] for i in rows if aj is not None and aj < len(t.rows[i])
                      and hasattr(t.rows[i][aj], "year"))
        every = [r[aj].toordinal() for r in t.rows if aj is not None and aj < len(r) and hasattr(r[aj], "year")]
        if len(days) >= 2 and every and (days[-1].toordinal() - days[0].toordinal()) \
                <= 0.10 * (max(every) - min(every)):
            parts.append(_span_words(days[0], days[-1]))
        return ", ".join(parts)

    # ------------------------------------------------------------------
    # one date where the file changes, and rows that are there twice
    # ------------------------------------------------------------------
    def _boundaries(self, t, out: list):
        """One date where several columns change at once: how values are written
        (text dates to dates, 'LAST, FIRST' to 'First Last', one ID prefix to
        another), which labels are used (site names to site codes), the sign of a
        money column, or how a number column is written (whole percents from a
        few values to amounts with cents). Usually a new system or a second export
        stacked under the first; only the owner knows whether a column also
        changed meaning. The labels of one column changing alone is a handoff (an
        old value another takes over from), never a boundary."""
        cols = self.cols[t.tid]
        dated = [c for c in cols if c.type == "date"]
        axis = next((c for c in dated if not profile_mod.is_audit(t, c)), dated[0] if dated else None)
        if axis is None or t.n_rows < BOUNDARY_ROWS:
            return
        day = {i: r[axis.j].toordinal() for i, r in enumerate(t.rows)
               if axis.j < len(r) and hasattr(r[axis.j], "year")}
        n = len(day)
        span = (max(day.values()) - min(day.values())) if day else 0
        if n < BOUNDARY_ROWS or span < 28:
            return
        need = max(20, 0.02 * n)
        at = len(t.row_index) == t.n_rows
        found = []          # (column, view, old key, new key, switch day)
        for c in cols:
            if c.type == "empty":
                continue
            texted = _texted(t, c.j)
            metric = _metric(c)
            groups: dict = {}
            labels: dict = {}
            for i in day:
                v = t.rows[i][c.j] if c.j < len(t.rows[i]) else None
                if metric:
                    if _is_num(v) and v != 0:
                        groups.setdefault("positive" if v > 0 else "negative", []).append(i)
                    continue
                groups.setdefault(_form(v, i in texted), []).append(i)
                if c is not axis and not c.sensitive and c.type in ("text", "number") and 2 <= c.distinct <= 60:
                    k = profile_mod.norm_key(v)
                    if k is not None:
                        labels.setdefault(k, []).append(i)
            for view, g in (("sign" if metric else "form", groups), ("label", labels)):
                found += [(c, view, a, b, d) for a, b, d in _successions(g, day, need, span)]
        # a stacked export whose blocks follow one another in time: its seam is a candidate date too
        seams, at_seam = [], {}
        copied = self._copy_rows(t, out)          # a window loaded twice across the seam still lets it follow on
        for s in t.segments[1:]:
            i0 = next((i for i, row in enumerate(t.row_index) if row >= s["start"]), None) if at else None
            before = [d for i, d in day.items() if i0 is not None and i < i0 and i not in copied]
            after = [d for i, d in day.items() if i0 is not None and i >= i0 and i not in copied]
            if len(before) >= need and len(after) >= need and max(before) <= min(after):
                seams.append(min(after))
                at_seam[min(after)] = s
        # the end of the period a title names, when rows run past it on both sides: a candidate date too
        end = self.title_dates.get(t.tid)
        if end is not None and sum(1 for d in day.values() if d <= end) >= need \
                and sum(1 for d in day.values() if d > end) >= need:
            seams.append(end + 1)
        found.sort(key=lambda f: f[4])
        clusters: list = []
        for f in found:           # switches within 14 days of each other are one date
            if clusters and f[4] - clusters[-1][0][4] <= 14:
                clusters[-1].append(f)
            else:
                clusters.append([f])
        # each column switches on its first switch day in the window; the date most columns share wins
        dates = []
        for cl in clusters:
            first: dict = {}
            for c, _v, _a, _b, d in cl:
                first[c.header] = min(first.get(c.header, d), d)
            dates.append(max(Counter(first.values()).items(), key=lambda kv: (kv[1], -kv[0]))[0])
        for d in seams:
            if all(abs(d - x) > 14 for x in dates):
                clusters.append([])
                dates.append(d)
        start = len(out)
        for cl, d in zip(clusters, dates):
            seg = next((s for x, s in at_seam.items() if abs(x - d) <= 14), None)
            self._boundary_at(t, axis, day, need, cl, d, out, seg)
        self._merge_near_boundaries(t, out, start)
        from .findings import HANDOFF_SHOWN
        for (tid, col), ho in self.handoffs.items():       # each renamed column once, stated with its counts
            if tid != t.tid:
                continue
            k = HANDOFF_SHOWN
            shown = "; ".join(f"{p['old']} -> {p['new']} ({_pair_why(p, ho['via'])})"
                              for p in ho["pairs"][:k])
            out.append({"recipe": f"handoff:{t.tid}:{col}", "kind": "history", "weight": 1,
                        "statement": f"From {ho['when']} on, {col} on {t.sheet} uses new values where old ones stop: "
                                     f"{shown}" + (f", and {len(ho['pairs']) - k:,} more" if len(ho["pairs"]) > k
                                                   else "") + ".",
                        "depends": [(t.sheet, col), (t.sheet, ho["via"])],
                        "numbers": {"table": t.tid, "col": col, "date": ho["date"], "via": ho["via"],
                                    "pairs": ho["pairs"][:20], "boundary": ho["boundary"]}})

    def _merge_near_boundaries(self, t, out: list, start: int):
        """Dates on one table where the file changes, BOUNDARY_NEAR days apart or
        less, are one switch: kept as the date with the most columns changing, each
        other column's change said with its own date."""
        mine = [i for i in out[start:] if i.get("recipe", "").startswith(f"boundary:{t.tid}:")]
        if len(mine) < 2:
            return
        mine.sort(key=lambda i: i["numbers"]["date"])
        groups, cur = [], [mine[0]]
        for i in mine[1:]:
            if _ord(i["numbers"]["date"]) - _ord(cur[-1]["numbers"]["date"]) <= BOUNDARY_NEAR:
                cur.append(i)
            else:
                groups.append(cur)
                cur = [i]
        groups.append(cur)
        drop = []
        for g in groups:
            if len(g) < 2:
                continue
            base = max(g, key=lambda i: (len({c["col"] for c in i["numbers"]["changes"]}),
                                         min(i["numbers"]["before"], i["numbers"]["after"])))
            have = {c["col"] for c in base["numbers"]["changes"]}
            for other in g:
                if other is base:
                    continue
                when = other["numbers"]["when"]
                for c in other["numbers"]["changes"]:
                    if c["col"] not in have:
                        base["numbers"]["changes"].append(dict(c, text=f"{c['text']} (from {when})"))
                        have.add(c["col"])
                base["numbers"]["handoffs"] = list(dict.fromkeys((base["numbers"].get("handoffs") or [])
                                                                 + (other["numbers"].get("handoffs") or [])))
                base["numbers"].setdefault("merged", []).append(other["numbers"]["date"])
                base["statement"] = base["statement"].rstrip(".") + f"; more columns change around {when}."
                drop.append(id(other))
        if drop:
            out[:] = [i for i in out if id(i) not in drop]

    def _boundary_at(self, t, axis, day: dict, need: float, found: list, d: int, out: list, seg=None):
        """What changes at one date: each column's change in words, the number
        columns written another way, and the handoffs in columns whose labels
        change. Stated when 2 or more columns change or a number column does.
        seg: the stacked block that starts at this date, whose header row and
        renamed labels (and the total rows that end each part) the question names."""
        import datetime as dt
        before = sorted((i for i in day if day[i] < d), key=lambda i: day[i])
        after = sorted((i for i in day if day[i] >= d), key=lambda i: day[i])
        if len(before) < need or len(after) < need:
            return
        when = dt.date.fromordinal(d)
        iso, words = when.isoformat(), f"{_MONTHS[when.month - 1]} {when.day}, {when.year}"
        changes: dict = {}          # header -> [(kind, words)]
        by_col: dict = {}
        for c, view, a, b, _d in found:
            by_col.setdefault(c.header, (c, {}))[1].setdefault(view, []).append((a, b))
        # handoffs: in a column whose labels change, which new value takes over from which old one. Every
        # value of 5 rows or more that stops (or starts) around the date takes part, not only the large ones
        handoffs, moved = [], {}
        span = max(day.values()) - min(day.values())
        for h, (c, views) in by_col.items():
            if "label" not in views:
                continue
            olds, news = set(), set()
            # rows a copies finding marks as loaded twice say nothing about when a value stops or starts
            copied = self._copy_rows(t, out)
            clean = {i: x for i, x in day.items() if i not in copied} if copied else day
            for k, ds in _label_days(t, c, clean).items():
                on_before = sum(1 for x in ds if x < d)
                if len(ds) < 5:
                    continue
                if on_before >= 0.9 * len(ds) and ds[-1] >= d - 0.15 * span:
                    olds.add(k)
                elif on_before <= 0.1 * len(ds) and ds[0] <= d + 0.15 * span:
                    news.add(k)
            moved[h] = (olds, news)
            got = self._handoff_pairs(t, c, olds, news) if olds and news else None
            # old values the shared rows cannot pair (every status sells every item): paired by when they run,
            # how many rows a day, and their mix of another column or their spelling
            done = {profile_mod.norm_key(p["old"]) for p in (got or {}).get("pairs", [])}
            taken = {profile_mod.norm_key(p["new"]) for p in (got or {}).get("pairs", [])}
            more = self._fallback_pairs(t, c, clean, olds - done, news - taken) if olds - done and news - taken \
                else []
            if more:
                got = got or {"pairs": [], "via": (more[0].get("via") or c.header)}
                got["pairs"] += more
            if got:
                # a value only written another way from the same date ('North Yard', then 'NORTH YARD') is in the
                # same list as the values renamed then
                got["pairs"] += [p for p in self._case_pairs(t, c, day, d, span, got["via"])
                                 if profile_mod.norm_key(p["old"]) not in {profile_mod.norm_key(x["old"])
                                                                          for x in got["pairs"]}]
                handoffs.append(dict(got, col=h))
        signs = []
        for h, (c, views) in by_col.items():
            if "sign" in views:
                first, then = views["sign"][0]
                changes[h] = [("sign", f"{first} before, {then} after")]
                signs.append((c, first, then))
                continue
            kind = "label" if "label" in views else "form"      # which values are used says more than how
            ho = next((x for x in handoffs if x["col"] == h), None)
            a, b = max(views.get("form") or [("", "")], key=lambda p: sum(1 for i in day if _form_of(t, c, i) in p))
            shown = self._form_change(t, c, a, b, before, after) if a else ""
            if a and "label" in views and not c.sensitive:
                # labels: an example is shown only when it is one value written two ways ('SMITH, JO', 'Jo Smith')
                shown = self._rewrite(t, c, a, b, before, after)
            if ho:          # an old value and its new one only when the file pairs them (a handoff)
                changes[h] = [(kind, f"\"{ho['pairs'][0]['old']}\" before, \"{ho['pairs'][0]['new']}\" after")]
            elif "label" in views and not shown:
                # labels that change with no pair the file supports: the values that stop and those that start,
                # named (never paired)
                no, nn = moved.get(h, (set(), set()))
                how = f"{_FORM_WORDS[a]} before, {_FORM_WORDS[b]} after; " \
                    if a in _FORM_WORDS and b in _FORM_WORDS else ""
                stop = f"{self._some(t, c, no)} stop{'s' if len(no) == 1 else ''}" if no else "no value stops"
                start = f"{self._some(t, c, nn)} start{'s' if len(nn) == 1 else ''}" if nn else "no new one starts"
                changes[h] = [(kind, f"{how}{stop} while {start}")]
            else:
                changes[h] = [("form", shown)]
        for c in self.cols[t.tid]:
            if c is axis or not _metric(c):
                continue
            xs = [t.rows[i][c.j] for i in before if c.j < len(t.rows[i]) and _is_num(t.rows[i][c.j])]
            ys = [t.rows[i][c.j] for i in after if c.j < len(t.rows[i]) and _is_num(t.rows[i][c.j])]
            got = _profile_change(xs, ys) if len(xs) >= need and len(ys) >= need else None
            if got:
                changes.setdefault(c.header, []).append(("number", f"{got[0]} before, {got[1]} after"))
        numeric = any(k in ("sign", "number") for cs in changes.values() for k, _w in cs)
        boundary = numeric or len(changes) >= 2
        for ho in handoffs:
            prev = self.handoffs.get((t.tid, ho["col"]))
            if prev is None or (boundary, len(ho["pairs"])) > (prev["boundary"], len(prev["pairs"])):
                self.handoffs[(t.tid, ho["col"])] = dict(ho, table=t.tid, sheet=t.sheet, date=iso, when=words,
                                                         boundary=boundary)
        if not boundary:
            return
        texts = [f"{h}: {w}" for h, cs in changes.items() for _k, w in cs]
        k = len(changes)
        nb, na = len(before), len(after)
        numbers = {"table": t.tid, "sheet": t.sheet, "col": axis.header, "date": iso, "when": words,
                   "before": nb, "after": na,
                   "changes": [{"col": h, "kind": kd, "text": w} for h, cs in changes.items() for kd, w in cs],
                   "handoffs": [ho["col"] for ho in handoffs]}
        if seg is not None:
            # a second header row at the seam: where, and the labels it names another way
            first = t.segments[0]["headers"]
            renamed = [(x, y) for x, y in zip(first, seg["headers"])
                       if y and re.sub(r"\W+", "", str(x).lower()) != re.sub(r"\W+", "", str(y).lower())]
            numbers["seam"] = {"row": seg["header_row"] + 1, "renamed": renamed[:3]}
        # total rows that end the parts on either side of the date, left out of every count
        if t.totals_rows and (seg is not None or len(t.segments) >= 2 or self._totals_between(t, before, after)):
            numbers["totals"] = [r + 1 for r in sorted(t.totals_rows)][:4]
        out.append({"recipe": f"boundary:{t.tid}:{iso}", "kind": "history", "oddity": True, "weight": 4,
                    "statement": f"Around {words}, {t.sheet} changes form in {k} column{'s' if k != 1 else ''} "
                                 f"({nb:,} rows before, {na:,} from then on): " + "; ".join(texts) + ".",
                    "depends": [(t.sheet, h) for h in changes], "numbers": numbers})
        for c, first, then in signs:          # a sign flip is one view of the boundary, stated with its counts
            vals = [(day[i], t.rows[i][c.j]) for i in day if c.j < len(t.rows[i]) and _is_num(t.rows[i][c.j])
                    and t.rows[i][c.j] != 0]
            i = sum(1 for x, _v in vals if x < d)
            out.append({"recipe": f"signflip:{t.tid}:{c.header}", "kind": "gotcha", "oddity": True, "weight": 4,
                        "statement": f"{c.header} on {t.sheet} is {first} before {iso} ({i:,} rows) and {then} from "
                                     f"{iso} on ({len(vals) - i:,} rows), so a plain sum mixes two sign rules.",
                        "depends": [(t.sheet, c.header)],
                        "numbers": {"col": c.header, "table": t.tid, "date": iso, "before": i, "after": len(vals) - i,
                                    "first": first, "then": then}})

    def _some(self, t, c, keys) -> str:
        """Up to 3 values of column c by their rows, quoted as written, and how many more."""
        rows = Counter(profile_mod.norm_key(r[c.j] if c.j < len(r) else None) for r in t.rows)
        ks = sorted(keys, key=lambda k: (-rows.get(k, 0), str(k)))
        shown = [f"\"{_written_key(t, c, k)}\"" for k in ks[:3]] + ([f"{len(ks) - 3:,} more"] if len(ks) > 3 else [])
        return _join_words(shown)

    @staticmethod
    def _totals_between(t, before: list, after: list) -> bool:
        """A total row sits in the sheet between the rows before a date and those after it."""
        if not before or not after or len(t.row_index) != t.n_rows:
            return False
        last = max(t.row_index[i] for i in before)
        first = min(t.row_index[i] for i in after)
        return any(min(last, first) < r < max(last, first) for r in t.totals_rows)

    def _form_change(self, t, c, a: str, b: str, before: list, after: list) -> str:
        """'"08/01/2023" before, "2023-12-23" after': the last value written the
        old way and the first written the new way. Names of people are described,
        never shown."""
        if c.sensitive:
            return f"{_FORM_WORDS.get(a, 'numbers')} before, {_FORM_WORDS.get(b, 'numbers')} after"
        old = next((i for i in reversed(before) if _form_of(t, c, i) == a), None)
        new = next((i for i in after if _form_of(t, c, i) == b), None)
        return f"\"{_shown(t, c, old, a)}\" before, \"{_shown(t, c, new, b)}\" after"

    def _rewrite(self, t, c, a: str, b: str, before: list, after: list) -> str:
        """'"SMITH, JO" before, "Jo Smith" after': one value written the old way
        and again the new way (the same words), or '' when no value is both."""
        last: dict = {}
        for i in reversed(before):
            if _form_of(t, c, i) == a:
                last.setdefault(_words(t.rows[i][c.j] if c.j < len(t.rows[i]) else None), i)
        for i in after:
            w = _words(t.rows[i][c.j] if c.j < len(t.rows[i]) else None)
            if _form_of(t, c, i) == b and w and w in last:
                return f"\"{_shown(t, c, last[w], a)}\" before, \"{_shown(t, c, i, b)}\" after"
        return ""

    def _handoff_pairs(self, t, c, olds: set, news: set) -> dict | None:
        """Which new value takes over from each old one: the one whose rows share
        the most of another column's values (the same staff, the same items),
        read on the column that tells the values apart best. A partner needs a
        Jaccard of 0.5 or more and 0.3 more than any other value of the column,
        old, new or carrying on (a clerk who shares a site with every other clerk
        is nobody's partner); a new value that only rewrites the same words (a
        name written last name first) is the same value, not a handoff."""
        rows_of: dict = {}
        for i, r in enumerate(t.rows):
            k = profile_mod.norm_key(r[c.j] if c.j < len(r) else None)
            if k is not None:
                rows_of.setdefault(k, []).append(i)
        via = [x for x in self.cols[t.tid] if x is not c and x.type in ("text", "number") and x.distinct >= 2
               and not x.distinct_capped and not _metric(x) and not profile_mod.is_audit(t, x)]
        best = None
        for x in via:
            sets = {k: {profile_mod.norm_key(t.rows[i][x.j] if x.j < len(t.rows[i]) else None) for i in rows} - {None}
                    for k, rows in rows_of.items()}
            jac = {(a, v): _jaccard(sets[a], sets[v]) for a in olds for v in rows_of if v != a}
            margins = []
            for a in olds:
                top = max(news, key=lambda b: jac[(a, b)])
                rest = [jac[(a, v)] for v in rows_of if v not in (a, top)] + [0.0]
                margins.append(jac[(a, top)] - max(rest))
            score = sum(margins) / len(margins)
            if best is None or score > best[0]:
                best = (score, x, jac, sets)
        if best is None:
            return None
        _s, x, jac, sets = best
        aj = self._axis_j(t)
        days = {k: [t.rows[i][aj].toordinal() for i in rows if aj is not None and aj < len(t.rows[i])
                    and hasattr(t.rows[i][aj], "year")] for k, rows in rows_of.items()}
        # rows per day while each value is in use: a value that carries on another keeps its pace
        rate = {k: len(ds) / (max(ds) - min(ds) + 1) if ds else 0.0 for k, ds in days.items()}
        pairs = []
        for a in sorted(olds, key=lambda k: -len(rows_of[k])):
            cands = sorted(news, key=lambda b: (-jac[(a, b)], abs(rate[a] - rate[b]) / max(rate[a], rate[b], 1e-9),
                                                -difflib.SequenceMatcher(None, a, b).ratio()))
            b = cands[0]
            second = max([jac[(a, v)] for v in rows_of if v not in (a, b)] + [0.0])
            if jac[(a, b)] < 0.5 or jac[(a, b)] - second < 0.3 or _words(a) == _words(b):
                continue
            pairs.append({"old": _written_key(t, c, a), "new": _written_key(t, c, b),
                          "jaccard": round(jac[(a, b)], 3), "shared": len(sets[a] & sets[b])})
        return {"pairs": pairs, "via": x.header} if pairs else None

    def _copy_rows(self, t, out: list) -> set:
        """Row positions a copies finding on this table marks as loaded twice (both rows of each pair)."""
        return {i for x in out if x.get("recipe", "").startswith(f"copies:{t.tid}:")
                for i in (x.get("numbers") or {}).get("row_ids") or []}

    def _fallback_pairs(self, t, c, day: dict, olds: set, news: set) -> list:
        """Old and new values paired where shared rows cannot tell them apart: their
        dates do not overlap (by a month at most), their rows a day are alike (half
        to twice), and their mix of another column matches (cosine 0.9 or more) or
        their spelling is close. Each new value pairs once, the best first."""
        import math
        rows_of: dict = {}
        for i in day:
            k = profile_mod.norm_key(t.rows[i][c.j] if c.j < len(t.rows[i]) else None)
            if k in olds or k in news:
                rows_of.setdefault(k, []).append(i)
        if not all(k in rows_of for k in olds) or not any(k in rows_of for k in news):
            return []
        span = {k: (min(day[i] for i in v), max(day[i] for i in v)) for k, v in rows_of.items()}
        rate = {k: len(v) / (span[k][1] - span[k][0] + 1) for k, v in rows_of.items()}
        mixes = [x for x in self.cols[t.tid] if x is not c and not x.sensitive and not x.distinct_capped
                 and 2 <= x.distinct <= 30 and x.type in ("text", "number") and x.semantic != "temporal"
                 and not _metric(x)]

        def mix(k, x):
            return Counter(profile_mod.norm_key(t.rows[i][x.j] if x.j < len(t.rows[i]) else None) for i in rows_of[k])

        def cos(p, q):
            num = sum(p[v] * q.get(v, 0) for v in p)
            den = math.sqrt(sum(v * v for v in p.values())) * math.sqrt(sum(v * v for v in q.values()))
            return num / den if den else 0.0
        scored = []
        for a in olds:
            for b in news:
                if b not in rows_of or span[a][1] > span[b][0] + 31:
                    continue
                r1, r2 = rate[a], rate[b]
                if not r1 or not r2 or min(r1, r2) / max(r1, r2) < 0.5:
                    continue
                best = max(((cos(mix(a, x), mix(b, x)), x.header) for x in mixes), default=(0.0, ""))
                spell = difflib.SequenceMatcher(None, a, b).ratio()
                if best[0] >= 0.9 or spell >= 0.6:
                    scored.append((best[0] + spell, a, b, best[1]))
        out, used_a, used_b = [], set(), set()
        for sc, a, b, via in sorted(scored, key=lambda x: -x[0]):
            if a in used_a or b in used_b or _words(a) == _words(b):
                continue
            # the best new value for this old one, and this old one the best for it: never a pair by elimination
            rival = max((x[0] for x in scored if x[1] == a and x[2] not in used_b and x[2] != b), default=0.0)
            if rival >= sc:
                continue
            used_a.add(a)
            used_b.add(b)
            out.append({"old": _written_key(t, c, a), "new": _written_key(t, c, b), "jaccard": 0.0, "shared": 0,
                        "via": via or c.header, "fallback": True})
        return out

    def _case_pairs(self, t, c, day: dict, d: int, span: int, via: str) -> list:
        """One value written one way before a date and another way after it, the
        two differing only in capitals, spaces or punctuation: the same pairs as a
        handoff ({old, new, jaccard, shared}), read on the handoff's own column."""
        vj = t.headers.index(via) if via in t.headers else None
        forms: dict = {}
        for i, o in day.items():
            v = t.rows[i][c.j] if c.j < len(t.rows[i]) else None
            k = profile_mod.norm_key(v)
            if k is None or not isinstance(v, str):
                continue
            forms.setdefault(re.sub(r"[^0-9a-z]+", "", k), {}).setdefault(v, []).append(i)
        out = []
        for ways in forms.values():
            if len(ways) < 2:
                continue
            olds, news = [], []
            for w, rows in ways.items():
                ds = sorted(day[i] for i in rows)
                on_before = sum(1 for x in ds if x < d)
                if len(ds) < 5:
                    continue
                if on_before >= 0.9 * len(ds) and ds[-1] >= d - 0.15 * span:
                    olds.append((len(ds), w, rows))
                elif on_before <= 0.1 * len(ds) and ds[0] <= d + 0.15 * span:
                    news.append((len(ds), w, rows))
            if not olds or not news:
                continue
            (_n, a, ra), (_m, b, rb) = max(olds), max(news)

            def seen(rows):
                return {profile_mod.norm_key(t.rows[i][vj] if vj < len(t.rows[i]) else None)
                        for i in rows} - {None} if vj is not None else set()
            sa, sb = seen(ra), seen(rb)
            out.append({"old": a.strip(), "new": b.strip(), "jaccard": round(_jaccard(sa, sb), 3),
                        "shared": len(sa & sb), "case_only": True})
        return out

    def _axis_j(self, t):
        dated = [c for c in self.cols[t.tid] if c.type == "date"]
        axis = next((c for c in dated if not profile_mod.is_audit(t, c)), dated[0] if dated else None)
        return axis.j if axis is not None else None

    def _copies(self, t, out: list):
        """Rows that are there twice under two document numbering families: the
        same date, the same entities (columns of 20 or more values) and every
        amount the same, once numbered one way and once another. A window of
        dates re-imported from a second system. Repeats inside one family (a split
        payment, a repeat order) are left alone, and so are two kinds of document
        for one sale (an invoice and its same-day payment), which a code column
        tells apart the same way on nearly every pair, unless that code is one
        name taking over from another (a second system writing it its own way).
        A timestamped date column is used when it
        is the only date: rows match on its day. A document number may sit on
        a few lines (an order of several items); free text that describes a row
        (a memo, a description) is written another way by another system and
        never matched on."""
        from .findings import _NOTES
        cols = self.cols[t.tid]
        dc = next((c for c in cols if c.type == "date" and not profile_mod.is_audit(t, c)),
                  next((c for c in cols if c.type == "date"), None))
        money = [c for c in cols if _metric(c)]
        if dc is None or not money or t.n_rows < 50:
            return
        def numbered(c) -> bool:          # written like document numbers: a prefix and digits, 'AB-1042'
            return c.type == "text" and sum(1 for r in t.rows if c.j < len(r) and _family(r[c.j]) is not None) \
                >= 0.9 * c.count
        docs = [c for c in cols if c.type in ("text", "number") and c.count and c.distinct >= 20
                and c.count <= GROUP_MAX * c.distinct and not c.distinct_capped
                and (c.semantic == "identifier" or profile_mod._numbers_as_ids(c) or numbered(c))]
        ents = [c for c in cols if c not in docs and c.type in ("text", "number") and not c.distinct_capped
                and c.semantic in ("identifier", "dimension") and c.distinct >= 20
                and not _NOTES.search(str(c.header))]
        ents.sort(key=lambda c: c.semantic != "identifier")          # an ID names the entity best
        if not ents and all(c.integers for c in money):
            return            # whole amounts and nothing else to match on: equal rows by chance
        for doc in docs:
            fam_n = Counter(_family(r[doc.j] if doc.j < len(r) else None) for r in t.rows)
            groups: dict = {}
            for i, r in enumerate(t.rows):
                v = r[dc.j] if dc.j < len(r) else None
                if not hasattr(v, "year"):
                    continue
                key = (v.isoformat()[:10],) + tuple(profile_mod.norm_key(r[c.j] if c.j < len(r) else None)
                                                    for c in ents) \
                    + tuple(round(float(r[c.j]), 2) if c.j < len(r) and _is_num(r[c.j]) else 0.0 for c in money)
                groups.setdefault(key, []).append(i)
            by_pair: dict = {}
            for g in groups.values():
                fams: dict = {}
                for i in g:
                    fams.setdefault(_family(t.rows[i][doc.j] if doc.j < len(t.rows[i]) else None), []).append(i)
                fams.pop(None, None)
                if len(fams) < 2:
                    continue
                fa, fb = sorted(fams, key=lambda f: (-fam_n[f], f))[:2]
                by_pair.setdefault((fa, fb), []).extend(zip(fams[fa], fams[fb]))
            skip = {doc.j, dc.j} | {c.j for c in ents + money} | {c.j for c in cols if profile_mod.is_audit(t, c)}
            marks = [c for c in cols if c.j not in skip and 2 <= c.distinct <= 30]
            for (fa, fb), pairs in by_pair.items():
                if len(pairs) < 3 or any(_marks_kind(t, c, pairs) and not self._renamed(t, c, dc, pairs)
                                         for c in marks):
                    continue          # an invoice and its payment: a code tells the two kinds apart every time
                self._copies_fact(t, doc, dc, ents, (fa, fb), pairs, out)

    def _renamed(self, t, c, dc, pairs: list) -> bool:
        """The code that tells a pair's two rows apart is one name taking over from
        another (PAYA until June, PAYB from June on): the first is used from the
        start and stops, the second starts later and carries on, and they share
        only a short stretch of dates (15% of their span or less). A second system
        writing the same thing another way, not two kinds of document."""
        (a, b), _k = Counter((profile_mod.norm_key(t.rows[i][c.j] if c.j < len(t.rows[i]) else None),
                              profile_mod.norm_key(t.rows[j][c.j] if c.j < len(t.rows[j]) else None))
                             for i, j in pairs).most_common(1)[0]
        span: dict = {}
        for r in t.rows:
            k, d = profile_mod.norm_key(r[c.j] if c.j < len(r) else None), (r[dc.j] if dc.j < len(r) else None)
            if k in (a, b) and hasattr(d, "year"):
                o = d.toordinal()
                lo, hi = span.get(k, (o, o))
                span[k] = (min(lo, o), max(hi, o))
        if a == b or len(span) != 2:
            return False
        (a0, a1), (b0, b1) = span[a], span[b]
        if (a0, a1) > (b0, b1):
            (a0, a1), (b0, b1) = (b0, b1), (a0, a1)
        union = max(a1, b1) - min(a0, b0)
        return a0 < b0 and a1 < b1 and union > 0 and max(0, min(a1, b1) - max(a0, b0)) <= 0.15 * union

    def _copies_fact(self, t, doc, dc, ents: list, fams: tuple, pairs: list, out: list):
        la, lb = (_family_label(f) for f in fams)
        ids = [[_written(t.rows[i][doc.j]) for i, _j in pairs], [_written(t.rows[j][doc.j]) for _i, j in pairs]]
        ds = sorted(t.rows[i][dc.j] for i, _j in pairs)
        lo, hi = ds[0].isoformat()[:10], ds[-1].isoformat()[:10]
        n = len(pairs)
        what = f"same {ents[0].header}, date and amount" if ents else "same date and amount"
        # the side a filter can name: a letter prefix no row of the other family starts with, all copies
        # inside the window; else every copy's number. rows: what the filter leaves out
        filters = []
        for k, f in enumerate(fams):
            pre = re.match(r"[A-Za-z]*", f).group(0)
            other = fams[1 - k]
            inside = [r for r in t.rows if doc.j < len(r) and _family(r[doc.j]) == f and hasattr(r[dc.j], "year")
                      and lo <= r[dc.j].isoformat()[:10] <= hi]
            ok = bool(pre) and not re.match(re.escape(pre), other, re.I) and len(inside) == n
            keys = {profile_mod.norm_key(x) for x in ids[k]}
            hit = len(inside) if ok else sum(1 for r in t.rows
                                             if profile_mod.norm_key(r[doc.j] if doc.j < len(r) else None) in keys)
            filters.append({"prefix": pre if ok else "", "ids": ids[k], "rows": hit})
        span = _span_words(ds[0], ds[-1])
        # which numbering came first (its rows start earlier) and whether the other takes over: the old one stops
        # by the window's end and the new one starts no earlier than the window; every old row there has a twin
        firsts, lasts = [], []
        for f in fams:
            fd = [r[dc.j].isoformat()[:10] for r in t.rows if doc.j < len(r) and _family(r[doc.j]) == f
                  and hasattr(r[dc.j], "year")]
            firsts.append(min(fd) if fd else "")
            lasts.append(max(fd) if fd else "")
        old = 0 if firsts[0] <= firsts[1] else 1
        new = 1 - old
        every = bool(filters[old]["prefix"])
        takes = lasts[old] <= hi and firsts[new] >= lo
        cut = next((i["numbers"]["when"] for i in out if i.get("recipe", "").startswith(f"boundary:{t.tid}:")
                    and lo <= i["numbers"]["date"] <= _iso_of(_ord(hi) + 31)), "")
        if takes and not cut:
            cut = _day_words(_ord(hi) + 1)
        out.append({"recipe": f"copies:{t.tid}:{doc.header}:{fams[0]}:{fams[1]}", "kind": "gotcha", "oddity": True,
                    "weight": 4,
                    "statement": f"{n:,} rows on {t.sheet} dated {span} appear twice, once with {la} numbers and once "
                                 f"with {lb} (for example {ids[0][0]} and {ids[1][0]}: {what}).",
                    "depends": [(t.sheet, doc.header), (t.sheet, dc.header)],
                    "numbers": {"table": t.tid, "col": doc.header, "date_col": dc.header, "rows": n,
                                "families": [la, lb], "window": [lo, hi], "span": span, "what": what,
                                "example": [ids[0][0], ids[1][0]], "filters": filters, "old": old,
                                "every": every, "takes_over": cut if takes else "",
                                "row_ids": sorted({i for p in pairs for i in p})[:5000]}})

    def _near_key_blocks(self, t, out: list):
        """Rows a near key repeats: one block (a period and an entity) loaded twice
        at two upload times, or numbers that appear twice, once under one code
        and once under another with the same amounts (a void and its twin)."""
        nk = profile_mod.near_key(t, self.cols[t.tid])
        if not nk:
            return
        key = [self.col(t.tid, h) for h in nk["cols"]]
        self._upload_block(t, key, nk, out)
        self._twin_pairs(t, key, nk, out)

    def _upload_block(self, t, key: list, nk: dict, out: list):
        dated = [c for c in key if c.type == "date"]
        rest = [c for c in key if c not in dated[:1]]
        if not dated or not rest:
            return
        finest = max(rest, key=lambda c: c.distinct)
        bcols = [c for c in key if c is not finest]

        def block(r):
            return tuple(profile_mod.norm_key(r[c.j] if c.j < len(r) else None) for c in bcols)
        sizes = Counter(block(r) for r in t.rows)
        med = sorted(sizes.values())[len(sizes) // 2]
        extra = [i for g in nk["groups"] for i in g]
        (bk, m), = Counter(block(t.rows[i]) for i in extra).most_common(1)
        size = sizes[bk]
        # the repeats sit in one block of a panel, which holds far more rows than the others
        if med < 3 or size < BLOCK_RATIO * med or m < 0.8 * len(extra) or m < 4:
            return
        rows = [i for i, r in enumerate(t.rows) if block(r) == bk]
        for ac in (c for c in self.cols[t.tid] if profile_mod.is_audit(t, c)):
            stamps = Counter(_stamp(t.rows[i][ac.j] if ac.j < len(t.rows[i]) else None) for i in rows)
            if len(stamps) != 2 or None in stamps:
                continue
            (t1, k1), (t2, k2) = sorted(stamps.items())
            # date-and-time stamps sort in time; text stamps ('batch 4') are only names, with no order
            timed = all(hasattr(t.rows[i][ac.j] if ac.j < len(t.rows[i]) else None, "year") for i in rows)
            r0 = t.rows[rows[0]]
            shown = [_shown_value(r0[c.j]) for c in bcols]
            keys = [r0[c.j].isoformat()[:10] if hasattr(r0[c.j], "year") else _written(r0[c.j]) for c in bcols]
            w1, w2 = _stamp_words(t1), _stamp_words(t2)
            label = "(" + ", ".join(shown) + ")"
            out.append({"recipe": f"nearkey:{t.tid}:{ac.header}", "kind": "gotcha", "oddity": True, "weight": 4,
                        "statement": f"On {t.sheet}, {label} has {size:,} rows where the others have {med:,}: "
                                     f"{ac.header} {w1} on {k1:,} rows and {w2} on {k2:,}.",
                        "depends": [(t.sheet, c.header) for c in bcols] + [(t.sheet, ac.header)],
                        "numbers": {"table": t.tid, "cols": [c.header for c in bcols], "types":
                                    ["date" if hasattr(r0[c.j], "year") else "value" for c in bcols],
                                    "keys": keys, "label": label, "size": size, "median": med, "col": ac.header,
                                    "times": [t1, t2], "shown": [w1, w2], "counts": [k1, k2], "timed": timed}})
            return

    def _twin_pairs(self, t, key: list, nk: dict, out: list):
        if len(key) != 1:
            return
        doc = key[0]
        groups = nk["groups"]
        if len(groups) < 3 or any(len(g) != 2 for g in groups):
            return
        cols = self.cols[t.tid]
        money = [c for c in cols if _metric(c) and c is not doc]
        skip = {doc.j} | {c.j for c in cols if c.type == "date" or profile_mod.is_audit(t, c)} | {c.j for c in money}
        if not money:
            return
        code, combos, opposite = None, set(), 0
        for i, j in groups:
            ri, rj = t.rows[i], t.rows[j]
            for c in money:
                a, b = (r[c.j] if c.j < len(r) else None for r in (ri, rj))
                if not (_is_num(a) and _is_num(b) and round(abs(a), 2) == round(abs(b), 2)):
                    return        # a twin carries the same amounts
            a, b = (r[money[0].j] for r in (ri, rj))
            opposite += a * b < 0          # a twin with the other sign nets its row to zero in any sum
            diff = [c for c in cols if c.j not in skip and profile_mod.norm_key(ri[c.j] if c.j < len(ri) else None)
                    != profile_mod.norm_key(rj[c.j] if c.j < len(rj) else None)]
            if len(diff) != 1 or not 2 <= diff[0].distinct <= 30 or (code is not None and diff[0] is not code):
                return            # twins differ in one code column, the same one every time
            code = diff[0]
            combos.add(frozenset(profile_mod.norm_key(r[code.j]) for r in (ri, rj)))
        if len(combos) != 1 or len(next(iter(combos))) != 2:
            return
        rare, common = sorted(next(iter(combos)), key=lambda k: (code.counter.get(k, 0), k))
        first = {}
        for r in t.rows:
            first.setdefault(profile_mod.norm_key(r[code.j] if code.j < len(r) else None), r[code.j])
        vr, vc = _written(first[rare]), _written(first[common])
        k = len(groups)
        out.append({"recipe": f"pairs:{t.tid}:{doc.header}", "kind": "gotcha", "oddity": True, "weight": 4,
                    "statement": f"{k:,} {doc.header} numbers on {t.sheet} appear twice, once as {vc} and once as "
                                 f"{vr} in {code.header}, with the same amount.",
                    "depends": [(t.sheet, doc.header), (t.sheet, code.header)],
                    "numbers": {"table": t.tid, "col": doc.header, "code": code.header, "values": [vc, vr],
                                "pairs": k, "rows": 2 * k, "netted": opposite == k,
                                "ids": [_written(t.rows[g[0]][doc.j]) for g in groups][:2000]}})

    def _exclusive_prefixes(self, t, out: list):
        """One value of a grouping column (a location, a class) whose rows all carry
        a document prefix no other value has ('TR-' only at ABC, while every other
        location mixes many kinds of invoice numbers): usually internal entries, like
        transfers, that count twice if left in. Vendor-specific numbering (each
        vendor its own prefix) is normal and not flagged. Read on every data
        table, not only the largest."""
        pre = re.compile(r"^([A-Za-z]{1,4})[-_ ]?(?=\d)")
        joined = {j["from_col"] for j in self.joins if j["from_table"] == t.tid}

        def family(v):
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                return "#"
            if not isinstance(v, str) or not v.strip():
                return None
            m = pre.match(v.strip())
            return m.group(0).upper() if m else ("#" if v.strip().isdigit() else "?")

        dims = [c for c in self.cols[t.tid] if c.semantic == "dimension" and c.type == "text"
                and 2 <= c.distinct <= 30 and not c.sensitive]
        ids = [c for c in self.cols[t.tid] if c.semantic == "identifier" and c.header not in joined]
        for ic in ids:
            for dc in dims:
                fams: dict = {}
                for r in t.rows:
                    dv = r[dc.j] if dc.j < len(r) else None
                    f = family(r[ic.j] if ic.j < len(r) else None)
                    if isinstance(dv, str) and dv.strip() and f:
                        fams.setdefault(dv.strip(), Counter())[f] += 1
                if len(fams) < 3:
                    continue
                for val, fc in fams.items():
                    if len(fc) != 1:
                        continue
                    p = next(iter(fc))
                    n = fc[p]
                    others = [o for v2, o in fams.items() if v2 != val]
                    widths = sorted(len(o) for o in others)
                    if p in ("#", "?") or n < 20 or widths[len(widths) // 2] < 3 \
                            or any(p in o for o in others):
                        continue
                    out.append({"recipe": f"exclusive:{t.tid}:{dc.header}:{val}", "kind": "gotcha",
                                "oddity": True, "weight": 4,
                                "statement": f"Every {val} row on {t.sheet} ({n:,} rows) has "
                                             f"{_a(ic.header)} {ic.header} starting {p}, and no other "
                                             f"{dc.header} has one.",
                                "depends": [(t.sheet, dc.header), (t.sheet, ic.header)],
                                "numbers": {"table": t.tid, "col": dc.header, "value": val, "prefix": p,
                                            "id_col": ic.header, "rows": n}})

    # ------------------------------------------------------------------
    # groups and single entities unlike their peers, and a measure the table lacks
    # ------------------------------------------------------------------
    def _odd_groups(self, t, out: list):
        """A location, channel or category whose rows behave unlike the others,
        from weighted evidence (each kind's weight is set in _odd_evidence,
        _odd_amounts and _odd_weak): present for only part of the dates while
        the others carry on, values of a shared column (the staff) seen on no
        other group's rows, far more of the money than of the rows, a price 5
        times every other group's, first rows after where the data was read to
        (the last 10% of the dates, or past the rows a calculated tab reads), and
        weaker signs (never a status the others often have, never an adjustment,
        far fewer items). Stated when the weight reaches ODD_ASK with one strong
        kind. A group whose rows another finding already covers (a larger group,
        a prefix only it has, a renamed value) is not stated again."""
        cols = self.cols[t.tid]
        groups = [g for g in cols if _grouping(g)]
        if not groups:
            return
        from .findings import money_column
        from .interview import is_unit_col
        aj = self._axis_j(t)
        day = {i: r[aj].toordinal() for i, r in enumerate(t.rows) if aj is not None and aj < len(r)
               and hasattr(r[aj], "year")}
        cuts = [_ord(i["numbers"]["date"]) for i in out if i.get("recipe", "").startswith(f"boundary:{t.tid}:")]
        renamed = {(col, profile_mod.norm_key(v)) for (tid, col), ho in self.handoffs.items() if tid == t.tid
                   for p in ho["pairs"] for v in (p["old"], p["new"])}
        covered = [(i["numbers"]["col"], profile_mod.norm_key(i["numbers"]["value"])) for i in out
                   if i.get("recipe", "").startswith(f"exclusive:{t.tid}:")]
        money = money_column(self, t)
        if money is not None and money.header in ((self.detection.get("pairs") or {}).get(t.tid) or []):
            money = None          # one side of a debit and credit pair: a share of it says nothing
        ctx = {"day": day, "cuts": cuts, "money": money, "price": self._price_col(t),
               "unit": next((c for c in cols if is_unit_col(c.header, self._role_of(t, c)) and c.type == "text"),
                            None), "read_to": self._read_to(t)}
        found = []
        for g in groups:
            rows_of: dict = {}
            for i, r in enumerate(t.rows):
                k = profile_mod.norm_key(r[g.j] if g.j < len(r) else None)
                if k is not None:
                    rows_of.setdefault(k, []).append(i)
            # a value renamed at a date (a name, then its code) is one group: its old rows join the new value's
            also: dict = {}
            ho = self.handoffs.get((t.tid, g.header)) or {}
            for p in ho.get("pairs") or []:
                old, new = profile_mod.norm_key(p["old"]), profile_mod.norm_key(p["new"])
                if old in rows_of and new in rows_of and old != new:
                    rows_of[new] = sorted(rows_of[new] + rows_of.pop(old))
                    also.setdefault(new, []).append(p["old"])
            ctx["also"] = also
            ctx["groups"] = len(rows_of)
            self._merged = also
            big = {k: rs for k, rs in rows_of.items() if len(rs) >= ODD_ROWS}
            for k, rs in big.items():
                if (g.header, k) in covered or ((g.header, k) in renamed and k not in also):
                    continue
                ev = self._odd_evidence(t, g, k, rs, rows_of, big, ctx)
                weight = sum(e["weight"] for e in ev)
                if weight >= ODD_ASK and any(e["weight"] >= 3 for e in ev):
                    found.append((weight, g, k, set(rs), ev, dict(also=also, groups=len(rows_of))))
        kept = []
        for weight, g, k, rs, ev, own in sorted(found, key=lambda f: (-f[0], f[1].distinct, -len(f[3]))):
            if any(len(rs & other) >= 0.9 * len(rs) for _w, _g, _k, other, _e, _o in kept):
                continue          # the rows of a group another finding already names (an item of an odd category)
            kept.append((weight, g, k, rs, ev, own))
        for weight, g, k, rs, ev, own in kept:
            self._odd_fact(t, g, k, rs, ev, weight, dict(ctx, **own), out)

    def _odd_evidence(self, t, g, k, rs: list, rows_of: dict, big: dict, ctx: dict) -> list:
        """[{kind, weight, text}] for one group, its evidence against its peers."""
        ev = []
        peers = {x: v for x, v in big.items() if x != k}
        mine = set(rs)
        day, noun = ctx["day"], g.header
        # present for only part of the dates while the others carry on, not at a date where the file changes
        ds = sorted(day[i] for i in rs if i in day)
        if len(ds) >= ODD_ROWS and day:
            lo, hi = min(day.values()), max(day.values())
            span = hi - lo
            cut = int(0.02 * len(ds))
            s, e = ds[cut], ds[-1 - cut]
            others = [d for i, d in day.items() if i not in mine]
            outside = sum(1 for d in others if d < s or d > e)
            near = max(14, 0.1 * span)          # starting or stopping this close to a date where the file changes
            if span >= 28 and e - s < 0.6 * span and outside >= max(5, 0.1 * len(others)) \
                    and not any(abs(s - c) <= near or abs(e - c) <= near for c in ctx["cuts"]):
                ev.append({"kind": "presence", "weight": 3,
                           "text": f"rows only from {_day_words(s)} to {_day_words(e)}, while the others run "
                                   f"{_day_words(lo)} to {_day_words(hi)}"})
            if span >= 28 and ds[0] - lo >= int(0.9 * span):          # dates are whole days: the mark is one too
                ev.append({"kind": "late", "weight": 3,
                           "text": f"first rows on {_day_words(ds[0])}, in the last 10% of the dates"})
        if ctx["read_to"] and not any(e["kind"] == "late" for e in ev):
            sheet, last = ctx["read_to"]
            first = min((t.row_index[i] for i in rs if i < len(t.row_index)), default=None)
            if first is not None and first + 1 > last:
                ev.append({"kind": "late", "weight": 3,
                           "text": f"first rows after row {last:,}, where the formulas on {sheet} stop reading"})
        # first rows after the end of the period the table's title names
        end = self.title_dates.get(t.tid)
        if end is not None and ds and ds[0] > end and not any(e["kind"] == "late" for e in ev):
            ev.append({"kind": "late", "weight": 3,
                       "text": f"first rows on {_day_words(ds[0])}, after {_day_words(end)}, where the title ends"})
        # values of a column the others share (the same staff at every site) that no other group has
        for p in self.cols[t.tid]:
            if p is g or p.type not in ("text", "number") or _metric(p) or p.distinct_capped or p.distinct < 3 \
                    or p.type == "date":
                continue
            sets = {x: {profile_mod.norm_key(t.rows[i][p.j] if p.j < len(t.rows[i]) else None) for i in v} - {None}
                    for x, v in rows_of.items()}
            own = sets.get(k) or set()
            elsewhere = set().union(*(s for x, s in sets.items() if x != k)) if len(sets) > 1 else set()
            if not own or own & elsewhere:
                continue
            seen = Counter(v for x, s in sets.items() if x != k for v in s)
            if not seen or sum(1 for n in seen.values() if n >= 2) < 0.5 * len(seen):
                continue          # each group has its own (an item of one category): nothing shared to leave
            n = len(own)
            # the values themselves, so the owner can tell what the group is ('its only Site, Q7')
            shown = sorted((_written_key(t, p, v) for v in own), key=str)
            named = ", ".join(shown[:3]) + (f" and {n - 3:,} more" if n > 3 else "")
            other = f"no other {_plural(noun, 1)}"
            ev.append({"kind": "private", "weight": 3, "values": shown[:6], "col": p.header,
                       "text": f"its only {p.header}, {named}, appears on the rows of {other}" if n == 1 else
                               f"its {n:,} {_plural(p.header, n)} ({named}) appear on the rows of {other}"})
            break
        if len(peers) >= 2:
            ev += self._odd_amounts(t, g, k, rs, peers, ctx)
            ev += self._odd_measure(t, g, k, rs, peers, ctx)
            ev += self._odd_weak(t, g, k, rs, peers, ctx)
            ev += self._odd_missing(t, g, k, rs, peers, ctx, ev)
            ev += self._odd_person(t, g, k, rs, peers)
        return ev

    def _odd_measure(self, t, g, k, rs: list, peers: dict, ctx: dict) -> list:
        """A second amount (overtime hours, a fee) the group holds ODD_MEASURE times its
        share of the rows or more, and a fifth of it at least, where every peer holds
        under twice its own share: weight 3."""
        from .recipes import pct
        skip = {x.j for x in (g, ctx["money"], ctx["price"]) if x is not None}
        pair = set((self.detection.get("pairs") or {}).get(t.tid) or [])
        n = t.n_rows
        for s in self.cols[t.tid]:
            # one side of a debit and credit pair: a share of it says only which side a group sits on
            if s.j in skip or not _metric(s) or s.sensitive or s.negatives or s.header in pair:
                continue
            tot = sum(abs(r[s.j]) for r in t.rows if s.j < len(r) and _is_num(r[s.j]))
            if not tot:
                continue

            def part(rows):
                return sum(abs(t.rows[i][s.j]) for i in rows if s.j < len(t.rows[i]) and _is_num(t.rows[i][s.j]))
            share = part(rs) / tot
            if share < 0.2 or share / (len(rs) / n) < ODD_MEASURE:
                continue
            if any((part(v) / tot) / (len(v) / n) >= 2 for v in peers.values()):
                continue
            return [{"kind": "measure", "weight": 3, "col": s.header,
                     "text": f"{pct(share)} of {s.header} on {pct(len(rs) / n)} of the rows"}]
        return []

    def _odd_missing(self, t, g, k, rs: list, peers: dict, ctx: dict, ev: list) -> list:
        """Values of another column every peer has on its rows and this group never
        does (a site with no manager and no scheduler, where each other site has
        both), counted over every such column, weight 1 each, ODD_MISSING at most.
        A value counts only when the peers' share predicts ODD_MISSING_EXPECT rows or more."""
        out = []
        said = {(e.get("col"), e.get("value")) for e in ev}
        # a column whose values the group has to itself (its own staff, its own vendor) lacks the others' by nature
        own = {e.get("col") for e in ev if e.get("kind") == "private"}
        skip = {x.j for x in (g, ctx["money"], ctx["price"]) if x is not None} | {c.j for c in self.cols[t.tid]
                                                                                  if c.header in own}
        theirs = [i for v in peers.values() for i in v]
        for c in self.cols[t.tid]:
            if c.j in skip or c.sensitive or c.distinct_capped or c.type != "text" or not (2 <= c.distinct <= 30) \
                    or c.semantic not in ("dimension", "identifier"):
                continue
            have = {profile_mod.norm_key(t.rows[i][c.j] if c.j < len(t.rows[i]) else None) for i in rs}
            got = Counter(profile_mod.norm_key(t.rows[i][c.j] if c.j < len(t.rows[i]) else None) for i in theirs)
            for v, m in got.most_common():
                if v is None or v in have or m / len(theirs) * len(rs) < ODD_MISSING_EXPECT:
                    continue
                if not all(any(profile_mod.norm_key(t.rows[i][c.j] if c.j < len(t.rows[i]) else None) == v for i in p)
                           for p in peers.values()):
                    continue
                shown = _written_key(t, c, v)
                if (c.header, shown) in said:
                    continue
                out.append({"kind": "missing", "weight": 1, "col": c.header, "value": shown,
                            "text": f"no {shown} in {c.header}, which every other {_plural(g.header, 1)} has"})
                if len(out) >= ODD_MISSING:
                    return out
        return out

    def _odd_person(self, t, g, k, rs: list, peers: dict) -> list:
        """A person a lookup tab names for the group (its manager) who is on no other
        tab, where the people it names for the peers are: weight 2."""
        names = self._people_of(t, g)
        if not names or names.get(k) is None:
            return []
        seen = self._roster_names(skip=names.get("_table"))
        if not seen:
            return []
        known = [x for x in peers if names.get(x) is not None]
        on = [x for x in known if profile_mod.norm_key(names[x]) in seen]
        if len(known) < 2 or len(on) < 2 / 3 * len(known) or profile_mod.norm_key(names[k]) in seen:
            return []
        return [{"kind": "person", "weight": 2, "col": names["_col"], "value": names[k],
                 "text": f"{names[k]}, its {names['_col']} on {names['_sheet']}, is on no other tab, where the "
                         f"others' are"}]

    def _people_of(self, t, g) -> dict:
        """{group key: the person a lookup tab names for it} from a small tab keyed by
        the group's values (codes or names) with a column of people (a manager, an
        owner, a contact), plus _col, _sheet and _table. {} when there is none."""
        cache = self.__dict__.setdefault("_people_cache", {})
        if (t.tid, g.header) in cache:
            return cache[(t.tid, g.header)]
        out: dict = {}
        vals = set(g.counter)
        for lt in self.tables:
            if lt is t or lt.n_rows > 200 or lt.wide:
                continue
            keys = [c for c in self.cols[lt.tid] if c.unique and c.type == "text" and set(c.counter) & vals]
            # a column of people that is not one of the keys: a header that names a role first
            people = sorted([c for c in self.cols[lt.tid] if c.type == "text" and c not in keys
                             and (c.is_person or _PERSON_HEAD.search(str(c.header)))],
                            key=lambda c: not _PERSON_HEAD.search(str(c.header)))
            if not keys or not people:
                continue
            p = people[0]
            for r in lt.rows:
                who = r[p.j] if p.j < len(r) else None
                if not isinstance(who, str) or not who.strip():
                    continue
                for kc in keys:
                    kk = profile_mod.norm_key(r[kc.j] if kc.j < len(r) else None)
                    if kk is not None:
                        out[kk] = who.strip()
            if out:
                out.update(_col=p.header, _sheet=lt.sheet, _table=lt.tid)
                break
        # a group merged from a name and its code: the code's person stands for both
        for new, olds in (getattr(self, "_merged", None) or {}).items():
            for o in olds:
                if new not in out and profile_mod.norm_key(o) in out:
                    out[new] = out[profile_mod.norm_key(o)]
        cache[(t.tid, g.header)] = out
        return out

    def _roster_names(self, skip=None) -> set:
        """Every person named on the other tabs, as normalized full names: a text
        column's values, and first and last name columns read together both ways."""
        cache = self.__dict__.setdefault("_roster_cache", {})
        if skip in cache:
            return cache[skip]
        out = set()
        for t in self.tables:
            if t.tid == skip:
                continue
            cols = [c for c in self.cols[t.tid] if c.type == "text"]
            first = next((c for c in cols if re.search(r"\bfirst\b", str(c.header), re.I)), None)
            last = next((c for c in cols if re.search(r"\b(last|sur)\s*name\b|\blast\b", str(c.header), re.I)), None)
            for c in cols:
                if c.avg_len <= 40:
                    out |= {k for k in c.counter if isinstance(k, str) and " " in k}
            if first is not None and last is not None:
                for r in t.rows:
                    a, b = (r[first.j] if first.j < len(r) else None), (r[last.j] if last.j < len(r) else None)
                    if isinstance(a, str) and isinstance(b, str):
                        out.add(profile_mod.norm_key(f"{a} {b}"))
                        out.add(profile_mod.norm_key(f"{b}, {a}"))
        cache[skip] = out
        return out

    def _odd_amounts(self, t, g, k, rs: list, peers: dict, ctx: dict) -> list:
        """Money share and unit price against the peers, only when the peers agree
        with each other (every peer within 2.5 times of the others): an item
        column, where every value has its own price, has nothing to compare."""
        from .recipes import fmt_num, pct
        ev = []
        m, price, unit = ctx["money"], ctx["price"], ctx["unit"]
        if m is not None:
            def total(rows):
                return sum(abs(t.rows[i][m.j]) for i in rows if m.j < len(t.rows[i]) and _is_num(t.rows[i][m.j]))
            tot = total(range(t.n_rows))
            if tot:
                ratio = {x: (total(v) / tot) / (len(v) / t.n_rows) for x, v in peers.items()}
                share = total(rs) / tot
                mine = share / (len(rs) / t.n_rows)
                vals = sorted(ratio.values())
                med = vals[len(vals) // 2]
                w = 0
                if vals[0] > 0 and vals[-1] <= ODD_AGREE * vals[0] and med > 0:
                    x = mine / med
                    w = 4 if x >= 5 and share >= 0.20 else 2 if x >= 3 and share >= 0.10 else 0
                elif vals[-1] > 0 and mine >= ODD_PRICE * vals[-1] and share >= 0.20:
                    w = 4             # peers that differ among themselves, and this group far beyond every one of them
                if w:
                    ev.append({"kind": "share", "weight": w,
                               "text": f"{pct(share)} of {m.header} on {pct(len(rs) / t.n_rows)} of the rows"})
        if price is not None:
            u = None
            if unit is not None:          # compare a price with prices of the same unit only
                got = Counter(profile_mod.norm_key(t.rows[i][unit.j] if unit.j < len(t.rows[i]) else None) for i in rs)
                u, n = got.most_common(1)[0]
                u = u if n >= 0.8 * len(rs) else False

            def prices(rows, unit_key):
                return [t.rows[i][price.j] for i in rows if price.j < len(t.rows[i]) and _is_num(t.rows[i][price.j])
                        and t.rows[i][price.j] > 0 and (unit_key is None or profile_mod.norm_key(
                            t.rows[i][unit.j] if unit.j < len(t.rows[i]) else None) == unit_key)]
            own = prices(rs, u) if u is not False else []
            theirs = {x: prices(v, u) for x, v in peers.items()} if u is not False else {}
            theirs = {x: v for x, v in theirs.items() if len(v) >= 5}
            alone = u not in (None, False) and len(theirs) < 2
            if alone:
                # a unit no peer counts in (bottles where the others count pounds): its price against every peer's
                theirs = {x: v for x, v in ((x, prices(v, None)) for x, v in peers.items()) if len(v) >= 5}
            if len(own) >= 5 and len(theirs) >= 2:
                meds = sorted(_median(v) for v in theirs.values())
                kinds = sorted(len({round(p, 4) for p in v}) for v in theirs.values())
                mo = _median(own)
                # a price the group itself decides (one price per item) is no evidence; peers that differ among
                # themselves say something only about a group far beyond every one of them, or in a unit of its own
                agree = meds[0] > 0 and meds[-1] <= ODD_AGREE * meds[0]
                if kinds[len(kinds) // 2] >= 2 and meds[0] > 0 and mo >= ODD_PRICE * meds[-1] \
                        and (agree or alone or mo >= 2 * ODD_PRICE * meds[-1]):
                    ev.append({"kind": "price", "weight": 3,
                               "text": f"median {price.header} {fmt_num(round(mo, 2))}, where the others' are "
                                       f"{fmt_num(round(meds[0], 2))} to {fmt_num(round(meds[-1], 2))}"})
        return ev

    def _odd_weak(self, t, g, k, rs: list, peers: dict, ctx: dict) -> list:
        """Weaker signs, 1 each: never a status value the peers have on 5% of
        their rows, never a nonzero adjustment the peers have on 10%, and far
        fewer items than the peers. A 'never' counts only when the peers' share
        would put 5 or more such rows in the group: fewer can be missing by chance."""
        from .recipes import pct
        ev = []
        mine = set(rs)
        theirs = [i for v in peers.values() for i in v]
        skip = {x.j for x in (g, ctx["money"], ctx["price"]) if x is not None}
        for c in self.cols[t.tid]:
            if c.j in skip or c.sensitive or c.distinct_capped:
                continue
            if c.type == "text" and 2 <= c.distinct <= 8 and (c.codes or _status_header(c.header)) \
                    and not any(e["kind"] == "lifecycle" for e in ev):
                got = Counter(profile_mod.norm_key(t.rows[i][c.j] if c.j < len(t.rows[i]) else None) for i in theirs)
                have = Counter(profile_mod.norm_key(t.rows[i][c.j] if c.j < len(t.rows[i]) else None) for i in rs)
                for v, n in got.items():
                    held = sum(1 for p in peers.values() if any(profile_mod.norm_key(
                        t.rows[i][c.j] if c.j < len(t.rows[i]) else None) == v for i in p))
                    if v is not None and n >= 0.05 * len(theirs) and not have.get(v) and held >= 2 / 3 * len(peers) \
                            and n / len(theirs) * len(rs) >= ODD_EXPECT:
                        ev.append({"kind": "lifecycle", "weight": 1, "col": c.header, "value": _written_key(t, c, v),
                                   "text": f"never {_written_key(t, c, v)} in {c.header}, which the others are on "
                                           f"{pct(n / len(theirs))} of their rows"})
                        break
            elif _metric(c) and not any(e["kind"] == "adjust" for e in ev):
                def on(rows):
                    return sum(1 for i in rows if c.j < len(t.rows[i]) and _is_num(t.rows[i][c.j]) and t.rows[i][c.j])
                n = on(theirs)
                if n >= 0.10 * len(theirs) and not on(mine) and c.nulls + c.zeros >= 0.3 * t.n_rows \
                        and n / len(theirs) * len(rs) >= ODD_EXPECT:
                    ev.append({"kind": "adjust", "weight": 1,
                               "text": f"no {c.header} on any row, where the others have one on "
                                       f"{pct(n / len(theirs))} of theirs"})
            elif c.type == "text" and c.distinct >= 10 and c.semantic == "dimension" \
                    and not any(e["kind"] == "items" for e in ev):
                count = {x: len({profile_mod.norm_key(t.rows[i][c.j] if c.j < len(t.rows[i]) else None) for i in v})
                         for x, v in peers.items()}
                own = len({profile_mod.norm_key(t.rows[i][c.j] if c.j < len(t.rows[i]) else None) for i in rs})
                med = sorted(count.values())[len(count) // 2]
                if own <= 0.34 * med:
                    ev.append({"kind": "items", "weight": 1,
                               "text": f"{own:,} {c.header} value{'s' if own != 1 else ''} where the others have "
                                       f"{med:,}"})
        return ev

    def _odd_fact(self, t, g, k, rs: set, ev: list, weight: int, ctx: dict, out: list):
        val = _written_key(t, g, k)
        n = len(rs)
        # the other groups, a renamed value's old and new names counted once
        others = ctx["groups"] - 1 if ctx.get("groups") else sum(1 for x in g.counter if x != k)
        ev = sorted(ev, key=lambda e: -e["weight"])
        priced = ctx["price"] is not None and any(e["kind"] in ("share", "price") for e in ev)
        unit = ""
        if priced and ctx["unit"] is not None:
            got = Counter(_written(t.rows[i][ctx["unit"].j]) for i in rs if ctx["unit"].j < len(t.rows[i]))
            u, hits = got.most_common(1)[0]
            unit = u if u and hits >= 0.8 * n else ""
        also = [str(x) for x in (ctx.get("also") or {}).get(k) or []]
        named = f"{val} (also {_join_words(also)})" if also else val
        # a word column that is one value on every row of the group, and not on every row of the table: 'all 25
        # rows are Produce', said where the group is folded into another question
        kind = ""
        for c in self.cols[t.tid]:
            if c is g or c.type != "text" or c.sensitive or c.distinct_capped or not (2 <= c.distinct <= 30) \
                    or c.semantic != "dimension":
                continue
            vals = {profile_mod.norm_key(t.rows[i][c.j] if c.j < len(t.rows[i]) else None) for i in rs}
            if len(vals) == 1 and None not in vals:
                v = _written(t.rows[next(iter(rs))][c.j])
                kind = f"all {n:,} rows are {v} in {c.header}"
                break
        out.append({"recipe": f"oddgroup:{t.tid}:{g.header}:{val}", "kind": "gotcha", "oddity": True, "weight": 3,
                    "statement": f"{named} in {g.header} on {t.sheet} ({n:,} rows) is unlike the other {others:,} "
                                 f"{g.header} values: " + "; ".join(e["text"] for e in ev) + ".",
                    "depends": [(t.sheet, g.header)],
                    "numbers": {"table": t.tid, "col": g.header, "value": val, "rows": n, "weight": weight,
                                "others": others, "evidence": ev, "kinds": [e["kind"] for e in ev],
                                "price": ctx["price"].header if priced else "", "unit": unit, "also": also,
                                "category": kind,
                                "lookup": (self._people_of(t, g) or {}).get("_sheet", "")}})

    def _price_col(self, t):
        """A table's unit price: a bound price role, else the factor with cents in a
        count times price equals amount triple, else a money column headed price,
        cost, each or rate. None when there is none."""
        from .rules import _RATE
        pbr = (self.playbook or {}).get("roles", {})
        for rid, r in self.detection["roles"].items():
            role = pbr.get(rid) or {}
            if r.get("table") == t.tid and r.get("col") is not None and role.get("unit") == "currency" \
                    and role.get("additive") is False:
                return r["col"]
        nums = [c for c in self.cols[t.tid] if _metric(c) and not c.sensitive and not _RATE.search(str(c.header))]
        rows = t.rows[:2000]
        for x in nums:
            for n, q in enumerate(nums):
                for p in nums[n + 1:]:
                    if x.j in (q.j, p.j):
                        continue
                    trio = [(r[q.j], r[p.j], r[x.j]) for r in rows if max(q.j, p.j, x.j) < len(r)
                            and _is_num(r[q.j]) and _is_num(r[p.j]) and _is_num(r[x.j])]
                    if len(trio) >= 5 and sum(1 for a, b, c in trio if abs(a * b - c) <= 0.0051) >= 0.9 * len(trio):
                        cents = [c for c in (q, p) if not c.integers]
                        if len(cents) == 1:
                            return cents[0]
        return next((c for c in nums if _PRICE_HEADER.search(str(c.header)) and not c.integers), None)

    def _role_of(self, t, c):
        return next((rid for rid, r in self.detection["roles"].items() if r.get("table") == t.tid
                     and r.get("header") == c.header), None)

    def _read_to(self, t):
        """(calculated tab, last row it reads) when a calculated tab's formulas read
        this tab only down to a row short of its end."""
        fa = self.formulas.get(self.file_of[t.tid]) or {}
        for sr in fa.get("short_ranges") or []:
            if sr.get("reads") == t.sheet:
                return sr["sheet"], int(sr["range_end"])
        return None

    def _entity_outliers(self, t, out: list):
        """Single entities unlike their peers, one finding per table listing them:
        an entity (an ID column of 20 or more values that repeats) with a money
        column at 0 on 6 or more of its rows where 95% of its peers' rows have it
        (never for a side of a debit and credit pair), one item per entity
        whatever the number of such columns; a test or placeholder record, whose
        number must stand out (far past its own sequence, all 9s or 0s, or TEST,
        DUMMY or XXX in it) together with one more sign: one amount on every row
        where under 5% of peers have that, or rows on one side of a date where
        the file changes (either alone fits a salaried hire after a new system);
        and a row whose ratio of two money columns of one family (a list and a
        billed amount) sits far from every other row's."""
        from .findings import _join
        from .recipes import fmt_num, pct
        cols = self.cols[t.tid]
        pair = set((self.detection.get("pairs") or {}).get(t.tid) or [])
        money = [c for c in cols if _metric(c) and not c.sensitive and not _AMOUNT_NOT.search(str(c.header))]
        items = []
        ids = [c for c in cols if c.semantic == "identifier" and c.type in ("text", "number") and not c.codes
               and not c.distinct_capped and c.distinct >= 20 and c.count and c.distinct / c.count < 0.5]
        from .findings import money_column
        main = money_column(self, t)
        cuts = [_ord(i["numbers"]["date"]) for i in out if i.get("recipe", "").startswith(f"boundary:{t.tid}:")]
        aj = self._axis_j(t)
        for idc in ids[:1]:
            rows_of: dict = {}
            for i, r in enumerate(t.rows):
                k = profile_mod.norm_key(r[idc.j] if idc.j < len(r) else None)
                if k is not None:
                    rows_of.setdefault(k, []).append(i)
            total = sum(len(v) for v in rows_of.values())
            zeros: dict = {}          # entity key -> [(money column, its rows at 0, the peers' share with it)]
            for mc in money:
                if mc.header in pair:
                    continue
                zero = {k: sum(1 for i in rs if not _is_num(t.rows[i][mc.j] if mc.j < len(t.rows[i]) else None)
                               or t.rows[i][mc.j] == 0) for k, rs in rows_of.items()}
                every = sum(zero.values())
                for k, rs in rows_of.items():
                    z = zero[k]
                    rest = total - len(rs)
                    live = rest - (every - z)
                    if z >= 6 and z >= 0.5 * len(rs) and rest and live >= 0.95 * rest:
                        zeros.setdefault(k, []).append((mc.header, z, live / rest))
            for k, hits in zeros.items():
                n = len(rows_of[k])
                h0, z0, _s = hits[0]
                if all(z == z0 for _h, z, _s in hits):
                    head = f"{_join([h for h, _z, _s in hits])} {'is' if len(hits) == 1 else 'are'} 0 on {z0:,} of " \
                           f"its {n:,} rows"
                else:
                    head = f"{h0} is 0 on {z0:,} of its {n:,} rows, " \
                        + _join([f"{h} on {z:,}" for h, z, _s in hits[1:]])
                lo, hi = pct(min(s for _h, _z, s in hits)), pct(max(s for _h, _z, s in hits))
                tail = f", where the others have {'it' if len(hits) == 1 else 'them'} on " \
                       f"{lo if lo == hi else f'{lo} to {hi}'} of theirs"
                items.append({"kind": "zero", "col": idc.header, "id": _written_key(t, idc, k), "rows": rows_of[k],
                              "strength": 3, "text": head + tail})
            written = {k: _written(t.rows[rs[0]][idc.j]) for k, rs in rows_of.items()}
            fam = _numbering(written)
            const = {k: main is not None and len(rs) >= 3 and len({t.rows[i][main.j] for i in rs
                                                                    if main.j < len(t.rows[i])}) == 1
                     and _is_num(t.rows[rs[0]][main.j] if main.j < len(t.rows[rs[0]]) else None)
                     for k, rs in rows_of.items()}
            steady = sum(const.values())
            for k, rs in rows_of.items():
                if not fam.get(k) or k in zeros:
                    continue          # the number must stand out: one amount or one side of a date alone is a salary
                why = [fam[k]]
                # rows that all start or stop where the file changes say most about where a record came from:
                # that clause comes before one amount on every row (still two reasons at most)
                ds = [t.rows[i][aj].toordinal() for i in rs if aj is not None and aj < len(t.rows[i])
                      and hasattr(t.rows[i][aj], "year")]
                for c in cuts:
                    if ds and (max(ds) < c or min(ds) >= c):
                        why.append(f"its rows are all {'before' if max(ds) < c else 'from'} {_day_words(c)}, "
                                   "where the file changes")
                        break
                if len(why) < 2 and const[k] and steady - 1 < 0.05 * (len(rows_of) - 1):
                    why.append(f"{main.header} is {fmt_num(t.rows[rs[0]][main.j])} on all {len(rs):,} of its rows")
                if len(why) >= 2:
                    items.append({"kind": "sentinel", "col": idc.header, "id": written[k], "rows": rs,
                                  "strength": 2, "text": "; ".join(why[:2])})
        items += self._ratio_rows(t, money, ids, aj)
        if not items:
            return
        items.sort(key=lambda x: -x["strength"])
        shown = items[:3]
        k = len(items)
        what = "; ".join(f"{x['id']}: {x['text']}" for x in shown)
        out.append({"recipe": f"outliers:{t.tid}", "kind": "gotcha", "oddity": True, "weight": 3,
                    "statement": f"{k:,} record{'s' if k != 1 else ''} on {t.sheet} behave{'s' if k == 1 else ''} unlike "
                                 f"{'their' if k != 1 else 'its'} peers: {what}.",
                    "depends": list(dict.fromkeys((t.sheet, x["col"]) for x in shown)),
                    "numbers": {"table": t.tid, "items": [dict({kk: v for kk, v in x.items() if kk != "rows"},
                                                               n=len(x["rows"])) for x in items[:20]],
                                "rows": sorted({i for x in items for i in x["rows"]})[:5000]}})

    def _ratio_rows(self, t, money: list, ids: list, aj) -> list:
        """Rows whose ratio of two money columns of one family ('List Price' and
        'Billed Price') has a robust z beyond 5, when the other rows hold tight
        (the 5th to 95th percentile ratios within ODD_AGREE of each other, and at
        most 1% of rows and 3 rows that far out). A pair where one is a count
        times the other (a line total over its price) is never read: its ratio is
        the count, and a large order is no slip."""
        from .recipes import pct
        doc = next((c for c in self.cols[t.tid] if c.semantic == "identifier" and c.unique), None)
        who = ids[0] if ids else doc
        named = self._row_label_cols(t) if who is None else []
        out = []
        for n, a in enumerate(money):
            for b in money[n + 1:]:
                fa, fb = _plain_words(a.header), _plain_words(b.header)
                if not fa or not fb or fa[-1] != fb[-1] or fa == fb:
                    continue
                pts = [(i, r[b.j] / r[a.j]) for i, r in enumerate(t.rows) if max(a.j, b.j) < len(r)
                       and _is_num(r[a.j]) and _is_num(r[b.j]) and r[a.j] > 0 and r[b.j] >= 0]
                if len(pts) < 30 or self._product_of(t, a, b):
                    continue
                vals = sorted(x for _i, x in pts)
                lo, hi = vals[int(0.05 * len(vals))], vals[int(0.95 * len(vals)) - 1]
                if lo <= 0 or hi > ODD_AGREE * lo:
                    continue          # the rows spread out: no one ratio is the norm to be far from
                med = _median(vals)
                mad = _median(sorted(abs(x - med) for x in vals)) * 1.4826
                if mad <= 0:
                    continue
                far = [(i, x) for i, x in pts if abs(x - med) / mad > ODD_Z]
                if not far or len(far) > max(1, min(3, 0.01 * len(pts))):
                    continue
                for i, x in far:
                    r = t.rows[i]
                    row = t.row_index[i] + 1 if i < len(t.row_index) else i + 2
                    # a row with no ID of its own is named by its key or label columns as well as its number
                    bits = [_written(r[c.j]) for c in named if c.j < len(r) and not _blank(r[c.j])]
                    ident = _written(r[who.j]) if who is not None and who.j < len(r) else \
                        f"row {row:,}" + (f" ({', '.join(bits)})" if bits else "")
                    out.append({"kind": "ratio", "col": who.header if who is not None else b.header, "id": ident,
                                "rows": [i], "strength": 1, "row": row, "a": a.header, "b": b.header,
                                "value": r[b.j], "base": r[a.j],
                                "text": f"on row {row:,} {b.header} is {pct(x)} of {a.header}, where the other rows "
                                        f"sit at {pct(lo)} to {pct(hi)}"})
        return out

    def _row_label_cols(self, t) -> list:
        """The columns that name a row when it has no ID of its own: the table's key
        (up to 3 columns), else its first ID-like column and its first column of
        names (text holding a different value on most rows). Never a person's name
        or another private column."""
        key = [self.col(t.tid, h) for h in (self.keys.get(t.tid) or [])[:3]]
        key = [c for c in key if c is not None and not c.sensitive]
        if key:
            return key
        cols = [c for c in self.cols[t.tid] if not c.sensitive and c.type in ("text", "number") and c.count]
        idc = next((c for c in cols if c.semantic == "identifier" and not c.codes), None)
        name = next((c for c in cols if c is not idc and c.type == "text" and c.semantic == "dimension"
                     and c.distinct >= 0.5 * c.count), None)
        return [c for c in (idc, name) if c is not None]

    def _product_of(self, t, a, b) -> bool:
        """b is another number column times a (or a times it), within a cent, on
        90% of the rows that have all three."""
        for q in self.cols[t.tid]:
            if q.j in (a.j, b.j) or not _metric(q):
                continue
            trio = [(r[q.j], r[a.j], r[b.j]) for r in t.rows[:2000] if max(q.j, a.j, b.j) < len(r)
                    and _is_num(r[q.j]) and _is_num(r[a.j]) and _is_num(r[b.j])]
            if len(trio) >= 5 and sum(1 for x, y, z in trio if abs(x * y - z) <= 0.0051 or abs(x * z - y) <= 0.0051) \
                    >= 0.9 * len(trio):
                return True
        return False

    def _derived_measure(self, t, out: list):
        """A table with a quantity, a price and an adjustment (a discount) but no
        amount column: the measure its totals need is Qty x Price minus the
        adjustment, and only the owner can confirm it and which lines count. An
        adjustment is an amount taken off: a rate ('Discount %', or values that
        all sit between 0 and 1) is never subtracted, and with only a rate
        nothing is asked."""
        cols = self.cols[t.tid]
        from .findings import _COUNT_WORDS
        from .rules import _RATE
        nums = [c for c in cols if _metric(c) and not c.sensitive]

        def amount(c) -> bool:          # dollars that add up, not a rate or a count
            if _RATE.search(str(c.header)) or _AMOUNT_NOT.search(str(c.header)):
                return False
            nz = [r[c.j] for r in t.rows if c.j < len(r) and _is_num(r[c.j]) and r[c.j]]
            return bool(nz) and not all(0 < abs(v) <= 1 for v in nz)
        qty = next((c for c in nums if c.integers and _COUNT_WORDS.search(str(c.header))), None)
        price = self._price_col(t) or next((c for c in nums if _PRICE_HEADER.search(str(c.header))), None)
        adj = next((c for c in nums if _ADJUST_HEADER.search(str(c.header)) and c not in (qty, price) and amount(c)),
                   None)
        if qty is None or price is None or adj is None or price is qty:
            return
        if any(c not in (qty, price, adj) and amount(c) for c in nums):
            return                # an amount column is there already
        n = sum(1 for r in t.rows if max(qty.j, price.j) < len(r) and _is_num(r[qty.j]) and _is_num(r[price.j]))
        if n < 20:
            return
        out.append({"recipe": f"derive:{t.tid}", "kind": "scope", "oddity": True, "weight": 3,
                    "statement": f"{t.sheet} has {qty.header}, {price.header} and {adj.header} on {n:,} rows but no "
                                 "amount column, so every total of the money has to be worked out.",
                    "depends": [(t.sheet, qty.header), (t.sheet, price.header), (t.sheet, adj.header)],
                    "numbers": {"table": t.tid, "qty": qty.header, "price": price.header, "adj": adj.header,
                                "rows": n, "name": "Net" if "Net" not in t.headers else "Net Amount"}})

    def _derived_totals(self, answers: dict) -> list:
        """The measure the owner confirmed for a table with no amount column,
        totaled by month over the rows every confirmed rule leaves in: Qty x Price,
        minus the adjustment unless the owner said to leave it off."""
        from .recipes import fmt_money
        out = []
        for a in (answers or {}).values():
            d = a.get("derive") if isinstance(a, dict) else None
            picked = set(a.get("options") or []) if isinstance(a, dict) else set()
            if not d or not picked & {"yes", "gross"}:
                continue
            t = next((x for x in self.tables if x.tid == d["table"]), None)
            if t is None or not all(h in t.headers for h in (d["qty"], d["price"])):
                continue
            minus = d["adj"] if "yes" in picked and d.get("adj") in t.headers else ""
            mixed = self.mixed_units.get((t.tid, minus)) if minus else None
            if mixed:             # an amount taken off in two units is never subtracted as one
                how = f"{d['qty']} x {d['price']} minus {minus}"
                out.append({"recipe": f"by_month:{t.tid}:{d['name']}", "kind": "gotcha", "weight": 1,
                            "statement": f"{d['name']} on {t.sheet} ({how}, per the owner) is not totaled here: "
                                         f"{minus} is in mixed units before and after {mixed}, per the owner.",
                            "depends": [(t.sheet, h) for h in (d["qty"], d["price"], minus)],
                            "numbers": {"table": t.tid, "col": d["name"], "mixed_units": mixed},
                            "files": [self.file_of[t.tid]]})
                continue
            jq, jp = t.headers.index(d["qty"]), t.headers.index(d["price"])
            jm = t.headers.index(minus) if minus else None
            aj = self._axis_j(t)
            months: dict = {}
            total = 0.0
            for r in self._ctx.rows(t, d["name"]):
                q, p = (r[jq] if jq < len(r) else None), (r[jp] if jp < len(r) else None)
                if not (_is_num(q) and _is_num(p)):
                    continue
                off = r[jm] if jm is not None and jm < len(r) and _is_num(r[jm]) else 0.0
                v = q * p - abs(off)
                total += v
                when = r[aj] if aj is not None and aj < len(r) else None
                if hasattr(when, "year"):
                    months[(when.year, when.month)] = months.get((when.year, when.month), 0.0) + v
            how = f"{d['qty']} x {d['price']}" + (f" minus {minus}" if minus else "")
            shown = "; ".join(f"{_MONTHS[m - 1]} {y} {fmt_money(v)}" for (y, m), v in sorted(months.items())[:12])
            more = len(months) - 12
            out.append({"recipe": f"by_month:{t.tid}:{d['name']}", "kind": "trend", "weight": 2,
                        "statement": f"{d['name']} on {t.sheet} ({how}, per the owner) totals {fmt_money(total)}"
                                     + (f"; by month: {shown}" + (f", and {more:,} more" if more > 0 else "")
                                        if months else "") + self._ctx.note(t, d["name"]) + ".",
                        "depends": [(t.sheet, h) for h in (d["qty"], d["price"], minus) if h],
                        "numbers": {"table": t.tid, "col": d["name"], "total": round(total, 2),
                                    "months": [[f"{y}-{m:02d}", round(v, 2)] for (y, m), v in sorted(months.items())]},
                        "files": [self.file_of[t.tid]]})
        return out

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


def _month(v) -> str:
    m = re.fullmatch(r"(\d{4})-(\d{2})-\d{2}.*", str(v))
    return f"{_MONTHS[int(m.group(2)) - 1]} {m.group(1)}" if m else str(v)


# --------------------------------------------------------------------------
# boundaries and copies: small helpers
# --------------------------------------------------------------------------
BOUNDARY_ROWS = 100       # a table this long, with dates, is read for one date where it changes
BLOCK_RATIO = 1.8         # a block loaded twice holds this many times the usual block's rows, or more
# odd groups and single entities: ratios set on first principles, frozen before any new book is seen
ODD_ROWS = 20             # a group this large or larger is compared with its peers
ODD_ASK = 4               # the evidence weight a group needs, with at least one kind of weight 3 or more
ODD_AGREE = 2.5           # peers agree when the largest of their figures is at most this times the smallest
ODD_PRICE = 5             # a group's median price this many times every peer's median, or more
ODD_Z = 5                 # a ratio this many robust deviations from the other rows'
ODD_EXPECT = 5            # a value a group never has is evidence only when its peers' share predicts this many
ODD_MEASURE = 3           # a group's share of a second measure this many times its share of the rows, or more
ODD_MISSING = 2           # values every peer has and a group lacks, counted as evidence at most this many times
ODD_MISSING_EXPECT = 8    # ... each only when the peers' share predicts this many rows of it in the group
_PERSON_HEAD = re.compile(r"\b(manager|mgr|owner|contact|supervisor|lead|director|head|rep|representative)\b", re.I)
UNIT_EXACT = 0.95         # quantity x rate = money to the cent on this share of the rows no other count adds to
UNIT_QTY = 1 / 3          # a group's quantity median at most this share of every peer's
UNIT_RATE = 1.5           # ... and its rate median at least this many times every peer's
UNPAID_IDS = 10           # IDs on a table of charges and payments before one that never pays is read against them
_PRICE_HEADER = re.compile(r"\b(price|cost|each|rate)\b", re.I)
_ADJUST_HEADER = re.compile(r"\b(disc|discounts?|adj|adjustments?|markdowns?|allowances?|rebates?|promos?|coupons?)\b",
                            re.I)
# a number column that is a count or a rate, never an amount of money
_AMOUNT_NOT = re.compile(r"%|\b(pct|percent|rate|per|qty|quantity|units?|count|counts|hours?|hrs|pieces|pcs|"
                         r"headcount|volume|days|weeks|months|years|age)\b", re.I)
_SENTINEL_WORD = re.compile(r"test|dummy|xxx", re.I)
# what a row is and how the dates fall: ratios set on first principles, frozen before any new book is seen
SNAP_ROWS = 30            # a table this long is read for a snapshot panel
SNAP_PER_DATE = 5         # a panel date holds at least this many rows (the median date)
SNAP_UNIQUE = 0.99        # the date and its key name this share of the rows, or more
SNAP_REPEAT = 0.8         # the key sets of consecutive dates overlap this much on average (Jaccard)
BALANCED = 0.99           # entries of 2 lines or more that must net to zero for the ID to be an entry number
CADENCE_DATES = 6         # dates on a cycle before the cycle is stated
CADENCE_SHARE = 0.8       # the share of dates on the cycle's weekday (or day of the month), and of its gaps
# a number that is a level at a date (a count on hand, a balance), and one that says it moved (never a level);
# a bare 'count' is as often a number of things that happened, so only 'counted' says a level
_STOCK = re.compile(r"\b(on ?hand|in stock|stock|balances?|bal|inventory|levels?|outstanding|remaining|closing|"
                    r"ending|opening|beginning|headcount|counted|qoh|soh)\b", re.I)
_FLOW = re.compile(r"\b(sold|sales|received|receipts?|purchased|purchases|used|usage|consumed|shipped|issued|waste|"
                   r"wasted|orders?|ordered|revenue|spend|spent|paid|payments?|change|variance|movement|flows?|burn|"
                   r"in|out)\b",
                   re.I)
# reference and partner tables: ratios set on first principles, frozen before any new book is seen
WINDOW_LINES = 5          # lines of one key outside its dates before they are counted as a finding
WINDOW_SHARE = 0.05       # ... and at least this share of that key's lines
TERMS_SHOWN = 30          # keys of a terms table stated one by one
TERMS_NEAR_END = 90       # days between an agreement's end and the last line that make the end worth saying
TERMS_TEXT = 200          # characters of a terms text quoted before it is cut at a word
CONFORM_LINES = 10        # lines in a run at the list price before it is stated
CONFORM_MONTHS = 3        # months in that run
CONFORM_AT = 0.9          # the share of the run's lines at the list price exactly
CONFORM_OFF = 0.10        # lines off the list inside a run, at most this share, to be asked about
CONFORM_ALL = 0.8         # runs holding this share of the lines with a reference price: the list is what is charged
ONSET_LINES = 50          # a table this long is read for codes that start for one group
ONSET_MONTHS = 3          # months a group is active without the code before it starts, and months after
ONSET_STEADY = 0.8        # the share of the group's months since the start that carry the code
ONSET_ROWS = 5            # rows of the code for that group
ONSET_CHANCE = 0.01       # the chance, at one steady share, that the code was missing from the rows before by luck
_START_HEAD = re.compile(r"\b(start|starts|begin|begins|beginning|from|effective|eff|valid from|since|opened|"
                         r"commence\w*)\b", re.I)
_END_HEAD = re.compile(r"\b(end|ends|ending|expir\w*|exp|thru|through|to|until|valid to|closes|closed|stop)\b", re.I)
_CODE_WORDS = re.compile(r"\b(type|kind|class|code|category|status|reason|charge|fee|line)\b", re.I)
# the unit a terms table's number carries, only as its header says it: a percent, a share, money
_PERCENT_HEAD = re.compile(r"%|\b(pct|percent|percentage|rate)\b", re.I)
_SHARE_HEAD = re.compile(r"\b(rate|share|ratio)\b", re.I)
_MONEY_HEAD = re.compile(r"[$€£]|\b(price|fee|fees|cost|charge|charges|amount|amt)\b", re.I)
# a formula that looks rows up by a label: only such formulas can leave out a value that has no label
_BY_LABEL = re.compile(r"\b(?:SUMIFS?|COUNTIFS?|AVERAGEIFS?)\s*\(", re.I)
_BY_LABEL_CALL = re.compile(r"\b(?:SUMIFS?|COUNTIFS?|AVERAGEIFS?)\s*\((?:[^()]|\([^()]*\))*\)", re.I)
# a text column that states terms in words (its values may be quoted in a terms fact)
_TERMS_HEAD = re.compile(r"\b(terms?|conditions?|notes?|remarks?|comments?|memo|agreement|clauses?)\b", re.I)
_MONTH_WORD = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
_PERIOD_HEADS = [(re.compile(r"\d{4}-\d{2}-\d{2}"), "date"),
                 (re.compile(_MONTH_WORD + r"(?:[\s\-'/]*(?:\d{2}|\d{4}))?", re.I), "month"),
                 (re.compile(r"q[1-4](?:[\s\-']*(?:fy)?\s?'?\d{2,4})?|(?:fy)?\s?\d{2,4}[\s\-]*q[1-4]", re.I), "quarter"),
                 (re.compile(r"h[12](?:[\s\-']*\d{2,4})?", re.I), "half year"),
                 (re.compile(r"(?:fy|cy)\s?'?\d{2,4}", re.I), "year"),
                 (re.compile(r"(?:week|wk)\s*\d{1,2}(?:[\s\-']*\d{2,4})?", re.I), "week"),
                 (re.compile(r"(?:month|m)\s*\d{1,2}", re.I), "month"),
                 (re.compile(r"(?:period|p)\s*\d{1,2}", re.I), "period")]
_TITLE_DATE = r"(?:\d{4}-\d{1,2}-\d{1,2}|\d{1,2}/\d{1,2}/\d{2,4}|" + _MONTH_WORD + r"\s+\d{1,2},?\s+\d{4}|" \
              r"\d{1,2}\s+" + _MONTH_WORD + r"\s+\d{4})"
_TITLE_MONTH = _MONTH_WORD + r"\s+\d{4}"
_TITLE_AS_OF = re.compile(r"\bas\s+(?:of|at)\s*:?\s*(" + _TITLE_DATE + r"|" + _TITLE_MONTH + r")", re.I)
_TITLE_RANGE = re.compile(r"(" + _TITLE_DATE + r"|" + _TITLE_MONTH + r")\s*(?:to|through|thru|until|-|–)\s*("
                          + _TITLE_DATE + r"|" + _TITLE_MONTH + r")", re.I)
_TITLE_QUARTER = re.compile(r"\bQ([1-4])\s*(?:FY)?\s*'?(\d{4})\b|\b(\d{4})\s*-?\s*Q([1-4])\b", re.I)
_TITLE_MONTH_RE = re.compile(r"\b(" + _TITLE_MONTH + r")\b", re.I)
_TITLE_RUN = re.compile(r"\b(?:run|printed|exported|generated|pulled)\b(?:\s+on)?\s*:?\s*(" + _TITLE_DATE + r")",
                        re.I)
_TITLE_YEAR = re.compile(r"(?<![\d/\-])(?:(?:FY|CY|year)\s*)?((?:19|20)\d{2})(?![\d/\-])", re.I)
# a period word next to a bare year: 'for 2025', 'as of 2025', 'during 2025', '2025 annual', '2025 year to date'
_YEAR_BEFORE = re.compile(r"\b(?:for|in|during|of|through|thru|to|from|since|ending|ended|calendar|year)\s*:?\s*$",
                          re.I)
_YEAR_AFTER = re.compile(r"^\s*(?:annual|yearly|year|ytd|full year|calendar year|year to date)\b", re.I)

_ID_FORM = re.compile(r"([A-Za-z]{0,4})([-_ /.#]?\d[\d\-_ /.]*)")
_LAST_FIRST = re.compile(r"[^\W\d_][\w'.\-]*(?: [^\W\d_][\w'.\-]*)*, [^\W\d_][\w'.\-]*(?: [^\W\d_][\w'.\-]*)*")
_FORM_WORDS = {"blank": "blanks", "flag": "yes or no values", "zero": "zeros", "int": "whole numbers",
               "dec": "numbers with decimals", "date": "dates", "text date": "dates typed as text",
               "last_first": "names written last name first", "caps": "capitals", "word": "words"}


def _metric(c) -> bool:
    """A number column of amounts or counts, not a code or a column of document numbers."""
    return c.type == "number" and c.semantic == "metric" and not c.codes and not profile_mod._numbers_as_ids(c)


def _form(v, texted: bool = False) -> str:
    """How a value is written: blank, a whole number or one with decimals, a date
    (or a date typed as text), an ID (its letter prefix and the shape of its
    digits, widths aside), a name written 'LAST, FIRST', capitals, or words."""
    if v is None or (isinstance(v, str) and not v.strip()):
        return "blank"
    if isinstance(v, bool):
        return "flag"
    if _is_num(v):
        return "zero" if v == 0 else "int" if float(v).is_integer() else "dec"
    if hasattr(v, "year"):
        return "text date" if texted else "date"
    s = str(v).strip()
    m = _ID_FORM.fullmatch(s)
    if m:
        return "id:" + m.group(1).upper() + re.sub(r"\d+", "#", m.group(2))
    if _LAST_FIRST.fullmatch(s):
        return "last_first"
    return "caps" if re.search(r"[A-Z]", s) and not re.search(r"[a-z]", s) else "word"


def _texted(t, j: int) -> set:
    """Row positions whose cell in column j was a date typed as text."""
    cache = t.__dict__.setdefault("_texted_rows", {})
    if j not in cache:
        rt = t.retyped.get(j) or {}
        at = {row: i for i, row in enumerate(t.row_index)} if len(t.row_index) == t.n_rows else {}
        cache[j] = {at[x] for x in rt.get("rows", []) if x in at} if rt.get("kind") == "date" else set()
    return cache[j]


def _form_of(t, c, i: int) -> str:
    r = t.rows[i]
    v = r[c.j] if c.j < len(r) else None
    if _metric(c):
        return "" if not _is_num(v) or v == 0 else "positive" if v > 0 else "negative"
    return _form(v, i in _texted(t, c.j))


def _shown(t, c, i, form: str) -> str:
    """A value as the owner would recognize it: a date typed as text in its own
    format, a date as YYYY-MM-DD, a number as written."""
    if i is None:
        return _FORM_WORDS.get(form, "")
    v = t.rows[i][c.j] if c.j < len(t.rows[i]) else None
    if hasattr(v, "year"):
        return v.strftime(c.retyped_format) if form == "text date" and c.retyped_format else v.isoformat()[:10]
    return _shown_value(v)


def _shown_value(v) -> str:
    from .findings import _short
    if hasattr(v, "year"):
        return f"{_MONTHS[v.month - 1]} {v.day}, {v.year}"
    if _is_num(v):
        return str(int(v)) if float(v).is_integer() else f"{v:,.2f}"
    return _short(v, 30)


def _written(v) -> str:
    if _is_num(v) and float(v).is_integer():
        return str(int(v))
    return str(v).strip() if v is not None else ""


def _written_key(t, c, key) -> str:
    """The first value of column c as written whose normalized key is this one."""
    from .findings import _short
    for r in t.rows:
        v = r[c.j] if c.j < len(r) else None
        if profile_mod.norm_key(v) == key:
            return _short(_written(v), 30)
    return str(key)


def _label_days(t, c, day: dict) -> dict:
    """{value key: its sorted days} for the dated rows of column c."""
    out: dict = {}
    for i, d in day.items():
        k = profile_mod.norm_key(t.rows[i][c.j] if c.j < len(t.rows[i]) else None)
        if k is not None:
            out.setdefault(k, []).append(d)
    return {k: sorted(ds) for k, ds in out.items()}


def _successions(groups: dict, day: dict, need: float, span: int) -> list:
    """(old, new, switch day) for values of one view where the new one takes over
    from the old: each on need rows or more over at least 10% of the span (3% of
    its rows trimmed at each end), the new starting after the old starts and
    ending after it ends, overlapping by at most 15% of the span and starting at
    most 14 days after the old one stops. That gap is read on the first and last
    days as recorded: trimming both ends opens a gap of its own on a sparse log,
    and an outlier row can only make the raw gap smaller."""
    info = {}
    for k, rows in groups.items():
        if len(rows) < need:
            continue
        ds = sorted(day[i] for i in rows)
        cut = int(0.03 * len(ds))
        s, e = ds[cut], ds[-1 - cut]
        if e - s >= 0.10 * span:
            info[k] = (s, e, ds)
    out = []
    for a, (sa, ea, da) in info.items():
        for b, (sb, eb, db) in info.items():
            if a == b or sb <= sa or eb <= ea or max(0, ea - sb) > 0.15 * span \
                    or db[0] - da[-1] > 14:
                continue
            out.append((a, b, _switch(da, db)))
    return out


def _switch(da: list, db: list) -> int:
    """The first of the new value's days that best splits the old value's rows
    (before it) from the new one's (on and after it)."""
    import bisect
    best = None
    for d in sorted(set(db)):
        cost = len(da) - bisect.bisect_left(da, d) + bisect.bisect_left(db, d)
        if best is None or cost < best[0]:
            best = (cost, d)
    return best[1]


def _profile_change(xs: list, ys: list) -> tuple | None:
    """(before, after) in words when a number column is written another way from
    a date: whole numbers to decimals, a handful of values to many, or a scale 5
    times larger or smaller. None when it reads the same on both sides."""
    from .recipes import fmt_num

    def prof(vs):
        ints = sum(1 for v in vs if float(v).is_integer()) / len(vs)
        vals = Counter(round(float(v), 6) for v in vs)
        nz = sorted(abs(float(v)) for v in vs if v)
        return ints, vals, (nz[len(nz) // 2] if nz else 0.0)
    (ia, va, ma), (ib, vb, mb) = prof(xs), prof(ys)
    few = (len(va) <= 6 < 12 < len(vb)) or (len(vb) <= 6 < 12 < len(va))
    whole = (ia >= 0.95 and ib <= 0.5) or (ib >= 0.95 and ia <= 0.5)
    scale = bool(ma and mb) and max(ma, mb) / min(ma, mb) >= 5
    if not (few or whole or scale):
        return None

    def words(ints, vals, other, med):
        kind = "whole numbers" if ints >= 0.95 else "numbers with decimals" if ints <= 0.5 else "numbers"
        if len(vals) <= 6 < 12 < len(other):
            kind += f" from {len(vals)} values ({', '.join(fmt_num(v) for v in sorted(vals))})"
        return kind + (f", around {fmt_num(round(med, 2))}" if scale else "")
    return words(ia, va, vb, ma), words(ib, vb, va, mb)


def _marks_kind(t, c, pairs: list) -> bool:
    """A code column tells the two rows of a pair apart the same way on 90% of
    pairs or more (Invoice on one, Payment on the other): two kinds of document,
    not a row there twice."""
    got = Counter((profile_mod.norm_key(t.rows[i][c.j] if c.j < len(t.rows[i]) else None),
                   profile_mod.norm_key(t.rows[j][c.j] if c.j < len(t.rows[j]) else None)) for i, j in pairs)
    (a, b), k = got.most_common(1)[0]
    return a != b and k >= 0.9 * len(pairs)


def _pair_why(p: dict, via: str) -> str:
    """What pairs an old value with its new one, in words: the values of another
    column their rows share, or, where those cannot tell them apart, that one
    follows the other."""
    if p.get("fallback"):
        return "one follows the other, alike in rows a day" + (f" and in {p['via']}" if p.get("via") else "")
    return f"same {p['shared']:,} {_plural(via, p['shared'])}"


def _none_worth(_r) -> float:
    return 0.0


def _jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a or b else 0.0


def _words(v) -> frozenset:
    return frozenset(re.findall(r"[a-z]+", str(v).lower()))


def _plural(header: str, n: int) -> str:
    from .recipes import plural
    return plural(header, n)


def _family(v):
    """A document number's family: its letter prefix and the shape of the rest,
    digit widths aside ('AB-1042' and 'AB-10420' are one family). Plain whole
    numbers are one family; anything else has none."""
    if _is_num(v):
        return "#" if float(v).is_integer() else None
    f = _form(v)
    return f[3:] if f.startswith("id:") else None


def _family_label(f: str) -> str:
    return f.split("#")[0] or "plain"


def _span_words(a, b) -> str:
    from .findings import _span
    return _span(a, b)


def _stamp(v):
    """An upload time as a sortable string: a date and time to the second, or the text as written."""
    if isinstance(v, (int, float)) or v is None:
        return None
    if hasattr(v, "year"):
        return v.isoformat()
    s = str(v).strip()
    return s or None


def _stamp_words(stamp: str) -> str:
    import datetime as dt
    try:
        d = dt.datetime.fromisoformat(stamp)
    except ValueError:
        return stamp
    return f"{_MONTHS[d.month - 1]} {d.day}, {d.year} {d.hour:02d}:{d.minute:02d}"


def _blank(v) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


def _listed(vals: list) -> str:
    from .findings import _listed as listed
    return listed(vals)


def _median(xs) -> float:
    xs = sorted(xs)
    return float(xs[len(xs) // 2]) if xs else 0.0


def _ord(iso: str) -> int:
    import datetime as dt
    return dt.date.fromisoformat(str(iso)[:10]).toordinal()


def _day_words(d: int) -> str:
    import datetime as dt
    x = dt.date.fromordinal(int(d))
    return f"{_MONTHS[x.month - 1]} {x.day}, {x.year}"


def _status_header(header) -> bool:
    from .findings import _status_header as status
    return status(header)


def _plain_words(header) -> list:
    return re.findall(r"[a-z]+", profile_mod.split_camel(str(header or "")).lower())


def _grouping(c) -> bool:
    """A column that splits the rows into a few groups (a location, a channel, a
    category): 2 to 30 values with 10 rows or more each, written as words or
    codes, never dates, people, notes or amounts."""
    from .findings import _NOTES
    return c.type in ("text", "number") and 2 <= c.distinct <= 30 and not c.distinct_capped \
        and c.count >= 10 * c.distinct and not c.sensitive and c.retyped_kind != "date" and c.avg_len <= 40 \
        and not _NOTES.search(str(c.header)) and (c.type == "text" or c.codes) \
        and c.semantic in ("dimension", "identifier")


def _numbering(written: dict) -> dict:
    """{key: why its number stands out}: past Q3 + 3 IQR of the numbers with its
    own letter prefix (10 numbers or more), all 9s or all 0s (3 digits or more),
    or TEST, DUMMY or XXX in it."""
    parts, fams = {}, {}
    for k, w in written.items():
        m = re.fullmatch(r"([A-Za-z]*)[-_ ]?(\d+)", str(w or "").strip())
        if m:
            parts[k] = (m.group(1).upper(), m.group(2))
            fams.setdefault(m.group(1).upper(), []).append((int(m.group(2)), w))
    lims = {}                 # each family's limit and the first and last number within it, once per family
    for pre, nums in fams.items():
        if len(nums) < 10:
            continue
        nums = sorted(nums)
        q1, q3 = nums[len(nums) // 4][0], nums[(3 * len(nums)) // 4][0]
        lim = q3 + 3 * (q3 - q1)
        rest = [x for x in nums if x[0] <= lim]
        if rest:
            lims[pre] = (lim, rest[0][1], rest[-1][1])
    out = {}
    for k, w in written.items():
        if _SENTINEL_WORD.search(str(w or "")):
            out[k] = f"{w} has {_SENTINEL_WORD.search(str(w)).group(0).upper()} in it"
            continue
        if k not in parts:
            continue
        pre, digits = parts[k]
        if len(digits) >= 3 and len(set(digits)) == 1 and digits[0] in "09":
            out[k] = f"its number is all {digits[0]}s"
            continue
        got = lims.get(pre)
        if got and int(digits) > got[0]:
            out[k] = f"its number is far past the others, which run {got[1]} to {got[2]}"
    return out


def _a(word: str) -> str:
    return "an" if word[:1].lower() in "aeio" else "a"


def _stem(path: str) -> str:
    return os.path.splitext(os.path.basename(path))[0]


def _period_kind(h) -> str:
    """What a grid's column header names: a date, a month, a quarter, a half year,
    a year, a week or a numbered period; '' for anything else."""
    s = str(h or "").strip()
    for rx, kind in _PERIOD_HEADS:
        if rx.fullmatch(s):
            return kind
    if re.fullmatch(r"\d{4}", s) and 1990 <= int(s) <= 2100:
        return "year"
    return ""


def _date_step(heads: list) -> str:
    """Dated grid columns read as weeks, months, quarters or years by their spacing."""
    import datetime as dt
    days = sorted(dt.date.fromisoformat(h[:10]).toordinal() for h in heads if re.fullmatch(r"\d{4}-\d{2}-\d{2}", h))
    gaps = sorted(b - a for a, b in zip(days, days[1:]))
    g = gaps[len(gaps) // 2] if gaps else 0
    return "week" if 6 <= g <= 8 else "month" if 27 <= g <= 32 else "quarter" if 88 <= g <= 93 \
        else "year" if 360 <= g <= 370 else "period"


def _period_words(h: str, kind: str) -> str:
    """A period header as a reader says it: 'Jan 2025' for a dated month, the header as written otherwise."""
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(h)):
        return _month(h) if kind in ("month", "quarter", "year") else _day_words(_ord(h))
    return str(h)


def _title_day(s: str, end: bool = False):
    """A date written in a title ('March 31, 2025', '3/31/2025', '2025-03-31'), or
    a month ('Mar 2025': its first day, or its last with end). None when it is neither."""
    import calendar
    import datetime as dt
    s = re.sub(r"\s+", " ", s.strip().replace(".", ""))
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%B %d, %Y", "%b %d, %Y", "%B %d %Y", "%b %d %Y", "%d %B %Y",
                "%d %b %Y"):
        try:
            return dt.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    for fmt in ("%B %Y", "%b %Y"):
        try:
            d = dt.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
        return d.replace(day=calendar.monthrange(d.year, d.month)[1]) if end else d
    if s[:4].lower() == "sept":
        return _title_day("Sep" + s[4:], end)
    return None


def _title_period(text: str, years: set | None = None) -> dict | None:
    """{kind, said, end} for the date a title or run note names: 'as of' a date
    first, then a range of two dates or months, a quarter, a month, a run date,
    and last a year. A bare four-digit number is a year only with a period word
    next to it ('for 2025', 'year 2025', '2025 annual'), or when it is the only
    number in the title and the rows' years (when given) include it: 'Store
    2019 daily sales' over rows of 2031 names a store. None when it names none."""
    import datetime as dt
    m = _TITLE_AS_OF.search(text)
    if m and _title_day(m.group(1), end=True):
        return {"kind": "as_of", "said": m.group(0).strip(), "end": _title_day(m.group(1), end=True)}
    m = _TITLE_RANGE.search(text)
    if m and _title_day(m.group(1)) and _title_day(m.group(2), end=True) \
            and _title_day(m.group(1)) <= _title_day(m.group(2), end=True):
        return {"kind": "range", "said": m.group(0).strip(), "end": _title_day(m.group(2), end=True)}
    m = _TITLE_QUARTER.search(text)
    if m and "fy" not in m.group(0).lower():          # a fiscal quarter's dates are not in the title
        q, y = (int(m.group(1)), int(m.group(2))) if m.group(1) else (int(m.group(4)), int(m.group(3)))
        last = dt.date(y + (q == 4), 1 if q == 4 else 3 * q + 1, 1) - dt.timedelta(days=1)
        return {"kind": "quarter", "said": m.group(0).strip(), "end": last}
    m = _TITLE_MONTH_RE.search(text)
    if m and _title_day(m.group(1), end=True):
        return {"kind": "month", "said": m.group(1).strip(), "end": _title_day(m.group(1), end=True)}
    m = _TITLE_RUN.search(text)
    if m and _title_day(m.group(1)):
        return {"kind": "run", "said": m.group(0).strip(), "end": _title_day(m.group(1))}
    for m in _TITLE_YEAR.finditer(text):
        # a fiscal year ends on a date the title does not name: never read as December 31
        if re.search(r"\bFY\s*'?$", text[:m.start(1)], re.I):
            continue
        y = int(m.group(1))
        near = m.group(0) != m.group(1) or _YEAR_BEFORE.search(text[:m.start()]) \
            or _YEAR_AFTER.search(text[m.end():])
        alone = len(re.findall(r"\d+", text)) == 1 and (years is None or y in years)
        if near or alone:
            return {"kind": "year", "said": m.group(0).strip(), "end": dt.date(y, 12, 31)}
    return None


def _cycle(days: list) -> dict | None:
    """The cycle distinct dates (ordinals) follow, or None: 80% of them on one
    weekday whose gaps are one step (7, 14 or 28 days) 80% of the time, or on one
    day of the month (or its last day) in consecutive months. {step, on, off, and
    weekday or day}: the dates on the cycle and the dates off it (20% at most)."""
    import calendar
    import datetime as dt
    if len(days) < CADENCE_DATES:
        return None
    wd = Counter(dt.date.fromordinal(d).weekday() for d in days)
    w, k = wd.most_common(1)[0]
    if k >= CADENCE_SHARE * len(days) and k >= CADENCE_DATES:
        reg = [d for d in days if dt.date.fromordinal(d).weekday() == w]
        step, g = Counter(b - a for a, b in zip(reg, reg[1:])).most_common(1)[0]
        on = [d for d in reg if (d - reg[0]) % step == 0]
        if g >= CADENCE_SHARE * (len(reg) - 1) and len(on) >= CADENCE_SHARE * len(days):
            mine = set(on)
            return {"step": step, "weekday": w, "on": on, "off": [d for d in days if d not in mine]}
        return None

    def dom(d):
        x = dt.date.fromordinal(d)
        return "last" if x.day == calendar.monthrange(x.year, x.month)[1] and x.day >= 28 else x.day
    dm = Counter(dom(d) for d in days)
    day, k = dm.most_common(1)[0]
    if k < CADENCE_SHARE * len(days) or k < CADENCE_DATES:
        return None
    on = [d for d in days if dom(d) == day]
    months = [dt.date.fromordinal(d).year * 12 + dt.date.fromordinal(d).month for d in on]
    if sum(1 for a, b in zip(months, months[1:]) if b - a == 1) < CADENCE_SHARE * (len(on) - 1):
        return None
    mine = set(on)
    return {"step": "month", "day": day, "on": on, "off": [d for d in days if d not in mine]}


def _cycle_by_rows(per_day: Counter) -> dict | None:
    """The cycle most rows keep to when the dates alone show none: the weekday
    carrying the most rows, its dates one step apart (7, 14 or 28 days) 80% of the
    time, and the dates on that step carrying CADENCE_SHARE of the rows or more.
    {step, weekday, on, off, by_rows}."""
    import datetime as dt
    days = sorted(per_day)
    total = sum(per_day.values())
    if len(days) < CADENCE_DATES or not total:
        return None
    wd = Counter()
    for d in days:
        wd[dt.date.fromordinal(d).weekday()] += per_day[d]
    w, _k = wd.most_common(1)[0]
    reg = [d for d in days if dt.date.fromordinal(d).weekday() == w]
    if len(reg) < CADENCE_DATES:
        return None
    step, g = Counter(b - a for a, b in zip(reg, reg[1:])).most_common(1)[0]
    if step not in (7, 14, 28) or g < CADENCE_SHARE * (len(reg) - 1):
        return None
    on = [d for d in reg if (d - reg[0]) % step == 0]
    if sum(per_day[d] for d in on) < CADENCE_SHARE * total or len(on) < CADENCE_DATES:
        return None
    mine = set(on)
    return {"step": step, "weekday": w, "on": on, "off": [d for d in days if d not in mine], "by_rows": True}


def _mon_words(ym: str) -> str:
    """'2025-07' -> 'Jul 2025'."""
    y, m = str(ym).split("-")[:2]
    return f"{_MONTHS[int(m) - 1]} {y}"


def _range_words(a: int, b: int) -> str:
    if a == b:
        return f"on {_day_words(a)}"          # one day: never 'X to X'
    return f"{_day_words(a) if a > -10 ** 9 else 'no start'} to {_day_words(b) if b < 10 ** 9 else 'no end'}"


def _window_words(p: dict) -> str:
    """'Kalo: 30 after its end on Jun 30, 2025' for one key's lines outside its dates."""
    bits = []
    if p["after"]:
        bits.append(f"{p['after']:,} after its end on {_day_words(_ord(p['end']))}")
    if p["before"]:
        bits.append(f"{p['before']:,} before its start on {_day_words(_ord(p['start']))}")
    if p["gap"]:
        bits.append(f"{p['gap']:,} between its dated rows")
    return f"{p['key']}: " + ", ".join(bits)


def _onset_words(f: dict) -> str:
    return (f"{f['code']} in {f['col']} starts for {f['group']} in {_mon_words(f['month'])} ({f['rows']:,} rows since), "
            f"after {f['before']} months of {f['group']} rows without it")


def _rate_words(c, v, money: bool = False) -> str:
    """A number from a terms table with only the unit its header or its role gives:
    '2%' when the header names a percent or a rate (a fraction under a rate or
    share header read as a percent), '$1.25' when the column is bound to a money
    role or its header names money, else the number as written ('Fee per Case'
    2.5 is 2.5, 'Min Order Qty' 1 is 1)."""
    header = profile_mod.split_camel(str(c.header))
    fractions = c.max is not None and abs(float(c.max)) <= 1 and (c.min is None or abs(float(c.min)) <= 1)
    if _PERCENT_HEAD.search(header) or (fractions and _SHARE_HEAD.search(header)):
        p = round(v * 100, 6) if fractions else v          # 0.02 as a share, 2 as a percent: the column decides
        return f"{p:g}%"
    if money or _MONEY_HEAD.search(header):
        return f"${v:,.2f}"
    x = round(float(v), 6)
    return f"{int(x):,}" if x.is_integer() else f"{x:,}"


def _forms(t, c) -> dict:
    """{normalized key: {value as written: rows}} for one column."""
    out: dict = {}
    for x in t.rows:
        v = x[c.j] if c.j < len(x) else None
        k = profile_mod.norm_key(v)
        if k is None or not isinstance(v, str):
            continue
        out.setdefault(k, Counter())[v] += 1
    return out


def _iso_of(d: int) -> str:
    import datetime as dt
    return dt.date.fromordinal(int(d)).isoformat()


def _join_words(items: list) -> str:
    from .findings import _join
    return _join([str(x) for x in items])


GROUP_MIN = 20            # groups of 2 lines or more before a table's groups are read
GROUP_MAX = 20            # lines per group on average, at most: more is a category, not a document
GROUP_CATS = 60           # values a line-category column has, at most
RATIO_GROUPS = 20         # groups where a category sits beside the rest of its side, before its ratio is read
RATIO_SHARE = 0.9         # ... and the share of them where it is that fixed ratio to the cent
RATIO_MAX = 0.5           # a companion line is at most half of what it is a share of
BOUNDARY_NEAR = 45        # dates where one table changes form this many days apart or less are one switch
CONTRA_SIDE = 0.8         # a category's usual side holds this share of its lines, when no lookup gives it
CONTRA_LINES = 5          # lines a category needs before its usual side is read from them
CONTRA_MIN = 3            # lines on the opposite side before they are asked about
CONTRA_RARE = 0.1         # with no prefix, a lookup's side counts only where this share of lines or fewer is opposite
CONTRA_LINES_RARE = 20    # ... on a category with this many lines or more
PREFIX_SHARE = 0.75       # a prefix marks lines when this share of them start with it, and of the rows that do
OPENING_TIMES = 2         # an opening group's largest amount is this many times the largest outside it, or more
# balances carried in, in words: an opening word beside 'balance', or brought or carried forward. 'Grand
# opening', 'Opened account' and 'Beginning of promo' are ordinary first entries
_OPENING_WORDS = re.compile(r"\b(open|opening|begin|beginning|start|starting|initial)\s+(bal|bals|balances?)\b|"
                            r"\b(bal|balances?)\s+(brought|carried|b)\s*(forward|fwd|/f)\b|"
                            r"\b(brought|carried|carry)\s+(forward|fwd|over)\b|\bb/f\b|"
                            # the standard carry-forward forms: balance forward, brought forward and their short spellings
                            r"\b(bal|balance)\s*(fwd|forward)\b", re.I)
# balances closed out at the end, in words: a closing entry or closing balances, a year-end (or period-end)
# close, closing the books, or balances carried forward or down. 'Store closing supplies' and 'Close out sale'
# are ordinary last entries
_CLOSING_WORDS = re.compile(r"\bclosing\s+(entry|entries|je|bal|bals|balances?)\b|"
                            r"\b(year|period|month|fiscal)[- ]?end\s+(close|closing)\b|"
                            r"\bclos(e|ing)\s+(the\s+)?(books|year|period)\b|"
                            r"\b(carried|carry)\s+(forward|fwd|down)\b|\bc/[fd]\b", re.I)
_ID_WORDS = {"id", "no", "nos", "num", "number", "ref", "#", "code", "key"}
_PREFIX = re.compile(r"^\s*([A-Za-z][A-Za-z ]{1,24}?)\s*[:\-]\s")


def _group_word(header: str) -> str:
    """What one group is, from its ID column's header: 'Order ID' -> 'order'."""
    words = [w for w in re.split(r"[\s_]+", str(header).strip().lower()) if w and w not in _ID_WORDS]
    return " ".join(words) if words else "group"


def _group_noun(header: str) -> str:
    return recipes_mod.plural(_group_word(header))


_NOTE_HEADER = re.compile(r"\b(memo|notes?|description|descr|details?|narrative|comments?)\b", re.I)


def _move_words(x: dict) -> str:
    """One group of balance moves in words: '14 transfers from Bank A to Bank B',
    '12 payments on Card from Bank A', '14 draws to Partner Draws from Bank A'."""
    n = x["entries"]
    frm, to = _join_words(x["from"]), _join_words(x["to"])
    memo = f" (memo \"{x['memo'][:40]}\")" if x.get("memo") else ""
    if x["kind"] == "transfer":
        return f"{n:,} transfer{'s' if n != 1 else ''} from {frm} to {to}{memo}"
    if x["kind"] == "payment":
        return f"{n:,} payment{'s' if n != 1 else ''} on {to} from {frm}{memo}"
    if x["kind"] == "draw":
        return f"{n:,} draw{'s' if n != 1 else ''} to {to} from {frm}{memo}"
    return f"{n:,} deposit{'s' if n != 1 else ''} to {to} from {frm}{memo}"


def _prefix_families(t, j: int, least: int) -> dict:
    """{prefix: row indexes} for text that starts with the same word followed by
    ':' or '-' ('Refund: ...', 'Return - ...'), with at least `least` rows each."""
    fams: dict = {}
    for i, r in enumerate(t.rows):
        v = r[j] if j < len(r) else None
        m = _PREFIX.match(v) if isinstance(v, str) else None
        if m:
            fams.setdefault(m.group(1).strip(), set()).add(i)
    return {k: v for k, v in fams.items() if len(v) >= least}


def _money_col(analysis, t):
    from .findings import money_column
    return money_column(analysis, t)


# a model row that is a level at each period end (cash, a balance): the stock words, less those that name a
# period's start; and one that says what moved in the period, never a level ('Cash in' and 'Cash out' at the end)
_LEVEL_ROW = re.compile(r"\bcash\b|\b(on ?hand|balances?|bal|outstanding|remaining|closing|ending)\b", re.I)
_NOT_LEVEL = re.compile(r"\b(flows?|from|change|changes|net|beginning|begin|opening|raised|burn|used|movement|"
                        r"interest|paid|payments?|received|receipts?)\b|\b(in|out)\s*$", re.I)


def _value_words(v) -> str:
    """A typed number as written: 42000 -> '42,000', 0.015 -> '0.015'."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return str(v)
    if float(v).is_integer():
        return f"{int(v):,}"
    return f"{v:,.10g}"


def _year_of(h) -> int | None:
    m = re.search(r"(?<!\d)((?:19|20)\d{2})(?!\d)", str(h))
    return int(m.group(1)) if m else None


def _formula_counts(sheet, t) -> dict:
    if not sheet.formulas:
        return {}
    out: dict = {}
    rows = set(t.row_index)
    for (r, c), _ in sheet.formulas.items():
        if r in rows and c in t.cols:
            j = t.cols.index(c)
            out[j] = out.get(j, 0) + 1
    return out
