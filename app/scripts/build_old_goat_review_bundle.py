"""Package readable Drive review documents without rewriting their prose.

Usage: python build_old_goat_review_bundle.py SOURCE_TEXTS.json OUTPUT.json
SOURCE_TEXTS contains Drive metadata plus the original readable text as content.
A missing/contradictory review heading stops packaging; never infer from a filename.
"""
import base64
import gzip
import hashlib
import json
from pathlib import Path
import re
import sys

LABELS={'FA':'Flavour Art','CAP':'Capella','CAP (SL)':'Capella Silverline',
        'FLV':'Flavorah','FE':'Flavor Express','FW':'Flavor West','INW':'Inawera',
        'LB':'Liquid Barn','SSA':'Sobucky Super Aromas','TFA':'The Flavor Apprentice'}
MARKER=re.compile(r'\((CAP|FA|FLV|FE(?: 100%)?|FW|INW|LB|SSA|TFA)\)')


def heading_identity(heading):
    marker=MARKER.search(heading)
    if marker:
        brand=marker.group(1).split()[0]
        before=heading[:marker.start()].strip()
        if before in ('Flavour Art','Flavor Art'):
            name=heading[marker.end():].strip()
        else:
            name=re.sub(r'\s+by\s+[^\n]+$','',before,flags=re.I)
            name=re.sub(r'\s*[—–-]\s*Flavour Art\s*$','',name,flags=re.I)
        if brand=='CAP' and re.search(r'\bSilver Line\b',heading[marker.end():],re.I):brand='CAP (SL)'
    elif heading.startswith('FA '):
        brand='FA';name=re.split(r'\s+[—–]\s+',heading[3:],maxsplit=1)[0]
    elif heading.startswith('Flavour Art – '):
        brand='FA';name=heading[len('Flavour Art – '):]
    else: raise ValueError('No explicit flavour/manufacturer review heading: '+heading)
    if not name.strip():raise ValueError('Missing flavour name: '+heading)
    return brand,name.strip()


def build(documents):
    records=[];excluded=[]
    for doc in documents:
        text=doc['content'];heading=text.split('\n',1)[0].strip()
        # This document was inspected and contains only site planning, not a review.
        if doc['id']=='1EsuAN7Cm_w64dDH8mAiXzQhaqcBFpv6q':
            excluded.append({'source_id':doc['id'],'filename':doc['title'],'reason':'Contains forum-planning notes, not a flavour review.'})
            continue
        brand,name=heading_identity(heading)
        records.append(dict(source_key='old-goat-drive:'+doc['id'],reviewer='Old Goat',
            code=brand,manufacturer=LABELS[brand],flavour=name,source_url=doc['url'].split('?')[0],
            review_text=text,source_id=doc['id'],source_filename=doc['title'],
            source_modified_at=doc.get('modified_time'),content_sha256=hashlib.sha256(text.encode()).hexdigest()))
    return dict(source='https://drive.google.com/drive/folders/1NcEtMMfDLE0IRyA_UL83Vz4THkPpL4ac',
        reviewer='Old Goat',collected_on='2026-11-13',permission='Author explicitly requested importing his Drive flavour reviews into TVW.',
        documents_read=len(documents),records=len(records),with_text=len(records),excluded_documents=excluded,
        coverage_note='Includes all 59 documents returned by the accessible main/manufacturer folder listings. The main listing returned exactly 50 documents without pagination; complete folder coverage is not confirmed.',
        encoding='gzip+base64 JSON UTF-8',gzip_base64=base64.b64encode(gzip.compress(json.dumps(records,ensure_ascii=False).encode(),mtime=0)).decode())


if __name__=='__main__':
    if len(sys.argv)!=3:raise SystemExit(__doc__)
    bundle=build(json.loads(Path(sys.argv[1]).read_text()))
    Path(sys.argv[2]).write_text(json.dumps(bundle,indent=2)+'\n')
    print('Packaged',bundle['records'],'reviews; excluded',len(bundle['excluded_documents']),'non-review document(s).')
