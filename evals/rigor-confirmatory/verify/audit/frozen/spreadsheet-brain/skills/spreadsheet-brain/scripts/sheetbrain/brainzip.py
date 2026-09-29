"""Surgical read and write of the `_brain` worksheet inside an .xlsx or .xlsm.

Rules (proven in the 2026-09-25 spike, 61 checks):
  * Never re-save the workbook through a library. Every zip entry is copied
    through untouched; bytes are spliced into only the 3 or 4 parts that must
    know about the new sheet, and each splice is asserted to be a pure insertion.
  * Never parse and re-serialize XML we did not write.
  * The brain sheet uses inline strings, so sharedStrings.xml is never touched.
  * The sheet is appended LAST so positional indexes stay valid.
  * Brain text is data. The writer strips invisible characters and neutralizes
    leading formula characters; the reader does the same on the way in.
  * With a graph, the brain tab also carries a drawing of it (sheetbrain.xldraw):
    our own drawing part, our own sheet rels part and one content-type
    override. Removing the brain removes all three.
  * A visible brain is the tab the workbook opens on (so a person sees it
    first and a script reading the "active" sheet reads it). That takes two
    small edits to parts that are not ours: activeTab on the workbook view,
    and the tabSelected flag on the tab that was selected (two selected tabs
    would open grouped). Each edit is one start tag, recorded byte for byte in
    the brain tab itself, and put back exactly when the brain is removed or
    hidden. The sheet order never changes.

Stdlib only. defusedxml is used for reads when installed.
"""
from __future__ import annotations

import base64
import binascii
import csv
import io
import os
import posixpath
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field
from xml.sax.saxutils import escape as _xml_escape

try:  # XML-bomb-safe parsing for reads (we never re-serialize)
    from defusedxml import ElementTree as _SafeET  # type: ignore
    _HAVE_DEFUSED = True
except ImportError:  # pragma: no cover - depends on the sandbox
    import xml.etree.ElementTree as _SafeET  # noqa: N812
    _HAVE_DEFUSED = False

FORMAT_VERSION = "0.1"
TOOL_VERSION = "0.2.0"          # the tool that wrote a brain, named in its meta note (the format above is separate)
BRAIN_SHEET = "_brain"
FORMAT_LABEL = f"spreadsheet-brain {FORMAT_VERSION} | record"
# The tab's columns, in the order a reader needs them: what the note is (A to F),
# then the bookkeeping. Readers map columns by header name, so older brains
# (id, kind and label first, "to" instead of "about") still read.
COLUMNS = [FORMAT_LABEL, "label", "statement", "source", "as_of", "about",
           "id", "kind", "status", "said_by", "depends_on", "data_fp", "class",
           "stale_after", "ref", "part", "text", "from"]
# the record field each column holds: the tab says "about", a record says "to"
COLUMN_FIELD = {FORMAT_LABEL: "record", "about": "to"}
HEADER_FIELD = {"about": "to"}          # header name read from a tab -> record field
# record fields, in the order records have always carried them
FIELDS = ["record", "id", "kind", "label", "statement", "source", "status", "as_of",
          "said_by", "from", "to", "depends_on", "data_fp", "class", "stale_after",
          "ref", "part", "text"]
assert sorted(FIELDS) == sorted(COLUMN_FIELD.get(c, c) for c in COLUMNS)
CELL_LIMIT = 32767            # Excel max characters per cell, in UTF-16 units
CHUNK = 32000                 # margin under the limit

NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS_STRICT = "http://purl.oclc.org/ooxml/spreadsheetml/main"
NS_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS_PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
NS_CT = "http://schemas.openxmlformats.org/package/2006/content-types"
REL_WORKSHEET = ("http://schemas.openxmlformats.org/officeDocument/2006/"
                 "relationships/worksheet")
CT_WORKSHEET = ("application/vnd.openxmlformats-officedocument."
                "spreadsheetml.worksheet+xml")
REL_DRAWING = ("http://schemas.openxmlformats.org/officeDocument/2006/"
               "relationships/drawing")
CT_DRAWING = "application/vnd.openxmlformats-officedocument.drawing+xml"
DRAWING_STEM = "brainmap"     # xl/drawings/brainmap1.xml: a name Excel itself never writes

MAX_UNCOMPRESSED = 2 * 1024 ** 3   # zip-bomb guard: 2 GiB total
MAX_RATIO = 200                     # per-entry compression ratio guard


class BrainError(RuntimeError):
    """A refusal or a failed safety check. The message is shown to the user."""


# --------------------------------------------------------------------------
# safe XML parsing
# --------------------------------------------------------------------------
_DOCTYPE = re.compile(rb"<!DOCTYPE|<!ENTITY", re.I)


def parse_xml(data: bytes):
    """Parse XML we only read. OOXML never needs a DOCTYPE, so one is refused
    outright (entity-expansion bombs need it) when defusedxml is missing."""
    if not _HAVE_DEFUSED and _DOCTYPE.search(data[:4096] if len(data) > 4096 else data):
        raise BrainError("XML part declares a DOCTYPE or ENTITY; refusing to parse")
    return _SafeET.fromstring(data)


# --------------------------------------------------------------------------
# text hygiene
# --------------------------------------------------------------------------
_ILLEGAL_XML = re.compile("[\x00-\x08\x0B\x0C\x0E-\x1F￾￿]|[\ud800-\udfff]")
_INVISIBLE = re.compile(
    "[\U000E0000-\U000E007F"      # Unicode Tags (invisible prompt smuggling)
    "\u202a-\u202e\u2066-\u2069"  # bidi overrides and isolates
    "\u200b-\u200d\u2060\ufeff]")  # zero-width characters, word joiner, BOM
_OOXML_ESCAPE = re.compile(r"_(x[0-9A-Fa-f]{4}_)")
_FORMULA_LEAD = ("=", "+", "-", "@", "\t", "\r")


def clean_text(s: str) -> tuple[str, list[str]]:
    """Strip characters a person cannot see but a model can read.
    Returns (clean, warnings). Used on write AND on read."""
    warnings = []
    n = len(_INVISIBLE.findall(s))
    if n:
        warnings.append(f"stripped {n} invisible characters")
        s = _INVISIBLE.sub("", s)
    if _ILLEGAL_XML.search(s):
        warnings.append("stripped XML-illegal control characters")
        s = _ILLEGAL_XML.sub("", s)
    return s, warnings


def normalize_text(s: str) -> str:
    s, _ = clean_text(s)
    return s.replace("\r\n", "\n").replace("\r", "\n")


def neutralize_formula_lead(s: str) -> str:
    """Brain text never starts like a formula (OWASP CSV injection). A leading
    apostrophe is also escaped so the reader can always strip exactly one."""
    return "'" + s if s.startswith(_FORMULA_LEAD + ("'",)) else s


def ooxml_escape(s: str) -> str:
    s = _OOXML_ESCAPE.sub(r"_x005F_\1", s)
    return _xml_escape(s)


def utf16_len(s: str) -> int:
    return len(s.encode("utf-16-le")) // 2


def split_utf16(s: str, limit: int | None = None) -> list[str]:
    """Split so each piece is <= limit UTF-16 units and no surrogate pair is cut."""
    limit = CHUNK if limit is None else limit
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
# records <-> rows
# --------------------------------------------------------------------------
def records_to_rows(records: list[dict]) -> list[list[str]]:
    rows = [list(COLUMNS)]
    for rec in records:
        text = normalize_text(str(rec.get("text", "") or ""))
        pieces = split_utf16(text)
        for i, piece in enumerate(pieces, 1):
            row = []
            for col in COLUMNS:
                field_ = COLUMN_FIELD.get(col, col)
                if field_ == "record":
                    v = rec.get("record", "")
                elif col == "part":
                    v = f"{i}/{len(pieces)}" if len(pieces) > 1 else ""
                elif col == "text":
                    v = piece
                elif i > 1 and col not in ("id",):
                    v = ""          # continuation rows carry only id + text
                elif field_ == "to":
                    v = rec.get("to") or rec.get("about") or ""
                else:
                    v = rec.get(field_, "")
                v = "" if v is None else str(v)
                row.append(neutralize_formula_lead(normalize_text(v)))
            rows.append(row)
    return rows


