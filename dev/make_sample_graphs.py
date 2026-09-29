"""Write the sample graphs the viewer is developed and checked against.

  dev/sample_graph_procurement.json   purchase history, about 40 dots
  dev/sample_graph_finance.json       formula flow through a 3-statement model
  dev/sample_graph_hostile.json       markup and script strings in every text field
  <out>/sample_graph_large.json       2,000 dots, mostly records (perf check)

All names are fictional. Output is deterministic.

    .venv/bin/python dev/make_sample_graphs.py [--large-out PATH]
"""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

DEV = Path(__file__).resolve().parent
TODAY = "2026-09-25"


def note(statement, source, status, as_of=TODAY, said_by=None):
    if said_by is None:
        said_by = {"told": "owner", "computed": "sb", "inferred": "ai", "web": "example.org"}[source]
    return {"statement": statement, "source": source, "status": status, "as_of": as_of, "said_by": said_by}


def node(id, label, type, group, size, status, source, summary, notes=(), records=False):
    return {
        "id": id, "label": label, "type": type, "group": group, "size": size,
        "status": status, "source": source, "records": records,
        "summary": summary, "notes": list(notes),
    }


def link(source, target, type, label="", status="current", weight=1.0):
    return {"source": source, "target": target, "type": type, "label": label, "status": status, "weight": weight}


