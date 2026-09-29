#!/usr/bin/env python3
"""The pre-registered analysis (evals/PREREG-2026-09-26.md), written before any
answer existed. Reads trials/ and judges/, writes results/results.json,
results/pairs.csv and results/summary.md.

    /usr/bin/python3 analyze.py
"""
import csv
import json
import os
import random
import statistics as st
import sys

from scipy import stats

R = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run_answers as ra  # noqa: E402
import run_judges as rj  # noqa: E402

OUT = os.path.join(R, "results")
SEED = 20260926
N_BOOT = 10000


def load_pairs():
    pairs, dropped = [], []
    brains = json.load(open(os.path.join(R, "brains.json")))
    for b in ra.B:
        if b not in brains:
            dropped.append({"business": b, "why": "no brain passed the gate in 4 builds; left out by the stopping rule"})
            continue
        for m in ra.MODELS:
            for r in ra.REPS:
                recs = {}
                for c in ra.CONDS:
                    p = ra.rec_path(b, m, r, c)
                    recs[c] = json.load(open(p)) if os.path.exists(p) else None
                d = rj.jdir(b, m, r)
                if not all(x and x.get("status") == "ok" for x in recs.values()):
                    why = ("reached outside its folder twice (isolation rule)" if any((x or {}).get("status") == "leak"
                                                                             for x in recs.values()) else "no answer")
                    dropped.append({"business": b, "model": m, "rep": r, "why": why,
                                    "status": {c: (x or {}).get("status", "missing") for c, x in recs.items()}})
                    continue
                js = []
                for w in (1, 2):
                    jp = os.path.join(d, f"judge{w}.json")
                    js.append(json.load(open(jp))["verdict"] if os.path.exists(jp) else None)
                if not all(js):
                    dropped.append({"business": b, "model": m, "rep": r, "why": "judge missing",
                                    "judges": [bool(x) for x in js]})
                    continue
                order = json.load(open(rj.order_path(b, m, r)))
                lab = {order["A"]: "A", order["B"]: "B"}          # cond -> label
                row = {"business": b, "set": ra.B[b]["set"], "model": m, "rep": r}
                for c in ra.CONDS:
                    per = [j[lab[c]] for j in js]
                    row[f"score_{c}_j1"] = per[0]["on_target"] + per[0]["right"] + per[0]["useful"]
                    row[f"score_{c}_j2"] = per[1]["on_target"] + per[1]["right"] + per[1]["useful"]
                    row[f"score_{c}"] = (row[f"score_{c}_j1"] + row[f"score_{c}_j2"]) / 2
                    row[f"mistakes_{c}"] = (len(per[0]["mistakes"]) + len(per[1]["mistakes"])) / 2
                    row[f"plain_{c}_j1"] = [bool(x) for x in per[0]["plain_correct"]]
                    row[f"plain_{c}_j2"] = [bool(x) for x in per[1]["plain_correct"]]
                    row[f"cites_{c}"] = [bool(p["cites_notes_tab"]) for p in per]
                    row[f"saw_brain_{c}"] = bool(recs[c].get("saw_brain_tab") or recs[c].get("read_brain_tab"))
                    row[f"words_{c}"] = len(recs[c]["answer"].split())
                prefs = []
                for j in js:
                    if j["better"] == "tie":
                        prefs.append(0)
                    else:
                        prefs.append(1 if order[j["better"]] == "brain" else -1)
                row["pref_j1"], row["pref_j2"] = prefs
                row["pref"] = sum(prefs) / 2
                row["diff"] = row["score_brain"] - row["score_none"]
                pairs.append(row)
    return pairs, dropped


def wilcoxon(d):
    d = [x for x in d]
    nz = [x for x in d if x != 0]
    if len(nz) < 1:
        return {"n": len(d), "nonzero": 0, "p": None}
    w = stats.wilcoxon(d, zero_method="wilcox", alternative="two-sided")
    w2 = stats.wilcoxon(d, zero_method="pratt", alternative="two-sided")
    return {"n": len(d), "nonzero": len(nz), "W": float(w.statistic), "p": float(w.pvalue),
            "p_pratt": float(w2.pvalue)}


def boot_ci(pairs, key="diff"):
    by = {}
    for p in pairs:
        by.setdefault(p["business"], []).append(p[key])
    names = sorted(by)
    rng = random.Random(SEED)
    means = []
    for _ in range(N_BOOT):
        pick = [rng.choice(names) for _ in names]
        vals = [v for n in pick for v in by[n]]
        means.append(sum(vals) / len(vals))
    means.sort()
    return [means[int(0.025 * N_BOOT)], means[int(0.975 * N_BOOT) - 1]]


def mcnemar(pairs, judge="j1"):
    b = c = both = neither = 0
    for p in pairs:
        for x, y in zip(p[f"plain_brain_{judge}"], p[f"plain_none_{judge}"]):
            if x and not y:
                b += 1
            elif y and not x:
                c += 1
            elif x and y:
                both += 1
            else:
                neither += 1
    n = b + c
    pv = float(stats.binomtest(min(b, c), n, 0.5).pvalue) if n else None
    return {"brain_only": b, "none_only": c, "both": both, "neither": neither, "p_exact": pv}


