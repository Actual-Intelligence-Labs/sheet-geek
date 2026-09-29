#!/usr/bin/env python3
"""Independent recomputation of the confirmatory test. Written from scratch; does not import analyze.py."""
import json, os, glob, re, sys, math, random, itertools, collections
import numpy as np
from scipy import stats

R = "/private/tmp/claude-501/-Users-zachkellman-ai-os/d9f6c33d-3142-469c-a96b-b93b58b87ba6/scratchpad/rigor2"
OUT = os.path.join(R, "verify_indep")
BIZ = json.load(open(os.path.join(R, "businesses.json")))
MODELS = ["haiku", "sonnet", "opus", "luna"]
REPS = [1, 2]


def load(p):
    with open(p) as f:
        return json.load(f)


# ---------- 1. build pairs from raw files ----------
pairs = []
incomplete = []
for b in sorted(BIZ):
    for m in MODELS:
        for r in REPS:
            tdir = os.path.join(R, "trials", b, m, f"r{r}")
            st = {}
            tr = {}
            for c in ("none", "brain"):
                p = os.path.join(tdir, f"{c}.json")
                if os.path.exists(p):
                    tr[c] = load(p)
                    st[c] = tr[c].get("status")
                else:
                    st[c] = "missing"
            jdir = os.path.join(R, "judges", b, m, f"r{r}")
            j1p, j2p = os.path.join(jdir, "judge1.json"), os.path.join(jdir, "judge2.json")
            jok = os.path.exists(j1p) and os.path.exists(j2p)
            ordp = os.path.join(R, "judge_orders", b, m, f"r{r}.json")
            if not (st["none"] == "ok" and st["brain"] == "ok" and jok):
                incomplete.append(dict(business=b, model=m, rep=r, status=st, judges=jok,
                                       judge_dir_exists=os.path.isdir(jdir), order_exists=os.path.exists(ordp)))
                continue
            order = load(ordp)  # {"A": cond, "B": cond}
            assert sorted(order.values()) == ["brain", "none"], order
            side = {order["A"]: "A", order["B"]: "B"}  # cond -> letter
            rec = dict(business=b, model=m, rep=r, order=order,
                       ans={c: tr[c]["answer"] for c in ("none", "brain")},
                       saw_tab_brain=tr["brain"].get("saw_brain_tab"))
            for jn, jp in (("j1", j1p), ("j2", j2p)):
                v = load(jp)["verdict"]
                for c in ("none", "brain"):
                    s = v[side[c]]
                    rec[f"score_{c}_{jn}"] = s["on_target"] + s["right"] + s["useful"]
                    rec[f"plain_{c}_{jn}"] = list(s["plain_correct"])
                    rec[f"mist_{c}_{jn}"] = len(s["mistakes"])
                    rec[f"cites_{c}_{jn}"] = bool(s["cites_notes_tab"])
                    for k in ("on_target", "right", "useful"):
                        assert 0 <= s[k] <= 5, (b, m, r, jn, k, s[k])
                bet = v["better"]
                rec[f"pref_{jn}"] = 0 if bet == "tie" else (1 if order[bet] == "brain" else -1)
            for c in ("none", "brain"):
                rec[f"score_{c}"] = (rec[f"score_{c}_j1"] + rec[f"score_{c}_j2"]) / 2
                rec[f"mist_{c}"] = (rec[f"mist_{c}_j1"] + rec[f"mist_{c}_j2"]) / 2
            rec["diff"] = rec["score_brain"] - rec["score_none"]
            pairs.append(rec)

# sanity: are there judge dirs for incomplete pairs, or complete trials lacking judges?
extra_judge_dirs = [x for x in incomplete if x["judge_dir_exists"]]
ok_both_no_judges = [x for x in incomplete if x["status"]["none"] == "ok" and x["status"]["brain"] == "ok"]


def wil(x):
    x = np.asarray(x, float)
    nz = x[x != 0]
    if len(nz) == 0:
        return dict(n=len(x), nonzero=0, W=None, p=None)
    res = stats.wilcoxon(x, alternative="two-sided")  # default zero_method wilcox, exact when n small & no ties
    try:
        pr = stats.wilcoxon(x, zero_method="pratt", alternative="two-sided").pvalue
    except Exception:
        pr = None
    return dict(n=len(x), nonzero=int(len(nz)), W=float(res.statistic), p=float(res.pvalue), p_pratt=None if pr is None else float(pr))


