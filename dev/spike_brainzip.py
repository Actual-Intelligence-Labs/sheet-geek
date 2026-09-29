"""Surgical read/write of a hidden `_brain` worksheet inside an .xlsx/.xlsm.

Design rules (the point of the spike):
  * Never re-save the workbook through a library. Copy every zip entry's
    decompressed bytes through untouched; only splice bytes into the 3-4 parts
    that must know about the new sheet.
  * Never parse-and-reserialize XML we did not author (ElementTree rewrites
    namespace prefixes and breaks mc:Ignorable). Edits are byte insertions at
    well-known anchors; every edit is checked to be a pure insertion.
  * The brain sheet uses inline strings, so sharedStrings.xml is never touched.
  * Append the sheet LAST in <sheets> so positional indices (localSheetId in
    definedNames, activeTab, firstSheet) stay valid.
  * Brain text is data. Writer strips invisible/illegal characters and
    neutralises leading formula characters; reader does the same on the way in.

Stdlib only (zipfile, re, hashlib) plus defusedxml when available for reads.
"""
from __future__ import annotations

import hashlib
import io
import os
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field
from xml.sax.saxutils import escape as _xml_escape

try:  # XML-bomb-safe parsing for READS only (we never re-serialize)
    from defusedxml import ElementTree as SafeET
except ImportError:  # pragma: no cover
    import xml.etree.ElementTree as SafeET  # noqa: N812

BRAIN_SHEET = "_brain"
FORMAT_LABEL = "spreadsheet-brain 0.1 | record"   # A1: neutral, not an instruction
COLUMNS = [FORMAT_LABEL, "id", "kind", "label", "from", "to", "source",
           "status", "learned_at", "said_by", "depends_on", "part", "text"]
CELL_LIMIT = 32767            # Excel max chars per cell, counted in UTF-16 units
CHUNK = 32000                 # margin under the limit

NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS_STRICT = "http://purl.oclc.org/ooxml/spreadsheetml/main"
NS_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS_PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
REL_WORKSHEET = ("http://schemas.openxmlformats.org/officeDocument/2006/"
                 "relationships/worksheet")
CT_WORKSHEET = ("application/vnd.openxmlformats-officedocument."
                "spreadsheetml.worksheet+xml")

MAX_UNCOMPRESSED = 2 * 1024 ** 3   # zip-bomb guard: 2 GiB total
MAX_RATIO = 200                     # per-entry compression ratio guard


class BrainError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# text hygiene
# --------------------------------------------------------------------------
_ILLEGAL_XML = re.compile(
    "[\x00-\x08\x0B\x0C\x0E-\x1F\ufffe\uffff]|[\ud800-\udfff]")
_INVISIBLE = re.compile(
    "[\U000E0000-\U000E007F"      # Unicode Tags (invisible prompt smuggling)
    "\u202a-\u202e\u2066-\u2069"  # bidi overrides / isolates (Trojan Source)
    "\u200b\u2060\ufeff]")        # zero-width space, word joiner, BOM
_OOXML_ESCAPE = re.compile(r"_(x[0-9A-Fa-f]{4}_)")
_FORMULA_LEAD = ("=", "+", "-", "@", "\t", "\r")


def clean_text(s: str) -> tuple[str, list[str]]:
    """Strip characters a person cannot see but a model can read.

    Returns (clean, warnings). Used on write AND on read.
    """
    warnings = []
    if _INVISIBLE.search(s):
        warnings.append(f"stripped {len(_INVISIBLE.findall(s))} invisible chars")
        s = _INVISIBLE.sub("", s)
    if _ILLEGAL_XML.search(s):
        warnings.append("stripped XML-illegal control chars")
        s = _ILLEGAL_XML.sub("", s)
    return s, warnings


def normalize_text(s: str) -> str:
    """Canonical brain text: invisible/illegal chars stripped, newlines LF.
    (CR must not reach the file: XML normalises it away, and the OOXML
    _x000D_ escape is decoded by some readers and not others.)"""
    s, _ = clean_text(s)
    return s.replace("\r\n", "\n").replace("\r", "\n")


