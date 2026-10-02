"""Questions about THIS file: every oddity code found becomes a question only
the owner can answer ("544 lines have an item code that isn't in your price
file, like SVC-X and ZQ20417. What are they?"), and an answer that opens a
new thread gets a follow-up ("ZQ20417 and ZQ20988 have the same description.
Same product?"). This is how the grill pulls out what lives in someone's head,
tailored to the sheet in front of it rather than a generic checklist.
"""
from __future__ import annotations

import re

from .profile import _is_num, norm_key


def _slug(s: str, n: int = 36) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(s).lower()).strip("_")[:n]


def _short(v, n: int = 40) -> str:
    from .brainzip import clean_text
    s, _ = clean_text(str(v))
    return re.sub(r"\s+", " ", s).strip()[:n]


def _join(items: list) -> str:
    items = [i for i in items if i]
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _listed(vals: list) -> str:
    """Every value when there are 8 or fewer (none hidden behind 'more'), else the
    first 3 and how many more."""
    vals = [_short(v, 30) for v in vals if v is not None and str(v) != ""]
    return _join(vals if len(vals) <= 8 else vals[:3] + [f"{len(vals) - 3:,} more"])


def _a(word: str) -> str:
    return "an" if word[:1].lower() in "aeio" else "a"


# a column of the business's places: a value that is not one of them may still be the business's own
_SITE = re.compile(r"\b(sites?|locations?|stores?|branch(es)?|outlets?|depots?|yards?|warehouses?|propert(y|ies)|"
                   r"hotels?|kitchens?|restaurants?|clinics?|offices?|plants?|facilit(y|ies)|shops?|ship to)\b", re.I)


def _ours(noun: str, peers: list | None = None) -> dict:
    """The option for a place of the business that is not one of its sites, named
    against two of the sites it is unlike when they fit."""
    peers = [str(p) for p in peers or [] if p][:2]
    for k in (2, 1, 0):
        like = f" unlike {' or '.join(peers[:k])}" if k and len(peers) >= k else ""
        label = f"Our own {noun}{like} (type what)"
        if len(label) <= 60:
            break
    return {"id": "ours", "label": label, "desc": "Type what it is"}


def _lower_first(s: str) -> str:
    return s[:1].lower() + s[1:]


def _peers(analysis, tid: str, col: str, val) -> list:
    """The two commonest other values of a column: the peers a value is unlike."""
    c = analysis.col(tid, col)
    return [str(k) for k, _n in (c.top if c is not None else []) if norm_key(k) != norm_key(val)][:2]


def _fit_desc(desc: str) -> str:
    """An option description cut to the room the ask tool shows, never mid-word."""
    from .interview import DESC_MAX, _cut
    return _cut(desc, DESC_MAX)


HEADER_MAX = 12           # the ask tool's room for a question's header


def header_words(*parts) -> str:
    """A question's header in whole words, never cut mid-word ('Blank Adjustm'):
    all the parts when they fit, else the last part (the name) alone, else as
    many of its whole words as fit, else the first part. A single word longer
    than the room is the only thing ever cut."""
    parts = [" ".join(str(p).split()) for p in parts if str(p or "").strip()]
    full = " ".join(parts)
    if len(full) <= HEADER_MAX:
        return full
    last = parts[-1] if parts else ""
    if len(last) <= HEADER_MAX:
        return last
    fit = ""
    for w in last.split():
        if len((fit + " " + w).strip()) > HEADER_MAX:
            break
        fit = (fit + " " + w).strip()
    if fit:
        return fit
    if parts and len(parts[0]) <= HEADER_MAX:
        return parts[0]
    return full[:HEADER_MAX]


_NOUNS = "rows|lines|records|values|codes|numbers|cells|items|entries|txns|transactions|orders|periods|months|" \
          "weeks|days|pairs|groups|copies|times|rules|formulas|inputs|questions"
_ONE = re.compile(r"(?<![\d.,$])\b1 (" + _NOUNS + r")\b(?: (have|are|were|appear|come|use|sit|fall|differ)\b)?")
_VERB = {"have": "has", "are": "is", "were": "was", "appear": "appears", "come": "comes", "use": "uses",
         "sit": "sits", "fall": "falls", "differ": "differs"}


def one_is_one(text):
    """'1 rows have' -> '1 row has': a count of one takes the singular, whatever
    template wrote it. Anything else is left as it is."""
    if not isinstance(text, str) or "1 " not in text:
        return text

    def sing(m):
        n = m.group(1)
        noun = n[:-3] + "y" if n.endswith("ies") else n[:-1]
        return f"1 {noun}" + (f" {_VERB[m.group(2)]}" if m.group(2) else "")
    return _ONE.sub(sing, text)


def values_of(header: str, n: int = 2) -> str:
    """'3 Entered By values', '1 Site', '4 Sites': the header counted, in
    whole words and singular when the count is 1 (see recipes.plural)."""
    from .recipes import plural
    return plural(str(header).strip(), n)


_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _month(v: str) -> str:
    m = re.fullmatch(r"(\d{4})-(\d{2})-\d{2}.*", str(v))
    return f"{_MONTHS[int(m.group(2)) - 1]} {m.group(1)}" if m else str(v)


def _rid(analysis, tid: str, header: str) -> str | None:
    for rid, r in analysis.detection["roles"].items():
        if r.get("table") == tid and r.get("header") == header:
            return rid
    return None


def _named(n: dict) -> list:
    return list(n.get("named") or n.get("cells") or [])


def _about(tid: str, col: str, aspect: str, values=None) -> dict:
    """What a question is about: the table, the column (or the row of a model),
    the values it names and which aspect of them it settles (meaning, treatment,
    unit, grain or scope)."""
    out = {"table": tid, "col": col, "aspect": aspect}
    if values:
        # every value it names (up to 30): which values an answer settles is part of what it is about
        out["values"] = [str(v) for v in values][:30]
    return out


def _about_dep(analysis, ins: dict, aspect: str, values=None) -> dict:
    """The table and column, or model row, a finding's first dependency names: the
    table that holds the cells the finding names when it names any (a label
    repeated on two blocks of one tab binds to the block the cells are in), else
    the first table on the tab with that row label or header."""
    n = ins.get("numbers") or {}
    cells = [c for c in (_cells_of(n) if isinstance(n, dict) else []) if "!" in str(c)]
    rows = [r for r in (n.get("row_index") or []) if isinstance(r, int)] if isinstance(n, dict) else []
    for sheet, name in ins.get("depends") or []:
        tabs = [t for t in analysis.tables if t.sheet == sheet and (name in t.headers or name in analysis.row_labels(t))]
        for x in cells:
            s, _, ref = str(x).rpartition("!")
            rc = _cell_rc(ref)
            hit = next((t for t in tabs if s == t.sheet and rc and t.top <= rc[0] <= t.last_row and rc[1] in t.cols),
                       None)
            if hit is not None:
                return _about(hit.tid, name, aspect, values)
        # a block of rows (a typed plan) binds to the table its rows are in
        hit = next((t for t in tabs if rows and any(t.top <= r <= t.last_row for r in rows)), None)
        if hit is not None:
            return _about(hit.tid, name, aspect, values)
        if tabs:
            return _about(tabs[0].tid, name, aspect, values)
    return {}


def _cell_rc(ref: str):
    """'B25' -> (row 24, column 1), 0-based; None when it is not one cell."""
    m = re.fullmatch(r"\$?([A-Z]{1,3})\$?(\d+)", str(ref or "").strip())
    if not m:
        return None
    ci = 0
    for ch in m.group(1):
        ci = ci * 26 + ord(ch) - 64
    return int(m.group(2)) - 1, ci - 1


# --------------------------------------------------------------------------
# what code counted about how a column is written, said once in a question about it
# --------------------------------------------------------------------------
def column_context(analysis, tid: str, col: str):
    """(clause for the prompt, clause for the note) from the column's counted
    structure facts, at most one: numbers or dates read from text, spellings that
    differ only in capitals or spaces counted as one, or a minority of another
    type among its values. None when code counted nothing about how it is written."""
    got = {}
    for i in analysis.insights:
        rec = i.get("recipe", "")
        n = i.get("numbers") or {}
        if n.get("table") != tid or n.get("col") != col or not rec.startswith("structure:"):
            continue
        got.setdefault(rec.split(":")[1], n)
    n = got.get("read_from_text")
    if n:
        k = int(n.get("cells") or 0)
        ex = (n.get("examples") or [""])[0]
        what = "dates" if n.get("kind") == "date" else "numbers"
        like = f", like '{_short(ex, 20)}'," if ex else ""
        return (f"I read the {k:,} {col} value{'s' if k != 1 else ''} typed as text{like} as {what}.",
                f"The {k:,} {col} value{'s' if k != 1 else ''} typed as text{like} are read as {what}.")
    n = got.get("spellings")
    if n and n.get("variants"):
        usual, forms = n["variants"][0][0], n["variants"][0][1]
        k = int(n.get("cells") or 0)
        return (f"I count the {k:,} {col} cell{'s' if k != 1 else ''} written in other capitals or spaces (like "
                f"'{_short(forms[0], 20)}') as '{_short(usual, 20)}'.",
                f"The {k:,} {col} cell{'s' if k != 1 else ''} written in other capitals or spaces (like "
                f"'{_short(forms[0], 20)}') count as '{_short(usual, 20)}'.")
    n = got.get("mixed_types")
    if n:
        k = int(n.get("cells") or 0)
        by = n.get("by")
        only = f"; {n.get('kind')}s only for {_listed(by[1])} in {by[0]}" if by else ""
        return (f"{col} holds {k:,} {n.get('kind')}{'s' if k != 1 else ''} (like '{_short(n.get('example'), 20)}') "
                f"among {n.get('majority')}s{only}, read as written.",
                f"{col} holds {k:,} {n.get('kind')}{'s' if k != 1 else ''} (like '{_short(n.get('example'), 20)}') "
                f"among {n.get('majority')}s{only}, read as written.")
    return _percent_scale(analysis, tid, col)


_PERCENT_HEAD = re.compile(r"%|\b(percent|percentage|pct|rate)\b", re.I)


def _percent_scale(analysis, tid: str, col: str):
    """A number column whose header names a percent or a rate: how its values are
    read, said so the owner confirms it ('read as percent points: 2 means 2%', or
    as fractions: '0.02 means 2%'). None for any other column."""
    c = analysis.col(tid, col)
    if c is None or c.type != "number" or not _PERCENT_HEAD.search(str(col)):
        return None
    vals = sorted(abs(float(k)) for k, _n in c.top if _is_num(k))
    if not vals or vals[-1] > 100:
        return None
    # a bare 'rate' (a pay rate, an hourly rate) is a percent only when every value is a fraction
    if not re.search(r"%|\b(percent|percentage|pct)\b", str(col), re.I) and vals[-1] > 1:
        return None
    from .recipes import fmt_num
    if vals[-1] > 1:
        ex = next((v for v in reversed(vals) if 1 < v <= 100), vals[-1])
        said = f"{col} is read as percent points: {fmt_num(ex)} means {fmt_num(ex)}%."
    else:
        ex = next((v for v in reversed(vals) if 0 < v <= 1), vals[-1])
        said = f"{col} is read as fractions: {fmt_num(ex)} means {fmt_num(round(ex * 100, 4))}%."
    return said, said


def with_clause(prompt: str, clause: str) -> str:
    """The prompt with one more sentence of evidence, set before its question."""
    clause = (clause or "").strip()
    if not clause or clause.rstrip(". ") in prompt:
        return prompt
    q = prompt.find("?")
    cut = prompt.rfind(". ", 0, q) if q >= 0 else -1
    if q < 0 or cut < 0:
        return prompt.rstrip() + " " + clause
    return prompt[:cut + 2] + clause + " " + prompt[cut + 2:]


def _about_role(analysis, rid: str, aspect: str, values=None) -> dict:
    r = analysis.detection["roles"].get(rid) or {}
    return _about(r["table"], r["header"], aspect, values) if r.get("table") else {}


# --------------------------------------------------------------------------
# stake: how much of the file an answer can move
# --------------------------------------------------------------------------
_COUNT_WORDS = re.compile(r"\b(qty|quantity|units?|count|counts|hours?|hrs|pieces|pcs|headcount|volume|days|weeks|"
                          r"months|years|age)\b", re.I)


def money_column(analysis, t):
    """The column a table's money is counted in: a number column that is not a
    code, an ID, a rate or a count, the one with the largest total, a column
    with cents before one of whole numbers. None when the table has none."""
    from .rules import _RATE
    cands = [c for c in analysis.cols.get(t.tid, []) if c.type == "number" and c.semantic == "metric"
             and not c.codes and not c.sensitive and c.count and not _RATE.search(str(c.header))
             and not _COUNT_WORDS.search(str(c.header))]
    return max(cands, key=lambda c: (not c.integers, abs(c.sum)), default=None)


def unit_changed(analysis, answers: dict) -> dict:
    """{(table, column): the date words} for each number column the owner said
    changed meaning or unit at a date where the file changes: named in the typed
    answer to that date's question, or, when 'A column's meaning changed' is
    picked without naming one, the only number column written another way
    there. Such a column is never summed across the date as one amount."""
    out = {}
    for qid, a in (answers or {}).items():
        if not qid.startswith("find_boundary_") or not isinstance(a, dict) or a.get("not_sure"):
            continue
        tid = (a.get("about") or {}).get("table")
        b = next((i for i in analysis.insights if i.get("recipe", "").startswith("boundary:")
                  and i["numbers"]["table"] == tid and qid.endswith(i["numbers"]["date"].replace("-", ""))), None)
        t = next((x for x in analysis.tables if x.tid == tid), None)
        if b is None or t is None:
            continue
        text = str(a.get("text") or "")
        picked = a.get("options") or []
        nums = [c for c in analysis.cols[t.tid] if c.type == "number" and c.semantic == "metric"]
        moved = {c["col"] for c in b["numbers"]["changes"]}
        # a column the owner says changed: the subject of a change in a dated sentence, or one of the columns the
        # file rewrites at the date, named other than as a term of arithmetic ('quantity times unit price')
        named = [c.header for c in nums if _said_changed(c.header, text) or (
            c.header in moved and _named_not_operand(c.header, text))]
        if not named and "changed" in picked:
            moved = {c["col"] for c in b["numbers"]["changes"] if c["kind"] in ("number", "sign")}
            named = list(moved) if len(moved) == 1 else []
        # a column whose only change there is its sign is one amount written with the other sign: a unit
        # change only when the owner picked that a meaning changed or named a unit
        kinds: dict = {}
        for c in b["numbers"]["changes"]:
            kinds.setdefault(c["col"], set()).add(c["kind"])
        named = [h for h in named if kinds.get(h) != {"sign"} or "changed" in picked or _UNIT_WORDS.search(text)]
        when = _owner_date(text, b["numbers"]["when"])
        for h in named:
            out[(tid, h)] = when
    return out