# ---------------------------------------------------------------------------
# Procurement
# ---------------------------------------------------------------------------
def procurement():
    N, L = [], []
    vendors = [
        ("Harbor Foods", 1900, "computed", "current", "$1.9M, 38% of spend . 614 items . 4 locations", [
            note("Harbor Foods is 38% of spend, $1.9M across 614 items.", "computed", "current"),
            note("Harbor is our broadline distributor. The contract renews every March.", "told", "confirmed", "2026-09-18"),
            note("Harbor Foods lists 14 distribution centers across the Southeast.", "web", "current", "2026-09-20", "harborfoods.example"),
        ]),
        ("Gulfstream Meats", 760, "computed", "disputed", "$760K, 15% of spend . 88 items . 3 locations", [
            note("Gulfstream prices beef per case.", "told", "disputed", "2026-09-18"),
            note("1,904 beef lines have unit prices that only make sense per lb, not per case.", "computed", "disputed"),
        ]),
        ("Coastline Produce", 820, "computed", "may-be-outdated", "$820K, 16% of spend . 203 items . 4 locations", [
            note("Coastline delivers produce six days a week.", "told", "may-be-outdated", "2025-02-10"),
            note("Coastline is 16% of spend, mostly produce.", "computed", "current"),
        ]),
        ("Bayview Dairy", 540, "computed", "current", "$540K, 11% of spend . 41 items . 3 locations", [
            note("Bayview Dairy is 11% of spend across 41 items.", "computed", "current"),
        ]),
        ("Palmetto Beverage", 420, "told", "confirmed", "$420K, 8% of spend . 57 items . 4 locations", [
            note("Seabreeze Drinks was the beverage vendor until June 2025.", "told", "superseded", "2025-06-30"),
            note("Palmetto Beverage took over beverages in July 2025.", "told", "confirmed", "2026-09-18"),
        ]),
        ("Keystone Paper & Supply", 310, "inferred", "unconfirmed", "$310K, 6% of spend . 96 items . 4 locations", [
            note("Keystone looks like the paper and chemicals supplier, going by item names.", "inferred", "unconfirmed"),
        ]),
    ]
    for name, spend, src, st, summ, notes in vendors:
        N.append(node("ent:vendor:" + name, name, "entity", "vendor", spend, st, src, summ, notes))

    locations = [
        ("Downtown Kitchen", 2100, "computed", "current", "$2.1M, 42% of store spend . 9,120 lines", [
            note("Downtown Kitchen is the busiest location: 42% of store spend.", "computed", "current"),
        ]),
        ("Beachside Grill", 1500, "computed", "current", "$1.5M, 31% of store spend . 6,480 lines", [
            note("Beachside Grill buys most of the seafood.", "computed", "current"),
        ]),
        ("Airport Cafe", 620, "inferred", "may-be-outdated", "$620K . last line 2025-08-31", [
            note("Airport Cafe has no lines after 2025-08-31. It may have closed.", "inferred", "unconfirmed"),
        ]),
        ("Commissary (CMSY)", 540, "told", "confirmed", "$540K of internal transfers . excluded from store spend", [
            note("Location CMSY is the commissary. Its lines are internal transfers and are left out of store spend.", "told", "confirmed", "2026-09-18"),
            note("CMSY lines never carry a vendor invoice number.", "computed", "current"),
        ]),
    ]
    for name, spend, src, st, summ, notes in locations:
        N.append(node("ent:location:" + name, name, "entity", "location", spend, st, src, summ, notes))

    categories = [
        ("Proteins", 1250, "computed", "current"), ("Produce", 780, "computed", "current"),
        ("Dairy", 520, "computed", "current"), ("Dry goods", 610, "computed", "current"),
        ("Beverages", 430, "computed", "current"), ("Paper & disposables", 260, "computed", "current"),
        ("Chemicals", 120, "inferred", "unconfirmed"), ("Bakery", 180, "computed", "current"),
    ]
    for name, spend, src, st in categories:
        notes = [note("%s is $%sK of spend." % (name, spend), "computed", "current")]
        if src == "inferred":
            notes = [note("I grouped these by item name. The sheet has no category for them.", "inferred", "unconfirmed")]
        N.append(node("ent:category:" + name, name, "entity", "category", spend, st, src,
                      "$%sK of spend" % spend, notes))

    N += [
        node("sheet:Detail", "Detail", "sheet", "tab", 900, "confirmed", "told",
             "Invoice lines . 18,240 rows . 14 columns",
             [note("One row is one invoice line.", "told", "confirmed", "2026-09-18"),
              note("Dates run 2025-01-02 to 2026-06-30.", "computed", "current")]),
        node("sheet:Price File", "Price File", "sheet", "tab", 520, "current", "computed",
             "Contract prices . 1,108 rows",
             [note("One row per item and vendor, with the contract price.", "computed", "current")]),
        node("sheet:Summary", "Summary", "sheet", "tab", 380, "unconfirmed", "inferred",
             "Pivot of Detail by vendor and month",
             [note("Summary looks like a pivot of Detail by vendor and month.", "inferred", "unconfirmed")]),
    ]
    cols = [
        ("Item #", 600, "computed", "current", "Joins Detail to Price File . 97% match",
         [note("Item # joins Detail to Price File. 97% of Detail lines find a match.", "computed", "current")]),
        ("Unit price", 560, "told", "confirmed", "Price per unit of measure",
         [note("Unit price already includes contract deals taken off invoice.", "told", "confirmed", "2026-09-18"),
          note("Rebates arrive later as separate credits, so they are not in Unit price.", "told", "unconfirmed", "2026-09-18")]),
        ("Ext price", 640, "computed", "current", "Qty x Unit price . 212 credit lines",
         [note("Ext price equals Qty x Unit price on 99.2% of lines.", "computed", "current"),
          note("212 lines are negative. They look like credits.", "computed", "current")]),
        ("UOM", 420, "computed", "disputed", "CS or LB . mixed on beef lines",
         [note("Lines are priced per CS (case) or per LB. Beef lines mix both.", "computed", "disputed")]),
        ("Vendor", 360, "computed", "current", "6 vendors", [note("Six distinct vendors.", "computed", "current")]),
        ("Location", 340, "computed", "current", "4 locations incl. the commissary", [note("Four locations, one of them the commissary.", "computed", "current")]),
        ("Invoice date", 300, "computed", "current", "2025-01-02 to 2026-06-30", [note("18 months of invoices.", "computed", "current")]),
    ]
    for name, size, src, st, summ, notes in cols:
        N.append(node("col:Detail.{%s}" % name, name, "column", "column", size, st, src, summ, notes))
    N += [
        node("metric:spend", "Total spend", "metric", "column", 700, "current", "computed",
             "$4.75M across 18,240 lines", [note("Store spend is $4.21M once commissary transfers are left out.", "computed", "current")]),
        node("metric:credits", "Credits", "metric", "column", 260, "unconfirmed", "computed",
             "-$38K on 212 lines", [note("212 credit lines add up to -$38K.", "computed", "current"),
                                    note("Credits are returns, not rebates.", "told", "unconfirmed", "2026-09-18")]),
        node("dim:month", "Month", "dimension", "column", 240, "current", "computed",
             "18 months", [note("Spend peaks in March and July.", "computed", "current")]),
    ]
    items = [
        ("10442", "Chicken breast 40 lb", 220, "Proteins", "Harbor Foods", "computed", "current"),
        ("10876", "Ground beef 80/20", 190, "Proteins", "Gulfstream Meats", "computed", "disputed"),
        ("20817", "Romaine hearts 24 ct", 96, "Produce", "Coastline Produce", "computed", "may-be-outdated"),
        ("31190", "Whole milk 4/1 gal", 88, "Dairy", "Bayview Dairy", "computed", "current"),
        ("50233", "Clamshell 9 in to-go", 64, "Paper & disposables", "Keystone Paper & Supply", "inferred", "unconfirmed"),
        ("44120", "Cold brew concentrate", 72, "Beverages", "Palmetto Beverage", "computed", "current"),
    ]
    for num, name, spend, cat, vendor, src, st in items:
        notes = [note("Item %s: $%sK over 18 months." % (num, spend), "computed", "current")]
        if num == "20817":
            notes.append(note("Romaine hearts cost three different prices across locations in May 2026.", "computed", "may-be-outdated", "2026-05-31"))
        if num == "10876":
            notes.append(note("Priced per lb on some lines and per case on others.", "computed", "disputed"))
        N.append(node("ent:item:" + num, "%s (%s)" % (name, num), "record", "item", spend, st, src,
                      "$%sK . %s" % (spend, vendor), notes, records=True))
        L.append(link("ent:item:" + num, "ent:category:" + cat, "part_of", "", "current", 0.4))
        L.append(link("ent:vendor:" + vendor, "ent:item:" + num, "relates", "sells", "current", 0.4))
    N.append(node("link:procurement_contracts.xlsx", "procurement_contracts.xlsx", "link", "file", 300, "unconfirmed", "told",
                  "Another file . contract terms by vendor",
                  [note("Contract prices live in procurement_contracts.xlsx, joined on Item #.", "told", "confirmed", "2026-09-18"),
                   note("Its Item # column matched 91% of Price File rows when last checked.", "computed", "may-be-outdated", "2026-03-02")]))

    # vendor -> location (sells to) and vendor -> category (supplies)
    sells = {
        "Harbor Foods": [("Downtown Kitchen", 1.0), ("Beachside Grill", 0.8), ("Airport Cafe", 0.4), ("Commissary (CMSY)", 0.5)],
        "Gulfstream Meats": [("Downtown Kitchen", 0.6), ("Beachside Grill", 0.5), ("Commissary (CMSY)", 0.3)],
        "Coastline Produce": [("Downtown Kitchen", 0.5), ("Beachside Grill", 0.5), ("Airport Cafe", 0.2), ("Commissary (CMSY)", 0.2)],
        "Bayview Dairy": [("Downtown Kitchen", 0.4), ("Beachside Grill", 0.3), ("Airport Cafe", 0.2)],
        "Palmetto Beverage": [("Downtown Kitchen", 0.3), ("Beachside Grill", 0.3), ("Airport Cafe", 0.2), ("Commissary (CMSY)", 0.1)],
        "Keystone Paper & Supply": [("Downtown Kitchen", 0.2), ("Beachside Grill", 0.2), ("Airport Cafe", 0.1), ("Commissary (CMSY)", 0.1)],
    }
    for v, locs in sells.items():
        for loc, w in locs:
            L.append(link("ent:vendor:" + v, "ent:location:" + loc, "relates", "sells to", "current", w))
    supplies = {
        "Harbor Foods": ["Proteins", "Dry goods", "Dairy", "Bakery"],
        "Gulfstream Meats": ["Proteins"],
        "Coastline Produce": ["Produce"],
        "Bayview Dairy": ["Dairy"],
        "Palmetto Beverage": ["Beverages"],
        "Keystone Paper & Supply": ["Paper & disposables", "Chemicals"],
    }
    for v, cats in supplies.items():
        for c in cats:
            st = "unconfirmed" if c == "Chemicals" else "current"
            L.append(link("ent:vendor:" + v, "ent:category:" + c, "relates", "supplies", st, 0.7))
    for name, *_ in cols:
        L.append(link("col:Detail.{%s}" % name, "sheet:Detail", "part_of", "", "current", 0.6))
    L += [
        link("col:Detail.{Item #}", "sheet:Price File", "joins_on", "Item # . 97%", "current", 1.0),
        link("col:Detail.{Unit price}", "sheet:Price File", "looks_up", "contract price", "current", 0.8),
        link("sheet:Summary", "sheet:Detail", "derived_from", "pivot", "unconfirmed", 0.9),
        link("sheet:Price File", "link:procurement_contracts.xlsx", "links_file", "Item #", "unconfirmed", 0.6),
        link("metric:spend", "col:Detail.{Ext price}", "derived_from", "sum", "current", 0.8),
        link("metric:credits", "col:Detail.{Ext price}", "derived_from", "negatives", "current", 0.5),
        link("dim:month", "col:Detail.{Invoice date}", "derived_from", "by month", "current", 0.4),
        link("col:Detail.{UOM}", "col:Detail.{Unit price}", "relates", "priced per", "current", 0.5),
        link("ent:vendor:Gulfstream Meats", "col:Detail.{UOM}", "relates", "per lb or per case?", "unconfirmed", 0.5),
        link("col:Detail.{Vendor}", "ent:vendor:Harbor Foods", "relates", "", "current", 0.3),
        link("col:Detail.{Location}", "ent:location:Commissary (CMSY)", "relates", "excluded", "current", 0.3),
        link("col:Detail.{Location}", "ent:location:Downtown Kitchen", "relates", "", "current", 0.3),
    ]
    for v, *_ in vendors:
        L.append(link("ent:vendor:" + v, "col:Detail.{Vendor}", "part_of", "", "current", 0.3))
    for loc, *_ in locations:
        L.append(link("ent:location:" + loc, "col:Detail.{Location}", "part_of", "", "current", 0.3))

    return {
        "title": "purchases_2025.xlsx",
        "subtitle": "Purchase history . 3 tabs . 18,240 rows",
        "sentence": "Dots are vendors, locations and categories. Lines show who supplies what. Bigger means more spend.",
        "generated": TODAY,
        "footer": "Contains data from purchases_2025.xlsx. Local file, no internet. Send the sheet, not this page.",
        "nodes": N,
        "links": L,
        "groups": [
            {"id": "vendor", "label": "Vendors"}, {"id": "location", "label": "Locations"},
            {"id": "category", "label": "Categories"}, {"id": "tab", "label": "Tabs"},
            {"id": "column", "label": "Columns and totals"}, {"id": "item", "label": "Items"},
            {"id": "file", "label": "Other files"},
        ],
        "start_here": [
            {"node": "ent:vendor:Harbor Foods", "text": "Harbor Foods is 38% of spend"},
            {"node": "ent:location:Commissary (CMSY)", "text": "The commissary's lines are transfers, left out of store spend"},
            {"node": "ent:vendor:Gulfstream Meats", "text": "Beef pricing is disputed: per case or per lb?"},
            {"node": "col:Detail.{Item #}", "text": "Item # ties invoice lines to your price file"},
            {"node": "ent:location:Airport Cafe", "text": "Airport Cafe went quiet after August. Closed?"},
        ],
    }