def neutralise_formula_lead(s: str) -> str:
    """Brain text must never start like a formula if some tool exports it to
    CSV later (OWASP CSV injection). Inline strings are safe inside xlsx."""
    # also escape a leading apostrophe so the reader can always strip one
    return "'" + s if s.startswith(_FORMULA_LEAD + ("'",)) else s


def ooxml_escape(s: str) -> str:
    """Escape for a <t> element: XML entities, protect literal _xHHHH_
    sequences (Excel/openpyxl decode them), encode CR which XML would
    normalise away."""
    s = _OOXML_ESCAPE.sub(r"_x005F_\1", s)
    s = s.replace("\r", "_x000D_")
    return _xml_escape(s)


def utf16_len(s: str) -> int:
    return len(s.encode("utf-16-le")) // 2


def split_utf16(s: str, limit: int | None = None) -> list[str]:
    """Split so each piece is <= limit UTF-16 units (Excel's unit) and no
    surrogate pair or escape is cut."""
    limit = CHUNK if limit is None else limit   # read the global at call time
    if utf16_len(s) <= limit:
        return [s]
    out, cur, cur_len = [], [], 0
    for ch in s:
        w = 2 if ord(ch) > 0xFFFF else 1
        if cur_len + w > limit:
            out.append("".join(cur))
            cur, cur_len = [], 0
        cur.append(ch)
        cur_len += w
    if cur:
        out.append("".join(cur))
    return out


# --------------------------------------------------------------------------
# records -> rows (chunking) and rows -> records (reassembly)
# --------------------------------------------------------------------------
def records_to_rows(records: list[dict]) -> list[list[str]]:
    rows = [list(COLUMNS)]
    for rec in records:
        text = normalize_text(rec.get("text", "") or "")
        pieces = split_utf16(text)
        for i, piece in enumerate(pieces, 1):
            row = []
            for col in COLUMNS:
                if col == FORMAT_LABEL:
                    v = rec.get("record", "")
                elif col == "part":
                    v = f"{i}/{len(pieces)}" if len(pieces) > 1 else ""
                elif col == "text":
                    v = piece
                else:
                    v = rec.get(col, "")
                row.append(neutralise_formula_lead(normalize_text(str(v))))
            rows.append(row)
    return rows


def rows_to_records(rows: list[list[str]]) -> list[dict]:
    """Inverse of records_to_rows. Tolerates missing trailing cells."""
    if not rows:
        return []
    head = rows[0]
    recs: list[dict] = []
    pending: dict[str, dict] = {}
    for r in rows[1:]:
        r = list(r) + [""] * (len(head) - len(r))
        d = {("record" if h == FORMAT_LABEL else h): (v or "")
             for h, v in zip(head, r)}
        for k, v in d.items():
            if isinstance(v, str) and v.startswith("'"):
                d[k] = v[1:]
        part = d.pop("part", "")
        if part:
            i, n = (int(x) for x in part.split("/"))
            key = f"{d['record']}|{d['id']}"
            if i == 1:
                pending[key] = d
            else:
                pending[key]["text"] += d["text"]
            if i == n:
                recs.append(pending.pop(key))
        else:
            recs.append(d)
    if pending:
        raise BrainError(f"incomplete multi-part records: {list(pending)}")
    return recs


# --------------------------------------------------------------------------
# sheet XML
# --------------------------------------------------------------------------
def _col_letter(i: int) -> str:
    s = ""
    i += 1
    while i:
        i, rem = divmod(i - 1, 26)
        s = chr(65 + rem) + s
    return s


