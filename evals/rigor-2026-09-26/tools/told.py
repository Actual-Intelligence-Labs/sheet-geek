"""Print the owner notes (source told) in each saved workbook's _brain tab, as JSON.
Usage: python told.py <file.xlsx> [...]"""
import json
import os
import sys

sys.path.insert(0, os.path.expanduser("~/lab/spreadsheet-brain/skills/spreadsheet-brain/scripts"))
from sheetbrain import brainzip  # noqa: E402

out = {}
for p in sys.argv[1:]:
    recs, _w, info = brainzip.read_brain(p)
    told = [{"label": r.get("label", ""), "statement": r.get("statement", ""), "said_by": r.get("said_by", "")}
            for r in recs if r.get("source") == "told"]
    out[os.path.basename(p)] = {"brain_tab": bool(info.get("present")), "rows": len(recs),
                                "told_count": len(told), "told": told}
print(json.dumps(out, indent=1))
