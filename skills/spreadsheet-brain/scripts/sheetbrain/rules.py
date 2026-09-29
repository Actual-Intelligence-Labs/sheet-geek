"""The owner's rules as one model, applied to every counted number.

A Rule names one table, the rows it touches (a conjunction of column
conditions: exact values, an ID prefix or a date window), what it does to them
(exclude, filter, map, scale or pair), which totals it covers (all of them, or
named metrics only), the answer it came from, and whether the owner confirmed it.
Only a confirmed rule changes a number.

A rule is confirmed in three ways: a pick on a question about exact values
("Internal entries" for Q7); a typed answer to a question about what belongs
in the totals that names values in exactly one column; or a tick on the
readback, where the owner sees each rule a typed sentence proposed, with its
rows and money. Until then a proposed rule changes nothing, and every number it
would change says so. "Q7 transfers never count" said about the totals leaves
out Location = Q7; "keep Q7" and "don't leave Q7 out" propose nothing.

A rule verb counts only as an instruction: 'drop them', 'should be removed',
never a word that says what a code is ('3 = dropped or broken'). In 'leave X
out of Y' the rows are X and Y is the totals it covers: a value named in Y is
never a row. When Y (or the sentence's subject, 'The basis is ...: fees
excluded') is one calculation ('rebate math', 'price comparisons'), the rule
is kept for that calculation only and changes no count or total here.
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field

from .profile import _is_num as _num
from .profile import norm_key

# free-text rules apply without a readback only from questions about what belongs in the totals:
# "fees and Q7 don't count" said about rebates is a rebate rule, not a reason to drop fees from spend
_KINDS = {"exclusion", "scope", "coverage"}

# what a total is a total of, when the owner says rows are not it ('escrow money, not income', 'not
# applied to the unit balance'): the rows the words before name leave that measure
_MEASURE_NOUN = (r"(?:income|revenue|sales|spend(?:ing)?|expenses?|costs?|loss(?:es)?|balances?|collections?|"
                 r"payroll|labou?r\s+cost)")
_NOT_MEASURE = (r"not\s+(?!sure\b|certain\b|clear\b|yet\b|only\b|just\b)(?:(?:get|be|been|being)\s+)?"
                r"(?:applied\s+to\s+)?(?:(?:a|an|the|our|any|their|[\w'-]+'s)\s+)?"
                r"(?:[\w'-]+\s+(?:or\s+)?){0,3}?" + _MEASURE_NOUN + r"\b")
# the plain-English verbs that make a typed sentence a rule, by what the rule would do. drop and remove
# also have an everyday sense, so they count only as an instruction (see _instruction)
_VERBS = (
    ("exclude", r"leav(?:e|es|ing)\s+(?:[\w'&/.-]+\s+){0,6}?out\b|left\s+out|"
                r"tak(?:e|es|ing)\s+(?:[\w'&/.-]+\s+){1,6}?out\b|taken\s+out\b|"
                r"take\s+out\s+(?=(?:of|the|these|those|them|all|any|every|code|codes)\b)|exclud(?:e|es|ed|ing)|"
                r"ignor(?:e|es|ed|ing)|(?:do|does|did|should|must)(?:n'?t|\s+not)\s+count|not\s+count(?:ed)?|"
                r"never\s+count(?:s|ed)?|not\s+part\s+of|not\s+ours|remov(?:es|ed|e)|drop(?:ped|s)?|"
                r"double.?count(?:s|ed)?|(?:stay|stays|stayed|kept)\s+out(?=\s+(?:of|from)\b)|"
                r"keep\s+(?:[\w'&/.-]+\s+){1,6}?out(?=\s+(?:of|from)\b)|" + _NOT_MEASURE),
    ("map", r"combin(?:e|es|ed)|merg(?:e|es|ed)|(?:is|are)\s+(?:all\s+)?the\s+same\b|same\s+as|"
            r"same\s+(?:[\w'-]+\s+){1,2}?as\b|count(?:ed)?\s+(?:them\s+)?together"),
    ("scale", r"divided\s+by\s+\d+(?:\.\d+)?|per\s+case\s+of\s+\d+"),
    ("keep", r"keep\s+the\s+(?:later|earlier|newer|older|latest|earliest|newest|oldest)"),
    ("filter", r"only\s+the\s+[\w'-]+(?:\s+[\w'-]+)?\s+one\s+is\s+real"),
)
RULE_VERB = re.compile(r"\b(?:" + "|".join(p for _, p in _VERBS) + r")", re.I)
_FAMILY = [(kind, re.compile(r"\b(?:" + p + r")", re.I)) for kind, p in _VERBS]
# verbs whose rows come before them ("Q7 never counts", "A1 and A2 are the same store"): their
# clause starts at the last break after the verb before, not at the verb
_AFTER = re.compile(r"(?:(?:do|does|did|should|must)(?:n'?t|\s+not)|not|never)\s+count|not\s+part\s+of|not\s+ours|"
                    r"double.?count|left\s+out|(?:is|are)\s+(?:all\s+)?the\s+same\b|same\s+as|"
                    r"same\s+(?:[\w'-]+\s+){1,2}?as\b|count(?:ed)?\s+(?:them\s+)?together|divided\s+by|per\s+case\s+of|"
                    r"(?:stay|stays|stayed|kept)\s+out(?=\s+(?:of|from)\b)|" + _NOT_MEASURE, re.I)
_NOT_MEASURE_RE = re.compile(_NOT_MEASURE, re.I)
# money said as money ('loss dollars', 'the dollar amounts'): the rule covers the money column, never the
# counts of rows
_DOLLARS = re.compile(r"\b(?:dollars?|money|amounts?)\b|\$", re.I)
# a clause that keeps rows in ('count them in spend', 'keep them in totals'): a leave-out said with it never
# leaves the same rows out of every count and total
_KEEP_CLAUSE = re.compile(r"\b(?:counts?|counted)\s+(?:[\w'&/.-]+\s+){0,3}?(?:in|toward|towards)\b|"
                          r"\b(?:keep|kept|stay|stays)\s+(?:them\s+|it\s+)?in\b", re.I)
# an ID prefix the owner names ('GV- lines', 'SKUs starting with GV-', 'item numbers starting with 7')
_PREFIX_TOKEN = re.compile(r"(?<![\w-])([A-Za-z][A-Za-z0-9]{0,7}-)(?=[\s,.;:)\"']|$)")
_PREFIX_SAID = re.compile(r"\b(?:start(?:s|ing)?|begin(?:s|ning)?)\s+with\s+[\"'‘“]?([A-Za-z0-9]{1,8}-?)", re.I)
# a condition on the rows a unit or leave-out rule is about ('on cellar lines', 'for imported items'): when
# its words name no value code can find, the rule is never offered without it
_COND_PHRASE = re.compile(r"\b(?:on|for|of)\s+(?:the\s+|all\s+|our\s+|any\s+|every\s+)?"
                          r"([A-Za-z][\w&/'-]*(?:\s+[A-Za-z][\w&/'-]*)?)\s+"
                          r"(?:lines?|items?|rows?|products?|entries|orders|skus?)\b"
                          r"(?=\s*(?:[,.;:)!?]|$|(?:is|are|was|were|get|gets|go|goes|come|comes|should|must|and|or|"
                          r"but|so|only|out|from|in|on|at|to|with|have|has|need|needs)\b))", re.I)
_BREAK = re.compile(r"[;:]\s*|,?\s*\b(?:but|while|whereas|then|also|so)\b\s*", re.I)   # never a list comma
# a rule verb the owner argued against: "don't leave Q7 out", "never ignore Q7", "keep Q7, do not drop
# it". 'count' is no target: "never counts" is itself the rule
_NEGATED = re.compile(r"\b(?:don'?t|do\s+not|does\s+not|doesn'?t|should\s*n'?o?t|never|not|no\s+need\s+to|keep|"
                      r"include|including)\b(?:\s+(?!(?:and|but|or|then)\b)[\w'&/.-]+){0,4}?\s+"
                      r"(?:leav|left|exclud|ignor|drop|remov|combin|merg|tak)", re.I)
_FACTOR = re.compile(r"\b(?:divided\s+by|per\s+case\s+of)\s+(\d+(?:\.\d+)?)", re.I)
_RATE = re.compile(r"%|\b(pct|percent|rate|per)\b", re.I)
# what may come right before an order ('drop them', 'we remove the test rows', 'should drop')
_ORDER_BEFORE = re.compile(r"(?:^|[.;:,!?(\"'-]|\b(?:and|then|so|but|please|pls|just|also|always|to|should|must|"
                           r"can|could|would|will|shall|we|i|you|let's|lets)\b)\W*$", re.I)
# where the words naming the totals a rule covers begin: 'from', 'out of', 'toward', or 'in', 'for',
# 'on' before a word for a total ('in rebate math')
_SCOPE_AT = re.compile(r"\b(?:out\s+of|from|toward|towards)\b", re.I)
_MEASURE = (r"(?:totals?|sums?|counts?|math|comparisons?|basis|spend(?:ing)?|calculations?|reports?|numbers|"
            r"figures|averages?|rates?|ratios?|dollars|revenue|sales|income|expenses?|costs?|margins?|balances?)")
_SCOPE_IN = re.compile(r"\b(?:in|for|on|of)\s+(?=(?:[\w'&/%.-]+\s+){0,4}?" + _MEASURE + r"\b)", re.I)
# one calculation, not every count and total: a rule said about it is kept for it alone
_CALC = re.compile(r"\b(?:math|comparisons?|compar(?:e|ing)|basis|calculations?|averages?|rates?|ratios?|"
                   r"benchmarks?|percentages?|margins?|rebates?|per[- ]unit|unit\s+(?:prices?|costs?))\b", re.I)
_LEAD_SCOPE = re.compile(r"^\W*(?:for|in|when\s+\w+ing)\s+([^,;:]{3,60}?)\s*,\s*", re.I)
_SUBJECT = re.compile(r"^\W*((?:the|our|a|an)\s+)?((?:[\w%&'-]+\s+){0,3}?[\w%&'-]+)\s+(?:is|are|was|were|"
                      r"excludes?|leaves?|uses?|means?|counts?|should|must|never|does|do)\b", re.I)
# words that stand for rows named earlier in the sentence ('Fees count in spend; leave them out of ...')
_PRONOUN = re.compile(r"\b(?:it|them|those|these|that|this|they)\b", re.I)
# a sentence that opens by pointing back ('They are not fee income', 'Those go to the escrow account')
ANAPHOR_START = re.compile(r"^\W*(?:they|them|those|these|it|this|that|both|such)\b", re.I)
# values listed with 'and' or a comma are each their own rule, never one rule for rows that have both
_LIST_GAP = re.compile(r",|&|\b(?:and|or|nor|plus|as\s+well\s+as)\b", re.I)
# a rule that takes more than this share of its table's money (or rows) out is never recommended
HEAVY = 0.25

KINDS = ("exclude", "filter", "map", "scale", "pair", "dedupe", "fill", "unit", "adjust")
# rule readbacks and just-in-time questions per workbook together, outside the question cap
MAX_READBACKS = 2


@dataclass
class Rule:
    """One owner rule on one table. predicate: [{col, op: in | prefix | between |
    blank, values}], all of which must hold. values: for map {col, to: {key: label}},
    for scale {col, by}, for pair {match: [columns that must agree]}, for dedupe
    {col, keep: first | last} (one row per value of col, the first or last in
    the file), for fill {col} (each row it names takes the last value of col
    above it), for adjust {col, minus} (col less the minus column on the rows
    it names). scope: [] for every count and total, the metric columns (or a
    measure worked out for the table, like 'Net') it
    covers, or one calculation in the owner's words ('rebate math'): kept for
    that calculation, it changes no count or total counted here."""
    kind: str
    table: str
    predicate: list = field(default_factory=list)
    values: dict = field(default_factory=dict)
    scope: list = field(default_factory=list)
    source: str = ""
    confirmed: bool = False

    def __post_init__(self):
        self._sets = [{norm_key(v) for v in c.get("values") or []} for c in self.predicate]

    def key(self) -> tuple:
        conds = tuple(sorted((c["col"], c.get("op", "in"), tuple(sorted(str(norm_key(v)) for v in c.get("values") or [])))
                             for c in self.predicate))
        vals = tuple(sorted((k, str(v)) for k, v in self.values.items() if k != "to"))
        to = tuple(sorted((self.values.get("to") or {}).keys()))
        return (self.kind, self.table, conds, vals, to)

    def to_dict(self) -> dict:
        return {"kind": self.kind, "table": self.table, "predicate": copy.deepcopy(self.predicate),
                "values": copy.deepcopy(self.values), "scope": list(self.scope), "source": self.source,
                "confirmed": self.confirmed}

    @classmethod
    def from_dict(cls, d: dict) -> "Rule":
        return cls(d.get("kind", ""), d.get("table", ""), list(d.get("predicate") or []), dict(d.get("values") or {}),
                   list(d.get("scope") or []), d.get("source", ""), bool(d.get("confirmed")))

    def covers(self, metric=None) -> bool:
        """A rule scoped to named metrics changes only those totals."""
        return not self.scope or (metric is not None and metric in self.scope)

    def cols(self) -> list:
        """Every column the rule reads or changes."""
        out = [c["col"] for c in self.predicate]
        if self.values.get("col"):
            out.append(self.values["col"])
        if self.values.get("minus"):
            out.append(self.values["minus"])
        return list(dict.fromkeys(out))

    def valid(self, idx: dict) -> bool:
        return self.kind in KINDS and all(h in idx for h in self.cols())

    def matches(self, row, idx: dict) -> bool:
        for c, keys in zip(self.predicate, self._sets):
            j = idx[c["col"]]
            v = row[j] if j < len(row) else None
            op = c.get("op", "in")
            if op == "in" and norm_key(v) not in keys:
                return False
            if op == "prefix":
                w = _written(v)           # a code written as a number ('6012') has a prefix too ('6')
                if not (w and any(w.upper().startswith(str(p).upper()) for p in c.get("values") or [])):
                    return False
            if op == "blank" and not (v is None or (isinstance(v, str) and not v.strip())):
                return False
            if op == "between":
                # ISO bounds; a bound with a time of day ('2024-09-04T08:26:00') compares the time too
                lo, hi = (list(c.get("values") or []) + ["", ""])[:2]
                d = v.isoformat()[:max(10, len(lo), len(hi))] if hasattr(v, "year") else None
                if d is None or (lo and d < lo) or (hi and d > hi):
                    return False
        return True


def apply(rules: list, t, cols=None, metric=None) -> list:
    """[(position in t.rows, row)] under the confirmed rules of this table that
    cover the metric, in order: filters and exclusions, then repeats (one row
    per value kept), then fills (a blank takes the value above it), then maps
    (values made one before any grouping), then adjustments (a column less
    another on the rows named), then scales (a column divided before any sum),
    then pairs (a row the rule names and its twin both removed)."""
    idx = {h: j for j, h in enumerate(t.headers)}
    mine = [r for r in rules or [] if r.confirmed and r.table == t.tid and r.covers(metric) and r.valid(idx)]
    out = list(enumerate(t.rows))
    if not mine:
        return out
    for r in mine:
        if r.kind == "exclude":
            out = [(i, row) for i, row in out if not r.matches(row, idx)]
        elif r.kind == "filter":
            out = [(i, row) for i, row in out if r.matches(row, idx)]
    for r in mine:
        if r.kind == "dedupe":
            out = _keep_one(r, out, idx)
    for r in mine:
        if r.kind == "fill":
            out = _fill_down(r, out, idx)
    for r in mine:
        if r.kind == "map":
            j, to = idx[r.values["col"]], {str(k): v for k, v in (r.values.get("to") or {}).items()}
            out = [(i, _set(row, j, to[str(norm_key(row[j]))])
                    if j < len(row) and str(norm_key(row[j])) in to and r.matches(row, idx) else row)
                   for i, row in out]
    for r in mine:
        if r.kind == "adjust" and r.values.get("minus") in idx:
            j, jm = idx[r.values["col"]], idx[r.values["minus"]]
            out = [(i, _set(row, j, row[j] - row[jm]) if max(j, jm) < len(row) and _num(row[j]) and _num(row[jm])
                    and r.matches(row, idx) else row) for i, row in out]
    for r in mine:
        if r.kind == "scale" and float(r.values.get("by") or 0):
            j, by = idx[r.values["col"]], float(r.values["by"])
            out = [(i, _set(row, j, row[j] / by)
                    if j < len(row) and _num(row[j]) and r.matches(row, idx) else row) for i, row in out]
    for r in mine:
        if r.kind == "pair":
            out = _drop_pairs(r, out, idx, cols or [])
    return out


def _set(row, j: int, v):
    row = list(row)
    row[j] = v
    return row


def _keep_one(r: Rule, rows: list, idx: dict) -> list:
    """One row per value of the rule's column among the rows it names: the first
    in the file, or the last. Blanks and rows it does not name all stay."""
    j, last = idx[r.values["col"]], r.values.get("keep") == "last"
    seen, kept = set(), []
    for i, row in (reversed(rows) if last else rows):
        k = norm_key(row[j] if j < len(row) else None)
        if k is None or not r.matches(row, idx) or k not in seen:
            kept.append((i, row))
            if k is not None and r.matches(row, idx):
                seen.add(k)
    return kept[::-1] if last else kept


def _fill_down(r: Rule, rows: list, idx: dict) -> list:
    """Each row the rule names takes the last value of its column in the rows
    above it; a row with nothing above it stays as it is."""
    j, last, out = idx[r.values["col"]], None, []
    for i, row in rows:
        v = row[j] if j < len(row) else None
        if r.matches(row, idx):
            out.append((i, _set(row, j, last) if last is not None else row))
            continue
        if not (v is None or (isinstance(v, str) and not v.strip())):
            last = v
        out.append((i, row))
    return out


def _drop_pairs(r: Rule, rows: list, idx: dict, cols: list) -> list:
    """The rows the rule names, each with one twin: a row it does not name that
    agrees on the matching columns (the rule's, else the table's ID columns) and
    carries the same amounts, sign aside. Both go; a row with no twin stays."""
    named = {c["col"] for c in r.predicate}
    match = [h for h in r.values.get("match") or [] if h in idx]
    if not match:
        match = [c.header for c in cols if c.semantic == "identifier" and c.header not in named]
    if not match:
        match = [c.header for c in cols if c.type == "text" and c.header not in named]
    money = [c.j for c in cols if c.type == "number" and c.semantic == "metric" and c.header not in named]
    if not match:
        return rows

    def key(row):
        return tuple(norm_key(row[idx[h]] if idx[h] < len(row) else None) for h in match) + tuple(
            round(abs(row[j]), 2) if j < len(row) and _num(row[j]) else None for j in money)
    free: dict = {}
    for n, (_i, row) in enumerate(rows):
        if not r.matches(row, idx):
            free.setdefault(key(row), []).append(n)
    drop = set()
    for n, (_i, row) in enumerate(rows):
        if r.matches(row, idx):
            twins = free.get(key(row)) or []
            if twins:
                drop |= {n, twins.pop(0)}
    return [x for n, x in enumerate(rows) if n not in drop]


# --------------------------------------------------------------------------
# where rules come from
# --------------------------------------------------------------------------
def confirmed(analysis, answers: dict) -> list:
    """Every rule the owner confirmed, once each: picks on questions about exact
    values, typed exclusion answers that name values in one column, readback
    ticks (an option the owner's typed words restated, ticked on the readback,
    counts as picked on its own question), a map a typed answer pairs value by
    value, and a unit rule's number typed with its pick. Then what they imply on
    their own table: a leave-out covers every spelling a confirmed map made one
    ('Cedar Gap' with CDG), leave-outs that take the same rows out are one rule
    out of every total either named ('every count and total' wins), and a unit
    rule on a price divides every column worked out from it (an amount that is
    quantity times that price). A pick that says what a record is ('not ours',
    'a test record') leaves it out of every count and total, on every tab its
    ID or value joins at 95% or more."""
    answers = with_inferred(answers)
    out: dict = {}
    measures: dict = {}
    for a in (answers or {}).values():
        d = a.get("derive") if isinstance(a, dict) else None
        if d and d.get("table") and d.get("name") and set(a.get("options") or []) & {"yes", "gross"}:
            measures.setdefault(d["table"], set()).add(str(d["name"]))
    try:
        analysis._confirmed_measures = measures
    except Exception:  # noqa: BLE001
        pass

    def add(rule):
        k = ident(analysis, rule)
        got = out.get(k)
        if got is None:
            out[k] = rule
        elif rule.kind in ("exclude", "filter"):
            # the same rows out of two totals ('Leave out of Sales totals' and 'Leave out of Discount
            # totals'): one rule, out of both; out of every count and total when either says so
            out[k] = Rule(got.kind, got.table, got.predicate, dict(got.values), _union_scope(got.scope, rule.scope),
                          got.source, got.confirmed)
    for qid, a in (answers or {}).items():
        ex = a.get("exclude") if isinstance(a, dict) else None
        if not ex or not set(a.get("options") or []) & set(ex.get("options") or []):
            continue
        if analysis.col(ex["table"], ex["col"]) is not None:   # the owner picked "internal" on these values
            # scope: the totals the pick is about (opening balances leave the period's flows, not its levels);
            # words the owner typed with the pick that say every total ('leave K4 out of every group
            # number') widen it back to what they said, and so does a pick that says what the record is
            scope = list(ex.get("scope") or [])
            identity = _identity_pick(a, ex)
            if scope and (identity or _typed_every(analysis, qid, a, ex["table"], ex["col"], ex["values"])):
                scope = []
            vals = [str(v) for v in ex["values"]]
            add(Rule("exclude", ex["table"], [{"col": ex["col"], "op": "in", "values": vals}],
                     scope=scope, source=qid, confirmed=True))
            if identity:
                for tid2, col2, got_vals in _joined_tabs(analysis, ex["table"], ex["col"], vals):
                    add(Rule("exclude", tid2, [{"col": col2, "op": "in", "values": got_vals}], source=qid,
                             confirmed=True))
    for qid, a in (answers or {}).items():
        if not isinstance(a, dict) or qid.startswith("_") or qid == "goal":
            continue
        if not (a.get("kind") in _KINDS or re.search(r"spend|exclud|leave_out", qid)):
            continue
        # only a clause whose verb leaves rows out, never one the owner argued against, and only
        # values written as they are in the data ('the other store' is not the value Other)
        # a rule said about one calculation ('fees stay out of rebate math') is never every total
        found = [c for s in _said(analysis, a.get("text", "")) for fam, part, negated, verb in _clauses(s)
                 if fam == "exclude" and not negated
                 for c in _from_sentence(analysis, part, qid, family=fam, said=s, exact=True, verb=verb)
                 if len(c["rule"].predicate) == 1 and not c.get("scope_words")
                 and c["rule"].predicate[0].get("op", "in") == "in"]
        places = {(c["rule"].table, c["rule"].predicate[0]["col"]) for c in found}
        if len(places) == 1:          # one column named: applied as the owner said, as always
            (tid, col), = places
            vals = list(dict.fromkeys(v for c in found for v in c["rule"].predicate[0]["values"]))
            scope: list = list(found[0]["rule"].scope)
            for c in found[1:]:
                scope = _union_scope(scope, c["rule"].scope)
            add(Rule("exclude", tid, [{"col": col, "op": "in", "values": vals}], scope=scope, source=qid,
                     confirmed=True))
    from .findings import READBACK
    for qid, a in (answers or {}).items():
        for d in (a.get("rules") or []) if isinstance(a, dict) else []:
            if d.get("infer") or d.get("kind") not in KINDS:
                continue                  # a readback line that ticks an option of another question
            if d.get("confirmed"):
                r = Rule.from_dict(dict(d, confirmed=True))
                # a pick scoped to one total, with words typed beside it that say every total for the same
                # values ('leave Q2 out of every group number'): what the owner typed wins
                one = r.kind in ("exclude", "filter") and len(r.predicate) == 1 \
                    and r.predicate[0].get("op", "in") == "in"
                if one and r.scope and not qid.startswith(READBACK) and not word_scope(analysis, r) \
                        and _typed_every(analysis, qid, a, r.table, r.predicate[0]["col"], r.predicate[0]["values"]):
                    r.scope = []
                add(r)
            elif _typed_map(qid, a, d):
                add(Rule.from_dict(dict(d, confirmed=True, source=d.get("source") or qid)))
    for qid, a in (answers or {}).items():
        # a price for a pack or case: divided by the number the owner typed with that pick, never a guess
        sc = a.get("scale") if isinstance(a, dict) else None
        by = typed_count(a.get("text") or "") if sc and sc.get("option") in (a.get("options") or []) else 0.0
        if by and analysis.col(sc["table"], sc["col"]) is not None:
            add(Rule("scale", sc["table"], list(sc.get("predicate") or []), {"col": sc["col"], "by": by},
                     source=qid, confirmed=True))
            # the same price on another tab the question named (the same items through their key)
            for extra in sc.get("also") or []:
                if extra.get("table") and extra.get("col") and analysis.col(extra["table"], extra["col"]) is not None:
                    add(Rule("scale", extra["table"], list(extra.get("predicate") or []),
                             {"col": extra["col"], "by": by}, source=qid, confirmed=True))
    got = _merge_same_rows(analysis, _expand_maps(analysis, list(out.values())))
    return got + _derived_scales(analysis, got)


def _union_scope(a: list, b: list) -> list:
    """Two scopes of one rule: every count and total when either is, else both."""
    if not a or not b:
        return []
    return list(dict.fromkeys(list(a) + list(b)))


# a pick that says what a record is, not only which totals it leaves: it is out of every count and total
_IDENTITY = re.compile(r"\bnot\s+ours\b|\bnot\s+one\s+of\s+ours\b|\btest\s+(?:record|row|entr(?:y|ies)|account|id)\b|"
                       r"\bnever\s+real\b|\bnot\s+real\b|\bnot\s+money\s+owed\b|"
                       r"\bnot\s+(?:a\s+)?part\s+of\s+(?:us|the\s+business)\b|\bsomeone\s+else'?s\b", re.I)


def _identity_pick(a: dict, ex: dict) -> bool:
    """The owner picked an option that says what the record is ('Not ours', 'A test
    record, never real'): the question may have named one measure, the answer
    leaves the record out of every count and total."""
    if ex.get("identity"):
        return True
    labs = picked_labels(a)
    return any(_IDENTITY.search(str(labs.get(o) or "")) for o in ex.get("options") or [] if o in labs)


def picked_labels(a: dict) -> dict:
    """{option id: label} of an answer's picks, in the order the question showed them."""
    opts = [o for o in a.get("options") or [] if o != "not_sure"]
    labels = list(a.get("labels") or [])
    descs = a.get("descs") or {}
    order = [k for k in descs if k in opts]
    if len(order) != len(labels):
        order = opts
    return dict(zip(order, labels))


def _joined_tabs(analysis, tid: str, col: str, values: list) -> list:
    """[(table, column, [values as written there])] for every other table whose
    column joins this one on 95% or more of rows and holds any of the values."""
    out, seen = [], {tid}
    keys = {norm_key(v) for v in values}
    for j in getattr(analysis, "joins", None) or []:
        if j.get("rows_matched", 0) < 0.95:
            continue
        if (j.get("from_table"), j.get("from_col")) == (tid, col):
            other, oc = j.get("to_table"), j.get("to_col")
        elif (j.get("to_table"), j.get("to_col")) == (tid, col):
            other, oc = j.get("from_table"), j.get("from_col")
        else:
            continue
        ot = next((t for t in analysis.tables if t.tid == other), None)
        if ot is None or other in seen or oc not in ot.headers or not _usable(analysis, ot):
            continue
        seen.add(other)
        jj = ot.headers.index(oc)
        got = list(dict.fromkeys(_written(r[jj]) or str(r[jj]) for r in ot.rows
                                 if jj < len(r) and norm_key(r[jj]) in keys))
        if got:
            out.append((other, oc, got))
    return out


def rows_of(analysis, r: Rule) -> frozenset:
    """The positions in its table's rows that a rule's conditions name (cached)."""
    memo = getattr(analysis, "_rows_of_memo", None)
    if memo is None:
        memo = {}
        try:
            analysis._rows_of_memo = memo
        except Exception:  # noqa: BLE001
            pass
    k = (r.table, r.key()[2])
    if k not in memo:
        t = next((t for t in analysis.tables if t.tid == r.table), None)
        idx = {h: j for j, h in enumerate(t.headers)} if t is not None else {}
        ok = t is not None and all(c["col"] in idx for c in r.predicate)
        memo[k] = frozenset(i for i, row in enumerate(t.rows) if r.matches(row, idx)) if ok else frozenset()
    return memo[k]


def _merge_same_rows(analysis, rules: list) -> list:
    """Confirmed leave-outs that take exactly the same rows out of one table are one
    rule: out of every total either named, or out of every count and total when
    either is ('every count and total' wins). A rule kept for one calculation in
    the owner's words stays its own, unless a rule for every count and total
    takes the same rows (it then says nothing more)."""
    excl = [r for r in rules if r.kind == "exclude" and r.confirmed and r.predicate]
    by_table: dict = {}
    for r in excl:
        by_table.setdefault(r.table, []).append(r)
    if not any(len(v) > 1 for v in by_table.values()):
        return rules
    drop, merged = set(), {}
    for tid, rs in by_table.items():
        if len(rs) < 2:
            continue
        groups: dict = {}
        for r in rs:
            rows = rows_of(analysis, r)
            if rows:
                groups.setdefault(rows, []).append(r)
        for g in groups.values():
            if len(g) < 2:
                continue
            calc = [r for r in g if word_scope(analysis, r)]
            plain = [r for r in g if not word_scope(analysis, r)]
            if any(not r.scope for r in plain):
                drop |= {id(r) for r in calc}
            if len(plain) < 2:
                continue
            first = min(plain, key=lambda r: (any(c.get("op", "in") != "in" for c in r.predicate), rules.index(r)))
            scope = list(plain[0].scope)
            for r in plain[1:]:
                scope = _union_scope(scope, r.scope)
            merged[id(first)] = Rule(first.kind, first.table, first.predicate, dict(first.values), scope,
                                     first.source, True)
            drop |= {id(r) for r in plain if r is not first}
    return [merged.get(id(r), r) for r in rules if id(r) not in drop]


def inferred_ticks(answers: dict) -> dict:
    """{question id: [(option id, readback id, what the readback showed)]} for the
    options the owner's typed words restated on their own question and the owner
    then ticked on a rules readback. A readback carries such a line as meta
    'infer' {option: {qid, option, label, desc}} or as a rule whose 'infer' names
    them; only a tick counts, never the words alone."""
    from .findings import READBACK
    out: dict = {}
    for k, a in (answers or {}).items():
        if not isinstance(a, dict) or not k.startswith(READBACK):
            continue
        picked = set(a.get("options") or [])
        for oid, inf in (a.get("infer") or {}).items():
            if oid in picked and isinstance(inf, dict) and inf.get("qid") and inf.get("option"):
                out.setdefault(inf["qid"], []).append((inf["option"], k, inf))
        for d in a.get("rules") or []:
            inf = d.get("infer")
            if isinstance(inf, dict) and d.get("confirmed") and inf.get("qid") and inf.get("option"):
                out.setdefault(inf["qid"], []).append((inf["option"], k, inf))
    return out


def with_inferred(answers: dict) -> dict:
    """The answers with each ticked readback line recorded as a pick on the question
    it came from (inferred_by names the readback): its rule applies as a pick's
    would, and its note says the owner ticked it."""
    ticks = inferred_ticks(answers)
    if not ticks:
        return answers
    out = dict(answers)
    for qid, got in ticks.items():
        a = out.get(qid)
        if not isinstance(a, dict):
            continue
        a = dict(a)
        opts = [o for o in a.get("options") or [] if o != "not_sure"]
        labels = list(a.get("labels") or [])
        descs = dict(a.get("descs") or {})
        for oid, _rb, inf in got:
            if oid in opts:
                continue
            opts.append(oid)
            if inf.get("label"):
                labels.append(str(inf["label"]))
            descs[oid] = str(inf.get("desc") or "")
        a.update(options=opts, labels=labels, descs=descs, not_sure=False, inferred_by=got[0][1],
                 ticked=[oid for oid, _rb, _i in got])
        if a.get("rules"):
            a["rules"] = [dict(d, confirmed=bool(d.get("confirmed")) or d.get("option") in opts) for d in a["rules"]]
        out[qid] = a
    return out


def _typed_every(analysis, qid: str, a: dict, tid: str, col: str, values: list) -> bool:
    """The owner's words with a narrower pick say every total for the same values
    ('a (leave K4 out) plus: ... leave K4 out of every group figure')."""
    text = a.get("text") or ""
    if not text or not re.search(r"\b(?:every|all|any)\b", text, re.I):
        return False
    want = {norm_key(v) for v in values}
    for s in _said(analysis, text):
        for fam, part, negated, verb in _clauses(s):
            if fam != "exclude" or negated:
                continue
            t = next((x for x in analysis.tables if x.tid == tid), None)
            if t is None:
                continue
            _m, words, at, _l = _scope_of(analysis, t, part, verb, "")
            if words or at >= len(part) or not re.search(r"\b(?:every|all|any)\b", part[at:], re.I):
                continue
            for c in _from_sentence(analysis, part, qid, family=fam, said=s, verb=verb, sentence=s):
                r = c["rule"]
                if r.table == tid and not r.scope and len(r.predicate) == 1 and r.predicate[0]["col"] == col \
                        and want <= {norm_key(v) for v in r.predicate[0]["values"]}:
                    return True
    return False


_NOT_SAME = re.compile(r"\b(?:not\s+the\s+same|different|separate|apart|distinct|except|but\s+not|no\b)", re.I)


def _typed_map(qid: str, a: dict, d: dict) -> bool:
    """A map a question offered, confirmed by typed words instead of a pick: the
    owner's text names every pair the rule makes one ('Ashford Mill is AFM,
    Brook Hollow is BKH, ...') and says none of them differ. Never on a readback,
    and never next to a pick that says otherwise."""
    from .findings import READBACK
    if qid.startswith(READBACK) or d.get("kind") != "map" or not isinstance(a, dict):
        return False
    text = a.get("text") or ""
    picked = [o for o in a.get("options") or [] if o != "not_sure"]
    if not text.strip() or (picked and picked != [d.get("option")]) or _NOT_SAME.search(text):
        return False
    to = (d.get("values") or {}).get("to") or {}
    vals = [str(v) for c in d.get("predicate") or [] for v in c.get("values") or []]
    groups: dict = {}
    for v in vals:
        lab = to.get(str(norm_key(v)))
        if lab is not None:
            groups.setdefault(str(lab), set()).add(v)
    for lab, members in groups.items():
        members = set(members) | {lab}
        if len({norm_key(m) for m in members}) < 2:
            continue
        if not all(_value_pattern(m).search(text) or re.search(r"(?<![\w-])" + re.escape(m) + r"(?![\w-])", text, re.I)
                   for m in members):
            return False
    return len(groups) > 0


def _expand_maps(analysis, rules: list) -> list:
    """Leave-outs cover every spelling a confirmed map made one: 'CDG' left out takes
    the old rows written 'Cedar Gap' with it. Only a confirmed map widens a
    leave-out, never one the owner has not ticked."""
    maps = [r for r in rules if r.kind == "map" and r.confirmed and r.values.get("col")]
    if not maps:
        return rules
    out = []
    for r in rules:
        if r.kind not in ("exclude", "filter") or not r.confirmed:
            out.append(r)
            continue
        preds, changed = [], False
        for c in r.predicate:
            if c.get("op", "in") != "in":
                preds.append(c)
                continue
            vals = [str(v) for v in c.get("values") or []]
            keys = {norm_key(v) for v in vals}
            for m in maps:
                if m.table != r.table or m.values["col"] != c["col"]:
                    continue
                to = {str(k): str(v) for k, v in (m.values.get("to") or {}).items()}
                groups: dict = {}
                for k, lab in to.items():
                    groups.setdefault(lab, {norm_key(lab)}).add(k)
                written = _spellings(analysis, r.table, c["col"])
                for w in [str(v) for p in m.predicate for v in p.get("values") or []] + list(groups):
                    written.setdefault(norm_key(w), w)
                for g in groups.values():
                    if not g & keys:
                        continue
                    for k in sorted(g - keys, key=str):
                        vals.append(written.get(k, k))
                        keys.add(k)
                        changed = True
            preds.append(dict(c, values=vals))
        if changed:
            r = Rule(r.kind, r.table, preds, dict(r.values), list(r.scope), r.source, r.confirmed)
        out.append(r)
    return out


def _spellings(analysis, tid: str, col: str) -> dict:
    """{normalized value: the value as written in the column}, first spelling seen."""
    t = next((x for x in analysis.tables if x.tid == tid), None)
    if t is None or col not in t.headers:
        return {}
    j, out = t.headers.index(col), {}
    for row in t.rows:
        v = row[j] if j < len(row) else None
        k = norm_key(v)
        if k is not None and k not in out:
            out[k] = _written(v) or str(v).strip()
    return out


def _derived_scales(analysis, rules: list) -> list:
    """A unit rule on a price reaches every column worked out from it: an amount
    that is a quantity times that price on 99% or more of the rows is divided on
    the same rows by the same number (never a column the owner already gave a
    rule of its own)."""
    out = []
    for r in rules:
        if r.kind != "scale" or not r.confirmed or r.values.get("follows"):
            continue
        t = next((x for x in analysis.tables if x.tid == r.table), None)
        src = r.values.get("col")
        if t is None or src not in t.headers:
            continue
        for d in _worked_out_from(analysis, t, src):
            if any(x.kind == "scale" and x.table == t.tid and x.values.get("col") == d and x.confirmed
                   for x in rules + out):
                continue
            out.append(Rule("scale", t.tid, [dict(c) for c in r.predicate],
                            {"col": d, "by": r.values.get("by"), "follows": src}, list(r.scope), r.source, True))
    return out


def _worked_out_from(analysis, t, src: str) -> list:
    """The number columns of t that are src times another number column on 99% or
    more of the rows where all three are numbers (at least 5 rows)."""
    memo = getattr(analysis, "_worked_out", None)
    if memo is None:
        memo = {}
        try:
            analysis._worked_out = memo
        except Exception:  # noqa: BLE001
            pass
    if (t.tid, src) not in memo:
        memo[(t.tid, src)] = _products_of(analysis, t, src)
    return list(memo[(t.tid, src)])


def _products_of(analysis, t, src: str) -> list:
    cols = [c for c in analysis.cols[t.tid] if c.type == "number" and not c.sensitive]
    j = t.headers.index(src)
    rows = t.rows[:5000]
    out = []
    for d in cols:
        if d.j == j:
            continue
        for q in cols:
            if q.j in (j, d.j):
                continue
            trio = [(row[j], row[q.j], row[d.j]) for row in rows if max(j, q.j, d.j) < len(row)
                    and _num(row[j]) and _num(row[q.j]) and _num(row[d.j])]
            if len(trio) >= 5 and sum(1 for p, n, x in trio
                                      if abs(p * n - x) <= max(0.0051, 0.0005 * abs(x))) >= 0.99 * len(trio):
                out.append(d.header)
                break
    return out


def protected(answers: dict) -> list:
    """Values the owner said are right on purpose (a ratio far from every other
    row's): [{table, col, row, value, source}]. Readers keep them as written and
    never 'fix' them; a pick other than 'Right, on purpose' protects nothing."""
    out = []
    for qid, a in (answers or {}).items():
        p = a.get("protect") if isinstance(a, dict) else None
        if p and p.get("option") in (a.get("options") or []):
            out.append({"table": p["table"], "col": p["col"], "row": p["row"], "value": p["value"], "source": qid})
    return out


def typed_count(text: str) -> float:
    """The first number the owner typed that can divide a price (more than 1):
    '12 per case' gives 12, 'a case of 6' gives 6; 0.0 when there is none."""
    for m in re.finditer(r"(?<![\d.,$])(\d+(?:\.\d+)?)(?![\d%])", text or ""):
        v = float(m.group(1))
        if v > 1:
            return v
    return 0.0


def exclusions_of(analysis, rules: list) -> dict:
    """{(table id, column index): {normalized values left out}}, for the rules that
    leave out values of one column from every total."""
    out: dict = {}
    for r in rules or []:
        if r.kind != "exclude" or r.scope or len(r.predicate) != 1 or r.predicate[0].get("op", "in") != "in":
            continue
        c = analysis.col(r.table, r.predicate[0]["col"])
        if c is not None:
            out.setdefault((r.table, c.j), set()).update(norm_key(v) for v in r.predicate[0]["values"])
    return out


def exclusions(analysis, answers: dict) -> dict:
    """{(table id, column index): {normalized values to leave out}}."""
    return exclusions_of(analysis, confirmed(analysis, answers))


def candidates(analysis, answers: dict) -> list:
    """Every rule a typed answer proposes, whatever the question was: one per
    table, column and values, and the conjunction when a sentence names values in
    two or more columns. Each carries the sentence, its rows and its money,
    counted over all rows. A sentence needs a rule verb (leave out, combine,
    divided by, ...) to propose anything, and each verb proposes only from its own
    clause: "Leave Q7 out and combine A1 and A2" is one exclusion and one map. A
    verb the owner argued against ("don't leave Q7 out") proposes nothing. Words
    typed on a readback are read too ('Neither. Leave Q70 out'): what the readback
    showed is not proposed again, anything new is read back once more. A leave-out
    of a value a confirmed map made one with another covers both spellings."""
    return _cands(analysis, answers)


def _cands(analysis, answers: dict, unbound: list | None = None) -> list:
    out, seen = [], {}
    maps = [r for r in confirmed(analysis, answers) if r.kind == "map"]
    for qid, a in (answers or {}).items():
        if not isinstance(a, dict) or qid.startswith("_"):
            continue
        sents = _said(analysis, a.get("text") or "")
        found = []
        for i, s in enumerate(sents):
            before = tuple(reversed(sents[max(0, i - 2):i]))          # nearest first
            # in an answer about a column's codes, each code's meaning is read on its own
            pieces = code_pieces(s, a.get("codes") or [])
            for p in pieces:
                for fam, part, negated, verb in _clauses(p):
                    if negated:
                        continue
                    found += _from_sentence(analysis, part, qid, family=fam, said=p, verb=verb,
                                            sentence=p if len(pieces) > 1 else s, before=before, unbound=unbound)
        for cand in (found + _code_lines(analysis, qid, a) + _picked(analysis, qid, a) + _unit_note(analysis, qid, a)
                     + _adjust_lines(analysis, qid, a)):
            cand = _widened(analysis, cand, maps)
            k = ident(analysis, cand["rule"])
            if k not in seen:
                seen[k] = len(out)
                out.append(cand)
                continue
            # the same rows said twice with two scopes ('they are not sales, so QX should not count toward the
            # totals'): one rule, out of both; out of every count and total when either says so
            old = out[seen[k]]
            r0, r1 = old["rule"], cand["rule"]
            if r0.kind in ("exclude", "filter") and r0.scope != r1.scope and not old.get("scope_words") \
                    and not cand.get("scope_words"):
                wide = Rule(r0.kind, r0.table, r0.predicate, dict(r0.values), _union_scope(r0.scope, r1.scope),
                            r0.source, r0.confirmed)
                out[seen[k]] = dict(old, **effect(analysis, wide), rule=wide,
                                    scope=[] if not wide.scope else list(wide.scope))
    return out


def _unit_note(analysis, qid: str, a: dict) -> list:
    """A group whose quantity the owner said is in another unit ('Another unit' on a
    unit-by-group question; the answer carries unit_group: {table, col, predicate or
    group_col and values, option}): a rule of kind unit that changes no row. It is
    never summed with the others' quantity: the column's note says so, and every
    number that sums the column carries the owner's note, not applied."""
    ug = a.get("unit_group") if isinstance(a, dict) else None
    if not ug or ug.get("option") not in (a.get("options") or []):
        return []
    t = next((x for x in analysis.tables if x.tid == ug.get("table")), None)
    if t is None or ug.get("col") not in t.headers:
        return []
    pred = [dict(c) for c in ug.get("predicate") or []]
    if not pred and ug.get("group_col") in t.headers and ug.get("values"):
        pred = [{"col": ug["group_col"], "op": "in", "values": [str(v) for v in ug["values"]]}]
    r = Rule("unit", t.tid, pred, {"col": ug["col"]}, source=qid)
    if not pred or not r.valid({h: j for j, h in enumerate(t.headers)}):
        return []
    said = (a.get("text") or "").strip() or ", ".join(a.get("labels") or [])
    return [dict(effect(analysis, r), rule=r, said=said, source=qid, scope=[], scope_words="")]


_MONTH_NAMES = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
_DATE_SAID = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b|\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b|"
                        r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?"
                        r"(?:,?\s+(\d{4}))?\b|\b(\d{1,2})(?:st|nd|rd|th)?\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|"
                        r"oct|nov|dec)[a-z]*\.?(?:,?\s+(\d{4}))?\b", re.I)
