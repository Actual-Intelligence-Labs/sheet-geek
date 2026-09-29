"""The private-remarks filter. Runs on every free-text answer BEFORE anything
is stored or written: text inside a workbook cannot be scrubbed once a
spreadsheet app has re-saved it.

Code rules first. A model may only move a sentence toward private, never back.
Only the user releases a private remark into the file.

Sentences are cut one way everywhere (the notes, the closer, this filter and
the rule reader): never after an abbreviation or inside a column name that has
a period in it ('Adj. Cost'). A sentence is classified whole, so a remark about
a deal keeps its own tail ('...; no claim filed yet'); only a clause that says
how to treat rows is taken out of a sentence about a deal, and it goes in the
file.
"""
from __future__ import annotations

import re

CONFIDENTIAL = re.compile(r"\b(between us|off the record|don'?t (share|tell|repeat)|confidential|"
                          r"private(ly)?|keep (this|it) quiet|not for (the file|sharing)|entre nous|"
                          r"hush)\b", re.I)
# a remark about how a person behaves; 'trust' alone is money handling ('a trust account'), not a remark
EVALUATIVE = re.compile(r"\b(pads?|padding|lazy|slow|unreliable|lying|liar|lies|shady|sketchy|"
                        r"rounds? up|cheap(skate)?|difficult|can'?t be trusted|cannot be trusted|"
                        r"(?:don'?t|do not|never|can'?t|cannot|won'?t) trust|untrustworthy|"
                        r"incompetent|sloppy|flaky|screws? up|messes? up|rips? (us )?off|overcharges? us|"
                        r"idiot|useless|annoying|hates?|drunk|always late)\b", re.I)
HR_DEAL = re.compile(r"\b(fired|firing|let go|leaving|quitting|quit|resign(ing|ed)?|lawsuit|sued|"
                     r"su(e|ing)|divorce|sick|medical|pregnan\w*|raise|salary|bonus|poach(ing)?|"
                     r"about to (churn|leave|cancel)|going to (churn|leave|cancel)|"
                     r"bankrupt\w*|layoffs?|acquisition|acquir\w+|merger)\b", re.I)
# 'churn' is also a model's line item ('Churn rate is 2.5%'): a remark only with a person, client or
# vendor as its subject ('the client will churn', 'they are churning')
CHURN = re.compile(r"\bchurn(?:s|ed|ing)?\b", re.I)
_PERSON_SUBJECT = re.compile(r"\b(he|she|they|him|her|them|guy|client|clients|customer|customers|vendor|vendors|"
                             r"rep|member|members|tenant|tenants|patient|patients)\b", re.I)
# the words of a deal. A sentence is a business term only when it also states the term itself
# (an amount, a percent), so 'fees never count in rebate math' says how to treat rows, not a deal
COMMERCIAL = re.compile(r"\b(cost plus|markup|mark-up|margin|rebate|kickback|allowance|net price|"
                        r"contract price|our price|we pay|discount of|\d+(\.\d+)?\s*%\s*(off|rebate)|"
                        r"per case through|terms? (are|is))\b", re.I)
_AMOUNT = re.compile(r"\$\s?\d|\d\s*%|\bpercent\b|\b\d+(?:\.\d+)?\s*(?:cents?|dollars?)\b|\bcost\s+plus\s+\d",
                     re.I)
# how a column's numbers are written ('Rate % is in percent points', '2.0 means 2%'): the numbers
# are examples of the unit, not the terms of a deal
_UNIT_CONVENTION = re.compile(r"\b(?:is|are)\s+(?:(?:given|stated|entered|kept|written|shown)\s+)?in\s+"
                              r"(?:\w+\s+){0,2}?(?:points?|percent|cents|dollars|thousands|millions|units|"
                              r"decimals?|fractions?)\b|\b\d+(?:\.\d+)?\s+means\s+\d+(?:\.\d+)?\s*%?", re.I)
