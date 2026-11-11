"""Recipe-preserving recovery. Plans are computed from recorded pours, never client totals.

Two bottles: correct the concentrates in the current vessel, homogenise, split
by measured liquid mass, then finish carriers separately. Do not offer this path
if the concentrate stage cannot fit the original vessel.
"""
from __future__ import annotations
import hashlib
import json
import math
from copy import deepcopy
from db import get_db


class RecoveryError(ValueError):
    pass


def number(value, label):
    if isinstance(value, bool):
        raise RecoveryError(f"Enter a numeric {label}, not a yes/no value.")
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise RecoveryError(f"Enter a valid {label}.")
    if not math.isfinite(value) or value < 0:
        raise RecoveryError(f"{label} must be finite and non-negative.")
    if value > 100000000:
        raise RecoveryError(f"{label} is outside the supported scale range. Check the decimal point.")
    return value


def key(p):
    category=str(p.get('category') or '').lower()
    if category in {'nicotine','pg','vg'}:
        return 'carrier:'+category
    if p.get('ingredient_id') is not None:
        return 'ingredient:'+str(p['ingredient_id'])
    # Older sessions may lack catalogue IDs; preserve their captured label identity.
    return json.dumps([category,p.get('brand') or '',p.get('name') or ''])


def _context(session_id, user_id):
    db = get_db()
    row = db.execute('SELECT * FROM mix_sessions WHERE id=? AND user_id=?', (session_id,user_id)).fetchone()
    if not row:
        raise RecoveryError('Mix session not found.')
    if row['completed_at']:
        raise RecoveryError('This mix is already complete.')
    if row['production_job_id']:
        raise RecoveryError('A production job has locked bottle quantities. Pause and ask the job manager to revise the job before resizing it.')
    meta = json.loads(row['result_json'] or '{}')
    state = meta.get('batch_recovery')
    if state and (state.get('split_done') or state.get('transfer_started')):
        raise RecoveryError('These bottles have already been split. Keep both labelled and pause; do not combine or discard their records.')
    steps = [dict(s) for s in db.execute('SELECT * FROM mix_session_steps WHERE mix_session_id=? ORDER BY step_no', (session_id,))]
    pending = next((s for s in steps if not s['completed_at']), None)
    if not state:
        p = json.loads(pending['payload_json'] or '{}') if pending else {}
        if p.get('source_event') != 'overpour' or p.get('ingredient_category') in ('nicotine','pg','vg'):
            raise RecoveryError('Recipe recovery starts from a paused flavour overpour. Nicotine has its own no-tare correction.')
    original_ml = float(state['original_ml'] if state else row['target_ml'])
    if state:
        ingredients=[{field:deepcopy(i[field]) for field in ('key','payload','original_grams','density')} | {'actual_grams':0.0}
                     for i in state['plan']['ingredients']]
    else:
        ingredients=[]
        captured={}
        baseline = meta.get('recovery_formula') or [json.loads(step['payload_json'] or '{}') for step in steps]
        for p in baseline:
            p=deepcopy(p)
            if p.get('step_type')!='add_ingredient':
                continue
            grams=number(p.get('original_pg_target_grams',p.get('target_grams',p.get('grams'))),'recipe target')
            ml=number(p.get('original_pg_target_ml',p.get('ml')),'recipe volume')
            if grams<=0 or ml<=0:
                continue
            density=grams/ml
            if not math.isfinite(density) or density<=0:
                raise RecoveryError('A captured ingredient density is invalid. Pause and check this recipe.')
            k=key(p)
            if k in captured:
                i=captured[k]
                if not math.isclose(i['density'],density,rel_tol=.001):
                    raise RecoveryError('Repeated entries for one ingredient have conflicting densities.')
                i['original_grams']+=grams
                i['payload']['target_grams']=i['original_grams']
                i['payload']['ml']+=ml
                continue
            p={field:value for field,value in p.items() if field in {'step_type','ingredient_id','name','brand','category','carrier','density','target_grams','grams','ml','pct'}}
            p.update(target_grams=grams,ml=ml)
            item=dict(key=k,payload=p,original_grams=grams,density=density,actual_grams=0.0)
            captured[k]=item
            ingredients.append(item)
        if not meta.get('recovery_formula'):
            composition=meta.get('planned_composition') or {}
            if 'flavor_pg_ml' in composition and 'flavor_vg_ml' in composition:
                expected=float(composition['flavor_pg_ml'])+float(composition['flavor_vg_ml'])
                captured_ml=sum(i['original_grams']/i['density'] for i in ingredients
                                if i['payload'].get('category') not in ('nicotine','pg','vg'))
                if not math.isclose(expected,captured_ml,abs_tol=0.001):
                    raise RecoveryError('This older session did not capture its complete flavour formula. Pause this mix; a proportional rescue cannot safely guess missing ingredients.')
    by_key = {i['key']:i for i in ingredients}
    for step in steps:
        p = json.loads(step['payload_json'] or '{}')
        if p.get('step_type')!='add_ingredient' or not (step['completed_at'] or p.get('overpour_recovery_pending')):
            continue
        measured=p.get('measured_grams')
        if measured is None:
            raise RecoveryError('A completed ingredient has no recorded weight. Correct that record before recovering this batch.')
        k=key(p)
        if k not in by_key:
            raise RecoveryError('An added ingredient is not in this recipe version. A proportional rescue cannot be calculated.')
        by_key[k]['actual_grams'] += number(measured,'recorded weight')
    if state:
        for i in ingredients:
            i['actual_grams'] = number(state['actuals'].get(i['key'],0),'recovery weight')
        if state['status']=='active' and state['cursor']<len(state['tasks']):
            task=state['tasks'][state['cursor']]
            if task['type']=='pour' and task.get('reading') is not None:
                by_key[task['key']]['actual_grams'] += number(task['reading'],'unfinished pour')
    fingerprint=hashlib.sha256(json.dumps([original_ml,[(i['key'],i['original_grams'],i['density'],i['actual_grams']) for i in ingredients],state and state.get('revision')],sort_keys=True).encode()).hexdigest()
    return db,row,meta,steps,ingredients,original_ml,fingerprint


