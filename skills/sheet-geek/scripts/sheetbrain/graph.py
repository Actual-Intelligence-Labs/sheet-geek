"""The brain as a graph (CONTRACTS.md section 2), built from the brain's own
records so the map and the Excel drawing show the same thing. Dots are what the
sheet is about (vendors, hotels, items, accounts, model rows) plus the notes
about them; lines are how they relate and what each note is about. Tabs and
columns are there too, but tucked away: structure is the skeleton, not the brain.

Everything here may come from a file someone else wrote, so nothing is trusted:
a malformed row is skipped, never a crash; the things drawn as dots are only the
ones code counted, never ones a sender typed; and a sender's notes are drawn as
the sender's, not as the owner's.
"""
from __future__ import annotations

import math
import os
import re
from collections import Counter

from .brain import col_sheet
from .brain import current as brain_current
from .recipes import plural

# computed notes worth a dot by default: what is odd, what is big, where money is
_SHOWN_INSIGHTS = ("exclusive", "unmatched", "signflip", "blanks", "formula", "negatives", "spread",
                   "top_share", "duplicates", "product_check", "check", "window", "listprice", "offlist", "onset",
                   "price_trend", "ratio", "contra", "opening", "closing")
# one palette for both views (map and Excel drawing), so a vendor is the same color everywhere
# things never share a hue with the notes (owner blue, counted gold), so a vendor never reads as a note
THING_COLORS = ["#7FB069", "#D96C5F", "#A77BD6", "#E07BB0", "#C9C34F", "#B5838D", "#8FB8A0", "#9C8FE0"]
FIXED_COLORS = {"file": "#F2F1EC", "sheet": "#8C8F96", "column": "#5A5E66", "other file": "#B8B2A7",
                "said": "#4F7CFF", "sender": "#D9925B", "found": "#E0B354", "guess": "#8A8F98",
                "question": "#C678DD", "web": "#4FB3C8"}
ALERT = {"disputed": "#E5534B", "left-out": "#7A7F87", "may-be-outdated": "#E0B354"}
_EDGE_LABEL = {"joins_on": "matches", "looks_up": "looks up", "derived_from": "calculated from",
               "feeds": "feeds", "determines": "sets", "same_as": "re-coded to"}
MAX_COLUMNS = 300          # a very wide tab never crowds out the things and the notes
_ZW = re.compile("[\u200b-\u200f\u202a-\u202e\u2060-\u2064\ufeff]")
_THING_ID = re.compile(r"^v:([^:]+):(.+)$")


def note_kind(r: dict, owners: set | None = None) -> str:
    """said (this owner), sender (whoever wrote a received file: not checked), found
    (counted by code), guess, question, web."""
    if r.get("record") == "open":
        return "question"
    src = r.get("source", "")
    if src == "told":
        return "said" if (r.get("said_by") or "owner") in (owners or {"owner"}) else "sender"
    return {"inferred": "guess", "web": "web"}.get(src, "found")


def note_title(r: dict) -> str:
    """A short name for a note, the way an Obsidian note has a title."""
    st = (r.get("statement") or "").strip()
    if r.get("record") == "open":
        st = re.sub(r"^(Not answered yet|The owner was not sure):\s*", "", st)
        return "Open: " + _clip(st, 52)
    if r.get("source") == "told":
        head = _clean(r.get("label") or "").rstrip("?: ")
        m = re.search(r"per the owner:\s*(.+?)(?:\.\s|\.$|$)", st)
        if head and m:
            return _clip(f"{head}: {m.group(1)}", 56)
    return _clip(st, 56)


def _clean(s) -> str:
    return re.sub(r"\s+", " ", _ZW.sub("", str(s or ""))).strip()


def _clip(s: str, n: int) -> str:
    s = _clean(s).rstrip(".")
    if len(s) <= n:
        return s
    cut = s[:n - 1].rsplit(" ", 1)[0]
    return cut.rstrip(",;:(") + "..."


def _text_map(r: dict) -> dict:
    out = {}
    for line in str(r.get("text") or "").split("\n"):
        k, sep, v = line.partition(":")
        if sep:
            out[k.strip()] = v.strip()
    return out