# a clause that says how to treat rows: counted in or out, compared, netted
_TREATMENT = re.compile(r"\b(?:counts?|counted|counting)\s+(?:\w+\s+){0,2}?(?:in|toward|towards|as|against)\b|"
                        r"\b(?:(?:do|does|did|should|must)(?:n'?t|\s+not)|never|not)\s+count|"
                        r"\bleav(?:e|es|ing)\s+(?:[\w'&/.-]+\s+){0,6}?out\b|\bleft\s+out\b|\bexclud\w*|"
                        r"\binclud\w*|\bignor\w*|\bcompar\w*|\bnett?(?:ed|ing)?\s+(?:them|it|out|against|off)\b|"
                        r"\bnetted\b|\bkeep\s+(?:\w+\s+){0,3}?(?:in|out)\b|\bkept\s+(?:in|out)\b", re.I)
_CLAUSE = re.compile(r";\s*|,\s*but\s+|\s+but\s+", re.I)
_NAME = re.compile(r"\b[A-Z][a-z]{2,}\b")
_STOP_CAPS = {"The", "This", "That", "These", "Those", "Our", "We", "They", "It", "Its", "And", "But",
              "Yes", "No", "Not", "All", "Any", "Some", "Each", "Every", "When", "If", "For", "From",
              "Per", "Only", "Always", "Never", "Also", "Just", "One", "Two", "Three", "January",
              "February", "March", "April", "May", "June", "July", "August", "September", "October",
              "November", "December", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
              "Saturday", "Sunday", "Q1", "Q2", "Q3", "Q4", "Unit", "Price", "Vendor", "Item",
              "Total", "Qty", "Invoice", "Location", "Store", "Hotel", "Excel", "Sheet", "Tab"}

# a sentence that leans on the one before it for its subject ('On their rows, ...', 'That is not a typo')
ANAPHOR = re.compile(r"^\W*(?:(?:(?:on|in|for|of|with|from|at|by|to|and|but|so|also|all|both|each|none|some|most|"
                     r"any|many|few|one)\s+(?:of\s+)?)?(?:their|those|these|they|them|it|its|this|that|there|his|"
                     r"her|such)\b|both\b(?!\s+(?:\S+\s+){0,2}?(?:and|&)\s))", re.I)
# a sentence whose object points back ('Drop them and keep the originals', 'Leave those out'): an order about
# what the sentence before named, so it stays with it
POINTS_BACK = re.compile(r"^\W*(?:please\s+|so\s+|then\s+|just\s+)?(?:drop|keep|leave|remove|exclude|ignore|count|"
                         r"combine|merge|treat|use|add|subtract|divide|include|move|delete|take|put|net|split|skip|"
                         r"mark|fix|check|sum)\s+(?:all\s+|both\s+|only\s+)?(?:of\s+)?"
                         r"(?:them|those|these|it|this|that)\b", re.I)
# a sentence that says the owner is unsure: it stays a note of its own, so the doubt never spreads
DOUBT = re.compile(r"\b(?:not\s+sure|unsure|no\s+idea|i\s+think|i\s+guess|i\s+believe|maybe|probably|perhaps|"
                   r"might|don'?t\s+know|do\s+not\s+know|can'?t\s+say|not\s+certain)\b", re.I)

_ABBREV = re.compile(r"(?:\b(?:e\.g|i\.e|etc|vs|approx|incl|excl|est|avg|no|nos|st|inc|co|ltd|dept|mr|mrs|ms|dr|"
                     r"jr|sr)|(?<![A-Za-z.])(?:[A-Za-z]\.)+[A-Za-z])\.$", re.I)     # 'approx.', 'U.S.'
_INITIAL_NEXT = re.compile(r"[A-Za-z]\.(?:\s|$)")                                     # 'U. S.': the next initial


