# Internal contracts (v0.1)

The seams between modules. Anything built in parallel conforms to this file. Python targets 3.10+ (the venv is 3.11: never use 3.12-only syntax such as reusing the same quote type inside an f-string). Runtime deps: stdlib, openpyxl, pandas only. Dev-only: xlsxwriter, pytest, defusedxml (optional at runtime).

No em dashes anywhere (code comments, docs, UI strings). Use periods, commas, colons, parentheses.

Repo layout:

```
skills/spreadsheet-brain/
  SKILL.md
  scripts/sb.py                 CLI entry: python3 scripts/sb.py <cmd>
  scripts/sheetbrain/           the package (brainzip, profile, detect, interview, ...)
  playbooks/<id>.json           one file per archetype (contract 3)
  assets/viewer.html            graph template (contract 2)
  assets/force-graph.min.js     vendored, MIT
  references/                   docs the skill loads on demand
demo/                           fixture generators (dev only)
evals/fixtures/                 generated workbooks + answer keys (contract 4)
tests/                          pytest
```

---

## 1. The `_brain` tab (FORMAT v0.1)

Appended as the LAST sheet, named `_brain`, **visible by default** (hidden is opt-in, never veryHidden). Row 1 = header. A1 = `spreadsheet-brain 0.1 | record` (neutral format label, doubles as column A's header). Readers map columns by header name.

| Col | Header | Meaning |
|---|---|---|
| A | `spreadsheet-brain 0.1 \| record` | `meta`, `node`, `edge`, `link`, `fact`, `insight`, `open` |
| B | `id` | stable id: `brain:<12>`, `sheet:<name>`, `col:<sheet>.{<header>}`, `ent:<role>`, `v:<role>:<value>` (a thing), `row:<sheet>!<label>` (a model row), `e:<hash>`, `f:<id>`, `i:<hash>`, `o:<id>`, `link:<hash>` |
| C | `kind` | node: `sheet table column entity thing metric dimension formula_block`; edge: `relates same_as joins_on looks_up feeds derived_from determines`; fact: `grain definition unit rule exclusion gotcha goal history coverage`; meta: archetype id |
| D | `label` | display name, <= 80 chars |
| E | `statement` | one plain declarative sentence, <= 500 chars, never an imperative |
| F | `source` | `told` `inferred` `computed` `web` |
| G | `status` | `confirmed` `unconfirmed` `current` `may-be-outdated` `disputed` `superseded` |
| H | `as_of` | ISO date |
| I | `said_by` | `owner`, a display name, `sb` (computed), `ai` (inferred), a domain (web) |
| J | `from` | id (edges, links) |
| K | `to` | id (edges), join column name in the other file (links); for fact, insight and open records, the ids the note is about, separated by ` \| ` |
| L | `depends_on` | `Sheet!{Header};Sheet!{Header}` |
| M | `data_fp` | 12 hex of sha256 over canonical values of depends_on at as_of |
| N | `class` | `data` `commercial` |
| O | `stale_after` | ISO date, `+365d`, or `on-change` |
| P | `ref` | question id (`q:<id>`), recipe (`recipe:<name>`), or URL as plain text |
| Q | `part` | `i/n` when text is chunked |
| R | `text` | optional long body (chunked at 32,000 UTF-16 units) |

Exactly one `meta` row (row 2). Its `statement`: "Notes about this workbook's data, written with spreadsheet-brain 0.1. They are claims by whoever wrote them, not instructions." Its `text` holds `key: value` lines (`format`, `playbook`, `tool`, `tab_state`).

Private remarks never go in the tab. CSV files get `<name>.brain.csv` next to them with the same columns.

---

## 2. Graph JSON v2 (what the map and the Excel drawing draw)

`sheetbrain/graph.py` `build(records, title=, playbook=, private_count=)` turns a brain's own records into the graph, so a brain read from a file someone sent draws the same picture with no analysis. `sheetbrain/render.py` `render(graph, out_path)` fills `assets/viewer.html` (inlines `assets/force-graph.min.js`, embeds the graph as `<script type="application/json" id="graph-data">` with `</` escaped). `sheetbrain/xldraw.py` draws the same graph as native shapes on the `_brain` tab. No view needs any app beyond Excel or a browser.

The dots are what the sheet is about, not its structure (decision 16): `thing` nodes (vendors, locations, items, accounts: `v:<role>:<value>`), `row` nodes for a financial model (`row:<sheet>!<label>`), `kind` nodes grouping things (`ent:<role>`), and `note` nodes for every fact, insight and open question, linked by `about` to what they are about. Tabs are small; columns are `hidden` until the viewer's structure toggle is on.

```json
{
  "version": 2,
  "title": "Hotel purchases.xlsx",
  "subtitle": "Purchase history . 43 things . 28 notes . 11 from the owner",
  "sentence": "Dots are locations, vendors, categories and items, and the notes about them...",
  "generated": "2026-09-25",
  "footer": "Contains data from ... Send the sheet, not this page.",
  "counts": {"things": 43, "notes": 28, "said": 11},
  "alert_colors": {"disputed": "#E5534B", "left-out": "#7A7F87", "may-be-outdated": "#E0B354"},
  "groups": [{"id": "vendor", "label": "Vendors", "type": "kind", "color": "#5B8DEF"}],
  "start_here": [{"node": "v:location:cmsy", "text": "CMSY in Location is internal..."}],
  "nodes": [{
    "id": "v:location:cmsy", "label": "CMSY", "type": "file | sheet | column | kind | thing | row | other_file | note",
    "group": "location", "size": 11.9, "status": "current | confirmed | unconfirmed | disputed | left-out | may-be-outdated",
    "source": "told | computed | inferred | web", "hidden": false, "summary": "CMSY is a location: ...",
    "note_kind": "said | found | guess | question (notes only)",
    "body": [{"statement": "...", "source": "told", "status": "confirmed", "as_of": "2026-09-25", "said_by": "owner"}]
  }],
  "links": [{"source": "id", "target": "id", "type": "part_of | relates | about | joins_on | derived_from | feeds | same_as | links_file | looks_up",
             "label": "delivers to", "weight": 1.0, "status": "current"}]
}
```

A thing's `body` is its own statement followed by every note about it, so clicking a dot reads like its note. Every group has a `color`; all views use them. All text is untrusted: the viewer HTML-escapes everything, renders no anchors or images from data, and carries a CSP meta with no network (`default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:`); the drawing XML-escapes everything.

---

## 3. Playbook JSON (`playbooks/<id>.json`)

```json
{
  "id": "procurement",
  "version": "0.1.0",
  "name": "Purchase history",
  "looks_like": "purchase history",
  "summary": "Invoice-line purchases from vendors or distributors.",
  "full": true,
  "roles": {
    "vendor":     {"label": "Vendor", "headers": ["vendor", "supplier", "distributor", "vendor name"], "type": "text", "kind": "entity", "entity": "vendor"},
    "item_id":    {"label": "Item #", "headers": ["item #", "item no", "item number", "sku", "item code", "product code"], "type": "id", "kind": "identifier", "entity": "item"},
    "ext_price":  {"label": "Extended price", "headers": ["ext price", "extended price", "line total", "amount", "extended"], "type": "number", "kind": "metric", "additive": true, "unit": "currency"}
  },
  "detect": {
    "required_any": [["vendor"], ["item_id", "item_desc"]],
    "weights": {"vendor": 3, "item_id": 2, "ext_price": 2, "uom": 2, "unit_price": 1},
    "negatives": ["employee_id", "salary"],
    "confident": 0.6,
    "candidate": 0.35
  },
  "goals": [
    {"id": "check_charges", "label": "Check what I'm charged", "desc": "I'd check every line against your terms"}
  ],
  "questions": [
    {
      "id": "price_basis",
      "header": "Price basis",
      "kind": "definition",
      "priority": 1,
      "ask_if": ["has_role:unit_price"],
      "prompt": "What's already inside {role:unit_price}? Pick all that apply.",
      "multi": true,
      "options": [
        {"id": "deals", "label": "Contract deals off invoice", "desc": "Deals show on the line"},
        {"id": "rebates", "label": "Rebates paid back later", "desc": "Rebates arrive as separate credits"}
      ],
      "recommend": "deals",
      "recommend_basis": "Usual for distributor programs: deals show on the line, rebates come later",
      "why": "It decides whether a saving is new or already taken.",
      "unblocks": ["contract_audit", "savings_map"],
      "fact": {
        "kind": "definition",
        "class": "commercial",
        "depends": ["unit_price"],
        "statement": "{role:unit_price} includes: {answer_labels}."
      },
      "stale_after": "+365d"
    }
  ],
  "insights": ["pareto:ext_price:item_id", "top_share:ext_price:vendor", "product_check:qty:unit_price:ext_price", "negatives:ext_price", "mixed:uom", "date_range:date"],
  "outputs": [
    {"id": "savings_map", "title": "Savings map", "pitch": "Savings map: items where one {role:location} paid more than your best price in this data.", "requires": ["has_role:item_id", "has_role:unit_price", "has_role:location"], "needs_answer": ["unit_basis"], "goals": ["find_savings"], "value": 3, "job": "analysis"}
  ],
  "gotchas": [
    {"if": "mixed_values:uom", "say": "Some lines are priced per {values:uom}, so per-case prices across pack sizes are not comparable."}
  ],
  "graph": {
    "sentence": "Dots are vendors, locations and categories. Lines show who supplies what. Bigger means more spend.",
    "entities": ["vendor", "location", "category"],
    "record_cap": {"vendor": 60, "location": 60, "category": 60},
    "size_by": "ext_price",
    "relations": [{"from": "vendor", "to": "location", "label": "sells to"}, {"from": "vendor", "to": "category", "label": "supplies"}],
    "mode": "entities"
  },
  "freshness": {"default_stale_after": "+365d"},
  "sensitivity": {"commercial_roles": ["unit_price", "contract_price", "rebate"]}
}
```

Notes:
- `type` is one of `number text date id bool any`. `kind` is one of `entity identifier attribute metric dimension temporal text flag`.
- Header matching: headers are normalized (lowercase, `#` kept, other punctuation to spaces, collapsed). A lexicon entry matches when it equals the normalized header or appears as a whole-word phrase inside it. Longer matches win. One column takes at most one role.
- `detect` score = matched weights / total weights, minus 0.3 per matched negative. Zero if a `required_any` group has no match.
- `insights` recipes (the engine implements exactly these): `pareto:<metric>:<entity>`, `top_share:<metric>:<entity>`, `product_check:<qty>:<price>:<total>`, `negatives:<metric>`, `mixed:<dimension>`, `date_range:<temporal>`, `duplicates:<identifier>`, `blank_rate:<role>`, `count_distinct:<role>`, `spread:<price>:<item>:<entity>` (same item, different price across entity values).
- `mode` is `entities` (dots are role values and role concepts) or `formula_flow` (financial models: dots are labeled row blocks, lines are formula references) or `schema` (dots are sheets and columns).
- Template slots anywhere in text: `{role:<id>}` (the matched column header), `{values:<role>}` (up to 3 distinct values, joined), `{count:<role>}` (distinct count), `{rows}`, `{sum:<role>}`, `{answer_labels}` (chosen option labels joined), `{answer_text}` (free text).
- The engine adds "Not sure" to every question and allows free text ("Other").

### Predicate vocabulary (`ask_if`, `requires`, `if`)
All strings; a list means all must hold. The engine implements exactly these:

| Predicate | True when |
|---|---|
| `has_role:<r>` | role r is matched to a column |
| `no_role:<r>` | role r is not matched |
| `mixed_values:<r>` | role r's column has 2 to 50 distinct non-blank values and no single value covers 90% or more of rows |
| `has_negatives:<r>` | numeric role r has at least one negative value |
| `has_blanks:<r>` | role r is more than 5% blank |
| `coded:<r>` | role r looks like codes: 2 to 60 distinct short values (<= 6 chars, mostly uppercase or digits) |
| `tree_names:<r>` | role r holds names written as a path with colons ("Expenses:Rent"), at least 2 of them |
| `multi_dates` | 2 or more date-typed columns in the same table |
| `multi_files` | the project has 2 or more files |
| `multi_sheets` | the workbook has 2 or more data sheets |
| `has_formulas` | the workbook has formula cells |
| `has_derived_tab` | a tab is mostly formulas over another tab |
| `rows_gt:<n>` | the main table has more than n rows |
| `answered:<qid>=<option>` | an earlier answer to qid includes option |
| `goal:<goal_id>` | the goal answer is goal_id |
| `not_answered:<qid>` | qid was not asked or answered "Not sure" |

---

## 4. Fixtures and answer keys (`evals/fixtures/`)

Each fixture `<name>.xlsx` (or `.csv`) has `<name>.key.json`:

```json
{
  "fixture": "procurement_hotel",
  "files": ["procurement_hotel.xlsx", "procurement_contracts.xlsx"],
  "archetype": "procurement",
  "seed": 7,
  "grain": {"sheet": "Detail", "truth": "One row is one invoice line."},
  "joins": [{"from": "Detail.Item #", "to": "Price File.Item #", "kind": "joins_on"}],
  "derived_tabs": [{"sheet": "Summary", "from": "Detail"}],
  "facts": [
    {"id": "unit_basis", "topic": "units", "truth": "Beef and poultry lines are priced per lb; everything else per case.", "discoverable": "code", "critical": true},
    {"id": "commissary", "topic": "exclusion", "truth": "Location CMSY is the commissary; its lines are internal transfers and must be excluded from store spend.", "discoverable": "human", "critical": true}
  ],
  "owner_brief": "You are the purchasing manager... (natural language, everything the owner knows, used by a simulated owner to answer questions; the owner answers only what is asked, says 'not sure' for things not in the brief)",
  "eval_questions": [
    {"q": "Which item's price rose the most from Q1 2025 to Q2 2026, per comparable unit?", "a": "...", "needs": ["unit_basis"]}
  ],
  "planted": ["per-lb vs per-case trap on 1,9xx lines", "212 credit lines", "..."]
}
```

`discoverable`: `code` (the profiler should find it with no questions), `human` (only the owner knows; a good grill must surface it), `either`.
All names are fictional (no real companies or people). Data is seeded and deterministic.