# the side of a date a rule's rows are on ('dated before April 17, 2026', 'from March 2 on')
_DATE_SIDE = re.compile(r"\b(before|prior\s+to|until|through|up\s+to|after|from|since|starting(?:\s+with)?|"
                        r"on\s+or\s+after)\s+(?:the\s+)?(?:[a-z]+\s+){0,2}?"
                        r"(?=\d|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)", re.I)
# an owner's rule that changes a column's arithmetic on some rows: 'subtract Overtime', 'minus the fees'
_SUBTRACT = re.compile(r"\b(?:subtract|subtracting|deduct|deducting|take\s+off|minus)\s+(?:the\s+)?", re.I)


def _date_iso(m, years: list):
    """The ISO date a _DATE_SAID match names; a date typed without a year takes the
    year of the table's dates it falls in (the first such)."""
    import datetime as dt
    g = m.groups()
    try:
        if g[0]:
            return dt.date(int(g[0]), int(g[1]), int(g[2]))
        if g[3]:
            y = int(g[5])
            return dt.date(y + 2000 if y < 100 else y, int(g[3]), int(g[4]))
        mon, day, year = (g[6], g[7], g[8]) if g[6] else (g[10], g[9], g[11])
        month = _MONTH_NAMES.index(mon.lower()[:3]) + 1
        if year:
            return dt.date(int(year), month, int(day))
        for y in years or []:
            return dt.date(int(y), month, int(day))
    except (ValueError, IndexError):
        return None
    return None