_CHANGE_VERB = r"(is|was|were|means|meant|includes|included|changed|became|switched|went)"
_WHEN = re.compile(r"\b(before|after|until|from|since|starting|as of|on)\b|\d{4}|\d{1,2}/\d{1,2}|"
                   r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b", re.I)
_OPERAND = re.compile(r"\b(times|x|multiplied by|minus|plus|less|subtract|subtracting|add|adding|of|by)\s+$", re.I)


def _said_changed(header: str, text: str) -> bool:
    """A typed sentence with a date or before/after makes the column the subject of
    a change: its header, then 'is', 'was', 'means', 'includes' or 'changed'."""
    from . import privacy
    pat = re.compile(r"(?<![\w])" + re.escape(header) + r"(?![\w])\s+(?:\w+\s+)?" + _CHANGE_VERB + r"\b", re.I)
    return any(pat.search(sent) and _WHEN.search(sent) for sent in privacy.said_sentences(text or ""))


def _named_not_operand(header: str, text: str) -> bool:
    """The typed words name the column other than as a term of arithmetic: some
    mention of it that no 'times', 'minus', 'of' or 'x' comes just before, and
    no 'times', 'x' or 'minus' just after."""
    for m in re.finditer(r"(?<![\w])" + re.escape(header) + r"(?![\w])", text or "", re.I):
        before, after = text[:m.start()], text[m.end():]
        if _OPERAND.search(before) or re.match(r"\s+(times|x|minus|plus|less)\b", after, re.I):
            continue
        return True
    return False


# words that name a unit, so a typed answer about a sign-only change can still say the unit changed
_UNIT_WORDS = re.compile(r"%|\b(percent|percents|percentage|dollars?|cents?|thousands?|millions?|units?|hours?|"
                         r"pieces?|cases?|each|currency|euros?|pounds?)\b", re.I)


def _owner_date(text: str, detected: str) -> str:
    """The date the owner typed for a switch ('switched on July 1, 2025'), in the
    detected date's words, else the detected date: the owner's date wins."""
    import datetime as dt
    for rx, fmts in ((r"\b(\d{4}-\d{2}-\d{2})\b", ("%Y-%m-%d",)), (r"\b(\d{1,2}/\d{1,2}/\d{2,4})\b", ("%m/%d/%Y", "%m/%d/%y")),
                     (r"\b((?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.? \d{1,2},? \d{4})\b",
                      ("%B %d %Y", "%b %d %Y"))):
        m = re.search(rx, text or "", re.I)
        if not m:
            continue
        s = re.sub(r"[.,]", "", m.group(1)).replace("Sept", "Sep").replace("sept", "sep")
        for f in fmts:
            try:
                d = dt.datetime.strptime(s.title() if "%b" in f or "%B" in f else s, f).date()
            except ValueError:
                continue
            return f"{_MONTHS[d.month - 1]} {d.day}, {d.year}"
    return detected


def measure_name(analysis, t, answers: dict | None = None):
    """What a table's totals are totals of, as a question should name it: the
    measure the owner confirmed for a table with no amount column ('Net'), else
    its money column, unless that is an amount taken off (a discount is never
    the measure a line counts toward) or a column the owner said changed unit.
    None when no column can be named: say 'the totals'."""
    for a in (answers or {}).values():
        d = a.get("derive") if isinstance(a, dict) else None
        if d and d.get("table") == t.tid and set(a.get("options") or []) & {"yes", "gross"}:
            return d.get("name")
    m = money_column(analysis, t)
    # the money column the owner's goal names ('the rent coming in' names Actual Rent over Market Rent when only
    # its words are there), else the table's own
    goal = (answers or {}).get("goal") or {}
    said = " ".join([str(goal.get("text") or "")] + list(goal.get("labels") or [])).lower()
    if said.strip():
        from .rules import _RATE
        named = [c for c in analysis.cols.get(t.tid, []) if c.type == "number" and c.semantic == "metric"
                 and not c.codes and not _RATE.search(str(c.header)) and not _COUNT_WORDS.search(str(c.header))
                 and (ws := [w for w in re.findall(r"[a-z]+", str(c.header).lower()) if len(w) >= 4])
                 and all(re.search(r"\b" + w, said) for w in ws)]
        if len(named) == 1:
            m = named[0]
    adjust = any(i.get("recipe") == f"derive:{t.tid}" and (i.get("numbers") or {}).get("adj") == getattr(m, "header", "")
                 for i in analysis.insights)
    if m is None or adjust or (t.tid, m.header) in unit_changed(analysis, answers):
        return None
    return m.header


def money_fmt(analysis, t, m):
    """How money_column's amounts are written: in dollars only when a currency
    role names that column (the flag rules.money_col carries), else as a plain
    number, so a file whose unit is still being asked is never said in dollars."""
    from .recipes import fmt_money, fmt_num
    from .rules import _is_money
    if m is not None and _is_money(analysis, t, m.header):
        return fmt_money
    return lambda x: fmt_num(round(x, 2))


def row_stake(analysis, t, hit) -> float:
    """The share of the table's money on the rows hit(row) picks (money counted
    without its sign); the share of its rows when the table has no money."""
    m = money_column(analysis, t)
    n = k = 0
    tot = part = 0.0
    for r in t.rows:
        h = bool(hit(r))
        n += 1
        k += h
        if m is not None:
            v = r[m.j] if m.j < len(r) else None
            if _is_num(v):
                tot += abs(v)
                part += abs(v) if h else 0.0
    if m is not None and tot:
        return part / tot
    return k / n if n else 0.0


def _col_stake(analysis, tid: str, col: str, test) -> float:
    """row_stake over the rows whose value in this column passes test(value)."""
    t = next((t for t in analysis.tables if t.tid == tid), None)
    if t is None or col not in t.headers:
        return 0.0
    j = t.headers.index(col)
    return row_stake(analysis, t, lambda r: test(r[j] if j < len(r) else None))


def _cells_of(n: dict) -> list:
    cells = n.get("cells")
    if isinstance(cells, list):
        return cells
    return [f"{n['sheet']}!{n['cell']}"] if n.get("sheet") and n.get("cell") else []


def _missing_codes(analysis, n: dict) -> list:
    """[(code as the file writes it, rows)] for every value of an unmatched finding
    that has no match, most rows first."""
    t = analysis.table(n["from_table"])
    col = n["from_col"]
    miss = {str(k) for k in n.get("missing_keys") or []}
    if col not in t.headers:
        return [(str(x), 0) for x in n.get("examples") or []]
    j = t.headers.index(col)
    got: dict = {}
    for r in t.rows:
        v = r[j] if j < len(r) else None
        k = norm_key(v)
        if k is not None and str(k) in miss:
            w = got.setdefault(k, [str(v).strip(), 0])
            w[1] += 1
    return sorted(((w, k) for w, k in got.values()), key=lambda x: (-x[1], x[0]))


def _codes_words(codes: list) -> str:
    """'SVC-A, SVC-B and SVC-C: 3 codes, 18 rows': the codes a pick names, then
    how many and on how many rows. Every code when there are 8 or fewer."""
    if not codes:
        return ""
    k, rows = len(codes), sum(n for _w, n in codes)
    return _fit_desc(f"{_listed([w for w, _n in codes])}: {k:,} code{'s' if k != 1 else ''}, "
                     f"{rows:,} row{'s' if rows != 1 else ''}")


def _class_prefix(analysis, tid, col: str):
    """(ID column, prefix) when every row with a negative value in col carries an
    ID starting with one prefix (a letter code before its digits) and 90% or more
    of the rows with that prefix are negative; None otherwise."""
    t = next((x for x in analysis.tables if x.tid == tid), None)
    if t is None or col not in t.headers:
        return None
    j = t.headers.index(col)
    neg = {i for i, r in enumerate(t.rows) if j < len(r) and _is_num(r[j]) and r[j] < 0}
    if len(neg) < 5:
        return None
    pre = re.compile(r"^([A-Za-z]{1,4})[-_ ]?(?=\d)")
    for c in analysis.cols.get(t.tid, []):
        if c.semantic != "identifier" or c.type != "text" or c.sensitive:
            continue
        fam: dict = {}
        for i, r in enumerate(t.rows):
            v = r[c.j] if c.j < len(r) else None
            m = pre.match(v.strip()) if isinstance(v, str) else None
            if m:
                fam.setdefault(m.group(0).upper(), set()).add(i)
        for p, rows in fam.items():
            if neg <= rows and len(neg) >= 0.9 * len(rows):
                return c.header, p
    return None


def _finding_stake(analysis, ins: dict) -> float:
    """What share of the table's money (its rows, or a model's formula cells) the
    answer to this finding can move."""
    rec, n = ins.get("recipe", ""), ins.get("numbers") or {}
    roles = analysis.detection["roles"]
    if rec.startswith("unmatched:"):
        miss = {str(k) for k in n.get("missing_keys") or []}
        return _col_stake(analysis, n["from_table"], n["from_col"], lambda v: str(norm_key(v)) in miss)
    if rec.startswith("negatives:"):
        r = roles.get(rec.split(":", 1)[1]) or {}
        return _col_stake(analysis, r.get("table", ""), r.get("header", ""), lambda v: _is_num(v) and v < 0)
    if rec.startswith(("product_check:", "duplicates:")):
        r = roles.get(rec.split(":")[-1]) or {}
        t = next((t for t in analysis.tables if t.tid == r.get("table")), None)
        k = n.get("mismatches") if rec.startswith("product_check:") else n.get("rows")
        return min(1.0, (k or 0) / t.n_rows) if t is not None and t.n_rows else 0.0
    if rec.startswith(("blanks:", "blankmeasure:")) and "stake" in n:
        return n["stake"]          # a slice's own money, or the rows a blank measure leaves unknown
    if rec.startswith("blanks:"):
        return _col_stake(analysis, n["table"], n["col"], lambda v: v is None or (isinstance(v, str) and not v.strip()))
    if rec.startswith("oddgroup:"):
        val = norm_key(n["value"])
        return _col_stake(analysis, n["table"], n["col"], lambda v: norm_key(v) == val)
    if rec.startswith("outliers:"):          # the money on the records named
        t = next((t for t in analysis.tables if t.tid == n["table"]), None)
        if t is None:
            return 0.0
        mine = {id(t.rows[i]) for i in n.get("rows") or [] if i < len(t.rows)}
        return row_stake(analysis, t, lambda r: id(r) in mine)
    if rec.startswith("derive:"):            # every total of the money waits on it
        return 1.0
    if rec.startswith("ratio:"):             # the money on the companion lines
        val = norm_key(n["value"])
        return _col_stake(analysis, n["table"], n["col"], lambda v: norm_key(v) == val)
    if rec.startswith(("window:", "offlist:", "onset:", "contra:", "opening:", "closing:")):   # money on the lines named
        t = next((t for t in analysis.tables if t.tid == n["table"]), None)
        if t is None:
            return 0.0
        mine = {id(t.rows[i]) for i in n.get("row_ids") or [] if i < len(t.rows)}
        return row_stake(analysis, t, lambda r: id(r) in mine)
    if rec.startswith("structure:versions:"):   # the money on the lines of the keys with dated rows
        t = next((t for t in analysis.tables if t.tid == n.get("lines_table")), None)
        if t is None:
            return 0.0
        mine = {id(t.rows[i]) for i in n.get("row_ids") or [] if i < len(t.rows)}
        return row_stake(analysis, t, lambda r: id(r) in mine)
    if rec.startswith(("listprice:", "listprice_all:")):   # the lines before a run at the list, which the answer places
        t = next((t for t in analysis.tables if t.tid == n["table"]), None)
        return min(1.0, sum(x["before"] for x in n.get("runs") or []) / t.n_rows) if t is not None and t.n_rows \
            else 0.0
    if rec.startswith("exclusive:"):
        val = norm_key(n["value"])
        return _col_stake(analysis, n["table"], n["col"], lambda v: norm_key(v) == val)
    if rec.startswith("signflip:"):
        both = (n.get("before") or 0) + (n.get("after") or 0)
        return min(n.get("before") or 0, n.get("after") or 0) / both if both else 0.0
    if rec.startswith("boundary:"):          # the money on the smaller side of the date
        cut = n["date"]
        s = _col_stake(analysis, n["table"], n["col"], lambda v: hasattr(v, "year") and v.isoformat()[:10] < cut)
        return min(s, 1.0 - s)
    if rec.startswith("handoff:"):           # the money on the old names
        olds = {norm_key(p["old"]) for p in n["pairs"]}
        return _col_stake(analysis, n["table"], n["col"], lambda v: norm_key(v) in olds)
    if rec.startswith("copies:"):            # the money on the smaller family's copies
        ids = {norm_key(x) for x in n["filters"][1]["ids"]}
        return _col_stake(analysis, n["table"], n["col"], lambda v: norm_key(v) in ids)
    if rec.startswith("pairs:"):             # the money on both rows of every pair
        ids = {norm_key(x) for x in n["ids"]}
        return _col_stake(analysis, n["table"], n["col"], lambda v: norm_key(v) in ids)
    if rec.startswith("nearkey:"):           # the money on one of the two uploads of the block
        t = next((t for t in analysis.tables if t.tid == n["table"]), None)
        if t is None or len(n.get("times") or []) != 2 or not all(h in t.headers for h in n["cols"] + [n["col"]]):
            return 0.0
        first, aj = n["times"][0], t.headers.index(n["col"])
        block = [(t.headers.index(h), k, kind) for h, k, kind in zip(n["cols"], n["keys"], n["types"])]

        def at(r, j):
            return r[j] if j < len(r) else None

        def hit(r):
            v = at(r, aj)
            return (v.isoformat() if hasattr(v, "year") else str(v).strip()) == first and all(
                (hasattr(at(r, j), "year") and at(r, j).isoformat()[:10] == k) if kind == "date"
                else norm_key(at(r, j)) == norm_key(k) for j, k, kind in block)
        return row_stake(analysis, t, hit)
    if rec.startswith(("unitgroup:", "unpaid:", "nonstock:")):    # the share of the money the rows named carry
        return float(n.get("share") or 0.0)
    if rec.startswith("period:"):                    # whether the period is complete moves every total of it
        return 1.0
    if rec.startswith("structure:join_variants:"):   # the money on the rows written the other way
        odd = {str(p[0]) for p in n.get("pairs") or []}
        return _col_stake(analysis, n["table"], n["col"], lambda v: isinstance(v, str) and v in odd)
    if rec == "formula:check":
        return (n.get("failing_count") or 0) / max(1, n.get("periods") or 1)
    if rec.startswith("formula:plan_block:"):   # the share of the model's formula cells the block feeds
        return float(n.get("share") or 0.0)
    if rec.startswith("formula:short_range:"):
        return min(1.0, (n.get("rows_left_out") or 0) / max(1, n.get("last_row") or 1))
    if rec.startswith("formula:"):
        return analysis.reach_share(_cells_of(n), (ins.get("files") or [""])[0])
    return 0.0


def _worth(q, stake: float, follow: bool = False) -> float:
    """Set a question's value from its stake, on the one scale every question uses."""
    from .interview import FOLLOW_UP, worth
    q.meta["stake"] = round(float(stake), 6)
    q.value = round(worth(stake, (q.meta.get("about") or {}).get("aspect", "meaning"))
                    * (FOLLOW_UP if follow else 1.0), 3)
    return q.value


def finding_questions(analysis, answers: dict) -> list:
    from .analyze import _value_words
    from .interview import MATERIAL, Q
    out = []
    by = {i["recipe"]: i for i in analysis.insights}
    hard = set((by.get("formula:hardcoded") or {}).get("numbers", {}).get("cells", []))
    plug_named = _named((by.get("formula:typed_in_formula_row") or {}).get("numbers", {}))
    chk = by.get("formula:check") if (by.get("formula:check") or {}).get("oddity") else None
    fails = [_month(f) for f in (chk or {}).get("numbers", {}).get("failing", [])]
    # a failing check fully explained by a typed number in the same month is one question, not two
    check_in_plug = bool(fails) and all(any(f in p for p in plug_named) for f in fails)
    # ID columns whose repeats a copies or twin-pairs finding explains: one question, with the evidence
    twice = {(i["numbers"]["table"], i["numbers"]["col"]) for i in analysis.insights
             if i.get("recipe", "").startswith(("copies:", "pairs:"))}
    for ins in analysis.insights:
        rec = ins.get("recipe", "")
        n = ins.get("numbers") or {}
        q = None
        if rec.startswith("unmatched:") and n.get("examples"):
            ex = _join([_short(x, 30) for x in n["examples"][:3]])
            col, where = n["from_col"], n["where"]
            fcol = analysis.col(n["from_table"], col)
            is_code = bool(fcol and fcol.semantic == "identifier")
            pairs = []
            sheet = analysis.table(n["from_table"]).sheet
            # every code with no match, as the file writes it, with its rows: a description lists the codes a pick
            # names (the data's own values and counts, never a claim about what they are)
            miss = _missing_codes(analysis, n)
            per_pick = {}
            if is_code and analysis.playbook.get("id") == "ledger":   # accounts: suspense, retired, gaps
                shown_all = _codes_words(miss)
                k_all = sum(k for _w, k in miss)
                per_pick["suspense"] = (f"The {k_all:,} row{'s' if k_all != 1 else ''} on {sheet} with {col} "
                                        f"{_listed([w for w, _k in miss])} are a holding or suspense account, per the "
                                        "owner.")
                opts = [{"id": "suspense", "label": "A holding or suspense account", "desc": shown_all},
                        {"id": "old_codes", "label": "Old or renumbered accounts",
                         "desc": "Replaced by accounts on that list"},
                        {"id": "missing", "label": "Missing from that list", "desc": "Real ones the list should have"}]
            elif is_code:     # item codes: fees and holding codes, re-coded items, gaps
                opts = [{"id": "not_items", "label": "Not real items", "desc": _codes_words(miss)},
                        {"id": "old_codes", "label": "Replaced codes", "desc": "The same thing under a code that was replaced"},
                        {"id": "missing", "label": "Missing from that list", "desc": "Real ones the list should have"}]
                # what code already paired (an old code and the listed code with its description) and the codes
                # left over, shown under the picks they would confirm
                pairs = _code_pairs(analysis, {"about": {"table": n["from_table"], "col": col}})
                paired = {norm_key(a) for a, _b, _d in pairs}
                if pairs:
                    shown = "; ".join(f"{a} -> {b}" for a, b, _d in pairs[:3]) \
                        + (f"; and {len(pairs) - 3:,} more" if len(pairs) > 3 else "")
                    opts[1] = dict(opts[1], desc=_fit_desc(f"The same thing under a new code: {shown}"))
                    olds = [(w, k) for w, k in miss if norm_key(w) in paired]
                    k_old = sum(k for _w, k in olds)
                    # each pick names its own codes and rows, never the question's full count
                    per_pick["old_codes"] = (f"The {k_old:,} row{'s' if k_old != 1 else ''} on {sheet} with {col} "
                                             f"{_listed([w for w, _k in olds])} are replaced codes, per the owner, "
                                             f"the same thing under a new code: {shown}.")
                rest = [(w, k) for w, k in miss if norm_key(w) not in paired]
                if rest:
                    opts[0] = dict(opts[0], desc=_codes_words(rest))
                    k_rest = sum(k for _w, k in rest)
                    per_pick["not_items"] = (f"The {k_rest:,} row{'s' if k_rest != 1 else ''} on {sheet} with {col} "
                                             f"{_listed([w for w, _k in rest])} are not real items, per the owner.")
            else:             # names (vendors, customers): left off on purpose, spelled differently, gaps
                opts = [{"id": "on_purpose", "label": "Not on that list on purpose", "desc": "For example, no contract with them"},
                        {"id": "spelled", "label": "Named differently there", "desc": "Same one, different spelling"},
                        {"id": "missing", "label": "Missing from that list", "desc": "It should be there"}]
            q = Q(f"find_unmatched_{_slug(col)}", "Not listed",
                  f"{n['rows']:,} rows on {analysis.table(n['from_table']).sheet} have {_a(col)} {col} that "
                  f"isn't in {where} (for example {ex}). What are they? Pick all that apply.",
                  opts,
                  why="Rows that match nothing get dropped or double counted unless someone says what they are.",
                  multi=True, kind="definition", priority=1, source="finding",
                  fact={"kind": "definition", "class": "data", "depends": [],
                        "statement": f"The {n['rows']:,} rows whose {col} is not in {where} (like {ex}) are, "
                                     "per the owner: {answer_labels}."},
                  meta={"finding": ins, "covers": ["suspense"] if analysis.playbook.get("id") == "ledger" else [],
                        "keys": sorted(str(k) for k in n.get("missing_keys") or []),
                        "about": _about(n["from_table"], col, "meaning", n["examples"][:3])})
            if is_code and analysis.playbook.get("id") != "ledger" and pairs:
                # the pairs shown under 'Replaced codes' are one thing each once it is picked
                from .rules import Rule
                to = {}
                for a_, b_, _d in pairs:
                    to[str(norm_key(a_))] = to[str(norm_key(b_))] = b_
                q.meta["rules"] = {"old_codes": Rule("map", n["from_table"], [{"col": col, "op": "in", "values": [
                    x for a_, b_, _d in pairs for x in (a_, b_)]}], {"col": col, "to": to},
                    source=q.id).to_dict()}
                q.meta["code_pairs"] = [[a_, b_] for a_, b_, _d in pairs]
            if per_pick:
                q.fact["statements"] = per_pick
        elif rec.startswith("negatives:"):
            col = rec.split(":", 1)[1]
            label = analysis.detection["roles"].get(col, {}).get("header", col)
            k = n.get("rows", 0)
            what = f"The {k:,} rows with a negative {label}"
            # an ID prefix every one of them carries, and few other rows do: said, so the answer names the kind
            marked = _class_prefix(analysis, analysis.detection["roles"].get(col, {}).get("table"), label)
            mark = f" All {k:,} have {_a(marked[0])} {marked[0]} starting {marked[1]}." if marked else ""
            if marked:
                what += f" (every {marked[0]} starting {marked[1]})"
            # what they are and how they count, one option each: the answer needs no follow-up unless typed
            q = Q(f"find_negatives_{_slug(col)}", "Negatives",
                  f"{k:,} rows have a negative {label}.{mark} What are they?",
                  [{"id": "credits", "label": "Credits that come off the totals",
                    "desc": "Money coming back, off every total"},
                   {"id": "apart", "label": "Credits kept out of totals",
                    "desc": "Money coming back, left out of every total"},
                   {"id": "mistakes", "label": "Mistakes to fix", "desc": "They should not be negative"}],
                  why="Whether negatives net against totals changes every sum.",
                  kind="definition", priority=1, source="finding",
                  fact={"kind": "definition", "class": "data", "depends": [col],
                        "statement": f"Negative {label} rows are, per the owner: {{answer_labels}}.",
                        "statements": {
                            "credits": f"{what} are credits that come off the totals, per the owner: money coming "
                                       "back, off every total.",
                            "apart": f"{what} are credits kept out of totals, per the owner: money coming back, left "
                                     "out of every total.",
                            "mistakes": f"{what} are mistakes to fix, per the owner: they should not be negative."}},
                  meta={"finding": ins, "touches": [f"has_negatives:{col}"],
                        "about": _about_role(analysis, col, "meaning")})
        elif rec.startswith("product_check:") and n.get("mismatches"):
            _, qty, price, total = rec.split(":")
            roles = analysis.detection["roles"]
            lq, lp, lt = (roles.get(r, {}).get("header", r) for r in (qty, price, total))
            q = Q(f"find_product_{_slug(total)}", "Which is right",
                  f"On {n['mismatches']:,} rows, {lt} isn't {lq} times {lp}. Which one is right on those rows?",
                  [{"id": "product", "label": f"{lq} x {lp} is right", "desc": f"{lt} was keyed wrong"},
                   {"id": "total", "label": f"{lt} is right", "desc": f"{lq} or {lp} is off"},
                   {"id": "varies", "label": "It depends on the row", "desc": "Some each way"}],
                  why="Totals and audits need to know which number to trust.",
                  kind="rule", priority=2, source="finding",
                  fact={"kind": "rule", "class": "data", "depends": [qty, price, total],
                        "statement": f"Where {lt} is not {lq} times {lp}, the owner says: {{answer_labels}}."},
                  meta={"finding": ins, "about": _about_role(analysis, total, "treatment")})
        elif rec.startswith("duplicates:"):
            col = rec.split(":", 1)[1]
            role = analysis.detection["roles"].get(col, {})
            label = role.get("header", col)
            if (role.get("table"), label) in twice:
                continue          # the copies or the twin pairs already ask about these repeats, with their evidence
            from .rules import Rule
            # keeping the first or last of each counts every value once in every count and total
            keep = {oid: Rule("dedupe", role.get("table", ""), [], {"col": label, "keep": how},
                              source=f"find_dupes_{_slug(col)}").to_dict()
                    for oid, how in (("keep_oldest", "first"), ("keep_newest", "last"))} \
                if analysis.col(role.get("table", ""), label) is not None else {}
            q = Q(f"find_dupes_{_slug(col)}", "Duplicates",
                  f"{n.get('values', 0):,} {label} values appear more than once ({n.get('rows', 0):,} rows). "
                  "Is that the same one twice?",
                  [{"id": "keep_oldest", "label": "Keep the first of each",
                    "desc": "The later rows are copies, left out of every count and total"},
                   {"id": "keep_newest", "label": "Keep the last of each",
                    "desc": "The earlier rows are copies, left out of every count and total"},
                   {"id": "different", "label": "Different things", "desc": "They only share the value; every row counts"}],
                  why="Duplicates inflate every count until someone says how to merge them.",
                  kind="rule", priority=1, source="finding",
                  fact={"kind": "rule", "class": "data", "depends": [col],
                        "statement": f"Rows that repeat a {label}: {{answer_labels}}, per the owner."},
                  meta={"finding": ins, "rules": keep, "about": _about_role(analysis, col, "grain")})
        elif rec.startswith("copies:"):
            q = _copies_question(analysis, ins)
        elif rec.startswith("pairs:"):
            q = _pairs_question(analysis, ins)
        elif rec.startswith("nearkey:"):
            q = _upload_question(analysis, ins)
        elif rec.startswith("handoff:") and not n.get("boundary") and len(n.get("pairs") or []) >= 2 \
                and not _has_dossier(analysis, n["table"], n["col"], offered=False):
            q = _handoff_question(analysis, ins, follow=False)     # a rename across many values, with no system change
        elif rec == "formula:typed_in_formula_row":
            cells = _named(n)[:3]
            also = ""
            if check_in_plug:
                also = (f" It's also the month the {chk['numbers'].get('row_label', 'check')} row fails "
                        f"(off by {chk['numbers'].get('max_abs', 0):,.2f}).")
            what = f"{_join(cells)}, a typed number in a row of formulas" \
                + (f" (also the month the {chk['numbers'].get('row_label', 'check')} row fails)"
                   if check_in_plug else "")
            # each pick names the whole claim: why the number is typed, and what to do with it
            q = Q("find_typed_plug", "Typed number",
                  f"{_join(cells)} {'is a typed number' if len(cells) == 1 else 'are typed numbers'} in a row "
                  "that is otherwise formulas." + also + " On purpose?",
                  [{"id": "test", "label": "A test value left in: put the link back",
                    "desc": "The row's formula comes back"},
                   {"id": "mistake", "label": "A mistake: restore the formula", "desc": "Typed there by mistake"},
                   {"id": "override", "label": "On purpose (an override)", "desc": "The typed number stays"}],
                  why="A typed number in a formula row stops updating, so every result downstream can drift.",
                  kind="rule", priority=1, source="finding",
                  fact={"kind": "rule", "class": "data", "depends": [],
                        "statement": what + ", is per the owner: {answer_labels}.",
                        "statements": {
                            "test": f"{what}, is a test value left in, per the owner: put the link back.",
                            "mistake": f"{what}, is a mistake, per the owner: restore the formula.",
                            "override": f"{what}, is on purpose, per the owner (an override): the typed number "
                                        "stays."}},
                  meta={"finding": ins, "covers": ["overrides"], "touches": ["has_role:check"],
                        "about": _about_dep(analysis, ins, "treatment", cells)})
        elif rec == "formula:pattern_breaks":
            diffs = dict(zip(n.get("cells", []), n.get("diffs") or []))
            picked = [(c, nm) for c, nm in zip(n.get("cells", []), _named(n)) if c not in hard][:5]
            cells = [nm for _, nm in picked]
            if not cells:
                continue
            # each break described against its row's usual formula, in labels; else the two formulas
            forms = dict(zip(n.get("cells", []), zip(n.get("formulas") or [], n.get("expected") or [],
                                                     n.get("expected_at") or [])))
            said = [f"{nm} {diffs[c]}" if diffs.get(c) else f"{nm} {_two_formulas(c, *forms[c])}"
                    if c in forms else nm for c, nm in picked]
            q = Q("find_pattern_breaks", "Odd formulas",
                  f"{'These formulas do' if len(cells) != 1 else 'This formula does'}n't match the rest of "
                  f"{'their' if len(cells) != 1 else 'its'} row: {'; '.join(said)}. "
                  f"{'Are they mistakes?' if len(cells) != 1 else 'Is it a mistake?'}",
                  [{"id": "all", "label": "All mistakes", "desc": "Fix them to match"},
                   {"id": "some", "label": "Some are on purpose", "desc": "Type which ones"},
                   {"id": "none", "label": "All on purpose", "desc": "Leave them"}],
                  why="A formula that breaks its row's pattern is the most common model error.",
                  kind="rule", priority=1, source="finding",
                  fact={"kind": "rule", "class": "data", "depends": [],
                        "statement": f"The formulas {'; '.join(said)}, which break their row's pattern, are per the "
                                     "owner: {answer_labels}."},
                  meta={"finding": ins, "covers": ["overrides"],
                        "about": _about_dep(analysis, ins, "treatment", cells)})
        elif rec.startswith("formula:typed_row:"):
            big = n.get("biggest")
            big_s = f"{big:,.0f}" if isinstance(big, (int, float)) and abs(big) >= 100 else str(big)
            where = analysis._named(n) if n.get("cell") and n.get("sheet") else n.get("cell")
            only = n.get("cells") == 1
            q = Q(f"find_typed_row_{_slug(n.get('row_label', ''))}", "Typed row",
                  f"The {n.get('row_label')} row on {n.get('sheet')} is typed, not calculated ("
                  + (f"its only number is {big_s} in {where}" if only else f"the largest is {big_s} in {where}")
                  + "). What is it?",
                  [{"id": "plan", "label": "A plan", "desc": "Not real yet"},
                   {"id": "actual", "label": "Actual, it happened", "desc": "A real number"},
                   {"id": "test", "label": "A test number", "desc": "Should come out"}],
                  why="A typed row on a calculated tab can decide the answer (cash, runway) without anyone noticing.",
                  kind="definition", priority=1, source="finding",
                  fact={"kind": "definition", "class": "data", "depends": [],
                        "statement": f"The typed {n.get('row_label')} row on {n.get('sheet')} ({big_s} in "
                                     f"{where}) is, per the owner: {{answer_labels}}."},
                  meta={"finding": ins, "about": _about_dep(analysis, ins, "meaning")})
        elif rec == "formula:orphan_inputs":
            cells = _named(n)[:3]
            twin = (n.get("twin") or [None])[0]
            # the same value typed where the model really reads it: name that cell, it is the other half, and say
            # when it sits in a month the file already holds as an actual
            actual = bool(twin) and len(cells) == 1 and _in_actuals(analysis, twin)
            same = (f"; the same {_value_words(n.get('value'))} is typed in {twin}"
                    + (", an actual month" if actual else "") + ", and nothing links them"
                    if twin and len(cells) == 1 else "")
            left = "Left over: the typed actual is the source" if actual else \
                "Left over: the typed one is the source" if same else "Left over"
            q = Q("find_orphans", "Unused input",
                  f"{_join(cells)} {'is an input' if len(cells) == 1 else 'are inputs'} that no formula uses"
                  f"{same}. Left over from an earlier version, or should something use it?",
                  [{"id": "leftover", "label": left, "desc": "Left over from an earlier version"},
                   {"id": "should_use", "label": "Something should use it",
                    "desc": "The typed one should link here" if same else "A link is missing"}],
                  why="An input nothing reads is either clutter or a broken link.",
                  kind="rule", priority=2, source="finding",
                  fact={"kind": "rule", "class": "data", "depends": [],
                        "statement": f"The unused input {_join(cells)}{same} is, per the owner: {{answer_labels}}.",
                        "statements": {"leftover": f"{_join(cells)}, an input no formula uses, is left over from "
                                                   "an earlier version, per the owner"
                                                   + (f": the typed {'actual' if actual else 'one'} in {twin} is the "
                                                      "source." if same else ".")}},
                  meta={"finding": ins, "about": _about_dep(analysis, ins, "treatment", cells)})
        elif rec == "formula:hardcoded":
            cells = _named(n)[:2]
            k, added = (n.get("constants") or [None])[0], (n.get("added") or [None])[0]
            # a link times a typed number: the change it makes, as a percent and in money, is the evidence
            change = _factor_words(k)
            what = (f"multiplies a link by a typed {k}: that {change}, adding {added:,.2f}"
                    if added is not None and len(cells) == 1 and change else
                    f"multiplies a link by a typed {k}, adding {added:,.2f}" if added is not None and len(cells) == 1
                    else f"{'has' if len(cells) == 1 else 'have'} a typed number inside the formula")
            q = Q("find_hardcoded", "Typed factor",
                  f"{_join(cells)} {what}. What is it?",
                  [{"id": "test", "label": "A test to take out (type what it tested)", "desc": "Not approved"},
                   {"id": "approved", "label": "An approved rate", "desc": "It belongs there"}],
                  why="A number buried in a formula is invisible to anyone reading the inputs.",
                  kind="rule", priority=1, source="finding",
                  fact={"kind": "rule", "class": "data", "depends": [],
                        "statement": f"The typed number inside {_join(cells)}"
                                     + (f" (a typed {k} that adds {added:,.2f})" if added is not None
                                        and len(cells) == 1 else "") + " is, per the owner: {answer_labels}.",
                        **({"statements": {
                            "test": f"The typed {k} in {_join(cells)}, which {change}, adding {added:,.2f}, is "
                                    "a test to take out, per the owner: not approved.",
                            "approved": f"The typed {k} in {_join(cells)}, which {change}, adding {added:,.2f}, "
                                        "is an approved rate, per the owner: it belongs there."}}
                           if added is not None and len(cells) == 1 and change else {})},
                  meta={"finding": ins, "covers": ["overrides"], "about": _about_dep(analysis, ins, "meaning", cells)})
        elif rec.startswith("formula:plan_block:"):
            q = _plan_block_question(analysis, ins)
        elif rec.startswith("formula:tieout:"):
            q = _tieout_question(analysis, ins)
        elif rec.startswith("ratio:"):
            q = _ratio_question(analysis, ins)
        elif rec.startswith("contra:"):
            q = _contra_question(analysis, ins)
        elif rec.startswith("opening:"):
            q = _opening_question(analysis, ins)
        elif rec.startswith("closing:"):
            q = _closing_question(analysis, ins)
        elif rec == "formula:check" and ins.get("oddity") and not check_in_plug:
            q = Q("find_check", "Check fails",
                  _short(ins["statement"], 220) + " Do you know why?",
                  [{"id": "known", "label": "Yes, I know why", "desc": "Type the reason"},
                   {"id": "news", "label": "News to me", "desc": "Needs looking into"}],
                  why="A failing check means some number in that period is wrong.",
                  kind="history", priority=1, source="finding",
                  fact={"kind": "history", "class": "data", "depends": [],
                        "statement": _short(ins["statement"], 220).rstrip(".") + ". The owner says: {answer_labels}."},
                  meta={"finding": ins, "touches": ["has_role:check"], "about": _about_dep(analysis, ins, "treatment")})
        elif rec.startswith("formula:short_range:") and _title_rows(analysis, n):
            q = _title_short_question(analysis, ins)
        elif rec.startswith("formula:short_range:"):
            q = Q(f"find_short_{_slug(n['sheet'] + '_' + n['reads'])}", "Short range",
                  f"The formulas on {n['sheet']} stop at row {n['range_end']:,} of {n['reads']}, which now has rows to "
                  f"{n['last_row']:,}. Should {n['sheet']} include the newer rows?",
                  [{"id": "extend", "label": "Yes, the ranges should grow", "desc": "The totals are short today"},
                   {"id": "on_purpose", "label": "No, it covers a fixed period", "desc": "The cutoff is on purpose"}],
                  recommend="extend", recommend_basis="The ranges started at the top of the data and stopped at "
                                                      "what was its last row.",
                  why=f"Every total on {n['sheet']} is short by the rows it does not read.",
                  kind="rule", priority=1, source="finding",
                  fact={"kind": "rule", "class": "data", "depends": [],
                        "statement": f"{n['sheet']} reads {n['reads']} only to row {n['range_end']:,}; per the owner: "
                                     "{answer_labels}.",
                        "statements": {"extend": f"{n['sheet']}'s formulas should include all rows of {n['reads']}, "
                                                 f"per the owner; today they stop at row {n['range_end']:,}, so the "
                                                 "totals are short.",
                                       "on_purpose": f"{n['sheet']} covers {n['reads']} only to row {n['range_end']:,} "
                                                     "on purpose, per the owner."}},
                  meta={"finding": ins, "about": next((_about(t.tid, "", "scope") for t in analysis.tables
                                                       if t.sheet == n["reads"] and not t.wide), {})})
        elif rec.startswith("blanks:"):
            col, sheet = n["col"], analysis.table(n["table"]).sheet
            rid = _rid(analysis, n["table"], col)
            vals = _join(n.get("values", [])[:4])
            noun = col.lower()
            sl = n.get("slice") or {}
            # blanks bunched where the column never applies are a counted fact; only the rest is asked
            where = f" where {sl['col']} is {_listed(sl['values'])}" if sl else ""
            money = f", {n['money']}" if sl and n.get("money") else ""
            q = Q(f"find_blank_{_slug(col)}", header_words("Blank", col),
                  f"{n['rows']:,} of {n['total']:,} rows on {sheet}{where} have no {col}{money} (the rest are {vals}). "
                  f"What does a blank {col} mean{' there' if sl else ''}?",
                  [{"id": "shared", "label": "Shared by all of them", "desc": f"It belongs to no single {noun}"},
                   {"id": "missing", "label": "Missing, should be filled", "desc": "They should be filled"},
                   {"id": "not_applicable", "label": f"No {noun} applies", "desc": "Those rows aren't split that way"}],
                  why=f"Totals by {noun} are wrong until the blanks are placed.",
                  kind="definition", priority=1, source="finding",
                  fact={"kind": "definition", "class": "data", "depends": [],
                        "statement": f"On {sheet}, a blank {col} ({n['rows']:,} rows) means: {{answer_labels}}, "
                                     "per the owner.",
                        "statements": {
                            "shared": f"On {sheet}, a blank {col} ({n['rows']:,} rows) means the row is shared by all "
                                      f"of them ({vals}), per the owner.",
                            "missing": f"On {sheet}, a blank {col} ({n['rows']:,} rows) is missing and should be "
                                       "filled, per the owner.",
                            "not_applicable": f"On {sheet}, a blank {col} ({n['rows']:,} rows) means no {noun} "
                                              "applies to that row, per the owner."}},
                  meta={"finding": ins, "touches": [f"has_blanks:{rid}", f"mixed_values:{rid}"] if rid else [],
                        # what a blank means is its own aspect: it never settles what the values mean
                        "about": _about(n["table"], col, "blanks")})
        elif rec.startswith("boundary:"):
            q = _boundary_question(analysis, ins)
        elif rec.startswith("oddgroup:"):
            if not n.get("price") and _has_dossier(analysis, n["table"], n["col"], offered=False, folds=True):
                continue          # the column's dossier carries this evidence; a price's unit is still asked here
            q = _odd_group_question(analysis, ins)
        elif rec.startswith("blankmeasure:"):
            q = _measure_blank_question(analysis, ins)
        elif rec.startswith("unitgroup:"):
            q = _unit_group_question(analysis, ins)
        elif rec.startswith("period:"):
            q = _period_question(analysis, ins)
        elif rec.startswith("nonstock:"):
            q = _nonstock_question(analysis, ins)
        elif rec.startswith("unpaid:"):
            q = _unpaid_question(analysis, ins)
        elif rec.startswith("outliers:"):
            q = _outliers_question(analysis, ins)
        elif rec.startswith("derive:"):
            q = _derive_question(analysis, ins, answers)
        elif rec.startswith("window:"):
            q = _window_question(analysis, ins, answers)
        elif rec.startswith("structure:versions:") and n.get("per"):
            q = _versions_question(analysis, ins)
        elif rec.startswith("structure:join_variants:") and n.get("cross_file"):
            q = _variants_question(analysis, ins)
        elif rec.startswith(("listprice:", "listprice_all:")):
            q = _listscope_question(analysis, ins)
        elif rec.startswith("offlist:"):
            q = _offlist_question(analysis, ins)
        elif rec.startswith("onset:"):
            q = _onset_question(analysis, ins)
        elif rec.startswith("exclusive:"):
            col, val, p, ic = n["col"], n["value"], n["prefix"], n["id_col"]
            rid = _rid(analysis, n["table"], col)
            noun = col.lower()
            like = next((a.get("meta_value") for a in answers.values() if isinstance(a, dict)
                         and a.get("meta_prefix") == p and set(a.get("options") or []) & {"internal", "double"}), None)
            opts = [{"id": "internal", "label": "Internal entries",
                     "desc": f"Its {n['rows']:,} rows come out of every count and total"},
                    {"id": "double", "label": "Already counted elsewhere", "desc": "Counting it again doubles it"},
                    {"id": "real", "label": f"A real {noun}", "desc": "It belongs in the totals"}]
            if _SITE.search(str(col)):
                # a place of the business that is not one of its sites (a kitchen, a warehouse): its own option,
                # named against two sites it is unlike
                opts[2] = _ours(noun, _peers(analysis, n["table"], col, val))
            q = Q(f"find_exclusive_{_slug(col + '_' + val)}", header_words("What is", val),
                  f"All {n['rows']:,} {val} rows have {_a(ic)} {ic} starting {p}, and no other {noun} has those. "
                  f"What is {val}?",
                  opts,
                  why=f"If {val} is internal, every total that includes it is counted twice.",
                  multi=True, kind="exclusion", priority=1, source="finding",
                  fact={"kind": "exclusion", "class": "data", "depends": [],
                        # the ID header as written, never made plural: 'Doc No' + 's' is a word nobody saw
                        "statement": f"{val} in {col} ({ic} starting {p}) is, per the owner: {{answer_labels}}.",
                        "statements": {
                            "internal": f"The {val} rows in {col} are internal entries (every {ic} starting {p}), "
                                        "per the owner.",
                            "double": f"The {val} rows in {col} are already counted elsewhere (every {ic} starting "
                                      f"{p}), per the owner.",
                            **({"ours": f"{val} in {col} is {_lower_first(opts[2]['label'][:-len(' (type what)')])}, "
                                        "per the owner."}
                               if opts[2]["id"] == "ours" else
                               {"real": f"{val} in {col} is a real {noun} and belongs in the totals, per the owner."})}},
                  meta={"finding": ins, "exclude": {"table": n["table"], "col": col, "values": [val],
                                                    "options": ["internal", "double"]},
                        "touches": [f"coded:{rid}", f"mixed_values:{rid}"] if rid else [],
                        "value": val, "prefix": p, "about": _about(n["table"], col, "treatment", [val])})
            if like and like != val:
                q.recommend = "internal"
                q.recommend_basis = f"Its {ic}s start {p}, like {like}'s, which the owner said is internal."
        if q is None or q.id in answers:
            continue
        own = q.meta.pop("stake_rows", None)
        _worth(q, own if own is not None else _finding_stake(analysis, ins))
        if rec.startswith("boundary:"):
            # 6 plus 2 for each column that changes, at most 14, at half weight while the smaller side is small
            k = len({c["col"] for c in n["changes"]})
            q.value = round(min(14.0, 6.0 + 2.0 * k) * min(1.0, 0.5 + 0.5 * q.meta["stake"] / MATERIAL), 3)
        out.append(q)
    for t in analysis.tables:
        for note in t.notes:
            m = re.match(r"(.+?) is written only on the first row of each group", note)
            if not m:
                continue
            col = m.group(1)
            qid = f"find_filldown_{_slug(col)}"
            if qid in answers:
                continue
            j = t.headers.index(col) if col in t.headers else -1
            blank = sum(1 for r in t.rows if j >= 0 and (j >= len(r) or r[j] is None or str(r[j]).strip() == ""))
            q = Q(qid, "Group label", f"{col} on {t.sheet} is written only on the first row of each group "
                                      f"({blank:,} rows below are blank). Does it apply to the rows below it?",
                  [{"id": "yes", "label": "Yes, it applies below", "desc": "Read blanks as the value above"},
                   {"id": "no", "label": "No, blanks mean blank", "desc": "Leave them empty"}],
                  recommend="yes", recommend_basis="Every blank sits under a filled row in the same group",
                  why="Totals by group are wrong until blanks are read the right way.",
                  kind="rule", priority=1, source="finding",
                  fact={"kind": "rule", "class": "data", "depends": [],
                        "statement": f"On {t.sheet}, {col} written on a group's first row: {{answer_labels}}, per "
                                     "the owner.",
                        "statements": {
                            "yes": f"On {t.sheet}, {col} written on a group's first row applies to the rows below "
                                   "it, per the owner.",
                            "no": f"On {t.sheet}, a blank {col} below a group's first row means blank, per the "
                                  "owner."}},
                  meta={"about": _about(t.tid, col, "treatment")})
            _worth(q, blank / t.n_rows if t.n_rows else 0.0)
            out.append(q)
    look = _lookup_question(analysis, answers)
    if look is not None:
        out.append(look)
    qs = _fold_same_rows(analysis, _one_per_value(analysis, out, answers) + code_dossiers(analysis, answers))
    qs = _answered_rows(analysis, qs, answers)
    for q in qs:
        _through_join(analysis, q)
    return qs


def _fold_same_rows(analysis, qs: list) -> list:
    """A group whose rows are the rows of a value another question names (a person
    who logs every row of one site) is that value: its evidence goes into that
    question, which is asked once."""
    drop = set()
    for q in qs:
        if not q.id.startswith("find_odd_") or q.kind == "unit" or not q.meta.get("finding"):
            continue
        host = _same_rows_host(analysis, q, [x for x in qs if x is not q and id(x) not in drop
                                             and not x.id.startswith("find_odd_")])
        if host is not None:
            n = q.meta["finding"]["numbers"]
            clause = f"{n['value']} in {n['col']} is on the same rows: {n['evidence'][0]['text']}" \
                if n.get("evidence") else f"{n['value']} in {n['col']} is on the same rows"
            host.prompt = with_clause(host.prompt, clause + ".")
            host.meta["also"] = list(host.meta.get("also") or []) + [q.meta.get("about")]
            drop.add(id(q))
    return [q for q in qs if id(q) not in drop]


def _answered_rows(analysis, qs: list, answers: dict) -> list:
    """A group whose rows are the rows of a value another answer already settled
    (90% or more both ways: a person who logs every row of one site the owner
    said is not theirs) is settled with it: its question is not asked."""
    got = []
    for qid, a in (answers or {}).items():
        if not isinstance(a, dict) or a.get("not_sure") or qid.startswith(("_", READBACK)):
            continue
        ab = a.get("about") or {}
        t = next((t for t in analysis.tables if t.tid == ab.get("table")), None)
        if t is None or ab.get("col") not in t.headers or not ab.get("values"):
            continue
        j = t.headers.index(ab["col"])
        for v in ab["values"]:
            k = norm_key(v)
            rows = {i for i, r in enumerate(t.rows) if j < len(r) and norm_key(r[j]) == k}
            if rows:
                got.append((t.tid, ab["col"], rows))
    if not got:
        return qs
    out = []
    for q in qs:
        n = (q.meta.get("finding") or {}).get("numbers") or {}
        if q.id.startswith("find_odd_") and q.kind != "unit" and n.get("table") and n.get("col"):
            t = next((t for t in analysis.tables if t.tid == n["table"]), None)
            if t is not None and n["col"] in t.headers:
                g = t.headers.index(n["col"])
                vals = {norm_key(n["value"])} | {norm_key(x) for x in n.get("also") or []}
                mine = {i for i, r in enumerate(t.rows) if g < len(r) and norm_key(r[g]) in vals}
                if mine and any(tid == t.tid and col != n["col"] and len(mine & rows) >= 0.9 * len(mine)
                                and len(mine & rows) >= 0.9 * len(rows) for tid, col, rows in got):
                    continue
        out.append(q)
    return out


def _through_join(analysis, q) -> None:
    """A finding about values of a lookup table (a product list) moves the money of
    the lines that look those keys up: its stake is measured there, on the table
    whose rows join it. With no such join the lookup's own share stays."""
    ab = q.meta.get("about") or {}
    if not ab.get("col") or q.id.startswith(("find_boundary_", "find_lookup_", READBACK)):
        return
    vals = {norm_key(v) for v in (q.meta.get("minority") if q.meta.get("dossier") else ab.get("values")) or []} - {None}
    lt = next((t for t in analysis.tables if t.tid == ab.get("table")), None)
    if not vals or lt is None or ab["col"] not in lt.headers:
        return
    j = next((j for j in analysis.joins if j["band"] == "auto" and j["to_table"] == lt.tid and
              analysis.table(j["from_table"]).n_rows > lt.n_rows and j["to_col"] in lt.headers), None)
    if j is None:
        return
    fact = analysis.table(j["from_table"])
    if j["from_col"] not in fact.headers:
        return
    jc, jk, jf = lt.headers.index(ab["col"]), lt.headers.index(j["to_col"]), fact.headers.index(j["from_col"])
    keys = {norm_key(r[jk]) for r in lt.rows if jc < len(r) and jk < len(r) and norm_key(r[jc]) in vals} - {None}
    if not keys:
        return
    stake = row_stake(analysis, fact, lambda r: jf < len(r) and norm_key(r[jf]) in keys)
    follow = q.id.startswith("follow_")
    _worth(q, stake, follow=follow)
    q.meta["through"] = fact.tid


_REF = re.compile(r"([^(),;!]+)!\$?([A-Z]{1,3})\$?(\d+)")


def cell_refs(values) -> set:
    """'Sheet!B5' for every cell the values name ('Growth rate (Rates!B5)' -> 'Rates!B5')."""
    return {f"{m.group(1).strip()}!{m.group(2)}{m.group(3)}" for v in values or [] for m in _REF.finditer(str(v))}


DRIVERS_MAX = 8           # clauses a driver readback lists at most (inputs that share a note are one clause),
                          # within 520 characters
_CASH_GOAL = re.compile(r"\b(cash|runway|burn|liquidity|bank balance)\b", re.I)


def driver_readback(analysis, answers: dict, taken: set) -> list:
    """One readback per formula model of the inputs that drive it and no other
    question names, most model reach first (the inputs that reach the cash row
    only break ties: a count of cash cells would favour inputs used in actual
    months). Each input is a short clause, 'label (cell) = value ("note")', the
    formula it drives only when the file gives no note; inputs that share a note
    are one clause. Membership does not follow the goal, so it stays the same from
    round to round. Once the owner answered, a second readback reads back the
    inputs still unconfirmed that reach 20% or more of the model. 'Right as read'
    confirms what is on record; 'Some are wrong' takes the owner's words."""
    import os

    from . import formulas as fm
    from .interview import Q
    out = []
    goal = answers.get("goal") or {}
    cashy = bool(_CASH_GOAL.search(" ".join([str(goal.get("text") or "")] + list(goal.get("labels") or []))))
    told = _told_cells(answers)
    for b in analysis.books:
        fa = analysis.formulas.get(b.path) or {}
        drivers = fa.get("drivers") or []
        ins = [x for x in fa.get("inputs") or [] if x.get("read")]
        base = f"find_drivers_{_slug(os.path.splitext(os.path.basename(b.path))[0], 24)}"
        if len(ins) < 2 or not drivers:
            continue
        # the first readback, then (once it is answered) one more for the inputs it left open
        qid = base if base not in answers else f"{base}_2"
        if qid in answers:
            continue
        cash_cells = set()
        if cashy:
            r = analysis.detection["roles"].get("cash") or {}
            t = next((t for t in analysis.tables if t.tid == r.get("table") and analysis.file_of[t.tid] == b.path), None)
            if t is not None and t.row_label_col >= 0:
                ri = next((i for i, row in zip(t.row_index, t.rows)
                           if str(row[t.row_label_col]).strip() == r.get("header")), None)
                cash_cells = {(t.sheet, ri, c) for c in t.cols} if ri is not None else set()
        graph = analysis.__dict__.get("_dep_graphs", {}).get(b.path)
        if graph is None:
            analysis.reach_share([], b.path)
            graph = analysis.__dict__.setdefault("_dep_graphs", {}).get(b.path) or fm.dependents(b)
        cands = []
        for x in ins:
            cell = f"{x['sheet']}!{x['cell']}"
            ds = [d for d in drivers if cell in d["inputs"]]
            if cell in taken or cell in told or not ds:
                continue
            reach = analysis.reach_share([cell], b.path)
            if reach <= 0 or (qid != base and reach < DRIVER_AGAIN):
                continue
            into = bool(fm.downstream(graph, x["sheet"], x["cell"]) & cash_cells) if cash_cells else False
            cands.append((reach, into, x, max(ds, key=lambda d: d["cells"])))
        if len(cands) < 2:
            continue
        # model reach first; reaching the cash line only breaks a tie
        cands.sort(key=lambda c: (-round(c[0], 3), not c[1], c[2]["sheet"], c[2]["r"]))
        clauses, used, top = [], [], 0.0
        from .analyze import _value_words
        groups: dict = {}
        order = []
        for reach, _into, x, d in cands[:DRIVERS_MAX * 3]:
            # inputs that share a note and a label pattern are one clause (costs per head of three teams)
            key = (_short(x["note"], 60).lower(), _label_pattern(x["label"])) if x.get("note") \
                else f"#{x['sheet']}!{x['cell']}"
            if key not in groups:
                order.append(key)
            groups.setdefault(key, []).append((reach, x, d))
        for key in order[:DRIVERS_MAX]:
            g = groups[key]
            bit = _input_clause([(x, d) for _r, x, d in g], _value_words)
            if clauses and len("; ".join(clauses + [bit])) > 520:
                break
            clauses.append(bit)
            used += [x for _r, x, _d in g]
            top = max([top] + [r for r, _x, _d in g])
        if len(used) < 2:
            continue
        cells = [f"{x['sheet']}!{x['cell']}" for x in used]
        t = next((t for t in analysis.tables if t.sheet == used[0]["sheet"] and analysis.file_of[t.tid] == b.path
                  and used[0]["label"] in analysis.row_labels(t)), None)
        if t is None:
            continue
        said = "; ".join(clauses)
        more = "These other inputs" if qid != base else "These inputs"
        q = Q(qid, "Inputs",
              f"{more} drive the model: {said}. Right as read?",
              [{"id": "right", "label": "Right as read", "desc": "Each value, its note and what it drives"},
               {"id": "wrong", "label": "Some are wrong (type which)", "desc": "Type which input and what is right"}],
              why="Every forecast number rests on these inputs, and a note that says the wrong period or basis "
                  "misleads whoever reads the model next.",
              kind="definition", priority=2, source="finding",
              fact={"kind": "definition", "class": "data", "depends": [],
                    "statement": f"The inputs that drive the model ({said}), per the owner: {{answer_labels}}.",
                    "statements": {"right": f"{more} drive the model, right as read, per the owner: {said}."}},
              meta={"about": _about(t.tid, used[0]["label"], "scope", cells),
                    "clause": f"{len(used)} inputs drive the model",
                    # each input by its label and cell, so a typed reply that names some confirms only those
                    "inputs": [{"label": x["label"], "cell": f"{x['sheet']}!{x['cell']}"} for x in used]})
        _worth(q, top)
        if qid != base:
            q.meta["leftover"] = True           # the second readback only fills room a round leaves
        out.append(q)
    return out


DRIVER_AGAIN = 0.2        # a second readback only for inputs that reach this share of the model or more


def _input_clause(items: list, value_words) -> str:
    """'Label (Sheet!B5) = 0.015 ("note")', or for inputs that share a note 'A, B and C
    (Sheet!B25, B26 and B27) = 1, 2 and 3 ("note")'; the formula an input drives only
    when the file gives it no note."""
    x0, d0 = items[0]
    if len(items) == 1:
        if x0.get("note"):
            return f"{x0['label']} ({x0['sheet']}!{x0['cell']}) = {value_words(x0['value'])} (\"{_short(x0['note'], 60)}\")"
        # no note: what it drives, the formula itself when it is short
        words = str(d0.get("words") or "")
        drives = f"{d0['row_label']} = {words}" if words and len(words) <= 30 else d0["row_label"]
        return f"{x0['label']} ({x0['sheet']}!{x0['cell']}) = {value_words(x0['value'])}, in {drives}"
    sheet = x0["sheet"]
    cells = _join([(f"{x['sheet']}!" if k == 0 or x["sheet"] != sheet else "") + x["cell"]
                   for k, (x, _d) in enumerate(items)])
    return (f"{_join([x['label'] for x, _d in items])} ({cells}) = {_join([value_words(x['value']) for x, _d in items])}"
            f" (\"{_short(x0['note'], 60)}\")")


def _label_pattern(label: str) -> str:
    """The shape of an input's label, for grouping inputs of one kind: its last
    word ('per month'), or 'one word' for a single-word label (a team's name)."""
    words = re.findall(r"[A-Za-z&%]+", str(label or ""))
    return "one word" if len(words) <= 1 else words[-1].lower()


def _told_cells(answers: dict) -> set:
    """Model cells the owner's answers settled: every cell a question named, except
    that a typed reply to an inputs readback settles only the inputs it names (by
    label or cell); the rest stay open."""
    out = set()
    for qid, a in (answers or {}).items():
        if not isinstance(a, dict):
            continue
        cells = cell_refs((a.get("about") or {}).get("values"))
        if qid.startswith("find_drivers_") and not a.get("options") and a.get("inputs"):
            text = str(a.get("text") or "")
            cells = {x["cell"] for x in a["inputs"] if x["cell"].split("!")[-1] in text
                     or re.search(r"(?<![\w])" + re.escape(str(x["label"])) + r"(?![\w])", text, re.I)}
        out |= cells
    return out


def _title_rows(analysis, n: dict) -> dict | None:
    """When a calculated tab's short range and the period a data tab's title names
    leave out the same rows: {table, fact, rows (sheet rows), first, last} for the
    rows dated past the title's end, else None."""
    import datetime as dt
    t = next((t for t in analysis.tables if t.sheet == n.get("reads") and not t.wide), None)
    if t is None:
        return None
    g = next((g for g in getattr(analysis, "grain_facts", None) or [] if g["recipe"] == f"grain:title_period:{t.tid}"),
             None)
    aj = analysis._axis_j(t)
    if g is None or aj is None or len(t.row_index) != t.n_rows:
        return None
    end = dt.date.fromisoformat(g["numbers"]["date"][:10]).toordinal()
    late = [(t.row_index[i] + 1, r[aj]) for i, r in enumerate(t.rows) if aj < len(r) and hasattr(r[aj], "year")
            and r[aj].toordinal() > end]
    beyond = {t.row_index[i] + 1 for i in range(t.n_rows) if t.row_index[i] + 1 > n["range_end"]}
    mine = {x for x, _d in late}
    if not late or not beyond or len(mine & beyond) < 0.8 * max(len(mine), len(beyond)):
        return None
    days = sorted(d for _x, d in late)
    return {"table": t, "fact": g, "rows": sorted(mine), "first": days[0], "last": days[-1]}


def _title_short_question(analysis, ins: dict):
    """A calculated tab's formulas stop where a data tab's title period ends, and
    the rows past both are dated after it: one question that quotes the title and
    its note, names the rows' dates and the formulas that stop, and asks whether
    those rows were added on purpose."""
    from .interview import Q
    n = ins["numbers"]
    got = _title_rows(analysis, n)
    t, g = got["table"], got["fact"]
    said = g["numbers"]["said"]
    note = next((x for x in t.notes if x and x.strip() and x.strip() != (t.title or "").strip()), "")
    notes = f" and its note says \"{_short(note, 80)}\"" if note else ""
    k = len(got["rows"])
    span = _span(got["first"], got["last"])
    fn = _join(n.get("functions") or []) or "formulas"
    rows = f"rows {got['rows'][0]:,} to {got['rows'][-1]:,}" if k > 1 else f"row {got['rows'][0]:,}"
    reads = f"{n['sheet']}'s {fn} read {n['reads']} only to row {n['range_end']:,}"
    head = (f"{t.sheet}'s title says \"{_short(said, 60)}\"{notes}, but {k:,} row{'s' if k != 1 else ''} dated {span} "
            f"come after it ({rows}), and {reads}.")
    return Q(f"find_short_{_slug(n['sheet'] + '_' + n['reads'])}", "Late rows",
             head + f" Were those {k:,} rows added on purpose?",
             [{"id": "added", "label": "Added on purpose: they belong", "desc": f"They count, and {n['sheet']} should "
                                                                                 "read them"},
              {"id": "other_export", "label": "From another export", "desc": "A later export"},
              {"id": "type", "label": "Something else (type it)", "desc": "Type what they are"}],
             why=f"Every total on {n['sheet']} is short by those rows, or the tab holds rows its title does not cover.",
             kind="scope", priority=1, source="finding",
             fact={"kind": "scope", "class": "data", "depends": [],
                   "statement": f"{head} Per the owner: {{answer_labels}}.",
                   "statements": {
                       "added": f"{head} Those rows were added on purpose and belong, per the owner: they count, and "
                                f"{n['sheet']} should read them.",
                       "other_export": f"{head} Those rows are from a later export, per the owner."}},
             meta={"finding": ins, "about": _about(t.tid, "", "scope"),
                   "clause": f"{k:,} rows on {t.sheet} dated {span} come after its title's period"})


def _one_per_value(analysis, qs: list, answers: dict | None = None) -> list:
    """One question per value: the same odd value on two tabs (a person who enters
    rows on one and checks them on the other) is asked once, on the tab with more at stake,
    listing both tabs with each one's evidence; leaving it out takes its rows out
    of both. Two join-variant questions naming the same pairs are asked once too.
    Once one of them is answered, the others are not asked."""
    answers = answers or {}
    said_odd = {norm_key(a.get("meta_value")) for k, a in answers.items() if k.startswith("find_odd_")
                and isinstance(a, dict) and a.get("kind") != "unit" and a.get("meta_value")}
    said_alias = {frozenset(norm_key(v) for v in (a.get("about") or {}).get("values") or [])
                  for k, a in answers.items() if k.startswith("alias_") and isinstance(a, dict)}
    qs = [q for q in qs if not (q.id.startswith("find_odd_") and q.kind != "unit"
                                and norm_key(q.meta.get("value")) in said_odd)
          and not (q.id.startswith("alias_") and frozenset(norm_key(v) for v in (q.meta.get("about") or {})
                                                            .get("values") or []) in said_alias)]
    groups: dict = {}
    for q in qs:
        n = (q.meta.get("finding") or {}).get("numbers") or {}
        if q.id.startswith("find_odd_") and q.kind != "unit":
            groups.setdefault(("odd", norm_key(n.get("value"))), []).append(q)
        elif q.id.startswith("alias_") and (q.meta.get("finding") or {}).get("recipe", "").startswith(
                "structure:join_variants:"):
            groups.setdefault(("alias", frozenset(norm_key(x) for p in n.get("pairs") or [] for x in p)), []).append(q)
    drop = set()
    for (kind, _k), same in groups.items():
        tabs = {(q.meta.get("about") or {}).get("table") for q in same}
        if len(same) < 2 or (kind == "odd" and len(tabs) < 2):
            continue
        same.sort(key=lambda q: (-q.value, -float(q.meta.get("stake", 0.0)), q.id))
        keep = same[0]
        drop |= {id(q) for q in same[1:]}
        if kind == "odd":
            _merge_odd(analysis, keep, same[1:])
        else:
            keep.meta["tables"] = list(dict.fromkeys((keep.meta.get("tables") or [])
                                                     + [t for q in same[1:] for t in q.meta.get("tables") or []]))
    return [q for q in qs if id(q) not in drop]


def _same_rows_host(analysis, q, others: list):
    """The question (about another column of the same table) naming a value whose
    rows are the odd group's rows, 90% or more both ways; None when there is none."""
    n = q.meta["finding"]["numbers"]
    t = next((t for t in analysis.tables if t.tid == n["table"]), None)
    if t is None or n["col"] not in t.headers:
        return None
    g = t.headers.index(n["col"])
    mine = {i for i, r in enumerate(t.rows) if g < len(r) and norm_key(r[g]) == norm_key(n["value"])}
    if not mine:
        return None
    for o in others:
        ab = o.meta.get("about") or {}
        if ab.get("table") != t.tid or not ab.get("col") or ab["col"] == n["col"] or ab["col"] not in t.headers:
            continue
        j = t.headers.index(ab["col"])
        for v in ab.get("values") or []:
            theirs = {i for i, r in enumerate(t.rows) if j < len(r) and norm_key(r[j]) == norm_key(v)}
            both = len(mine & theirs)
            if theirs and both >= 0.9 * len(mine) and both >= 0.9 * len(theirs):
                return o
    return None


def _merge_odd(analysis, keep, others: list) -> None:
    """The odd-group question 'keep' rewritten to name every tab the value is odd
    on, each with its first line of evidence, and to leave it out of each."""
    from .rules import Rule
    parts = [keep] + others
    val = keep.meta["value"]
    where, said = [], []
    for q in parts:
        n = q.meta["finding"]["numbers"]
        t = analysis.table(n["table"])
        where.append(f"{n['col']} on {t.sheet} ({n['rows']:,} rows)")
        said.append(f"on {t.sheet}, {n['evidence'][0]['text']}")
    rules = [Rule("exclude", q.meta["finding"]["numbers"]["table"],
                  [{"col": q.meta["finding"]["numbers"]["col"], "op": "in", "values": [val]}], source=keep.id).to_dict()
             for q in parts]
    rows = sum(q.meta["finding"]["numbers"]["rows"] for q in parts)
    keep.prompt = (f"{val} is unlike the others in {_join(where)}: {'; '.join(said[:2])}. What is {val}?")
    keep.options = [dict(o, desc=f"Its {rows:,} rows on {_join([analysis.table(q.meta['finding']['numbers']['table']).sheet for q in parts])} "
                                 "come out of every count and total") if o["id"] == "leave_out" else o
                    for o in keep.options]
    keep.meta["rules"] = dict(keep.meta.get("rules") or {}, leave_out=rules)
    keep.meta["propose"] = [p for q in parts for p in q.meta.get("propose") or []]
    keep.meta["tables"] = list(dict.fromkeys(q.meta["finding"]["numbers"]["table"] for q in parts))
    keep.meta["also"] = [q.meta.get("about") for q in others]
    keep.meta["clause"] = f"{val} is unlike the others in {_join(where)}"
    stmt = f"{val} in {_join(where)}"
    keep.fact = dict(keep.fact, statement=f"{stmt}, per the owner: {{answer_labels}}.",
                     statements={"count": f"{stmt} is part of the business and counts in every total, per the owner."})


# --------------------------------------------------------------------------
# a date where the file changes, and rows that are there twice
# --------------------------------------------------------------------------
def _boundary_question(analysis, ins: dict):
    """One question per date where several columns change at once: what
    happened, and whether any column means something else from then on. The
    answer is a dated note in the owner's words."""
    from .interview import Q
    n = ins["numbers"]
    t = analysis.table(n["table"])
    when = n["when"]
    k = len({c["col"] for c in n["changes"]})
    # two lines of evidence: at most 3 changes, those that move a sum first; the finding keeps them all
    first = sorted(n["changes"], key=lambda c: ("sign", "number", "label", "form").index(c["kind"])
                   if c["kind"] in ("sign", "number", "label", "form") else 4)[:3]
    rest = list(dict.fromkeys(c["col"] for c in n["changes"] if c["col"] not in {x["col"] for x in first}))
    listed = "; ".join(f"{c['col']}: {c['text']}" for c in first) \
        + (f"; and {len(rest):,} more column{'s' if len(rest) != 1 else ''} ({_join(rest)})" if rest else "")
    signs = [c["col"] for c in n["changes"] if c["kind"] == "sign"]
    rids = [r for r in (_rid(analysis, t.tid, h) for h in signs) if r]
    # what code counted at the seam, said so the answer confirms it: a second header row with other names,
    # and the total rows that end each part, left out of every count
    layout = []
    seam = n.get("seam") or {}
    if seam:
        names = "; ".join(f"{b} for {a}" for a, b in (seam.get("renamed") or [])[:2])
        layout.append(f"a second header row sits at row {seam['row']:,}" + (f" with other names ({names})" if names
                                                                           else ""))
    tot = n.get("totals") or []
    if tot:
        rows = _join([f"{r:,}" for r in tot])
        layout.append(f"each part ends with a total row (row{'s' if len(tot) != 1 else ''} {rows}), left out of "
                      "every count")
    said = ("; ".join(layout) + ".") if layout else ""
    said = said[:1].upper() + said[1:]
    tail = f" {said}" if said else ""
    opts = [{"id": "system", "label": "A new system or export", "desc": "The same things, written another way"},
            {"id": "changed", "label": "A column's meaning changed", "desc": "Type which column and how"},
            {"id": "stacked", "label": "Two files stacked", "desc": "One file under the other"}]
    statements = {
        "system": f"{t.sheet} changes form around {when}: a new system or export, per the owner.{tail}",
        "stacked": f"{t.sheet} changes form around {when}: two files stacked, per the owner.{tail}"}
    # a column whose only change is its sign: 'same meaning, only the sign flipped', with the net code counted,
    # in place of the option with the least behind it (two files stacked, unless the date is a stacked seam)
    only_sign = [h for h in signs if {c["kind"] for c in n["changes"] if c["col"] == h} == {"sign"}]
    if only_sign:
        h = only_sign[0]
        ch = next(c for c in n["changes"] if c["col"] == h)
        bal = (getattr(analysis, "balanced", None) or {}).get(t.tid) or {}
        net = f"; the net is {bal['rule']}" if bal.get("rule") else ""
        # the net code counted is evidence in the prompt, so the pick that confirms it adds nothing unseen
        if bal.get("rule") and bal.get("entries") and bal.get("netted") == bal["entries"]:
            tail = (tail + " " if tail else " ") + (f"Read as {bal['rule']}, every entry by {bal['col']} nets to "
                                                    "zero.")
        sign = {"id": "sign", "label": "Same meaning, only the sign flipped",
                "desc": f"Both signs of {h} mean the same thing{net}"}
        drop = "changed" if seam else "stacked"
        opts = [o for o in opts if o["id"] != drop] + [sign]
        statements.pop(drop, None)
        m = re.match(r"(\w+) before, (\w+) after", ch["text"])
        was = f"{m.group(1)} before {when} and {m.group(2)} from then on" if m else ch["text"]
        statements["sign"] = (f"{h} on {t.sheet} is {was}; both signs of {h} mean the same thing{net}, per the "
                              f"owner.{tail}")
    # new names that take over at this date, every pair sharing 80% or more of its evidence: one more pick here,
    # so the answer about the switch also says the names are the same things (the map covers every pair)
    renamed, rules = _renamed_at(analysis, t, n), {}
    if renamed and "stacked" in {o["id"] for o in opts} and not seam:
        listing, maps, cols = renamed
        opts = [o for o in opts if o["id"] != "stacked"] + [
            {"id": "renamed", "label": "Same things under new names", "desc": _fit_desc(listing)}]
        statements.pop("stacked", None)
        shown = _fit_desc(listing)
        statements["renamed"] = (f"{t.sheet} changes form around {when}: the same things under new names, per the "
                                 f"owner: {shown}. Each counts as its new name in every count and total by "
                                 f"{_join(cols)}.{tail}")
        rules["renamed"] = maps
    return Q(f"find_boundary_{_slug(t.sheet, 16)}_{n['date'].replace('-', '')}", "Switch date",
             f"Around {when}, {t.sheet} changes form ({n['before']:,} rows before, {n['after']:,} from then on): "
             f"{listed}.{tail} What happened then? Does any column mean something different before and after? Pick "
             "all that apply.",
             opts,
             why="A total that runs across the date adds up two ways of writing things, and a column that changed "
                 "meaning adds up two different things.",
             multi=True, kind="history", priority=1, source="finding",
             fact={"kind": "history", "class": "data", "depends": [],
                   "statement": f"{t.sheet} changes form around {when}; per the owner: {{answer_labels}}.",
                   "statements": statements},
             meta={"finding": ins, "covers": ["sign_rule"] if signs else [],
                   "touches": [x for r in rids for x in (f"has_role:{r}", f"has_negatives:{r}")],
                   "about": _about(t.tid, n["col"], "history"), **({"rules": rules} if rules else {}),
                   "exclusive": [["sign", "changed"]] if only_sign and not seam else [],
                   "clause": f"{t.sheet} changes form around {when} in {k} column{'s' if k != 1 else ''}"})


def _renamed_at(analysis, t, n: dict):
    """(the pairs in words, the first HANDOFF_SHOWN then a short 'and X -> Y' tail;
    one map rule per column over every pair; the columns) for the handoffs at a
    boundary's date on columns no dossier asks about, when every pair shares 80% or
    more of its evidence. None otherwise."""
    from .analyze import _pair_why
    from .rules import Rule
    his = [i for h in n.get("handoffs") or [] for i in analysis.insights
           if i.get("recipe") == f"handoff:{t.tid}:{h}" and not _has_dossier(analysis, t.tid, h)]
    pairs = [(i["numbers"]["col"], p, i["numbers"]["via"]) for i in his for p in i["numbers"]["pairs"]]
    if not pairs or not all(p["jaccard"] >= 0.8 for _c, p, _v in pairs):
        return None
    cols = list(dict.fromkeys(c for c, _p, _v in pairs))
    head = [f"{p['old']} -> {p['new']} ({_pair_why(p, v)})" for _c, p, v in pairs[:HANDOFF_SHOWN]]
    tail = [f"{p['old']} -> {p['new']}" for _c, p, _v in pairs[HANDOFF_SHOWN:]]
    listing = "; ".join(head) + (f"; and {', '.join(tail)}" if tail else "")
    maps = []
    for c in cols:
        to, vals = {}, []
        for c2, p, _v in pairs:
            if c2 == c:
                to[str(norm_key(p["old"]))] = to[str(norm_key(p["new"]))] = p["new"]
                vals += [p["old"], p["new"]]
        maps.append(Rule("map", t.tid, [{"col": c, "op": "in", "values": vals}], {"col": c, "to": to}).to_dict())
    return listing, maps, cols


def _handoff_question(analysis, ins: dict, follow: bool, more: list | None = None):
    """New values that take over from old ones at a date, each pair with what its
    rows share: the same things under new names? 'Yes, all' makes each shown
    pair one value in every count; it is recommended only when every pair
    shares 80% or more. more: the handoffs of other columns at the same switch,
    listed in the same question."""
    from .interview import Q
    from .rules import Rule
    parts = [ins] + list(more or [])
    n = ins["numbers"]
    t = analysis.table(n["table"])
    col, via = n["col"], n["via"]
    y, mo, dd = (int(x) for x in n["date"].split("-"))
    when = f"{_MONTHS[mo - 1]} {dd}, {y}"
    # the pairs shown, shared out between the columns: each at least one, the rest to the columns with more,
    # so a column with few pairs never takes room another needs
    counts = [len(x["numbers"]["pairs"]) for x in parts]
    rooms = [min(1, c) for c in counts]
    spare = HANDOFF_SHOWN - sum(rooms)
    while spare > 0 and any(r < c for r, c in zip(rooms, counts)):
        for i, c in enumerate(counts):
            if spare > 0 and rooms[i] < c:
                rooms[i] += 1
                spare -= 1
    room_of = {id(x): max(1, r) for x, r in zip(parts, rooms)}

    def said(x):
        m = x["numbers"]
        from .analyze import _pair_why
        return "; ".join(f"{p['old']} -> {p['new']} ({_pair_why(p, m['via'])})" for p in m["pairs"][:room_of[id(x)]])
    cols = [x["numbers"]["col"] for x in parts]
    shown = said(ins) if len(parts) == 1 else "; ".join(f"{x['numbers']['col']}: {said(x)}" for x in parts)
    # when every pair, shown or not, shares 80% or more of its evidence, the pairs past the room are one short
    # tail and the map covers them all; otherwise the map covers only the pairs shown
    every = all(p["jaccard"] >= 0.8 for x in parts for p in x["numbers"]["pairs"])
    rest = [p for x in parts for p in x["numbers"]["pairs"][room_of[id(x)]:]] if every else []
    if rest:
        shown += "; and " + ", ".join(f"{p['old']} -> {p['new']}" for p in rest)
    qid = f"{'follow' if follow else 'find'}_handoff_{_slug(t.sheet, 16)}_{_slug('_'.join(cols))}"
    rules, values = [], []
    for x in parts:
        m = x["numbers"]
        mine = m["pairs"] if every else m["pairs"][:room_of[id(x)]]
        to = {}
        for p in mine:
            to[str(norm_key(p["old"]))] = to[str(norm_key(p["new"]))] = p["new"]
        vals = [v for p in mine for v in (p["old"], p["new"])]
        values += vals
        rules.append(Rule("map", t.tid, [{"col": m["col"], "op": "in", "values": vals}], {"col": m["col"], "to": to},
                          source=qid).to_dict())
    by = _join(cols)
    what = f"{by} on {t.sheet} {'uses' if len(parts) == 1 else 'use'}"
    # the treatment the pick shows is said in its note: each old value counts as its new one
    treat = f"Each old value counts as its new one in every count and total by {by}"
    q = Q(qid, "New names?",
          f"From {when} on, {what} new values where old ones stop. Same things under new names? {shown}.",
          [{"id": "all", "label": "Yes, all the same", "desc": treat},
           {"id": "some", "label": "Some of them", "desc": "Type which ones"},
           {"id": "none", "label": "No, different things", "desc": "Keep them apart"}],
          why=f"Until old and new names are one, every count by {by} splits one thing in two.",
          kind="mapping", priority=1, source="finding",
          fact={"kind": "mapping", "class": "data", "depends": [],
                "statement": f"{by} on {t.sheet}, old and new values from {when} ({shown}): {{answer_labels}}, per the "
                             "owner.",
                "statements": {"all": f"From {when} on, {what} new names for the same things, per the owner: {shown}. "
                                      f"{treat}.",
                               "none": f"The old and new values of {by} on {t.sheet} are different things, per the "
                                       f"owner: {shown}."}},
          meta={"finding": ins, "rules": {"all": rules if len(rules) > 1 else rules[0]},
                "about": _about(t.tid, col, "meaning", values),
                "also": [_about(t.tid, x["numbers"]["col"], "meaning") for x in parts[1:]],
                # typed words that pair every value shown confirm the map, as the pick does
                "map_text": {"option": "all", "values": values},
                "clause": f"{len(n['pairs'])} {col} values on {t.sheet} replaced by new ones from {when}"})
    if all(p["jaccard"] >= 0.8 for x in parts for p in x["numbers"]["pairs"][:room_of[id(x)]]):
        q.recommend, q.recommend_basis = "all", f"Each pair shares 80% or more of its {plural_noun(via)}."
    return q


def _has_dossier(analysis, tid: str, col: str, offered: bool = True, folds: bool = False) -> bool:
    """The column has a dossier (offered: one that already offers its old and new values as one; folds: one
    that lists its values and folds another finding's evidence beside them, never a names-and-codes map)."""
    return any((q.meta.get("about") or {}).get("table") == tid and q.meta["about"].get("col") == col
               and (not offered or "handoff" in (q.meta.get("rules") or {})) and not (folds and q.meta.get("lookup"))
               for q in code_dossiers(analysis, {}))


def _copies_question(analysis, ins: dict):
    """Rows there twice, once under each numbering family: which copy counts.
    Keeping one family leaves the other family's copies out of every count and
    total (by its prefix inside the window when all its rows there are copies,
    else by their numbers)."""
    from .interview import Q
    from .rules import Rule
    n = ins["numbers"]
    t = analysis.table(n["table"])
    col, fam, rows = n["col"], n["families"], n["rows"]
    out_a, out_b = (f.get("rows", rows) for f in n["filters"])      # the rows each keep leaves out
    named = [f if f != "plain" else "plain-number" for f in fam]
    qid = f"find_copies_{_slug(t.sheet, 16)}_{_slug(col)}"
    rules = {}
    for oid, drop in (("keep_a", 1), ("keep_b", 0)):
        f = n["filters"][drop]
        pred = ([{"col": col, "op": "prefix", "values": [f["prefix"]]},
                 {"col": n["date_col"], "op": "between", "values": list(n["window"])}] if f["prefix"]
                else [{"col": col, "op": "in", "values": list(f["ids"])}])
        rules[oid] = Rule("exclude", t.tid, pred, source=qid).to_dict()
    # each keep says the copies it leaves out, by count and by the numbering the prompt names them with
    gone = {"keep_a": f"the {out_b:,} {named[1]} copies are left out of every count and total",
            "keep_b": f"the {out_a:,} {named[0]} copies are left out of every count and total"}
    opts = [{"id": "keep_a", "label": f"Keep the {named[0]} rows", "desc": gone["keep_a"][:1].upper() + gone["keep_a"][1:]},
            {"id": "keep_b", "label": f"Keep the {named[1]} rows", "desc": gone["keep_b"][:1].upper() + gone["keep_b"][1:]},
            {"id": "both", "label": "Both are real", "desc": "Every row counts"}]
    # what code counted about the two numberings: every old row there has a twin, and the new numbering takes over
    old = n.get("old")
    said = []
    if old is not None and n.get("every"):
        said.append(f"every {fam[old]} row dated {n['span']} has a {fam[1 - old]} twin")
    if old is not None and n.get("takes_over"):
        # the date the file changes form at, when one falls inside the window or just after it
        import datetime as dt
        lo, hi = n["window"]
        end = (dt.date.fromisoformat(hi) + dt.timedelta(days=31)).isoformat()
        at = sorted((i["numbers"]["date"], i["numbers"]["when"]) for i in analysis.insights
                    if i.get("recipe", "").startswith(f"boundary:{t.tid}:") and lo <= i["numbers"]["date"] <= end)
        said.append(f"{fam[1 - old]} numbering is used from {at[0][1] if at else n['takes_over']} on")
    tail = (" " + "; ".join(said)[:1].upper() + "; ".join(said)[1:] + ".") if said else ""
    # the pick, then the copies it leaves out, then the evidence: the rows the treatment names are never
    # read as the kept ones
    statements = {k: (f"The {rows:,} rows dated {n['span']} that appear twice ({fam[0]} and {fam[1]} "
                      f"numbers): keep the {named[i]} rows, per the owner; {gone[k]}.")
                  for i, k in enumerate(("keep_a", "keep_b"))}
    if old is not None and said:
        # keeping the old rows: the new system loaded them again
        k = "keep_a" if old == 0 else "keep_b"
        opts = [dict(o, label=f"Re-imported by the new system: keep {named[old]} rows"[:60]) if o["id"] == k else o
                for o in opts]
        statements[k] = (f"The {rows:,} rows dated {n['span']} that appear twice were re-imported by the new system, "
                         f"per the owner: keep the {named[old]} rows; {gone[k]}.{tail}")
    return Q(qid, "Copies",
             f"{rows:,} rows dated {n['span']} appear twice, once with {fam[0]} numbers and once with {fam[1]} (for "
             f"example {n['example'][0]} and {n['example'][1]}: {n['what']}).{tail} Which copy counts?",
             opts,
             why="A window loaded twice counts its money twice in every total.",
             kind="rule", priority=1, source="finding",
             fact={"kind": "rule", "class": "data", "depends": [],
                   "statement": f"The {rows:,} rows on {t.sheet} dated {n['span']} that appear twice ({fam[0]} and "
                                f"{fam[1]} numbers): {{answer_labels}}, per the owner.",
                   "statements": statements},
             meta={"finding": ins, "rules": rules, "about": _about(t.tid, col, "treatment", n["example"]),
                   "clause": f"{rows:,} rows on {t.sheet} dated {n['span']} appear twice under two numberings"})


def _pairs_question(analysis, ins: dict):
    """Numbers there twice, once under the usual code and once under another,
    with the same amounts: what the second row does to its twin. 'Cancels both'
    leaves each pair out of every count and total. 'Reverses it' does the same
    when a twin carries its row's sign (undoing it takes both rows out of the
    sums); when every twin has the other sign the two rows already net to zero
    and nothing is left out."""
    from .interview import Q
    from .rules import Rule
    n = ins["numbers"]
    t = analysis.table(n["table"])
    col, code, (usual, other), k = n["col"], n["code"], n["values"], n["pairs"]
    qid = f"find_pairs_{_slug(t.sheet, 16)}_{_slug(col)}"
    rule = Rule("pair", t.tid, [{"col": code, "op": "in", "values": [other]}], {"match": [col]}, source=qid)
    netted = bool(n.get("netted"))
    rules = {"cancels": rule.to_dict()} if netted else {"cancels": rule.to_dict(), "reverses": rule.to_dict()}
    return Q(qid, "Twin rows",
             f"{k:,} {col} numbers on {t.sheet} appear twice, once as {usual} and once as {other} in {code} with the "
             f"same amount. What does {_a(other)} {other} row do to its twin?",
             [{"id": "cancels", "label": "Cancels both",
               "desc": f"Both rows of each pair ({2 * k:,} rows) come out of every count and total"},
              {"id": "reverses", "label": "Reverses it",
               "desc": "It reverses its twin; the two rows net to zero" if netted
               else f"It reverses its twin; both rows of each pair ({2 * k:,} rows) come out of the sums"},
              {"id": "separate", "label": "Separate things", "desc": "Both rows count"}],
             why="A row and its void both in the totals count money that never moved.",
             kind="rule", priority=1, source="finding",
             fact={"kind": "rule", "class": "data", "depends": [],
                   "statement": f"{_a(other).capitalize()} {other} row in {code} on {t.sheet}, twin of {_a(usual)} "
                                f"{usual} row with the same {col}: {{answer_labels}}, per the owner."},
             meta={"finding": ins, "rules": rules,
                   "about": _about(t.tid, code, "treatment", [usual, other]),
                   "clause": f"{k:,} {col} numbers on {t.sheet} appear as both {usual} and {other}"})


def _upload_question(analysis, ins: dict):
    """One block of a panel (a period and an entity) there twice, each copy with
    its own upload time: keep the later, the earlier, or both. Keeping one
    leaves the other upload's rows of that block out of every count and total."""
    from .interview import Q
    from .rules import Rule
    n = ins["numbers"]
    t = analysis.table(n["table"])
    (w1, w2), (k1, k2), (t1, t2) = n["shown"], n["counts"], n["times"]
    qid = f"find_upload_{_slug(t.sheet, 16)}_{_slug(n['col'])}"
    where = [{"col": h, "op": "between", "values": [v, v]} if kind == "date" else {"col": h, "op": "in", "values": [v]}
             for h, v, kind in zip(n["cols"], n["keys"], n["types"])]
    timed = n.get("timed", True)

    def stamp(s):          # a date and time is matched as a time; a text stamp as the text it is
        return {"col": n["col"], "op": "between", "values": [s, s]} if timed \
            else {"col": n["col"], "op": "in", "values": [s]}
    rules = {"later": Rule("exclude", t.tid, where + [stamp(t1)], source=qid).to_dict(),
             "earlier": Rule("exclude", t.tid, where + [stamp(t2)], source=qid).to_dict()}
    # text stamps have no order: each option names the stamp it keeps
    keep = ("Keep the later upload", "Keep the earlier upload") if timed \
        else (f"Keep the {_short(w2, 24)} rows", f"Keep the {_short(w1, 24)} rows")
    ask = "Keep the later upload, the earlier, or both?" if timed else "Which upload counts?"
    return Q(qid, "Loaded twice",
             f"{n['label']} on {t.sheet} has {n['size']:,} rows where others have {n['median']:,}, {n['col']} {w1} and "
             f"{w2}. {ask}",
             [{"id": "later", "label": keep[0],
               "desc": f"Leave the {k1:,} rows from {w1} out of every count and total"},
              {"id": "earlier", "label": keep[1],
               "desc": f"Leave the {k2:,} rows from {w2} out of every count and total"},
              {"id": "both", "label": "Keep both", "desc": "Both uploads count"}],
             why="A block loaded twice counts its money twice in every total.",
             kind="rule", priority=1, source="finding",
             fact={"kind": "rule", "class": "data", "depends": [],
                   "statement": f"{n['label']} on {t.sheet}, loaded at {w1} and at {w2}: {{answer_labels}}, per the "
                                "owner."},
             meta={"finding": ins, "rules": rules, "about": _about(t.tid, n["col"], "treatment"),
                   "clause": f"{n['label']} on {t.sheet} has {n['size']:,} rows where others have {n['median']:,}"})


# --------------------------------------------------------------------------
# groups and entities unlike their peers, blanks in the measure, a measure the table lacks
# --------------------------------------------------------------------------
def _odd_group_question(analysis, ins: dict):
    """A group (a location, a channel, a category) unlike the others, with its
    two strongest pieces of evidence. When the money or the price sets it apart
    and the table has a price, the question is the price's unit: a pack or case
    divides the price by the number the owner types, never by the price ratio.
    Otherwise: part of the business, not ours, or another group's other name."""
    from .interview import Q
    from .rules import Rule
    n = ins["numbers"]
    t = analysis.table(n["table"])
    col, val, k, rows = n["col"], n["value"], n["others"], n["rows"]
    lines = "; ".join(e["text"] for e in n["evidence"][:2])
    qid = f"find_odd_{_slug(t.sheet, 16)}_{_slug(col + '_' + val)}"
    head = f"{val} in {col} on {t.sheet} ({rows:,} rows) is unlike the other {k:,} {col} values: {lines}."
    named = _unit_named_elsewhere(analysis, t, col, val)
    if named:
        head += f" {named}"
    where = [{"col": col, "op": "in", "values": [val]}]
    leave = {"id": "leave_out", "label": "Leave it out of totals",
             "desc": f"Its {rows:,} rows come out of every count and total"}
    rules = {"leave_out": Rule("exclude", t.tid, where, source=qid).to_dict()}
    meta = {"finding": ins, "rules": rules, "value": val,
            "clause": f"{val} in {col} on {t.sheet} is unlike the other {k:,} {col} values",
            # said inside another question about the same values: the value and its strongest evidence
            "merge_clause": (f"{val} in {col}: {n['evidence'][0]['text']}" if n.get("evidence") else "")
                            + (f"; {n['category']}" if n.get("category") and n.get("evidence") else "")}
    if n.get("price"):
        price = n["price"]
        one = f"one {n['unit']}" if n.get("unit") else "each one"
        # the columns worked out from the price on this tab, and the other tabs that carry the same items at the
        # same price (joined by their key at 95% or more): one answer divides the price on every one of them
        derived = _derived_from(analysis, t, price)
        also = _same_price_tabs(analysis, t, col, [val] + list(n.get("also") or []), price)
        lineage = ""
        if derived:
            lineage += f" {_join(derived)} on {t.sheet} {'is' if len(derived) == 1 else 'are'} worked out from {price}."
        for x in also:
            dx = f", and {_join(x['derived'])} with it" if x["derived"] else ""
            lineage += (f" The same {x['key_col']} values are on {x['sheet']} at the same {x['col']}: "
                        f"{x['rows']:,} rows, {x['money_words']}{dx}.")
        with_it = [f"{_join(derived)} with it"] if derived else []
        with_it += [f"the same on {x['sheet']} ({x['rows']:,} rows)" for x in also]
        pack = (f"{price} for {val} in {col} on {t.sheet} is for a pack or case of {{answer_number}}, per the owner: "
                f"{price} on its {rows:,} rows is divided by {{answer_number}}"
                + (", " + _join(with_it) if with_it else "") + ".")
        return Q(qid, "Unit price",
                 head + lineage + f" Is {price} there for {one} as counted, or for a pack or case? A pack or case "
                                  f"price is divided by how many are in one.",
                 [{"id": "pack", "label": "For a pack or case",
                   "desc": f"Type how many are in one; {price} on its {rows:,} rows is divided by that"
                           + (f", and the same on {_join([x['sheet'] for x in also])}" if also else "")},
                  {"id": "right", "label": "Right as it is", "desc": f"{price} there is for {one}"}, leave],
                 why=f"A price for a case read as a price for {one} makes every total and comparison of it wrong.",
                 kind="unit", priority=1, source="finding",
                 fact={"kind": "unit", "class": "data", "depends": [],
                       "statement": f"{price} for {val} in {col} on {t.sheet} ({rows:,} rows), per the owner: "
                                    "{answer_labels}.",
                       "statements": {"right": f"{price} for {val} in {col} on {t.sheet} is right as it is, for {one} "
                                               "as counted, per the owner.",
                                      # filled from the number the owner typed with the pick, else not written
                                      "pack": pack}},
                 meta=dict(meta, about=_about(t.tid, price, "unit", [val]),
                           scale={"option": "pack", "table": t.tid, "col": price, "predicate": where},
                           scale_also=[{"table": x["table"], "col": x["col"], "predicate": x["predicate"]}
                                       for x in also],
                           tables=[t.tid] + [x["table"] for x in also] if also else None,
                           option_aspect={"pack": "unit", "right": "unit", "leave_out": "treatment",
                                          "type": "unit"}))
    scope = _odd_scope_question(analysis, ins, t, meta)
    if scope is not None:
        return scope
    opts = [{"id": "count", "label": "Part of the business, count it", "desc": "It stays in every total"}, leave,
            {"id": "same", "label": f"Same as another {col}", "desc": f"Type which {col}"}]
    if _SITE.search(str(col)):
        # a place: 'another's other name' has the least behind it and gives way to a place of ours unlike the sites
        # (its own staff or items say so); a value new late, that a summary tab has no column for, may be a new site
        opts[2] = _ours(col.lower(), _peers(analysis, t.tid, col, val))
        if any(e.get("kind") == "late" for e in n.get("evidence") or []) and _summary_lacks(analysis, t, col, val):
            peers = _peers(analysis, t.tid, col, val)
            opts[0] = {"id": "count", "label": f"A new {col.lower()} like {' or '.join(peers)}: counts"[:60],
                       "desc": "It stays in every total"}
    return Q(qid, "Unlike others",
             head + f" What is {val}?",
             opts,
             why=f"One {col} that is not like the others changes every total and share by {col}.",
             kind="definition", priority=1, source="finding",
             fact={"kind": "definition", "class": "data", "depends": [],
                   "statement": f"{val} in {col} on {t.sheet} ({rows:,} rows), per the owner: {{answer_labels}}.",
                   "statements": dict({"count": f"{val} in {col} on {t.sheet} is part of the business and counts in "
                                                "every total, per the owner." if opts[0]["label"].startswith("Part")
                                       else f"{val} in {col} on {t.sheet} is {_lower_first(opts[0]['label'])} in "
                                            "every total, per the owner."},
                                      **({"ours": f"{val} in {col} on {t.sheet} is "
                                                  f"{_lower_first(opts[2]['label'][:-len(' (type what)')])}, per the "
                                                  "owner."}
                                         if opts[2]["id"] == "ours" else {}))},
             meta=dict(meta, about=_about(t.tid, col, "meaning", [val]),
                       option_aspect={"count": "treatment", "leave_out": "treatment", "same": "meaning",
                                      "ours": "meaning", "type": "meaning"},
                       # the typed name, when it is a value of the column, is read back as one rule with its counts
                       propose=[{"option": "same", "table": t.tid, "col": col, "value": val}]))


def _summary_lacks(analysis, t, col: str, val) -> bool:
    """A calculated summary tab has a column for other values of this column but
    none for this one (a counted summary-labels fact names it)."""
    for i in analysis.insights:
        n = i.get("numbers") or {}
        if i.get("recipe", "").startswith("structure:labels:") and n.get("source") == t.tid and n.get("col") == col \
                and norm_key(val) in {norm_key(x) for x in n.get("missing") or []}:
            return True
    return False


def _odd_scope_question(analysis, ins: dict, t, meta: dict):
    """A group of a column a lookup tab lists (branches, sites) unlike the others:
    is it ours, counted in the totals? Leaving it out takes its rows out under
    every name it was written with (a name and, from a date, its code). None
    when no lookup tab lists the column's values."""
    from .interview import Q
    from .rules import Rule
    n = ins["numbers"]
    lt = next((x for x in analysis.tables if x.sheet == n.get("lookup") and x.tid != t.tid), None)
    if lt is None:
        return None
    col, val, rows = n["col"], n["value"], n["rows"]
    have = set((analysis.col(t.tid, col) or type("c", (), {"counter": {}})()).counter)
    key = next((c for c in analysis.cols[lt.tid] if c.unique and c.type == "text" and set(c.counter) & have), None)
    if key is None:
        return None
    listed = [str(r[key.j]).strip() for r in lt.rows if key.j < len(r) and r[key.j] is not None]
    also = n.get("also") or []
    named = f"{val} (also {_join(also)})" if also else val
    lines = "; ".join(e["text"] for e in n["evidence"][:3])
    m = money_column(analysis, t)
    toward = f"{m.header} totals" if m is not None else "the totals"
    qid = f"find_odd_{_slug(t.sheet, 16)}_{_slug(col + '_' + val)}"
    names = [val] + list(also)
    rule = Rule("exclude", t.tid, [{"col": col, "op": "in", "values": names}], source=qid).to_dict()
    # 'not ours' says what it is, so it leaves every tab its name is on (a roster, a branch list), not one measure
    joined = {}
    for v in names:
        for sh, tid, c, m in _id_tabs(analysis, t, col, v):
            if tid != lt.tid or c != key.header:
                joined.setdefault((tid, c), [sh, []])[1].append(v)
    rules = [rule] + [Rule("exclude", tid, [{"col": c, "op": "in", "values": vs}], source=qid).to_dict()
                      for (tid, c), (_sh, vs) in joined.items()]
    return Q(qid, header_words("Ours?", val),
             f"{lt.sheet} lists {len(listed):,} {values_of(col, len(listed))} ({_listed(listed)}). {named} in {col} on "
             f"{t.sheet} ({rows:,} rows) is unlike the others: {lines}. Is {val} ours, counted in {toward}?",
             [{"id": "count", "label": "Ours, count it", "desc": f"It stays in {toward}"},
              {"id": "leave_out", "label": "Not ours: leave it out (type why)",
               "desc": f"Its {rows:,} rows, under {'both names' if also else 'its name'}, come out of every count "
                       "and total"}],
             why=f"One {col} that is not the business's changes every total and share by {col}.",
             kind="coverage", priority=1, source="finding",
             fact={"kind": "coverage", "class": "data", "depends": [],
                   "statement": f"{named} in {col} on {t.sheet} ({rows:,} rows), per the owner: {{answer_labels}}.",
                   "statements": {
                       "count": f"{val} in {col} on {t.sheet} is ours, counted in {toward}, per the owner: it stays in "
                                f"{toward}.",
                       "leave_out": f"{named} in {col} on {t.sheet} is not ours, per the owner: leave it out; its "
                                    f"{rows:,} rows, under {'both names' if also else 'its name'}, come out of every "
                                    "count and total."}},
             meta=dict(meta, rules={"leave_out": rules if len(rules) > 1 else rule}, about=_about(t.tid, col, "scope", names),
                       option_aspect={"count": "scope", "leave_out": "scope", "type": "meaning"},
                       tables=[t.tid, lt.tid]))


def _period_question(analysis, ins: dict):
    """The title names a period whose end the last date on the cycle falls short of:
    is the period complete? 'Complete' is recommended when the next date on the
    cycle falls after the period's end."""
    import datetime as dt

    from .interview import Q
    n = ins["numbers"]
    t = analysis.table(n["table"])
    col = n["col"]
    last = _span(*(dt.date.fromisoformat(n["last"]),) * 2)
    end = _span(*(dt.date.fromisoformat(n["end"]),) * 2)
    oc = n.get("off_code") or {}
    off = f"; the {oc['rows']:,} {oc['value']} rows fall off that cycle, mostly on a {oc['weekday']}" if oc else ""
    head = (f"{t.sheet}'s title says \"{_short(n['said'], 60)}\", to {end}. {col} falls every {n['step']} days on a "
            f"{n['weekday']} ({n['dates']:,} dates, the last on {last}){off}.")
    noun = col.lower()
    q = Q(f"find_period_{_slug(t.sheet, 16)}_{_slug(col)}", "Complete?",
          f"{head} Is the period to {end} complete?",
          [{"id": "complete", "label": f"Complete: {last} was the last {noun}"[:60],
            "desc": f"Nothing more after {last}"},
           {"id": "missing", "label": "Some are missing (type which)", "desc": "Type which dates or rows"}],
          why="A period read as complete when it is not makes every total of it short.",
          kind="coverage", priority=1, source="finding",
          fact={"kind": "coverage", "class": "data", "depends": [],
                "statement": f"{head} Per the owner: {{answer_labels}}.",
                "statements": {"complete": f"{head} The period to {end} is complete, per the owner: {last} was the "
                                           f"last {noun}; nothing more after {last}."}},
          meta={"finding": ins, "about": _about(t.tid, col, "scope"),
                "clause": f"the last {noun} on {t.sheet} is {last}, before the title's {end}"})
    if n.get("complete"):
        nxt = _span(*(dt.date.fromisoformat(n["next"]),) * 2)
        q.recommend, q.recommend_basis = "complete", f"The next {noun} on the cycle, {nxt}, falls after {end}."
    return q


def _nonstock_question(analysis, ins: dict):
    """Lines of a category a list gives no cost (gift cards): how they count, pick
    all that apply. Leaving them out of a total takes their rows out of that
    column's totals only."""
    from .interview import Q
    from .rules import Rule
    n = ins["numbers"]
    t = analysis.table(n["table"])
    lt = analysis.table(n["lookup"])
    col, cat, k = n["col"], n["category"], n["rows"]
    qid = f"find_nonstock_{_slug(t.sheet, 16)}_{_slug(cat)}"
    measure = n.get("money") or ""
    opts, rules = [], {}
    pred = [{"col": col, "op": "in", "values": list(n["values"])}]
    if measure:
        opts.append({"id": "count", "label": f"Count them as {measure}"[:60], "desc": f"They stay in {measure} totals"})
        opts.append({"id": "leave", "label": f"Leave out of {measure} totals"[:60],
                     "desc": f"Their {k:,} lines stay out of {measure} totals"})
        rules["leave"] = Rule("exclude", t.tid, pred, scope=[measure], source=qid).to_dict()
    if n.get("adj"):
        opts.append({"id": "adj", "label": f"Leave out of {n['adj']} totals"[:60],
                     "desc": f"Their {k:,} lines stay out of {n['adj']} totals"})
        rules["adj"] = Rule("exclude", t.tid, pred, scope=[n["adj"]], source=qid).to_dict()
    if len(opts) < 2:
        return None
    head = ins["statement"].rstrip(".")
    return Q(qid, header_words("Count", cat), f"{head}. How do the {cat} lines count? Pick all that apply.",
             opts[:3], why=f"Lines that are not stock counted as sales move every total and average they are in.",
             multi=True, kind="exclusion", priority=1, source="finding",
             fact={"kind": "exclusion", "class": "data", "depends": [],
                   "statement": f"The {k:,} {cat} lines on {t.sheet} (no {n['blank_col']} on {lt.sheet}), per the "
                                "owner: {answer_labels}."},
             meta={"finding": ins, "rules": rules, "about": _about(t.tid, col, "treatment", n["keys"]),
                   "exclusive": [["count", "leave"]] if measure else [], "tables": [t.tid, lt.tid],
                   "clause": f"{cat} lines on {t.sheet} have no {n['blank_col']} on {lt.sheet}"})


def _lookup_question(analysis, answers: dict):
    """A list the main table looks up (a chart of accounts) whose few-valued
    columns the findings used to classify its rows (a category for a blank
    slice, a normal side for lines on the other side): is that how the rows roll
    up? One question, naming each column with its values."""
    from .interview import Q
    main = analysis.main_table
    if main is None:
        return None
    used: dict = {}
    for i in analysis.insights:
        n = i.get("numbers") or {}
        rec = i.get("recipe", "")
        by = (n.get("slice") or {}).get("col") if rec.startswith("blanks:") else n.get("by") \
            if rec.startswith("structure:blank_slice:") else None
        if by and " on " in by:
            col, _, sheet = by.rpartition(" on ")
            used.setdefault(sheet, set()).add(col)
        if rec.startswith("contra:") and n.get("lookup"):
            t = next((t for t in analysis.tables if t.tid == n.get("table")), None)
            got = analysis._side_ref(t, analysis.col(t.tid, n["col"])) if t is not None else None
            if got:
                used.setdefault(got[0].sheet, set()).add(got[2].header)
    for j in analysis.joins:
        if j["band"] != "auto" or j["from_table"] != main.tid:
            continue
        lt = analysis.table(j["to_table"])
        if lt.sheet not in used:
            continue
        qid = f"find_lookup_{_slug(lt.sheet, 24)}"
        if qid in (answers or {}):
            return None
        cols = [c for c in analysis.cols[lt.tid] if c.header in used[lt.sheet]]
        if not cols:
            continue
        key = j["to_col"]
        # the list's few-valued columns by their job: which report section a key lands in, and its usual side
        jobs = [c for c in analysis.cols[lt.tid] if c.type == "text" and c.header != key and not c.distinct_capped
                and 2 <= c.distinct <= 15]
        side = next((c for c in jobs if {str(k).lower() for k in c.counter} <= {"debit", "credit", "dr", "cr"}), None)
        cat = next((c for c in sorted(jobs, key=lambda c: -c.distinct) if c is not side), None)
        a = cat or side or cols[0]
        domain = _LOOKUP_DOMAIN.get((analysis.playbook or {}).get("id")) or f"list of {values_of(key, 2)}"
        parts = ([f"{cat.header} deciding which report section each {key} lands in"] if cat else []) \
            + ([f"{side.header} its usual side"] if side else [])
        with_ = f", with {' and '.join(parts)}" if parts else ""
        yes_desc = (f"{cat.header} gives each {key} its report section" if cat else
                    f"{side.header} gives each {key} its usual side" if side else f"Every {key} is on it")
        said = " and ".join(x for x in ([f"{cat.header} gives each {key} its report section"] if cat else [])
                            + ([f"{side.header} its usual side" if cat else f"{side.header} gives each {key} its usual "
                                                                             "side"] if side else []))
        return_q = Q(qid, header_words("Roll-up", lt.sheet),
                     f"{lt.sheet} lists {lt.n_rows:,} {key} values; I read the {main.n_rows:,} rows on {main.sheet} "
                     f"through it. Is {lt.sheet} your {domain}{with_}?",
                     [{"id": "yes", "label": f"Yes, it is the {domain}"[:60], "desc": yes_desc},
                      {"id": "partly", "label": "Partly (type how)", "desc": "Type what differs"},
                      {"id": "reference", "label": "Reference only", "desc": "Only for reference"}],
                     why=f"Every total by {a.header} rests on {lt.sheet}; if reports group another way, they differ.",
                     kind="mapping", priority=2, source="finding",
                     fact={"kind": "mapping", "class": "data", "depends": [],
                           "statement": f"{lt.sheet}, per the owner: {{answer_labels}}.",
                           "statements": {"yes": f"{lt.sheet} is the {domain}, per the owner"
                                                 + (f": {said}." if said else ".")}},
                     meta={"about": _about(lt.tid, a.header, "meaning", [k for k, _n in a.top]),
                           "tables": [main.tid, lt.tid],
                           "clause": f"{lt.sheet} gives each {key} " + " and ".join(c.header for c in (cat, side) if c)})
        # a sentence the owner typed anywhere that says what the list is backs the yes
        named = _names_sheet(answers, lt.sheet)
        if named:
            return_q.recommend, return_q.recommend_basis = "yes", f"You wrote: \"{_short(named, 120)}\""
        _worth(return_q, 0.02)
        return return_q
    return None


# what a list another table looks its keys up in is called, in each playbook's own words
_LOOKUP_DOMAIN = {"ledger": "chart of accounts", "ar_ap": "chart of accounts", "procurement": "item list",
                  "payroll_hr": "employee roster", "inventory": "item list", "sales_transactions": "product list"}


def _names_sheet(answers: dict, sheet: str) -> str:
    """The first sentence the owner typed in any answer that says what a sheet is
    ('The Accounts tab is the chart of accounts'), or ''."""
    from . import privacy
    pat = re.compile(r"(?<![\w])" + re.escape(str(sheet)) + r"(?![\w])(\s+(tab|sheet))?\s+(is|are)\s+(the|our|my|a)\b",
                     re.I)
    for qid, a in (answers or {}).items():
        if not isinstance(a, dict) or qid.startswith("_"):
            continue
        for sent in privacy.said_sentences(str(a.get("text") or "")):
            for part in re.split(r"\band\b|;", sent):
                if pat.search(part):
                    return part.strip(" ,.")
    return ""


def _unit_group_question(analysis, ins: dict):
    """A group whose quantity reads as another unit (few, whole, at a rate far
    above the rest): the same unit as the rest, or another. 'Another unit' keeps
    that group's quantity out of every sum with the others'."""
    from .interview import Q
    n = ins["numbers"]
    t = analysis.table(n["table"])
    q_, g, v = n["col"], n["group_col"], n["value"]
    head = ins["statement"].rstrip(".")
    return Q(f"find_unit_{_slug(t.sheet, 16)}_{_slug(q_ + '_' + v)}", header_words("Unit", q_),
             f"{head}. Are {q_} on {g} {v} rows in the same unit as the rest?",
             [{"id": "same", "label": "Same unit as the rest", "desc": f"{q_} adds up with the other {g} values"},
              {"id": "other", "label": "Another unit (type which)",
               "desc": f"Type the unit; {q_} on its {n['rows']:,} rows is never added with the rest"}],
             why=f"Summing {q_} in two units makes every total and average of it wrong.",
             kind="unit", priority=1, source="finding",
             fact={"kind": "unit", "class": "data", "depends": [],
                   "statement": f"{q_} on {g} {v} rows on {t.sheet}, per the owner: {{answer_labels}}.",
                   "statements": {"same": f"{q_} on {g} {v} rows on {t.sheet} is in the same unit as the rest, per "
                                          f"the owner: {q_} adds up with the other {g} values."}},
             meta={"finding": ins, "about": _about(t.tid, q_, "unit", [v]),
                   "unit_group": {"option": "other", "table": t.tid, "col": q_,
                                  "predicate": [{"col": g, "op": "in", "values": [v]}]},
                   "clause": f"{q_} on {g} {v} rows on {t.sheet} reads as another unit"})


def _unpaid_question(analysis, ins: dict):
    """An ID charged month after month that never pays, where the others pay: not
    money owed (its charges leave the income totals), a real balance, or else."""
    from .interview import Q
    from .rules import Rule
    n = ins["numbers"]
    t = analysis.table(n["table"])
    col, who = n["col"], n["value"]
    qid = f"find_unpaid_{_slug(t.sheet, 16)}_{_slug(col)}"
    # what the ID is decides every count and total it is in, on every tab its ID is on: 'not money owed' leaves
    # it out of all of them, whatever measure the finding was counted in
    joined = _id_tabs(analysis, t, col, who)
    where = _join([f"{m:,} row{'s' if m != 1 else ''} on {s}" for s, _tid, _c, m in
                   [(t.sheet, t.tid, col, n["rows"])] + joined])
    rules = [Rule("exclude", t.tid, [{"col": col, "op": "in", "values": [who]}], source=qid).to_dict()] \
        + [Rule("exclude", tid, [{"col": c, "op": "in", "values": [who]}], source=qid).to_dict()
           for _s, tid, c, _m in joined]
    return Q(qid, "Never pays",
             ins["statement"].rstrip(".") + ". What is it?",
             [{"id": "not_owed", "label": "Not money owed: leave it out (type why)",
               "desc": f"Out of every count and total: {where}"},
              {"id": "owed", "label": "A real balance owed", "desc": "Every charge counts"},
              {"id": "type", "label": "Something else (type it)", "desc": "Type what it is"}],
             why=f"Charges nobody pays inflate income and the balance owed until someone says what {who} is.",
             kind="definition", priority=1, source="finding",
             fact={"kind": "definition", "class": "data", "depends": [],
                   "statement": f"{who} in {col} on {t.sheet}, charged and never paying, per the owner: {{answer_labels}}.",
                   "statements": {
                       "not_owed": f"{who} in {col} on {t.sheet} is not money owed, per the owner: leave it out of "
                                   f"every count and total ({where}).",
                       "owed": f"{who} in {col} on {t.sheet} is a real balance owed, per the owner: every charge counts."}},
             meta={"finding": ins, "rules": {"not_owed": rules if len(rules) > 1 else rules[0]},
                   "about": _about(t.tid, col, "meaning", [who]),
                   "tables": [t.tid] + [tid for _s, tid, _c, _m in joined] if joined else None,
                   "clause": f"{who} on {t.sheet} is charged and never pays"})


def _derived_from(analysis, t, price: str) -> list:
    """The number columns of t worked out as price times another column on 99% or
    more of the rows (rules._worked_out_from): they move with the price."""
    from .rules import _worked_out_from
    try:
        return [d for d in _worked_out_from(analysis, t, price) if d != price]
    except (ValueError, KeyError):
        return []


def _same_price_tabs(analysis, t, col: str, vals: list, price: str) -> list:
    """Other tabs that hold the group's items (their key joins t's at 95% or more,
    directly or through one list both look up) at the same price on 80% or more
    of those rows: [{table, sheet, col (the price there), key_col, rows,
    money_words, derived, predicate}]. [] when no other tab carries them."""
    if col not in t.headers or price not in t.headers:
        return []
    gj, pj = t.headers.index(col), t.headers.index(price)
    group = {norm_key(v) for v in vals} - {None}
    mine = [r for r in t.rows if gj < len(r) and norm_key(r[gj]) in group]
    links, seen = [], set()
    good = [j for j in analysis.joins if float(j.get("rows_matched") or 0) >= 0.95]
    for j in good:
        for (ta, ca), (tb, cb) in (((j["from_table"], j["from_col"]), (j["to_table"], j["to_col"])),
                                   ((j["to_table"], j["to_col"]), (j["from_table"], j["from_col"]))):
            if ta != t.tid or ca == col:
                continue
            if tb != t.tid:
                links.append((ca, tb, cb))
            # another tab that looks up the same list by the same key
            for k in good:
                if (k["to_table"], k["to_col"]) == (tb, cb) and k["from_table"] not in (t.tid, tb):
                    links.append((ca, k["from_table"], k["from_col"]))
    out = []
    for kc, uid, uc in links:
        if (uid, uc) in seen or kc not in t.headers:
            continue
        seen.add((uid, uc))
        u = next((x for x in analysis.tables if x.tid == uid), None)
        if u is None or u.wide or analysis.is_derived(u.tid) or uc not in u.headers:
            continue
        kj, uj = t.headers.index(kc), u.headers.index(uc)
        prices: dict = {}
        for r in mine:
            k, p = norm_key(r[kj] if kj < len(r) else None), r[pj] if pj < len(r) else None
            if k is not None and _is_num(p) and p > 0:
                prices.setdefault(k, set()).add(round(float(p), 4))
        theirs = [r for r in u.rows if uj < len(r) and norm_key(r[uj]) in prices]
        if len(theirs) < 5:
            continue
        best = None
        for c in analysis.cols.get(u.tid, []):
            if c.type != "number" or c.j == uj or c.sensitive:
                continue
            hit = sum(1 for r in theirs if c.j < len(r) and _is_num(r[c.j]) and any(
                abs(float(r[c.j]) - p) <= max(0.005, 0.005 * p) for p in prices[norm_key(r[uj])]))
            if hit >= 0.8 * len(theirs) and (best is None or hit > best[0]):
                best = (hit, c)
        if best is None:
            continue
        up = best[1]
        m = money_column(analysis, u)
        derived = _derived_from(analysis, u, up.header)
        money = sum(abs(r[m.j]) for r in theirs if m is not None and m.j < len(r) and _is_num(r[m.j]))
        words = f"{money_fmt(analysis, u, m)(money)} of {m.header}" if m is not None else "no money column"
        keys = sorted({str(r[uj]).strip() for r in theirs if uj < len(r) and r[uj] is not None})
        out.append({"table": u.tid, "sheet": u.sheet, "col": up.header, "key_col": uc, "rows": len(theirs),
                    "money_words": words, "derived": derived,
                    "predicate": [{"col": uc, "op": "in", "values": keys}]})
    return out[:2]


def _unit_named_elsewhere(analysis, t, col: str, val: str) -> str:
    """In a unit column: how many rows in other units have a pack text that names
    this unit ('2,138 CS rows and 167 EA rows have a Pack that names LB'), or ''."""
    from collections import Counter

    from .interview import is_unit_col
    c = analysis.col(t.tid, col)
    if c is None or not is_unit_col(col, _rid(analysis, t.tid, col)) or len(str(val).strip()) < 2:
        return ""
    pack = next((x for x in analysis.cols[t.tid] if x is not c and x.type == "text"
                 and (_rid(analysis, t.tid, x.header) == "pack" or re.search(r"\bpack", str(x.header), re.I))), None)
    if pack is None:
        return ""
    word = re.compile(r"(?<![A-Za-z])" + re.escape(str(val).strip()) + r"(?![A-Za-z])", re.I)
    got = Counter()
    for r in t.rows:
        u = r[c.j] if c.j < len(r) else None
        p = r[pack.j] if pack.j < len(r) else None
        if norm_key(u) != norm_key(val) and u is not None and isinstance(p, str) and word.search(p):
            got[str(u).strip()] += 1
    if sum(got.values()) < 5:
        return ""
    parts = [f"{m:,} {u} rows" for u, m in got.most_common(2)]
    more = sum(got.values()) - sum(m for _u, m in got.most_common(2))
    if more:
        parts.append(f"{more:,} more")
    return f"{_join(parts)} have a {pack.header} that names {val}."


def _measure_blank_question(analysis, ins: dict):
    """The main measure blank on rows that are otherwise filled in: zero,
    unknown (the rows left out), or the last value carried on (read back as a
    rule with its counts before it changes a number)."""
    from .interview import Q
    from .rules import Rule
    n = ins["numbers"]
    t = analysis.table(n["table"])
    k = n["rows"]
    # asked about the input the money is worked out from, when the blanks come from it
    col = n.get("input") or n["col"]
    qid = f"find_blankm_{_slug(t.sheet, 16)}_{_slug(n['col'])}"
    rule = Rule("exclude", t.tid, [{"col": col, "op": "blank", "values": []}], source=qid)
    where = f", mostly at {n['where']}" if n.get("where") else ""
    if n.get("input"):
        rest = [h for h in n.get("with") or [] if h != col]
        also = f" and {_join(rest)}" if rest else ""
        same = (f"; {n['col']}{also} ({n['col']} = {col} x {n['price']}) {'is' if not rest else 'are'} blank on the "
                "same rows")
        head = f"{col} on {t.sheet} is blank on {k:,} of {n['total']:,} rows{where}{same}."
    else:
        also = n.get("with") or []
        same = f", the same rows where {_join(also)} {'is' if len(also) == 1 else 'are'} blank" if also else ""
        head = f"{col} on {t.sheet} is blank on {k:,} of {n['total']:,} rows{same}{where}."
    what = f"A blank {col} on {t.sheet} ({k:,} row{'s' if k != 1 else ''})"
    # one meaning each, and what it does to the totals
    return Q(qid, header_words("Blank", col),
             f"{head} What does a blank {col} mean?",
             [{"id": "zero", "label": "Not counted that period: count it as zero",
               "desc": "A blank adds nothing to any total"},
              {"id": "real_zero", "label": "A real zero", "desc": "Nothing was there; it adds nothing to any total"},
              {"id": "unknown", "label": "Unknown: leave the row out",
               "desc": f"The {k:,} row{'s' if k != 1 else ''} come out of every count and total"}],
             why=f"Counts and averages of {col} are off until a blank is read the right way.",
             kind="definition", priority=1, source="finding",
             fact={"kind": "definition", "class": "data", "depends": [],
                   "statement": f"{what}: {{answer_labels}}, per the owner.",
                   "statements": {
                       "zero": f"{what} means it was not counted that period: count it as zero, per the owner; a blank "
                               "adds nothing to any total.",
                       "real_zero": f"{what} is a real zero, per the owner: nothing was there; it adds nothing to any "
                                    "total.",
                       "unknown": f"{what} is unknown, per the owner: leave the row out; the {k:,} "
                                  f"row{'s' if k != 1 else ''} come out of every count and total."}},
             meta={"finding": ins, "rules": {"unknown": rule.to_dict()}, "about": _about(t.tid, col, "blanks"),
                   "clause": f"{col} on {t.sheet} is blank on {k:,} rows{where}"})


def _outliers_question(analysis, ins: dict):
    """Single records unlike their peers, in one question: leave each out, or
    all real. A lone ratio far from every other row's can be right on purpose,
    which is kept as a value no reader should 'fix'."""
    from .interview import Q
    from .rules import Rule
    n = ins["numbers"]
    t = analysis.table(n["table"])
    items = n["items"]
    k = len(items)
    qid = f"find_outliers_{_slug(t.sheet, 16)}"
    # 3 or more: one 'leave all out' only when every one is a record of one column; else the first two, each its own
    same = [x for x in items if x["col"] == items[0]["col"] and x["kind"] != "ratio"]
    each = k > 2 and len(same) < k
    shown = 2 if each else 3
    listed = "; ".join(f"{_rec(x, 30)} ({x['text']})" for x in items[:shown]) \
        + (f"; and {k - shown:,} more" if k > shown else "")

    def rule(xs):
        if len(xs) == 1 and xs[0]["kind"] == "ratio":
            x = xs[0]
            pred = [{"col": x["b"], "op": "in", "values": [x["value"]]}, {"col": x["a"], "op": "in", "values": [x["base"]]}]
            if x["col"] not in (x["a"], x["b"]) and not str(x["id"]).startswith("row "):
                pred.insert(0, {"col": x["col"], "op": "in", "values": [x["id"]]})
            return Rule("exclude", t.tid, pred, source=qid).to_dict()
        return Rule("exclude", t.tid, [{"col": xs[0]["col"], "op": "in", "values": [x["id"] for x in xs]}],
                    source=qid).to_dict()
    rules, protect = {}, {}
    if k == 1 and items[0]["kind"] == "ratio":
        x = items[0]
        from .recipes import fmt_num
        # a value kept as an exception says what it is and why: the pick carries the owner's words
        opts = [{"id": "leave_1", "label": "Leave that row out (type why)",
                 "desc": f"Row {x['row']:,} comes out of every count and total"},
                {"id": "right", "label": "Right, on purpose (say why)", "desc": "Keep it as written; do not fix it"}]
        rules["leave_1"] = rule([x])
        # kept on the answer only when 'Right, on purpose' is the pick
        protect = {"option": "right", "table": t.tid, "col": x["b"], "row": x["row"], "value": x["value"]}
        about = _about(t.tid, x["b"], "treatment", [x["id"]])
        # the kept value and what it was compared with, as the prompt showed them
        said = re.sub(r"^on row [\d,]+ " + re.escape(str(x["b"])) + r" is ", "", x["text"])
        statements = {"right": f"{x['b']} {fmt_num(x['value'])} on {t.sheet} {_rec_named(x)} is right, on purpose, "
                               f"per the owner: {said}; keep it as written, do not fix it.",
                      "leave_1": f"{t.sheet} {_rec_named(x)} comes out of every count and total, per the owner: "
                                 f"{x['b']} there is {said}."}
        ask, multi = "Is it right?", False
    elif k == 1 and items[0]["kind"] == "sentinel":
        # one record whose number stands out: what it is, with what that means for every tab its ID is on
        x = items[0]
        joined = _id_tabs(analysis, t, x["col"], x["id"])
        where = _join([f"{m:,} row{'s' if m != 1 else ''} on {s}" for s, _tid, _c, m in
                       [(t.sheet, t.tid, x["col"], x["n"])] + joined])
        gone = [rule([x])] + [Rule("exclude", tid, [{"col": c, "op": "in", "values": [x["id"]]}],
                                   source=qid).to_dict() for _s, tid, c, _m in joined]
        about = _about(t.tid, x["col"], "treatment", [x["id"]])
        money = _record_money(analysis, t, x)
        if money is not None:
            # money on its rows: whether any of it went out decides the cash and cost totals, so each way is a pick
            m, total, fmt = money
            opts = [{"id": "test", "label": "Not real, nothing was paid out (type why)",
                     "desc": f"Leave it out of every tab: {where}"},
                    {"id": "paid", "label": "Not real, but money went out (type why)",
                     "desc": f"Its {x['n']:,} row{'s' if x['n'] != 1 else ''} ({fmt(total)} of {m}) stay in the "
                             "money totals"},
                    {"id": "keep", "label": "Real, keep it", "desc": "Every row counts"}]
            statements = {"test": f"{_rec(x, 30)} on {t.sheet} is not real, per the owner: nothing was paid out; "
                                  f"leave it out of every tab ({where}).",
                          "paid": f"{_rec(x, 30)} on {t.sheet} is not real, per the owner, but money went out: its "
                                  f"{x['n']:,} row{'s' if x['n'] != 1 else ''} ({fmt(total)} of {m}) stay in the "
                                  "money totals.",
                          "keep": f"{_rec(x, 30)} on {t.sheet} is real, per the owner; every row counts."}
        else:
            opts = [{"id": "test", "label": "A test record, never real (type why)",
                     "desc": f"Leave it out of every tab: {where}"},
                    {"id": "keep", "label": "Real, keep it", "desc": "Every row counts"},
                    {"id": "type", "label": "Something else (type what it is)", "desc": "Type what it is"}]
            statements = {"test": f"{_rec(x, 30)} on {t.sheet} is a test record, never real, per the owner: leave "
                                  f"it out of every tab ({where}).",
                          "keep": f"{_rec(x, 30)} on {t.sheet} is real, per the owner; every row counts."}
        rules["test"] = gone
        ask, multi = "What is it?", False
    else:
        if k <= 2 or each:
            opts = []
            for i, x in enumerate(items[:2], 1):
                opts.append({"id": f"leave_{i}", "label": f"Leave {_rec(x, 24, label=True)} out (type why)",
                             "desc": f"Its {x['n']:,} row{'s' if x['n'] != 1 else ''} come out of every count and total"})
                rules[f"leave_{i}"] = rule([x])
        else:
            opts = [{"id": "leave_all", "label": f"Leave all {len(same):,} out",
                     "desc": f"Every row of {_join([_short(x['id'], 20) for x in same[:4]])} comes out of every count "
                             "and total"}]
            rules["leave_all"] = rule(same)
        opts.append({"id": "keep", "label": "All real, keep them" if k != 1 else "Real, keep it",
                     "desc": "Every row counts"})
        if k > 2 and not each:
            opts.append({"id": "type", "label": "Some of them", "desc": "Type which ones"})
        about = _about(t.tid, items[0]["col"], "treatment", [x["id"] for x in items[:shown]])
        named = [_rec(x, 30) for x in items[:shown]]
        statements = {"keep": f"{_join(named)}{f' and {k - shown:,} more' if k > shown else ''} on {t.sheet} "
                              f"{'are' if k != 1 else 'is'} real; every row counts, per the owner."}
        ask, multi = "Leave any out of totals?", True
    return Q(qid, "Unlike peers",
             f"{k:,} record{'s' if k != 1 else ''} on {t.sheet} behave{'s' if k == 1 else ''} unlike "
             f"{'their' if k != 1 else 'its'} peers: {listed}. {ask}",
             opts, why="A test record, a placeholder or a slip counts in every total until someone says what it is.",
             multi=multi, kind="rule", priority=1, source="finding",
             fact={"kind": "rule", "class": "data", "depends": [],
                   "statement": f"Records on {t.sheet} unlike their peers ({listed}): {{answer_labels}}, per the owner.",
                   "statements": statements},
             meta={"finding": ins, "rules": rules, "about": about, "protect": protect,
                   "exclusive": ["keep"] if multi else [],
                   "clause": f"{k:,} record{'s' if k != 1 else ''} on {t.sheet} unlike "
                             f"{'their' if k != 1 else 'its'} peers"})


def _id_tabs(analysis, t, col: str, value) -> list:
    """[(sheet, table, column, rows)] for every other table this ID column joins at
    95% or more that holds the value: a record left out of one is left out of all."""
    out, seen = [], set()
    key = norm_key(value)
    for j in analysis.joins:
        if j.get("rows_matched", 0) < 0.95:
            continue
        if (j["from_table"], j["from_col"]) == (t.tid, col):
            other, oc = j["to_table"], j["to_col"]
        elif (j["to_table"], j["to_col"]) == (t.tid, col):
            other, oc = j["from_table"], j["from_col"]
        else:
            continue
        if other in seen or other == t.tid:
            continue
        seen.add(other)
        ot = analysis.table(other)
        if oc not in ot.headers:
            continue
        jj = ot.headers.index(oc)
        m = sum(1 for r in ot.rows if jj < len(r) and norm_key(r[jj]) == key)
        if m:
            out.append((ot.sheet, ot.tid, oc, m))
    return out


def _factor_words(k) -> str:
    """'raises it by 7%' for a typed 1.07, 'lowers it by 5%' for 0.95; '' for a factor
    that is no change or not a number."""
    try:
        f = float(k)
    except (TypeError, ValueError):
        return ""
    pct = round(abs(f - 1) * 100, 2)
    if not pct or f <= 0:
        return ""
    words = f"{int(pct)}%" if float(pct).is_integer() else f"{pct}%"
    return f"{'raises' if f > 1 else 'lowers'} it by {words}"


def _in_actuals(analysis, named: str) -> bool:
    """A model cell ('Opening seats, Jan 2026 (Revenue!B4)') that sits in the
    periods a typed-to-formula switch holds as actuals: its tab switches, and its
    column is the last typed period or before it."""
    m = _REF.search(str(named or ""))
    if not m:
        return False
    sheet, ci = m.group(1).strip(), 0
    for ch in m.group(2):
        ci = ci * 26 + ord(ch) - 64
    for fa in (analysis.formulas or {}).values():
        cols = ((fa or {}).get("actuals_boundary") or {}).get("cols") or {}
        if sheet in cols and ci - 1 <= int(cols[sheet]):
            return True
    return False


def _rec_named(x: dict) -> str:
    """A record in a sentence, its row number said once: 'row 25 (305, North)',
    or 'row 25', or '<ID>'."""
    s = str(x["id"])
    if s.startswith("row "):
        return s
    return f"row {x['row']:,} ({_short(s, 30)})" if x.get("row") else _short(s, 30)


def _record_money(analysis, t, x: dict):
    """(money column, the money on a record's rows without its sign, how to write it)
    when the record's rows carry money that is not zero, else None."""
    m = money_column(analysis, t)
    if m is None or x.get("col") not in t.headers:
        return None
    j = t.headers.index(x["col"])
    key = norm_key(x["id"])
    total = sum(abs(r[m.j]) for r in t.rows if j < len(r) and norm_key(r[j]) == key and m.j < len(r)
                and _is_num(r[m.j]))
    return (m.header, round(total, 2), money_fmt(analysis, t, m)) if total else None


def _rec(x: dict, n: int, label: bool = False) -> str:
    """A record as the owner can find it: its ID, or a row named by its number
    and its key or label columns ('row 12 (North Yard, 4B)'), never cut inside
    a word; in an option label only the row number."""
    s = str(x["id"])
    if not s.startswith("row "):
        return _short(s, n)
    return s.split(" (", 1)[0] if label else s


def _derive_question(analysis, ins: dict, answers: dict | None = None):
    """A table with a quantity, a price and an adjustment but no amount: confirm
    the measure every total needs, and that the adjustment's totals use the same
    lines, two picks (pick all that apply). When the adjustment is among the
    columns a date where the file changes rewrites, it is asked after that date's
    question is answered, and says the unit the owner gave it from then on. After a
    yes the measure's totals are worked out under the owner's rules."""
    from .interview import Q
    n = ins["numbers"]
    t = analysis.table(n["table"])
    qty, price, adj, name = n["qty"], n["price"], n["adj"], n["name"]
    answers = answers or {}
    later = ""
    for b in analysis.insights:
        bn = b.get("numbers") or {}
        if not b.get("recipe", "").startswith(f"boundary:{t.tid}:") or adj not in {c["col"] for c in bn.get("changes") or []}:
            continue
        bid = f"find_boundary_{_slug(t.sheet, 16)}_{bn['date'].replace('-', '')}"
        if bid not in answers:
            return None                 # the switch comes first: its answer may change what the adjustment is
        when = unit_changed(analysis, answers).get((t.tid, adj))
        if when:
            later = f" {adj} changed unit at {when}, per the owner; its totals count it in its unit from then on."
    q = Q(f"find_derive_{_slug(t.sheet, 16)}", "Measure",
          f"{t.sheet} has no amount column ({n['rows']:,} rows). {name} = {qty} x {price} minus {adj}, on the lines "
          f"that count; {adj} totals use the same lines.{later} Which of these is right? Pick all that apply.",
          [{"id": "yes", "label": f"Yes, count {name} that way", "desc": f"Every total of {name} uses it"},
           {"id": "gross", "label": f"{qty} x {price} only", "desc": f"{adj} is left out of {name}"},
           {"id": "same_lines", "label": f"{adj} totals use the same lines"[:60],
            "desc": f"The lines that count for {name}"}],
          why="Every total of the money depends on how it is worked out.",
          multi=True, kind="definition", priority=1, source="finding",
          fact={"kind": "definition", "class": "data", "depends": [],
                "statement": f"The measure on {t.sheet}, per the owner: {{answer_labels}}.",
                "statements": {"yes": f"{name} on {t.sheet} = {qty} x {price} minus {adj}, on the lines that count, "
                                      "per the owner.",
                               "gross": f"{name} on {t.sheet} = {qty} x {price} only, per the owner; {adj} is left "
                                        f"out of {name}.",
                               "same_lines": f"{adj} totals on {t.sheet} use the same lines that count for {name}, "
                                             "per the owner." + (f" {adj} changed unit at {when}, per the owner; its "
                                                                 "totals count it in its unit from then on."
                                                                 if later else "")}},
          meta={"finding": ins, "about": _about(t.tid, price, "meaning"),
                "derive": {"table": t.tid, "qty": qty, "price": price, "adj": adj, "name": name},
                "exclusive": [["yes", "gross"]],
                "clause": f"{t.sheet} has no amount column"})
    return q


# --------------------------------------------------------------------------
# reference and partner tables: lines outside their dates, list prices, codes that start
# --------------------------------------------------------------------------
LIST_BEFORE = 5           # lines before a run at the list price that make its scope worth asking


def _terms_of(analysis, key) -> dict | None:
    """The counted terms row of a partner (the latest when it has several), with
    words to quote, or None."""
    rows = [x for x in getattr(analysis, "terms", None) or [] if x.get("texts") and not x.get("summary")
            and norm_key(x.get("key")) == norm_key(key)]
    return max(rows, key=lambda x: x.get("start") or "") if rows else None


def _terms_quote(analysis, key) -> str:
    """'<Partner>'s Terms on <tab> say "<text>".' for a partner whose terms row
    has words, '' otherwise: the answer's note then carries the basis the terms state."""
    x = _terms_of(analysis, key)
    if x is None:
        return ""
    h = x.get("terms_col") or next(iter(x["texts"]))
    said = str(x["texts"].get(h) or "").strip().rstrip(".")
    if len(said) > 140:
        said = said[:140].rsplit(" ", 1)[0] + "..."
    return f"{x['key']}'s {h} on {x['sheet']} say \"{said}\"." if said else ""


_WAIVE = re.compile(r"\b(waived?|waives|no charge|not charged|free|included|none|no\s+\w+\s+(fee|charge|surcharge)s?)\b",
                    re.I)


def _terms_waive(analysis, key, names: list) -> bool:
    """A partner's terms say a charge is waived, included or none: a clause of its
    terms text that names the charge (a word of 4 letters or more from its code or
    item) beside a waiving word."""
    x = _terms_of(analysis, key)
    if x is None:
        return False
    words = {w for nm in names for w in re.findall(r"[a-z]{4,}", str(nm or "").lower())}
    for text in (x.get("texts") or {}).values():
        for part in re.split(r"[.;]", str(text or "")):
            if _WAIVE.search(part) and any(re.search(r"\b" + re.escape(w[:5]), part.lower()) for w in words):
                return True
    return False


def _window_question(analysis, ins: dict, answers: dict | None = None):
    """Lines dated outside the dates a reference table gives their key: what
    applies to them, with the terms the partner's row states quoted. Partners
    outside on different sides are asked one at a time. The answer is the
    owner's note; it changes no number."""
    from .analyze import _window_words
    from .interview import Q
    n = ins["numbers"]
    t, r = analysis.table(n["table"]), analysis.table(n["ref"])
    k, col, per = n["rows"], n["col"], n["per"]
    qid = f"find_window_{_slug(t.sheet, 16)}_{_slug(col)}"
    # partners outside their dates on different sides (lines before one's start, after another's end) need
    # different answers: one question per partner, the one with the most lines first, the others waiting
    sides = {("before" if p["before"] else "") + ("after" if p["after"] else "") + ("gap" if p["gap"] else "")
             for p in per}
    one = None
    if len(per) > 1 and len(sides) > 1:
        done = set(answers or {})
        one = next((p for p in per if f"{qid}_{_slug(p['key'], 20)}" not in done), None)
        if one is None:
            return None
        per, k = [one], one["before"] + one["after"] + one["gap"]
        qid = f"{qid}_{_slug(one['key'], 20)}"
    listed = "; ".join(_window_words(p) for p in per[:2]) + (f"; and {len(per) - 2:,} more" if len(per) > 2 else "")
    where = f"the {r.sheet} dates for their {col}"
    quote = _terms_quote(analysis, per[0]["key"])
    tail = f" {quote}" if quote else ""
    q = Q(qid, "Out of dates",
             f"{k:,} lines on {t.sheet} are dated outside the {n['start']} and {n['end']} dates {r.sheet} gives their "
             f"{col} ({listed}).{tail} What applies to these {k:,} lines?",
             [{"id": "carry", "label": "The same terms carry on", "desc": f"The terms on {r.sheet} still apply to them"},
              {"id": "other", "label": "Terms not in the file", "desc": "An earlier or later agreement; type which"},
              {"id": "none", "label": "No terms apply", "desc": "Nothing applies to those lines"}],
             why=f"Every check of those lines against {r.sheet} is wrong until someone says which terms they are on.",
             kind="coverage", priority=1, source="finding",
             fact={"kind": "coverage", "class": "data", "depends": [],
                   "statement": f"The {k:,} lines on {t.sheet} dated outside {where}: {{answer_labels}}, per the owner.",
                   "statements": {
                       "carry": f"The {k:,} lines on {t.sheet} dated outside {where}: the same terms carry on, per the "
                                f"owner.{tail}",
                       "none": f"The {k:,} lines on {t.sheet} dated outside {where}: no terms apply, per the owner."
                               f"{tail}"}},
             meta={"finding": ins, "about": _about(t.tid, col, "scope", [p["key"] for p in per[:3]]),
                   "tables": [t.tid, r.tid],
                   "clause": f"{k:,} lines on {t.sheet} dated outside the {r.sheet} dates for their {col}"})
    if one is not None:
        mine = {id(t.rows[i]) for i in one.get("row_ids") or [] if i < len(t.rows)}
        q.meta["stake_rows"] = row_stake(analysis, t, lambda x: id(x) in mine)
    return q


def _variants_question(analysis, ins: dict):
    """Names two files write another way, differing only in capitals or
    punctuation ('Acme Supply Co.' and 'Acme Supply Co'): one question
    listing the pairs. The files already match them once capitals and a closing
    period are set aside; the answer says whether they are one thing. None when
    the pairs differ only in spaces."""
    import os

    from .interview import Q
    n = ins["numbers"]
    ft, tt = analysis.table(n["table"]), analysis.table(n["to_table"])
    pairs = [(a, b) for a, b in n["pairs"] if " ".join(str(a).split()) != " ".join(str(b).split())]
    if not pairs:
        return None
    ff, tf = (os.path.basename(analysis.file_of[x.tid]) for x in (ft, tt))
    shown = "; ".join(f"'{_short(a, 30)}' and '{_short(b, 30)}'" for a, b in pairs[:3]) \
        + (f"; and {len(pairs) - 3:,} more" if len(pairs) > 3 else "")
    k = len(pairs)
    return Q("alias_" + _slug(f"{n['col']}_{n['to_col']}_{ft.sheet}_{tt.sheet}", 40), "Same thing?",
             f"{n['col']} on {ft.sheet} ({ff}) and {n['to_col']} on {tt.sheet} ({tf}) write {k:,} "
             f"value{'s' if k != 1 else ''} two ways, only in capitals or punctuation: {shown} ({n['rows']:,} rows). "
             f"Is each the same thing written two ways?",
             [{"id": "same", "label": "Same thing, written two ways", "desc": "Count them as one"},
              {"id": "different", "label": "Different things", "desc": "Keep them apart"}],
             why="Two spellings of one name split every count and total by it, and a lookup between the files "
                 "can miss.",
             kind="mapping", priority=1, source="finding",
             fact={"kind": "mapping", "class": "data", "depends": [],
                   "statement": f"{n['col']} on {ft.sheet} and {n['to_col']} on {tt.sheet}, written two ways "
                                f"({shown}): {{answer_labels}}, per the owner.",
                   "statements": {"same": f"{shown}: the same thing written two ways, per the owner; count them as "
                                          "one.",
                                  "different": f"{shown}: different things, per the owner."}},
             meta={"finding": ins, "about": _about(ft.tid, n["col"], "meaning", [x for p in pairs[:3] for x in p]),
                   "tables": [ft.tid, tt.tid],
                   "clause": f"{k:,} {n['col']} value{'s' if k != 1 else ''} written two ways on {ft.sheet} and "
                             f"{tt.sheet}"})


def _versions_question(analysis, ins: dict):
    """A reference key on two or more dated rows (a contract renewed mid-period):
    which row applies to which lines, with the lines each row's dates cover. The
    dates never overlap, so 'by date' is recommended; the answer is the owner's
    note and changes no number."""
    from .interview import Q
    n = ins["numbers"]
    r, t = analysis.table(n["table"]), analysis.table(n["lines_table"])
    x = n["per"][0]
    rows = x["rows"][:3]
    listed = " and ".join(f"{g['range']} ({g['lines']:,} line{'s' if g['lines'] != 1 else ''} on {t.sheet})"
                          for g in rows)
    out = f"; {x['outside']:,} of its lines fall in neither" if x["outside"] else ""
    more = len(n["per"]) - 1
    also = f" {more:,} other {values_of(n['col'], more)} {'have' if more != 1 else 'has'} dated rows too." \
        if more else ""
    who = x["key"]
    quote = _terms_quote(analysis, who)
    also += f" {quote}" if quote else ""
    return Q(f"find_versions_{_slug(r.sheet, 16)}_{_slug(n['col'])}", "Dated rows",
             f"On {r.sheet}, {who} has {len(x['rows']):,} dated rows: {listed}{out}.{also} Does each line take the row "
             "whose dates cover it?",
             [{"id": "by_date", "label": "The row whose dates cover it",
               "desc": f"Each line on {t.sheet} takes the {r.sheet} row for its date"},
              {"id": "latest", "label": "The latest row for every line", "desc": "The newer terms apply to all of them"},
              {"id": "type", "label": "Another way", "desc": "Type which row applies to which lines"}],
             recommend="by_date", recommend_basis=f"The dates of {who}'s rows never overlap.",
             why=f"Every check of {who}'s lines against {r.sheet} uses the wrong terms on some lines until someone "
                 "says which row applies.",
             kind="coverage", priority=1, source="finding",
             fact={"kind": "coverage", "class": "data", "depends": [],
                   "statement": f"{who} on {r.sheet} has dated rows ({listed}); which row applies to which lines on "
                                f"{t.sheet}, per the owner: {{answer_labels}}.",
                   "statements": {
                       "by_date": f"Each line of {who} on {t.sheet} takes the {r.sheet} row whose dates cover it, per "
                                  f"the owner: {listed}." + (f" {quote}" if quote else ""),
                       "latest": f"The latest {r.sheet} row of {who} applies to every line on {t.sheet}, per the "
                                 "owner." + (f" {quote}" if quote else "")}},
             meta={"finding": ins, "about": _about(r.tid, n["col"], "scope", [who]), "tables": [t.tid, r.tid],
                   "clause": f"{who} has {len(x['rows']):,} dated rows on {r.sheet}"})


def _listscope_question(analysis, ins: dict):
    """A group that charges the reference price exactly only from one month on:
    is the reference today's prices only, or the prices for the whole period?
    Asked once per reference, about the group with the most lines before its run."""
    from .interview import Q
    n = ins["numbers"]
    runs = [x for x in n["runs"] if x["before"] - x["before_at"] >= LIST_BEFORE]
    if not runs:
        return None
    t, r = analysis.table(n["table"]), analysis.table(n["ref"])
    x = max(runs, key=lambda x: x["before"])
    tp, rp = n["col"], n["ref_col"]
    who = f"{x['value']} lines" if x["value"] else "the lines"
    when = _month(x["from"] + "-01")
    off = x["before"] - x["before_at"]
    rid = _rid(analysis, r.tid, rp)
    quote = _terms_quote(analysis, x["value"]) if x["value"] else ""
    tail = f" {quote}" if quote else ""
    # the date the list stands at, when its title names one
    from .analyze import _day_words
    title_p = (getattr(analysis, "title_period", None) or {}).get(r.tid) or {}
    asof = (f" (as of {_day_words(title_p['end'])})" if title_p.get("kind") == "as_of" and title_p.get("end")
            else "")
    today = f"Today's prices only{asof}"
    opts = [{"id": "current", "label": today if len(today) <= 60 else "Today's prices only",
             "desc": "Older lines were at older prices"},
            {"id": "whole", "label": "Prices for the whole period", "desc": "Lines that are not at it were charged "
                                                                            "off the list"}]
    statements = {
        "current": f"{rp} on {r.sheet} is today's prices only{asof}, per the owner: older lines were at older "
                   f"prices.{tail}",
        "whole": f"{rp} on {r.sheet} is the prices for the whole period, per the owner: lines that are not at it "
                 f"were charged off the list.{tail}"}
    rec = basis = None
    if x["value"]:
        # the partner reprices at the month its run starts: offered, and recommended when its terms say so
        mon = _MONTH_NAMES[int(x["from"][5:7]) - 1]
        opts.append({"id": "reprices", "label": f"{_short(x['value'], 30)} reprices each {mon}",
                     "desc": "Older lines were at older prices"})
        statements["reprices"] = (f"{x['value']} reprices each {mon}, per the owner: older lines on {t.sheet} were "
                                  f"at older prices.{tail}")
        said = " ".join(((_terms_of(analysis, x["value"]) or {}).get("texts") or {}).values())
        if re.search(r"\b(" + mon + r"|" + mon[:3] + r")\b", said, re.I):
            rec, basis = "reprices", f"{x['value']}'s terms name {mon}."
            # a recommended option's description says its label again and no more
            opts[-1] = dict(opts[-1], desc=f"New prices each {mon}")
            statements["reprices"] = f"{x['value']} reprices each {mon}, per the owner: new prices each {mon}.{tail}"
    q = Q(f"find_listscope_{_slug(r.sheet, 16)}_{_slug(rp)}", "List dates",
          f"{tp} on {t.sheet} matches {rp} on {r.sheet} exactly for {who} only from {when} on; {off:,} of the "
          f"{x['before']:,} lines before then are not at it.{tail} Is {r.sheet} today's prices only, or the prices "
          "for the whole period?",
          opts,
          why=f"Whether older lines are checked against {r.sheet} depends on the dates its prices hold for.",
          kind="coverage", priority=1, source="finding",
          fact={"kind": "coverage", "class": "data", "depends": [],
                "statement": f"{rp} on {r.sheet}, per the owner: {{answer_labels}}.",
                "statements": statements},
          meta={"finding": ins, "about": _about(r.tid, rp, "scope", [x["value"]] if x["value"] else None),
                "tables": [t.tid, r.tid],
                "touches": [f"has_role:{rid}"] if rid else [],
                "clause": f"{tp} on {t.sheet} matches {rp} on {r.sheet} for {who} only from {when}"})
    if rec:
        q.recommend, q.recommend_basis = rec, basis
    return q


_MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
                "November", "December"]


