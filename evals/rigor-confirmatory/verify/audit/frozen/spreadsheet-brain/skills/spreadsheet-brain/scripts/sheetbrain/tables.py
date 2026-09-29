"""Find the tables inside a sheet: title rows, header rows (including merged,
multi-row headers), stacked or side-by-side tables, totals rows, and wide
(pivoted) layouts. Pure Python over the rows from workbook.py.
"""
from __future__ import annotations

import bisect
import datetime as dt
import re
from dataclasses import dataclass, field

_TOTAL = re.compile(r"^\s*(grand\s+)?(sub\s*)?totals?\b|^\s*total\s|\bsub-?totals?\b|\bgrand\s+totals?\b|"
                    r"^[\w&/.\- ]{1,30}\s+totals?\s*$", re.I)
_PERIOD = re.compile(
    r"^(q[1-4]|h[12]|fy\s?\d{2,4}|\d{4}|(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)"
    r"[a-z]*[\s\-']*\d{0,4}|m\d{1,2}|month\s*\d+|week\s*\d+|p\d{1,2})$", re.I)


@dataclass
class Table:
    sheet: str
    tid: str
    top: int                     # first row of the block (0-based)
    header_rows: list            # row indexes used as header
    first_data: int
    last_row: int                # inclusive
    cols: list                   # column indexes in the sheet
    headers: list                # header text per col (unique)
    rows: list = field(default_factory=list)       # data rows restricted to cols
    row_index: list = field(default_factory=list)  # sheet row index per data row
    totals_rows: list = field(default_factory=list)
    title: str = ""
    wide: bool = False
    row_label_col: int = -1      # position in cols of the row-label column (wide tables)
    notes: list = field(default_factory=list)
    # stacked exports: one {"header_row", "headers", "start"} per header set, in order, when
    # the body repeats its header; "start" is the sheet row of the segment's first data row
    segments: list = field(default_factory=list)
    # cells read from text: position in cols -> {"kind": "date" | "number", "format",
    # "rows": sheet rows of the retyped cells, "examples": the text as written}
    retyped: dict = field(default_factory=dict)

    @property
    def n_rows(self) -> int:
        return len(self.rows)

    def column(self, j: int) -> list:
        return [r[j] if j < len(r) else None for r in self.rows]

    def ref(self) -> str:
        return self.tid


def _nonempty(v) -> bool:
    return v is not None and not (isinstance(v, str) and v.strip() == "")


def _is_str(v) -> bool:
    return isinstance(v, str) and v.strip() != ""


def _is_period(v) -> bool:
    if isinstance(v, (dt.datetime, dt.date)):
        return True
    if isinstance(v, str):
        return bool(_PERIOD.match(v.strip()))
    if isinstance(v, int) and 1990 <= v <= 2100:
        return True
    return False


