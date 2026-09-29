"""Deterministic analyses named in playbooks. Every number a user sees comes
from here, never from the model. Each recipe returns an insight dict with a
declarative statement, the numbers, and the columns it depends on.
"""
from __future__ import annotations

import datetime as dt
import re
from collections import Counter, defaultdict

from .profile import norm_key


def fmt_money(x: float) -> str:
    a = abs(x)
    sign = "-" if x < 0 else ""
    if a >= 1e9:
        return f"{sign}${a / 1e9:.2f}B"
    if a >= 1e6:
        return f"{sign}${a / 1e6:.2f}M"
    if a >= 1e4:
        return f"{sign}${a / 1e3:.1f}k"
    return f"{sign}${a:,.0f}"


def fmt_num(x) -> str:
    if isinstance(x, float) and not x.is_integer():
        return f"{x:,.2f}"
    return f"{int(x):,}"


def fmt_value(x: float, money: bool) -> str:
    return fmt_money(x) if money else fmt_num(round(x, 2))


def pct(x: float) -> str:
    v = 100 * x
    if 99 < v < 100:
        return f"{v:.1f}%"
    return f"{v:.0f}%" if v >= 10 or v == 0 else f"{v:.1f}%"


# a header that ends in a preposition or 'By' ('Entered By', 'Ship To') names who or where, not a thing: it is
# counted as '<header> values', never 'Entered Bies'
_ROLE_TAIL = re.compile(r"(?<![-\w])(by|to|from|for|of|at|on|in|with|per|via)\s*$", re.I)


def plural(noun: str, n: int = 2) -> str:
    if _ROLE_TAIL.search(str(noun)) and " " in str(noun).strip():
        return f"{noun} value" if n == 1 else f"{noun} values"
    if n == 1:
        return noun
    if noun.endswith("y") and not noun.endswith(("ay", "ey", "oy")):
        return noun[:-1] + "ies"
    if noun.endswith(("s", "x", "ch", "sh")):
        return noun + "es"
    return noun + "s"