# ---------------------------------------------------------------------------
# Finance (formula flow)
# ---------------------------------------------------------------------------
def finance():
    N, L = [], []
    sheets = [
        ("inputs", "Inputs", "Assumptions . 64 typed cells"),
        ("revenue", "Revenue", "Volume x price by product . 216 formulas"),
        ("pl", "P&L", "Monthly income statement . 412 formulas"),
        ("cf", "Cash Flow", "Indirect method . 288 formulas"),
        ("bs", "Balance Sheet", "Month-end balances . 336 formulas"),
        ("checks", "Checks", "Tie-outs . 36 formulas"),
    ]
    for gid, name, summ in sheets:
        N.append(node("sheet:" + name, name, "sheet", gid, 380, "current", "computed", summ,
                      [note("%s: %s." % (name, summ), "computed", "current")]))

    def block(bid, label, group, size, src="computed", st="current", summ="", notes=None):
        N.append(node("f:" + bid, label, "formula_block", group, size, st, src, summ,
                      notes if notes is not None else [note(summ or label, "computed", "current")]))
        sheet = dict((g, n) for g, n, _ in sheets)[group]
        L.append(link("f:" + bid, "sheet:" + sheet, "part_of", "", "current", 0.4))

    block("growth", "Growth rates", "inputs", 180, "told", "confirmed", "Monthly growth by product",
          [note("Growth rates come from the 2026 budget.", "told", "confirmed", "2026-09-10")])
    block("price", "Price per unit", "inputs", 160, "told", "confirmed", "List price by product",
          [note("Prices went up 4% in January.", "told", "confirmed", "2026-09-10")])
    block("headcount", "Headcount plan", "inputs", 150, "told", "current", "Hires by month and team",
          [note("Headcount plan adds 6 people in Q2.", "told", "current", "2026-09-10")])
    block("taxrate", "Tax rate", "inputs", 120, "told", "disputed", "21% in the model",
          [note("Tax rate is 21%.", "told", "disputed", "2026-01-15"),
           note("The board deck uses a 25% blended rate.", "web", "disputed", "2026-08-30", "board-deck.pdf")])
    block("capexplan", "Capex schedule", "inputs", 130, "told", "current", "Equipment purchases by month")
    block("opening", "Opening balances", "inputs", 140, "computed", "current", "Balances at 2025-12-31")

    block("volume", "Unit volume", "revenue", 300, summ="Units by product and month")
    block("revprod", "Revenue by product", "revenue", 420, summ="Volume x price")
    block("revenue", "Total revenue", "revenue", 640, summ="$18.4M for FY26",
          notes=[note("Total revenue is $18.4M for FY26.", "computed", "current"),
                 note("Revenue grows 3.1% a month on average.", "computed", "current")])

    block("cogs", "COGS", "pl", 380, summ="38% of revenue",
          notes=[note("COGS is a flat 38% of revenue every month.", "computed", "current"),
                 note("The 38% looks like a planning shortcut, not a real cost build.", "inferred", "unconfirmed")])
    block("gp", "Gross profit", "pl", 460, summ="$11.4M, 62% margin")
    block("payroll", "Payroll", "pl", 300, summ="Headcount x loaded cost")
    block("opex", "Opex", "pl", 360, summ="Rent, software, marketing, payroll")
    block("ebitda", "EBITDA", "pl", 520, summ="$3.2M, 17% margin")
    block("da", "D&A", "pl", 180, summ="Straight line over 5 years")
    block("interest", "Interest", "pl", 160, summ="Debt balance x 7.5%")
    block("tax", "Tax expense", "pl", 200, summ="Pre-tax income x Tax rate")
    block("ni", "Net income", "pl", 560, summ="$2.1M for FY26")

    block("wc", "Working capital change", "cf", 220, summ="Change in receivables and payables")
    block("ocf", "Operating cash flow", "cf", 440, summ="Net income plus non-cash, less working capital")
    block("capex", "Capex", "cf", 200, summ="From the capex schedule")
    block("fin", "Financing", "cf", 180, summ="Debt draws and repayments")
    block("endcash", "Ending cash", "cf", 480, summ="$4.6M at 2026-12-31")

    block("cash", "Cash", "bs", 400, summ="Ties to Ending cash")
    block("ar", "Receivables", "bs", 260, summ="45 days of revenue")
    block("ppe", "PP&E", "bs", 240, summ="Gross capex less D&A")
    block("debt", "Debt", "bs", 230, summ="Term loan balance")
    block("equity", "Equity", "bs", 300, summ="Opening equity plus Net income")
    block("assets", "Total assets", "bs", 460, summ="Cash, receivables, PP&E")
    block("le", "Liabilities and equity", "bs", 440, summ="Debt plus equity")

    block("bscheck", "Balance check", "checks", 340, "computed", "confirmed", "Assets minus L&E is 0.00 every month",
          [note("The balance sheet balances every month. Off by 0.00.", "computed", "confirmed")])
    block("cashtie", "Cash tie-out", "checks", 260, "computed", "current", "Ending cash equals BS cash",
          [note("Ending cash on Cash Flow equals Cash on the Balance Sheet in all 12 months.", "computed", "current")])

    # Pattern breaks
    N.append(node("i:pb1", "P&L row 47: typed 18,500", "insight", "pl", 150, "may-be-outdated", "computed",
                  "A typed number where the row's formula belongs (Mar 2026)",
                  [note("Opex row 47, March 2026, holds a typed 18,500 where every other month has a formula.", "computed", "may-be-outdated", "2026-04-02"),
                   note("It may have been a one-off correction that is no longer needed.", "inferred", "unconfirmed")]))
    N.append(node("i:pb2", "Tax: old rate cell", "insight", "pl", 140, "disputed", "inferred",
                  "Tax expense points at Inputs!C14, not the current rate",
                  [note("Tax expense reads Inputs!C14 (21%). The notes say the rate moved to C18 (25%).", "inferred", "disputed")]))
    N.append(node("i:pb3", "Cash Flow row 22: range stops in Nov", "insight", "cf", 130, "may-be-outdated", "computed",
                  "SUM range ends at November, December is left out",
                  [note("Row 22 sums January to November. December is not included.", "computed", "may-be-outdated", "2026-09-12")]))

    flows = [
        ("growth", "volume", "feeds"), ("price", "revprod", "feeds"), ("volume", "revprod", "feeds"),
        ("revprod", "revenue", "feeds"), ("cogs", "revenue", "derived_from"), ("revenue", "gp", "feeds"),
        ("cogs", "gp", "feeds"), ("headcount", "payroll", "feeds"), ("payroll", "opex", "feeds"),
        ("gp", "ebitda", "feeds"), ("opex", "ebitda", "feeds"), ("da", "capexplan", "derived_from"),
        ("ebitda", "ni", "feeds"), ("da", "ni", "feeds"), ("interest", "ni", "feeds"), ("tax", "ni", "feeds"),
        ("taxrate", "tax", "feeds"), ("ebitda", "tax", "feeds"), ("interest", "debt", "looks_up"),
        ("ni", "ocf", "feeds"), ("wc", "ocf", "feeds"), ("wc", "ar", "derived_from"),
        ("ocf", "endcash", "feeds"), ("capex", "endcash", "feeds"), ("fin", "endcash", "feeds"),
        ("opening", "endcash", "feeds"), ("capexplan", "capex", "feeds"), ("endcash", "cash", "feeds"),
        ("capex", "ppe", "feeds"), ("ni", "equity", "feeds"), ("fin", "debt", "feeds"),
        ("revenue", "ar", "feeds"), ("da", "ppe", "feeds"),
        ("cash", "assets", "feeds"), ("ar", "assets", "feeds"), ("ppe", "assets", "feeds"),
        ("debt", "le", "feeds"), ("equity", "le", "feeds"),
        ("assets", "bscheck", "feeds"), ("le", "bscheck", "feeds"),
        ("endcash", "cashtie", "feeds"), ("cash", "cashtie", "feeds"),
    ]
    for a, b, t in flows:
        w = 1.0 if t == "feeds" else 0.7
        st = "unconfirmed" if (a, b) == ("cogs", "revenue") else "current"
        L.append(link("f:" + a, "f:" + b, t, "", st, w))
    L += [
        link("i:pb1", "f:opex", "relates", "breaks the pattern", "current", 0.5),
        link("i:pb2", "f:tax", "relates", "reads the old cell", "current", 0.5),
        link("i:pb2", "f:taxrate", "looks_up", "Inputs!C14", "unconfirmed", 0.5),
        link("i:pb3", "f:ocf", "relates", "short range", "current", 0.5),
    ]
    return {
        "title": "operating_model_fy26.xlsx",
        "subtitle": "Financial model . 6 tabs . 1,482 formulas",
        "sentence": "Dots are labeled blocks of rows. Lines follow the formulas, from inputs through revenue and the P&L to cash and the balance sheet.",
        "generated": TODAY,
        "footer": "Contains data from operating_model_fy26.xlsx. Local file, no internet. Send the sheet, not this page.",
        "nodes": N,
        "links": L,
        "groups": [
            {"id": "inputs", "label": "Inputs"}, {"id": "revenue", "label": "Revenue"},
            {"id": "pl", "label": "P&L"}, {"id": "cf", "label": "Cash Flow"},
            {"id": "bs", "label": "Balance Sheet"}, {"id": "checks", "label": "Checks"},
        ],
        "start_here": [
            {"node": "f:revenue", "text": "Revenue is built from growth, volume and price"},
            {"node": "i:pb1", "text": "One P&L row has a typed number where a formula belongs"},
            {"node": "i:pb2", "text": "Tax rate: the model says 21%, the notes say 25%"},
            {"node": "f:bscheck", "text": "The balance sheet balances every month"},
        ],
    }