def rows_to_records(rows: list[list[str]]) -> tuple[list[dict], list[str]]:
    """Inverse of records_to_rows. Maps columns by header name, so a brain with
    any subset of columns, in any order, still reads (the "about" column is the
    record's "to"; a column named "to" still works). Returns (records, warnings)."""
    warnings: list[str] = []
    if not rows:
        return [], warnings
    head = [h.strip() for h in rows[0]]
    if not head or not head[0].startswith("spreadsheet-brain "):
        return [], ["brain tab has no format label in A1"]
    names = ["record" if i == 0 else HEADER_FIELD.get(h, h) for i, h in enumerate(head)]
    recs: list[dict] = []
    pending: dict[str, dict] = {}
    for r in rows[1:]:
        r = list(r) + [""] * (len(names) - len(r))
        d: dict = {}
        for n, v in zip(names, r):
            if n and (v or n not in d):       # two columns for one field: the filled one wins
                d[n] = v or ""
        for k, v in d.items():
            if isinstance(v, str) and v.startswith("'"):
                d[k] = v[1:]
        part = d.pop("part", "")
        if part:
            m = re.fullmatch(r"(\d+)/(\d+)", part.strip())
            if not m:
                warnings.append(f"bad part marker {part!r}; row kept as is")
                recs.append(d)
                continue
            i, n = int(m.group(1)), int(m.group(2))
            key = d.get("id", "")
            if i == 1:
                pending[key] = d
            elif key in pending:
                pending[key]["text"] = pending[key].get("text", "") + d.get("text", "")
            if i == n and key in pending:
                recs.append(pending.pop(key))
        elif any(v for v in d.values()):
            recs.append(d)
    for key, d in pending.items():
        warnings.append(f"incomplete multi-part record {key!r}")
        recs.append(d)
    return recs, warnings


# --------------------------------------------------------------------------
# sheet XML
# --------------------------------------------------------------------------
def col_letter(i: int) -> str:
    s = ""
    i += 1
    while i:
        i, rem = divmod(i - 1, 26)
        s = chr(65 + rem) + s
    return s


def col_index(letters: str) -> int:
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


# column widths in Excel's character units: the statement gets room to be read
# on one line (no wrapping: that would need a style in the workbook's own
# styles part, which the brain never touches)
_WIDTHS = {FORMAT_LABEL: 13, "label": 30, "statement": 110, "source": 10, "as_of": 11,
           "about": 36, "id": 26, "kind": 12, "status": 13, "said_by": 10, "depends_on": 30,
           "data_fp": 14, "class": 11, "stale_after": 11, "ref": 22, "part": 6, "text": 60,
           "from": 26}
EMU_PER_PX = 9525
DEFAULT_COL_PX = 64           # an unsized column at the usual 7 px digit width
DRAWING_ZOOM = 80             # the tab opens zoomed out so the whole drawing fits a laptop screen


def _col_width(ci: int, name: str) -> int:
    return _WIDTHS.get(name, 14 if ci > 1 else 12)


def _col_px(width: float, mdw: int = 7) -> int:
    """Excel's own column width to pixels rule (ECMA-376 18.3.1.13)."""
    return int(((256 * width + int(128 / mdw)) / 256) * mdw)


def drawing_origin(ncols: int = len(COLUMNS), mdw: int = 7) -> tuple[int, int]:
    """Where the brain drawing starts: (EMU from the sheet's left edge, the
    0-based column it starts in). One empty column sits between the table and
    the drawing so the two never touch."""
    px = sum(_col_px(_col_width(ci, name), mdw) for ci, name in enumerate(COLUMNS[:ncols], 1))
    gap_cols = 1
    return (px + gap_cols * DEFAULT_COL_PX) * EMU_PER_PX, ncols + gap_cols


def build_sheet_xml(rows: list[list[str]], *, drawing_rid: str | None = None,
                    view_col: int | None = None, grid: tuple | None = None,
                    selected: bool = False, put_back: list | None = None) -> bytes:
    """A minimal, schema-ordered worksheet using inline strings only.
    Row 1 frozen so a person reading the tab keeps the headers in view.
    With drawing_rid the sheet points at its drawing, with view_col the tab
    opens scrolled so that column is the first one on screen (zoomed out so the
    drawing fits, without gridlines), and grid (first_col, last_col, width)
    sizes the columns the drawing is anchored to and pins the row height, so
    the drawing lands the same on every platform. selected marks the tab as
    the selected one (the workbook opens on it); put_back is the record of the
    edits that made it so (see _put_back_xml)."""
    ncols = max(len(r) for r in rows)
    for r in rows:
        for v in r:
            if utf16_len(v) > CELL_LIMIT:
                raise BrainError("cell over 32,767 characters; chunk first")
    cols = []
    for ci, name in enumerate(COLUMNS[:ncols], 1):
        w = _col_width(ci, name)
        cols.append(f'<col min="{ci}" max="{ci}" width="{w}" customWidth="1"/>')
    if grid:
        g0, g1, gw = grid
        if g0 + 1 > ncols:
            cols.append(f'<col min="{g0 + 1}" max="{g1 + 1}" width="{gw}" customWidth="1"/>')
    sel = ' tabSelected="1"' if selected else ""
    if view_col is None:
        view = (f'<sheetViews><sheetView{sel} workbookViewId="0">'
                '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
                '</sheetView></sheetViews>')
    else:
        c = col_letter(view_col)
        view = (f'<sheetViews><sheetView{sel} showGridLines="0" zoomScale="{DRAWING_ZOOM}" '
                f'zoomScaleNormal="{DRAWING_ZOOM}" workbookViewId="0" topLeftCell="{c}1">'
                f'<pane ySplit="1" topLeftCell="{c}2" activePane="bottomLeft" state="frozen"/>'
                f'<selection pane="bottomLeft" activeCell="{c}2" sqref="{c}2"/>'
                '</sheetView></sheetViews>')
    parts = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n',
        f'<worksheet xmlns="{NS_MAIN}" xmlns:r="{NS_R}">',
        f'<dimension ref="A1:{col_letter(ncols - 1)}{len(rows)}"/>',
        view,
        '<sheetFormatPr defaultRowHeight="15" customHeight="1"/>' if grid else
        '<sheetFormatPr defaultRowHeight="15"/>',
        "<cols>" + "".join(cols) + "</cols>",
        "<sheetData>",
    ]
    # long text in the "text" column would run on over the empty cells to its right, under the
    # drawing. An empty text cell next to it stops that (readers see an empty value, as before).
    stop_after = COLUMNS.index("text") if "text" in COLUMNS[:ncols - 1] else None
    for ri, row in enumerate(rows, 1):
        parts.append(f'<row r="{ri}">')
        for ci, v in enumerate(row):
            if v == "":
                if ri > 1 and stop_after is not None and ci == stop_after + 1 and row[stop_after]:
                    parts.append(f'<c r="{col_letter(ci)}{ri}" t="inlineStr"><is><t></t></is></c>')
                continue
            sp = ' xml:space="preserve"' if v != v.strip() or "\n" in v else ""
            parts.append(f'<c r="{col_letter(ci)}{ri}" t="inlineStr"><is>'
                         f"<t{sp}>{ooxml_escape(v)}</t></is></c>")
        parts.append("</row>")
    parts.append("</sheetData>")
    parts.append('<pageMargins left="0.7" right="0.7" top="0.75" bottom="0.75" '
                 'header="0.3" footer="0.3"/>')
    if drawing_rid:               # CT_Worksheet order: drawing comes after pageMargins
        parts.append(f'<drawing r:id="{drawing_rid}"/>')
    if put_back:                  # the record of our view edits, as a comment (see _put_back_xml)
        parts.append(_put_back_xml(put_back))
    parts.append("</worksheet>")
    return "".join(parts).encode("utf-8")