class Ctx:
    """What a recipe needs: role -> column, the table rows under the owner's
    confirmed rules, those rows' column profiles, and playbook roles."""

    def __init__(self, analysis, detection: dict, playbook: dict):
        self.a = analysis
        self.det = detection
        self.pb = playbook
        self.rules: list = []
        self.not_items: set = set()
        self._ruled: dict = {}

    def set_rules(self, rules: list, not_items=None):
        """The confirmed rules every count and total goes through from now on. A value
        left out is left out under every spelling a confirmed map makes it one with
        (the old name and its new code), since exclusions are applied before maps."""
        self.rules = _widen(list(rules or []))
        self.not_items = set(not_items or ())
        self._ruled = {}

    def _applies(self, t, metric=None, skip=()) -> list:
        idx = {h: j for j, h in enumerate(t.headers)}
        return [r for r in self.rules if r.confirmed and r.table == t.tid and r.covers(metric) and r.valid(idx)
                and r.kind not in skip]

    def _view(self, t, metric=None, skip=()) -> list:
        """[(position in t.rows, row)] under the rules that cover this metric, cached per rule set."""
        from .rules import apply
        mine = self._applies(t, metric, skip)
        if not mine:
            return list(enumerate(t.rows))
        key = (t.tid, tuple(sorted(str(r.key()) for r in mine)))
        if key not in self._ruled:
            self._ruled[key] = apply(mine, t, self.a.cols.get(t.tid, []), metric)
        return self._ruled[key]

    def rows(self, t, metric=None, skip=()):
        """Data rows under the owner's confirmed rules (all of a table's rows when it has none).
        metric: the column being totaled, so a rule scoped to other totals leaves it alone.
        skip: rule kinds left out, like 'scale' for a check on the values as recorded."""
        if not self._applies(t, metric, skip):
            return t.rows
        return [r for _i, r in self._view(t, metric, skip)]

    def col(self, t, j: int, metric=None):
        """Column j profiled over the ruled rows: the table's own profile when no rule applies."""
        own = self.a.cols[t.tid][j]
        mine = self._applies(t, metric)
        if not mine:
            return own
        key = ("col", t.tid, j, tuple(sorted(str(r.key()) for r in mine)))
        if key not in self._ruled:
            import copy
            from .profile import profile_column
            view = self._view(t, metric)
            v = copy.copy(t)
            v.rows = [r for _i, r in view]
            v.row_index = [t.row_index[i] for i, _r in view] if len(t.row_index) == len(t.rows) else []
            self._ruled[key] = profile_column(v, j, round(own.formula_share * own.count))
        return self._ruled[key]

    def role(self, rid: str):
        r = self.det["roles"].get(rid)
        if not r or r.get("col") is None:
            return None
        return r

    def label(self, rid: str) -> str:
        r = self.det["roles"].get(rid)
        return r["header"] if r else rid

    def noun(self, rid: str) -> str:
        role = self.pb.get("roles", {}).get(rid, {})
        return role.get("entity") or role.get("label", rid).lower()

    def is_money(self, rid: str) -> bool:
        return self.pb.get("roles", {}).get(rid, {}).get("unit") == "currency"

    def note(self, t, metric=None, skip=()) -> str:
        """The rules a number went through, named after it: ' (leaving out Q7, per the owner)'."""
        from .rules import where
        mine = self._applies(t, metric, skip)
        if not mine:
            return ""
        shown, parts = [], []
        for r in mine:
            one = len(r.predicate) == 1 and r.predicate[0].get("op", "in") == "in"
            if r.kind == "exclude" and one:
                shown += [str(v) for v in r.predicate[0]["values"]]
            elif r.kind == "exclude":
                parts.append(f"leaving out rows where {where(r)}")
            elif r.kind == "filter":
                parts.append(f"only rows where {where(r)}")
            elif r.kind == "map":
                vals = [str(v) for v in (r.predicate[0]["values"] if r.predicate else [])]
                parts.append(f"counting {' and '.join(vals)} as one")
            elif r.kind == "scale":
                by = float(r.values.get("by") or 0)
                parts.append(f"{r.values.get('col')} divided by {fmt_num(by)}"
                             + (f" where {where(r)}" if r.predicate else ""))
            elif r.kind == "pair":
                parts.append(f"leaving out rows where {where(r)} with their twin rows")
            elif r.kind == "dedupe":
                parts.append(f"each {r.values.get('col')} once, keeping the {r.values.get('keep', 'first')} row")
            elif r.kind == "fill":
                parts.append(f"a blank {r.values.get('col')} taking the value above it")
        if shown:
            parts.insert(0, f"leaving out {', '.join(sorted(dict.fromkeys(shown))[:3])}")
        return f" ({'; '.join(parts)}, per the owner)"

    def same_table(self, *rids):
        infos = [self.role(r) for r in rids]
        if any(i is None for i in infos):
            return None
        tids = {i["table"] for i in infos}
        if len(tids) != 1:
            return None
        t = self.a.table(tids.pop())
        return t, [i["col"].j for i in infos]


def _widen(rules: list) -> list:
    """Each confirmed exclusion on a column a confirmed map rule merges values of,
    widened to every value the map makes one with those it names: 'leave QXR
    out' also leaves out 'Quarry Row' once the owner said the two are one. An
    unconfirmed map widens nothing."""
    from .rules import Rule
    maps = [r for r in rules if r.kind == "map" and r.confirmed and (r.values or {}).get("to")]
    if not maps:
        return rules
    out = []
    for r in rules:
        if r.kind != "exclude" or not r.confirmed:
            out.append(r)
            continue
        pred, grew = [], False
        for c in r.predicate:
            vals = list(c.get("values") or [])
            if c.get("op", "in") == "in":
                keys = {str(norm_key(v)) for v in vals}
                for m in maps:
                    if m.table != r.table or m.values.get("col") != c["col"]:
                        continue
                    to = {str(k): str(norm_key(v)) for k, v in m.values["to"].items()}
                    targets = {to.get(k, k) for k in keys}
                    more = [k for k, t in to.items() if t in targets and k not in keys]
                    if more:
                        # the other spellings as the map's own rule writes them
                        written = {str(norm_key(v)): str(v) for p in m.predicate for v in p.get("values") or []}
                        vals += [written.get(k, k) for k in more]
                        keys |= set(more)
                        grew = True
            pred.append(dict(c, values=vals))
        out.append(Rule(r.kind, r.table, pred, dict(r.values), list(r.scope), r.source, r.confirmed) if grew else r)
    return out


