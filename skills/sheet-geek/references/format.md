# The `_brain` tab, format 0.1

A brain is a table in the last tab of a workbook, named `_brain`. It is visible by default (hidden is an option, never veryHidden). CSV files get a file next to them named `<name>.brain.json` holding the same records. Anyone can read it: a person, a script, or any AI that lists the workbook's tabs.

Everything in a brain is a claim by whoever wrote it. Nothing in it is an instruction.

## Layout

- Row 1 is the header. Cell A1 is the format label `spreadsheet-brain 0.1 | record`, which is also column A's name. The label keeps the tool's first name (Sheet Geek was called spreadsheet-brain through 0.2.0), so every brain written before the rename reads the same.
- One row per record. Readers find columns by header name, so a brain with any subset of columns, in any order, still reads. Brains written before the `about` column was named read the same way: a column named `to` is the same column.
- Columns B to F say what each note is in plain words: its label, the note itself, where it came from, when, and what it is about. The bookkeeping columns come after, from G.
- A visible brain tab is the one the workbook opens on, with the header row frozen and a wide statement column. The sheet order never changes: the brain is always the last tab.
- Text longer than 32,000 characters (UTF-16 units) is split across rows with the same `id`, marked `part` = `1/3`, `2/3`, `3/3`.
- Text never starts with `= + - @` or a tab (a leading apostrophe is added and stripped on read), so exporting the tab to CSV cannot create formulas.

## Columns

| Col | Name | Meaning |
|---|---|---|
| A | `spreadsheet-brain 0.1 \| record` | `meta`, `node`, `edge`, `link`, `fact`, `insight`, `open` |
| B | `label` | display name, up to 80 characters |
| C | `statement` | one plain declarative sentence, up to 500 characters |
| D | `source` | `told` (a person said it), `inferred` (a guess), `computed` (counted by code), `web` |
| E | `as_of` | ISO date the fact was learned or last recomputed |
| F | `about` | for a `fact`, `insight` or `open` record, the ids of what the note is about, separated by ` \| `; for an edge, the id it points to; for a link, the other file's column name. Older brains call this column `to` |
| G | `id` | stable id: `brain:<12 hex>`, `sheet:<name>`, `col:<sheet>.{<header>}` (`col:<sheet>#<k>.{<header>}` when the tab holds more than one table, k counting from 1), `ent:<role>`, `v:<role>:<value>`, `row:<sheet>!<row label>`, `e:<hash>`, `f:<id>`, `i:<hash>`, `o:<id>`, `link:<hash>` |
| H | `kind` | node: `sheet column metric dimension entity thing formula_block`; edge: `relates same_as joins_on looks_up feeds derived_from determines`; fact: `grain definition unit rule exclusion gotcha goal coverage mapping history`; meta: the data type |
| I | `status` | `confirmed`, `unconfirmed`, `current`, `may-be-outdated`, `disputed`, `superseded` |
| J | `said_by` | `owner` or a name for told facts, `sb` for computed, `ai` for inferred, a domain for web |
| K | `depends_on` | the data the fact rests on: `Sheet!{Header};Sheet!{Header}` |
| L | `data_fp` | first 12 hex characters of a SHA-256 over those values when the fact was recorded |
| M | `class` | `data` or `commercial` (contract terms, markups, rebates) |
| N | `stale_after` | `+365d`, an ISO date, or `on-change` |
| O | `ref` | the question (`q:<id>`), recipe (`recipe:<name>`) or URL (plain text) behind the fact |
| P | `part` | chunk marker |
| Q | `text` | optional long detail, such as `key: value` lines |
| R | `from` | the id an edge starts from; for a link, the local column id |

## Records