# --------------------------------------------------------------------------
# opening on the brain tab, and putting it back
# --------------------------------------------------------------------------
# An edit to a part that is not ours is one start tag, before and after. The
# list rides in an XML comment at the end of the brain tab's own part, so
# removing or hiding the brain can put every byte back. A comment, not an
# extension element: Excel, openpyxl and pandas all skip comments without a
# word, where openpyxl warns about an extension it does not know. A record read
# from a file is untrusted: it is only ever applied when it is exactly one of
# our two edits (see _valid_put_back), so a sender cannot use it to write
# anything else.
_PUT_BACK_HEAD = "spreadsheet-brain put-back 1:"
_PUT_BACK_RE = re.compile(rb"<!--spreadsheet-brain put-back 1: ([A-Za-z0-9+/=. ]*) -->")
_TAG_RE = rb"<(?:[A-Za-z_][\w.-]*:)?%s(?=[\s/>])[^<>]*>"
_SELECTED_RE = re.compile(rb"""\s+tabSelected\s*=\s*(["'])(?:1|true)\1""")
_ACTIVE_RE = re.compile(rb"""\s+activeTab\s*=\s*(["'])(\d*)\1""")


def _put_back_xml(entries: list) -> str:
    """The record as one comment: part.was.now per edit, each base64 (which
    never holds a '.' or a '-', so the comment is always well formed)."""
    b64 = lambda x: base64.b64encode(x).decode()  # noqa: E731
    items = " ".join(f"{b64(p.encode('utf-8'))}.{b64(was)}.{b64(now)}" for p, was, now in entries)
    return f"<!--{_PUT_BACK_HEAD} {items} -->"


def _first_tag(buf: bytes, name: str, view_id: bool = False) -> re.Match | None:
    """The first start tag of element `name` (any prefix). With view_id, the
    first one for workbook view 0 (the one the brain tab uses)."""
    for m in re.finditer(_TAG_RE % name.encode(), buf):
        if not view_id:
            return m
        v = re.search(rb"""\bworkbookViewId\s*=\s*(["'])(\d+)\1""", m.group(0))
        if v is None or v.group(2) == b"0":
            return m
    return None


def _active_tab(tag: bytes) -> int:
    m = _ACTIVE_RE.search(tag)
    try:
        return int(m.group(2)) if m else 0
    except ValueError:
        return 0


def _with_active_tab(tag: bytes, idx: int) -> bytes:
    m = _ACTIVE_RE.search(tag)
    if m:
        return tag[:m.start()] + b' activeTab="%d"' % idx + tag[m.end():]
    end = len(tag) - (2 if tag.endswith(b"/>") else 1)
    return tag[:end].rstrip() + b' activeTab="%d"' % idx + tag[end:]


def _valid_put_back(pkg: "Package", part: str, was: bytes, now: bytes) -> bool:
    """True only for the edits this module makes: activeTab on the first
    workbook view, a bookViews block added before <sheets>, or tabSelected
    taken off a sheet view."""
    if part == pkg.workbook_part:
        tag = _TAG_RE % b"workbookView"
        if re.fullmatch(tag, was) and re.fullmatch(tag, now):
            return _ACTIVE_RE.sub(b"", was) == _ACTIVE_RE.sub(b"", now)
        if re.fullmatch(_TAG_RE % b"sheets", was):
            p = re.match(rb"<([A-Za-z_][\w.-]*:)?", was).group(1) or b""
            block = rb"<%sbookViews><%sworkbookView activeTab=\"\d+\"/></%sbookViews>" % (
                re.escape(p), re.escape(p), re.escape(p))
            return re.fullmatch(block + re.escape(was), now) is not None
        return False
    if part in {s.part for s in pkg.sheets}:
        tag = _TAG_RE % b"sheetView"
        return (re.fullmatch(tag, was) is not None and re.fullmatch(tag, now) is not None
                and _SELECTED_RE.search(was) is not None and _SELECTED_RE.sub(b"", was, count=1) == now)
    return False


def _read_put_back(pkg: "Package", brain_part: str) -> list:
    """The put-back record in the brain tab: only entries that are one of our
    own edits, for a part of this workbook."""
    m = _PUT_BACK_RE.search(pkg.data.get(brain_part, b""))
    out = []
    for item in (m.group(1).split() if m else [])[:64]:
        bits = item.split(b".")
        if len(bits) != 3:
            continue
        try:
            part = base64.b64decode(bits[0], validate=True).decode("utf-8")
            was = base64.b64decode(bits[1], validate=True)
            now = base64.b64decode(bits[2], validate=True)
        except (binascii.Error, ValueError):
            continue
        if part in pkg.data and part != brain_part and _valid_put_back(pkg, part, was, now):
            out.append((part, was, now))
    return out


def _in_effect(new_data: dict, entry: tuple) -> bool:
    part, _, now = entry
    return part in new_data and new_data[part].count(now) == 1


def _apply(new_data: dict, part: str, was: bytes, now: bytes) -> bool:
    """Swap one start tag. Only when the old tag is there exactly once and the
    new one is not there yet, so putting it back later is unambiguous."""
    before = new_data[part]
    if was == now or before.count(was) != 1 or before.count(now) != 0:
        return False
    after = before.replace(was, now, 1)
    if after.replace(now, was, 1) != before:
        raise BrainError(f"view edit in {part} did not swap exactly one start tag")
    new_data[part] = after
    return True


def _open_on_brain(pkg: "Package", new_data: dict, brain_index: int, brain_part: str,
                   prior: list) -> list:
    """Make the brain the tab the workbook opens on, with no other tab selected.
    Edits new_data and returns the put-back record: the earlier record's
    entries that are still in effect, plus any new edits."""
    kept = [e for e in prior if _in_effect(new_data, e)]
    entries = list(kept)
    wb = pkg.workbook_part
    m = _first_tag(new_data[wb], "workbookView")
    if m is None and _first_tag(new_data[wb], "bookViews") is not None:
        pass                      # an empty view list: a second one would break the file, so leave it
    elif m is None:
        sheets = _first_tag(new_data[wb], "sheets")
        if sheets is None:
            raise BrainError("workbook has no sheet list")
        was = sheets.group(0)
        p = re.match(rb"<([A-Za-z_][\w.-]*:)?", was).group(1) or b""
        now = (b"<%sbookViews><%sworkbookView activeTab=\"%d\"/></%sbookViews>" % (p, p, brain_index, p)
               + was)
        if _apply(new_data, wb, was, now):
            entries.append((wb, was, now))
    elif _active_tab(m.group(0)) != brain_index:
        was = m.group(0)
        now = _with_active_tab(was, brain_index)
        if _apply(new_data, wb, was, now):
            entries.append((wb, was, now))
    for s in pkg.sheets:
        if s.part == brain_part or s.part not in new_data:
            continue
        m = _first_tag(new_data[s.part], "sheetView", view_id=True)
        if m is None or not _SELECTED_RE.search(m.group(0)):
            continue
        was = m.group(0)
        now = _SELECTED_RE.sub(b"", was, count=1)
        if _apply(new_data, s.part, was, now):     # not one unambiguous tag: leave that tab alone
            entries.append((s.part, was, now))
    return entries


def _put_back(pkg: "Package", new_data: dict, entries: list) -> list:
    """Undo our view edits (newest first). Returns the entries that could not
    be put back because an app changed that tag since."""
    missed = []
    for e in reversed(entries):
        part, was, now = e
        if _in_effect(new_data, e):
            new_data[part] = new_data[part].replace(now, was, 1)
        else:
            missed.append(e)
    return missed


