import csv, itertools, numpy as np
from scipy import stats
R="/private/tmp/claude-501/-Users-zachkellman-ai-os/d9f6c33d-3142-469c-a96b-b93b58b87ba6/scratchpad/rigor"
rows=[r for r in csv.DictReader(open(f"{R}/results/pairs.csv")) if r["set"]=="heldout"]
d=np.array([float(r["diff"]) for r in rows]); print("pairs",len(d),"mean",d.mean(),"median",np.median(d))
print("wilcoxon default",stats.wilcoxon(d).pvalue, "approx",stats.wilcoxon(d,method="approx").pvalue)
bm={}
for r in rows: bm.setdefault(r["business"],[]).append(float(r["diff"]))
means={b:np.mean(v) for b,v in bm.items()}; print({b:(round(m,4),len(bm[b])) for b,m in means.items()})
x=np.array(list(means.values())); t=stats.ttest_1samp(x,0); print("business t",t.statistic,t.pvalue)
se=x.std(ddof=1)/2; tc=stats.t.ppf(.975,3); print("t CI",x.mean()-tc*se,x.mean()+tc*se, "mean of business means",x.mean())
flips=[np.mean(np.array(s)*x) for s in itertools.product([1,-1],repeat=4)]
print("sign-flip p two-sided", np.mean([abs(f)>=abs(x.mean())-1e-12 for f in flips]))
# exact bootstrap distribution of pooled mean over 4 businesses
bs=list(bm); from collections import Counter
dist=[]
for combo in itertools.product(bs,repeat=4):
    vals=sum((bm[b] for b in combo),[]); dist.append(np.mean(vals))
dist=np.sort(dist); print("exact boot 2.5/97.5",np.quantile(dist,.025),np.quantile(dist,.975),"P(<=0)",np.mean(dist<=0))
# model-level within business: cell means business x model
cm={}
for r in rows: cm.setdefault((r["business"],r["model"]),[]).append(float(r["diff"]))
c=np.array([np.mean(v) for v in cm.values()]); print("16 cells wilcoxon",stats.wilcoxon(c).pvalue,"n",len(c))
# preference and wins per business
for b in bs:
    rr=[r for r in rows if r["business"]==b]
    print(b,"wins",sum(float(r["diff"])>0 for r in rr),"/",len(rr),"mean none",np.mean([float(r["score_none"]) for r in rr]),"brain",np.mean([float(r["score_brain"]) for r in rr]))
# per judge
for j in ("j1","j2"):
    dj=np.array([float(r[f"score_brain_{j}"])-float(r[f"score_none_{j}"]) for r in rows]); print(j,dj.mean(),stats.wilcoxon(dj).pvalue)