def _dep(ctx, *rids) -> list:
    out = []
    for r in rids:
        info = ctx.role(r)
        if info:
            out.append((ctx.a.table(info["table"]).sheet, info["header"]))
    return out


def _num(v):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v)


_MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def day_words(iso: str) -> str:
    """'2025-03-03' -> 'Mar 3, 2025'; '2025-03' -> 'Mar 2025'."""
    m = re.fullmatch(r"(\d{4})-(\d{2})(?:-(\d{2}))?.*", str(iso))
    if not m:
        return str(iso)
    mon = _MON[int(m.group(2)) - 1]
    return f"{mon} {int(m.group(3))}, {m.group(1)}" if m.group(3) else f"{mon} {m.group(1)}"


def _snapshot(ctx, t, metric_label: str):
    """The snapshot panel a stock column sits on (a count at each date, never a
    sum across dates), or None."""
    snap = (getattr(ctx.a, "snapshots", None) or {}).get(t.tid)
    return snap if snap and metric_label in snap.get("stock", []) else None


def _latest_rows(rows: list, snap: dict) -> list:
    j, latest = snap["j"], snap["latest"]
    return [r for r in rows if j < len(r) and hasattr(r[j], "year") and r[j].toordinal() == latest]


def _on_latest(snap) -> str:
    return f" on {snap['col']} {day_words(snap['latest_iso'])}, the latest of {snap['dates']:,} dates" if snap else ""


def pareto(ctx, metric, entity):
    st = ctx.same_table(metric, entity)
    if not st:
        return None
    t, (jm, je) = st
    sums: dict = defaultdict(float)
    snap = _snapshot(ctx, t, ctx.label(metric))
    rows = ctx.rows(t, ctx.label(metric))
    for r in (_latest_rows(rows, snap) if snap else rows):
        v = _num(r[jm] if jm < len(r) else None)
        k = r[je] if je < len(r) else None
        if v is None or k is None:
            continue
        sums[norm_key(k)] += v
    total = sum(v for v in sums.values() if v > 0)
    if total <= 0 or len(sums) < 5:
        return None
    acc, n80 = 0.0, 0
    for v in sorted((v for v in sums.values() if v > 0), reverse=True):
        acc += v
        n80 += 1
        if acc >= 0.8 * total:
            break
    noun = ctx.noun(entity)
    return {"recipe": f"pareto:{metric}:{entity}", "kind": "concentration",
            "statement": f"{fmt_num(n80)} of {fmt_num(len(sums))} {plural(noun)} account for 80% of "
                         f"{ctx.label(metric)}{_on_latest(snap)}{ctx.note(t, ctx.label(metric))}.",
            "numbers": dict({"n80": n80, "n": len(sums), "total": total},
                            **({"as_of": snap["latest_iso"], "period_col": snap["col"]} if snap else {})),
            "depends": _dep(ctx, metric, entity), "weight": 2 if n80 / len(sums) < 0.25 else 1}


def top_share(ctx, metric, entity):
    st = ctx.same_table(metric, entity)
    if not st:
        return None
    t, (jm, je) = st
    sums: dict = defaultdict(float)
    first: dict = {}
    snap = _snapshot(ctx, t, ctx.label(metric))
    rows = ctx.rows(t, ctx.label(metric))
    for r in (_latest_rows(rows, snap) if snap else rows):
        v = _num(r[jm] if jm < len(r) else None)
        k = r[je] if je < len(r) else None
        if v is None or k is None:
            continue
        nk = norm_key(k)
        sums[nk] += v
        first.setdefault(nk, k)
    total = sum(v for v in sums.values() if v > 0)
    if total <= 0 or len(sums) < 2:
        return None
    top = max(sums, key=sums.get)
    share = sums[top] / total
    noun = ctx.noun(entity)
    money = ctx.is_money(metric)
    return {"recipe": f"top_share:{metric}:{entity}", "kind": "concentration",
            "statement": f"{first[top]} is {pct(share)} of {ctx.label(metric)}{_on_latest(snap)} "
                         f"({fmt_value(sums[top], money)}), the largest of {fmt_num(len(sums))} "
                         f"{plural(noun)}{ctx.note(t, ctx.label(metric))}.",
            "numbers": dict({"top": str(first[top]), "share": round(share, 4), "value": sums[top],
                             "n": len(sums)},
                            **({"as_of": snap["latest_iso"], "period_col": snap["col"]} if snap else {})),
            "entity_value": str(first[top]), "entity_role": entity,
            "depends": _dep(ctx, metric, entity), "weight": 3 if share >= 0.3 else 1}


