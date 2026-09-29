"""Deterministic profiling: column types and stats, keys, which columns
determine others, and joins across tables and files. No model involved.
"""
from __future__ import annotations

import datetime as dt
import re
from collections import Counter
from dataclasses import dataclass, field

from . import brainzip

_ID_HEADER = re.compile(r"(\bid\b|#|\bno\b\.?|\bnumber\b|\bnum\b|\bcode\b|\bsku\b|\bkey\b|\bref\b|"
                        r"\bupc\b|\bgtin\b|\bacct\b|\baccount\b)", re.I)
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[a-z]{2,}$", re.I)
_PHONE = re.compile(r"^\+?[\d\s().\-]{7,20}$")
_PERSON_HEADER = re.compile(r"\b(first|last|full)?\s*name\b|\bcontact\b|\bemployee\b|\bowner\b|"
                            r"\brep\b|\bmanager\b|\bbuyer\b|\bperson\b", re.I)
MAX_EXACT_DISTINCT = 1_000_000


def norm_key(v):
    """Normalize a value for joining: trim, casefold, drop leading zeros."""
    if v is None:
        return None
    if isinstance(v, bool):
        return str(v).lower()
    if isinstance(v, float):
        if v != v:  # NaN
            return None
        return str(int(v)) if v.is_integer() else repr(round(v, 6))
    if isinstance(v, int):
        return str(v)
    if isinstance(v, (dt.datetime, dt.date)):
        return v.isoformat()[:10]
    s = " ".join(str(v).split()).casefold().rstrip(".,;")
    if s.isdigit():
        s = s.lstrip("0") or "0"
    return s or None


def shape(v) -> str:
    s = str(v)[:24]
    out = []
    for ch in s:
        if ch.isdigit():
            c = "9"
        elif ch.isalpha():
            c = "A" if ch.isupper() else "a"
        else:
            c = ch
        if not out or out[-1] != c or c not in "9Aa":
            out.append(c)
    return "".join(out)


def _clean_sample(v) -> str:
    if isinstance(v, (dt.datetime, dt.date)):
        return v.isoformat()[:10]
    if isinstance(v, float):
        return f"{v:.4g}" if abs(v) < 1e6 else f"{v:,.0f}"
    s, _ = brainzip.clean_text(str(v))
    s = re.sub(r"\s+", " ", s).strip()
    return s[:40]


@dataclass
class Col:
    table: str
    header: str
    j: int
    type: str = "empty"          # number date text bool empty
    semantic: str = "text"       # identifier metric dimension temporal text flag
    count: int = 0
    nulls: int = 0
    distinct: int = 0
    distinct_capped: bool = False
    top: list = field(default_factory=list)
    min: object = None
    max: object = None
    sum: float = 0.0
    negatives: int = 0
    zeros: int = 0
    integers: bool = True
    numeric_text: int = 0
    avg_len: float = 0.0
    shapes: list = field(default_factory=list)
    samples: list = field(default_factory=list)
    unique: bool = False
    formula_share: float = 0.0
    is_email: bool = False
    is_phone: bool = False
    is_person: bool = False
    codes: bool = False
    retyped: int = 0             # cells read from text (text dates, '$9.50'), see Table.retyped
    retyped_kind: str = ""       # date | number
    retyped_examples: list = field(default_factory=list)
    retyped_format: str = ""     # the text date format read, e.g. '%m/%d/%Y'
    ambiguous_dates: int = 0     # text dates that read both day first and month first, left as text
    # spellings that differ only in capitals or spaces: [(usual spelling, [(variant, n), ...])]
    variants: list = field(default_factory=list)
    counter: Counter = field(default_factory=Counter, repr=False)

    @property
    def blank_rate(self) -> float:
        tot = self.count + self.nulls
        return self.nulls / tot if tot else 0.0

    @property
    def sensitive(self) -> bool:
        return self.is_email or self.is_phone or self.is_person

    def to_dict(self, samples: bool = True) -> dict:
        d = {
            "header": self.header, "type": self.type, "semantic": self.semantic,
            "count": self.count, "blank_rate": round(self.blank_rate, 4),
            "distinct": self.distinct, "unique": self.unique,
        }
        if self.distinct_capped:
            d["distinct_capped"] = True
        if self.type == "number":
            d.update({"min": _num(self.min), "max": _num(self.max), "sum": _num(self.sum),
                      "negatives": self.negatives, "integers": self.integers})
        if self.type == "date":
            d.update({"min": _iso(self.min), "max": _iso(self.max)})
        if self.numeric_text:
            d["numbers_stored_as_text"] = self.numeric_text
        if self.retyped:
            d["read_from_text"] = {"kind": self.retyped_kind, "cells": self.retyped}
        if self.formula_share:
            d["formula_share"] = round(self.formula_share, 3)
        if self.type == "text" and not self.sensitive and self.distinct <= 50:
            d["top"] = [[_clean_sample(k), n] for k, n in self.top[:8]]
        if self.shapes:
            d["shapes"] = [s for s, _ in self.shapes[:3]]
        if samples and not self.sensitive:
            d["samples"] = self.samples[:5]
        for flag in ("is_email", "is_phone", "is_person", "codes"):
            if getattr(self, flag):
                d[flag] = True
        return d


