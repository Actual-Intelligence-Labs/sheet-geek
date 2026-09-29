# v0.2 stage 1: handoff notes between lanes

Each lane could change only its own files, so it left notes for the others. Items marked INTEGRATE are done in the integration pass; the rest is context for stage 2.

1. **INTEGRATE** (build:replies-and-notes) Item 3 (write path): Composer._rec still cuts statements at 500 characters. The Q&A frame repeats the prompt in every note, so a long prompt plus typed text can hit that cut until item 3 removes it. Also, typed answers are now split into one told note per sentence (ids f:<qid>, f:<qid>:2, ...). A '1,200-character typed answer reads back byte-identical' test should use a single sentence, or join the notes that carry ref q:<qid>.

2. (build:replies-and-notes) Item 7 (rules readback): per-option statements no longer claim 'counted totals leave its rows out'. _exclusion_facts remains the only place that states an applied exclusion. Playbook exclusion fact templates ({answer_labels}) are now effectively unused: the Q&A frame replaces them, and only fact.statements maps are used. Item 7(e) rewording of those templates is therefore cosmetic. Answers now carry 'about' ({table, col, values, aspect}), 'descs' and, after 'ok', 'accepted'.

3. (build:replies-and-notes) Item 8 (ranking and coverage): every question now has meta.about with aspect in meaning, treatment, unit, grain, scope or history (playbook kinds map through interview._ASPECT). The answer stores about too, so coverage can key on (table, col, aspect) from state.json. Use interview.goal_ids(answers) for any goal-weighted ranking; never read answers['goal']['options'] directly for ranking. _options_for no longer caps at 4, so len(_options_for(q)) can be 5.

4. (build:replies-and-notes) Item 13 (code dossiers, replacing _code_questions): use interview.is_unit_col(header, rid) and _UNIT_CODES to skip unit-of-measure columns only. Give each dossier meta.about = {table, col, values, aspect: 'meaning'} and keep options to at most 3 real ones plus Not sure (the new contract lint in tests/test_faithful.py runs over finding_questions, follow_ups, _alias_questions and grow_questions on every fixture, and will cover new detectors that emit through finding_questions). Per-option statements must use only words from the prompt, label, description or interview._FRAME, or they fall back to the Q&A frame.

5. (build:replies-and-notes) Items 14 to 18 (new finding questions): the same contract applies. Include a digit in the prompt, set meta.about naming an existing table and column (or a model row label), use no bundled labels (no 'and', no comma lists, no ', not ...') in kinds exclusion, definition, coverage, rule and unit, and keep curated statements to words that were on screen. The signflip label is now 'Same meaning' and its 'same' statement dropped the unseen 'Both are the same kind of' clause. Duplicates labels ('Same, keep the oldest row', ...) were left for item 15.

6. **INTEGRATE** (build:replies-and-notes) Item 10 (_things linking, not my lane): brain._things still finds re-coded pairs by parsing told statements for 'all the same' and 'X -> Y'. The follow_codes curated 'all' statement keeps 'all the same thing' so this still works, but item 10(f) should switch the linking to answer options and meta.about. The Q&A frame puts the whole prompt into told notes, so word matching on statements now also catches words from the prompt.

7. (build:replies-and-notes) sb.py (not touched): cmd_answer's lead line uses answers['goal']['labels'], which is now empty when the goal was typed. It falls back to 'based on your answers', which is fine.

8. **INTEGRATE** (build:save-and-sources) Lane that owns findings.follow_ups / tests/test_findings.py: test_an_answer_retires_the_matching_guess now fails. Your new follow_net_* prompt 'Of the N negative rows, ...' becomes an open item that contains 'negative' after the answer. Either reword the prompt, or narrow the test's filter to record == 'fact' (the guess it checks is a fact).

9. **INTEGRATE** (build:save-and-sources) Playbook lane: add evidence gates to three gotchas so 10d takes effect. In sales_transactions.json, the order_date gotcha gets "evidence": ["times_near_midnight:order_date"] and the discount gotcha gets "evidence": ["full_discount:discount"]. In procurement.json, the contract_price gotcha gets "evidence": ["no_insight:<recipe prefix of the rank-19 price-list conformity insight when it disagrees>"]. The field is a list of 'check:role' strings, or 'no_insight:<recipe prefix>'; an unknown check never passes. brain.gotcha_lint (a clause that has a {values|count|sum|rows} slot and a hedge word) already passes on every playbook; please keep it passing when you edit gotcha 'say' text.

10. (build:save-and-sources) Rank-19 lane (price-list conformity): tell the playbook lane the recipe prefix your conformity insight uses when the prices do not float, so the contract-price guess can be gated with no_insight:<prefix>.

11. **INTEGRATE** (build:save-and-sources) Lane that owns Composer._things or the question meta: item 10f is still open. Notes should link to their question's meta.about table and column, falling back to word matching only within the same table.