def find_tables(sheet, max_tables: int = 12) -> list:
    rows = sheet.values
    if not rows:
        return []
    width = max(len(r) for r in rows)
    grid = [list(r) + [None] * (width - len(r)) for r in rows]
    # fill merged ranges into a working copy so merged headers read everywhere
    filled = [list(r) for r in grid]
    merged_top_rows = set()
    for (r1, c1, r2, c2) in sheet.merged:
        if r1 >= len(filled):
            continue
        v = grid[r1][c1] if c1 < width else None
        for r in range(r1, min(r2, len(filled) - 1) + 1):
            for c in range(c1, min(c2, width - 1) + 1):
                if filled[r][c] is None:
                    filled[r][c] = v
        if c2 > c1:
            merged_top_rows.add(r1)

    counts = [sum(1 for v in r if _nonempty(v)) for r in grid]
    bands = []
    start = None
    for i, n in enumerate(counts + [0]):
        if n and start is None:
            start = i
        elif not n and start is not None:
            bands.append((start, i - 1))
            start = None

    blocks = []
    for (a, b) in bands:
        occupied = [any(_nonempty(grid[r][c]) for r in range(a, b + 1)) for c in range(width)]
        c = 0
        while c < width:
            if not occupied[c]:
                c += 1
                continue
            s = c
            while c < width and occupied[c]:
                c += 1
            blocks.append((a, b, s, c - 1))

    tables: list = []
    pending_title = ""
    pending_header = None
    trimmed = []
    for (a, b, c1, c2) in blocks:
        rows_in = [r for r in range(a, b + 1) if any(_nonempty(grid[r][c]) for c in range(c1, c2 + 1))]
        trimmed.append((rows_in[0], rows_in[-1], c1, c2))
    for bi, (a, b, c1, c2) in enumerate(trimmed):
        ncols = c2 - c1 + 1
        span_rows = b - a + 1
        # a one-column band of up to 5 text rows above a table is its title
        title_band = (ncols == 1 and span_rows <= 5 and any(x[0] > b for x in trimmed[bi + 1:])
                      and all(_is_str(grid[r][c1]) for r in range(a, b + 1) if _nonempty(grid[r][c1])))
        # single cells or tiny bands are titles or notes, not tables
        if ncols == 1 and span_rows <= 2 or span_rows == 1 and ncols <= 2 or title_band:
            text = " ".join(str(grid[r][c]) for r in range(a, b + 1) for c in range(c1, c2 + 1)
                            if _nonempty(grid[r][c]))
            if tables and a > tables[-1].last_row:
                tables[-1].notes.append(text[:200])
            pending_title = text[:200]
            continue
        t = _build_table(sheet.name, grid, filled, merged_top_rows, a, b, c1, c2,
                         len(tables), pending_title)
        pending_title = ""
        if t is not None and not t.rows and t.header_rows:
            pending_header = t              # a header whose data starts after a blank row
            continue
        if t is not None and t.rows and not t.header_rows and pending_header is not None \
                and set(t.cols) & set(pending_header.cols) and t.top - pending_header.last_row <= 3:
            _adopt_header(t, pending_header)
        pending_header = None
        if t is None or not t.rows or (len(t.rows) == 1 and len(t.cols) <= 2 and not t.wide):
            if t is not None and tables:
                tables[-1].notes.append(" ".join(t.headers)[:200])
            continue
        # continuation of the previous table after a blank row (no header of its own)
        prev = tables[-1] if tables else None
        if (prev and t.header_rows == [] and prev.cols == t.cols and not t.wide):
            prev.rows.extend(t.rows)
            prev.row_index.extend(t.row_index)
            prev.totals_rows.extend(t.totals_rows)
            prev.last_row = t.last_row
            continue
        tables.append(t)
        if len(tables) >= max_tables:
            break
    for i, t in enumerate(tables):
        t.tid = sheet.name if len(tables) == 1 else f"{sheet.name}#{i + 1}"
        _retype(t)
        _sum_totals(t)
    return tables


def _header_score(row: list) -> float:
    vals = [v for v in row if _nonempty(v)]
    if not vals:
        return 0.0
    # a row holding a plain number (not a year or period) is data, not a header
    if any(isinstance(v, (int, float)) and not isinstance(v, bool) and not _is_period(v) for v in vals):
        return 0.0
    strs = sum(1 for v in vals if _is_str(v) or _is_period(v))
    return strs / len(row)


def _adopt_header(t, h) -> None:
    """A header row separated from its data by a blank line (common in models):
    the data block takes the header block's labels, column by column."""
    by_col = dict(zip(h.cols, h.headers))
    t.headers = _unique([by_col.get(c, t.headers[i]) for i, c in enumerate(t.cols)])
    t.header_rows = list(h.header_rows)
    t.title = t.title or h.title
    periods = sum(1 for c in t.cols if c in by_col and _is_period_text(by_col[c]))
    if periods >= 4:
        t.wide = True
        for j in range(len(t.cols)):
            col = [r[j] for r in t.rows if j < len(r)]
            if col and sum(1 for v in col if _is_str(v)) >= 0.6 * len(col):
                t.row_label_col = j
                break


def _is_period_text(s: str) -> bool:
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s or ""):
        return True
    return _is_period(s)