def _offlist_question(analysis, ins: dict):
    """A few lines off the reference price inside a run where every other line is
    at it, the item and both prices named: known with a claim filed, known and not
    claimed yet, or news to the owner."""
    from .interview import Q
    n = ins["numbers"]
    t, r = analysis.table(n["table"]), analysis.table(n["ref"])
    k, tp, rp, side = n["rows"], n["col"], n["ref_col"], n["side"]
    who = f"{n['value']} lines" if n["value"] else "lines"
    when = _join([_month(m + "-01") for m in n["months"]])
    quote = _terms_quote(analysis, n["value"]) if n["value"] else ""
    it = n.get("item") or {}
    what = (f" ({it['item']}: charged {it['charged']} where the list says {it['list']})" if it.get("list") and
            it.get("charged") else f" ({it['item']})" if it.get("item") else "")
    base = f"The {k:,} {who} on {t.sheet} {side} the {rp} on {r.sheet} in {when}{what}"
    return Q(f"find_offlist_{_slug(t.sheet, 16)}_{_slug(n['value'] or tp)}", "Off the list",
             f"{k:,} {who} on {t.sheet} in {when} are {side} the {rp} on {r.sheet}{what}, where the other {n['at']:,} "
             f"lines since {_month(n['from'] + '-01')} are at it." + (f" {quote}" if quote else "")
             + " A known issue, or news to you?",
             [{"id": "claimed", "label": "Known, claim filed", "desc": "Type when, if you like"},
              {"id": "known", "label": "Known, not claimed yet", "desc": "Worth claiming"},
              {"id": "news", "label": "News to me", "desc": "Worth looking into"}],
             why="A price off the list inside a run at the list is either an error to recover or a deal nobody wrote "
                 "down.",
             kind="history", priority=1, source="finding",
             fact={"kind": "history", "class": "data", "depends": [],
                   "statement": f"{base}: {{answer_labels}}, per the owner.",
                   "statements": {
                       "claimed": f"{base} are known, per the owner: a claim is filed." + (f" {quote}" if quote else ""),
                       "known": f"{base} are known, per the owner: not claimed yet." + (f" {quote}" if quote else ""),
                       "news": f"{base} are news to the owner." + (f" {quote}" if quote else "")}},
             meta={"finding": ins, "about": _about(t.tid, tp, "history", [n["value"]] if n["value"] else None),
                   "tables": [t.tid, r.tid],
                   "clause": f"{k:,} {who} on {t.sheet} in {when} {side} the {rp} on {r.sheet}{what}"})