def _num(v, default: float = 0.0) -> float:
    try:
        x = float(v)
        return x if math.isfinite(x) else default
    except (TypeError, ValueError):
        return default


def _note(r: dict) -> dict:
    return {"statement": r.get("statement", ""), "source": r.get("source", ""),
            "status": r.get("status", ""), "as_of": r.get("as_of", ""), "said_by": r.get("said_by", "")}


def _abouts(r: dict) -> list:
    return [x.strip() for x in str(r.get("to") or "").split("|") if x.strip()]


def build(records: list, *, title: str = "", playbook: dict | None = None, private_count: int = 0,
          owners: set | None = None) -> dict:
    """Graph JSON from a brain's records. Needs nothing but the records, so a
    brain read from a file someone sent draws the same map. `owners` are the
    said_by names of this machine's owner; any other told note is the sender's."""
    pb = playbook or {}
    owners = set(owners or {"owner"})
    recs = []
    for r in brain_current(records):   # one record per id: the row that holds now
        rid = str(r.get("id", ""))
        if r.get("record") == "meta" or not rid:
            continue
        if r.get("status") == "superseded" and r.get("source") != "told":
            continue                   # an old count is history; an owner's word stays on the map
        recs.append(dict(r, id=rid))
    meta = next((r for r in records if isinstance(r, dict) and r.get("record") == "meta"), {})
    file_id = str(meta.get("id") or "brain:file")
    title = title or _clean(meta.get("label")) or "Spreadsheet"
    sheets = {r["id"][6:] for r in recs if r["id"].startswith("sheet:")}
    nodes: dict = {}
    links: list = []

    def _col_sheet(cid: str) -> str:
        return col_sheet(cid, sheets)      # 'col:Summary#2.{Region}' sits on Summary

    def node(nid, label, typ, group, size, *, hidden=False, **kw):
        if nid in nodes:
            return nodes[nid]
        lab = _clean(label)[:80] or _clean(nid)[:80] or "(no name)"
        n = {"id": nid, "label": lab, "type": typ, "group": group, "size": round(size, 2),
             "status": kw.get("status", "current"), "source": kw.get("source", "computed"),
             "hidden": hidden, "summary": kw.get("summary", ""), "body": list(kw.get("body", []))}
        nodes[nid] = n
        return n

    def link(a, b, typ, label="", weight=1.0, status="current"):
        if a in nodes and b in nodes and a != b:
            links.append({"source": a, "target": b, "type": typ, "label": _clean(label)[:60],
                          "weight": round(weight, 3), "status": status})

    kind_name = str(pb.get("name") or meta.get("kind") or "Spreadsheet").replace("_", " ")
    node(file_id, title, "file", "file", 34, summary=kind_name[:1].upper() + kind_name[1:])
    # only what code counted becomes a thing or a model row: a sender cannot type a vendor into the picture
    counted = lambda r: r.get("source", "computed") == "computed"  # noqa: E731
    for r in recs:
        if r.get("record") == "node" and r["id"].startswith("sheet:"):
            node(r["id"], r.get("label") or r["id"][6:], "sheet", "sheet", 20, summary=r.get("statement", ""),
                 body=[_note(r)])
    things = [r for r in recs if r.get("record") == "node" and r.get("kind") == "thing" and counted(r)
              and _THING_ID.match(r["id"])]
    amounts = {r["id"]: abs(_num(_text_map(r).get("amount"))) for r in things}
    kind_of = {r["id"]: _THING_ID.match(r["id"]).group(1) for r in things}
    kind_max = Counter()
    for r in things:
        kind_max[kind_of[r["id"]]] = max(kind_max[kind_of[r["id"]]], amounts[r["id"]])
    for r in recs:
        if r.get("record") == "node" and r.get("kind") == "entity" and counted(r) \
                and re.match(r"^ent:[^:]+$", r["id"]):
            k = r["id"][4:]
            node(r["id"], _hub_label(r.get("label"), k), "kind", k, 14, summary=r.get("statement", ""),
                 body=[_note(r)])
    for r in things:          # a kind of thing with no entity record still gets its group dot
        k = kind_of[r["id"]]
        if f"ent:{k}" not in nodes:
            tm = _text_map(r)
            noun = (tm.get("kind") or k).replace("_", " ")
            n_k = sum(1 for x in things if kind_of[x["id"]] == k)
            hub = node(f"ent:{k}", plural(noun).capitalize(), "kind", k, 14,
                       summary=f"{n_k} {plural(noun, n_k)} shown, the biggest by size.")
            if tm.get("sheet"):
                hub["sheet"] = tm["sheet"]
    for r in things:
        k = kind_of[r["id"]]
        share = amounts[r["id"]] / kind_max[k] if kind_max[k] else 0.3
        n = node(r["id"], r.get("label") or _THING_ID.match(r["id"]).group(2), "thing", k,
                 6 + 22 * math.sqrt(share), summary=r.get("statement", ""), body=[_note(r)])
        if _text_map(r).get("left_out") == "yes":
            n["status"] = "left-out"
    for r in recs:
        if r.get("record") == "node" and r.get("kind") == "formula_block" and counted(r) \
                and r["id"].startswith("row:"):
            tm = _text_map(r)
            sheet = tm.get("sheet") or "model"
            n = node(r["id"], r.get("label") or r["id"][4:], "row", f"rows:{sheet}",
                     6 + 3 * math.sqrt(max(0.0, _num(tm.get("refs")))), summary=r.get("statement", ""),
                     body=[_note(r)])
            n["sheet"] = sheet
            if tm.get("flags"):
                n["status"] = "disputed"
    for r in recs:
        if r.get("record") == "link":
            node(r["id"], r.get("label") or "another file", "other_file", "other file", 16,
                 source=r.get("source", "computed"), summary=r.get("statement", ""), body=[_note(r)])
    # notes: every fact, finding and question is a dot on what it is about
    notes = [r for r in recs if r.get("record") in ("fact", "insight", "open")]
    for r in notes:
        nk = note_kind(r, owners)
        recipe = str(r.get("ref") or "").replace("recipe:", "")
        hidden = (r.get("record") == "insight" and not recipe.startswith(_SHOWN_INSIGHTS)) or \
                 (r.get("record") == "fact" and r.get("source") == "computed" and r.get("kind") == "grain")
        n = node(r["id"], note_title(r), "note", nk, 5 if nk not in ("said", "sender") else 6.5, hidden=hidden,
                 source=r.get("source", "computed"), status=r.get("status", "current"),
                 summary=r.get("statement", ""), body=[_note(r)])
        n["note_kind"] = nk
    # columns last and capped: they are the skeleton, and a very wide tab must not push out the brain
    cols = [r for r in recs if r.get("record") == "node" and r["id"].startswith("col:")]
    for r in cols[:MAX_COLUMNS]:
        node(r["id"], r.get("label") or r["id"], "column", "column", 5, hidden=True, summary=r.get("statement", ""),
             body=[_note(r)])
    # the skeleton's lines
    for n in list(nodes.values()):
        nid = n["id"]
        if n["type"] == "sheet":
            link(nid, file_id, "part_of", weight=0.6)
        elif n["type"] == "column":
            link(nid, f"sheet:{_col_sheet(nid)}", "part_of", weight=0.3)
        elif n["type"] == "thing":
            link(nid, f"ent:{kind_of[nid]}", "part_of", weight=0.25)
        elif n["type"] == "kind" and n.get("sheet"):
            link(nid, f"sheet:{n['sheet']}", "part_of", weight=0.4)
        elif n["type"] == "row":
            link(nid, f"sheet:{n['sheet']}", "part_of", weight=0.15)
    for r in recs:
        if r.get("record") == "node" and r.get("kind") == "entity" and r.get("from") and r["id"] in nodes:
            link(r["id"], f"sheet:{_col_sheet(str(r['from']))}", "part_of", weight=0.4)
        if r.get("record") == "link" and r.get("from"):
            sheet = _col_sheet(str(r["from"]))
            link(f"sheet:{sheet}" if f"sheet:{sheet}" in nodes else file_id, r["id"], "links_file",
                 f"matches on {r.get('to', '')}")
    # relations: things to things, tabs to tabs, rows to rows
    wmax = 0.0
    rel_links = []
    for r in recs:
        if r.get("record") != "edge":
            continue
        a, b, kind = str(r.get("from", "")), str(r.get("to", "")), str(r.get("kind", "relates"))
        if kind == "determines":
            continue          # column to column bookkeeping: true, but not the brain
        if a.startswith("col:") or b.startswith("col:"):
            ca, cb = _col_sheet(a), _col_sheet(b)
            if kind == "joins_on" and ca and cb and ca != cb:
                link(f"sheet:{ca}", f"sheet:{cb}", "joins_on", f"{a[a.find('.{') + 2:-1]} matches", weight=1.5,
                     status=r.get("status", "current"))
            continue
        if a not in nodes or b not in nodes or a == b:
            continue
        w = _num(_text_map(r).get("weight"), 1.0)
        label = r.get("label", "") if kind == "relates" else _EDGE_LABEL.get(kind, kind.replace("_", " "))
        if kind == "relates":
            label = str(label).replace(nodes[a]["label"], "").replace(nodes[b]["label"], "").strip()
            wmax = max(wmax, w)
        lk = {"source": a, "target": b, "type": kind, "label": _clean(label)[:60], "weight": w,
              "status": r.get("status", "current")}
        links.append(lk)
        rel_links.append(lk)
    for lk in rel_links:          # relation widths 0.6 to 4, by weight
        if lk["type"] == "relates" and wmax:
            lk["weight"] = round(0.6 + 3.4 * lk["weight"] / wmax, 3)
    by_id = {r["id"]: r for r in recs}
    for r in notes:
        targets = sorted({t for t in _abouts(r) if t in nodes and t != r["id"]}) or [file_id]
        for t in targets:
            link(r["id"], t, "about", weight=0.5, status=r.get("status", "current"))
        if all(nodes[t]["hidden"] for t in targets) and not nodes[r["id"]]["hidden"]:
            # a note about hidden columns shows on their tab, once
            for sheet in sorted({_col_sheet(t) for t in targets if _col_sheet(t)}):
                link(r["id"], f"sheet:{sheet}", "about", weight=0.3)
    # a dot reads like its note: what it is, then what is said about it, each once
    for lk in links:
        if lk["type"] == "about" and lk["source"] in by_id:
            body = nodes[lk["target"]]["body"]
            entry = _note(by_id[lk["source"]])
            if all(b["statement"] != entry["statement"] for b in body):
                body.append(entry)
    for n in nodes.values():
        n["body"] = n["body"][:24]
        confirmed = any(b["source"] == "told" and b["status"] == "confirmed"
                        and (b.get("said_by") or "owner") in owners for b in n["body"][1:])
        if n["type"] in ("thing", "row", "sheet", "kind") and confirmed and n["status"] == "current":
            n["status"] = "confirmed"
            n["source"] = "told"
    groups = _groups(nodes)
    shown = [n for n in nodes.values() if not n["hidden"]]
    dropped = max(0, len(cols) - MAX_COLUMNS)
    return {
        "version": 2,
        "title": title,
        "subtitle": _subtitle(pb, nodes),
        "sentence": _sentence(nodes, pb),
        "generated": meta.get("as_of", ""),
        "footer": f"Contains data from {title}. Local file, no internet. Send the sheet, not this page."
                  + (f" {private_count} private note{'s' if private_count != 1 else ''} kept on this machine "
                     f"{'are' if private_count != 1 else 'is'} not shown." if private_count else "")
                  + (f" {dropped:,} more columns are not drawn." if dropped else ""),
        "nodes": list(nodes.values()),
        "links": links,
        "groups": groups,
        "start_here": _start_here(nodes, links),
        "alert_colors": ALERT,
        "owners": sorted(owners),
        "counts": {"things": sum(1 for n in shown if n["type"] in ("thing", "row")),
                   "notes": sum(1 for n in shown if n["type"] == "note"),
                   "said": sum(1 for n in nodes.values() if n.get("note_kind") == "said"),
                   "sender": sum(1 for n in nodes.values() if n.get("note_kind") == "sender")},
    }