def _fix_active_tab(buf: bytes, brain_index: int, visible: list, removing: bool) -> bytes:
    """When the brain goes away or is hidden and the original view could not be
    put back byte for byte, keep activeTab pointing at a visible tab."""
    m = _first_tag(buf, "workbookView")
    if m is None:
        return buf
    cur = _active_tab(m.group(0))
    new = cur
    if cur == brain_index:
        new = visible[0] if visible else 0
    elif removing and cur > brain_index:
        new = cur - 1
    if new == cur:
        return buf
    return buf[:m.start()] + _with_active_tab(m.group(0), new) + buf[m.end():]


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
    infos: list
    data: dict
    comment: bytes
    workbook_part: str
    sheets: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    refusals: list = field(default_factory=list)
    calc_manual: bool = False
    calc_iterate: bool = False
    external_links: int = 0
    connections: bool = False
    defined_names: list = field(default_factory=list)


def _resolve(base_dir: str, target: str) -> str:
    if target.startswith("/"):
        return target.lstrip("/")
    parts = (base_dir + "/" + target).split("/") if base_dir else target.split("/")
    out: list[str] = []
    for p in parts:
        if p == "..":
            if out:
                out.pop()
        elif p and p != ".":
            out.append(p)
    return "/".join(out)


def rels_part_for(part: str) -> str:
    d, f = part.rsplit("/", 1) if "/" in part else ("", part)
    return f"{d}/_rels/{f}.rels" if d else f"_rels/{f}.rels"


def load_package(path: str) -> Package:
    with open(path, "rb") as fh:
        head = fh.read(8)
    if head.startswith(b"\xd0\xcf\x11\xe0"):
        raise BrainError("This file is password-protected or an old .xls. "
                         "I can't add a brain to it. Save it as .xlsx first.")
    if not head.startswith(b"PK"):
        raise BrainError("This is not an .xlsx workbook.")
    with zipfile.ZipFile(path) as zf:
        infos = zf.infolist()
        total = sum(i.file_size for i in infos)
        if total > MAX_UNCOMPRESSED:
            raise BrainError(f"Workbook unpacks to {total} bytes; over the safety limit.")
        for i in infos:
            if (i.compress_size and i.file_size / i.compress_size > MAX_RATIO
                    and i.file_size > 10 * 1024 ** 2):
                raise BrainError(f"Suspicious compression ratio in {i.filename}.")
        lower = [i.filename.lower() for i in infos]
        if len(set(lower)) != len(lower):
            raise BrainError("Workbook has duplicate internal part names.")
        data = {i.filename: zf.read(i) for i in infos}
        comment = zf.comment
    if "_rels/.rels" not in data:
        raise BrainError("Workbook has no package relationships part.")
    root_rels = parse_xml(data["_rels/.rels"])
    wb_part = None
    for rel in root_rels:
        if rel.get("Type", "").endswith("/officeDocument"):
            wb_part = _resolve("", rel.get("Target", ""))
    if not wb_part or wb_part not in data:
        raise BrainError("Workbook part not found.")
    pkg = Package(infos, data, comment, wb_part)
    wb_xml = data[wb_part]
    if NS_STRICT.encode() in wb_xml[:4000]:
        raise BrainError("Strict Open XML workbooks are not supported yet.")
    wb = parse_xml(wb_xml)
    rels = parse_xml(data[rels_part_for(wb_part)])
    wb_dir = wb_part.rsplit("/", 1)[0]
    rid_to_part = {r.get("Id"): _resolve(wb_dir, r.get("Target", ""))
                   for r in rels if r.get("TargetMode") != "External"}
    for s in wb.iter(f"{{{NS_MAIN}}}sheet"):
        rid = s.get(f"{{{NS_R}}}id")
        pkg.sheets.append(SheetRef(s.get("name"), int(s.get("sheetId")), rid,
                                   s.get("state", "visible"), rid_to_part.get(rid, "")))
    prot = wb.find(f"{{{NS_MAIN}}}workbookProtection")
    if prot is not None and prot.get("lockStructure") in ("1", "true"):
        pkg.refusals.append("The workbook's sheet list is locked by its owner, so I "
                            "won't add a tab. The brain stays on this machine instead.")
    if any(n.startswith("_xmlsignatures/") for n in data):
        pkg.warnings.append("This workbook is digitally signed. Adding a tab breaks "
                            "the signature.")
    if any(n.lower().endswith("vbaproject.bin") for n in data):
        pkg.warnings.append("This workbook has macros. Macros that loop over every sheet will see "
                            "the new _brain tab.")
    custom = data.get("docProps/custom.xml", b"")
    if b"_MarkAsFinal" in custom:
        pkg.warnings.append("The owner marked this workbook as final.")
    sheet_count_fn = re.compile(rb"<(?:\w+:)?f[^>]*>[^<]*\bSHEETS?\(")
    if any(sheet_count_fn.search(v) for k, v in data.items() if k.startswith("xl/worksheets/")):
        pkg.warnings.append("Some formulas count the workbook's sheets; the new tab changes that count.")
    calc = wb.find(f"{{{NS_MAIN}}}calcPr")
    if calc is not None:
        pkg.calc_manual = calc.get("calcMode") == "manual"
        pkg.calc_iterate = calc.get("iterate") in ("1", "true")
    pkg.external_links = sum(1 for n in data if n.lower().startswith("xl/externallinks/")
                             and n.lower().endswith(".xml"))
    pkg.connections = any(n.lower().endswith("connections.xml") for n in data)
    dn = wb.find(f"{{{NS_MAIN}}}definedNames")
    if dn is not None:
        for d in dn:
            pkg.defined_names.append((d.get("name"), (d.text or "").strip()))
    return pkg


# --------------------------------------------------------------------------
# byte-level splices (each asserted to be a pure insertion)
# --------------------------------------------------------------------------
def _insert_before(buf: bytes, anchor: bytes, ins: bytes) -> bytes:
    idx = buf.rfind(anchor)
    if idx < 0:
        raise BrainError(f"anchor {anchor!r} not found")
    return buf[:idx] + ins + buf[idx:]


def _prefix(buf: bytes, ns: str) -> bytes:
    """The prefix bound to namespace ns on the root element (b'' for default)."""
    root = re.search(rb"<([A-Za-z_][\w.-]*:)?(workbook|Relationships|Types)\b[^>]*>", buf)
    if not root:
        raise BrainError("root element not found")
    for m in re.finditer(rb'xmlns(?::([\w.-]+))?="([^"]+)"', root.group(0)):
        if m.group(2).decode() == ns:
            return (m.group(1) + b":") if m.group(1) else b""
    raise BrainError(f"namespace {ns} not declared on root")


def _add_to_workbook_xml(buf: bytes, name: str, sheet_id: int, rid: str,
                         state: str) -> tuple[bytes, bytes]:
    p = _prefix(buf, NS_MAIN)
    local = b""
    try:
        r = _prefix(buf, NS_R)
    except BrainError:
        # openpyxl without lxml declares the namespace on each sheet element, not
        # the root: declare it on ours the same way
        r, local = b"r:", b' xmlns:r="' + NS_R.encode() + b'"'
    if not r:
        raise BrainError("relationships namespace has no prefix")
    st = b"" if state == "visible" else b' state="' + state.encode() + b'"'
    ins = (b"<" + p + b"sheet" + local + b' name="' + _xml_escape(name).encode() + b'" sheetId="'
           + str(sheet_id).encode() + b'"' + st + b" " + r + b'id="' + rid.encode()
           + b'"/>')
    return _insert_before(buf, b"</" + p + b"sheets>", ins), ins


def _add_rel(buf: bytes, rid: str, target: str,
             rel_type: str = REL_WORKSHEET) -> tuple[bytes, bytes]:
    p = _prefix(buf, NS_PKG_REL)
    ins = (b"<" + p + b'Relationship Id="' + rid.encode() + b'" Type="'
           + rel_type.encode() + b'" Target="' + _xml_escape(target).encode() + b'"/>')
    return _insert_before(buf, b"</" + p + b"Relationships>", ins), ins


def _override_el(buf: bytes, part: str, ct: str) -> bytes:
    p = _prefix(buf, NS_CT)
    return (b"<" + p + b'Override PartName="/' + _xml_escape(part).encode() + b'" ContentType="'
            + ct.encode() + b'"/>')


