# Chat runs quoted in the launch film

The Sheet Geek launch film's two chat shots quote these runs. Both are Claude Opus 5.5, and the film says so on screen. Each run is one clean-room session, the same isolation the studies use: its own folder holding only the workbook, no skill installed, a scrubbed environment, reads outside the folder denied, nothing kept between runs.

- **Without the brain:** `evals/fixtures/finance_model.xlsx` (the finance model, a synthetic development fixture).
- **With the brain:** the same workbook after one version 0.2 build with an AI playing the owner from the fixture's brief (`demo/try-it/Finance model - WITH brain.xlsx`).

The prompt, word for word (the film shows only "What's our runway?"):

> I just got this file from a colleague: finance_model.xlsx. What's our runway? Python 3 with openpyxl is at /usr/bin/python3 if you want to open the file. Work numbers out from the file; don't guess. Answer in plain words, under 150 words.

| File | Model | Brain | Status |
|---|---|---|---|
| brain-claude-opus-5-5-1.json | claude-opus-5-5 | brain | ok |
| brain-claude-opus-5-5-2.json | claude-opus-5-5 | brain | ok |
| brain-claude-opus-5-5-3.json | claude-opus-5-5 | brain | ok |
| brain-claude-sonnet-5-1.json | claude-sonnet-5 | brain | leak |
| brain-claude-sonnet-5-2.json | claude-sonnet-5 | brain | leak |
| brain-claude-sonnet-5-3.json | claude-sonnet-5 | brain | ok |
| none-claude-opus-5-5-1.json | claude-opus-5-5 | none | ok |
| none-claude-opus-5-5-2.json | claude-opus-5-5 | none | ok |
| none-claude-opus-5-5-3.json | claude-opus-5-5 | none | ok |
| none-claude-sonnet-5-1.json | claude-sonnet-5 | none | leak |
| none-claude-sonnet-5-2.json | claude-sonnet-5 | none | leak |
| none-claude-sonnet-5-3.json | claude-sonnet-5 | none | leak |

"leak" means the isolation detector flagged the run (the model searched outside its folder), so its answer was discarded and never quoted, the same rule as the studies. That left 3 Opus 5.5 runs each way and 1 Sonnet 5 run with the brain.

What the film quotes (verbatim sentences, markdown bold removed; an ellipsis marks every cut inside a quote; a quote may end at a sentence end while the answer goes on):

- Without the brain, from `none-claude-opus-5-5-2.json`: "Your runway runs out in May 2027, … unless the company raises money. … Ask whether it's committed or just planned."
- With the brain, from `brain-claude-opus-5-5-3.json`: "The notes tab in the file says that round hasn't closed." (The first cut of the film quoted `brain-claude-opus-5-5-1.json`: "…the money runs out in May 2027. … The owner's notes in the file … say that round "is not closed yet," so I left it out.")

All three Opus runs without the brain found the May 2027 run-out and flagged the $15M raise as typed in and unconfirmed. All three with the brain found the same date and cited the owner's note that the round is not closed. The difference the film shows is that one: without the brain the AI has to ask; with it, the file already says.