def _onset_question(analysis, ins: dict):
    """A code that starts for one group after months without it: known, news to
    the owner, or a mistake. Never a rule on its own."""
    from .analyze import _mon_words
    from .interview import Q
    from .rules import Rule
    n = ins["numbers"]
    t = analysis.table(n["table"])
    f = n["items"][0]
    code, col, group, when = f["code"], f["col"], f["group"], _mon_words(f["month"])
    more = len(n["items"]) - 1
    qid = f"find_onset_{_slug(t.sheet, 16)}_{_slug(col)}"
    # what the new rows are (the item on most of them, what one costs) and what the group's terms say of it
    it = n.get("item") or {}
    what = (f"; mostly {it['item']} at {it['charged']} of {it.get('money_col')} each" if it.get("item") and
            it.get("charged") else f"; mostly {it['item']}" if it.get("item") else "")
    quote = _terms_quote(analysis, group)
    clash = _terms_waive(analysis, group, [code, it.get("item") or ""])
    said = (f" {quote}" if quote else "") + (" The terms waive it." if clash else "")
    # 'Should not be there' leaves those rows out only once the owner ticks the rule with its counts
    gone = Rule("exclude", t.tid, [{"col": n["group_col"], "op": "in", "values": [group]},
                                   {"col": col, "op": "in", "values": [code]}], source=qid)
    return Q(qid, "Starts late",
             f"{code} in {col} starts for {group} on {t.sheet} in {when} ({f['rows']:,} rows since{what}), after "
             f"{f['before']} months of {group} rows without it" + (f", and {more:,} more like it" if more else "")
             + f".{said} Known, or news to you?",
             [{"id": "known", "label": "A known change", "desc": "Type what changed, if you like"},
              {"id": "news", "label": "News to me", "desc": "Worth looking into"},
              {"id": "mistake", "label": "Should not be there", "desc": "Those rows are a mistake"}],
             why=f"A {col} that starts for one {n['group_col']} is often a new charge or term nobody wrote down.",
             kind="history", priority=1, source="finding",
             fact={"kind": "history", "class": "data", "depends": [],
                   "statement": f"{code} in {col} starting for {group} in {when}: {{answer_labels}}, per the owner.",
                   "statements": {
                       "news": f"{code} in {col} starting for {group} in {when}{what} is news to the owner.{said}",
                       "mistake": f"The {f['rows']:,} {code} rows for {group} since {when}{what} should not be there, "
                                  f"per the owner: those rows are a mistake.{said}"}},
             meta={"finding": ins, "about": _about(t.tid, col, "history", [code]),
                   "propose": [{"option": "mistake", "rule": gone.to_dict()}],
                   "clause": f"{code} in {col} starts for {group} on {t.sheet} in {when}{what}"
                             + ("; the terms waive it" if clash else "")})


