"""Shared conservative flavour search; suggestions never select an ingredient."""
from difflib import SequenceMatcher
from functools import lru_cache
from html import unescape
import re
from config.canonical_library_brands import canonical_library_brand

_ALIASES = {
    'the flavour apprentice':'tfa', 'the flavor apprentice':'tfa',
    'flavor apprentice':'tfa', 'tpa':'tfa', 'capella':'cap',
    'flavorah':'flv', 'flavour art':'fa', 'flavourart':'fa', 'flavor art':'fa', 'flavorart':'fa',
    'flavor west':'fw', 'flavour west':'fw', 'inawera':'inw',
    'one on one':'ooo', 'real flavors':'rf', 'real flavours':'rf', 'liquid barn':'lb',
    'jungle flavors':'jf', 'jungle flavours':'jf', 'wonder flavours':'wf', 'wonder flavors':'wf',
}

@lru_cache(maxsize=40000)
def words(value):
    text = re.sub(r'[^\w]+', ' ', unescape(str(value or '')).casefold()).strip()
    for alias, short in _ALIASES.items():
        text = re.sub(r'\b'+re.escape(alias)+r'\b', short, text)
    return text

@lru_cache(maxsize=60000)
def search_score(brand, name, query, fuzzy=0):
    q = words(str(query or '')[:120])
    if not q: return 0
    n = words(name)
    b = words(canonical_library_brand(str(brand or ''), str(name or '')))
    candidate = n+' '+b
    tokens = q.split()[:8]
    available = candidate.split()
    def direct(t):
        return any(t == w if any(c.isdigit() for c in t) else t in w for w in available)
    if all(direct(t) for t in tokens):
        return 100 if q == n else 90 if n.startswith(q) else 80
    if not fuzzy or len(q)<4: return 0
    for token in tokens:
        if direct(token): continue
        if len(token)<4 or any(c.isdigit() for c in token): return 0
        if not any(not any(c.isdigit() for c in w) and abs(len(token)-len(w))<=2
                   and SequenceMatcher(None,token,w).ratio()>=0.80 for w in available):
            return 0
    return 40