def build_sheet_xml(rows: list[list[str]]) -> bytes:
    """A minimal, schema-ordered worksheet using inline strings only."""
    ncols = max(len(r) for r in rows)
    for r in rows:
        for v in r:
            if utf16_len(v) > CELL_LIMIT:
                raise BrainError("cell over 32,767 chars; chunk first")
    parts = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n',
        f'<worksheet xmlns="{NS_MAIN}" xmlns:r="{NS_R}">',
        f'<dimension ref="A1:{_col_letter(ncols - 1)}{len(rows)}"/>',
        '<sheetViews><sheetView workbookViewId="0"/></sheetViews>',
        '<sheetFormatPr defaultRowHeight="15"/>',
        '<cols><col min="1" max="12" width="16" customWidth="1"/>'
        '<col min="13" max="13" width="100" customWidth="1"/></cols>',
        "<sheetData>",
    ]
    for ri, row in enumerate(rows, 1):
        parts.append(f'<row r="{ri}">')
        for ci, v in enumerate(row):
            if v == "":
                continue
            sp = ' xml:space="preserve"' if v != v.strip() or "\n" in v else ""
            parts.append(f'<c r="{_col_letter(ci)}{ri}" t="inlineStr"><is>'
                         f'<t{sp}>{ooxml_escape(v)}</t></is></c>')
        parts.append("</row>")
    parts.append("</sheetData>")
    parts.append('<pageMargins left="0.7" right="0.7" top="0.75" '
                 'bottom="0.75" header="0.3" footer="0.3"/>')
    parts.append("</worksheet>")
    return "".join(parts).encode("utf-8")


# --------------------------------------------------------------------------
# package inspection
# --------------------------------------------------------------------------
@dataclass
class SheetRef:
    name: str
    sheet_id: int
    rid: str
    state: str
    part: str


@dataclass
class Package:
    infos: list[zipfile.ZipInfo]
    data: dict[str, bytes]
    comment: bytes
    workbook_part: str
    sheets: list[SheetRef] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _resolve(base_dir: str, target: str) -> str:
    if target.startswith("/"):
        return target.lstrip("/")
    parts = (base_dir + "/" + target).split("/") if base_dir else target.split("/")
    out = []
    for p in parts:
        if p == "..":
            out.pop()
        elif p and p != ".":
            out.append(p)
    return "/".join(out)


def load_package(path: str | os.PathLike) -> Package:
    with open(path, "rb") as fh:
        head = fh.read(8)
    if head.startswith(b"\xd0\xcf\x11\xe0"):
        raise BrainError("OLE/CFB container: password-encrypted xlsx or "
                         "legacy .xls. Refusing.")
    if not head.startswith(b"PK"):
        raise BrainError("not a zip package")
    with zipfile.ZipFile(path) as zf:
        infos = zf.infolist()
        total = sum(i.file_size for i in infos)
        if total > MAX_UNCOMPRESSED:
            raise BrainError(f"uncompressed size {total} over guard")
        for i in infos:
            if i.compress_size and i.file_size / i.compress_size > MAX_RATIO \
                    and i.file_size > 10 * 1024 ** 2:
                raise BrainError(f"suspicious compression ratio: {i.filename}")
        names_lower = [i.filename.lower() for i in infos]
        if len(set(names_lower)) != len(names_lower):
            raise BrainError("duplicate part names (case-insensitive)")
        data = {i.filename: zf.read(i) for i in infos}
        comment = zf.comment
    # office document relationship -> workbook part
    root_rels = SafeET.fromstring(data["_rels/.rels"])
    wb_part = None
    for rel in root_rels:
        if rel.get("Type", "").endswith("/officeDocument"):
            wb_part = _resolve("", rel.get("Target"))
    if not wb_part or wb_part not in data:
        raise BrainError("no workbook part")
    pkg = Package(infos, data, comment, wb_part)
    wb_xml = data[wb_part]
    if NS_STRICT.encode() in wb_xml[:2000]:
        raise BrainError("Strict OOXML (ISO 29500 strict) not supported")
    wb = SafeET.fromstring(wb_xml)
    rels_part = _rels_part_for(wb_part)
    rels = SafeET.fromstring(data[rels_part])
    wb_dir = wb_part.rsplit("/", 1)[0]
    rid_to_part = {r.get("Id"): _resolve(wb_dir, r.get("Target"))
                   for r in rels if r.get("TargetMode") != "External"}
    for s in wb.iter(f"{{{NS_MAIN}}}sheet"):
        rid = s.get(f"{{{NS_R}}}id")
        pkg.sheets.append(SheetRef(s.get("name"), int(s.get("sheetId")), rid,
                                   s.get("state", "visible"),
                                   rid_to_part.get(rid, "")))
    prot = wb.find(f"{{{NS_MAIN}}}workbookProtection")
    if prot is not None and prot.get("lockStructure") in ("1", "true"):
        pkg.warnings.append("workbook structure is protected: adding a sheet "
                            "violates the owner's lock; ask first")
    if any(n.startswith("_xmlsignatures/") for n in data):
        pkg.warnings.append("digitally signed: any change invalidates the "
                            "signature")
    return pkg