def sentence_spans(text: str, headers=(), cut_before_lowercase: bool = False) -> list:
    """[(start, end)] of each sentence in the text, spaces trimmed. Never cuts
    after an abbreviation ('approx.', 'U.S.'), before a lowercase word (unless
    cut_before_lowercase, for reading rules), or inside a column name that has
    a period in it ('Adj. Cost'). A line break always ends a sentence, and a
    lone capital with a period ('about A. B is a fee') ends one too."""
    text = text or ""
    inside = [(m.start(), m.end()) for h in headers or () if re.search(r"[.!?]\s", str(h))
              for m in re.finditer(re.escape(str(h)), text)]
    out, start = [], 0

    def add(a: int, b: int):
        while a < b and text[a].isspace():
            a += 1
        while b > a and text[b - 1].isspace():
            b -= 1
        if a < b:
            out.append((a, b))
    for m in re.finditer(r"(?<=[.!?])\s+|\s*\n\s*", text):
        before = text[start:m.start()].strip()
        initials = bool(re.search(r"(?<![A-Za-z])[A-Za-z]\.$", before)) and (
            bool(_INITIAL_NEXT.match(text, m.end())) or bool(re.search(r"(?<![A-Za-z])[A-Za-z]\.\s+[A-Za-z]\.$", before)))
        lower = not cut_before_lowercase and text[m.end():m.end() + 1].islower()
        if "\n" not in m.group(0) and (_ABBREV.search(before) or initials or lower
                                       or any(a < m.start() < b for a, b in inside)):
            continue
        add(start, m.start())
        start = m.end()
    add(start, len(text))
    return out


def said_sentences(text: str, headers=(), cut_before_lowercase: bool = False) -> list:
    """The typed words cut into sentences, each exactly as typed (see sentence_spans)."""
    return [text[a:b] for a, b in sentence_spans(text, headers, cut_before_lowercase)]


def continues(before: str, sentence: str) -> bool:
    """A sentence that starts with a word pointing back ('their', 'that', 'it'), or an
    order whose object points back ('Drop them and keep the originals'), belongs
    with the sentence before it, so its subject is kept; a doubt on either side keeps
    them apart."""
    return bool(before) and bool(ANAPHOR.match(sentence or "") or POINTS_BACK.match(sentence or "")) \
        and not DOUBT.search(sentence) and not DOUBT.search(before)


def is_commercial(s: str, amounts=None) -> bool:
    """A deal's own terms: deal words with the term itself (an amount or a percent),
    never how a column's numbers are written ('2.0 means 2%'). amounts: the numbers
    the workbook's own cells hold; a sentence whose every amount is one of them
    ('billed $14.30 against the $13.10 list price', both in the lines) adds
    nothing the file does not already show, so it is not withheld."""
    if not (COMMERCIAL.search(s or "") and _AMOUNT.search(s or "") and not _UNIT_CONVENTION.search(s or "")):
        return False
    return not (amounts and _all_in_file(s, amounts))


# the dollar amounts a sentence states ('$14.30', '$1,240', '$2.5k')
_DOLLAR = re.compile(r"\$\s?(\d[\d,]*(?:\.\d+)?)(?:\s*(k|m)\b)?", re.I)


def _all_in_file(s: str, amounts) -> bool:
    """Every amount the sentence states is a dollar amount some money cell of the
    workbook holds. A percent, a rate or an amount written any other way ('cost plus
    5%', '2 cents a case') is a term the file does not show, so it is kept."""
    found = list(_DOLLAR.finditer(s or ""))
    if not found or sum(1 for _m in _AMOUNT.finditer(s or "")) > len(found):
        return False
    for m in found:
        v = float(m.group(1).replace(",", "")) * {"k": 1e3, "m": 1e6}.get((m.group(2) or "").lower(), 1)
        if round(abs(v), 2) not in amounts:
            return False
    return True