def _build_table(sheet_name, grid, filled, merged_top_rows, a, b, c1, c2, idx, title):
    width = c2 - c1 + 1
    sub = [grid[r][c1:c2 + 1] for r in range(a, b + 1)]
    fsub = [filled[r][c1:c2 + 1] for r in range(a, b + 1)]
    # title rows: one non-empty cell (or one merged run) across a wide block
    k = 0
    title_parts = [title] if title else []
    while k < len(sub) - 1 and width >= 3:
        nonempty = [v for v in sub[k] if _nonempty(v)]
        if len(nonempty) == 1 and _is_str(nonempty[0]):
            title_parts.append(str(nonempty[0])[:200])
            k += 1
            continue
        break
    # header row: first row (within the first 8) that is mostly strings, followed
    # by a row that is not
    header_rows = []
    h = None
    for i in range(k, min(k + 8, len(sub))):
        sc = _header_score(fsub[i])
        last = i + 1 >= len(sub)
        nxt = _header_score(fsub[i + 1]) if not last else sc   # the last row is never a header by itself
        if sc >= 0.5 and (nxt < sc or (i == k and last)):
            h = i
            break
        if sc >= 0.5 and i + 1 < len(sub):
            # two string rows in a row: multi-row header if the top one is merged
            if (a + i) in merged_top_rows and _header_score(fsub[i + 1]) >= 0.5:
                h = i + 1
                header_rows = [a + i]
                break
    if h is None:
        if _header_score(fsub[k]) >= 0.5:
            h = k
        else:
            h = -1
    headers: list = []
    if h >= 0:
        header_rows = header_rows + [a + h]
        for c in range(width):
            parts = []
            for hr in header_rows:
                v = filled[hr][c1 + c]
                if _nonempty(v):
                    sv = _fmt_header(v)
                    if not parts or parts[-1] != sv:
                        parts.append(sv)
            headers.append(" ".join(parts) if parts else f"Column {_letter(c1 + c)}")
        data_start = h + 1
    else:
        headers = [f"Column {_letter(c1 + c)}" for c in range(width)]
        data_start = k
    headers = _unique(headers)
    t = Table(sheet_name, sheet_name, a, header_rows, a + data_start, b,
              list(range(c1, c2 + 1)), headers, title=" . ".join(p for p in title_parts if p))
    body = []
    for i in range(data_start, len(sub)):
        row = sub[i]
        first_text = next((v for v in row if _is_str(v)), "")
        if first_text and is_total_label(first_text):
            t.totals_rows.append(a + i)
            continue
        if not any(_nonempty(v) for v in row):
            continue
        body.append((a + i, list(row)))
    if h >= 0:
        body = _split_segments(t, body)
    for r, row in body:
        t.rows.append(row)
        t.row_index.append(r)
    if h < 0 and not t.rows:
        return None
    _fill_down(t)
    # wide layout: 4+ header cells that are periods
    periods = sum(1 for x in (grid[header_rows[-1]][c1:c2 + 1] if header_rows else []) if _is_period(x))
    if periods >= 4:
        t.wide = True
        for j in range(width):
            col = [r[j] for r in t.rows if j < len(r)]
            if col and sum(1 for v in col if _is_str(v)) >= 0.6 * len(col):
                t.row_label_col = j
                break
    return t


def is_total_label(s) -> bool:
    """'Total', 'Grand total', '*** RUN TOTAL ***': punctuation is dropped
    before the label is read."""
    return bool(_TOTAL.match(" ".join(re.sub(r"[^0-9A-Za-z]+", " ", str(s)).split())))


def _norm_label(v) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(v).lower()) if _nonempty(v) else ""


_DATE_TEXT = re.compile(r"^\s*\d{1,4}[/\-.]\d{1,2}[/\-.]\d{1,4}(\s+\d{1,2}:\d{2}(:\d{2})?)?\s*$")
_NUM_TEXT = re.compile(r"^\s*\(?\s*-?\s*[$€£]?\s*-?(\d{1,3}(,\d{3})+|\d+)?(\.\d+)?\s*\)?\s*$")


def _num_text(s):
    """'$179.00', '1,250', '(42.10)' as a number; None for anything else,
    including codes kept with a leading zero ('0042')."""
    if not isinstance(s, str) or not _NUM_TEXT.match(s) or not any(ch.isdigit() for ch in s):
        return None
    core = re.sub(r"[\s$€£,()]", "", s)
    digits = core.lstrip("-")
    if digits.startswith("0") and len(digits) > 1 and not digits.startswith("0."):
        return None
    try:
        n = float(core) if "." in core else int(core)
    except ValueError:
        return None
    return -abs(n) if s.strip().startswith("(") and s.strip().endswith(")") else n