def _adjust_lines(analysis, qid: str, a: dict) -> list:
    """'Before <date>, <A> includes <B>; subtract <B>' (or 'on rows dated before
    ..., subtract <B>'): a rule of kind adjust that takes column B off column A on
    the rows the date names, proposed for the readback. A and B are number columns
    of one table the sentence names; the date side is before, or from, a date the
    owner typed. Nothing is proposed without all three."""
    import datetime as dt
    if not isinstance(a, dict) or not (a.get("text") or "").strip():
        return []
    out = []
    sents = _said(analysis, a.get("text") or "")
    for i, s in enumerate(sents):
        sub = _SUBTRACT.search(s)
        if not sub or _NEGATED.search(s[:sub.end()]):
            continue
        near = s + " " + (sents[i - 1] if i else "")
        for t in analysis.tables:
            if not _usable(analysis, t):
                continue
            aj = analysis._axis_j(t) if hasattr(analysis, "_axis_j") else None
            if aj is None:
                continue
            nums = {c.header for c in analysis.cols[t.tid] if c.type == "number" and c.semantic == "metric"}
            minus = next((h for a0, _b, h in _named_headers(t, s) if a0 == sub.end() and h in nums), None)
            if minus is None:
                continue
            target = next((h for _a, _b, h in sorted(_named_headers(t, s)) if h in nums and h != minus), None) or \
                next((h for _a, _b, h in sorted(_named_headers(t, near)) if h in nums and h != minus), None)
            if target is None:
                continue
            dc = analysis.cols[t.tid][aj]
            years = sorted({d.year for d in (dc.min, dc.max) if hasattr(d, "year")})
            side = None
            for text in (s, sents[i - 1] if i else ""):
                for m in _DATE_SIDE.finditer(text):
                    d = _DATE_SAID.search(text, m.end())
                    day = _date_iso(d, years) if d and d.start() - m.end() <= 30 else None
                    if day is not None:
                        side = (m.group(1).lower(), day)
                        break
                if side:
                    break
            if side is None:
                continue
            word, day = side
            if word in ("before", "prior to", "until"):
                vals = ["", (day - dt.timedelta(days=1)).isoformat()]
            elif word in ("through", "up to"):
                vals = ["", day.isoformat()]
            else:
                vals = [day.isoformat(), ""]
            r = Rule("adjust", t.tid, [{"col": t.headers[aj], "op": "between", "values": vals}],
                     {"col": target, "minus": minus}, source=qid)
            if not r.valid({h: j for j, h in enumerate(t.headers)}):
                continue
            eff = effect(analysis, r)
            if eff["rows"]:
                out.append(dict(eff, rule=r, said=s, source=qid, scope=[], scope_words=""))
    return out


def unit_groups(analysis, answers: dict) -> list:
    """The unit rules the owner's answers give ([Rule of kind unit])."""
    return [c["rule"] for qid, a in (answers or {}).items() if isinstance(a, dict)
            for c in _unit_note(analysis, qid, a)]


def _widened(analysis, cand: dict, maps: list) -> dict:
    """A proposed leave-out read back with every spelling a confirmed map made one."""
    r = cand["rule"]
    if not maps or r.kind not in ("exclude", "filter"):
        return cand
    wide = _expand_maps(analysis, [Rule.from_dict(dict(r.to_dict(), confirmed=True))] + maps)[0]
    if wide.key() == Rule.from_dict(r.to_dict()).key():
        return cand
    wide.confirmed = False
    return dict(cand, **effect(analysis, wide), rule=wide)


def unbound(analysis, answers: dict) -> list:
    """Sentences with a rule verb whose condition names rows code cannot find on a
    tab ('Cost Each on cellar lines is ... divided by 12' on a tab with no
    cellar column and no key to one): [{table, said, source, words}]. Nothing is offered
    for them there; the numbers they touch are counted as they are, and say so."""
    got: list = []
    _cands(analysis, answers, unbound=got)
    seen, out = set(), []
    for u in got:
        k = (u["table"], u["said"])
        if k not in seen:
            seen.add(k)
            out.append(u)
    return out


def word_scope(analysis, r: Rule) -> list:
    """The part of a rule's scope that is one calculation in the owner's words
    ('rebate math'), not a column of its table nor a measure worked out for it
    ('Net', Qty x Price minus Discount): a scope naming a measure is a total,
    like a column's."""
    t = next((t for t in analysis.tables if t.tid == r.table), None)
    heads = set(t.headers) if t is not None else set()
    measures = measure_names(analysis, r.table) if r.scope else set()
    return [s for s in r.scope or [] if s not in heads and s not in measures]


def measure_names(analysis, tid: str) -> set:
    """The names of measures worked out for a table rather than read from one of
    its columns: the measure a table with no amount column needs (Qty x Price
    minus its adjustment), found in the data or confirmed by the owner."""
    ins = getattr(analysis, "insights", None) or []
    st = getattr(analysis, "_structural", None) or []
    memo = getattr(analysis, "_measure_memo", None)
    stamp = (id(ins), len(ins), id(st), len(st))
    if memo is None or memo[0] != stamp:
        got: dict = {}
        for i in list(ins) + list(st):
            rec, n = str(i.get("recipe", "")), i.get("numbers") or {}
            if rec.startswith("derive:") and n.get("name") and n.get("table"):
                got.setdefault(n["table"], set()).add(str(n["name"]))
            elif rec.startswith("by_month:") and n.get("table") and n.get("col"):
                got.setdefault(n["table"], set()).add(str(n["col"]))
        memo = (stamp, got)
        try:
            analysis._measure_memo = memo
        except Exception:  # noqa: BLE001
            pass
    return set(memo[1].get(tid, set())) | set((getattr(analysis, "_confirmed_measures", None) or {}).get(tid, ()))


def ident(analysis, r: Rule) -> tuple:
    """A rule's identity: what it does to which rows, and the calculation it is kept
    for when it is kept for one. A rule scoped to a column total is the same rule
    (the readback's scope option narrows a rule, it does not make a new one)."""
    return r.key() + (tuple(word_scope(analysis, r)),)


def _picked(analysis, qid: str, a: dict) -> list:
    """The rule a pick offers for the readback ('Use the last value': a blank
    takes the value above it; 'Same as another Site' with a value of that column
    typed: the two counted as one). Proposed only; it changes a number once
    the owner ticks it with its counts."""
    out = []
    for p in a.get("propose") or []:
        if p.get("option") not in (a.get("options") or []):
            continue
        if p.get("rule"):
            r = Rule.from_dict(dict(p["rule"], confirmed=False))
        else:
            other = _typed_value(analysis, p["table"], p["col"], a.get("text") or "", p["value"])
            if other is None:
                continue
            to = {str(norm_key(p["value"])): other, str(norm_key(other)): other}
            r = Rule("map", p["table"], [{"col": p["col"], "op": "in", "values": [p["value"], other]}],
                     {"col": p["col"], "to": to})
        t = next((t for t in analysis.tables if t.tid == r.table), None)
        if t is None or not r.valid({h: j for j, h in enumerate(t.headers)}):
            continue
        r.source = qid
        said = (a.get("text") or "").strip() or ", ".join(a.get("labels") or [])
        out.append(dict(effect(analysis, r), rule=r, said=said, source=qid, scope=[]))
    return out