def _add_override(buf: bytes, part: str, ct: str = CT_WORKSHEET,
                  extra: tuple = ()) -> tuple[bytes, bytes]:
    """One splice for one or more overrides: (part, ct) plus each (part, ct)
    in extra, written next to each other so the insertion stays one piece."""
    ins = _override_el(buf, part, ct) + b"".join(_override_el(buf, p2, c2) for p2, c2 in extra)
    return _insert_before(buf, b"</" + _prefix(buf, NS_CT) + b"Types>", ins), ins


def _remove_override(buf: bytes, part: str) -> bytes:
    return re.sub(rb'<([A-Za-z_][\w.-]*:)?Override\b[^>]*PartName="/' +
                  re.escape(_xml_escape(part).encode()) + rb'"[^>]*/>', b"", buf, count=1)


def _remove_rel(buf: bytes, rid: str) -> bytes:
    return re.sub(rb'<([A-Za-z_][\w.-]*:)?Relationship\b[^>]*\bId="' +
                  re.escape(rid.encode()) + rb'"[^>]*/>', b"", buf, count=1)


def _has_override(buf: bytes, part: str) -> bool:
    return re.search(rb'<([A-Za-z_][\w.-]*:)?Override\b[^>]*PartName="/' +
                     re.escape(_xml_escape(part).encode()) + rb'"', buf, re.I) is not None


def _free_rid(buf: bytes) -> str:
    used = set(re.findall(rb'Id="(rId\d+)"', buf))
    k = 1
    while f"rId{k}".encode() in used:
        k += 1
    return f"rId{k}"


def _sheet_rels_xml(rid: str, target: str) -> bytes:
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            f'<Relationships xmlns="{NS_PKG_REL}"><Relationship Id="{rid}" '
            f'Type="{REL_DRAWING}" Target="{_xml_escape(target)}"/></Relationships>').encode("utf-8")


def _drawing_rels(pkg: "Package", sheet_part: str) -> list[tuple[str, str]]:
    """(rId, drawing part) for every drawing the given sheet points at."""
    rels_part = rels_part_for(sheet_part)
    if rels_part not in pkg.data:
        return []
    base = sheet_part.rsplit("/", 1)[0] if "/" in sheet_part else ""
    out = []
    for rel in parse_xml(pkg.data[rels_part]):
        if rel.get("TargetMode") == "External":
            continue
        if rel.get("Type", "").endswith("/drawing"):
            out.append((rel.get("Id", ""), _resolve(base, rel.get("Target", ""))))
    return out


def _referenced_elsewhere(pkg: "Package", part: str, skip: str) -> bool:
    """True when a rels part other than `skip` points at `part`."""
    for name, buf in pkg.data.items():
        if not name.endswith(".rels") or name == skip:
            continue
        d, f = name.rsplit("/", 1) if "/" in name else ("", name)
        d = d[:-len("_rels")].rstrip("/") if d.endswith("_rels") else d
        src = (d + "/" + f[:-len(".rels")]) if d else f[:-len(".rels")]
        base = src.rsplit("/", 1)[0] if "/" in src else ""
        try:
            rels = parse_xml(buf)
        except Exception:
            continue
        for rel in rels:
            if rel.get("TargetMode") != "External" and _resolve(base, rel.get("Target", "")) == part:
                return True
    return False


def _new_drawing_part(pkg: "Package") -> str:
    """A drawing part name nobody uses yet (checked without case, with its rels)."""
    wb_dir = pkg.workbook_part.rsplit("/", 1)[0]
    lower = {n.lower() for n in pkg.data}
    k = 1
    while (f"{wb_dir}/drawings/{DRAWING_STEM}{k}.xml".lower() in lower
           or f"{wb_dir}/drawings/_rels/{DRAWING_STEM}{k}.xml.rels".lower() in lower):
        k += 1
    return f"{wb_dir}/drawings/{DRAWING_STEM}{k}.xml"


def _add_to_app_xml(buf: bytes, name: str) -> bytes | None:
    """Best effort: bump the worksheet count and add the title. None means leave
    app.xml untouched (it is informational; Excel rewrites it on save)."""
    m = re.search(rb"<vt:lpstr>Worksheets</vt:lpstr></vt:variant>\s*"
                  rb"<vt:variant><vt:i4>(\d+)</vt:i4>", buf)
    t = re.search(rb'<TitlesOfParts><vt:vector size="(\d+)" baseType="lpstr">', buf)
    first = re.search(rb"<HeadingPairs><vt:vector[^>]*><vt:variant>"
                      rb"<vt:lpstr>([^<]*)</vt:lpstr>", buf)
    if not m or not t or not first or first.group(1) != b"Worksheets":
        return None
    n, size = int(m.group(1)), int(t.group(1))
    pos = t.end()
    for _ in range(n):
        e = buf.find(b"</vt:lpstr>", pos)
        if e < 0:
            return None
        pos = e + len(b"</vt:lpstr>")
    new = buf[:pos] + b"<vt:lpstr>" + _xml_escape(name).encode() + b"</vt:lpstr>" + buf[pos:]
    new = new[:m.start(1)] + str(n + 1).encode() + new[m.end(1):]
    t2 = re.search(rb'<TitlesOfParts><vt:vector size="(\d+)"', new)
    return new[:t2.start(1)] + str(size + 1).encode() + new[t2.end(1):]


def _set_sheet_state(buf: bytes, name: str, state: str) -> bytes:
    """Change the state attribute on OUR sheet element only."""
    pat = re.compile(rb'<([A-Za-z_][\w.-]*:)?sheet\b[^>]*\bname="' +
                     re.escape(_xml_escape(name).encode()) + rb'"[^>]*/>')
    m = pat.search(buf)
    if not m:
        raise BrainError("brain sheet element not found in workbook.xml")
    el = m.group(0)
    el2 = re.sub(rb'\s+state="[^"]*"', b"", el)
    if state != "visible":            # right after sheetId, where the first write put it
        el2 = re.sub(rb'(\bsheetId="[^"]*")', rb'\1 state="' + state.encode() + b'"', el2, count=1)
    return buf[:m.start()] + el2 + buf[m.end():]


# --------------------------------------------------------------------------
# zip write (atomic, verified)
# --------------------------------------------------------------------------
def _write_zip(pkg: Package, new_data: dict, added: list, dst: str,
               drop: tuple = ()) -> None:
    """Build the new package in memory, verify it in memory, then write it.

    An existing file is rewritten IN PLACE (same inode), never replaced by a
    renamed temp file: a rename drops extended attributes such as macOS's
    com.apple.quarantine, which silently turns off Excel's Protected View for
    a downloaded workbook. The original bytes are held in memory and restored
    if anything fails after the write starts."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zout:
        for info in pkg.infos:               # original order, original ZipInfo
            if info.filename in drop:
                continue
            zi = zipfile.ZipInfo(info.filename, info.date_time)
            zi.compress_type = info.compress_type
            zi.external_attr = info.external_attr
            zi.create_system = info.create_system
            zout.writestr(zi, new_data[info.filename])
        for zi in added:
            zout.writestr(zi, new_data[zi.filename])
        zout.comment = pkg.comment
    blob = buf.getvalue()
    with zipfile.ZipFile(io.BytesIO(blob)) as zchk:
        bad = zchk.testzip()
        if bad:
            raise BrainError(f"CRC failure in {bad}")
        for info in pkg.infos:
            if info.filename in drop:
                continue
            if zchk.read(info.filename) != new_data[info.filename]:
                raise BrainError(f"verify failed: {info.filename}")
    if not os.path.exists(dst):
        fd, tmp = tempfile.mkstemp(suffix=".xlsx.tmp", dir=os.path.dirname(os.path.abspath(dst)))
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(blob)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, dst)             # a brand-new file has no attributes to lose
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        return
    with open(dst, "rb") as fh:
        original = fh.read()
    try:
        with open(dst, "r+b") as fh:
            fh.seek(0)
            fh.write(blob)
            fh.truncate()
            fh.flush()
            os.fsync(fh.fileno())
        with open(dst, "rb") as fh:
            if fh.read() != blob:
                raise BrainError("the file did not read back as written")
    except BaseException:
        with open(dst, "r+b") as fh:
            fh.seek(0)
            fh.write(original)
            fh.truncate()
            fh.flush()
            os.fsync(fh.fileno())
        raise


def lock_file_for(path: str) -> str:
    d, f = os.path.split(os.path.abspath(path))
    return os.path.join(d, "~$" + f)


def is_open_elsewhere(path: str) -> bool:
    """Excel's ~$ owner file or LibreOffice's .~lock.<name># next to the workbook."""
    d, f = os.path.split(os.path.abspath(path))
    return (os.path.exists(os.path.join(d, "~$" + f))
            or os.path.exists(os.path.join(d, f".~lock.{f}#")))