def _cell_kind(v) -> str:
    """number, date, or text; text that reads as a number or a date counts as typed."""
    if isinstance(v, bool):
        return "text"
    if isinstance(v, (int, float, dt.datetime, dt.date)):
        return "typed"
    if isinstance(v, str) and (_DATE_TEXT.match(v) or _num_text(v) is not None):
        return "typed"
    return "text"


_PLACEHOLDER = {"n/a", "na", "tbd", "tba", "pending", "none", "null", "void", "unknown", "-", "?"}


def _split_segments(t, body: list) -> list:
    """Two exports pasted one under the other: a row in the body that repeats the
    header, or puts header-like text into the number and date columns, ends one
    segment and starts the next. The row leaves the data; its labels are kept."""
    if len(body) < 4:
        return body
    from .profile import shape
    width = len(t.headers)
    labels = {_norm_label(h) for h in t.headers if not h.startswith("Column ")}
    filled = [0] * width
    typed = [0] * width
    shapes = [{} for _ in range(width)]
    seen = [{} for _ in range(width)]
    for _, row in body:
        for j, v in enumerate(row[:width]):
            if _nonempty(v):
                filled[j] += 1
                typed[j] += _cell_kind(v) == "typed"
                s, k = shape(v), _norm_label(v)
                shapes[j][s] = shapes[j].get(s, 0) + 1
                seen[j][k] = seen[j].get(k, 0) + 1
    typed_cols = [j for j in range(width) if filled[j] and typed[j] >= 0.8 * filled[j]]
    kept, seams = [], []
    for r, row in body:
        if _header_score(row) >= 0.5:
            cells = [v for v in row[:width] if _nonempty(v)]
            same = sum(1 for v in cells if _norm_label(v) in labels)
            texty = [j for j in typed_cols if j < len(row) and _is_str(row[j])
                     and _cell_kind(row[j]) == "text"]
            if labels and same >= max(2, 0.5 * len(labels)) or (
                    typed_cols and len(texty) >= max(1, 0.5 * len(typed_cols))
                    and len(cells) >= 0.8 * width and all(len(str(v)) <= 60 for v in cells)
                    and _reads_as_labels(row, width, typed_cols, texty, shapes, seen, filled)):
                seams.append((r, [_fmt_header(v) if _nonempty(v) else "" for v in row[:width]]))
                continue
        kept.append((r, row))
    if not seams or len(kept) < 2:
        return body
    t.segments = [{"header_row": t.header_rows[-1], "headers": list(t.headers), "start": kept[0][0]}]
    for r, hdrs in seams:
        nxt = next((kr for kr, _ in kept if kr > r), None)
        t.segments.append({"header_row": r, "headers": hdrs, "start": nxt})
    return kept


def _reads_as_labels(row, width, typed_cols, texty, shapes, seen, filled) -> bool:
    """A row with text in its number and date columns is a header only when that
    text is not a placeholder ('TBD', 'N/A'), no other cell has the shape of the
    column's IDs ('SO-999'), and some text cell is written nowhere else in its column."""
    if all(str(row[j]).strip().lower().rstrip(".") in _PLACEHOLDER for j in texty):
        return False          # a cancelled order with no date or amount yet is still an order
    from .profile import shape
    others = [j for j in range(min(width, len(row))) if j not in typed_cols and _nonempty(row[j])]
    for j in others:
        s = shape(row[j])
        if "9" in s and shapes[j].get(s, 0) >= 0.5 * filled[j]:
            return False      # an ID written like the column's other IDs is data
    return not others or any(seen[j].get(_norm_label(row[j]), 0) <= 1 for j in others)


def _strptime(v: str, fmt: str):
    try:
        return dt.datetime.strptime(v.strip(), fmt)
    except ValueError:
        return None


def _day_first(fmt: str):
    """True for a day-before-month format, False for month-before-day, None when
    the format has no numeric day and month to swap."""
    if "%d" not in fmt or "%m" not in fmt:
        return None
    return fmt.index("%d") < fmt.index("%m")


def date_reading(fmt: str) -> str:
    """How a text date format reads, in words: 'month first (MM/DD/YYYY)'."""
    shown = fmt
    for a, b in (("%Y", "YYYY"), ("%y", "YY"), ("%m", "MM"), ("%d", "DD"), ("%b", "MON"), ("%H", "hh"),
                 ("%M", "mm"), ("%S", "ss")):
        shown = shown.replace(a, b)
    order = ("year first" if fmt.startswith("%Y") else {True: "day first", False: "month first"}.get(
        _day_first(fmt), "as written"))
    return f"{order} ({shown})"