def _caps(w: str) -> bool:
    """A value written in capitals ('DUES', 'Q7', 'TR-'): typed text names it only in
    capitals, or in quotes. The ordinary word 'dues' is never the code DUES."""
    return any(ch.isalpha() for ch in w) and w.upper() == w


def _value_pattern(w: str):
    """A value as a whole token of the typed text: in capitals only for a value in
    capitals, unless the owner put it in quotes; any case for other values."""
    body = r"(?<![\w-])" + re.escape(w) + r"(?![\w-])"
    if not _caps(w):
        return re.compile(body, re.I)
    return re.compile(body + r"|(?<=[\"'‘“])(?i:" + re.escape(w) + r")(?=[\"'’”])")


def _typed_value(analysis, tid: str, col: str, text: str, skip) -> str | None:
    """The longest value of the column, as written, that the typed text names as
    a whole word, other than skip; None when it names none."""
    t = next((t for t in analysis.tables if t.tid == tid), None)
    if t is None or col not in t.headers or not text.strip():
        return None
    j, gone = t.headers.index(col), norm_key(skip)
    vals = {w for row in t.rows for w in [_written(row[j] if j < len(row) else None)] if w and norm_key(w) != gone}
    hits = [w for w in vals if _value_pattern(w).search(text)]
    return max(hits, key=lambda w: (len(w), w)) if hits else None


def _code_str(c) -> str:
    return _written(c) or str(c).strip()


def code_segments(text: str, codes: list, headers=(), cut_before_lowercase: bool = False) -> list:
    """[(code, start, end)] for each code the owner wrote as '<code> = ...',
    '<code>: ...' or '<code> - ...' at the start of the text or after a break
    (a space, a comma, a semicolon): each runs to the next one, or to the end of
    its sentence, whichever comes first, so '5 = staff meal.' never takes in the
    sentence after it. A code written in capitals matches only in capitals."""
    from .privacy import sentence_spans
    hits = []
    for code in sorted({_code_str(c) for c in codes or [] if _code_str(c)}, key=len, reverse=True):
        pat = re.compile(r"(?:^|(?<=[\s,;(]))" + re.escape(code) + r"\s*(?:=|:|-(?=\s))", 0 if _caps(code) else re.I)
        for m in pat.finditer(text or ""):
            if not any(a <= m.start() < b for _c, a, b in hits):
                hits.append((code, m.start(), m.end()))
    hits.sort(key=lambda h: h[1])
    ends = [b for _a, b in sentence_spans(text or "", headers, cut_before_lowercase)] or [len(text or "")]
    out = []
    for i, (c, a, _e) in enumerate(hits):
        nxt = hits[i + 1][1] if i + 1 < len(hits) else len(text)
        stop = next((b for b in ends if b > a), len(text))
        out.append((c, a, min(nxt, stop)))
    return out


def code_pieces(sentence: str, codes: list) -> list:
    """One typed sentence cut where each code's meaning starts ('3 = sent back,
    5 = kept' is two pieces), each piece exactly as typed; words before the first
    code stay a piece of their own. Without codes, the sentence whole."""
    segs = code_segments(sentence, codes) if codes else []
    if len(segs) < 2:
        return [sentence]
    head = sentence[:segs[0][1]].strip(" ,;")
    return ([head] if head else []) + [sentence[a:b].strip(" ,;") for _c, a, b in segs]


def _code_refs(text: str, codes: list, header: str = "") -> list:
    """[(start, end, code)] where the text names a code of the column outside its
    own '<code> = ...' words: 'code 4', 'codes 4 and 5', '(5)', 'the 4s', '#4',
    '<column> 4', and a code with letters written exactly as it is ('R', 'XJ')."""
    by: dict = {}
    for c in codes or []:
        s = _code_str(c)
        if s:
            by[s if _caps(s) else s.lower()] = s

    def code_of(tok: str):
        if tok in by:
            return by[tok]
        s = by.get(tok.lower())
        return s if s is not None and not _caps(s) else None
    out = []
    tok = r"[\w-]+"
    lead = r"\bcodes?" + (r"|(?<![\w-])" + re.escape(header) if header else "")
    for m in re.finditer(r"(?:" + lead + r")\s*#?\s*(" + tok + r"(?:\s*(?:,|&|\band\b|\bor\b)\s*#?\s*" + tok + r")*)",
                         text, re.I):
        for t in re.finditer(tok, m.group(1)):
            c = code_of(t.group(0))
            if c is not None:
                out.append((m.start(1) + t.start(), m.start(1) + t.end(), c))
    for pat in (r"\(\s*(" + tok + r")\s*\)", r"\bthe\s+(" + tok + r")'?s\b", r"#(" + tok + r")"):
        for m in re.finditer(pat, text, re.I):
            c = code_of(m.group(1))
            if c is not None:
                out.append((m.start(1), m.end(1), c))
    for s in by.values():            # a code with letters, exactly as written; never the words 'A' or 'I'
        if not re.search(r"[A-Za-z]", s) or s in ("A", "I", "a", "i"):
            continue
        for m in re.finditer(r"(?<![\w-])" + re.escape(s) + r"(?![\w-])", text):
            out.append((m.start(), m.end(), s))
    seen, uniq = set(), []
    for a, b, c in sorted(out):
        if not any(a < y and x < b for x, y in seen):
            seen.add((a, b))
            uniq.append((a, b, c))
    return uniq


def _code_lines(analysis, qid: str, a: dict) -> list:
    """In an answer about a column's codes, '3 = sent back, leave out' proposes
    leaving out exactly that code, one or two characters included: the code is
    the one written before '=', and the rule verb is an instruction in its own
    words. A later sentence that names a code ('so leave code 4 out of waste',
    'Staff meal (5) I do count') binds to the code it names, never to the code
    whose words came before it."""
    ab = a.get("about") or {}
    t = next((t for t in analysis.tables if t.tid == ab.get("table")), None)
    text = a.get("text") or ""
    if t is None or ab.get("col") not in t.headers or not a.get("codes") or not text:
        return []
    from .privacy import sentence_spans
    col, codes = ab["col"], a["codes"]
    heads = {c.header for cols in analysis.cols.values() for c in cols}
    segs = code_segments(text, codes, heads, cut_before_lowercase=True)
    out = []

    def propose(vals, part, verb, said, pre):
        scope, words, _rows_end, _lead = _scope_of(analysis, t, part, verb, pre)
        col_scope, other = _col_scope(analysis, t, part, verb, pre)
        words = words or other
        r = Rule("exclude", t.tid, [{"col": col, "op": "in", "values": list(dict.fromkeys(vals))}], source=qid)
        col_scope = _money_scope(analysis, t, r, col_scope)
        if words:
            r.scope = [words]
        elif col_scope:
            r.scope = list(col_scope)         # 'out of loss dollars': that money, never the count of rows
        out.append(dict(effect(analysis, r), rule=r, said=said, source=qid,
                        scope=[] if words else (list(col_scope) or scope), scope_words=words))
    spans = sentence_spans(text, heads, cut_before_lowercase=True)

    def code_before(a0):
        """The code a sentence that opens by pointing back ('They are not fee income') is
        about: the code whose own words are the sentence before it, or the one before that
        when the sentence between carries on from it ('Holds go into an escrow account.'
        after '3300 = an escrow hold.')."""
        k = next((i for i, (x, _y) in enumerate(spans) if x == a0), None)
        if not k or not ANAPHOR_START.match(text[a0:]):
            return None
        px, py = spans[k - 1]
        own = next((c for c, s0, e0 in segs if s0 <= px < e0), None)
        if own is not None:
            return own
        lead = _lead_stems(text[px:py])
        prior = [(c, s0, e0) for c, s0, e0 in segs if e0 <= px]
        if lead and prior:
            c, s0, e0 = prior[-1]
            between = [x for x, _y in spans if e0 <= x < px]
            if lead & _stems_of(text[s0:e0]) and not between:
                return c
        return None
    for code, start, end in segs:                   # each code's own words
        said = text[start:end].strip(" ,;")
        for fam, part, negated, verb in _clauses(said):
            if fam != "exclude" or negated:
                continue
            _s, _w, rows_end, _lead = _scope_of(analysis, t, part, verb, "")
            named = [c for _x, _y, c in _code_refs(part[:rows_end], codes, col) if c != code]
            propose(named or [code], part, verb, said, said[:max(0, said.find(part))])
            break
    for a0, b0 in spans:                             # the other sentences
        taken = sorted((max(a0, s), min(b0, e)) for _c, s, e in segs if s < b0 and e > a0)
        free, at = [], a0
        for s, e in taken:
            if s > at:
                free.append((at, s))
            at = max(at, e)
        if at < b0:
            free.append((at, b0))
        for x, y in free:
            piece = text[x:y]
            for fam, part, negated, verb in _clauses(piece):
                if fam != "exclude" or negated:
                    continue
                off = x + max(0, piece.find(part))
                _s, _w, rows_end, _lead = _scope_of(analysis, t, part, verb, text[a0:off])
                refs = _code_refs(part[:rows_end], codes, col)
                if not refs and _PRONOUN.search(part[:rows_end]):
                    refs = _code_refs(text[a0:off], codes, col)       # named earlier in the same sentence
                if not refs and _PRONOUN.search(part[:rows_end]):
                    c0 = code_before(a0)                              # or in the sentence it points back to
                    refs = [(0, 0, c0)] if c0 is not None else []
                if refs:
                    propose([c for _x, _y, c in refs], part, verb, text[a0:b0], text[a0:off])
    return out


def offered(answers: dict, analysis=None) -> set:
    """Rules already read back to the owner, ticked or not. A rule a question
    offered as an option and the owner did not pick is not read back by that;
    if the owner then typed it, the readback asks. A rule kept for one
    calculation is not the same rule as the one for every total. A rule shown
    before a map made its value one with another is the same rule after."""
    from .findings import READBACK
    ds = [d for k, a in (answers or {}).items() if isinstance(a, dict) and k.startswith(READBACK)
          for d in a.get("rules") or [] if not d.get("infer") and d.get("kind") in KINDS]
    if analysis is None:
        return {Rule.from_dict(d).key() for d in ds}
    maps = [r for r in confirmed(analysis, answers) if r.kind == "map"] if ds else []
    out = set()
    for d in ds:
        r = Rule.from_dict(dict(d, confirmed=True))
        out.add(ident(analysis, r))
        if maps:
            out.add(ident(analysis, _expand_maps(analysis, [r] + maps)[0]))
    return out


def _declined_ids(analysis, answers: dict) -> set:
    """Rules a readback showed that the owner answered without ticking them: a
    tick on another rule or words of their own ('No, those stay in'). Not
    sure is not a no: those stay the owner's unapplied note."""
    from .findings import READBACK
    out = set()
    for k, a in (answers or {}).items():
        if not isinstance(a, dict) or not k.startswith(READBACK) or a.get("not_sure"):
            continue
        if not (a.get("options") or (a.get("text") or "").strip()):
            continue
        for d in a.get("rules") or []:
            if not d.get("confirmed") and not d.get("infer") and d.get("kind") in KINDS:
                out.add(ident(analysis, Rule.from_dict(dict(d, confirmed=True))))
    return out


def proposals(analysis, answers: dict, scoped: bool = False) -> list:
    """Rules typed sentences propose that the owner has neither confirmed nor seen
    in a readback yet. scoped: also the rules kept for one calculation, which are
    read back as that (scoped_readback), never as a rule for every total. Rules
    that name the same rows the same way (Item # SVC-A, SVC-B and Category Service
    on the same lines) are one option: the one written as in the data, with the
    fewest conditions; once any of them is shown, none is asked again."""
    conf = confirmed(analysis, answers)
    done = {ident(analysis, r) for r in conf}
    shown = offered(answers, analysis)
    out = []
    # a unit note is never ticked: it marks the sums it would split, it never changes a row
    for g in _same_rows(analysis, [c for c in candidates(analysis, answers) if c["rule"].kind != "unit"]):
        if any(ident(analysis, c["rule"]) in shown for c in g):
            continue
        # a rule a pick already carries out on the same rows, as wide or wider, is never asked again (one wider
        # than the pick is: '<value> out of every count, not just <measure>'); nor is a lookup tab's own row for
        # a key, which no count or total adds up
        if _by_rows(analysis, g[0]["rule"]):
            if _covered(analysis, g[0], conf) or _on_lookup_key(analysis, g[0]["rule"]):
                continue
        elif any(ident(analysis, c["rule"]) in done for c in g):
            continue
        if scoped or not g[0].get("scope_words"):
            out.append(dict(g[0], narrower=_narrower(analysis, g[0], conf)))
    return out


def _covered(analysis, c: dict, applied: list) -> bool:
    """A typed rule the confirmed rules already carry out on the same rows: every
    row a leave-out names is out of every total it names (a pick for every count
    and total covers a scoped one, never the other way round); a unit rule's rows
    are divided the same way already."""
    r = c["rule"]
    t = next((x for x in analysis.tables if x.tid == r.table), None)
    if t is None or r.kind not in ("exclude", "scale", "adjust"):
        return False
    idx = {h: j for j, h in enumerate(t.headers)}
    if not r.valid(idx) or (r.kind == "exclude" and word_scope(analysis, r)):
        return False
    rows = rows_of(analysis, r)
    if not rows:
        return False
    if r.kind == "exclude":
        for m in (r.scope or [None]):
            if {i for i, _row in apply(applied, t, analysis.cols[t.tid], metric=m)} & rows:
                return False
        return True
    return any(x.kind == r.kind and x.confirmed and x.table == r.table and x.values.get("col") == r.values.get("col")
               and str(x.values.get("by") or x.values.get("minus")) == str(r.values.get("by") or r.values.get("minus"))
               and rows <= rows_of(analysis, x) for x in applied)


def _by_rows(analysis, r: Rule) -> bool:
    """A leave-out settled by the rows it takes out of each total, not by its form."""
    return r.kind in ("exclude", "filter", "scale", "adjust") and not word_scope(analysis, r)


def _narrower(analysis, c: dict, applied: list) -> list:
    """The totals a confirmed rule already leaves a typed leave-out's rows out of,
    when the typed one is wider (for every count and total): the readback says
    '<value> out of every count, not just <those>'."""
    r = c["rule"]
    t = next((x for x in analysis.tables if x.tid == r.table), None)
    if t is None or r.kind != "exclude" or r.scope:
        return []
    rows = rows_of(analysis, r)
    got = []
    for x in applied:
        if x.kind == "exclude" and x.table == r.table and x.scope and not word_scope(analysis, x) \
                and rows and rows <= rows_of(analysis, x):
            got += [s for s in x.scope if s not in got]
    return got


def _on_lookup_key(analysis, r: Rule) -> bool:
    """A leave-out of a lookup tab's own rows by its key: no count or total adds up
    those rows, so it changes nothing to read back or mark."""
    if r.kind not in ("exclude", "filter") or not r.predicate:
        return False
    key = lookup_key(analysis, r.table)
    return bool(key) and all(c.get("col") == key for c in r.predicate)


def _same_rows(analysis, cands: list) -> list:
    """Candidates in groups that do the same thing to the same rows, the one to
    show first in each."""
    groups: dict = {}
    order = []
    for c in cands:
        r = c["rule"]
        key = ("id", ident(analysis, r))
        if r.kind in ("exclude", "filter"):
            t = next((x for x in analysis.tables if x.tid == r.table), None)
            if t is not None:
                idx = {h: j for j, h in enumerate(t.headers)}
                if r.valid(idx):
                    rows = frozenset(i for i, row in enumerate(t.rows) if r.matches(row, idx))
                    key = (r.kind, r.table, tuple(word_scope(analysis, r)),
                           tuple(sorted(s for s in r.scope if s not in word_scope(analysis, r))), rows)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(c)

    def prefer(c):
        r = c["rule"]
        said = c.get("said") or ""
        vals = [str(v) for p in r.predicate for v in p.get("values") or [] if p.get("op", "in") != "between"]
        written = all(re.search(r"(?<![\w-])" + re.escape(v) + r"(?![\w-])", said) for v in vals) if vals else False
        return (len(r.predicate), not written, any(p.get("via") for p in r.predicate))
    return [sorted(groups[k], key=prefer) for k in order]


def unapplied(analysis, answers: dict, applied: list | None = None) -> list:
    """Rules the owner wrote that no confirmed rule covers and that would still
    change a number: every counted number they touch says it is not applied. A
    rule kept for one calculation changes no count or total, so it marks none; a
    rule the owner was shown and did not tick is not their note (see declined)."""
    return _live(analysis, answers, applied, declined=False)


def declined(analysis, answers: dict, applied: list | None = None) -> list:
    """Rules a readback proposed that the owner answered without ticking (another
    tick, or words of their own), and that would still change a number: said as
    proposed rules the owner did not tick, never as the owner's note."""
    return _live(analysis, answers, applied, declined=True)


