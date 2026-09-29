"""Detectors for one date where a file changes and for rows that are there
twice. A boundary is a date where several columns change form, labels or sign,
or a number column is written another way; a handoff is a new value taking over
from an old one. Copies are rows loaded twice under two numbering families, an
upload is a block of a panel loaded twice at two times, and twin pairs are
numbers there twice under two codes with the same amounts. Odd groups are a
location, channel or category whose rows behave unlike the others; measure
blanks, blank slices and a derived measure say what a blank or a missing
amount column means; entity outliers are single records unlike their peers.
Every detector fires on its plant on 19 of 20 seeds or more, stays silent on
its null twin and asks nothing on the noise books. Every book here is synthetic
(tests/synth.py) or built in the test."""
import datetime as dt
import math
import os
import random
import re
import sys

import pytest

openpyxl = pytest.importorskip("openpyxl")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "skills", "sheet-geek", "scripts"))
import synth  # noqa: E402
from conftest import SEEDS  # noqa: E402
from sheetbrain import findings, interview  # noqa: E402
from sheetbrain.analyze import Analysis  # noqa: E402
from sheetbrain.profile import norm_key  # noqa: E402
from test_faithful import LINTED, _bundled  # noqa: E402

NEEDED = math.ceil(0.95 * len(SEEDS))
NEW = ("find_boundary_", "find_handoff_", "follow_handoff_", "find_copies_", "find_upload_", "find_pairs_")
RECIPES = ("boundary:", "handoff:", "copies:", "nearkey:", "pairs:")


def _built(tmp_path, seed, name, twin=False):
    m = synth.build(tmp_path / f"{name}{seed}{int(twin)}.xlsx", seed, name, twin=twin)
    return m, Analysis([m["path"]])


def _found(a, prefix):
    return [i for i in a.insights if i.get("recipe", "").startswith(prefix)]


def _asks(a, answers=None):
    return [q for q in interview.candidates(a, answers or {}) if q.id.startswith(NEW)]


def _q(a, prefix, answers=None):
    return next((q for q in interview.candidates(a, answers or {}) if q.id.startswith(prefix)), None)


def _ans(q, raw):
    return interview.parse_answers([q], raw)[q.id]


def _contract(a, q):
    """The option contract: at most 3 real options plus Not sure, one claim per
    label, a counted number in the prompt, meta.about naming a table and column
    that exist, and curated notes that use only words the owner saw."""
    shown = interview._options_for(q)
    assert shown[-1]["id"] == "not_sure" and len(shown) - 1 <= 3, q.id
    if q.kind in LINTED:
        assert not [o["label"] for o in q.options if _bundled(o["label"])], q.id
    ab = q.meta["about"]
    t = next(t for t in a.tables if t.tid == ab["table"])
    assert ab["col"] in t.headers and re.search(r"\d", q.prompt), q.id
    env = interview.Env(a, {})
    for oid, tpl in (q.fact.get("statements") or {}).items():
        o = next(o for o in q.options if o["id"] == oid)
        stmt = interview.fill(tpl, env, interview._answer(q, [oid], ""))
        assert not interview.unseen_words(stmt, q.prompt, o["label"], interview._shown_desc(q, o)), (q.id, oid)


# --------------------------------------------------------------------------
# rank 14: one date where the file changes, and the handoffs at it
# --------------------------------------------------------------------------
def _boundary_ok(tmp_path, seed) -> bool:
    """One boundary at the split date listing every switching column, the
    adjustment flagged, the site change shown as a pair the file really has (a
    handoff), and one question whose prompt lists at most 3 changes and names
    the other columns."""
    m, a = _built(tmp_path, seed, "system_change")
    p = m["plants"][0]
    found = _found(a, "boundary:")
    if len(found) != 1:
        return False
    n = found[0]["numbers"]
    split = dt.date.fromisoformat(p["date"][:10])
    cols = {c["col"] for c in n["changes"]}
    adjust, site = p["columns"][4], p["columns"][3]
    ok = 0 <= (dt.date.fromisoformat(n["date"]) - split).days <= 7 and cols == set(p["columns"])
    ok = ok and any(c["col"] == adjust and c["kind"] == "number" for c in n["changes"])   # whole percents to dollars
    pairs = {f"\"{o}\" before, \"{nw}\" after" for o, nw in p["handoffs"]}
    ok = ok and all(c["text"] in pairs for c in n["changes"] if c["col"] == site)
    qs = [q for q in _asks(a) if q.id.startswith("find_boundary_")]
    listed = qs[0].prompt.split("): ", 1)[-1].split(". What happened")[0] if len(qs) == 1 else ""
    return ok and len(qs) == 1 and all(h in qs[0].prompt for h in p["columns"]) \
        and len(listed.split("; ")) <= 4 and listed.endswith("more columns (" + listed.split("(")[-1])


def test_one_boundary_at_the_split_date_lists_every_switching_column(tmp_path):
    hits = sum(_boundary_ok(tmp_path, seed) for seed in SEEDS)
    assert hits >= NEEDED, hits


def test_the_boundary_holds_on_seeds_it_was_never_tuned_on(tmp_path):
    """Seeds 20 to 39: sparse logs of about one row a day, where trimming both
    ends of each value's dates opened a gap of its own and hid the switch."""
    fresh = range(20, 40)
    hits = sum(_boundary_ok(tmp_path, seed) for seed in fresh)
    assert hits >= math.ceil(0.95 * len(fresh)), hits
    for seed in list(fresh)[:6]:
        _m, a = _built(tmp_path, seed, "system_change", twin=True)
        assert not [i["recipe"] for i in a.insights if i["recipe"].startswith(RECIPES)], seed