def _num(v):
    if v is None:
        return None
    if isinstance(v, float):
        return round(v, 4)
    return v


def _is_num(v) -> bool:
    """A number cell (an int or a float, never True or False). The one copy every
    module reads."""
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _iso(v):
    return v.isoformat()[:10] if isinstance(v, (dt.datetime, dt.date)) else v


def profile_column(table, j: int, formula_cells: int = 0) -> Col:
    header = table.headers[j]
    c = Col(table.tid, header, j)
    vals = table.column(j)
    n_num = n_date = n_text = n_bool = 0
    total_len = 0
    shapes: Counter = Counter()
    samples: list = []
    seen_samples = set()
    counter: Counter = Counter()
    spellings: Counter = Counter()
    first_by_key: dict = {}
    emails = phones = 0
    for v in vals:
        if v is None or (isinstance(v, str) and v.strip() == ""):
            c.nulls += 1
            continue
        c.count += 1
        if isinstance(v, bool):
            n_bool += 1
        elif isinstance(v, (int, float)):
            n_num += 1
            fv = float(v)
            if c.min is None or fv < c.min:
                c.min = v
            if c.max is None or fv > c.max:
                c.max = v
            c.sum += fv
            if fv < 0:
                c.negatives += 1
            if fv == 0:
                c.zeros += 1
            if isinstance(v, float) and not v.is_integer():
                c.integers = False
        elif isinstance(v, (dt.datetime, dt.date)):
            n_date += 1
            if c.min is None or v < c.min:
                c.min = v
            if c.max is None or v > c.max:
                c.max = v
        else:
            n_text += 1
            s = str(v)
            total_len += len(s)
            if re.fullmatch(r"\s*-?\$?[\d,]+(\.\d+)?\s*", s):
                c.numeric_text += 1
            if _EMAIL.match(s.strip()):
                emails += 1
            elif _PHONE.match(s.strip()) and sum(ch.isdigit() for ch in s) >= 7:
                phones += 1
            if len(spellings) < MAX_EXACT_DISTINCT:
                spellings[s] += 1
        shapes[shape(v)] += 1
        k = norm_key(v)
        if k is not None and len(counter) < MAX_EXACT_DISTINCT:
            counter[k] += 1
            first_by_key.setdefault(k, v)
        elif k is not None:
            c.distinct_capped = True
        if len(samples) < 8 and k not in seen_samples:
            seen_samples.add(k)
            samples.append(_clean_sample(v))
    c.counter = counter
    c.variants = _variants(spellings)
    rt = (getattr(table, "retyped", None) or {}).get(j)
    if rt:
        here = set(getattr(table, "row_index", None) or [])       # a filtered view counts its own rows
        c.retyped = sum(1 for r in rt["rows"] if r in here) if here else len(rt["rows"])
        c.retyped_kind = rt["kind"]
        c.retyped_examples = [str(x) for x in rt["examples"]]
        c.retyped_format = rt.get("format") or ""
        if rt.get("ambiguous"):
            c.ambiguous_dates = sum(1 for r in rt["text_rows"] if r in here) if here else len(rt["text_rows"])
    c.distinct = len(counter)
    c.top = [(first_by_key[k], n) for k, n in counter.most_common(10)]
    c.shapes = shapes.most_common(3)
    c.samples = samples
    c.avg_len = total_len / n_text if n_text else 0.0
    c.unique = c.count >= 2 and c.nulls == 0 and c.distinct == c.count and not c.distinct_capped
    tot = c.count
    if tot == 0:
        c.type = "empty"
    elif n_bool / tot >= 0.9:
        c.type = "bool"
    elif n_num / tot >= 0.9:
        c.type = "number"
    elif n_date / tot >= 0.9:
        c.type = "date"
    else:
        c.type = "text"
    if c.type == "text":
        lowered = {str(k) for k in counter}
        if lowered and lowered <= {"y", "n", "yes", "no", "true", "false", "0", "1", "x"}:
            c.type = "bool"
    c.is_email = n_text > 0 and emails / max(1, n_text) >= 0.7
    c.is_phone = n_text > 0 and phones / max(1, n_text) >= 0.7 and not c.is_email
    hdr = split_camel(header)
    c.is_person = bool(_PERSON_HEADER.search(hdr)) and c.type == "text" and not c.is_email \
        and not _ID_HEADER.search(hdr) and c.avg_len < 40
    c.formula_share = formula_cells / tot if tot else 0.0
    c.semantic = _semantic(c, table.n_rows)
    return c