def _build_plan(ingredients,original_ml,mode,new_ml=None):
    minimum = max([original_ml]+[original_ml*i['actual_grams']/i['original_grams'] for i in ingredients])
    if not math.isfinite(minimum) or minimum>original_ml*100 or minimum>100000:
        raise RecoveryError('The recorded overpour needs an impractically large batch. Check the weight and pause the mix.')
    minimum = math.ceil((minimum-1e-9)*10)/10
    if mode=='two_bottles':
        volume = 2*original_ml
        if minimum>volume+1e-7:
            raise RecoveryError(f'Two {original_ml:g} ml bottles are not enough. This mix needs at least {minimum:g} ml.')
    elif mode=='larger_vessel':
        volume = minimum if new_ml is None else number(new_ml,'new batch size')
        if volume<minimum-1e-7:
            raise RecoveryError(f'The corrected batch must be at least {minimum:g} ml.')
    else:
        raise RecoveryError('Choose two bottles or a larger vessel.')
    if volume>original_ml*100 or volume>100000:
        raise RecoveryError('This correction is too large. Pause and review the recorded weights.')
    lines=[]
    for item in ingredients:
        i=deepcopy(item)
        i['total_target_grams']=i['original_grams']*volume/original_ml
        i['add_grams']=max(0,i['total_target_grams']-i['actual_grams'])
        i['display_add_grams']=round(i['add_grams'],2)
        lines.append(i)
    # Carrier that is already in the bottle remains there during concentrate correction.
    partial_ml=sum((i['actual_grams']+(i['display_add_grams'] if i['payload'].get('category') not in ('pg','vg') else 0))/i['density'] for i in lines)
    if mode=='two_bottles' and partial_ml>original_ml+1e-7:
        raise RecoveryError(f'The corrected concentrate stage needs {partial_ml:.2f} ml before splitting, more than the current {original_ml:g} ml bottle. Use a larger mixing vessel; do not overfill it.')
    return dict(mode=mode,original_ml=original_ml,new_ml=volume,minimum_ml=minimum,bottle_count=2 if mode=='two_bottles' else 1,
                bottle_ml=original_ml if mode=='two_bottles' else volume,partial_ml=partial_ml,ingredients=lines,
                caution='Use the same nicotine base strength and carrier. Top-ups below are additional amounts, not full doses. Scale estimates are limited by 0.01 g readings.')