# ---------------------------------------------------------------------------
# Hostile strings
# ---------------------------------------------------------------------------
def hostile():
    X = [
        "<img src=x onerror=alert(1)>",
        "javascript:alert(document.cookie)",
        "</script><script>alert('closed early')</script>",
        "\"><svg onload=alert(2)>",
        "<!--<script>alert(3)//",
        "{{constructor.constructor('alert(4)')()}}",
        "data:text/html,<script>alert(5)</script>",
        "<a href=\"javascript:alert(6)\">click me</a>",
        "<iframe src=\"https://evil.example/\"></iframe>",
        "\u202eevil.exe\u202c right-to-left override",
        "line\u2028separator and para\u2029separator",
        "<style>body{display:none}</style>",
    ]
    N = []
    for i, s in enumerate(X):
        N.append({
            "id": "h:%d:%s" % (i, s),
            "label": s,
            "type": ["entity", "constructor", "__proto__", "sheet"][i % 4],
            "group": ["<b>vendor</b>", "red;background:url(https://evil.example/x.png)", "__proto__", "toString"][i % 4],
            "size": [12, "1e400", None, "NaN", -5, 3.5][i % 6],
            "status": ["disputed", "constructor", "may-be-outdated", "<img src=x onerror=alert(7)>"][i % 4],
            "source": ["told", "inferred", "hasOwnProperty", "web"][i % 4],
            "records": False,
            "summary": "<b>bold?</b> " + s,
            "name": "<img src=x onerror=alert('tooltip')>",
            "notes": [
                {"statement": s, "source": "told", "status": "confirmed", "as_of": "<img src=x onerror=alert(8)>", "said_by": "<script>alert(9)</script>"},
                {"statement": "<a href=\"javascript:alert(10)\">a link that must stay text</a>", "source": "web", "status": "current", "as_of": TODAY, "said_by": "javascript:alert(11)"},
            ],
        })
    N.append({"id": "__proto__", "label": "__proto__", "type": "entity", "group": "constructor", "size": 5,
              "status": "current", "source": "computed", "notes": "not a list"})
    N.append({"id": "h:dup", "label": "first copy wins", "size": 4})
    N.append({"id": "h:dup", "label": "<img src=x onerror=alert('dup')>", "size": 400})
    N.append({"id": "h:long", "label": "L" * 600 + "<img src=x onerror=alert(12)>", "size": 8,
              "summary": "W" * 3000, "notes": [{"statement": "x" * 5000}]})
    N.append("not an object")
    N.append(None)
    ids = [n["id"] for n in N if isinstance(n, dict)]
    L = []
    for i in range(len(X)):
        L.append({"source": ids[i], "target": ids[(i + 1) % len(X)],
                  "type": ["feeds", "__proto__", "constructor", "looks_up"][i % 4],
                  "label": "<img src=x onerror=alert(13)>", "status": "unconfirmed" if i % 2 else "current",
                  "weight": ["heavy", 1, None, 1e308][i % 4]})
    L.append({"source": "__proto__", "target": ids[0], "type": "relates", "label": "</script>"})
    L.append({"source": "h:missing", "target": ids[0], "type": "feeds"})
    L.append({"source": ids[1], "target": ids[1], "type": "feeds", "label": "self loop"})
    L.append({"source": "h:long", "target": ids[2], "type": "same_as"})
    return {
        "title": "<script>alert('title')</script>evil.xlsx",
        "subtitle": "<img src=x onerror=alert('subtitle')>",
        "sentence": "<b>Dots</b> are <i>markup</i> that must render as text. javascript:alert(14)",
        "generated": "<img src=x onerror=alert(15)>",
        "footer": "<a href=\"https://evil.example\">evil link</a></script><script>alert(16)</script>",
        "nodes": N,
        "links": L,
        "groups": [
            {"id": "<b>vendor</b>", "label": "<img src=x onerror=alert(17)>"},
            {"id": "__proto__", "label": "</script><script>alert(18)</script>"},
            "junk",
        ],
        "start_here": [
            {"node": ids[0], "text": "<img src=x onerror=alert(19)>"},
            {"node": ids[2], "text": "</script><script>alert(20)</script>"},
            {"node": "h:missing", "text": "points at nothing"},
            {"node": "__proto__"},
        ],
    }


