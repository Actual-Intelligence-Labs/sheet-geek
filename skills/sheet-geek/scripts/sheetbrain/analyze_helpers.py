"""Module-level helpers of analyze.py: periods, cycles, numbering and how values are written.
Kept in their own file so each file stays under 256 KiB, the most the plugin directory's checker reads;
analyze.py imports them back, so `from .analyze import _period_kind` still works."""
from __future__ import annotations

import os
import re
from collections import Counter

from . import profile as profile_mod
from . import recipes as recipes_mod
from .profile import _is_num

_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
_AS_RECORDED = ("product_check:",)      # recipes that read values before the owner's unit rules


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