12. **INTEGRATE** (build:save-and-sources) sb.verify_received (not in my lane) still calls is_imperative on every received record, computed ones included, so a received column note like 'Check Date on X is ...' is counted as 'reads like an instruction' in the received_line. Switch it to brain.reads_as_command(statement, headers), with headers taken from the col: node labels, and skip our own computed notes.

13. **INTEGRATE** (build:save-and-sources) Docs lane (SKILL.md, references): a failed save now returns ok:false with next 'ask' (the question _save_failed: Try again, This machine only, Stop here) and a 'then' hint. SKILL.md still says 'ok: false: show say and stop', so it needs a line that says: on a failed save, ask the question. written[] entries now carry result (added, replaced, unchanged, sidecar, local or not_written), verified and reason.

14. (build:save-and-sources) Anyone calling say.done_card: the signature changed to done_card(name, rows, private_n, outcome, *, result=None, reason='', copy='', touched=False, drawing_links=0, graph_path=''). sb.cmd_save is the only caller today. say.tab_counts(rows) and say.SAVED are new public helpers.

15. (build:save-and-sources) The item 1/2 lane now splits typed answers into one told note per sentence, so f:<qid> holds only the first sentence. My never-cut test uses a single long sentence for that reason. Told statements are no longer capped anywhere, but a single statement over 32,767 characters would make brainzip refuse the write. The save now reports that honestly as a failure instead of claiming success.

16. (build:save-and-sources) brain.py exports new helpers: reads_as_command, gotcha_clauses, gotcha_lint, STATEMENT_CAP and _clip. Use reads_as_command, not is_imperative, for any new lint on notes people wrote.

17. (build:rows-values-synth) Rank 14 (boundaries): Table.segments = [{header_row, headers, start}] uses 0-based sheet rows. It is empty unless the body repeats a header, and start is the seam. Table.retyped[j] = {kind: date|number, format, rows (0-based sheet rows), examples}: use it to rebuild the 'text date' form family per row, since those cells are datetimes in t.rows now.

18. (build:rows-values-synth) Ranks 18a and 21b: detect.debit_credit_pair(table, cols) returns (left, right) Cols or None. detection['pairs'] = {tid: [debit_header, credit_header]}. Roles filled from a pair carry 'inferred': True.

19. (build:rows-values-synth) Rank 13 (dossiers): integer code columns now come out as Col.semantic == 'identifier' with Col.codes True (2 to 30 values, 10+ rows each, code-like header or equal width of 3+ digits). Text code columns keep the old c.codes rule. Col.variants lists spellings that differ only in capitals or spaces: [(usual, [(variant, n)])].

20. (build:rows-values-synth) New structure insight recipes: structure:segments:<tid>, structure:read_from_text:<tid>:<col>, structure:mixed_types:<tid>:<col>:<kind>, structure:spellings:<tid>:<col>. All are kind 'structure', weight 0, with no oddity flag. The structure:totals_rows statement changed to '<sheet> has N total rows inside the data (rows a, b), left out of every count.' Its numbers now carry row_numbers and sheet. apply_answers already keeps them through the 'structure' prefix.

21. (build:rows-values-synth) Brain and fingerprint owners: retyped cells change column_values, so a brain saved by v0.1 on a file with text dates or currency text reads may-be-outdated once after the upgrade.

22. **INTEGRATE** (build:rows-values-synth) Item 6 (_column_statement): with rate headers unbound, 'Rebate %' already shows no $ and no sum. The plan's %/pct/rate/per sum skip still belongs in brain._column_statement for columns bound through other routes.

23. (build:rows-values-synth) Replies-and-notes lane: 12f (_UNIT_CODES only for unit-of-measure columns) and its test (a 2-letter 'CS' in a Channel column is not skipped) are yours.

24. (build:rows-values-synth) Ranks 13 to 21 test writers: import synth; call synth.build(path, seed, name, twin=False) or synth.build_book(path, seed, [name or (name, twin), ...]). Take synth_seed as a test argument to get 20 seeds (SB_SEEDS=3 while iterating). The noise_fixtures fixture gives the four noise-budget builds. Each plant has 'expect' (the question kind) and 'rows' (1-based Excel rows); twins have expect None. Add new trap classes with the @synth.trap(kind) decorator. Not in synth yet: the rank 13e fact column mixing lookup codes and names, and the rank 19 purchases-plus-terms two-file set.

25. (build:rows-values-synth) detect.match_roles now uses _lex_best (score and phrase) plus _binds(). The old _lex_match(header_n, lex) still returns the score.

26. **INTEGRATE** (fix:replies-and-notes) privacy.py / sb.py owner: privacy.screen rejoins kept sentences with '; ' and drops line breaks, so the owner's words are not verbatim and one line no longer splits from the next. sb.py answer intake (about line 468, and line 525 for the build choice) stores that rejoined text. Fix it once in privacy.screen: remove only the private sentences from the original text and keep the rest byte for byte. brain._without_private shows the pattern: classify, then text.replace(sentence, '', 1), then strip.