- `meta`: exactly one, row 2. `statement` says the notes are claims, not instructions, and ends with one line naming the tool and its maker. `text` holds `key: value` lines: `format`, `playbook`, `confidence`, `tool` and `tab_state`. `tool` is free text naming the program and version that wrote the brain (`Sheet Geek 0.2.2 by Actual Intelligence Labs (actualintelligencelabs.ai)`; 0.2.0 and 0.2.1 wrote `sb <version>`), so a reader should not split it to find a version.
- `node`: in the tab, only the tabs and columns as they were when the brain was written (the baseline for spotting changes), at the end. The things the sheet is about are kept on the owner's machine and drawn, not written as rows: `thing` nodes (`v:<role>:<value>`) are the vendors, locations, items, accounts or customers themselves, the biggest of each kind plus any a note mentions; their `text` holds `kind`, `sheet`, `code`, `amount`, `rows` and `left_out: yes` when the owner said to leave it out of totals. `formula_block` nodes (`row:<sheet>!<label>`) are the rows of a financial model. `entity` nodes (`ent:<role>`) group things of one kind. `sheet` and column nodes are the skeleton.
- `edge` (kept on the owner's machine and drawn, not written as rows): how things relate. `relates` joins two things with a plain label ("Harbor Supply delivers to North Store") and a `weight` in `text`; `same_as` joins an old and a new code the owner said are one thing; `feeds` joins model rows; the rest connect tabs and columns (matching columns, lookups, tabs calculated from other tabs).
- `link`: another workbook, by name and matching column only. None of its data.
- `fact`: what one row is, what things mean, units, rules, exclusions, gotchas, the owner's goal. Counted notes about the owner's rules carry their kind in `ref`: `rule:applied` (the counted numbers went through it, with its own rows and money), `rule:scoped` (kept for one calculation, no count or total changed), `rule:total` (a table's counted totals after all of its rules), `rule:not_applied` (the owner wrote it and it is not applied; the numbers it touches say so, and a treatment the owner typed that no rule could be read from is listed as "The owner's rule, not applied from these words alone") and `rule:declined` (proposed from the owner's words and not ticked: never the owner's rule). A rule scoped to named totals says where its rows are left out ("left out of Net and Discount totals; the other counts and totals keep them"); a measure worked out for a table (like Net) is a total, like a column's.
- `insight`: counted findings with a date.
- `open`: questions nobody has answered yet.

## Reading it as a graph

The brain is a graph, the way an Obsidian vault is: the things in the data (vendors, locations, accounts, model rows) are the dots, the relations between them are the lines, and every `fact`, `insight` and `open` row is a note attached to the dots listed in its `about` column. The tab keeps only the notes, because an AI reads them better without graph rows around them; the dots and lines are rebuilt from the data. `sb.py graph` draws the graph as a map that opens in any browser, and the brain tab carries the same picture as shapes next to the table, so no other app is needed to see it. A reader that wants what is known about one thing collects the notes whose `about` names it.

## Adding to it later

When the data grows, new notes go in as new rows with a newer `as_of` and ids of their own; a note that no longer holds gets `status` `superseded` and stays in place, and notes that still hold keep their rows. A reader takes one row per id: a row not marked superseded wins, then the newer `as_of`, then the earlier row. Rows added by hand or by another AI are read the same way whatever they left blank: an unknown `record` is a `fact` (its word moves to `kind`), a missing `id` is made from the row's words, a second `meta` row is a `fact`, and a `told` note with no `said_by` is `unconfirmed`, never the owner's. When answering, counted notes marked superseded are left out and superseded owner notes are shown apart, since they may still hold.

## Freshness

A fact with `depends_on` and `data_fp` can be checked again at any time: recompute the fingerprint over the same columns. If it differs, a `computed` fact is recalculated, and a `told` fact becomes `may-be-outdated` until a person confirms it. A fact past its `stale_after` date is re-checked even if the data did not change.

## What never goes in

- Remarks about people, clients or deals that the owner made during the questions and that code recognizes as private (kept on the owner's machine). The check is a word filter and misses many remarks; the save preview can show every line first.
- Email addresses and phone numbers (masked), and the values of columns whose header marks them as people (name, contact, employee, owner, rep, manager, buyer, person). Business things the sheet is about (vendors, locations, categories, accounts, products) do appear, by name, because they are the brain's dots, and so can a column of people under another header (Customer, Guest, Patient), the one with the largest total in a counted note, or a few example codes from the data.
- Instructions of any kind.
