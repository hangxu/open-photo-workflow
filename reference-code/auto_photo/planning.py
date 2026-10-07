"""Collection planning with reusable pages and explicit adapter call accounting.

Only an explicitly supplied external command may call a planner. Built-in image
creation remains a separate handoff. State is persisted before every call, so
an interrupted or unknown result cannot silently cause another paid attempt.
"""
from copy import deepcopy
from pathlib import Path
import json
import subprocess
import time
from uuid import uuid4
from .plan import compact_planner_prompt, validate_plan
from .workflow import load_batch, apply_plan, _atomic, _safe, _lock, _change, _check_item


def _rows(state):
    rows=[]
    for item in state['items']:
        if not item.get('prepared'):
            continue
        _check_item(item)
        preview=Path(item['preview_path'])
        stamp=item.get('preview_stamp')
        stat=preview.stat()
        if stamp and (stat.st_size!=stamp['size'] or stat.st_mtime_ns!=stamp['mtime_ns']):
            raise ValueError(f'{item["id"]}: preview metadata changed; prepare a new batch')
        row={key:item[key] for key in ('id','source_revision','width','height','preview_path','stats','scene_hint','candidates')}
        row['source_format']=Path(item['source']).suffix.lower().lstrip('.')
        rows.append(row)
    if not rows:
        raise ValueError('No successfully prepared inputs to plan')
    return rows


def _validate_size(max_photos):
    if type(max_photos) is not int or not 1<=max_photos<=32:
        raise ValueError('max_photos must be between 1 and 32')


def _payload(state,rows,page,max_photos,context=None):
    if page == 0:
        page_instructions = ' First page: inspect collection_overview to establish the shared collection strategy and styles.'
    else:
        page_instructions = (' Continuation page: carry forward the collection strategy and styles in shared_context from the first page; '
                             'do not repeat whole-collection inspection or request the full overview again.')
    payload = {'schema_version':2,'batch_id':state['batch_id'],'requested_edits':state.get('requested_edits'),
               'instructions':compact_planner_prompt()+page_instructions+
                   ' Inspect every photo supplied in this page, not only representatives, and return its execution plan. '
                   'Null requested_edits means unspecified intent, never an invitation to infer beauty retouching. Return a plan object or {plan,usage,model}.',
               'page':page+1,'page_count':(len(rows)+max_photos-1)//max_photos,'collection_count':len(rows),
               'shared_context':context or {},'photos':rows[page*max_photos:(page+1)*max_photos]}
    if page == 0:
        payload['collection_overview'] = [{key:r[key] for key in ('id','source_revision','preview_path','width','height','scene_hint')} for r in rows]
    else:
        payload['collection_overview_ref'] = {'batch_id':state['batch_id'],'page':1}
    return payload