def _live(analysis, answers: dict, applied, declined: bool) -> list:
    applied = confirmed(analysis, answers) if applied is None else applied
    done = {ident(analysis, r) for r in applied}
    no = _declined_ids(analysis, answers)
    out = []
    for c in candidates(analysis, answers):
        r = c["rule"]
        if c.get("scope_words") or (ident(analysis, r) in no) != declined:
            continue
        # a typed rule a pick already carries out (the same rows, as wide or wider) is applied, not open; one
        # wider than the pick is still the owner's rule, not applied
        if _by_rows(analysis, r):
            if _covered(analysis, c, applied) or _on_lookup_key(analysis, r):
                continue
        elif ident(analysis, r) in done:
            continue
        t = analysis.table(r.table)
        idx = {h: j for j, h in enumerate(t.headers)}
        live = sum(1 for _i, row in apply(applied, t, analysis.cols[t.tid]) if r.matches(row, idx))
        if live:
            out.append(c)
    return out


# words that state a treatment even without a rule verb ('never add their visits into hours', 'subtract Overtime')
_TREAT_SAID = re.compile(r"\bnever\s+(?:add|sum|total|compare|mix|combine|net)\b|\b(?:subtract|deduct)\b", re.I)


def unread(analysis, answers: dict) -> list:
    """Sentences the owner typed that give an order about rows (leave out, exclude,
    drop, divide, subtract, never add) from which no rule could be read in that
    answer, none left unbound either: [{said, source}]. Each is listed as the
    owner's rule, not applied from these words alone, never dropped. A sentence
    that only says two things are the same, or what something is, is no order."""
    from .findings import READBACK
    out, seen = [], set()
    everywhere = [str(c.get("said") or "") for c in _cands(analysis, answers)]
    conf = confirmed(analysis, answers)
    for qid, a in (answers or {}).items():
        if not isinstance(a, dict) or qid.startswith("_") or qid == "goal" or not (a.get("text") or "").strip():
            continue
        one = {qid: a}
        saids = [str(c.get("said") or "") for c in _cands(analysis, one)] + everywhere + \
            [str(u.get("said") or "") for u in unbound(analysis, one)]
        for s in _said(analysis, a.get("text") or ""):
            said_rule = [fam for fam, _part, negated, _v in _clauses(s)
                         if not negated and fam in ("exclude", "filter", "scale")]
            if not said_rule and not (_TREAT_SAID.search(s) and not _NEGATED.search(s)):
                continue
            bare = s.strip().rstrip(" .!?;:")
            if any(bare in x or (x.strip(" .!?;:") and x.strip(" .!?;:") in s) for x in saids) \
                    or _names_a_ruled_value(analysis, s, conf):
                continue
            if qid.startswith(READBACK) and re.match(r"\W*(?:no|none|neither|not)\b", s, re.I):
                continue
            if s not in seen:
                seen.add(s)
                out.append({"said": s, "source": qid})
    return out


def _names_a_ruled_value(analysis, s: str, conf: list) -> bool:
    """The sentence names a value, or an ID prefix, of a column a confirmed rule
    already acts on for those rows: what it orders is applied by that rule."""
    ruled: dict = {}
    for r in conf:
        for c in r.predicate:
            ruled.setdefault((r.table, c["col"]), set()).update(norm_key(v) for v in c.get("values") or [])
    if not ruled:
        return False
    for t, c, vals in _vocab(analysis):
        keys = ruled.get((t.tid, c.header))
        if keys and any(k in keys and _hits(pat, s, [], len(s), True, w) is not None for k, w, pat in vals):
            return True
    for _a, _b, p in _prefixes(s):
        for t in analysis.tables:
            cond = _prefix_cond(analysis, t, p, s) if _usable(analysis, t) else None
            if cond is not None and (t.tid, cond["col"]) in ruled:
                return True
    return False


def _said(analysis, text: str) -> list:
    """The owner's typed sentences, never cut inside a column name like 'Adj. Cost'
    or after an abbreviation, but cut before a lowercase word too: 'leave Q7 out.
    combine A1 and A2' is two rules."""
    from .privacy import said_sentences
    headers = {c.header for cols in analysis.cols.values() for c in cols}
    return said_sentences(text or "", headers, cut_before_lowercase=True)


def _instruction(s: str, m) -> bool:
    """drop and remove have an everyday sense ('3 = dropped or broken'): they are a
    rule only as an order or a rule said with a modal ('drop them', 'we remove the
    test rows', 'should be removed', 'are dropped from totals'). Every other rule
    verb is one wherever it stands."""
    w = m.group(0).lower()
    if not w.startswith(("drop", "remov")):
        return True
    if re.match(r"[A-Za-z]", s[m.end():m.end() + 1]):
        return False                      # part of a longer word ('dropdown')
    before, after = s[:m.start()], s[m.end():]
    if w in ("drop", "remove"):
        return bool(_ORDER_BEFORE.search(before))
    if w in ("dropped", "removed"):
        passive = re.search(r"\b(?:be|been|being|get|gets|got|getting|is|are|was|were)\s+(?:\w+\s+)?$", before, re.I)
        modal = re.search(r"\b(?:should|must|can|will|would|need|needs|has|have|had|to)\b", before[-40:], re.I)
        return bool(passive) and bool(modal or re.match(r"\s+(?:from|out\s+of)\b", after, re.I))
    return False                          # 'drops', 'removes': what something does, not an order


def _verbs(s: str) -> list:
    """The rule verbs in a sentence that are instructions."""
    return [m for m in RULE_VERB.finditer(s) if _instruction(s, m)]


def _family(s: str) -> str | None:
    """What a sentence's first rule verb would do to rows, or None without one."""
    ms = _verbs(s)
    return _kind_at(s, ms[0]) if ms else None


def _kind_at(s: str, m) -> str | None:
    """What the rule verb matched at m would do to rows."""
    got = [(x.end() == m.end(), kind) for kind, pat in _FAMILY for x in [pat.match(s, m.start())] if x]
    return max(got, key=lambda g: g[0])[1] if got else None


def _clauses(s: str) -> list:
    """[(family, clause, negated, (verb start, verb end) in the clause)], one per
    rule verb in the sentence. A clause runs to the next clause. It starts at its
    verb, or, for the first verb and for a verb whose rows come before it, at the
    last break ('but', ';') after the verb before, else right after that verb:
    'For Q7, leave it out' keeps Q7, and 'Keep Q7 but leave Q70 out' does not. A
    value belongs to the clause it sits in, so the separable 'leave X out' keeps
    X. negated: the words from the verb before up to this verb argue against it
    ('don't leave ... out')."""
    ms = _verbs(s)
    begins = []
    for k, m in enumerate(ms):
        lo = ms[k - 1].end() if k else 0
        if k and not _AFTER.match(s, m.start()):
            begins.append(m.start())
        else:
            cuts = [x.end() for x in _BREAK.finditer(s, lo, m.start())]
            begins.append(cuts[-1] if cuts else lo)
    out = []
    for k, m in enumerate(ms):
        end = begins[k + 1] if k + 1 < len(ms) else len(s)
        window = s[ms[k - 1].end() if k else 0:m.end()]
        out.append((_kind_at(s, m), s[begins[k]:end], bool(_NEGATED.search(window)),
                    (m.start() - begins[k], m.end() - begins[k])))
    return out


def _written(v):
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, str):
        return v.strip()
    if isinstance(v, int) or (isinstance(v, float) and v.is_integer()):
        return str(int(v))
    return None


def _vocab(analysis) -> list:
    """[(table, column, [(key, value as written, pattern)])] for the columns a
    sentence can name values of: grouping and code columns of 300 values or
    fewer on data tables. A value matches as a whole token of 2 or more
    characters; a value in capitals only in capitals, or quoted ('Q7' never
    matches 'q7' and never 'Q70'; 'DUES' never matches the word 'dues')."""
    got = getattr(analysis, "_rule_vocab", None)
    if got is not None:
        return got
    out = []
    for t in analysis.tables:
        if t.wide or analysis.is_derived(t.tid):
            continue
        for c in analysis.cols[t.tid]:
            if c.sensitive or c.distinct_capped or not (1 <= c.distinct <= 300) or c.type not in ("text", "number"):
                continue
            if not (c.codes or c.semantic in ("dimension", "identifier")) or \
                    (c.type == "number" and not (c.codes or c.semantic == "identifier")):
                continue
            vals, seen = [], set()
            for row in t.rows:
                v = row[c.j] if c.j < len(row) else None
                k = norm_key(v)
                w = _written(v)
                if k is None or k in seen or not w or len(w) < 2:
                    continue
                seen.add(k)
                vals.append((k, w, _value_pattern(w)))
            if vals:
                out.append((t, c, vals))
    analysis._rule_vocab = out
    return out


_GENERIC_HEAD = re.compile(r"(?:grand\s+)?(?:totals?|sums?|counts?|amounts?|numbers?|values?|figures?|money|"
                           r"spend|sales|income|costs?|balances?)", re.I)


def _named_headers(t, s: str) -> list:
    """[(start, end, header)] for the table's headers a sentence names: whole
    tokens of 4 or more characters, the longest winning where two overlap."""
    hits = []
    for h in t.headers:
        hs = str(h or "").strip()
        if len(hs) < 4 or hs.startswith("Column "):
            continue
        # a one-word header that is also the plain word for a total ('Total', 'Amount') is named only as
        # written: 'every total' is every total, never the Total column of a summary tab
        flags = 0 if _GENERIC_HEAD.fullmatch(hs) else re.I
        hits += [(m.start(), m.end(), h) for m in re.finditer(r"(?<![\w-])" + re.escape(hs) + r"(?![\w-])", s, flags)]
    kept = []
    for a, b, h in sorted(hits, key=lambda x: x[0] - x[1]):
        if not any(a < b2 and a2 < b for a2, b2, _ in kept):
            kept.append((a, b, h))
    return kept


def _scope_words(text: str) -> str:
    """The words that name a calculation, as typed: markers, a reason or an aside,
    and a closing 'only' taken off ('of item price comparisons and rebate math
    only' gives 'item price comparisons and rebate math')."""
    t = re.sub(r"^\W*(?:out\s+of|of|from|toward|towards|in|for|on)\s+", "", (text or "").strip(), flags=re.I)
    t = re.split(r"\s*[(\[;:]|\s+(?:but|because|since|so|as|while)\s+|,", t)[0]
    t = re.sub(r"\s*\bonly\b\s*$", "", t.strip(" .!?"), flags=re.I).strip(" .!?")
    t = re.sub(r"^only\s+", "", t, flags=re.I)
    return re.sub(r"^(The|A|An|Our)\b", lambda m: m.group(0).lower(), t)


# the totals a rule covers said after the rows with 'only' ('leave K4 out, Loss Value only', '..., only for Amount')
_ONLY_SCOPE = re.compile(r"[,;:]\s*(only\s+(?:for|in|on|from)\s+[\w.%&'/ -]{2,40}?|[\w.%&'/ -]{2,40}?\s+only)"
                         r"\s*[.!?]?\s*$", re.I)
# a calculation named as the sentence's subject ('Rebates are paid on ... (fees excluded)'): a rule
# said inside it is kept for that calculation alone
_LEAD_CALC = re.compile(r"^\W*(?:(?:the|our|a|an)\s+)?((?:[\w%&'-]+\s+){0,1}?(?:rebates?|allowances?|commissions?|"
                        r"margins?|benchmarks?|comparisons?))\b", re.I)


# a scope the owner waves off ('..., not just Unit Price'): it says the rule is wider than that, never that
# it is about it
_NOT_JUST = re.compile(r",?\s*\bnot\s+(?:just|only|merely|simply)\s+(?:the\s+|in\s+|on\s+|for\s+)?[^,;:.()]+", re.I)
# a count among the totals a rule names ('... hours, overtime and headcount', 'every count'): every count and
# total, since a count moves with every total
_COUNT_SCOPE = re.compile(r"\b(?:head\s*-?counts?|counts?|number\s+of\s+[a-z]+|how\s+many)\b", re.I)
# a total said by what it measures, not by a column's name: the table's sales measure
_SALES_WORD = re.compile(r"\b(?:sales|revenue|income|turnover|takings)\b", re.I)


def _scope_phrase(text: str) -> str:
    """The words naming the totals a rule covers, list commas kept: 'out of cost,
    pay, hours and headcount' gives 'cost, pay, hours and headcount'. Cut at a
    reason, an aside or a new clause; a scope waved off ('not just X') is dropped."""
    t = _NOT_JUST.sub("", text or "")
    t = re.sub(r"^\W*(?:out\s+of|of|from|toward|towards|in|for|on)\s+", "", t.strip(), flags=re.I)
    t = re.split(r"\s*[(\[;:]|\s+(?:but|because|since|so|as|while|when|if|unless)\s+|[.!?](?:\s|$)", t)[0]
    return t.strip(" ,.!?")


def _scope_items(analysis, t, text: str) -> tuple:
    """(every, [totals]) for the words naming a rule's totals: every is True when
    they name a count ('headcount', 'every count') or plain totals only; totals are
    the columns of t they name and the measure a sales word stands for (a measure
    worked out for the table, else its amount column, never a price)."""
    phrase = _scope_phrase(text)
    if not phrase:
        return True, []
    if _COUNT_SCOPE.search(phrase):
        return True, []
    totals = [h for _a, _b, h in _named_headers(t, phrase)
              if (c := analysis.col(t.tid, h)) is not None and c.type == "number" and c.semantic == "metric"]
    if _SALES_WORD.search(phrase):
        m = sorted(measure_names(analysis, t.tid))
        if m:
            totals.append(m[0])
        else:
            col, _money = money_col(analysis, t)
            if col and not _not_a_total(analysis, t, col) and col not in totals:
                totals.append(col)
    return False, list(dict.fromkeys(totals))


def _scope_of(analysis, t, part: str, verb, pre: str = "") -> tuple:
    """(metric columns the rule is said about, the calculation it is kept for in the
    owner's words or '', where the words naming the totals begin in the clause, the
    end of a leading scope phrase or 0). In 'leave X out of Y' the rows are X and Y
    names the totals; so does a leading 'For price comparisons, ...', the measure
    in 'X is escrow money, not income', and the sentence's subject when it is one
    calculation ('The basis is ...: fees excluded', 'Rebates are paid on ... (fees
    excluded)'). A scope that is plain totals ('every total', 'store spend') is
    every count and total; so is a list that names a count ('cost, pay, hours and
    headcount'). A scope named as what it measures ('sales') is that measure, and
    one the owner waves off ('not just Unit Price') is no scope at all."""
    vs, ve = verb if verb else (0, 0)
    w = part[vs:ve].lower()
    tail = part[ve:]
    not_measure = bool(verb) and (m := _NOT_MEASURE_RE.match(part, vs)) is not None and m.end() == ve
    if verb and (w.endswith("part of") or (w.endswith("out") and re.match(r"\s*(?:of|from)\b", tail, re.I))):
        at = ve
    elif not_measure:
        at = vs                       # 'X is escrow money, not income': the rows are before, the measure is said here
    else:
        hits = [x.start() for pat in (_SCOPE_AT, _SCOPE_IN) for x in [pat.search(part, ve)] if x] if verb else []
        # '<metric> only' or 'only for <metric>' after the rows ('leave K4 out, Loss Value only')
        only = _ONLY_SCOPE.search(part, ve) if verb else None
        if only:
            hits.append(only.start(1))
        at = min(hits) if hits else len(part)
    lead = _LEAD_SCOPE.match(part)
    lead_end = lead.end() if lead and lead.end() <= vs and _CALC.search(lead.group(1)) else 0
    said = _NOT_JUST.sub("", part[at:]) if at < len(part) else ""
    words = ""
    if said and not not_measure and _scope_items(analysis, t, said)[0] and _COUNT_SCOPE.search(_scope_phrase(said)):
        words = ""                    # a list that names a count is every count and total, whatever else it names
    elif said and _CALC.search(said):
        words = _scope_words(said)
    elif lead_end:
        words = _scope_words(lead.group(1))
    elif pre.strip() and (sub := _SUBJECT.match(pre)) and _CALC.search(sub.group(2)):
        words = _scope_words((sub.group(1) or "") + sub.group(2))
    elif (lc := _LEAD_CALC.match(pre if pre.strip() else part[:vs])):
        words = _scope_words(lc.group(1))
        if words[:1].isupper() and words[1:2].islower():
            words = words[0].lower() + words[1:]          # 'Rebates paid ...' is kept for rebates
    named = {h for _a, _b, h in _named_headers(t, said if said else part)}
    metrics = [h for h in sorted(named) if (c := analysis.col(t.tid, h)) is not None and c.type == "number"
               and c.semantic == "metric"]
    return metrics, words, at, lead_end


def _col_scope(analysis, t, part: str, verb, pre: str = "") -> tuple:
    """(the columns a rule is said about, set on the rule itself so that ticking it is
    never broader than the sentence; words naming a column only another tab has).
    'leave Q7 out of Amount' covers Amount, 'out of loss dollars' covers the table's
    money column, 'out of sales and discount numbers' the sales measure and
    Discount, 'out of every total' and a list naming a count ('..., hours and
    headcount') cover every count and total ([])."""
    metrics, words, at, _lead = _scope_of(analysis, t, part, verb, pre)
    if words or at >= len(part):
        return [], ""
    vs, ve = verb if verb else (0, 0)
    nm = _NOT_MEASURE_RE.match(part, vs) if verb else None
    if nm is not None and nm.end() == ve:
        # 'X is escrow money, not income': the measure the noun names, else the money the rows carry
        if _SALES_WORD.search(nm.group(0)) and measure_names(analysis, t.tid):
            return [sorted(measure_names(analysis, t.tid))[0]], ""
        return [MONEY], ""
    said = _NOT_JUST.sub("", part[at:])
    every, totals = _scope_items(analysis, t, said)
    if every:
        return [], ""
    if totals:
        return totals, ""
    if metrics:
        return metrics, ""
    elsewhere = [h for x in analysis.tables if x.tid != t.tid for _a, _b, h in _named_headers(x, said)
                 if h not in t.headers and (c := analysis.col(x.tid, h)) is not None and c.type == "number"]
    if elsewhere:
        return [], _scope_words(said)          # a column this tab does not have: nothing here is counted without it
    if _DOLLARS.search(said):
        return [MONEY], ""                     # the money those rows carry (see _money_scope)
    return [], ""