def _rels_part_for(part: str) -> str:
    d, f = part.rsplit("/", 1) if "/" in part else ("", part)
    return f"{d}/_rels/{f}.rels" if d else f"_rels/{f}.rels"


# --------------------------------------------------------------------------
# byte-level splices (each asserted to be a pure insertion)
# --------------------------------------------------------------------------
def _insert_before(buf: bytes, anchor: bytes, ins: bytes) -> bytes:
    idx = buf.rfind(anchor)
    if idx < 0:
        raise BrainError(f"anchor {anchor!r} not found")
    return buf[:idx] + ins + buf[idx:]


def _insert_before_regex(buf: bytes, pattern: bytes, ins: bytes) -> bytes:
    m = list(re.finditer(pattern, buf))
    if not m:
        raise BrainError(f"anchor {pattern!r} not found")
    idx = m[-1].start()
    return buf[:idx] + ins + buf[idx:]


def _prefix(buf: bytes, ns: str) -> bytes:
    """Return the prefix (b'' for default) bound to namespace ns in buf's
    root element, so inserted elements use whatever prefix the file uses."""
    root = re.search(rb"<([A-Za-z_][\w.-]*:)?workbook\b[^>]*>|"
                     rb"<([A-Za-z_][\w.-]*:)?Relationships\b[^>]*>|"
                     rb"<([A-Za-z_][\w.-]*:)?Types\b[^>]*>", buf)
    tag = root.group(0)
    for m in re.finditer(rb'xmlns(?::([\w.-]+))?="([^"]+)"', tag):
        if m.group(2).decode() == ns:
            return (m.group(1) + b":") if m.group(1) else b""
    raise BrainError(f"namespace {ns} not declared on root")


def _add_to_workbook_xml(buf: bytes, name: str, sheet_id: int, rid: str,
                         state: str) -> tuple[bytes, bytes]:
    p = _prefix(buf, NS_MAIN)
    r = _prefix(buf, NS_R)
    if not r:
        raise BrainError("relationships namespace has no prefix")
    ins = (b"<" + p + b'sheet name="' + _xml_escape(name).encode() +
           b'" sheetId="' + str(sheet_id).encode() + b'" state="' +
           state.encode() + b'" ' + r + b'id="' + rid.encode() + b'"/>')
    return _insert_before(buf, b"</" + p + b"sheets>", ins), ins


def _add_rel(buf: bytes, rid: str, target: str) -> tuple[bytes, bytes]:
    p = _prefix(buf, NS_PKG_REL)
    ins = (b"<" + p + b'Relationship Id="' + rid.encode() + b'" Type="' +
           REL_WORKSHEET.encode() + b'" Target="' + target.encode() + b'"/>')
    return _insert_before(buf, b"</" + p + b"Relationships>", ins), ins


def _add_override(buf: bytes, part: str) -> tuple[bytes, bytes]:
    ns = "http://schemas.openxmlformats.org/package/2006/content-types"
    p = _prefix(buf, ns)
    ins = (b"<" + p + b'Override PartName="/' + part.encode() +
           b'" ContentType="' + CT_WORKSHEET.encode() + b'"/>')
    return _insert_before(buf, b"</" + p + b"Types>", ins), ins


