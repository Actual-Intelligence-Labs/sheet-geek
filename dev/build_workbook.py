"""Step 1: build a realistic test workbook with xlsxwriter.

Three data sheets (Vendors, Products, Orders) joined by lookup formulas, plus
every feature that commonly breaks when a Python library re-saves a file:
chart, floating image, in-cell image, named ranges, data validation lists,
conditional formatting (cell rule + 2010 data bar), sparkline, frozen pane,
merged header, comment, textbox shape, Excel Table, custom doc property.

Formula cells get real cached values (computed here) so we can see whether a
re-save keeps them.
"""
import datetime as dt
import pathlib
import random

import xlsxwriter
from PIL import Image, ImageDraw

HERE = pathlib.Path(__file__).resolve().parent
OUT = HERE / "out"
OUT.mkdir(exist_ok=True)
random.seed(7)

# --- a small PNG logo to embed ---------------------------------------------
logo = OUT / "logo.png"
img = Image.new("RGB", (120, 48), (20, 20, 20))
d = ImageDraw.Draw(img)
d.rectangle([4, 4, 115, 43], outline=(45, 104, 255), width=3)
d.text((14, 16), "TEST LOGO", fill=(240, 240, 240))
img.save(logo)

vendors = [
    ("V001", "Gulf Linen Supply", "Linens", "Net 30"),
    ("V002", "Suncoast Produce", "Food", "Net 15"),
    ("V003", "Keys Coffee Roasters", "Beverage", "Net 30"),
    ("V004", "Bayside Chemical", "Janitorial", "Net 45"),
    ("V005", "Harbor Paper Co", "Paper Goods", "Net 30"),
    ("V006", "Mangrove Meats", "Food", "Net 7"),
    ("V007", "Tidewater Glassware", "Tabletop", "Net 60"),
    ("V008", "Palm Uniforms", "Uniforms", "Net 30"),
]
products = [
    ("SKU-100", "King sheet set", "V001", 42.50),
    ("SKU-101", "Bath towel", "V001", 6.25),
    ("SKU-102", "Romaine case", "V002", 31.00),
    ("SKU-103", "Citrus case", "V002", 27.40),
    ("SKU-104", "Espresso beans 5lb", "V003", 58.00),
    ("SKU-105", "Decaf beans 5lb", "V003", 61.00),
    ("SKU-106", "Floor cleaner 5gal", "V004", 44.90),
    ("SKU-107", "Sanitizer tabs", "V004", 18.75),
    ("SKU-108", "Napkins 1000ct", "V005", 22.10),
    ("SKU-109", "To-go cups 500ct", "V005", 35.60),
    ("SKU-110", "Ribeye case", "V006", 289.00),
    ("SKU-111", "Chicken breast case", "V006", 96.50),
    ("SKU-112", "Wine glass 24ct", "V007", 74.00),
    ("SKU-113", "Rocks glass 36ct", "V007", 63.20),
    ("SKU-114", "Server apron", "V008", 11.40),
]
properties = ["Bayfront Hotel", "Lido Resort", "Siesta Inn"]
vendor_name = {v[0]: v[1] for v in vendors}
prod = {p[0]: p for p in products}

orders = []
start = dt.date(2026, 7, 1)
for i in range(40):
    sku = random.choice(products)[0]
    qty = random.randint(1, 30)
    orders.append((f"PO-{2600 + i}", start + dt.timedelta(days=i * 2),
                   random.choice(properties), sku, qty))

wb = xlsxwriter.Workbook(OUT / "original.xlsx")
wb.set_properties({"title": "Q3 procurement test workbook",
                   "author": "spike", "company": "AIL spike",
                   "created": dt.datetime(2026, 9, 25, 9, 0, 0)})
wb.set_custom_property("Department", "Procurement")

bold = wb.add_format({"bold": True, "bg_color": "#DDE6FF", "border": 1})
title = wb.add_format({"bold": True, "font_size": 14, "align": "center",
                       "valign": "vcenter", "bg_color": "#2D68FF",
                       "font_color": "#FFFFFF"})
money = wb.add_format({"num_format": "$#,##0.00"})
date_f = wb.add_format({"num_format": "yyyy-mm-dd"})
red = wb.add_format({"bg_color": "#FFC7CE", "font_color": "#9C0006"})

# --- Vendors: an Excel Table + floating image + in-cell image --------------
ws_v = wb.add_worksheet("Vendors")
ws_v.add_table(0, 0, len(vendors), 3, {
    "name": "tblVendors", "style": "Table Style Medium 2",
    "columns": [{"header": h} for h in
                ("VendorID", "VendorName", "Category", "PaymentTerms")],
    "data": [list(v) for v in vendors]})
ws_v.set_column("A:A", 10)
ws_v.set_column("B:B", 24)
ws_v.set_column("C:D", 14)
ws_v.insert_image("F2", str(logo))                 # floating (drawing part)
ws_v.write("F8", "In-cell logo:")
ws_v.embed_image("G8", str(logo))                  # Place-in-cell (richData)