def _composition(plan,row,meta):
    from services import guided_mix_session_service as guided
    total=sum(i['total_target_grams']/i['density'] for i in plan['ingredients'])
    nic=sum(i['total_target_grams']/i['density'] for i in plan['ingredients'] if i['payload'].get('category')=='nicotine')
    pg=sum(i['total_target_grams']/i['density'] for i in plan['ingredients'] if guided._pour_carrier_for_ratio(i['payload'],default_nic_carrier=meta.get('nicotine_carrier') or 'PG')=='PG')
    pg_percent=round(100*pg/total) if total else 0
    plan.update(target_nicotine_mg_ml=float(row['target_nic']),target_ratio=row['target_pgvg'],
                planned_nicotine_mg_ml=round(nic*float(meta.get('nic_base_mg_ml') or 100)/total,3) if total else 0,
                planned_ratio=f'{pg_percent}/{100-pg_percent}')
    return plan


def preview(session_id,user_id,mode,new_ml=None):
    _,row,meta,_,items,original,fingerprint=_context(session_id,user_id)
    result=_composition(_build_plan(items,original,mode,new_ml),row,meta)
    result['fingerprint']=fingerprint
    return result


def _save(db,session_id,meta,state):
    from services import guided_mix_session_service as guided
    state['revision']=int(state.get('revision',0))+1
    meta['batch_recovery']=state
    guided._write_session_meta(db,session_id,meta)
    db.commit()


def apply(session_id,user_id,mode,fingerprint,new_ml=None,capacity_ml=None):
    db=get_db()
    db.execute('BEGIN IMMEDIATE')
    try:
        db,row,meta,steps,items,original,current=_context(session_id,user_id)
        if fingerprint!=current:
            raise RecoveryError('The recorded pours changed. Review a fresh recovery plan before applying it.')
        plan=_composition(_build_plan(items,original,mode,new_ml),row,meta)
        capacity=number(capacity_ml,'mixing vessel capacity')
        required=plan['partial_ml'] if mode=='two_bottles' else plan['new_ml']
        if capacity<required or (mode=='two_bottles' and capacity<original):
            raise RecoveryError(f'Confirm a vessel with at least {required:.2f} ml working capacity and room to mix.')
        previous=meta.get('batch_recovery') or {}
        tasks=[dict(type='prepare_vessel',bottle=0)]
        for i in plan['ingredients']:
            if mode=='two_bottles' and i['payload'].get('category') in ('pg','vg'):
                continue
            if i['display_add_grams']>0:
                tasks.append(dict(type='tare',key=i['key'],bottle=0))
                tasks.append(dict(type='pour',key=i['key'],target_grams=i['display_add_grams'],bottle=0))
                tasks.append(dict(type='move_ingredient',key=i['key'],bottle=0))
        tasks.append(dict(type='mix',bottle=0))
        if mode=='two_bottles':
            tasks.append(dict(type='transfer'))
            for bottle in (1,2):
                tasks.append(dict(type='select_bottle',bottle=bottle))
                for i in plan['ingredients']:
                    if i['payload'].get('category') in ('pg','vg') and i['add_grams']>0.005:
                        tasks.append(dict(type='tare',key=i['key'],bottle=bottle))
                        tasks.append(dict(type='pour',key=i['key'],target_grams=round(i['add_grams']/2,2),bottle=bottle))
                        tasks.append(dict(type='move_ingredient',key=i['key'],bottle=bottle))
                tasks.append(dict(type='mix',bottle=bottle))
        tasks.append(dict(type='finish'))
        additions=list(previous.get('additions') or [])
        if previous.get('status')=='active' and previous['cursor']<len(previous['tasks']):
            partial=previous['tasks'][previous['cursor']]
            if partial['type']=='pour' and partial.get('reading',0)>0:
                additions.append(dict(key=partial['key'],grams=partial['reading'],target=partial['target_grams'],bottle=partial['bottle']))
        state=dict(status='active',revision=int(previous.get('revision',0)),cursor=0,plan=plan,original_ml=original,
                   actuals={i['key']:i['actual_grams'] for i in items},additions=additions,
                   original_pours=previous.get('original_pours') or {i['key']:i['actual_grams'] for i in items},
                   tasks=tasks,split_done=False,events=list(previous.get('events') or []))
        state['events'].append(dict(event='recovery_plan_applied',mode=mode,new_ml=plan['new_ml']))
        old_ml=float(meta.get('batch_size_ml') or row['target_ml'])
        if old_ml>0:
            composition=dict(meta.get('planned_composition') or {})
            for field,value in composition.items():
                if field.endswith(('_ml','_g')) and isinstance(value,(int,float)):
                    composition[field]=value*plan['new_ml']/old_ml
            meta['planned_composition']=composition
        meta['batch_size_ml']=plan['new_ml']
        meta['vessel_size_ml']=plan['bottle_ml']
        db.execute('UPDATE mix_sessions SET target_ml=? WHERE id=?',(plan['new_ml'],session_id))
        _save(db,session_id,meta,state)
        return state
    except Exception:
        db.rollback()
        raise


