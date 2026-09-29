# Pre-registered confirmatory test: does a spreadsheet brain help on businesses nobody has seen?

Drafted 2026-09-26, before version 0.2 of the tool exists and before any business for this test exists. The analysis below is fixed at the freeze and followed as written. Anything done differently is reported as a deviation.

## Why a second test

The first pre-registered test (`PREREG-2026-09-26.md`) found that a brain raised judged answer quality on 4 held-out businesses (31 pairs, p = 0.00009), but the pairs came from only 4 businesses: at the business level the effect was 3 of 4 positive and not significant (p = 0.11). This test asks the question at the level that matters for a new user: across many businesses nobody has seen, does the brain help?

## Question

When an AI with no skill and no hint is handed a workbook, does a brain built by the tool (version 0.2, frozen below) make its answers better than the same workbook without a brain, across businesses?

## Design

- **Businesses:** 12 new synthetic businesses, built after the freeze by agents that never read this repo, each in a different line of work and none like the seven used so far (hotel purchasing, SaaS finance model, bakery books, restaurant inventory, home-care payroll, outdoor-gear online orders, apartment rent ledger). Each has one or two workbooks, an owner brief, at least 5 owner-only facts that change correct answers, three plain questions whose answers are computed by code from the workbook under the owner's rules, and one open request. Five of the twelve main workbooks are written with xlsxwriter and seven with openpyxl, so the file writer meets more than one kind of file. Builders get no list of trap types and are told not to design anything for an AI tool: they write the facts a real owner of that kind of business carries, which keeps the businesses from being shaped around the tool's detectors. Each business passes a separate verifier (answers recomputed independently, naive answers differ from the key, no owner fact stated anywhere in the workbook, questions that do not name their rule) before its brain is built. The builder spec is kept with the results.
- **Brains:** built by an agent playing the owner from the brief, running the full skill, answering only from the brief ("not sure" where it is silent; the brief's own words when no option says exactly what the brief says). Gate before any answer run: the tab exists and holds at least one owner note, and two checkers read every owner note against the brief (contradictions; owner claims the brief never makes). A brain that fails is rebuilt from scratch, at most 4 builds; a business with no passing brain is left out and reported.
- **Answering models (cold, no skill):** Claude Haiku 4.5, Claude Sonnet 5, Claude Opus 5.5, OpenAI GPT-5.6-Luna; 2 repetitions per business, model and condition: 96 pairs, 192 trials.
- **Isolation:** exactly the final setup of the first test (v5.1 and the path detector as amended by its item 16), unchanged: each trial in its own root with only its workbook copies, scrubbed environment, OS read denial for Claude runs, Codex with plugins and apps off, trials of one business never at the same time, a flagged trial re-run once and a second flag dropping the pair.
- **Prompt:** as in the first test: "I just got {this file / these files} from a colleague: {names}. {open request} Also: (1) {q1} (2) {q2} (3) {q3} Python 3 with openpyxl is at /usr/bin/python3 if you want to open the files. Work numbers out from the files; don't guess. Answer in plain words, under 500 words total."
- **Judges:** two per pair from two model families (Claude Opus 5.5; OpenAI GPT-6-Astra), each blind to which answer had the brain (coin-flip order kept outside the judge's folder), seeing the owner's brief, the reference (questions, correct answers, how they are computed, grading rules) and the two answers. One change from the first test, to remove a bias found there: the judge prompt says the workbook may contain a tab of notes written by its owner, so an answer that cites such notes is not inventing a source.

## Measures

- **Primary:** for each business, the mean over its pairs of (brain score minus no-brain score), where a score is the mean of the two judges' on target + right + useful (0 to 15).
- **Secondary:** (1) plain-question accuracy per business (judge 1 grades; agreement with judge 2 reported); (2) owner-catchable mistakes; (3) judge preference; (4) fact capture: the share of each brief's owner facts that land in the brain as correct owner notes the tool asked for (reported, not tested).

## Analysis (fixed at the freeze)

- **Primary test:** Wilcoxon signed-rank, two-sided, alpha 0.05, on the 12 business-level mean differences. Reported with the mean and median business difference and a 95% bootstrap CI over businesses (10,000 resamples).
- **Secondary tests:** Wilcoxon signed-rank on the 12 business-level differences in plain-question accuracy, in mistakes, and in preference; pair-level results (Wilcoxon, McNemar) reported as descriptive only, since pairs within a business are not independent.
- **Breakdowns (described, not tested):** per model, per business, per judge.
- **Missing data:** as in the first test. A business with fewer than half its pairs complete is reported but left out of the primary test.

## Integrity

- The tool is frozen before any business for this test exists: manifest hash below. No change to the tool until the analysis is done.
- I (the operator) do not read the new businesses' briefs, keys or workbooks before the brains are gated; builders' self-checks and a separate verifier decide whether a business is sound, and only pass or fail reaches me.
- Answer agents never see briefs or keys; judges never see the condition; builders never read this repo.
- Every trial, verdict, exclusion and deviation is kept and reported.

## Freeze

(filled in at freeze time)
