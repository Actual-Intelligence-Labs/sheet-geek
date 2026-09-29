from load import *
from scipy import stats
import statistics as st
P = {(p["b"],p["m"],p["r"]):p for p in pairs()}
strict_clean=[("inventory","haiku",2),("inventory","luna",1),("inventory","luna",2),("payroll","haiku",2),("payroll","luna",2),("orders","haiku",2)]
attrib_only=[("inventory","haiku",1),("payroll","haiku",1),("payroll","luna",1),("orders","luna",1),("orders","luna",2),("rentroll","haiku",1),("rentroll","luna",1),("rentroll","luna",2)]
def rep(name,keys):
    d=[P[k]["diff"] for k in keys]
    w=stats.wilcoxon(d) if any(d) else None
    ex=stats.wilcoxon(d,method="exact") if any(d) else None
    j1=[P[k]["s_brain_j"][0]-P[k]["s_none_j"][0] for k in keys]; j2=[P[k]["s_brain_j"][1]-P[k]["s_none_j"][1] for k in keys]
    print(name,len(d),"diffs",d,"mean",round(st.mean(d),3),"median",st.median(d),"p",w.pvalue, "exact p", ex.pvalue,"sign test",stats.binomtest(sum(x>0 for x in d),sum(x!=0 for x in d)).pvalue, "j1 mean",st.mean(j1),"j2 mean",st.mean(j2))
    pn=[sum(P[k]["plain_none"][0]) for k in keys]; pb=[sum(P[k]["plain_brain"][0]) for k in keys]
    print("   plain j1 correct none",sum(pn),"brain",sum(pb))
rep("STRICT no-cite (my reading)",strict_clean)
rep("STRICT minus inventory luna r2",[k for k in strict_clean if k!=("inventory","luna",2)])
rep("no explicit tab/notes mention (attrib allowed)",strict_clean+attrib_only)
# judges' own flags: neither judge flags either answer
jc=[k for k,p in P.items() if not any(p["cites_brain"]) and not any(p["cites_none"])]
rep("judge flags none",sorted(jc))
print(sorted(set(jc)-set(strict_clean+attrib_only)), sorted(set(strict_clean+attrib_only)-set(jc)))
# complementary: citing pairs
cite=[k for k in P if k not in strict_clean]
rep("pairs with any cite (complement of strict)",cite)
# within haiku+luna only, to separate model from citation
hl=[k for k in P if k[1] in ("haiku","luna")]
rep("haiku+luna all",hl)
rep("haiku+luna citing",[k for k in hl if k not in strict_clean])
so=[k for k in P if k[1] in ("sonnet","opus")]
rep("sonnet+opus all (all brain answers cite)",so)
