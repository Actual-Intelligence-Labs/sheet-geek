import json, warnings, sys
warnings.simplefilter("ignore")
import openpyxl
R="/private/tmp/claude-501/-Users-zachkellman-ai-os/d9f6c33d-3142-469c-a96b-b93b58b87ba6/scratchpad/rigor2"
brains=json.load(open(R+"/brains.json")); B=json.load(open(R+"/businesses.json"))
def told(b):
    out=[]
    for f in B[b]["files"]:
        ws=openpyxl.load_workbook(f"{brains[b]['dir']}/{f}",read_only=True)["_brain"]
        rows=list(ws.iter_rows(values_only=True))
        hi=next(i for i,r in enumerate(rows) if r and any(isinstance(x,str) and x.strip()=="statement" for x in r))
        h=[str(x) if x else "" for x in rows[hi]]
        si=h.index("statement"); so=h.index("source"); li=h.index("label")
        for r in rows[hi+1:]:
            if r and len(r)>so and r[so]=="told": out.append((f[:18], r[li], r[si]))
    return out
if __name__=="__main__":
    for b in sys.argv[1:]:
        t=told(b); print(b, len(t))
        for x in t: print(" -",x[0],"|",x[1],"|",x[2])
