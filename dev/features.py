"""Zip-level feature detector: what a workbook still contains, read straight
from the OOXML parts (independent of any library's partial object model)."""
import re
import zipfile


def _sheets_xml(parts):
    return {n: v.decode("utf-8", "replace") for n, v in parts.items()
            if re.match(r"xl/worksheets/sheet\d+\.xml$", n)}


def detect(path: str) -> dict:
    with zipfile.ZipFile(path) as zf:
        parts = {i.filename: zf.read(i) for i in zf.infolist()}
    sheets = _sheets_xml(parts)
    allsheets = "".join(sheets.values())
    drawings = "".join(v.decode("utf-8", "replace") for n, v in parts.items()
                       if re.match(r"xl/drawings/drawing\d+\.xml$", n))
    wb = parts.get("xl/workbook.xml", b"").decode("utf-8", "replace")
    names = re.findall(r'<definedName[^>]*name="([^"]+)"', wb)
    brain_state = None
    m = re.search(r'<sheet [^>]*name="_brain"[^>]*/>', wb)
    if m:
        st = re.search(r'state="(\w+)"', m.group(0))
        brain_state = st.group(1) if st else "visible"
    f_cells = re.findall(r"<c [^>]*>(<f[^>]*>.*?</f>|<f[^>]*/>)(<v>[^<]*</v>)?",
                         allsheets)
    return {
        "parts": len(parts),
        "chart_parts": sorted(n for n in parts if re.match(r"xl/charts/chart\d+\.xml$", n)),
        "chart_is_bar": any(b"barChart" in v for n, v in parts.items() if "charts/chart" in n),
        "media": sorted(n for n in parts if n.startswith("xl/media/")),
        "floating_pic_in_drawing": bool(re.search(r"<(\w+:)?pic>", drawings)),
        "textbox_shape": bool(re.search(r"<(\w+:)?sp[ >]", drawings))
                         and bool(re.search(r"<(\w+:)?txBody>", drawings)),
        "in_cell_image_richdata": any(n.startswith("xl/richData/") for n in parts)
                                    and ' vm="' in allsheets,
        "sparkline": "sparklineGroup" in allsheets,
        "databar_x14_ext": "x14:dataBar" in allsheets,
        "databar_any": "<dataBar" in allsheets,
        "cf_cellIs": 'type="cellIs"' in allsheets,
        "data_validations": allsheets.count("<dataValidation "),
        "defined_names": names,
        "frozen_pane": 'state="frozen"' in allsheets,
        "merged_header_A1_I1": 'ref="A1:I1"' in allsheets,
        "comments_part": any(re.match(r"xl/comments\d*\.xml$", n) or
                             "comments" in n.lower() and n.endswith(".xml")
                             for n in parts),
        "table_part": any(n.startswith("xl/tables/") for n in parts),
        "hyperlink": "<hyperlink " in allsheets,
        "formula_cells": len(f_cells),
        "formula_cells_with_cached_value": sum(1 for f, v in f_cells if v and v != "<v></v>"),
        "custom_docprop": b"Department" in parts.get("docProps/custom.xml", b""),
        "brain_sheet_state": brain_state,
        "shared_strings_part": "xl/sharedStrings.xml" in parts,
    }