def _variants(spellings: Counter) -> list:
    """Values written more than one way that differ only in capitals or spaces
    ('North', 'north', 'North '): the usual spelling and every other one with its count."""
    groups: dict = {}
    for s, n in spellings.items():
        groups.setdefault(" ".join(s.split()).casefold(), []).append((s, n))
    out = []
    for forms in groups.values():
        if len(forms) < 2:
            continue
        forms.sort(key=lambda x: (-x[1], x[0]))
        out.append((forms[0][0], forms[1:]))
    out.sort(key=lambda x: -sum(n for _, n in x[1]))
    return out


def split_camel(h: str) -> str:
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", h)


_NOT_ID_WORDS = re.compile(r"\b(name|names|description|desc|title|type|category|class|status|"
                           r"notes?|memo|comment)\b", re.I)


def is_id_header(h: str) -> bool:
    s = split_camel(h)
    return bool(_ID_HEADER.search(s)) and not _NOT_ID_WORDS.search(s)


# a code word that ends the header or comes before an ID word: 'Dept', 'Reason Code', 'Class No',
# never 'Group Size'
_CODE_HEADER = re.compile(r"\b(code|codes|type|class|status|reason|category|dept|department|division|"
                          r"group|kind)( (id|no|num|number|code|#))?$")
_MONEY_COUNT = re.compile(r"\b(pay|gross|net|amount|amt|price|rate|cost|fee|fees|salary|wage|wages|total|qty|"
                          r"quantity|size|hours|hrs|units|count)\b")
_METRIC_LEX: dict = {}


def _plain_header(h: str) -> str:
    s = split_camel(str(h)).lower().replace("#", " # ")
    return " ".join(re.sub(r"[^a-z0-9#%$]+", " ", s).split())


def _metric_header(h: str) -> bool:
    """The header names an amount or a count: a money or count word, or a phrase
    from any playbook's metric lexicon."""
    s = _plain_header(h)
    if _MONEY_COUNT.search(s):
        return True
    from . import detect
    key = detect.PLAYBOOK_DIR
    if key not in _METRIC_LEX:
        _METRIC_LEX[key] = [p for pb in detect.load_playbooks().values() for r in pb.get("roles", {}).values()
                            if r.get("kind") == "metric" for p in r.get("headers", [])]
    return detect._lex_match(detect.norm_header(h), _METRIC_LEX[key]) > 0


def _int_codes(c: Col, id_header: bool) -> bool:
    """Whole numbers that name things rather than count them: 2 to 30 values, each
    on 10 or more rows, with a code-like header, or every value the same width of
    3 or more digits under a header that names no amount or count."""
    if not c.integers or c.min is None or float(c.min) < 0 or not (2 <= c.distinct <= 30) \
            or c.count < 10 * c.distinct:
        return False
    if id_header or _CODE_HEADER.search(_plain_header(c.header)):
        return True
    widths = {len(k) for k in c.counter}
    return len(widths) == 1 and widths.pop() >= 3 and not _metric_header(c.header)