@dataclass
class WriteResult:
    mode: str                     # added | replaced | unchanged | removed
    brain_part: str
    changed_parts: list
    untouched_parts: int
    warnings: list
    backup: str | None
    path: str
    drawing_part: str | None = None   # the brain drawing's part, when the tab has one
    view_parts: list = field(default_factory=list)   # other tabs whose only change is "no longer selected"
    new_parts: list = field(default_factory=list)    # parts this write added (all of them ours)


def find_brain_sheet(pkg: Package) -> SheetRef | None:
    for s in pkg.sheets:
        if s.name.lower() == BRAIN_SHEET.lower():
            return s
    return None


def _is_brain_part(pkg: Package, ref: SheetRef) -> bool:
    try:
        rows, _ = _rows_from_part(pkg, ref, max_rows=1)
    except Exception:
        return False
    return bool(rows and rows[0] and rows[0][0].startswith("spreadsheet-brain "))


def _drop_drawings(pkg: Package, sheet_part: str, new_data: dict, drop: list) -> None:
    """Take every drawing off the brain sheet: its relationship, its part, the
    drawing's own rels and its content-type override. The sheet's rels part
    goes too once nothing is left in it. A drawing some other part also points
    at is left alone."""
    sheet_rels = rels_part_for(sheet_part)
    ct = "[Content_Types].xml"
    current = _drawing_rels(pkg, sheet_part)
    for rid, d_part in current:
        new_data[sheet_rels] = _remove_rel(new_data[sheet_rels], rid)
        if d_part in pkg.data and not _referenced_elsewhere(pkg, d_part, sheet_rels):
            drop.append(d_part)
            d_rels = rels_part_for(d_part)
            if d_rels in pkg.data:
                drop.append(d_rels)
            new_data[ct] = _remove_override(new_data[ct], d_part)
    if current and not re.search(rb"<([A-Za-z_][\w.-]*:)?Relationship\b", new_data[sheet_rels]):
        drop.append(sheet_rels)


def _check_insertions(inserted: dict, new_data: dict) -> None:
    for p, (base, ins) in inserted.items():
        if new_data[p].replace(ins, b"", 1) != base:
            raise BrainError(f"splice in {p} was not a pure insertion")


def _visible_indexes(pkg: Package, skip: SheetRef | None, removing: bool = False) -> list:
    """Positions of the visible tabs other than `skip`, as they will be once
    `skip` is removed (removing=True) or as they are."""
    k = pkg.sheets.index(skip) if skip in pkg.sheets else len(pkg.sheets)
    return [i - 1 if removing and i > k else i for i, s in enumerate(pkg.sheets)
            if s is not skip and s.state == "visible"]