def sign_test(pairs):
    pos = sum(1 for p in pairs if p["pref"] > 0)
    neg = sum(1 for p in pairs if p["pref"] < 0)
    ties = sum(1 for p in pairs if p["pref"] == 0)
    n = pos + neg
    return {"brain_better": pos, "no_brain_better": neg, "ties_dropped": ties,
            "p_exact": float(stats.binomtest(pos, n, 0.5).pvalue) if n else None}


def block(pairs, name):
    if not pairs:
        return {"name": name, "pairs": 0}
    diffs = [p["diff"] for p in pairs]
    md = [p["mistakes_brain"] - p["mistakes_none"] for p in pairs]
    acc = lambda c, j: sum(sum(p[f"plain_{c}_{j}"]) for p in pairs) / (3 * len(pairs))  # noqa: E731
    return {
        "name": name, "pairs": len(pairs),
        "score_brain_mean": st.mean(p["score_brain"] for p in pairs),
        "score_none_mean": st.mean(p["score_none"] for p in pairs),
        "diff_mean": st.mean(diffs), "diff_median": st.median(diffs),
        "wilcoxon": wilcoxon(diffs),
        "boot_ci_95": boot_ci(pairs) if len({p["business"] for p in pairs}) > 1 else None,
        "plain_acc_brain_j1": acc("brain", "j1"), "plain_acc_none_j1": acc("none", "j1"),
        "plain_acc_brain_j2": acc("brain", "j2"), "plain_acc_none_j2": acc("none", "j2"),
        "mcnemar_j1": mcnemar(pairs, "j1"), "mcnemar_j2": mcnemar(pairs, "j2"),
        "mistakes_brain_mean": st.mean(p["mistakes_brain"] for p in pairs),
        "mistakes_none_mean": st.mean(p["mistakes_none"] for p in pairs),
        "mistakes_wilcoxon": wilcoxon(md),
        "preference": sign_test(pairs),
        "wins_by_score": {"brain": sum(d > 0 for d in diffs), "none": sum(d < 0 for d in diffs),
                          "tie": sum(d == 0 for d in diffs)},
    }


def main():
    os.makedirs(OUT, exist_ok=True)
    pairs, dropped = load_pairs()
    H = [p for p in pairs if p["set"] == "heldout"]
    D = [p for p in pairs if p["set"] == "dev"]
    res = {"primary_heldout": block(H, "held-out (primary)"), "dev": block(D, "development (secondary)"),
           "all": block(pairs, "all businesses run (descriptive)"),
           "per_model_heldout": {m: block([p for p in H if p["model"] == m], m) for m in ra.MODELS},
           "per_business": {b: block([p for p in pairs if p["business"] == b], b) for b in ra.B
                            if any(p["business"] == b for p in pairs)},
           "dropped": dropped}
    s1 = [p[f"score_{c}_j1"] for p in pairs for c in ra.CONDS]
    s2 = [p[f"score_{c}_j2"] for p in pairs for c in ra.CONDS]
    if len(s1) > 2:
        rho = stats.spearmanr(s1, s2)
        res["judge_agreement"] = {"spearman_rho": float(rho.correlation), "p": float(rho.pvalue), "n": len(s1)}
        agree = sum(a == b for p in pairs for c in ra.CONDS for a, b in zip(p[f"plain_{c}_j1"], p[f"plain_{c}_j2"]))
        res["plain_grade_agreement"] = agree / (len(pairs) * 2 * 3)
        res["preference_agreement"] = sum(p["pref_j1"] == p["pref_j2"] for p in pairs) / len(pairs)
    res["blinding"] = {
        "answers_citing_notes_tab_brain": sum(any(p["cites_brain"]) for p in pairs),
        "answers_citing_notes_tab_none": sum(any(p["cites_none"]) for p in pairs),
        "brain_runs_that_saw_the_tab": sum(p["saw_brain_brain"] for p in pairs), "pairs": len(pairs)}
    json.dump(res, open(os.path.join(OUT, "results.json"), "w"), indent=1)
    cols = ["business", "set", "model", "rep", "score_none", "score_brain", "diff", "score_none_j1", "score_brain_j1",
            "score_none_j2", "score_brain_j2", "mistakes_none", "mistakes_brain", "pref_j1", "pref_j2",
            "plain_none_j1", "plain_brain_j1", "plain_none_j2", "plain_brain_j2", "saw_brain_brain",
            "words_none", "words_brain"]
    with open(os.path.join(OUT, "pairs.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for p in pairs:
            w.writerow([json.dumps(p[c]) if isinstance(p[c], list) else p[c] for c in cols])
    P = res["primary_heldout"]
    lines = ["# Results", ""]
    if P.get("pairs"):
        lines += [f"Primary (held-out, {P['pairs']} pairs): open-request score {P['score_none_mean']:.2f} without "
                  f"the brain vs {P['score_brain_mean']:.2f} with it (0 to 15). Mean difference "
                  f"{P['diff_mean']:+.2f}, median {P['diff_median']:+.2f}, 95% CI (business bootstrap) "
                  f"{P['boot_ci_95']}. Wilcoxon signed-rank two-sided p = {P['wilcoxon'].get('p')}."]
    print("\n".join(lines))
    print(json.dumps({k: res[k] for k in ("primary_heldout", "dev")}, indent=1)[:6000])
    open(os.path.join(OUT, "summary.md"), "w").write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
