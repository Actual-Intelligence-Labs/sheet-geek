# Security

Spreadsheets are untrusted input. A workbook someone emails you can carry text written to steer an AI: hidden characters, formulas, notes phrased as commands, or a whole `_brain` tab. Assistants that read spreadsheets have been tricked this way before. This tool assumes every cell is hostile, and puts the defenses in code wherever it can, because a model can be talked out of a rule and code cannot.

## What the code enforces

| Threat | Defense |
|---|---|
| Damaging the workbook | The brain tab is spliced into the file's zip without re-saving anything else. Every splice is checked to be a pure insertion, every untouched part is re-verified byte for byte, the write goes to a temp file and is swapped in atomically, and a backup is taken first (kept on your machine, never next to the file). |
| Writing to a file you shouldn't | Refuses encrypted or legacy files, Strict Open XML, workbooks whose sheet list is locked, and files Excel or LibreOffice has open (it looks for their lock files). Warns on digitally signed workbooks. Never touches a tab named `_brain` that it did not write. |
| Zip and XML bombs | Size and compression-ratio limits; XML with a DOCTYPE or ENTITY is refused (defusedxml is used when installed). |
| Instructions hidden in a received brain | The format has no instruction field. The reader strips invisible Unicode (tag characters, bidirectional overrides, zero-width characters), ignores formulas, caps size, flags notes that read like commands, and hands brain content to the model fenced as notes, never as instructions. A `.Rules` tab is flagged. |
| Cell text steering the questions | Questions come from fixed templates. Headers and values quoted from cells are cleaned of hidden characters and capped in length (80 characters for headers, 40 for values). |
| Formula injection through the brain | Brain text never starts with `= + - @` or a tab. |
| Private remarks leaking | Sentences said during the questions that code flags as private (marked "between us" or "confidential", and some remarks judging a person or about HR matters or deals) are kept on your machine and only released into the file if you say so. The check runs before the first write, because text inside a workbook cannot be scrubbed once a spreadsheet app has re-saved it. It is a word filter and misses many remarks about people's health, jobs or deals, so the save preview can show every line first. |
| Personal data in the brain | The brain holds definitions, totals and counted findings. Code masks the email addresses and phone numbers it finds, and does not list the values of columns whose header marks them as people (name, contact, employee, owner, rep, manager, buyer, person). Names can still appear: a column of people under another header (Customer, Guest, Patient), the person or customer with the largest total in a counted note, or an example code. Check the preview before saving into a file you will share, or share a copy without the brain. |
| Web research leaking data | Research is opt-in. Every proposed search is checked by code against the text values in the data tables and the file and tab names; a search containing one of them, or any number other than a four-digit year, is marked blocked, and the AI is told to run only the searches that pass, after you say yes. The check compares whole values, so text outside the data tables is not covered. |
| The map calling home | The map is one local HTML file with a content security policy that allows no network access. Text from cells is never turned into links or images. |

## What code cannot promise

Prompt-level defenses reduce risk; they do not remove it. If you open a stranger's workbook with any AI, treat what that AI says with the same care you would give the stranger. The brain's `said_by` column tells you who wrote each note, and notes from someone else are shown as theirs, not as facts.

## Where your data goes

- The brain tab lives in your file (for a CSV, in a `<name>.brain.json` file next to it). Anyone you send the file to can read it (hidden tabs are not private).
- The local index lives in `~/.sheet-geek` (folder permissions 700; an existing `~/.spreadsheet-brain` from before the rename stays in use): private notes, raw answers, backups (the five newest per file), a profile of the data, a log, and the map of how your files connect. Nothing in it leaves your machine.
- Your AI tool keeps its own conversation history under its own policy. Answers you type during the questions are part of that conversation.

## Reporting a problem

Email interested@actualintelligencelabs.ai with "Sheet Geek security" in the subject. Please don't open a public issue for a security problem. Include a workbook that reproduces it, with fake data.