# ---------------------------------------------------------------------------
# Large graph (performance)
# ---------------------------------------------------------------------------
def large(n_total=2000, seed=7):
    rnd = random.Random(seed)
    N, L = [], []
    vendors = ["Vendor %02d" % i for i in range(14)]
    locations = ["Store %02d" % i for i in range(12)]
    categories = ["Category %02d" % i for i in range(16)]
    for i, v in enumerate(vendors):
        N.append(node("ent:vendor:" + v, v, "entity", "vendor", rnd.randint(200, 2000), "current", "computed", "vendor",
                      [note("%s supplies part of the catalog." % v, "computed", "current")]))
    for loc in locations:
        N.append(node("ent:location:" + loc, loc, "entity", "location", rnd.randint(300, 1500), "current", "computed", "location"))
    for c in categories:
        N.append(node("ent:category:" + c, c, "entity", "category", rnd.randint(100, 900),
                      rnd.choice(["current", "current", "may-be-outdated"]), rnd.choice(["computed", "inferred"]), "category"))
    for s in ("Detail", "Price File", "Summary"):
        N.append(node("sheet:" + s, s, "sheet", "tab", 500, "current", "computed", "tab"))
    for c in ("Item #", "Vendor", "Location", "Unit price", "Ext price", "UOM"):
        N.append(node("col:Detail.{%s}" % c, c, "column", "column", 300, "current", "computed", "column"))
        L.append(link("col:Detail.{%s}" % c, "sheet:Detail", "part_of"))
    L.append(link("col:Detail.{Item #}", "sheet:Price File", "joins_on", "Item # . 97%"))
    L.append(link("sheet:Summary", "sheet:Detail", "derived_from"))
    for v in vendors:
        for loc in rnd.sample(locations, 5):
            L.append(link("ent:vendor:" + v, "ent:location:" + loc, "relates", "sells to", "current", rnd.random()))
        for c in rnd.sample(categories, 3):
            L.append(link("ent:vendor:" + v, "ent:category:" + c, "relates", "supplies", "current", rnd.random()))
    i = 0
    while len(N) < n_total:
        i += 1
        v = rnd.choice(vendors)
        c = rnd.choice(categories)
        rid = "ent:item:%05d" % (10000 + i)
        N.append(node(rid, "Item %05d" % (10000 + i), "record", "item", rnd.randint(1, 120),
                      rnd.choice(["current"] * 8 + ["may-be-outdated", "disputed"]),
                      rnd.choice(["computed"] * 6 + ["inferred"]), "record", [], records=True))
        L.append(link("ent:vendor:" + v, rid, "relates", "sells", "current", 0.2))
        L.append(link(rid, "ent:category:" + c, "part_of", "", "current", 0.2))
    return {
        "title": "big_purchases.xlsx",
        "subtitle": "Performance check . %d dots" % len(N),
        "sentence": "Dots are vendors, stores, categories and items. Turn on Show records to see every item.",
        "generated": TODAY,
        "footer": "Generated test graph.",
        "nodes": N,
        "links": L,
        "groups": [{"id": "vendor", "label": "Vendors"}, {"id": "location", "label": "Stores"},
                   {"id": "category", "label": "Categories"}, {"id": "tab", "label": "Tabs"},
                   {"id": "column", "label": "Columns"}, {"id": "item", "label": "Items"}],
        "start_here": [{"node": "ent:vendor:Vendor 00", "text": "Vendor 00 supplies part of the catalog"}],
    }


def write(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print("wrote", path, "(%d nodes, %d links)" % (len(data["nodes"]), len(data["links"])))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--large-out", default=None, help="where to write the 2,000-dot graph (skipped if absent)")
    args = ap.parse_args()
    write(DEV / "sample_graph_procurement.json", procurement())
    write(DEV / "sample_graph_finance.json", finance())
    write(DEV / "sample_graph_hostile.json", hostile())
    if args.large_out:
        write(Path(args.large_out), large())


if __name__ == "__main__":
    main()