def product_check(ctx, qty, price, total):
    st = ctx.same_table(qty, price, total)
    if not st:
        return None
    t, (jq, jp, jt) = st
    ok = bad = 0
    # the check reads the values as recorded: an owner's unit rule (Price divided by 12) changes
    # what a price means, not whether the file's own arithmetic holds
    for r in ctx.rows(t, ctx.label(total), skip=("scale",)):
        q, p, x = (_num(r[j] if j < len(r) else None) for j in (jq, jp, jt))
        if q is None or p is None or x is None:
            continue
        if abs(q * p - x) <= 0.0051:          # to the cent: keying errors are often a few cents or dollars
            ok += 1
        else:
            bad += 1
    n = ok + bad
    if n < 5:
        return None
    share = ok / n
    note = ctx.note(t, ctx.label(total), skip=("scale",))
    scaled = [r.values.get("col") for r in ctx._applies(t, ctx.label(total))
              if r.kind == "scale" and r.values.get("col") in (ctx.label(qty), ctx.label(price), ctx.label(total))]
    if scaled:
        note += f" ({' and '.join(dict.fromkeys(scaled))} as recorded, before the owner's unit rule)"
    if share < 0.5:
        return {"recipe": f"product_check:{qty}:{price}:{total}", "kind": "check",
                "statement": f"{ctx.label(total)} is not {ctx.label(qty)} times {ctx.label(price)} "
                             f"on most rows ({pct(share)} match){note}.",
                "numbers": {"match": share, "mismatches": bad},
                "depends": _dep(ctx, qty, price, total), "weight": 1}
    return {"recipe": f"product_check:{qty}:{price}:{total}", "kind": "check",
            "statement": f"{ctx.label(total)} equals {ctx.label(qty)} times {ctx.label(price)} on "
                         f"{pct(share)} of rows ({fmt_num(bad)} rows differ){note}.",
            "numbers": {"match": round(share, 4), "mismatches": bad},
            "depends": _dep(ctx, qty, price, total), "weight": 2 if bad else 0,
            "oddity": bad > 0}


def _ruled(ctx, rid, metric: bool = False):
    """(table, the role's column profiled over the ruled rows, the rules note) or None."""
    info = ctx.role(rid)
    if not info:
        return None
    t = ctx.a.table(info["table"])
    m = ctx.label(rid) if metric else None
    return t, ctx.col(t, info["col"].j, m), ctx.note(t, m)


def negatives(ctx, metric):
    got = _ruled(ctx, metric, metric=True)
    if not got:
        return None
    t, col, note = got
    if col.type != "number" or not col.negatives:
        return None
    s = sum(v for v in (_num(r[col.j] if col.j < len(r) else None) for r in ctx.rows(t, ctx.label(metric)))
            if v is not None and v < 0)
    money = ctx.is_money(metric)
    return {"recipe": f"negatives:{metric}", "kind": "gotcha",
            "statement": f"{fmt_num(col.negatives)} rows have a negative {ctx.label(metric)}, "
                         f"totaling {fmt_value(s, money)}{note}.",
            "numbers": {"rows": col.negatives, "sum": s},
            "depends": _dep(ctx, metric), "weight": 2, "oddity": True}


