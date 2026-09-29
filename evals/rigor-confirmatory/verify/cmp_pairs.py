import csv,json,sys
exec(open('verify.py').read().split('# sanity: are there judge dirs')[0])
mine={(p['business'],p['model'],p['rep']):p for p in pairs}
rows=list(csv.DictReader(open(R+'/results/pairs.csv')))
bad=collections.Counter(); n=0
for r in rows:
    k=(r['business'],r['model'],int(r['rep'])); p=mine[k]; n+=1
    chk={'score_none':p['score_none'],'score_brain':p['score_brain'],'diff':p['diff'],
         'score_none_j1':p['score_none_j1'],'score_brain_j1':p['score_brain_j1'],'score_none_j2':p['score_none_j2'],'score_brain_j2':p['score_brain_j2'],
         'mistakes_none':p['mist_none'],'mistakes_brain':p['mist_brain'],'pref_j1':p['pref_j1'],'pref_j2':p['pref_j2']}
    for c,v in chk.items():
        if abs(float(r[c])-v)>1e-9: bad[c]+=1
    for c in ('plain_none_j1','plain_brain_j1','plain_none_j2','plain_brain_j2'):
        cc=c.split('_'); if_ = json.loads(r[c].lower())
        if if_!=p[f'plain_{cc[1]}_{cc[2]}']: bad[c]+=1
print('rows',n,'mine',len(mine),'mismatches',dict(bad), 'same keys', set(mine)==set((r['business'],r['model'],int(r['rep'])) for r in rows))
# bootstrap seed sensitivity
biz=json.load(open('mine.json'))['primary']['per_business']; x=np.array(list(biz.values()))
lo=[];hi=[]
for s in range(50):
    rng=np.random.default_rng(s); idx=rng.integers(0,12,(10000,12)); m=x[idx].mean(1); lo.append(np.percentile(m,2.5)); hi.append(np.percentile(m,97.5))
print('boot 50 seeds lower range',min(lo),max(lo),'upper range',min(hi),max(hi))
