"""Attributed imported reference reviews, separate from community ratings and private tests."""
import base64
import gzip
import hashlib
import json
import re
from urllib.parse import urlsplit
from pathlib import Path
from db import get_db
from config.catalogue_identity import canonical_item

SCHEMA = '''CREATE TABLE IF NOT EXISTS expert_flavour_reviews (
 source_key TEXT PRIMARY KEY, reviewer TEXT NOT NULL, manufacturer TEXT NOT NULL,
 flavour_name TEXT NOT NULL, source_url TEXT NOT NULL, review_text TEXT NOT NULL DEFAULT '',
 flavour_id INTEGER REFERENCES flavours(id), match_status TEXT NOT NULL,
 match_brand TEXT NOT NULL, testing_header TEXT NOT NULL DEFAULT '', imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_expert_review_flavour ON expert_flavour_reviews(flavour_id);
CREATE TABLE IF NOT EXISTS expert_review_imports (
 bundle_hash TEXT PRIMARY KEY, imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 record_count INTEGER NOT NULL
);'''

# Manufacturer aliases only. Series/versions in flavour names remain significant.
BRANDS = {
 'fa':'FA','flavour art':'FA','flavourart':'FA','flavor art':'FA',
 'inawera':'INW','inw':'INW','flv':'FLV','flavorah':'FLV',
 'wonder flavors':'WF','wonder flavours':'WF','wf':'WF',
 'sobucky super aromas':'SSA','ssa':'SSA','tfa':'TFA','tpa':'TFA','the flavor apprentice':'TFA',
 'jf':'JF','jungle flavors':'JF','jungle flavours':'JF','flavor jungle':'JF','flavorjungle':'JF',
 'vta':'VT','vt':'VT','vape train australia':'VT',
 'pur':'PUR','purilum':'PUR','lb':'LB','liquid barn':'LB',
 'mol':'MOL','mb':'MOL','molinberry':'MOL','mf':'MF','medicine flower':'MF',
 'ooo':'OOO','one on one':'OOO','del':'Delosi','delosi':'Delosi',
 'cnv':'Chemnovatic','chemnovatic':'Chemnovatic','duo':'Duomei','duomei':'Duomei',
 'diyfs':'DIY Flavor Shack','diy flavor shack':'DIY Flavor Shack',
 'fe':'FE','flavor express':'FE','flavour express':'FE',
 'bcf':'Bull City Flavors','bull city flavors':'Bull City Flavors',
 'far':'Flavor Artists','flavor artists':'Flavor Artists',
 'odf':'One Drop Flavors','one drop flavors':'One Drop Flavors',
 'osdiy':'One Stop Diy Shop','one stop diy shop':'One Stop Diy Shop',
 'nf':'Natures Flavors','natures flavors':'Natures Flavors',
 'vso':'VSO Flavors','vso flavors':'VSO Flavors',
 'fvz':'Flavrz','flavrz':'Flavrz',
 'ap':'Apex','apex flavors (silvercloud)':'Apex','sterling cloud':'Apex','apex':'Apex',
 'rfsc':'Real Flavors SC','real flavors sc':'Real Flavors SC',
 'nomz':'NomNomz Super Concentrates','nomnomz super concentrates':'NomNomz Super Concentrates',
}
SOURCE_BRANDS = {'CAP SL':'CAP (SL)','FA PAZZO':'Flavour Art FANA (Pazzo)',
 'FJU':'JF','FJU II':'JF','JF OS':'Jungle Flavors One Shots',
 'DIYFC':'DIY Flavor Chaser One Shots','FLVRZ':'FLAVRZ Flavor Shots',
 'FLVRZ II':'FLAVRZ One Shots','GREM':'Gremlin','RSD':'Ricks',
 'SB':'SmokyBlue One Shots','TOC':'Toc Flavors','VK':'The Vape Kitchen',
 'FL':'Flavor Laboratories','NR':'Nicotine River'}


def identity(brand,name):
    brand = BRANDS.get(str(brand or '').strip().casefold(),str(brand or '').strip())
    # Explicit SC in a catalogue name can disambiguate the Real Flavors line.
    if brand.casefold()=='real flavors' and re.search(r'\(sc\)',name,re.I):
        brand='Real Flavors SC'; name=re.sub(r'\s*\(sc\)','',name,flags=re.I)
    return canonical_item(brand,name)[2]


