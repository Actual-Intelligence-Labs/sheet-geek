"""How a table's rows are written: blanks, the dates where the file changes, rows loaded twice,
groups and entities unlike their peers, and measures a table lacks. These are methods of Analysis
(analyze.py), kept in their own file so each file stays under 256 KiB, the most the plugin directory's
checker reads."""
from __future__ import annotations

import difflib
import re
from collections import Counter

from . import profile as profile_mod
from . import tables as tables_mod
from .profile import _is_num

from .analyze_helpers import (
    _a, _ADJUST_HEADER, _AMOUNT_NOT, _blank, BLOCK_RATIO, BOUNDARY_NEAR, BOUNDARY_ROWS, _day_words,
    _family, _family_label, _form, _form_of, _FORM_WORDS, GROUP_MAX, _grouping, _iso_of, _jaccard,
    _join_words, _label_days, _listed, _marks_kind, _median, _metric, _MONTHS, _numbering, ODD_AGREE,
    ODD_ASK, ODD_EXPECT, ODD_MEASURE, ODD_MISSING, ODD_MISSING_EXPECT, ODD_PRICE, ODD_ROWS, ODD_Z, _ord,
    _pair_why, _PERSON_HEAD, _plain_words, _plural, _PRICE_HEADER, _profile_change, _shown, _shown_value,
    _span_words, _stamp, _stamp_words, _status_header, _successions, _texted, _words, _written,
    _written_key,
)


class RowFacts:
    """The row-level checks Analysis inherits."""


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