def workbook_amounts(analysis, limit: int = 400000) -> frozenset:
    """The money amounts the open workbooks' cells hold (to the cent), for the
    business-term screen: a price already in the lines is no new exposure. Only
    money columns count (a currency role, or a number column with cents), never a
    count, a code or an ID, so a quantity of 12 never vouches for '$12'."""
    got = getattr(analysis, "_cell_amounts", None)
    if got is not None:
        return got
    roles = getattr(analysis, "detection", {}).get("roles", {}) if hasattr(analysis, "detection") else {}
    units = ((getattr(analysis, "playbook", None) or {}).get("roles") or {})
    money = {(r.get("table"), r.get("header")) for rid, r in roles.items() if (units.get(rid) or {}).get("unit") == "currency"}
    out: set = set()
    n = 0
    for t in analysis.tables:
        for c in analysis.cols.get(t.tid, []):
            if c.type != "number" or c.semantic != "metric" or c.codes or c.sensitive:
                continue
            if (t.tid, c.header) not in money and getattr(c, "integers", True):
                continue
            for row in t.rows:
                v = row[c.j] if c.j < len(row) else None
                if isinstance(v, (int, float)) and not isinstance(v, bool) and v == v:
                    out.add(round(abs(float(v)), 2))
                    n += 1
            if n > limit:
                break
    got = frozenset(out)
    try:
        analysis._cell_amounts = got
    except Exception:  # noqa: BLE001
        pass
    return got


def term_parts(s: str, amounts=None) -> list:
    """[(piece, is a business term)] of one sentence: whole, unless it states a deal
    and also says how to treat rows in a clause of its own ('...; fees never count in
    rebate math'). Then that clause is its own piece and goes in the file; every other
    piece, a tail like 'no claim filed yet' included, stays with the deal."""
    if not is_commercial(s, amounts):
        return [(s, False)]
    cuts = [0] + [m.end() for m in _CLAUSE.finditer(s)] + [len(s)]
    pieces = [(cuts[k], cuts[k + 1]) for k in range(len(cuts) - 1) if s[cuts[k]:cuts[k + 1]].strip()]
    rule = [bool(_TREATMENT.search(s[a:b])) and not is_commercial(s[a:b], amounts) for a, b in pieces]
    if len(pieces) < 2 or not any(rule) or all(rule):
        return [(s, True)]
    out: list = []
    for (a, b), is_rule in zip(pieces, rule):
        if out and not is_rule and not out[-1][2]:
            out[-1] = (out[-1][0], b, False)       # the deal's pieces stay together
        else:
            out.append((a, b, is_rule))
    return [(s[a:b].strip(), not is_rule) for a, b, is_rule in out]


def classify_parts(text: str, names: set | None = None, headers: set | None = None, amounts=None) -> list:
    """[(piece, class, reason, sentence number)]: each sentence whole, except a
    treatment clause taken out of a sentence about a deal."""
    low_names = {n.lower() for n in (names or set()) if n and len(n) >= 3}
    heads = {str(h) for h in (headers or set()) if h}
    low_heads = {h.lower() for h in heads}
    # a word of a column name is the data's own vocabulary, not a person's name ('Deposits', 'Freight')
    head_words = {w.rstrip("s") for h in low_heads for w in re.findall(r"[a-z]+", h)}
    out, prev = [], ""
    for n, s in enumerate(said_sentences(text, heads)):
        low = s.lower()
        cls, reason = "data", ""
        who = [x for x in low_names if re.search(r"\b" + re.escape(x) + r"\b", low)]
        lead = re.match(r"\W*([A-Za-z]+)", s)
        caps_all = [m for m in _NAME.finditer(s) if m.group(0) not in _STOP_CAPS
                    and m.group(0).lower() not in low_heads and m.group(0).lower().rstrip("s") not in head_words]
        # a sentence's first word is capitalized because it comes first ('Churn rate is ...', 'Medical
        # supplies ...'): never a name beside a word that is also business vocabulary; a remark on how someone
        # behaves ('John is lazy') still names them
        caps = [m.group(0) for m in caps_all if not (lead and m.start() == lead.start(1))]
        # a word of the data's own vocabulary ('Churn rate', a line item) is no remark about anyone
        hr = [m for m in HR_DEAL.finditer(s) if m.group(0).lower().rstrip("s") not in head_words]
        churn = [m for m in CHURN.finditer(s) if m.group(0).lower().rstrip("s") not in head_words
                 and _PERSON_SUBJECT.search(s)]
        if CONFIDENTIAL.search(s):
            cls, reason = "private", "confidentiality marker"
        elif (who or caps_all) and EVALUATIVE.search(s):
            cls, reason = "private", "remark about a person, client or deal"
        elif (who or caps) and (hr or churn):
            cls, reason = "private", "remark about a person, client or deal"
        elif (hr and re.search(r"\b(he|she|they|him|her|them|guy|client|customer|vendor|rep)\b", low)) or churn:
            cls, reason = "private", "remark about a person, client or deal"
        elif prev == "private" and ANAPHOR.match(s) and not re.match(r"\W*there\b", s, re.I):
            cls, reason = "private", "follows a private remark"
        prev = cls
        if cls == "private":
            out.append((s, cls, reason, n))
            continue
        for piece, term in term_parts(s, amounts):
            out.append((piece, "commercial" if term else "data", "business terms" if term else "", n))
    return out


