"""Evidence-backed observations from attributed reviews, never community votes.

Extraction is deterministic and versioned. Test conditions stay distinct from
recommendations; prose that cannot be interpreted numerically stays as prose.
Only reviews with a verified exact catalogue link may inform a selected flavour.
"""
import hashlib
import json
import re
from collections import Counter
from db import get_db

VERSION = 1
SCHEMA = """CREATE TABLE IF NOT EXISTS flavour_review_knowledge (
 source_key TEXT PRIMARY KEY REFERENCES expert_flavour_reviews(source_key) ON DELETE CASCADE,
 content_hash TEXT NOT NULL, extractor_version INTEGER NOT NULL,
 facts_json TEXT NOT NULL, extracted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);"""
LABELS = {
 'tested_percent':'Tested percentage', 'mix_percent':'Recommended in a mix',
 'solo_percent':'Recommended standalone', 'recommended_percent':'Recommended percentage (context unspecified)',
 'tested_steep':'Steep used for testing', 'steep_days':'Steep guidance',
 'strength':'Strength', 'sweetness':'Sweetness', 'off_notes':'Off-notes',
 'texture':'Mouthfeel / texture', 'aroma':'Aroma', 'profile':'Flavour character',
 'pairings':'Pairing suggestions', 'uses':'Uses / recipe roles', 'cautions':'Cautions',
 'rating':'Original reviewer score', 'setup':'Testing setup',
}
NUM = r'(?:\d+(?:\.\d+)?|\.\d+|one|two|three|four|five|six|seven|ten|fourteen)'
WORDS = dict(one=1,two=2,three=3,four=4,five=5,six=6,seven=7,ten=10,fourteen=14)
PERCENT = re.compile(r'(?<![\w.])(\d+(?:\.\d+)?|\.\d+)\s*%?(?:\s*(?:-|–|—|to)\s*(\d+(?:\.\d+)?|\.\d+))?\s*%')
DURATION = re.compile(r'\b('+NUM+r')(?:\s*(?:-|–|—|to)\s*('+NUM+r'))?[\s-]*(days?|weeks?)\b',re.I)
SECTION = re.compile(
 r'^(?:[^\w(]*)(testing setup|aroma(?:[^\n:]*?)|flavour profile(?:[^\n:]*?)|flavour on the vape|'
 r'profile & function|what to expect|what it gets right|what to watch out for|'
 r'standalone usability|mixing versatility|common uses|use case recommendations|'
 r'recommended mixing percentages|steeping|scorecard|final thoughts|where to buy|'
 r'label accuracy|accuracy & enjoyment|enjoyment|overall goat score|final goat score)'
 r'\s*:?\s*$',re.I)
SECTIONS = {'testing setup':'setup','aroma':'aroma','flavour profile':'profile',
 'flavour on the vape':'profile','profile & function':'profile','what to expect':'uses',
 'what it gets right':'uses','what to watch out for':'cautions',
 'standalone usability':'uses','mixing versatility':'uses','common uses':'uses',
 'use case recommendations':'uses'}


def _number(raw):
    return WORDS[raw.lower()] if raw.lower() in WORDS else float(raw)