def _finish(db,session_id,row,meta,state):
    from services import guided_mix_session_service as guided
    # Retain original records; unfinished pours not physically added carry zero actual.
    for step in db.execute('SELECT * FROM mix_session_steps WHERE mix_session_id=?',(session_id,)).fetchall():
        if step['completed_at']:
            continue
        p=json.loads(step['payload_json'] or '{}')
        p['superseded_by_recovery']=True
        if p.get('step_type')=='add_ingredient':
            grams=float(p.get('measured_grams') or 0) if p.get('overpour_recovery_pending') else 0.0
            density=guided._density_from_step_payload(p) or 1
            p.update(measured_grams=grams,measured_ml=grams/density,actual_ml=grams/density)
        db.execute("UPDATE mix_session_steps SET completed_at=datetime('now'),payload_json=? WHERE id=?",(json.dumps(p),step['id']))
    next_no=db.execute('SELECT COALESCE(MAX(step_no),0)+1 FROM mix_session_steps WHERE mix_session_id=?',(session_id,)).fetchone()[0]
    by_key={i['key']:i for i in state['plan']['ingredients']}
    for index,addition in enumerate(state['additions']):
        i=by_key[addition['key']]
        p=deepcopy(i['payload'])
        grams=addition['grams']
        p.update(measured_grams=grams,measured_ml=grams/i['density'],actual_ml=grams/i['density'],target_grams=addition['target'],
                 display_target_grams=addition['target'],grams=addition['target'],ml=addition['target']/i['density'],target_ml=addition['target']/i['density'],
                 recovery_topup=True,recovery_bottle=addition['bottle'])
        db.execute("INSERT INTO mix_session_steps (mix_session_id,step_no,step_type,payload_json,completed_at) VALUES (?,?,'add_ingredient',?,datetime('now'))",(session_id,next_no+index,json.dumps(p)))
    plan=state['plan']
    meta['batch_size_ml']=plan['new_ml']
    meta['vessel_size_ml']=plan['bottle_ml']
    meta.setdefault('mix_event_log',[]).append(dict(event='recipe_rebalanced',original_ml=state['original_ml'],new_ml=plan['new_ml'],bottles=plan['bottle_count']))
    total_ml=sum(state['actuals'][i['key']]/i['density'] for i in plan['ingredients'])
    nicotine_ml=sum(state['actuals'][i['key']]/i['density'] for i in plan['ingredients'] if i['payload'].get('category')=='nicotine')
    pg_ml=sum(state['actuals'][i['key']]/i['density'] for i in plan['ingredients'] if guided._pour_carrier_for_ratio(i['payload'],default_nic_carrier=meta.get('nicotine_carrier') or 'PG')=='PG')
    state['estimated_volume_ml']=round(total_ml,2)
    state['estimated_nicotine_mg_ml']=round(nicotine_ml*float(meta.get('nic_base_mg_ml') or 100)/total_ml,3) if total_ml else 0
    pg_percent=round(100*pg_ml/total_ml) if total_ml else 0
    state['estimated_ratio']=f'{pg_percent}/{100-pg_percent}'
    if state.get('split_done'):
        state['bottles']=[]
        for bottle,share in ((1,1-state['split_fraction']),(2,state['split_fraction'])):
            weights={k:g*share for k,g in state['split_actuals'].items()}
            for addition in state['additions']:
                if addition['bottle']==bottle:
                    weights[addition['key']]+=addition['grams']
            volume=sum(weights[i['key']]/i['density'] for i in plan['ingredients'])
            nic_volume=sum(weights[i['key']]/i['density'] for i in plan['ingredients'] if i['payload'].get('category')=='nicotine')
            state['bottles'].append(dict(number=bottle,estimated_volume_ml=round(volume,2),
                estimated_nicotine_mg_ml=round(nic_volume*float(meta.get('nic_base_mg_ml') or 100)/volume,3) if volume else 0))
    state['status']='complete'
    db.execute("UPDATE mix_sessions SET target_ml=?,completed_at=datetime('now') WHERE id=?",(plan['new_ml'],session_id))