def export_requests(root,max_photos=8):
    """Explicit export only; running/preparing a batch creates no request files."""
    _validate_size(max_photos)
    state=load_batch(root)
    rows=_rows(state)
    directory=_safe(Path(root)/'planning_exports')
    paths=[]
    for page in range((len(rows)+max_photos-1)//max_photos):
        path=directory/f'request-{page+1:03d}.json'
        payload=_payload(state,rows,page,max_photos)
        if page:
            payload['collection_overview_ref']['request_path']=str(directory/'request-001.json')
            payload['instructions']+=' Continue these exported pages in the same planning context, carrying the first page strategy and any prior page decisions.'
        if not path.exists() or json.loads(path.read_text())!=payload:
            _atomic(path,payload)
        paths.append(str(path))
    return paths


def _validate_page(answer,photos,collection_rows):
    # The shared collection strategy may name representatives outside this page.
    candidate=deepcopy(answer)
    strategy=candidate.get('collection_strategy')
    representatives=None
    if isinstance(strategy,dict) and 'representative_ids' in strategy:
        representatives=strategy.pop('representative_ids')
        if not isinstance(representatives,list) or any(not isinstance(v,str) for v in representatives) or len(set(representatives))!=len(representatives) or set(representatives)-{r['id'] for r in collection_rows}:
            raise ValueError('representative_ids must identify collection photos')
    normalized=validate_plan(candidate,photos)
    if representatives is not None:
        normalized['collection_strategy']['representative_ids']=representatives
    return normalized


def plan_with_command(root,command,max_photos=8,timeout=180,freeze=True,revise=False):
    _validate_size(max_photos)
    if not isinstance(command,list) or not command or any(not isinstance(value,str) or not value for value in command):
        raise ValueError('Planner command must be a JSON array of argument strings; no shell is used')
    if isinstance(timeout,bool) or not isinstance(timeout,(int,float)) or not 0<timeout<=3600:
        raise ValueError('Planner timeout must be positive and at most 3600 seconds')
    state=load_batch(root)
    if not isinstance(state.get('requested_edits'),str) or not state['requested_edits'].strip():
        raise ValueError('External planner requires an explicit batch brief; prepare with --brief or import a conversation-based plan')
    with _lock(root,'.planner.lock'):
        rows=_rows(state)
        recipe={'command':command,'max_photos':max_photos,'source_revisions':[[r['id'],r['source_revision']] for r in rows],'requested_edits':state['requested_edits']}
        sessions=state.get('planning_sessions',[])
        session=next((s for s in sessions if s['recipe']==recipe),None)
        if session and session.get('applied_revision') is not None:
            if session['applied_revision']!=state['plan_revision']:
                raise ValueError('The cached planner plan has since been revised; import or explicitly choose a new planning command')
            return apply_plan(root,state['plan'],freeze=freeze,revise=revise)
        if session is None:
            session={'id':str(uuid4()),'recipe':recipe,'instructions':compact_planner_prompt(),'pages':[],'status':'planning'}
            _change(root,lambda latest:latest.setdefault('planning_sessions',[]).append(deepcopy(session)))
        def update_session(change):
            def update(latest):
                current=next(s for s in latest['planning_sessions'] if s['id']==session['id'])
                change(current)
            return _change(root,update)
        combined={'schema_version':2,'batch_id':state['batch_id'],'styles':{},'collection_strategy':{},'photos':[]}
        for page in range((len(rows)+max_photos-1)//max_photos):
            if load_batch(root).get('stop_requested'):
                return load_batch(root)
            payload=_payload(state,rows,page,max_photos,{'styles':combined['styles'],'collection_strategy':combined['collection_strategy']})
            record=next((p for p in session['pages'] if p['page']==page),None)
            if record:
                if record['status']!='validated':
                    raise ValueError('Previous planner call failed or has an unknown outcome; inspect state before explicitly choosing a new command')
                answer=_validate_page(record['answer'],payload['photos'],rows)
            else:
                record={'request_id':str(uuid4()),'page':page,'status':'running','calls':1,'usage':None,'model':None}
                update_session(lambda current:current['pages'].append(deepcopy(record)))
                session['pages'].append(record)
                started=time.monotonic()
                try:
                    result=subprocess.run(command,input=json.dumps(payload,ensure_ascii=False,allow_nan=False),text=True,capture_output=True,timeout=timeout,check=False)
                    if result.returncode:
                        raise ValueError(f'Planner exited {result.returncode}: {result.stderr[-1500:]}')
                    if len(result.stdout)>5_000_000:
                        raise ValueError('Planner response exceeds 5 MB')
                    envelope=json.loads(result.stdout)
                    if not isinstance(envelope,dict):
                        raise ValueError('Planner must return a JSON object')
                    answer=_validate_page(envelope.get('plan',envelope),payload['photos'],rows)
                    if answer['batch_id']!=state['batch_id']:
                        raise ValueError('Planner batch identity mismatch')
                    record.update(status='validated',answer=answer,usage=envelope.get('usage') if 'plan' in envelope else None,model=envelope.get('model') if 'plan' in envelope else None)
                except Exception as exc:
                    record.update(status='failed' if not isinstance(exc,subprocess.TimeoutExpired) else 'outcome_unknown',error=f'{type(exc).__name__}: {exc}')
                    raise
                finally:
                    record['seconds']=time.monotonic()-started
                    update_session(lambda current:next(p for p in current['pages'] if p['page']==page).update(deepcopy(record)))
            if answer['batch_id']!=state['batch_id']:
                raise ValueError('Cached planner batch identity mismatch')
            for sid,style in answer['styles'].items():
                if sid in combined['styles'] and combined['styles'][sid]!=style:
                    raise ValueError('Planner changed a shared style between pages')
                combined['styles'][sid]=style
            strategy=answer['collection_strategy']
            for key,value in strategy.items():
                if key=='representative_ids':
                    combined['collection_strategy'].setdefault(key,[]).extend(v for v in value if v not in combined['collection_strategy'].get(key,[]))
                elif key in combined['collection_strategy'] and combined['collection_strategy'][key]!=value:
                    raise ValueError('Planner changed collection strategy between pages')
                else:
                    combined['collection_strategy'][key]=value
            combined['photos'].extend(answer['photos'])
        result=apply_plan(root,combined,freeze=freeze,revise=revise)
        def applied(current):
            current.update(status='applied',applied_revision=result['plan_revision'])
            # The canonical plan now owns the response, avoiding one copy per layer.
            for page in current['pages']:
                page.pop('answer',None)
        return update_session(applied)
