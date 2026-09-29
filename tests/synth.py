"""Seeded synthetic workbooks with planted traps and their null twins.

Nothing here comes from a real business: names are made from syllables, headers
are drawn from pools, and every size, date, code and prefix comes from the seed.
Each trap is one general class of problem a detector must find; its null twin
looks like it and must stay silent.

    m = synth.build(path, seed, "code_minority")               # the plant
    m = synth.build(path, seed, "code_minority", twin=True)    # its null twin
    m = synth.build_book(path, seed, ["reimport", "odd_group"])  # one tab per trap

Every call returns a manifest: the writer used (xlsxwriter or openpyxl, by
seed) and one entry per plant with its sheet, columns, Excel rows (1-based),
values and the question kind a detector should ask (None on a twin).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import random

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "evals", "fixtures")

# books no new detector may ask about (the noise budget): each entry is one build. finance_model.xlsx is a copy
# of a development business's workbook, so it is not one of them (the rule: never test on the development
# books); the synthetic models of rank 20 and their null twins stand in for it
NOISE_FIXTURES = [[os.path.join(FIXTURES, "messy_multitable.xlsx")],
                  [os.path.join(FIXTURES, "hostile_brain.xlsx")],
                  [os.path.join(FIXTURES, "crm_contacts.csv"), os.path.join(FIXTURES, "crm_deals.csv")]]

_SYL = ["ka", "lo", "mi", "ra", "ven", "tor", "sil", "bar", "del", "nor", "pa", "qui", "zen", "mar", "ul",
        "fen", "gor", "tas", "bri", "hal", "dun", "ess", "ri", "vo", "cam", "lin", "osk", "pel", "tru", "wy"]

POOLS = {
    "date": ["Date", "Txn Date", "Posted", "Entry Date", "Service Date", "Doc Date"],
    "doc": ["Ref", "Doc No", "Entry ID", "Voucher", "Line Ref", "Slip"],
    "money": ["Amount", "Net", "Value", "Line Amount", "Extended", "Charge Amt"],
    "qty": ["Qty", "Units", "Pieces", "Count"],
    "price": ["Price", "Unit Price", "Each", "Unit Cost"],
    "site": ["Site", "Location", "Branch", "Outlet", "Depot", "Yard"],
    "person": ["Clerk", "Staff", "Entered By", "Handler", "Operator"],
    "item": ["Item", "Product", "Article", "Goods", "Line Item"],
    "party": ["Client", "Member", "Holder", "Payer", "Patron"],
    "party_id": ["Member ID", "Holder No", "Acct ID", "Client ID", "Person ID"],
    "code": ["Disposition", "Reason", "Result", "Outcome", "Kind", "Class"],
    "status": ["Status", "State", "Flag", "Stage"],
    "category": ["Category", "Group", "Family", "Section", "Department"],
    "note": ["Notes", "Memo", "Comment", "Remarks"],
    "submitted": ["Submitted", "Uploaded At", "Entered At", "Loaded"],
    "adjust": ["Adj", "Discount", "Markdown", "Allowance"],
    "account": ["Account", "Ledger Account", "GL Line", "Book Account"],
    "entry": ["Entry", "Journal No", "Batch", "Voucher No"],
}
TRAPS = {}          # name -> (builder, expected question kind)


class Formula:
    """A formula cell: its text and the value a spreadsheet app would have saved."""
    def __init__(self, text: str, value):
        self.text, self.value = text, value


def trap(kind):
    def reg(fn):
        TRAPS[fn.__name__.lstrip("_")] = (fn, kind)
        return fn
    return reg


# --------------------------------------------------------------------------
# public
# --------------------------------------------------------------------------
def build(path, seed: int, name: str, twin: bool = False, writer: str | None = None) -> dict:
    """Write one workbook holding the trap (or its twin) and return its manifest."""
    return build_book(path, seed, [(name, twin)], writer)


def build_book(path, seed: int, traps: list, writer: str | None = None) -> dict:
    """Several traps in one workbook, one tab each. traps: names or (name, twin)."""
    writer = writer or ("xlsxwriter", "openpyxl")[seed % 2]
    sheets, plants, used = [], [], []
    for item in traps:
        name, twin = (item, False) if isinstance(item, str) else item
        fn, kind = TRAPS[name]
        rng = random.Random(f"{name}:{int(twin)}:{seed}")
        tab = _sheet_name(rng, name, used)
        rows, found, *more = fn(rng, twin)
        sheets.append({"name": tab, "rows": rows})
        for extra in (more[0] if more else []):          # a trap that needs a second tab (a lookup list)
            if isinstance(extra, dict):                  # a tab its formulas name: {"name", "rows"}
                used.append(extra["name"])
                sheets.append(extra)
            else:
                sheets.append({"name": _sheet_name(rng, name, used), "rows": extra})
        for p in found:
            own = p.pop("kind", kind)          # a trap with several plants names each one's kind
            p.update({"trap": name, "twin": twin, "sheet": tab, "expect": None if twin else own})
            plants.append(p)
    (_write_xlsxwriter if writer == "xlsxwriter" else _write_openpyxl)(str(path), sheets)
    return json.loads(json.dumps({"seed": seed, "writer": writer, "path": str(path), "plants": plants},
                                 default=_jsonable))


def build_budget(path, seed: int, writer: str | None = None) -> dict:
    """One workbook with 15 planted issues of known stake, for the question
    budget: 11 tabs each with an integer code column whose rare code carries a
    set share of the tab's money (two large, nine small and all different), one
    tab whose code column is also blank on a set share of rows, and a main tab
    where one site's documents alone carry their own prefix and some amounts are
    negative. Each plant's stake is the share of its tab's money the answer moves.
    Which kind carries the largest stake turns with the seed: a code, the blanks
    or the one site."""
    rng = random.Random(f"budget:{seed}")
    writer = writer or ("xlsxwriter", "openpyxl")[seed % 2]
    top = ("codes", "blanks", "exclusive")[seed % 3]
    used, sheets, plants = [], [], []
    small = sorted(rng.sample(range(2, 40), 9))
    shares = [round(rng.uniform(0.32, 0.45), 3), round(rng.uniform(0.25, 0.29), 3)] + [s / 1000 for s in small]
    rng.shuffle(shares)
    for i, share in enumerate(shares):
        tab = _sheet_name(rng, "budget", used)
        h = _headers(rng, "date", "doc", "code", "money")
        n, k = rng.randint(60, 90), rng.randint(3, 5)
        codes = sorted(rng.sample(range(100 * i + 1, 100 * i + 60), k))
        rare, m = codes[-1], max(3, round(0.05 * n))
        start, days = _start(rng), rng.randint(120, 300)
        rows = [{"date": _day(start, rng, days), "code": rng.choice(codes[:-1]),
                 "amount": round(rng.uniform(20, 400), 2)} for _ in range(n - m)]
        rest = sum(r["amount"] for r in rows)
        for _ in range(m):          # the rare code's rows carry exactly the planted share of the money
            rows.append({"date": _day(start, rng, days), "code": rare,
                         "amount": round(share / (1 - share) * rest / m, 2)})
        rows.sort(key=lambda r: r["date"])
        pre = _caps(rng, 2)
        for j, r in enumerate(rows):
            r["doc"] = f"{pre}-{1000 + j}"
        sheets.append({"name": tab, "rows": _table([("date", h["date"]), ("doc", h["doc"]), ("code", h["code"]),
                                                    ("amount", h["money"])], rows)})
        got = sum(r["amount"] for r in rows if r["code"] == rare) / sum(r["amount"] for r in rows)
        plants.append({"kind": "codes", "sheet": tab, "col": h["code"], "value": rare, "stake": round(got, 6)})
    # a code column that is also blank on a share of its rows
    tab = _sheet_name(rng, "budget", used)
    h = _headers(rng, "date", "doc", "category", "money")
    n = rng.randint(140, 180)
    codes = _codes(rng, 4, rng.randint(2, 3))
    start = _start(rng)
    rows = [{"date": _day(start, rng, 200), "code": rng.choice(codes[:3]),
             "amount": round(rng.uniform(20, 400), 2)} for _ in range(n)]
    for r in rng.sample(rows, 5):
        r["code"] = codes[3]
    blank = rng.sample([r for r in rows if r["code"] != codes[3]], rng.randint(22, 30))
    for r in blank:
        r["code"] = None
    if top == "blanks":
        _carry(rows, blank, rng.uniform(0.5, 0.6))
    rows.sort(key=lambda r: r["date"])
    for j, r in enumerate(rows):
        r["doc"] = f"BK-{2000 + j}"
    sheets.append({"name": tab, "rows": _table([("date", h["date"]), ("doc", h["doc"]), ("code", h["category"]),
                                                ("amount", h["money"])], rows)})
    tot = sum(r["amount"] for r in rows)
    plants.append({"kind": "blanks", "sheet": tab, "col": h["category"],
                   "stake": round(sum(r["amount"] for r in blank) / tot, 6)})
    plants.append({"kind": "codes", "sheet": tab, "col": h["category"], "value": codes[3],
                   "stake": round(sum(r["amount"] for r in rows if r["code"] == codes[3]) / tot, 6)})
    # the main tab: one site's documents alone start with their own prefix; a few negative amounts
    tab = _sheet_name(rng, "budget", used)
    b = _log(rng, n=rng.randint(360, 440), sites=5)
    h = _log_headers(rng)
    h["money"] = "Amount"
    odd = b["sites"][0]
    fams = _codes(rng, 4, 2)
    for r in b["rows"]:
        if r["site"] == odd:
            r["site"] = rng.choice(b["sites"][1:])
    for r in rng.sample(b["rows"], rng.randint(40, 60)):
        r["site"] = odd
    if top == "exclusive":
        _carry(b["rows"], [r for r in b["rows"] if r["site"] == odd], rng.uniform(0.5, 0.6), priced=True)
    neg = rng.sample([r for r in b["rows"] if r["site"] != odd], rng.randint(6, 10))
    for r in neg:                   # small credits: well under 1% of the money
        r["amount"] = -round(rng.uniform(1, 5), 2)
    b["rows"].sort(key=lambda r: r["date"])
    for j, r in enumerate(b["rows"]):
        r["doc"] = f"{fams[0] if r['site'] == odd else rng.choice(fams[1:])}-{5000 + j}"
    sheets.append({"name": tab, "rows": _table(_log_cols(h), b["rows"])})
    tot = sum(abs(r["amount"]) for r in b["rows"])
    plants.append({"kind": "exclusive", "sheet": tab, "col": h["site"], "value": odd, "prefix": fams[0],
                   "stake": round(sum(abs(r["amount"]) for r in b["rows"] if r["site"] == odd) / tot, 6)})
    plants.append({"kind": "negatives", "sheet": tab, "col": "Amount",
                   "stake": round(sum(abs(r["amount"]) for r in neg) / tot, 6)})
    (_write_xlsxwriter if writer == "xlsxwriter" else _write_openpyxl)(str(path), sheets)
    return json.loads(json.dumps({"seed": seed, "writer": writer, "path": str(path), "plants": plants},
                                 default=_jsonable))


def cells(path) -> list:
    """Every sheet's cell values, for comparing two builds."""
    import openpyxl
    wb = openpyxl.load_workbook(str(path), data_only=True)
    try:
        return [(ws.title, [list(r) for r in ws.iter_rows(values_only=True)]) for ws in wb.worksheets]
    finally:
        wb.close()


# --------------------------------------------------------------------------
# writers
# --------------------------------------------------------------------------
def _write_xlsxwriter(path: str, sheets: list) -> None:
    import xlsxwriter
    wb = xlsxwriter.Workbook(path, {"strings_to_formulas": False, "strings_to_urls": False,
                                    "strings_to_numbers": False})
    wb.set_properties({"created": dt.datetime(2026, 1, 1)})
    day = wb.add_format({"num_format": "yyyy-mm-dd"})
    stamp = wb.add_format({"num_format": "yyyy-mm-dd hh:mm"})
    for s in sheets:
        ws = wb.add_worksheet(s["name"])
        for r, row in enumerate(s["rows"]):
            for c, v in enumerate(row):
                if v is None:
                    continue
                if isinstance(v, dt.datetime):
                    ws.write_datetime(r, c, v, stamp if (v.hour, v.minute) != (0, 0) else day)
                elif isinstance(v, Formula):
                    ws.write_formula(r, c, v.text, None, v.value)
                elif isinstance(v, str):
                    ws.write_string(r, c, v)
                else:
                    ws.write_number(r, c, v)
    wb.close()


def _write_openpyxl(path: str, sheets: list) -> None:
    import openpyxl
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for s in sheets:
        ws = wb.create_sheet(s["name"])
        for r, row in enumerate(s["rows"], 1):
            for c, v in enumerate(row, 1):
                if v is None:
                    continue
                cell = ws.cell(row=r, column=c, value=v.text if isinstance(v, Formula) else v)
                if isinstance(v, dt.datetime):
                    cell.number_format = "yyyy-mm-dd hh:mm" if (v.hour, v.minute) != (0, 0) else "yyyy-mm-dd"
    wb.save(path)


def _jsonable(v):
    if isinstance(v, (dt.datetime, dt.date)):
        return v.isoformat()
    raise TypeError(type(v))


# --------------------------------------------------------------------------
# small makers
# --------------------------------------------------------------------------
def _word(rng, n: int = 2) -> str:
    return "".join(rng.choice(_SYL) for _ in range(n)).capitalize()


def _names(rng, k: int, n: int = 2) -> list:
    out = []
    while len(out) < k:
        w = _word(rng, n)
        if w not in out:
            out.append(w)
    return out


def _people(rng, k: int) -> list:
    """(first, last) pairs, all different."""
    out = []
    while len(out) < k:
        p = (_word(rng), _word(rng, 3))
        if p not in out:
            out.append(p)
    return out


def _caps(rng, width: int) -> str:
    return "".join(rng.choice("ABCDEFGHJKLMNPQRSTUVWXYZ") for _ in range(width))


def _codes(rng, k: int, width: int) -> list:
    out = []
    while len(out) < k:
        c = _caps(rng, width)
        if c not in out:
            out.append(c)
    return out


def _carry(rows: list, part: list, share: float, priced: bool = False) -> None:
    """Scale the part's amounts so they carry this share of the rows' money
    (priced: scale the price and keep amount = qty x price)."""
    mine = {id(r) for r in part}
    rest = sum(abs(r["amount"]) for r in rows if id(r) not in mine)
    f = share / (1 - share) * rest / sum(abs(r["amount"]) for r in part)
    for r in part:
        if priced:
            r["price"] = round(r["price"] * f, 2)
            r["amount"] = round(r["qty"] * r["price"], 2)
        else:
            r["amount"] = round(r["amount"] * f, 2)


def _headers(rng, *roles) -> dict:
    """One header per role from its pool, all different."""
    out = {}
    for r in roles:
        pool = [h for h in POOLS[r.split(".")[0]] if h not in out.values()]
        out[r] = rng.choice(pool)
    return out


def _start(rng) -> dt.datetime:
    return dt.datetime(rng.randint(2021, 2025), rng.randint(1, 12), 1)


def _day(start, rng, days: int, lo: float = 0.0, hi: float = 1.0) -> dt.datetime:
    return start + dt.timedelta(days=rng.randint(int(lo * days), max(int(lo * days), int(hi * days) - 1)))


def _table(cols: list, rows: list, top: int = 0) -> list:
    """Header plus data rows for dict rows; cols is [(key, header)]. Each dict
    gets its Excel row in '_row'."""
    out = [[h for _, h in cols]]
    for i, r in enumerate(rows):
        r["_row"] = top + 2 + i
        out.append([r.get(k) for k, _ in cols])
    return out


def _sheet_name(rng, name: str, used: list) -> str:
    base = _word(rng, 2)[:12]
    tab = base
    while tab in used:
        tab = f"{base}{len(used)}"
    used.append(tab)
    return tab


def _log(rng, n: int | None = None, sites: int | None = None, staff: int | None = None,
         items: int | None = None, days: int | None = None) -> dict:
    """A plain activity log: date, site, clerk, item, qty, price, amount, a unique
    document number. Sites share clerks and items and spread over the whole span."""
    n = n or rng.randint(240, 420)
    start, days = _start(rng), days or rng.randint(240, 420)
    b = {"start": start, "days": days, "sites": _names(rng, sites or rng.randint(4, 6)),
         "staff": [f"{f} {l}" for f, l in _people(rng, staff or rng.randint(5, 8))],
         "items": _names(rng, items or rng.randint(10, 16))}
    b["price"] = {it: round(rng.uniform(5, 9), 2) for it in b["items"]}
    rows = []
    for _ in range(n):
        it, qty = rng.choice(b["items"]), rng.randint(1, 6)
        rows.append({"date": _day(start, rng, days), "site": rng.choice(b["sites"]),
                     "person": rng.choice(b["staff"]), "item": it, "qty": qty, "price": b["price"][it],
                     "amount": round(qty * b["price"][it], 2)})
    b["rows"] = rows
    b["prefix"], b["first_no"] = _caps(rng, rng.randint(2, 3)), rng.randint(1000, 8000)
    return b


def _number(b: dict) -> None:
    """Sort by date and give each row the next document number."""
    b["rows"].sort(key=lambda r: r["date"])
    for i, r in enumerate(b["rows"]):
        r["doc"] = f"{b['prefix']}-{b['first_no'] + i}"


def _log_cols(h: dict, *extra) -> list:
    cols = [("date", h["date"]), ("doc", h["doc"]), ("site", h["site"]), ("person", h["person"]),
            ("item", h["item"])]
    return cols + list(extra) + [("qty", h["qty"]), ("price", h["price"]), ("amount", h["money"])]


def _log_headers(rng, *more) -> dict:
    return _headers(rng, "date", "doc", "site", "person", "item", "qty", "price", "money", *more)


# --------------------------------------------------------------------------
# traps (rank 13: codes)
# --------------------------------------------------------------------------
@trap("codes")
def _code_minority(rng, twin):
    """An integer code column (3 to 8 values) where one minority code carries
    most of the money. Twin: codes with a one-to-one name column, a plain-word
    category and Qty 1 to 6, money spread evenly."""
    b = _log(rng)
    h = _log_headers(rng, "code", "category")
    k = rng.randint(3, 8)
    codes = sorted(rng.sample(range(1, 30), k))
    if twin:
        names = _names(rng, k, 3)
        cats = _names(rng, rng.randint(3, 5), 3)
        for r in b["rows"]:
            i = rng.randrange(k)
            r["code"], r["code_name"], r["cat"] = codes[i], names[i], rng.choice(cats)
        _number(b)
        cols = _log_cols(h, ("code", h["code"]), ("code_name", h["code"] + " Name"), ("cat", h["category"]))
        return _table(cols, b["rows"]), [{"columns": [h["code"], h["category"], h["qty"]], "rows": []}]
    minority = rng.choice(codes)
    share = rng.uniform(0.05, 0.09)
    for r in b["rows"]:
        if rng.random() < share:
            r["code"] = minority
            r["price"] = round(r["price"] * rng.uniform(8, 15), 2)
            r["amount"] = round(r["qty"] * r["price"], 2)
        else:
            r["code"] = rng.choice([c for c in codes if c != minority])
    _number(b)
    rows = _table(_log_cols(h, ("code", h["code"])), b["rows"])
    hit = [r for r in b["rows"] if r["code"] == minority]
    return rows, [{"columns": [h["code"], h["money"]], "col": h["code"], "values": codes, "value": minority,
                   "rows": [r["_row"] for r in hit], "money": round(sum(r["amount"] for r in hit), 2)}]


@trap("codes")
def _status_rare(rng, twin):
    """A status column holding one dominant code and two rare ones (codes 1 to 3
    letters wide, or short words). Twin: a category of plain words, no status header."""
    b = _log(rng, n=rng.randint(160, 300))
    h = _log_headers(rng, "status", "category")
    if twin:
        cats = _names(rng, 3, 3)
        for r in b["rows"]:
            r["cat"] = rng.choice(cats)
        _number(b)
        return _table(_log_cols(h, ("cat", h["category"])), b["rows"]), [{"columns": [h["category"]], "rows": []}]
    if rng.random() < 0.3:
        main, *rare = _names(rng, 3, rng.randint(1, 2))          # short words: 'Ka', 'Venlo'
    else:
        main, *rare = _codes(rng, 3, rng.randint(1, 3))
    for r in b["rows"]:
        r["status"] = main
    for code in rare:
        for r in rng.sample(b["rows"], rng.randint(3, 7)):
            r["status"] = code
    _number(b)
    rows = _table(_log_cols(h, ("status", h["status"])), b["rows"])
    return rows, [{"columns": [h["status"]], "col": h["status"], "value": main, "values": rare,
                   "rows": [r["_row"] for r in b["rows"] if r["status"] in rare]}]


@trap("codes")
def _code_inside(rng, twin):
    """Lines whose type (4 or 5 words) each post to one 4-digit account code,
    except 6 to 14 lines of one type posted to another type's code. Twin: every
    line of a type posts to its own code (the type names each code)."""
    h = _headers(rng, "date", "doc", "money")
    typ = rng.choice(["Type", "Line Type", "Txn Type", "Kind"])
    acct = rng.choice(["Account", "GL", "Acct Code", "Ledger Code"])
    k = rng.randint(4, 5)
    kinds = _names(rng, k, 3)
    codes = [c * 100 for c in rng.sample(range(10, 90), k)]
    code_of = dict(zip(kinds, codes))
    start, days = _start(rng), rng.randint(200, 360)
    pre, first = _caps(rng, 2), rng.randint(1000, 8000)
    rows = []
    for i in range(rng.randint(420, 560)):
        kd = rng.choice(kinds)
        rows.append({"date": _day(start, rng, days), "kind": kd, "code": code_of[kd],
                     "amount": round(rng.uniform(20, 900), 2)})
    rows.sort(key=lambda r: r["date"])
    for i, r in enumerate(rows):
        r["doc"] = f"{pre}-{first + i}"
    odd, other = rng.sample(kinds, 2)
    hit = [] if twin else rng.sample([r for r in rows if r["kind"] == odd], rng.randint(6, 14))
    for r in hit:
        r["code"] = code_of[other]
    table = _table([("date", h["date"]), ("doc", h["doc"]), ("kind", typ), ("code", acct), ("amount", h["money"])],
                   rows)
    return table, [{"columns": [acct, typ], "col": acct, "by": typ, "value": odd, "code": code_of[other],
                    "usual": code_of[odd], "money": round(sum(r["amount"] for r in hit), 2),
                    "rest": sum(1 for r in rows if r["kind"] == odd) - len(hit),
                    "rows": [r["_row"] for r in hit]}]