def mixed(ctx, dim):
    got = _ruled(ctx, dim)
    if not got:
        return None
    _t, col, note = got
    if not (2 <= col.distinct <= 50) or not col.count:
        return None
    parts = [f"{k} ({pct(n / col.count)})" for k, n in col.top[:4]]
    more = f" and {col.distinct - 4} more" if col.distinct > 4 else ""
    top_share_v = col.top[0][1] / col.count if col.top else 1
    return {"recipe": f"mixed:{dim}", "kind": "mix",
            "statement": f"{ctx.label(dim)} has {col.distinct} values: {', '.join(parts)}{more}{note}.",
            "numbers": {"distinct": col.distinct, "values": [str(k) for k, _ in col.top[:10]]},
            "depends": _dep(ctx, dim), "weight": 2 if top_share_v < 0.9 else 0,
            "oddity": top_share_v < 0.9}


def date_range(ctx, temporal):
    got = _ruled(ctx, temporal)
    if not got:
        return None
    t, col, note = got
    if col.type != "date" or col.min is None:
        return None
    a, b = col.min, col.max
    months = (b.year - a.year) * 12 + (b.month - a.month) + 1
    # a range whose two ends are one day reads 'on' that day, never 'from X to X'
    said = (f"{ctx.label(temporal)} is {a.isoformat()[:10]} on every row" if a.isoformat()[:10] == b.isoformat()[:10]
            else f"{ctx.label(temporal)} runs from {a.isoformat()[:10]} to {b.isoformat()[:10]} ({months} months)")
    return {"recipe": f"date_range:{temporal}", "kind": "scope",
            "statement": f"{said}{note}.",
            "numbers": {"min": a.isoformat()[:10], "max": b.isoformat()[:10], "months": months, "table": t.tid,
                        "col": col.header},
            "depends": _dep(ctx, temporal), "weight": 0}


def duplicates(ctx, ident):
    """Values of an ID column that appear more than once, when the column is a
    key: nearly one row per value (90% or more), not unique only together with
    another column, and not grouping rows that balance to zero (a journal entry's
    lines). Otherwise the table's grain explains the repeats."""
    got = _ruled(ctx, ident)
    if not got:
        return None
    t, col, note = got
    dup_vals = sum(1 for n in col.counter.values() if n > 1)
    if not dup_vals or (col.count and col.distinct / col.count < 0.9):
        return None
    key = ctx.a.keys.get(t.tid) or []
    exact = not (getattr(ctx.a, "key_extra", None) or {}).get(t.tid)
    entry = ((getattr(ctx.a, "balanced", None) or {}).get(t.tid) or {}).get("col") == col.header
    if (len(key) > 1 and col.header in key and exact) or entry or _groups_balance(ctx, t, col):
        return None
    rows = sum(n for n in col.counter.values() if n > 1)
    return {"recipe": f"duplicates:{ident}", "kind": "gotcha",
            "statement": f"{fmt_num(dup_vals)} {ctx.label(ident)} values appear more than once "
                         f"({fmt_num(rows)} rows){note}.",
            "numbers": {"values": dup_vals, "rows": rows},
            "depends": _dep(ctx, ident), "weight": 2, "oddity": True}


def _groups_balance(ctx, t, col) -> bool:
    """Rows sharing a value of this column net to zero in 90% or more of the
    groups of 2 or more rows (at least 3 groups): debit minus credit where the
    table has both sides, else its money column."""
    from .findings import money_column
    pair = (ctx.det.get("pairs") or {}).get(t.tid)
    if pair and all(h in t.headers for h in pair):
        jd, jc = (t.headers.index(h) for h in pair)
        net = lambda r: (_num(r[jd] if jd < len(r) else None) or 0.0) - (_num(r[jc] if jc < len(r) else None) or 0.0)  # noqa: E731
    else:
        m = money_column(ctx.a, t)
        if m is None:
            return False
        net = lambda r: _num(r[m.j] if m.j < len(r) else None) or 0.0  # noqa: E731
    sums: dict = defaultdict(float)
    sizes: Counter = Counter()
    for r in ctx.rows(t):
        k = norm_key(r[col.j] if col.j < len(r) else None)
        if k is not None:
            sums[k] += net(r)
            sizes[k] += 1
    groups = [k for k, n in sizes.items() if n > 1]
    return len(groups) >= 3 and sum(1 for k in groups if abs(sums[k]) <= 0.005) >= 0.9 * len(groups)