def classify(text: str, names: set | None = None, headers: set | None = None, amounts=None) -> list:
    """[(sentence, class, reason)] with class in data | private | commercial."""
    return [(s, c, r) for s, c, r, _n in classify_parts(text, names, headers, amounts)]


def split(text: str, names: set | None = None, headers: set | None = None, amounts=None) -> dict:
    """{'data': str, 'commercial': str, 'private': [(sentence, reason)]}."""
    res = classify(text, names, headers, amounts)
    return {
        "data": " ".join(s for s, c, _ in res if c == "data"),
        "commercial": " ".join(s for s, c, _ in res if c == "commercial"),
        "private": [(s, r) for s, c, r in res if c == "private"],
    }


def screen(text: str, names: set | None = None, headers: set | None = None, amounts=None) -> tuple:
    """(what may be kept: the owner's words with only the private sentences taken
    out, the rest byte for byte, line breaks included; [(private sentence, reason)];
    whether any kept sentence is a business term)."""
    text = text or ""
    kept, private, commercial = "", [], False
    at = pos = 0              # copied up to at; searched up to pos
    for sent, c, reason in classify(text, names, headers, amounts):
        start = text.find(sent, pos)
        if start < 0:
            continue
        pos = start + len(sent)
        if c == "private":
            private.append((sent, reason))
            kept += text[at:start]
            while pos < len(text) and text[pos] in " \t":    # and the spaces that led to the next one
                pos += 1
            if start == 0 or text[start - 1] == "\n":         # a whole line: and its line break
                pos += 2 if text.startswith("\r\n", pos) else 1 if text.startswith("\n", pos) else 0
            at = pos
            continue
        commercial = commercial or c == "commercial"
    kept += text[at:]
    return kept.strip(), private, commercial


def vocabulary(analysis) -> set:
    """The data's own words: every column name in the open files and every line-item
    label down a grid or an input list ('Churn rate', 'Pay per person'). A word of
    them is never read as a person's name or a remark about one."""
    got = getattr(analysis, "_vocabulary", None)
    if got is not None:
        return set(got)
    out = {c.header for cols in analysis.cols.values() for c in cols if c.header}
    for t in getattr(analysis, "tables", None) or []:
        try:
            out |= {str(x) for x in analysis.row_labels(t) if x}
        except Exception:  # noqa: BLE001
            continue
    try:
        analysis._vocabulary = frozenset(out)
    except Exception:  # noqa: BLE001
        pass
    return out


def entity_names(analysis, limit: int = 3000) -> set:
    """Values from person and counterparty columns: what makes a remark personal."""
    out: set = set()
    for tid, cols in analysis.cols.items():
        for c in cols:
            if c.is_person or c.semantic == "dimension" and c.type == "text" and c.distinct <= 500:
                for k, _ in c.top[:50]:
                    if isinstance(k, str) and 3 <= len(k) <= 40:
                        out.add(k)
                if c.is_person:
                    for k in list(c.counter)[:limit]:
                        if isinstance(k, str):
                            out.add(k)
    return out