MONEY = "\x00money"


def _money_scope(analysis, t, r: Rule, scope: list) -> list:
    """A scope said as money ('out of loss dollars', 'not income') is the money column
    the rule's rows carry their money in; [] when the table has none."""
    if scope != [MONEY]:
        return scope
    idx = {h: j for j, h in enumerate(t.headers)}
    col, money = money_col(analysis, t)
    if lookup_key(analysis, t.tid):
        return []                         # a lookup tab's money is no total: its rows are only looked up
    if not r.valid(idx):
        return [col] if col else []
    got, _m = _carried_by(analysis, t, Rule(r.kind, r.table, r.predicate), idx, col, money)
    return [got] if got else []


def _hits(pat, text: str, spans: list, stop: int, exact: bool, w: str):
    """The first place the value's pattern matches in text before stop, outside spans."""
    return next((m for m in pat.finditer(text) if m.start() < stop
                 and not any(m.start() < b and a < m.end() for a, b in spans)
                 and (not exact or m.group(0) == w)), None)


def _usable(analysis, t) -> bool:
    return not t.wide and not analysis.is_derived(t.tid) and bool(t.n_rows)


def _prefixes(text: str) -> list:
    """[(start, end, prefix)] for the ID prefixes a sentence names: 'GV- lines',
    'starting with GV-', 'numbers starting with 7'."""
    out = [(m.start(1), m.end(1), m.group(1)) for m in _PREFIX_TOKEN.finditer(text or "")]
    out += [(m.start(1), m.end(1), m.group(1)) for m in _PREFIX_SAID.finditer(text or "")
            if not any(a <= m.start(1) < b for a, b, _p in out)]
    return sorted(out)


def _prefix_cond(analysis, t, prefix: str, said: str):
    """The column of t whose values a prefix names: 2 or more of its values start
    with it (an ID, code or grouping column, as written, case aside). The column
    the sentence names wins, then an ID or code column, then the most values."""
    best = None
    low = prefix.lower()
    heads = {h for _a, _b, h in _named_headers(t, said)}
    for c in analysis.cols[t.tid]:
        if c.sensitive or c.type not in ("text", "number") or not c.distinct:
            continue
        if c.type == "number" and not (c.codes or c.semantic == "identifier"):
            continue
        n = sum(1 for k in (c.counter or {}) if str(k).lower().startswith(low))
        if n < 2 or n == c.distinct and c.distinct > 2 and not prefix.endswith("-"):
            continue                  # a prefix every value has names no rows apart ('item codes start with 6'? all)
        # the header said in the plural or short ('SKUs starting with GV-') counts as named
        said_head = c.header in heads or re.search(r"(?<![\w-])" + re.escape(str(c.header)) + r"s?(?![\w-])",
                                                   said, re.I) is not None
        rank = (said_head, c.semantic == "identifier" or bool(c.codes), n)
        if best is None or rank > best[0]:
            best = (rank, c.header)
    return {"col": best[1], "op": "prefix", "values": [prefix]} if best else None


def _join_bind(analysis, t, hits: list, lookup_only: bool = False):
    """A condition named on another table, bound to t through a key: t's column
    joins that table's key on 95% or more of t's rows, and each key sits on one
    side of the condition. hits: [(table, column header, [values as written])].
    lookup_only: only through a key that is unique on the other table (a lookup
    tab), never through another table of lines. Returns a predicate condition on
    t's key column ({col, op: in, values: the keys, via: where the condition was
    read}), or None when no key binds it."""
    if not hits:
        return None
    for lt, lc, vals in hits:
        if lt.tid == t.tid:
            continue
        pairs = []
        for j in analysis.joins:
            if j["from_table"] == t.tid and j["to_table"] == lt.tid:
                pairs.append((j["from_col"], j["to_col"]))
            elif j["to_table"] == t.tid and j["from_table"] == lt.tid:
                pairs.append((j["to_col"], j["from_col"]))
        for tk, lk in pairs:
            if tk not in t.headers or lk not in lt.headers or lc not in lt.headers:
                continue
            if lookup_only and not getattr(analysis.col(lt.tid, lk), "unique", False):
                continue
            ki, ci = lt.headers.index(lk), lt.headers.index(lc)
            want = {norm_key(v) for v in vals}
            side: dict = {}
            for row in lt.rows:
                k = norm_key(row[ki] if ki < len(row) else None)
                if k is None:
                    continue
                side.setdefault(k, set()).add(norm_key(row[ci] if ci < len(row) else None) in want)
            if not side:
                continue
            mixed = sum(1 for v in side.values() if len(v) > 1)
            keys = {k for k, v in side.items() if v == {True}}
            if not keys or mixed > 0.01 * len(side):
                continue
            tj = t.headers.index(tk)
            filled = [norm_key(row[tj] if tj < len(row) else None) for row in t.rows]
            filled = [k for k in filled if k is not None]
            if not filled or sum(1 for k in filled if k in side) < 0.95 * len(filled):
                continue
            shown = {}
            for row in lt.rows:
                k = norm_key(row[ki] if ki < len(row) else None)
                if k in keys and k not in shown:
                    shown[k] = _written(row[ki]) or str(row[ki])
            return {"col": tk, "op": "in", "values": sorted(shown.values(), key=str),
                    "via": {"table": lt.tid, "sheet": lt.sheet, "col": lc, "values": list(vals), "key": lk}}
    return None


def loose_words(analysis, cond_text: str) -> list:
    """The condition phrases of a sentence that name no value code can find ('for
    imported items'): nothing binds them."""
    got = []
    for m in _COND_PHRASE.finditer(cond_text or ""):
        words = m.group(1)
        if re.fullmatch(r"(?:a|one|each|every|all|the|these|those|them|it|our|any|its|their)", words, re.I):
            continue
        bound = any(any(_value_pattern(w).search(words) for _k, w, _p in vals) for _t, _c, vals in _vocab(analysis))
        bound = bound or any(re.search(r"(?<![\w-])" + re.escape(p), words) for _a, _b, p in _prefixes(cond_text))
        if not bound:
            got.append(m.group(0))
    return got


# a verb that keeps its object in the counts: 'keep the ZQ- originals', 'retain them' (never 'keep X out')
_KEEP_VERB = re.compile(r"\b(?:keep|keeps|keeping|retain|retains|retaining)\b(?!\s+(?:[\w'&/.-]+\s+){0,6}?out\b)"
                        r"(?!\s+the\s+(?:later|earlier|newer|older|latest|earliest|newest|oldest)\b)", re.I)


def _keep_spans(s: str, verb) -> list:
    """[(start, end)] of what each keep verb in a clause governs: from the verb to
    the next rule verb, break or end of the clause. Values there are kept in, so
    the clause's leave-out never takes them."""
    out = []
    vs = verb[0] if verb else -1
    for m in _KEEP_VERB.finditer(s or ""):
        if m.start() <= vs < m.end():
            continue
        ends = [x.start() for x in _verbs(s) if x.start() > m.end()]
        ends += [x.start() for x in _BREAK.finditer(s, m.end())]
        if vs > m.end():
            ends.append(vs)
        out.append((m.start(), min(ends) if ends else len(s)))
    return out


def _from_sentence(analysis, s: str, qid: str, family: str | None = None, said: str | None = None,
                   exact: bool = False, verb=None, sentence: str | None = None, before=(),
                   unbound: list | None = None) -> list:
    """The candidates one clause proposes, per table. said: the words the readback
    quotes; sentence: the whole sentence the clause is from, for the rows a
    pronoun stands for and a subject that scopes the rule; before: the sentences
    before it, nearest first, for a sentence that opens by pointing back ('They
    are not fee income'). exact: a value counts only when written as it is in the
    data. The rows are the values named before the words that name the totals
    ('leave X out of Y': X, never Y), or an ID prefix ('GV- lines'). A condition
    on the rows ('on cellar lines') is kept as a predicate, bound on a tab without
    that column through a key it joins; a rule whose condition binds nowhere on
    a tab is not offered there (unbound collects what was not offered)."""
    if _NEGATED.search(s):
        return []
    family = family or _family(s)
    if family not in KINDS:
        return []
    if verb is None:
        ms = _verbs(s)
        verb = (ms[0].start(), ms[0].end()) if ms else None
    fm = _FACTOR.search(s)
    factor = float(fm.group(1)) if fm else 0.0
    if family == "scale" and not factor:
        return []
    whole = sentence if sentence is not None else (said or s)
    pos = whole.find(s)
    pre = whole[:pos] if pos > 0 else ""
    post = whole[pos + len(s):] if pos >= 0 else ""
    skip = [fm.span(1)] if fm else []
    tables = [t for t in analysis.tables if _usable(analysis, t)]
    # the objects of a verb that keeps rows in ('drop the copies and keep the ZQ- originals'): never the rows
    # a leave-out in the same clause names
    kept = _keep_spans(s, verb) if family in ("exclude", "filter") else []
    skip = skip + kept
    # a sentence that points back ('They are not sales, so QX should not count'): it points back only when
    # it names no value itself
    ante = bool(before) and family in ("exclude", "filter") and bool(_PRONOUN.search(s))
    says_value = False
    # a unit rule with no condition of its own takes the one the sentence before gave the same column ('Unit
    # Cost on bar lines is the case price ... The real cost is Unit Cost divided by 12')
    prior = before[0] if family == "scale" and before else ""
    whole_kept = [(a + pos, b + pos) for a, b in kept] if pos >= 0 else []
    by_table: dict = {}
    for t in tables:
        _m, _w, at, lead_end = _scope_of(analysis, t, s, verb, pre)
        by_table[t.tid] = {"t": t, "heads": _named_headers(t, s), "cols": {}, "at": at, "lead": lead_end,
                           "pre": {}, "rest": {}, "prefix": [], "prior": {}}
    # a unit rule's condition may sit anywhere in its sentence ('Cost Each on cellar lines is the case price,
    # ..., so the cost is Cost Each divided by 12'): the rest of the sentence, less its column names
    rest_skip = ([(pos, pos + len(s))] + ([(pos + fm.start(1), pos + fm.end(1))] if fm else [])) if pos >= 0 else []
    for t, c, vals in _vocab(analysis):
        info = by_table.get(t.tid)
        if info is None:
            continue
        spans = skip + [(a, b) for a, b, _h in info["heads"]] + ([(0, info["lead"])] if info["lead"] else [])
        whole_heads = [(a, b) for a, b, _h in _named_headers(t, whole)] if family == "scale" else []
        prior_heads = [(a, b) for a, b, _h in _named_headers(t, prior)] if prior else []
        for k, w, pat in vals:
            m = _hits(pat, s, spans, info["at"], exact, w)
            if m:
                info["cols"].setdefault(c.header, []).append((m.start(), k, w, m.end()))
                continue
            x = _hits(pat, pre, [], len(pre), exact, w) if pre else None
            if x and family in ("exclude", "filter", "map"):
                info["pre"].setdefault(c.header, []).append((x.start(), k, w, x.end()))
            if ante and not says_value and _hits(pat, whole, whole_kept, len(whole), exact, w) is not None:
                says_value = True
            if family == "scale" and pos >= 0:
                y = _hits(pat, whole, rest_skip + whole_heads, len(whole), exact, w)
                if y:
                    info["rest"].setdefault(c.header, []).append((y.start(), k, w, y.end()))
                z = _hits(pat, prior, prior_heads, len(prior), exact, w) if prior else None
                if z:
                    info["prior"].setdefault(c.header, []).append((z.start(), k, w, z.end()))
    # an ID prefix the rows are named by ('leave GV- lines out'), before the words that name the totals; or
    # named earlier in the sentence for a pronoun to stand for ('GV- lines are not sales; leave them out')
    pre_prefixes = _prefixes(pre) if pre and family in ("exclude", "filter") else []
    for tid, info in by_table.items():
        info["pre_prefix"] = []
        for a0, b0, p in _prefixes(s):
            if a0 >= info["at"] or any(a0 < b and a < b0 for a, b in skip):
                continue
            cond = _prefix_cond(analysis, info["t"], p, s)
            if cond is not None:
                info["prefix"].append((a0, b0, cond))
        for a0, b0, p in pre_prefixes:
            cond = _prefix_cond(analysis, info["t"], p, pre)
            if cond is not None:
                info["pre_prefix"].append((a0 - len(pre) - 1, a0 - len(pre) - 1, cond))
    if ante and not says_value and (any(info["prefix"] for info in by_table.values()) or pre_prefixes
                                    or any(_prefixes(whole[b:]) for _a, b in whole_kept[-1:])):
        says_value = True
    # a unit rule said with no condition anywhere in its sentence, after a sentence that names the same column
    # with one: that condition
    if prior and not any(info["cols"] or info["rest"] or info["prefix"] for info in by_table.values()) \
            and not loose_words(analysis, whole) \
            and {h for x in tables for _a, _b, h in _named_headers(x, s)} & \
            {h for x in tables for _a, _b, h in _named_headers(x, prior)}:
        for tid, info in by_table.items():
            info["rest"] = dict(info["prior"])
            for _a0, _b0, p in _prefixes(prior):
                cond = _prefix_cond(analysis, info["t"], p, prior)
                if cond is not None:
                    info["prefix"].append((len(s), len(s), cond))
    # values named on some tab, for a tab that lacks them (bound through a key, or not offered there)
    named_any = {}
    for tid, info in by_table.items():
        src = info["cols"] or (info["rest"] if family == "scale" else {})
        for h, v in src.items():
            named_any.setdefault((tid, h), []).extend(w for _p, _k, w, _e in v)
    # words that set a condition naming no value anywhere ('for imported items'): nothing binds them
    def loose_in(cond_text: str) -> list:
        return loose_words(analysis, cond_text)
    loose = loose_in(whole) if family == "scale" else []
    # 'leave out of X' with nothing between: the rows are the ones the sentence named before it
    implied = bool(verb) and re.fullmatch(r"(?:leav(?:e|es|ing)|tak(?:e|es|ing)|left|taken)\s+out",
                                          s[verb[0]:verb[1]], re.I) is not None
    out = []
    named_heads_all = {h for x in tables for _a, _b, h in _named_headers(x, s)}
    for tid, info in by_table.items():
        t, heads, cols = info["t"], info["heads"], dict(info["cols"])
        named = {h for _a, _b, h in heads}
        via = None
        if not cols and info["pre"] and (_PRONOUN.search(s[:info["at"]]) or implied):
            cols = info["pre"]                  # 'Fees count in spend; leave them out of ...': them is Fees
        if not cols and not info["prefix"] and info["pre_prefix"] and (_PRONOUN.search(s[:info["at"]]) or implied):
            info["prefix"] = info["pre_prefix"]     # 'SKUs starting with GV- ...; leave them out': them is GV-
        if not cols and not info["prefix"] and before and family in ("exclude", "filter") \
                and _PRONOUN.search(s[:info["at"]]) and not says_value:
            cols = _antecedent(analysis, t, before, exact)     # 'They are not fee income': the sentence before
        if family == "map":
            cols = _map_values(cols, info["pre"], s, verb)
        if family == "scale":
            if named_heads_all and not named:
                continue                        # the sentence names a column this tab does not have
            if not cols and info["rest"]:
                cols = info["rest"]
            if not cols and not info["prefix"]:
                other = [(analysis.table(x), h, v) for (x, h), v in named_any.items() if x != tid]
                if other:
                    via = _join_bind(analysis, t, other)
                    if via is None:
                        if unbound is not None and named:
                            unbound.append({"table": tid, "said": said or s, "source": qid,
                                            "words": ", ".join(sorted({w for _t, _h, v in other for w in v})[:4])})
                        continue
                if loose:
                    if unbound is not None and named:
                        unbound.append({"table": tid, "said": said or s, "source": qid, "words": loose[0]})
                    continue
        elif family in ("exclude", "filter") and (cols or info["prefix"]) and (lz := loose_in(s[:info["at"]])):
            # rows named with a condition code cannot find: never the rows without it
            if unbound is not None:
                unbound.append({"table": tid, "said": said or s, "source": qid, "words": lz[0]})
            continue
        elif family in ("exclude", "filter") and not cols and not info["prefix"]:
            # a value of a lookup tab ('leave the Paper items out'), reaching the lines through its key
            other = [(analysis.table(x), h, v) for (x, h), v in named_any.items() if x != tid]
            via = _join_bind(analysis, t, other, lookup_only=True) if other else None
            if via is None:
                continue
        if not cols and not info["prefix"] and via is None and not (family == "scale" and named):
            continue
        # a value found in two columns belongs to the one the sentence names; when it names
        # neither, each column is its own candidate and the owner picks
        where_: dict = {}
        for h, hits in cols.items():
            for p, _k, _w, _e in hits:
                where_.setdefault(p, []).append(h)
        for p, hs in where_.items():
            keep = [h for h in hs if h in named][:1]
            if len(hs) > 1 and keep:
                for h in hs:
                    if h not in keep:
                        cols[h] = [x for x in cols[h] if x[0] != p]
        cols = {h: sorted(v) for h, v in cols.items() if v}
        conds = [{"col": h, "op": "in", "values": list(dict.fromkeys(w for _p, _k, w, _e in v))} for h, v in cols.items()]
        # a conjunction joins columns the sentence names apart, never two readings of one word
        holders: dict = {}
        for h, v in cols.items():
            for p, _k, _w, _e in v:
                holders.setdefault(p, []).append(h)
        own = [cond for cond, v in zip(conds, cols.values()) if any(holders[p] == [cond["col"]] for p, _k, _w, _e in v)]
        # and only for rows that have both ('Q7 on the Web channel'), never for values listed with 'and'
        # or a comma ('fees and Q7 excluded' is two rules, each for every row it names)
        seq = sorted((p, e, h) for h, v in cols.items() if h in {c["col"] for c in own} for p, _k, _w, e in v)
        text = s if info["cols"] else (pre or s)
        joined = all(not _LIST_GAP.search(text[e1:p2]) for (_p1, e1, h1), (p2, _e2, h2) in zip(seq, seq[1:])
                     if h1 != h2)
        prefix_conds = [cond for _a, _b, cond in info["prefix"]
                        if not any(c["col"] == cond["col"] for c in conds)]
        conds += prefix_conds
        own += prefix_conds
        if via is not None:
            conds, own = [via], [via]
        metrics, words, _at, _lead = _scope_of(analysis, t, s, verb, pre)
        metrics = [h for h in metrics if h not in cols]
        col_scope, other_words = _col_scope(analysis, t, s, verb, pre)
        words = words or other_words
        rules = []
        if family in ("exclude", "filter"):
            rules = [Rule(family, tid, [cond]) for cond in conds]
            if len(own) >= 2 and joined:
                rules.append(Rule(family, tid, own))
        elif family == "map":
            for cond in conds:
                if cond.get("op") == "in" and len(cond["values"]) >= 2:
                    label = " and ".join(cond["values"])
                    rules.append(Rule("map", tid, [cond], {"col": cond["col"],
                                                           "to": {str(norm_key(v)): label for v in cond["values"]}}))
        elif family == "scale":
            targets = metrics[:1] or ([] if named_heads_all else _price_cols(analysis, t))
            for target in targets:
                rules.append(Rule("scale", tid, conds, {"col": target, "by": factor}))
            metrics = [m for m in metrics if m not in targets]
        # a leave-out said beside a clause that keeps the same rows in ('count them in spend, leave them
        # out of ...') is never every count and total: kept for the words it names, or not proposed
        keeps = family == "exclude" and not words and not col_scope and _keeps(whole, s, cols, info["prefix"])
        if keeps:
            rest = s[info["at"]:].strip() if info["at"] < len(s) else ""
            if not rest:
                continue
            words = _scope_words(rest)
        for r in rules:
            r.source = qid
            own_scope = _money_scope(analysis, t, r, list(col_scope))
            if words and r.kind in ("exclude", "filter"):
                r.scope = [words]
            elif own_scope and r.kind in ("exclude", "filter"):
                r.scope = list(own_scope)
            kept = words if r.kind in ("exclude", "filter") else ""
            out.append(dict(effect(analysis, r), rule=r, said=said or s, source=qid,
                            scope=[] if kept else (own_scope or metrics[:1]), scope_words=kept))
    # the same words found on several tabs belong to the tabs the sentence names ('... on Spoilage'), when it names any
    tabs = {c["rule"].table for c in out}
    if len(tabs) > 1:
        named = {t.tid for t in tables if t.tid in tabs and _TAB_SAID(t.sheet).search(whole)}
        if named:
            out = [c for c in out if c["rule"].table in named]
    return out