def test_a_label_change_with_no_pair_is_counted_not_paired(tmp_path):
    """Sites that stop and codes that start with no shared staff: no handoff, so
    the change says how many values stop and start, never an old and new pair
    the file does not support."""
    rng = random.Random(4)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Log"
    ws.append(["Date", "Ref", "Outlet", "Handler", "Amount"])
    start, split = dt.datetime(2024, 1, 1), dt.datetime(2024, 6, 1)
    olds, news = ["Kalo", "Venmi", "Torsil"], ["QX", "ZR", "PW"]
    for i in range(360):
        d = start + dt.timedelta(days=i * 330 // 360)
        after = d >= split
        ws.append([d.strftime("%m/%d/%Y") if not after else d, f"{'MB' if after else 'Q'}-{1000 + i}",
                   rng.choice(news if after else olds), f"H{rng.randrange(6) + (6 if after else 0)}",
                   round(rng.uniform(10, 400), 2)])
    path = tmp_path / "unpaired.xlsx"
    wb.save(path)
    a = Analysis([str(path)])
    b = _found(a, "boundary:")
    assert len(b) == 1 and not a.handoffs.get((a.tables[0].tid, "Outlet"))
    text = next(c["text"] for c in b[0]["numbers"]["changes"] if c["col"] == "Outlet")
    # round 3 fix 1: the values that stop and those that start are named (up to 3 each), never paired
    assert all(f'"{v}"' in text for v in olds + news) and " stop while " in text and "->" not in text \
        and " before, \"" not in text, text


def test_renamed_sites_pair_through_shared_people_and_a_new_site_does_not(tmp_path):
    hits = 0
    for seed in SEEDS:
        m, a = _built(tmp_path, seed, "system_change")
        p = m["plants"][0]
        site = p["columns"][3]
        ho = a.handoffs.get((a.tables[0].tid, site)) or {}
        pairs = sorted((x["old"], x["new"]) for x in ho.get("pairs", []))
        hits += pairs == sorted(map(tuple, p["handoffs"])) and ho.get("via") == p["via"] \
            and p["new_value"] not in {x["new"] for x in ho["pairs"]}
    assert hits >= NEEDED, hits


def test_the_twins_have_no_boundary(tmp_path):
    """One format throughout, and an item sold only mid-year beside the others."""
    for seed in SEEDS:
        _m, a = _built(tmp_path, seed, "system_change", twin=True)
        assert not [i["recipe"] for i in a.insights if i["recipe"].startswith(RECIPES)], seed
        assert not _asks(a), seed


def test_a_number_column_that_changes_where_the_title_ends_is_a_boundary(tmp_path):
    """The end of the period a title names is a candidate date: an adjustment
    written as whole percents up to it and as dollars after it is one boundary
    there, though no other column changes. The same rows with the title taken
    off have no date to read, and the twin (whole percents throughout) has no
    boundary."""
    hits = 0
    for seed in SEEDS:
        m, a = _built(tmp_path, seed, "title_switch")
        p = m["plants"][0]
        found = _found(a, "boundary:")
        qs = [q for q in _asks(a) if q.id.startswith("find_boundary_")]
        hits += len(found) == 1 and found[0]["numbers"]["date"] == p["date"][:10] \
            and [(c["col"], c["kind"]) for c in found[0]["numbers"]["changes"]] == [(p["columns"][0], "number")] \
            and len(qs) == 1
        _tw, b = _built(tmp_path, seed, "title_switch", twin=True)
        assert b.title_dates and not _found(b, RECIPES) and not _asks(b), seed
    assert hits >= NEEDED, hits
    for seed in SEEDS[:5]:
        m = synth.build(tmp_path / f"notitle{seed}.xlsx", seed, "title_switch")
        rows = dict(synth.cells(m["path"]))[m["plants"][0]["sheet"]]
        path = str(tmp_path / f"notitle{seed}b.xlsx")
        synth._write_xlsxwriter(path, [{"name": "Log", "rows": rows[2:]}])
        a = Analysis([path])
        assert not a.title_dates and not _found(a, "boundary:"), seed


def test_the_site_column_is_one_dossier_that_carries_the_handoff_date(tmp_path):
    hits = 0
    for seed in SEEDS:
        m, a = _built(tmp_path, seed, "system_change")
        p = m["plants"][0]
        site = p["columns"][3]
        tid = a.tables[0].tid
        on_site = [q for q in interview.candidates(a, {}) if (q.meta.get("about") or {}).get("col") == site
                   and q.meta["about"].get("table") == tid]
        ho = a.handoffs.get((tid, site)) or {}
        ok = len(on_site) == 1 and on_site[0].id.startswith("codes_") and bool(ho) \
            and f"From {ho['when']} on" in on_site[0].prompt \
            and all(f"{x['old']} -> {x['new']}" in on_site[0].prompt for x in ho["pairs"][:findings.DOSSIER_PAIRS]) \
            and on_site[0].prompt.count(" -> ") == min(len(ho["pairs"]), findings.DOSSIER_PAIRS) \
            and ("handoff" in on_site[0].meta["rules"]) == (len(ho["pairs"]) <= findings.DOSSIER_PAIRS
                                                           and "all_count" not in [o["id"] for o in on_site[0].options])
        if ok:
            _contract(a, on_site[0])
        hits += ok
    assert hits >= NEEDED, hits


def test_same_names_become_one_value_only_when_the_owner_says_so(tmp_path):
    """The handoff map is offered in the column's dossier when it fits there, else
    asked after the boundary question; ticking it makes each old name its new one
    in every count, and not ticking it changes nothing."""
    hits = 0
    for seed in SEEDS:
        m, a = _built(tmp_path, seed, "system_change")
        p = m["plants"][0]
        site = p["columns"][3]
        t = a.tables[0]
        j = t.headers.index(site)
        olds = {norm_key(o) for o, _n in p["handoffs"]}
        before = {norm_key(r[j]) for r in a._ctx.rows(t)}
        dossier = next((q for q in findings.code_dossiers(a, {}) if q.meta["about"]["col"] == site), None)
        if dossier is not None and "handoff" in (dossier.meta.get("rules") or {}):
            answers = {dossier.id: interview._answer(dossier, ["handoff"], "")}
        else:
            b = _q(a, "find_boundary_")
            answers = {b.id: _ans(b, "a")}
            f = _q(a, "follow_handoff_", answers)
            if f is None:
                continue
            _contract(a, f)
            a.apply_answers(answers)
            assert olds <= {norm_key(r[j]) for r in a._ctx.rows(t)}, seed   # nothing mapped before the pick
            answers[f.id] = _ans(f, "a")
            assert answers[f.id]["options"] == ["all"], seed
        a.apply_answers(answers)
        after = {norm_key(r[j]) for r in a._ctx.rows(t)}
        hits += olds <= before and not (olds & after)
    assert hits >= NEEDED, hits


def test_a_sign_flip_alone_is_a_boundary_with_its_sign_view(tmp_path):
    rng = random.Random(7)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Book"
    ws.append(["Date", "Ref", "Party", "Amount"])
    start = dt.datetime(2024, 1, 1)
    for i in range(240):
        d = start + dt.timedelta(days=i * 300 // 240)
        amt = round(rng.uniform(10, 500), 2)
        ws.append([d, f"Q-{1000 + i}", rng.choice(["Kalo", "Venmi", "Torsil", "Pamar"]),
                   amt if d < dt.datetime(2024, 6, 1) else -amt])
    path = tmp_path / "flip.xlsx"
    wb.save(path)
    a = Analysis([str(path)])
    b = _found(a, "boundary:")
    assert len(b) == 1 and b[0]["numbers"]["date"] == "2024-06-01"
    assert [c["kind"] for c in b[0]["numbers"]["changes"]] == ["sign"]
    assert _found(a, "signflip:")[0]["numbers"]["col"] == "Amount"
    q = _q(a, "find_boundary_")
    assert "Amount: positive before, negative after" in q.prompt and "sign_rule" in q.meta["covers"]
    _contract(a, q)


def _alias_book(path):
    """A main tab, and a second tab whose Region column holds two real alias
    pairs and pairs that are two things (another word, another number, another
    one-letter word as in 'Kalonor Yard A' and 'Kalonor Yard B')."""
    rng = random.Random(3)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Lines"
    ws.append(["Date", "Doc No", "Item", "Amount"])
    for i in range(120):
        ws.append([dt.datetime(2024, 1, 1) + dt.timedelta(days=i), f"L-{i + 1}", rng.choice(["Ka", "Lo", "Mi"]),
                   round(rng.uniform(5, 90), 2)])
    ws2 = wb.create_sheet("Stores")
    ws2.append(["Region", "Opened", "Sales"])
    regions = ["Tormi Foods"] * 14 + ["Tormi Foods Co"] * 12 + ["Pelkadun Dairy"] * 6 + ["Pelkadun Diary"] * 5 \
        + ["Kalonorwest"] * 9 + ["Kalonoreast"] * 9 + ["Depot 12"] * 8 + ["Depot 13"] * 8 \
        + ["Kalonor Yard A"] * 7 + ["Kalonor Yard B"] * 7
    for k, r in enumerate(regions):
        ws2.append([r, dt.datetime(2020, 1, 1) + dt.timedelta(days=k), round(rng.uniform(100, 900), 2)])
    wb.save(path)
    return str(path)


def test_whole_word_pairs_are_never_aliases_and_a_second_real_pair_is_still_asked(tmp_path):
    a = Analysis([_alias_book(tmp_path / "alias.xlsx")])
    pairs = {(p["a"], p["b"]) for p in interview.alias_pairs(a)}
    assert pairs == {("Tormi Foods", "Tormi Foods Co"), ("Pelkadun Dairy", "Pelkadun Diary")}
    first = interview._alias_questions(a, {})
    assert len(first) == 1 and "Tormi Foods Co" in first[0].prompt
    answers = {first[0].id: interview._answer(first[0], ["same"], "")}
    second = interview._alias_questions(a, answers)
    assert len(second) == 1 and "Pelkadun Diary" in second[0].prompt


def test_a_slip_is_inside_a_word_never_a_whole_word():
    slip = interview._spelling_slip
    assert not slip("warehouse a", "warehouse b") and not slip("location a", "location b")
    assert not slip("store x west", "store y west") and not slip("a block", "b block")
    assert slip("sunrise dairy", "sunrise diary") and slip("grey stone", "gray stone") and slip("web", "website")


# --------------------------------------------------------------------------
# rank 15: copies, uploads loaded twice, twin pairs, and ID repeats a key explains
# --------------------------------------------------------------------------
def test_a_window_re_imported_under_new_numbers_is_one_copies_question(tmp_path):
    hits = 0
    for seed in SEEDS:
        m, a = _built(tmp_path, seed, "reimport")
        p = m["plants"][0]
        qs = [q for q in _asks(a) if q.id.startswith("find_copies_")]
        if len(qs) != 1:
            continue
        n = qs[0].meta["finding"]["numbers"]
        lo, hi = p["window"][0][:10], p["window"][1][:10]
        hits += n["rows"] == p["copies"] and lo <= n["window"][0] <= n["window"][1] <= hi \
            and qs[0].prompt.startswith(f"{p['copies']:,} rows dated") and n["example"][1] == p["example"][1]
    assert hits >= NEEDED, hits


def test_split_payments_and_repeat_orders_in_one_family_ask_nothing(tmp_path):
    """The twin also holds orders entered twice (the same date, client and
    amount) inside one family: equal rows in one family are never copies."""
    for seed in SEEDS:
        _m, a = _built(tmp_path, seed, "reimport", twin=True)
        assert not _found(a, "copies:") and not _asks(a), seed


def test_keeping_one_family_leaves_the_other_copies_out_and_both_changes_nothing(tmp_path, synth_seed):
    m, a = _built(tmp_path, synth_seed, "reimport")
    q = _q(a, "find_copies_")
    if q is None:
        pytest.skip("no copies found on this seed")
    _contract(a, q)
    t = a.tables[0]
    n = len(t.rows)
    a.apply_answers({q.id: _ans(q, "c")})
    assert len(a._ctx.rows(t)) == n                     # both are real: nothing is left out
    a.apply_answers({q.id: _ans(q, "a")})
    j = t.headers.index(q.meta["finding"]["numbers"]["col"])
    kept = a._ctx.rows(t)
    assert len(kept) == n - m["plants"][0]["copies"]
    assert not {norm_key(x) for x in q.meta["finding"]["numbers"]["filters"][1]["ids"]} \
        & {norm_key(r[j]) for r in kept}


_CLIENTS = ["Kalo", "Venmi", "Torsil", "Pamar", "Quiess", "Rabar", "Lozen", "Oskdun", "Tasmi", "Delnor", "Brihal",
            "Wyess", "Camlin", "Pelru", "Truvo", "Gorfen", "Ulmar", "Zenpa", "Silka", "Norbar", "Fendel", "Marqui"]


def _save(path, head, rows, title="Book"):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = title
    ws.append(head)
    for r in rows:
        ws.append(r)
    wb.save(path)
    return str(path)


def _invoices_and_payments(path, typed=True):
    """A register of invoices, some paid the same day for the same amount: an
    INV- document and a PMT- document for one sale. typed: a Type column says
    which is which."""
    rng = random.Random(5)
    rows = []
    for k in range(160):
        d = dt.datetime(2024, 1, 1) + dt.timedelta(days=k * 300 // 160)
        c, amt = rng.choice(_CLIENTS), round(rng.uniform(20, 900), 2)
        rows.append([d, f"INV-{3000 + k}", "Invoice", c, amt])
        if rng.random() < 0.4:
            rows.append([d, f"PMT-{7000 + k}", "Payment", c, amt])
    if typed:
        return _save(path, ["Date", "Doc No", "Type", "Customer", "Amount"], rows)
    return _save(path, ["Date", "Doc No", "Customer", "Amount"], [r[:2] + r[3:] for r in rows])


def test_an_invoice_and_its_same_day_payment_are_two_documents_not_a_copy(tmp_path):
    a = Analysis([_invoices_and_payments(tmp_path / "typed.xlsx")])
    assert not _found(a, "copies:") and not _asks(a)
    # the same register with nothing telling the two kinds apart is asked, so the Type column is what settles it
    b = Analysis([_invoices_and_payments(tmp_path / "untyped.xlsx", typed=False)])
    assert _found(b, "copies:")


def _orders_again(path, repeats=0):
    """Timestamped orders (the only date column) with a window loaded again
    under a WX number family. repeats: orders in the window entered twice in the
    first family too (the same time, customer and amount), each copied again."""
    rng = random.Random(9)
    rows = []
    for k in range(200):
        d = dt.datetime(2024, 3, 1) + dt.timedelta(days=k, hours=rng.randint(8, 20), minutes=rng.randint(0, 59))
        rows.append([f"OR-{5000 + k}", d, rng.choice(_CLIENTS), rng.randint(1, 5), round(rng.uniform(15, 500), 2)])
    window = [r for r in rows if dt.datetime(2024, 5, 1) <= r[1] < dt.datetime(2024, 5, 20)]
    for r in window[:repeats]:
        rows.insert(rows.index(r) + 1, [f"OR-{9000 + rows.index(r)}"] + r[1:])
    out, copies = [], 0
    for r in rows:
        out.append(r)
        if dt.datetime(2024, 5, 1) <= r[1] < dt.datetime(2024, 5, 20):
            copies += 1
            out.append([f"WX{copies:05d}"] + r[1:])
    return _save(path, ["Order ID", "Order Time", "Customer", "Qty", "Total"], out), copies


def test_timestamped_orders_loaded_again_are_one_copies_question(tmp_path):
    """The only date carries a time of day, so it reads as an entry stamp; rows
    still match on its day."""
    path, copies = _orders_again(tmp_path / "stamped.xlsx")
    a = Analysis([path])
    q = _q(a, "find_copies_")
    assert q is not None and q.meta["finding"]["numbers"]["rows"] == copies == 19
    _contract(a, q)
    t = a.tables[0]
    a.apply_answers({q.id: _ans(q, "a")})
    assert len(a._ctx.rows(t)) == len(t.rows) - copies


def test_an_order_entered_twice_and_copied_twice_is_two_copies(tmp_path):
    """Two equal rows in one family, both loaded again: every copy is counted,
    and each option says how many rows its rule leaves out."""
    path, copies = _orders_again(tmp_path / "repeats.xlsx", repeats=3)
    a = Analysis([path])
    q = _q(a, "find_copies_")
    n = q.meta["finding"]["numbers"]
    assert n["rows"] == copies == 22 and [f["rows"] for f in n["filters"]] == [22, 22]
    assert all(o["desc"].startswith("The 22 ") and "left out of every count and total" in o["desc"]
               for o in q.options[:2])
    t = a.tables[0]
    for pick in ("a", "b"):
        a.apply_answers({q.id: _ans(q, pick)})
        assert len(a._ctx.rows(t)) == len(t.rows) - 22, pick


def test_a_block_loaded_twice_names_both_upload_times(tmp_path):
    hits = 0
    for seed in SEEDS:
        m, a = _built(tmp_path, seed, "double_upload")
        p = m["plants"][0]
        qs = [q for q in _asks(a) if q.id.startswith("find_upload_")]
        if len(qs) != 1:
            continue
        n = qs[0].meta["finding"]["numbers"]
        times = [x[:16] for x in p["times"]]
        hits += [x[:16] for x in n["times"]] == times and n["size"] == 2 * p["block_rows"] \
            and all(w in qs[0].prompt for w in n["shown"]) and p["site"] in qs[0].prompt
    assert hits >= NEEDED, hits


def test_scattered_same_day_repeats_in_an_event_log_ask_nothing(tmp_path):
    for seed in SEEDS:
        _m, a = _built(tmp_path, seed, "double_upload", twin=True)
        assert not _found(a, "nearkey:") and not _asks(a), seed


def test_keeping_the_later_upload_leaves_the_earlier_block_out(tmp_path, synth_seed):
    m, a = _built(tmp_path, synth_seed, "double_upload")
    q = _q(a, "find_upload_")
    if q is None:
        pytest.skip("no upload block found on this seed")
    _contract(a, q)
    t = a.tables[0]
    n = len(t.rows)
    a.apply_answers({q.id: _ans(q, "c")})
    assert len(a._ctx.rows(t)) == n
    a.apply_answers({q.id: _ans(q, "a")})
    assert len(a._ctx.rows(t)) == n - m["plants"][0]["block_rows"]


def _panel(path, stamp):
    """A weekly count panel (week x site x item) where one site's block of week 5
    was loaded twice. stamp(week, site, again) gives the upload column's value."""
    rng = random.Random(6)
    start, sites, items = dt.datetime(2024, 1, 1), ["Kalo", "Venmi", "Torsil"], _CLIENTS[:12]
    rows = []
    for w in range(10):
        wk = start + dt.timedelta(weeks=w)
        for s in sites:
            for it in items:
                q = rng.randint(0, 40)
                rows.append([wk, s, it, q, round(q * 3.5, 2), stamp(w, s, False)])
    block = [r for r in rows if r[0] == start + dt.timedelta(weeks=4) and r[1] == "Venmi"]
    k = rows.index(block[-1]) + 1
    rows[k:k] = [r[:5] + [stamp(4, "Venmi", True)] for r in block]
    return _save(path, ["Week", "Site", "Item", "Count", "Value", "Uploaded At"], rows), block


def test_text_upload_stamps_are_named_not_ordered_and_the_pick_applies(tmp_path):
    """'batch 11-Ve' sorts before 'batch 5-Ve' as text, so text stamps are never
    called later or earlier: each option names the stamp it keeps, and its rule
    matches the text."""
    path, block = _panel(tmp_path / "text.xlsx", lambda w, s, again: f"batch {11 if again else w + 1}-{s[:2]}")
    a = Analysis([path])
    q = _q(a, "find_upload_")
    assert q is not None and not [o for o in q.options if re.search(r"later|earlier", o["label"])]
    _contract(a, q)
    t = a.tables[0]
    n = len(t.rows)
    for pick in ("a", "b"):
        o = q.options["ab".index(pick)]
        a.apply_answers({q.id: _ans(q, pick)})
        kept = a._ctx.rows(t)
        assert len(kept) == n - len(block), pick
        assert o["label"].split("Keep the ")[1].split(" rows")[0] in {r[5] for r in kept}, pick


def test_the_upload_stake_is_the_block_not_every_row_of_that_upload(tmp_path):
    """One upload a week for every site: the stamp the block's first copy shares
    with the other sites is not what the answer moves."""
    path, block = _panel(tmp_path / "weekly.xlsx", lambda w, s, again: dt.datetime(2024, 1, 3, 9 + 3 * again)
                         + dt.timedelta(weeks=w))
    a = Analysis([path])
    ins = _found(a, "nearkey:")
    assert len(ins) == 1
    t = a.tables[0]
    total = sum(r[4] for r in t.rows)
    assert findings._finding_stake(a, ins[0]) == pytest.approx(sum(r[4] for r in block) / total)


def test_twin_pairs_are_one_question_with_their_count(tmp_path):
    hits = 0
    for seed in SEEDS:
        m, a = _built(tmp_path, seed, "void_pairs")
        p = m["plants"][0]
        qs = _asks(a)
        on_code = [q for q in interview.candidates(a, {}) if (q.meta.get("about") or {}).get("col") == p["col"]
                   and q.source != "playbook"]            # no dossier beside the pairs question
        hits += len(qs) == 1 and qs[0].id.startswith("find_pairs_") and len(on_code) == 1 \
            and qs[0].prompt.startswith(f"{p['pairs']:,} ") and not [q for q in interview.candidates(a, {})
                                                                   if q.id.startswith("find_dupes_")]
    assert hits >= NEEDED, hits


def test_cancels_both_removes_each_pair_from_counted_totals(tmp_path, synth_seed):
    m, a = _built(tmp_path, synth_seed, "void_pairs")
    q = _q(a, "find_pairs_")
    if q is None:
        pytest.skip("no twin pairs found on this seed")
    _contract(a, q)
    t = a.tables[0]
    n = len(t.rows)
    a.apply_answers({q.id: _ans(q, "c")})
    assert len(a._ctx.rows(t)) == n
    a.apply_answers({q.id: _ans(q, "a")})
    assert len(a._ctx.rows(t)) == n - 2 * m["plants"][0]["pairs"]
    # a twin with its row's sign: undoing it takes both rows out of the sums too
    a.apply_answers({q.id: _ans(q, "b")})
    assert len(a._ctx.rows(t)) == n - 2 * m["plants"][0]["pairs"] and a.rules and not a.unapplied_rules


def test_a_twin_with_the_other_sign_already_nets_and_reverses_leaves_nothing_out(tmp_path):
    rng = random.Random(8)
    rows = [[4000 + k, dt.datetime(2024, 2, 1) + dt.timedelta(days=k), rng.choice(_CLIENTS), "R",
             round(rng.uniform(20, 900), 2)] for k in range(200)]
    for r in rng.sample(rows[:], 8):
        rows.insert(rows.index(r) + 1, [r[0], r[1], r[2], "V", -r[4]])
    a = Analysis([_save(tmp_path / "netted.xlsx", ["Check No", "Date", "Payee", "Kind", "Amount"], rows)])
    q = _q(a, "find_pairs_")
    assert q is not None and "reverses" not in q.meta["rules"]
    assert "net to zero" in next(o["desc"] for o in q.options if o["id"] == "reverses")
    _contract(a, q)
    t = a.tables[0]
    a.apply_answers({q.id: _ans(q, "b")})
    assert len(a._ctx.rows(t)) == len(t.rows)
    assert sum(r[4] for r in a._ctx.rows(t)) == pytest.approx(sum(r[4] for r in t.rows))


def test_a_register_with_every_number_once_asks_nothing(tmp_path):
    """The twin also reuses a few numbers for other payments (another payee and
    date, or another amount): not a void and its twin. A number really used twice
    may still get the v0.1 duplicates question (find_dupes_), and nothing else."""
    for seed in SEEDS:
        _m, a = _built(tmp_path, seed, "void_pairs", twin=True)
        assert not _found(a, "pairs:") and not _asks(a), seed
        others = [q.id for q in interview.candidates(a, {}) if q.source == "finding"]
        assert all(i.startswith("find_dupes_") for i in others), (seed, others)


def _orders(path, lines=True):
    """Sales lines. lines: several lines per order (an order ID many rows share);
    else one row per order, with a few rows entered twice."""
    rng = random.Random(11)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Orders"
    ws.append(["Order ID", "Order Date", "Customer", "Product", "Qty", "Unit Price", "Line Total"])
    rows = []
    for k in range(160):
        d = dt.datetime(2024, 1, 1) + dt.timedelta(days=k)
        for _ in range(rng.randint(2, 4) if lines else 1):
            q, pr = rng.randint(1, 5), round(rng.uniform(4, 40), 2)
            rows.append([f"S-{5000 + k}", d, rng.choice(["Kalo", "Venmi", "Torsil", "Pamar", "Quiess"]),
                         rng.choice(["Rabar", "Lozen", "Oskdun", "Tasmi"]), q, pr, round(q * pr, 2)])
    if not lines:
        rows += [list(r) for r in rng.sample(rows, 6)]
    for r in rows:
        ws.append(r)
    wb.save(path)
    return str(path)


def test_a_multi_line_order_table_asks_nothing_about_its_order_id(tmp_path):
    a = Analysis([_orders(tmp_path / "lines.xlsx")])
    assert a.detection["roles"].get("order_id", {}).get("header") == "Order ID"
    assert not _found(a, "duplicates:")
    assert not [q for q in interview.candidates(a, {}) if q.id.startswith("find_dupes_")]
    # one row per order with a few entered twice: the key repeats, and that is asked
    b = Analysis([_orders(tmp_path / "once.xlsx", lines=False)])
    q = _q(b, "find_dupes_")
    assert q is not None and not [o["label"] for o in q.options if _bundled(o["label"])]
    # 'Different things' retires the finding; the rows all stay
    t = b.tables[0]
    n = len(t.rows)
    b.apply_answers({q.id: _ans(q, "c")})
    assert not _found(b, "duplicates:") and len(b._ctx.rows(t)) == n


def test_keeping_the_first_or_last_of_each_counts_every_id_once(tmp_path):
    """Both keep options carry a rule: after the pick every counted total holds
    each order once, and the brain says the rule was applied."""
    from sheetbrain import brain
    b = Analysis([_orders(tmp_path / "once.xlsx", lines=False)])
    q = _q(b, "find_dupes_")
    t = b.tables[0]
    j = t.headers.index("Order ID")
    for pick, keep in (("a", "first"), ("b", "last")):
        answers = {q.id: _ans(q, pick)}
        b.apply_answers(answers)
        kept = b._ctx.rows(t)
        assert len(kept) == len(t.rows) - 6 and len({r[j] for r in kept}) == len(kept), pick
        assert [r.values for r in b.rules] == [{"col": "Order ID", "keep": keep}] and not b.unapplied_rules
        text = " ".join(r.get("statement", "") for r in brain.Composer(b, b.paths[0], "b1", answers).compose())
        assert "each Order ID on Orders is counted once" in text, pick


def test_an_id_a_two_column_key_explains_asks_nothing(tmp_path):
    """Orders of one or two lines: Order ID repeats on under 10% of rows, and
    Order ID with Line is unique, so the key explains the repeats."""
    rng = random.Random(12)
    rows = []
    for k in range(200):
        d, c = dt.datetime(2024, 1, 1) + dt.timedelta(days=k), rng.choice(_CLIENTS)
        for line, item in enumerate(rng.sample(_CLIENTS[:6], 2 if rng.random() < 0.08 else 1), 1):
            rows.append([f"S-{6000 + k}", line, d, c, item, rng.randint(1, 5), round(rng.uniform(4, 40), 2)])
    a = Analysis([_save(tmp_path / "key.xlsx", ["Order ID", "Line", "Order Date", "Customer", "Product", "Qty",
                                                "Line Total"], rows)])
    col = a.col(a.tables[0].tid, "Order ID")
    assert col.distinct / col.count >= 0.9 and "Order ID" in a.keys[a.tables[0].tid] \
        and len(a.keys[a.tables[0].tid]) == 2
    assert not _found(a, "duplicates:") and not [q for q in interview.candidates(a, {})
                                                 if q.id.startswith(("find_dupes_",) + NEW)]


def _reversals(path, undo=True):
    """Card charges, a few reversed the same day under the same transaction ID.
    undo: the reversal carries the other sign, so each repeated ID nets to zero;
    else the charge is simply there twice."""
    rng = random.Random(13)
    rows = []
    for k in range(220):
        r = [f"T-{8000 + k}", dt.datetime(2024, 1, 1) + dt.timedelta(days=k), rng.choice(_CLIENTS),
             round(rng.uniform(20, 900), 2)]
        rows.append(r)
        if rng.random() < 0.06:
            rows.append(r[:3] + [-r[3] if undo else r[3]])
    return _save(path, ["Transaction ID", "Date", "Customer", "Amount"], rows)


def test_an_id_whose_repeats_net_to_zero_asks_nothing(tmp_path):
    a = Analysis([_reversals(tmp_path / "undo.xlsx")])
    col = a.col(a.tables[0].tid, "Transaction ID")
    assert col.distinct / col.count >= 0.9 and not a.keys[a.tables[0].tid]      # neither the share nor a key
    assert not _found(a, "duplicates:") and not [q for q in interview.candidates(a, {})
                                                 if q.id.startswith(("find_dupes_",) + NEW)]
    # the same charges there twice with one sign do not balance, and are asked
    b = Analysis([_reversals(tmp_path / "twice.xlsx", undo=False)])
    assert _q(b, "find_dupes_") is not None


# --------------------------------------------------------------------------
# every new question keeps the contract; the noise books get none
# --------------------------------------------------------------------------
def test_every_new_question_keeps_the_option_contract(tmp_path, synth_seed):
    for name in ("system_change", "reimport", "double_upload", "void_pairs"):
        _m, a = _built(tmp_path, synth_seed, name)
        qs = _asks(a)
        b = next((q for q in qs if q.id.startswith("find_boundary_")), None)
        if b is not None:
            qs += [q for q in findings.follow_ups(a, {b.id: _ans(b, "a")}) if q.id.startswith(NEW)]
        for q in qs:
            _contract(a, q)


def test_no_other_plant_and_no_null_twin_gets_a_new_question(tmp_path):
    """Odd groups, stacked exports, entities, journals: none of them is a date
    where the file changes or a row there twice. Each detector's own twin is
    checked on every seed above; this sweep over every trap takes the first 5."""
    # the balanced-entries journal writes its credits as negatives from one date on: a real sign boundary
    own = {"system_change": ("boundary:", "handoff:"), "reimport": ("copies:",), "double_upload": ("nearkey:",),
           "void_pairs": ("pairs:",), "balanced_entries": ("boundary:",), "title_switch": ("boundary:",),
           # practice round 1: a second system that takes over (and loads a few days twice), and renames
           "reimport_lines": ("copies:", "boundary:", "handoff:"), "case_rename": ("boundary:", "handoff:"),
           # practice round 3: two stacked exports of two systems, statuses renamed at a switch, a branch
           # written by name and then by code
           "two_exports": ("boundary:", "handoff:"), "status_switch": ("boundary:", "handoff:"),
           "branch_scope": ("handoff:",)}
    for name in synth.TRAPS:
        for twin in (False, True):
            for seed in SEEDS[:5]:
                m = synth.build(tmp_path / f"{name}{seed}{int(twin)}.xlsx", seed, name, twin=twin)
                got = [i["recipe"] for i in Analysis([m["path"]]).insights if i["recipe"].startswith(RECIPES)
                       and (twin or not i["recipe"].startswith(own.get(name, ("-",))))]
                assert not got, (name, twin, seed, got)


def test_the_noise_books_get_no_new_question(noise_fixtures):
    for paths in noise_fixtures:
        a = Analysis(paths)
        assert not [i["recipe"] for i in a.insights if i["recipe"].startswith(RECIPES)], paths
        assert not _asks(a), paths


# --------------------------------------------------------------------------
# rank 16: a location, channel or category whose rows behave unlike the others
# --------------------------------------------------------------------------
PEERS = ("find_odd_", "find_blankm_", "find_outliers_", "find_derive_")
PEER_RECIPES = ("oddgroup:", "blankmeasure:", "outliers:", "derive:")
GROUPS = ("odd_group", "share_group", "late_group", "per_case", "title_late")


def _peer_asks(a, answers=None):
    return [q for q in interview.candidates(a, answers or {}) if q.id.startswith(PEERS)]


def _odd_q(a, p):
    """The odd-group question about the plant's column and value, or None."""
    return next((q for q in _peer_asks(a) if q.id.startswith("find_odd_")
                 and (q.meta["finding"]["numbers"]["col"], q.meta["finding"]["numbers"]["value"])
                 == (p["col"], p["value"])), None)


def test_a_site_open_mid_span_with_its_own_clerks_is_asked_with_both_lines(tmp_path):
    """Present only in the middle of the dates while the others carry on, and
    clerks nobody else has: both pieces of evidence are in the one question."""
    hits = 0
    for seed in SEEDS:
        m, a = _built(tmp_path, seed, "odd_group")
        p = m["plants"][0]
        q = _odd_q(a, p)
        if q is None or len(_peer_asks(a)) != 1:
            continue
        _contract(a, q)
        kinds = q.meta["finding"]["numbers"]["kinds"]
        # the private values are named, so the owner can tell who they are (brief C2)
        k, who = len(p["private"]), p["columns"][1]
        said = rf"its only {who}, .+?, appears" if k == 1 else rf"its {k} {who}\w* \(.+?\) appear"
        hits += {"presence", "private"} <= set(kinds) and "rows only from" in q.prompt \
            and bool(re.search(said + r" on the rows of no other", q.prompt)) \
            and all(x in q.prompt for x in p["private"]) \
            and [o["id"] for o in q.options] == ["count", "leave_out", "ours"]
    assert hits >= NEEDED, hits


def test_leaving_an_odd_site_out_takes_its_rows_out_and_counting_it_changes_nothing(tmp_path, synth_seed):
    m, a = _built(tmp_path, synth_seed, "odd_group")
    p = m["plants"][0]
    q = _odd_q(a, p)
    if q is None:
        pytest.skip("no odd group found on this seed")
    t = a.tables[0]
    a.apply_answers({q.id: _ans(q, "a")})
    assert len(a._ctx.rows(t)) == len(t.rows) and not a.rules
    a.apply_answers({q.id: _ans(q, "b")})
    assert len(a._ctx.rows(t)) == len(t.rows) - len(p["rows"])


def test_a_category_priced_per_case_asks_what_the_price_is_for(tmp_path):
    hits = 0
    for seed in SEEDS:
        m, a = _built(tmp_path, seed, "per_case")
        p = m["plants"][0]
        q = _odd_q(a, p)
        if q is None:
            continue
        _contract(a, q)
        hits += [o["id"] for o in q.options] == ["pack", "right", "leave_out"] \
            and f"Is {p['columns'][1]} there for each one as counted, or for a pack or case?" in q.prompt \
            and q.meta["about"] == {"table": a.tables[0].tid, "col": p["columns"][1], "aspect": "unit",
                                    "values": [p["value"]]}
    assert hits >= NEEDED, hits


def test_twelve_per_case_divides_that_price_by_twelve_and_nothing_else(tmp_path, synth_seed):
    """The divisor is the number the owner typed with the pick, never the price
    ratio; the pick with no number divides nothing."""
    m, a = _built(tmp_path, synth_seed, "per_case")
    p = m["plants"][0]
    q = _odd_q(a, p)
    if q is None:
        pytest.skip("no odd group found on this seed")
    ans = _ans(q, "a) 12 per case")
    assert ans["options"] == ["pack"] and ans["text"] == "12 per case"
    a.apply_answers({q.id: ans})
    scale = [r for r in a.rules if r.kind == "scale" and not r.values.get("follows")]
    assert len(scale) == 1 and scale[0].values == {"col": p["columns"][1], "by": 12.0} and not a.unapplied_rules
    t = a.tables[0]
    j, jc = t.headers.index(p["columns"][1]), t.headers.index(p["col"])
    for before, after in zip(t.rows, a._ctx.rows(t)):
        if norm_key(before[jc]) == norm_key(p["value"]):
            assert after[j] == pytest.approx(before[j] / 12)
        else:
            assert after[j] == before[j]
    a.apply_answers({q.id: _ans(q, "a")})
    assert not [r for r in a.rules if r.kind == "scale"]


def test_a_site_with_most_of_the_money_on_few_rows_is_asked(tmp_path):
    hits = 0
    for seed in SEEDS:
        m, a = _built(tmp_path, seed, "share_group")
        p = m["plants"][0]
        q = _odd_q(a, p)
        if q is None:
            continue
        _contract(a, q)
        n = q.meta["finding"]["numbers"]
        hits += "share" in n["kinds"] and re.search(r"\d+% of \S.* on \d+% of the rows", q.prompt) is not None
    assert hits >= NEEDED, hits


def test_a_site_that_first_appears_in_the_last_tenth_is_asked_on_a_first_build(tmp_path):
    """A first build (no brain, no answers): a value that only starts at the end
    of the dates is asked now, not only when the file comes back grown."""
    hits = 0
    for seed in SEEDS:
        m, a = _built(tmp_path, seed, "late_group")
        p = m["plants"][0]
        q = _odd_q(a, p)
        if q is None:
            continue
        _contract(a, q)
        hits += "late" in q.meta["finding"]["numbers"]["kinds"] and "in the last 10% of the dates" in q.prompt
    assert hits >= NEEDED, hits


def test_a_site_that_first_appears_after_the_title_period_ends_is_asked(tmp_path):
    """The title names a period the rows run past, and one site's first rows come
    after it ends: that is late evidence, beside the site's partial presence.
    Under a title that covers every row (the twin) the same site is only
    partly present, which is not enough to ask."""
    hits = 0
    for seed in SEEDS:
        m, a = _built(tmp_path, seed, "title_late")
        p = m["plants"][0]
        q = _odd_q(a, p)
        if q is None or len(_peer_asks(a)) != 1:
            continue
        _contract(a, q)
        end = dt.date.fromisoformat(p["end"][:10])
        words = f"{synth._MONTHS[end.month - 1]} {end.day}, {end.year}"
        hits += {"late", "presence"} <= set(q.meta["finding"]["numbers"]["kinds"]) \
            and f"after {words}, where the title ends" in q.prompt
    assert hits >= NEEDED, hits


def test_the_group_twins_ask_nothing(tmp_path):
    """A clean table, a seasonal site that shares clerks and items, every site
    from the start, and every category priced per unit."""
    for name in GROUPS:
        for seed in SEEDS:
            _m, a = _built(tmp_path, seed, name, twin=True)
            assert not _found(a, "oddgroup:") and not _peer_asks(a), (name, seed)


# --------------------------------------------------------------------------
# rank 17: blanks that matter, price units and a measure the table lacks
# --------------------------------------------------------------------------
def test_a_notes_column_mostly_blank_asks_nothing(tmp_path):
    for seed in SEEDS[:5]:
        m, a = _built(tmp_path, seed, "sparse_blank", twin=True)
        note = m["plants"][0]["columns"][0]
        assert not [q for q in interview.candidates(a, {}) if (q.meta.get("about") or {}).get("col") == note
                    and q.source == "finding"], seed
        assert not _found(a, "blankmeasure:") and not _found(a, "blanks:"), seed


def test_a_measure_blank_on_a_few_rows_is_asked_with_where_they_bunch(tmp_path):
    hits = 0
    for seed in SEEDS:
        m, a = _built(tmp_path, seed, "sparse_blank")
        p = m["plants"][0]
        qs = _peer_asks(a)
        if len(qs) != 1 or not qs[0].id.startswith("find_blankm_"):
            continue
        _contract(a, qs[0])
        hits += qs[0].prompt.startswith(f"{p['col']} on ") and f"blank on {len(p['rows'])} of" in qs[0].prompt \
            and f"mostly at {p['site']} in " in qs[0].prompt
    assert hits >= NEEDED, hits


def test_unknown_leaves_the_blank_rows_out_and_zero_leaves_them_in(tmp_path, synth_seed):
    m, a = _built(tmp_path, synth_seed, "sparse_blank")
    q = _q(a, "find_blankm_")
    if q is None:
        pytest.skip("no blank measure found on this seed")
    t = a.tables[0]
    a.apply_answers({q.id: interview._answer(q, ["zero"], "")})
    assert len(a._ctx.rows(t)) == len(t.rows)
    a.apply_answers({q.id: interview._answer(q, ["unknown"], "")})
    assert len(a._ctx.rows(t)) == len(t.rows) - len(m["plants"][0]["rows"]) and not a.unapplied_rules


def test_a_status_blank_exactly_where_the_payment_is_blank_is_a_counted_fact(tmp_path):
    rng = random.Random(14)
    rows = []
    for k in range(240):
        paid = rng.random() < 0.8
        amt = round(rng.uniform(40, 900), 2)
        rows.append([dt.datetime(2024, 1, 1) + dt.timedelta(days=k), f"IV-{3000 + k}", rng.choice(_CLIENTS), amt,
                     amt if paid else None, rng.choice(["Settled", "Part"]) if paid else None])
    a = Analysis([_save(tmp_path / "paid.xlsx", ["Date", "Invoice", "Customer", "Amount", "Payment", "Status"], rows)])
    fact = _found(a, "structure:blank_match:")
    assert len(fact) == 1 and "the same rows where Payment is blank" in fact[0]["statement"]
    assert not [q for q in interview.candidates(a, {}) if q.id.startswith(("find_blank_", "find_blankm_"))]


def _slice_ok(a, p) -> bool:
    """The blanks on the first kind's accounts are one counted fact, and only the
    other kind's blanks are asked, with that slice's rows, money (written as
    every other note on the table writes it) and stake."""
    fact = _found(a, "structure:blank_slice:")
    q = _q(a, "find_blank_")
    nv, ns, k = p["never_rows"], p["slice_rows"], len(p["rows"])
    if len(fact) != 1 or q is None or f"where {p['by']} is {p['never']}" not in fact[0]["statement"] \
            or f"{nv:,} of the {nv:,} rows" not in fact[0]["statement"]:
        return False
    t = a.tables[0]
    fmt = findings.money_fmt(a, t, findings.money_column(a, t))
    return q.prompt.startswith(f"{k:,} of {ns:,} rows on {p['sheet']} where {p['by']} is {p['filled']} have no "
                               f"{p['col']}, {fmt(p['blank_money'])} of {p['money']}") \
        and q.meta["stake"] == pytest.approx(p["blank_money"] / p["total_money"], abs=1e-4)


def test_a_class_blank_on_balance_accounts_is_asked_only_for_the_expense_slice(tmp_path):
    hits = 0
    for seed in SEEDS:
        m, a = _built(tmp_path, seed, "blank_slice")
        hits += _slice_ok(a, m["plants"][0])
    assert hits >= NEEDED, hits


def test_blanks_spread_over_every_account_are_no_slice(tmp_path):
    """The twin: about as many blanks, on every account alike. There is no slice
    to state or to scope the question to (the plain blanks question of the whole
    column may still be asked; it is not this detector's)."""
    for seed in SEEDS:
        _m, a = _built(tmp_path, seed, "blank_slice", twin=True)
        assert not _found(a, "structure:blank_slice:"), seed
        assert not [q for q in interview.candidates(a, {}) if q.id.startswith("find_blank_")
                    and q.meta["finding"]["numbers"].get("slice")], seed


def _no_amount_book(path):
    rng = random.Random(22)
    rows = []
    for k in range(180):
        q, pr = rng.randint(1, 6), round(rng.uniform(4, 30), 2)
        rows.append([dt.datetime(2024, 1, 1) + dt.timedelta(days=k * 2), f"S-{7000 + k}",
                     rng.choice(["Kalo", "Venmi", "Torsil"]), q, pr, round(rng.uniform(1, 4), 2) if rng.random() < 0.3
                     else 0])
    return _save(path, ["Date", "Order ID", "Product", "Qty", "Price", "Discount"], rows), rows


def _derive_ok(m, a) -> bool:
    """One derived-measure confirm naming the three columns; after yes, monthly
    totals of Qty x Price minus the adjustment; after 'only', without it."""
    p = m["plants"][0]
    qs = [q for q in _peer_asks(a) if q.id.startswith("find_derive_")]
    if len(qs) != 1 or f"Net = {p['qty']} x {p['price']} minus {p['adj']}, on the lines that count" not in qs[0].prompt \
            or _found(a, "by_month:"):
        return False
    _contract(a, qs[0])
    rows = dict(synth.cells(m["path"]))[p["sheet"]]
    head = rows[0]
    jd, jq, jp, ja = (head.index(h) for h in (next(h for h in head if h in synth.POOLS["date"]), p["qty"], p["price"],
                                              p["adj"]))
    data = [r for r in rows[1:] if r[jq] is not None]
    net = sum(r[jq] * r[jp] - (r[ja] or 0) for r in data)
    first = min(r[jd] for r in data)
    key = f"{first.year}-{first.month:02d}"
    in_first = sum(r[jq] * r[jp] - (r[ja] or 0) for r in data if (r[jd].year, r[jd].month) == (first.year, first.month))
    a.apply_answers({qs[0].id: _ans(qs[0], "a")})
    got = _found(a, "by_month:")
    if len(got) != 1 or got[0]["numbers"]["total"] != pytest.approx(net, abs=0.01) \
            or dict(got[0]["numbers"]["months"])[key] != pytest.approx(in_first, abs=0.01):
        return False
    a.apply_answers({qs[0].id: _ans(qs[0], "b")})
    return _found(a, "by_month:")[0]["numbers"]["total"] == pytest.approx(sum(r[jq] * r[jp] for r in data), abs=0.01)


def test_a_table_with_no_amount_gets_one_derived_measure_and_monthly_totals_after_yes(tmp_path):
    hits = 0
    for seed in SEEDS:
        m, a = _built(tmp_path, seed, "derive")
        hits += _derive_ok(m, a)
    assert hits >= NEEDED, hits


def test_the_same_log_with_its_amount_column_derives_nothing(tmp_path):
    for seed in SEEDS:
        _m, a = _built(tmp_path, seed, "derive", twin=True)
        assert not _found(a, "derive:") and not _peer_asks(a), seed


def test_an_open_unit_question_keeps_the_product_check_out_of_the_assumptions(tmp_path):
    from sheetbrain import say
    rng = random.Random(3)
    items = {f"P{k}": (["Kalo", "Venmi", "Torsil", "Pamar"][k % 4], round(rng.uniform(5, 9), 2)) for k in range(12)}
    rows = []
    for i in range(300):
        it = rng.choice(list(items))
        cat, pr = items[it]
        pr = round(pr * 12, 2) if cat == "Kalo" else pr
        q = rng.randint(1, 6)
        rows.append([dt.datetime(2024, 1, 1) + dt.timedelta(days=i), f"S-{1000 + i}",
                     rng.choice(["North", "South", "East"]), it, cat, q, pr, round(q * pr, 2)])
    head = ["Order Date", "Order ID", "Store", "Product", "Category", "Qty", "Unit Price", "Gross Sales"]
    a = Analysis([_save(tmp_path / "case.xlsx", head, rows)])
    assert _found(a, "product_check:") and _q(a, "find_odd_") is not None
    text = say.readout(a, 0.5)
    assert "I'm assuming" not in text and "The arithmetic holds" in text
    # with every category priced per unit, the check is an assumption again
    flat = [r[:6] + [items[r[3]][1], round(r[5] * items[r[3]][1], 2)] for r in rows]
    b = Analysis([_save(tmp_path / "flat.xlsx", head, flat)])
    assert not _found(b, "oddgroup:") and "I'm assuming" in say.readout(b, 0.5)


# --------------------------------------------------------------------------
# rank 18: single entities unlike their peers
# --------------------------------------------------------------------------
def _entity_q(a, p):
    q = _q(a, "find_outliers_")
    return q if q is not None and p["value"] in [x["id"] for x in q.meta["finding"]["numbers"]["items"]] else None


def test_a_test_record_far_past_its_sequence_is_asked(tmp_path):
    hits = 0
    for seed in SEEDS:
        m, a = _built(tmp_path, seed, "sentinel")
        p = m["plants"][0]
        q = _entity_q(a, p)
        if q is None or len(_peer_asks(a)) != 1:
            continue
        _contract(a, q)
        x = q.meta["finding"]["numbers"]["items"][0]
        # practice round 4, fix 4: its rows carry money, so whether any of it went out is a pick of its own
        hits += x["kind"] == "sentinel" and p["value"] in q.prompt and [o["label"] for o in q.options][:2] == \
            ["Not real, nothing was paid out (type why)", "Not real, but money went out (type why)"]
    assert hits >= NEEDED, hits


def test_leaving_a_test_record_out_takes_only_its_rows(tmp_path, synth_seed):
    m, a = _built(tmp_path, synth_seed, "sentinel")
    p = m["plants"][0]
    q = _entity_q(a, p)
    if q is None:
        pytest.skip("no test record found on this seed")
    t = a.tables[0]
    a.apply_answers({q.id: _ans(q, "a")})
    assert len(a._ctx.rows(t)) == len(t.rows) - len(p["rows"])
    a.apply_answers({q.id: _ans(q, "b")})
    assert len(a._ctx.rows(t)) == len(t.rows) and not a.rules


def test_a_holder_who_never_pays_is_asked(tmp_path):
    hits = 0
    for seed in SEEDS:
        m, a = _built(tmp_path, seed, "zero_activity")
        p = m["plants"][0]
        q = _entity_q(a, p)
        if q is None:
            continue
        _contract(a, q)
        x = q.meta["finding"]["numbers"]["items"][0]
        hits += x["kind"] == "zero" and f"{p['columns'][1]} is 0 on {len(p['rows'])} of its" in q.prompt
    assert hits >= NEEDED, hits


def test_a_row_far_below_its_list_is_asked_and_right_on_purpose_is_kept_as_written(tmp_path):
    """'Right, on purpose' keeps the value as protected; any other pick (leave it
    out, typed text, Not sure) carries no protected value at all."""
    from sheetbrain import brain, rules
    hits = 0
    for seed in SEEDS:
        m, a = _built(tmp_path, seed, "ratio_outlier")
        p = m["plants"][0]
        q = _entity_q(a, p)
        if q is None:
            continue
        _contract(a, q)
        answers = {q.id: _ans(q, "b")}
        a.apply_answers(answers)
        text = " ".join(r.get("statement", "") for r in brain.Composer(a, a.paths[0], "b1", answers).compose())
        # practice round 4, fix 4: the pick carries the owner's words, and the note keeps the value and its peers
        hits += [o["id"] for o in q.options] == ["leave_1", "right"] and f"row {p['rows'][0]:,}" in q.prompt \
            and "is right, on purpose, per the owner:" in text and "keep it as written, do not fix it." in text \
            and answers[q.id]["protect"]["row"] == p["rows"][0] and len(a._ctx.rows(a.tables[0])) == len(a.tables[0].rows)
        assert [x["row"] for x in rules.protected(answers)] == [p["rows"][0]]
        for raw in ("a", "c) a slip we fixed later", "d"):
            other = {q.id: _ans(q, raw)}
            assert "protect" not in other[q.id] and not rules.protected(other), raw
    assert hits >= NEEDED, hits


def test_the_entity_twins_ask_nothing(tmp_path):
    """A salaried person with the same pay every period, a normal number and
    rows throughout; a ledger whose accounts sit on one side of a debit and
    credit pair; and list and billed amounts that all hold together."""
    for name in ("sentinel", "zero_activity", "ratio_outlier"):
        for seed in SEEDS:
            _m, a = _built(tmp_path, seed, name, twin=True)
            assert not _found(a, "outliers:") and not _peer_asks(a), (name, seed)


# --------------------------------------------------------------------------
# every group and entity question keeps the contract; nothing else is asked
# --------------------------------------------------------------------------
def test_every_group_and_entity_question_keeps_the_option_contract(tmp_path, synth_seed):
    for name in GROUPS + ("sparse_blank", "sentinel", "zero_activity", "ratio_outlier", "derive", "blank_slice"):
        _m, a = _built(tmp_path, synth_seed, name)
        for q in _peer_asks(a) + [q for q in interview.candidates(a, {}) if q.id.startswith("find_blank_")
                                  and name == "blank_slice"]:
            _contract(a, q)


def test_no_other_plant_and_no_twin_gets_a_group_or_entity_question(tmp_path):
    """Codes, boundaries, copies, journals, models: none of them is a group or a
    record unlike its peers, a blank measure or a table missing its amount. A
    code column's odd value goes into its dossier, so only the questions are
    checked on plants; twins get no finding at all. The one exception is a
    minority code planted at 8 to 15 times its peers' price: what that price is
    for is its own unit question, beside the dossier."""
    own = set(GROUPS) | {"sparse_blank", "sentinel", "zero_activity", "ratio_outlier", "derive", "blank_slice",
                         "odd_twice", "case_price", "measure_coblank",      # practice round 1
                         "sentinel_roster", "branch_scope", "person_site",  # practice round 3
                         "case_price_tabs", "sentinel_unpaid"}              # practice round 4
    for name in synth.TRAPS:
        for twin in (False, True):
            if name in own and not twin:
                continue
            for seed in SEEDS[:5]:
                m = synth.build(tmp_path / f"{name}{seed}{int(twin)}.xlsx", seed, name, twin=twin)
                a = Analysis([m["path"]])
                p = m["plants"][0]
                priced = [q for q in _peer_asks(a) if name == "code_minority" and not twin
                          and q.id.startswith("find_odd_") and q.kind == "unit"
                          and (q.meta["finding"]["numbers"]["col"], q.meta["finding"]["numbers"]["value"])
                          == (p["col"], str(p["value"]))]
                assert not [q for q in _peer_asks(a) if q not in priced], (name, twin, seed)
                if twin:
                    got = [i["recipe"] for i in a.insights if i["recipe"].startswith(PEER_RECIPES)]
                    assert not got, (name, seed, got)


def test_the_noise_books_get_no_group_or_entity_question(noise_fixtures):
    for paths in noise_fixtures:
        a = Analysis(paths)
        assert not [i["recipe"] for i in a.insights if i["recipe"].startswith(PEER_RECIPES)], paths
        assert not _peer_asks(a), paths


# --------------------------------------------------------------------------
# fixes after review: merged questions, pack units beside a dossier, rates,
# line totals, salaried hires, credits in price comparisons, blanks, readbacks
# --------------------------------------------------------------------------
def _billed(path, far=3):
    """420 invoices billed at 90% to 100% of list; far of them at 30%."""
    rng = random.Random(31)
    rows = []
    for k in range(420):
        lst = round(rng.uniform(50, 900), 2)
        rows.append([dt.datetime(2024, 1, 1) + dt.timedelta(days=k // 2), f"IV-{4000 + k}", lst,
                     round(lst * rng.uniform(0.9, 1.0), 2)])
    for r in rng.sample(rows, far):
        r[3] = round(r[2] * 0.3, 2)
    return _save(path, ["Date", "Invoice", "List Price", "Billed Price"], rows)


def test_three_ratio_rows_ask_one_question_without_error(tmp_path):
    """Three rows far off their list are three records of no one column: the
    first two are offered one by one and the prompt counts the rest."""
    a = Analysis([_billed(tmp_path / "three.xlsx")])
    q = _q(a, "find_outliers_")
    assert q is not None and len(q.meta["finding"]["numbers"]["items"]) == 3
    _contract(a, q)
    assert [o["id"] for o in q.options] == ["leave_1", "leave_2", "keep"]
    assert q.prompt.startswith("3 records on Book behave unlike their peers: ") and "; and 1 more." in q.prompt
    assert not any(o["label"].startswith("Leave all") for o in q.options)
    b = Analysis([_billed(tmp_path / "none.xlsx", far=0)])
    assert not _found(b, "outliers:") and _q(b, "find_outliers_") is None


def _pay_zero(path, zero=True):
    """40 people paid every two weeks for 12 periods; one is at 0 in both Gross
    Pay and Net Pay on every row."""
    rng = random.Random(33)
    ids = [f"E{101 + k}" for k in range(40)]
    rows = []
    for p in range(12):
        d = dt.datetime(2024, 1, 5) + dt.timedelta(days=14 * p)
        for i in ids:
            g = 0 if zero and i == "E107" else round(rng.uniform(800, 2400), 2)
            rows.append([d, i, g, round(g * 0.78, 2)])
    return _save(path, ["Pay Date", "Employee ID", "Gross Pay", "Net Pay"], rows)


def test_one_entity_at_zero_in_two_money_columns_is_one_record(tmp_path):
    a = Analysis([_pay_zero(tmp_path / "zero.xlsx")])
    items = _found(a, "outliers:")[0]["numbers"]["items"]
    assert [x["id"] for x in items] == ["E107"]
    assert items[0]["text"].startswith("Gross Pay and Net Pay are 0 on 12 of its 12 rows, where the others have "
                                       "them on")
    q = _q(a, "find_outliers_")
    _contract(a, q)
    assert q.prompt.startswith("1 record on Book behaves unlike its peers: E107 (")
    assert [o["label"] for o in q.options].count("Leave E107 out (type why)") == 1
    b = Analysis([_pay_zero(tmp_path / "paid.xlsx", zero=False)])
    assert not _found(b, "outliers:")


_LAST = ["Ashford", "Brenner", "Calloway", "Dunmore", "Ellery", "Fairbank", "Garrow", "Hollis", "Ingram", "Jessup",
         "Kendal", "Linwood", "Marlow", "Norcott", "Oakes", "Pembroke", "Quarry", "Radley", "Stanton", "Thorne",
         "Upton", "Varley", "Whitlock", "Yardley", "Zeller", "Ambrose", "Bexley", "Crane", "Dalby", "Everett"]


def _pay_switch(path, extra):
    """A pay register that switches systems halfway: 'LAST, FIRST' names and site
    names before, 'First Last' names and site codes after. extra: 'salaried' adds
    a hire from the switch with the same pay every period and the next number;
    'test' adds E9999 from the switch, with pay that varies."""
    rng = random.Random(35)
    sites = {"Northgate": "NG", "Riverside": "RS", "Hillcrest": "HC"}
    staff = [(f"E{1001 + k}", _CLIENTS[k % len(_CLIENTS)], _LAST[k], list(sites)[k % 3]) for k in range(30)]
    rate = {i: round(rng.uniform(15, 40), 2) for i, *_x in staff}
    start = dt.datetime(2024, 1, 5)
    rows = []
    for p in range(26):
        d = start + dt.timedelta(days=14 * p)
        after = p >= 13
        people = staff + ([("E1031", "Marqui", "Everly", "Riverside")] if extra == "salaried" and after else []) \
            + ([("E9999", "Tess", "Tester", "Hillcrest")] if extra == "test" and after else [])
        for i, first, last, site in people:
            hrs = round(rng.uniform(20, 80), 2)
            gross = 2400 if i == "E1031" else round(hrs * rate.get(i, 25.0), 2)
            rows.append([d, i, f"{first} {last}" if after else f"{last.upper()}, {first.upper()}",
                         sites[site] if after else site, hrs, gross])
    return _save(path, ["Pay Date", "Employee ID", "Employee", "Site", "Hours", "Gross"], rows)


def test_a_salaried_hire_after_a_system_change_is_not_a_test_record(tmp_path):
    """One amount on every row and rows only after the switch: both hold for a
    real hire, so without a number that stands out nothing is asked."""
    a = Analysis([_pay_switch(tmp_path / "hire.xlsx", "salaried")])
    assert _found(a, "boundary:")
    assert not _found(a, "outliers:") and _q(a, "find_outliers_") is None


def test_a_test_number_whose_rows_start_at_the_switch_is_asked(tmp_path):
    """Sign (iii): a number that stands out, with rows on one side of the date
    where the file changes (its pay varies, so one amount is no sign here)."""
    a = Analysis([_pay_switch(tmp_path / "test.xlsx", "test")])
    assert _found(a, "boundary:")
    items = _found(a, "outliers:")[0]["numbers"]["items"]
    assert [(x["id"], x["kind"]) for x in items] == [("E9999", "sentinel")]
    assert "its number is all 9s" in items[0]["text"] and "where the file changes" in items[0]["text"]
    q = _q(a, "find_outliers_")
    _contract(a, q)
    assert "Not real, nothing was paid out (type why)" in [o["label"] for o in q.options] and "E9999" in q.prompt


def _coded(path, factor=12, cats=("BEV", "FRZ", "DRY", "PRD", "DAI")):
    """Purchases with a caps category code; the first code's items priced at
    factor times the others'."""
    rng = random.Random(37)
    items = {f"Item {k + 1}": (cats[k % len(cats)], round(rng.uniform(5, 9), 2)) for k in range(20)}
    rows = []
    for k in range(360):
        it = rng.choice(list(items))
        cat, pr = items[it]
        pr = round(pr * factor, 2) if cat == cats[0] else pr
        q = rng.randint(1, 6)
        rows.append([dt.datetime(2024, 1, 1) + dt.timedelta(days=k), f"PO-{2000 + k}", it, cat, q, pr,
                     round(q * pr, 2)])
    return _save(path, ["Invoice Date", "Invoice No", "Item Code", "Cat Code", "Qty", "Unit Price", "Ext Price"], rows)


def test_a_coded_category_priced_per_case_is_asked_its_unit_beside_its_dossier(tmp_path):
    from sheetbrain import say
    a = Analysis([_coded(tmp_path / "coded.xlsx")])
    qs = interview.candidates(a, {})
    assert any(q.id.startswith("codes_") and q.meta["about"]["col"] == "Cat Code" for q in qs)
    q = next(q for q in qs if q.id.startswith("find_odd_"))
    _contract(a, q)
    assert [o["id"] for o in q.options] == ["pack", "right", "leave_out"] and q.meta["about"]["col"] == "Unit Price"
    assert "what that price is for is asked below" in say.readout(a, 0.5)
    assert "what that price is for is not settled yet" in say.readout(a, 0.5, asked=[])
    b = Analysis([_coded(tmp_path / "flat.xlsx", factor=1)])
    assert not [q for q in interview.candidates(b, {}) if q.id.startswith("find_odd_")]


def test_a_price_under_five_times_its_peers_is_no_unit_question(tmp_path):
    """The detection floor, frozen on first principles: a category at 4 times its
    peers' price (a pack of 4) is not strong evidence on its own, whatever its
    money share; at 8 times it is asked."""
    a = Analysis([_coded(tmp_path / "four.xlsx", factor=4, cats=("Kalo", "Venmi", "Torsil", "Pamar", "Quiess"))])
    assert not [q for q in interview.candidates(a, {}) if q.id.startswith("find_odd_")]
    b = Analysis([_coded(tmp_path / "eight.xlsx", factor=8, cats=("Kalo", "Venmi", "Torsil", "Pamar", "Quiess"))])
    assert [q.kind for q in interview.candidates(b, {}) if q.id.startswith("find_odd_")] == ["unit"]


def test_a_percent_discount_is_never_subtracted_as_an_amount(tmp_path):
    """Qty, Price and a discount rate (by its header, or by values that all sit
    between 0 and 1) and no amount: nothing to derive, nothing asked."""
    rng = random.Random(38)
    rows = [[dt.datetime(2024, 1, 1) + dt.timedelta(days=k * 2), f"S-{7000 + k}", rng.choice(["Kalo", "Venmi"]),
             rng.randint(1, 6), round(rng.uniform(4, 30), 2), rng.choice([0, 0, 0.1, 0.15])] for k in range(180)]
    for head in ("Discount %", "Discount"):
        path = _save(tmp_path / f"{head[-1]}.xlsx", ["Date", "Order ID", "Product", "Qty", "Price", head], rows)
        a = Analysis([path])
        assert not _found(a, "derive:") and _q(a, "find_derive_") is None, head
    path, _rows = _no_amount_book(tmp_path / "dollars.xlsx")
    assert _found(Analysis([path]), "derive:")


def _lines(path, bulk=True):
    """400 order lines of 1 to 4 units with a line total; bulk: one real order of 60."""
    rng = random.Random(39)
    rows = []
    for k in range(400):
        q, pr = rng.randint(1, 4), round(rng.uniform(5, 120), 2)
        rows.append([dt.datetime(2024, 1, 1) + dt.timedelta(hours=k * 9), f"SO-{2000 + k}",
                     rng.choice(_CLIENTS), q, pr, round(q * pr, 2)])
    if bulk:
        rows[150][3], rows[150][5] = 60, round(60 * rows[150][4], 2)
    return _save(path, ["Date", "Order", "Customer", "Qty", "Price", "Extended Price"], rows)


def test_a_line_total_over_its_price_is_never_a_ratio_outlier(tmp_path):
    for bulk in (True, False):
        a = Analysis([_lines(tmp_path / f"lines{int(bulk)}.xlsx", bulk)])
        assert not _found(a, "outliers:") and _q(a, "find_outliers_") is None, bulk


def _purchases(path):
    rng = random.Random(9)
    vendors = ["Kalo Supply", "Venmi Foods", "Torsil Trading", "Pamar Goods"]
    items = {f"IT-{100 + k}": round(rng.uniform(4, 40), 2) for k in range(15)}
    rows = []
    for k in range(400):
        it, v = rng.choice(list(items)), rng.choice(vendors)
        p, q = round(items[it] * rng.choice([1, 1, 1.1, 1.2]), 2), rng.randint(1, 12)
        q = -q if rng.random() < 0.05 else q
        rows.append([dt.datetime(2024, 1, 1) + dt.timedelta(days=k // 2), f"INV-{5000 + k}", v, it, q, p,
                     round(q * p, 2)])
    return _save(path, ["Invoice Date", "Invoice No", "Vendor", "Item Code", "Qty", "Unit Price", "Ext Price"], rows)


def test_netting_credits_everywhere_marks_the_price_comparison_not_applied(tmp_path):
    """Price comparisons read the lines before credits whatever the answer, so
    'net them against totals' marks them not applied; 'totals only' needs no mark."""
    a = Analysis([_purchases(tmp_path / "pur.xlsx")])
    neg = _q(a, "find_negatives_")
    answers = {neg.id: _ans(neg, "Some are returns from the stores")}
    fu = next(q for q in findings.follow_ups(a, answers) if q.id.startswith("follow_net_"))
    _contract(a, fu)
    mark = "credits in price comparisons is not applied here"
    a.apply_answers(dict(answers, **{fu.id: _ans(fu, "a")}))
    assert mark in next(i for i in a.insights if i["recipe"].startswith("spread:"))["statement"]
    assert not [i for i in a.insights if mark in i["statement"] and not i["recipe"].startswith("spread:")]
    a.apply_answers(dict(answers, **{fu.id: _ans(fu, "b")}))
    assert not [i for i in a.insights if mark in i["statement"]]


def _status_book(path, matched=True):
    """Invoices with a Status column and an Outcome blank exactly where the
    Status is Open (matched), or blank at random."""
    rng = random.Random(40)
    rows = []
    for k in range(240):
        st = rng.choice(["Open", "Closed", "Closed"])
        blank = st == "Open" if matched else rng.random() < 0.33
        rows.append([dt.datetime(2024, 1, 1) + dt.timedelta(days=k), f"IV-{3000 + k}", rng.choice(_CLIENTS),
                     round(rng.uniform(40, 900), 2), st, None if blank else rng.choice(["Won", "Lost", "Void"])])
    return _save(path, ["Date", "Invoice", "Customer", "Amount", "Status", "Outcome"], rows)


def _split_book(path, matched=True):
    """Lines with a Credit and a Debit column and a Dept blank exactly where
    Credit holds the amount (matched), or blank at random."""
    rng = random.Random(41)
    rows = []
    for k in range(240):
        cr = rng.random() < 0.3
        amt = round(rng.uniform(20, 900), 2)
        blank = cr if matched else rng.random() < 0.3
        rows.append([dt.datetime(2024, 1, 1) + dt.timedelta(days=k), rng.choice(_CLIENTS[:12]),
                     None if blank else rng.choice(["Pelru", "Truvo", "Gorfen"]), amt if cr else None,
                     None if cr else amt])
    return _save(path, ["Date", "Account", "Dept", "Credit", "Debit"], rows)


def test_a_blank_on_the_rows_of_one_value_or_one_side_is_a_counted_fact(tmp_path):
    a = Analysis([_status_book(tmp_path / "status.xlsx")])
    fact = _found(a, "structure:blank_match:")
    assert len(fact) == 1 and fact[0]["numbers"]["how"] == "value" \
        and "the same rows where Status is Open" in fact[0]["statement"]
    assert not [q for q in interview.candidates(a, {}) if q.id.startswith("find_blank_")]
    b = Analysis([_split_book(tmp_path / "split.xlsx")])
    fact = _found(b, "structure:blank_match:")
    assert len(fact) == 1 and fact[0]["numbers"]["how"] == "filled" \
        and "the same rows where Credit has a value" in fact[0]["statement"]
    for twin in (_status_book(tmp_path / "status0.xlsx", False), _split_book(tmp_path / "split0.xlsx", False)):
        assert not _found(Analysis([twin]), "structure:blank_match:"), twin


def test_a_blank_measure_offers_three_single_meanings_each_with_its_note(tmp_path):
    """Round 3 fix 4: 'Not counted that period: count it as zero', 'A real zero'
    and 'Unknown: leave the row out', one meaning each, each with its own note;
    only 'unknown' leaves rows out."""
    m, a = _built(tmp_path, 0, "sparse_blank")
    q = _q(a, "find_blankm_")
    assert [o["label"] for o in q.options] == ["Not counted that period: count it as zero", "A real zero",
                                               "Unknown: leave the row out"]
    _contract(a, q)
    assert set(q.fact["statements"]) == {"zero", "real_zero", "unknown"}
    assert not [o for o in q.options if " or " in o["label"]]


def _renamed_header(m: dict, header: str) -> Analysis:
    """The built book with the plant's column header renamed (the rows unchanged)."""
    p = m["plants"][0]
    wb = openpyxl.load_workbook(m["path"])
    ws = wb.worksheets[0]
    cell = next(c for row in ws.iter_rows(max_row=12) for c in row if c.value == p["col"])
    cell.value = header
    wb.save(m["path"])
    p["col"] = header
    return Analysis([m["path"]])


def test_same_as_another_site_with_its_name_typed_is_read_back_as_one(tmp_path):
    """Round 4 fix 18: a place column offers 'our own ...' in place of 'same as
    another'; on a column that is not a place, 'same as another' with a typed
    name is read back as one rule."""
    m, a = _built(tmp_path, 0, "late_group")
    assert [o["id"] for o in _odd_q(a, m["plants"][0]).options][2] == "ours"
    a = _renamed_header(m, "Crew")                    # not a place: 'same as another' is offered
    p = m["plants"][0]
    q = _odd_q(a, p)
    assert q is not None and [o["id"] for o in q.options][2] == "same"
    t = a.tables[0]
    jc = t.headers.index(p["col"])
    other = next(str(r[jc]) for r in t.rows if norm_key(r[jc]) != norm_key(p["value"]))
    answers = {q.id: _ans(q, f"c) {other}")}
    assert answers[q.id]["options"] == ["same"] and answers[q.id]["text"] == other
    a.apply_answers(answers)
    assert not a.rules and [c["rule"].kind for c in a.unapplied_rules] == ["map"]
    rb = findings.readback(a, answers)
    assert rb is not None and rb.options[0]["label"].startswith(f"{p['col']} {p['value']} with {other} as one")
    answers[rb.id] = _ans(rb, "a")
    a.apply_answers(answers)
    assert [r.kind for r in a.rules] == ["map"]
    assert not [r for r in a._ctx.rows(t) if norm_key(r[jc]) == norm_key(p["value"])]
    # the name typed must be a value of the column: anything else proposes no rule
    assert not findings.readback(a, {q.id: _ans(q, "c) the one downtown")})


def test_entity_outliers_stay_fast_on_thousands_of_ids(tmp_path):
    import time
    rng = random.Random(42)
    rows = []
    for k in range(12000):
        s = round(rng.uniform(10, 500), 2)
        rows.append([dt.datetime(2024, 1, 1) + dt.timedelta(minutes=k * 30), f"SO-{100000 + k}",
                     f"C{rng.randrange(3000):05d}", s, round(s * 0.07, 2), round(rng.uniform(3, 15), 2)])
    a = Analysis([_save(tmp_path / "big.xlsx", ["Order Date", "Order No", "Customer ID", "Subtotal", "Tax Amount",
                                                  "Shipping Fee"], rows)])
    t0 = time.perf_counter()
    a._entity_outliers(a.tables[0], [])
    assert time.perf_counter() - t0 < 2.0