# --------------------------------------------------------------------------
# formula models: a typed block that feeds the model, a row that does not tie to its link
# --------------------------------------------------------------------------
def _two_formulas(cell: str, formula: str, expected: str, at) -> str:
    """A break no words describe, as its formula beside the row's usual one moved
    to its cell: 'is =C4*1.1+D2 where the row has =C4*$B$2'."""
    from . import formulas
    if at:
        try:
            r, c, _, _ = formulas._cell_rc(cell.rpartition("!")[2])
            expected = formulas.moved(expected, r - at[0], c - at[1])
        except (ValueError, TypeError):
            pass
    return f"is {_short(formula, 50)} where the row has {_short(expected, 50)}"


def _plan_block_question(analysis, ins: dict):
    """Typed rows covering the forecast that feed much of the model: an approved
    plan, an estimate or a placeholder, and when and by whom it was approved."""
    from .interview import Q
    n = ins["numbers"]
    rows, many = _join(n["rows"]), len(n["rows"]) != 1
    # named by the section label above the block, so a label another block repeats is never mistaken for it
    sec = _section_above(analysis, n["sheet"], (n.get("row_index") or [0])[0])
    under = f" under {sec}" if sec and sec not in n["rows"] else ""
    what = f"The {rows} row{'s' if many else ''}{under} on {n['sheet']}"
    span = f"{n['periods']} periods ({_month(n['first'])} to {_month(n['last'])})"
    # the inputs each row is multiplied by (a head count times a cost per head), with their notes: one answer
    # says what the block is and confirms the rates it is priced at
    pairs = _block_inputs(analysis, n)
    times = ""
    if pairs:
        from .analyze import _value_words
        times = (" Each row is multiplied by an input: " if many else " It is multiplied by an input: ") + "; ".join(
            f"{x['label']} ({x['sheet']}!{x['cell']}) = {_value_words(x['value'])}"
            + (f" (\"{_short(x['note'], 60)}\")" if x.get("note") else "") for x in pairs) + "."
    return Q(f"find_plan_block_{_slug(n['sheet'] + '_' + n['rows'][0])}", "Typed block",
             f"{what} {'are' if many else 'is'} typed, not calculated, in {span} and feed{'' if many else 's'} "
             f"{n['feeds']:,} formula cells.{times} What {'are they' if many else 'is it'}? If approved, type when "
             "and by whom.",
             [{"id": "approved", "label": "An approved plan", "desc": "Type when and by whom"},
              {"id": "estimate", "label": "An estimate", "desc": "A working guess, not approved"},
              {"id": "placeholder", "label": "A placeholder", "desc": "Filler until real numbers exist"}],
             why="Every forecast number downstream rests on these typed rows, and nothing in the file says who set them.",
             kind="definition", priority=1, source="finding",
             fact={"kind": "definition", "class": "data", "depends": [],
                   "statement": f"{what} (typed in {span}) {'are' if many else 'is'}, per the owner: {{answer_labels}}.",
                   "statements": {
                       "approved": f"{what}, typed in {span}, {'are' if many else 'is'} an approved plan, per the owner."
                                   + times,
                       "estimate": f"{what}, typed in {span}, {'are' if many else 'is'} an estimate, per the owner."
                                   + times,
                       "placeholder": f"{what}, typed in {span}, {'are' if many else 'is'} a placeholder, per the "
                                      "owner." + times}},
             meta={"finding": ins,
                   # the inputs it names are this question's too: a readback of inputs never asks them again
                   "about": _about_dep(analysis, ins, "meaning",
                                       [f"{x['label']} ({x['sheet']}!{x['cell']})" for x in pairs] or None),
                   "clause": f"{what} {'are' if many else 'is'} typed in {n['periods']} periods and feed"
                             f"{'' if many else 's'} {n['feeds']:,} formula cells"})