@trap("codes")
def _lookup_mixed(rng, twin):
    """A log whose site column holds a lookup's short codes on most rows and the
    same sites' full names on the rest; the lookup, on its own tab, lists each
    code with its name. Twin: codes only."""
    b = _log(rng, n=rng.randint(200, 360))
    h = _log_headers(rng)
    codes = _codes(rng, len(b["sites"]), rng.randint(2, 3))
    name_of = dict(zip(codes, b["sites"]))
    p_names = 0.0 if twin else rng.uniform(0.15, 0.4)
    for r in b["rows"]:
        code = rng.choice(codes)
        r["site"] = name_of[code] if rng.random() < p_names else code
    _number(b)
    code_h, name_h = f"{h['site']} Code", f"{h['site']} Name"
    lookup = [[code_h, name_h]] + [[c, name_of[c]] for c in codes]
    table = _table(_log_cols(h), b["rows"])
    named = [r for r in b["rows"] if r["site"] in b["sites"]]
    return table, [{"columns": [h["site"]], "col": h["site"], "names": len(named),
                    "codes": len(b["rows"]) - len(named), "lookup_cols": [code_h, name_h],
                    "map": {name_of[c]: c for c in codes}, "rows": [r["_row"] for r in named]}], [lookup]


# codes from well-known standards: states and provinces, countries, currencies, apparel sizes
_STD = {"state": ["AL", "AZ", "CA", "CO", "FL", "GA", "IL", "MA", "MI", "MN", "NC", "NJ", "NY", "OH", "OR", "PA",
                  "TX", "VA", "WA", "WI", "ON", "BC", "QC", "AB"],
        "country2": ["US", "CA", "MX", "GB", "DE", "FR", "IT", "ES", "NL", "SE", "JP", "AU", "BR", "IN", "CN", "IE"],
        "country3": ["USA", "CAN", "MEX", "GBR", "DEU", "FRA", "ITA", "ESP", "NLD", "SWE", "JPN", "AUS", "BRA",
                     "IND", "CHN", "IRL"],
        "currency": ["USD", "EUR", "GBP", "CAD", "AUD", "JPY", "CHF", "MXN", "SEK", "NZD"],
        "size": ["XS", "S", "M", "L", "XL", "XXL"]}


@trap("unit")
def _standard_sets(rng, twin):
    """A log with columns written in well-known standards, each with a few rare
    values: a ship-to state or province, a country (two letters under a header
    that says country, or three letters under one that does not), an apparel
    size and a currency. The plant's currency column names 2 or 3 currencies,
    the others on 5 to 15% of rows: a question about the money's unit. Twin: one
    currency on every row, and nothing to ask."""
    b = _log(rng)
    h = _log_headers(rng)
    three = rng.random() < 0.5
    std = {"state": (rng.choice(["Ship State", "State", "Bill To State", "Province"]),
                     rng.sample(_STD["state"], rng.randint(4, 8))),
           "country": (rng.choice(["Origin", "Destination"] if three else ["Country", "Ship Country", "Country Code"]),
                       rng.sample(_STD["country3" if three else "country2"], rng.randint(3, 6))),
           "size": (rng.choice(["Size", "Fit Size"]), rng.sample(_STD["size"], rng.randint(3, 6)))}
    main, *other = rng.sample(_STD["currency"], rng.randint(2, 3))
    share = rng.uniform(0.05, 0.15)
    for r in b["rows"]:
        for k, (_hd, vals) in std.items():
            r[k] = rng.choices(vals, weights=[len(vals) - i for i in range(len(vals))])[0]
        r["ccy"] = rng.choice(other) if not twin and rng.random() < share else main
    _number(b)
    ccy = rng.choice(["Currency", "Ccy", "Cur"])
    rows = _table(_log_cols(h, *[(k, hd) for k, (hd, _v) in std.items()], ("ccy", ccy)), b["rows"])
    if twin:
        return rows, [{"columns": [hd for hd, _v in std.values()] + [ccy], "rows": []}]
    return rows, [{"columns": [ccy, h["money"]], "col": ccy, "value": main, "values": other,
                   "rows": [r["_row"] for r in b["rows"] if r["ccy"] != main]}]


# --------------------------------------------------------------------------
# rank 14: one date where the export changes
# --------------------------------------------------------------------------
@trap("boundary")
def _system_change(rng, twin):
    """A log exported by one system and then another from a random date: text
    dates, 'LAST, FIRST' names, site names, integer percent adjustments and one ID
    prefix before; datetimes, 'First Last' names, site codes, dollar adjustments
    and another prefix after. A new site with its own staff opens after the
    switch. Twin: one format throughout, and one item sold only mid-year."""
    h = _headers(rng, "date", "doc", "party_id", "party", "site", "item", "adjust", "money")
    start, days = _start(rng), rng.randint(300, 400)
    split = start + dt.timedelta(days=int(days * rng.uniform(0.35, 0.65)))
    sites = _names(rng, rng.randint(3, 5))
    codes = dict(zip(sites, _codes(rng, len(sites), rng.randint(2, 4))))
    people = _people(rng, rng.randint(8, 12))
    ids = [f"{_caps(rng, 1)}{n}" for n in rng.sample(range(100, 999), len(people))]
    home = {i: sites[k % len(sites)] for k, i in enumerate(ids)}
    new_site, new_people = _codes(rng, 1, 3)[0], _people(rng, 2)
    new_ids = [f"{_caps(rng, 1)}{n}" for n in rng.sample(range(100, 999), 2)]
    items = _names(rng, rng.randint(6, 10))
    seasonal = items[0] if twin else None
    pa, pb = _codes(rng, 2, rng.randint(2, 3))
    rows = []
    for _ in range(rng.randint(320, 480)):
        d = _day(start, rng, days)
        after = d >= split and not twin
        if after and rng.random() < 0.12:
            k = rng.randrange(2)
            pid, (first, last), site = new_ids[k], new_people[k], new_site
        else:
            k = rng.randrange(len(ids))
            pid, (first, last) = ids[k], people[k]
            site = codes[home[pid]] if after else home[pid]
        item = rng.choice(items[1:] if seasonal else items)
        if seasonal and 0.35 * days <= (d - start).days <= 0.65 * days and rng.random() < 0.2:
            item = seasonal
        rows.append({"date": d, "pid": pid, "person": f"{first} {last}" if after or twin
                     else f"{last.upper()}, {first.upper()}", "site": site, "item": item,
                     "adj": round(rng.uniform(0.5, 15), 2) if after or twin else rng.choice([10, 15, 20, 25]),
                     "amount": round(rng.uniform(20, 400), 2)})
    rows.sort(key=lambda r: r["date"])
    na = nb = 0
    for r in rows:
        if r["date"] >= split and not twin:
            nb += 1
            r["doc"] = f"{pb}{r['date'].year % 100:02d}-{nb:04d}"
        else:
            na += 1
            r["doc"] = f"{pa}-{na:05d}"
            if not twin:
                r["date"] = r["date"].strftime("%m/%d/%Y")
    cols = [("date", h["date"]), ("doc", h["doc"]), ("pid", h["party_id"]), ("person", h["party"]),
            ("site", h["site"]), ("item", h["item"]), ("adj", h["adjust"]), ("amount", h["money"])]
    table = _table(cols, rows)
    if twin:
        return table, [{"columns": [h["date"], h["item"]], "seasonal": seasonal, "rows": []}]
    return table, [{"date": split, "col": h["date"],
                    "columns": [h["date"], h["doc"], h["party"], h["site"], h["adjust"]],
                    "switches": {h["date"]: "text date to date", h["doc"]: f"{pa}- to {pb}",
                                 h["party"]: "LAST, FIRST to First Last", h["site"]: "names to codes",
                                 h["adjust"]: "whole percents to dollars"},
                    "handoffs": [[s, codes[s]] for s in sites], "new_value": new_site, "via": h["party_id"],
                    "rows": [r["_row"] for r in rows if isinstance(r["date"], str)]}]


# --------------------------------------------------------------------------
# rank 15: copies, double uploads, void pairs
# --------------------------------------------------------------------------
@trap("copies")
def _reimport(rng, twin):
    """Sales lines where a window of dates was loaded again under a new document
    number family. Twin: split payments (two or three lines on one date and client
    whose amounts sum to one order), repeat orders (the same client and item on
    a later date) and orders entered twice (the same date, client and amount),
    all numbered in one family."""
    h = _headers(rng, "doc", "date", "party", "item", "qty", "money")
    start, days = _start(rng), rng.randint(200, 360)
    clients, items = _names(rng, rng.randint(24, 40)), _names(rng, rng.randint(8, 14))
    rows = [{"date": _day(start, rng, days), "party": rng.choice(clients), "item": rng.choice(items),
             "qty": rng.randint(1, 6), "amount": round(rng.uniform(15, 600), 2)}
            for _ in range(rng.randint(240, 400))]
    rows.sort(key=lambda r: r["date"])
    pa, pb = _codes(rng, 2, rng.randint(2, 3))
    first = rng.randint(1000, 9000)
    for i, r in enumerate(rows):
        r["doc"] = f"{pa}-{first + i}"
    if twin:
        extra = []
        for r in rng.sample(rows, rng.randint(6, 10)):          # split payments: parts that sum to the order
            cents = round(r["amount"] * 100)
            cuts = sorted(rng.sample(range(1, cents), rng.randint(1, 2)))
            parts = [b - a for a, b in zip([0] + cuts, cuts + [cents])]
            r["amount"] = parts[0] / 100
            extra += [dict(r, amount=p / 100) for p in parts[1:]]
        for r in rng.sample(rows, rng.randint(6, 10)):          # repeat orders: same client and item, later
            q = rng.randint(1, 6)
            extra.append(dict(r, date=r["date"] + dt.timedelta(days=rng.randint(7, 60)), qty=q,
                              amount=round(rng.uniform(15, 600), 2)))
        extra += [dict(r) for r in rng.sample(rows, rng.randint(4, 8))]      # the same order entered twice
        rows = sorted(rows + extra, key=lambda r: r["date"])
        for i, r in enumerate(rows):
            r["doc"] = f"{pa}-{first + i}"
        return _table([("doc", h["doc"]), ("date", h["date"]), ("party", h["party"]), ("item", h["item"]),
                       ("qty", h["qty"]), ("amount", h["money"])], rows), [{"columns": [h["doc"]], "rows": []}]
    lo = rng.uniform(0.3, 0.7)
    w0 = start + dt.timedelta(days=int(lo * days))
    w1 = w0 + dt.timedelta(days=rng.randint(12, 25))
    width = rng.randint(4, 6)
    out, copies = [], []
    for r in rows:
        out.append(r)
        if w0 <= r["date"] <= w1:
            copies.append(dict(r, doc=f"{pb}{len(copies) + 1:0{width}d}"))
            out.append(copies[-1])
    table = _table([("doc", h["doc"]), ("date", h["date"]), ("party", h["party"]), ("item", h["item"]),
                    ("qty", h["qty"]), ("amount", h["money"])], out)
    return table, [{"columns": [h["doc"], h["date"], h["party"], h["money"]], "col": h["doc"],
                    "families": [f"{pa}-", pb], "window": [w0, w1], "copies": len(copies),
                    "example": [next(r["doc"] for r in rows if w0 <= r["date"] <= w1), copies[0]["doc"]]
                    if copies else [], "rows": [c["_row"] for c in copies]}]