def _add_to_app_xml(buf: bytes, name: str) -> bytes | None:
    """Best effort: bump the Worksheets count and add the title. Returns None
    (leave app.xml untouched) if the layout is not the common one. app.xml is
    informational; Excel rewrites it on save."""
    m = re.search(rb"<vt:lpstr>Worksheets</vt:lpstr></vt:variant>\s*"
                  rb"<vt:variant><vt:i4>(\d+)</vt:i4>", buf)
    t = re.search(rb'<TitlesOfParts><vt:vector size="(\d+)" baseType="lpstr">',
                  buf)
    if not m or not t:
        return None
    # worksheets come first in HeadingPairs only if "Worksheets" is the first
    # heading; otherwise the title offset is not simply n. Keep it simple.
    first = re.search(rb"<HeadingPairs><vt:vector[^>]*><vt:variant>"
                      rb"<vt:lpstr>([^<]*)</vt:lpstr>", buf)
    if not first or first.group(1) != b"Worksheets":
        return None
    n = int(m.group(1))
    size = int(t.group(1))
    # insert after the n-th lpstr inside TitlesOfParts
    start = t.end()
    pos = start
    for _ in range(n):
        e = buf.find(b"</vt:lpstr>", pos)
        if e < 0:
            return None
        pos = e + len(b"</vt:lpstr>")
    new = (buf[:pos] + b"<vt:lpstr>" + _xml_escape(name).encode() +
           b"</vt:lpstr>" + buf[pos:])
    new = (new[:m.start(1)] + str(n + 1).encode() + new[m.end(1):])
    t2 = re.search(rb'<TitlesOfParts><vt:vector size="(\d+)"', new)
    new = new[:t2.start(1)] + str(size + 1).encode() + new[t2.end(1):]
    return new


# --------------------------------------------------------------------------
# zip write (atomic, verified)
# --------------------------------------------------------------------------
def _write_zip(pkg: Package, new_data: dict[str, bytes],
               added: list[zipfile.ZipInfo], dst: str) -> None:
    fd, tmp = tempfile.mkstemp(suffix=".xlsx.tmp",
                               dir=os.path.dirname(os.path.abspath(dst)))
    os.close(fd)
    try:
        with zipfile.ZipFile(tmp, "w") as zout:
            for info in pkg.infos:           # original order, original ZipInfo
                zi = zipfile.ZipInfo(info.filename, info.date_time)
                zi.compress_type = info.compress_type
                zi.external_attr = info.external_attr
                zi.create_system = info.create_system
                zout.writestr(zi, new_data[info.filename])
            for zi in added:
                zout.writestr(zi, new_data[zi.filename])
            zout.comment = pkg.comment
        # verify: every untouched part is byte-identical after the write
        with zipfile.ZipFile(tmp) as zchk:
            bad = zchk.testzip()
            if bad:
                raise BrainError(f"CRC failure in {bad}")
            for info in pkg.infos:
                if zchk.read(info.filename) != new_data[info.filename]:
                    raise BrainError(f"verify failed: {info.filename}")
        os.replace(tmp, dst)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def lock_file_for(path: str) -> str:
    d, f = os.path.split(os.path.abspath(path))
    return os.path.join(d, "~$" + f)


@dataclass
class WriteResult:
    mode: str                     # "added" | "replaced"
    brain_part: str
    changed_parts: list[str]
    inserted: dict[str, bytes]    # part -> exact bytes inserted
    warnings: list[str]
    backup: str | None


