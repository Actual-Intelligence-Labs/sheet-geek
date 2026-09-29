"""Value-level fingerprints (decision 10). A fact remembers the fingerprint of
exactly the data it depends on, so a reopen can tell which notes may be out of
date without keeping any old data. Layout-only re-saves do not change them.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import re

_DEP = re.compile(r"^(.*?)!\{(.*)\}$")


def canon(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "T" if v else "F"
    if isinstance(v, float):
        return repr(round(v, 9)) if not v.is_integer() else str(int(v))
    if isinstance(v, int):
        return str(v)
    if isinstance(v, (dt.datetime, dt.date)):
        return v.isoformat()
    return str(v).strip()


def _h(parts) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(p.encode("utf-8", "surrogatepass"))
        h.update(b"\x1f")
    return h.hexdigest()[:12]


def dep_string(deps: list) -> str:
    """[(sheet, header)] -> 'Sheet!{Header};Sheet!{Header}'."""
    seen = []
    for s, h in deps:
        d = f"{s}!{{{h}}}"
        if d not in seen:
            seen.append(d)
    return ";".join(seen)


def parse_deps(s: str) -> list:
    out = []
    for part in (s or "").split(";"):
        m = _DEP.match(part.strip())
        if m:
            out.append((m.group(1), m.group(2)))
    return out


def column_values(analysis, path: str, sheet: str, header: str):
    """Values of one column (or one labeled row in a wide table) in file `path`."""
    for t in analysis.tables:
        if analysis.file_of[t.tid] != path or t.sheet != sheet:
            continue
        if header in t.headers:
            j = t.headers.index(header)
            return t.column(j)
        if t.wide and t.row_label_col >= 0:
            for r in t.rows:
                lab = r[t.row_label_col] if t.row_label_col < len(r) else None
                if isinstance(lab, str) and lab.strip() == header:
                    return r
    return None


def column_domain(analysis, path: str, sheet: str, header: str):
    """What a column MEANS rather than every value it holds: its type, and its
    categories (up to 1,000) or the shapes of its values. A note a person gave
    about a column rests on this, so appending rows does not flag it, but a
    new unit, a new category or a type change does."""
    for t in analysis.tables:
        if analysis.file_of[t.tid] != path or t.sheet != sheet:
            continue
        if header in t.headers:
            c = analysis.cols[t.tid][t.headers.index(header)]
            parts = [f"type:{c.type}", f"semantic:{c.semantic}"]
            if c.distinct <= 1000 and not c.distinct_capped and c.semantic not in ("identifier", "metric") \
                    and c.type != "date":
                parts += sorted(str(k) for k in c.counter)
            else:
                parts += sorted(f"shape:{s}" for s, _ in c.shapes[:3])
                if c.type == "number":
                    parts += [f"negatives:{c.negatives > 0}", f"integers:{c.integers}"]
            return parts
    vals = column_values(analysis, path, sheet, header)      # a labeled row in a model
    return [canon(v) for v in vals] if vals is not None else None


def deps_fp(analysis, path: str, deps: list, level: str = "values") -> str:
    """Fingerprint of the data a fact depends on; '' if none resolvable.
    level="values": every value (counted facts). level="domain": what the data
    means (notes a person gave); stored with a "d:" prefix."""
    parts = []
    found = False
    for sheet, header in deps:
        if level == "domain":
            dom = column_domain(analysis, path, sheet, header)
            if dom is None:
                parts.append(f"missing:{sheet}!{header}")
                continue
            found = True
            parts.append(sheet)            # not the header: a rename is reported, not a meaning change
            parts.extend(dom)
            continue
        vals = column_values(analysis, path, sheet, header)
        if vals is None:
            parts.append(f"missing:{sheet}!{header}")
            continue
        found = True
        parts.append(f"{sheet}!{header}")
        parts.extend(canon(v) for v in vals)
    if not found:
        return ""
    return ("d:" if level == "domain" else "") + _h(parts)


def fp_like(analysis, path: str, deps: list, stored: str) -> str:
    """Recompute at the same level the stored fingerprint was taken at."""
    return deps_fp(analysis, path, deps, "domain" if (stored or "").startswith("d:") else "values")


def file_fp(analysis, path: str) -> dict:
    """Per-sheet fingerprint over every data cell (values and formula text)."""
    out = {}
    book = next(b for b in analysis.books if b.path == path)
    for s in book.data_sheets():
        parts = []
        for r, row in enumerate(s.values):
            for c, v in enumerate(row):
                f = s.formulas.get((r, c))
                if v is None and f is None:
                    continue
                parts.append(f"{r},{c}:{f or canon(v)}")
        out[s.name] = {"fp": _h(parts), "rows": s.n_rows}
    return out