# --- Products: cross-sheet INDEX/MATCH lookup into Vendors -----------------
ws_p = wb.add_worksheet("Products")
for c, h in enumerate(("SKU", "ProductName", "VendorID", "UnitCost",
                       "VendorName")):
    ws_p.write(0, c, h, bold)
for r, (sku, name, vid, cost) in enumerate(products, start=1):
    ws_p.write(r, 0, sku)
    ws_p.write(r, 1, name)
    ws_p.write(r, 2, vid)
    ws_p.write_number(r, 3, cost, money)
    ws_p.write_formula(
        r, 4, f"=INDEX(Vendors!$B$2:$B$9,MATCH(C{r+1},Vendors!$A$2:$A$9,0))",
        None, vendor_name[vid])
ws_p.set_column("A:A", 10)
ws_p.set_column("B:B", 22)
ws_p.set_column("E:E", 24)
# VendorID column restricted to the Vendors table ids via a named range
ws_p.data_validation(1, 2, len(products), 2,
                     {"validate": "list", "source": "=VendorIDs"})

# --- Orders: merged header, frozen pane, lookups, CF, chart, sparkline -----
ws_o = wb.add_worksheet("Orders")
ws_o.merge_range("A1:I1", "Q3 Purchase Orders (all properties)", title)
ws_o.set_row(0, 24)
heads = ("OrderID", "OrderDate", "Property", "SKU", "Qty", "UnitCost",
         "Total", "Vendor", "TotalWithTax")
for c, h in enumerate(heads):
    ws_o.write(1, c, h, bold)
ws_o.freeze_panes(2, 0)
totals = []
for i, (oid, odate, prop_, sku, qty) in enumerate(orders):
    r = i + 2
    x = r + 1
    cost = prod[sku][3]
    total = round(qty * cost, 2)
    totals.append(total)
    ws_o.write(r, 0, oid)
    ws_o.write_datetime(r, 1, dt.datetime.combine(odate, dt.time()), date_f)
    ws_o.write(r, 2, prop_)
    ws_o.write(r, 3, sku)
    ws_o.write_number(r, 4, qty)
    ws_o.write_formula(r, 5, f"=VLOOKUP(D{x},Products!$A$2:$D$16,4,FALSE)",
                       money, cost)
    ws_o.write_formula(r, 6, f"=E{x}*F{x}", money, total)
    ws_o.write_formula(
        r, 7,
        f"=INDEX(Vendors!$B$2:$B$9,MATCH(VLOOKUP(D{x},Products!$A$2:$C$16,3,FALSE),Vendors!$A$2:$A$9,0))",
        None, vendor_name[prod[sku][2]])
    ws_o.write_formula(r, 8, f"=G{x}*(1+TaxRate)", money,
                       round(total * 1.07, 4))
last = len(orders) + 2  # 1-based last data row
ws_o.set_column("A:A", 10)
ws_o.set_column("B:B", 11)
ws_o.set_column("C:C", 16)
ws_o.set_column("F:G", 11)
ws_o.set_column("H:H", 22)
ws_o.set_column("I:I", 13)
ws_o.data_validation(f"C3:C{last}", {"validate": "list",
                                      "source": properties})
ws_o.data_validation(f"D3:D{last}", {"validate": "list",
                                      "source": "=SKUList"})
ws_o.conditional_format(f"G3:G{last}", {"type": "cell", "criteria": ">",
                                         "value": 500, "format": red})
ws_o.conditional_format(f"E3:E{last}", {"type": "data_bar",
                                         "bar_color": "#2D68FF",
                                         "bar_solid": True})  # x14 ext
ws_o.write_comment("G2", "Total = Qty x UnitCost, pre-tax.")
ws_o.write("K1", "Qty trend")
ws_o.add_sparkline("L1", {"range": f"Orders!E3:E{last}", "type": "column"})
ws_o.insert_textbox("K20", "Totals exclude freight.",
                    {"width": 220, "height": 40})
ws_o.write_url("K24", "https://example.com/po-policy", string="PO policy")

chart = wb.add_chart({"type": "column"})
chart.add_series({"name": "Order total",
                  "categories": f"=Orders!$A$3:$A${last}",
                  "values": f"=Orders!$G$3:$G${last}",
                  "fill": {"color": "#2D68FF"}})
chart.set_title({"name": "Order totals, Q3"})
chart.set_legend({"none": True})
ws_o.insert_chart("K3", chart, {"x_scale": 1.3, "y_scale": 1.1})

# --- named ranges ----------------------------------------------------------
wb.define_name("VendorIDs", "=Vendors!$A$2:$A$9")
wb.define_name("SKUList", "=Products!$A$2:$A$16")
wb.define_name("TaxRate", "=0.07")
wb.define_name("Orders!LocalNote", '="sheet-scoped name"')  # localSheetId

ws_o.activate()
wb.close()
print(f"wrote {OUT / 'original.xlsx'}  orders={len(orders)} "
      f"sum_total={sum(totals):.2f}")