def _semantic(c: Col, n_rows: int) -> str:
    if c.type == "date":
        return "temporal"
    if c.type == "bool":
        return "flag"
    if c.type == "empty":
        return "text"
    ratio = c.distinct / c.count if c.count else 0
    id_header = is_id_header(c.header)
    if c.type == "number":
        year_like = c.integers and c.min is not None and 1990 <= float(c.min) and float(c.max) <= 2100
        if year_like and c.distinct <= 60:
            return "temporal"
        counts = re.search(r"\b(number|no\.?|#|count|num) of\b|\bnumber of\b", c.header, re.I)
        if _int_codes(c, id_header) and not counts:
            c.codes = True
            return "identifier"          # reason codes, departments, GL numbers: never summed
        if c.integers and id_header and not counts and c.min is not None and float(c.min) >= 0:
            return "identifier"          # account codes, store numbers, invoice numbers
        return "metric"
    # text
    if c.is_email or (id_header and c.avg_len <= 30):
        return "identifier"      # item codes, SKUs, invoice numbers repeat across lines and are still IDs
    short = c.avg_len <= 6
    upperish = sum(1 for k, _ in c.top if isinstance(k, str) and (k.isupper() or k.isdigit()
                                                                   or re.fullmatch(r"[A-Z0-9\-_/.]+", k)))
    if 2 <= c.distinct <= 60 and short and c.top and upperish >= 0.6 * len(c.top):
        c.codes = True
    if ratio >= 0.9 and c.count >= 20 and c.avg_len <= 24 and not c.is_person:
        return "identifier"
    if c.avg_len > 40:
        return "text"
    if c.distinct <= max(60, int(0.05 * n_rows)):
        return "dimension"
    return "text" if c.avg_len > 24 else "dimension"


# --------------------------------------------------------------------------
# keys and functional dependencies
# --------------------------------------------------------------------------
# a header that says when a row was written, not what it is about: 'Submitted', 'Uploaded At', never 'Entered By'
_AUDIT_HEADER = re.compile(r"^(?:date |time )?(?:submitted|entered|created|uploaded|loaded|imported|modified|updated|"
                           r"synced|exported|timestamp)(?: (?:at|on|date|time|timestamp|ts))?$")


def is_audit(table, c) -> bool:
    """A column that records when a row was written (an upload or entry time):
    datetimes with a time of day on at least half the rows, or a header that
    says so. Never part of what makes a row one thing."""
    if _AUDIT_HEADER.match(_plain_header(c.header)):
        return True
    if c.type != "date":
        return False
    stamps = [v for v in table.column(c.j) if isinstance(v, dt.datetime)]
    return len(stamps) >= 5 and sum(1 for v in stamps if (v.hour, v.minute, v.second) != (0, 0, 0)) \
        >= 0.5 * len(stamps)


def _numbers_as_ids(c: Col) -> bool:
    """Whole numbers nearly all different under a header that names no amount or
    count: document numbers read as a metric only because they are numbers."""
    return c.type == "number" and c.integers and bool(c.count) and c.distinct / c.count >= 0.9 \
        and not _metric_header(c.header)


KEY_SHARE = 0.99          # a near key names this share of the rows or more, one row per value


def composite_key(table, cols: list) -> list:
    """Smallest set of 1 to 3 columns that is unique over the data rows (or, when
    none is, on 99% of them), leaving out columns that record when a row was
    written. A one-column list has no key worth a note."""
    return key_info(table, cols)["cols"]


def key_info(table, cols: list) -> dict:
    """{cols, extra}: the smallest set of 1 to 3 columns unique over the data rows
    (extra 0) or, when no set of that size is, unique on 99% of the rows or more
    (extra: the rows over one per key value, the exceptions). A near key of one
    column must read as an ID. At each size an exact key comes before a near one.
    {cols: []} when none comes that close."""
    import itertools
    none = {"cols": [], "extra": 0}
    if len(cols) < 2:
        return none
    cols = [c for c in cols if not is_audit(table, c)]
    singles = [c for c in cols if c.unique and c.semantic != "metric" and c.type != "bool"]
    if singles:
        best = sorted(singles, key=lambda c: (c.semantic != "identifier", c.j))[0]
        return {"cols": [best.header], "extra": 0}
    cands = [c for c in cols if c.type in ("text", "number", "date") and c.semantic != "metric"
             and c.distinct >= 2 and c.nulls == 0]
    cands.sort(key=lambda c: -c.distinct)
    cands = cands[:6]
    n = table.n_rows
    limit = int((1 - KEY_SHARE) * n)
    one = [c for c in cols if c.type in ("text", "number") and c.nulls == 0 and c.count - c.distinct <= limit
           and (c.semantic == "identifier" or is_id_header(c.header) or _numbers_as_ids(c))]
    if one:
        c = max(one, key=lambda c: (c.semantic == "identifier", c.distinct, -c.j))
        return {"cols": [c.header], "extra": c.count - c.distinct}
    for size in (2, 3):
        near = None
        for combo in itertools.combinations(cands, size):
            reach = 1
            for c in combo:
                reach *= c.distinct
            if reach < n:
                continue
            seen, extra = set(), 0
            for r in table.rows:
                key = tuple(norm_key(r[c.j] if c.j < len(r) else None) for c in combo)
                if key in seen:
                    extra += 1
                    if extra > limit:
                        break
                seen.add(key)
            if not extra:
                return {"cols": [c.header for c in combo], "extra": 0}
            if extra <= limit and (near is None or extra < near[1]):
                near = (combo, extra)
        if near is not None:
            return {"cols": [c.header for c in near[0]], "extra": near[1]}
    return none