def _block_inputs(analysis, n: dict) -> list:
    """The inputs a typed block's rows are multiplied by: each formula row that reads
    a row of the block and one input (a head count times a cost per head), the
    input with its label, value and note, at most 3, in the block's row order."""
    from . import formulas as fm
    rows = set(n.get("row_index") or [])
    out, seen = [], set()
    for fa in (analysis.formulas or {}).values():
        ins = {f"{x['sheet']}!{x['cell']}": x for x in (fa or {}).get("inputs") or []}
        for d in (fa or {}).get("drivers") or []:
            toks = fm.tokens(d.get("formula") or "")
            refs = [fm.ref_of(tok, d["sheet"]) for tok in toks]
            # a block row times an input, side by side in the formula ('C30*Levers!$B$12')
            for i in range(len(toks) - 2):
                if toks[i + 1] != ("op", "*"):
                    continue
                for a_, b_ in ((refs[i], refs[i + 2]), (refs[i + 2], refs[i])):
                    if not a_ or not b_ or a_[0] != n.get("sheet") or a_[1] not in rows:
                        continue
                    cell = f"{b_[0]}!{_col_letters(b_[2])}{b_[1] + 1}"
                    x = ins.get(cell)
                    if x is not None and cell not in seen:
                        seen.add(cell)
                        out.append(x)
    return out[:3]


def _col_letters(c: int) -> str:
    s = ""
    c += 1
    while c:
        c, r = divmod(c - 1, 26)
        s = chr(65 + r) + s
    return s


def _section_above(analysis, sheet: str, ri: int) -> str:
    """The label of the section a block of rows sits in: the nearest row above it
    (up to 3 rows, then the block's own header row) holding only text, first cell
    filled; '' when there is none."""
    s = next((s for b in analysis.books for s in b.data_sheets() if s.name == sheet), None)
    if s is None:
        return ""
    for r in range(ri - 1, max(-1, ri - 4), -1):
        row = s.values[r] if r < len(s.values) else []
        filled = [(c, v) for c, v in enumerate(row) if v is not None and str(v).strip()]
        if not filled:
            continue
        if filled[0][0] <= 1 and all(isinstance(v, str) for _c, v in filled) and not any(_is_num(v) for _c, v in filled):
            return str(filled[0][1]).strip()[:40]
        break
    return ""


def _tieout_question(analysis, ins: dict):
    """A row typed in its first periods that links to another row later, whose
    typed periods do not match that row: what the gap is."""
    from .interview import Q
    n = ins["numbers"]
    link = n["link_label"]
    return Q(f"find_tieout_{_slug(n['sheet'] + '_' + n['row_label'])}", "Tie-out",
             _short(ins["statement"], 400) + " What is the gap?",
             [{"id": "other", "label": f"Money {link} leaves out", "desc": "In the typed periods only"},
              {"id": "timing", "label": "Timing", "desc": "In another period"},
              {"id": "mistake", "label": "A mistake", "desc": "The typed periods are wrong"}],
             why=f"Readers compare {n['row_label']} with {link} and will call the gap an error unless someone says "
                 "what it is.",
             kind="definition", priority=1, source="finding",
             fact={"kind": "definition", "class": "data", "depends": [],
                   "statement": f"The gap between the typed {n['row_label']} on {n['sheet']} and {link} "
                                f"({n['gap']:,.2f} over {n['typed']} periods) is, per the owner: {{answer_labels}}."},
             meta={"finding": ins, "about": _about_dep(analysis, ins, "meaning"),
                   "clause": f"typed {n['row_label']} on {n['sheet']} is {abs(n['gap']):,.2f} "
                             f"{'above' if n['gap'] > 0 else 'below'} {link}"})


# --------------------------------------------------------------------------
# group arithmetic: a companion line at a fixed ratio, lines on the unusual side, an opening entry
# --------------------------------------------------------------------------
def _ratio_question(analysis, ins: dict):
    """A line category that is a fixed share of the rest of its group: money
    collected for someone else, a fee, or part of the sale. Collected money is
    left out of totals on that pick."""
    from .interview import Q
    n = ins["numbers"]
    b, col, of = n["value"], n["col"], n["of"]
    t = analysis.table(n["table"])
    # the pick leaves out every line of that value: when it has more lines than the ratio counted, both counts
    k, rows = int(n.get("hits") or 0), int(n.get("rows") or 0)
    out = (f"All {rows:,} {b} lines are left out of every count and total: {k:,} at {n['pct']} and {rows - k:,} "
           "other lines") if rows > k else "Left out of every count and total"
    return Q(f"find_ratio_{_slug(col + '_' + b)}", "Fixed share",
             f"In {n['hits']:,} {n['noun']} on {t.sheet}, {_a(b)} {b} line in {col} is exactly {n['pct']} of {of}. "
             "What is it?",
             [{"id": "collected", "label": "Collected for someone else", "desc": out},
              {"id": "fee", "label": "A fee or commission", "desc": "Charged at that share"},
              {"id": "part", "label": "Part of the sale", "desc": "Counts like the other lines"}],
             why=f"If the {b} lines are money held for someone else, every total that includes them is too high.",
             kind="definition", priority=1, source="finding",
             fact={"kind": "definition", "class": "data", "depends": [],
                   "statement": f"The {b} lines in {col}, exactly {n['pct']} of {of}, are per the owner: "
                                "{answer_labels}.",
                   "statements": {
                       "collected": f"The {b} lines in {col} ({n['pct']} of {of}) are collected for someone else, "
                                    "per the owner: " + (out[:1].lower() + out[1:]) + ".",
                       "fee": f"The {b} lines in {col} ({n['pct']} of {of}) are a fee or commission, per the owner.",
                       "part": f"The {b} lines in {col} ({n['pct']} of {of}) are part of the sale, per the owner."}},
             meta={"finding": ins, "exclude": {"table": n["table"], "col": col, "values": [b],
                                               "options": ["collected"]},
                   "about": _about(n["table"], col, "meaning", [b])})


def _contra_question(analysis, ins: dict):
    """Lines on the side opposite their category's usual side: what they are, pick
    all that apply. When a list gives each account a category, one option per
    group of what they give back (credits on cost accounts, debits on income
    accounts), each naming its accounts and the words after the memo's prefix;
    otherwise refunds, corrections or mistakes."""
    from .interview import Q
    n = ins["numbers"]
    groups = [g for g in n.get("groups") or [] if g.get("rows")][:2]
    pre = n.get("prefix") or ""
    pcol = n.get("prefix_col") or ""
    opts, statements = [], {}
    for g in groups:
        accts = _listed(g["accounts"])
        memo = f"{pre}: {g['memo']}" if pre and g.get("memo") else pre
        said = f"{g['rows']:,} line{'s' if g['rows'] != 1 else ''} on {accts}" + (f", {pcol} \"{memo}\"" if memo else "")
        what = f"{pre}s" if pre else "Money back"
        if g["kind"] == "cost":
            label = f"{what} credited back to the cost account"
            stmt = (f"The {g['rows']:,} line{'s' if g['rows'] != 1 else ''} credited to {accts}"
                    + (f" ({pcol} \"{memo}\")" if memo else "")
                    + f" are {what.lower()} credited back to the cost account, per the owner.")
        else:
            label = f"{what} to customers, debited to income"
            stmt = (f"The {g['rows']:,} line{'s' if g['rows'] != 1 else ''} debited to {accts}"
                    + (f" ({pcol} \"{memo}\")" if memo else "")
                    + f" are {what.lower()} to customers, debited to income, per the owner.")
        opts.append({"id": g["kind"], "label": label[:60], "desc": _fit_desc(said)})
        statements[g["kind"]] = stmt
    if not opts:
        opts = [{"id": "credits", "label": "Refunds", "desc": "Money given back"},
                {"id": "corrections", "label": "Corrections", "desc": "Undoing an earlier line"}]
    elif len(opts) == 1:
        opts.append({"id": "corrections", "label": "Corrections", "desc": "Undoing an earlier line"})
    opts.append({"id": "mistakes", "label": "Mistakes to fix", "desc": "On the wrong side"})
    return Q(f"find_contra_{_slug(n['col'] + '_' + n['side_col'])}", "Other side",
             ins["statement"] + " What are they? Pick all that apply.",
             opts[:3],
             why="Lines on the unusual side either come off the totals or are errors; nobody can tell which from the "
                 "numbers.",
             multi=True, kind="definition", priority=1, source="finding",
             fact={"kind": "definition", "class": "data", "depends": [],
                   "statement": f"The {n['rows']:,} lines on the side opposite their {n['col']}'s usual side"
                                + (f", marked \"{n['prefix']}\"," if n.get("prefix") else "")
                                + " are per the owner: {answer_labels}.",
                   **({"statements": statements} if statements else {})},
             meta={"finding": ins, "about": _about(n["table"], n["col"], "meaning", n["values"][:6])})


def _opening_question(analysis, ins: dict):
    """The first group carries balances in: the pick scopes it to balances. It is
    left out of the totals of the period's activity (the table's flow columns)
    and kept in row counts and in any level column, where a balance carried in
    belongs."""
    from .interview import Q
    n = ins["numbers"]
    v, col = n["value"], n["col"]
    flows = n.get("flows") or []
    exclude = {"table": n["table"], "col": col, "values": [v], "options": ["opening"], "scope": flows} \
        if flows else None
    # the notes count what the prompt counted: an entry's lines, or the rows carried in by their code
    unit = "rows" if re.search(r"\brows?\b", ins["statement"]) and not re.search(r"\blines?\b", ins["statement"]) \
        else "lines"
    k = f"{n['lines']} {unit if n['lines'] != 1 else unit[:-1]}"
    return Q(f"find_opening_{_slug(col + '_' + v)}", "Opening",
             ins["statement"] + f" Is {v} the opening balances carried in, or activity in the period?",
             [{"id": "opening", "label": "Opening balances", "desc": "Carried in, not activity in the period"},
              {"id": "activity", "label": "Activity in the period", "desc": "It counts like any other"},
              {"id": "mistake", "label": "A mistake", "desc": "It should not be here"}],
             why="Balances carried in make every total of the period's activity too high.",
             kind="definition", priority=1, source="finding",
             fact={"kind": "definition", "class": "data", "depends": [],
                   "statement": f"{col} {v} ({k}) is, per the owner: {{answer_labels}}.",
                   "statements": {
                       "opening": f"{col} {v} ({k}) is the opening balances carried in, not activity in the period, "
                                  "per the owner.",
                       "activity": f"{col} {v} ({k}) is activity in the period, per the owner.",
                       "mistake": f"{col} {v} ({k}) is a mistake, per the owner."}},
             meta={"finding": ins, "exclude": exclude, "about": _about(n["table"], col, "scope", [v])})


def _closing_question(analysis, ins: dict):
    """The last group closes the period out: the pick scopes it to balances. It is
    left out of the totals of the period's activity (the table's flow columns)
    and kept in row counts and in any level column."""
    from .interview import Q
    n = ins["numbers"]
    v, col = n["value"], n["col"]
    flows = n.get("flows") or []
    exclude = {"table": n["table"], "col": col, "values": [v], "options": ["closing"], "scope": flows} \
        if flows else None
    return Q(f"find_closing_{_slug(col + '_' + v)}", "Closing",
             ins["statement"] + f" Is {v} the closing entry that carries the balances out, or activity in the period?",
             [{"id": "closing", "label": "Closing entry", "desc": "Carries the balances out, not activity in the period"},
              {"id": "activity", "label": "Activity in the period", "desc": "It counts like any other"},
              {"id": "mistake", "label": "A mistake", "desc": "It should not be here"}],
             why="An entry that closes the period out doubles every total of the period's activity it touches.",
             kind="definition", priority=1, source="finding",
             fact={"kind": "definition", "class": "data", "depends": [],
                   "statement": f"{col} {v} ({n['lines']} lines) is, per the owner: {{answer_labels}}.",
                   "statements": {
                       "closing": f"{col} {v} ({n['lines']} lines) is the closing entry that carries the balances "
                                  "out, not activity in the period, per the owner.",
                       "activity": f"{col} {v} ({n['lines']} lines) is activity in the period, per the owner.",
                       "mistake": f"{col} {v} ({n['lines']} lines) is a mistake, per the owner."}},
             meta={"finding": ins, "exclude": exclude, "about": _about(n["table"], col, "scope", [v])})


# --------------------------------------------------------------------------
# column dossiers: what a column's codes mean, and which count toward the money
# --------------------------------------------------------------------------
DOSSIER_ROWS = 30         # tables this small are read whole by anyone; no dossier
MINORITY = 0.10           # a value on at most this share of rows, and at most half the commonest's
CONCENTRATED = 0.10       # a value whose rows sit within this share of the table's dates says when
SHOWN = 8                 # values a prompt lists before 'and N more'
BESIDE = 5                # ... and when another finding's evidence is folded in beside them
HANDOFF_SHOWN = 6         # old -> new pairs a handoff question lists; a rule covers only the pairs shown
DOSSIER_PAIRS = 3         # ... and a dossier, where they sit beside the values
INSIDE_SHARE = 0.95       # a column's values keep to one code each on this share of rows before an exception says so
INSIDE_ROWS = 3           # rows of one value on another code before they are named
INSIDE_MAX = 0.2          # ... and at most this share of that value's rows (more is two ways, not an exception)
_STATUS = re.compile(r"\b(status|state|stage|flag)\b", re.I)
# 'Ship State', 'Home State': a place, not a status
_PLACE = re.compile(r"\b(ship|shipping|bill|billing|home|mail|mailing|delivery|residence|birth)\b", re.I)
_NOTES = re.compile(r"\b(notes?|memo|comments?|remarks?|descriptions?|details?)\b", re.I)


def _status_header(header) -> bool:
    h = str(header or "")
    return bool(_STATUS.search(h)) and not _PLACE.search(h)


def _cryptic(v) -> bool:
    """A value written as a code, not a word: 2 characters or fewer, or 6 or fewer
    with no lowercase letter ('X', 'QT', 'ZKW', '4A', 'M7', never 'North')."""
    s = str(v).strip()
    return bool(s) and (len(s) <= 2 or (len(s) <= 6 and not re.search(r"[a-z]", s)))


def _int_codes(c) -> bool:
    """Whole numbers that name things: typed as codes, or spread thin over their
    range under a header that names no amount or count (a quantity 1 to 6 fills
    its range; codes 4, 15, 23 and 28 do not)."""
    from .profile import _MONEY_COUNT, _plain_header
    if c.type != "number" or not c.integers or c.min is None or float(c.min) < 0 or c.semantic == "temporal":
        return False
    if c.codes:
        return True
    span = float(c.max) - float(c.min) + 1
    return span > 0 and c.distinct / span < 0.5 and not _MONEY_COUNT.search(_plain_header(c.header))


def _numbered(c) -> bool:
    """Values that number individual things (C101, K207, each a few letters and a
    number): an ID column, not codes to explain, once there are 10 of them or the
    header says ID."""
    from .profile import is_id_header
    return c.type == "text" and all(re.fullmatch(r"[A-Za-z]{0,4}[-_ ]?\d+", str(k).strip()) for k in c.counter) \
        and (c.distinct >= 10 or is_id_header(c.header))


def _width(c) -> float:
    return c.avg_len if c.type == "text" else max((len(str(k)) for k in c.counter), default=0)


def _one_to_one(t, c, cols) -> bool:
    """A longer name column that says the same thing on every row (code 3 is
    always 'Returned'): the names already explain the codes."""
    for n in cols:
        if n is c or n.type != "text" or n.distinct != c.distinct or _width(n) <= _width(c):
            continue
        pairs: dict = {}
        if all(pairs.setdefault(a, b) == b for a, b in
               ((norm_key(r[c.j] if c.j < len(r) else None), norm_key(r[n.j] if n.j < len(r) else None))
                for r in t.rows) if a is not None and b is not None) \
                and len(set(pairs.values())) == len(pairs):
            return True
    return False


def _named_by_lookup(analysis, t, c) -> bool:
    """A lookup table this column joins has a column that names each value."""
    for j in analysis.joins:
        if j["from_table"] != t.tid or j["from_col"] != c.header or j["band"] != "auto":
            continue
        if any(x.type == "text" and x.header != j["to_col"] for x in analysis.cols.get(j["to_table"], [])):
            return True
    return False


def _span(a, b) -> str:
    """'Mar 4 to 6, 2023', 'Nov 28 to Dec 3, 2022', 'Dec 30, 2021 to Jan 2, 2022'."""
    ma, mb = _MONTHS[a.month - 1], _MONTHS[b.month - 1]
    if (a.year, a.month, a.day) == (b.year, b.month, b.day):
        return f"{ma} {a.day}, {a.year}"
    if (a.year, a.month) == (b.year, b.month):
        return f"{ma} {a.day} to {b.day}, {a.year}"
    if a.year == b.year:
        return f"{ma} {a.day} to {mb} {b.day}, {a.year}"
    return f"{ma} {a.day}, {a.year} to {mb} {b.day}, {b.year}"


def _value_stats(analysis, t, c, money) -> tuple:
    """({key: [as written, rows, money, money without sign, first date, last date]},
    (the table's first date, last date) or None)."""
    from .rules import _written
    date = next((x for x in analysis.cols[t.tid] if x.type == "date"), None)
    stats: dict = {}
    lo = hi = None
    for r in t.rows:
        v = r[c.j] if c.j < len(r) else None
        k = norm_key(v)
        d = r[date.j] if date is not None and date.j < len(r) else None
        d = d if hasattr(d, "year") else None
        if d is not None:
            lo, hi = min(lo or d, d), max(hi or d, d)
        if k is None:
            continue
        s = stats.setdefault(k, [_written(v) or str(v).strip(), 0, 0.0, 0.0, None, None])
        s[1] += 1
        m = r[money.j] if money is not None and money.j < len(r) else None
        if _is_num(m):
            s[2] += m
            s[3] += abs(m)
        if d is not None:
            s[4], s[5] = min(s[4] or d, d), max(s[5] or d, d)
    return stats, ((lo, hi) if lo is not None else None)


def _listing(stats: dict, keys: list, money, span, fmt=None, shown: int = SHOWN) -> str:
    """'Q: 6 rows, $1,234, all Mar 4 to 6, 2023; K: 40 rows, $9,870' (fmt writes
    the money: money_fmt). At most shown values, then 'and N more'."""
    from .recipes import fmt_money
    fmt = fmt or fmt_money
    wide = span is not None and (span[1] - span[0]).days >= 28
    parts = []
    for k in keys[:shown]:
        w, n, m, _abs, d1, d2 = stats[k]
        bit = f"{_short(w, 30)}: {n:,} row{'s' if n != 1 else ''}"
        if money is not None:
            bit += f", {fmt(m)}"
        if wide and n >= 2 and d1 is not None and (d2 - d1).days <= CONCENTRATED * (span[1] - span[0]).days:
            bit += f", all {_span(d1, d2)}"
        parts.append(bit)
    if len(keys) > shown:
        parts.append(f"and {len(keys) - shown:,} more")
    return "; ".join(parts)


def _status_lines_up(analysis, t, c, stats: dict, keys: list):
    """(value as written, the other column, rows) when one value of a status column
    sits exactly on the rows another column (an ID, a second amount) leaves blank,
    5 rows or more; else None. An amount is taken before an ID."""
    rows_of: dict = {}
    for i, r in enumerate(t.rows):
        k = norm_key(r[c.j] if c.j < len(r) else None)
        if k is not None:
            rows_of.setdefault(k, set()).add(i)
    others = sorted([x for x in analysis.cols[t.tid] if x is not c and not x.sensitive and x.nulls >= 5
                     and (x.semantic in ("identifier", "metric"))], key=lambda x: x.semantic != "metric")
    blanks = {x.header: {i for i, r in enumerate(t.rows) if (x.j >= len(r) or r[x.j] is None
                                                             or (isinstance(r[x.j], str) and not r[x.j].strip()))}
              for x in others}
    for x in others:
        for k in keys:
            mine = rows_of.get(k) or set()
            if len(mine) >= 5 and mine == blanks[x.header]:
                # every column blank on exactly those rows (a tenant and a rent), the money first
                also = [y.header for y in others if y is not x and blanks[y.header] == mine]
                return stats[k][0], x.header, len(mine), [x.header] + also
    return None


def _code_inside(t, c, cols, money, fmt, stats: dict) -> dict | None:
    """A few rows of one group on a code the rest of the group never uses: '15
    rows with Type Payment use 2200 (19,370.00) where the other 480 use 1100'.
    Read against a word column (a type, a class) whose values each keep to one
    code of this column on INSIDE_SHARE of all rows, with 2 or more different
    codes among them (so the codes follow it); the exception is 3 or more rows
    of one value and at most INSIDE_MAX of that value's rows. The exception with
    the most money is named. {text, col, value, code, usual, rows} or None."""
    from collections import Counter

    from .rules import _written
    best = None
    for g in cols:
        if g is c or g is money or g.type != "text" or g.sensitive or g.distinct_capped \
                or not (2 <= g.distinct <= 30) or g.count < 10 * g.distinct or _NOTES.search(str(g.header)):
            continue
        by: dict = {}
        shown: dict = {}
        for r in t.rows:
            gv = r[g.j] if g.j < len(r) else None
            gk, ck = norm_key(gv), norm_key(r[c.j] if c.j < len(r) else None)
            if gk is None or ck is None:
                continue
            by.setdefault(gk, Counter())[ck] += 1
            shown.setdefault(gk, _written(gv) or str(gv).strip())
        top = {gk: cnt.most_common(1)[0] for gk, cnt in by.items()}
        n = sum(sum(cnt.values()) for cnt in by.values())
        kept = sum(k for _code, k in top.values())
        if not n or kept == n or kept < INSIDE_SHARE * n or len({code for code, _k in top.values()}) < 2:
            continue
        for gk, cnt in by.items():
            usual, k = top[gk]
            for code, m in cnt.items():
                if code == usual or m < INSIDE_ROWS or m > INSIDE_MAX * sum(cnt.values()):
                    continue
                cash = sum(abs(r[money.j]) for r in t.rows if money is not None and money.j < len(r)
                           and _is_num(r[money.j]) and norm_key(r[g.j] if g.j < len(r) else None) == gk
                           and norm_key(r[c.j] if c.j < len(r) else None) == code)
                if best is None or (cash, m) > best[0]:
                    best = ((cash, m), g, gk, code, m, usual, sum(cnt.values()) - m, shown[gk])
    if best is None:
        return None
    (cash, _m), g, _gk, code, m, usual, rest, gv = best
    x, y = stats[code][0] if code in stats else code, stats[usual][0] if usual in stats else usual
    money_words = f" ({fmt(cash)})" if money is not None else ""
    return {"text": f"{m:,} row{'s' if m != 1 else ''} with {g.header} {_short(gv, 30)} use{'s' if m == 1 else ''} "
                    f"{_short(x, 20)}{money_words} where the other {rest:,} use {_short(y, 20)}",
            "col": g.header, "value": gv, "code": x, "usual": y, "rows": m}


def _lookup_map(analysis, t, c) -> dict | None:
    """A column that holds a lookup's codes on some rows and its names on others:
    {lookup, code, name, to: {key: code as written}, codes: rows, names: rows,
    example: (name, code)}. Each form must be on at least 3 rows and 2% of rows."""
    from .rules import _written
    if c.type != "text" or c.sensitive or c.count < DOSSIER_ROWS:
        return None
    best = None
    have = set(c.counter)
    for lt in analysis.tables:
        if lt is t or lt.wide or analysis.is_derived(lt.tid) or lt.n_rows > 5000:
            continue
        # the lookup's own keys: unique columns that share values with this one
        lcols = [x for x in analysis.cols[lt.tid] if x.unique and x.type in ("text", "number")
                 and not x.distinct_capped and have & set(x.counter)]
        for kc in lcols:
            for nc in lcols:
                if kc is nc or nc.type != "text" or _width(nc) <= _width(kc):
                    continue
                to, example = {}, None
                for r in lt.rows:
                    kv, nv = (r[kc.j] if kc.j < len(r) else None), (r[nc.j] if nc.j < len(r) else None)
                    kk, nk = norm_key(kv), norm_key(nv)
                    if kk is None or nk is None:
                        continue
                    code = _written(kv) or str(kv).strip()
                    to[kk], to[nk] = code, code
                    if example is None and c.counter.get(nk):
                        example = (str(nv).strip(), code)
                codes = sum(n for k, n in c.counter.items() if k in to and to[k] and norm_key(to[k]) == k)
                names = sum(n for k, n in c.counter.items() if k in to and norm_key(to[k]) != k)
                floor = max(3, 0.02 * c.count)
                if codes + names < 0.95 * c.count or codes < floor or names < floor or example is None:
                    continue
                if best is None or codes + names > best["codes"] + best["names"]:
                    used = {k: v for k, v in to.items() if k in c.counter}
                    best = {"lookup": lt.tid, "code": kc.header, "name": nc.header, "to": used,
                            "codes": codes, "names": names, "example": example}
    return best