def blank_rate(ctx, rid):
    got = _ruled(ctx, rid)
    if not got:
        return None
    _t, col, note = got
    if col.blank_rate < 0.02:
        return None
    return {"recipe": f"blank_rate:{rid}", "kind": "gotcha",
            "statement": f"{ctx.label(rid)} is blank on {pct(col.blank_rate)} of rows{note}.",
            "numbers": {"blank_rate": round(col.blank_rate, 4)},
            "depends": _dep(ctx, rid), "weight": 1, "oddity": col.blank_rate >= 0.05}


def count_distinct(ctx, rid):
    got = _ruled(ctx, rid)
    if not got:
        return None
    _t, col, note = got
    noun = ctx.noun(rid)
    return {"recipe": f"count_distinct:{rid}", "kind": "scope",
            "statement": f"There are {fmt_num(col.distinct)} distinct {plural(noun, col.distinct)} "
                         f"in {ctx.label(rid)}{note}.",
            "numbers": {"distinct": col.distinct}, "depends": _dep(ctx, rid), "weight": 0}


def spread(ctx, price, item, entity):
    st = ctx.same_table(price, item, entity)
    if not st:
        return None
    t, (jp, ji, je) = st
    qty_info = ctx.role("qty")
    jq = qty_info["col"].j if qty_info and qty_info["table"] == t.tid else None
    # compare within the same period, so ordinary price drift over time is not called a saving: the
    # table's own period column (a snapshot date, or dates on a cycle of a week or more) when it has one,
    # else the month of its date
    jd = None
    for rid2, info2 in ctx.det["roles"].items():
        role2 = ctx.pb.get("roles", {}).get(rid2, {})
        if role2.get("kind") == "temporal" and info2.get("table") == t.tid and info2.get("col") is not None \
                and info2["col"].type == "date":
            jd = info2["col"].j
            break
    per = _period_col(ctx, t)
    if per is not None:
        jd = per.j
    prices: dict = defaultdict(list)
    not_items = getattr(ctx, "not_items", set()) or set()     # fees and holding codes, per the owner
    names = _names(ctx, t, ji)
    # a group whose quantity or price may be in another unit (asked, or answered) is never compared with the rest
    apart = _unit_groups(ctx, t)
    for r in ctx.rows(t, ctx.label(price)):
        p = _num(r[jp] if jp < len(r) else None)
        i = r[ji] if ji < len(r) else None
        if norm_key(i) in not_items:
            continue          # a fee is not a product, so its price is not compared
        if any(j < len(r) and norm_key(r[j]) == k for j, k in apart):
            continue
        e = r[je] if je < len(r) else None
        if p is None or p <= 0 or i is None or e is None:
            continue
        q = _num(r[jq]) if jq is not None and jq < len(r) else None
        if q is not None and q <= 0:
            continue          # credits and returns are not purchase prices
        d = r[jd] if jd is not None and jd < len(r) else None
        month = (d.isoformat()[:10] if per is not None else d.strftime("%Y-%m")) \
            if isinstance(d, (dt.datetime, dt.date)) else ""
        prices[(norm_key(i), month)].append((norm_key(e), p, q))
    flagged = set()
    excess = 0.0
    per_item: dict = defaultdict(float)
    same = True
    for (key, month), lst in prices.items():
        ents = {e for e, _, _ in lst}
        if len(ents) < 2:
            continue
        lo = min(p for _, p, _ in lst)
        hi = max(p for _, p, _ in lst)
        same = same and hi - lo <= 0.005
        if hi > lo * 1.05:
            flagged.add(key)
            if jq is not None:
                x = sum((p - lo) * q for _, p, q in lst if q is not None and q > 0)
                excess += x
                per_item[key] += x
    items = len(flagged)
    if not items:
        return _price_trend(ctx, t, price, item, entity, prices, names, per) if same and jd is not None else None
    noun = ctx.noun(item)
    when = (f" on the same {per.header}" if per is not None else " in the same month") if jd is not None else ""
    top = sorted(flagged, key=lambda k: -per_item.get(k, 0))[:3]
    named = ", ".join(names.get(k, k) for k in top)
    stmt = (f"{fmt_num(items)} {plural(noun, items)} {_spread_verb(ctx, price)} more than 5% apart across "
            f"{plural(ctx.noun(entity))}{when} ({'for example ' if items > 3 else ''}{named}).")
    if jq is not None and excess > 0:
        stmt = stmt[:-1] + f"; paying the lowest price seen would have cost {fmt_money(excess)} less " \
                           f"in this data."
    stmt = stmt[:-1] + ctx.note(t, ctx.label(price)) + "."
    return {"recipe": f"spread:{price}:{item}:{entity}", "kind": "opportunity",
            "statement": stmt, "numbers": {"items": items, "excess": round(excess, 2)},
            "depends": _dep(ctx, price, item, entity), "weight": 3}