NEAR_KEY = 0.15           # a near key leaves at most this share of rows over one per key value


def near_key(table, cols: list) -> dict | None:
    """The fewest columns (1 to 3) that are one per row except for a few rows,
    leaving out columns that record when a row was written: {cols: [headers],
    extra: rows over one per key value, groups: [[row positions] sharing a key]}.
    None when a column set is already unique at the smallest size that comes
    close, or when none does. At most 8 candidate columns (92 sets), smallest
    sets first."""
    import itertools
    n = table.n_rows
    if n < 20 or len(cols) < 2:
        return None
    cols = [c for c in cols if not is_audit(table, c)]
    if any(c.unique and c.semantic != "metric" and c.type != "bool" for c in cols):
        return None
    cands = [c for c in cols if c.type in ("text", "number", "date") and c.nulls == 0 and c.distinct >= 2
             and not c.distinct_capped and (c.semantic != "metric" or _numbers_as_ids(c))]
    cands = sorted(cands, key=lambda c: -c.distinct)[:8]
    for size in (1, 2, 3):
        best = None
        for combo in itertools.combinations(cands, size):
            reach = 1
            for c in combo:
                reach *= c.distinct
            if reach < (1 - NEAR_KEY) * n:
                continue          # too few combinations to come close to one per row
            limit = min(NEAR_KEY * n, best[0] - 1 if best else n)
            if size == 1 and n - combo[0].distinct > limit:
                continue          # a single column's repeats are known from its profile
            groups: dict = {}
            extra = 0
            for i, r in enumerate(table.rows):
                g = groups.setdefault(tuple(norm_key(r[c.j] if c.j < len(r) else None) for c in combo), [])
                extra += bool(g)
                g.append(i)
                if extra > limit:
                    break         # already repeats more than a near key may, or than the best so far
            if extra <= limit:
                best = (extra, combo, groups)
                if not extra:
                    break         # unique: a key, nothing to look for
        if best is None:
            continue
        extra, combo, groups = best
        if extra == 0:
            return None           # a key already: nothing repeats
        if extra <= NEAR_KEY * n:
            return {"cols": [c.header for c in combo], "extra": extra,
                    "groups": [g for g in groups.values() if len(g) > 1]}
    return None


def functional_deps(table, cols: list, max_rows: int = 200_000) -> list:
    """A determines B (each A value maps to one B value, <= 1% exceptions).
    Only non-unique A columns (a unique column trivially determines all)."""
    rows = table.rows[:max_rows]
    lhs = [c for c in cols if c.semantic in ("identifier", "dimension") and not c.unique
           and 2 <= c.distinct <= 100_000 and not c.sensitive]
    rhs = [c for c in cols if c.semantic in ("dimension", "text", "identifier")
           and 2 <= c.distinct and c.type == "text" and not c.sensitive]
    lhs.sort(key=lambda c: -c.distinct)
    out = []
    for a in lhs[:8]:
        for b in rhs[:14]:
            if a is b or b.distinct > a.distinct:
                continue
            m: dict = {}
            bad = set()
            for r in rows:
                ka = norm_key(r[a.j] if a.j < len(r) else None)
                kb = norm_key(r[b.j] if b.j < len(r) else None)
                if ka is None or kb is None:
                    continue
                prev = m.setdefault(ka, kb)
                if prev != kb:
                    bad.add(ka)
            if m and len(bad) / len(m) <= 0.01 and len(m) > b.distinct:
                out.append({"from": a.header, "to": b.header, "exceptions": len(bad),
                            "groups": len(m)})
    return out


# --------------------------------------------------------------------------
# joins across tables and files
# --------------------------------------------------------------------------
_SYN = {
    "sku": "item", "product": "item", "item number": "item", "item no": "item", "item #": "item",
    "item code": "item", "product code": "item", "supplier": "vendor", "distributor": "vendor",
    "e-mail": "email", "email address": "email", "contact email": "email", "customer": "client",
    "acct": "account", "gl": "account", "store": "location", "site": "location", "property": "location",
}