def extract(text, reviewer=''):
    """Return contextual facts with literal source evidence, without guessing."""
    facts=[];seen=set();section='';section_lines=[];section_kind=None
    def add(kind,evidence,**data):
        key=(kind,evidence,json.dumps(data,sort_keys=True))
        if key in seen:return
        seen.add(key)
        facts.append(dict(kind=kind,label=LABELS[kind],evidence=evidence,
                          basis='explicit text',**data))
    def flush():
        if section_kind and section_lines:
            add(section_kind,'\n'.join(section_lines).strip())
    lines=text.splitlines()
    for i,line in enumerate(lines):
        raw=line.strip()
        if not raw:
            if section_kind:section_lines.append(line)
            continue
        heading=SECTION.match(raw)
        if heading:
            flush();section_lines=[]
            section=heading.group(1).lower()
            section_kind=next((v for k,v in SECTIONS.items() if section.startswith(k)),None)
            continue
        section_lines.append(line)
        clean=re.sub(r'^[^\w(]+','',raw).strip()
        lower=clean.lower()
        # Review header percentages describe testing, not recipe recommendations.
        tested=bool(re.match(r'tested\s+(?:at|@)',lower)) or (i==0 and reviewer=='SessionDrummer')
        context=None
        if re.match(r'(?:in mixes|in a mix|mixing percentage|in recipes)\s*:',lower):context='mix_percent'
        elif re.match(r'(?:standalone|solo)\s*:',lower):context='solo_percent'
        elif re.match(r'recommended (?:mixing )?(?:percentage|usage|range)',lower):context='recommended_percent'
        elif section=='recommended mixing percentages':context='recommended_percent'
        # Only scan explicit percentage contexts. Never read PG/VG, nicotine,
        # comparisons elsewhere in a paragraph, or a rating as a usage range.
        if tested or context:
            segment=re.split(r'[,;]?\s*(?:hellvape|rda:|base ratio:|nicotine:)',clean,flags=re.I)[0]
            if i==0 and reviewer=='SessionDrummer':
                segment=re.split(r'\s+[–—]\s+',segment,maxsplit=1)[0]
            if context and re.search(r"\b(?:not|never|avoid|don't|don’t|maximum|below|under|above)\b",segment,re.I):
                add('cautions',raw)
                segment=''
            for match in PERCENT.finditer(segment):
                low=float(match.group(1));high=float(match.group(2) or low)
                if 0<low<=high<=100:
                    add('tested_percent' if tested else context,raw,low=low,high=high,unit='%')
        # A setup's steep is an observation. "Minimum ..." elsewhere is guidance.
        for sentence in re.split(r'(?<=[.!?])\s+',clean):
            s=sentence.lower()
            steep_context=bool(re.search(r'\b(?:steep(?:ed|ing)?|let it rest|needs? patience|shake.and.vape|shake & vape)\b',s))
            if not (steep_context or section=='steeping'):continue
            directive=bool(re.search(r'\b(?:recommend|minimum|needs?|allow|give it|let it rest|let it steep)\b',s))
            observed=section=='testing setup' or bool(re.search(r'\b(?:steeped|i (?:let|gave|tested)|after|tested|testing)\b',s))
            kind='tested_steep' if observed or (reviewer=='SessionDrummer' and not directive) else 'steep_days'
            if re.search(r"\b(?:not|never|don't|don’t|doesn.t|isn.t)\b[^.!?]{0,35}"+NUM+r"[\s-]*(?:days?|weeks?)",s):
                add('cautions',raw)
                continue
            for match in DURATION.finditer(sentence):
                factor=7 if match.group(3).lower().startswith('week') else 1
                low=_number(match.group(1))*factor;high=_number(match.group(2) or match.group(1))*factor
                if 0<=low<=high<=365:add(kind,raw,low=low,high=high,unit='days')
            if (re.search(r'\b(?:shake\s*(?:and|&|-)\s*vape|shake-and-vape)\b',s)
                    and not re.search(r"\b(?:no|not|isn.t|is not|never|isn’t)\b",s)):
                add(kind,raw,low=0,high=0,unit='days')
        explicit={'sweetness':'sweetness','off-notes':'off_notes','off notes':'off_notes',
                  'mouthfeel':'texture','strength':'strength','aroma':'aroma',
                  'inhale':'profile','exhale':'profile','aftertaste':'profile'}
        for prefix,kind in explicit.items():
            if lower.startswith(prefix+':'):add(kind,raw)
        # Narrative SD reviews don't have fixed section headings. Keep contextual
        # sentences verbatim, including qualifiers/negations, rather than tags.
        for sentence in re.split(r'(?<=[.!?])\s+',raw):
            s=sentence.lower()
            for kind,pattern in [
                ('strength',r'\b(?:strength wise|strength scale|potent|potency|plenty strong|full at|super strong)\b'),
                ('sweetness',r'\bsweetness\b'),
                ('off_notes',r'\b(?:off[- ]notes?|off[- ]putting)\b'),
                ('texture',r'\b(?:mouthfeel|texture)\b'),
                ('aroma',r'\b(?:smells?|smelled|aroma)\b'),
                ('profile',r'\b(?:presented as|tast(?:e|ed)s? like|main notes|flavou?r profile|inhale|exhale|aftertaste)\b'),
                ('uses',r'\b(?:can be used|works in|best suited|main driver|as (?:a|an) .{0,35}(?:mixer|base|blender))\b'),
                ('pairings',r'\b(?:pairs? (?:well|beautifully|with)|plays? well with|works? (?:well|beautifully) with|pair it with)\b'),
                ('cautions',r'\b(?:use sparingly|too much|overpower|dominat|fades?|fading|muting|separat|crystali[sz]|shake the|dry out)\w*'),
            ]:
                if re.search(pattern,s):add(kind,sentence)
        # Preserve scales and dimensions, including section headings on prior lines.
        for m in re.finditer(r'(?<![\d.])(\d+(?:\.\d+)?)\s*/\s*(5|10|100)(?!\d)',clean):
            value=float(m.group(1));scale=int(m.group(2))
            if 0<=value<=scale:
                prefix=clean[:m.start()].strip(' :–—-')
                dimension=prefix if len(prefix)<70 and re.search(r'rating|score|accuracy|enjoyment|strength|versatility',prefix,re.I) else section or 'Unspecified; see evidence'
                add('rating',raw,value=value,scale=scale,dimension=dimension)
    flush()
    return facts


