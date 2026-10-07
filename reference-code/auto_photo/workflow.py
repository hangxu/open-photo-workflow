"""Versioned, non-destructive photo jobs with one authoritative state file.

Identity is explicit, never a content digest. Size and modification time detect
ordinary external edits; they do not prove byte-for-byte content identity.
"""
from __future__ import annotations
from collections import Counter
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import time
from uuid import uuid4
from PIL import Image, ImageCms, ImageOps
from .plan import validate_plan, BOUNDS
from .preflight import analyze_image, optional_local_candidates
from .grade import grade_image
from .superres import finish_generated
from .restoration import restore_regions
from .artifacts import stamp as _stamp, atomic_json
from .converter import SUPPORTED as RAW_FORMATS
from .resources import rawtherapee_profile

PROJECT = Path(__file__).resolve().parent.parent
TILES = ('TL','TR','BL','BR')


def _now():
    return datetime.now(timezone.utc).isoformat()


def _safe(path):
    path = Path(path).expanduser().absolute()
    if any(p.is_symlink() for p in (path,*path.parents)):
        raise ValueError(f'Symlink output path forbidden: {path}')
    return path


def _atomic(path,value):
    atomic_json(_safe(path),value)


@contextmanager
def _lock(root, name='.workflow.lock'):
    """Short state lock. Never hold this across image work or external calls."""
    root = _safe(root)
    root.mkdir(parents=True,exist_ok=True)
    with _safe(root/name).open('a') as handle:
        try:
            fcntl.flock(handle,fcntl.LOCK_EX | (fcntl.LOCK_NB if name != '.workflow.lock' else 0))
        except BlockingIOError as exc:
            raise RuntimeError('Batch is already being written by another process') from exc
        try:
            yield root
        finally:
            fcntl.flock(handle,fcntl.LOCK_UN)


def _save(root, state):
    state['summary'] = dict(Counter(i['status'] for i in state['items']),total=len(state['items']))
    state['state_revision'] = state.get('state_revision',0)+1
    state['updated_at'] = _now()
    stored = deepcopy(state)
    # Plans are stored exactly once; load_batch supplies the convenient item view.
    for item in stored['items']:
        item.pop('plan',None)
    _atomic(Path(root)/'state.json',stored)
    return state


def load_batch(root):
    root = _safe(root)
    if not (root/'state.json').exists() and (root/'manifest.json').exists():
        raise ValueError('Historical manifest batches are read-only; create a new schema 2 batch')
    state = json.loads(_safe(root/'state.json').read_text(encoding='utf-8'))
    if state.get('schema_version') != 2 or state.get('root') != str(root):
        raise ValueError('State belongs to another root or schema; historical batches are read-only')
    plans = {p['id']:p for p in (state.get('plan') or {}).get('photos',[])}
    for item in state['items']:
        item['plan'] = plans.get(item['id'])
    return state


def _verify(state):
    """Validate state structure without scanning all source or artifact bytes."""
    if state.get('schema_version') != 2:
        raise ValueError('Historical batches are read-only')
    ids = [i['id'] for i in state['items']]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate photo ids in state')
    for item in state['items']:
        if item.get('source_revision') != 1:
            raise ValueError('Unsupported source revision; create a new batch for replaced sources')


def _check_item(item):
    for key in ('source','input','paired_jpeg'):
        if item.get(key) and item.get(key+'_stamp') != _stamp(item[key]):
            raise ValueError(f'{item["id"]}: {key} metadata changed; create a new batch')
    generated = ([item['generated']] if item.get('generated') else []) + list(item.get('generated_tiles',{}).values())
    for artifact in generated:
        if artifact.get('stamp') != _stamp(artifact['path']):
            raise ValueError(f'{item["id"]}: imported generation metadata changed; explicitly replace the import')


def _find(state, photo_id):
    for item in state['items']:
        if item['id'] == photo_id:
            return item
    raise ValueError('Unknown photo id')


def _change(root, callback):
    with _lock(root) as root:
        state = load_batch(root)
        callback(state)
        return _save(root,state)


def _safe_id(stem):
    return re.sub(r'[^\w.-]+','_',stem).strip('.') or 'photo'


def _work(root):
    directory = _safe(Path(root)/'.work'/str(uuid4()))
    directory.mkdir(parents=True)
    return directory


def _relocate(value, before, after):
    if isinstance(value,dict):
        return {key:_relocate(v,before,after) for key,v in value.items()}
    if isinstance(value,list):
        return [_relocate(v,before,after) for v in value]
    if isinstance(value,str) and (value == str(before) or value.startswith(str(before)+os.sep)):
        return str(after)+value[len(str(before)):]
    return value