def _TAB_SAID(sheet: str):
    """A tab's name said as a tab in typed words: as written, after 'on', 'in', 'from', 'to', 'the', 'both',
    'and' or 'or', or before 'tab' or 'sheet'."""
    s = re.escape(str(sheet))
    return re.compile(r"(?:\b(?:on|in|from|to|the|both|and|or)\s+|,\s*)" + s + r"(?![\w-])|(?<![\w-])" + s
                      + r"\s+(?:tab|sheet)\b")


def _keeps(whole: str, s: str, cols: dict, prefix: list) -> bool:
    """The sentence also keeps these rows in ('Fees count in spend; leave them out
    of price comparisons'): a keep clause that names one of the clause's values, or
    one the clause points back to with a pronoun."""
    for m in _KEEP_CLAUSE.finditer(whole or ""):
        lo = max([0] + [x.end() for x in _BREAK.finditer(whole, 0, m.start())])
        nxt = _BREAK.search(whole, m.end())
        piece = whole[lo:nxt.start() if nxt else len(whole)]
        if s.strip() and s.strip() in piece:
            continue                      # the keep words are this clause's own ('count them in, leave ... out')
        vals = [w for v in cols.values() for _p, _k, w, _e in v] + [c["values"][0] for _a, _b, c in prefix]
        if any(_value_pattern(str(w)).search(piece) for w in vals) or _PRONOUN.search(s) or \
                _PRONOUN.search(piece):
            return True
    return False


def _map_values(cols: dict, pre: dict, s: str, verb) -> dict:
    """The values a combine sentence makes one: those in its clause, and for 'same
    <noun> as X' or 'combine them' the value named just before it in the same
    sentence ('WRX = Westridge (...; same site as WR, combine them)')."""
    out = {h: list(v) for h, v in cols.items()}
    same_as = bool(verb) and re.match(r"same\s+", s[verb[0]:verb[1]], re.I) is not None and \
        re.search(r"\bas$", s[verb[0]:verb[1]], re.I) is not None
    pointing = bool(_PRONOUN.search(s))
    for h, hits in pre.items():
        have = {k for _p, k, _w, _e in out.get(h, [])}
        extra = [x for x in hits if x[1] not in have]
        if not extra:
            continue
        if same_as and len(out.get(h, [])) == 1:
            out[h] = [max(extra)] + out[h]            # the nearest value before 'same ... as'
        elif pointing and len(out.get(h, [])) < 2:
            out[h] = extra + out.get(h, [])
    return out


def _antecedent(analysis, t, before, exact: bool) -> dict:
    """What a sentence that opens by pointing back ('They are not fee income') is
    about: the values its sentence before names first (its subject, before any
    ';'); or, when that one names none but carries on from the one before ('Holds
    go into an escrow account.' after '4410 is an escrow hold.'), that one's. A
    category's value met as a plain lowercase word ('... sent new lamps') is a
    common noun there, never the rows the pronoun stands for."""
    prev = [b for b in (before or ()) if b][:2]
    for n, b in enumerate(prev):
        if n == 1 and not (_lead_stems(prev[0]) & _stems_of(b)):
            break
        head = re.split(r";", b, maxsplit=1)[0]
        got: dict = {}
        for tt, c, vals in _vocab(analysis):
            if tt.tid != t.tid:
                continue
            for k, w, pat in vals:
                m = _hits(pat, head, [], len(head), exact, w)
                if m and not _common_use(m.group(0), w):
                    got.setdefault(c.header, []).append((m.start(), k, w, m.end()))
        if got:
            return got
        if n == 0 and any(_hits(pat, head, [], len(head), exact, w) is not None
                          for tt, c, vals in _vocab(analysis) for k, w, pat in vals):
            return {}                     # the sentence before names its subject on another tab
    return {}


def _common_use(said: str, value: str) -> bool:
    """A value written with capitals met as an all-lowercase word ('lamps' for the
    category 'Lamps'): an ordinary noun, not the value."""
    return said.islower() and not str(value).islower() and not any(ch.isdigit() for ch in said)


def _stem5(w: str) -> str:
    """A word's first five letters, a plural 's' taken off first ('Holds' and 'hold' meet)."""
    w = w.lower()
    return (w[:-1] if len(w) > 4 and w.endswith("s") and not w.endswith("ss") else w)[:5]


def _stems_of(text: str) -> set:
    return {_stem5(w) for w in re.findall(r"[a-z]{4,}", (text or "").lower())}


def _lead_stems(text: str) -> set:
    """The stem of a sentence's first word that is not a small word ('Holds go into ...': holds)."""
    for w in re.findall(r"[A-Za-z]+", text or ""):
        if len(w) >= 4 and w.lower() not in ("they", "them", "their", "these", "those", "this", "that", "every",
                                             "each", "also", "then", "some"):
            return {_stem5(w)}
    return set()


def _price_cols(analysis, t) -> list:
    """The columns a unit rule may divide when the sentence names none: this
    table's price, a money column that is not summed; else the factor that is not
    a whole count in a count times price equals total triple, found from the
    values; else up to 2 number columns, each its own readback option."""
    pbr = (analysis.playbook or {}).get("roles", {})
    for rid, r in analysis.detection["roles"].items():
        role = pbr.get(rid) or {}
        if r.get("table") == t.tid and r.get("col") is not None and role.get("unit") == "currency" \
                and role.get("additive") is False:
            return [r["header"]]
    nums = [c for c in analysis.cols[t.tid] if c.type == "number" and c.semantic == "metric"
            and not c.sensitive and not _RATE.search(str(c.header))]
    rows = t.rows[:2000]
    whole = {c.j: all(float(row[c.j]).is_integer() for row in rows if c.j < len(row) and _num(row[c.j]))
             for c in nums}
    for x in nums:
        for n, q in enumerate(nums):
            for p in nums[n + 1:]:
                if x.j in (q.j, p.j):
                    continue
                trio = [(row[q.j], row[p.j], row[x.j]) for row in rows
                        if max(q.j, p.j, x.j) < len(row) and _num(row[q.j]) and _num(row[p.j]) and _num(row[x.j])]
                # a product on at least 90% of rows: the count is the whole-number factor
                if len(trio) >= 5 and sum(1 for a, b, c in trio if abs(a * b - c) <= 0.0051) >= 0.9 * len(trio):
                    return [c.header for c in [c for c in (q, p) if not whole[c.j]] or [q, p]]
    return [c.header for c in nums][:2]


def money_col(analysis, t) -> tuple:
    """(the column a rule's money is counted in, whether it is dollars): the
    playbook's size column when it is on this table, else a summed money column,
    else any bound amount that is not a rate, else the column findings count the
    table's money in (findings.money_column), whose unit no role has named, so
    it is never said in dollars. findings read the dollar flag here too, so a
    table's money is written one way everywhere."""
    pb = analysis.playbook or {}
    pbr = pb.get("roles", {})
    roles = analysis.detection["roles"]
    order = [(pb.get("graph") or {}).get("size_by") or ""]
    order += [rid for rid in roles if (pbr.get(rid) or {}).get("unit") == "currency" and (pbr.get(rid) or {}).get("additive")]
    order += [rid for rid in roles if (pbr.get(rid) or {}).get("kind") == "metric"
              and (pbr.get(rid) or {}).get("unit") in (None, "currency") and (pbr.get(rid) or {}).get("additive") is not False]
    for rid in order:
        r = roles.get(rid)
        if r and r.get("table") == t.tid and r.get("col") is not None and r["col"].type == "number" \
                and not _RATE.search(r["header"]):
            return r["header"], (pbr.get(rid) or {}).get("unit") == "currency"
    from .findings import money_column
    m = money_column(analysis, t)
    return (m.header, False) if m is not None else ("", False)


def effect(analysis, r: Rule) -> dict:
    """What a rule touches, counted over all rows: {rows, sum, col, money, total,
    after}. rows: the rows it names (pairs: pairs removed; dedupe: repeats
    removed); sum: their money; total: all rows' money; after: the money once
    this rule alone is applied."""
    t = analysis.table(r.table)
    idx = {h: j for j, h in enumerate(t.headers)}
    col, money = money_col(analysis, t)
    if r.kind in ("scale", "fill", "unit", "adjust") and r.values.get("col") in idx:
        col, money = r.values["col"], money if col == r.values["col"] else _is_money(analysis, t, r.values["col"])
    elif r.kind in ("exclude", "filter", "pair", "dedupe") and r.valid(idx):
        col, money = _carried_by(analysis, t, r, idx, col, money)
        if col and col not in r.scope and _not_a_total(analysis, t, col):
            # a price, or a lookup tab's column: never summed; the amount beside it, else the rows alone
            col, money = _amount_beside(analysis, t, col)
    js = idx.get(col)
    n = s = total = 0.0
    if not r.valid(idx):
        return {"rows": 0, "sum": 0.0, "col": col, "money": money, "total": 0.0, "after": 0.0}
    for row in t.rows:
        v = row[js] if js is not None and js < len(row) and _num(row[js]) else 0.0
        total += v
        if r.matches(row, idx):
            n += 1
            s += v
    ruled = apply([Rule.from_dict(dict(r.to_dict(), confirmed=True, scope=[]))], t, analysis.cols[t.tid])
    after = sum(row[js] for _i, row in ruled if js is not None and js < len(row) and _num(row[js]))
    if r.kind == "pair":
        n = (t.n_rows - len(ruled)) / 2
    elif r.kind == "dedupe":
        n, s = t.n_rows - len(ruled), total - after
    elif r.kind == "adjust":
        s = total - after                 # what the rule takes off the column on its rows
    return {"rows": int(n), "sum": s, "col": col if js is not None else "", "money": money, "total": total,
            "after": after}


def lookup_key(analysis, tid: str) -> str:
    """The key of a lookup tab (a list other tables look values up in: one of its
    columns is unique, and a longer table's column joins it on 95% or more of
    rows), else ''."""
    memo = getattr(analysis, "_lookup_memo", None)
    if memo is None:
        memo = {}
        try:
            analysis._lookup_memo = memo
        except Exception:  # noqa: BLE001
            pass
    if tid in memo:
        return memo[tid]
    t = next((x for x in analysis.tables if x.tid == tid), None)
    got = ""
    for j in (getattr(analysis, "joins", None) or []) if t is not None else []:
        if j.get("rows_matched", 0) < 0.95:
            continue
        for side, other in (("to", "from"), ("from", "to")):
            if j.get(f"{side}_table") != tid:
                continue
            c = analysis.col(tid, j.get(f"{side}_col"))
            ot = next((x for x in analysis.tables if x.tid == j.get(f"{other}_table")), None)
            if c is not None and getattr(c, "unique", False) and ot is not None and ot.n_rows > t.n_rows:
                got = c.header
                break
        if got:
            break
    memo[tid] = got
    return got


def _amount_beside(analysis, t, price: str) -> tuple:
    """(a table's amount column other than a price, whether it is dollars): the
    number column with the largest total that is no price, rate, count or code;
    ('', False) on a lookup tab or when there is none."""
    if lookup_key(analysis, t.tid):
        return "", False
    from .analyze import _ADJUST_HEADER
    from .findings import _COUNT_WORDS
    # never an amount taken off (a discount): that is no size of the rows
    cands = [c for c in analysis.cols.get(t.tid, []) if c.type == "number" and c.semantic == "metric" and not c.codes
             and not c.sensitive and c.header != price and not _RATE.search(str(c.header))
             and not _COUNT_WORDS.search(str(c.header)) and not _ADJUST_HEADER.search(str(c.header))
             and not _not_a_total(analysis, t, c.header)]
    best = max(cands, key=lambda c: abs(c.sum or 0), default=None)
    return (best.header, _is_money(analysis, t, best.header)) if best is not None else ("", False)


def _not_a_total(analysis, t, col: str) -> bool:
    """A money column no count or total sums: any column of a lookup tab, a price
    role said not to add up, or the table's unit price when no role says it is an
    amount."""
    if not col:
        return False
    if lookup_key(analysis, t.tid):
        return True
    pbr = (analysis.playbook or {}).get("roles", {})
    bound = [(pbr.get(rid) or {}) for rid, r in analysis.detection["roles"].items()
             if r.get("table") == t.tid and r.get("header") == col]
    if any(b.get("additive") is False for b in bound):
        return True
    if any(b.get("additive") for b in bound):
        return False
    pc = analysis._price_col(t) if hasattr(analysis, "_price_col") else None
    return pc is not None and getattr(pc, "header", None) == col


def _carried_by(analysis, t, r: Rule, idx: dict, col: str, money: bool) -> tuple:
    """(the money column a rule's rows carry their money in, whether it is dollars):
    the table's money column, unless it is zero on every row the rule names and
    another money column is not ('Status is X' on payment rows is measured in
    Payment, never '$0 of Charge'). A rule scoped to named columns is measured in
    the first of them."""
    heads = [h for h in r.scope if h in idx]
    if heads:
        return heads[0], _is_money(analysis, t, heads[0]) or (heads[0] == col and money)
    j = idx.get(col)
    rows = [row for row in t.rows if r.matches(row, idx)]
    if not rows:
        return col, money
    if j is not None and any(j < len(row) and _num(row[j]) and row[j] for row in rows):
        return col, money
    best = None
    for c in analysis.cols[t.tid]:
        if c.type != "number" or c.semantic != "metric" or c.codes or c.sensitive or _RATE.search(str(c.header)) \
                or c.header == col:
            continue
        got = sum(abs(row[c.j]) for row in rows if c.j < len(row) and _num(row[c.j]))
        if got and (best is None or (_is_money(analysis, t, c.header), got) > best[0]):
            best = ((_is_money(analysis, t, c.header), got), c.header)
    if best is None:
        return col, money
    return best[1], best[0][0] or money


def _is_money(analysis, t, header: str) -> bool:
    pbr = (analysis.playbook or {}).get("roles", {})
    return any(r.get("table") == t.tid and r.get("header") == header and (pbr.get(rid) or {}).get("unit") == "currency"
               for rid, r in analysis.detection["roles"].items())


def where(r: Rule) -> str:
    """The rows a rule names, in words: 'Location is Q7', 'Site is A and Unit is 7'."""
    parts = []
    for c in r.predicate:
        raw = [str(v) for v in c.get("values") or []]
        # each value once, with how many times it was listed when more than once ('#4471 (3 times)')
        seen: dict = {}
        for v in raw:
            seen[v] = seen.get(v, 0) + 1
        vals = [v if n == 1 else f"{v} ({n} times)" for v, n in seen.items()]
        shown = vals[0] if len(vals) == 1 else ", ".join(vals[:-1]) + " or " + vals[-1] if vals else ""
        op = c.get("op", "in")
        via = c.get("via") or {}
        if via and op == "in":
            got = [str(v) for v in via.get("values") or []]
            said = got[0] if len(got) == 1 else ", ".join(got[:-1]) + " or " + got[-1] if got else ""
            parts.append(f"{c['col']} is one whose {via.get('col')} on {via.get('sheet')} is {said} "
                         f"({len(vals):,} value{'s' if len(vals) != 1 else ''})")
            continue
        parts.append(f"{c['col']} is blank" if op == "blank" else
                     f"{c['col']} starts with {shown}" if op == "prefix" else
                     f"{c['col']} is on {raw[0]}" if op == "between" and len(raw) >= 2 and raw[0] == raw[1] else
                     f"{c['col']} is on or before {raw[1]}" if op == "between" and len(raw) >= 2 and not raw[0] else
                     f"{c['col']} is on or after {raw[0]}" if op == "between" and len(raw) >= 2 and not raw[1] else
                     f"{c['col']} is from {raw[0]} to {raw[1]}" if op == "between" and len(raw) >= 2 else
                     f"{c['col']} is {shown}")
    return " and ".join(parts)