def _spread_verb(ctx, price: str) -> str:
    """How a price is said from what the table's roles make it: a pay rate is paid
    at rates, a selling price sold at prices, anything else bought at prices."""
    role = (ctx.pb.get("roles") or {}).get(price) or {}
    words = " ".join([price, str(role.get("label") or ""), ctx.label(price)]).lower()
    if re.search(r"\b(pay|wage|salary|hourly)\b|pay_rate", words):
        return "were paid at rates"
    if (ctx.pb or {}).get("id") in ("sales_transactions", "bookings_events") or re.search(r"\b(sell|selling|sale)\b",
                                                                                          words):
        return "were sold at prices"
    return "were bought at prices"


def _unit_groups(ctx, t) -> list:
    """[(column index, value key)] of groups whose quantity or price may be in
    another unit: a group code found priced unlike the others, or counted in
    another unit, asked about or answered. Their rows are never compared."""
    out = []
    for i in getattr(ctx.a, "_structural", None) or []:
        n = i.get("numbers") or {}
        rec = i.get("recipe", "")
        if n.get("table") != t.tid or n.get("col") not in t.headers:
            continue
        if (rec.startswith("oddgroup:") and n.get("price")) or rec.startswith("unitgroup:"):
            out.append((t.headers.index(n["col"]), norm_key(n.get("value"))))
    return out


def _period_col(ctx, t):
    """A table's own period column: its snapshot date, or a date column whose
    dates follow a cycle of a week or more (a week ending, a pay date). None when
    its dates are days in a log."""
    a = ctx.a
    snap = (getattr(a, "snapshots", None) or {}).get(t.tid)
    if snap:
        return a.col(t.tid, snap["col"])
    for (tid, header), cyc in (getattr(a, "cadence", None) or {}).items():
        c = a.col(tid, header) if tid == t.tid else None
        if c is not None and 2 <= c.distinct <= 0.25 * t.n_rows:
            return c
    return None


def _price_trend(ctx, t, price, item, entity, prices: dict, names: dict, per):
    """Prices the same for every entity within each period, that move from one
    period to the next: a note on the trend, never a saving between entities."""
    series: dict = defaultdict(list)
    for (key, when), lst in prices.items():
        if len({e for e, _, _ in lst}) >= 2 and when:
            series[key].append((when, lst[0][1]))
    if not series:
        return None
    moved = {k: sorted(v) for k, v in series.items() if len({round(p, 2) for _, p in v}) > 1}
    if not moved:
        return None
    noun = ctx.noun(item)
    k0 = max(moved, key=lambda k: (abs(moved[k][-1][1] - moved[k][0][1]), str(k)))
    (w0, p0), (w1, p1) = moved[k0][0], moved[k0][-1]
    period = per.header if per is not None else "month"
    at = "on" if per is not None else "in"
    return {"recipe": f"price_trend:{price}:{item}:{entity}", "kind": "trend",
            "statement": f"{ctx.label(price)} is the same for every {ctx.label(entity)} within each {period} "
                         f"({fmt_num(len(series))} {plural(noun, len(series))} bought by more than one), and "
                         f"changes over time for {fmt_num(len(moved))} of them (for example {names.get(k0, k0)}: "
                         f"${p0:,.2f} {at} {day_words(w0)} to ${p1:,.2f} {at} {day_words(w1)})"
                         f"{ctx.note(t, ctx.label(price))}.",
            "numbers": {"items": len(series), "moved": len(moved), "period": period},
            "depends": _dep(ctx, price, item, entity), "weight": 2}