def header_tokens(h: str) -> set:
    s = split_camel(h).lower().strip()
    s = _SYN.get(s, s)
    toks = set(re.findall(r"[a-z0-9]+|#", s))
    return {(_SYN.get(t, t)) for t in toks} - {"the", "of", "name", "no", "number", "#", "id", "code"} \
        or toks


def header_sim(a: str, b: str) -> float:
    ta, tb = header_tokens(a), header_tokens(b)
    if not ta or not tb:
        return 0.0
    if a.strip().lower() == b.strip().lower() or ta == tb:
        return 1.0
    return len(ta & tb) / len(ta | tb)


def _joinable(c: Col) -> bool:
    if c.distinct < 3 or c.type in ("bool", "empty", "date"):
        return False
    if c.type == "number":
        return c.semantic == "identifier"
    return c.semantic in ("identifier", "dimension") and c.avg_len <= 60


def find_joins(tables: list, cols_by_table: dict, file_of: dict) -> list:
    out = []
    items = [(t, c) for t in tables for c in cols_by_table[t.tid] if _joinable(c)]
    for i in range(len(items)):
        ta, ca = items[i]
        for k in range(i + 1, len(items)):
            tb, cb = items[k]
            if ta.tid == tb.tid:
                continue
            if (ca.type == "number") != (cb.type == "number") and not (
                    ca.semantic == "identifier" and cb.semantic == "identifier"):
                continue
            sa, sb = ca.counter, cb.counter
            if not sa or not sb:
                continue
            small, big = (sa, sb) if len(sa) <= len(sb) else (sb, sa)
            shared = sum(1 for key in small if key in big)
            if shared < 3:
                continue
            a_in_b = sum(1 for key in sa if key in sb) / len(sa)
            b_in_a = sum(1 for key in sb if key in sa) / len(sb)
            hs = header_sim(ca.header, cb.header)
            best = max(a_in_b, b_in_a)
            if best < 0.2 or (hs < 0.3 and best < 0.5):
                continue
            # orient many -> one: toward the unique side when exactly one side is unique and it covers most
            # of the many side's rows (or the two value sets nearly agree), so the values a fact column has
            # and its lookup lacks surface as unmatched; else the side whose values are (nearly) all found
            # in the other, so a short unique list inside a longer column (a few budgeted categories) is a
            # subset of it, not a lookup the column is missing from
            a_first = a_in_b >= b_in_a
            if ca.unique != cb.unique:
                many, one = (ca, cb) if cb.unique else (cb, ca)
                many_cov = sum(n for key, n in many.counter.items() if key in one.counter) / max(1, many.count)
                if many_cov >= 0.6 or (a_in_b >= 0.8 and b_in_a >= 0.8):
                    a_first = cb.unique          # many side first, unique side second
            if a_first:
                src_t, src_c, dst_t, dst_c, cont = ta, ca, tb, cb, a_in_b
            else:
                src_t, src_c, dst_t, dst_c, cont = tb, cb, ta, ca, b_in_a
            rows_cov = (sum(n for key, n in src_c.counter.items() if key in dst_c.counter)
                        / max(1, src_c.count))
            if rows_cov >= 0.95 and (hs >= 0.3 or dst_c.unique or cont >= 0.98):
                band = "auto"
            elif hs >= 0.8 and dst_c.unique and rows_cov >= 0.6:
                band = "auto"      # same name, a real lookup key: unmatched rows are a finding, not a doubt
            elif rows_cov >= 0.2:
                band = "ask"
            else:
                band = "none"
            if band == "none":
                continue
            out.append({
                "from_table": src_t.tid, "from_col": src_c.header,
                "to_table": dst_t.tid, "to_col": dst_c.header,
                "from_file": file_of[src_t.tid], "to_file": file_of[dst_t.tid],
                "values_matched": round(cont, 4), "rows_matched": round(rows_cov, 4),
                "to_unique": dst_c.unique, "header_similarity": round(hs, 2),
                "shared_values": shared, "band": band,
                "cross_file": file_of[src_t.tid] != file_of[dst_t.tid],
            })
    # keep the strongest join per table pair and column
    out.sort(key=lambda j: (-j["rows_matched"], -j["header_similarity"]))
    seen = set()
    kept = []
    for j in out:
        key = (j["from_table"], j["from_col"], j["to_table"])
        if key in seen:
            continue
        seen.add(key)
        kept.append(j)
    return kept