def exact_signrank_p(x):
    """Exact two-sided p by full enumeration of sign flips (uses average ranks, handles ties); zeros dropped."""
    x = np.asarray(x, float)
    x = x[x != 0]
    n = len(x)
    ranks = stats.rankdata(np.abs(x))
    wplus = ranks[x > 0].sum()
    tot = ranks.sum()
    obs = min(wplus, tot - wplus)
    cnt = 0
    N = 0
    for signs in itertools.product([0, 1], repeat=n):
        wp = sum(rk for rk, s in zip(ranks, signs) if s)
        N += 1
        if min(wp, tot - wp) <= obs + 1e-9:
            cnt += 1
    return float(obs), cnt / N


def boot(x, B=10000, seed=12345):
    rng = np.random.default_rng(seed)
    x = np.asarray(x, float)
    idx = rng.integers(0, len(x), size=(B, len(x)))
    means = x[idx].mean(axis=1)
    return [float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))]


def business_level(P, key, bizlist=None):
    by = collections.defaultdict(list)
    for p in P:
        by[p["business"]].append(key(p))
    bl = sorted(by) if bizlist is None else [b for b in bizlist if b in by]
    per = {b: float(np.mean(by[b])) for b in bl}
    vals = np.array([per[b] for b in bl])
    out = dict(businesses=len(bl), pairs=sum(len(by[b]) for b in bl), per_business=per,
               mean=float(vals.mean()), median=float(np.median(vals)),
               positive=int((vals > 0).sum()), negative=int((vals < 0).sum()), zero=int((vals == 0).sum()),
               wilcoxon=wil(vals), boot_ci_95=boot(vals) if len(vals) > 1 else None)
    if len(vals) <= 20:
        W, p = exact_signrank_p(vals)
        out["exact_enum"] = dict(W=W, p=p)
    return out


def acc(lst):
    return sum(lst) / len(lst)


res = {}
res["n_pairs"] = len(pairs)
res["pairs_per_business"] = dict(collections.Counter(p["business"] for p in pairs))
res["pairs_per_model"] = dict(collections.Counter(p["model"] for p in pairs))
res["incomplete"] = [dict(business=x["business"], model=x["model"], rep=x["rep"], status=x["status"], judges=x["judges"]) for x in incomplete]
res["n_incomplete"] = len(incomplete)
res["incomplete_with_judge_dirs"] = len(extra_judge_dirs)
res["both_ok_but_no_judges"] = len(ok_both_no_judges)

# thin-business rule: fewer than half its pairs (8 planned per business)
thin = [b for b in BIZ if res["pairs_per_business"].get(b, 0) < 4]
res["thin_businesses"] = thin
primary_biz = sorted(b for b in BIZ if b not in thin)

res["primary"] = business_level([p for p in pairs if p["business"] in primary_biz], lambda p: p["diff"])
res["pair_level"] = dict(
    score_brain_mean=float(np.mean([p["score_brain"] for p in pairs])),
    score_none_mean=float(np.mean([p["score_none"] for p in pairs])),
    diff_mean=float(np.mean([p["diff"] for p in pairs])),
    diff_median=float(np.median([p["diff"] for p in pairs])),
    wilcoxon=wil([p["diff"] for p in pairs]),
    wins=dict(brain=sum(p["diff"] > 0 for p in pairs), none=sum(p["diff"] < 0 for p in pairs), tie=sum(p["diff"] == 0 for p in pairs)),
)

# ---------- 2. secondary ----------
sec = {}
for jn in ("j1", "j2"):
    allb = [x for p in pairs for x in p[f"plain_brain_{jn}"]]
    alln = [x for p in pairs for x in p[f"plain_none_{jn}"]]
    sec[f"plain_acc_pooled_{jn}"] = dict(brain=acc(allb), none=acc(alln), n_items=len(allb))
    bo = sum(1 for p in pairs for a, c in zip(p[f"plain_brain_{jn}"], p[f"plain_none_{jn}"]) if a and not c)
    no = sum(1 for p in pairs for a, c in zip(p[f"plain_brain_{jn}"], p[f"plain_none_{jn}"]) if c and not a)
    both = sum(1 for p in pairs for a, c in zip(p[f"plain_brain_{jn}"], p[f"plain_none_{jn}"]) if a and c)
    nei = sum(1 for p in pairs for a, c in zip(p[f"plain_brain_{jn}"], p[f"plain_none_{jn}"]) if not a and not c)
    sec[f"mcnemar_{jn}"] = dict(brain_only=bo, none_only=no, both=both, neither=nei,
                                p_exact=float(stats.binomtest(bo, bo + no, 0.5).pvalue) if bo + no else None)
    sec[f"plain_acc_business_{jn}"] = business_level(pairs, lambda p, jn=jn: acc(p[f"plain_brain_{jn}"]) - acc(p[f"plain_none_{jn}"]))