def write_brain(src: str, records: list, *, dst: str | None = None,
                state: str = "visible", backup_dir: str | None = None,
                update_app_xml: bool = True, graph: dict | None = None) -> WriteResult:
    """Add or replace the brain sheet. dst=None writes in place after a backup.

    First write touches: the new sheet part, workbook.xml, its rels,
    [Content_Types].xml and (best effort) docProps/app.xml. A visible brain is
    also the tab the workbook opens on: activeTab in workbook.xml points at it
    and the tab that was selected loses its tabSelected flag (one start tag in
    that tab's part). Both edits are recorded in the brain tab and put back
    when the brain is removed or hidden. Later writes touch ONLY the brain
    sheet part (plus workbook.xml when the state changes, and the two view
    edits again if an app moved the selection since).

    graph (the dict sheetbrain.graph.build returns) also draws the brain on the
    tab, to the right of the table, and the tab opens scrolled to the drawing.
    That adds three parts of our own (the drawing, the brain sheet's rels, one
    content-type override) and later writes redraw it in place. A write with
    no graph takes an earlier drawing away, so the picture can never show a
    note the table no longer has. If the drawing cannot be made, the notes are
    still written and a warning says so.
    """
    if state not in ("visible", "hidden"):
        raise BrainError("state must be visible or hidden (never veryHidden)")
    if is_open_elsewhere(src):
        raise BrainError("This workbook looks open in Excel or LibreOffice. Close it and try again.")
    pkg = load_package(src)
    if pkg.refusals:
        raise BrainError(pkg.refusals[0])
    warnings = list(pkg.warnings)
    rows = records_to_rows(records)
    drawing_xml, view_col, grid = None, None, None
    if graph is not None:
        try:
            from . import xldraw
            origin_x, view_col = drawing_origin(len(rows[0]))
            drawn = xldraw.drawing_parts(graph, origin_emu_x=origin_x, first_col=view_col)
            drawing_xml, grid = drawn["xml"], drawn.get("cols")
        except Exception as e:  # noqa: BLE001  (the picture never costs the notes)
            drawing_xml, view_col, grid = None, None, None
            warnings.append(f"The drawing of the brain could not be made ({type(e).__name__}), "
                            "so the tab holds the notes only.")
    ct = "[Content_Types].xml"
    wb_part = pkg.workbook_part
    new_data = dict(pkg.data)
    inserted: dict[str, tuple[bytes, bytes]] = {}     # part -> (bytes before, inserted bytes)
    added: list = []
    drop: list = []
    d_part = None
    ref_info = next(i for i in pkg.infos if i.filename == wb_part)
    selected = state == "visible"

    def new_entry(name: str, data: bytes) -> None:
        zi = zipfile.ZipInfo(name, ref_info.date_time)
        zi.compress_type = zipfile.ZIP_DEFLATED
        added.append(zi)
        new_data[name] = data

    existing = find_brain_sheet(pkg)
    if existing:
        if not existing.part or existing.part not in pkg.data:
            raise BrainError("The brain tab is listed but its data is missing.")
        if not _is_brain_part(pkg, existing):
            raise BrainError(f"A tab named {existing.name!r} already exists and it is "
                             "not a brain. I won't touch it.")
        if b't="s"' in pkg.data[existing.part]:
            warnings.append("A spreadsheet app saved this file since the last brain "
                            "update, so old brain text stays in the file's string table "
                            "until the next save in that app.")
        part = existing.part
        sheet_rels = rels_part_for(part)
        current = [(r, p) for r, p in _drawing_rels(pkg, part) if p in pkg.data]
        d_rid = None
        if drawing_xml is not None and current:
            d_rid, d_part = current[0]                  # redraw in place, same part, same rId
            new_data[d_part] = drawing_xml
            if not _has_override(pkg.data[ct], d_part):
                new_data[ct], ins = _add_override(pkg.data[ct], d_part, CT_DRAWING)
                inserted[ct] = (pkg.data[ct], ins)
        elif drawing_xml is not None:
            _drop_drawings(pkg, part, new_data, drop)   # a stale link to a missing part, if any
            d_part = _new_drawing_part(pkg)
            target = posixpath.relpath(d_part, posixpath.dirname(part))
            if sheet_rels in pkg.data and sheet_rels not in drop:
                base = new_data[sheet_rels]
                d_rid = _free_rid(base)
                new_data[sheet_rels], ins = _add_rel(base, d_rid, target, REL_DRAWING)
                inserted[sheet_rels] = (base, ins)
            else:
                if sheet_rels in drop:
                    drop.remove(sheet_rels)
                    new_data[sheet_rels] = _sheet_rels_xml("rId1", target)
                else:
                    new_entry(sheet_rels, _sheet_rels_xml("rId1", target))
                d_rid = "rId1"
            new_entry(d_part, drawing_xml)
            base = new_data[ct]
            new_data[ct], ins = _add_override(base, d_part, CT_DRAWING)
            inserted[ct] = (base, ins)
        else:
            _drop_drawings(pkg, part, new_data, drop)
        if existing.state != state:
            new_data[wb_part] = _set_sheet_state(pkg.data[wb_part], existing.name, state)
        _check_insertions(inserted, new_data)
        brain_index = pkg.sheets.index(existing)
        prior = _read_put_back(pkg, part)
        if selected:
            put_back = _open_on_brain(pkg, new_data, brain_index, part, prior)
        else:
            missed = _put_back(pkg, new_data, prior)
            if missed or not prior:
                new_data[wb_part] = _fix_active_tab(new_data[wb_part], brain_index,
                                                    _visible_indexes(pkg, existing), removing=False)
            put_back = []
        new_data[part] = build_sheet_xml(rows, drawing_rid=d_rid,
                                         view_col=view_col if d_rid else None,
                                         grid=grid if d_rid else None,
                                         selected=selected, put_back=put_back)
        changed = [p for p in pkg.data if p not in drop and new_data[p] != pkg.data[p]]
        changed += [zi.filename for zi in added] + drop
        mode = "replaced" if changed else "unchanged"
    else:
        wb_dir = wb_part.rsplit("/", 1)[0]
        lower = {n.lower() for n in pkg.data}
        n = 1
        while (f"{wb_dir}/worksheets/sheet{n}.xml".lower() in lower
               or f"{wb_dir}/worksheets/_rels/sheet{n}.xml.rels".lower() in lower):
            n += 1
        part = f"{wb_dir}/worksheets/sheet{n}.xml"
        rels_part = rels_part_for(wb_part)
        rid = _free_rid(pkg.data[rels_part])
        sheet_id = max((s.sheet_id for s in pkg.sheets), default=0) + 1
        wb_new, ins = _add_to_workbook_xml(pkg.data[wb_part], BRAIN_SHEET, sheet_id, rid, state)
        new_data[wb_part], inserted[wb_part] = wb_new, (pkg.data[wb_part], ins)
        rel_new, ins = _add_rel(pkg.data[rels_part], rid, f"worksheets/sheet{n}.xml")
        new_data[rels_part], inserted[rels_part] = rel_new, (pkg.data[rels_part], ins)
        if drawing_xml is not None:
            d_part = _new_drawing_part(pkg)
        extra = ((d_part, CT_DRAWING),) if d_part else ()
        ct_new, ins = _add_override(pkg.data[ct], part, CT_WORKSHEET, extra)
        new_data[ct], inserted[ct] = ct_new, (pkg.data[ct], ins)
        if update_app_xml and "docProps/app.xml" in pkg.data:
            app_new = _add_to_app_xml(pkg.data["docProps/app.xml"], BRAIN_SHEET)
            if app_new is not None:
                new_data["docProps/app.xml"] = app_new
        _check_insertions(inserted, new_data)
        put_back = (_open_on_brain(pkg, new_data, len(pkg.sheets), part, [])
                    if selected else [])
        new_entry(part, build_sheet_xml(rows, drawing_rid="rId1" if d_part else None,
                                        view_col=view_col if d_part else None,
                                        grid=grid if d_part else None,
                                        selected=selected, put_back=put_back))
        if d_part:
            sheet_rels = rels_part_for(part)
            new_entry(sheet_rels, _sheet_rels_xml(
                "rId1", posixpath.relpath(d_part, posixpath.dirname(part))))
            new_entry(d_part, drawing_xml)
        changed = [p for p in pkg.data if new_data[p] != pkg.data[p]]
        changed += [zi.filename for zi in added]
        mode = "added"
    theirs = {s.part for s in pkg.sheets} - {part}
    view_parts = [p for p in changed if p in theirs]
    new_parts = [zi.filename for zi in added]
    target = dst or src
    backup = None
    if mode == "unchanged":
        if dst and os.path.abspath(dst) != os.path.abspath(src):
            shutil.copy2(src, dst)
        return WriteResult(mode, part, [], len(pkg.infos), warnings, None, target, d_part)
    if dst is None:
        backup = make_backup(src, backup_dir)
    _write_zip(pkg, new_data, added, target, drop=tuple(drop))
    if dst and os.path.abspath(dst) != os.path.abspath(src):
        shutil.copymode(src, dst)          # a copy keeps the original's permissions
    untouched = sum(1 for i in pkg.infos if i.filename not in changed)
    return WriteResult(mode, part, changed, untouched, warnings, backup, target, d_part,
                       view_parts, new_parts)


def make_backup(src: str, backup_dir: str | None) -> str:
    bdir = backup_dir or os.path.dirname(os.path.abspath(src))
    os.makedirs(bdir, exist_ok=True)
    import datetime as _dt
    stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = os.path.join(bdir, f"{stamp}__{os.path.basename(src)}")
    shutil.copy2(src, backup)
    return backup


def remove_brain(src: str, *, dst: str | None = None,
                 backup_dir: str | None = None) -> WriteResult:
    """The inverse splice: put back the tab selection the brain took (recorded
    in the brain tab), drop our sheet element, relationship, override and part,
    and the brain drawing with its rels and override when the tab has one.
    If no app saved the file since our write, the result is the original,
    byte for byte. If one did, the workbook still opens on a visible tab."""
    pkg = load_package(src)
    ref = find_brain_sheet(pkg)
    if ref is None:
        raise BrainError("This workbook has no brain tab.")
    if not _is_brain_part(pkg, ref):
        raise BrainError(f"The tab {ref.name!r} is not a brain; not removing it.")
    new_data = dict(pkg.data)
    wb_part = pkg.workbook_part
    entries = _read_put_back(pkg, ref.part)
    missed = _put_back(pkg, new_data, entries)
    pat = re.compile(rb'<([A-Za-z_][\w.-]*:)?sheet\b[^>]*\bname="' +
                     re.escape(_xml_escape(ref.name).encode()) + rb'"[^>]*/>')
    new_data[wb_part] = pat.sub(b"", new_data[wb_part], count=1)
    if missed or not entries:
        new_data[wb_part] = _fix_active_tab(new_data[wb_part], pkg.sheets.index(ref),
                                            _visible_indexes(pkg, ref, removing=True), removing=True)
    rels_part = rels_part_for(wb_part)
    new_data[rels_part] = _remove_rel(pkg.data[rels_part], ref.rid)
    new_data["[Content_Types].xml"] = _remove_override(pkg.data["[Content_Types].xml"], ref.part)
    drop = [ref.part]
    sheet_rels = rels_part_for(ref.part)
    if sheet_rels in pkg.data:
        _drop_drawings(pkg, ref.part, new_data, drop)
        if sheet_rels not in drop:
            drop.append(sheet_rels)        # the rels of a sheet that is gone
    app = pkg.data.get("docProps/app.xml")
    if app is not None:
        m = re.search(rb"<vt:lpstr>Worksheets</vt:lpstr></vt:variant>\s*"
                      rb"<vt:variant><vt:i4>(\d+)</vt:i4>", app)
        t = re.search(rb'<TitlesOfParts><vt:vector size="(\d+)"', app)
        title = b"<vt:lpstr>" + _xml_escape(ref.name).encode() + b"</vt:lpstr>"
        if m and t and title in app:
            a2 = app.replace(title, b"", 1)
            m2 = re.search(rb"<vt:lpstr>Worksheets</vt:lpstr></vt:variant>\s*"
                           rb"<vt:variant><vt:i4>(\d+)</vt:i4>", a2)
            a2 = a2[:m2.start(1)] + str(int(m2.group(1)) - 1).encode() + a2[m2.end(1):]
            t2 = re.search(rb'<TitlesOfParts><vt:vector size="(\d+)"', a2)
            a2 = a2[:t2.start(1)] + str(int(t2.group(1)) - 1).encode() + a2[t2.end(1):]
            new_data["docProps/app.xml"] = a2
    changed = [p for p in new_data if p in pkg.data and p not in drop and new_data[p] != pkg.data[p]]
    backup = make_backup(src, backup_dir) if dst is None else None
    _write_zip(pkg, new_data, [], dst or src, drop=tuple(drop))
    theirs = {s.part for s in pkg.sheets} - {ref.part}
    return WriteResult("removed", ref.part, changed + drop,
                       len(pkg.infos) - len(changed) - len(drop), [], backup, dst or src,
                       view_parts=[p for p in changed if p in theirs])