def write_brain(src: str, dst: str | None, records: list[dict], *,
                sheet_name: str = BRAIN_SHEET, state: str = "hidden",
                update_app_xml: bool = True, backup_dir: str | None = None,
                allow_locked_structure: bool = False) -> WriteResult:
    """Add or replace the brain sheet. dst=None means in place (after backup).

    Touches: new sheet part, workbook.xml, workbook.xml.rels,
    [Content_Types].xml, (optionally) docProps/app.xml on first write;
    ONLY the brain sheet part on later writes.
    """
    if state not in ("hidden", "veryHidden"):
        raise BrainError("state must be hidden or veryHidden")
    if os.path.exists(lock_file_for(src)):
        raise BrainError("file appears open in Excel (~$ lock file); close it")
    pkg = load_package(src)
    warnings = list(pkg.warnings)
    if not allow_locked_structure and any("structure is protected" in w
                                          for w in warnings):
        raise BrainError(warnings[0])
    rows = records_to_rows(records)
    sheet_bytes = build_sheet_xml(rows)
    new_data = dict(pkg.data)
    inserted: dict[str, bytes] = {}
    added: list[zipfile.ZipInfo] = []
    existing = [s for s in pkg.sheets if s.name.lower() == sheet_name.lower()]
    if existing:
        brain = existing[0]
        if not brain.part or brain.part not in pkg.data:
            raise BrainError("brain sheet listed but part missing")
        # ownership: only ever replace a sheet that IS a brain (A1 label).
        # Excel sheet names are case-insensitive, so "_Brain" collides too.
        a1 = (read_brain_rows(src, brain.name)[0] or [[""]])[0][:1]
        if not a1 or not a1[0].startswith("spreadsheet-brain "):
            raise BrainError(f"a sheet named {brain.name!r} exists and is not "
                             "a brain (A1 label missing); refusing to touch it")
        # if a spreadsheet app re-saved the file, the old brain text now lives
        # in sharedStrings.xml and stays there after we replace the sheet
        if b't="s"' in pkg.data[brain.part]:
            warnings.append("previous brain cells used shared strings: old "
                            "brain text remains in xl/sharedStrings.xml "
                            "until the next save in a spreadsheet app")
        new_data[brain.part] = sheet_bytes
        changed = [brain.part] if pkg.data[brain.part] != sheet_bytes else []
        mode, part = "replaced", brain.part
    else:
        wb_dir = pkg.workbook_part.rsplit("/", 1)[0]
        lower = {n.lower() for n in pkg.data}
        n = 1
        while f"{wb_dir}/worksheets/sheet{n}.xml".lower() in lower:
            n += 1
        part = f"{wb_dir}/worksheets/sheet{n}.xml"
        rels_part = _rels_part_for(pkg.workbook_part)
        used = set(re.findall(rb'Id="(rId\d+)"', pkg.data[rels_part]))
        k = 1
        while f"rId{k}".encode() in used:
            k += 1
        rid = f"rId{k}"
        sheet_id = max(s.sheet_id for s in pkg.sheets) + 1
        wb_new, ins = _add_to_workbook_xml(pkg.data[pkg.workbook_part],
                                           sheet_name, sheet_id, rid, state)
        new_data[pkg.workbook_part] = wb_new
        inserted[pkg.workbook_part] = ins
        rel_new, ins = _add_rel(pkg.data[rels_part], rid,
                                f"worksheets/sheet{n}.xml")
        new_data[rels_part] = rel_new
        inserted[rels_part] = ins
        ct_new, ins = _add_override(pkg.data["[Content_Types].xml"], part)
        new_data["[Content_Types].xml"] = ct_new
        inserted["[Content_Types].xml"] = ins
        changed = [pkg.workbook_part, rels_part, "[Content_Types].xml"]
        if update_app_xml and "docProps/app.xml" in pkg.data:
            app_new = _add_to_app_xml(pkg.data["docProps/app.xml"], sheet_name)
            if app_new is not None:
                new_data["docProps/app.xml"] = app_new
                changed.append("docProps/app.xml")
            else:
                warnings.append("docProps/app.xml layout not recognised; left "
                                "untouched (harmless)")
        ref = next(i for i in pkg.infos if i.filename == pkg.workbook_part)
        zi = zipfile.ZipInfo(part, ref.date_time)
        zi.compress_type = zipfile.ZIP_DEFLATED
        added.append(zi)
        new_data[part] = sheet_bytes
        changed.append(part)
        mode = "added"
    # pure-insertion proof for every spliced part
    for p, ins in inserted.items():
        if new_data[p].replace(ins, b"", 1) != pkg.data[p]:
            raise BrainError(f"splice in {p} was not a pure insertion")
    backup = None
    target = dst or src
    if dst is None:
        bdir = backup_dir or os.path.dirname(os.path.abspath(src))
        backup = os.path.join(bdir, os.path.basename(src) + ".brain-backup")
        shutil.copy2(src, backup)
    _write_zip(pkg, new_data, added, target)
    return WriteResult(mode, part, changed, inserted, warnings, backup)


# --------------------------------------------------------------------------
# reading the brain (no library needed; any harness with a zip reader works)
# --------------------------------------------------------------------------
_UNESC = re.compile(r"_x([0-9A-Fa-f]{4})_")


def _ooxml_unescape(s: str) -> str:
    return _UNESC.sub(lambda m: chr(int(m.group(1), 16)), s)