@trap("near_key")
def _double_upload(rng, twin):
    """A weekly count panel (week x site x item) where one week's block for one
    site was loaded twice at two submit times. Twin: an event log where the same
    day, site and item repeat here and there."""
    h = _headers(rng, "date", "site", "item", "qty", "money", "submitted")
    start = _start(rng)
    start -= dt.timedelta(days=start.weekday())
    sites, items = _names(rng, rng.randint(3, 4)), _names(rng, rng.randint(10, 16))
    cost = {it: round(rng.uniform(2, 30), 2) for it in items}
    rows = []
    if twin:
        days = rng.randint(90, 160)
        for _ in range(rng.randint(300, 500)):
            d, it = _day(start, rng, days), rng.choice(items)
            q = rng.randint(1, 12)
            rows.append({"date": d, "site": rng.choice(sites), "item": it, "qty": q,
                         "amount": round(q * cost[it], 2),
                         "at": d + dt.timedelta(hours=rng.randint(7, 20), minutes=rng.randint(0, 59))})
        for r in rng.sample(rows, len(rows) // 20):
            rows.append(dict(r, at=r["at"] + dt.timedelta(minutes=rng.randint(5, 300))))
        rows.sort(key=lambda r: (r["date"], r["at"]))
    else:
        weeks = rng.randint(8, 12)
        for w in range(weeks):
            wk = start + dt.timedelta(weeks=w)
            for s in sites:
                at = wk + dt.timedelta(days=rng.randint(1, 3), hours=rng.randint(7, 18), minutes=rng.randint(0, 59))
                for it in items:
                    q = rng.randint(0, 40)
                    rows.append({"date": wk, "site": s, "item": it, "qty": q, "amount": round(q * cost[it], 2),
                                 "at": at})
        w, s = rng.randrange(weeks), rng.choice(sites)
        block = [r for r in rows if r["date"] == start + dt.timedelta(weeks=w) and r["site"] == s]
        again = block[0]["at"] + dt.timedelta(hours=rng.randint(2, 72), minutes=rng.randint(1, 59))
        copies = [dict(r, at=again) for r in block]
        k = rows.index(block[-1]) + 1
        rows[k:k] = copies
    table = _table([("date", h["date"]), ("site", h["site"]), ("item", h["item"]), ("qty", h["qty"]),
                    ("amount", h["money"]), ("at", h["submitted"])], rows)
    if twin:
        return table, [{"columns": [h["date"], h["site"], h["item"]], "rows": []}]
    return table, [{"columns": [h["date"], h["site"], h["submitted"]], "week": block[0]["date"], "site": s,
                    "times": [block[0]["at"], again], "block_rows": len(block),
                    "rows": [r["_row"] for r in copies]}]


@trap("pairs")
def _void_pairs(rng, twin):
    """A register where k numbers appear twice, once under the regular type code
    and once under a void code, with the same amount. Twin: a type column of plain
    words, and a few numbers used twice for two different payments (another
    payee and date, or another amount). The twin is silent for the twin-pairs
    detector only: a number really used twice is a fair v0.1 duplicates question
    (find_dupes_), expected on some seeds, so any sweep that says 'the twin asks
    nothing' over this trap leaves find_dupes_ out."""
    h = _headers(rng, "doc", "date", "party", "code", "money")
    start, days = _start(rng), rng.randint(150, 300)
    payees = _names(rng, rng.randint(20, 40))
    rows = [{"date": _day(start, rng, days), "party": rng.choice(payees), "amount": round(rng.uniform(20, 2500), 2)}
            for _ in range(rng.randint(150, 300))]
    rows.sort(key=lambda r: r["date"])
    first = rng.randint(1000, 9000)
    for i, r in enumerate(rows):
        r["doc"] = first + i
    cols = [("doc", h["doc"]), ("date", h["date"]), ("party", h["party"]), ("type", h["code"]),
            ("amount", h["money"])]
    if twin:
        kinds = _names(rng, 2, 3)
        for r in rows:
            r["type"] = rng.choice(kinds)
        for k, r in enumerate(rng.sample(rows, rng.randint(4, 8))):          # a number reused for another payment
            other = rng.choice([p for p in payees if p != r["party"]])
            twin_row = dict(r, party=other, date=r["date"] + dt.timedelta(days=rng.randint(1, 40))) if k % 2 \
                else dict(r, amount=round(r["amount"] + rng.uniform(5, 200), 2))
            rows.insert(rows.index(r) + 1, twin_row)
        return _table(cols, rows), [{"columns": [h["doc"], h["code"]], "rows": []}]
    reg, void = _codes(rng, 2, 1)
    for r in rows:
        r["type"] = reg
    picked = rng.sample(rows, rng.randint(4, 12))
    pairs = []
    for r in sorted(picked, key=lambda x: x["doc"]):
        v = dict(r, type=void, date=r["date"] + dt.timedelta(days=rng.randint(0, 5)))
        rows.insert(rows.index(r) + 1, v)
        pairs.append((r, v))
    table = _table(cols, rows)
    return table, [{"columns": [h["doc"], h["code"], h["money"]], "col": h["code"], "values": [reg, void],
                    "pairs": len(pairs), "numbers": [r["doc"] for r, _ in pairs],
                    "rows": [x["_row"] for p in pairs for x in p]}]


# --------------------------------------------------------------------------
# rank 16: odd groups
# --------------------------------------------------------------------------
@trap("odd_group")
def _odd_group(rng, twin):
    """One site present only in the middle 30% of dates, with its own clerks. Twin:
    a seasonal site in the same window that shares clerks and items."""
    b = _log(rng, n=rng.randint(300, 450))
    h = _log_headers(rng)
    odd = b["sites"][-1]
    own = [f"{f} {l}" for f, l in _people(rng, rng.randint(1, 2))] if not twin else []
    for r in b["rows"]:
        r["site"] = rng.choice(b["sites"][:-1])
    for r in rng.sample(b["rows"], rng.randint(25, 45)):
        r.update(site=odd, date=_day(b["start"], rng, b["days"], 0.35, 0.65))
        if own:
            r["person"] = rng.choice(own)
    _number(b)
    table = _table(_log_cols(h), b["rows"])
    hit = [r for r in b["rows"] if r["site"] == odd]
    return table, [{"columns": [h["site"], h["person"], h["date"]], "col": h["site"], "value": odd,
                    "private": own, "window": [0.35, 0.65], "rows": [] if twin else [r["_row"] for r in hit]}]


@trap("odd_group")
def _share_group(rng, twin):
    """One site with about 12% of the rows and 45% of the money, from prices far
    above its peers. Twin: every site alike."""
    b = _log(rng, n=rng.randint(300, 450), sites=5)
    h = _log_headers(rng)
    heavy = b["sites"][0]
    for r in b["rows"]:
        r["site"] = rng.choice(b["sites"][1:])
    if not twin:
        for r in rng.sample(b["rows"], int(0.12 * len(b["rows"]))):
            r["site"] = heavy
        rest = sum(r["amount"] for r in b["rows"] if r["site"] != heavy)
        own = sum(r["amount"] for r in b["rows"] if r["site"] == heavy)
        f = 0.45 / 0.55 * rest / own
        for r in b["rows"]:
            if r["site"] == heavy:
                r["price"] = round(r["price"] * f, 2)
                r["amount"] = round(r["qty"] * r["price"], 2)
    _number(b)
    table = _table(_log_cols(h), b["rows"])
    return table, [{"columns": [h["site"], h["money"]], "col": h["site"], "value": None if twin else heavy,
                    "rows": [] if twin else [r["_row"] for r in b["rows"] if r["site"] == heavy]}]


@trap("odd_group")
def _late_group(rng, twin):
    """A site that first appears in the last 10% of dates. Twin: every site from
    the start."""
    b = _log(rng, n=rng.randint(300, 450))
    h = _log_headers(rng)
    late = b["sites"][-1]
    for r in b["rows"]:
        if r["site"] == late:
            r["site"] = rng.choice(b["sites"][:-1])
    if not twin:
        for r in rng.sample(b["rows"], rng.randint(22, 35)):
            r.update(site=late, date=_day(b["start"], rng, b["days"], 0.9, 1.0))
    _number(b)
    table = _table(_log_cols(h), b["rows"])
    return table, [{"columns": [h["site"], h["date"]], "col": h["site"], "value": None if twin else late,
                    "rows": [] if twin else [r["_row"] for r in b["rows"] if r["site"] == late]}]


def _titled(rng, b: dict, lo: float, hi: float, covers: bool) -> tuple:
    """(title, end) for a log whose title names a period of months: from the
    first row's month to the month holding a date between lo and hi of the span
    (covers: to the last row's month, so every row falls inside it)."""
    import calendar
    first = min(r["date"] for r in b["rows"])
    last = max(r["date"] for r in b["rows"]) if covers else \
        b["start"] + dt.timedelta(days=int(b["days"] * rng.uniform(lo, hi)))
    end = dt.datetime(last.year, last.month, calendar.monthrange(last.year, last.month)[1])
    word = rng.choice(["Sales", "Activity", "Orders", "Ledger", "Log"])
    return f"{_word(rng)} {word} {_MONTHS[first.month - 1]} {first.year} to {_MONTHS[end.month - 1]} {end.year}", end


@trap("odd_group")
def _title_late(rng, twin):
    """A log whose title names a period of months that the rows run past, and a
    site whose first rows come after the period ends (well before the last 10% of
    the dates). Twin: the same site at the same dates under a title whose period
    covers every row."""
    b = _log(rng, n=rng.randint(300, 450))
    h = _log_headers(rng)
    late = b["sites"][-1]
    for r in b["rows"]:
        if r["site"] == late:
            r["site"] = rng.choice(b["sites"][:-1])
    _title, end = _titled(rng, b, 0.62, 0.76, covers=False)
    after = (end - b["start"]).days + 1
    for r in rng.sample(b["rows"], rng.randint(24, 36)):
        r.update(site=late, date=b["start"] + dt.timedelta(days=rng.randint(after, b["days"] - 1)))
    if twin:
        _title, end = _titled(rng, b, 0, 0, covers=True)
    _number(b)
    table = [[_title], []] + _table(_log_cols(h), b["rows"], top=2)
    return table, [{"columns": [h["site"], h["date"]], "col": h["site"], "value": None if twin else late,
                    "title": _title, "end": end,
                    "rows": [] if twin else [r["_row"] for r in b["rows"] if r["site"] == late]}]


@trap("boundary")
def _title_switch(rng, twin):
    """A log whose title names a period of months that the rows run past, and an
    adjustment column written as whole percents from 4 values up to the end of
    that period and as dollars with cents after it. No other column changes, so
    only the title's date puts the switch up for reading. Twin: dollars with
    cents throughout, under the same title."""
    b = _log(rng, n=rng.randint(320, 460))
    h = _log_headers(rng, "adjust")
    _title, end = _titled(rng, b, 0.4, 0.6, covers=False)
    for r in b["rows"]:
        r["adj"] = round(rng.uniform(0.5, 15), 2) if r["date"] > end or twin else rng.choice([10, 15, 20, 25])
    _number(b)
    table = [[_title], []] + _table(_log_cols(h, ("adj", h["adjust"])), b["rows"], top=2)
    return table, [{"columns": [h["adjust"]], "col": h["date"], "title": _title, "end": end,
                    "date": end + dt.timedelta(days=1),
                    "rows": [] if twin else [r["_row"] for r in b["rows"] if r["date"] > end]}]


@trap("unit_price")
def _per_case(rng, twin):
    """One category's items priced per case of 4 to 48 while the rest are priced
    per unit. Twin: every category priced per unit."""
    b = _log(rng, n=rng.randint(260, 420), items=rng.randint(12, 18))
    h = _log_headers(rng, "category")
    cats = _names(rng, rng.randint(4, 6), 3)
    cat_of = {it: cats[i % len(cats)] for i, it in enumerate(b["items"])}
    pack = rng.randint(4, 48)
    odd = cats[0]
    for r in b["rows"]:
        r["cat"] = cat_of[r["item"]]
        if r["cat"] == odd and not twin:
            r["price"] = round(b["price"][r["item"]] * pack, 2)
            r["amount"] = round(r["qty"] * r["price"], 2)
    _number(b)
    table = _table(_log_cols(h, ("cat", h["category"])), b["rows"])
    return table, [{"columns": [h["category"], h["price"]], "col": h["category"], "value": None if twin else odd,
                    "pack": None if twin else pack,
                    "rows": [] if twin else [r["_row"] for r in b["rows"] if r["cat"] == odd]}]


# --------------------------------------------------------------------------
# rank 17: blanks in the main measure
# --------------------------------------------------------------------------
@trap("blanks")
def _sparse_blank(rng, twin):
    """The main money column blank on about 0.3% of rows, all at one site within
    a few days. Twin: no blanks in the measure, and a notes column 90% blank."""
    b = _log(rng, n=rng.randint(600, 900))
    h = _log_headers(rng, "note")
    _number(b)
    if twin:
        for r in b["rows"]:
            r["note"] = f"{_word(rng)} {_word(rng)}" if rng.random() < 0.1 else None
        return _table(_log_cols(h, ("note", h["note"])), b["rows"]), [{"columns": [h["note"]], "rows": []}]
    site = rng.choice(b["sites"])
    k = max(2, round(0.003 * len(b["rows"])))
    at = rng.randrange(len(b["rows"]) - 40)
    near = [r for r in b["rows"][at:at + 40]][:k]
    for r in near:
        r["site"], r["amount"] = site, None
    table = _table(_log_cols(h), b["rows"])
    return table, [{"columns": [h["money"]], "col": h["money"], "site": site, "rows": [r["_row"] for r in near]}]


# --------------------------------------------------------------------------
# rank 18: single entities unlike their peers
# --------------------------------------------------------------------------
@trap("derive")
def _derive(rng, twin):
    """A sales log with a quantity, a price and an adjustment taken off about 30%
    of the lines, but no amount column. Twin: the same log with its amount column
    (quantity times price, less the adjustment)."""
    b = _log(rng, n=rng.randint(160, 320))
    h = _log_headers(rng, "adjust")
    for r in b["rows"]:
        r["adj"] = round(rng.uniform(0.5, 4), 2) if rng.random() < 0.3 else 0
        r["amount"] = round(r["qty"] * r["price"] - r["adj"], 2)
    _number(b)
    cols = [c for c in _log_cols(h, ("adj", h["adjust"])) if twin or c[0] != "amount"]
    table = _table(cols, b["rows"])
    return table, [{"columns": [h["qty"], h["price"], h["adjust"]], "qty": h["qty"], "price": h["price"],
                    "adj": h["adjust"], "rows": [] if twin else [r["_row"] for r in b["rows"]]}]


_KINDS_OF_ACCOUNT = [("Balance", "Expense"), ("Asset", "Expense"), ("Balance Sheet", "Income Statement"),
                     ("Liability", "Cost")]


@trap("blanks")
def _blank_slice(rng, twin):
    """Lines on 10 to 14 accounts, a chart tab that types each account one of two
    kinds, and a class column always blank on the lines of the first kind's
    accounts and blank on 8% to 15% of the other lines. Twin: the class is blank
    on about as many lines, spread over every account."""
    h = _headers(rng, "date", "doc", "account", "money")
    cls = rng.choice(["Class", "Division", "Cost Center", "Program"])
    kind_h = rng.choice(["Type", "Kind", "Statement", "Report Group"])
    never, filled = rng.choice(_KINDS_OF_ACCOUNT)
    accts = _names(rng, rng.randint(10, 14), 3)
    nb = rng.randint(3, 5)
    bal, exp = accts[:nb], accts[nb:]
    classes = _names(rng, rng.randint(3, 4))
    start, days = _start(rng), rng.randint(200, 360)
    share, miss = rng.uniform(0.3, 0.45), rng.uniform(0.08, 0.15)
    even = share + (1 - share) * miss
    pre, first = _caps(rng, 2), rng.randint(1000, 8000)
    rows = []
    for k in range(rng.randint(300, 460)):
        on_bal = rng.random() < share
        blank = rng.random() < even if twin else on_bal or rng.random() < miss
        rows.append({"date": _day(start, rng, days), "doc": f"{pre}-{first + k}",
                     "acct": rng.choice(bal if on_bal else exp), "cls": None if blank else rng.choice(classes),
                     "amount": round(rng.uniform(20, 900), 2)})
    rows.sort(key=lambda r: r["date"])
    table = _table([("date", h["date"]), ("doc", h["doc"]), ("acct", h["account"]), ("cls", cls),
                    ("amount", h["money"])], rows)
    chart = {"name": f"{_word(rng)} Chart", "rows": [[h["account"], kind_h]] + [[a, never if a in bal else filled]
                                                                                for a in accts]}
    hit = [r for r in rows if r["acct"] in exp and r["cls"] is None]
    return table, [{"columns": [cls, h["account"]], "col": cls, "by": f"{kind_h} on {chart['name']}",
                    "never": never, "filled": filled, "money": h["money"],
                    "never_rows": sum(1 for r in rows if r["acct"] in bal),
                    "slice_rows": sum(1 for r in rows if r["acct"] in exp),
                    "blank_money": round(sum(r["amount"] for r in hit), 2),
                    "total_money": round(sum(r["amount"] for r in rows), 2),
                    "rows": [] if twin else [r["_row"] for r in hit]}], [chart]


@trap("entity")
def _zero_activity(rng, twin):
    """Holders billed every month; one holder never pays. Twin: a ledger with a
    debit/credit pair where some accounts are only ever credited."""
    if twin:
        h = _headers(rng, "date", "account", "note")
        start, days = _start(rng), rng.randint(180, 360)
        accts = _names(rng, rng.randint(20, 28), 3)
        side = {a: rng.random() < 0.5 for a in accts}
        rows = []
        for a in accts:
            for _ in range(rng.randint(6, 12)):
                amt = round(rng.uniform(20, 900), 2)
                rows.append({"date": _day(start, rng, days), "acct": a, "dr": amt if side[a] else None,
                             "cr": None if side[a] else amt})
        rows.sort(key=lambda r: r["date"])
        return _table([("date", h["date"]), ("acct", h["account"]), ("dr", "Debit"), ("cr", "Credit")], rows), \
            [{"columns": [h["account"], "Debit", "Credit"], "rows": []}]
    h = _headers(rng, "date", "party_id", "money")
    start = _start(rng)
    months = rng.randint(8, 12)
    pre = _caps(rng, 1)
    ids = [f"{pre}{n}" for n in sorted(rng.sample(range(100, 999), rng.randint(24, 36)))]
    never = rng.choice(ids)
    rows = []
    for m in range(months):
        d = dt.datetime(start.year + (start.month - 1 + m) // 12, (start.month - 1 + m) % 12 + 1, 1)
        for i in ids:
            due = round(rng.uniform(400, 1800), 2)
            paid = 0 if i == never or rng.random() < 0.02 else due
            rows.append({"date": d, "id": i, "due": due, "paid": paid})
    paid_h = rng.choice(["Paid", "Received", "Collected"])
    table = _table([("date", h["date"]), ("id", h["party_id"]), ("due", h["money"]), ("paid", paid_h)], rows)
    return table, [{"columns": [h["party_id"], paid_h], "col": h["party_id"], "value": never,
                    "rows": [r["_row"] for r in rows if r["id"] == never]}]


@trap("entity")
def _sentinel(rng, twin, unpaid=False):
    """A pay register with one test ID: its number far outside the sequence and
    all 9s, a constant amount on every row, rows only late in the span. Twin: a
    salaried person with constant pay (whole dollars on some seeds), a normal
    number and rows throughout. unpaid: the test ID's last 5 rows only, each at 0."""
    h = _headers(rng, "date", "party_id", "party", "qty", "money")
    start = _start(rng)
    periods = rng.randint(12, 24)
    pre, base = _caps(rng, 1), rng.randint(1000, 5000)
    staff, no = [], base
    for f, l in _people(rng, rng.randint(20, 32)):
        no += rng.randint(1, 3)          # a normal sequence with small gaps
        staff.append((f"{pre}{no}", f"{f} {l}"))
    rate = {i: round(rng.uniform(15, 40), 2) for i, _ in staff}
    steady = staff[rng.randrange(len(staff))][0] if twin else None
    salary = rng.randint(1500, 3500) if rng.random() < 0.5 else round(rng.uniform(1500, 3500), 2)
    rows = []
    for p in range(periods):
        d = start + dt.timedelta(days=14 * p)
        for i, name in staff:
            hrs = round(rng.uniform(20, 80), 2)
            rows.append({"date": d, "id": i, "name": name, "hrs": hrs,
                         "gross": salary if i == steady else round(hrs * rate[i], 2)})
    plant = []
    if not twin:
        test_id = f"{pre}{'9' * (len(str(base)) + 1)}"
        fixed = round(rng.uniform(100, 999), 2)
        late = [start + dt.timedelta(days=14 * p) for p in range(int(periods * 0.7), periods)]
        name = f"{_word(rng)} {_word(rng, 3)}"
        if unpaid:
            late, fixed = late[-5:], 0
        rows += [{"date": d, "id": test_id, "name": name, "hrs": 1, "gross": fixed} for d in late]
        rows.sort(key=lambda r: r["date"])
        plant = [test_id]
    table = _table([("date", h["date"]), ("id", h["party_id"]), ("name", h["party"]), ("hrs", h["qty"]),
                    ("gross", h["money"])], rows)
    return table, [{"columns": [h["party_id"], h["money"]], "col": h["party_id"],
                    "value": plant[0] if plant else steady,
                    "rows": [r["_row"] for r in rows if plant and r["id"] == plant[0]]}]


@trap("entity")
def _ratio_outlier(rng, twin):
    """Two money columns of one family (a list and a billed figure) that sit at
    90% to 100% of each other, except one row at about 30%. Twin: no such row."""
    h = _headers(rng, "date", "party_id")
    fam = rng.choice(["Amount", "Price", "Charge", "Fee"])
    a_h, b_h = f"List {fam}", f"Billed {fam}"
    start, days = _start(rng), rng.randint(120, 300)
    pre = _caps(rng, 2)
    ids = [f"{pre}{n}" for n in rng.sample(range(100, 999), rng.randint(20, 30))]
    rows = []
    for _ in range(rng.randint(120, 250)):
        lst = round(rng.uniform(50, 900), 2)
        rows.append({"date": _day(start, rng, days), "id": rng.choice(ids), "list": lst,
                     "billed": round(lst * rng.uniform(0.9, 1.0), 2)})
    rows.sort(key=lambda r: r["date"])
    odd = None
    if not twin:
        odd = rng.choice(rows)
        odd["billed"] = round(odd["list"] * rng.uniform(0.25, 0.35), 2)
    table = _table([("date", h["date"]), ("id", h["party_id"]), ("list", a_h), ("billed", b_h)], rows)
    return table, [{"columns": [a_h, b_h], "col": b_h, "value": odd["id"] if odd else None,
                    "rows": [odd["_row"]] if odd else []}]


# --------------------------------------------------------------------------
# rank 21: journals
# --------------------------------------------------------------------------
def _journal(rng, tax=False, contra=False, opening=False, ordinary=False, closing=False, last_ordinary=False,
             chart=False, plain_contra=False):
    """Balanced entries: sales credit an income account (plus, with tax, a
    companion line at a fixed rate), costs debit an expense account, cash takes
    the other side. contra: 4 lines on an account's unusual side, memos sharing a
    prefix. opening: one first entry carrying large balances in. ordinary: the
    first entry's memo says 'opening' in its ordinary sense (a grand opening).
    closing: one last entry closing the income out to equity. last_ordinary: the
    last entry's memo says 'closing' in its ordinary sense (store closing
    supplies). chart: a chart tab giving each account its normal side (Debit or
    Credit), returned as a third item. plain_contra: 4 lines on the income
    accounts' debit side with ordinary memos (no shared prefix); only the
    chart says they are on the unusual side."""
    h = _headers(rng, "entry", "date", "account", "note")
    start, days = _start(rng), rng.randint(200, 360)
    income, expense = _names(rng, 2 if chart else rng.randint(2, 3), 3), _names(rng, rng.randint(3, 5), 3)
    cash, tax_acct, equity = _names(rng, 3, 3)
    rate = rng.choice([0.03, 0.045, 0.05, 0.065, 0.07, 0.085])
    pre = _caps(rng, 2)
    entries = []
    for _ in range(rng.randint(56, 70)):
        amt = round(rng.uniform(100, 2000), 2)
        lines = [(rng.choice(income), None, amt, f"{_word(rng)} {_word(rng)}")]
        if tax:
            t = round(amt * rate, 2)
            lines.append((tax_acct, None, t, f"{_word(rng)} {_word(rng)}"))
            amt = round(amt + t, 2)
        entries.append([_day(start, rng, days, 0.02), [(cash, amt, None, f"{_word(rng)} {_word(rng)}")] + lines])
    for _ in range(rng.randint(12, 24)):
        amt = round(rng.uniform(50, 900), 2)
        entries.append([_day(start, rng, days, 0.02), [(rng.choice(expense), amt, None, f"{_word(rng)}"),
                                                        (cash, None, amt, f"{_word(rng)}")]])
    contra_prefix = rng.choice(["Refund", "Return", "Reversal", "Chargeback"])
    if contra:
        for _ in range(4):
            amt = round(rng.uniform(20, 300), 2)
            entries.append([_day(start, rng, days, 0.02), [(rng.choice(income), amt, None,
                                                            f"{contra_prefix}: {_word(rng)} {_word(rng)}"),
                                                           (cash, None, amt, f"{_word(rng)}")]])
    if plain_contra:
        for k in range(4):
            amt = round(rng.uniform(20, 300), 2)
            entries.append([_day(start, rng, days, 0.02), [(income[k % len(income)], amt, None,
                                                            f"{_word(rng)} {_word(rng)}"),
                                                           (cash, None, amt, f"{_word(rng)}")]])
    entries.sort(key=lambda e: e[0])
    if ordinary:
        memo = rng.choice(["Grand opening flyers", "Store opening supplies", "Opened new account",
                           "Beginning of promo", "Opening week stock"])
        entries[0][1] = [(acct, dr, cr, memo) for acct, dr, cr, _ in entries[0][1]]
    if last_ordinary:
        memo = rng.choice(["Store closing supplies", "Close out sale", "Closing shift float", "Closed old account",
                           "Closing time cleanup"])
        amt = round(rng.uniform(50, 900), 2)
        entries.append([start + dt.timedelta(days=days), [(rng.choice(expense), amt, None, memo),
                                                          (cash, None, amt, memo)]])
    if closing:
        big = round(sum(cr for _d, lines in entries for acct, _dr, cr, _m in lines if acct in income and cr), 2)
        memo = rng.choice(["Closing entry", "Year-end close", "Close the books", "Closing balances",
                           "Balance carried forward"])
        entries.append([start + dt.timedelta(days=days), [(income[0], big, None, memo), (equity, None, big, memo)]])
    if opening:
        big = round(rng.uniform(50000, 150000), 2)
        memo = rng.choice(["Opening balances", "Balance brought forward", "Carried forward", "Beginning balances"])
        entries.insert(0, [start, [(cash, big, None, memo), (equity, None, big, memo)]])
    rows, first = [], rng.randint(100, 900)
    for k, (d, lines) in enumerate(entries):
        for acct, dr, cr, memo in lines:
            rows.append({"entry": f"{pre}{first + k}", "date": d, "acct": acct, "dr": dr, "cr": cr, "memo": memo})
    table = _table([("entry", h["entry"]), ("date", h["date"]), ("acct", h["account"]), ("dr", "Debit"),
                    ("cr", "Credit"), ("memo", h["note"])], rows)
    plants = []
    if tax:
        plants.append({"kind": "ratio", "columns": [h["account"], "Credit"], "col": h["account"],
                       "values": [tax_acct], "rate": rate, "groups": len([e for e in entries if any(
                           x[0] == tax_acct for x in e[1])]),
                       "rows": [r["_row"] for r in rows if r["acct"] == tax_acct]})
    if contra:
        plants.append({"kind": "contra", "columns": [h["account"], "Debit", h["note"]], "col": h["account"],
                       "prefix": contra_prefix, "values": income,
                       "rows": [r["_row"] for r in rows if str(r["memo"]).startswith(contra_prefix + ":")]})
    if opening:
        plants.append({"kind": "opening", "columns": [h["entry"], h["date"]], "col": h["entry"],
                       "value": rows[0]["entry"], "rows": [r["_row"] for r in rows if r["entry"] == rows[0]["entry"]]})
    if closing:
        plants.append({"kind": "closing", "columns": [h["entry"], h["date"]], "col": h["entry"],
                       "value": rows[-1]["entry"],
                       "rows": [r["_row"] for r in rows if r["entry"] == rows[-1]["entry"]]})
    if not chart:
        return table, plants
    side = rng.choice(["Normal Side", "Normal Balance", "Side", "Dr/Cr"])
    dr, cr = rng.choice([("Debit", "Credit"), ("Dr", "Cr")])
    credit_normal = set(income) | {tax_acct, equity}
    ref = [[h["account"], side]] + [[a, cr if a in credit_normal else dr] for a in [cash] + income + expense]
    if plain_contra:
        plants.append({"kind": "contra", "columns": [h["account"], "Debit"], "col": h["account"], "prefix": None,
                       "values": income, "side": side,
                       "rows": [r["_row"] for r in rows if r["acct"] in income and r["dr"]]})
    return table, plants, ref


@trap("ratio")
def _fixed_ratio(rng, twin):
    """A journal where a companion line is a fixed percent of the sale line in
    every sale entry. Twin: the same journal without it."""
    table, plants = _journal(rng, tax=not twin)
    return table, plants or [{"columns": [], "rows": []}]


@trap("ratio")
def _fee_line(rng, twin):
    """Order lines with one amount column: most orders end with a fee line that is
    a fixed percent of the order's other lines. Twin: the fee line's amount is a
    flat charge plus a varying share, never one fixed percent."""
    oid = rng.choice(["Order ID", "Order No", "Invoice No", "Ticket No"])
    h = _headers(rng, "date", "item", "money")
    start, days = _start(rng), rng.randint(120, 300)
    items = _names(rng, rng.randint(8, 14))
    price = {it: round(rng.uniform(4, 60), 2) for it in items}
    fee = rng.choice(["Card fee", "Processing", "Service charge", "Surcharge"])
    rate = rng.choice([0.02, 0.025, 0.029, 0.03, 0.035])
    pre, first = _caps(rng, 2), rng.randint(1000, 8000)
    rows = []
    for k in range(rng.randint(70, 110)):
        d = _day(start, rng, days)
        lines = [{"item": it, "amount": round(price[it] * rng.randint(1, 4), 2)}
                 for it in rng.sample(items, rng.randint(1, 4))]
        if rng.random() < 0.8:
            base = sum(x["amount"] for x in lines)
            amt = round(base * rate, 2) if not twin else round(rng.uniform(0.2, 0.8) + base * rng.uniform(0.01, 0.04), 2)
            lines.append({"item": fee, "amount": amt})
        for x in lines:
            rows.append(dict(x, oid=f"{pre}{first + k}", date=d))
    table = _table([("oid", oid), ("date", h["date"]), ("item", h["item"]), ("amount", h["money"])], rows)
    if twin:
        return table, [{"columns": [], "rows": []}]
    return table, [{"columns": [h["item"], h["money"]], "col": h["item"], "values": [fee], "rate": rate,
                    "rows": [r["_row"] for r in rows if r["item"] == fee]}]


@trap("negatives")
def _discount_line(rng, twin):
    """Order lines with one amount column: most orders end with a discount line, a
    negative amount that is a fixed percent of the order's other lines. A share
    on the other sign is a negative, asked once by the negatives question, never
    also as a fixed share. Twin: no discount lines."""
    oid = rng.choice(["Order ID", "Order No", "Invoice No", "Ticket No"])
    h = _headers(rng, "date", "item")
    h["money"] = rng.choice(["Amount", "Line Amount"])     # a header read as the money, so negatives are asked
    start, days = _start(rng), rng.randint(120, 300)
    items = _names(rng, rng.randint(8, 14))
    price = {it: round(rng.uniform(4, 60), 2) for it in items}
    disc = rng.choice(["Member discount", "Loyalty discount", "Promo", "Staff discount"])
    rate = rng.choice([0.05, 0.1, 0.15, 0.2])
    pre, first = _caps(rng, 2), rng.randint(1000, 8000)
    rows = []
    for k in range(rng.randint(70, 110)):
        d = _day(start, rng, days)
        lines = [{"item": it, "amount": round(price[it] * rng.randint(1, 4), 2)}
                 for it in rng.sample(items, rng.randint(1, 4))]
        if not twin and rng.random() < 0.8:
            lines.append({"item": disc, "amount": -round(sum(x["amount"] for x in lines) * rate, 2)})
        for x in lines:
            rows.append(dict(x, oid=f"{pre}{first + k}", date=d))
    table = _table([("oid", oid), ("date", h["date"]), ("item", h["item"]), ("amount", h["money"])], rows)
    if twin:
        return table, [{"columns": [], "rows": []}]
    return table, [{"columns": [h["item"], h["money"]], "col": h["item"], "money": h["money"], "values": [disc],
                    "rate": rate, "rows": [r["_row"] for r in rows if r["item"] == disc]}]


@trap("contra")
def _opposite_side(rng, twin):
    """4 lines on an income account's debit side with memos sharing a prefix.
    Twin: none."""
    table, plants = _journal(rng, contra=not twin)
    return table, plants or [{"columns": [], "rows": []}]


@trap("opening")
def _opening_entry(rng, twin):
    """A first entry that carries opening balances in. Twin: none, and the first
    entry's memo says 'opening' in its ordinary sense (a grand opening)."""
    table, plants = _journal(rng, opening=not twin, ordinary=twin)
    return table, plants or [{"columns": [], "rows": []}]


@trap("closing")
def _closing_entry(rng, twin):
    """A last entry that closes the income out to equity, under a memo that says
    so (a closing entry, a year-end close, balances carried forward). Twin: none,
    and the last entry's memo says 'closing' in its ordinary sense (store
    closing supplies)."""
    table, plants = _journal(rng, closing=not twin, last_ordinary=twin)
    return table, plants or [{"columns": [], "rows": []}]


@trap("contra")
def _chart_side(rng, twin):
    """A journal with a chart tab giving each account its normal side, and 4 lines
    on the income accounts' debit side with ordinary memos (no prefix marks
    them); cash, paid in and out, sits on both sides by nature. Twin: the same
    journal and chart without those lines."""
    table, plants, ref = _journal(rng, chart=True, plain_contra=not twin)
    return table, plants or [{"columns": [], "rows": []}], [ref]


@trap("journal")
def _journal_three(rng, twin):
    """All three journal plants at once (a fixed-ratio line, opposite-side lines
    and an opening entry); each plant names its own kind. Twin: none of them."""
    table, plants = _journal(rng, tax=not twin, contra=not twin, opening=not twin)
    return table, plants or [{"columns": [], "rows": []}]


# --------------------------------------------------------------------------
# rank 11: what a row is and how the dates fall
# --------------------------------------------------------------------------
_PANEL = {"date": ["Count Date", "Snapshot Date", "Inventory Date", "Week Ending"],
          "site": ["Location", "Storage Area", "Warehouse", "Zone"],
          "item": ["Item", "Item Name", "Product", "Description"],
          "stock": ["On Hand", "Qty On Hand", "Counted", "Stock"], "flow": ["Units Sold", "Sold", "Qty Sold"],
          "cost": ["Unit Cost", "Cost", "Avg Cost"],
          "value": ["Value", "Stock Value", "Extended Value", "Inventory Value"], "sales": ["Sales", "Sold Value"]}


@trap("grain")
def _snapshot_panel(rng, twin):
    """A weekly count panel: 4 sites count the same 30 items every week for 20
    weeks (3% of counts skipped), with a count on hand, a unit cost and a value
    that is the two multiplied. Twin: a weekly log of what was sold, each week
    holding a different third of the site and item pairs."""
    h = {k: rng.choice(v) for k, v in _PANEL.items()}
    start = _start(rng)
    start -= dt.timedelta(days=start.weekday())
    sites, items = _names(rng, 4), _names(rng, 30, 3)
    cost = {it: round(rng.uniform(2, 40), 2) for it in items}
    rows = []
    for w in range(20):
        d = start + dt.timedelta(weeks=w)
        for s in sites:
            for it in items:
                if (twin and rng.random() > 0.33) or (not twin and rng.random() < 0.03):
                    continue
                q = rng.randint(1, 60)
                rows.append({"date": d, "site": s, "item": it, "qty": q, "cost": cost[it],
                             "amount": round(q * cost[it], 2)})
    qty, money = (h["flow"], h["sales"]) if twin else (h["stock"], h["value"])
    table = _table([("date", h["date"]), ("site", h["site"]), ("item", h["item"]), ("qty", qty),
                    ("cost", h["cost"]), ("amount", money)], rows)
    latest = max(r["date"] for r in rows)
    return table, [{"columns": [h["date"], qty, money], "col": h["date"], "site_col": h["site"], "money": money,
                    "stock": [qty, money], "latest": latest, "dates": 20,
                    "rows": [r["_row"] for r in rows if r["date"] == latest]}]


@trap("grain")
def _count_panel(rng, twin):
    """A count panel whose stock header is a bare 'Count', a word no header rule
    reads as a level: 3 to 5 sites count the same 20 to 40 items every week for
    12 to 20 weeks, with a unit cost and a value that is the two multiplied. Only
    the playbook role the column binds to says it is a count on hand. Twin: a
    weekly log where each week holds a different third of the site and item pairs."""
    h = {"date": rng.choice(["Count Date", "Inventory Date", "Snapshot Date"]),
         "site": rng.choice(["Location", "Storage Area", "Warehouse"]),
         "item": rng.choice(["Item", "Item Name", "Description"]), "cost": rng.choice(["Unit Cost", "Cost"])}
    start = _start(rng)
    start -= dt.timedelta(days=start.weekday())
    weeks = rng.randint(12, 20)
    sites, items = _names(rng, rng.randint(3, 5)), _names(rng, rng.randint(20, 40), 3)
    cost = {it: round(rng.uniform(2, 40), 2) for it in items}
    rows = []
    for w in range(weeks):
        d = start + dt.timedelta(weeks=w)
        for s in sites:
            for it in items:
                if (twin and rng.random() > 0.33) or (not twin and rng.random() < 0.03):
                    continue
                q = rng.randint(1, 60)
                rows.append({"date": d, "site": s, "item": it, "qty": q, "cost": cost[it],
                             "amount": round(q * cost[it], 2)})
    table = _table([("date", h["date"]), ("site", h["site"]), ("item", h["item"]), ("qty", "Count"),
                    ("cost", h["cost"]), ("amount", "Value")], rows)
    latest = max(r["date"] for r in rows)
    return table, [{"columns": [h["date"], "Count", "Value"], "col": h["date"], "site_col": h["site"],
                    "money": "Value", "stock": ["Count", "Value"], "latest": latest, "dates": weeks,
                    "rows": [r["_row"] for r in rows if r["date"] == latest]}]


def journal_entries(rng, balanced: bool = True, flip: bool = False) -> tuple:
    """(rows, headers, manifest) of a journal whose entries hold 2 to 5 lines on a
    debit and a credit column. balanced: every entry nets to zero. flip: credits
    are written as negative numbers from a random date on."""
    h = _headers(rng, "entry", "date", "account")
    start, days = _start(rng), rng.randint(200, 360)
    accounts = _names(rng, rng.randint(6, 10), 3)
    split = start + dt.timedelta(days=int(days * rng.uniform(0.3, 0.7)))
    pre, first = _caps(rng, 2), rng.randint(100, 900)
    rows, sizes = [], []
    for k in range(rng.randint(60, 90)):
        d = _day(start, rng, days)
        n = rng.randint(2, 5)
        a = rng.randint(1, n - 1)
        total = round(rng.uniform(100, 3000), 2)

        def parts(m, amt):
            cents = round(amt * 100)
            cuts = sorted(rng.sample(range(1, cents), m - 1)) if m > 1 else []
            return [(y - x) / 100 for x, y in zip([0] + cuts, cuts + [cents])]
        dr, cr = parts(a, total), parts(n - a, total if balanced else round(total * rng.uniform(0.5, 0.9), 2))
        sizes.append(n)
        for amt in dr:
            rows.append({"entry": f"{pre}{first + k}", "date": d, "acct": rng.choice(accounts), "dr": amt, "cr": None})
        for amt in cr:
            rows.append({"entry": f"{pre}{first + k}", "date": d, "acct": rng.choice(accounts), "dr": None,
                         "cr": -amt if flip and d >= split else amt})
    rows.sort(key=lambda r: (r["date"], r["entry"]))
    table = _table([("entry", h["entry"]), ("date", h["date"]), ("acct", h["account"]), ("dr", "Debit"),
                    ("cr", "Credit")], rows)
    return table, h, {"entries": len(sizes), "lo": min(sizes), "hi": max(sizes), "split": split}


@trap("grain")
def _balanced_entries(rng, twin):
    """Journal entries of 2 to 5 lines on a debit and a credit column, each netting
    to zero once credits turn negative from a random date on (a sign boundary the
    boundary detector finds too). Twin: the same layout where no entry's lines
    net and credits keep their sign."""
    table, h, info = journal_entries(rng, balanced=not twin, flip=not twin)
    return table, [{"columns": [h["entry"], "Debit", "Credit"], "col": h["entry"], **info,
                    "rows": list(range(2, len(table) + 1))}]


@trap("grain")
def _pay_cycle(rng, twin):
    """A pay register run every 14 days on one weekday for 8 to 15 people, with 3
    off-cycle runs for one or two of them on another weekday. Twin: the same
    runs on dates scattered over the week."""
    h = _headers(rng, "date", "party_id", "party", "qty", "money")
    start = _start(rng)
    start += dt.timedelta(days=(4 - start.weekday()) % 7 + 7 * rng.randint(0, 1))      # a Friday
    staff = [(f"{_caps(rng, 1)}{rng.randint(100, 999)}", f"{f} {l}") for f, l in _people(rng, rng.randint(8, 15))]
    runs = [start + dt.timedelta(days=14 * k) for k in range(rng.randint(18, 26))]
    if twin:
        runs = sorted({start + dt.timedelta(days=14 * k + rng.randint(-5, 5)) for k in range(len(runs))})
    extra = [] if twin else sorted(rng.sample(range(1, len(runs) - 1), 3))
    extra = [runs[k] + dt.timedelta(days=4) for k in extra]            # a Tuesday
    rows = []
    for d in sorted(runs + extra):
        who = staff if d in runs else rng.sample(staff, rng.randint(1, 2))
        for pid, name in who:
            hrs = round(rng.uniform(20, 80), 2)
            rows.append({"date": d, "id": pid, "name": name, "hrs": hrs, "gross": round(hrs * rng.uniform(15, 40), 2)})
    table = _table([("date", h["date"]), ("id", h["party_id"]), ("name", h["party"]), ("hrs", h["qty"]),
                    ("gross", h["money"])], rows)
    return table, [{"columns": [h["date"]], "col": h["date"], "step": 14, "weekday": "Friday", "off": len(extra),
                    "last": runs[-1], "rows": [r["_row"] for r in rows if r["date"] in extra] or [2]}]


@trap("trend")
def _price_trend(rng, twin):
    """Weekly purchases at 3 to 5 sites where every site pays the same price for an
    item in a given week, and prices step up over the weeks. Twin: the same with
    prices that never change."""
    start = _start(rng)
    start -= dt.timedelta(days=start.weekday())
    sites, pre = _names(rng, rng.randint(3, 5)), _caps(rng, 2)
    items = [f"{pre}-{100 + i}" for i in range(rng.randint(10, 16))]
    base = {it: round(rng.uniform(3, 40), 2) for it in items}
    drift = {it: rng.uniform(0.005, 0.02) for it in items}
    rows = []
    for w in range(rng.randint(16, 24)):
        d = start + dt.timedelta(weeks=w)
        for s in sites:
            for it in items:
                if rng.random() > 0.6:
                    continue
                p = base[it] if twin else round(base[it] * (1 + drift[it]) ** w, 2)
                q = rng.randint(1, 12)
                rows.append({"date": d, "site": s, "item": it, "qty": q, "price": p, "amount": round(q * p, 2)})
    h = {"date": rng.choice(["Week Ending", "Invoice Date", "Delivery Date"]),
         "site": rng.choice(["Location", "Ship To", "Site"]), "item": rng.choice(["Item #", "Item Code", "SKU"]),
         "price": rng.choice(["Unit Price", "Price", "Unit Cost"]), "amount": rng.choice(["Ext Price", "Line Total"])}
    table = _table([("date", h["date"]), ("site", h["site"]), ("item", h["item"]), ("qty", "Qty"),
                    ("price", h["price"]), ("amount", h["amount"])], rows)
    return table, [{"columns": [h["price"], h["item"], h["site"]], "col": h["price"], "period": h["date"],
                    "rows": [r["_row"] for r in rows]}]


# --------------------------------------------------------------------------
# rank 19: reference and partner tables
# --------------------------------------------------------------------------
# the planted vendor's share of the lines in list_price and fee_onset, against 1 for each other vendor (1: all equal)
LEAD_WEIGHT = 2
_TERMS_TEXT = ["Net 30, rebate paid each quarter", "Net 15 on all lines", "Fixed prices for the term",
               "Net 45, freight included", "Cost plus a fee per case"]


def _buys(rng, vendors: list, months: int = 12, n: int = 0, weights: list | None = None) -> dict:
    """Purchase lines over whole months: date, vendor, item, qty, price and amount,
    each vendor with its own items (weights: each vendor's share of the lines)."""
    start = dt.datetime(rng.randint(2021, 2025), rng.randint(1, 12), 1)
    end = dt.datetime(start.year + (start.month - 1 + months) // 12, (start.month - 1 + months) % 12 + 1, 1)
    days = (end - start).days
    items = {v: _names(rng, rng.randint(4, 7), 3) for v in vendors}
    price = {(v, it): round(rng.uniform(4, 60), 2) for v in vendors for it in items[v]}
    rows = []
    for _ in range(n or rng.randint(360, 520)):
        v = rng.choices(vendors, weights)[0] if weights else rng.choice(vendors)
        it = rng.choice(items[v])
        q = rng.randint(1, 12)
        rows.append({"date": _day(start, rng, days), "vendor": v, "item": it, "qty": q, "price": price[(v, it)],
                     "amount": round(q * price[(v, it)], 2)})
    rows.sort(key=lambda r: r["date"])
    return {"start": start, "end": end, "days": days, "items": items, "price": price, "rows": rows}


def _buy_cols(h: dict, *extra) -> list:
    return [("date", h["date"]), ("vendor", h["vendor"]), ("item", h["item"])] + list(extra) + \
        [("qty", h["qty"]), ("price", h["price"]), ("amount", h["amount"])]


def _buy_headers(rng) -> dict:
    return {"date": rng.choice(["Date", "Invoice Date", "Delivery Date"]),
            "vendor": rng.choice(["Vendor", "Supplier", "Distributor"]),
            "item": rng.choice(["Item", "Product", "Description"]), "qty": rng.choice(["Qty", "Quantity", "Cases"]),
            "price": rng.choice(["Unit Price", "Price", "Unit Cost"]),
            "amount": rng.choice(["Amount", "Ext Price", "Line Total"])}


@trap("window")
def _terms_window(rng, twin):
    """Purchases from 4 to 6 vendors over 12 months, and a terms tab giving each
    vendor a start and an end date, a rebate rate and its terms in words. One
    vendor has 30 lines dated after its end, and another has two dated rows one
    after the other. Twin: every line inside its vendor's dates, one row each."""
    vendors = _names(rng, rng.randint(4, 6))
    b = _buys(rng, vendors)
    h = _buy_headers(rng)
    late, split = vendors[0], vendors[1]
    t0, t1 = b["start"] - dt.timedelta(days=rng.randint(30, 200)), b["end"] + dt.timedelta(days=rng.randint(100, 400))
    rows = [r for r in b["rows"] if twin or r["vendor"] != late]
    cut = b["start"] + dt.timedelta(days=int(b["days"] * rng.uniform(0.55, 0.7)))
    ends = {v: t1 for v in vendors}
    after = []
    if not twin:
        ends[late] = cut
        mine = [r for r in b["rows"] if r["vendor"] == late]
        inside = [r for r in mine if r["date"] <= cut]
        after = [dict(r, date=cut + dt.timedelta(days=rng.randint(1, (b["end"] - cut).days - 1))) for r in
                 rng.sample(mine, 30)]
        rows = sorted(rows + inside + after, key=lambda r: r["date"])
    table = _table(_buy_cols(h), rows)
    s_h, e_h = rng.choice([("Start", "End"), ("Start Date", "End Date"), ("Effective", "Expires"),
                           ("Valid From", "Valid To")])
    terms = [[h["vendor"], s_h, e_h, rng.choice(["Rebate %", "Rebate Rate"]), "Terms"]]
    mid = b["start"] + dt.timedelta(days=int(b["days"] * rng.uniform(0.3, 0.6)))
    for v in vendors:
        rate = rng.choice([1, 1.5, 2, 2.5, 3])
        if v == split and not twin:
            terms.append([v, t0, mid, rate, rng.choice(_TERMS_TEXT)])
            terms.append([v, mid + dt.timedelta(days=1), t1, rate + 0.5, rng.choice(_TERMS_TEXT)])
        else:
            terms.append([v, t0, ends[v], rate, rng.choice(_TERMS_TEXT)])
    plants = [{"kind": "window", "columns": [h["vendor"], h["date"]], "col": h["vendor"], "value": late,
               "lines": len(after), "end": cut, "rows": [r["_row"] for r in after] or [2]},
              {"kind": "versions", "columns": [h["vendor"]], "col": h["vendor"], "value": split, "split": mid,
               "rows": [r["_row"] for r in rows if r["vendor"] == split][:50] or [2]}]
    return table, plants, [terms]


@trap("conformity")
def _list_price(rng, twin):
    """Purchases of each vendor's items over 12 months, and a price list giving
    each item one price. One vendor, the largest (twice the lines of any other),
    charges exactly the list price from month 7 on, except 4 lines in month 10
    that are above it; before month 7, and at the other vendors, prices float
    around the list. Twin: every vendor floats."""
    vendors = _names(rng, rng.randint(4, 5))
    b = _buys(rng, vendors, n=rng.randint(420, 560), weights=[LEAD_WEIGHT] + [1] * (len(vendors) - 1))
    h = _buy_headers(rng)
    follows = vendors[0]
    listed = {it: round(rng.uniform(4, 60), 2) for v in vendors for it in b["items"][v]}

    def month(r):
        return (r["date"].year - b["start"].year) * 12 + r["date"].month - b["start"].month

    def floating(p):
        while True:
            x = round(p * rng.uniform(0.9, 1.1), 2)
            if abs(x - p) > 0.005:
                return x
    above = []
    late = [r for r in b["rows"] if r["vendor"] == follows and month(r) == 9]
    for r in rng.sample(late, min(4, len(late))) if not twin else []:
        r["above"] = True
    for r in b["rows"]:
        ref = listed[r["item"]]
        if not twin and r["vendor"] == follows and month(r) >= 6:
            r["price"] = round(ref * rng.uniform(1.03, 1.08), 2) if r.get("above") else ref
            if r.get("above"):
                above.append(r)
        else:
            r["price"] = floating(ref)
        r["amount"] = round(r["qty"] * r["price"], 2)
    table = _table(_buy_cols(h), b["rows"])
    plist = [[h["item"], rng.choice(["List Price", "Contract Price", "Agreed Price"])]] + \
        [[it, p] for it, p in sorted(listed.items())]
    return table, [{"columns": [h["price"], h["vendor"]], "col": h["price"], "value": follows,
                    "from": month_start(b["start"], 6), "above": len(above), "above_month": month_start(b["start"], 9),
                    "rows": [r["_row"] for r in above] or [2]}], [plist]


def month_start(start, k: int) -> str:
    """'YYYY-MM' of the k-th month from start (0 is start's month)."""
    m = start.month - 1 + k
    return f"{start.year + m // 12}-{m % 12 + 1:02d}"


_LINE_TYPES = ["Goods", "Freight", "Deposit", "Surcharge", "Service Fee", "Delivery", "Handling"]


@trap("onset")
def _fee_onset(rng, twin):
    """Purchases from 4 or 5 vendors over 12 months with a line type in words.
    Goods and freight lines run all year at every vendor; a fee line type (3 to
    5 lines a month) runs all year at some of them. One vendor, the largest
    (twice the lines of any other), starts carrying the fee in month 5, after 4
    months without it, and carries it every month after. Twin: that vendor
    carries the fee from its first month."""
    vendors = _names(rng, rng.randint(4, 5))
    b = _buys(rng, vendors, n=rng.randint(360, 480), weights=[LEAD_WEIGHT] + [1] * (len(vendors) - 1))
    h = _buy_headers(rng)
    goods, freight, fee = rng.sample(_LINE_TYPES, 3)
    late = vendors[0]
    payers = [late] + [v for v in vendors[1:] if rng.random() < 0.5]
    for r in b["rows"]:
        r["kind"] = freight if rng.random() < 0.15 else goods
    fees = []
    for m in range(12):
        for v in payers:
            if v == late and not twin and m < 4:
                continue
            for _ in range(rng.randint(3, 5)):
                d = dt.datetime.strptime(month_start(b["start"], m) + "-01", "%Y-%m-%d") + dt.timedelta(
                    days=rng.randint(0, 27))
                p = round(rng.uniform(5, 40), 2)
                fees.append({"date": d, "vendor": v, "item": None, "kind": fee, "qty": 1, "price": p, "amount": p})
    rows = sorted(b["rows"] + fees, key=lambda r: r["date"])
    kh = rng.choice(["Line Type", "Charge Type", "Line Kind", "Entry Type"])
    table = _table(_buy_cols(h, ("kind", kh)), rows)
    return table, [{"columns": [kh, h["vendor"]], "col": kh, "value": fee, "group": late,
                    "month": month_start(b["start"], 4),
                    "rows": [r["_row"] for r in rows if r["vendor"] == late and r["kind"] == fee]}]


# --------------------------------------------------------------------------
# rank 8: a model whose inputs feed most of its formulas
# --------------------------------------------------------------------------
_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _col(i: int) -> str:
    return "ABCDEFGHIJKLMNOPQRSTUVWXYZ"[i]


@trap("model")
def _model_input(rng, twin):
    """A 12-month model: revenue grows from a start value by an input rate, and
    costs, gross profit, tax, net profit and cash follow from it, all formulas,
    with the inputs listed below the grid. One early month's revenue is typed
    over its formula, so most of the model's formula cells sit downstream of it;
    the last cash cell carries a typed factor that feeds nothing. Twin: neither."""
    growth = rng.choice([0.02, 0.03, 0.04, 0.05])
    cost, tax = rng.choice([0.55, 0.6, 0.65]), rng.choice([0.2, 0.25])
    start = rng.randint(800, 5000)
    first_in = 10                                   # the inputs block starts on this sheet row (1-based)
    ref = {"growth": f"$B${first_in + 1}", "start": f"$B${first_in + 2}", "cost": f"$B${first_in + 3}",
           "tax": f"$B${first_in + 4}"}
    labels = ["Revenue", "Costs", "Gross Profit", "Tax", "Net Profit", "Cash"]
    plug = None if twin else rng.choice([1, 2])       # Feb or Mar revenue, typed
    typed = round(start * (1 + growth) ** (plug or 0) * rng.uniform(1.05, 1.2), 2) if plug else None
    val = {lab: [0.0] * 12 for lab in labels}
    grid = []
    for k, lab in enumerate(labels):
        r = k + 2                                     # sheet row of this line
        row = [lab]
        for m in range(12):
            c, p = _col(m + 1), _col(m)
            if lab == "Revenue":
                if m == plug:
                    v, f = typed, None
                else:
                    v = start if m == 0 else val["Revenue"][m - 1] * (1 + growth)
                    f = f"={ref['start']}" if m == 0 else f"={p}{r}*(1+{ref['growth']})"
            elif lab == "Costs":
                v, f = val["Revenue"][m] * cost, f"={c}2*{ref['cost']}"
            elif lab == "Gross Profit":
                v, f = val["Revenue"][m] - val["Costs"][m], f"={c}2-{c}3"
            elif lab == "Tax":
                v, f = val["Gross Profit"][m] * tax, f"={c}4*{ref['tax']}"
            elif lab == "Net Profit":
                v, f = val["Gross Profit"][m] - val["Tax"][m], f"={c}4-{c}5"
            else:
                k2 = 0.97 if m == 11 and not twin else 1
                v = val["Net Profit"][m] * k2 + (val["Cash"][m - 1] if m else 0.0)
                f = (f"={p}{r}+{c}6*0.97" if k2 != 1 else f"={p}{r}+{c}6") if m else f"={c}6"
            val[lab][m] = v
            row.append(Formula(f, round(v, 2)) if f else v)
        grid.append(row)
    rows = [["Line Item"] + _MONTHS] + grid
    rows += [[]] * (first_in - len(rows) - 1)
    rows += [["Assumption", "Value"], ["Growth", growth], ["Start revenue", start], ["Cost share", cost],
             ["Tax rate", tax]]
    if twin:
        return rows, [{"columns": ["Revenue"], "rows": []}]
    return rows, [{"columns": ["Revenue"], "col": "Revenue", "cell": f"{_col(plug + 1)}2", "typed": typed,
                   "factor_cell": "M7", "rows": [2]}]


# --------------------------------------------------------------------------
# rank 20: a formula model read in words
# --------------------------------------------------------------------------
_INPUT_NOTES = ["per month", "share of the month's revenue", "set each budget round", "from the last close",
                "monthly, loaded", "rough figure"]


@trap("model")
def _model_plan(rng, twin):
    """The model below, its headcount block on the model's own tab."""
    return _plan_model(rng, twin)


@trap("model")
def _model_plan_inputs(rng, twin):
    """The model below, its headcount block a period grid on the inputs tab (under
    the label | value | note rows), where a typed plan usually lives. Twin: that
    grid holds one constant per row."""
    return _plan_model(rng, twin, on_inputs=True)


def _plan_model(rng, twin, on_inputs=False, mult_on="costs"):
    """A 24-month model on one tab that reads an inputs tab laid out label | value
    | note (the tax rate has no note). Revenue grows by the growth input from a
    start value; churned, costs, payroll, profit, tax, net, funding and cash follow,
    with a bank row linked to cash and a check row between them. Plants: one
    revenue month uses churn where the row uses growth; one costs month multiplies
    by a typed k; the start value is typed on the grid while the start input feeds
    nothing (its typed twin); the headcount row is a typed block that feeds payroll
    and everything below it; a collections row is typed in its first 3 months, a
    few percent off the revenue it links to after that; one bank month is typed,
    so the check fails there; a funding row holds one typed raise, without which
    cash goes below zero. Twin: none of them (headcount reads a constant input,
    collections match revenue, funding is all zeros)."""
    tab = rng.choice(["Inputs", "Assumptions", "Drivers"])
    year = rng.randint(2026, 2030)
    heads = [f"{m} {year + k // 12}" for k, m in enumerate(_MONTHS * 2)]
    n = len(heads)
    lab = {"growth": rng.choice(["Growth", "Monthly growth", "Growth rate"]),
           "churn": rng.choice(["Churn", "Monthly churn", "Attrition"]),
           "cost": rng.choice(["Cost share", "Direct cost %", "Cost of sales %"]),
           "cph": rng.choice(["Cost per head", "Loaded cost", "Pay per head"]),
           "tax": rng.choice(["Tax rate", "Income tax"]), "cash0": rng.choice(["Opening cash", "Starting cash"]),
           "start": rng.choice(["Start revenue", "Opening revenue", "Base revenue"]), "heads0": "Base heads"}
    g, ch = rng.choice([0.02, 0.025, 0.03, 0.035, 0.04]), rng.choice([0.01, 0.015, 0.02, 0.03])
    cs, cph, tr = rng.choice([0.3, 0.35, 0.4, 0.45]), rng.randint(8, 12) * 1000, rng.choice([0.2, 0.21, 0.25])
    r0, h0 = rng.randint(20, 40) * 1000, rng.randint(10, 16)
    ref = {k: f"{tab}!$B${i + 2}" for i, k in enumerate(["growth", "churn", "cost", "cph", "tax", "cash0", "start",
                                                          "heads0"])}
    kb, km, kp, mr = rng.sample(range(4, 18), 4)             # break, multiplier, bank plug and raise months
    k = rng.choice([1.05, 1.08, 1.1, 1.12])
    u = rng.uniform(0.02, 0.06)
    names = {"rev": "Revenue", "churned": "Churned", "costs": "Costs", "heads": rng.choice(["Headcount", "Heads"]),
             "pay": "Payroll", "profit": "Profit", "taxes": "Tax", "net": "Net", "fund": rng.choice(["Funding",
                                                                                                    "Equity in"]),
             "cash": rng.choice(["Cash", "Ending Cash", "Closing Cash", "Cash on Hand", "Bank balance"]),
             "bank": rng.choice(["Bank statement", "Per bank", "Bank record"]),
             "check": rng.choice(["Check", "Difference"]), "coll": rng.choice(["Collections", "Receipts",
                                                                               "Billings"])}
    order = ["rev", "churned", "costs", "heads", "pay", "profit", "taxes", "net", "fund", "cash", "bank", "check",
             "coll"]
    shown = [key for key in order if not (on_inputs and key == "heads")]
    row = {key: i + 2 for i, key in enumerate(shown)}          # sheet row (1-based) of each line
    hc, h = [], h0
    for m in range(n):
        h += 1 if m and rng.random() < 0.35 else 0
        hc.append(h)
    if len(set(hc)) < 3:
        hc[-1], hc[-2] = h0 + 3, h0 + 2
    if on_inputs:
        # the headcount grid sits under the 7 input rows, a blank row and its own header row
        row["heads"] = 11
        hc = [h0] * n if twin else hc
    v = {key: [0.0] * n for key in order}
    f = {key: [None] * n for key in order}
    for m in range(n):
        c, p = _col(m + 1), _col(m) if m else None
        if m == 0:
            v["rev"][m] = r0
            f["rev"][m] = None if not twin else f"={ref['start']}"
        else:
            rate = ch if m == kb and not twin else g
            v["rev"][m] = round(v["rev"][m - 1] * (1 + rate), 2)
            f["rev"][m] = f"={p}{row['rev']}*(1+{ref['churn'] if m == kb and not twin else ref['growth']})"
        v["churned"][m], f["churned"][m] = round(v["rev"][m] * ch, 2), f"={c}{row['rev']}*{ref['churn']}"
        kk = k if m == km and not twin and mult_on == "costs" else 1
        v["costs"][m] = round(v["rev"][m] * cs * kk, 2)
        f["costs"][m] = f"={c}{row['rev']}*{ref['cost']}" + (f"*{k}" if kk != 1 else "")
        v["heads"][m] = hc[m] if not twin or on_inputs else h0
        f["heads"][m] = None if not twin or on_inputs else f"={ref['heads0']}"
        heads_at = f"{tab}!{c}{row['heads']}" if on_inputs else f"{c}{row['heads']}"
        v["pay"][m], f["pay"][m] = round(v["heads"][m] * cph, 2), f"={heads_at}*{ref['cph']}"
        v["profit"][m] = round(v["rev"][m] - v["churned"][m] - v["costs"][m] - v["pay"][m], 2)
        f["profit"][m] = f"={c}{row['rev']}-{c}{row['churned']}-{c}{row['costs']}-{c}{row['pay']}"
        v["taxes"][m], f["taxes"][m] = round(max(0, v["profit"][m]) * tr, 2), f"=MAX(0,{c}{row['profit']})*{ref['tax']}"
        v["net"][m], f["net"][m] = round(v["profit"][m] - v["taxes"][m], 2), f"={c}{row['profit']}-{c}{row['taxes']}"
    burn = [sum(v["net"][:m + 1]) for m in range(n)]
    cash0 = round(-burn[mr + 3] - 0.5 * v["net"][mr + 4], 2)
    raise_ = 0.0 if twin else round(-burn[-1] * 1.2, -3)
    for m in range(n):
        c, p = _col(m + 1), _col(m) if m else None
        v["fund"][m] = raise_ if m == mr else 0
        before = v["cash"][m - 1] if m else cash0
        v["cash"][m] = round(before + v["net"][m] + v["fund"][m], 2)
        f["cash"][m] = (f"={p}{row['cash']}" if m else f"={ref['cash0']}") + f"+{c}{row['net']}+{c}{row['fund']}"
        plug = m == kp and not twin
        v["bank"][m] = round(v["cash"][m] + rng.randint(5, 50) * 100, 2) if plug else v["cash"][m]
        f["bank"][m] = None if plug else f"={c}{row['cash']}"
        v["check"][m] = round(v["cash"][m] - v["bank"][m], 2)
        f["check"][m] = f"=ROUND({c}{row['cash']}-{c}{row['bank']},2)"
        typed = m < 3
        v["coll"][m] = round(v["rev"][m] * (1 + (u if not twin else 0)), 2) if typed else v["rev"][m]
        f["coll"][m] = None if typed else f"={c}{row['rev']}"
        if m == km and not twin and mult_on == "coll":        # a typed factor on one link of the tie-out row
            v["coll"][m], f["coll"][m] = round(v["rev"][m] * k, 2), f"={c}{row['rev']}*{k}"
    grid = [["Line"] + heads]
    for key in shown:
        grid.append([names[key]] + [Formula(f[key][m], v[key][m]) if f[key][m] else v[key][m] for m in range(n)])
    notes = rng.sample(_INPUT_NOTES, 6)
    inputs = [[rng.choice(["Input", "Assumption", "Driver"]), "Value", rng.choice(["Note", "Comment", "Source"])],
              [lab["growth"], g, notes[0]], [lab["churn"], ch, notes[1]], [lab["cost"], cs, notes[2]],
              [lab["cph"], cph, notes[3]], [lab["tax"], tr], [lab["cash0"], cash0, notes[4]],
              [lab["start"], r0, notes[5]]] + ([[lab["heads0"], h0, "people"]] if twin and not on_inputs else [])
    if on_inputs:
        inputs += [[], [rng.choice(["Plan", "Staffing plan", "Hiring plan"])] + heads, [names["heads"]] + hc]
    if twin:
        return grid, [{"columns": [], "rows": []}], [{"name": tab, "rows": inputs}]
    low = min(range(n), key=lambda m: v["cash"][m])
    cash_wo = [v["cash"][m] - (raise_ if m >= mr else 0) for m in range(n)]
    below = next(m for m in range(n) if cash_wo[m] < 0)
    cv = v["costs"][km] if mult_on == "costs" else v["coll"][km]
    plants = [
        {"what": "driver", "col": names["rev"], "row": names["rev"], "input": lab["growth"], "input_cell": "B2",
         "inputs_tab": tab, "rows": [row["rev"]]},
        {"what": "break", "col": names["rev"], "cell": f"{_col(kb + 1)}{row['rev']}", "uses": lab["churn"],
         "instead": lab["growth"], "rows": [row["rev"]]},
        {"what": "multiplier", "col": names[mult_on], "cell": f"{_col(km + 1)}{row[mult_on]}", "k": k,
         "added": round(cv - cv / k, 2), "rows": [row[mult_on]]},
        {"what": "plug", "col": names["bank"], "cell": f"{_col(kp + 1)}{row['bank']}", "check": names["check"],
         "month": heads[kp], "rows": [row["bank"]]},
        {"what": "plan_block", "col": names["heads"], "rows": [row["heads"]], "on_inputs": on_inputs,
         "block_tab": tab if on_inputs else None},
        {"what": "tieout", "col": names["coll"], "link": names["rev"], "rows": [row["coll"]]},
        {"what": "orphan", "col": lab["start"], "cell": f"{tab}!B8", "twin_cell": f"B{row['rev']}", "value": r0,
         "rows": [8]},
        {"what": "no_note", "col": lab["tax"], "cell": f"{tab}!B6", "rows": [6]},
        {"what": "series", "col": names["cash"], "min": v["cash"][low], "min_at": heads[low], "rows": [row["cash"]]},
        {"what": "plan", "col": names["fund"], "cash": names["cash"], "below": heads[below], "raise_at": heads[mr],
         "rows": [row["fund"]]}]
    return grid, plants, [{"name": tab, "rows": inputs}]


_SPELLINGS = [str.upper, str.lower, lambda s: s + ".", lambda s: "  " + s]


@trap("structure")
def _join_variants(rng, twin):
    """Lines that name a party, and a list tab of the parties with a rep each,
    where 1 or 2 parties are written another way on the list (capitals, a closing
    period, leading spaces). Twin: every party written the same on both."""
    h = _headers(rng, "date", "doc", "party", "money")
    parties = _names(rng, rng.randint(5, 8), 3)
    odd = rng.sample(parties, rng.randint(1, 2))
    how = {p: rng.choice(_SPELLINGS) for p in odd}
    start, days = _start(rng), rng.randint(150, 300)
    pre, first = _caps(rng, 2), rng.randint(1000, 8000)
    rows = [{"date": _day(start, rng, days), "party": rng.choice(parties), "amount": round(rng.uniform(10, 900), 2)}
            for _ in range(rng.randint(150, 260))]
    rows.sort(key=lambda r: r["date"])
    for k, r in enumerate(rows):
        r["doc"] = f"{pre}-{first + k}"
    table = _table([("date", h["date"]), ("doc", h["doc"]), ("party", h["party"]), ("amount", h["money"])], rows)
    ref = [[h["party"], rng.choice(["Rep", "Contact", "Owner", "Manager"])]] + \
        [[p if twin or p not in how else how[p](p), f"{_word(rng)} {_word(rng, 3)}"] for p in parties]
    return table, [{"columns": [h["party"]], "col": h["party"],
                    "pairs": [] if twin else [[p, how[p](p)] for p in odd],
                    "rows": [] if twin else [r["_row"] for r in rows if r["party"] in how]}], [ref]


@trap("structure")
def _summary_labels(rng, twin):
    """A summary tab of one row per category, each a SUMIFS and a COUNTIFS of a
    data tab by the row's own label, missing the rows for 1 or 2 of the
    categories, so their lines never reach its totals. The summary is the
    trap's tab; the data tab is the one its formulas name. Twin: a row for every
    category."""
    h = _headers(rng, "date", "doc", "category", "money")
    cats = _names(rng, rng.randint(5, 8), 3)
    missing = [] if twin else rng.sample(cats, rng.randint(1, 2))
    start, days = _start(rng), rng.randint(150, 300)
    data = [[h["date"], h["doc"], h["category"], h["money"]]]
    pre, first = _caps(rng, 2), rng.randint(1000, 8000)
    for k in range(rng.randint(180, 280)):
        data.append([_day(start, rng, days), f"{pre}-{first + k}", rng.choice(cats), round(rng.uniform(10, 400), 2)])
    data[1:] = sorted(data[1:], key=lambda r: r[0])
    tab = f"{_word(rng)} Data"
    summary = [[h["category"], "Total", "Lines"]]
    for c in [c for c in cats if c not in missing]:
        r = len(summary) + 1
        mine = [x for x in data[1:] if x[2] == c]
        summary.append([c, Formula(f"=SUMIFS('{tab}'!D:D,'{tab}'!C:C,A{r})", round(sum(x[3] for x in mine), 2)),
                        Formula(f"=COUNTIFS('{tab}'!C:C,A{r})", len(mine))])
    return summary, [{"columns": [h["category"]], "col": h["category"], "data": tab, "missing": missing,
                      "lines": sum(1 for x in data[1:] if x[2] in missing),
                      "rows": [] if twin else list(range(2, len(summary) + 1))}], [{"name": tab, "rows": data}]


_CURRENCY = ["USD", "EUR", "GBP", "CAD", "AUD"]


@trap("model")
def _model_actuals(rng, twin):
    """A 24-month model on three tabs, each grid under a title naming its currency.
    A plan tab (revenue, costs, payroll and a profit row that subtracts the two
    cost rows, which hold positive numbers) and this cash tab (receipts, spend and
    cash) hold typed actuals for their first months and formulas after, all
    switching at the same month (3 rows and 2 rows); an inputs tab holds the
    rates. Plant: receipts, typed in the actual months, are a few percent off the
    plan's revenue they link to after the switch. Twin: receipts match revenue,
    and the cash tab's title says thousands where the plan's names a currency."""
    plan, tab = rng.choice(["Plan", "Operations", "Budget", "Forecast"]), rng.choice(["Inputs", "Assumptions"])
    year = rng.randint(2026, 2030)
    heads = [f"{m} {year + k // 12}" for k, m in enumerate(_MONTHS * 2)]
    n, act = len(heads), rng.randint(4, 8)
    cur = rng.choice(_CURRENCY)
    g, cs = rng.choice([0.02, 0.03, 0.04]), rng.choice([0.3, 0.35, 0.4])
    pay, cash0 = rng.randint(8, 14) * 1000, rng.randint(40, 90) * 1000
    u = rng.uniform(0.03, 0.08)
    inputs = [["Input", "Value", "Note"], ["Growth", g, "per month"], ["Cost share", cs, "of revenue"],
              ["Monthly payroll", pay, "per month"], ["Opening cash", cash0, "at the start"]]
    rev, costs, wages = [], [], []
    for m in range(n):
        c, p = _col(m + 1), _col(m)
        if m < act:
            r = round((rev[-1] if rev else rng.randint(30, 60) * 1000) * rng.uniform(0.98, 1.06), 2)
            rev.append(r)
            costs.append(round(r * cs * rng.uniform(0.95, 1.05), 2))
            wages.append(round(pay * rng.uniform(0.9, 1.1), 2))
        else:
            rev.append(Formula(f"={p}3*(1+{tab}!$B$2)", round(_val(rev[-1]) * (1 + g), 2)))
            costs.append(Formula(f"={c}3*{tab}!$B$3", round(_val(rev[-1]) * cs, 2)))
            wages.append(Formula(f"={tab}!$B$4", pay))
    profit = [Formula(f"={_col(m + 1)}3-{_col(m + 1)}4-{_col(m + 1)}5",
                      round(_val(rev[m]) - _val(costs[m]) - _val(wages[m]), 2)) for m in range(n)]
    plan_rows = [[f"{rng.choice(['Operating plan', 'Monthly plan', 'Budget'])} ({cur})"], ["Line"] + heads,
                 ["Revenue"] + rev, ["Costs"] + costs, ["Payroll"] + wages, ["Profit"] + profit]
    got, spent, cash = [], [], []
    for m in range(n):
        c, p = _col(m + 1), _col(m)
        if m < act:
            got.append(round(_val(rev[m]) * (1 + (0 if twin else u)), 2))
            spent.append(round(_val(costs[m]) + _val(wages[m]), 2))
        else:
            got.append(Formula(f"={plan}!{c}3", _val(rev[m])))
            spent.append(Formula(f"={plan}!{c}4+{plan}!{c}5", round(_val(costs[m]) + _val(wages[m]), 2)))
        before = f"{p}5" if m else f"{tab}!$B$5"
        was = _val(cash[-1]) if cash else cash0
        cash.append(Formula(f"={before}+{c}3-{c}4", round(was + _val(got[m]) - _val(spent[m]), 2)))
    title = "Cash (in thousands)" if twin else f"Cash ({cur})"
    rows = [[title], ["Line"] + heads, [rng.choice(["Receipts", "Collections", "Cash in"])] + got,
            [rng.choice(["Spend", "Payments", "Cash out"])] + spent, [rng.choice(["Cash", "Bank balance"])] + cash]
    more = [{"name": plan, "rows": plan_rows}, {"name": tab, "rows": inputs}]
    if twin:
        return rows, [{"columns": [], "rows": []}], more
    return rows, [{"what": "tieout", "col": rows[2][0], "link": "Revenue", "plan": plan, "typed": act,
                   "last_actual": heads[act - 1], "rows": [3]}], more


def _val(v):
    """The number a cell holds, typed or saved with its formula."""
    return v.value if isinstance(v, Formula) else v


# --------------------------------------------------------------------------
# rank 5: stacked exports
# --------------------------------------------------------------------------
_RENAMES = {"id": ["Line ID", "Record No", "Ref No", "Slip No"], "date": ["Work Date", "Day", "Posted On", "On"],
            "name": ["Name", "Person", "Staff Name", "Worker"], "code": ["Dept", "Group", "Class", "Team"],
            "qty": ["Hours", "Qty", "Units", "Pieces"], "price": ["Price", "Each", "Unit Price", "Unit Cost"],
            "amount": ["Gross", "Amount", "Line Value", "Pay"]}


@trap("structure")
def _stacked_export(rng, twin):
    """Two exports pasted one under the other: a 3-row title band, a first block
    closed by a starred total row, a second header row with renamed labels, a
    second block and a trailing report total. 30% of dates are 'MM/DD/YYYY' text,
    5 prices are '$179.00' text and 3 cells spell one group in lowercase or with a
    trailing space. The group column holds plain words, so no other detector has
    anything to ask. Twin: one clean block, plus a data row whose amount equals
    the running sum but carries an ID and a date, and a line with 'TBD' for its
    date and 'N/A' for its price and amount."""
    keys = ["id", "date", "name", "code", "qty", "price", "amount"]
    first = {k: rng.choice(_RENAMES[k]) for k in keys}
    second = {k: rng.choice([x for x in _RENAMES[k] if x != first[k]]) for k in keys}
    start, days = _start(rng), rng.randint(60, 200)
    people = [f"{f} {l}" for f, l in _people(rng, rng.randint(6, 12))]
    codes = _names(rng, rng.randint(3, 4), 3)
    pre, no = _caps(rng, 1), rng.randint(1000, 5000)

    def block(n):
        out = []
        for _ in range(n):
            q = rng.randint(1, 40)
            p = round(rng.uniform(8, 60), 2)
            out.append({"date": _day(start, rng, days), "name": rng.choice(people), "code": rng.choice(codes),
                        "qty": q, "price": p, "amount": round(q * p, 2)})
        out.sort(key=lambda r: r["date"])
        return out

    def total(rows, label):
        return {"name": label, "qty": sum(r["qty"] for r in rows), "amount": round(sum(r["amount"] for r in rows), 2)}

    title = [[f"{_word(rng)} {_word(rng)} Co"], [rng.choice(["Register export", "Activity export", "Line detail"])],
             [f"Run {(start + dt.timedelta(days=days)).strftime('%Y-%m-%d')}"], []]
    if twin:
        rows = block(rng.randint(40, 80))
        k = rng.randint(10, len(rows) - 5)
        rows.insert(k, {"date": rows[k - 1]["date"], "name": rng.choice(people), "code": rng.choice(codes),
                        "qty": sum(r["qty"] for r in rows[:k]), "price": 1.0,
                        "amount": round(sum(r["amount"] for r in rows[:k]), 2), "equal": True})
        k2 = rng.randint(3, len(rows) - 2)        # a line not dated or priced yet: data, not a header
        wait = rng.choice(["TBD", "Pending", "TBA"])
        rows.insert(k2, {"date": wait, "name": rng.choice(people), "code": rng.choice(codes), "qty": None,
                         "price": rng.choice(["N/A", "n/a", "-"]), "amount": rng.choice(["N/A", "n/a", "-"])})
        for i, r in enumerate(rows):
            r["id"] = f"{pre}{no + i}"
        table = _table([(k3, first[k3]) for k3 in keys], rows)
        eq = next(r for r in rows if r.get("equal"))
        return table, [{"columns": [first["amount"]], "rows": [], "row_equal_to_sum": eq["_row"],
                        "placeholder_row": rows[k2]["_row"], "placeholder": wait}]
    b1, b2 = block(rng.randint(30, 60)), block(rng.randint(30, 60))
    data = b1 + b2
    for i, r in enumerate(data):
        r["id"] = f"{pre}{no + i}"
    real_dates = sorted({r["date"] for r in data})
    for r in rng.sample(data, round(0.3 * len(data))):
        r["date"] = r["date"].strftime("%m/%d/%Y")
    texted = rng.sample(data, 5)
    for r in texted:
        r["price"] = f"${r['price']:,.2f}"
    code = rng.choice(codes)
    spelled = rng.sample([r for r in data if r["code"] == code], min(3, sum(r["code"] == code for r in data)))
    for r in spelled:
        r["code"] = rng.choice([code.lower(), code + " ", code.lower() + " "])
    t1 = total(b1, rng.choice(["*** RUN TOTAL ***", "*** END OF BATCH ***", "### BATCH SUM ###", "--- SUMMARY ---"]))
    t2 = total(b2, rng.choice(["Run Total", "Grand Total", "All Lines"]))
    hdr2 = {k: second[k] for k in keys}
    rows = title + [[first[k] for k in keys]]
    top = len(rows)
    body = b1 + [t1, hdr2] + b2 + [t2]
    for i, r in enumerate(body):
        r["_row"] = top + 1 + i
        rows.append([r.get(k) for k in keys])
    return rows, [{"columns": [first[k] for k in keys], "title_rows": [1, 2, 3], "title": title[0][0],
                   "header_rows": [top, hdr2["_row"]], "total_rows": [t1["_row"], t2["_row"]],
                   "total_labels": [t1["name"], t2["name"]], "data_rows": len(data),
                   "date_col": first["date"], "text_dates": sum(isinstance(r["date"], str) for r in data),
                   "distinct_dates": len(real_dates), "price_col": first["price"],
                   "price_sum": round(sum(float(str(r["price"]).lstrip("$").replace(",", "")) for r in data), 2),
                   "text_prices": [r["_row"] for r in texted], "code_col": first["code"], "code": code,
                   "spellings": [r["_row"] for r in spelled], "renamed": [[first[k], second[k]] for k in keys],
                   "rows": [t1["_row"], hdr2["_row"], t2["_row"]]}]


# --------------------------------------------------------------------------
# practice round 1 (dev/v02 brief, section D): detection gaps, each as one general class
# --------------------------------------------------------------------------
@trap("structure")
def _part_totals(rng, twin):
    """A register exported in two parts, the second under the first after a blank
    row and with no header of its own. Each part ends in a total row with no ID,
    no date and no label: its hours and pay are the part's sums and its rate is
    the part's average rate (no sum), and the last row totals both parts. Twin:
    one part, a data row whose pay equals the running sum but that carries an ID
    and a date, and no total row."""
    heads = {"id": rng.choice(["Line ID", "Record No", "Ref No", "Slip No"]),
             "date": rng.choice(["Work Date", "Pay Date", "Posted On"]),
             "name": rng.choice(["Name", "Worker", "Staff Name"]), "code": rng.choice(["Dept", "Team", "Class"]),
             "qty": rng.choice(["Hours", "Hrs"]), "price": rng.choice(["Rate", "Pay Rate"]),
             "amount": rng.choice(["Gross", "Pay", "Amount"])}
    keys = ["id", "date", "name", "code", "qty", "price", "amount"]
    start, days = _start(rng), rng.randint(90, 240)
    people = [f"{f} {l}" for f, l in _people(rng, rng.randint(6, 12))]
    codes = _names(rng, rng.randint(3, 4), 3)
    pre, no = _caps(rng, 1), rng.randint(1000, 5000)

    def block(n, lo, hi):
        out = []
        for _ in range(n):
            q = rng.choice([4, 6, 7.5, 8, 8.5, 10, 12])
            p = round(rng.uniform(14, 48), 2)
            out.append({"date": _day(start, rng, days, lo, hi), "name": rng.choice(people),
                        "code": rng.choice(codes), "qty": q, "price": p, "amount": round(q * p, 2)})
        out.sort(key=lambda r: r["date"])
        return out
    if twin:
        rows = block(rng.randint(40, 80), 0, 1)
        k = rng.randint(10, len(rows) - 5)
        rows.insert(k, {"date": rows[k - 1]["date"], "name": rng.choice(people), "code": rng.choice(codes),
                        "qty": 8, "price": 1.0, "amount": round(sum(r["amount"] for r in rows[:k]), 2),
                        "equal": True})
        for i, r in enumerate(rows):
            r["id"] = f"{pre}{no + i}"
        table = _table([(x, heads[x]) for x in keys], rows)
        eq = next(r for r in rows if r.get("equal"))
        return table, [{"columns": [heads["amount"]], "rows": [], "row_equal_to_sum": eq["_row"]}]
    b1, b2 = block(rng.randint(30, 60), 0, 0.5), block(rng.randint(30, 60), 0.5, 1)
    for i, r in enumerate(b1 + b2):
        r["id"] = f"{pre}{no + i}"
    t1 = {"qty": sum(r["qty"] for r in b1), "price": round(sum(r["price"] for r in b1) / len(b1), 2),
          "amount": round(sum(r["amount"] for r in b1), 2)}
    t2 = {"qty": sum(r["qty"] for r in b1 + b2), "amount": round(sum(r["amount"] for r in b1 + b2), 2)}
    rows = [[heads[x] for x in keys]]
    body = b1 + [t1, {}] + b2 + [t2]
    for i, r in enumerate(body):
        r["_row"] = 2 + i
        rows.append([r.get(x) for x in keys] if r else [])
    return rows, [{"columns": [heads["amount"], heads["price"]], "total_rows": [t1["_row"], t2["_row"]],
                   "data_rows": len(b1) + len(b2), "rows": [t1["_row"], t2["_row"]]}]


@trap("model")
def _link_multiplier(rng, twin):
    """The rank 20 model with its typed factor on one link month of the row that
    is typed in its first months and linked after (the tie-out row), so a question
    about the row's typed months and one about the factor share a row. Twin: the
    model with nothing planted."""
    return _plan_model(rng, twin, mult_on="coll")


@trap("copies")
def _reimport_lines(rng, twin):
    """Orders of 1 to 4 lines, numbered in one family, until a second system takes
    over at a date: from then on orders carry another numbering, a source column
    its new name and a memo its own wording. The new system also loaded the few
    days before it took over, so those orders are there twice, once under each
    numbering, with the same client, item, date and amounts. Twin: every sale is
    an invoice line and a payment line on one date with one amount and client,
    two numbering families kept side by side all year and named in a type column."""
    h = _headers(rng, "doc", "date", "party", "item", "qty", "price", "money", "note")
    src = rng.choice(["Source", "System", "Origin", "Feed"])
    start, days = _start(rng), rng.randint(200, 360)
    clients, items = _names(rng, rng.randint(24, 40)), _names(rng, rng.randint(8, 14))
    price = {it: round(rng.uniform(6, 90), 2) for it in items}
    pa, pb = _codes(rng, 2, rng.randint(2, 3))
    if twin:
        kind = rng.choice(["Type", "Kind", "Doc Type"])
        rows, n = [], rng.randint(120, 200)
        for k in range(n):
            d, who, amt = _day(start, rng, days), rng.choice(clients), round(rng.uniform(20, 900), 2)
            rows.append({"doc": f"{pa}-{1000 + k}", "date": d, "party": who, "kind": "Invoice", "amount": amt})
            rows.append({"doc": f"{pb}-{5000 + k}", "date": d, "party": who, "kind": "Payment", "amount": amt})
        rows.sort(key=lambda r: r["date"])
        table = _table([("doc", h["doc"]), ("date", h["date"]), ("party", h["party"]), ("kind", kind),
                        ("amount", h["money"])], rows)
        return table, [{"columns": [h["doc"], kind], "rows": []}]
    switch = start + dt.timedelta(days=int(days * rng.uniform(0.4, 0.65)))
    w0 = switch - dt.timedelta(days=rng.randint(3, 8))
    old, new = _codes(rng, 2, rng.randint(3, 4))
    first, width = rng.randint(1000, 8000), rng.randint(5, 6)
    gap = (switch - w0).days
    orders = sorted([_day(start, rng, days) for _ in range(rng.randint(110, 170))]
                    + [w0 + dt.timedelta(days=rng.randrange(gap)) for _ in range(rng.randint(8, 14))])
    rows, copies, k_old, k_new = [], [], 0, 0
    for d in orders:
        who = rng.choice(clients)
        lines = []
        for it in rng.sample(items, rng.randint(1, 4)):
            q = rng.randint(1, 6)
            lines.append({"date": d, "party": who, "item": it, "qty": q, "price": price[it],
                          "amount": round(q * price[it], 2)})
        if d < switch:
            k_old += 1
            rows += [dict(x, doc=f"{pa}-{first + k_old}", src=old, note=f"SALE {x['item'].upper()}") for x in lines]
        if d >= w0:
            k_new += 1
            got = [dict(x, doc=f"{pb}{k_new:0{width}d}", src=new, note=f"Sale - {x['item']}") for x in lines]
            rows += got
            if d < switch:
                copies += got
    rows.sort(key=lambda r: (r["date"], r["doc"]))
    table = _table([("doc", h["doc"]), ("date", h["date"]), ("party", h["party"]), ("item", h["item"]),
                    ("qty", h["qty"]), ("price", h["price"]), ("amount", h["money"]), ("src", src),
                    ("note", h["note"])], rows)
    return table, [{"columns": [h["doc"], h["date"], h["party"], h["money"], src], "col": h["doc"],
                    "families": [f"{pa}-", pb], "window": [w0, switch - dt.timedelta(days=1)],
                    "copies": len(copies), "source": src, "codes": [old, new],
                    "rows": [c["_row"] for c in copies] or [2]}]


@trap("handoff")
def _case_rename(rng, twin):
    """A log where each site has its own clerks. At a date a new system renames
    two sites and writes a third only in capitals ('North Yard', then 'NORTH
    YARD'): all three belong in one list of old and new names. Twin: no rename,
    and the third site written in capitals on scattered rows all through."""
    b = _log(rng, n=rng.randint(600, 800), sites=5, days=rng.randint(200, 300))
    h = _log_headers(rng)
    own = {s: [f"{f} {l}" for f, l in _people(rng, rng.randint(2, 3))] for s in b["sites"]}
    for r in b["rows"]:
        r["person"] = rng.choice(own[r["site"]])
    cut = b["start"] + dt.timedelta(days=int(b["days"] * rng.uniform(0.4, 0.6)))
    a1, a2, c3 = b["sites"][:3]
    renamed = dict(zip((a1, a2), _names(rng, 2, 3)))
    if twin:
        for r in rng.sample([r for r in b["rows"] if r["site"] == c3], 8):
            r["site"] = c3.upper()
    else:
        for r in b["rows"]:
            if r["date"] >= cut:
                r["site"] = renamed.get(r["site"], c3.upper() if r["site"] == c3 else r["site"])
    _number(b)
    table = _table(_log_cols(h), b["rows"])
    pairs = [] if twin else [[a1, renamed[a1]], [a2, renamed[a2]], [c3, c3.upper()]]
    return table, [{"columns": [h["site"], h["person"]], "col": h["site"], "pairs": pairs, "date": cut,
                    "rows": [] if twin else [r["_row"] for r in b["rows"] if r["site"] == c3.upper()]}]


_CASE_SPELLINGS = [str.upper, str.lower, lambda s: s + "."]


@trap("structure")
def _file_variants(rng, twin):
    """Lines that name a party, and a list tab of the parties with a rep each,
    where 1 or 2 parties are written another way on the list, only in capitals or
    with a closing period. Built as two files (synth.build_files), the pair is one
    question; in one book it stays a counted fact. Twin: every party written the
    same on both."""
    h = _headers(rng, "date", "doc", "party", "money")
    parties = _names(rng, rng.randint(5, 8), 3)
    odd = rng.sample(parties, rng.randint(1, 2))
    how = {p: rng.choice(_CASE_SPELLINGS) for p in odd}
    start, days = _start(rng), rng.randint(150, 300)
    pre, first = _caps(rng, 2), rng.randint(1000, 8000)
    rows = [{"date": _day(start, rng, days), "party": rng.choice(parties), "amount": round(rng.uniform(10, 900), 2)}
            for _ in range(rng.randint(150, 260))]
    rows.sort(key=lambda r: r["date"])
    for k, r in enumerate(rows):
        r["doc"] = f"{pre}-{first + k}"
    table = _table([("date", h["date"]), ("doc", h["doc"]), ("party", h["party"]), ("amount", h["money"])], rows)
    ref = [[h["party"], rng.choice(["Rep", "Contact", "Owner", "Manager"])]] + \
        [[p if twin or p not in how else how[p](p), f"{_word(rng)} {_word(rng, 3)}"] for p in parties]
    return table, [{"columns": [h["party"]], "col": h["party"],
                    "pairs": [] if twin else [[p, how[p](p)] for p in odd],
                    "rows": [] if twin else [r["_row"] for r in rows if r["party"] in how] or [2]}], [ref]


@trap("scope")
def _hire_dates(rng, twin):
    """A pay register (the main table: pay date, person ID, hours and pay) and a
    staff list with each person's hire date, reaching years before the register
    starts. The data runs over the register's pay dates. Twin: every hire date
    inside the register's span."""
    h = {"date": rng.choice(["Pay Date", "Check Date", "Paid Date"]), "id": rng.choice(["Worker ID", "Payroll ID",
                                                                                       "Badge"]),
         "name": rng.choice(["Worker", "Staff Name", "Name"]), "hours": rng.choice(["Hours", "Hours Worked", "Total Hours"]),
         "pay": rng.choice(["Gross", "Earnings", "Total Pay"]), "hire": rng.choice(["Hire Date", "Date Hired", "Start Date"])}
    start, days = _start(rng), rng.randint(120, 240)
    ids = [f"W{1000 + k}" for k in rng.sample(range(900), rng.randint(20, 40))]
    names = {i: f"{f} {l}" for i, (f, l) in zip(ids, _people(rng, len(ids)))}
    pays = sorted({start + dt.timedelta(days=14 * k) for k in range(days // 14)})
    rows = []
    for d in pays:
        for i in ids:
            hrs = rng.choice([60, 70, 72, 76, 80])
            rows.append([d, i, names[i], hrs, round(hrs * rng.uniform(15, 40), 2)])
    reg = [[h["date"], h["id"], h["name"], h["hours"], h["pay"]]] + rows
    lo = start - dt.timedelta(days=rng.randint(4, 9) * 365) if not twin else start
    hi = start - dt.timedelta(days=30) if not twin else start + dt.timedelta(days=days // 2)
    staff = [[h["id"], h["hire"], "Home Site"]] + \
        [[i, lo + dt.timedelta(days=rng.randint(0, (hi - lo).days)), rng.choice(["North", "South", "East"])]
         for i in ids]
    return reg, [{"columns": [h["date"]], "col": h["date"], "hire": h["hire"], "first": pays[0], "last": pays[-1],
                  "rows": [2]}], [staff]


@trap("blanks")
def _measure_coblank(rng, twin):
    """The line amount (quantity times price) and the quantity both blank on a
    few rows at one site within a few days: not counted. Blank on the same rows,
    the two say nothing about what a blank means, so it is asked. Twin: the
    amount and quantity blank exactly on the rows a type column marks as notes,
    which says what the blanks are."""
    b = _log(rng, n=rng.randint(500, 800))
    h = _log_headers(rng)
    kind = rng.choice(["Line Type", "Entry Kind", "Row Kind"])
    _number(b)
    for r in b["rows"]:
        r["kind"] = "Item"
    k = rng.randint(5, 9)
    if twin:
        hit = rng.sample(b["rows"], k)
        for r in hit:
            r["kind"], r["qty"], r["amount"] = "Note", None, None
    else:
        site = rng.choice(b["sites"])
        at = rng.randrange(len(b["rows"]) - 40)
        hit = b["rows"][at:at + k]
        for r in hit:
            r["site"], r["qty"], r["amount"] = site, None, None
    table = _table(_log_cols(h, ("kind", kind)), b["rows"])
    return table, [{"columns": [h["money"], h["qty"]], "col": h["money"], "qty": h["qty"],
                    "rows": [] if twin else [r["_row"] for r in hit]}]


_UNITS = ["LB", "EA", "QT", "GAL", "DZ", "BUNCH", "ROLL", "BOX", "OZ", "PT"]


@trap("unit_price")
def _case_price(rng, twin):
    """Categories each counted in their own unit and at their own price level
    (spread wide: the cheapest level is a tenth of the dearest or less), and one
    category counted in a unit no other uses, its price for a case of 24 to 48.
    Twin: that category priced per unit, inside the others' range."""
    cats = _names(rng, rng.randint(5, 7), 3)
    units = rng.sample(_UNITS, len(cats))
    odd, odd_unit = cats[0], rng.choice(["SACK", "CAN", "JAR", "TIN"])
    levels = [0.5, 1.2, 2.5, 4.0, 6.0, 8.0, 5.0][:len(cats) - 1]
    rng.shuffle(levels)
    level = dict(zip(cats[1:], levels))
    level[odd] = rng.uniform(5, 8) * (1 if twin else rng.choice([24, 36, 48]))
    unit = dict(zip(cats[1:], units[1:]))
    unit[odd] = odd_unit
    items = {c: _names(rng, rng.randint(3, 5), 2) for c in cats}
    cost = {(c, it): round(level[c] * rng.uniform(0.7, 1.4), 2) for c in cats for it in items[c]}
    h = {"date": rng.choice(["Count Date", "Date", "Week Ending"]), "cat": rng.choice(["Category", "Group", "Section"]),
         "item": rng.choice(["Item", "Product", "Article"]), "unit": rng.choice(["Unit", "UOM", "Count Unit"]),
         "qty": rng.choice(["Count", "Qty", "On Hand"]), "price": rng.choice(["Unit Cost", "Cost", "Price"]),
         "money": rng.choice(["Stock Value", "Extended", "Line Value"])}
    start, days = _start(rng), rng.randint(120, 300)
    rows = []
    for _ in range(rng.randint(320, 520)):
        c = rng.choice(cats)
        it = rng.choice(items[c])
        q = rng.randint(1, 10)
        rows.append({"date": _day(start, rng, days), "cat": c, "item": it, "unit": unit[c], "qty": q,
                     "price": cost[(c, it)], "amount": round(q * cost[(c, it)], 2)})
    rows.sort(key=lambda r: r["date"])
    table = _table([(k, h[k]) for k in ("date", "cat", "item", "unit", "qty", "price")] + [("amount", h["money"])],
                   rows)
    return table, [{"columns": [h["cat"], h["price"]], "col": h["cat"], "value": None if twin else odd,
                    "price": h["price"], "unit": odd_unit,
                    "rows": [] if twin else [r["_row"] for r in rows if r["cat"] == odd]}]


@trap("odd_group")
def _odd_twice(rng, twin):
    """The same site unlike the others on two tabs (a log and a count sheet kept
    by the same staff): present only in the middle of the dates, with its own
    clerks, on both. One question names both tabs. Twin: a seasonal site in the
    same window on both tabs that shares clerks and items with the others."""
    first, second = _odd_group(rng, twin), _odd_group(rng, twin)
    odd = first[1][0]["value"]
    rows2 = second[0]
    j = rows2[0].index(second[1][0]["col"])
    other = second[1][0]["value"]
    for r in rows2[1:]:
        if r[j] == other:
            r[j] = odd
    plant = dict(first[1][0], second_rows=second[1][0]["rows"])
    return first[0], [plant], [rows2]


# --------------------------------------------------------------------------
# practice round 3: detection and question gaps, each as one general class
# --------------------------------------------------------------------------
@trap("boundary")
def _two_exports(rng, twin):
    """Two exports of one register, the second after the first in time: a first
    block closed by a total row, a second header row with renamed labels, a
    second block and its own total row. The second system writes another ID
    prefix and names first name first. Twin: one export, one ID form and one name
    form, no second header row and no total rows."""
    keys = ["id", "date", "name", "code", "qty", "price", "amount"]
    first = {k: rng.choice(_RENAMES[k]) for k in keys}
    second = {k: rng.choice([x for x in _RENAMES[k] if x != first[k]]) for k in keys}
    start, days = _start(rng), rng.randint(160, 300)
    split = start + dt.timedelta(days=int(days * rng.uniform(0.4, 0.6)))
    people = _people(rng, rng.randint(6, 10))
    codes = _names(rng, rng.randint(3, 4), 3)
    pa, pb = _codes(rng, 2, 2)

    def block(n, lo, hi, new):
        out = []
        for _ in range(n):
            f, l = rng.choice(people)
            q, p = rng.randint(1, 30), round(rng.uniform(8, 60), 2)
            d = start + dt.timedelta(days=rng.randint(int(lo * days), int(hi * days) - 1))
            out.append({"date": d, "name": f"{f} {l}" if new else f"{l.upper()}, {f.upper()}", "code": rng.choice(codes),
                        "qty": q, "price": p, "amount": round(q * p, 2)})
        out.sort(key=lambda r: r["date"])
        return out
    cut = (split - start).days / days
    b1, b2 = block(rng.randint(120, 180), 0, cut, False), block(rng.randint(120, 180), cut, 1, not twin)
    for i, r in enumerate(b1):
        r["id"] = f"{pa}-{1000 + i}"
    for i, r in enumerate(b2):
        r["id"] = f"{pa}-{1000 + len(b1) + i}" if twin else f"{pb}{r['date'].year % 100:02d}-{i + 1:04d}"
    if twin:
        rows = _table([(k, first[k]) for k in keys], b1 + b2)
        return rows, [{"columns": [first["id"], first["name"]], "rows": []}]

    def total(rows, label):
        return {"name": label, "qty": sum(r["qty"] for r in rows), "amount": round(sum(r["amount"] for r in rows), 2)}
    t1 = total(b1, rng.choice(["*** RUN TOTAL ***", "--- SUMMARY ---"]))
    t2 = total(b2, rng.choice(["Run Total", "Grand Total"]))
    hdr2 = {k: second[k] for k in keys}
    rows = [[first[k] for k in keys]]
    body = b1 + [t1, hdr2] + b2 + [t2]
    for i, r in enumerate(body):
        r["_row"] = 2 + i
        rows.append([r.get(k) for k in keys])
    return rows, [{"columns": [first["id"], first["name"]], "date": split, "header_row": hdr2["_row"],
                   "total_rows": [t1["_row"], t2["_row"]], "renamed": [[first[k], second[k]] for k in keys],
                   "rows": [t1["_row"], hdr2["_row"], t2["_row"]]}]


@trap("conformity")
def _list_text(rng, twin):
    """The list price book (one vendor at the list from month 7, a few lines above
    it in month 10) with 5 prices typed as text ('$31.50'): the question about
    the lines off the list says how they were read. Twin: every vendor floats
    around the list, with the same 5 text prices."""
    table, plants, more = _list_price(rng, twin)
    h = table[0]
    j = h.index(plants[0]["col"])
    rows = [r for r in table[1:] if isinstance(r[j], (int, float))]
    for r in rng.sample(rows, 5):
        r[j] = f"${r[j]:,.2f}"
    return table, [dict(plants[0], text_prices=5)], more


@trap("conformity")
def _list_terms(rng, twin):
    """The list price book with a terms tab giving each vendor dates and its terms
    in words: the vendor at the list resets its list every <month>, the month its
    run at the list starts. Twin: every vendor floats around the list, and no
    vendor's terms name a month."""
    table, plants, more = _list_price(rng, twin)
    p = plants[0]
    h = table[0]
    jv, jd = h.index(next(x for x in ("Vendor", "Supplier", "Distributor") if x in h)), 0
    vendors = sorted({r[jv] for r in table[1:]})
    days = [r[jd] for r in table[1:]]
    mon = dt.datetime.strptime(p["from"] + "-01", "%Y-%m-%d").strftime("%B")
    terms = [[h[jv], "Start", "End", "Terms"]]
    for v in vendors:
        said = f"List prices reset every {mon}" if v == p["value"] and not twin else rng.choice(_TERMS_TEXT)
        terms.append([v, min(days) - dt.timedelta(days=30), max(days) + dt.timedelta(days=200), said])
    return table, [dict(p, month=mon, terms=f"List prices reset every {mon}")], more + [terms]


@trap("scope")
def _title_short(rng, twin):
    """A data tab under a title naming a period of months and a run note, whose
    rows run past the period's end at the bottom, and a summary tab whose SUMIFS
    read the data only down to the period's last row. Twin: the title's period
    covers every row and the SUMIFS read to the last one."""
    b = _log(rng, n=rng.randint(260, 380))
    h = _log_headers(rng, "category")
    cats = _names(rng, rng.randint(3, 5), 3)
    for r in b["rows"]:
        r["cat"] = rng.choice(cats)
    title, end = _titled(rng, b, 0.78, 0.86, covers=twin)
    _number(b)
    top = 3
    cols = _log_cols(h, ("cat", h["category"]))
    body = _table(cols, b["rows"], top=top)
    table = [[title], [f"Export, run {(end + dt.timedelta(days=5)).strftime('%m/%d/%Y')}"], []] + body
    last_in = max(r["_row"] for r in b["rows"] if r["date"] <= end)
    tab = f"{_word(rng)} Data"
    jc, jm = [k for k, _h in cols].index("cat"), [k for k, _h in cols].index("amount")
    cl, ml = _col(jc), _col(jm)
    first = top + 2
    summary = [[h["category"], "Total"]]
    for c in cats:
        r = len(summary) + 1
        v = round(sum(x["amount"] for x in b["rows"] if x["cat"] == c and x["_row"] <= last_in), 2)
        summary.append([c, Formula(f"=SUMIFS('{tab}'!${ml}${first}:${ml}${last_in},'{tab}'!${cl}${first}:${cl}${last_in},"
                                   f"A{r})", v)])
    late = [r["_row"] for r in b["rows"] if r["date"] > end]
    return summary, [{"columns": [h["date"]], "col": h["date"], "data": tab, "title": title,
                      "end": end, "range_end": last_in, "rows": list(range(2, len(summary) + 1)), "late": late}], \
        [{"name": tab, "rows": table}]


@trap("entity")
def _sentinel_roster(rng, twin):
    """The sentinel pay register with a roster tab that lists every ID with a
    hire date, the test ID included. Twin: the salaried twin with its roster."""
    table, plants = _sentinel(rng, twin)
    h = table[0]
    ji, jn = 1, 2
    ids = {}
    for r in table[1:]:
        ids.setdefault(r[ji], r[jn])
    roster = [[h[ji], "Hire Date", "Home Site"]] + [[i, dt.datetime(2019, 1, 1) + dt.timedelta(days=rng.randint(0, 900)),
                                                    rng.choice(["North", "South", "East"])] for i in ids]
    return table, plants, [roster]


@trap("codes")
def _lookup_status(rng, twin):
    """A product list (40 items, a status on each) and the lines that sell them:
    one status is on 4 items (a tenth of the list) whose lines carry about 1% of
    the money, so what the answer moves is measured on the lines. Twin: every
    item on the list has the same status."""
    items = _names(rng, 40, 3)
    sku = {it: f"{_caps(rng, 2)}-{100 + k}" for k, it in enumerate(items)}
    retail = {it: round(rng.uniform(5, 40), 2) for it in items}
    rare_word = rng.choice(["Withdrawn", "Retired", "Phased Out"])
    common = rng.choice(["Active", "Current", "Live"])
    start, days = _start(rng), rng.randint(150, 300)
    rare = [] if twin else items[-4:]
    weights = [0.1 if it in rare else 1.0 for it in items]
    rows = []
    for k in range(rng.randint(500, 700)):
        it = rng.choices(items, weights)[0]
        q, pr = rng.randint(1, 6), round(retail[it] * rng.uniform(0.85, 0.99), 2)
        rows.append({"date": _day(start, rng, days), "sku": sku[it], "qty": q, "price": pr, "amount": round(q * pr, 2)})
    rows.sort(key=lambda r: r["date"])
    pre = _caps(rng, 2)
    for i, r in enumerate(rows):
        r["doc"] = f"{pre}-{3000 + i}"
    table = _table([("date", "Date"), ("doc", "Order No"), ("sku", "SKU"), ("qty", "Qty"), ("price", "Unit Price"),
                    ("amount", "Amount")], rows)
    lst = [["SKU", "Product", "Status", "Retail"]] + [[sku[it], it, rare_word if it in rare else common, retail[it]]
                                                       for it in items]
    mine = {sku[i] for i in rare}
    share = sum(r["amount"] for r in rows if r["sku"] in mine) / sum(r["amount"] for r in rows)
    return table, [{"columns": ["Status"], "col": "Status", "value": None if twin else rare_word,
                    "items": sorted(mine), "share": round(share, 6),
                    "rows": [r["_row"] for r in rows if r["sku"] in mine] or [2]}], [lst]


def _stock_model(rng, twin):
    """(grid rows, inputs rows) of a 24-month model of a stock (subscribers,
    members or seats): at start, new (x a growth rate), lost (x a churn rate,
    twin: none) and at end (start plus new less lost)."""
    tab = rng.choice(["Rates", "Assumptions", "Drivers"])
    year = rng.randint(2026, 2030)
    heads = [f"{m} {year + k // 12}" for k, m in enumerate(_MONTHS * 2)]
    g, ch, r0 = rng.choice([0.03, 0.04, 0.05]), rng.choice([0.01, 0.015, 0.02]), rng.randint(50, 200) * 1000
    gl, cl = rng.choice(["Growth rate", "Monthly growth"]), rng.choice(["Monthly attrition", "Attrition"])
    noun = rng.choice(["Subscribers", "Members", "Seats"])
    start_l, new_l, lost_l, end_l = (f"{noun} at start", f"New {noun.lower()}", f"Lost {noun.lower()}",
                                     f"{noun} at end")
    inputs = [["Input", "Value", "Note"], [gl, g, f"rate on {noun.lower()} at the start of each month"],
              [f"{noun} on day one", r0, "at the start"]]
    if not twin:
        inputs.append([cl, ch, f"rate of {noun.lower()} leaving, on those at the start of each month"])
    names = [start_l, new_l] + ([] if twin else [lost_l]) + [end_l]
    row = {k: i + 2 for i, k in enumerate(names)}
    gref, sref, cref = f"{tab}!$B$2", f"{tab}!$B$3", f"{tab}!$B$4"
    grid = [["Line"] + heads] + [[k] for k in names]
    begin = r0
    for m in range(len(heads)):
        c, p = _col(m + 1), _col(m)
        new = round(begin * g, 2)
        lost = 0.0 if twin else round(begin * ch, 2)
        end = round(begin + new - lost, 2)
        vals = {start_l: Formula(f"={sref}" if m == 0 else f"={p}{row[end_l]}", begin),
                new_l: Formula(f"={c}{row[start_l]}*{gref}", new),
                lost_l: Formula(f"={c}{row[start_l]}*{cref}", lost),
                end_l: Formula(f"={c}{row[start_l]}+{c}{row[new_l]}" + ("" if twin else f"-{c}{row[lost_l]}"), end)}
        for k in names:
            grid[names.index(k) + 1].append(vals[k])
        begin = end
    return grid, inputs, tab, gl, cl, (new_l, lost_l)


@trap("model")
def _model_flows(rng, twin):
    """A model where a growth rate and a churn rate each multiply the same
    beginning stock in their own row, and the ending row adds the one and
    subtracts the other: the growth rate's question asks its period and its basis
    together. Twin: no churn, the ending row adds new business only."""
    grid, inputs, tab, gl, cl, (new_l, lost_l) = _stock_model(rng, twin)
    return grid, [{"what": "sibling", "col": new_l, "input": gl, "other": None if twin else lost_l,
                   "rows": [3]}], [{"name": tab, "rows": inputs}]


@trap("grain")
def _panel_keys(rng, twin):
    """A weekly count panel (sites x items x weeks) whose rows also carry a count
    number unique on every row: the counted key (the count number) and the
    panel's own (site and item on each week) name different columns, so the
    one-tap confirm of what a row is enters a round. Twin: a weekly log of what
    was sold (each week a different third of the site and item pairs), with the
    same count number on every row."""
    table, plants = _snapshot_panel(rng, twin)
    h = table[0]
    out = [["Count No"] + h] + [[f"CT-{10000 + i}"] + r for i, r in enumerate(table[1:])]
    return out, [dict(plants[0], rows=[2], key="Count No")]


@trap("trend")
def _price_parity(rng, twin):
    """Weekly purchases where every site pays the same price for an item in a
    week, and prices step up: a one-tap confirm that they buy from the same
    suppliers at the same price. Twin: prices that never change, each site paying
    its own."""
    table, plants = _price_trend(rng, twin)
    if twin:
        h = table[0]
        jp, js, jq, ja = h.index(plants[0]["col"]), h.index(plants[0]["columns"][2]), h.index("Qty"), len(h) - 1
        sites = sorted({r[js] for r in table[1:]})
        bump = {s: 1 + 0.08 * k for k, s in enumerate(sites)}
        for r in table[1:]:
            r[jp] = round(r[jp] * bump[r[js]], 2)
            r[ja] = round(r[jq] * r[jp], 2)
    return table, plants


@trap("codes")
def _recoded_items(rng, twin):
    """Order lines against an item list: 3 codes on the lines are not on the list
    and share their description with a listed code (the same item under a new
    code), and 2 codes with their own prefix are fees. Twin: the 3 missing codes'
    descriptions match nothing on the list."""
    items = _names(rng, rng.randint(24, 32), 3)
    pre, fee_pre = _caps(rng, 2), _caps(rng, 3)
    code = {it: f"{pre}{1000 + k * 7}" for k, it in enumerate(items)}
    price = {it: round(rng.uniform(4, 60), 2) for it in items}
    olds = rng.sample(items, 3)
    old_code = {it: f"{pre}{5000 + k * 11}" for k, it in enumerate(olds)}
    fees = [f"{fee_pre}-{w.upper()}" for w in _names(rng, 2, 2)]
    start, days = _start(rng), rng.randint(150, 300)
    rows = []
    for _ in range(rng.randint(600, 800)):
        it = rng.choice(items)
        q = rng.randint(1, 8)
        rows.append({"date": _day(start, rng, days), "code": code[it], "desc": it, "qty": q, "price": price[it],
                     "amount": round(q * price[it], 2)})
    for it in olds:
        for _ in range(rng.randint(4, 6)):
            q = rng.randint(1, 8)
            rows.append({"date": _day(start, rng, days, 0, 0.3), "code": old_code[it],
                         "desc": it if not twin else f"{it} {_word(rng)}", "qty": q, "price": price[it],
                         "amount": round(q * price[it], 2)})
    for f in fees:
        for _ in range(rng.randint(5, 7)):
            p = round(rng.uniform(5, 30), 2)
            rows.append({"date": _day(start, rng, days), "code": f, "desc": f"{f.split('-')[1].title()} fee", "qty": 1,
                         "price": p, "amount": p})
    rows.sort(key=lambda r: r["date"])
    table = _table([("date", "Date"), ("code", "Item Code"), ("desc", "Description"), ("qty", "Qty"),
                    ("price", "Unit Price"), ("amount", "Amount")], rows)
    groups = _names(rng, 4, 3)
    lst = [["Item Code", "Description", "Group"]] + [[code[it], it, groups[k % 4]] for k, it in enumerate(items)]
    return table, [{"columns": ["Item Code"], "col": "Item Code", "pairs": [[old_code[i], code[i]] for i in olds],
                    "fees": fees, "rows": [r["_row"] for r in rows if r["code"] not in code.values()]}], [lst]


@trap("unit")
def _piece_rate(rng, twin):
    """A pay register (job, hours, overtime hours, rate, gross = hours x rate plus
    overtime at time and a half) where one job is paid per visit: its 'hours' are
    whole numbers 3 to 34 at a rate 1.7 times every other job's. Twins: a
    part-time job (few hours, a rate like its peers') on some seeds, a senior job
    (the same hours, a rate far above) on the others."""
    jobs = _names(rng, 5, 3)
    odd = jobs[0]
    base = {j: rng.uniform(15, 40) for j in jobs[1:]}
    top = max(base.values())
    kind = "piece" if not twin else ("part" if rng.randint(0, 1) == 0 else "senior")
    base[odd] = top * (1.7 if kind != "part" else 0.9)
    start = _start(rng)
    staff = [(f"{_caps(rng, 1)}{100 + k}", rng.choice(jobs)) for k in range(rng.randint(30, 45))]
    for k in range(4):
        staff[k] = (staff[k][0], odd)
    rows = []
    for p in range(rng.randint(10, 16)):
        d = start + dt.timedelta(days=14 * p)
        for sid, job in staff:
            if job == odd and kind in ("piece", "part"):
                hrs = rng.randint(3, 34) if kind == "piece" else round(rng.uniform(4, 22), 2)
                ot = 0
            else:
                hrs = round(rng.uniform(56, 96), 2)
                ot = round(rng.uniform(1, 8), 2) if rng.random() < 0.15 else 0
            rate = round(base[job] * rng.uniform(0.95, 1.05), 2)
            rows.append({"date": d, "id": sid, "job": job, "hrs": hrs, "ot": ot, "rate": rate,
                         "gross": round(hrs * rate + ot * rate * 1.5, 2)})
    table = _table([("date", "Pay Date"), ("id", "Worker ID"), ("job", "Position"), ("hrs", "Regular Hours"),
                    ("ot", "Overtime Hours"), ("rate", "Hourly Rate"), ("gross", "Gross Wages")], rows)
    return table, [{"columns": ["Position", "Regular Hours"], "col": "Regular Hours", "group": "Position", "value": odd,
                    "variant": kind,
                    "rows": [r["_row"] for r in rows if r["job"] == odd]}]


@trap("odd_group")
def _branch_scope(rng, twin):
    """A pay register over branches, a branches tab (code, name, manager) and a
    roster (ID, first and last name, job). Every branch is written by name until
    a date and by its code after. One branch has 13% of the rows and 60% of the
    overtime, none of two jobs every other branch has, and a manager who is on no
    other tab. Twin: branches by name throughout, and a busy branch with 20% of
    the rows, 25% of the overtime, every job and a manager on the roster."""
    codes = _codes(rng, 5, 3)
    names = _names(rng, 5, 3)
    odd = codes[0]
    jobs = ["Manager", "Scheduler"] + _names(rng, 3, 3)
    people = _people(rng, 60)
    staff = []
    for k, (f, l) in enumerate(people):
        br = codes[k % 5]
        job = jobs[0] if k < 5 else jobs[1] if k < 10 else rng.choice(jobs[2:])
        if br == odd and job in jobs[:2] and not twin:
            job = rng.choice(jobs[2:])            # the odd branch has no manager and no scheduler on the register
        staff.append({"id": f"E{200 + k}", "first": f, "last": l, "br": br, "job": job})
    mgr = {c: f"{staff[k]['first']} {staff[k]['last']}" for k, c in enumerate(codes)}
    if not twin:
        mgr[odd] = f"{_word(rng)} {_word(rng, 3)}"      # on no roster
    start, periods = _start(rng), rng.randint(14, 20)
    switch = int(periods * rng.uniform(0.4, 0.6))
    share = 0.13 if not twin else 0.2
    rows = []
    for p in range(periods):
        d = start + dt.timedelta(days=14 * p)
        for s_ in staff:
            here = s_["br"] == odd
            if rng.random() > (share / 0.2 if here else (1 - share) / 0.8):
                continue
            hrs = round(rng.uniform(60, 80), 2)
            ot = (round(rng.uniform(8, 16), 2) if not twin else round(rng.uniform(1, 3), 2)) if here else \
                (round(rng.uniform(1, 3), 2) if rng.random() < 0.3 else 0)
            rate = round(rng.uniform(18, 35), 2)
            name = names[codes.index(s_["br"])]
            rows.append({"date": d, "id": s_["id"], "br": s_["br"] if p >= switch and not twin else name,
                         "job": s_["job"], "hrs": hrs, "ot": ot, "rate": rate,
                         "gross": round(hrs * rate + ot * rate * 1.5, 2)})
    table = _table([("date", "Payment Date"), ("id", "Worker ID"), ("job", "Position"), ("br", "Location"),
                    ("hrs", "Regular Hours"), ("ot", "Overtime Hours"), ("rate", "Hourly Rate"),
                    ("gross", "Gross Wages")], rows)
    branches = [["Location Code", "Location Name", "Manager"]] + [[c, names[k], mgr[c]] for k, c in enumerate(codes)]
    roster = [["Worker ID", "First", "Last", "Position"]] + [[x["id"], x["first"], x["last"], x["job"]]
                                                             for x in staff]
    return table, [{"columns": ["Location"], "col": "Location", "value": odd, "also": names[0],
                    "rows": [r["_row"] for r in rows if r["br"] in (odd, names[0])]}], [branches, roster]


@trap("grain")
def _pay_cycle_title(rng, twin):
    """A pay register run every 14 days on a Friday, under a title naming its
    period, which ends 10 days after the last run; manual checks (one code) fall
    on Tuesdays on more dates than the runs but on 1% of the rows. Twins:
    scattered run dates (even seeds), or the last run on the title's end (odd seeds)."""
    start = _start(rng)
    start += dt.timedelta(days=(4 - start.weekday()) % 7)                  # a Friday
    staff = [(f"{_caps(rng, 1)}{rng.randint(100, 999)}", f"{f} {l}") for f, l in _people(rng, rng.randint(40, 60))]
    n = rng.randint(16, 22)
    runs = [start + dt.timedelta(days=14 * k) for k in range(n)]
    kind = "plant" if not twin else ("scattered" if rng.randint(0, 1) == 0 else "ends")
    if kind == "scattered":
        runs = sorted({start + dt.timedelta(days=14 * k + rng.randint(-5, 5)) for k in range(n)})
    extra = sorted({start + dt.timedelta(days=7 * k + 4) for k in range(2 * n)})[:n + 6]      # Tuesdays, more dates
    rows = []
    for d in runs:
        for pid, name in staff:
            hrs = round(rng.uniform(40, 80), 2)
            rows.append({"date": d, "id": pid, "name": name, "type": "R", "gross": round(hrs * rng.uniform(15, 40), 2)})
    k_extra = max(1, len(rows) // 100)
    for d in rng.sample(extra, min(len(extra), k_extra)) if kind == "plant" else []:
        pid, name = rng.choice(staff)
        rows.append({"date": d, "id": pid, "name": name, "type": "M", "gross": round(rng.uniform(100, 900), 2)})
    rows.sort(key=lambda r: r["date"])
    last = max(runs)
    end = last + dt.timedelta(days=10 if kind != "ends" else 0)
    title = f"Pay runs {start.strftime('%m/%d/%Y')} through {end.strftime('%m/%d/%Y')}"
    table = [[title], []] + _table([("date", "Payment Date"), ("id", "Worker ID"), ("name", "Employee"),
                                    ("type", "Type"), ("gross", "Gross")], rows, top=2)
    return table, [{"columns": ["Payment Date"], "col": "Payment Date", "last": last, "end": end, "variant": kind,
                    "rows": [r["_row"] for r in rows if r["date"] == last]}]


@trap("opening")
def _opening_ties(rng, twin):
    """A journal whose first entry carries opening balances in, with two ordinary
    entries on the same first date. Twin: no opening entry, the ordinary entries on
    the first date, one of them saying 'opening' in its ordinary sense."""
    table, plants = _journal(rng, opening=not twin)
    h = table[0]
    je, jd, ja, jdr, jcr, jm = (h.index(x) for x in (h[0], h[1], h[2], "Debit", "Credit", h[5]))
    first = min(r[jd] for r in table[1:])
    accts = sorted({r[ja] for r in table[1:]})
    pre = str(table[1][je])[:2]
    extra = []
    for k in range(2):
        amt = round(rng.uniform(50, 500), 2)
        memo = rng.choice(["Grand opening flyers", "Store opening supplies"]) if twin and k == 0 else _word(rng)
        e = f"{pre}{9000 + k}"
        a, b = rng.sample(accts, 2)
        extra += [[e, first, a, amt, None, memo], [e, first, b, None, amt, memo]]
    body = table[1:]
    at = 1 if not twin else 0
    n_open = sum(1 for r in body if r[je] == body[0][je]) if not twin else 0
    rows = [h] + body[:n_open] + extra + body[n_open:]
    return rows, [dict(plants[0], rows=list(range(2, 2 + n_open))) if plants else {"columns": [], "rows": []}]


@trap("opening")
def _carried_in(rng, twin):
    """A charges and payments ledger by holder: monthly charges, payments on their
    own rows, and on the first date 4 rows with their own code that carry balances
    in ('Balance forward <an earlier date>'), no account and a charge only. Twin: none of
    those rows."""
    ids = [f"{_caps(rng, 1)}{n}" for n in sorted(rng.sample(range(100, 999), rng.randint(24, 36)))]
    start = _start(rng)
    months = rng.randint(8, 12)
    code_c, code_p, code_b = rng.choice(["Rent", "Dues", "Fee"]), rng.choice(["Payment", "Receipt"]), "PRIOR"
    gl_c, gl_p = rng.choice(["Rent Income", "Dues Income"]), rng.choice(["Cash", "Operating Cash"])
    rows = []
    for m in range(months):
        d = dt.datetime(start.year + (start.month - 1 + m) // 12, (start.month - 1 + m) % 12 + 1, 1)
        for i in ids:
            amt = round(rng.uniform(400, 1500), 2)
            rows.append({"date": d, "id": i, "code": code_c, "desc": f"{code_c} for {d.strftime('%B %Y')}",
                         "gl": gl_c, "charge": amt, "paid": 0})
            rows.append({"date": d + dt.timedelta(days=rng.randint(1, 20)), "id": i, "code": code_p,
                         "desc": f"{code_p} received", "gl": gl_p, "charge": 0, "paid": amt})
    plant = []
    if not twin:
        before = (start - dt.timedelta(days=1)).strftime("%m/%d/%Y")
        carry = rng.choice(["Balance forward", "Brought forward"])
        for i in rng.sample(ids, 4):
            rows.append({"date": start, "id": i, "code": code_b, "desc": f"{carry} {before}", "gl": None,
                         "charge": round(rng.uniform(200, 900), 2), "paid": 0})
            plant.append(i)
    rows.sort(key=lambda r: (r["date"], r["code"] != code_b))
    table = _table([("date", "Date"), ("id", "Holder ID"), ("code", "Code"), ("desc", "Description"),
                    ("gl", "Account"), ("charge", "Charge"), ("paid", "Paid")], rows)
    return table, [{"columns": ["Code"], "col": "Code", "value": code_b, "said": None if twin else carry,
                    "rows": [r["_row"] for r in rows if r["code"] == code_b] or [2]}]


@trap("boundary")
def _status_switch(rng, twin):
    """Order lines exported by one system and then another: the order number
    changes form, and at the switch one status is written with another spelling
    and another is renamed. Every status sells every item, so shared items cannot
    pair them. Twin: one system, statuses in even shares, and one item sold only
    mid-year."""
    items = _names(rng, rng.randint(8, 12), 3)
    start, days = _start(rng), rng.randint(240, 360)
    split = start + dt.timedelta(days=int(days * rng.uniform(0.4, 0.6)))
    done_a, done_b = rng.choice([("Finished", "Handed Over"), ("Closed", "Delivered"), ("Settled", "Received")])
    can_a = rng.choice(["Rejected", "Voided"])
    can_b = {"Rejected": "Rejects", "Voided": "Voids"}[can_a]
    other = "Returned"
    pa, pb = _codes(rng, 2, 2)
    rows = []
    for _ in range(rng.randint(500, 700)):
        d = _day(start, rng, days)
        after = d >= split and not twin
        st = rng.choices([done_b if after else done_a, can_b if after else can_a, other],
                         [0.4, 0.32, 0.28] if twin else [0.8, 0.08, 0.12])[0]
        it = rng.choice(items[1:])
        if twin and 0.4 * days <= (d - start).days <= 0.6 * days and rng.random() < 0.3:
            it = items[0]                     # a seasonal item: sold only mid-year
        q = rng.randint(1, 5)
        rows.append({"date": d, "item": it, "status": st, "qty": q, "amount": round(q * rng.uniform(5, 40), 2)})
    rows.sort(key=lambda r: r["date"])
    na = nb = 0
    for r in rows:
        if r["date"] >= split and not twin:
            nb += 1
            r["doc"] = f"#{70000 + nb}"
        else:
            na += 1
            r["doc"] = f"{pa}-{2000 + na}"
    table = _table([("date", "Date"), ("doc", "Order ID"), ("item", "Item"), ("status", "Status"), ("qty", "Qty"),
                    ("amount", "Amount")], rows)
    if twin:
        return table, [{"columns": ["Status"], "rows": []}]
    return table, [{"columns": ["Status", "Order ID"], "col": "Status", "date": split,
                    "pairs": [[done_a, done_b], [can_a, can_b]],
                    "rows": [r["_row"] for r in rows if r["status"] in (done_b, can_b)]}]


@trap("entity")
def _unpaid_ledger(rng, twin):
    """A ledger of monthly charges and payments on their own rows, by holder: one
    holder is charged every month and never pays, where every other pays. Twin:
    that holder pays once, in one lump, for the whole period."""
    ids = [f"{_caps(rng, 1)}{n}" for n in sorted(rng.sample(range(100, 999), rng.randint(24, 40)))]
    units = {i: f"{rng.randint(1, 4)}{rng.randint(0, 2)}{rng.randint(1, 9)}" for i in ids}
    never = rng.choice(ids)
    start = _start(rng)
    months = rng.randint(9, 12)
    rows, total = [], 0.0
    for m in range(months):
        d = dt.datetime(start.year + (start.month - 1 + m) // 12, (start.month - 1 + m) % 12 + 1, 1)
        for i in ids:
            amt = round(rng.uniform(600, 1800), 2)
            rows.append({"date": d, "id": i, "unit": units[i], "code": "Monthly charge", "charge": amt,
                         "paid": 0})
            if i == never:
                total += amt
                continue
            rows.append({"date": d + dt.timedelta(days=rng.randint(1, 20)), "id": i, "unit": units[i],
                         "code": "Payment received", "charge": 0, "paid": round(amt * rng.uniform(0.95, 1.05), 2)})
    if twin:
        rows.append({"date": dt.datetime(start.year, start.month, 5), "id": never, "unit": units[never],
                     "code": "Payment received", "charge": 0, "paid": round(total, 2)})
    rows.sort(key=lambda r: r["date"])
    table = _table([("date", "Date"), ("id", "Holder ID"), ("unit", "Unit"), ("code", "Description"),
                    ("charge", "Charge"), ("paid", "Payment")], rows)
    return table, [{"columns": ["Holder ID"], "col": "Holder ID", "value": never,
                    "rows": [r["_row"] for r in rows if r["id"] == never]}]


@trap("codes")
def _status_roster(rng, twin, abbr=False):
    """A roster of units as of a date: one status is exactly the rows with no
    holder and no rent. Twin: statuses in even shares, blanks spread over all.
    abbr: the month-to-month status written as an abbreviation."""
    n = rng.randint(80, 120)
    sts = ["Current", "M2M" if abbr else "Month to month", "Notice", "Vacant"]
    rows = []
    for k in range(n):
        st = rng.choices(sts, [0.3, 0.25, 0.25, 0.2] if twin else [0.7, 0.15, 0.05, 0.1])[0]
        blank = st == "Vacant" if not twin else rng.random() < 0.1
        rows.append({"unit": f"{100 + k}", "prop": rng.choice(["North", "South"]),
                     "id": None if blank else f"T{10000 + k}", "status": st,
                     "market": round(rng.uniform(900, 2200), 0), "rent": None if blank else round(rng.uniform(850, 2100), 0)})
    as_of = _start(rng) + dt.timedelta(days=200)
    table = [[f"Unit roster as of {as_of.strftime('%m/%d/%Y')}"], []] + _table(
        [("unit", "Unit"), ("prop", "Property"), ("id", "Holder ID"), ("status", "Status"), ("market", "Market Rent"),
         ("rent", "Actual Rent")], rows, top=2)
    return table, [{"columns": ["Status", "Actual Rent"], "col": "Status", "value": "Vacant",
                    "rows": [r["_row"] for r in rows if r["status"] == "Vacant"] or [3]}]


@trap("exclusion")
def _gift_cards(rng, twin):
    """A product list of 40 items in 5 categories with a unit cost, one category (3
    items) with no cost at all, and order lines (quantity, price and a discount
    on 17% of the other lines) that never discount those 3. Twin: 3 blank costs
    spread over three categories, and discounts anywhere."""
    cats = _names(rng, 5, 3)
    odd = cats[0]
    items = [(f"{_caps(rng, 2)}-{100 + k}", _word(rng, 3), odd if k < 3 else cats[1 + k % 4]) for k in range(40)]
    blank = {s for s, _n, c in items if c == odd} if not twin else {items[3][0], items[8][0], items[13][0]}
    cost = {s: round(rng.uniform(2, 30), 2) for s, _n, _c in items}
    retail = {s: round(cost[s] * rng.uniform(1.5, 3), 2) for s, _n, _c in items}
    lst = [["SKU", "Product", "Category", "Unit Cost", "Retail"]] + [[s, n, c, None if s in blank else cost[s],
                                                                       retail[s]] for s, n, c in items]
    start, days = _start(rng), rng.randint(150, 300)
    rows = []
    for k in range(rng.randint(600, 800)):
        s, _n, c = rng.choice(items)
        q = rng.randint(1, 4)
        pr = round(retail[s] * rng.uniform(0.9, 0.99), 2)
        disc = round(pr * q * 0.1, 2) if rng.random() < 0.17 and (twin or c != odd) else 0
        rows.append({"date": _day(start, rng, days), "sku": s, "qty": q, "price": pr, "disc": disc,
                     "amount": round(q * pr - disc, 2)})
    rows.sort(key=lambda r: r["date"])
    for i, r in enumerate(rows):
        r["doc"] = f"OL-{5000 + i}"
    table = _table([("date", "Date"), ("doc", "Order ID"), ("sku", "SKU"), ("qty", "Qty"), ("price", "Unit Price"),
                    ("disc", "Discount"), ("amount", "Net Sales")], rows)
    return table, [{"columns": ["SKU"], "col": "SKU", "category": odd,
                    "rows": [r["_row"] for r in rows if r["sku"] in blank] or [2]}], [lst]


@trap("window")
def _terms_sides(rng, twin):
    """Purchases from 4 to 6 vendors and a terms tab of dates: one vendor's lines
    start before its start date (30% of its lines and 30 more), another has 30
    lines after its end, so the two need different answers, asked one after the
    other. Twin: every line inside its vendor's dates."""
    vendors = _names(rng, rng.randint(4, 6))
    b = _buys(rng, vendors)
    h = _buy_headers(rng)
    v1, v2 = vendors[0], vendors[1]
    t0, t1 = b["start"] - dt.timedelta(days=60), b["end"] + dt.timedelta(days=200)
    cut1 = b["start"] + dt.timedelta(days=int(b["days"] * 0.3))
    cut2 = b["start"] + dt.timedelta(days=int(b["days"] * 0.7))
    starts, ends = {v: t0 for v in vendors}, {v: t1 for v in vendors}
    rows = b["rows"]
    if not twin:
        starts[v1], ends[v2] = cut1, cut2
        mine = [r for r in rows if r["vendor"] == v1 and r["date"] >= cut1]
        for r in rng.sample(mine, min(30, len(mine))):
            r["date"] = b["start"] + dt.timedelta(days=rng.randint(0, (cut1 - b["start"]).days - 1))
        theirs = [r for r in rows if r["vendor"] == v2 and r["date"] <= cut2]
        for r in rng.sample(theirs, min(30, len(theirs))):
            r["date"] = cut2 + dt.timedelta(days=rng.randint(1, (b["end"] - cut2).days - 1))
    rows.sort(key=lambda r: r["date"])
    table = _table(_buy_cols(h), rows)
    terms = [[h["vendor"], "Start", "End", "Terms"]] + [[v, starts[v], ends[v], rng.choice(_TERMS_TEXT)]
                                                        for v in vendors]
    out = [r for r in rows if not starts[r["vendor"]] <= r["date"] <= ends[r["vendor"]]]
    return table, [{"columns": [h["vendor"]], "col": h["vendor"], "values": [v1, v2],
                    "before": sum(r["vendor"] == v1 for r in out), "after": sum(r["vendor"] == v2 for r in out),
                    "rows": [r["_row"] for r in out] or [2]}], [terms]


@trap("odd_group")
def _person_site(rng, twin):
    """A log with site codes and the person who logged each row: one person logs
    every row of one site, and that site is present only in the middle of the
    dates. The person and the site are one question. Twin: sites by name, and
    that person logs rows at every site."""
    b = _log(rng, n=rng.randint(360, 480), sites=5)
    h = _log_headers(rng)
    codes = _codes(rng, 5, 2)
    site_of = dict(zip(b["sites"], b["sites"] if twin else codes))
    odd = b["sites"][-1]
    who = f"{_word(rng)} {_word(rng, 3)}"
    for r in b["rows"]:
        r["site"] = site_of[rng.choice(b["sites"][:-1])]
    for r in rng.sample(b["rows"], rng.randint(40, 60)):
        r.update(site=site_of[odd], date=_day(b["start"], rng, b["days"], 0.35, 0.65))
        if not twin:
            r["person"] = who
    if twin:
        for r in rng.sample(b["rows"], rng.randint(40, 60)):
            r["person"] = who
    _number(b)
    table = _table(_log_cols(h), b["rows"])
    return table, [{"columns": [h["site"], h["person"]], "col": h["person"], "value": who, "site": site_of[odd],
                    "rows": [r["_row"] for r in b["rows"] if r["person"] == who] or [2]}]


# --------------------------------------------------------------------------
# practice round 4
# --------------------------------------------------------------------------
@trap("orphan")
def _orphan_actual(rng, twin):
    """The 24-month model of model_actuals (its receipts matching revenue) with one
    more input that no formula reads, whose value is typed on the plan tab in the
    first month, an actual. Twin: the input holds the plan's revenue in its last
    month, a forecast month, where it is a formula, never typed."""
    rows, _plants, more = _model_actuals(rng, True)
    plan, inputs = more[0]["rows"], more[1]["rows"]
    value = _val(plan[2][-1]) if twin else plan[2][1]
    inputs.append([rng.choice(["Start revenue", "Opening revenue", "First month revenue"]), value, "at the start"])
    return rows, [{"what": "orphan", "col": inputs[-1][0], "value": value, "plan": more[0]["name"],
                   "inputs": more[1]["name"], "month": plan[1][-1] if twin else plan[1][1], "rows": [3]}], more


@trap("contra")
def _contra_split(rng, twin, vendor_only=False):
    """A journal with a chart tab that gives each account a category and its
    normal side: sales debit the bank and credit an income account, costs are
    charged to a card, the card is paid from the bank. Vendors refund some costs
    (the cost account credited, the card debited) and customers are refunded
    some sales (the income account debited, the bank credited), every refund line
    with a memo 'Refund: <words>'. Twin: no refunds at all. vendor_only: the
    vendor refunds only."""
    h = _headers(rng, "entry", "date", "account", "note")
    start, days = _start(rng), rng.randint(240, 360)
    income, expense = _names(rng, 2, 3), _names(rng, rng.randint(3, 4), 3)
    bank, card, equity = _names(rng, 3, 3)
    cats = {"income": rng.choice(["Income", "Revenue", "Sales"]),
            "expense": rng.choice(["Expense", "Operating Expense", "Cost of Goods"]),
            "bank": "Bank", "card": "Credit Card", "equity": "Equity"}
    vendor_memo = rng.choice(["bad lot", "missing units", "billing error", "wrong item"])
    customer_memo = rng.choice(["order cancellation", "event cancelled", "late booking cancelled"])
    pre = _caps(rng, 2)
    entries = []
    for _ in range(rng.randint(70, 90)):
        amt = round(rng.uniform(100, 2000), 2)
        entries.append([_day(start, rng, days), [(bank, amt, None, f"{_word(rng)} sale"),
                                                  (rng.choice(income), None, amt, f"{_word(rng)} sale")]])
    for _ in range(rng.randint(70, 90)):
        amt = round(rng.uniform(30, 600), 2)
        entries.append([_day(start, rng, days), [(rng.choice(expense), amt, None, f"{_word(rng)}"),
                                                  (card, None, amt, f"{_word(rng)}")]])
    for _ in range(rng.randint(3, 4)):
        amt = round(rng.uniform(500, 3000), 2)
        entries.append([_day(start, rng, days), [(card, amt, None, "Card payment"), (bank, None, amt, "Card payment")]])
    vendor, customer = [], []
    for k in range(rng.randint(4, 6)):
        amt = round(rng.uniform(20, 300), 2)
        acct = expense[k % len(expense)]
        if twin:
            continue
        vendor.append(acct)
        entries.append([_day(start, rng, days), [(acct, None, amt, f"Refund: {vendor_memo}"),
                                                  (card, amt, None, f"Refund: {vendor_memo}")]])
    if not twin and not vendor_only:
        for _ in range(rng.randint(4, 6)):
            amt = round(rng.uniform(50, 400), 2)
            acct = rng.choice(income)
            customer.append(acct)
            entries.append([_day(start, rng, days), [(acct, amt, None, f"Refund: {customer_memo}"),
                                                      (bank, None, amt, f"Refund: {customer_memo}")]])
    entries.sort(key=lambda e: e[0])
    rows, first = [], rng.randint(100, 900)
    for k, (d, lines) in enumerate(entries):
        for acct, dr, cr, memo in lines:
            rows.append({"entry": f"{pre}{first + k}", "date": d, "acct": acct, "dr": dr, "cr": cr, "memo": memo})
    table = _table([("entry", h["entry"]), ("date", h["date"]), ("acct", h["account"]), ("dr", "Debit"),
                    ("cr", "Credit"), ("memo", h["note"])], rows)
    side = rng.choice(["Normal Side", "Usual Side"])
    kind_of = {**{a: "income" for a in income}, **{a: "expense" for a in expense}, bank: "bank", card: "card",
               equity: "equity"}
    credit_normal = {"income", "card", "equity"}
    chart = [[h["account"], "Category", side]] + [[a, cats[k], "Credit" if k in credit_normal else "Debit"]
                                                  for a, k in kind_of.items()]
    return table, [{"columns": [h["account"], h["note"]], "col": h["account"], "prefix": "Refund",
                    "vendor": sorted(set(vendor)), "customer": sorted(set(customer)),
                    "vendor_memo": vendor_memo, "customer_memo": customer_memo, "card": card, "bank": bank,
                    "rows": [r["_row"] for r in rows if str(r["memo"]).startswith("Refund:")
                             and r["acct"] not in (card, bank)] or [2]}], [chart]


@trap("contra")
def _contra_vendor(rng, twin):
    """The contra_split journal with the vendor refunds only: one group, credits on
    cost accounts. Twin: no refunds at all."""
    return _contra_split(rng, twin, vendor_only=True)


@trap("leave_out")
def _books_moves(rng, twin):
    """A journal with a chart tab that gives each account a category (income,
    expense, bank, credit card, equity) and its normal side: sales go to the bank,
    costs to a card or the bank. Planted: money moved from checking to savings,
    the card paid from checking, and the owner's draws from checking, each with
    its own memo. Twin: only sales and costs, no entry that moves money between
    balance-sheet accounts."""
    h = _headers(rng, "entry", "date", "account", "note")
    start, days = _start(rng), rng.randint(240, 360)
    income, expense = _names(rng, 2, 3), _names(rng, rng.randint(3, 4), 3)
    checking, savings, card, draws = (f"{_word(rng)} Checking", f"{_word(rng)} Savings", f"{_word(rng)} Card",
                                      rng.choice(["Owner Payouts", "Member Draws", "Partner Draws"]))
    pre = _caps(rng, 2)
    entries = []
    for _ in range(rng.randint(70, 90)):
        amt = round(rng.uniform(100, 2000), 2)
        entries.append([_day(start, rng, days), [(checking, amt, None, f"{_word(rng)} sale"),
                                                  (rng.choice(income), None, amt, f"{_word(rng)} sale")]])
    for _ in range(rng.randint(40, 55)):
        amt = round(rng.uniform(30, 600), 2)
        paid = card if rng.random() < 0.7 else checking
        entries.append([_day(start, rng, days), [(rng.choice(expense), amt, None, f"{_word(rng)}"),
                                                  (paid, None, amt, f"{_word(rng)}")]])
    moves = {}
    if not twin:
        for kind, frm, to, memo, n in (("transfer", checking, savings, "Move to reserve", rng.randint(6, 12)),
                                        ("payment", checking, card, f"{card} payment", rng.randint(6, 12)),
                                        ("draw", checking, draws, "Partner payout", rng.randint(6, 12))):
            moves[kind] = n
            for _ in range(n):
                amt = round(rng.uniform(300, 3000), 2)
                entries.append([_day(start, rng, days), [(to, amt, None, memo), (frm, None, amt, memo)]])
    entries.sort(key=lambda e: e[0])
    rows, first = [], rng.randint(100, 900)
    for k, (d, lines) in enumerate(entries):
        for acct, dr, cr, memo in lines:
            rows.append({"entry": f"{pre}{first + k}", "date": d, "acct": acct, "dr": dr, "cr": cr, "memo": memo})
    table = _table([("entry", h["entry"]), ("date", h["date"]), ("acct", h["account"]), ("dr", "Debit"),
                    ("cr", "Credit"), ("memo", h["note"])], rows)
    cats = {**{a: rng.choice(["Income", "Revenue"]) for a in income}, **{a: "Expense" for a in expense},
            checking: "Bank", savings: "Bank", card: "Credit Card", draws: "Equity"}
    credit_normal = {a for a, c in cats.items() if c in ("Income", "Revenue", "Credit Card", "Equity")}
    chart = [[h["account"], "Category", "Normal Side"]] + [[a, c, "Credit" if a in credit_normal else "Debit"]
                                                         for a, c in cats.items()]
    return table, [{"columns": [h["account"]], "col": h["account"], "checking": checking, "savings": savings,
                    "card": card, "draws": draws, "moves": moves,
                    "rows": [r["_row"] for r in rows if r["memo"] in ("Move to reserve", f"{card} payment",
                                                                        "Partner payout")] or [2]}], [chart]


@trap("codes")
def _status_abbr(rng, twin):
    """The status roster with one status written as an abbreviation (M2M) on its
    rows: the question keeps asking what it means. Twin: the roster's twin."""
    table, plants = _status_roster(rng, twin, abbr=not twin)
    return table, [dict(p, abbr=None if twin else "M2M") for p in plants]


@trap("entity")
def _sentinel_unpaid(rng, twin):
    """The sentinel pay register whose test ID was never paid: its last 5 rows
    only, each at 0. Twin: the sentinel's salaried twin."""
    return _sentinel(rng, twin, unpaid=True)

@trap("unit_price")
def _case_price_tabs(rng, twin):
    """A purchase log (category, item code, quantity, unit cost, extended value =
    quantity x unit cost) where one category's items are priced per case of 6 to
    24, and a waste tab that holds the same items by their code at the same unit
    cost, its loss = quantity x unit cost; an item list gives each code its
    category. Twin: every category priced per unit, on both tabs."""
    cats = _names(rng, rng.randint(4, 6), 3)
    odd = cats[0]
    pre = _caps(rng, 2)
    items = [f"{pre}{100 + k * 3}" for k in range(rng.randint(18, 26))]
    cat_of = {it: cats[i % len(cats)] for i, it in enumerate(items)}
    pack = rng.randint(6, 24)
    cost = {it: round(rng.uniform(2, 9) * (pack if cat_of[it] == odd and not twin else 1), 2) for it in items}
    h = {"date": rng.choice(["Received", "Date", "Bought On"]), "cat": rng.choice(["Category", "Group", "Section"]),
         "item": rng.choice(["Item Ref", "Part No", "Item No"]), "qty": rng.choice(["Qty", "Units", "Pieces"]),
         "price": rng.choice(["Unit Cost", "Cost", "Unit Price"]),
         "money": rng.choice(["Net Value", "Line Value", "Extended"])}
    start, weeks = _start(rng), rng.randint(20, 30)
    rows = []
    for _ in range(rng.randint(300, 420)):
        it = rng.choice(items)
        q = rng.randint(1, 12)
        rows.append({"date": _day(start, rng, weeks * 7), "cat": cat_of[it], "item": it, "qty": q, "price": cost[it],
                     "amount": round(q * cost[it], 2)})
    rows.sort(key=lambda r: r["date"])
    table = _table([("date", h["date"]), ("cat", h["cat"]), ("item", h["item"]), ("qty", h["qty"]),
                    ("price", h["price"]), ("amount", h["money"])], rows)
    wh = {"date": "Logged On", "qty": rng.choice(["Wasted", "Units Lost", "Qty Lost"]),
          "loss": rng.choice(["Lost Value", "Loss", "Waste Value"])}
    w_items, w_cost = items, cost
    waste = []
    for _ in range(rng.randint(160, 240)):
        it = rng.choice(w_items)
        q = rng.randint(1, 4)
        waste.append({"date": _day(start, rng, weeks * 7), "item": it, "qty": q, "price": w_cost[it],
                      "loss": round(q * w_cost[it], 2)})
    waste.sort(key=lambda r: r["date"])
    wt = _table([("date", wh["date"]), ("item", h["item"]), ("qty", wh["qty"]), ("price", h["price"]),
                 ("loss", wh["loss"])], waste)
    lst = [[h["item"], "Name", h["cat"]]] + [[it, _word(rng, 3), cat_of[it]] for it in items]
    mine = {it for it in items if cat_of[it] == odd}
    return table, [{"columns": [h["cat"], h["price"]], "col": h["cat"], "value": None if twin else odd,
                    "price": h["price"], "pack": pack, "money": h["money"], "loss": wh["loss"],
                    "waste_rows": 0 if twin else sum(1 for r in waste if r["item"] in mine),
                    "rows": [] if twin else [r["_row"] for r in rows if r["cat"] == odd]}], [wt, lst]


def build_files(folder, seed: int, name: str, twin: bool = False) -> dict:
    """The trap's book with each tab written as its own file (a lookup kept in a
    second file): the manifest of build() plus 'paths', one per tab."""
    import openpyxl
    m = build(os.path.join(str(folder), f"{name}{seed}{int(twin)}_all.xlsx"), seed, name, twin=twin)
    wb = openpyxl.load_workbook(m["path"], data_only=False)
    paths = []
    try:
        for k, ws in enumerate(wb.worksheets):
            rows = [[c for c in r] for r in ws.iter_rows(values_only=True)]
            p = os.path.join(str(folder), f"{name}{seed}{int(twin)}_{k}.xlsx")
            (_write_xlsxwriter if m["writer"] == "xlsxwriter" else _write_openpyxl)(p, [{"name": ws.title,
                                                                                           "rows": rows}])
            paths.append(p)
    finally:
        wb.close()
    return dict(m, paths=paths)
