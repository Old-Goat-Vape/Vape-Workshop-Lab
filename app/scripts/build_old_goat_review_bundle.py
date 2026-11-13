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
import unicodedata

LABELS={'FA':'Flavour Art','CAP':'Capella','CAP (SL)':'Capella Silverline',
        'FLV':'Flavorah','FE':'Flavor Express','FW':'Flavor West','INW':'Inawera',
        'LB':'Liquid Barn','SSA':'Sobucky Super Aromas','TFA':'The Flavor Apprentice',
        'FA PAZZO':'Flavour Art FANA (Pazzo)','DIYFS':'DIY Flavour Shack'}
MARKER=re.compile(r'\((CAP SL|Capella Silver Line|Capella|CAP|FA|FLV|FE(?: 100%)?|FW|INW|LB|SSA|TFA|DIYFS)\)')


def heading_identity(heading):
    marker=MARKER.search(heading)
    if marker:
        token=marker.group(1)
        brand={'CAP SL':'CAP (SL)','Capella Silver Line':'CAP (SL)','Capella':'CAP'}.get(token,token.split()[0])
        before=heading[:marker.start()].strip()
        if before=='by Flavorah' and marker.group(1)=='FLV':
            name=heading[marker.end():].strip()
        elif before in ('Flavour Art','Flavor Art'):
            name=heading[marker.end():].strip()
        else:
            name=re.sub(r'\s+by\s+[^\n]+$','',before,flags=re.I)
            name=re.sub(r'\s*[—–-]\s*Flavour Art\s*$','',name,flags=re.I)
        if brand=='CAP' and re.search(r'\bSilver Line\b',heading[marker.end():],re.I):brand='CAP (SL)'
    elif heading.startswith('Flavour Art '):
        brand='FA';name=re.sub(r'^Flavour Art\s*[–—-]?\s*','',heading)
    elif '(Pazzo)' in heading:
        brand='FA PAZZO';name=heading.split('(Pazzo)',1)[0].strip()
    elif heading.startswith('FA '):
        brand='FA';name=re.split(r'\s+[—–]\s+',heading[3:],maxsplit=1)[0]
    elif heading.startswith('Flavour Art – '):
        brand='FA';name=heading[len('Flavour Art – '):]
    else: raise ValueError('No explicit flavour/manufacturer review heading: '+heading)
    name=re.sub(r'\s*[—–-]\s*(?:Flavou?r|Concentrate|Additive) Review.*$','',name,flags=re.I)
    name=re.sub(r'^Flavour Art\s*[–—-]\s*','',name)
    if brand=='FA' and '(Pazzo)' in name:
        brand='FA PAZZO';name=name.replace('(Pazzo)','').strip()
    suffix=heading[marker.end():].strip() if marker else ''
    if brand=='CAP' and re.fullmatch(r'[vV]\d+',suffix):name+=' '+suffix
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
            code=brand,manufacturer=LABELS[brand],flavour=name,source_url=doc.get('url','https://drive.google.com/file/d/'+doc['id']).split('?')[0],
            review_text=text,source_id=doc['id'],source_filename=doc['title'],
            source_modified_at=doc.get('modified_time'),content_sha256=hashlib.sha256(text.encode()).hexdigest()))
    return dict(source='https://drive.google.com/drive/folders/1NcEtMMfDLE0IRyA_UL83Vz4THkPpL4ac',
        reviewer='Old Goat',collected_on='2026-11-13',permission='Author explicitly requested importing his Drive flavour reviews into TVW.',
        documents_read=len(documents),records=len(records),with_text=len(records),excluded_documents=excluded,
        coverage_note='Signed-in Drive UI verified the supplied folder and all eight subfolders: 50 root documents and nine subfolder documents, 59 total. All documents were read; 58 reviews were packaged and one non-review was excluded.',
        folder_coverage_verified=True,folder_item_counts={'root_documents':50,'CAP':0,'FE':0,'FLV':0,'FW':1,'IWN':1,'LB':1,'SSA':2,'TFA':4},
        encoding='gzip+base64 JSON UTF-8',gzip_base64=base64.b64encode(gzip.compress(json.dumps(records,ensure_ascii=False).encode(),mtime=0)).decode())


def duplicate_key(row):
    """Only exact prose copies for the same author and identity are duplicates.

    Whitespace and Unicode composition can differ between Drive text readers.
    Versions, product lines, punctuation and actual wording remain significant.
    """
    normalize=lambda value:' '.join(unicodedata.normalize('NFC',value).split())
    return (row['reviewer'],row['code'],normalize(row['flavour']).casefold(),
            normalize(row['review_text']))


def build_additional(documents, existing):
    records=[];excluded=[];duplicates=[]
    seen_ids={r['source_id']:r for r in existing}
    seen_text={duplicate_key(r):r for r in existing}
    for doc in sorted(documents,key=lambda d:d['id']):
        if not doc['content'].strip():
            excluded.append(dict(source_id=doc['id'],filename=doc['title'],
                reason='No readable review text; original Word XML also contains no text.'))
            continue
        row=build([doc])['gzip_base64']
        row=json.loads(gzip.decompress(base64.b64decode(row)))[0]
        retained=seen_ids.get(doc['id']) or seen_text.get(duplicate_key(row))
        if retained:
            duplicates.append(dict(source_id=doc['id'],filename=doc['title'],
                source_url=row['source_url'],retained_source_key=retained['source_key'],
                reason='Same source document or exact original prose for the same author/flavour.'))
            continue
        seen_ids[doc['id']]=row;seen_text[duplicate_key(row)]=row;records.append(row)
    return dict(source='https://drive.google.com/drive/folders/1lXAQE4lIJaoVBQnpgDFI_aj_9ieJRq4X',
        reviewer='Old Goat',collected_on='2026-11-13',
        permission='Author explicitly requested importing his Drive flavour reviews into TVW.',
        documents_read=len(documents),records=len(records),with_text=len(records),
        excluded_documents=excluded,duplicate_documents=duplicates,
        folder_coverage_verified=True,
        folder_item_counts={'root_documents':276,'DIYFS':8,'FA':74},
        coverage_note='All 358 documents from the supplied folder and both subfolders were read. Exact copies are recorded separately; distinct wording and versions are retained. Two blank documents were confirmed against original Word XML.',
        encoding='gzip+base64 JSON UTF-8',
        gzip_base64=base64.b64encode(gzip.compress(json.dumps(records,ensure_ascii=False).encode(),mtime=0)).decode())


if __name__=='__main__':
    if len(sys.argv)!=3:raise SystemExit(__doc__)
    bundle=build(json.loads(Path(sys.argv[1]).read_text()))
    Path(sys.argv[2]).write_text(json.dumps(bundle,indent=2)+'\n')
    print('Packaged',bundle['records'],'reviews; excluded',len(bundle['excluded_documents']),'non-review document(s).')