def code_dossiers(analysis, answers: dict) -> list:
    """One question per code column on every table of 30 or more rows that has
    money: every value with its rows, money and, when its rows bunch up, the
    dates; what the codes mean; and which do not count toward the money. A
    column that holds a lookup's codes on some rows and its names on others gets
    the lookup's own map to confirm instead. Codes from a well-known standard
    (states, countries, sizes) explain themselves and are never asked; a
    currency column asks which currency the money is in. The same column (same
    header, same values) on two tabs is asked once, on the tab with more money.
    Read from the rows as written, once per analysis; each call gets its own
    copies."""
    import copy
    # what a table's totals are of, and which money columns the owner said changed unit, decide the wording
    names = {t.tid: measure_name(analysis, t, answers) for t in analysis.tables}
    changed = unit_changed(analysis, answers)
    key = (tuple(sorted(names.items(), key=str)), tuple(sorted(changed.items())))
    cache = analysis.__dict__.setdefault("_dossiers_by", {})
    if key not in cache:
        cache[key] = _dossiers(analysis, names, changed)
    return [copy.deepcopy(q) for q in cache[key] if q.id not in answers]


def _dossiers(analysis, names: dict | None = None, changed: dict | None = None) -> list:
    from .analyze import _pair_why
    from .interview import Q, is_unit_col
    from .rules import Rule
    from .standards import standard_set
    found = {}
    # a two-code column whose codes only mark a row and its void twin: the twin-pairs question asks about it
    twin_codes = {(i["numbers"]["table"], i["numbers"]["code"]) for i in analysis.insights
                  if i.get("recipe", "").startswith("pairs:")}
    pair_of = analysis.detection.get("pairs") or {}
    for t in analysis.tables:
        if t.wide or analysis.is_derived(t.tid) or t.n_rows < DOSSIER_ROWS:
            continue
        money = money_column(analysis, t)
        if money is None:
            continue
        cols = analysis.cols[t.tid]
        total = sum(abs(r[money.j]) for r in t.rows if money.j < len(r) and _is_num(r[money.j]))
        # the measure a value counts toward: never a discount, nor a column the owner said changed unit
        measure = (names or {}).get(t.tid) if names is not None else measure_name(analysis, t, {})
        towards = f"{measure} totals" if measure else "the totals"
        mixed = (changed or {}).get((t.tid, money.header))
        # amounts in two units are never summed as one, and a column that is not the measure (a discount) is not
        # shown as what each value is worth
        shown_money = None if mixed or measure != money.header else money
        mixed_words = f" ({money.header} is not summed here: mixed units before and after {mixed})" if mixed else ""
        # a sheet (or two files' sheets of one name) holding several tables: each table's place in the id
        same = [x.tid for x in analysis.tables if x.sheet == t.sheet]
        tag = _slug(t.sheet, 16) + (f"_{same.index(t.tid) + 1}" if len(same) > 1 else "")
        for c in cols:
            qid = f"codes_{tag}_{_slug(c.header, 30)}"
            if c is money or c.distinct_capped:
                continue
            # the same column on two tabs is one question; two columns that share values are two
            key = (frozenset(c.counter), norm_key(c.header))
            rid = _rid(analysis, t.tid, c.header)
            mapped = _lookup_map(analysis, t, c)
            if mapped:
                q = _map_dossier(analysis, t, c, money, mapped, qid)
                found.setdefault(key, []).append((total, q))
                continue
            if c.type not in ("text", "number") or c.sensitive or not (2 <= c.distinct <= 30) \
                    or c.count < 10 * c.distinct or c.retyped_kind == "date" or c.avg_len > 40:
                continue
            if (c.type == "number" and not _int_codes(c)) or _numbered(c):
                continue
            if _NOTES.search(str(c.header)) or rid == "notes" or is_unit_col(c.header, rid):
                continue
            if (t.tid, c.header) in twin_codes and c.distinct == 2:
                continue
            std = standard_set(c.header, c.counter)
            if std and std != "currency":
                continue                  # FL, DEU, XL: a standard every reader knows
            stats, span = _value_stats(analysis, t, c, money)
            if (getattr(analysis, "title_period", None) or {}).get(t.tid, {}).get("kind") == "as_of" \
                    or t.tid in (getattr(analysis, "snapshots", None) or {}):
                span = None               # a snapshot's dates are things about its rows, not when they happened
            if std == "currency":         # which currency the money is in: its unit, never rows to leave out
                keys = sorted(stats, key=lambda k: (-stats[k][1], stats[k][0]))
                found.setdefault(key, []).append((total, _currency_question(t, c, money, stats, keys, total, qid)))
                continue
            status = _status_header(c.header)
            words = [s[0] for s in stats.values() if re.search(r"[a-z]", s[0])]
            cryptic = c.type == "number" or sum(_cryptic(s[0]) for s in stats.values()) >= 0.6 * len(stats)
            caps = bool(words) and any(re.fullmatch(r"[A-Z]{2,3}", s[0]) and s[1] >= 20 for s in stats.values())
            if not (cryptic or status or caps):
                continue
            if not status and (_one_to_one(t, c, cols) or _named_by_lookup(analysis, t, c)):
                continue
            keys = sorted(stats, key=lambda k: (-stats[k][1], stats[k][0]))
            top = stats[keys[0]][1]
            minority = [k for k in keys if stats[k][1] <= MINORITY * c.count and stats[k][1] <= 0.5 * top]
            share = sum(stats[k][3] for k in minority) / total if total else 0.0
            # a table of charges and payments: a code's share is taken on both sides, the larger
            other = next((analysis.col(t.tid, h) for h in pair_of.get(t.tid) or [] if h != money.header), None) \
                if money.header in (pair_of.get(t.tid) or []) else None
            if other is not None:
                side, _s = _value_stats(analysis, t, c, other)
                tot2 = sum(abs(r[other.j]) for r in t.rows if other.j < len(r) and _is_num(r[other.j]))
                if tot2:
                    share = max(share, sum(side[k][3] for k in minority if k in side) / tot2)
            if status and not (cryptic or caps) and not minority:
                continue              # words, none of them rare: nothing to ask about what counts
            fmt = money_fmt(analysis, t, money)
            # a value unlike the others (a site present only mid-year with its own staff) says so here too; a
            # price's unit is its own question
            odd = [i["numbers"] for i in analysis.insights if i.get("recipe", "").startswith("oddgroup:")
                   and i["numbers"]["table"] == t.tid and i["numbers"]["col"] == c.header
                   and not i["numbers"].get("price")]
            # a few rows of one group on a code the rest of that group never uses
            inside = _code_inside(t, c, cols, shown_money, fmt, stats)
            # new values that take over from old ones at one date go into this column's one question
            ho = analysis.handoffs.get((t.tid, c.header))
            # evidence folded in keeps the prompt to two lines: fewer values are listed beside it
            # every value when there are SHOWN or fewer (a code hidden behind 'more' cannot be explained)
            listing = _listing(stats, keys, shown_money, span, fmt,
                               shown=SHOWN if len(keys) <= SHOWN or not (odd or inside or ho) else BESIDE) \
                + mixed_words
            if ho:
                listing += (f". From {ho['when']} on, new values take over from old ones: "
                            + "; ".join(f"{p['old']} -> {p['new']} ({_pair_why(p, ho['via'])})"
                                        for p in ho["pairs"][:DOSSIER_PAIRS])
                            + (f", and {len(ho['pairs']) - DOSSIER_PAIRS:,} more"
                               if len(ho["pairs"]) > DOSSIER_PAIRS else ""))
            for o in odd:
                listing += f". {o['value']} is unlike the others: " + "; ".join(e["text"] for e in o["evidence"][:2])
            if inside:
                listing += f". {inside['text']}"
            mh = money.header
            if status and not (cryptic or caps):          # words: only which of them count
                prompt = f"Which {c.header} values on {t.sheet} count toward {towards}? {listing}."
                typed = {"id": "type", "label": "I'll type which", "desc": f"Type the values that do not count "
                                                                            f"toward {towards}"}
            else:
                ex = stats[keys[-1]][0]
                prompt = (f"What do the codes in {c.header} on {t.sheet} mean? {listing}. Type them like "
                          f"\"{_short(ex, 20)} = what it means\". If any should not count toward {towards}, "
                          "say which.")
                typed = {"id": "type", "label": "I'll type them", "desc": "Type what each one means"}
            opts, meta_rules = [], {}
            # what each pick settles: a meaning, or only whether the rows count (typed text says what it is)
            aspect = {"all_count": "treatment", "tab": "meaning",
                      "type": "treatment" if status and not (cryptic or caps) else "meaning"}
            if 1 <= len(minority) <= 2:
                for i, k in enumerate(minority, 1):
                    w, n, m = stats[k][:3]
                    oid = f"leave_{i}"
                    label = f"Leave {_short(w, 20)} out of {towards}"
                    desc = f"{n:,} row{'s' if n != 1 else ''}" + (f", {fmt(m)}" if shown_money is not None else "")
                    if measure and (len(label) > LABEL_MAX or re.search(r"\band\b|,", measure, re.I)):
                        # a long header (or one that reads as two things) is named under the option instead
                        label, desc = f"Leave {_short(w, 20)} out of the totals", f"{desc} of {measure}"
                    opts.append({"id": oid, "label": label, "desc": desc})
                    meta_rules[oid] = Rule("exclude", t.tid, [{"col": c.header, "op": "in", "values": [w]}],
                                           scope=[measure] if measure else [], source=qid).to_dict()
                    aspect[oid] = "treatment"
                opts.append({"id": "all_count", "label": "All of them count",
                             "desc": f"Every {c.header} value stays in {towards}"})
            elif status and not (cryptic or caps):
                opts = [typed, {"id": "all_count", "label": "All of them count",
                                "desc": f"Every {c.header} value stays in {towards}"}]
            else:
                opts = [typed, {"id": "tab", "label": "Another tab explains them",
                                "desc": "A tab or file; name it"}]
            # a status whose one value is exactly the rows another column leaves blank (the units with no tenant
            # and no rent): asked on that column, which says what the statuses count toward. A status written
            # partly in capitals (a code on many rows) is asked this way too, its meaning kept asked beside it
            lined = _status_lines_up(analysis, t, c, stats, keys) if status and not cryptic else None
            if lined:
                v, x, n_, blank_cols = lined
                # a value written as an abbreviation keeps its meaning asked in the same question
                abbr = [stats[k][0] for k in keys if _cryptic(stats[k][0]) or re.fullmatch(r"[A-Z]{2,4}", stats[k][0])]
                ask = (f" And what {'does' if len(abbr) == 1 else 'do'} {_join(abbr[:3])} mean? Pick, and type the "
                       "meanings." if abbr else "")
                have = _join([f"{_a(h)} {h}" for h in blank_cols])
                prompt = (f"Only {v} in {c.header} on {t.sheet} has no {_join(blank_cols) if len(blank_cols) > 1 else x}"
                          f" ({n_:,} row{'s' if n_ != 1 else ''}). Do all other {c.header} values count?{ask} "
                          f"{listing}.")
                opts = [{"id": "all_but", "label": f"Yes: every {c.header} but {_short(v, 20)} counts"[:60],
                         "desc": f"Every other {c.header} value has {have}"},
                        {"id": "type", "label": "Some others do not (type which)", "desc": "Type which do not count"}]
                meta_rules = {}
                aspect = {"all_but": "treatment", "type": "meaning" if abbr else "treatment"}
                lined = (v, x, n_, blank_cols, have)
            # offered only beside picks it never contradicts ('All of them count' empties any other pick)
            # a map over only some of the pairs would leave the rest apart: with more pairs than the dossier
            # shows, the handoff follow-up after the boundary question offers the map instead
            if ho and len(opts) < 3 and not any(o["id"] == "all_count" for o in opts) \
                    and len(ho["pairs"]) <= DOSSIER_PAIRS:
                shown = ho["pairs"][:DOSSIER_PAIRS]          # a rule covers only the pairs the prompt shows
                p0 = shown[0]
                to = {}
                for p in shown:
                    to[str(norm_key(p["old"]))] = to[str(norm_key(p["new"]))] = p["new"]
                opts.append({"id": "handoff", "label": "Same things under new names",
                             "desc": f"Count {_short(p0['old'], 20)} as {_short(p0['new'], 20)}, and each pair shown "
                                     "as one"})
                meta_rules["handoff"] = Rule("map", t.tid, [{"col": c.header, "op": "in",
                                                             "values": [x for p in shown for x in (p["old"], p["new"])]}],
                                             {"col": c.header, "to": to}, source=qid).to_dict()
                aspect["handoff"] = "meaning"
            vals = [stats[k][0] for k in keys]
            # which values count is what the question settles: words, or a status lined up with blank columns
            words_only = bool(status and not (cryptic or caps)) or bool(lined)
            q = Q(qid, "Codes", prompt, opts,
                  why="Codes only the team reads are what gets lost when people change, and a code that should "
                      "not count changes every total.",
                  multi=bool(meta_rules), kind="definition", priority=1, source="finding",
                  fact={"kind": "definition", "class": "data", "depends": [],
                        "statement": f"The codes in {c.header} on {t.sheet}, per the owner: {{answer_labels}}.",
                        "statements": {"all_count": f"Every {c.header} value counts toward {towards}, per the "
                                                    "owner."} if any(o["id"] == "all_count" for o in opts) else {}},
                  # a question about which values count settles what counts, never what the values mean
                  meta={"about": _about(t.tid, c.header, "treatment" if words_only else "meaning",
                                        vals), "codes": vals, "dossier": True,
                        "minority": [stats[k][0] for k in minority],
                        "rules": meta_rules, "money": mh, "measure": measure, "option_aspect": aspect,
                        # 'All of them count' ticked with a leave-out says two opposite things
                        "exclusive": ["all_count"] if meta_rules else [],
                        # codes only the team reads come first among questions of equal value and stake
                        "cryptic": cryptic, "inside": inside,
                        "clause": f"{len(keys)} {'values' if words_only else 'codes'} in "
                                  f"{c.header} on {t.sheet} never explained"})
            if lined:
                counted = [stats[k][0] for k in keys if stats[k][0] != lined[0]]
                each = f"{_join(counted)} each have {lined[4]}" if 1 < len(counted) <= SHOWN else \
                    f"every other {c.header} value has {lined[4]}"
                q.fact["statements"] = {"all_but": f"Every {c.header} value on {t.sheet} but {lined[0]} counts, per "
                                                   f"the owner: {each}; {lined[0]} has no "
                                                   f"{_join(lined[3])}."}
            _worth(q, share)
            found.setdefault(key, []).append((total, q))
    out = [max(qs, key=lambda x: (x[0], -len(x[1].id)))[1] for qs in found.values()]
    return sorted(out, key=lambda q: q.id)


def _currency_question(t, c, money, stats: dict, keys: list, total: float, qid: str):
    """A column of currency codes beside the money: which currency the money is
    in settles its unit. No rows are offered to leave out."""
    from .interview import Q
    mh, top = money.header, _short(stats[keys[0]][0], 10)
    q = Q(qid, "Currency",
          f"{c.header} on {t.sheet} names {len(keys)} currencies ({_listing(stats, keys, None, None)}). Which "
          f"currency is {mh} in?",
          [{"id": "row", "label": "Each row's own currency", "desc": f"{mh} is in the {c.header} on its row"},
           {"id": "one", "label": f"All in {top}", "desc": f"{mh} is in {top} on every row"}],
          why="A total that adds two currencies is off by the exchange rate.",
          kind="unit", priority=1, source="finding",
          fact={"kind": "unit", "class": "data", "depends": [],
                "statement": f"The currency of {mh} on {t.sheet}, per the owner: {{answer_labels}}."},
          meta={"about": _about(t.tid, mh, "unit", [stats[k][0] for k in keys]), "dossier": True,
                "clause": f"{c.header} on {t.sheet} names {len(keys)} currencies"})
    _worth(q, sum(stats[k][3] for k in keys[1:]) / total if total else 0.0)
    return q


def _map_dossier(analysis, t, c, money, mapped: dict, qid: str):
    """The lookup-map form of a dossier: the column holds names on N rows and
    codes on M; the lookup's own rows say which name is which code."""
    from .interview import Q
    from .rules import Rule
    lt = analysis.table(mapped["lookup"])
    name, code = mapped["example"]

    def names_rows(r):
        k = norm_key(r[c.j] if c.j < len(r) else None)
        return k in mapped["to"] and norm_key(mapped["to"][k]) != k
    fewer = names_rows if mapped["names"] <= mapped["codes"] else (lambda r: not names_rows(r))
    written = {}
    for r in t.rows:
        v = r[c.j] if c.j < len(r) else None
        written.setdefault(norm_key(v), str(v).strip() if v is not None else "")
    # the rule names each value as the column writes it (it matches every spelling of it all the same)
    rule = Rule("map", t.tid, [{"col": c.header, "op": "in", "values": sorted(
        {str(v) for v in mapped["to"].values()} | {written.get(k) or str(k) for k in mapped["to"]})}],
        {"col": c.header, "to": dict(mapped["to"])}, source=qid)
    # every name the column holds, with its code: typed words that pair them all confirm the map as a pick does
    pairs = sorted({(str(k), str(v)) for k, v in mapped["to"].items() if norm_key(v) != k})
    named = [written.get(k, k) for k, _v in pairs] + [v for _k, v in pairs]
    treat = f"Each name counts as its code in every count and total by {c.header}"
    # names that stop where codes start at a date the file changes: said, so the answer carries when
    ho = (getattr(analysis, "handoffs", None) or {}).get((t.tid, c.header)) or {}
    when = f" Names are written before {ho['when']} and codes from then on." if ho.get("when") else ""
    # every pair when there are few, so the answer names each one; else one example
    every = [(written.get(k, k), v) for k, v in pairs]
    shown = ("; ".join(f"\"{_short(n_, 30)}\" is {_short(v, 20)}" for n_, v in every) + f" on {lt.sheet}"
             if 2 <= len(every) <= HANDOFF_SHOWN else
             f"for example \"{_short(name, 30)}\" is {_short(code, 20)} on {lt.sheet}")
    q = Q(qid, "Names, codes",
          f"{c.header} on {t.sheet} holds names on {mapped['names']:,} rows and codes on {mapped['codes']:,} "
          f"({shown}).{when} Is each name the same thing as its code?",
          [{"id": "same", "label": "Each name is its code",
            "desc": f"Count \"{_short(name, 30)}\" as {_short(code, 20)}, and each other name as its code on "
                    f"{lt.sheet}. {treat}"},
           {"id": "different", "label": "They are different things", "desc": "Keep the names apart from the codes"}],
          why="Until names and codes are one, every count by this column splits one thing in two.",
          kind="mapping", priority=1, source="finding",
          fact={"kind": "mapping", "class": "data", "depends": [],
                "statement": f"{c.header} on {t.sheet}, names and codes, per the owner: {{answer_labels}}.",
                "statements": {"same": f"Each name in {c.header} on {t.sheet} is its code on {lt.sheet}, per the owner "
                                       f"({shown})"
                                       + (f": names are written before {ho['when']} and codes from then on"
                                          if ho.get("when") else "") + f". {treat}.",
                               "different": f"The names and codes in {c.header} on {t.sheet} are different things, "
                                            "per the owner."}},
          meta={"about": _about(t.tid, c.header, "meaning", [name, code]), "dossier": True,
                "rules": {"same": rule.to_dict()}, "lookup": mapped["lookup"],
                "map_text": {"option": "same", "values": [x for x in named if x]},
                # only 'each name is its code' says what the column holds
                "option_aspect": {"same": "meaning", "different": "treatment"},
                "clause": f"{c.header} on {t.sheet} holds names on {mapped['names']:,} rows and codes on "
                          f"{mapped['codes']:,}"})
    _worth(q, row_stake(analysis, t, fewer))
    return q


def follow_ups(analysis, answers: dict) -> list:
    """Questions an answer opened: asked only after that answer exists."""
    from .interview import Q
    out = []
    for qid, ans in answers.items():
        if not isinstance(ans, dict):
            continue
        opts = ans.get("options") or []
        # the pairs a pick on 'old or changed codes' confirmed were shown under it: asked again only when the
        # owner typed something beside the pick (some of them, or which); a bare pick keeps the follow-up
        # at no value, settled by the pick, for anything that reads it
        if qid.startswith("find_unmatched_") and "old_codes" in opts:
            pairs = _code_pairs(analysis, ans)
            fid = "follow_codes_" + qid[len("find_unmatched_"):]
            if pairs and fid not in answers:
                shown = [f"{a} -> {b} ({d})" for a, b, d in pairs[:4]]
                q = Q(fid, "Same item?",
                      (f"These {len(shown)} pairs of codes look" if len(shown) != 1 else "This pair of codes looks")
                      + " like the same thing under a new code: " + "; ".join(shown) + ". Same?",
                      [{"id": "all", "label": "Yes, all the same", "desc": "Count each pair as one"},
                       {"id": "some", "label": "Some of them", "desc": "Type which ones"},
                       {"id": "none", "label": "No, different things", "desc": "Keep them apart"}],
                      recommend="all", recommend_basis="Same description, and the old code stops when the new one starts",
                      why="Price changes and totals per item are wrong while one product has two codes.",
                      kind="mapping", priority=1, source="finding",
                      fact={"kind": "mapping", "class": "data", "depends": [],
                            "statement": "Re-coded items, per the owner (" + "; ".join(shown) + "): {answer_labels}.",
                            "statements": {"all": "These codes are all the same thing under a new code, per the owner: "
                                                  + "; ".join(shown) + ".",
                                           "none": "These codes are different things, per the owner: "
                                                   + "; ".join(shown) + "."}},
                      meta={"about": dict(_parent_about(analysis, qid, ans),
                                          values=[a for a, _b, _d in pairs[:4]])})
                _worth(q, _finding_stake(analysis, _unmatched_for(analysis, qid, ans) or {}), follow=True)
                q.gated = True
                if not (ans.get("text") or "").strip() and ans.get("code_pairs"):
                    q.value, q.meta["confirmed_by_pick"] = 0.0, qid
                out.append(q)
        if qid.startswith("find_unmatched_") and "suspense" in opts:
            fid = "follow_suspense_" + qid[len("find_unmatched_"):]
            lines = _suspense_lines(analysis, _unmatched_for(analysis, qid, ans))
            said = (ans.get("text") or "").lower()
            # skip only when the owner already named the lines themselves (a date or an amount),
            # not when they only explained what the account is
            named = any(ln.split(":")[0].lower() in said or ln.split("$")[1].split(" ")[0].replace(".00", "") in said
                        for ln in lines if ":" in ln and "$" in ln)
            if lines and fid not in answers and not named:
                q = Q(fid, "Parked lines",
                      "The biggest parked lines are: " + "; ".join(lines) + ". Do you know what any of them are? "
                      "Type what you know, like '<date> = what it is, which account'.",
                      [{"id": "type", "label": "I'll type what I know", "desc": "Type what they are"},
                       {"id": "open", "label": "Not yet, leave them open", "desc": "They stay on the to-sort list"}],
                      why="What you already know about these lines is exactly what gets lost otherwise.",
                      kind="history", priority=1, source="finding",
                      fact={"kind": "history", "class": "data", "depends": [],
                            "statement": "Parked lines the owner identified: {answer_text}."},
                      meta={"about": _parent_about(analysis, qid, ans)})
                _worth(q, _finding_stake(analysis, _unmatched_for(analysis, qid, ans) or {}), follow=True)
                q.gated = True
                out.append(q)
        if qid.startswith("find_boundary_") and not ans.get("not_sure"):
            # the columns whose values were renamed at that date, unless a dossier already asks about the column
            ab = ans.get("about") or {}
            b = next((i for i in analysis.insights if i.get("recipe", "").startswith("boundary:")
                      and i["numbers"]["table"] == ab.get("table")
                      and qid.endswith(i["numbers"]["date"].replace("-", ""))), None)
            hos = []
            for h in (b or {}).get("numbers", {}).get("handoffs", []):
                ho = next((i for i in analysis.insights if i.get("recipe") == f"handoff:{ab.get('table')}:{h}"), None)
                if ho is None or _has_dossier(analysis, ab.get("table"), h):
                    continue
                hos.append(ho)
            # one switch that renamed values in two columns is one question listing both columns' pairs
            q = _handoff_question(analysis, hos[0], follow=True, more=hos[1:]) if hos else None
            if q is not None and q.id not in answers and not any(x.id in answers for x in
                                                                  [_handoff_question(analysis, h, follow=True)
                                                                   for h in hos]):
                _worth(q, max(_finding_stake(analysis, h) for h in hos), follow=True)
                q.gated = True
                out.append(q)
        # lines on the unusual side the owner called refunds: net them or leave them out. Negative rows are
        # asked that with their own options; a typed answer that picked no treatment opens it
        # (lines on the unusual side: only a typed answer that picked no group opens it)
        net_of = next(((p, r) for p, r in (("find_negatives_", "negatives:"), ("find_contra_", "contra:"))
                       if qid.startswith(p) and (ans.get("text") or "").strip()
                       and not {"credits", "apart", "cost", "income", "corrections"} & set(opts)), None)
        if net_of:
            fid = "follow_net_" + qid[len(net_of[0]):]
            if fid not in answers:
                ins = next((i for i in analysis.insights if i.get("recipe", "").startswith(net_of[1])
                            and _net_slug(i) == qid[len(net_of[0]):]), {})
                if net_of[1] == "contra:":
                    n = ins.get("numbers") or {}
                    about = _about(n["table"], n["col"], "treatment", n.get("values")) if n else \
                        _parent_about(analysis, qid, ans)
                    where = f"on the side opposite their {n['col']}'s usual side" if n else ""
                    q = _net_question(fid, "refunds or returns", "lines", n.get("rows", 0), about, where,
                                      also=_measure_words(analysis))
                else:
                    rid = ins.get("recipe", ":").split(":", 1)[1]
                    label = analysis.detection["roles"].get(rid, {}).get("header", rid)
                    q = _net_question(fid, "credits", "rows", (ins.get("numbers") or {}).get("rows", 0),
                                      _about_role(analysis, rid, "treatment"), f"with a negative {label}",
                                      also=_measure_words(analysis))
                _worth(q, _finding_stake(analysis, ins), follow=True)
                q.gated = True
                out.append(q)
        # items the owner said are not real (fees, holding codes): which calculations they stay out of
        if qid.startswith("find_unmatched_") and "not_items" in opts:
            q = _not_items_scope(analysis, qid, ans, answers)
            if q is not None:
                out.append(q)
    base = _rebate_base(analysis, answers)
    if base is not None:
        out.append(base)
    rb = readback(analysis, answers)
    if rb is not None:
        out.append(rb)
    return out


REBATE = "follow_rebate_base"
_REBATE_HEAD = re.compile(r"\b(rebates?|allowances?)\b", re.I)


def _rebate_base(analysis, answers: dict):
    """When the session holds a rebate or allowance rate and the owner has said
    which rows stay out of the totals, what the rebate is paid on: each answered
    exclusion as an option (net of credits, without the fee lines, without a
    value left out), multi-select. The answer is the owner's note."""
    from .interview import Q
    from .rules import confirmed
    if REBATE in answers:
        return None
    rate = next(((t, c) for t in analysis.tables for c in analysis.cols.get(t.tid, [])
                 if c.type == "number" and _REBATE_HEAD.search(str(c.header))), None)
    if rate is None:
        return None
    opts = []
    netted = any(isinstance(a, dict) and ((k.startswith("find_negatives_") and "credits" in (a.get("options") or []))
                                          or (k.startswith("follow_net_") and set(a.get("options") or [])
                                              & {"net", "totals_only"}))
                 for k, a in answers.items())
    if netted:
        opts.append({"id": "net", "label": "Net of credits", "desc": "Credits come off before the rate"})
    for k, a in answers.items():
        if k.startswith("find_unmatched_") and isinstance(a, dict) and "not_items" in (a.get("options") or []):
            f = _unmatched_for(analysis, k, a)
            paired = {norm_key(x) for x, _y in a.get("code_pairs") or []}
            fees = [str(x) for x in ((f or {}).get("numbers") or {}).get("examples") or [] if norm_key(x) not in paired]
            if fees:
                opts.append({"id": "fees", "label": "Without the fee lines",
                             "desc": _fit_desc(f"{_listed(fees)} rows stay out")})
            break
    for r in confirmed(analysis, answers):
        if len(opts) >= 3:
            break
        if r.kind != "exclude" or r.scope or len(r.predicate) != 1 or r.predicate[0].get("op", "in") != "in":
            continue
        vals = [str(v) for v in r.predicate[0].get("values") or []][:2]
        lab = f"Without {_join(vals)}"
        if vals and len(lab) <= 60 and not any(o["label"] == lab for o in opts):
            opts.append({"id": f"ex_{len(opts)}", "label": lab, "desc": f"The {r.predicate[0]['col']} rows left out "
                                                                          "of the totals stay out"})
    if len(opts) < 2:
        return None
    t, c = rate
    q = Q(REBATE, "Rebate base",
          f"{c.header} on {t.sheet} gives rebate rates ({c.count:,} rows). What is the rebate paid on? Pick all that "
          "apply.",
          opts[:3], why="A rebate checked against the wrong base looks short or long by the rows it should leave out.",
          multi=True, kind="rule", priority=2, source="finding",
          fact={"kind": "rule", "class": "data", "depends": [],
                "statement": f"The rebate at the rates in {c.header} on {t.sheet} is paid, per the owner: "
                             "{answer_labels}."},
          meta={"about": _about(t.tid, c.header, "scope")})
    _worth(q, 0.02)
    q.gated = True
    return q


def _not_items_scope(analysis, qid: str, ans: dict, answers: dict):
    """After 'Not real items': which calculations those codes stay out of (the
    money totals, price comparisons, a rebate's base), multi-select. Ticking the
    totals leaves their rows out of that column's totals only."""
    from .interview import Q
    from .rules import Rule
    fid = "follow_items_" + qid[len("find_unmatched_"):]
    finding = _unmatched_for(analysis, qid, ans)
    if fid in answers or not finding:
        return None
    n = finding["numbers"]
    t = analysis.table(n["from_table"])
    col = n["from_col"]
    paired = {norm_key(a) for a, _b in ans.get("code_pairs") or []}
    codes = [str(x) for x in n.get("examples") or [] if norm_key(x) not in paired]
    keys = {str(k) for k in n.get("missing_keys") or [] if k not in paired}
    if not codes or not keys:
        return None
    m = money_column(analysis, t)
    listed = _listed(codes)
    opts = []
    rules = {}
    if m is not None:
        opts.append({"id": "totals", "label": f"{_short(m.header, 30)} totals", "desc": f"Their rows stay out of "
                                                                                    f"{m.header} totals"})
        vals = sorted({str(r[t.headers.index(col)]).strip() for r in t.rows if col in t.headers
                       and t.headers.index(col) < len(r) and str(norm_key(r[t.headers.index(col)])) in keys})
        rules["totals"] = Rule("exclude", t.tid, [{"col": col, "op": "in", "values": vals}], scope=[m.header],
                               source=fid).to_dict()
    opts.append({"id": "prices", "label": "Price comparisons", "desc": "They are never compared as items"})
    roles = analysis.detection["roles"]
    if any(r in roles for r in ("rebate", "allowance")):
        opts.append({"id": "rebate", "label": "The rebate base", "desc": "Rebates are not paid on them"})
    q = Q(fid, "Not items",
          f"{col} on {t.sheet} has {n['values']:,} code{'s' if n['values'] != 1 else ''} that are not real items, per "
          f"the owner, like {listed}. Which should they stay out of? Pick all that apply.",
          opts[:3], why="A fee counted as an item moves every total and price it is in.",
          multi=True, kind="rule", priority=1, source="finding",
          fact={"kind": "rule", "class": "data", "depends": [],
                "statement": f"The {col} codes on {t.sheet} that are not real items ({listed}) stay out of, per the "
                             "owner: {answer_labels}."},
          meta={"about": _about(t.tid, col, "treatment", codes), "rules": rules})
    _worth(q, _finding_stake(analysis, finding), follow=True)
    q.gated = True
    return q