sec["mistakes_business"] = business_level(pairs, lambda p: p["mist_brain"] - p["mist_none"])
sec["mistakes_pair_means"] = dict(brain=float(np.mean([p["mist_brain"] for p in pairs])), none=float(np.mean([p["mist_none"] for p in pairs])))
sec["preference_business"] = business_level(pairs, lambda p: (p["pref_j1"] + p["pref_j2"]) / 2)
prefs = [x for p in pairs for x in (p["pref_j1"], p["pref_j2"])]
sec["preference_counts_judgements"] = dict(brain=prefs.count(1), none=prefs.count(-1), tie=prefs.count(0))
# pooled pair-level preference as in results: per pair average? count by sign of mean
pm = [(p["pref_j1"] + p["pref_j2"]) / 2 for p in pairs]
sec["preference_pairs_by_mean_sign"] = dict(brain=sum(x > 0 for x in pm), none=sum(x < 0 for x in pm), tie=sum(x == 0 for x in pm))
# agreement
s1 = [p[f"score_{c}_j1"] for p in pairs for c in ("none", "brain")]
s2 = [p[f"score_{c}_j2"] for p in pairs for c in ("none", "brain")]
rho, rp = stats.spearmanr(s1, s2)
sec["judge_agreement_spearman"] = dict(rho=float(rho), p=float(rp), n=len(s1))
sec["judge_agreement_pearson"] = float(stats.pearsonr(s1, s2)[0])
g1 = [x for p in pairs for c in ("none", "brain") for x in p[f"plain_{c}_j1"]]
g2 = [x for p in pairs for c in ("none", "brain") for x in p[f"plain_{c}_j2"]]
sec["plain_grade_agreement"] = dict(share=sum(a == b for a, b in zip(g1, g2)) / len(g1), n=len(g1), disagreements=sum(a != b for a, b in zip(g1, g2)))
sec["preference_agreement"] = sum(p["pref_j1"] == p["pref_j2"] for p in pairs) / len(pairs)
sec["mean_score_by_judge"] = {jn: dict(brain=float(np.mean([p[f"score_brain_{jn}"] for p in pairs])), none=float(np.mean([p[f"score_none_{jn}"] for p in pairs]))) for jn in ("j1", "j2")}
per_model = {}
for m in MODELS:
    P = [p for p in pairs if p["model"] == m]
    if not P:
        per_model[m] = dict(pairs=0)
        continue
    per_model[m] = dict(pairs=len(P),
                        score_brain_mean=float(np.mean([p["score_brain"] for p in P])),
                        score_none_mean=float(np.mean([p["score_none"] for p in P])),
                        diff_mean=float(np.mean([p["diff"] for p in P])),
                        diff_median=float(np.median([p["diff"] for p in P])),
                        plain_acc_brain_j1=acc([x for p in P for x in p["plain_brain_j1"]]),
                        plain_acc_none_j1=acc([x for p in P for x in p["plain_none_j1"]]),
                        plain_acc_brain_j2=acc([x for p in P for x in p["plain_brain_j2"]]),
                        plain_acc_none_j2=acc([x for p in P for x in p["plain_none_j2"]]),
                        mistakes_brain_mean=float(np.mean([p["mist_brain"] for p in P])),
                        mistakes_none_mean=float(np.mean([p["mist_none"] for p in P])),
                        wilcoxon=wil([p["diff"] for p in P]))
sec["per_model"] = per_model
res["secondary"] = sec

# ---------- 3. sensitivity ----------
sens = {}
STRICT = re.compile(r"_brain|notes?\s+tabs?|hidden\s+tabs?|documentation\s+tabs?|brain\s+tabs?", re.I)
BROAD = re.compile(r"_brain|notes?\s+tabs?|hidden\s+(`?_?\w*`?\s+)?tabs?|documentation\s+tabs?|brain\s+tabs?|owner'?s\s+notes?|your\s+notes|"
                   r"notes\s+(in|stored|saved|recorded)\s+(in\s+)?(the|your|each|their)?\s*(file|workbook|spreadsheet)|"
                   r"(file|workbook)\s+notes|notes\s+(your\s+colleague|you)\s+(left|gave)|workbook'?s?\s+notes|"
                   r"notes\s+in\s+(a|the|each)\s+(hidden\s+)?tab|as\s+instructed\s+in\s+the\s+workbook", re.I)