def topic(r: Rule) -> str:
    """What a rule is about, in a few words: 'Location Q7', 'Price where Group is G'."""
    if r.kind == "dedupe":
        return f"repeated {r.values.get('col', '')} values"
    if r.kind == "fill":
        return f"blank {r.values.get('col', '')} values"
    if r.kind == "unit":
        return f"{r.values.get('col', '')} where {where(r)}"
    if r.kind == "adjust":
        return f"{r.values.get('col', '')} minus {r.values.get('minus', '')} where {where(r)}"
    if r.kind == "scale":
        return r.values.get("col", "") + (f" where {where(r)}" if r.predicate else "")
    if r.kind == "map":
        vals, seen = [], set()
        for v in (r.predicate[0].get("values") or []) if r.predicate else []:
            if norm_key(v) not in seen:          # each value once, however many spellings the rule lists
                seen.add(norm_key(v))
                vals.append(str(v))
        return f"{r.values.get('col', '')} {' and '.join(vals)}".strip()
    if len(r.predicate) == 1 and r.predicate[0].get("via"):
        via = r.predicate[0]["via"]
        return f"{r.predicate[0]['col']} by {via.get('col')} {' and '.join(str(v) for v in via.get('values') or [])}"
    if len(r.predicate) == 1 and r.predicate[0].get("op", "in") == "in":
        vals = list(dict.fromkeys(str(v) for v in r.predicate[0].get("values") or []))
        return f"{r.predicate[0]['col']} {' and '.join(vals[:3])}" + (f" and {len(vals) - 3} more" if len(vals) > 3 else "")
    return f"rows where {where(r)}"


def map_pairs(analysis, r: Rule) -> list:
    """[(the value counted as, [the values that count as it, as the data writes
    them])] of a map rule, one entry per value it counts as. A map that makes
    its values one has one entry; a map that pairs each name with its own code,
    or each old value with its new one, has one entry per pair."""
    to = {str(k): str(v) for k, v in ((r.values or {}).get("to") or {}).items()}
    col = (r.values or {}).get("col")
    written: dict = {}
    t = next((x for x in getattr(analysis, "tables", None) or [] if x.tid == r.table), None)
    if t is not None and col in t.headers:
        j = t.headers.index(col)
        for row in t.rows:
            v = row[j] if j < len(row) else None
            if v is not None and str(v).strip():
                written.setdefault(str(norm_key(v)), str(v).strip())
    for c in r.predicate:
        for v in c.get("values") or []:
            written.setdefault(str(norm_key(v)), str(v))
    keys = list(to) or [str(norm_key(v)) for c in r.predicate for v in c.get("values") or []]
    groups: dict = {}
    for k in keys:
        tgt = to.get(k)
        if tgt is None:                   # a map with no targets makes all its values one
            tgt = written.get(keys[0], keys[0])
        vals = groups.setdefault(str(norm_key(tgt)), [tgt, []])[1]
        w = written.get(k, k)
        if w not in vals:
            vals.append(w)
    return [(tgt, vals) for tgt, vals in groups.values()]


def map_words(analysis, r: Rule, most: int = 5) -> str:
    """How a map rule counts its values, in words: 'A, a and A. are counted as
    one' when it makes them one; 'Northside counts as NS, Eastgate as EG and 2
    more pairs' when it pairs each value with its own match, never 'as one'."""
    groups = map_pairs(analysis, r)
    bits = []
    for tgt, vals in groups:
        src = [v for v in vals if norm_key(v) != norm_key(tgt)]
        if src:
            bits.append((" and ".join(src), tgt))
    if len(groups) <= 1 or not bits:
        vals = list(dict.fromkeys(str(v) for v in (r.predicate[0]["values"] if r.predicate else [])))
        said = ", ".join(vals[:-1]) + " and " + vals[-1] if len(vals) > 1 else "".join(vals)
        return f"{said} are counted as one"
    shown = bits[:most]
    words = [f"{a} {'count' if ' and ' in a else 'counts'} as {b}" if k == 0 else f"{a} as {b}"
             for k, (a, b) in enumerate(shown)]
    more = len(bits) - len(shown)
    if more:
        words.append(f"{more} more {'pair' if more == 1 else 'pairs'}")
    return ", ".join(words[:-1]) + " and " + words[-1] if len(words) > 1 else words[0]


def not_items(analysis, answers: dict) -> set:
    """Codes the owner said are not real items (fees, holding codes): kept in totals,
    left out of price comparisons. Codes the owner named win; codes the owner said
    were re-coded are real items and stay in. When the owner was asked which
    calculations they stay out of (follow_items_) and did not pick price
    comparisons, they stay in those too: the rule is never wider than the answer."""
    from .findings import _code_pairs, _slug
    out: set = set()
    for qid, a in (answers or {}).items():
        if not qid.startswith(("find_unmatched_", "grow_unmatched_")) or not isinstance(a, dict) \
                or "not_items" not in (a.get("options") or []):
            continue
        fu = (answers or {}).get("follow_items_" + qid.split("_unmatched_", 1)[1])
        if isinstance(fu, dict) and not fu.get("not_sure") and fu.get("options") \
                and "prices" not in fu.get("options"):
            continue
        if a.get("keys"):          # the codes that answer was about, not codes that showed up later
            missing = {str(k) for k in a["keys"]}
        else:
            ins = next((i for i in analysis.insights if i.get("recipe", "").startswith("unmatched:")
                        and "find_unmatched_" + _slug(i["numbers"]["from_col"]) == qid), None)
            if not ins:
                continue
            missing = {str(k) for k in ins["numbers"].get("missing_keys") or []}
        text = a.get("text", "") or ""
        named = {k for k in missing if re.search(r"(?<![\w-])" + re.escape(k) + r"(?![\w-])", text, re.I)}
        old = {norm_key(o) for o, _n, _d in _code_pairs(analysis, a)}
        out |= (named or missing) - old
    return out


# --------------------------------------------------------------------------
# what the owner sees of a rule before ticking it
# --------------------------------------------------------------------------
def heavy(analysis, c: dict) -> str:
    """'removes 48% of Charge' for a rule that would take more than a quarter of
    its table's money out of every count and total, else 'removes 34% of the
    rows' for one that takes more than a quarter of its rows (every row of a
    common code); '' otherwise. Such a rule is never recommended."""
    r = c["rule"]
    if r.kind not in ("exclude", "filter") or word_scope(analysis, r):
        return ""
    t = analysis.table(r.table)
    named = c.get("rows", 0)
    gone_rows = named if r.kind == "exclude" else t.n_rows - named
    if c.get("col") and c.get("total", 0) > 0:
        gone = c["sum"] if r.kind == "exclude" else c["total"] - c["sum"]
        share = gone / c["total"]
        if share > HEAVY:
            return f"removes {round(share * 100)}% of {c['col']}"
    if t.n_rows and gone_rows / t.n_rows > HEAVY:
        return f"removes {round(100 * gone_rows / t.n_rows)}% of the rows"
    return ""


def _option_rules(q) -> dict:
    """{option id: [Rule]} for the options of a question that would apply or propose
    rules when picked (one option may carry a rule per tab: 'leave it out' on a
    value two tabs share)."""
    out: dict = {}
    for oid, d in ((q.meta or {}).get("rules") or {}).items():
        out[oid] = [Rule.from_dict(x) for x in (d if isinstance(d, list) else [d]) if isinstance(x, dict)]
    ex = (q.meta or {}).get("exclude")
    if ex and ex.get("col"):
        for oid in ex.get("options") or []:
            out.setdefault(oid, [Rule("exclude", ex["table"], [{"col": ex["col"], "op": "in",
                                                                "values": [str(v) for v in ex.get("values") or []]}])])
    for p in (q.meta or {}).get("propose") or []:
        if p.get("rule"):
            out.setdefault(p.get("option"), [Rule.from_dict(p["rule"])])
    return {oid: rs for oid, rs in out.items() if rs}


def _fit(label: str, extra: str, n: int = 60) -> str:
    """label, then ', extra', within n characters: the row count goes first, then the
    extra is cut to its first words."""
    if extra in label:
        return label
    head = extra.split()[0] if extra.split() else ""
    if head and re.search(r", " + re.escape(head) + r"(?!\w)", label):
        return label                      # dressed already, the extra cut to fit: dressing again changes nothing
    for body in (label, re.sub(r"\s*\([\d,]+ rows?\)$", "", label)):
        if len(body) + 2 + len(extra) <= n:
            return f"{body}, {extra}"
    body = re.sub(r"\s*\([\d,]+ rows?\)$", "", label)
    room = n - len(body) - 2
    words = extra.split()
    short = ""
    for w in words:
        if len((short + " " + w).strip()) > room:
            break
        short = (short + " " + w).strip()
    return f"{body}, {short}" if short else body[:n]


def dress(analysis, q):
    """What the owner must see on an option that applies or proposes a rule, said
    in its label: a rule that removes more than a quarter of its table's money
    says so ('removes 48% of Charge') and is never the recommended option; two
    options whose labels read the same name their tabs ('..., on Monthly').
    Returns the question."""
    if q is None:
        return q
    by = _option_rules(q)
    if not by:
        return q
    idx = {o["id"]: o for o in q.options}
    for oid, rs in by.items():
        o = idx.get(oid)
        for r in rs:
            t = next((t for t in analysis.tables if t.tid == r.table), None)
            if o is None or t is None or not r.valid({h: j for j, h in enumerate(t.headers)}):
                continue
            note = heavy(analysis, dict(effect(analysis, r), rule=r))
            if note:
                o["label"] = _fit(o["label"], note)
                if q.recommend == oid:
                    q.recommend, q.recommend_basis = None, ""
                break
    for oid, rs in by.items():
        # a rule said about one money column ('out of loss dollars') is offered as that, never as every
        # count and total; its description says so
        o = idx.get(oid)
        cols = [h for r in rs for h in r.scope if h not in word_scope(analysis, r)]
        if o is not None and cols and any(r.kind in ("exclude", "filter") for r in rs):
            o["desc"] = re.sub(r"left out of every count and total",
                               f"left out of {' and '.join(dict.fromkeys(cols))} totals only", o.get("desc") or "")
    if any(h for rs in by.values() for r in rs for h in r.scope if h not in word_scope(analysis, r)) \
            and "to every count and total" in (q.prompt or ""):
        q.prompt = q.prompt.replace("to every count and total", "as described")
    from .findings import READBACK
    if str(q.id).startswith(READBACK):
        _dress_readback(analysis, q, by, idx)
    labels: dict = {}
    for oid, rs in by.items():
        if oid in idx and len({r.table for r in rs}) == 1:     # an option on several tabs names them itself
            labels.setdefault(idx[oid]["label"], []).append(oid)
    for same in labels.values():
        tabs = {oid: analysis.table(by[oid][0].table).sheet for oid in same
                if any(t.tid == by[oid][0].table for t in analysis.tables)}
        if len(same) < 2 or len(set(tabs.values())) < 2:
            continue
        for oid in same:
            idx[oid]["label"] = _fit(idx[oid]["label"], f"on {tabs[oid]}")
    if str(q.id).startswith(READBACK):
        _name_other_tabs(analysis, q, by, idx)
    return q


def _dress_readback(analysis, q, by: dict, idx: dict):
    """A rules readback says each rule's scope in its own label ('Outlet = QX (125
    rows), out of Net only'), never as a pick-all item of its own; a rule wider than
    a pick the owner made says so ('..., every count, not just Net'); a rule that
    takes one column off another says which ('Hours minus Overtime to 2026-04-16'); and
    a rule on a tab other than the main one names it."""
    scopes = (q.meta or {}).get("scopes") or {}
    if scopes:
        q.options = [o for o in q.options if o["id"] not in scopes]
        q.meta["scopes"] = {}
        for sid in scopes:
            idx.pop(sid, None)
    live = list(getattr(analysis, "rules", None) or [])
    for oid, rs in by.items():
        o = idx.get(oid)
        if o is None or not rs:
            continue
        r = rs[0]
        t = next((x for x in analysis.tables if x.tid == r.table), None)
        if t is None:
            continue
        if r.kind == "adjust":
            lo, hi = (list((r.predicate[0].get("values") if r.predicate else None) or ["", ""]) + ["", ""])[:2]
            when = f"to {hi}" if hi and not lo else f"from {lo}" if lo and not hi else f"{lo} to {hi}" if lo else ""
            head = f"{r.values.get('col')} minus {r.values.get('minus')} {when}".strip()
            n = re.search(r"\(([\d,]+ rows?)\)$", o["label"])
            if not o["label"].startswith(head):
                o["label"] = (head[:60 - len(n.group(0)) - 1] + " " + n.group(0)) if n else head[:60]
        elif r.kind in ("exclude", "filter"):
            cols = [h for h in r.scope if h not in word_scope(analysis, r)]
            if cols:
                o["label"] = _fit_whole(o["label"], [f"out of {' and '.join(dict.fromkeys(cols))} only",
                                                     f"{' and '.join(dict.fromkeys(cols))} only"])
            elif not r.scope:
                wider = _narrower(analysis, {"rule": r}, live)
                if wider:
                    o["label"] = _fit_whole(o["label"], [f"every count, not just {' and '.join(wider)}",
                                                         f"not just {' and '.join(wider)}"])


def _fit_whole(label: str, notes: list) -> str:
    """The label with the first note that fits whole within 60 characters; a note
    cut to its first words could say the opposite ('every count, not'), so none
    goes in when none fits."""
    for note in notes:
        got = _fit(label, note)
        if note in got:
            return got
    return label


def _name_other_tabs(analysis, q, by: dict, idx: dict):
    """A readback rule on a tab other than the main one names its tab."""
    main = getattr(getattr(analysis, "main_table", None), "tid", None)
    for oid, rs in by.items():
        o = idx.get(oid)
        if o is None or not rs or main is None or len({x.table for x in rs}) != 1 or rs[0].table == main:
            continue
        t = next((x for x in analysis.tables if x.tid == rs[0].table), None)
        if t is not None and f"on {t.sheet}" not in o["label"]:
            o["label"] = _fit_whole(o["label"], [f"on {t.sheet}"])


def scoped_readback(analysis, answers: dict):
    """The rules a typed sentence kept for one calculation ('leave fees out of
    rebate math'), read back as that: each option one rule with its rows, and the
    prompt names the calculation in the owner's words. A tick keeps the rule for
    that calculation only; no count or total in the brain changes. None when there
    is nothing to read back or the extra prompts are spent."""
    from .findings import READBACK, _hash, _rule_label
    from .interview import Q
    from .recipes import fmt_money, fmt_num
    if sum(1 for k in answers or {} if k.startswith(READBACK)) >= MAX_READBACKS:
        return None
    props = [c for c in proposals(analysis, answers, scoped=True) if c.get("scope_words")]
    if not props:
        return None
    words = props[0]["scope_words"]
    shown = sorted([c for c in props if c["scope_words"] == words], key=lambda c: -c["rows"])[:3]
    opts, meta_rules = [], {}
    for k, c in enumerate(shown, 1):
        f = fmt_money if c["money"] else (lambda x: fmt_num(round(x, 2)))
        adds = f"; they add {f(c['sum'])} to {c['col']}" if c["col"] else ""
        opts.append({"id": f"r{k}", "label": _rule_label(c),
                     "desc": f"Rows where {where(c['rule'])}: left out of {words}{adds}"})
            # (what the other totals do is the tool's to say, in the prompt; an option's description is
            # quoted in the owner's note when ticked, so it says only what the owner ticks)
        meta_rules[f"r{k}"] = c["rule"].to_dict()
    rows = 0
    for tid in dict.fromkeys(c["rule"].table for c in shown):
        t = analysis.table(tid)
        idx = {h: j for j, h in enumerate(t.headers)}
        mine = [c["rule"] for c in shown if c["rule"].table == tid]
        rows += sum(1 for row in t.rows if any(r.valid(idx) and r.matches(row, idx) for r in mine))
    first = shown[0]["rule"]
    col = first.predicate[0]["col"] if first.predicate else first.values.get("col", "")
    vals = [str(v) for v in (first.predicate[0].get("values") or [])][:6] if first.predicate else []
    k = len(shown)
    q = Q(f"{READBACK}_{_hash([str(ident(analysis, c['rule'])) for c in shown])}", "Your rules",
          f"From what you typed, {k} rule{'s' if k != 1 else ''} would leave {rows:,} row{'s' if rows != 1 else ''} "
          f"out only for {words}. Which should I keep that way? Pick all that apply.",
          opts, why="Ticked or not, every other count and total keeps these rows.", multi=True, kind="rule",
          priority=1, source="finding",
          fact={"kind": "rule", "class": "data", "depends": [],
                "statement": f"Rules the owner said to keep only for {words}: {{answer_labels}}."},
          meta={"rules": meta_rules, "scopes": {},
                "about": dict({"table": first.table, "col": col, "aspect": "treatment"}, **({"values": vals} if vals else {})),
                "tables": list(dict.fromkeys(c["rule"].table for c in shown))})
    q.gated = True
    return dress(analysis, q)