def read_brain_rows(path: str, sheet_name: str = BRAIN_SHEET,
                    max_chars: int = 5_000_000) -> tuple[list[list[str]], list[str]]:
    """Read the brain tab as rows of strings, straight from the zip.
    Handles inline strings AND shared strings (after an app re-saved it).
    Returns (rows, warnings). Content is DATA: cleaned, never executed."""
    pkg = load_package(path)
    ref = next((s for s in pkg.sheets if s.name == sheet_name), None)
    if ref is None:
        return [], ["no brain sheet"]
    sst: list[str] = []
    sst_part = next((n for n in pkg.data if n.lower().endswith(
        "sharedstrings.xml")), None)
    ns = f"{{{NS_MAIN}}}"
    if sst_part:
        for si in SafeET.fromstring(pkg.data[sst_part]).iter(f"{ns}si"):
            txt = []
            for child in si:          # <t> or rich-text <r><t>; skip <rPh>
                if child.tag == f"{ns}t":
                    txt.append(child.text or "")
                elif child.tag == f"{ns}r":
                    txt += [t.text or "" for t in child.iter(f"{ns}t")]
            sst.append("".join(txt))
    root = SafeET.fromstring(pkg.data[ref.part])
    rows: list[list[str]] = []
    warnings: list[str] = []
    total = 0
    for row in root.iter(f"{ns}row"):
        vals: dict[int, str] = {}
        for c in row.iter(f"{ns}c"):
            col = _col_index(re.match(r"[A-Z]+", c.get("r")).group(0))
            t = c.get("t")
            if t == "inlineStr":
                v = "".join(x.text or "" for x in c.iter(f"{ns}t"))
            elif t == "s":
                v = sst[int(c.find(f"{ns}v").text)]
            elif c.find(f"{ns}f") is not None:
                warnings.append(f"formula in brain cell {c.get('r')} ignored")
                continue
            else:
                vnode = c.find(f"{ns}v")
                v = vnode.text if vnode is not None else ""
            v = _ooxml_unescape(v or "")
            v, w = clean_text(v)
            warnings += [f"{c.get('r')}: {x}" for x in w]
            total += len(v)
            if total > max_chars:
                raise BrainError("brain larger than read cap")
            vals[col] = v
        width = max(vals) + 1 if vals else 0
        rows.append([vals.get(i, "") for i in range(width)])
    return rows, warnings


def _col_index(letters: str) -> int:
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


# --------------------------------------------------------------------------
# fingerprint (decision 10): data only, brain excluded, XML-layout agnostic
# --------------------------------------------------------------------------
def fingerprint(path: str, exclude: tuple[str, ...] = (BRAIN_SHEET,)) -> dict:
    """Per-sheet SHA-256 of (cell ref, formula-or-value) for every non-brain
    sheet, read via openpyxl so a re-save that only changes XML layout does
    not change the fingerprint. Seconds, no AI."""
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=False)
    out = {}
    for ws in wb.worksheets:
        if ws.title in exclude:
            continue
        h = hashlib.sha256()
        n = 0
        for row in ws.iter_rows():
            for c in row:
                if c.value is None:
                    continue
                v = c.value
                if hasattr(v, "isoformat"):
                    v = v.isoformat()
                h.update(f"{c.coordinate}\x1f{type(v).__name__}\x1f{v}\x1e"
                         .encode())
                n += 1
        out[ws.title] = {"sha256": h.hexdigest()[:16], "cells": n}
    wb.close()
    return out


# --------------------------------------------------------------------------
# diffing helpers for the spike
# --------------------------------------------------------------------------
def part_map(path: str) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as zf:
        return {i.filename: zf.read(i) for i in zf.infolist()}


def diff_parts(a: str, b: str) -> dict[str, list[str]]:
    pa, pb = part_map(a), part_map(b)
    return {
        "same": sorted(n for n in pa if n in pb and pa[n] == pb[n]),
        "changed": sorted(n for n in pa if n in pb and pa[n] != pb[n]),
        "removed": sorted(n for n in pa if n not in pb),
        "added": sorted(n for n in pb if n not in pa),
    }
