import json, glob, re, collections, sys, os
R="/private/tmp/claude-501/-Users-zachkellman-ai-os/d9f6c33d-3142-469c-a96b-b93b58b87ba6/scratchpad/rigor2"
PATH=re.compile(r"(?<![\w.~$-])(/(?:[\w.@+ -]*[\w.@+-]/?)+)")
SYS=("/usr/","/bin/","/System/","/Library/","/dev/","/opt/homebrew","/sbin/","/usr","/bin")
KW=re.compile(r"rigor2|briefs?/|brief\.txt|keys/|reference\.json|schema\.json|judge|sbj-|claude-501|spreadsheet-brain|/Users/zachkellman(?!/Library/Python|/\.cache/codex-runtimes)|answer_[AB]|\.claude/|\.codex/|mdfind|find /|/private/tmp(?!/sbt)|\.\./\.\.", re.I)
def texts_claude(log):
    cmds=[];outs=[]
    for line in open(log,errors="replace"):
        try: ev=json.loads(line)
        except: continue
        msg=ev.get("message") if isinstance(ev.get("message"),dict) else {}
        for c in msg.get("content") or []:
            if not isinstance(c,dict): continue
            if c.get("type")=="tool_use": cmds.append(json.dumps(c.get("input")))
            if c.get("type")=="tool_result":
                cc=c.get("content"); outs.append(cc if isinstance(cc,str) else json.dumps(cc))
    return cmds,outs
def texts_codex(log):
    t=open(log,errors="replace").read()
    after=t.split("\ncodex\n",1)[-1]
    # commands: line after 'exec'
    lines=after.split("\n"); cmds=[]
    for i,l in enumerate(lines):
        if l=="exec" and i+1<len(lines): cmds.append(lines[i+1])
    return cmds,[after]
agg=collections.Counter(); per={}
for p in sorted(glob.glob(R+"/trials/*/*/r*/*.json")):
    r=json.load(open(p))
    if r["status"]!="ok": continue
    log=p[:-5]+f".try{r['tries']}.log"
    root=r["root"]; own=(root, root.replace("/private","",1))
    cmds,outs=(texts_codex(log) if r["model"]=="luna" else texts_claude(log))
    hits=set()
    for txt in cmds+outs:
        for m in PATH.finditer(txt):
            q=m.group(1).rstrip("/.,;:)'\"")
            if not q or q=="/" or q.startswith(own) or q.startswith(SYS): continue
            hits.add(q)
    kws=set()
    for txt in cmds:
        for m in KW.finditer(txt): kws.add(m.group(0))
    key=p.split("trials/")[1]
    per[key]=(sorted(hits),sorted(kws),len(cmds))
    for h in hits: agg[re.sub(r"sbt-[0-9a-f]+","sbt-X",h)[:90]]+=1
print("ok trials scanned:",len(per))
print("distinct outside paths (count of trials):")
for k,v in agg.most_common(80): print(v,k)
print("command keyword hits:")
for k,(h,kw,n) in per.items():
    if kw: print(k,kw)
print("zero-command trials:",[k for k,(h,kw,n) in per.items() if n==0])