27. **INTEGRATE** (fix:replies-and-notes) item 10 lane (owner of brain._things): I changed the re-code same_as block to decide from the answer (options == ['all'] and no typed text, once per question) instead of from 'all the same' in note text. The Q&A frame puts every OLD -> NEW arrow into every sentence note, so a text test would link pairs the owner called different. Please keep this if you rework _things.

28. **INTEGRATE** (fix:replies-and-notes) for_tab owner (brain.py): style regression 'insights =[' should be 'insights = ['.

29. **INTEGRATE** (fix:replies-and-notes) item 5 lane (profile.py): the new docstring example uses 'RDG', a code from a development business. Please replace it with a neutral one such as ('ABC', 'abc', 'ABC ').

30. **INTEGRATE** (fix:replies-and-notes) dev/lint_playbooks.py owner (not run by the suite): it is out of date. It rejects the recommend_if key (already true before this change), does not know the new {rows:role} slot (add 'rows' to ROLE_SLOTS for role-form slots; the bare {rows} stays in BARE_SLOTS), and rejects role slots in recommend_basis, which candidates() now fills with interview.fill. Its predicate vocabulary should also gain repeats:<role>.

31. (fix:replies-and-notes) rules.not_items calls findings._code_pairs(analysis, a). _code_pairs now picks the unmatched finding that the answer's about names (then the first as a fallback), so with two unmatched tables the not-items set now comes from the right table. No signature change.

32. **INTEGRATE** (fix:save-and-sources) Playbook lane: in sales_transactions.json add "evidence": ["times_near_midnight:order_date"] to the UTC gotcha and "evidence": ["full_discount:discount"] to the discount/comps gotcha. The comps gate now needs this table's currency-unit roles (and count-unit roles for price times quantity) to be bound. Also drop the literal 'and more' after {values:channel} in the channel gotcha (brain now appends ', plus N more' itself only when the list was cut). Then remove the xfail marks on tests/test_save.py::test_no_playbook_value_list_trails_off and ::test_a_sales_book_with_no_times_of_day_gets_no_time_zone_note. procurement.json contract_price gotcha: add evidence no_insight:<rank-19 recipe prefix> once that recipe is named.

33. **INTEGRATE** (fix:save-and-sources) Item-5 lane (analyze._table_insights): add "table": t.tid to the numbers of structure:totals_rows (analyze.py around line 369), and to any other insight that has no depends. brain.Composer._insight_hid now hashes numbers['table'] (with the file prefix stripped), which separates i:b85a47cf~2 on messy_multitable and i:f847c178~2 on procurement_hotel. Single-table ids do not change.

34. **INTEGRATE** (fix:save-and-sources) Composer._columns owner: when a sheet holds more than one table, put the table (for example the tid suffix) in col: node ids. col:Regional Sales.{Region}~2 and five col:Summary.{...}~2 ids still depend on the order-based suffix.

35. **INTEGRATE** (fix:save-and-sources) Composer._things owner (plan 10f): link each told note to its question's meta.about table and column, falling back to word matching only within that table, and add plan 10's linking test.

36. **INTEGRATE** (fix:save-and-sources) interview lane: interview.fill '{values:x}' still joins only the top 3 values and says nothing when more exist. Consider rendering '..., and N more' there generally (brain handles only the explicit 'and more' template tail).

37. **INTEGRATE** (fix:save-and-sources) Docs lane (SKILL.md): no change is required. A failed save still emits next 'ask', and sb.py answer now reads that reply as a save choice (retry, this machine only, start again, or stop), never as an owner answer. The answer can emit next 'done' or 'start', both already in NEXT_HELP.

38. (fix:save-and-sources) Anyone editing cmd_answer: the _save_failed dispatch sits right after `state = ctx.store.state(bid)` and before raw parsing. It must stay ahead of the stop-word regex.

39. **INTEGRATE** (fix:rows-values-synth) brain.py owner: brain._column_statement (called at brain.py:493) still appends '(reads as <label>)' for any bound role. Now that detect only binds debit and credit from header evidence the label is no longer wrong, but those entries still carry inferred:true in a.detection['roles'][rid]. Please leave out the '(reads as ...)' clause, or record the note as inferred/unconfirmed, when a.detection['roles'].get(rid, {}).get('inferred') is true. Entries marked 'unbound' never reach detection['roles'].

40. (fix:rows-values-synth) New structure recipe from this lane: structure:ambiguous_dates:<tid>:<header> (kind structure, weight 0, numbers: table, col, cells, examples). structure:read_from_text numbers now carry 'format'. structure:totals_rows numbers now carry 'table'. Col has two new fields: retyped_format and ambiguous_dates.

41. (fix:rows-values-synth) detect exports pair_sides(pair) -> (debit, credit) | None. debit_credit_pair still returns (left, right) by position, and detection['pairs'] is unchanged.

42. (fix:rows-values-synth) synth changes that detector lanes should know: the reimport twin now has split payments and repeat orders in one number family; status_rare codes may be 1 to 3 letters or short words; per_case pack is 4 to 48; the sentinel twin salary is a whole-dollar int on about half the seeds; the stacked_export twin has a 'TBD'/'N/A' line (manifest keys placeholder_row and placeholder).