def _net_slug(ins: dict) -> str:
    """The id suffix of the question a negatives or contra finding asks."""
    n = ins.get("numbers") or {}
    if ins["recipe"].startswith("contra:"):
        return _slug(f"{n.get('col')}_{n.get('side_col')}")
    return _slug(ins["recipe"].split(":", 1)[1])


# what a playbook's reports total besides the totals, in its own words: a ledger's report lines, a buyer's prices
_MEASURE_WORDS = {"procurement": "price comparisons and rates", "ledger": "report lines and class totals",
                  "ar_ap": "report lines and aging totals", "financial_model": "report lines and ratios"}


def _measure_words(analysis) -> str:
    return _MEASURE_WORDS.get((analysis.playbook or {}).get("id"), "averages and rates")


def _net_question(fid: str, noun: str, unit: str, rows: int, about: dict, where: str = "",
                  also: str = "price comparisons and rates"):
    """Whether the credits (or refunds) an answer named come off the totals, and
    where the rule applies: totals only, or price comparisons and rates too. The
    prompt names the rows itself (where), so it stands alone as an open item."""
    from .interview import Q
    cap = noun[:1].upper() + noun[1:]
    Also = also[:1].upper() + also[1:]
    return Q(fid, "Net credits",
             f"Should the {noun} among the {rows:,} {unit}{(' ' + where) if where else ''} come off the totals (net "
             f"them), or be left out? And do {also} net them too?",
             [{"id": "net", "label": "Net them against totals", "desc": f"Totals, {also} are after {noun}"},
              {"id": "totals_only", "label": "Net them in totals only",
               "desc": f"{Also} use the lines before {noun}"},
              {"id": "exclude", "label": "Leave them out", "desc": "Left out of every total"}],
             why="Gross and net totals can differ by thousands.",
             kind="rule", priority=1, source="finding",
             fact={"kind": "rule", "class": "data", "depends": [],
                   "statement": f"{cap} in totals, per the owner: {{answer_labels}}.",
                   "statements": {"net": f"{cap} come off the totals (net them), per the owner; {also} are after "
                                         f"{noun}.",
                                  "totals_only": f"{cap} come off the totals only (net them), per the owner; "
                                                 f"{also} use the lines before {noun}.",
                                  "exclude": f"{cap} are left out of totals, per the owner: left out of every "
                                             "total."}},
             meta={"about": about})


READBACK = "confirm_rules"
KNOWN = "find_known_"
# findings whose question is 'a known issue, or news to you?' at heart: small, and asked together when they would
# not get a slot of their own
# (which total is right, product_check, is a treatment each answer decides: asked alone, never batched)
_KNOWN_KINDS = ("find_offlist_", "find_onset_", "find_window_")
KNOWN_MAX = 12.0          # a batch is never worth more than a full-stake finding


def batch_known(analysis, qs: list, answers: dict) -> list:
    """Two or more small 'known issue or news?' findings (a price off the list, a
    code that starts for one group, rows whose line total is not quantity times
    price, lines outside their dates) that fall under TAU or would not get a slot
    (another question holds their column, or the cap runs out first) are asked as
    one: 'Which of these do you already know about?', one option per finding (the
    top 3 by value). The batch takes one slot; each pick names its own finding."""
    from .interview import HARD_CAP, TAU, Q, ranked, substantive
    members = [q for q in qs if q.id.startswith(_KNOWN_KINDS) and not q.gated and q.id not in answers]
    if len(members) < 2:
        return qs
    room = max(0, HARD_CAP - substantive(answers))
    order = [q.id for q in ranked(qs) if not q.id.startswith(READBACK)][:room]
    lose = sorted([q for q in members if q.id not in order or q.value < TAU], key=lambda q: (-q.value, q.id))[:3]
    if len(lose) < 2:
        return qs
    opts, about, statements = [], {}, {}
    for k, q in enumerate(lose, 1):
        oid = f"known_{k}"
        label, said = _known_words(analysis, q)
        opts.append({"id": oid, "label": label, "desc": _fit_desc(said)})
        about[oid] = q.meta.get("about") or {}
        statements[oid] = f"{said.rstrip('. ')}: the owner already knows about it."
    first = lose[0]
    q = Q(KNOWN + _hash([x.id for x in lose]), "Known issues?",
          f"I found {len(lose)} smaller things in the numbers: " + "; ".join(o["label"] for o in opts)
          + ". Which of these do you already know about? Pick all that apply.",
          opts, why="What you already know about these is what gets lost; what you don't may be money to recover.",
          multi=True, kind="history", priority=2, source="finding",
          fact={"kind": "history", "class": "data", "depends": [],
                "statement": "Of the smaller things found, the owner already knows about: {answer_labels}.",
                "statements": statements},
          meta={"about": dict(first.meta.get("about") or {}), "members": [x.id for x in lose],
                "member_about": about, "tables": list(dict.fromkeys(
                    t for x in lose for t in ([(x.meta.get("about") or {}).get("table")] + (x.meta.get("tables") or []))
                    if t)),
                "clause": "; ".join(o["label"] for o in opts)})
    # a batch is worth what its members' stakes together move, on the history scale: never the sum of their values
    from .interview import worth
    q.meta["stake"] = round(sum(float(x.meta.get("stake") or 0.0) for x in lose), 6)
    q.value = round(min(KNOWN_MAX, worth(q.meta["stake"], "history")), 3)
    gone = {id(x) for x in lose}
    return [x for x in qs if id(x) not in gone] + [q]


def _known_words(analysis, q) -> tuple:
    """(a short label with its count, the finding's evidence in one sentence) for a
    member of a batch."""
    n = (q.meta.get("finding") or {}).get("numbers") or {}
    if q.id.startswith("find_offlist_"):
        who = f"{n.get('value')} lines" if n.get("value") else "lines"
        label = f"{n.get('rows', 0):,} {who} {n.get('side', 'off')} the list"
    elif q.id.startswith("find_onset_"):
        f = (n.get("items") or [{}])[0]
        label = f"{f.get('code')} starting for {f.get('group')}"
    elif q.id.startswith("find_product_"):
        label = f"{n.get('mismatches', 0):,} rows where the line total is off"
    else:
        label = f"{n.get('rows', 0):,} lines outside their dates"
    said = q.meta.get("clause") or (q.prompt.split(". ")[0] if q.prompt else label)
    label = " ".join(str(label).split())
    if len(label) > 60:
        label = label[:57].rsplit(" ", 1)[0] + "..."
    return label, said


def readback(analysis, answers: dict):
    """One question that reads back what typed answers said: first the options a
    typed reply restated on its own question ('So: <option>', one line each, a
    tick records that option there), then the rules typed answers proposed,
    whatever those questions were about: each rule with its rows, and money under
    it, so only what the owner ticks changes a number. At most 3 lines (inferred
    options first, then the conjunction a sentence named first, then the
    largest); one option scopes the ticked rules to the metric a sentence named.
    A rule whose rows a pick already leaves out of the same or wider totals is
    not offered again, nor one on a list tab no total counts."""
    from . import rules
    from .interview import Q
    if sum(1 for k in answers if k.startswith(READBACK)) >= rules.MAX_READBACKS:
        return None
    props = _new_proposals(analysis, answers, rules.proposals(analysis, answers))
    if not props:
        return None
    # the options typed replies restated ride on a readback the typed rules already call for, ahead of them,
    # leaving a rule at least one line: they never spend a readback of their own
    lines = _inferred_lines(analysis, answers)[:2]
    groups: dict = {}
    for c in props:
        groups.setdefault(c["said"], []).append(c)
    ordered = []
    for g in sorted(groups.values(), key=lambda g: -max(c["rows"] for c in g)):
        ordered += sorted(g, key=lambda c: (len(c["rule"].predicate) < 2, -c["rows"]))
    room = 3 - len(lines)
    metric = next((c["scope"][0] for c in ordered if c["scope"]), "") if room >= 2 else ""
    shown = ordered[:room - 1 if metric else room]
    # a scope is offered only when every rule it would scope has that column to total
    if metric and not (any(c["scope"] for c in shown)
                       and all(metric in analysis.table(c["rule"].table).headers for c in shown)):
        metric = ""
        shown = ordered[:room]
    opts, meta_rules, infer, statements = [], {}, {}, {}
    for k, x in enumerate(lines, 1):
        oid = f"i{k}"
        opts.append({"id": oid, "label": x["label"], "desc": x["desc"]})
        infer[oid] = x["infer"]
        if x.get("statement"):
            statements[oid] = x["statement"]
    mixed = unit_changed(analysis, answers)
    for k, c in enumerate(shown, 1):
        oid = f"r{k}"
        desc = _rule_desc(analysis, c, mixed.get((c["rule"].table, c.get("col"))))
        opts.append({"id": oid, "label": c.get("label") or _rule_label(c), "desc": _fit_desc(desc)})
        meta_rules[oid] = c["rule"].to_dict()
    scopes = {}
    if metric:
        opts.append({"id": "scope", "label": f"Only for {metric}"[:60],
                     "desc": f"The rules you tick change {metric} alone"})
        scopes["scope"] = [metric]
    rows, stake = 0, 0.0
    for tid in dict.fromkeys(c["rule"].table for c in shown):
        t = analysis.table(tid)
        idx = {h: j for j, h in enumerate(t.headers)}
        mine = [c["rule"] for c in shown if c["rule"].table == tid]
        hit = lambda row: any(r.valid(idx) and r.matches(row, idx) for r in mine)  # noqa: E731
        rows += sum(1 for row in t.rows if hit(row))
        stake = max(stake, row_stake(analysis, t, hit))
    stake = max([stake] + [float(x.get("stake") or 0.0) for x in lines])
    k = len(shown)
    ask = []
    if lines:
        ask.append("I read your typed answer as the line starting 'So:'" if len(lines) == 1 else
                   f"I read {len(lines)} typed answers as the lines starting 'So:'")
    if shown:
        ask.append(f"{k} rule{'s' if k != 1 else ''} from what you typed would change {rows:,} "
                   f"row{'s' if rows != 1 else ''}")
    if shown and not lines:
        prompt = (f"From what you typed, {k} rule{'s' if k != 1 else ''} would change {rows:,} "
                  f"row{'s' if rows != 1 else ''}. Which should I apply to every count and total? Pick all that apply.")
    elif shown:
        prompt = "; ".join(ask) + ". Which should I apply to every count and total? Pick all that apply."
    else:
        prompt = "; ".join(ask) + ". Tick each that is right. Pick all that apply."
    if shown:
        first = shown[0]["rule"]
        col = first.predicate[0]["col"] if first.predicate else first.values.get("col", "")
        vals = first.predicate[0].get("values") if first.predicate else None
        about = _about(first.table, col, "treatment", vals)
    else:
        about = dict(lines[0].get("about") or {})
    tables = list(dict.fromkeys([c["rule"].table for c in shown]
                                + [(x.get("about") or {}).get("table") for x in lines if (x.get("about") or {}).get("table")]))
    # a ticked 'So:' line is recorded on its own question, where its note is written (rules.with_inferred)
    fact = {"kind": "rule", "class": "data", "depends": [],
            "statement": "Rules the owner said to apply to every count and total: {answer_labels}."}
    q = Q(f"{READBACK}_{_hash([str(c['rule'].key()) for c in shown] + [x['key'] for x in lines])}", "Your rules",
          prompt, opts, why="Until you pick, every count and total includes these rows and says so.",
          multi=True, kind="rule", priority=1, source="finding", fact=fact,
          meta={"rules": meta_rules, "scopes": scopes, "about": about, "infer": infer,
                # every table a rule is on, so the answer reaches each brain whose numbers it changes
                "tables": tables})
    _worth(q, stake, follow=True)
    q.gated = True
    return q


def _inferred_lines(analysis, answers: dict) -> list:
    """The options typed replies restated on their own questions (interview's
    'inferred'), one line each, not yet offered on a readback: {label ('So: ...'),
    desc (the curated statement the tick writes, else the option's own
    description), infer {qid, option, label, desc}, statement, about, stake, key}.
    The largest stake first."""
    from .interview import Env, fill
    offered = {(inf.get("qid"), inf.get("option")) for k, a in (answers or {}).items()
               if k.startswith(READBACK) and isinstance(a, dict) for inf in (a.get("infer") or {}).values()
               if isinstance(inf, dict)}
    env = Env(analysis, answers)
    out = []
    for qid, a in (answers or {}).items():
        if not isinstance(a, dict) or qid.startswith(("_", READBACK)) or not a.get("inferred"):
            continue
        for x in a["inferred"]:
            if (qid, x.get("option")) in offered or x.get("option") in (a.get("options") or []):
                continue
            tpl = ((a.get("fact") or {}).get("statements") or {}).get(x["option"])
            one = dict(a, options=[x["option"]], labels=[x["label"]], descs={x["option"]: x.get("desc", "")})
            stmt = fill(tpl, env, one) if tpl else ""
            label = f"So: {x['label']}"
            if len(label) > LABEL_MAX:
                label = label[:LABEL_MAX - 3].rsplit(" ", 1)[0] + "..."
            desc = stmt if stmt and len(stmt) <= 200 else (x.get("desc") or "")
            out.append({"label": label, "desc": _fit_desc(desc or f"Your answer to: {_short(a.get('prompt', ''), 150)}"),
                        "statement": stmt if stmt and desc == stmt else "",
                        "infer": {"qid": qid, "option": x["option"], "label": x["label"], "desc": x.get("desc", "")},
                        "about": a.get("about") or {}, "stake": float(a.get("stake") or 0.0),
                        "key": f"{qid}:{x['option']}"})
    return sorted(out, key=lambda x: (-x["stake"], x["key"]))


def _new_proposals(analysis, answers: dict, props: list) -> list:
    """The typed-rule proposals worth reading back: never one whose rows a
    confirmed leave-out already takes out of the same or wider totals (one whose
    typed scope is wider says so in its label), and never a leave-out on a list
    tab (the table another looks its keys up in) that has no money: no total
    counts its rows."""
    from . import rules
    conf = [r for r in rules.confirmed(analysis, answers) if r.kind == "exclude"]
    # a list: a tab joined (either way) to a larger one
    size = {t.tid: t.n_rows for t in analysis.tables}
    looked_up = {x for j in analysis.joins if j.get("band") == "auto" and j["to_table"] != j["from_table"]
                 for x, y in ((j["to_table"], j["from_table"]), (j["from_table"], j["to_table"]))
                 if size.get(x, 0) < size.get(y, 0)}
    out = []
    for c in props:
        r = c["rule"]
        if r.kind != "exclude":
            out.append(c)
            continue
        t = analysis.table(r.table)
        if t.tid in looked_up and money_column(analysis, t) is None and t is not analysis.main_table:
            continue
        idx = {h: j for j, h in enumerate(t.headers)}
        if not r.valid(idx):
            out.append(c)
            continue
        mine = [i for i, row in enumerate(t.rows) if r.matches(row, idx)]
        wider = None
        for x in conf:
            if x.table != r.table or not x.valid(idx) or not mine:
                continue
            if not all(x.matches(t.rows[i], idx) for i in mine):
                continue
            if not x.scope or (r.scope and set(r.scope) <= set(x.scope)):
                wider = False             # a pick already leaves these rows out of the same or wider totals
                break
            wider = x
        if wider is False:
            continue
        # one wider than a pick (every count, not just one total) stays; rules.dress says so in its label
        out.append(c)
    return out


def _hash(parts: list) -> str:
    import hashlib
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:8]


LABEL_MAX = 60


def _rule_label(c: dict) -> str:
    """One rule as one claim: 'Location = Q7 (12 rows)', 'Site = A with Unit = 7
    (3 rows)'. Never cut inside a condition: a label too long keeps its first
    condition and says how many more there are, and a value too long to show is
    counted instead. The description names every value."""
    r = c["rule"]
    tail = f" ({c['rows']:,} row{'s' if c['rows'] != 1 else ''})"
    room = LABEL_MAX - len(tail)

    def cond(p, short=False):
        vals = [_short(v, 200) for v in p.get("values") or []]
        if p.get("op") == "between" and len(p.get("values") or []) >= 2:
            lo, hi = (str(v)[:10] for v in p["values"][:2])
            return (f"{p['col']} {lo}" if lo == hi else f"{p['col']} to {hi}" if not lo else
                    f"{p['col']} from {lo}" if not hi else f"{p['col']} {lo} to {hi}")
        if p.get("op") == "prefix" and vals:
            return f"{p['col']} starts {vals[0]}"
        if p.get("op") == "blank" or not vals:
            return f"{p['col']} blank"
        if short or len(vals) >= 3:
            return f"{p['col']}: {len(vals)} value{'s' if len(vals) != 1 else ''}"
        if len(vals) == 1:
            return f"{p['col']} = {vals[0]}"
        return f"{p['col']} = {vals[0]} or {vals[1]}"

    def fit(head, conds):
        """head, then the conditions joined by 'with', the first alone when they do not fit."""
        more = len(conds) - 1
        extra = f" +{more} more condition{'s' if more > 1 else ''}" if more else ""
        for body in (head + " with ".join(cond(p) for p in conds), head + cond(conds[0]) + extra,
                     head + cond(conds[0], short=True) + extra):
            if len(body) <= room:
                break
        return body
    if r.kind == "map":
        vals = [_short(v, 200) for v in (r.predicate[0].get("values") if r.predicate else [])]
        body = f"{r.values.get('col')} {vals[0]} with {vals[1]} as one" if len(vals) == 2 else ""
        if not body or len(body) > room:
            body = f"{r.values.get('col')}: {len(vals)} values as one"
    elif r.kind == "scale":
        by = float(r.values.get("by") or 0)
        head = f"{r.values.get('col')} divided by {int(by) if by.is_integer() else by}"
        body = fit(head + " where ", r.predicate) if r.predicate else head
    elif r.kind == "fill":
        body = f"A blank {r.values.get('col')} takes the value above"
        if len(body) > room:
            body = "A blank takes the value above"
    elif r.kind == "adjust":
        head = f"{r.values.get('col')} minus {r.values.get('minus')}"
        body = fit(head + " where ", r.predicate) if r.predicate else head
    else:
        body = fit("Only " if r.kind == "filter" else "", r.predicate)
    return body + tail


def _rule_desc(analysis, c: dict, mixed: str | None = None) -> str:
    """What ticking the rule does, with its money, after the rows it names in full:
    'Rows where Location is Q7, A1 or A2: left out of every count and total'. A
    column the owner said changed unit at a date is not summed (mixed: the date)."""
    from .recipes import fmt_money, fmt_num
    from .rules import where
    r = c["rule"]
    f = fmt_money if c["money"] else (lambda x: fmt_num(round(x, 2)))
    rows = f"Rows where {where(r)}: " if r.predicate else ""
    if r.kind == "adjust":
        return rows + f"{r.values.get('col')} becomes {r.values.get('col')} minus {r.values.get('minus')}"
    if mixed and c["col"]:
        return rows + ("left out of every count and total" if r.kind == "exclude" else "as you typed") \
            + f"; {c['col']} is not summed: mixed units before and after {mixed}"
    adds = f"; they add {f(c['sum'])} to {c['col']}" if c["col"] else ""
    if r.kind == "exclude":
        return rows + "left out of every count and total" + adds
    if r.kind == "filter":
        return rows + f"the only rows of {analysis.table(r.table).sheet} counted" + adds
    if r.kind == "map":
        from .rules import map_pairs, map_words
        if len(map_pairs(analysis, r)) > 1:
            # each value paired with its own match: said pair by pair, never 'as one'
            return rows + map_words(analysis, r) + " in every count and total" + (
                f"; together {f(c['sum'])} of {c['col']}" if c["col"] else "")
        return rows + "counted as one in every count and total" + (
            f"; together {f(c['sum'])} of {c['col']}" if c["col"] else "")
    return rows + f"{r.values.get('col')} totals {f(c['total'])} now and {f(c['after'])} after"


def _unmatched_for(analysis, qid: str, ans: dict | None = None) -> dict | None:
    """The unmatched finding an answer was about: the table and column its
    about names, else the one whose column gives the question id, else the first."""
    found = [i for i in analysis.insights if i.get("recipe", "").startswith("unmatched:")]
    ab = (ans or {}).get("about") or {}
    return (next((i for i in found if (i["numbers"]["from_table"], i["numbers"]["from_col"])
                  == (ab.get("table"), ab.get("col"))), None)
            or next((i for i in found if "find_unmatched_" + _slug(i["numbers"]["from_col"]) == qid), None)
            or (found[0] if found else None))


def _parent_about(analysis, qid: str, ans: dict) -> dict:
    """What a follow-up is about: the table and column of the answer that opened it."""
    ab = ans.get("about") or {}
    if ab.get("table") and ab.get("col"):
        return _about(ab["table"], ab["col"], "meaning")
    n = (_unmatched_for(analysis, qid, ans) or {}).get("numbers") or {}
    return _about(n.get("from_table", ""), n.get("from_col", ""), "meaning")


def _suspense_lines(analysis, finding: dict | None = None) -> list:
    """The largest lines sitting in an account missing from the chart: date,
    amount and what the line says, so the owner can say what they are."""
    finding = finding or _unmatched_for(analysis, "")
    if not finding:
        return []
    n = finding["numbers"]
    t = analysis.table(n["from_table"])
    if n["from_col"] not in t.headers:
        return []
    jc = t.headers.index(n["from_col"])
    missing = set(n.get("missing_keys") or [])
    cols = analysis.cols[t.tid]
    date = next((c for c in cols if c.type == "date"), None)
    money = [c for c in cols if c.type == "number" and c.semantic == "metric"]
    pref = ("memo", "desc", "note", "payee", "vendor", "name")
    words = sorted([c for c in cols if c.type == "text" and c.semantic == "dimension" and c.header != n["from_col"]
                    and any(p in c.header.lower() for p in pref)],
                   key=lambda c: next(i for i, p in enumerate(pref) if p in c.header.lower()))
    rows = []
    for r in t.rows:
        if norm_key(r[jc] if jc < len(r) else None) not in missing:
            continue
        amt = max((abs(r[c.j]) for c in money if c.j < len(r) and isinstance(r[c.j], (int, float))
                   and not isinstance(r[c.j], bool)), default=0)
        rows.append((amt, r))
    rows.sort(key=lambda x: -x[0])
    out = []
    for amt, r in rows[:4]:
        d = r[date.j] if date and date.j < len(r) else None
        ds = f"{_MONTHS[d.month - 1]} {d.day}" if hasattr(d, "month") else ""
        what = next((_short(r[c.j], 40) for c in words if c.j < len(r) and r[c.j]), "")
        out.append(" ".join(x for x in (ds + ":" if ds else "", f"${amt:,.2f}", f"({what})" if what else "") if x))
    return out


def _code_pairs(analysis, ans) -> list:
    """(old code, new code, description) where an unmatched code shares its
    description with a code that IS in the reference list, for the unmatched
    finding the answer was about."""
    finding = _unmatched_for(analysis, "", ans)
    if not finding:
        return []
    n = finding["numbers"]
    t = analysis.table(n["from_table"])
    if n["from_col"] not in t.headers:
        return []
    jc = t.headers.index(n["from_col"])
    jd = None
    for fd in analysis.fds.get(t.tid, []):
        if fd["from"] == n["from_col"] and re.search(r"desc|name|product|title", fd["to"], re.I):
            jd = t.headers.index(fd["to"])
            break
    if jd is None:
        return []
    ref = analysis.table(n["to_table"])
    ref_codes = set()
    if n["to_col"] in ref.headers:
        rj = ref.headers.index(n["to_col"])
        ref_codes = {norm_key(r[rj]) for r in ref.rows if rj < len(r)}
    missing = set(n.get("missing_keys") or [])
    desc_to_codes: dict = {}
    first_code: dict = {}
    for r in t.rows:
        c = r[jc] if jc < len(r) else None
        d = r[jd] if jd < len(r) else None
        kc, kd = norm_key(c), norm_key(d)
        if kc is None or kd is None:
            continue
        desc_to_codes.setdefault(kd, set()).add(kc)
        first_code.setdefault(kc, (str(c), str(d)))
    pairs = []
    for kd, codes in desc_to_codes.items():
        olds = [c for c in codes if c in missing]
        news = [c for c in codes if c in ref_codes]
        for o in olds:
            for nw in news:
                if o != nw:
                    pairs.append((first_code[o][0], first_code[nw][0], _short(first_code[o][1], 30)))
    return sorted(pairs)[:6]


def grow_questions(analysis, answers: dict, report: dict, since: str = "", prev: list | None = None) -> list:
    """When a sheet with a brain comes back with more data: questions about what is
    NEW only, each with the answer the brain already suggests. The owner's earlier
    rules cover the rest without asking (ABC rows stay out, credits stay netted)."""
    from .interview import Q
    import difflib
    out = []
    when = f"since {_month(since)}" if since else "since the brain was written"
    # 1. codes that were not there when the owner explained the unmatched ones
    for ins in analysis.insights:
        if not ins.get("recipe", "").startswith("unmatched:"):
            continue
        n = ins["numbers"]
        fc = analysis.col(n["from_table"], n["from_col"])
        if not fc or fc.semantic != "identifier":
            continue          # a new name (a vendor) is asked about once, below, as a new name
        slug = _slug(n["from_col"])
        first = answers.get(f"find_unmatched_{slug}")
        if not isinstance(first, dict):
            continue
        known = set(first.get("keys") or [])
        for r in prev or []:          # the codes the brain had already seen, as written in the file
            if r.get("record") == "insight" and str(r.get("ref", "")).startswith("recipe:unmatched:") \
                    and str(r.get("ref", "")).endswith(":" + n["from_col"]) and "known:" in str(r.get("text", "")):
                known |= {x.strip() for x in str(r["text"]).split("known:", 1)[1].split("|") if x.strip()}
        if not known:
            continue
        for qid, a in answers.items():
            if qid.startswith(f"grow_unmatched_{slug}") and isinstance(a, dict):
                known |= set(a.get("keys") or [])
        new = sorted({str(k) for k in n.get("missing_keys") or []} - known)
        if not new:
            continue
        t = analysis.table(n["from_table"])
        jc = t.headers.index(n["from_col"])
        shown, rows, cat_of = {}, 0, {}
        cat_col = next((c for c in analysis.cols[t.tid] if re.search(r"categ|type|class|group", c.header, re.I)
                        and c.semantic == "dimension"), None)
        for r in t.rows:
            k = norm_key(r[jc] if jc < len(r) else None)
            if k is None:
                continue
            if cat_col is not None and cat_col.j < len(r):
                cat_of.setdefault(k, r[cat_col.j])
            if k in new:
                rows += 1
                shown.setdefault(k, str(r[jc]).strip())
        from .rules import not_items
        fees = not_items(analysis, answers) & known if "not_items" in (first.get("options") or []) else set()
        pre = lambda k: (re.match(r"^([a-z]+)-", k or "") or [None, None])[1]  # noqa: E731
        fee_cats = {cat_of.get(f) for f in fees} - {None}
        fee_pre = {pre(f) for f in fees} - {None}
        like_fee = [k for k in new if cat_of.get(k) in fee_cats or (pre(k) and pre(k) in fee_pre)]
        # a new code whose description is an item the reference list already has: a re-code
        ref_desc = set()
        ref = analysis.table(n["to_table"])
        jd_ref = next((i for i, h in enumerate(ref.headers) if re.search(r"desc|name|product", h, re.I)), None)
        jd = next((i for i, h in enumerate(t.headers) if re.search(r"desc|name|product", h, re.I)), None)
        if jd_ref is not None and jd is not None:
            ref_desc = {norm_key(r[jd_ref]) for r in ref.rows if jd_ref < len(r)} - {None}
        desc_of = {}
        if jd is not None:
            for r in t.rows:
                k = norm_key(r[jc] if jc < len(r) else None)
                if k in new and jd < len(r):
                    desc_of.setdefault(k, norm_key(r[jd]))
        like_recode = [k for k in new if desc_of.get(k) in ref_desc]
        names = ", ".join(shown[k] for k in new[:4])
        q = Q(f"grow_unmatched_{slug}_{_slug('_'.join(new), 20)}", "New codes",
              f"{rows:,} rows {when} have {_a(n['from_col'])} {n['from_col']} that isn't in {n['where']} and was not "
              f"there before: {names}{' and more' if len(new) > 4 else ''}. What are they? Pick all that apply.",
              [{"id": "not_items", "label": "Not real items", "desc": "Codes that are not items"},
               {"id": "old_codes", "label": "Old or changed codes", "desc": "The same thing under a new code"},
               {"id": "missing", "label": "Missing from that list", "desc": "Real items the list should have"}],
              why="New codes that match nothing are where a fee gets priced like a product or a product gets lost.",
              multi=True, kind="definition", priority=1, source="finding",
              fact={"kind": "definition", "class": "data", "depends": [],
                    "statement": f"The newer {n['from_col']} codes not in {n['where']} ({names}) are, per the owner: "
                                 "{answer_labels}."},
              meta={"keys": new,
                    "about": _about(n["from_table"], n["from_col"], "meaning", [shown.get(k, k) for k in new[:4]])})
        if like_fee and len(like_fee) == len(new):
            known_fee = sorted(fees)[0].upper()
            q.recommend, q.recommend_basis = "not_items", (
                f"{'It looks' if len(new) == 1 else 'They look'} like {known_fee}, which the owner said is a fee.")
        elif like_recode and len(like_recode) == len(new):
            q.recommend, q.recommend_basis = "old_codes", (
                "Same description as an item already in the reference list, so likely the same product "
                "under a new code.")
        q.value = 30.0
        out.append(q)
    # 2. new names in a grouping column (a new hotel, a new vendor): new, the same as one, or not real
    for header, vals in (report.get("new_values") or {}).items():
        t, c = next(((t, c) for t in analysis.tables for c in analysis.cols[t.tid]
                     if c.header == header and t is analysis.main_table), (None, None))
        if c is None or c.semantic != "dimension" or c.sensitive:
            continue
        vals = [v for v in vals if v][:3]
        if not vals:
            continue
        rid = _rid(analysis, t.tid, header)
        noun = ((analysis.playbook or {}).get("roles", {}).get(rid or "", {}).get("entity") or header).lower()
        others = [str(k) for k, _ in c.top if str(k)[:40] not in vals]
        match = None
        for v in vals:
            close = difflib.get_close_matches(v, others, n=1, cutoff=0.85)
            if close:
                match = close[0]
                break
        rows = sum(n_ for k, n_ in c.top if str(k)[:40] in vals)
        listed = _join([f'"{v}"' for v in vals])
        elsewhere = next((i["numbers"]["where"] for i in analysis.insights if i.get("recipe", "").startswith("unmatched:")
                          and i["numbers"].get("from_col") == header
                          and any(norm_key(v) in set(i["numbers"].get("missing_keys") or []) for v in vals)), "")
        opts = [{"id": "real", "label": f"A new {noun}", "desc": "It belongs in the totals"}]
        if match:
            opts.append({"id": "alias", "label": f"Same as {match[:30]}", "desc": "A new spelling of an existing one"})
        opts.append({"id": "leave_out", "label": f"Not a real {noun}",
                     "desc": "Internal or a test, left out of totals"})
        q = Q(f"grow_new_{_slug(header)}_{_slug('_'.join(vals), 20)}", header_words("New", noun),
              f"{listed} {'is' if len(vals) == 1 else 'are'} new in {header} {when} ({rows:,} rows)"
              + (f" and not in {elsewhere} either" if elsewhere else "")
              + f". What {'is it' if len(vals) == 1 else 'are they'}?",
              opts, why=f"A new {noun} changes every total and share by {noun}.",
              kind="definition", priority=1, source="finding",
              fact={"kind": "definition", "class": "data", "depends": [],
                    "statement": f"{listed} in {header}, new {when}, is per the owner: {{answer_labels}}.",
                    "statements": {
                        "real": f"{listed} in {header} {'is a new' if len(vals) == 1 else 'are new'} {noun}"
                                f"{'' if len(vals) == 1 else 's'} ({when}) and "
                                f"{'belongs' if len(vals) == 1 else 'belong'} in the totals, per the owner.",
                        "alias": f"{listed} in {header} is the same as {match}, per the owner.",
                        "leave_out": f"{listed} in {header} is not a real {noun}, per the owner."}},
              meta={"exclude": {"table": t.tid, "col": header, "values": vals, "options": ["leave_out"]},
                    "about": _about(t.tid, header, "meaning", vals)})
        if match:         # a near spelling is evidence; "it looks like the others" was never checked
            q.recommend, q.recommend_basis = "alias", f"It is spelled almost like {match}."
        q.value = 25.0
        out.append(q)
    return out[:4]


def plural_noun(noun: str) -> str:
    from .recipes import plural
    return plural(noun)