def _hub_label(label, k: str) -> str:
    lab = _clean(label).replace("_", " ")
    return (lab or plural(k.replace("_", " "))).capitalize()


def _groups(nodes: dict) -> list:
    seen, out = set(), []
    names = {"file": "The workbook", "sheet": "Tabs", "column": "Columns", "other file": "Other files",
             "said": "What the owner said", "sender": "What the sender said (not checked)",
             "found": "What it counted", "guess": "Guesses to confirm", "question": "Open questions",
             "web": "From the web"}
    hubs = {n["group"]: n["label"] for n in nodes.values() if n["type"] == "kind"}
    fixed_types = ("note", "file", "sheet", "column", "other_file")
    for n in nodes.values():
        g = n["group"]
        if g in seen:
            continue
        seen.add(g)
        if n["type"] in fixed_types:
            lab = names.get(g, g)
        elif g in hubs:
            lab = hubs[g]          # the legend says what the hub dot says
        elif n["type"] == "row":
            lab = f"{n.get('sheet', g[5:])} rows"
        else:
            lab = g.replace("_", " ").capitalize()
        out.append({"id": g, "label": lab, "type": n["type"]})
    k = 0
    for g in out:                 # things and model rows share the rotating palette, in order of appearance
        if g["type"] in fixed_types and g["id"] in FIXED_COLORS:
            g["color"] = FIXED_COLORS[g["id"]]
        else:
            g["color"] = THING_COLORS[k % len(THING_COLORS)]
            k += 1
    return out


