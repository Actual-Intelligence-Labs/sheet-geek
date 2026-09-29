import json,re,sys,collections
sys.argv=['x']
exec(open('verify.py').read().split('# ---------- 3. sensitivity')[0])
exec(open('verify.py').read().split('# (d) mechanical grading of single-number plain questions')[1].split('mech = dict(')[0])
import glob
diff=0
for f in glob.glob(R+'/judges/*/*/*/judge2.raw.json'):
    if load(f)!=load(f.replace('.raw',''))['verdict']: diff+=1
print('judge2 raw != parsed verdict:', diff)
keys = {b: load(os.path.join(R, "keys", f"{b}.json")) for b in BIZ}
cnt=collections.Counter()
for p in pairs:
    b=p['business']
    for i,q in enumerate(keys[b]['eval_questions']):
        if not isinstance(q['answer'],(int,float)) or isinstance(q['answer'],bool): continue
        k=float(q['answer'])
        for c in ('brain','none'):
            h=hit(p['ans'][c],k); j=p[f'plain_{c}_j1'][i]
            if h and not j:
                cnt[(c,p['model'],b,i)]+=1
                # locate the matching number context
                for m in NUM.finditer(p['ans'][c]):
                    vals=numbers_in(m.group(0))
                    if any(abs(v-k)<=abs(k)*0.01 for v in vals):
                        s=p['ans'][c][max(0,m.start()-90):m.end()+30].replace('\n',' ')
                        print(c,p['model'],b,p['rep'],'q',i+1,'|',s)
                        break
print(cnt)
print(collections.Counter((k[0],k[1]) for k in cnt))
