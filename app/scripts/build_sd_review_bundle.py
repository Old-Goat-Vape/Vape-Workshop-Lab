"""Convert browser-collected public SD DOM records; never fetches a site or uses credentials.

Usage: python build_sd_review_bundle.py INDEX.json SERIES.json OUTPUT.json
The output is a plain review manifest suitable for validation before packaging.
Name/header disagreements remain empty and require source review.
"""
import sys
import json,re,hashlib
from pathlib import Path
if len(sys.argv)!=4:
    raise SystemExit('Usage: build_sd_review_bundle.py INDEX.json SERIES.json OUTPUT.json')
idx=json.loads(Path(sys.argv[1]).read_text())
series=json.loads(Path(sys.argv[2]).read_text())
by={}
for item in series:
    dst=by.setdefault(item['url'],{'posts':[]})
    known={p['post_id']:p for p in dst['posts']}
    known.update({p['post_id']:p for p in item['posts'] if p['author']=='SessionDrummer'})
    dst['posts']=list(known.values())
def norm(s): return re.sub(r'[^a-z0-9]','',s.casefold())
def splittext(text,name):
    # Exact flavour header, followed by source manufacturer and testing percentage.
    pat=re.compile(r'(?mi)^\s*\)?\s*'+re.escape(name)+r'\s*\([^\n]{1,130}?\)\s*[^\n]{0,30}?\d+(?:\.\d+)?\s*%')
    m=pat.search(text)
    if not m:return None
    tail=text[m.start():]
    nexthead=re.search(r'(?m)\n\s*[^\n]{1,140}\([^\n]{1,80}\)\s*[\d./ ]+%\s*\(',tail[1:])
    return tail[:nexthead.start()+1].strip() if nexthead else tail.strip()
records=[];missing=[]
for row in idx:
    url=row['source_url'];base=re.sub(r'/\d+$','',url);num=url.rsplit('/',1)[1] if re.search(r'/\d+$',url) else None
    posts=by.get(base,{}).get('posts',[])
    post=next((p for p in posts if p['number']==num and p['author']=='SessionDrummer'),None)
    text=None
    if post and norm(post['text'].lstrip(') ').split('(')[0])==norm(row['flavour']):text=post['text'].strip()
    if not text:
        for p in posts:
            if p['author']!='SessionDrummer':continue
            text=splittext(p['text'],row['flavour'])
            if text:break
    if not text:missing.append(row)
    records.append({**row,'reviewer':'SessionDrummer','review_text':text or '', 'source_key':hashlib.sha256((url+'|'+row['code']+'|'+row['flavour']).encode()).hexdigest()})
Path(sys.argv[3]).write_text(json.dumps(records,ensure_ascii=False))
print('reviews with verified text',sum(bool(r['review_text']) for r in records),'pending',len(missing))
print('missing examples',[(r['code'],r['flavour']) for r in missing[:15]])