def flag(p, text_re, use_judges=True):
    j = use_judges and any(p[f"cites_{c}_{jn}"] for c in ("none", "brain") for jn in ("j1", "j2"))
    t = text_re is not None and any(text_re.search(p["ans"][c]) for c in ("none", "brain"))
    return j or t


for name, rx, uj in (("a_judge_flag_only", None, True), ("a_text_strict_only", STRICT, False), ("a_judge_or_text_strict", STRICT, True), ("a_judge_or_text_broad", BROAD, True)):
    kept = [p for p in pairs if not flag(p, rx, uj)]
    d = business_level(kept, lambda p: p["diff"])
    d["dropped_pairs"] = len(pairs) - len(kept)
    d["dropped_by_model"] = dict(collections.Counter(p["model"] for p in pairs if flag(p, rx, uj)))
    d["kept_by_model"] = dict(collections.Counter(p["model"] for p in kept))
    d["pairs_per_business_kept"] = dict(collections.Counter(p["business"] for p in kept))
    # thin rule applied (fewer than 4 pairs kept -> left out)
    thin_b = [b for b in BIZ if d["pairs_per_business_kept"].get(b, 0) < 4]
    d["thin_under_4"] = thin_b
    d["with_thin_rule"] = business_level([p for p in kept if p["business"] not in thin_b], lambda p: p["diff"]) if len(BIZ) - len(thin_b) > 1 else None
    sens[name] = d
# flag counts
sens["flag_counts"] = dict(
    judge_cites_brain_answer_any_judge=sum(any(p[f"cites_brain_{jn}"] for jn in ("j1", "j2")) for p in pairs),
    judge_cites_none_answer_any_judge=sum(any(p[f"cites_none_{jn}"] for jn in ("j1", "j2")) for p in pairs),
    judge_cites_brain_j1=sum(p["cites_brain_j1"] for p in pairs), judge_cites_brain_j2=sum(p["cites_brain_j2"] for p in pairs),
    judge_cites_none_j1=sum(p["cites_none_j1"] for p in pairs), judge_cites_none_j2=sum(p["cites_none_j2"] for p in pairs),
    text_strict_brain=sum(bool(STRICT.search(p["ans"]["brain"])) for p in pairs),
    text_strict_none=sum(bool(STRICT.search(p["ans"]["none"])) for p in pairs),
    text_broad_brain=sum(bool(BROAD.search(p["ans"]["brain"])) for p in pairs),
    text_broad_none=sum(bool(BROAD.search(p["ans"]["none"])) for p in pairs),
    saw_tab_brain=sum(bool(p["saw_tab_brain"]) for p in pairs),
)
# (b) single judge
for jn in ("j1", "j2"):
    sens[f"b_{jn}_only"] = business_level(pairs, lambda p, jn=jn: p[f"score_brain_{jn}"] - p[f"score_none_{jn}"])
# (c) leave one model out
for m in MODELS:
    kept = [p for p in pairs if p["model"] != m]
    d = business_level(kept, lambda p: p["diff"])
    sens[f"c_without_{m}"] = d
# also only-model breakdown at business level (descriptive)
for m in MODELS:
    kept = [p for p in pairs if p["model"] == m]
    if len(set(p["business"] for p in kept)) > 1:
        sens[f"c_only_{m}"] = business_level(kept, lambda p: p["diff"])

# (d) mechanical grading of single-number plain questions
NUM = re.compile(r"(?<![\w.])[-−]?\$?\s?(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)(\s?%|\s?(?:k|K|thousand|m|M|million)\b)?")


def numbers_in(text):
    out = []
    for m in NUM.finditer(text):
        s = m.group(1).replace(",", "")
        try:
            v = float(s)
        except ValueError:
            continue
        suf = (m.group(2) or "").strip().lower()
        neg = m.group(0).lstrip().startswith(("-", "−"))
        cands = [v]
        if suf == "%":
            cands.append(v / 100)
        elif suf in ("k", "thousand"):
            cands.append(v * 1000)
        elif suf in ("m", "million"):
            cands.append(v * 1e6)
        if neg:
            cands += [-c for c in cands]
        out += cands
    return out