def _retype(t) -> None:
    """Read text dates and currency text the way they were meant, in memory only:
    a column that is 90% dates or date text in one format holds dates, and a
    column of numbers holds '$9.50' as 9.5. What was retyped is kept on t.retyped."""
    from .workbook import _DATE_FORMATS
    for j in range(len(t.headers)):
        if t.wide and j == t.row_label_col:
            continue
        cells = [(i, r[j]) for i, r in enumerate(t.rows) if j < len(r) and _nonempty(r[j])]
        if not cells:
            continue
        n_num = sum(1 for _, v in cells if isinstance(v, (int, float)) and not isinstance(v, bool))
        n_date = sum(1 for _, v in cells if isinstance(v, (dt.datetime, dt.date)))
        texts = [(i, v) for i, v in cells if isinstance(v, str)]
        if not texts:
            continue
        parsed, fmt = {}, None
        dated = [(i, v) for i, v in texts                  # '03/14/2025', or 'Mar 14, 2025'
                 if _DATE_TEXT.match(v) or (re.search(r"[A-Za-z]{3}", v) and re.search(r"\d{4}", v))]
        if dated and n_date + len(dated) >= 0.9 * len(cells):
            best, tied = 0, []
            for f in _DATE_FORMATS:          # one consistent format: the first that reads the most
                n = sum(1 for _, v in dated[:200] if _strptime(v, f))
                if n > best:
                    best, fmt, tied = n, f, [f]
                elif n and n == best:
                    tied.append(f)
            if {_day_first(f) for f in tied} >= {True, False}:
                # '06/02/2025' reads as June 2 and as 6 February: leave it as written, and say so
                t.retyped[j] = {"kind": "date", "format": "", "ambiguous": True, "rows": [],
                                "text_rows": [t.row_index[i] for i, _ in dated],
                                "examples": list(dict.fromkeys(v for _, v in dated))[:3]}
                continue
            if fmt:
                parsed = {i: d for i, v in dated if (d := _strptime(v, fmt))}
            if n_date + len(parsed) < 0.9 * len(cells):
                parsed = {}
        if parsed:
            kind = "date"
        else:
            nums = {i: n for i, v in texts if (n := _num_text(v)) is not None}
            money = sum(1 for i, v in texts if i in nums and re.search(r"[$€£,]", v))
            if n_num > 0.5 * len(cells) or (money and len(nums) >= 0.9 * len(texts)):
                parsed = nums          # every price written '$10.00' is still a column of numbers
            if n_num + len(parsed) < 0.9 * len(cells):
                parsed = {}
            kind, fmt = "number", ""
        if not parsed:
            continue
        examples = []
        for i, v in parsed.items():
            if len(examples) < 3 and t.rows[i][j] not in examples:
                examples.append(t.rows[i][j])
            t.rows[i][j] = v
        t.retyped[j] = {"kind": kind, "format": fmt, "rows": [t.row_index[i] for i in parsed],
                        "examples": examples}