def _finish_pending(root, photo_id):
    """Recover a two-phase publication, including rename-before-state crashes."""
    state = load_batch(root)
    item = _find(state,photo_id)
    pending = item.get('pending_commit')
    if not pending:
        return state
    temporary, published = _safe(pending['temporary']), _safe(pending['published'])
    if not published.exists():
        if not temporary.exists():
            raise ValueError('Interrupted publication lost its temporary output')
        published.parent.mkdir(parents=True,exist_ok=True)
        os.replace(temporary,published)
        directory = os.open(published.parent,os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    patch = pending['patch']
    # Decode only new/recovered outputs, never every historic artifact.
    for key in ('jpeg_path','preview_path'):
        if patch.get(key):
            with Image.open(_safe(patch[key])) as image:
                image.load()
    if patch.get('jpeg_path'):
        patch['final_stamp'] = _stamp(patch['jpeg_path'])
    if patch.get('preview_path'):
        patch['preview_stamp'] = _stamp(patch['preview_path'])
    if patch.get('input'):
        patch['input_stamp'] = _stamp(patch['input'])
    generated = ([patch['generated']] if patch.get('generated') else []) + list(patch.get('generated_tiles',{}).values())
    for artifact in generated:
        if not artifact.get('stamp'):
            with Image.open(_safe(artifact['path'])) as image:
                image.load()
            artifact['stamp'] = _stamp(artifact['path'])
    def commit(latest):
        current = _find(latest,photo_id)
        if 'generation_attempts' in patch:
            received={row['attempt_id']:row for row in patch['generation_attempts']}
            merged=[received.pop(row['attempt_id'],row) for row in current.get('generation_attempts',[])]
            merged.extend(received.values())
            patch['generation_attempts']=merged
            patch['generation_calls']=sum(row.get('counted') is True for row in merged)
        current.update(patch)
        if patch.get('final_created_at'):
            latest.setdefault('first_final_at',patch['final_created_at'])
        current.pop('pending_commit',None)
        current.pop('active_attempt',None)
        current.pop('error',None)
        _refresh_review(latest)
    return _change(root,commit)


def _commit_directory(root, photo_id, temporary, published, patch):
    temporary,published = _safe(temporary),_safe(published)
    patch = _relocate(patch,temporary,published)
    def stage(state):
        item = _find(state,photo_id)
        item['pending_commit'] = {'temporary':str(temporary),'published':str(published),'patch':patch}
        item['status'] = 'publishing'
    _change(root,stage)
    return _finish_pending(root,photo_id)


def _recover(root):
    for item in load_batch(root)['items']:
        if item.get('pending_commit'):
            try:
                _finish_pending(root,item['id'])
            except Exception as exc:
                _change(root,lambda state,pid=item['id'],error=exc: _find(state,pid).update(status='error',error=f'{type(error).__name__}: {error}'))
        elif item.get('active_attempt'):
            def interrupted(state,pid=item['id']):
                current = _find(state,pid)
                current.pop('active_attempt',None)
                current['status'] = 'planned' if current.get('plan') else 'queued'
            _change(root,interrupted)
    return load_batch(root)


def _stopped(root):
    return bool(load_batch(root).get('stop_requested'))


def prepare_batch(input_path,output_dir,recursive=False,preview_edge=1280,detect=True,
                  raw_profile=None,raw_executable=None,selected_ids=None,brief=None,pair_raw_jpeg=True):
    if brief is not None and (not isinstance(brief,str) or not brief.strip()):
        raise ValueError('brief must be a nonempty string when supplied')
    if type(preview_edge) is not int or preview_edge < 1:
        raise ValueError('preview_edge must be a positive integer')
    source = Path(input_path).expanduser().resolve(strict=True)
    root = _safe(output_dir)
    if root == source or root in source.parents or (source.is_dir() and source in root.parents):
        raise ValueError('Output must be separate from the input and its ancestors/descendants')
    if not isinstance(pair_raw_jpeg,bool):
        raise ValueError('pair_raw_jpeg must be boolean')
    existing = load_batch(root) if (root/'state.json').exists() else None
    legacy_preparation = existing is not None and 'pair_raw_jpeg' not in existing.get('prepare_recipe',{})
    from .sources import discover_sources
    discovered = discover_sources(source,recursive=recursive,pair_raw_jpeg=False if legacy_preparation else pair_raw_jpeg)
    if not discovered:
        raise ValueError('No supported image inputs')
    stems = Counter(_safe_id(Path(row['source']).stem) for row in discovered)
    counts = Counter()
    pairs = []
    used = set()
    for discovered_row in discovered:
        path = Path(discovered_row['source'])
        stem = _safe_id(path.stem)
        counts[stem] += 1
        pid = stem if stems[stem] == 1 else f'{stem}-{counts[stem]:03d}'
        while pid in used:
            pid += '-photo'
        used.add(pid)
        row = dict(discovered_row,id=pid)
        if legacy_preparation:
            row = {key:row[key] for key in ('id','source','source_stamp','source_revision')}
        else:
            row['raw_deferred'] = bool(row.get('is_raw'))
        pairs.append((pid,row))
    if selected_ids is not None:
        wanted = set(selected_ids)
        if not wanted or wanted-used:
            raise ValueError('selected_ids must identify discovered photos')
        pairs = [(pid,row) for pid,row in pairs if pid in wanted]
    profile = Path(raw_profile).expanduser().resolve() if raw_profile else rawtherapee_profile()
    recipe = {'recursive':bool(recursive),'preview_edge':preview_edge,'detect':bool(detect),
              'raw_profile':str(profile),'raw_executable':str(raw_executable) if raw_executable else None,
              'selected_ids':sorted(selected_ids) if selected_ids is not None else None}
    if not legacy_preparation:
        recipe['pair_raw_jpeg'] = pair_raw_jpeg
    # Only new batches adopt the camera-JPEG input policy. A resumed batch
    # without this field keeps its original rendering behavior and identity.
    if existing is None or 'raw_generation_input' in existing.get('prepare_recipe',{}):
        recipe['raw_generation_input'] = 'configured-profile' if raw_profile else 'camera-input-v2'
    sources = [row for _,row in pairs]
    with _lock(root,'.runner.lock'):
        with _lock(root):
            if (root/'manifest.json').exists() and not (root/'state.json').exists():
                raise ValueError('Historical batches are read-only; create a new schema 2 batch')
            if (root/'state.json').exists():
                state = load_batch(root)
                identity_keys = ('id','source','source_stamp','source_revision') if legacy_preparation else ('id','source','source_stamp','source_revision','paired_jpeg','paired_jpeg_stamp')
                old_sources = [{key:item.get(key) for key in identity_keys} for item in state['items']]
                new_sources = [{key:item.get(key) for key in identity_keys} for item in sources]
                if state['input_path'] != str(source) or state['prepare_recipe'] != recipe or state.get('requested_edits') != brief or old_sources != new_sources:
                    raise ValueError('Input or preparation recipe changed; use a new batch')
                if state.get('preparation_complete'):
                    return state
            else:
                if any(p.name not in ('.workflow.lock','.runner.lock') for p in root.iterdir()):
                    raise FileExistsError('Nonempty output without matching state; use a new batch')
                state = {'schema_version':2,'state_revision':0,'batch_id':str(uuid4()),'root':str(root),
                         'input_path':str(source),'prepare_recipe':recipe,'requested_edits':brief,
                         'plan':None,'plan_revision':0,'plan_frozen':False,'stop_requested':False,
                         'preparation_complete':False,'created_at':_now(),
                         'items':[dict(row,input=None,preview_path=None,width=None,height=None,prepared=False,status='queued',plan_revision=0,stages={}) for row in sources]}
                _save(root,state)
        _recover(root)
        for row in load_batch(root)['items']:
            if row.get('prepared') or _stopped(root):
                continue
            work = _work(root)
            started = time.monotonic()
            _change(root,lambda state,pid=row['id']: _find(state,pid).update(status='preparing',active_attempt={'id':str(uuid4()),'temporary':str(work)}))
            try:
                _check_item(row)
                developed = Path(row['source'])
                raw_record = None
                if row.get('raw_deferred'):
                    from .sources import prepare_preview
                    info = prepare_preview(row,work,max_edge=preview_edge,raw_profile=profile,raw_executable=raw_executable)
                    info['input'] = None
                    info['input_stamp'] = None
                    detection_source = info['preview_path']
                else:
                    # Older schema 2 batches retain their original eager-input behavior.
                    if developed.suffix.lower() in RAW_FORMATS:
                        from .rawtherapee import convert_batch
                        raw_record = convert_batch(developed,work/'raw',profile=profile,executable=raw_executable,write_record=False)
                        rendered = raw_record['items'][0]
                        if rendered['status'] == 'error':
                            raise ValueError(rendered['error'])
                        developed = Path(rendered['output'])
                    info = analyze_image(developed,work/'preview.jpg',max_edge=preview_edge)
                    info['input'] = str(developed)
                    info['input_stamp'] = _stamp(developed)
                    detection_source = developed
                info.pop('source_stamp',None)
                info['candidates'] = optional_local_candidates(detection_source) if detect else {'available':False,'regions':[],'limitations':['Local detection disabled']}
                info['scene_hint'] = {'captured_at':info.get('time',{}).get('captured_at'),'camera':info.get('time',{}).get('camera_model'),
                                      'suggested_group':None,'basis':'Visual confirmation required; metadata is only a hint'}
                if raw_record:
                    info['raw_record'] = raw_record
                _check_item(row)
                info.update(prepared=True,status='awaiting_plan',preflight_seconds=time.monotonic()-started)
                _commit_directory(root,row['id'],work,root/'photos'/row['id']/'prepared',info)
            except Exception as exc:
                # A publishing record must survive so resume can finish its commit.
                def failed(state,pid=row['id'],error=exc):
                    current = _find(state,pid)
                    current.update(status='error',error=f'{type(error).__name__}: {error}')
                    current.pop('active_attempt',None)
                _change(root,failed)
        def done(state):
            state['preparation_complete'] = not state.get('stop_requested') and all(i.get('prepared') or i['status']=='error' for i in state['items'])
        return _change(root,done)


def _recipe(state,photo):
    style = state['plan']['styles'][photo['style_id']]
    combined = {}
    for key,(low,high) in BOUNDS.items():
        default = 1 if key == 'saturation' else 0
        a,b = style['adjustments'].get(key,default),photo['adjustments'].get(key,default)
        combined[key] = min(high,max(low,a*b if key=='saturation' else a+b))
    recipe = {'preset':style['preset'],'adjustments':combined}
    for key in ('white_balance_kelvin','wb_green'):
        if key in photo or key in style:
            recipe[key] = photo.get(key,style.get(key))
    if photo.get('subject_lighting'):
        recipe['subject_lighting'] = deepcopy(photo['subject_lighting'])
    return recipe


def _retire_final(item):
    if item.get('final_revision'):
        item.setdefault('previous_finals',[]).append({key:item[key] for key in ('final_revision','final_path','jpeg_path','final_stamp','final_created_at','technical_pass','review') if key in item})
    for key in ('final_revision','final_path','jpeg_path','final_stamp','final_created_at','technical_pass','review','numerical_qa','numerical_qa_passed','blocked_stage','error','review_reason','review_warnings'):
        item.pop(key,None)


def apply_plan(root,plan,freeze=True,revise=False):
    with _lock(root,'.runner.lock'):
        with _lock(root) as root:
            state = load_batch(root)
            if plan == state.get('plan'):
                if freeze and not state['plan_frozen']:
                    state['plan_frozen'] = True
                    return _save(root,state)
                return state
            ready = [i for i in state['items'] if i.get('prepared')]
            normalized = validate_plan(plan,ready)
            if normalized['batch_id'] != state['batch_id']:
                raise ValueError('Plan batch_id mismatch')
            old_plan = state.get('plan')
            if old_plan == normalized:
                if freeze and not state['plan_frozen']:
                    state['plan_frozen'] = True
                    return _save(root,state)
                return state
            if old_plan and state.get('plan_frozen') and not revise:
                raise ValueError('Plan is frozen; explicitly revise it to produce new versions')
            previous = {p['id']:p for p in (old_plan or {}).get('photos',[])}
            state['plan_revision'] += 1
            state['plan'] = normalized
            state['plan_frozen'] = bool(freeze)
            for item in ready:
                new = next(p for p in normalized['photos'] if p['id']==item['id'])
                old = previous.get(item['id'])
                changed = old != new or (old and old_plan['styles'][old['style_id']] != normalized['styles'][new['style_id']])
                item['plan'] = new
                if changed:
                    _retire_final(item)
                    item['plan_revision'] = state['plan_revision']
                    item['status'] = 'planned'
                    item.pop('generation_request',None)
                    old_generation = (old or {}).get('generation') or {}
                    new_generation = new.get('generation') or {}
                    preserve_generation = old and old['route']==new['route'] and all(old_generation.get(k)==new_generation.get(k) for k in ('prompt','method')) and old_plan['styles'][old['style_id']].get('prompt')==normalized['styles'][new['style_id']].get('prompt')
                    if (item.get('raw_deferred') and old
                            and state['prepare_recipe'].get('raw_generation_input') != 'camera-input-v2'
                            and _recipe({'plan':old_plan},old) != _recipe(state,new)):
                        preserve_generation = False
                        if item.get('input'):
                            item.setdefault('previous_inputs',[]).append({'path':item['input'],'recipe':(item.get('raw_input') or {}).get('recipe')})
                        item.update(input=None,input_stamp=None)
                        item.pop('raw_input',None)
                    if not preserve_generation:
                        for key in ('generated','generated_tiles','tiled_prepared'):
                            if item.get(key):
                                item.setdefault('previous_generation',[]).append({key:item[key]})
                            item.pop(key,None)
                    item['stages'] = {}
            _refresh_review(state)
            return _save(root,state)


def freeze_plan(root):
    state = load_batch(root)
    if not state.get('plan'):
        raise ValueError('No plan to freeze')
    return apply_plan(root,state['plan'],freeze=True)


def _review_valid(state,item):
    review = item.get('review') or {}
    tolerance=(state.get('plan') or {}).get('collection_strategy',{}).get('delivery',{}).get('tolerance','minor')
    severity=review.get('severity','none')
    valid = bool(item.get('final_revision') and review.get('final_revision')==item['final_revision'] and review.get('passed') and severity!='major' and not (tolerance=='strict' and severity!='none'))
    delivery=(state.get('plan') or {}).get('collection_strategy',{}).get('delivery',{})
    required=delivery.get('viewing_long_edge',2560)
    dimensions=item.get('final_dimensions')
    if dimensions:
        required=min(required,max(dimensions))
    if review.get('viewing_long_edge') is not None and review['viewing_long_edge']<required:
        valid=False
    if valid and review.get('review_type')=='representative':
        try:
            representative = _find(state,review['representative_id'])
        except (ValueError,KeyError):
            return False
        approval = representative.get('review') or {}
        return (representative.get('final_revision')==review.get('representative_final_revision') and
                approval.get('review_type')!='representative' and _review_valid(state,representative) and
                approval.get('final_revision')==representative.get('final_revision') and
                representative.get('technical_pass') is True and representative.get('numerical_qa_passed') is True and item.get('numerical_qa_passed') is True and _review_group(representative)==_review_group(item))
    return valid


def _review_group(item):
    photo = item.get('plan') or {}
    parameters = item.get('stages',{}).get('C',{}).get('effective_parameters')
    if not photo.get('scene_id') or not parameters:
        return None
    return (photo['style_id'],photo['scene_id'],parameters,photo.get('subject_lighting',[]),photo.get('white_balance_kelvin'),photo.get('wb_green'))


def _refresh_review(state):
    eligible = [i for i in state['items'] if (i.get('plan') or {}).get('route')=='C' and i.get('final_path') and i.get('technical_pass') and i.get('numerical_qa_passed') and i['status']!='error']
    approved = [i for i in eligible if _review_valid(state,i) and _review_group(i) and (i.get('review') or {}).get('review_type')!='representative']
    representatives = []
    for item in eligible:
        group = _review_group(item)
        if group and not any(g==group for g,_ in representatives):
            representatives.append((group,item['id']))
    for item in state['items']:
        if not item.get('final_path') or item.get('blocked_stage') or item['status']=='error':
            continue
        photo = item.get('plan') or {}
        group = _review_group(item)
        review = item.get('review') or {}
        if review.get('review_type')=='representative' and not _review_valid(state,item):
            item.pop('review',None)
            review={}
        if review.get('final_revision')==item.get('final_revision') and review.get('passed') is False:
            item['status']='needs_revision'
        elif _review_valid(state,item) and item.get('technical_pass'):
            item['status']='auto_complete' if review.get('review_type')=='representative' else 'completed'
        elif photo.get('route')!='C' or photo.get('needs_review') or not item.get('technical_pass') or not item.get('numerical_qa_passed',True) or not group:
            item['status']='needs_review'
        elif any(_review_group(i)==group for i in approved):
            representative=next(i for i in approved if _review_group(i)==group)
            item['status']='auto_complete'
            item['review_reason']='Same visually confirmed scene and exact effective recipe as an approved representative; numerical checks passed'
            item['review']={'final_revision':item['final_revision'],'passed':True,'notes':item['review_reason'],'review_type':'representative',
                            'representative_id':representative['id'],'representative_final_revision':representative['final_revision'],'reviewed_at':_now()}
        elif any(g==group and pid==item['id'] for g,pid in representatives):
            item['status']='needs_review'
        else:
            item['status']='awaiting_style_review'


def _numerical_qa(item,record):
    with Image.open(item['final_path']) as image:
        image.thumbnail((1280,1280),Image.Resampling.LANCZOS)
        histogram=image.convert('L').histogram()
    count=sum(histogram)
    after={'luminance_mean':sum(i*n for i,n in enumerate(histogram))/(255*count),'shadow_clip_fraction':sum(histogram[:3])/count,'highlight_clip_fraction':sum(histogram[253:])/count}
    before=item['stats']
    qa=record['qa']
    gates={'geometry':qa['geometry']['same_oriented_dimensions'] and qa['geometry']['sample_pointwise_recomputation_exact'],
           'newly_saturated':qa['newly_saturated_pixel_fraction']<=.02,'shadow_clipping':after['shadow_clip_fraction']-before['shadow_clip_fraction']<=.01,
           'highlight_clipping':after['highlight_clip_fraction']-before['highlight_clip_fraction']<=.01,'luminance_change':abs(after['luminance_mean']-before['luminance_mean'])<=.15}
    item['numerical_qa']={'after':after,'gates':gates}
    item['numerical_qa_passed']=all(gates.values())
    item['technical_pass']=bool(gates['geometry'])


def _publish_image(path,image,fmt,**kwargs):
    path=_safe(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,temporary=tempfile.mkstemp(prefix='.image-',dir=path.parent)
    os.close(fd)
    try:
        image.save(temporary,format=fmt,**kwargs)
        with Image.open(temporary) as check:
            check.load()
        with open(temporary,'rb') as handle:
            os.fsync(handle.fileno())
        os.link(temporary,path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _publish_final(directory,item,source):
    directory=_safe(directory)
    directory.mkdir(parents=True,exist_ok=True)
    destination=directory/'final.jpg'
    with Image.open(source) as image:
        image.load()
        if image.format=='JPEG' and image.getexif().get(274,1)==1:
            shutil.copyfile(source,destination)
        else:
            upright=ImageOps.exif_transpose(image).convert('RGB')
            icc=ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes()
            _publish_image(destination,upright,'JPEG',quality=96,subsampling=0,icc_profile=icc)
    with Image.open(destination) as image:
        image.load()
        dimensions=list(image.size)
    with destination.open('rb') as handle:
        os.fsync(handle.fileno())
    item.update(final_path=str(destination),jpeg_path=str(destination),final_revision=str(uuid4()),final_dimensions=dimensions,final_created_at=_now(),technical_pass=True)


def _generation_request(state,item):
    photo=item['plan']
    shared=state['plan']['styles'][photo['style_id']].get('prompt','')
    prompt='\n\n'.join(v for v in (shared,photo['generation']['prompt']) if v)
    return {'request_id':str(uuid4()),'source_revision':item['source_revision'],'plan_revision':item['plan_revision'],
            'input':item['input'],'prompt':prompt,'scale':photo['generation']['scale'],'sharpen':photo['generation']['sharpen'],
            'method':photo['generation']['method'],'route':photo['route'],'protect_regions':photo['protect_regions'],
            'max_attempts':photo['generation'].get('max_attempts',1),'upgrade_reason':photo['generation'].get('upgrade_reason'),
            'provider':'built-in imagegen handoff','status':'awaiting_import','automatic_calls':0}


def _ensure_raw_input(root,state,item):
    """Publish a full-resolution editing input only when a generative route needs it."""
    if not item.get('raw_deferred'):
        return state,item
    if item.get('input'):
        # A C-route revision may have published a RAW render with a different
        # crop/size. Return to the actual camera input dimensions for generation.
        record=(item.get('raw_input') or {}).get('record',{})
        dimensions=record.get('dimensions')
        if dimensions and list(dimensions)!=[item.get('width'),item.get('height')]:
            kind=record.get('camera_base',{}).get('source_kind','raw_render')
            basis='developed_raw' if kind=='raw_render' else kind
            state=_change(root,lambda latest:_find(latest,item['id']).update(width=dimensions[0],height=dimensions[1],dimensions_basis=basis))
            item=deepcopy(_find(state,item['id']))
        return state,item
    work=_work(root)
    preparation=state['prepare_recipe']
    camera_input=preparation.get('raw_generation_input')=='camera-input-v2'
    started=time.monotonic()
    _change(root,lambda latest:_find(latest,item['id']).update(status='preparing_input' if camera_input else 'developing_raw',active_attempt={'id':str(uuid4()),'temporary':str(work),'plan_revision':item['plan_revision']}))
    try:
        if camera_input:
            from .raw_pipeline import render_camera_base
            # Freeze the pairing discovered at prepare time; do not let a newly
            # added sibling change an existing batch's editing input.
            record=render_camera_base(item['source'],work/'render',executable=preparation.get('raw_executable'),
                                      paired_jpeg=item.get('paired_jpeg') or False,write_record=False)
            input_info={'policy':'camera-input-v2','record':record}
            source_kind=record.get('camera_base',{}).get('source_kind','raw_render')
            dimensions_basis='developed_raw' if source_kind=='raw_render' else source_kind
        else:
            from .rawtherapee import render_photo
            recipe=_recipe(state,item['plan'])
            record=render_photo(item['source'],work/'render',recipe,profile=preparation.get('raw_profile'),executable=preparation.get('raw_executable'),write_record=False)
            input_info={'recipe':recipe,'record':record}
            dimensions_basis='developed_raw'
        if record.get('status')!='completed':
            raise ValueError(record.get('error') or record.get('reason') or 'RAW input preparation failed')
        output=record.get('jpeg_path') or record['final_path']
        with Image.open(output) as image:
            image.load()
            width,height=image.size
        _check_item(item)
        input_info['seconds']=time.monotonic()-started
        state=_commit_directory(root,item['id'],work,Path(root)/'photos'/item['id']/'inputs'/str(uuid4()),
            {'input':output,'width':width,'height':height,'dimensions_basis':dimensions_basis,'raw_input':input_info,'status':'planned'})
        return state,deepcopy(_find(state,item['id']))
    finally:
        current=_find(load_batch(root),item['id'])
        if work.exists() and not current.get('pending_commit'):
            shutil.rmtree(work)


def _raw_numerical_qa(item,record):
    # The camera JPEG/embedded thumbnail has a different tone curve from the RAW
    # render. It is useful for visual review, but not a pointwise numerical baseline.
    expected=record.get('dimensions')
    actual=item['final_dimensions']
    qa=record.get('qa') or {}
    technical=record.get('technical_pass',qa.get('technical_pass',True))
    valid_dimensions=bool(actual[0]>0 and actual[1]>0 and (not expected or list(expected)==actual))
    item['technical_pass']=bool(technical and valid_dimensions)
    item['numerical_qa']={'gates':{'decodable_dimensions':valid_dimensions},'baseline':'planning preview has a separate camera tone curve','engine':qa}
    item['numerical_qa_passed']=False
    item['review_reason']='RAW tone was rendered from sensor data; review this final against its planning preview'


def run_batch(root,cv_runtime=None,model_dir=None,selected_ids=None):
    root=_safe(root)
    with _lock(root,'.runner.lock'):
        state=_recover(root)
        if not state.get('plan') or state.get('stop_requested'):
            return state
        wanted=set(selected_ids) if selected_ids is not None else {i['id'] for i in state['items']}
        if not wanted or wanted-{i['id'] for i in state['items']}:
            raise ValueError('selected_ids must identify batch photos')
        if not state['plan_frozen'] and selected_ids is None:
            raise ValueError('Draft plans may run selected representative ids only; freeze before the whole batch')
        previous_runtime=os.environ.get('AUTO_PHOTO_CV_RUNTIME')
        if cv_runtime is not None:
            os.environ['AUTO_PHOTO_CV_RUNTIME']=str(Path(cv_runtime).expanduser().resolve(strict=True))
        try:
            for pid in [i['id'] for i in state['items'] if i['id'] in wanted]:
                if _stopped(root):
                    break
                state=load_batch(root)
                item=deepcopy(_find(state,pid))
                if not item.get('plan') or item.get('final_path') or item['status'] in ('needs_revision','error') or item.get('pending_commit'):
                    continue
                photo=item['plan']
                work=None
                started=time.monotonic()
                try:
                    _check_item(item)
                    if photo['route']!='C':
                        state,item=_ensure_raw_input(root,state,item)
                        if _stopped(root):
                            break
                    if photo['route']!='C' and not item.get('generated') and not (photo['generation']['method']=='tiled' and set(item.get('generated_tiles',{}))==set(TILES)):
                        if photo['generation']['method']=='tiled' and not item.get('tiled_prepared'):
                            from .tiled import prepare
                            work=_work(root)
                            tiled=prepare(item['input'],work/'tiles',write_record=False)
                            state=_commit_directory(root,pid,work,root/'photos'/pid/'tiles'/str(uuid4()),{'tiled_prepared':tiled,'status':'planned'})
                            item=deepcopy(_find(state,pid))
                            work=None
                        if _stopped(root):
                            break
                        if not item.get('generation_request'):
                            request=_generation_request(state,item)
                            if item.get('tiled_prepared'):
                                request['tiles']=item['tiled_prepared']['tiles']
                            _change(root,lambda latest,request=request: _find(latest,pid).update(generation_request=request,status='awaiting_generation'))
                        continue
                    work=_work(root)
                    _change(root,lambda latest: _find(latest,pid).update(status='processing',active_attempt={'id':str(uuid4()),'temporary':str(work),'plan_revision':item['plan_revision']}))
                    result=deepcopy(item)
                    if photo['route']=='C':
                        if item.get('raw_deferred'):
                            from .rawtherapee import render_photo
                            preparation=state['prepare_recipe']
                            record=render_photo(item['source'],work/'raw',_recipe(state,photo),profile=preparation.get('raw_profile'),executable=preparation.get('raw_executable'),write_record=False)
                            if record.get('status')!='completed':
                                raise ValueError(record.get('error') or record.get('reason') or 'RAW development failed')
                            result['stages']={'C':record}
                            _publish_final(work/'delivery',result,record.get('jpeg_path') or record['final_path'])
                            result['width'],result['height']=result['final_dimensions']
                            result['dimensions_basis']='developed_raw'
                            _raw_numerical_qa(result,record)
                        else:
                            record=grade_image(item['input'],work/'grade',_recipe(state,photo),keep_master=False,write_record=False)
                            result['stages']={'C':record}
                            _publish_final(work/'delivery',result,record['outputs']['final']['path'])
                            _numerical_qa(result,record)
                    else:
                        generated=item.get('generated',{}).get('path')
                        if photo['generation']['method']=='tiled':
                            from .tiled import merge
                            merged=merge(item['tiled_prepared'],{key:value['path'] for key,value in item['generated_tiles'].items()},work/'merge',cv_runtime=cv_runtime,write_record=False)
                            if merged.get('status')!='completed':
                                raise ValueError(merged.get('reason','Tile merge failed'))
                            result['stages']['merge']=merged
                            generated=merged['final_path']
                        if _stopped(root):
                            _change(root,lambda latest: _find(latest,pid).update(status='generated_ready',active_attempt=None))
                            break
                        scale=photo['generation']['scale']
                        models=Path(model_dir).expanduser().resolve() if model_dir else PROJECT/'.tools/fsrcnn'
                        model=models/f'FSRCNN_x{scale}.pb'
                        record=finish_generated(generated,work/'finish',scale=scale,model_path=model if scale!=1 and model.is_file() else None,sharpen=photo['generation']['sharpen'],write_record=False)
                        result['stages']['SR']=record
                        if record['status']!='completed':
                            raise ValueError(record.get('reason','Finishing stage blocked'))
                        final=record['final_path']
                        if photo['route']=='B':
                            if _stopped(root):
                                _change(root,lambda latest: _find(latest,pid).update(status='generated_ready',active_attempt=None))
                                break
                            restored=restore_regions(item['input'],final,work/'restore',photo['protect_regions'],write_record=False)
                            result['stages']['restore']=restored
                            if restored['status']!='completed' and not (restored['status']=='needs_review' and restored.get('technical_pass') is True):
                                raise ValueError('Restoration geometry is invalid; no publishable final')
                            if restored.get('review_warnings'):
                                result['review_warnings']=deepcopy(restored['review_warnings'])
                            final=restored.get('jpeg_path') or restored['final_path']
                        _publish_final(work/'delivery',result,final)
                    _check_item(item)
                    # Keep concise parameters/QA in state, not deleted work paths.
                    for record in result['stages'].values():
                        for key in ('outputs','artifacts','output_dir','final_path','upscaled_path','jpeg_path','manifest_path','output_stamp'):
                            record.pop(key,None)
                    patch={key:result[key] for key in ('final_path','jpeg_path','final_revision','final_dimensions','final_created_at','width','height','dimensions_basis','technical_pass','stages','numerical_qa','numerical_qa_passed','review_warnings','review_reason') if key in result}
                    patch.update(status='needs_review',run_seconds=time.monotonic()-started)
                    delivery=work/'delivery'
                    published=root/'photos'/pid/'versions'/result['final_revision']
                    _commit_directory(root,pid,delivery,published,patch)
                except Exception as exc:
                    def failed(latest,error=exc):
                        current=_find(latest,pid)
                        current.update(status='error',error=f'{type(error).__name__}: {error}')
                        current.pop('active_attempt',None)
                    _change(root,failed)
                finally:
                    # Unpublished work is retained for interrupted publication recovery.
                    pending=_find(load_batch(root),pid).get('pending_commit')
                    if work and work.exists() and not pending:
                        shutil.rmtree(work)
        finally:
            if cv_runtime is not None:
                if previous_runtime is None:
                    os.environ.pop('AUTO_PHOTO_CV_RUNTIME',None)
                else:
                    os.environ['AUTO_PHOTO_CV_RUNTIME']=previous_runtime
        return load_batch(root)


def import_generated(root,photo_id,image_path,metadata=None,replace=False,tile=None):
    if tile is not None and tile not in TILES:
        raise ValueError('tile must be TL, TR, BL or BR')
    with _lock(root,'.runner.lock') as root:
        state=_recover(root)
        item=_find(state,photo_id)
        if not item.get('plan') or item['plan']['route']=='C':
            raise ValueError('Generated import requires an A/B/T plan')
        if item.get('raw_deferred') and not item.get('input'):
            raise ValueError('Run the plan to develop the full-resolution RAW input before importing generation')
        is_tiled=item['plan']['generation']['method']=='tiled'
        if is_tiled != (tile is not None):
            raise ValueError('Tiled plans require one independently imported tile at a time')
        checked=deepcopy(item)
        if replace:
            if tile:
                checked.get('generated_tiles',{}).pop(tile,None)
            else:
                checked.pop('generated',None)
        _check_item(checked)
        source=Path(image_path).expanduser().resolve(strict=True)
        stamp=_stamp(source)
        meta=dict(metadata or {})
        json.dumps(meta,allow_nan=False)
        from .generation_budget import record_generation_import
        existing=item.get('generated_tiles',{}).get(tile) if tile else item.get('generated')
        if existing and existing.get('import_stamp')==stamp and not replace:
            if meta.get('attempt_id'):
                accounting=record_generation_import(state,item,meta,tile=tile)
                if any(item.get(key)!=value for key,value in accounting.items()):
                    def settle(latest):
                        current=_find(latest,photo_id)
                        current.update(record_generation_import(latest,current,meta,tile=tile))
                    return _change(root,settle)
            return state
        if existing and not replace:
            raise ValueError('Another generation is already imported; explicitly replace to create a new version')
        if tile and not item.get('tiled_prepared'):
            raise ValueError('Run the plan once to prepare tile inputs before importing tiles')
        accounting=record_generation_import(state,item,meta,tile=tile)
        with Image.open(source) as image:
            if image.format not in ('PNG','JPEG'):
                raise ValueError('Generated image must be PNG or JPEG')
            image.load()
            upright=ImageOps.exif_transpose(image)
            width,height=upright.size
            expected=item['tiled_prepared']['tiles'][tile]['dimensions'] if tile else (item['width'],item['height'])
            if abs((width/height)/(expected[0]/expected[1])-1)>max(.01,2/min(expected)):
                raise ValueError('Generated framing/aspect ratio differs from its input')
            extension='.png' if image.format=='PNG' else '.jpg'
        work=_work(root)
        destination=work/f'generated{extension}'
        with source.open('rb') as inp,destination.open('xb') as output:
            shutil.copyfileobj(inp,output)
            output.flush()
            os.fsync(output.fileno())
        if _stamp(source)!=stamp:
            raise ValueError('Generation changed during import')
        generated={'artifact_id':str(uuid4()),'path':str(destination),'import_stamp':stamp,'width':width,'height':height,
                   'metadata':{**meta,'model':meta.get('model'),'usage':meta.get('usage')},'source_revision':item['source_revision'],'plan_revision':item['plan_revision']}
        if tile:
            all_tiles=dict(item.get('generated_tiles',{}))
            all_tiles[tile]=generated
            patch={'generated_tiles':all_tiles,'status':'generated_ready' if set(all_tiles)==set(TILES) else 'awaiting_generation'}
        else:
            patch={'generated':generated,'status':'generated_ready'}
        patch.update(accounting)
        if item.get('generation_request'):
            request=deepcopy(item['generation_request'])
            request['status']='imported' if patch['status']=='generated_ready' else 'partially_imported'
            if tile:
                request['imported_tiles']=sorted(all_tiles)
            patch['generation_request']=request
        if replace:
            _change(root,lambda latest:_retire_final(_find(latest,photo_id)))
        return _commit_directory(root,photo_id,work,root/'photos'/photo_id/'generated'/generated['artifact_id'],patch)


def record_reviews(root,decisions):
    if not isinstance(decisions,list) or not decisions:
        raise ValueError('Review decisions must be a nonempty list')
    allowed={'id','final_revision','passed','notes','reviewer','severity','viewing_long_edge','issues'}
    seen=set()
    for decision in decisions:
        if not isinstance(decision,dict) or set(decision)-allowed or not {'id','final_revision','passed','notes'}<=decision.keys():
            raise ValueError('Invalid review decision fields')
        if not isinstance(decision['id'],str) or decision['id'] in seen:
            raise ValueError('Review ids must be unique strings')
        seen.add(decision['id'])
        if not isinstance(decision['final_revision'],str) or not decision['final_revision'] or not isinstance(decision['passed'],bool) or not isinstance(decision['notes'],str) or not decision['notes'].strip() or not isinstance(decision.get('reviewer','codex'),str) or not decision.get('reviewer','codex').strip():
            raise ValueError('Review requires final revision, boolean passed, nonempty notes and reviewer')
        severity=decision.get('severity','none')
        if severity not in ('none','minor','major'):
            raise ValueError('Review severity must be none, minor or major')
        if decision['passed'] and severity=='major':
            raise ValueError('Major defects cannot be approved')
        edge=decision.get('viewing_long_edge')
        if edge is not None and (type(edge) is not int or not 1<=edge<=100000):
            raise ValueError('Review viewing_long_edge must be a positive integer')
        issues=decision.get('issues',[])
        if not isinstance(issues,list) or any(not isinstance(issue,str) or not issue.strip() for issue in issues):
            raise ValueError('Review issues must be a list of nonempty strings')
    with _lock(root) as root:
        state=load_batch(root)
        pairs=[]
        tolerance=(state.get('plan') or {}).get('collection_strategy',{}).get('delivery',{}).get('tolerance','minor')
        for decision in decisions:
            if decision['passed'] and tolerance=='strict' and decision.get('severity','none')!='none':
                raise ValueError('Strict delivery tolerance requires severity none')
            item=_find(state,decision['id'])
            if not item.get('final_path') or item.get('blocked_stage') or item.get('pending_commit'):
                raise ValueError('No publishable final exists')
            if decision['final_revision']!=item.get('final_revision') or item.get('final_stamp')!=_stamp(item['jpeg_path']):
                raise ValueError('Review final revision is stale or its file metadata changed')
            if decision['passed'] and not item.get('technical_pass'):
                raise ValueError('Technical checks have not passed')
            pairs.append((item,decision))
        for item,decision in pairs:
            item['review']={'final_revision':decision['final_revision'],'passed':decision['passed'],'notes':decision['notes'],
                            'reviewer':decision.get('reviewer','codex'),'review_type':'visual','reviewed_at':_now(),
                            'severity':decision.get('severity','none'),'issues':list(decision.get('issues',[]))}
            if decision.get('viewing_long_edge') is not None:
                item['review']['viewing_long_edge']=decision['viewing_long_edge']
            if _review_valid(state,item) and item.get('technical_pass'):
                state.setdefault('first_approved_at',item['review']['reviewed_at'])
        _refresh_review(state)
        return _save(root,state)


def record_review(root,photo_id,final_revision,passed,notes,reviewer='codex',*,severity='none',viewing_long_edge=None,issues=None):
    decision={'id':photo_id,'final_revision':final_revision,'passed':passed,'notes':notes,'reviewer':reviewer,'severity':severity,'issues':issues or []}
    if viewing_long_edge is not None:
        decision['viewing_long_edge']=viewing_long_edge
    return record_reviews(root,[decision])


def stop_batch(root):
    return _change(root,lambda state:state.update(stop_requested=True,stopped_at=_now()))


def resume_batch(root,cv_runtime=None,model_dir=None,selected_ids=None):
    def resume(state):
        state['stop_requested']=False
        for item in state['items']:
            if item['status']=='error' and not item.get('pending_commit'):
                item['status']='planned' if item.get('plan') else 'queued'
                item.pop('error',None)
    state=_change(root,resume)
    if not state.get('preparation_complete'):
        recipe=dict(state['prepare_recipe'])
        policy=recipe.pop('raw_generation_input',None)
        if policy=='camera-input-v2':
            # The persisted fallback profile is not an explicit user override.
            recipe['raw_profile']=None
        state=prepare_batch(state['input_path'],root,brief=state.get('requested_edits'),**recipe)
    return run_batch(root,cv_runtime=cv_runtime,model_dir=model_dir,selected_ids=selected_ids)


def cleanup_batch(root,temporary_only=True):
    with _lock(root,'.runner.lock') as root:
        state=_recover(root)
        active=[Path(i['pending_commit']['temporary']) for i in state['items'] if i.get('pending_commit')]
        work=root/'.work'
        removed=[]
        if work.exists():
            for path in work.iterdir():
                if any(path==p or path in p.parents for p in active):
                    continue
                _safe(path)
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()
                removed.append(str(path))
        return _change(root,lambda latest:latest.update(last_cleanup={'at':_now(),'removed_temporary_entries':len(removed)}))


def batch_status(root):
    state=load_batch(root)
    result={key:state[key] for key in ('batch_id','root','summary','state_revision','plan_revision','plan_frozen','stop_requested')}
    result['items']=[{key:item[key] for key in ('id','status','error','final_path','jpeg_path','final_revision','technical_pass','review_reason') if key in item} for item in state['items']]
    return result