def _names(ctx, t, ji) -> dict:
    """Item key -> its name, when a column names each item one to one (a description)."""
    head = t.headers[ji] if ji < len(t.headers) else ""
    for fd in ctx.a.fds.get(t.tid, []):
        if fd["from"] == head and re.search(r"desc|name|product|title", fd["to"], re.I):
            jn = t.headers.index(fd["to"])
            out = {}
            for r in t.rows:
                k = norm_key(r[ji] if ji < len(r) else None)
                if k is not None and k not in out and jn < len(r) and r[jn]:
                    out[k] = str(r[jn])[:40]
            return out
    return {}


RECIPES = {
    "pareto": pareto, "top_share": top_share, "product_check": product_check,
    "negatives": negatives, "mixed": mixed, "date_range": date_range,
    "duplicates": duplicates, "blank_rate": blank_rate, "count_distinct": count_distinct,
    "spread": spread,
}


# recipes that add a metric's values up: never across a date where the owner said its unit changed
_SUMS = ("top_share", "pareto", "negatives")


def _mixed_note(ctx, name: str, args: list, res: dict | None = None):
    """In place of a sum of a column the owner said changed unit at a date: a note
    that says so (mixed units before and after that date). None otherwise. res:
    what the recipe found. Negative rows are still counted (what they are is still
    the owner's to say), never totaled; with nothing found there is nothing to say."""
    if name not in _SUMS or not args:
        return None
    info = ctx.role(args[0])
    mixed = getattr(ctx.a, "mixed_units", None) or {}
    when = mixed.get((info["table"], info["header"])) if info else None
    if not when:
        return None
    t = ctx.a.table(info["table"])
    if name == "negatives":
        rows = ((res or {}).get("numbers") or {}).get("rows") or 0
        if not rows:
            return None
        return dict(res, statement=f"{fmt_num(rows)} row{'s' if rows != 1 else ''} {'have' if rows != 1 else 'has'} "
                                   f"a negative {info['header']} on {t.sheet}; not totaled: mixed units before and "
                                   f"after {when}, per the owner.",
                    numbers={"rows": rows, "table": t.tid, "col": info["header"], "mixed_units": when})
    return {"recipe": f"{name}:{':'.join(args)}", "kind": "gotcha",
            "statement": f"{info['header']} on {t.sheet} is not summed here: mixed units before and after {when}, "
                         "per the owner.",
            "numbers": {"table": t.tid, "col": info["header"], "mixed_units": when},
            "depends": _dep(ctx, args[0]), "weight": 1}


def run_all(ctx, specs: list) -> list:
    out = []
    for spec in specs:
        name, *args = spec.split(":")
        fn = RECIPES.get(name)
        if not fn:
            continue
        try:
            res = fn(ctx, *args)
            # a sum across a date where the owner said the unit changed is said as that, never totaled
            res = (_mixed_note(ctx, name, args, res) or res) if res else res
        except (TypeError, ValueError, ZeroDivisionError, AttributeError):
            res = None
        if res:
            # which files the numbers came from: a brain only carries insights from its own file
            files = set()
            for rid in args:
                info = ctx.role(rid)
                if info:
                    files.add(ctx.a.file_of[info["table"]])
            if rid_q := ctx.role("qty"):
                if name == "spread":
                    files.add(ctx.a.file_of[rid_q["table"]])
            res["files"] = sorted(files)
            out.append(res)
    return _main_dates_first(ctx, out)


def _main_dates_first(ctx, out: list) -> list:
    """The span of the data is the main table's own date column (the pay or
    transaction date), never a hire or birth date on a list beside it: that date
    range comes first among the date ranges, whatever order the playbook names
    them in. Nothing else moves."""
    at = [i for i, x in enumerate(out) if x["recipe"].startswith("date_range:")]
    main = ctx.a.main_table
    if len(at) < 2 or main is None:
        return out
    aj = ctx.a._axis_j(main)
    axis = main.headers[aj] if aj is not None and aj < len(main.headers) else None

    def rank(x):
        n = x.get("numbers") or {}
        return (n.get("table") != main.tid, n.get("col") != axis)
    ranges = sorted((out[i] for i in at), key=rank)          # sorted() keeps the playbook's order on a tie
    out = list(out)
    for i, x in zip(at, ranges):
        out[i] = x
    return out


def today() -> str:
    return dt.date.today().isoformat()