def hit(text, key):
    tol = abs(key) * 0.01
    if key == 0:
        tol = 1e-9
    return any(abs(v - key) <= tol for v in numbers_in(text))


keys = {b: load(os.path.join(R, "keys", f"{b}.json")) for b in BIZ}
numq = {b: [i for i, q in enumerate(keys[b]["eval_questions"]) if isinstance(q["answer"], (int, float)) and not isinstance(q["answer"], bool)] for b in BIZ}
mech = dict(numeric_questions_total=sum(len(v) for v in numq.values()),
            businesses_with_numeric_q=sum(1 for v in numq.values() if v),
            numeric_q_per_business_counts=dict(collections.Counter(len(v) for v in numq.values())))
rows = []
for p in pairs:
    b = p["business"]
    for i in numq[b]:
        k = float(keys[b]["eval_questions"][i]["answer"])
        rows.append(dict(business=b, model=p["model"], rep=p["rep"], q=i,
                         mech_brain=hit(p["ans"]["brain"], k), mech_none=hit(p["ans"]["none"], k),
                         j1_brain=p["plain_brain_j1"][i], j1_none=p["plain_none_j1"][i],
                         j2_brain=p["plain_brain_j2"][i], j2_none=p["plain_none_j2"][i]))
mech["items"] = len(rows)
for f in ("mech", "j1", "j2"):
    mech[f"acc_brain_{f}"] = acc([r[f"{f}_brain"] for r in rows])
    mech[f"acc_none_{f}"] = acc([r[f"{f}_none"] for r in rows])
for jn in ("j1", "j2"):
    agree = sum(r[f"mech_{c}"] == r[f"{jn}_{c}"] for r in rows for c in ("brain", "none"))
    mech[f"agreement_with_{jn}"] = agree / (2 * len(rows))
    mech[f"mech_true_judge_false_{jn}"] = sum(r[f"mech_{c}"] and not r[f"{jn}_{c}"] for r in rows for c in ("brain", "none"))
    mech[f"mech_false_judge_true_{jn}"] = sum((not r[f"mech_{c}"]) and r[f"{jn}_{c}"] for r in rows for c in ("brain", "none"))
# business-level wilcoxon on mechanical accuracy difference (numeric questions only)
by = collections.defaultdict(list)
for r in rows:
    by[r["business"]].append(r)
per = {b: acc([r["mech_brain"] for r in v]) - acc([r["mech_none"] for r in v]) for b, v in by.items()}
vals = np.array(list(per.values()))
mech["business_level"] = dict(businesses=len(vals), mean=float(vals.mean()), median=float(np.median(vals)),
                              positive=int((vals > 0).sum()), negative=int((vals < 0).sum()), zero=int((vals == 0).sum()),
                              wilcoxon=wil(vals), boot_ci_95=boot(vals))
perj = {b: acc([r["j1_brain"] for r in v]) - acc([r["j1_none"] for r in v]) for b, v in by.items()}
vj = np.array(list(perj.values()))
mech["business_level_judge1_same_items"] = dict(mean=float(vj.mean()), positive=int((vj > 0).sum()), wilcoxon=wil(vj))
mbm = collections.defaultdict(list)
for r in rows:
    mbm[r["model"]].append(r)
mech["per_model"] = {m: dict(items=len(v), brain=acc([r["mech_brain"] for r in v]), none=acc([r["mech_none"] for r in v])) for m, v in mbm.items()}
bo = sum(r["mech_brain"] and not r["mech_none"] for r in rows)
no = sum(r["mech_none"] and not r["mech_brain"] for r in rows)
mech["mcnemar"] = dict(brain_only=bo, none_only=no, p_exact=float(stats.binomtest(bo, bo + no, 0.5).pvalue) if bo + no else None)
sens["d_mechanical"] = mech
res["sensitivity"] = sens

# write per-pair table for the second model family (no answers, no key data)
with open(os.path.join(OUT, "codex_in", "pairs.csv"), "w") as f:
    f.write("business,model,rep,j1_none,j1_brain,j2_none,j2_brain\n")
    for p in pairs:
        f.write(f"{p['business']},{p['model']},{p['rep']},{p['score_none_j1']},{p['score_brain_j1']},{p['score_none_j2']},{p['score_brain_j2']}\n")

# strip answers before dumping
json.dump(res, open(os.path.join(OUT, "mine.json"), "w"), indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o))
print("pairs", len(pairs))
