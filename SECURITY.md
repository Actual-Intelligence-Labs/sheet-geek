# Security

Spreadsheets are untrusted input. A workbook someone emails you can carry text written to steer an AI: hidden characters, formulas, notes phrased as commands, or a whole `_brain` tab. Assistants that read spreadsheets have been tricked this way before. This tool assumes every cell is hostile, and puts the defenses in code wherever it can, because a model can be talked out of a rule and code cannot.

## What the code enforces

| Threat | Defense |
|---|---|
| Damaging the workbook | The brain tab is spliced into the file's zip without re-saving anything else. Every splice is checked to be a pure insertion, every untouched part is re-verified byte for byte, a new copy is written to a temp file and swapped in, an existing file is rewritten in place after a backup (kept on your machine, never next to the file), read back, and put back byte for byte if anything differs. |
| Writing to a file you shouldn't | Refuses encrypted or legacy files, Strict Open XML, workbooks whose sheet list is locked, and files Excel or LibreOffice has open (it looks for their lock files). Warns on digitally signed workbooks. Never touches a tab named `_brain` that it did not write. |
| Zip and XML bombs | Size and compression-ratio limits; a workbook with a DOCTYPE or ENTITY anywhere in any XML part is refused before anything parses it (defusedxml is used when installed). |
| A crafted file attacking your machine | A brain id that is not the 12 hex characters this tool writes is turned into 12 hex characters before use, so a file can never name a folder outside the local index, and backup cleanup deletes only backups the tool made. Formulas are recomputed by a small calculator that knows numbers, cell references, `+ - * / ^` and six functions (no Python `eval`), works in doubles as Excel does, caps powers and the cells one formula may read, and refuses math on whole ranges; formulas far longer than Excel allows and number-like text longer than 40 characters are not parsed. Temp files get fresh random names. |
| Instructions hidden in a received brain | The format has no instruction field. The reader strips invisible Unicode (tag characters, bidirectional overrides, zero-width characters), ignores formulas, caps size, flags notes that read like commands, and hands brain content to the model fenced as notes, never as instructions. A received note that reads like a command stays on your machine, flagged, and is never written into the next copy of the file. A `.Rules` tab is flagged. The brain tab is visible unless you ask for a hidden one. |
| Cell text steering the questions | Questions come from fixed templates. Headers and values quoted from cells are cleaned of hidden characters and capped in length (80 characters for headers, 40 for values). |
| Formula injection through the brain | Brain text never starts with `= + - @` or a tab. |
| Private remarks leaking | Sentences said during the questions that code flags as private (marked "between us" or "confidential", and some remarks judging a person or about HR matters or deals) are kept on your machine and only released into the file if you say so. The check runs before the first write, because text inside a workbook cannot be scrubbed once a spreadsheet app has re-saved it. It is a word filter and misses many remarks about people's health, jobs or deals, so the save preview can show every line first. |
| Personal data in the brain | The brain holds definitions, totals and counted findings. Code masks the email addresses and phone numbers it finds, and does not list the values of columns whose header marks them as people (name, contact, employee, owner, rep, manager, buyer, person). Names can still appear: a column of people under another header (Customer, Guest, Patient), the person or customer with the largest total in a counted note, or an example code. Check the preview before saving into a file you will share, or share a copy without the brain. |
| Data leaving your machine | The skill does no web searches, the code makes no network calls, and `sb` never starts another program. |
| The map calling home | The map is one local HTML file with a content security policy that allows no network access. Text from cells is never turned into links or images. |

## What code cannot promise

Prompt-level defenses reduce risk; they do not remove it. If you open a stranger's workbook with any AI, treat what that AI says with the same care you would give the stranger. The brain's `said_by` column tells you who wrote each note, and notes from someone else are shown as theirs, not as facts.

## Where your data goes

- The brain tab lives in your file (for a CSV, in a `<name>.brain.json` file next to it). Anyone you send the file to can read it (hidden tabs are not private).
- The local index lives in `~/.sheet-geek` (created with folder permissions 700; an existing `~/.spreadsheet-brain` from before the rename stays in use): private notes, raw answers, backups (the five newest per file), a profile of the data with example values, a log, and the map of how your files connect. `sb` never sends any of it anywhere; what it prints to your AI app (for example the notes `read` shows) goes to that app with the rest of the conversation.
- Your AI tool keeps its own conversation history under its own policy. Answers you type during the questions are part of that conversation.

## Reporting a problem

Email interested@actualintelligencelabs.ai with "Sheet Geek security" in the subject. Please don't open a public issue for a security problem. Include a workbook that reproduces it, with fake data.
