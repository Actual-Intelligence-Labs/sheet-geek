exec(open('verify.py').read().split('# sanity: are there judge dirs')[0])
import collections
g=collections.defaultdict(list)
for p in pairs:
    t=load(os.path.join(R,'trials',p['business'],p['model'],f"r{p['rep']}",'brain.json'))
    g[(p['model'],t.get('read_brain_tab'))].append(p['diff'])
for k,v in sorted(g.items(),key=str): print(k,len(v),round(float(np.mean(v)),3))
# primary restricted to pairs whose brain run read the tab
