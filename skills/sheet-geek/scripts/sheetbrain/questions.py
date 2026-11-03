"""Turning what code found into the owner's questions: finding_questions ranks every finding into
a question, and follow_ups asks the next one when an answer opens a new thread. Split from findings.py
so each file stays under 256 KiB, the most the plugin directory's checker reads; findings.py still
answers for these names."""
from __future__ import annotations

import re

from .profile import norm_key

from .findings import (
    _a, _about, _about_dep, _about_role, _answered_rows, _class_prefix, _closing_question, code_dossiers,
    _code_pairs, _codes_words, _contra_question, _copies_question, _derive_question, _factor_words,
    _finding_stake, _fit_desc, _fold_same_rows, _handoff_question, HANDOFF_SHOWN, _has_dossier,
    header_words, _in_actuals, _join, _listed, _listscope_question, _lookup_question, _lower_first,
    _measure_blank_question, _measure_words, _missing_codes, _month, _named, _net_question, _net_slug,
    _nonstock_question, _not_items_scope, _odd_group_question, _offlist_question, _one_per_value,
    _onset_question, _opening_question, _ours, _outliers_question, _pairs_question, _parent_about, _peers,
    _period_question, _plan_block_question, _ratio_question, readback, _rebate_base, _rid, _short, _SITE,
    _slug, _suspense_lines, _through_join, _tieout_question, _title_rows, _title_short_question,
    _two_formulas, _unit_group_question, _unmatched_for, _unpaid_question, _upload_question,
    _variants_question, _versions_question, _window_question, _worth,
)


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
