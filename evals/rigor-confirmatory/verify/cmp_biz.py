exec(open('verify.py').read().split('# sanity: are there judge dirs')[0])
r=json.load(open(R+'/results/results.json'))
bad=[]
for b,v in r['per_business'].items():
    P=[p for p in pairs if p['business']==b]
    mine=dict(pairs=len(P),score_brain_mean=np.mean([p['score_brain'] for p in P]),score_none_mean=np.mean([p['score_none'] for p in P]),
      diff_mean=np.mean([p['diff'] for p in P]),diff_median=np.median([p['diff'] for p in P]),
      plain_acc_brain_j1=np.mean([x for p in P for x in p['plain_brain_j1']]),plain_acc_none_j1=np.mean([x for p in P for x in p['plain_none_j1']]),
      plain_acc_brain_j2=np.mean([x for p in P for x in p['plain_brain_j2']]),plain_acc_none_j2=np.mean([x for p in P for x in p['plain_none_j2']]),
      mistakes_brain_mean=np.mean([p['mist_brain'] for p in P]),mistakes_none_mean=np.mean([p['mist_none'] for p in P]))
    w=stats.wilcoxon([p['diff'] for p in P]).pvalue
    for k,x in mine.items():
        if abs(float(x)-float(v[k]))>1e-9: bad.append((b,k,x,v[k]))
    if abs(w-v['wilcoxon']['p'])>1e-9: bad.append((b,'wil',w,v['wilcoxon']['p']))
    pr=[p['pref_j1'] for p in P]+[p['pref_j2'] for p in P]
print('per_business mismatches:',bad)