def source_brand(row):
    code=row['code']
    if code in SOURCE_BRANDS:return SOURCE_BRANDS[code]
    return BRANDS.get(re.sub(r' (?:II|III|IV)$','',code).casefold(),re.sub(r' (?:II|III|IV)$','',code))


def catalogue_index(db):
    index={}
    for r in db.execute("""SELECT id,brand,canonical_name FROM flavours
        WHERE COALESCE(merged_into_flavour_id,0)=0 AND COALESCE(is_hidden,0)=0
        AND COALESCE(is_invalid,0)=0 AND LOWER(COALESCE(moderation_status,'active'))='active'
        AND LOWER(COALESCE(category,'Flavor')) IN ('flavor','flavour')"""):
        key=identity(r['brand'],r['canonical_name'])
        if key:index.setdefault(key,[]).append(r['id'])
    return index


def import_records(records):
    """Local trusted bundle only; duplicate source identities never multiply votes."""
    db=get_db(); index=catalogue_index(db)
    counts={'matched':0,'unmatched':0,'ambiguous':0,'source_review_needed':0}
    for row in records:
        parsed=urlsplit(row['source_url'])
        if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError('Review sources must be HTTP/HTTPS URLs without credentials.')
        brand=source_brand(row)
        candidates=index.get(identity(brand,row['flavour']),[])
        text=row.get('review_text','')
        status=('source_review_needed' if not text else 'matched' if len(candidates)==1 else 'ambiguous' if candidates else 'unmatched')
        fid=candidates[0] if status=='matched' else None
        header=text.split('\n',1)[0].split(' – ',1)[0].split(' — ',1)[0][:500] if text else ''
        db.execute('''INSERT OR IGNORE INTO expert_flavour_reviews
            (source_key,reviewer,manufacturer,flavour_name,source_url,review_text,flavour_id,match_status,match_brand,testing_header)
            VALUES(?,?,?,?,?,?,?,?,?,?)''',(row['source_key'],row['reviewer'],row['manufacturer'],row['flavour'],row['source_url'],text,fid,status,brand,header))
        counts[status]+=1
    db.commit()
    return counts


def import_bundled_once():
    path=Path(__file__).resolve().parents[1]/'data'/'flavour_reviews'/'sd-2026-11-13.json'
    if not path.exists():return None
    raw=path.read_bytes(); digest=hashlib.sha256(raw).hexdigest();db=get_db()
    if db.execute('SELECT 1 FROM expert_review_imports WHERE bundle_hash=?',(digest,)).fetchone():return None
    bundle=json.loads(raw)
    records=json.loads(gzip.decompress(base64.b64decode(bundle['gzip_base64'])))
    result=import_records(records)
    db.execute('INSERT INTO expert_review_imports(bundle_hash,record_count) VALUES(?,?)',(digest,len(records)))
    db.commit()
    return result


def for_flavour(flavour_id):
    return [dict(r) for r in get_db().execute("SELECT * FROM expert_flavour_reviews WHERE flavour_id=? AND match_status='matched' ORDER BY reviewer,source_key",(flavour_id,))]


def report(search='',page=1):
    db=get_db()
    counts={r['match_status']:r['n'] for r in db.execute('SELECT match_status,COUNT(*) n FROM expert_flavour_reviews GROUP BY match_status')}
    where="WHERE match_status!='matched'";args=[]
    if search:
        where+=' AND (manufacturer LIKE ? OR flavour_name LIKE ?)';args=['%'+search+'%']*2
    total=db.execute('SELECT COUNT(*) FROM expert_flavour_reviews '+where,args).fetchone()[0]
    rows=[dict(r) for r in db.execute('SELECT * FROM expert_flavour_reviews '+where+' ORDER BY manufacturer,flavour_name LIMIT 50 OFFSET ?',args+[(page-1)*50])]
    return counts,rows,total


def reconcile_unmatched():
    """Recheck exact identities after catalogue additions; no fuzzy or source corrections."""
    db=get_db();index=catalogue_index(db);changed=0
    for row in db.execute("SELECT source_key,match_brand,flavour_name FROM expert_flavour_reviews WHERE match_status IN ('unmatched','ambiguous')").fetchall():
        ids=index.get(identity(row['match_brand'],row['flavour_name']),[])
        status='matched' if len(ids)==1 else 'ambiguous' if ids else 'unmatched'
        db.execute('UPDATE expert_flavour_reviews SET flavour_id=?,match_status=? WHERE source_key=?',
            (ids[0] if status=='matched' else None,status,row['source_key']))
        changed+=status=='matched'
    db.commit()
    return changed
