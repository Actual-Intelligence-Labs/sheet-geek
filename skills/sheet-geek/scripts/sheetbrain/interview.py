"""The interview engine. Code decides whether to ask, what to ask, in what
order, with which options and which recommendation; the model relays the text
word for word. This is what lets a weaker model grill as well as a strong one.

Budget: one list of every question worth asking, ranked by value (stake x
certainty x kind weight). Round 1 is the goal plus the 3 most valuable; later
rounds up to 4 more. At most 10 substantive questions per workbook (a Not sure
counts; the goal, the closing question and the build pick do not). After the
last round, one closing question asks what someone new would get wrong. Anything
unasked becomes an open item that is asked just in time when an output needs it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import brainzip
from .profile import norm_key

ROUND1 = 4          # the goal plus the 3 most valuable questions
ROUND_N = 4         # later rounds: at most 4 substantive questions each
MAX_ROUNDS = 4      # a ceiling only: rounds stop sooner when nothing is worth asking or the cap is reached
HARD_CAP = 10       # substantive questions per workbook, a Not sure included
TAU = 1.5           # a question worth less than this waits for just in time
NOT_SURE = {"id": "not_sure", "label": "Not sure", "desc": "Skip it; I'll mark it open, not guess"}
CLOSER = "known_issues"

# value = kind weight x certainty x (6 + 6 x the stake's materiality). The stake is the share of the
# table's money the answer can move (its rows when it has no money; for a model cell, the share of the
# formula cells downstream of it); from 5% of the money on (materiality) it counts in full
MATERIAL = 0.05
KIND_WEIGHT = {"meaning": 1.0, "treatment": 1.0, "unit": 1.0, "grain": 1.0, "blanks": 1.0, "scope": 0.8,
               "history": 0.6}
PRIOR = 0.5          # certainty of a playbook question no detector backs: a prior about files like this
BACKED = 0.75        # a playbook question about a column a detector found something in
FOLLOW_UP = 1.5      # a follow-up continues a thread the owner opened: worth more, never ahead by rule


def worth(stake: float, aspect: str = "meaning", certainty: float = 1.0) -> float:
    """What a question is worth on the one scale every source uses."""
    s = max(0.0, min(1.0, float(stake or 0.0)))
    return round(KIND_WEIGHT.get(aspect, 1.0) * certainty * (6.0 + 6.0 * min(1.0, s / MATERIAL)), 3)


@dataclass
class Q:
    id: str
    header: str
    prompt: str
    options: list
    why: str = ""
    multi: bool = False
    recommend: str | None = None
    recommend_basis: str = ""
    unblocks: list = field(default_factory=list)
    kind: str = "definition"
    priority: int = 2
    fact: dict | None = None
    stale_after: str = ""
    gated: bool = False          # depends on an earlier answer
    source: str = "playbook"
    value: float = 0.0
    meta: dict = field(default_factory=dict)


# --------------------------------------------------------------------------
# predicates (the fixed vocabulary in CONTRACTS.md section 3)
# --------------------------------------------------------------------------
class Env:
    def __init__(self, analysis, answers: dict):
        self.a = analysis
        self.det = analysis.detection
        self.answers = answers or {}

    def col(self, rid):
        r = self.det["roles"].get(rid)
        return r.get("col") if r else None

    def holds(self, pred: str) -> bool:
        name, _, arg = pred.partition(":")
        a = self.a
        if name == "has_role":
            return arg in self.det["roles"]
        if name == "no_role":
            return arg not in self.det["roles"]
        if name == "mixed_values":
            c = self.col(arg)
            return bool(c and 2 <= c.distinct <= 50 and c.count
                        and c.top and c.top[0][1] / c.count < 0.9)
        if name == "has_negatives":
            c = self.col(arg)
            return bool(c and c.type == "number" and c.negatives > 0)
        if name == "has_blanks":
            c = self.col(arg)
            return bool(c and c.blank_rate > 0.05)
        if name == "coded":
            c = self.col(arg)
            return bool(c and c.codes)
        if name == "repeats":           # some value in the column is on more than one row
            c = self.col(arg)
            return bool(c and not c.distinct_capped and 0 < c.distinct < c.count)
        if name == "tree_names":        # "Expenses:Rent": the account tree written into the name
            c = self.col(arg)
            return bool(c and sum(1 for k in c.counter if isinstance(k, str) and ":" in k) >= 2)
        if name == "multi_dates":
            return any(sum(1 for c in a.cols[t.tid] if c.type == "date") >= 2 for t in a.tables)
        if name == "multi_files":
            return len(a.books) >= 2
        if name == "multi_sheets":
            return any(len(b.data_sheets()) >= 2 for b in a.books)
        if name == "has_formulas":
            return any(fa.get("count", 0) > 0 for fa in a.formulas.values())
        if name == "has_derived_tab":
            return bool(a.derived)
        if name == "rows_gt":
            try:
                return bool(a.main_table) and a.main_table.n_rows > int(arg)
            except ValueError:
                return False
        if name == "answered":
            qid, _, opt = arg.partition("=")
            ans = self.answers.get(qid)
            return bool(ans and opt in ans.get("options", []))
        if name == "goal":        # a pick, or a typed goal that holds every topic word of the goal's label
            ans = self.answers.get("goal")
            if not ans:
                return False
            if arg in (ans.get("options") or []):
                return True
            label = next((g.get("label", "") for g in (a.playbook or {}).get("goals") or [] if g.get("id") == arg), "")
            return goal_clearly_names(str(ans.get("text") or ""), label)
        if name == "has_totals_rows":
            return any(t.totals_rows for t in a.tables)
        if name == "not_answered":
            ans = self.answers.get(arg)
            return not ans or ans.get("not_sure", False)
        return False

    def all(self, preds) -> bool:
        if isinstance(preds, str):
            preds = [preds]
        return all(self.holds(p) for p in (preds or []))


# --------------------------------------------------------------------------
# template slots
# --------------------------------------------------------------------------
_SLOT = re.compile(r"\{(role|values|count|rows|sum):([a-z0-9_]+)\}|\{(rows|answer_labels|answer_text|answer_number)\}")


def answer_number(answer: dict | None) -> str:
    """The number the owner typed with a pick that needs one ('12 to a case' gives
    '12'), as they wrote it; '' when they typed none."""
    from .rules import typed_count
    got = typed_count(str((answer or {}).get("text") or ""))
    return (str(int(got)) if float(got).is_integer() else str(got)) if got else ""


def fill(template: str, env: Env, answer: dict | None = None) -> str:
    """A template with its slots filled. A statement that names the number the
    owner typed ({answer_number}) is empty when they typed none: it is never
    written with a number nobody gave."""
    if "{answer_number}" in str(template) and not answer_number(answer):
        return ""

    def rep(m):
        kind, arg, bare = m.group(1), m.group(2), m.group(3)
        if bare == "answer_number":
            return answer_number(answer)
        if bare == "rows":
            return f"{env.a.main_table.n_rows:,}" if env.a.main_table else "the"
        if bare == "answer_labels":
            labs = [_in_sentence(x) for x in (answer or {}).get("labels") or []]
            if labs:
                return _join(labs)
            if "{answer_text}" in template:
                return ""            # the words come in through {answer_text}; don't say them twice
            return _quoted((answer or {}).get("text", ""))
        if bare == "answer_text":
            text = _quoted((answer or {}).get("text", ""))
            return text or _join([_in_sentence(x) for x in (answer or {}).get("labels") or []])
        r = env.det["roles"].get(arg)
        if kind == "role":
            return r["header"] if r else arg.replace("_", " ")
        c = r.get("col") if r else None
        # values the owner said are internal or left out are not listed or counted as the column's own
        gone = _left_out(env, r) if c else set()
        if kind == "values":
            if not c:
                return arg.replace("_", " ")
            # examples come from the majority: never a value seen only on the rows of a group still being asked
            odd = _odd_only(env, r)
            left = [k for k, _ in c.top if norm_key(k) not in gone and norm_key(k) not in odd]
            n = c.distinct - (sum(1 for k in c.counter if norm_key(k) in gone) if gone else 0)
            # every value when there are 8 or fewer (none hidden behind 'more'), else the 3 commonest and how many more
            shown = [s for s in (_short(k) for k in left[:8 if n <= 8 else 3]) if s]
            more = n - len(shown)              # a cut list says so, and by how many,
            if re.search(r"(such as|for example|include)\s*$", template[:m.start()], re.I):
                more = 0                       # unless the words before it already say it is a sample
            return _join(shown + ([f"{more:,} more"] if more > 0 else []))
        if kind == "count":            # different values in the column
            return f"{c.distinct - (sum(1 for k in c.counter if norm_key(k) in gone) if gone else 0):,}" if c \
                else "several"
        if kind == "rows":             # rows with something in the column
            return f"{c.count:,}" if c else "several"
        if kind == "sum":
            if not c or c.type != "number":
                return "the total"
            from .recipes import fmt_money
            return fmt_money(c.sum)
        return m.group(0)
    return _SLOT.sub(rep, template)


def _odd_only(env: Env, r: dict | None) -> set:
    """Keys of a role's column seen only on the rows of a group code found unlike
    the others (on the same table) that the owner has not answered about yet."""
    if not r or not r.get("table") or r.get("col") is None:
        return set()
    a = env.a
    t = next((t for t in a.tables if t.tid == r["table"]), None)
    odd = [i["numbers"] for i in getattr(a, "insights", None) or [] if i.get("recipe", "").startswith("oddgroup:")
           and i["numbers"].get("table") == r["table"] and i["numbers"].get("col") != r.get("header")]
    if t is None or not odd:
        return set()
    said = {norm_key(x.get("meta_value")) for x in (env.answers or {}).values() if isinstance(x, dict)}
    groups = [(t.headers.index(n["col"]), norm_key(n["value"])) for n in odd
              if norm_key(n["value"]) not in said and n["col"] in t.headers]
    if not groups:
        return set()
    j = r["col"].j
    inside, outside = set(), set()
    for row in t.rows:
        v = norm_key(row[j] if j < len(row) else None)
        hit = any(norm_key(row[g] if g < len(row) else None) == k for g, k in groups)
        (inside if hit else outside).add(v)
    return inside - outside


def _left_out(env: Env, r: dict | None) -> set:
    """Keys of a role's column the owner said are not its own: picked as internal,
    counted elsewhere or left out (a question's exclude options), or a confirmed
    rule that leaves those values out of every count and total."""
    out: set = set()
    if not r or not r.get("table"):
        return out
    tid, col = r["table"], r.get("header")
    for a in (getattr(env, "answers", None) or {}).values():
        if not isinstance(a, dict) or a.get("not_sure"):
            continue
        ex = a.get("exclude") or {}
        if ex.get("table") == tid and ex.get("col") == col and set(a.get("options") or []) & set(ex.get("options") or []) \
                and not ex.get("scope"):
            out |= {norm_key(v) for v in ex.get("values") or []}
        for d in a.get("rules") or []:
            pred = d.get("predicate") or []
            if d.get("confirmed") and d.get("kind") == "exclude" and d.get("table") == tid and not d.get("scope") \
                    and len(pred) == 1 and pred[0].get("col") == col and pred[0].get("op", "in") == "in":
                out |= {norm_key(v) for v in pred[0].get("values") or []}
    out.discard(None)
    return out


def _quoted(text) -> str:
    """The owner's own words, quoted, so they read as what someone said, never as a command."""
    t = str(text or "").strip().rstrip(";, ")
    if not t:
        return ""
    t = t.replace('"', "'")
    return f'"{t if t[-1] in ".!?" else t + "."}"'


def _in_sentence(label: str) -> str:
    """An option label read inside a sentence: 'Credits or returns' -> 'credits or returns',
    without the button's aside ('Not real items (fees, holds)' -> 'not real items')."""
    s = re.sub(r"\s*\([^)]*\)", "", str(label)).strip()
    first = s.split(" ", 1)[0]
    if first and first[0].isupper() and first != "I" and (len(first) == 1 or first[1:].islower()):
        s = s[0].lower() + s[1:]          # 'A test to take out' -> 'a test to take out'; 'ABC' stays
    # button labels are in the owner's voice ("Check what I'm charged"); a note is about them
    s = re.sub(r"\bI'm\b", "they are", s)
    s = re.sub(r"\bI\b", "they", s)
    s = re.sub(r"\b[Mm]y\b", "their", s)
    s = re.sub(r"\b[Oo]ur\b", "their", s)
    return s


# words any note about a pick may use without the owner having seen them
_FRAME = {"owner", "said", "says", "per", "count", "counted", "counts", "total", "totals", "row", "rows",
          "one", "all", "each", "every", "same", "thing", "things", "mean", "means", "meant", "value", "values",
          "before", "after", "not", "also", "only", "should", "would", "today", "here", "there", "their",
          "them", "they", "these", "those", "this", "that", "which", "what", "with", "from", "into", "than",
          "then", "and", "are", "was", "were", "has", "have", "had", "for", "its", "the", "but", "both",
          "other", "others", "column", "columns", "tab", "tabs", "sheet", "file", "written", "applies",
          "apply", "where", "when", "under", "does", "don", "isn", "aren", "been", "being", "some", "any"}


def _stem(w: str) -> str:
    """A rough word stem: 'codes' and 'code', 'cleaned' and 'clean' meet."""
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        w = w[:-1]
    return w[:5]


def _stems(text: str) -> set:
    return {_stem(w) for w in re.findall(r"[a-z]+", str(text or "").lower()) if len(w) >= 3}


def unseen_words(statement: str, prompt: str, label: str, desc: str = "") -> set:
    """Content words in a per-option statement that the owner never saw: not in
    the question, the picked label or its description, nor in the fixed frame
    words ('per the owner', 'rows', 'totals'). A statement with any of them is
    not used; the note quotes the pick instead."""
    seen = _stems(prompt) | _stems(label) | _stems(desc) | {_stem(w) for w in _FRAME}
    return {w for w in re.findall(r"[a-z]+", str(statement or "").lower())
            if len(w) >= 3 and _stem(w) not in seen}


def _header(h) -> str:
    """A header that fits the ask tool, in whole words (findings.header_words)."""
    from .findings import header_words
    return header_words(h)


def _short(v) -> str:
    s, _ = brainzip.clean_text(str(v))
    s = re.sub(r"\s+", " ", s).strip()
    return s[:40]


def _join(items: list) -> str:
    items = [i for i in items if i]
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


# --------------------------------------------------------------------------
# candidates
# --------------------------------------------------------------------------
def _gated(preds) -> bool:
    return any(p.startswith(("goal:", "answered:", "not_answered:")) for p in (preds or []))


def candidates(analysis, answers: dict, *, multi_file_note: str = "", settle: bool = True) -> list:
    """Every question worth asking now, each with its value. settle=False keeps the
    detector questions an answer about the same column settled (the closing
    question still names what code found and never asked)."""
    env = Env(analysis, answers)
    pb = analysis.playbook
    out: list = []
    goals = pb.get("goals") or []
    if goals and "goal" not in answers:
        several = len(analysis.books) > 1
        out.append(Q("goal", "Goal",
                     ("What do you want from these sheets?" if several else
                      "What do you want from this sheet?") + " Pick all that apply.",
                     [{"id": g["id"], "label": g["label"], "desc": g.get("desc", "")} for g in goals[:4]],
                     why="It decides which questions are worth your time and what I build.",
                     multi=True, kind="goal", priority=1, source="builtin",
                     fact={"kind": "goal", "class": "data", "depends": [],
                           "statement": "The owner's goal for this data is: {answer_labels}."}))
    from . import findings
    found = findings.finding_questions(analysis, answers)     # what code found in THIS file, dossiers included
    covered = covered_ids(analysis, answers, found)
    said = _settled_by(answers, unsure=True)
    detected = found + _bridge_questions(analysis, answers) + _alias_questions(analysis, answers, said)
    # one question per value: a finding about some of the values another finding on the same column names
    # is folded into it, its evidence carried in the survivor's prompt
    detected = _fold_same_values(detected)
    live = [q for q in detected if q.gated or not _is_settled(q, said)]
    # the columns a detector found something in and still asks about, with the largest stake; a finding the
    # owner already answered backs nothing (a prior never borrows the stake of a question already asked)
    touched: dict = {}
    lowest: dict = {}                    # (table, column) -> the least valuable unasked finding on it
    pending = {}                         # (table, column, aspect) a detector asks about -> its questions' labels
    for q in live:
        ab = q.meta.get("about") or {}
        if ab.get("col") and not q.id.startswith(findings.READBACK):
            key = (ab.get("table"), ab["col"])
            touched[key] = max(touched.get(key, 0.0), float(q.meta.get("stake", 0.0)))
            lowest[key] = min(lowest.get(key, q.value or 0.0), q.value or 0.0)
            pending.setdefault(key + (ab.get("aspect"),), []).append([o.get("label", "") for o in q.options])
    goal_text = str((answers.get("goal") or {}).get("text") or "")
    goal_hits = _goal_answers(analysis, answers)          # an option the typed goal names: backed, recommended
    for q in pb.get("questions", []):
        if q["id"] in answers or q["id"] in covered:
            continue
        preds = q.get("ask_if", [])
        if not env.all(preds):
            continue
        # a recommendation only when the data backs it (recommend_if); a prior alone is not evidence
        rec = q.get("recommend") if q.get("recommend_if") and env.all(q["recommend_if"]) else None
        meta = _playbook_about(analysis, q)
        ab = meta.get("about") or {}
        if ab.get("col") and any(_same_ask(q.get("options") or [], labels)
                                 for labels in pending.get((ab.get("table"), ab["col"], ab.get("aspect")), [])):
            continue                     # merged: the detector's question asks the same thing about this column
        # a playbook question enters a round only when a detector found something in its column, or
        # when it asks what a row is on a table code could not key; otherwise it waits for just in time
        # (its stake is what the detector found there, so it never outranks the finding that backs it)
        key = (ab.get("table"), ab.get("col"))
        if _governs_money(analysis, env, q, ab):
            # how every value of a money column is read (its unit, the basis its prices are on), once the
            # role the question turns on is in the session: the whole column is at stake
            meta.update(backed=True, stake=1.0)
        elif ab.get("col") and key in touched:
            meta.update(backed=True, stake=touched[key], below=lowest.get(key))
        elif q.get("kind") == "grain" and not _grain_typed(analysis, ab.get("table")):
            meta.update(backed=True, stake=1.0)
        elif q["id"] in goal_hits:
            # the goal names one of its options ('draws kept out'): asked, never taken as answered, at a prior's
            # certainty (no detector found anything there), so it never outranks a finding with evidence
            meta.update(backed=True, stake=1.0, goal_named=True, certainty=PRIOR)
        elif _goal_names(goal_text, q):
            # the owner's own goal names what the question is about ('keep transfers out of costs'): backed
            meta.update(backed=True, stake=1.0, goal_named=True)
        prompt, basis = fill(q["prompt"], env), fill(q.get("recommend_basis", ""), env) if rec else ""
        if q["id"] in goal_hits and not rec:
            # the option the owner's goal names is recommended; the pick stays theirs
            rec, basis = goal_hits[q["id"]], "Your goal names it."
        # a model's formulas back the question: the formula the rate or unit drives, ranked by how much of the
        # model it reaches; a readout assumption (where actuals end) shown with its count and recommended
        drv = _driver_evidence(analysis, q, ab)
        # an option can name a column by its role ('{role:record_key}'): filled like the prompt, never shown raw
        options = [dict(o, **{k: fill(o[k], env) for k in ("label", "desc") if "{" in str(o.get(k) or "")})
                   for o in q.get("options", [])][:4]
        if drv:
            prompt = f"{drv['statement']} {prompt}"
            meta.update(backed=True, stake=drv["reach"], reach=True)
            meta["about"] = dict(ab, values=[drv["cell"]])          # the input cell it reads, by its address
            sib = drv.get("sibling")
            if sib:
                # a rate with a sibling flow on the same stock: its period and its base in words any owner can
                # pick (per month, on the stock it multiplies), the basis the formulas show under it, recommended
                stock = sib.get("stock") or "the balance"
                before = f"Per month, on {stock}"
                if len(before) > 60:
                    before = "Per month, on the beginning balance"
                net = f"Per month, net of {sib['other']}"
                options = [{"id": "before", "label": before,
                            "desc": f"{sib['other']} is subtracted on its own row"},
                           {"id": "net", "label": net if len(net) <= 60 else "Per month, net of the other flow",
                            "desc": f"{sib['other']} already comes off"},
                           {"id": "yearly", "label": "Per year", "desc": "A rate per year"}]
                prompt = (f"{drv['statement']} Is {ab['col']} a rate per month on {stock}, net of "
                          f"{sib['other']}, or per year?")
                rec, basis = "before", sib["net"]
                meta["sibling"] = {"other": sib["other"], "stock": stock, "net": sib["net"], "col": ab["col"]}
        seen_by = _insight_evidence(analysis, q)
        fact = q.get("fact")
        # rows to leave out, named from the books: entries that only move money between balance-sheet accounts, or
        # lines of a category the item list gives no cost; the detected groups back the question
        named = _leave_out_named(analysis, q) if q.get("kind") == "exclusion" and q["id"] == "leave_out" else None
        if named:
            options, fact, stake = named
            meta.update(backed=True, stake=max(float(meta.get("stake") or 0.0), stake) if meta.get("backed")
                        else stake)
            rec, basis = None, ""
            hit = _goal_option(goal_text, options)
            if hit:
                rec, basis = hit, "Your goal names it."
        if seen_by:
            prompt = f"{seen_by['statement']} {prompt}"
            rec, basis = q["backed_by"]["recommend"], seen_by["statement"]
            meta.update(backed=True, stake=1.0, reach=True)
            fact = _backed_fact(q, rec, seen_by)
            folded = _with_source(q, seen_by)
            if folded is not None:
                # typed numbers that turn to formulas: where the typed ones come from rides on the same pick, so
                # one answer carries the switch and its source (nothing recommends a source code cannot see)
                options, fact = folded
                rec, basis = None, ""
                prompt = prompt.rstrip("? ") + ", and where do the typed months come from?"
        out.append(Q(q["id"], _header(q["header"]), prompt,
                     options, why=q.get("why", ""),
                     multi=bool(q.get("multi")), recommend=rec,
                     recommend_basis=basis,
                     unblocks=q.get("unblocks", []),
                     kind=q.get("kind", "definition"), priority=int(q.get("priority", 2)),
                     fact=fact, stale_after=q.get("stale_after", ""),
                     gated=_gated(preds), meta=meta))
    # the inputs that drive a formula model and no other question names, read back in one question
    taken = {c for q in out + detected for c in findings.cell_refs((q.meta.get("about") or {}).get("values"))}
    out += findings.driver_readback(analysis, answers, taken)
    out += detected
    out += findings.follow_ups(analysis, answers)            # threads an answer opened
    out += basis_follow_ups(answers)                          # a rate's basis a typed period left open
    out += confirm_questions(analysis, answers)               # what code counted, to confirm in room left over
    batched = {m for q in out for m in q.meta.get("members") or []}
    done = {m for a in answers.values() if isinstance(a, dict) for m in a.get("members") or []}
    seen, uniq = set(), []
    for q in out:
        if q.id in seen or q.id in answers or q.id in done or (q.id in batched and not q.meta.get("members")):
            continue
        # what the owner already settled about a column (its meaning, its treatment) for the values it names,
        # or was asked and was not sure of, is never asked again under another id; a follow-up continues its
        # own thread. A question about other values of the same column is still asked
        if settle and not q.gated and _is_settled(q, said):
            continue
        # a follow-up whose every value a confirmed map already made one (a handoff after a codes answer mapped
        # the same names) is answered too
        if settle and q.gated and q.kind == "mapping" and not q.meta.get("confirmed_by_pick") and _mapped_all(q, said):
            continue
        seen.add(q.id)
        uniq.append(q)
    for q in uniq:
        q.value = value(q, analysis, answers, env)
        if q.meta.get("below") is not None and q.source == "playbook":
            # a prior on a column stays below every finding there not asked yet
            q.value = round(min(q.value, max(0.0, q.meta["below"] - 0.01)), 3)
        _with_context(analysis, q)
        _singular(q)
    uniq = findings.batch_known(analysis, uniq, answers)       # small 'known issue?' findings asked as one
    return uniq


def _same_ask(options: list, labels: list) -> bool:
    """A playbook question's options ask what a detector's question asks: half or
    more of its labels share a topic word with one of the detector's labels. Two
    questions on one column that ask different things (which total is right,
    which rows count) are both kept, one per column per round."""
    mine = [_topic_stems(fill_free(o.get("label", ""))) for o in options]
    theirs = set().union(*[_topic_stems(x) for x in labels]) if labels else set()
    mine = [m for m in mine if m]
    return bool(mine) and sum(1 for m in mine if m & theirs) * 2 >= len(mine)


def fill_free(label: str) -> str:
    """A label with its slots taken out."""
    return re.sub(r"\{[^}]*\}", " ", str(label or ""))


BASIS = "follow_basis_"
_PERIOD_WORDS = re.compile(r"\b(month\w*|monthly|annual\w*|year\w*|quarter\w*|weekly|week)\b", re.I)
_BASIS_WORDS = re.compile(r"\b(net|gross|before|separately|subtract\w*|after)\b", re.I)


def basis_follow_ups(answers: dict) -> list:
    """A rate asked with its sibling flow, answered in typed words that say its
    period but not its basis: one one-tap question on the basis the formulas
    show ('Churn is subtracted on its own row, so Growth is on the opening
    seats before churn. Right?'), recommended from them."""
    out = []
    for qid, a in (answers or {}).items():
        sib = (a.get("sibling") or {}) if isinstance(a, dict) else {}
        text = str(a.get("text") or "") if isinstance(a, dict) else ""
        if not sib or a.get("options") or not text.strip() or f"{BASIS}{qid}" in answers:
            continue
        if not _PERIOD_WORDS.search(text) or _BASIS_WORDS.search(text):
            continue
        col, other, stock = sib["col"], sib["other"], sib["stock"]
        said = f"{sib['net']} So {col} is a rate on {stock}, before {other}."
        q = Q(f"{BASIS}{qid}", "Rate basis", f"{said} Right?",
              [{"id": "right", "label": f"Right: before {other}"[:60], "desc": f"{other} is subtracted on its own row"},
               {"id": "net", "label": f"Net of {other}"[:60], "desc": f"{other} already comes off"}],
              recommend="right", recommend_basis="The formulas show it.",
              why=f"Whether {col} is before or after {other} changes every projection of {stock}.",
              kind="unit", priority=1, source="finding",
              fact={"kind": "unit", "class": "data", "depends": [],
                    "statement": f"{said} Per the owner: {{answer_labels}}.",
                    "statements": {"right": f"{col} is a rate on {stock}, before {other}, per the owner: "
                                            f"{other} is subtracted on its own row."}},
              meta={"about": dict(a.get("about") or {}, aspect="treatment"), "stake": a.get("stake") or 1.0},
              gated=True)
        q.value = worth(float(a.get("stake") or 1.0), "unit") * FOLLOW_UP
        out.append(q)
    return out


def _leave_out_named(analysis, q: dict):
    """(options, fact, stake) for a playbook's leave-out question whose options the
    books can name: each group of entries that only moves money between
    balance-sheet accounts ('14 transfers from Bank A to Bank B'), with a note
    naming its accounts; else a category an item list gives no cost, in place of
    the generic option with the least behind it. None when the books name none."""
    from . import findings
    moves = next((i for i in analysis.insights if i.get("recipe", "").startswith("moves:")), None)
    fact = dict(q.get("fact") or {})
    prompt_goal = re.sub(r"^Which of these should |\? Pick all that apply\.$", "", q.get("prompt", ""))
    if moves:
        from .analyze import _move_words
        opts, statements = [], {}
        for k, g in enumerate(moves["numbers"]["groups"][:3], 1):
            words = _move_words(dict(g, memo=""))
            label = words if len(words) <= 60 else words[:57].rsplit(" ", 1)[0] + "..."
            mc = moves["numbers"].get("memo_col") or "Memo"
            desc = f"{g['entries']:,} entries, {g['rows']:,} lines" + (f", {mc} \"{g['memo'][:60]}\"" if g.get("memo")
                                                                      else "")
            oid = f"moves_{k}"
            opts.append({"id": oid, "label": label, "desc": desc})
            statements[oid] = (f"The {words}" + (f" ({mc} \"{g['memo'][:60]}\")" if g.get("memo") else "")
                               + f" {prompt_goal or 'stay out of the totals'}, per the owner.")
        fact["statements"] = statements
        return opts, fact, float(moves["numbers"].get("share") or 0.0)
    non = next((i for i in analysis.insights if i.get("recipe", "").startswith("nonstock:")), None)
    if non and len(q.get("options") or []) >= 3:
        n = non["numbers"]
        lab = f"{n['category']} lines"[:60]
        opts = list(q["options"])[:2] + [{"id": "nonstock", "label": lab,
                                          "desc": f"{n['rows']:,} lines with no {n['blank_col']} on the list"}]
        fact["statements"] = dict(fact.get("statements") or {}, nonstock=(
            f"The {n['rows']:,} {n['category']} lines {prompt_goal or 'stay out of the totals'}, per the owner."))
        return opts, fact, float(n.get("share") or 0.0)
    return None


def _goal_option(goal_text: str, options: list) -> str | None:
    """The one option the typed goal names: it holds a topic word of that option's
    own (a word no other option has), and none of any other option's own words
    ('draws kept out' names '6 draws to Partner Draws'). None otherwise."""
    if not str(goal_text or "").strip():
        return None
    said = _topic_words(goal_text)
    own = {}
    for o in options:
        words = _topic_words(re.sub(r"\d[\d,]*", " ", o.get("label", "")))
        others = set().union(*[_topic_words(re.sub(r"\d[\d,]*", " ", x.get("label", ""))) for x in options
                               if x is not o]) if len(options) > 1 else set()
        own[o["id"]] = words - others
    hits = [oid for oid, w in own.items() if w & said]
    return hits[0] if len(hits) == 1 else None


def _with_context(analysis, q: Q) -> None:
    """A question about a column carries one clause of what code counted about how
    that column is written (numbers read from text, spellings merged, another type
    among its values), so the owner's answer confirms or corrects it."""
    from . import findings
    ab = q.meta.get("about") or {}
    if not ab.get("col") or q.kind in ("goal", "build") or q.id == CLOSER or q.meta.get("context"):
        return
    said = findings.column_context(analysis, ab.get("table"), ab["col"])
    if not said:
        return
    ask, note = said
    q.meta["context"] = ask
    q.prompt = findings.with_clause(q.prompt, ask)
    if q.fact and q.fact.get("statements") and q.source != "playbook":
        q.fact = dict(q.fact, statements={k: v.rstrip() + " " + note for k, v in q.fact["statements"].items()})


# --------------------------------------------------------------------------
# what a question is about within its column: the values it names
# --------------------------------------------------------------------------
def _ident(about: dict):
    """The values a question or an answer names in its column (its model cells,
    else its values, normalized), or None when it names none."""
    cells = _cells(about)
    if cells:
        return frozenset(("cell", c) for c in cells)
    vals = {norm_key(v) for v in (about or {}).get("values") or []} - {None}
    return frozenset(vals) if vals else None


def _settled_by(answers: dict, unsure: bool = False) -> dict:
    """(table, column, aspect) -> [(the values an answer named or None, a mapping
    answer)], for every answer that settled that aspect of the column (unsure:
    the aspect it was asked about too, even when the owner was not sure)."""
    from .findings import READBACK
    out: dict = {}
    for qid, a in (answers or {}).items():
        if not isinstance(a, dict) or (a.get("not_sure") and not unsure) or qid.startswith(("_", READBACK)) \
                or qid in ("goal", CLOSER):
            continue
        ab = a.get("about") or {}
        if not (ab.get("table") and ab.get("col") and ab.get("aspect")):
            continue
        # a Not sure on a playbook prior settles only other priors on that aspect: never a question that carries
        # its own counted evidence (weak)
        weak = bool(a.get("not_sure")) and not qid.startswith(_DETECTED) and not a.get("fact")
        got = (_ident(ab), a.get("kind") == "mapping", weak)
        for asp in {ab["aspect"]} | ({ab["asked"]} if unsure and ab.get("asked") else set()):
            out.setdefault((ab["table"], ab["col"], asp), []).append(got)
        if a.get("not_sure"):
            continue
        # a confirmed map's values are what the answer settled: every pair it made one is answered
        mapped = {norm_key(v) for d in a.get("rules") or [] if d.get("kind") == "map" and d.get("confirmed")
                  for c in d.get("predicate") or [] for v in c.get("values") or []} - {None}
        if mapped:
            out.setdefault((ab["table"], ab["col"], "meaning"), []).append((frozenset(mapped), True, False))
        # a typed answer that says what to do with a code's rows ('both the V row and the original come out')
        # settles that code's treatment
        treated = _typed_treatment(a)
        if treated:
            out.setdefault((ab["table"], ab["col"], "treatment"), []).append((frozenset(treated), False, False))
    return out


_TREATS = re.compile(r"\b(come|comes|came) out\b|\b(leave|left|drop\w*|exclud\w*|remov\w*|cancel\w*|"
                     r"ignor\w*)\b|\b(do not|don't|does not|doesn't|never) count\b|\bout of (every|all|the) "
                     r"(counts?|totals?)\b", re.I)


def _typed_treatment(a: dict) -> set:
    """The codes of a dossier answer whose typed words say what happens to their
    rows: a sentence that names the code (its '<code> =' words, or 'the V row')
    and says they come out, are dropped, left out or cancelled."""
    codes = a.get("codes") or []
    text = str(a.get("text") or "")
    if not codes or not text.strip():
        return set()
    from . import privacy, rules
    out = set()
    header = (a.get("about") or {}).get("col") or ""
    for s in privacy.said_sentences(text):
        if not _TREATS.search(s):
            continue
        named = {c for c, _a, _b in rules.code_segments(s, codes)}
        try:
            named |= {c for _a, _b, c in rules._code_refs(s, codes, header)}
        except Exception:  # noqa: BLE001
            pass
        out |= {norm_key(c) for c in named}
    return out - {None}


def _is_settled(q: Q, said: dict) -> bool:
    """An answer settles a question about the same aspect of the same column when
    their values overlap, or either names none. A mapping answer settles only the
    values it mapped (a question about other codes of the column stays open), and a
    question about cells of a model's row only an answer about those cells."""
    ab = q.meta.get("about") or {}
    if not ab.get("col"):
        return False
    mine = _ident(ab)
    cells = bool(_cells(ab))
    counted = q.source == "finding" or bool(q.meta.get("confirm"))
    for entry in said.get((ab.get("table"), ab.get("col"), ab.get("aspect")), []):
        vals, mapping, weak = entry[0], entry[1], (entry[2] if len(entry) > 2 else False)
        if weak and counted:
            continue                  # a Not sure on a prior never settles a question with evidence of its own
        if cells:
            if vals is not None and mine & vals:
                return True
            continue
        if mapping:
            if vals is not None and mine is not None and mine <= vals:
                return True
            continue
        if vals is None or mine is None or mine & vals:
            return True
    return False


def _mapped_all(q: Q, said: dict) -> bool:
    """Every value a mapping question names is among the values a confirmed map
    of the same column made one."""
    ab = q.meta.get("about") or {}
    mine = _ident(ab)
    if not ab.get("col") or not mine or _cells(ab):
        return False
    return any(e[1] and e[0] and mine <= e[0] for e in said.get((ab.get("table"), ab["col"], "meaning"), []))


def _fold_same_values(qs: list) -> list:
    """Two detector questions on the same aspect of one column whose values are one
    inside the other are one question: the one naming more values (the larger on a
    tie) is asked, carrying the other's evidence in a clause before its question."""
    drop = set()
    live = [q for q in qs if not q.gated and _ident(q.meta.get("about") or {}) is not None
            and not _cells(q.meta.get("about") or {}) and (q.meta.get("about") or {}).get("col")]
    for q in sorted(live, key=lambda x: (-len(_ident(x.meta["about"])), -(x.value or 0.0), x.id)):
        if id(q) in drop:
            continue
        ab, mine = q.meta["about"], _ident(q.meta["about"])
        for o in live:
            oa = o.meta["about"]
            if o is q or id(o) in drop or (oa.get("table"), oa.get("col"), oa.get("aspect")) \
                    != (ab.get("table"), ab.get("col"), ab.get("aspect")) or not _ident(oa) <= mine:
                continue
            clause = (o.meta.get("merge_clause") or o.meta.get("clause") or "").rstrip(". ")
            if clause and clause not in q.prompt:
                from .findings import with_clause
                q.prompt = with_clause(q.prompt, clause + ".")
            _gain_identity(q, o)
            q.value = max(q.value or 0.0, o.value or 0.0)
            q.meta["stake"] = max(float(q.meta.get("stake") or 0.0), float(o.meta.get("stake") or 0.0))
            q.meta["merged"] = list(q.meta.get("merged") or []) + [o.id]
            drop.add(id(o))
    return [q for q in qs if id(q) not in drop]


_NEW_SINCE = re.compile(r"\b(?:rows only from|first rows on)\s+([A-Z][a-z]{2} \d{1,2}, \d{4})")


def _gain_identity(q: Q, o: Q) -> None:
    """A question that absorbs a value first seen late (a newcomer the file only
    has from a date on) gains the option that says what it is: 'New since <date>
    (type what)', in place of the option with the least behind it (another name
    for it), so the answer can name the newcomer."""
    n = ((o.meta.get("finding") or {}).get("numbers") or {})
    if not o.id.startswith("find_odd_") or not n.get("value") or any(x["id"] == "new_since" for x in q.options):
        return
    got = next((m.group(1) for e in n.get("evidence") or [] for m in [_NEW_SINCE.search(e.get("text", ""))] if m), None)
    if not got or not q.options:
        return
    swap = next((i for i, x in enumerate(q.options) if x["id"] in ("spelled", "same")), len(q.options) - 1)
    label = f"{n['value']} is new since {got} (type what)"
    if len(label) > 60:
        label = f"New since {got} (type what)"
    q.options = [x for i, x in enumerate(q.options) if i != swap] + [
        {"id": "new_since", "label": label, "desc": "Type what it is"}]
    if q.fact is not None:
        q.fact = dict(q.fact, statements=dict(q.fact.get("statements") or {}, new_since=(
            f"{n['value']} in {n.get('col')} is new since {got}, per the owner.")))


def _singular(q: Q) -> None:
    """A count of one in the singular, in every text the owner or a note reads ('1 row', never '1 rows')."""
    from .findings import one_is_one
    q.prompt = one_is_one(q.prompt)
    q.options = [dict(o, label=one_is_one(o.get("label", "")), desc=one_is_one(o.get("desc", ""))) for o in q.options]
    if q.fact:
        q.fact = dict(q.fact, statement=one_is_one(q.fact.get("statement", "")),
                      **({"statements": {k: one_is_one(v) for k, v in q.fact["statements"].items()}}
                         if q.fact.get("statements") else {}))
    if q.meta.get("clause"):
        q.meta["clause"] = one_is_one(q.meta["clause"])


# words that name no topic of their own: a goal and a question sharing only these share nothing
_GENERIC = {"data", "sheet", "sheets", "file", "files", "number", "numbers", "report", "reports", "clean",
            "cleaned", "want", "need", "know", "keep", "kept", "make", "help", "tell", "show", "list", "work",
            "right", "wrong", "each", "every", "other", "these", "those", "this", "that", "they", "them", "their",
            "which", "what", "when", "where", "there", "here", "about", "into", "from", "with", "without",
            "should", "would", "could", "your", "yours", "ours", "mine", "owner", "said", "says", "count",
            "counts", "counted", "total", "totals", "value", "values", "rows", "line", "lines", "thing", "things",
            "month", "months", "year", "years", "week", "weeks", "time", "also", "only", "just", "more", "most",
            "some", "many", "much", "have", "does", "done", "being", "been", "were", "will", "than", "then",
            # what every question of a playbook is about (its own subject), never a topic of one question
            "model", "models", "workbook", "workbooks", "spreadsheet", "spreadsheets", "tab", "tabs"}


def _topic_stems(text: str) -> set:
    """The words of a text that name a topic, as rough stems: 4 letters or more,
    not a slot, not a word every question or goal uses."""
    text = re.sub(r"\{[^}]*\}", " ", str(text or ""))
    return {_stem(w) for w in re.findall(r"[a-z]+", text.lower())
            if len(w) >= 4 and w not in _GENERIC and w not in _STOP}


def _word_stem(w: str) -> str:
    """A whole word's stem, stricter than _stem: 'draws' and 'draw', 'charged' and
    'charge' meet; 'control' and 'contractors' never do."""
    w = w.lower()
    if len(w) > 4 and w.endswith("ies"):
        w = w[:-3] + "y"
    elif len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        w = w[:-1]
    if len(w) > 5 and w.endswith("ing"):
        w = w[:-3]
    elif len(w) > 5 and w.endswith("ed"):
        w = w[:-2]
    if len(w) > 4 and w.endswith("e"):
        w = w[:-1]
    return w


def _topic_words(text: str) -> set:
    """The topic words of a text as whole-word stems (4 letters or more, not a slot,
    not a word every question or goal uses)."""
    text = re.sub(r"\{[^}]*\}", " ", str(text or ""))
    return {_word_stem(w) for w in re.findall(r"[a-z]+", text.lower())
            if len(w) >= 4 and w not in _GENERIC and w not in _STOP}


def goal_clearly_names(text: str, label: str) -> bool:
    """The owner's typed goal holds every topic word of a goal's label ('find savings
    before the renewal' and 'Find savings'). A label with no topic word of its own
    is never named."""
    words = _topic_words(label)
    return bool(words) and bool(str(text or "").strip()) and words <= _topic_words(text)


def _goal_answers(analysis, answers: dict) -> dict:
    """{playbook question id: the option the owner's typed goal names}: the goal's
    words hold every topic word of one option's label, and no other option has
    them ('draws kept out of expenses' names 'Owner draws'). The goal never
    answers the question: it backs it and recommends that option (a
    recommendation, never a pick). Only a question whose own record is the goal
    (its fact is of the goal kind) is settled by the goal note (_goal_retired)."""
    text = str(((answers or {}).get("goal") or {}).get("text") or "")
    if not text.strip():
        return {}
    said = _topic_words(text)
    out = {}
    for q in (analysis.playbook or {}).get("questions", []):
        if q.get("id") in (answers or {}) or q.get("kind") in ("goal", "build"):
            continue
        opts = q.get("options") or []
        for o in opts:
            words = _topic_words(o.get("label", ""))
            # the option's own words, none of them shared with another option ('Cash' beside 'Cash, adjusted')
            others = set().union(*[_topic_words(x.get("label", "")) for x in opts if x is not o]) if len(opts) > 1 \
                else set()
            if words and words <= said and not words & others:
                out[q["id"]] = o["id"]
                break
    return out


def _goal_retired(analysis, answers: dict) -> set:
    """Playbook questions the typed goal settles: only those whose own fact is the
    owner's goal (who relies on a model), which the goal note already records.
    Every other question the goal names stays asked (_goal_answers)."""
    hits = _goal_answers(analysis, answers)
    return {q["id"] for q in (analysis.playbook or {}).get("questions", [])
            if q.get("id") in hits and (q.get("fact") or {}).get("kind") == "goal"}


def _goal_names(goal_text: str, q: dict) -> bool:
    """The owner's typed goal names what a playbook question is about: a topic
    word shared with the question's header or its fact ('keep transfers out
    of costs' and 'leave these out of income and cost totals')."""
    if not goal_text.strip():
        return False
    return bool(_topic_stems(goal_text) & (_topic_stems(q.get("header", ""))
                                           | _topic_stems((q.get("fact") or {}).get("statement", ""))))


# answers from questions a detector asked (findings, dossiers, joins, aliases, follow-ups)
_DETECTED = ("find_", "codes_", "join_", "alias_", "follow_", "grow_")
_CELL = re.compile(r"!\$?[A-Z]{1,3}\$?\d+\b")


def _cells(about: dict) -> set:
    """The model cells a question names ('Sales, Mar 2030 (Model!F9)' -> {'Model!F9'}), or none."""
    return {m.group(0).lstrip("!").replace("$", "") for v in (about or {}).get("values") or []
            for m in _CELL.finditer(str(v))}


def _answered_cells(answers: dict) -> dict:
    """(table, column, aspect) -> the model cells the answers about it named."""
    out: dict = {}
    for qid, a in (answers or {}).items():
        ab = (a.get("about") or {}) if isinstance(a, dict) else {}
        if ab.get("col"):
            for asp in {ab.get("aspect"), ab.get("asked")} - {None}:
                out.setdefault((ab.get("table"), ab["col"], asp), set()).update(_cells(ab))
    return out


def covered_keys(answers: dict, unsure: bool = False) -> set:
    """(table, column, aspect) the owner's answers settled: what a column means,
    how it is treated, its unit, its grain, its scope or what its blanks mean. A
    Not sure settles nothing, nor does an answer about another aspect (unsure:
    count what each question was asked about too, so it is not asked again under
    another id); the goal, the readbacks and the closing question are about no
    one column."""
    from .findings import READBACK
    out = set()
    for qid, a in (answers or {}).items():
        if not isinstance(a, dict) or (a.get("not_sure") and not unsure) or qid.startswith(("_", READBACK)) \
                or qid in ("goal", CLOSER):
            continue
        ab = a.get("about") or {}
        if ab.get("table") and ab.get("col") and ab.get("aspect"):
            out.add((ab["table"], ab["col"], ab["aspect"]))
            if unsure and ab.get("asked"):
                out.add((ab["table"], ab["col"], ab["asked"]))
    return out


def _governs_money(analysis, env: Env, q: dict, about: dict) -> bool:
    """A unit or basis question whose answer decides how every value of a money
    column is read (the prices a rebate or allowance is paid on), once the role
    it turns on is bound in the session."""
    if q.get("kind") != "unit" and "basis" not in str(q.get("id", "")):
        return False
    turns = _EXTRA_TOUCH.get(q.get("id"), [])
    if not about.get("col") or not turns or not any(env.holds(p) for p in turns):
        return False
    from .rules import _is_money
    t = next((t for t in analysis.tables if t.tid == about.get("table")), None)
    return t is not None and _is_money(analysis, t, about["col"])


# --------------------------------------------------------------------------
# one-tap confirms of what code counted, for room left over
# --------------------------------------------------------------------------
CONFIRM = "confirm_grain"
UNIFORM = "confirm_same_price"


def confirm_questions(analysis, answers: dict) -> list:
    """Questions that put what code counted to the owner as a one-tap confirm:
    what a row is on the book's tables (their keys, entries that net to zero,
    a snapshot, the dates' cycle and span) and a price the same at every place
    in each period. They fill room the findings leave; a confirm whose tables
    carry two counted readings that disagree enters a round."""
    out = []
    g = _grain_confirm(analysis, answers)
    if g is not None:
        out.append(g)
    u = _uniform_confirm(analysis, answers)
    if u is not None:
        out.append(u)
    return out


def _span_of(analysis, t) -> tuple:
    """(first date, last date) of a table's own date column, as words, or ('', '')."""
    from .analyze import _day_words
    aj = analysis._axis_j(t)
    ds = [r[aj].toordinal() for r in t.rows if aj is not None and aj < len(r) and hasattr(r[aj], "year")]
    return (_day_words(min(ds)), _day_words(max(ds))) if ds else ("", "")


def _grain_line(analysis, t, main: bool = True) -> tuple:
    """(what code counted about what a row of t is, in one clause, with how its rows
    are dated; the key sets it names) or ('', []) when nothing counted settles it.
    Every tab says how it is dated: a line each time something happens (dates on
    most days of its span and on any weekday), or every N days on one weekday. A
    list's dates (a roster's hire dates) are things about its rows, never what it
    covers: a tab other than the main one gives its span only when its dates are
    one of those two. A tab whose title names the date it stands at says that
    date. A key or a cycle that pairs or off-cycle rows break is named with them."""
    from .analyze import _day_words, _join_words
    cyc = next((c for (tid, _h), c in (getattr(analysis, "cadence", None) or {}).items() if tid == t.tid
                and c.get("step") != "month" and int(c.get("step") or 0) >= 7), None)
    event = _event_log(analysis, t)
    lo, hi = _span_of(analysis, t) if main or cyc or event else ("", "")
    dated = f", dated {lo} to {hi}" if lo and lo != hi else f", dated {lo}" if lo else ""
    asof = (getattr(analysis, "title_period", None) or {}).get(t.tid) or {}
    if asof.get("kind") == "as_of":
        dated = f", as of {_day_words(asof['end'])}"
    bal = (getattr(analysis, "balanced", None) or {}).get(t.tid)
    snap = (getattr(analysis, "snapshots", None) or {}).get(t.tid)
    keys = list(analysis.keys.get(t.tid) or [])
    from .findings import _NOTES
    if any(_NOTES.search(str(k)) for k in keys):
        keys = []                 # free text unique on every row says nothing about what a row is
    extra = (getattr(analysis, "key_extra", None) or {}).get(t.tid) or 0
    sets = []
    sides = _one_side_rows(analysis, t)
    if bal and bal.get("entries") and bal.get("netted") == bal["entries"]:
        line = (f"{t.sheet} holds {t.n_rows:,} lines in {bal['entries']:,} entries that each net to zero by "
                f"{bal['col']}{dated}")
        sets.append(frozenset([bal["col"]]))
    elif snap:
        named = _site_key(analysis, t, list(snap["keys"]))
        line = (f"{t.sheet} holds one row per {_join_words(named)} on each {snap['col']}: {snap['dates']:,} "
                f"dates{dated.replace(', dated', ' from', 1) if dated else ''}")
        sets.append(frozenset(snap["keys"] + [snap["col"]]))
    elif keys and extra <= 0.01 * max(1, t.n_rows):
        line = f"{t.sheet} holds {t.n_rows:,} rows, one per {_join_words(_site_key(analysis, t, keys))}{dated}"
    elif sides:
        # a table with no key whose rows each fill one of two money columns: a charge or a payment
        a_, b_, opening = sides
        line = f"{t.sheet} holds {t.n_rows:,} rows, one per {a_} or per {b_}{dated}"
        if opening:
            line += f", including opening balances carried in ({opening})"
    else:
        return "", []
    if keys:
        sets.append(frozenset(keys))
        if _disagree(sets):
            # two counted readings name different columns: the owner sees both
            line += f", and each row has its own {_join_words(keys)}"
        line += _key_exceptions(analysis, t, keys)
    if cyc:
        import datetime as dt
        day = dt.date.fromordinal(cyc["on"][0]).strftime("%A")
        line += f"; its {cyc['col']} dates fall every {cyc['step']} days on a {day}"
        oc = cyc.get("off_code")
        if oc:
            line += f", except {oc['rows']:,} {oc['value']} rows, mostly on a {oc['weekday']}"
        elif cyc.get("off"):
            line += f", except {len(cyc['off']):,} dates off that cycle"
    elif event:
        line += "; a line each time it happens, dated that day"
    return line, sets


def _event_log(analysis, t) -> bool:
    """A tab whose rows are dated each time something happens: its dates fall on
    half or more of the days of their span, over a month or more, and on every
    weekday but at most two."""
    aj = analysis._axis_j(t)
    if aj is None or t.n_rows < 20:
        return False
    import datetime as dt
    ds = {r[aj].toordinal() for r in t.rows if aj < len(r) and hasattr(r[aj], "year")}
    if len(ds) < 20:
        return False
    span = max(ds) - min(ds) + 1
    weekdays = {dt.date.fromordinal(d).weekday() for d in ds}
    return span >= 28 and len(ds) >= 0.5 * span and len(weekdays) >= 5


def _one_side_rows(analysis, t):
    """(A, B, opening words) when a tab with no key has a pair of money columns
    and every row fills exactly one of them (the other blank or 0): a row is one A
    or one B. The opening words name a code whose rows all sit on the first date
    on one side (balances carried in), else ''. None otherwise."""
    from .profile import _is_num
    pair = list(((analysis.detection or {}).get("pairs") or {}).get(t.tid) or [])
    if len(pair) != 2 or not all(h in t.headers for h in pair) or t.n_rows < 20:
        return None
    ja, jb = (t.headers.index(h) for h in pair)

    def filled(r, j):
        v = r[j] if j < len(r) else None
        return _is_num(v) and v != 0
    side = []
    for r in t.rows:
        a_, b_ = filled(r, ja), filled(r, jb)
        if a_ == b_:
            return None
        side.append(0 if a_ else 1)
    opening = ""
    aj = analysis._axis_j(t)
    if aj is not None:
        first = min((r[aj].toordinal() for r in t.rows if aj < len(r) and hasattr(r[aj], "year")), default=None)
        for c in analysis.cols.get(t.tid, []):
            if c.type != "text" or c.distinct_capped or not (2 <= c.distinct <= 30) or c.j in (ja, jb):
                continue
            rows_of: dict = {}
            for i, r in enumerate(t.rows):
                k = norm_key(r[c.j] if c.j < len(r) else None)
                if k is not None:
                    rows_of.setdefault(k, []).append(i)
            for k, rs in rows_of.items():
                if len(rs) >= 2 and len({side[i] for i in rs}) == 1 and all(
                        aj < len(t.rows[i]) and hasattr(t.rows[i][aj], "year") and t.rows[i][aj].toordinal() == first
                        for i in rs) and len(rs) < 0.2 * t.n_rows:
                    opening = f"{c.header} {str(t.rows[rs[0]][c.j]).strip()}, {len(rs):,} rows on the first date"
                    break
            if opening:
                break
    return pair[0], pair[1], opening


def _site_key(analysis, t, keys: list) -> list:
    """The key columns of a tab with any two that name one thing, one to one on
    every row (a site and the person who counts it), said as the one that names a
    place (else both)."""
    from .findings import _SITE
    if len(keys) < 2:
        return keys
    out = list(keys)
    for i, a_ in enumerate(keys):
        for b_ in keys[i + 1:]:
            if a_ not in out or b_ not in out or a_ not in t.headers or b_ not in t.headers:
                continue
            ja, jb = t.headers.index(a_), t.headers.index(b_)
            fw, bw = {}, {}
            ok = True
            for r in t.rows:
                x, y = norm_key(r[ja] if ja < len(r) else None), norm_key(r[jb] if jb < len(r) else None)
                if fw.setdefault(x, y) != y or bw.setdefault(y, x) != x:
                    ok = False
                    break
            if not ok:
                continue
            site = [h for h in (a_, b_) if _SITE.search(str(h))]
            if len(site) == 1:
                out.remove(b_ if site[0] == a_ else a_)
    return out


def _key_exceptions(analysis, t, keys: list) -> str:
    """', except ...' for rows a counted finding says break a named key: twin rows
    that share their pair's number, copies there twice under two numberings."""
    got = []
    for i in getattr(analysis, "insights", None) or []:
        n = i.get("numbers") or {}
        rec = i.get("recipe", "")
        if n.get("table") != t.tid or n.get("col") not in keys:
            continue
        if rec.startswith("pairs:"):
            vals = n.get("values") or ["", ""]
            got.append(f"{n.get('pairs', 0):,} {vals[1]} rows that share their pair's {n['col']}")
        elif rec.startswith("copies:"):
            got.append(f"{n.get('rows', 0):,} rows there twice under two numberings")
    return (", except " + " and ".join(got)) if got else ""


def _disagree(sets: list) -> bool:
    """Two counted readings of what a row is disagree when neither names columns
    the other holds: a count number beside site, item and date does; a line key
    of entry and account beside entries by entry does not."""
    return any(not (a <= b or b <= a) for i, a in enumerate(sets) for b in sets[i + 1:])


def _grain_confirm(analysis, answers: dict):
    if CONFIRM in (answers or {}):
        return None
    # data tables only: never a grid of periods, a calculated tab or a label | value list of inputs
    tabs = [t for t in analysis.tables if not t.wide and not analysis.is_derived(t.tid) and t.n_rows >= 20
            and not analysis.row_labels(t)]
    main = analysis.main_table
    tabs.sort(key=lambda t: (t is not main, -t.n_rows))
    lines, conflict, shown = [], False, []
    for t in tabs:
        line, sets = _grain_line(analysis, t, main=t is main)
        if not line:
            continue
        lines.append(line)
        shown.append(t)
        conflict = conflict or _disagree(sets)
        if len(lines) == 2:
            break
    if not lines:
        return None
    said = "; ".join(lines)
    covers = [q["id"] for q in (analysis.playbook or {}).get("questions", [])
              if q.get("kind") in ("grain", "coverage") and not _gated(q.get("ask_if"))]
    q = Q(CONFIRM, "Rows", f"I read {'these tabs' if len(lines) > 1 else 'this tab'} this way: {said}. Is that right "
                           "as read?",
          [{"id": "right", "label": "Right as read", "desc": "Each row is what it says"},
           {"id": "wrong", "label": "Some of it is wrong (type which)", "desc": "Type what is not right"}],
          recommend="right", recommend_basis="Counted from the rows.",
          why="What a row is, and which dates the rows cover, decides every count and total.",
          kind="grain", priority=3, source="builtin",
          fact={"kind": "grain", "class": "data", "depends": [],
                "statement": f"{said}; per the owner: {{answer_labels}}.",
                "statements": {"right": f"{said}, right as read, per the owner."}},
          meta={"about": {"table": shown[0].tid, "col": "", "aspect": "grain"}, "covers": covers,
                "leftover": True, "confirm": True, "tables": [t.tid for t in shown],
                "sheets": sorted({t.sheet for t in analysis.tables}), "clause": lines[0]})
    if conflict:
        # two counted readings of one tab name different columns: that is worth a round of its own
        q.meta.update(backed=True, stake=0.0)
        q.value = 6.0
    else:
        q.value = 6.5
    return q


def _uniform_confirm(analysis, answers: dict):
    """A price the same at every place in each period, that moves over time: is it
    because they buy from the same suppliers at the same price?"""
    if UNIFORM in (answers or {}):
        return None
    ins = next((i for i in analysis.insights if i.get("recipe", "").startswith("price_trend:")), None)
    if ins is None:
        return None
    rid = ins["recipe"].split(":")[1]
    r = analysis.detection["roles"].get(rid) or {}
    if not r.get("table"):
        return None
    said = ins["statement"].rstrip(". ")
    q = Q(UNIFORM, "Same price", f"{said}. Is that because they buy from the same suppliers at the same price?",
          [{"id": "right", "label": "Right as read", "desc": "The same suppliers at the same price"},
           {"id": "wrong", "label": "Some of it is wrong (type which)", "desc": "Type what is not right"}],
          recommend="right", recommend_basis="Counted from the rows.",
          why="Whether prices can explain differences between places depends on it.",
          kind="grain", priority=3, source="builtin",
          fact={"kind": "definition", "class": "data", "depends": [],
                "statement": f"{said}; per the owner: {{answer_labels}}.",
                "statements": {"right": f"{said}, because they buy from the same suppliers at the same price, per "
                                        "the owner."}},
          meta={"about": {"table": r["table"], "col": r["header"], "aspect": "uniform"}, "leftover": True,
                "confirm": True, "sheets": sorted({t.sheet for t in analysis.tables}), "clause": said})
    q.value = 6.2
    return q


def _grain_typed(analysis, tid) -> bool:
    """Code settled what a row is: the table has a key, it is a snapshot panel or
    a table of entries that net to zero, or it is a grid or a calculated tab."""
    t = next((t for t in analysis.tables if t.tid == tid), None)
    return t is None or t.wide or analysis.is_derived(t.tid) or bool(analysis.keys.get(t.tid)) \
        or tid in (getattr(analysis, "snapshots", None) or {}) or tid in (getattr(analysis, "balanced", None) or {})


def _driver_evidence(analysis, q: dict, about: dict) -> dict | None:
    """For a rate or unit question about an input of a formula model: the input's
    cell, the formula it drives in words, and the share of the model's formula
    cells downstream of it. None when no formula reads it."""
    if q.get("kind") != "unit" or not about.get("col"):
        return None
    t = next((t for t in analysis.tables if t.tid == about.get("table")), None)
    if t is None:
        return None
    fa = analysis.formulas.get(analysis.file_of[t.tid]) or {}
    inp = next((x for x in fa.get("inputs") or [] if x["sheet"] == t.sheet and x["label"] == about["col"]
                and x["read"]), None)
    if inp is None:
        return None
    cell = f"{inp['sheet']}!{inp['cell']}"
    drivers = [i for i in analysis.insights if i.get("recipe", "").startswith("model:driver:")
               and cell in (i["numbers"].get("inputs") or [])]
    reach = analysis.reach_share([cell], analysis.file_of[t.tid])
    if not drivers or not reach:
        return None
    best = max(drivers, key=lambda i: i["numbers"]["cells"])
    out = {"statement": best["statement"], "reach": reach, "cell": cell}
    sib = _sibling_flow(analysis, analysis.file_of[t.tid], cell)
    if sib:
        out.update(sibling=sib, statement=f"{sib['flow_statement']} {sib['other_statement']} {sib['net']}")
    return out


def _sibling_flow(analysis, path: str, cell: str) -> dict | None:
    """A rate whose driver multiplies a stock row that another input's driver
    multiplies in another flow row, while a subtotal adds the one flow and
    subtracts the other (new business grows the stock and churn takes it away):
    {flow, other, net, flow_statement, other_statement}. None otherwise."""
    from . import formulas as fm
    fa = analysis.formulas.get(path) or {}
    b = next((b for b in analysis.books if b.path == path), None)
    if b is None:
        return None
    sheets = {s.name: s for s in b.data_sheets()}
    drivers = fa.get("drivers") or []

    def rows_of(f, sheet):
        return {ref[1] for tok in fm.tokens(f) for ref in [fm.ref_of(tok, sheet)] if ref and ref[0] == sheet}

    def signed(f, sheet):
        """{row: +1 or -1} for a plain sum of rows of the same tab."""
        out, sign = {}, 1
        for tok in fm.tokens(f):
            ref = fm.ref_of(tok, sheet)
            if ref is not None and ref[0] == sheet:
                out.setdefault(ref[1], sign)
                sign = 1
            elif tok == ("op", "-"):
                sign = -1
            elif tok[0] == "op" and tok[1] in "+(":
                sign = 1 if tok[1] == "+" else sign
            elif tok[0] in ("func",):
                return {}
        return out
    for d1 in [d for d in drivers if cell in d["inputs"]]:
        stock1 = rows_of(d1["formula"], d1["sheet"])
        for d2 in drivers:
            if d2 is d1 or d2["sheet"] != d1["sheet"] or cell in d2["inputs"] or d2["r"] == d1["r"]:
                continue
            if not stock1 & rows_of(d2["formula"], d2["sheet"]):
                continue
            s = sheets.get(d1["sheet"])
            if s is None:
                continue
            for (r, _c), f in s.formulas.items():
                if r in (d1["r"], d2["r"]):
                    continue
                sg = signed(f, d1["sheet"])
                if sg.get(d1["r"]) == 1 and sg.get(d2["r"]) == -1:
                    total = fm._row_label(s, r) or f"row {r + 1}"
                    ins = {i["recipe"]: i["statement"] for i in analysis.insights
                           if i.get("recipe", "").startswith("model:driver:")}
                    common = sorted(stock1 & rows_of(d2["formula"], d2["sheet"]))
                    stock = next((fm._row_label(s, x) for x in common if fm._row_label(s, x)), "")
                    return {"flow": d1["row_label"], "other": d2["row_label"], "total": total, "stock": stock,
                            "flow_statement": ins.get(f"model:driver:{d1['sheet']}:{d1['row_label']}", ""),
                            "other_statement": ins.get(f"model:driver:{d2['sheet']}:{d2['row_label']}", ""),
                            "net": f"{total} adds {d1['row_label']} and subtracts {d2['row_label']}."}
    return None


def _insight_evidence(analysis, q: dict) -> dict | None:
    """The insight a playbook question names in 'backed_by' ({insight: recipe
    prefix, recommend: option}): its counted statement becomes the evidence and
    its option the recommendation, a one-tap confirm."""
    by = q.get("backed_by") or {}
    if not by.get("insight") or by.get("recommend") not in {o["id"] for o in q.get("options", [])}:
        return None
    return next((i for i in analysis.insights if i.get("recipe", "").startswith(by["insight"])), None)


def _backed_fact(q: dict, rec: str, ins: dict) -> dict | None:
    """The playbook fact, with the counted switch the prompt showed kept in the
    note of the recommended pick: '..., after Aug 2026 on P&L and Revenue.'"""
    fact = q.get("fact")
    n = ins.get("numbers") or {}
    if not fact or not fact.get("statement") or not n.get("last_actual"):
        return fact
    from .analyze import _join_words, _month
    lab = next(o["label"] for o in q["options"] if o["id"] == rec)
    said = fact["statement"].replace("{answer_labels}", _in_sentence(lab)).rstrip(".")
    where = _join_words(n.get("sheets") or [n.get("sheet")])
    return dict(fact, statements=dict(fact.get("statements") or {},
                                      **{rec: f"{said}, after {_month(n['last_actual'])} on {where}."}))


# where the typed actuals come from, folded into the pick that confirms a typed-to-formula switch
_SOURCES = [{"id": "typed_books", "label": "Typed from the books at each close, then formulas"},
            {"id": "typed_hand", "label": "Typed by hand from another source"},
            {"id": "other", "label": "Somewhere else (type it)", "desc": "Type where they come from"}]


def _with_source(q: dict, ins: dict):
    """(options, fact) for a question backed by a typed-to-formula switch
    (formula:actuals_boundary): its options say where the typed numbers come
    from, each note keeping the switch month and tabs the prompt showed. None for
    any other backing."""
    n = ins.get("numbers") or {}
    fact = q.get("fact")
    if not str(ins.get("recipe", "")).startswith("formula:actuals_boundary") or not n.get("last_actual") \
            or not fact or not fact.get("statement"):
        return None
    from .analyze import _join_words, _month
    where = _join_words(n.get("sheets") or [n.get("sheet")])
    when = _month(n["last_actual"])
    opts = [dict(o, desc=o.get("desc") or f"Typed through {when}, formulas after") for o in _SOURCES]
    statements = {}
    for o in opts[:2]:
        said = fact["statement"].replace("{answer_labels}", _in_sentence(o["label"])).rstrip(".")
        statements[o["id"]] = f"{said}, after {when} on {where}."
    return opts, dict(fact, statements=statements)


def _playbook_about(analysis, q: dict) -> dict:
    """{'about': {table, col, aspect}} for a playbook question: the column its
    prompt or its conditions name, or the main table when it names none."""
    roles = analysis.detection["roles"]
    named = re.findall(r"\{(?:role|values|count|rows|sum):([a-z0-9_]+)\}", q.get("prompt", ""))
    named += [p.partition(":")[2] for p in q.get("ask_if", []) if not _gated([p])]
    aspect = _ASPECT.get(q.get("kind", "definition"), "meaning")
    r = next((roles[x] for x in named if x in roles and roles[x].get("table")), None)
    if r:
        return {"about": {"table": r["table"], "col": r["header"], "aspect": aspect}}
    t = analysis.main_table
    return {"about": {"table": t.tid, "col": "", "aspect": aspect}} if t is not None else {}


_ASPECT = {"grain": "grain", "unit": "unit", "coverage": "scope", "exclusion": "treatment", "rule": "treatment",
           "definition": "meaning", "mapping": "meaning", "history": "history"}


# playbook questions that settle the same thing as a guess in the playbook
_EXTRA_TOUCH = {"price_basis": ["has_role:rebate", "has_role:allowance"], "actuals_end": ["has_role:period_type"],
                "sign_rule": ["has_negatives:cost", "has_negatives:amount"], "rebate_programs": ["has_role:rebate"]}


def covered_ids(analysis, answers: dict, found: list | None = None) -> set:
    """Playbook questions a question about this file already covers (a typed
    number found in a formula row covers 'are there overrides?'), or that a file
    already open answers (its contract terms). An assumption code stated in the
    readout ('months through June look like actuals') is not a confirmation and
    covers nothing."""
    out = set()
    for a in answers.values():
        if isinstance(a, dict) and not a.get("not_sure"):
            out.update(a.get("covers") or [])
    for q in found or []:
        out.update(q.meta.get("covers") or [])
    # the owner's typed goal settles a question whose own record is the goal ('for the board'); any other
    # question it names stays asked, backed by the goal (_goal_answers)
    out |= _goal_retired(analysis, answers)
    # the terms are counted facts already, key by key: read them, don't ask. Only when they are keyed by the partner
    # the question is about (a role its fact depends on), never by an item a dated price list names
    ct = next((q for q in (analysis.playbook or {}).get("questions", []) if q["id"] == "contract_terms"), None)
    keyed = {x.get("role") for x in getattr(analysis, "terms", None) or []} - {None, ""}
    if ct and keyed & set((ct.get("fact") or {}).get("depends") or []):
        out.add("contract_terms")
    # what a row is, stated by code with its counts (a snapshot panel, entries that net to zero, a grid of
    # periods): a playbook question that kind of fact settles (its settled_by) is not asked about that table
    grain = {(g["numbers"]["table"], g["recipe"].split(":")[1]) for g in getattr(analysis, "grain_facts", None) or []}
    # a panel whose stock columns are read on one date (snapshot_stock) settles 'a count on one date, or a log of
    # moves?'; a panel with no stock column found says what a row is, not that its numbers are counts
    grain |= {(g["numbers"]["table"], "snapshot_stock") for g in getattr(analysis, "grain_facts", None) or []
              if g["recipe"].startswith("grain:snapshot:") and g["numbers"].get("stock")}
    for q in (analysis.playbook or {}).get("questions", []) if grain else []:
        tid = (_playbook_about(analysis, q).get("about") or {}).get("table")
        if any((tid, k) in grain for k in q.get("settled_by") or []):
            out.add(q["id"])
    # what a model's formulas and titles settle for the whole file (the sign subtotals use, the money scale the
    # titles name), stated by code with its evidence; asked only when that evidence conflicts
    settles = {k for i in getattr(analysis, "insights", None) or [] for k in i.get("settles") or []}
    for q in (analysis.playbook or {}).get("questions", []) if settles else []:
        if settles & set(q.get("settled_by") or []):
            out.add(q["id"])
    return out


def settled(analysis, answers: dict) -> set:
    """Predicates an answer has settled: a playbook guess on the same thing
    ('negatives may be credits') is retired once the owner has said what it is."""
    qdefs = {q["id"]: q for q in (analysis.playbook or {}).get("questions", [])}
    out = set()
    for qid, a in answers.items():
        if not isinstance(a, dict) or a.get("not_sure") or qid.startswith("_"):
            continue
        out.update(a.get("touches") or [])
        out.update(p for p in (qdefs.get(qid) or {}).get("ask_if", []) if not _gated([p]))
        out.update(_EXTRA_TOUCH.get(qid, []))
    return out


def _bridge_questions(analysis, answers: dict) -> list:
    out = []
    n = 0
    for j in analysis.joins:
        if j["band"] != "ask":
            continue
        # 'the same ID?' only of two ID columns: two columns of people's names that share values (a branch
        # manager and an employee) are never an ID pairing
        ends = [analysis.col(j["from_table"], j["from_col"]), analysis.col(j["to_table"], j["to_col"])]
        if any(c is None or c.semantic != "identifier" for c in ends):
            continue
        qid = "join_" + re.sub(r"[^a-z0-9]+", "_",
                               f"{j['from_table']}_{j['from_col']}_{j['to_table']}".lower())[:40]
        if qid in answers:
            continue
        ft = analysis.table(j["from_table"])
        tt = analysis.table(j["to_table"])
        where_f = f"{ft.sheet}" if not j["cross_file"] else _file_label(analysis, j["from_table"])
        where_t = f"{tt.sheet}" if not j["cross_file"] else _file_label(analysis, j["to_table"])
        prompt = (f"{j['from_col']} ({where_f}) and {j['to_col']} ({where_t}) share "
                  f"{j['rows_matched'] * 100:.0f}% of values. Are they the same ID?")
        out.append(Q(qid, "Same IDs?", prompt,
                     [{"id": "same", "label": "Same ID, some missing", "desc": "The same ID, with some missing"},
                      {"id": "different", "label": "Different IDs", "desc": "Different IDs that share some values"}],
                     why="Connecting the wrong columns makes every joined number wrong.",
                     kind="mapping", priority=1, source="builtin",
                     fact={"kind": "mapping", "class": "data", "depends": [],
                           "statement": f"{j['from_col']} ({where_f}) and {j['to_col']} ({where_t}) "
                                        "are {answer_labels}."},
                     meta={"join": j, "about": {"table": j["from_table"], "col": j["from_col"], "aspect": "meaning"}}))
        # a wrong link moves every joined number: the rows it would join are the stake
        out[-1].meta["stake"] = float(j["rows_matched"])
        out[-1].value = worth(j["rows_matched"])
        n += 1
        if n >= 2:
            break
    return out


def _file_label(analysis, tid: str) -> str:
    import os
    return os.path.basename(analysis.file_of[tid])


# standard unit-of-measure codes: inside a unit column they are units, never codes to explain
_UNIT_CODES = {"CS", "CA", "EA", "LB", "LBS", "OZ", "KG", "G", "GAL", "L", "ML", "BX", "CT", "PK", "DZ", "BG",
               "PC", "PCS", "EACH", "CASE", "BOX", "BAG", "HR", "HRS", "FT", "IN", "M", "YD", "UNIT", "UNITS"}
_UNIT_ROLES = {"uom", "pack"}
_UNIT_HEADER = re.compile(r"^\s*(uom|u/?m|units?|unit of measure|measure|pack|pack size|case pack|"
                          r"(count|order|sell|selling|price|pricing|billing|inventory|counting)\s+units?)\s*$", re.I)


def is_unit_col(header: str, rid: str | None = None) -> bool:
    """A unit-of-measure column, by its role or its header. Only there do CS, EA
    or LB read as units; in a Channel or Status column 'CS' is a code to explain."""
    return (rid or "") in _UNIT_ROLES or bool(_UNIT_HEADER.match(str(header or "")))


def alias_pairs(analysis) -> list:
    """Category values that look like two names for one thing ('Web' and
    'Website', 'Store A' and 'Stor A'), on every data table: only
    the owner knows. Two values that differ by more than a spelling slip (another
    word in place of a word, as in 'North Branch' and 'South Branch', or another
    number) are two things and never asked."""
    import difflib

    from .findings import _NOTES
    out = []
    for t in analysis.tables:
        if t.wide or analysis.is_derived(t.tid):
            continue
        for c in analysis.cols[t.tid]:
            if c.semantic != "dimension" or c.type != "text" or c.sensitive or not (2 <= c.distinct <= 60) \
                    or _NOTES.search(str(c.header)):
                continue          # a memo or a description is free text, not names of things
            vals = [str(k) for k, _ in c.top[:60] if isinstance(k, str)]
            counts = {str(k): n for k, n in c.top[:60]}
            for i in range(len(vals)):
                for j in range(i + 1, len(vals)):
                    a, b = vals[i].strip(), vals[j].strip()
                    la, lb = a.lower(), b.lower()
                    if len(la) < 3 or len(lb) < 3 or la == lb or not _spelling_slip(la, lb):
                        continue
                    prefix = (lb.startswith(la) or la.startswith(lb)) and abs(len(la) - len(lb)) <= 8
                    close = difflib.SequenceMatcher(None, la, lb).ratio() >= 0.88
                    if prefix or close:
                        out.append({"table": t.tid, "header": c.header, "a": a, "b": b,
                                    "rows": counts.get(vals[i], 0) + counts.get(vals[j], 0)})
    out.sort(key=lambda x: -x["rows"])
    return out


def _spelling_slip(a: str, b: str) -> bool:
    """Two spellings differ only by a slip: once their shared start and end are
    set aside, what is left is nothing on one side (a word or letters added), the
    same letters in another order, or one letter for another. Anything else (two
    different words, even one-letter words as in 'Depot A' and 'Depot B', or two
    different numbers) names two things."""
    i = 0
    while i < min(len(a), len(b)) and a[i] == b[i]:
        i += 1
    k = 0
    while k < min(len(a), len(b)) - i and a[-1 - k] == b[-1 - k]:
        k += 1
    ra, rb = a[i:len(a) - k], b[i:len(b) - k]
    if re.search(r"\d", ra + rb):
        return False

    def word(s, r):          # the remainder stands alone in s: no letter just before or just after it
        return (i == 0 or not s[i - 1].isalpha()) and (i + len(r) == len(s) or not s[i + len(r)].isalpha())
    if ra and rb and word(a, ra) and word(b, rb):
        return False
    return not ra or not rb or sorted(ra) == sorted(rb) or (len(ra) == 1 and len(rb) == 1)


def _alias_questions(analysis, answers: dict, said: dict | None = None) -> list:
    """One 'same thing?' question per column, about its largest pair of spellings
    that no answer has settled and no handoff already pairs; a pair the owner
    answered, or a column's settled pair, lets the next pair be asked."""
    out = []
    said = said if said is not None else _settled_by(answers, unsure=True)
    asked = set()
    # a spelling already asked about (answered or not sure) is not asked again in another pair
    before = {((a.get("about") or {}).get("table"), (a.get("about") or {}).get("col"), norm_key(v))
              for k, a in (answers or {}).items() if k.startswith("alias_") and isinstance(a, dict)
              for v in (a.get("about") or {}).get("values") or []}
    for p in alias_pairs(analysis):
        qid = "alias_" + re.sub(r"[^a-z0-9]+", "_", f"{p['header']}_{p['a']}_{p['b']}".lower())[:40]
        if qid in answers or (p["table"], p["header"]) in asked \
                or {(p["table"], p["header"], norm_key(p["a"])), (p["table"], p["header"], norm_key(p["b"]))} & before:
            continue
        ho = analysis.handoffs.get((p["table"], p["header"])) or {}
        paired = {norm_key(v) for x in ho.get("pairs") or [] for v in (x["old"], x["new"])}
        if {norm_key(p["a"]), norm_key(p["b"])} <= paired:
            continue          # the handoff already asks whether these two are one
        probe = Q(qid, "", "", [], meta={"about": {"table": p["table"], "col": p["header"],
                                                   "values": [p["a"], p["b"]], "aspect": "meaning"}})
        if _is_settled(probe, said):
            continue
        asked.add((p["table"], p["header"]))
        out.append(Q(qid, "Same thing?", f"Are \"{_short(p['a'])}\" and \"{_short(p['b'])}\" in {p['header']} the "
                                         "same thing?",
                     [{"id": "same", "label": "Same thing", "desc": "Count them together"},
                      {"id": "different", "label": "Different things", "desc": "Keep them apart"}],
                     why="Two names for one thing split every count and total in two.",
                     kind="mapping", priority=1, source="builtin",
                     fact={"kind": "mapping", "class": "data", "depends": [],
                           "statement": f"In {p['header']}, \"{_short(p['a'])}\" and \"{_short(p['b'])}\": "
                                        "{answer_labels}.",
                           "statements": {
                               "same": f"In {p['header']}, \"{_short(p['a'])}\" and \"{_short(p['b'])}\" are the "
                                       "same thing and count together.",
                               "different": f"In {p['header']}, \"{_short(p['a'])}\" and \"{_short(p['b'])}\" are "
                                            "different things."}},
                     meta={"alias": p, "about": {"table": p["table"], "col": p["header"],
                                                 "values": [p["a"], p["b"]], "aspect": "meaning"},
                           # folded into the column's own question: the pair, said as what it looks like
                           "merge_clause": f"\"{_short(p['a'])}\" and \"{_short(p['b'])}\" look like one name "
                                           "written two ways"}))
        # two names for one thing split the rows they are on
        t = analysis.table(p["table"])
        out[-1].meta["stake"] = p["rows"] / t.n_rows if t.n_rows else 0.0
        out[-1].value = worth(out[-1].meta["stake"])
    return out


def goal_ids(answers: dict) -> list:
    """The goals to rank by: the owner's picks, or else the goals their typed words
    point at. Notes read the picks alone."""
    g = answers.get("goal") or {}
    return list(g.get("options") or g.get("inferred_options") or [])


def value(q: Q, analysis, answers: dict, env: Env) -> float:
    """stake x certainty x kind weight. A detector sets its own stake and value
    (findings, dossiers, joins, aliases, follow-ups at 1.5x). A playbook question
    is a prior about files like this one: backed by a detector, its stake is what
    the detector found in its column (or the whole table, for what a row is) and
    its certainty lower than the finding's; unbacked, its stake is the whole table
    at a prior's certainty. A little lower again when it serves none of the owner's goals."""
    if q.kind == "goal":
        return 100.0
    if q.meta.get("confirmed_by_pick"):
        return 0.0                # the pick that opened it already confirmed what it would ask
    if q.meta.get("confirm"):
        return q.value            # a confirm of what code counted carries its own value
    if q.source != "playbook":
        return q.value or worth(q.meta.get("stake", 0.0))
    goal = goal_ids(answers)
    outputs = {o["id"]: o for o in analysis.playbook.get("outputs", [])}
    serves = any(set(goal) & set((outputs.get(oid) or {}).get("goals", [])) for oid in q.unblocks)
    backed = bool(q.meta.get("backed"))
    q.meta.setdefault("stake", 1.0)
    aspect = (q.meta.get("about") or {}).get("aspect") or _ASPECT.get(q.kind, "meaning")
    # what a model's formulas back (the reach of a rate, a detected boundary) counts whatever the goal
    serves = serves or bool(q.meta.get("reach"))
    certainty = q.meta.get("certainty") or (BACKED if backed else PRIOR)
    return round(worth(q.meta["stake"], aspect, certainty) * (1.0 if not goal or serves else 0.8), 3)


def substantive(answers: dict) -> int:
    """Questions that count toward the cap, a Not sure included: not the goal,
    the closing question, the build pick nor a rule readback."""
    from .findings import READBACK
    return len([k for k in answers if not k.startswith(("_", READBACK)) and k not in ("goal", CLOSER)])


def in_round(q: Q) -> bool:
    """A question that may be asked in a round: anything a detector found, and a
    playbook question only when a detector backs it; the rest wait for just in time.
    A confirm of what code counted only fills room left over."""
    if q.meta.get("leftover") and not q.meta.get("backed"):
        return False
    return q.source != "playbook" or bool(q.meta.get("backed"))


def ranked(cands: list, first_round: bool = False) -> list:
    """The questions worth asking now, most valuable first: one sort by value
    (a follow-up already carries its 1.5x, so it never jumps the queue by rule),
    the larger stake first on a tie, then codes only the team reads, none below
    TAU, and at most one per table and column (the rest wait for a later round).
    A readback of the owner's typed rules never takes a column's place."""
    from .findings import READBACK
    rest = [q for q in cands if q.kind != "goal" and q.value >= TAU and in_round(q)
            and not (first_round and q.gated)]
    rest.sort(key=lambda q: (-q.value, -float(q.meta.get("stake", 0.0)), not q.meta.get("cryptic"), q.priority,
                             q.id))
    out, taken = [], {}
    for q in rest:
        ab = q.meta.get("about") or {}
        if not q.id.startswith(READBACK) and ab.get("col"):
            # one question per column and values per round: two questions about other values of one column
            # (a spelling pair and the names missing from a list) may share a round
            key = (ab.get("table"), ab["col"])
            mine = _ident(ab)
            if any(_same_slot(v, mine) for v in taken.get(key, [])):
                continue
            taken.setdefault(key, []).append(mine)
        out.append(q)
    return out


def _same_slot(a, b) -> bool:
    """Two questions on one column take one slot of a round when their values
    overlap or either names none; model cells only when they are the same cells."""
    cells = lambda v: bool(v) and all(isinstance(x, tuple) for x in v)  # noqa: E731
    if cells(a) or cells(b):
        return a == b
    return a is None or b is None or bool(a & b)


def next_round(analysis, state: dict) -> list:
    """The next round: the goal first, then the most valuable questions, at most
    3 in round 1 and 4 after, never past 10 substantive questions, plus the
    readback of rules the owner typed (outside the cap). [] when done."""
    from .findings import READBACK
    answers = state.get("answers", {})
    asked = substantive(answers)
    rnd = state.get("round", 0)
    if state.get("stopped") or asked >= HARD_CAP or rnd >= MAX_ROUNDS:
        return []
    cands = candidates(analysis, answers)
    goal_q = [q for q in cands if q.kind == "goal"][:1]
    pick = ranked(cands, first_round=rnd == 0)
    room = min((ROUND1 - len(goal_q)) if rnd == 0 else ROUND_N, HARD_CAP - asked)
    subs_all = [q for q in pick if not q.id.startswith(READBACK)]
    # a confirm of what code counted enters a round when it is worth more than the least valuable pick there
    subs_all = _with_confirms(cands, subs_all, room, first_round=rnd == 0)
    # a thread the last round opened (a follow-up) goes first into the last slot the cap leaves
    if room == HARD_CAP - asked and room <= 1:
        subs_all.sort(key=lambda q: not q.gated)
    if asked + room >= HARD_CAP and rnd + 1 < MAX_ROUNDS and room > 1 and len(subs_all) > room \
            and any(_opens_thread(q) for q in subs_all[:room]):
        # the round would spend the cap with a question that may open a follow-up: one slot waits for it. The
        # most valuable opener stays in this round; the least valuable of the others waits instead
        head = subs_all[:room]
        keep = max((q for q in head if _opens_thread(q)), key=lambda q: (q.value, -head.index(q)))
        drop = min((q for q in head if q is not keep), key=lambda q: (q.value, -head.index(q)))
        subs_all = [q for q in subs_all if q is not drop]
        room -= 1
    subs = subs_all[:room]
    if len(subs) < room:
        subs += filling(cands, subs, first_round=rnd == 0)[:room - len(subs)]
    readback = [q for q in pick if q.id.startswith(READBACK)][:1]
    return goal_q + subs + readback


def _with_confirms(cands: list, subs: list, room: int, first_round: bool = False) -> list:
    """The round's picks with each one-tap confirm of what code counted (a leftover
    that only fills room) moved in when it is worth more than the least valuable
    pick the round would take; the pick it passes waits."""
    confirms = sorted([q for q in cands if q.meta.get("confirm") and q.meta.get("leftover") and not in_round(q)
                       and q.value >= TAU and not (first_round and q.gated)], key=lambda q: -q.value)
    out = list(subs)
    for c in confirms:
        head = out[:room]
        if len(head) < room or not head:
            break                        # room is left over: filling places it
        low = min(head, key=lambda q: q.value)
        if c.value > low.value and c.id not in {q.id for q in out}:
            out.insert(out.index(low), c)
    return out


def _opens_thread(q: Q) -> bool:
    """A question an answer to which can open a follow-up of its own."""
    return q.id.startswith(("find_unmatched_", "find_negatives_", "find_contra_", "find_boundary_"))


def filling(cands: list, taken: list, first_round: bool = False) -> list:
    """Room left once every question a detector found (or backs) is asked: the
    playbook's questions about a column that is in this file (its role binds to
    a column), then those about a whole table and the confirms of what code
    counted, most valuable first, one per column (or table). A playbook question
    worth less than TAU, or one gated on an answer, still waits for just in time."""
    from .findings import READBACK
    seen = {((q.meta.get("about") or {}).get("table"), (q.meta.get("about") or {}).get("col")) for q in taken}
    ids = {q.id for q in taken}
    pool = [q for q in cands if (q.source == "playbook" or q.meta.get("leftover")) and not in_round(q)
            and q.id not in ids and not q.id.startswith(READBACK) and q.kind != "goal" and q.value >= TAU
            and not (first_round and q.gated)]
    # a column's question before a whole table's, then by value
    pool.sort(key=lambda q: (not (q.meta.get("about") or {}).get("col"), -q.value, q.priority, q.id))
    out = []
    seen = {(k[0], k[1] or ("kind", _kind_of(q))) for k, q in
            zip([((q.meta.get("about") or {}).get("table"), (q.meta.get("about") or {}).get("col")) for q in taken],
                taken)}
    for q in pool:
        ab = q.meta.get("about") or {}
        # a column is taken by one question; a whole table by one question of each kind (what a row is beside
        # which rows count)
        key = (ab.get("table"), ab.get("col") or ("kind", _kind_of(q)))
        if key in seen:
            continue
        seen.add(key)
        out.append(q)
    return out


def _kind_of(q: Q) -> str:
    return (q.meta.get("about") or {}).get("aspect") or q.kind


def closer_question(analysis, answers: dict):
    """Asked once, after the last round and before the build pick, outside the
    cap: what someone new would get wrong, seeded with up to 3 things code found
    and never asked, each a short clause with its counts."""
    if CLOSER in answers:
        return None
    # what code found and never asked, even a finding an answer about other values of its column set aside
    found = [q for q in ranked(candidates(analysis, answers, settle=False)) if q.source == "finding"
             and not q.gated and q.id not in answers]
    noticed = [_clause(q) for q in found[:3]]
    prompt = "Last one: what would someone new get wrong in this file?"
    if noticed:
        prompt += " I noticed: " + "; ".join(noticed) + "."
    prompt += (" Anything else: rows that do not belong in totals, numbers in a different unit, codes that changed "
               "meaning, something planned but not final, a known mistake.")
    t = analysis.main_table
    return Q(CLOSER, "Last one", prompt,
             [{"id": "type", "label": "I'll type it", "desc": "Type it as your answer"},
              {"id": "nothing", "label": "Nothing to add", "desc": "No note is written"}],
             why="What only you know about this file is what gets lost when someone else picks it up.",
             kind="history", priority=1, source="builtin",
             meta={"about": {"table": t.tid if t is not None else "", "col": "", "aspect": "history"},
                   "noticed": [q.id for q in found[:3]]})


def _clause(q: Q) -> str:
    """A finding in one short clause with its counts: the detector's own, else the
    first counted sentence of its prompt that is not the question itself."""
    if q.meta.get("clause"):
        return q.meta["clause"]
    sents = [s.strip() for s in re.split(r"(?<=[.?!])\s+", q.prompt) if s.strip()]
    s = next((x for x in sents if not x.endswith("?") and re.search(r"\d", x)), sents[0] if sents else q.header)
    s = s.rstrip(".?! ")
    return s if len(s) <= 110 else s[:107].rsplit(" ", 1)[0] + "..."


def build_question(analysis, answers: dict):
    """The last step: now that it understands the data, what should it build?
    The top suggestions as options; "Other" (the harness adds it, or free text)
    covers anything else the owner has in mind."""
    from . import say
    _, items = say.suggestions(analysis, answers)
    if not items:
        return None
    items = sorted(items, key=lambda it: (-it["score"], bool(it.get("missing"))))
    opts = []
    for it in items[:4]:
        desc = it["pitch"]
        if it.get("missing"):
            desc = f"{desc} (needs one more answer)"
        opts.append({"id": it["id"], "label": it["title"][:60], "desc": _cut(desc)})
    if len(opts) < 2:
        opts.append({"id": "just_ask", "label": "Nothing yet, I'll just ask", "desc": "Use the brain to answer questions"})
    return Q("_build", "Build", "I understand this data now. What should I build for you first? Pick one, or "
                                "type something else entirely.",
             opts[:4], why="", multi=False, kind="build", priority=1, source="builtin")


def open_items(analysis, state: dict) -> list:
    """Eligible questions never asked, most valuable first: only aspects no
    answer settled, each with its evidence in its prompt. Stored as open items,
    asked just in time."""
    from .findings import READBACK
    answers = state.get("answers", {})
    covered = covered_ids(analysis, answers)
    routed = set(closer_routes(analysis, answers))
    out = [q for q in candidates(analysis, answers) if q.kind != "goal" and q.id not in answers
           and q.id not in covered and not q.id.startswith(READBACK) and q.id not in routed
           and not q.meta.get("confirmed_by_pick")
           # what a row is, on a table whose key code counted, is not left open
           and not (q.source == "playbook" and q.kind == "grain"
                    and _grain_typed(analysis, (q.meta.get("about") or {}).get("table")))]
    return sorted(out, key=lambda q: (-q.value, -float(q.meta.get("stake", 0.0)), q.id))


def closer_routes(analysis, answers: dict) -> dict:
    """{open question id: [the closing answer's sentences that name its values]}:
    what the owner wrote in the closer about something still open closes it (the
    note stays the closer's, so it never counts as that question's answer)."""
    a = (answers or {}).get(CLOSER) or {}
    text = str(a.get("text") or "") if isinstance(a, dict) else ""
    if not text.strip():
        return {}
    from . import privacy
    sents = privacy.said_sentences(text)
    out: dict = {}
    cands = [q for q in candidates(analysis, {k: v for k, v in answers.items() if k != CLOSER})
             if q.kind != "goal" and q.id not in answers]
    for q in cands:
        vals = [str(v) for v in (q.meta.get("about") or {}).get("values") or [] if len(str(v).strip()) >= 3]
        hit = [s for s in sents if any(re.search(r"(?<![\w])" + re.escape(v.strip()) + r"(?![\w])", s, re.I)
                                       for v in vals)]
        if hit:
            out[q.id] = hit
    # a finding about a whole column (its negative lines, the base a rate is paid on) is closed by a sentence with
    # its kind's cue words, and its column's header when more than one finding of that kind is open
    for prefix, cue in _COLUMN_CUES:
        of_kind = [q for q in cands if q.id.startswith(prefix) and q.id not in out]
        for q in of_kind:
            head = str((q.meta.get("about") or {}).get("col") or "")
            hit = [s for s in sents if cue.search(s) and (len(of_kind) == 1 or (head and re.search(
                r"(?<![\w])" + re.escape(head) + r"(?![\w])", s, re.I)))]
            if hit:
                out[q.id] = hit
    return out


# what a sentence says when it answers a finding about a whole column
_COLUMN_CUES = (("find_negatives_", re.compile(r"\b(negative|negatives|credits?|returns?|refunds?)\b", re.I)),
                ("follow_rebate_base", re.compile(r"\b(rebates?|basis|base)\b", re.I)))


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------
def _options_for(q: Q) -> list:
    """The options as shown, in letter order: the recommendation first, then the
    rest, then Not sure as its own letter on every question but the goal and the build."""
    opts = list(q.options)
    if q.recommend:
        opts.sort(key=lambda o: 0 if o["id"] == q.recommend else 1)
    unsure = next((o for o in opts if o["id"] == "not_sure"), None)
    opts = [o for o in opts if o["id"] != "not_sure"][:4]
    if q.kind not in ("goal", "build"):
        opts.append(unsure or dict(NOT_SURE))
    return opts


DESC_MAX = 200                   # the ask tool's room under an option
# every single-choice question says so: the owner's words ride on the pick, never instead of it
CARRY_WORDS = "Pick one, and add words if it needs more."


def _cut(desc: str, n: int = DESC_MAX) -> str:
    """A description cut to fit, ending on a whole sentence, never mid-word."""
    if len(desc) <= n:
        return desc
    cut = desc[:n].rfind(". ")
    return desc[:cut + 1] if cut > 60 else desc[:n - 3].rsplit(" ", 1)[0] + "..."


def _shown_desc(q: Q, o: dict, limit: int = 0) -> str:
    """What the owner reads under an option: its own description, and for the
    recommended one, why it is recommended. With a limit, only the part the
    ask tool shows."""
    desc = o.get("desc", "")
    if q.recommend and o["id"] == q.recommend and q.recommend_basis \
            and q.recommend_basis.rstrip(". ") not in desc:          # never the same sentence twice
        desc = f"{desc.rstrip('. ')}. {q.recommend_basis}" if desc else q.recommend_basis
    return _cut(desc, limit) if limit else desc


def render_ask(qs: list) -> dict:
    """The exact payload for a structured ask tool (AskUserQuestion shape). The
    tool takes 4 options; when Not sure does not fit, the question says so."""
    out = []
    for q in qs:
        opts = []
        shown = _options_for(q)
        typed = []
        if len(shown) > 4 and shown[-1]["id"] == "not_sure":
            # Not sure keeps its place in every list; an option past the tool's four is named to type instead
            typed = [o for o in shown[:-1] if o is not shown[-1]][3:]
            shown = shown[:3] + [shown[-1]]
        for o in shown[:4]:
            label = o["label"]
            if q.recommend and o["id"] == q.recommend:
                # the mark stays whole: a long label gives way at a word, and an answer still finds its option
                room = 60 - len(" (Recommended)")
                if len(label) > room:
                    label = re.sub(r"\s*\([^)]*$", "", label[:room].rsplit(" ", 1)[0]).rstrip(" ,;:")
                label = f"{label} (Recommended)"
            opts.append({"label": label[:60], "description": _shown_desc(q, o, DESC_MAX)})
        text = q.prompt
        if typed:
            text += " Or choose Other and type " + " or ".join(f"\"{o['label']}\"" for o in typed) + "."
        elif len(shown) > 4:
            text += " Not sure is fine: choose Other and say so."
        if not q.multi and q.kind not in ("goal", "build"):
            text += " " + CARRY_WORDS
        if q.why:
            text += f" Why I ask: {q.why}"
        out.append({"question": text, "header": _header(q.header), "multiSelect": q.multi,
                    "options": opts})
    return {"questions": out}


def render_text(qs: list, lead: str = "") -> str:
    lines = []
    if lead:
        lines.append(lead)
    has_rec = any(q.recommend for q in qs)
    if len(qs) == 1:
        tip = 'Reply with a letter, or a letter and your words like "a) ...", or type your own answer'
        if qs[0].multi:
            tip = 'Reply with the letters ("ab"), or type your own answer'
        lines.append(tip + ("" if qs[0].kind == "build" else ', and "not sure" is always fine') + ".")
    else:
        tip = 'Reply like "1a 2b 3a"'
        if any(q.multi for q in qs):
            tip += ' (for "pick all that apply", list the letters: "2ab")'
        if has_rec:
            tip += ', or "ok" to take my recommendations'
        lines.append(tip + ". For your own answer, type it after the number, and \"not sure\" is always fine. A "
                           "pick can carry your words too, like \"2b, because ...\".")
    for i, q in enumerate(qs, 1):
        lines.append("")
        num = "" if len(qs) == 1 else f"{i}. "
        lines.append(f"{num}{q.prompt}" + (" Pick all that apply." if q.multi and "all that apply"
                                           not in q.prompt else ""))
        if q.why:
            lines.append(f"   Why: {q.why}")
        opts = _options_for(q)
        if q.kind == "build":          # what each one is matters more than a compact line
            for k, o in enumerate(opts):
                d = o.get("desc", "")
                text = d if d.lower().startswith(o["label"].lower()) else f"{o['label']}: {d}".rstrip(": ")
                lines.append(f"   {chr(97 + k)}) {text}")
            continue
        # one option per line with its description, so any word a note borrows from it was seen
        for k, o in enumerate(opts):
            lab = o["label"]
            if q.recommend and o["id"] == q.recommend:
                lab += " (recommended)"
            desc = _shown_desc(q, o)
            lines.append(f"   {chr(97 + k)}) {lab}" + (f": {desc}" if desc else ""))
    return "\n".join(lines)


# --------------------------------------------------------------------------
# answers
# --------------------------------------------------------------------------
def _norm(s: str) -> str:
    s = re.sub(r"\(recommended\)", "", str(s), flags=re.I)
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def parse_answers(qs: list, raw) -> dict:
    """Map a structured-ask result (dict keyed by question text, header or id)
    or a text reply ("1a 2b,c 3 my own words", or "ok") onto option ids."""
    out: dict = {}
    if isinstance(raw, str):
        raw = raw.strip()
        if raw.lower().strip(" .!") in ("ok", "okay", "yes", "sure", "go", "recommended", "fine"):
            # recommendations where they exist; the rest stay open, never guessed
            for q in qs:
                out[q.id] = _answer(q, [q.recommend] if q.recommend else ["not_sure"], "")
                out[q.id]["accepted"] = bool(q.recommend)    # took the recommendation, did not pick it
            return out
        if len(qs) == 1 and re.fullmatch(r"[1-9]", raw) and int(raw) <= len(qs[0].options):
            # one question, a lone number: the user means the option in that position
            return {qs[0].id: _answer(qs[0], [qs[0].options[int(raw) - 1]["id"]], "")}
        # split only at question numbers that ascend ("3 cost plus 2 a case" stays one answer)
        cuts, last = [], 0
        # a multi-line reply is split only where a line starts with a number; a one-line reply
        # anywhere, but never at "2% rebate", "2.10" or "$2" inside someone's words
        multiline = "\n" in raw.strip()
        start = r"(?:^|(?<=\n))\s*" if multiline else r"(?:^|(?<=\s))"
        pattern = start + r"(\d{1,2})(?![\d%$,]|\.\d)\s*[.):]?\s*"
        for m in re.finditer(pattern, raw):
            k = int(m.group(1))
            if len(qs) == 1 and raw[:m.start()].strip():
                continue          # one question: its number can only open the reply ('5 = sent back, 1 = kept')
            if last < k <= len(qs):
                attached = m.end(1) < len(raw) and raw[m.end(1)].isalpha()   # "1b", not "1 b..."
                cuts.append((k, m.start(), m.end(), attached))
                last = k
        for i, (k, _s, e, attached) in enumerate(cuts):
            end = cuts[i + 1][1] if i + 1 < len(cuts) else len(raw)
            q = qs[k - 1]
            body = raw[e:end].strip().rstrip(",;/")
            out[q.id] = _parse_body(q, body, attached)
        if not cuts and len(qs) == 1 and raw.strip():
            # one question: "a", "b" or the user's own words, no number needed
            out[qs[0].id] = _parse_body(qs[0], raw.strip(), attached=bool(re.fullmatch(r"[a-eA-E]", raw.strip())))
        return out
    if isinstance(raw, dict):
        for key, val in raw.items():
            q = _find_q(qs, key)
            if q is None:
                continue
            vals = val if isinstance(val, list) else [val]
            ids, texts = [], []
            for v in vals:
                for piece in ([v] if not isinstance(v, str) else _split_multi(q, v)):
                    oid = _match_label(q, piece)
                    if oid:
                        ids.append(oid)
                    elif str(piece).strip():
                        texts.append(str(piece).strip())
            ans = _answer(q, ids, "; ".join(texts))
            if q.kind == "goal" and ans["text"]:
                ans = _goal_from_text(q, ans, ans["text"])
            out[q.id] = ans
    return out


_LETTERS = re.compile(r"^\(?([a-e](?:[\s,&+/]*[a-e])*)\)?(?=$|[\s,.;:)\-])", re.I)
_MARKED = re.compile(r"(?:\)|[.:,]|\s*-)(?=\s|$)")                 # "a) ...", "a. ...", "a - ", never "a.m."
_SPACED = re.compile(r"[a-e](?:\s*[,&+/]\s*[a-e]|\s+[a-e](?![a-z]))+", re.I)   # "a, b", "a b", never "a bed"
_LETTER_WORDS = {"ad", "be", "ace"}          # English words written in option letters, in letter order


def _parse_body(q: Q, body: str, attached: bool = False) -> dict:
    """'a', 'ab', 'a,b', 'a) my own words', or plain words. A letter followed by
    words is a pick only when it is written right after the number ("1b clean
    list"), marked off ("a) ...", "a: ...", "a - ...") or opens a type-it option
    ("a S1 = new lead"). Otherwise it may be a word ("a report by team"), and
    the whole reply is kept as typed."""
    opts = _options_for(q)
    if re.fullmatch(r"not sure\.?|skip\.?|pass\.?|n/?a\.?|dunno\.?", body.strip(), re.I):
        return _answer(q, ["not_sure"], "")
    m = _LETTERS.match(body)
    if m:
        letters = re.findall(r"[a-e]", m.group(1), re.I)
        rest = body[m.end():].strip(" ,.;:-")
        valid = all(ord(x.lower()) - 97 < len(opts) for x in letters)
        marked = bool(_MARKED.match(body, m.end(1)))
        # "a, b" or "ab" on a pick-all question are letters; "a bed", "bad" or "be sure" are words
        low = m.group(1).lower()
        spaced = len(letters) > 1 and (bool(_SPACED.fullmatch(low)) or (
            q.multi and low.isalpha() and list(low) == sorted(set(low)) and low not in _LETTER_WORDS))
        # "a S1 = new lead" opens a type-it option; "a few are fees" is the article
        typed_it = valid and len(letters) == 1 and opts[ord(letters[0].lower()) - 97]["id"] == "type" \
            and not rest[:1].islower()
        if valid and (attached or not rest or marked or spaced or typed_it):
            ids = [opts[ord(x.lower()) - 97]["id"] for x in letters]
            if not q.multi:
                ids = ids[:1]
            ans = _answer(q, ids, rest)
            if q.kind == "goal" and rest:
                ans = _goal_from_text(q, ans, rest)
            return ans
    if re.fullmatch(r"\(?[a-e]\)?\.?", body.strip(), re.I):
        return _answer(q, ["not_sure"], "")        # a letter past the last option: nothing picked, nothing said
    ans = _match_free(q, body)
    if q.kind == "goal" and ans.get("text") and not ans.get("options"):
        ans = _goal_from_text(q, ans, ans["text"])
    return ans


_STOP = {"the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "it", "my", "our", "this", "that",
         "what", "i", "we", "with", "by", "is", "are", "be", "get", "all", "every", "each", "from", "out"}


def _goal_from_text(q: Q, ans: dict, text: str) -> dict:
    """A goal typed in the owner's own words stays their words: it never becomes
    a pick. It points at every goal whose label's topic words it holds
    (inferred_options; those also open the goal's gated questions, Env.holds),
    else at one goal when the words it shares with the labels all belong to a
    single option (ranking only). Neither reaches a note."""
    words = {w for w in re.findall(r"[a-z]+", text.lower()) if w not in _STOP and len(w) > 2}
    stems = {w[:5] for w in words}
    picked = list(ans.get("options") or [])
    # every goal whose label's topic words the text holds (these also open the goal's gated questions, see Env)
    clear = [o["id"] for o in q.options if o["id"] not in picked and goal_clearly_names(text, o["label"])]
    if clear:
        return dict(ans, inferred_options=clear)
    hits = []
    for o in q.options:
        # the option's own label words only; its description is too generic to match on
        ow = {w for w in re.findall(r"[a-z]+", o["label"].lower()) if w not in _STOP and len(w) > 3}
        if {w[:5] for w in ow} & stems and o["id"] not in picked:
            hits.append(o["id"])
    return dict(ans, inferred_options=hits if len(hits) == 1 else [])


def _split_multi(q: Q, v: str) -> list:
    """Commas split a reply only when every piece is an option, so typed words
    with commas in them stay exactly as typed."""
    if _match_label(q, v):
        return [v]
    if q.multi and "," in v:
        pieces = [p.strip() for p in v.split(",") if p.strip()]
        if pieces and all(_match_label(q, p) for p in pieces):
            return pieces
    return [v]


def _find_q(qs: list, key: str):
    """The question an answer's key names: its id, its header, or its text as the ask tool showed it (the
    prompt, then 'Why I ask: ...'), however long the prompt is."""
    k = _norm(key)
    for q in qs:
        p = _norm(q.prompt)
        if k in (_norm(q.id), _norm(q.header)) or len(k) > 10 and (p.startswith(k[:60]) or len(p) > 10
                                                                    and k.startswith(p)):
            return q
    return None


def _match_label(q: Q, piece) -> str | None:
    p = _norm(piece)
    if not p:
        return None
    for o in _options_for(q):
        if p == _norm(o["label"]) or p == _norm(o["id"]):
            return o["id"]
    if len(p) == 1 and "a" <= p <= "e":
        opts = _options_for(q)
        k = ord(p) - 97
        if k < len(opts):
            return opts[k]["id"]
    # a label the ask tool showed cut to fit ('Per month, before X (Recommended)' for a longer label): the one
    # option whose label starts with it
    cut = _norm(re.sub(r"\s*\([^)]*$", "", str(piece)))
    if len(cut) >= 12:
        hits = [o["id"] for o in _options_for(q) if _norm(o["label"]).startswith(cut)]
        if len(hits) == 1:
            return hits[0]
    return None


def _match_free(q: Q, body: str) -> dict:
    oid = _match_label(q, body)
    if oid:
        return _answer(q, [oid], "")
    return _answer(q, [], body)


def _names_all(text: str, values) -> bool:
    """The typed text names every one of these values, each as a whole word."""
    vals = [str(v).strip() for v in values or [] if str(v).strip()]
    return bool(vals) and bool((text or "").strip()) and all(
        re.search(r"(?<![\w])" + re.escape(v) + r"(?![\w])", text, re.I) for v in vals)


# words that turn a sentence around: a word just after one of them is said the other way
_NEGATE = re.compile(r"^(not|no|never|none|nothing|nor|without|isn't|aren't|wasn't|weren't|don't|doesn't|didn't|"
                     r"won't|can't|cannot|shouldn't)$", re.I)
# a typed reply that says a reading is not right as shown
_CONTRADICTS = re.compile(r"\b(not|wrong|missing|except|isn't|aren't|wasn't|weren't|doesn't|don't|incorrect)\b|n't\b",
                          re.I)
# a typed reply to a mapping that says the pairs are not one
_REJECTS = re.compile(r"\b(not|no|never|different|separate|apart|none|isn't|aren't|unrelated)\b|n't\b", re.I)
# a typed reply to a mapping that gives the cause of new names
_CAUSE = re.compile(r"\b(switch\w*|chang\w*|renam\w*|moved|migrat\w*|software|system|platform|same|because|"
                    r"why|new names?|replaced)\b", re.I)
_TREAT_VERB = re.compile(r"\b(leave|left|drop\w*|exclud\w*|remov\w*|skip\w*|ignor\w*|count\w*|keep|kept|"
                         r"stay\w*|out)\b", re.I)


def _text_stems(text: str) -> tuple:
    """(the typed words said as they are, the words said the other way): each as a
    rough stem, 4 letters or more; a word within two words after 'not', 'no' or
    'never' is said the other way."""
    toks = [x.strip("'") for x in re.findall(r"[A-Za-z][A-Za-z']*", str(text or ""))]
    pos, neg = set(), set()
    for i, w in enumerate(toks):
        lw = w.lower().strip("'")
        if len(lw) < 4 or lw in _GENERIC or lw in _STOP:
            continue
        near = any(_NEGATE.match(x.lower()) or x.lower().endswith("n't") for x in toks[max(0, i - 2):i])
        (neg if near else pos).add(_stem(lw))
    return pos, neg


def _option_words(q: Q, o: dict) -> set:
    """The words an option stands for: its label, the description shown under it,
    its curated statement and the values its rule names (rough stems, 4 letters
    or more, none that every question uses)."""
    parts = [o.get("label", ""), _shown_desc(q, o)]
    st = ((q.fact or {}).get("statements") or {}).get(o["id"])
    if st:
        parts.append(re.sub(r"\{[^}]*\}", " ", st))
    rs = (q.meta.get("rules") or {}).get(o["id"])
    for d in (rs if isinstance(rs, list) else [rs] if rs else []):
        parts += [str(v) for c in d.get("predicate") or [] for v in c.get("values") or []]
    ex = q.meta.get("exclude") or {}
    if o["id"] in (ex.get("options") or []):
        parts += [str(v) for v in ex.get("values") or []]
    return {_stem(w) for w in re.findall(r"[a-z]+", " ".join(parts).lower())
            if len(w) >= 4 and w not in _GENERIC and w not in _STOP}


def _infer_option(q: Q, text: str):
    """What a typed reply with no pick says of the options, never a pick of its own:
    ('infer', [option ids]) for options it restates, to be read back as a line to
    tick; ('pick', option id) only when it says a counted reading is wrong (the
    owner said so); None otherwise.
    - A confirm of what code counted: words that restate the reading and
      contradict nothing (no 'not', 'wrong', 'missing' or 'except', no number or
      tab other than those shown) restate 'right'; words that contradict it pick
      'wrong'.
    - A mapping: a cause that rejects no pair ('we switched systems, that is why the
      names change') restates the map option.
    - Otherwise: the one option (on a pick-all question, each option) whose own
      words the reply shares two or more of, said as they are, with no word only
      another option has (more of them said as they are than said the other
      way, after 'not' or 'never'); or that names every value its rule names
      beside a treatment verb."""
    from .findings import READBACK
    text = str(text or "").strip()
    if not text or q.kind in ("goal", "build") or q.id.startswith(READBACK) or q.id == CLOSER:
        return None
    ids = {o["id"] for o in q.options}
    if q.meta.get("confirm") and {"right", "wrong"} <= ids:
        shown = re.sub(r"[,$]", "", q.prompt)
        nums = [re.sub(r"[,$]", "", x).rstrip(".") for x in re.findall(r"\d[\d,.]*", text)]
        other_num = any(x and x not in shown for x in nums)
        tabs = [s_ for s_ in q.meta.get("sheets") or [] if re.search(r"(?<!\w)" + re.escape(s_) + r"(?!\w)", text)]
        other_tab = any(s_ not in q.prompt for s_ in tabs)
        if _CONTRADICTS.search(text) or other_num:
            return ("pick", "wrong")
        pos, _neg = _text_stems(text)
        reading = _topic_stems(q.prompt)
        if not other_tab and (any(s_ in q.prompt for s_ in tabs) or len(pos & reading) >= 2):
            return ("infer", ["right"])
        return None
    mt = (q.meta.get("map_text") or {}).get("option")
    if mt or q.kind == "mapping":
        opt = mt if mt in ids else next((x for x in ("all", "same") if x in ids), None)
        if opt and _CAUSE.search(text) and not _REJECTS.search(text):
            return ("infer", [opt])
        return None
    if q.meta.get("dossier") or q.meta.get("codes"):
        return None                   # typed meanings of codes are read code by code, as rules to read back
    opts = [o for o in q.options if o["id"] not in ("not_sure", "type", "skip")]
    if len(opts) < 2:
        return None
    # the question's own statement is every pick's frame, never one option's words
    frame = {_stem(w) for w in re.findall(r"[a-z]+", re.sub(r"\{[^}]*\}", " ", str((q.fact or {}).get("statement")
                                                                                   or "")).lower())}
    words = {o["id"]: _option_words(q, o) - frame for o in opts}
    own = {oid: w - set().union(*[x for k, x in words.items() if k != oid]) for oid, w in words.items()}
    pos, neg = _text_stems(text)
    # two or more of an option's own words said as they are, more of them than said the other way
    hits = [oid for oid, w in own.items() if len(w & pos) >= 2 and len(w & pos) > len(w & neg)]
    # every value an option's rule names, said beside a treatment verb
    for o in opts:
        rs = (q.meta.get("rules") or {}).get(o["id"])
        vals = [str(v) for d in (rs if isinstance(rs, list) else [rs] if rs else [])
                for c in d.get("predicate") or [] for v in c.get("values") or []]
        if vals and o["id"] not in hits and _names_all(text, vals) and _TREAT_VERB.search(text) \
                and not any(_NEGATE.match(w) for w in re.findall(r"[a-z']+", text.lower())):
            hits.append(o["id"])
    if not hits:
        return None
    if not q.multi:
        if len(hits) != 1 or any(own[k] & pos for k in own if k != hits[0]):
            return None
    elif len(hits) > 1:
        # picks that rule the others out, or two that rule out one another, say nothing together
        excl = q.meta.get("exclusive") or []
        alone = {e for e in excl if isinstance(e, str)}
        hits = [h for h in hits if h not in alone]
        hits = [h for h in hits if not any(h in e and len(set(e) & set(hits)) > 1 for e in excl
                                               if isinstance(e, (list, tuple)))]
    return ("infer", hits) if hits else None


def _answer(q: Q, ids: list, text: str) -> dict:
    ids = [i for i in ids if i]
    if ids == ["type"] and not (text or "").strip():
        ids = ["not_sure"]            # "I'll type them" with nothing typed: still open, not an answer
    if ids == ["skip"]:
        ids = ["not_sure"]            # "Skip for now" says nothing about the data: open, like Not sure
    # a typed reply with no pick: what it restates of the options is kept to read back, never a pick; a reply
    # that says a counted reading is wrong is that pick
    inferred = []
    if not [i for i in ids if i != "not_sure"] and (text or "").strip():
        got = _infer_option(q, text)
        if got and got[0] == "pick":
            ids = [got[1]]
        elif got:
            inferred = [i for i in got[1] if i in {o["id"] for o in q.options}]
    # exclusive: an option id that rules out every other pick ('All of them count'), or a list of ids that
    # rule out one another only ('Count them' beside 'Leave them out'; another leave-out goes with either)
    excl = q.meta.get("exclusive") or []
    real = {i for i in ids if i != "not_sure"}
    alone = {e for e in excl if isinstance(e, str)}
    if (alone & real and len(real) > 1) or any(len(set(e) & real) > 1 for e in excl if isinstance(e, (list, tuple))):
        ids = []                      # picks that say opposite things settle nothing; typed text still counts
    not_sure = ids == ["not_sure"] or (not ids and not text)
    ids = [i for i in ids if i != "not_sure"]
    labels = [o["label"] for o in q.options if o["id"] in ids]
    out = {"options": ids, "labels": labels, "text": text, "not_sure": not_sure,
           "recommended_taken": bool(q.recommend and q.recommend in ids),
           "header": q.header, "prompt": q.prompt, "kind": q.kind,
           # what the owner was shown under each pick, so a note can use only words they saw
           "descs": {o["id"]: _shown_desc(q, o, DESC_MAX) for o in q.options if o["id"] in ids}}
    if q.recommend and q.recommend in ids:
        out["recommended_label"] = next((o["label"] for o in q.options if o["id"] == q.recommend), "")
    if q.meta.get("stake") is not None:
        out["stake"] = q.meta["stake"]          # what a detector found in the column, kept once answered
    if q.meta.get("about"):
        out["about"] = q.meta["about"]
        # the aspect the answer settled: 'All of them count' says what counts, not what the codes mean
        oa = q.meta.get("option_aspect") or {}
        said = [oa[i] for i in ids if oa.get(i)] + ([oa.get("type", "meaning")] if (text or "").strip() else [])
        if oa and said:
            got = "meaning" if "meaning" in said else said[0]
            if got != q.meta["about"].get("aspect"):
                out["about"] = dict(q.meta["about"], aspect=got, asked=q.meta["about"].get("aspect"))
    if (q.source in ("builtin", "finding") or q.meta.get("confirm")) and q.fact:
        out["fact"] = q.fact
    if q.meta.get("header"):
        out["meta_header"] = q.meta["header"]
    # codes: a dossier's values, one note per code; scale: a unit rule the typed number completes;
    # derive: a measure a yes confirms; propose: rules a pick offers for the readback
    # tables: every table the question reads, so the answer reaches each of their brains
    # members: the findings a batched question asked about, each answered with it
    for k in ("covers", "touches", "exclude", "keys", "codes", "scale", "derive", "propose", "tables", "members",
              "unit_group", "member_about", "code_pairs", "inputs", "sibling"):
        if q.meta.get(k):
            out[k] = q.meta[k]
    # protect: a value the owner said is right on purpose, only when that is the pick
    if (q.meta.get("protect") or {}).get("option") in ids:
        out["protect"] = q.meta["protect"]
    if q.meta.get("rules"):
        # a rule readback: every rule shown is kept, and only the ticked ones are confirmed. A scope
        # narrows the ticked rules; ticked alone it confirms nothing, since the rules shown are often
        # other readings of one sentence
        scope = [m for oid in ids for m in (q.meta.get("scopes") or {}).get(oid, [])]
        picked = [oid for oid in q.meta["rules"] if oid in ids]
        # typed words that pair every value the question showed confirm the map it offered, as a pick would
        mt = q.meta.get("map_text") or {}
        if mt.get("option") in q.meta["rules"] and mt["option"] not in picked and _names_all(text, mt.get("values")):
            picked.append(mt["option"])
        # a rule offered with its own scope ('out of Amount totals') keeps it unless a scope option narrows it
        # one pick may carry a rule per table (the same value asked once for two tabs)
        out["rules"] = [dict(d, option=oid, confirmed=oid in picked,
                             scope=(scope if oid in picked and scope else list(d.get("scope") or [])))
                        for oid, ds in q.meta["rules"].items() for d in (ds if isinstance(ds, list) else [ds])]
        if scope and not picked:          # no rule was picked, so no note says one was
            out["options"] = [i for i in out["options"] if i not in (q.meta.get("scopes") or {})]
            out["labels"] = [o["label"] for o in q.options if o["id"] in out["options"]]
            out["descs"] = {k: v for k, v in out["descs"].items() if k in out["options"]}
            out["not_sure"] = not out["options"] and not text
    # a pack or case price on a tab whose items other tabs carry at the same price: the number the owner typed
    # with the pick divides the price on every one of them (the question named each tab with its rows)
    sc = q.meta.get("scale") or {}
    if q.meta.get("scale_also") and sc.get("option") in ids:
        from .rules import Rule, typed_count
        by = typed_count(text or "")
        if by:
            out["rules"] = list(out.get("rules") or []) + [
                dict(Rule("scale", x["table"], list(x.get("predicate") or []), {"col": x["col"], "by": by},
                          source=q.id, confirmed=True).to_dict(), option=sc["option"])
                for x in q.meta["scale_also"]]
    if q.meta.get("value"):
        out["meta_value"], out["meta_prefix"] = q.meta["value"], q.meta.get("prefix", "")
    if inferred:
        # read back on the next rules readback as a line to tick; never an option, never a note of its own
        out["inferred"] = [{"option": i, "label": next(o["label"] for o in q.options if o["id"] == i),
                            "desc": _shown_desc(q, next(o for o in q.options if o["id"] == i), DESC_MAX)}
                           for i in inferred]
    if q.meta.get("infer"):
        out["infer"] = q.meta["infer"]          # a readback's lines that restate another question's option
    return out