def _subtitle(pb: dict, nodes: dict) -> str:
    things = sum(1 for n in nodes.values() if n["type"] in ("thing", "row"))
    said = sum(1 for n in nodes.values() if n.get("note_kind") == "said")
    sender = sum(1 for n in nodes.values() if n.get("note_kind") == "sender")
    notes = sum(1 for n in nodes.values() if n["type"] == "note" and not n["hidden"])
    kind = str(pb.get("name") or "Spreadsheet").replace("_", " ").capitalize()
    return (f"{kind} . {things} things . {notes} notes"
            + (f" . {said} from the owner" if said else "")
            + (f" . {sender} from the sender, not checked" if sender else ""))


def _sentence(nodes: dict, pb: dict | None = None) -> str:
    if any(n["type"] == "row" for n in nodes.values()):
        return ("Dots are the model's rows and the notes about them. Lines show which rows feed which. "
                "Red means worth a look. Click a dot to read its note.")
    kinds = [n["label"].lower() for n in nodes.values() if n["type"] == "kind"][:4]
    if not kinds:
        return "Dots are this file's tabs and the notes about them. Click a dot to read its note."
    what = ", ".join(kinds[:-1]) + (" and " + kinds[-1] if len(kinds) > 1 else "".join(kinds))
    size = ((pb or {}).get("graph") or {}).get("size_by") or ""
    more = "more money" if ((pb or {}).get("roles", {}).get(size, {}).get("unit") == "currency") else "more rows"
    return (f"Dots are {what}, and the notes about them. Lines show how they connect and what each note is "
            f"about. Bigger means {more}. Click a dot to read its note.")


def _start_here(nodes: dict, links: list) -> list:
    out = []
    about = {}
    for lk in links:
        if lk["type"] == "about":
            about.setdefault(lk["source"], []).append(lk["target"])
    for kind, upto in (("said", 2), ("found", 3)):
        for n in [n for n in nodes.values() if n.get("note_kind") == kind and not n["hidden"]]:
            if len(out) >= upto:
                break
            tgt = next((t for t in about.get(n["id"], []) if nodes[t]["type"] in ("thing", "row")), n["id"])
            out.append({"node": tgt, "text": _clip(n["summary"], 90)})
    if not out:
        biggest = max((n for n in nodes.values() if not n["hidden"] and n["type"] != "file"),
                      key=lambda n: n["size"], default=None)
        if biggest:
            out.append({"node": biggest["id"], "text": f"Start with {biggest['label']}"})
    return out[:3]


def title_for(path: str) -> str:
    return os.path.basename(path)
