"""Compare the independent plain-question regrade with judge 1 (and judge 2).
Read-only on the rigor tree. Regrade transcribed from the orchestrator's task text:
one string of T/F per (business, model, rep, cond) for q1..q3."""
import json, os, itertools
from scipy.stats import binomtest

R = "/private/tmp/claude-501/-Users-zachkellman-ai-os/d9f6c33d-3142-469c-a96b-b93b58b87ba6/scratchpad/rigor"

RG = """
inventory haiku 1 brain FFF
inventory haiku 1 none FFF
inventory haiku 2 brain FFF
inventory haiku 2 none FFF
inventory luna 1 brain TFF
inventory luna 1 none FFF
inventory luna 2 brain TFF
inventory luna 2 none FFF
inventory opus 1 brain TFF
inventory opus 1 none FFF
inventory opus 2 brain TFF
inventory opus 2 none FFF
inventory sonnet 1 brain TFF
inventory sonnet 1 none FFF
inventory sonnet 2 brain TFF
payroll haiku 1 brain FFF
payroll haiku 1 none FFF
payroll haiku 2 brain FFF
payroll haiku 2 none FFF
payroll luna 1 brain FFF
payroll luna 1 none FFF
payroll luna 2 brain FFF
payroll luna 2 none FFF
payroll opus 1 brain FTF
payroll opus 1 none FFF
payroll opus 2 brain FTF
payroll opus 2 none FFF
payroll sonnet 1 brain FTF
payroll sonnet 1 none FFF
payroll sonnet 2 brain FFF
payroll sonnet 2 none FFF
orders haiku 1 brain FFF
orders haiku 1 none FFF
orders haiku 2 brain FFF
orders haiku 2 none FFF
orders luna 1 brain FFF
orders luna 1 none TFF
orders luna 2 brain FFF
orders luna 2 none FFF
orders opus 1 brain FFF
orders opus 1 none FFF
orders opus 2 brain FFF
orders opus 2 none FFF
orders sonnet 1 brain FFF
orders sonnet 1 none FFF
orders sonnet 2 brain FFF
orders sonnet 2 none FFF
rentroll haiku 1 brain FFF
rentroll haiku 1 none FFF
rentroll haiku 2 brain FFT
rentroll haiku 2 none FFF
rentroll luna 1 brain FFT
rentroll luna 1 none FFF
rentroll luna 2 brain FFT
rentroll luna 2 none FFF
rentroll opus 1 brain TTT
rentroll opus 1 none TFF
rentroll opus 2 brain TTT
rentroll opus 2 none TFF
rentroll sonnet 1 brain TTT
rentroll sonnet 1 none TFF
rentroll sonnet 2 brain TTT
rentroll sonnet 2 none TFF
"""
rg = {}
for line in RG.strip().splitlines():
    b, m, r, c, s = line.split()
    rg[(b, m, int(r), c)] = [ch == "T" for ch in s]
assert sum(len(v) for v in rg.values()) == 189, sum(len(v) for v in rg.values())

biz = json.load(open(f"{R}/businesses.json"))
held = [b for b, v in biz.items() if v["set"] == "heldout"]

def judge_grades(jname):
    out = {}
    for b in held:
        for m in sorted(os.listdir(f"{R}/judges/{b}")):
            for rd in sorted(os.listdir(f"{R}/judges/{b}/{m}")):
                rep = int(rd[1:])
                order = json.load(open(f"{R}/judge_orders/{b}/{m}/{rd}.json"))
                v = json.load(open(f"{R}/judges/{b}/{m}/{rd}/{jname}.json"))["verdict"]
                for lab in ("A", "B"):
                    out[(b, m, rep, order[lab])] = list(v[lab]["plain_correct"])
    return out

j1 = judge_grades("judge1")
j2 = judge_grades("judge2")
units = sorted({k[:3] for k in j1})
print("held-out pairs judged:", len(units))
extra = sorted(set(rg) - set(j1))
print("regrade keys with no judged pair (excluded):", extra)
missing = sorted(set(j1) - set(rg))
print("judged keys missing from regrade:", missing)

def agree(a, b, label):
    keys = sorted(set(a) & set(b))
    n = agr = 0
    both_t = both_f = a_only = b_only = 0
    dis = []
    for k in keys:
        for qi, (x, y) in enumerate(zip(a[k], b[k])):
            n += 1
            if x == y:
                agr += 1
                both_t += x; both_f += (not x)
            else:
                if x: a_only += 1
                else: b_only += 1
                dis.append((k, qi + 1, x, y))
    po = agr / n
    pa = (both_t + a_only) / n; pb = (both_t + b_only) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    kappa = (po - pe) / (1 - pe)
    print(f"\n{label}: n={n} agree={agr} ({po:.4f}) kappa={kappa:.3f} "
          f"TT={both_t} FF={both_f} first-only-T={a_only} second-only-T={b_only}")
    for d in dis:
        print("   disagree:", d)
    return n, agr, po, kappa, dis

def mcnemar(g, label):
    bo = no = bb = nn = 0
    per_q = {}
    for (b, m, r) in units:
        gb, gn = g[(b, m, r, "brain")], g[(b, m, r, "none")]
        for qi in range(3):
            x, y = gb[qi], gn[qi]
            if x and not y: bo += 1; per_q.setdefault((b, qi + 1), [0, 0])[0] += 1
            elif y and not x: no += 1; per_q.setdefault((b, qi + 1), [0, 0])[1] += 1
            elif x and y: bb += 1
            else: nn += 1
    p = binomtest(bo, bo + no, 0.5).pvalue if bo + no else 1.0
    acc_b = sum(sum(g[(b, m, r, "brain")]) for (b, m, r) in units)
    acc_n = sum(sum(g[(b, m, r, "none")]) for (b, m, r) in units)
    print(f"\n{label} McNemar: brain_only={bo} none_only={no} both={bb} neither={nn} "
          f"p={p:.6g}  acc brain {acc_b}/93={acc_b/93:.4f} none {acc_n}/93={acc_n/93:.4f}")
    print("   discordant by (business,q):", per_q)
    return bo, no, bb, nn, p

res = {}
res["agree_rg_j1"] = agree(rg, j1, "regrade vs judge1")
res["agree_rg_j2"] = agree(rg, j2, "regrade vs judge2")
res["agree_j1_j2"] = agree(j1, j2, "judge1 vs judge2")
res["mc_rg"] = mcnemar(rg, "REGRADE")
res["mc_j1"] = mcnemar(j1, "JUDGE1")
res["mc_j2"] = mcnemar(j2, "JUDGE2")

# split agreement by condition
for c in ("brain", "none"):
    a = {k: v for k, v in rg.items() if k[3] == c}
    agree(a, j1, f"regrade vs judge1, cond={c}")

# Business-level view of regrade: per business discordant counts; question-level sign test
from collections import Counter