# --------------------------------------------------------------------------
# reading the brain (no spreadsheet library needed)
# --------------------------------------------------------------------------
_UNESC = re.compile(r"_x([0-9A-Fa-f]{4})_")


def _ooxml_unescape(s: str) -> str:
    return _UNESC.sub(lambda m: chr(int(m.group(1), 16)), s)


def _shared_strings(pkg: Package) -> list[str]:
    ns = f"{{{NS_MAIN}}}"
    part = next((n for n in pkg.data if n.lower().endswith("sharedstrings.xml")), None)
    out: list[str] = []
    if not part:
        return out
    for si in parse_xml(pkg.data[part]).iter(f"{ns}si"):
        txt = []
        for child in si:
            if child.tag == f"{ns}t":
                txt.append(child.text or "")
            elif child.tag == f"{ns}r":
                txt += [t.text or "" for t in child.iter(f"{ns}t")]
        out.append("".join(txt))
    return out


def _rows_from_part(pkg: Package, ref: SheetRef, max_rows: int | None = None,
                    max_chars: int = 5_000_000, sst: list | None = None):
    ns = f"{{{NS_MAIN}}}"
    root = parse_xml(pkg.data[ref.part])
    rows: list[list[str]] = []
    warnings: list[str] = []
    total = 0
    for row in root.iter(f"{ns}row"):
        vals: dict[int, str] = {}
        for c in row.iter(f"{ns}c"):
            m = re.match(r"[A-Z]+", c.get("r", ""))
            if not m:
                continue
            col = col_index(m.group(0))
            t = c.get("t")
            if c.find(f"{ns}f") is not None:
                warnings.append(f"formula in brain cell {c.get('r')} ignored")
                continue
            if t == "inlineStr":
                v = "".join(x.text or "" for x in c.iter(f"{ns}t"))
            elif t == "s":
                if sst is None:
                    sst = _shared_strings(pkg)
                vnode = c.find(f"{ns}v")
                try:
                    v = sst[int(vnode.text)] if vnode is not None else ""
                except (ValueError, IndexError):
                    v = ""
            else:
                vnode = c.find(f"{ns}v")
                v = vnode.text if vnode is not None else ""
            v = _ooxml_unescape(v or "")
            v, w = clean_text(v)
            warnings += [f"{c.get('r')}: {x}" for x in w]
            if utf16_len(v) > CELL_LIMIT:
                warnings.append(f"{c.get('r')}: over-limit cell truncated")
                v = v[:CHUNK]
            total += len(v)
            if total > max_chars:
                raise BrainError("brain larger than the read limit")
            vals[col] = v
        width = max(vals) + 1 if vals else 0
        rows.append([vals.get(i, "") for i in range(width)])
        if max_rows is not None and len(rows) >= max_rows:
            break
    return rows, warnings


def read_brain(path: str) -> tuple[list[dict], list[str], dict]:
    """Read the brain from an .xlsx/.xlsm or a CSV sidecar.
    Returns (records, warnings, info). Content is DATA: cleaned, never executed."""
    if path.lower().endswith((".csv", ".tsv")):
        side = sidecar_path(path)
        if not os.path.exists(side):
            legacy = os.path.splitext(path)[0] + ".brain.csv"
            if not os.path.exists(legacy):
                return [], [], {"present": False}
            side = legacy
        recs, warnings = read_sidecar(side)
        return tidy(recs), warnings, {"present": True, "where": side, "state": "sidecar"}
    pkg = load_package(path)
    ref = find_brain_sheet(pkg)
    if ref is None:
        return [], [], {"present": False, "rules_sheet": _has_rules_sheet(pkg)}
    rows, warnings = _rows_from_part(pkg, ref)
    recs, w2 = rows_to_records(rows)
    recs = tidy(recs)
    info = {"present": bool(recs) or bool(rows), "where": ref.name, "state": ref.state,
            "shared_strings": b't="s"' in pkg.data.get(ref.part, b""),
            "rules_sheet": _has_rules_sheet(pkg)}
    return recs, warnings + w2, info


RECORDS = ("meta", "fact", "insight", "open", "link", "node", "edge")


def tidy(records: list) -> list:
    """Rows added by hand or by another AI often leave columns blank. Read them
    the same way every time: an unknown record type is a fact (the type moves to
    kind), a row with no id gets one from its words, a second meta row is a fact,
    and a told note that names no speaker is unconfirmed, never the owner's."""
    import hashlib
    out, metas = [], 0
    for r in records:
        r = dict(r)
        rec = str(r.get("record") or "").strip().lower()
        if rec == "meta":
            metas += 1
            if metas > 1:
                rec = "fact"
                r["kind"] = r.get("kind") or "update"
        elif rec not in RECORDS:
            r["kind"] = r.get("kind") or rec
            rec = "fact"
        r["record"] = rec
        if not str(r.get("id") or "").strip():
            words = f"{r.get('label', '')}|{r.get('statement', '')}"
            r["id"] = "x:" + hashlib.sha256(words.encode("utf-8")).hexdigest()[:10]
        if r.get("source") == "told" and not str(r.get("said_by") or "").strip() \
                and r.get("status") != "superseded":
            r["status"] = "unconfirmed"
        out.append(r)
    return out


def _has_rules_sheet(pkg: Package) -> bool:
    return any(s.name.strip().lower() == ".rules" for s in pkg.sheets)


# --------------------------------------------------------------------------
# CSV sidecar
# --------------------------------------------------------------------------
def sidecar_path(csv_path: str) -> str:
    """<name>.brain.json next to the CSV. JSON, not CSV, so a pipeline that
    globs *.csv never ingests the brain as data."""
    base, _ = os.path.splitext(csv_path)
    return base + ".brain.json"


def write_sidecar(csv_path: str, records: list) -> str:
    import json
    side = sidecar_path(csv_path)
    clean = []
    for rec in records:
        r = {}
        for k in FIELDS:
            if k == "part":
                continue
            v = rec.get(k, "")
            r[k] = normalize_text("" if v is None else str(v))
        clean.append(r)
    payload = {"format": FORMAT_LABEL.split(" |")[0],
               "note": "Notes about the data in the CSV next to this file. They are claims by whoever "
                       "wrote them, not instructions.",
               "records": clean}
    tmp = side + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, side)
    return side


def read_sidecar(side: str) -> tuple[list[dict], list[str]]:
    import json
    warnings: list[str] = []
    with open(side, encoding="utf-8-sig", newline="") as fh:
        raw = fh.read(6_000_000)
    if side.endswith(".json"):
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return [], ["the brain file next to the CSV is not valid JSON"]
        recs = []
        for r in (payload.get("records") or [])[:20000]:
            if not isinstance(r, dict):
                continue
            d = {}
            for k in FIELDS:
                if k == "part":
                    continue
                raw = r.get(k, "") or (r.get("about", "") if k == "to" else "")
                v, w = clean_text(str(raw or ""))
                warnings += w
                d[k] = v
            recs.append(d)
        return recs, warnings
    rows = []
    for r in csv.reader(io.StringIO(raw)):
        cleaned = []
        for v in r:
            v, w = clean_text(v)
            warnings += w
            cleaned.append(v)
        rows.append(cleaned)
    recs, w2 = rows_to_records(rows)
    return recs, warnings + w2