def _sum_totals(t) -> None:
    """A row whose ID and date cells are blank (or say Total) and whose money
    equals, within a cent, the sum of the 3 or more rows since the last break is
    a total row, whatever its label: one money column (a column with cents) is
    enough, since a total row often leaves a rate blank or shows its average.
    With no column of cents, every number on the row must equal its sum. A sum
    since the first row (a grand total after stacked parts) counts the same."""
    if t.wide or len(t.rows) < 4:
        return
    from .profile import is_id_header
    width = len(t.headers)
    guards, metrics = [], []
    for j in range(width):
        col = [r[j] for r in t.rows if j < len(r) and _nonempty(r[j])]
        if not col:
            continue
        dates = sum(1 for v in col if isinstance(v, (dt.datetime, dt.date)))
        nums = sum(1 for v in col if isinstance(v, (int, float)) and not isinstance(v, bool))
        coded = sum(1 for v in col if isinstance(v, str) and any(ch.isdigit() for ch in v)
                    and " " not in v.strip())      # 'E1001', 'INV-204': an ID by its shape
        if dates >= 0.5 * len(col) or is_id_header(t.headers[j]) \
                or (len(col) >= 0.8 * len(t.rows) and coded >= 0.9 * len(col)):
            guards.append(j)
        elif nums >= 0.8 * len(col):
            metrics.append(j)
    if not guards or not metrics:
        return
    # money: a number column with cents on some of its rows (a count or a quantity has none)
    cents = {j for j in metrics if any(isinstance(r[j], float) and not float(r[j]).is_integer()
                                       for r in t.rows if j < len(r))}

    def is_num(v):
        return isinstance(v, (int, float)) and not isinstance(v, bool)

    def adds_up(nums, sums) -> bool:
        if cents:
            return any(j in cents and is_num(v) and v != 0 and abs(v - sums[j]) <= 0.01 for j, v in nums)
        return all(is_num(v) and abs(v - sums[j]) < 0.005 for j, v in nums) and any(v != 0 for _, v in nums)
    # breaks: the start of each stacked segment, and every labelled total row
    starts = {s["start"] for s in t.segments[1:]}
    labelled = sorted(t.totals_rows)
    sums = [0.0] * width
    grand = [0.0] * width
    n = parts = 0
    drop = set()
    prev = -1
    for i, r in enumerate(t.rows):
        here = t.row_index[i]
        if here in starts or bisect.bisect_right(labelled, prev) < bisect.bisect_left(labelled, here):
            sums, n = [0.0] * width, 0
            parts += 1
        prev = here
        vals = [r[j] if j < len(r) else None for j in metrics]
        nums = [(j, v) for j, v in zip(metrics, vals) if _nonempty(v)]
        unkeyed = not any(_nonempty(r[j]) and not is_total_label(r[j]) for j in guards if j < len(r))
        if n >= 3 and nums and unkeyed and (adds_up(nums, sums) or (parts and adds_up(nums, grand))):
            drop.add(i)
            sums, n = [0.0] * width, 0
            parts += 1
            continue
        for j, v in nums:
            if is_num(v):
                sums[j] += v
                grand[j] += v
        n += 1
    if not drop:
        return
    t.totals_rows = sorted(t.totals_rows + [t.row_index[i] for i in drop])
    t.rows = [r for i, r in enumerate(t.rows) if i not in drop]
    t.row_index = [x for i, x in enumerate(t.row_index) if i not in drop]


def _fill_down(t) -> None:
    """A label typed only on the first row of each group ('East' then blanks)
    applies to the rows below it. Read it that way, in memory only, and say so."""
    if len(t.rows) < 4:
        return
    for j in range(min(2, len(t.headers))):
        col = [r[j] if j < len(r) else None for r in t.rows]
        filled = [v for v in col if _is_str(v)]
        blanks = sum(1 for v in col if not _nonempty(v))
        if not filled or not col or not _is_str(col[0]):
            continue
        if not (0.25 <= blanks / len(col) <= 0.9) or len(set(filled)) != len(filled):
            continue
        # every other column is mostly filled on those rows: a real grouping, not a sparse field
        others = [sum(1 for r in t.rows if any(_nonempty(v) for i, v in enumerate(r) if i != j))]
        if others[0] < 0.9 * len(t.rows):
            continue
        last = None
        n = 0
        for r in t.rows:
            while len(r) <= j:
                r.append(None)
            if _is_str(r[j]):
                last = r[j]
            elif last is not None and not _nonempty(r[j]):
                r[j] = last
                n += 1
        if n:
            t.notes.append(f"{t.headers[j]} is written only on the first row of each group; "
                           f"it applies to the {n} rows below it.")


def _fmt_header(v) -> str:
    if isinstance(v, dt.datetime):
        return v.strftime("%Y-%m-%d") if (v.hour, v.minute) == (0, 0) else v.isoformat()
    if isinstance(v, dt.date):
        return v.isoformat()
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    from .brainzip import clean_text
    s, _ = clean_text(str(v))
    return re.sub(r"\s+", " ", s).strip()[:80]


def _letter(i: int) -> str:
    s = ""
    i += 1
    while i:
        i, rem = divmod(i - 1, 26)
        s = chr(65 + rem) + s
    return s


def _unique(headers: list) -> list:
    seen: dict = {}
    out = []
    for h in headers:
        base = h or "Column"
        if base in seen:
            seen[base] += 1
            out.append(f"{base} ({seen[base]})")
        else:
            seen[base] = 1
            out.append(base)
    return out
