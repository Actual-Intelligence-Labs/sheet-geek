import json, re, sys
sys.path.insert(0,"/private/tmp/claude-501/-Users-zachkellman-ai-os/d9f6c33d-3142-469c-a96b-b93b58b87ba6/scratchpad/audit")
from scan import texts_claude, texts_codex, KW, R
for key in sys.argv[1:]:
    p=R+"/trials/"+key; r=json.load(open(p)); log=p[:-5]+f".try{r['tries']}.log"
    cmds,outs=(texts_codex(log) if r["model"]=="luna" else texts_claude(log))
    print("=====",key,"root",r["root"])
    for i,c in enumerate(cmds):
        if KW.search(c) or "/tmp/claude" in c:
            print("CMD:",c[:600])
            # show corresponding output head for claude
            if r["model"]!="luna" and i < len(outs): print("  OUT:",outs[i][:400].replace("\n"," | "))