def refresh():
    """Incremental startup extraction also backfills reviews imported earlier."""
    db=get_db();changed=0
    for row in db.execute("""SELECT r.source_key,r.review_text,r.reviewer,k.content_hash,k.extractor_version
        FROM expert_flavour_reviews r LEFT JOIN flavour_review_knowledge k USING(source_key)""").fetchall():
        digest=hashlib.sha256(row['review_text'].encode()).hexdigest()
        if row['content_hash']==digest and row['extractor_version']==VERSION:continue
        facts=extract(row['review_text'],row['reviewer'])
        db.execute("""INSERT INTO flavour_review_knowledge(source_key,content_hash,extractor_version,facts_json)
            VALUES(?,?,?,?) ON CONFLICT(source_key) DO UPDATE SET content_hash=excluded.content_hash,
            extractor_version=excluded.extractor_version,facts_json=excluded.facts_json,extracted_at=CURRENT_TIMESTAMP""",
            (row['source_key'],digest,VERSION,json.dumps(facts,ensure_ascii=False)))
        changed+=1
    db.commit()
    return changed


def _knowledge(rows):
    observations=[];authors=set();sources=set();seen=set()
    for row in rows:
        authors.add(row['reviewer']);sources.add(row['source_key'])
        for fact in json.loads(row['facts_json'] or '[]'):
            # Exact copies by the same author cannot inflate coverage.
            key=(row['reviewer'],json.dumps(dict(fact,evidence=' '.join(fact['evidence'].split())),sort_keys=True))
            if key in seen:continue
            seen.add(key)
            observations.append(dict(fact,reviewer=row['reviewer'],source_url=row['source_url'],
                source_key=row['source_key'],flavour_name=row['flavour_name']))
    steep=[f for f in observations if f['kind']=='steep_days' and 'high' in f]
    groups=[dict(kind=k,label=label,facts=[f for f in observations if f['kind']==k])
            for k,label in LABELS.items() if any(f['kind']==k for f in observations)]
    return dict(review_count=len(sources),reviewer_count=len(authors),groups=groups,
        # Longest explicit guidance is an editable planning suggestion, not a
        # prediction of when a complete recipe will taste ready.
        suggested_steep_days=max((f['high'] for f in steep),default=None),
        steep_sources=steep,observation_count=len(observations))


def for_flavours(flavour_ids):
    ids=sorted(set(flavour_ids))
    if not ids:return _knowledge([])
    from config.catalogue_identity import visible_flavour_sql
    rows=get_db().execute(f"""SELECT r.*,k.facts_json FROM expert_flavour_reviews r
        JOIN flavour_review_knowledge k USING(source_key) JOIN flavours f ON f.id=r.flavour_id
        WHERE r.match_status='matched' AND f.id IN ({','.join('?' for _ in ids)})
        AND {visible_flavour_sql('f')} ORDER BY r.reviewer,r.source_key""",ids).fetchall()
    return _knowledge(rows)


def for_reference(reference_id):
    from config.catalogue_identity import equivalent_recipe_reference_ids,visible_flavour_sql
    db=get_db();refs=equivalent_recipe_reference_ids(db,reference_id)
    if not refs:return _knowledge([])
    ids=[r['id'] for r in db.execute(f"""SELECT f.id FROM flavours f
        WHERE f.source_flavor_ref_id IN ({','.join('?' for _ in refs)})
        AND {visible_flavour_sql('f')}""",refs)]
    return for_flavours(ids)


def coverage():
    db=get_db();counts=Counter();documents=0
    for row in db.execute('SELECT facts_json FROM flavour_review_knowledge'):
        facts=json.loads(row['facts_json']);documents+=bool(facts)
        counts.update({f['kind'] for f in facts})
    return dict(reviews_with_findings=documents,fields=dict(counts),version=VERSION)
