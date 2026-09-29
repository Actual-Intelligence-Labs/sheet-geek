"""Archetype detection and role matching from playbook lexicons. Code decides;
the model only confirms on a tie.
"""
from __future__ import annotations

import json
import os
import re

from .profile import split_camel

PLAYBOOK_DIR = os.environ.get("SPREADSHEET_BRAIN_PLAYBOOKS") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "playbooks")


def load_playbooks(directory: str | None = None) -> dict:
    d = directory or PLAYBOOK_DIR
    out = {}
    if not os.path.isdir(d):
        return out
    for fn in sorted(os.listdir(d)):
        if fn.endswith(".json") and not fn.startswith("_"):
            with open(os.path.join(d, fn), encoding="utf-8") as fh:
                pb = json.load(fh)
            out[pb["id"]] = pb
    return out


def norm_header(h: str) -> str:
    s = split_camel(str(h)).lower()
    s = re.sub(r"[^a-z0-9#%$]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _lex_match(header_n: str, lexicon: list) -> int:
    """Length of the best lexicon phrase that equals or appears (whole words)
    inside the normalized header. 0 = no match."""
    return _lex_best(header_n, lexicon)[0]


def _lex_best(header_n: str, lexicon: list) -> tuple:
    """(score, phrase) of the best lexicon phrase; (0, "") when none matches."""
    best = (0, "")
    for phrase in lexicon:
        p = norm_header(phrase)
        if not p:
            continue
        if header_n == p:
            best = max(best, (len(p) + 100, p))       # exact beats contained
        elif re.search(r"(^| )" + re.escape(p) + r"( |$)", header_n):
            best = max(best, (len(p), p))
    return best


# a last word after a thing's name that names something else about it ('Customer Region')
_ATTR_NOUNS = {"region", "type", "status", "category", "class", "group", "date", "city", "state", "country", "zip",
               "email", "phone", "address", "count", "size", "segment", "tier", "source", "channel", "rep",
               "owner", "territory", "area", "zone", "market", "industry", "contact", "notes", "since"}
# a header that is only an ID word names nothing, so its values must be one per row
_BARE_ID = {"id", "key", "code", "ref", "reference", "number", "no", "num", "#", "uid", "identifier",
            "ref #", "record id", "row id", "unique id"}
_PERCENT_TOKENS = {"%", "pct", "percent"}
_RATE_TOKENS = {"rate", "per", "ratio"}          # dollars or a percent: the values decide
_RATIO_LABELS = _PERCENT_TOKENS | {"margin", "ratio", "per"}
_MONEY_WORDS = re.compile(r"\b(price|cost|pay|fee|fees|wage|bill|billing|charge|rent|hourly|daily|nightly|"
                          r"weekly|monthly)\b")


def _tokens(header_n: str) -> set:
    return set(header_n.replace("%", " % ").split())


def _percent_values(col) -> bool:
    """Every value reads as a percent: all within 0 to 1, or all within 0 to 100
    with at most one decimal."""
    if col is None or col.type != "number" or col.min is None or float(col.min) < 0:
        return False
    if float(col.max) <= 1:
        return True
    return float(col.max) <= 100 and all(len(k.split(".")[1]) <= 1 for k in col.counter if "." in k)


def _reads_as_rate(header_n: str, phrase: str, col=None) -> bool:
    """The header names a rate or a share, not dollars: a percent word the lexicon
    phrase does not carry, or (for a column) 'rate' or 'per' over values that read as
    a percent, or (for a row label) margin, ratio or per."""
    extra = _tokens(header_n) - _tokens(phrase)
    if extra & _PERCENT_TOKENS:
        return True
    if col is None:
        return bool(extra & _RATIO_LABELS)
    return bool(extra & _RATE_TOKENS) and _percent_values(col) and not _MONEY_WORDS.search(header_n)


def _binds(rid: str, role: dict, col, header_n: str, phrase: str) -> bool:
    """Checks on the values and the whole header, beyond the lexicon hit."""
    kind = role.get("kind")
    if kind == "identifier" and col.count and (rid == "record_key" or phrase in _BARE_ID) \
            and col.distinct / col.count < 0.9:
        return False          # a 12-value 'Code' column is a category, not the record's key
    if kind in ("entity", "attribute") and role.get("type") == "text" and header_n != phrase:
        m = re.search(r"(^| )" + re.escape(phrase) + r"( |$)", header_n)
        tail = header_n[m.end():].split() if m else []
        if tail and tail[-1] in _ATTR_NOUNS:
            return False      # 'Customer Region' is a region, not a customer
    if role.get("unit") == "currency" and _reads_as_rate(header_n, phrase, col):
        return False          # 'Rebate %' is a rate, never dollars
    return True


def _type_ok(role: dict, col) -> bool:
    t = role.get("type", "any")
    if t == "any":
        return col.type != "empty"
    if t == "number":
        if role.get("kind") == "metric" and col.codes:
            return False      # numbers that name things are never summed
        return col.type == "number"
    if t == "date":
        return col.type == "date" or (col.semantic == "temporal")
    if t == "bool":
        return col.type == "bool"
    if t == "text":
        return col.type == "text"
    if t == "id":
        return (col.semantic == "identifier" or col.codes
                or (col.type in ("text", "number") and col.count
                    and col.distinct / col.count >= 0.3 and (col.type == "text" or col.integers)))
    return True


def match_roles(pb: dict, cols: list, row_labels: list | None = None) -> dict:
    """role id -> {"header": h, "col": Col | None, "row_label": bool}."""
    roles = pb.get("roles", {})
    cands = []
    for rid, role in roles.items():
        lex = role.get("headers", [])
        for col in cols:
            hn = norm_header(col.header)
            s, phrase = _lex_best(hn, lex)
            if s and _type_ok(role, col) and _binds(rid, role, col, hn, phrase):
                cands.append((s, rid, col.header, col))
        for lab in row_labels or []:
            ln = norm_header(lab)
            s, phrase = _lex_best(ln, lex)
            if s and not (role.get("unit") == "currency" and _reads_as_rate(ln, phrase)):
                cands.append((s - 1, rid, lab, None))
    cands.sort(key=lambda x: -x[0])
    out: dict = {}
    used = set()
    for s, rid, header, col in cands:
        if rid in out or (col is not None and id(col) in used):
            continue
        out[rid] = {"header": header, "col": col, "row_label": col is None}
        if col is not None:
            used.add(id(col))
    return out


def _is_amount(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v != 0


# words that put an amount on one side of a ledger, beside the ledger playbook's own lexicons
_DEBIT_WORDS = ["debit", "debits", "dr", "charge", "charges", "bill", "billed", "invoice", "invoiced", "withdrawal",
                "expense", "expenses", "spent", "money out", "paid out"]
_CREDIT_WORDS = ["credit", "credits", "cr", "payment", "payments", "receipt", "receipts", "deposit", "deposits",
                 "refund", "refunds", "money in", "paid in", "received"]
_LEX_CACHE: dict = {}


def _lexicons() -> dict:
    """Phrases read once from the playbooks: every currency role's headers, and
    the ledger's money-out and money-in headers as the debit and credit sides."""
    if PLAYBOOK_DIR not in _LEX_CACHE:
        pbs = load_playbooks()
        roles = (pbs.get("ledger") or {}).get("roles", {})
        _LEX_CACHE[PLAYBOOK_DIR] = {
            "money": [h for pb in pbs.values() for r in pb.get("roles", {}).values()
                      if r.get("unit") == "currency" for h in r.get("headers", [])],
            "debit": _DEBIT_WORDS + roles.get("money_out", {}).get("headers", []),
            "credit": _CREDIT_WORDS + roles.get("money_in", {}).get("headers", [])}
    return _LEX_CACHE[PLAYBOOK_DIR]


def _is_money(c) -> bool:
    """Cents in the values, or a header that names money."""
    hn = norm_header(c.header)
    lex = _lexicons()
    return not c.integers or bool(_MONEY_WORDS.search(hn)) or any(
        _lex_match(hn, lex[k]) for k in ("money", "debit", "credit"))


def pair_sides(pair):
    """(debit, credit) of a pair, read from the headers: 'Payment | Charge' gives
    Charge as the debit. None when the headers do not say which side is which."""
    lex = _lexicons()
    side = []
    for c in pair:
        hn = norm_header(c.header)
        d, k = _lex_match(hn, lex["debit"]), _lex_match(hn, lex["credit"])
        side.append("debit" if d > k else "credit" if k > d else None)
    a, b = pair
    if side[0] == "debit" and side[1] != "debit" or side[1] == "credit" and side[0] != "credit":
        return a, b
    if side[1] == "debit" and side[0] != "debit" or side[0] == "credit" and side[1] != "credit":
        return b, a
    return None


def debit_credit_pair(t, cols: list):
    """Two non-negative money columns that are never both nonzero on a row and
    between them fill most rows are one amount split by side (Charge | Payment,
    Debit | Credit). Returns (left, right) Cols, or None; pair_sides says which is the debit."""
    money = [c for c in cols if c.type == "number" and c.semantic == "metric" and not c.negatives
             and not c.codes and not _tokens(norm_header(c.header)) & (_PERCENT_TOKENS | _RATE_TOKENS)
             and _is_money(c)]
    n = len(t.rows)
    if len(money) < 2 or n < 10:
        return None
    best = None
    for i, a in enumerate(money):
        for b in money[i + 1:]:
            both = only_a = only_b = 0
            for r in t.rows:
                ha = _is_amount(r[a.j] if a.j < len(r) else None)
                hb = _is_amount(r[b.j] if b.j < len(r) else None)
                if ha and hb:
                    both += 1
                    break
                only_a += ha
                only_b += hb
            if both or min(only_a, only_b) < max(3, 0.05 * n) or only_a + only_b < 0.8 * n:
                continue
            if best is None or only_a + only_b > best[0]:
                best = (only_a + only_b, a, b)
    if best is None:
        return None
    return tuple(sorted(best[1:], key=lambda c: c.j))


def _infer_required(pb: dict, matched: dict, pair) -> dict:
    """When a playbook misses only one required group and the values show a
    debit/credit pair, the pair fills the group's debit and credit roles. When the
    headers do not say which side is which, the pair counts toward the score but
    binds nothing ("unbound")."""
    groups = pb.get("detect", {}).get("required_any", [])
    failing = [g for g in groups if not any(r in matched for r in g)]
    if len(failing) != 1 or not pair:
        return matched
    used = {id(i["col"]) for i in matched.values() if i.get("col") is not None}
    if any(id(c) in used for c in pair):
        return matched
    roles = pb.get("roles", {})
    out = dict(matched)
    sides = pair_sides(pair)
    for side, col in zip(("debit", "credit"), sides or pair):
        rid = next((r for r in failing[0] if side in {norm_header(h) for h in roles.get(r, {}).get("headers", [])}
                    and _type_ok(roles[r], col)), None)
        if rid is None:
            return matched
        out[rid] = {"header": col.header, "col": col, "row_label": False, "inferred": True}
        if sides is None:
            out[rid]["unbound"] = True
    return out


def _prefer_calculated(analysis, pb: dict, roles: dict) -> None:
    """In a model, a money role read from row labels binds to the most downstream
    calculated row with a matching label, never to a typed input when such a row exists."""
    formula_rows: dict = {}
    for b in analysis.books:
        for s in b.data_sheets():
            formula_rows[(b.path, s.name)] = {r for (r, _) in s.formulas}
    depth: dict = {}
    for fa in analysis.formulas.values():
        edges = [(e["from"], e["to"]) for e in (fa or {}).get("row_flow", [])]
        for _ in range(50):          # longest chain of rows feeding each row
            moved = False
            for a, b in edges:
                if depth.get(b, 0) < depth.get(a, 0) + 1 and depth.get(a, 0) < 50:
                    depth[b] = depth.get(a, 0) + 1
                    moved = True
            if not moved:
                break
    wide = {t.tid for t in analysis.tables if t.wide}
    for rid, role in pb.get("roles", {}).items():
        cur = roles.get(rid)
        if role.get("unit") != "currency" or (cur and (not cur.get("row_label") or cur["table"] in wide)):
            continue          # a column, or a row on a period grid, already reads as calculated
        lex = role.get("headers", [])
        cands = []
        for t in analysis.tables:
            if not t.wide or t.row_label_col < 0:
                continue
            rows = formula_rows.get((analysis.file_of[t.tid], t.sheet), set())
            for i, r in enumerate(t.rows):
                lab = r[t.row_label_col] if t.row_label_col < len(r) else None
                if not isinstance(lab, str) or not lab.strip() or t.row_index[i] not in rows:
                    continue
                ln = norm_header(lab)
                s, phrase = _lex_best(ln, lex)
                if s and not _reads_as_rate(ln, phrase):          # 'Profit margin %' is never the profit
                    node = t.sheet + "!" + " ".join(lab.split())[:60]      # as formulas names rows
                    cands.append((depth.get(node, 0), s, t.tid, lab.strip()))
        if cands:          # a whole-label match first, then the most downstream row
            d, s, tid, lab = max(cands, key=lambda x: (x[1] >= 100, x[0], x[1]))
            roles[rid] = {"table": tid, "header": lab, "col": None, "row_label": True}


def score(pb: dict, matched: dict, formula_heavy: bool = False) -> float:
    det = pb.get("detect", {})
    for group in det.get("required_any", []):
        if not any(r in matched for r in group):
            return 0.0
    weights = det.get("weights") or {r: 1 for r in pb.get("roles", {})}
    total = sum(weights.values()) or 1
    got = sum(w for r, w in weights.items() if r in matched)
    s = got / total
    s -= 0.3 * sum(1 for r in det.get("negatives", []) if r in matched)
    if formula_heavy and pb.get("graph", {}).get("mode") == "formula_flow":
        s += 0.25
    return max(0.0, min(1.0, s))


def detect(analysis, playbooks: dict) -> dict:
    per_table = {}
    formula_heavy = any(f.get("count", 0) >= 50 for f in analysis.formulas.values()) and \
        any(t.wide for t in analysis.tables)
    pairs = {}
    for t in analysis.tables:
        p = debit_credit_pair(t, analysis.cols[t.tid])
        if p:
            pairs[t.tid] = p
    for t in analysis.tables:
        cols = analysis.cols[t.tid]
        labels = analysis.row_labels(t)
        best = []
        for pid, pb in playbooks.items():
            if pid == "generic":
                continue
            m = _infer_required(pb, match_roles(pb, cols, labels), pairs.get(t.tid))
            best.append((score(pb, m, formula_heavy and t.wide), pid, m))
        best.sort(key=lambda x: -x[0])
        per_table[t.tid] = best[:3]
    main = analysis.main_table
    ranked = per_table.get(main.tid, []) if main else []
    arche, conf, runner = "generic", 0.0, None
    if ranked:
        conf, arche, _ = ranked[0]
        if len(ranked) > 1:
            runner = {"id": ranked[1][1], "score": round(ranked[1][0], 3)}
    pb = playbooks.get(arche)
    thresholds = (pb or {}).get("detect", {})
    candidate = thresholds.get("candidate", 0.35)
    confident = thresholds.get("confident", 0.6)
    if conf < candidate:
        arche = "generic" if "generic" in playbooks else arche
    tie = bool(runner and conf >= candidate and runner["score"] >= candidate
               and conf - runner["score"] <= 0.10)
    # roles for the chosen playbook across all tables, main table first
    roles: dict = {}
    chosen = playbooks.get(arche, {})
    order = [main] + [t for t in analysis.tables if t is not main] if main else analysis.tables
    for t in order:
        m = _infer_required(chosen, match_roles(chosen, analysis.cols[t.tid], analysis.row_labels(t)),
                            pairs.get(t.tid))
        for rid, info in m.items():
            if rid not in roles and not info.get("unbound"):
                roles[rid] = {"table": t.tid, "header": info["header"], "col": info["col"],
                              "row_label": info["row_label"]}
                if info.get("inferred"):
                    roles[rid]["inferred"] = True
    _prefer_calculated(analysis, chosen, roles)
    tables = {}
    for tid, ranked_t in per_table.items():
        if ranked_t and ranked_t[0][0] >= 0.35:
            tables[tid] = {"archetype": ranked_t[0][1], "score": round(ranked_t[0][0], 3)}
        else:
            tables[tid] = {"archetype": "generic", "score": round(ranked_t[0][0], 3) if ranked_t else 0}
    mixed = len({v["archetype"] for tid, v in tables.items()
                 if v["archetype"] != "generic" and not analysis.is_derived(tid)}) > 1
    return {
        "archetype": arche,
        "closest": ranked[0][1] if ranked else None,
        "confidence": round(conf, 3),
        "confident": conf >= confident,
        "looks_like": (playbooks.get(arche) or {}).get("looks_like", "a spreadsheet"),
        "runner_up": runner,
        "tie": tie,
        "mixed": mixed,
        "tables": tables,
        "roles": roles,
        "pairs": {tid: [a.header, b.header] for tid, (a, b) in pairs.items()},
    }
