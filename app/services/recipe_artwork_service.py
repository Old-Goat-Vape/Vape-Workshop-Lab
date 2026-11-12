"""Display-only artwork selection. Uploaded photos always take precedence."""
import json
from flask import url_for

PROFILE_ART = {
    'Fruit':'fruit', 'Berry':'fruit', 'Citrus':'fruit', 'Tropical':'fruit',
    'Dessert':'dessert', 'Bakery':'bakery', 'Custard':'cream', 'Cream':'cream',
    'Beverage':'beverage', 'Candy':'candy', 'Tobacco':'tobacco', 'Menthol':'menthol',
    'Nut':'nut', 'Spice':'spice',
}
TAG_PROFILES = {k.lower(): k for k in PROFILE_ART}
TAG_PROFILES.update({'fruity':'Fruit','fruits':'Fruit','berries':'Berry','desserts':'Dessert','creamy':'Cream','drinks':'Beverage','drink':'Beverage','mint':'Menthol','nuts':'Nut','spices':'Spice'})
DEFAULT_ART = 'img/dashboard/mixing-bench.webp'

def profile_list(raw):
    if isinstance(raw, (list,tuple)):
        return [p for p in raw if isinstance(p,str) and p in PROFILE_ART]
    if isinstance(raw,str):
        try:
            decoded=json.loads(raw)
            if isinstance(decoded,list): return profile_list(decoded)
        except (ValueError,TypeError):
            pass
    return []

def choose_artwork(profiles=None, tags=None):
    selected=profile_list(profiles)
    if not selected:
        tag_list=tags.split(',') if isinstance(tags,str) else (tags if isinstance(tags,(list,tuple)) else [])
        selected=[TAG_PROFILES[t.strip().lower()] for t in tag_list if isinstance(t,str) and t.strip().lower() in TAG_PROFILES]
    if selected:
        profile=selected[0]
        return {'filename':f'img/recipe-profiles/{PROFILE_ART[profile]}.webp','label':f'{profile} profile artwork','profile':profile,'automatic':True}
    return {'filename':DEFAULT_ART,'label':'TVW workshop artwork','profile':None,'automatic':True}

def recipe_artwork(profiles=None, tags=None, has_image=False, version_id=None):
    if has_image and version_id:
        return {'url':url_for('tvw_recipes.tvw_recipe_image',version_id=version_id),'label':'Mixer photo','profile':None,'automatic':False}
    result=choose_artwork(profiles,tags)
    return {**result,'url':url_for('static',filename=result['filename'])}

def preview_artworks():
    return {**{p:recipe_artwork([p]) for p in PROFILE_ART}, **{tag:recipe_artwork([profile]) for tag,profile in TAG_PROFILES.items()}}