def advance(session_id,user_id,revision,cursor,weight=None,recording_error=False):
    from services import guided_mix_session_service as guided
    db=get_db()
    db.execute('BEGIN IMMEDIATE')
    try:
        row=db.execute('SELECT * FROM mix_sessions WHERE id=? AND user_id=?',(session_id,user_id)).fetchone()
        if not row:
            raise RecoveryError('Mix session not found.')
        meta=json.loads(row['result_json'] or '{}')
        state=meta.get('batch_recovery')
        if not state or state['status'] not in {'active','blocked'}:
            raise RecoveryError('No active recovery step. Review the saved recovery state.')
        if int(revision)!=state['revision'] or int(cursor)!=state['cursor']:
            raise RecoveryError('This step changed in another request. Reload to see the saved state.')
        task=state['tasks'][state['cursor']]
        if state['status']=='blocked':
            if recording_error is not True or task['type']!='pour':
                raise RecoveryError('Recovery is paused. Review a new plan, or explicitly correct a typing mistake; do not change a record to hide liquid already poured.')
            number(weight,'corrected recorded weight')
            previous=state['additions'].pop()
            if previous['key']!=task['key'] or previous['bottle']!=task['bottle']:
                raise RecoveryError('The last recorded pour does not match this recovery step.')
            state['actuals'][previous['key']]-=previous['grams']
            state['events'].append(dict(event='recorded_weight_corrected',old_grams=previous['grams'],new_grams=float(weight),key=previous['key'],bottle=previous['bottle']))
            state['status']='active'
            state.pop('message',None)

        if task['type']=='pour':
            grams=number(weight,'current scale reading')
            target=task['target_grams']
            task['reading']=grams
            if round(grams,2)<target:
                _save(db,session_id,meta,state)
                return state
            i=next(i for i in state['plan']['ingredients'] if i['key']==task['key'])
            state['actuals'][i['key']]+=grams
            state['additions'].append(dict(key=i['key'],grams=grams,target=target,bottle=task['bottle']))
            if round(grams,2)>target:
                state['status']='blocked'
                state['message']=f"{('Bottle '+str(task['bottle'])) if task['bottle'] else 'Current bottle'}: {i['payload'].get('name')} recorded at {grams:.2f} g against {target:.2f} g. Stop and keep the liquid. Never return mixed liquid to a stock bottle. " + ('Keep both bottles labelled; a physical overpour after splitting needs additional capacity and a separate correction.' if state.get('split_done') else 'Review an updated plan using this additional liquid before continuing.')
                _save(db,session_id,meta,state)
                return state
        elif task['type']=='transfer':
            grams=number(weight,'transferred mixture weight')
            total=sum(state['actuals'].values())
            target=round(total/2,2)
            task['reading']=grams
            if grams>0:
                state['transfer_started']=True
            if total<=0 or round(grams,2)!=target:
                state['message']=f'Mix thoroughly first. With the empty second bottle tared, transfer until its scale reads {target:.2f} g. Transfer only between these mixing bottles; do not tare during the transfer.'
                _save(db,session_id,meta,state)
                return state
            fraction=grams/total
            state['split_done']=True
            state['split_actuals']=dict(state['actuals'])
            state['split_fraction']=fraction
            state['split_weight_grams']=grams
            state['bottle_volumes_ml']=[state['plan']['new_ml']*(1-fraction),state['plan']['new_ml']*fraction]
            # Use the measured split proportion, not an assumed exact half.
            for future in state['tasks'][state['cursor']+1:]:
                if future['type']=='pour':
                    i=next(i for i in state['plan']['ingredients'] if i['key']==future['key'])
                    share=(1-fraction) if future['bottle']==1 else fraction
                    future['target_grams']=round(i['add_grams']*share,2)
        elif task['type']=='finish':
            _finish(db,session_id,row,meta,state)
        state.pop('message',None)
        state['events'].append(dict(event='recovery_step_complete',cursor=state['cursor'],type=task['type']))
        state['cursor']+=1
        _save(db,session_id,meta,state)
        return state
    except Exception:
        db.rollback()
        raise
