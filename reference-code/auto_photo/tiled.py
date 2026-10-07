"""Four overlapping generated tiles, bounded registration and native FSRCNN.

This module performs only local image operations. The batch coordinator owns
versions, cancellation and review; standalone calls can keep a small stage
record. No image-generation service is called here.
"""
from __future__ import annotations

import argparse
import io
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import time

from PIL import Image, ImageCms, ImageFilter, ImageOps
from .artifacts import atomic_json, stamp

KEYS = ('TL', 'TR', 'BL', 'BR')
ICC = ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes()
PROJECT = Path(__file__).resolve().parent.parent


def rgb(path):
    with Image.open(path) as original:
        image = ImageOps.exif_transpose(original)
        profile = image.info.get('icc_profile')
        if profile:
            image = ImageCms.profileToProfile(
                image, ImageCms.ImageCmsProfile(io.BytesIO(profile)),
                ImageCms.createProfile('sRGB'), outputMode='RGB')
        return image.convert('RGB')


def info(path):
    with Image.open(path) as image:
        dimensions = list(image.size)
    return dict(stamp(path), dimensions=dimensions)


def _cv(directory=None):
    selected = directory or os.environ.get('AUTO_PHOTO_CV_RUNTIME')
    if selected and str(Path(selected).resolve()) not in sys.path:
        sys.path.insert(0, str(Path(selected).resolve()))
    import cv2
    import numpy as np
    return cv2, np


def _save_image(image, path, **options):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.stem + '-', dir=path.parent)
    os.close(fd)
    try:
        image.save(temporary, format='JPEG' if path.suffix.lower() in ('.jpg', '.jpeg') else 'PNG', **options)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _run(stage, output, recipe, write_record, operation):
    """Retry unfinished owned stages; only reuse a completed matching record."""
    out = Path(output).expanduser().resolve()
    record_path = out / (stage + '.json')
    previous = None
    if write_record and out.exists() and any(out.iterdir()):
        if record_path.is_file():
            previous = json.loads(record_path.read_text())
        if not previous or previous.get('schema_version') != 2:
            raise FileExistsError('Historical or unowned output directory; choose a new output')
        if previous.get('status') == 'completed':
            if previous.get('recipe') != recipe:
                raise FileExistsError('Completed stage has different inputs or settings; use a new output')
            outputs = previous.get('outputs', [])
            if outputs and all(Path(row['path']).is_file() and stamp(row['path']) == {k:row[k] for k in ('path','size','mtime_ns')} for row in outputs):
                return dict(previous, reused=True)
            raise FileExistsError('Published output changed; use a new output')
    out.mkdir(parents=True, exist_ok=True)
    record = dict(schema_version=2, stage=stage, status='running', recipe=recipe)
    if write_record:
        atomic_json(record_path, record)
    started = time.monotonic()
    try:
        result = operation(out)
    except BaseException as exc:
        record.update(status='interrupted' if isinstance(exc, (KeyboardInterrupt, SystemExit)) else 'failed',
                      error=f'{type(exc).__name__}: {exc}', elapsed_seconds=round(time.monotonic()-started, 3))
        if write_record:
            atomic_json(record_path, record)
        raise
    record.update(result, status='completed', elapsed_seconds=round(time.monotonic()-started, 3))
    if write_record:
        atomic_json(record_path, record)
    return record


def _source_output(source, output):
    source = Path(source).expanduser().resolve(strict=True)
    out = Path(output).expanduser().resolve()
    if out == source or out in source.parents:
        raise ValueError('Output must be a dedicated directory outside the input path')
    return source


def prepare(source, output, *, overlap=.12, write_record=True):
    source = _source_output(source, output)
    if source.suffix.lower() not in ('.jpg', '.jpeg', '.png'):
        raise ValueError('Source must be JPG or PNG')
    if isinstance(overlap, bool) or not isinstance(overlap, (int, float)) or not math.isfinite(overlap) or not 0 < overlap < .5:
        raise ValueError('Overlap must be a fraction between 0 and 0.5')
    recipe = {'source': stamp(source), 'overlap': overlap}
    def perform(out):
        image = rgb(source)
        w, h = image.size
        x0, x1 = round(w*(1-overlap)/2), round(w*(1+overlap)/2)
        y0, y1 = round(h*(1-overlap)/2), round(h*(1+overlap)/2)
        if x1 <= x0 or y1 <= y0:
            raise ValueError('Source too small for the requested tile overlap')
        boxes = {'TL': [0,0,x1,y1], 'TR': [x0,0,w,y1],
                 'BL': [0,y0,x1,h], 'BR': [x0,y0,w,h]}
        tiles = {}
        for key, box in boxes.items():
            path = out/'inputs'/f'{key}.png'
            _save_image(image.crop(box), path, icc_profile=ICC)
            tiles[key] = dict(info(path), crop_box=box)
        return {'source': info(source), 'oriented_source_dimensions': [w,h],
                'working_color_space': 'sRGB', 'overlap_full_source_fraction': overlap,
                'overlap_pixels': [x1-x0,y1-y0], 'tiles': tiles,
                'outputs': [tiles[key] for key in KEYS]}
    return _run('prepare', output, recipe, write_record, perform)


def framing(ref, mov, cv2, np, tolerance):
    def small(im):
        w,h=im.size; s=min(1,1200/max(w,h)); size=(max(1,round(w*s)),max(1,round(h*s)))
        return cv2.cvtColor(np.asarray(im.resize(size,Image.Resampling.LANCZOS)),cv2.COLOR_RGB2GRAY)
    r,m=small(ref),small(mov); sift=cv2.SIFT_create(nfeatures=7000)
    kr,dr=sift.detectAndCompute(r,None); km,dm=sift.detectAndCompute(m,None)
    if dr is None or dm is None: return None, {'passed':False,'reason':'insufficient SIFT texture'}
    pairs=cv2.BFMatcher().knnMatch(dm,dr,k=2)
    good=[q[0] for q in pairs if len(q)==2 and q[0].distance<.72*q[1].distance]
    if len(good)<25: return None, {'passed':False,'reason':'fewer than 25 ratio matches','ratio_matches':len(good)}
    src=np.float32([km[q.queryIdx].pt for q in good]); dst=np.float32([kr[q.trainIdx].pt for q in good])
    mat,mask=cv2.estimateAffinePartial2D(src,dst,method=cv2.RANSAC,ransacReprojThreshold=3)
    if mat is None or mask is None: return None, {'passed':False,'reason':'affine RANSAC failed'}
    rh,rw=r.shape; mh,mw=m.shape
    corners=np.float32([[0,0],[mw,0],[mw,mh],[0,mh]])
    expect=np.float32([[0,0],[rw,0],[rw,rh],[0,rh]])
    error=np.linalg.norm(cv2.transform(corners[None],mat)[0]-expect,axis=1)/np.hypot(rw,rh)
    passed=int(mask.sum())>=25 and float(mask.mean())>.35 and float(error.max())<tolerance
    report={'passed':passed,'ratio_matches':len(good),'inliers':int(mask.sum()),'inlier_fraction':float(mask.mean()),'corner_error_fraction_diagonal':error.tolist(),'max_corner_error':float(error.max()),'corner_tolerance':tolerance,'matrix_small':mat.tolist(),'reference_small_dimensions':[rw,rh],'moving_small_dimensions':[mw,mh]}
    return (mat,(rw,rh),(mw,mh)),report

def ecc_refine(ref, aligned, cv2, np):
    h,w=aligned.shape[:2]; sw=min(900,w); sh=max(1,round(h*sw/w))
    if min(sw, sh) < 32: return np.eye(3), {'accepted':False, 'reason':'small image'}
    def edges(a):
        gray=cv2.cvtColor(cv2.resize(a,(sw,sh)),cv2.COLOR_RGB2GRAY).astype('float32')/255
        return cv2.GaussianBlur(cv2.magnitude(cv2.Sobel(gray,cv2.CV_32F,1,0),cv2.Sobel(gray,cv2.CV_32F,0,1)),(0,0),.7)
    re,me=edges(ref),edges(aligned); before=float(np.mean(np.abs(re-me))); identity=np.eye(3)
    report={'accepted':False,'edge_error_before':before,'optical_flow':False}
    try:
        corr,mat=cv2.findTransformECC(re,me,np.eye(2,3,dtype='float32'),cv2.MOTION_AFFINE,(cv2.TERM_CRITERIA_EPS|cv2.TERM_CRITERIA_COUNT,100,1e-6),None,5)
        cand=cv2.warpAffine(me,mat,(sw,sh),flags=cv2.INTER_LINEAR|cv2.WARP_INVERSE_MAP,borderMode=cv2.BORDER_CONSTANT)
        # Compare interior to avoid black-padding improvement artifacts.
        margin=12; region=(slice(margin,-margin),slice(margin,-margin))
        before=float(np.mean(np.abs(re[region]-me[region])));after=float(np.mean(np.abs(re[region]-cand[region])))
        corners=np.float32([[0,0],[sw,0],[sw,sh],[0,sh]])
        shift=float(np.linalg.norm(cv2.transform(corners[None],mat)[0]-corners,axis=1).max())
        singular=np.linalg.svd(mat[:,:2],compute_uv=False)
        accepted=bool(after<before*.99 and shift<.025*np.hypot(sw,sh) and singular.min()>.97 and singular.max()<1.03)
        report.update(accepted=accepted,correlation=float(corr),edge_error_before=before,edge_error_after=after,max_corner_shift_small_pixels=shift,matrix_small=mat.tolist())
        if accepted:
            m=np.eye(3);m[:2]=mat
            full=np.diag([w/sw,h/sh,1])@m@np.diag([sw/w,sh/h,1])
            return np.linalg.inv(full),report
    except cv2.error as e: report['reason']='ECC failed: '+str(e).split('\n')[0]
    return identity,report

def tone_offsets(aligned,boxes,np):
    samples=[]
    for a,b in [('TL','TR'),('TL','BL'),('TR','BR'),('BL','BR')]:
        ba,bb=boxes[a],boxes[b];x0,y0=max(ba[0],bb[0]),max(ba[1],bb[1]);x1,y1=min(ba[2],bb[2]),min(ba[3],bb[3])
        aa=aligned[a][y0-ba[1]:y1-ba[1]:12,x0-ba[0]:x1-ba[0]:12].reshape(-1,3)/255
        ab=aligned[b][y0-bb[1]:y1-bb[1]:12,x0-bb[0]:x1-bb[0]:12].reshape(-1,3)/255
        valid=(aa.min(1)>.015)&(ab.min(1)>.015)&(aa.max(1)<.98)&(ab.max(1)<.98)
        samples.append((a,b,aa[valid],ab[valid]))
    offsets={k:np.zeros(3) for k in KEYS}
    if any(len(aa)<20 for a,b,aa,ab in samples):return offsets,{'accepted':False,'reason':'insufficient overlap samples'}
    for ch in range(3):
        arrays=[];targets=[]
        for a,b,aa,ab in samples:
            A=np.zeros((len(aa),3))
            if a!='TL':A[:,KEYS.index(a)-1]=1
            if b!='TL':A[:,KEYS.index(b)-1]=-1
            arrays.append(A);targets.append(ab[:,ch]-aa[:,ch])
        A=np.concatenate(arrays);y=np.concatenate(targets);weights=np.ones(len(y))
        for it in range(8):
            rw=np.sqrt(weights);x=np.linalg.lstsq(np.vstack((A*rw[:,None],np.eye(3)*3)),np.r_[y*rw,np.zeros(3)],rcond=None)[0]
            x=np.clip(x,-.08,.08);weights=np.minimum(1,.025/(np.abs(A@x-y)+1e-8))
        for k in KEYS[1:]:offsets[k][ch]=x[KEYS.index(k)-1]
    def score(corrected):
        return {a+'-'+b:float(np.median(np.abs(aa+offsets[a]*corrected-ab-offsets[b]*corrected))*255) for a,b,aa,ab in samples}
    before,after=score(False),score(True);accepted=bool(np.mean(list(after.values()))<np.mean(list(before.values()))*.95)
    return offsets,{'accepted':accepted,'method':'robust IRLS RGB offsets, TL anchor','offset_bounds':[-.08,.08],'offsets_normalized':{k:v.tolist() for k,v in offsets.items()},'before_median_abs_levels':before,'after_median_abs_levels':after,'required_improvement_fraction':.05}

def merge(prepared, tiles, output, *, cv_runtime=None, corner_tolerance=.025,
          diagnostics=False, write_record=True):
    """Register four native generations and preserve their actual sampling size."""
    if isinstance(prepared, (str, Path)):
        path = Path(prepared)
        prepared = json.loads((path/'prepare.json' if path.is_dir() else path).read_text())
    if set(tiles) != set(KEYS) or set(prepared['tiles']) != set(KEYS):
        raise ValueError('Exactly TL, TR, BL and BR are required')
    if not isinstance(corner_tolerance, (int, float)) or isinstance(corner_tolerance, bool) or not 0 < corner_tolerance < .1:
        raise ValueError('Corner tolerance must be between 0 and 0.1')
    paths = {key: _source_output(value['path'] if isinstance(value, dict) else value, output)
             for key, value in tiles.items()}
    references = {key: _source_output(prepared['tiles'][key]['path'], output) for key in KEYS}
    w, h = prepared['oriented_source_dimensions']
    recipe = {'tiles': {key: stamp(paths[key]) for key in KEYS},
              'references': {key: stamp(references[key]) for key in KEYS},
              'source_dimensions': [w,h],
              'crop_boxes': {key: prepared['tiles'][key]['crop_box'] for key in KEYS},
              'corner_tolerance': corner_tolerance, 'diagnostics': diagnostics}

    def perform(out):
        cv2, np = _cv(cv_runtime)
        report = {'method': 'SIFT similarity, bounded ECC affine, RGB offset and cosine feather',
                  'tiles': {}, 'outputs': []}
        images, refs, registrations, densities = {}, {}, {}, []
        for key in KEYS:
            ref, moving = rgb(references[key]), rgb(paths[key])
            images[key], refs[key] = moving, ref
            registration, check = framing(ref, moving, cv2, np, corner_tolerance)
            report['tiles'][key] = {'native': info(paths[key]),
                                     'crop_box': prepared['tiles'][key]['crop_box'],
                                     'framing': check}
            if not check['passed']:
                raise ValueError(f'{key}: crop/framing check failed ({check.get("reason", "corner mismatch")})')
            registrations[key] = registration
            densities.append(min(moving.width/ref.width, moving.height/ref.height))
        scale = min(densities)
        W, H = round(w*scale), round(h*scale)
        boxes = {key: [round(value*(W/w if i%2 == 0 else H/h))
                       for i, value in enumerate(prepared['tiles'][key]['crop_box'])] for key in KEYS}
        def left(length, start, end):
            if end <= start:
                raise ValueError('No overlap at the generated sampling size')
            t = np.clip((np.arange(length)-start)/(end-start), 0, 1)
            return (.5+.5*np.cos(np.pi*t)).astype('float32')
        lx = left(W, boxes['TR'][0], boxes['TL'][2])
        ly = left(H, boxes['BL'][1], boxes['TL'][3])
        weights = {'TL': ly[:,None]*lx, 'TR': ly[:,None]*(1-lx),
                   'BL': (1-ly[:,None])*lx, 'BR': (1-ly[:,None])*(1-lx)}
        total = np.zeros((H,W,3), np.float32)
        count = np.zeros((H,W), np.float32)
        aligned, valid_masks, resized_refs = {}, {}, {}
        for key in KEYS:
            x0,y0,x1,y1 = boxes[key]
            tw, th = x1-x0, y1-y0
            moving = images[key]
            matrix, rs, ms = registrations[key]
            affine = np.eye(3)
            affine[:2] = matrix
            transform = np.diag([tw/rs[0],th/rs[1],1]) @ affine @ np.diag([ms[0]/moving.width,ms[1]/moving.height,1])
            reference = np.asarray(refs[key].resize((tw,th), Image.Resampling.LANCZOS))
            preview = cv2.warpAffine(np.asarray(moving), transform[:2], (tw,th), flags=cv2.INTER_LANCZOS4, borderMode=cv2.BORDER_CONSTANT)
            refinement, detail = ecc_refine(reference, preview, cv2, np)
            transform = refinement @ transform
            pixels = cv2.warpAffine(np.asarray(moving), transform[:2], (tw,th), flags=cv2.INTER_LANCZOS4, borderMode=cv2.BORDER_CONSTANT).astype('float32')
            valid = cv2.warpAffine(np.ones((moving.height,moving.width), np.uint8), transform[:2], (tw,th), flags=cv2.INTER_NEAREST, borderMode=cv2.BORDER_CONSTANT)
            pixels[valid == 0] = reference[valid == 0]
            aligned[key], valid_masks[key], resized_refs[key] = pixels, valid > 0, reference
            weight = weights[key][y0:y1,x0:x1]
            total[y0:y1,x0:x1] += pixels*weight[...,None]
            count[y0:y1,x0:x1] += weight
            report['tiles'][key].update(canvas_box=boxes[key], native_to_registered_affine=transform[:2].tolist(), ecc=detail,
                                        source_gap_fill_fraction=float((valid == 0).mean()))
            if diagnostics:
                path = out/f'{key}-registered.png'
                _save_image(Image.fromarray(np.rint(np.clip(pixels,0,255)).astype('uint8')), path, icc_profile=ICC)
                artifact = info(path)
                report['tiles'][key]['registered'] = artifact
                report['outputs'].append(artifact)
        if count.min() < .999:
            raise ValueError('Uncovered output pixels')
        if diagnostics:
            uncorrected = out/'native-merged-uncorrected.png'
            _save_image(Image.fromarray(np.rint(np.clip(total/count[...,None],0,255)).astype('uint8')), uncorrected, icc_profile=ICC)
            report['uncorrected_output'] = info(uncorrected)
            report['outputs'].append(report['uncorrected_output'])
        offsets, tone = tone_offsets(aligned, boxes, np)
        if tone['accepted']:
            total.fill(0)
            for key in KEYS:
                pixels = np.clip(aligned[key]+offsets[key]*255, 0, 255)
                pixels[~valid_masks[key]] = resized_refs[key][~valid_masks[key]]
                x0,y0,x1,y1 = boxes[key]
                total[y0:y1,x0:x1] += pixels*weights[key][y0:y1,x0:x1,None]
        destination = out/'native-merged.png'
        result = Image.fromarray(np.rint(np.clip(total/count[...,None],0,255)).astype('uint8'))
        _save_image(result, destination, icc_profile=ICC)
        artifact = info(destination)
        report['outputs'].append(artifact)
        report.update(final_path=str(destination), output=artifact, dimensions=[W,H],
                      canvas_dimensions=[W,H], sampling_density=scale,
                      tone_matching=tone, color_matching_applied=tone['accepted'])
        return report
    return _run('merge', output, recipe, write_record, perform)


def fsrcnn_array(image, model_path, scale=2, *, cv_runtime=None, tile_size=512, halo=32):
    """Run the native model in overlapping chunks, returning RGB uint8 pixels."""
    if type(scale) is not int or scale not in (2, 4):
        raise ValueError('FSRCNN requires a native 2x or 4x model')
    if type(tile_size) is not int or tile_size < 1 or type(halo) is not int or halo < 0:
        raise ValueError('Tile size must be positive and halo nonnegative')
    model = Path(model_path).expanduser().resolve(strict=True)
    cv2, np = _cv(cv_runtime)
    pixels = np.asarray(image)
    if pixels.ndim != 3 or pixels.shape[2] != 3 or pixels.dtype != np.uint8:
        raise ValueError('FSRCNN input must be RGB uint8')
    h, w = pixels.shape[:2]
    sr = cv2.dnn_superres.DnnSuperResImpl_create()
    sr.readModel(str(model))
    sr.setModel('fsrcnn', scale)
    result = np.empty((h*scale,w*scale,3), np.uint8)
    for y in range(0,h,tile_size):
        for x in range(0,w,tile_size):
            x1, y1 = min(w,x+tile_size), min(h,y+tile_size)
            lx, ly, rx, ry = max(0,x-halo), max(0,y-halo), min(w,x1+halo), min(h,y1+halo)
            patch = cv2.cvtColor(sr.upsample(cv2.cvtColor(pixels[ly:ry,lx:rx],cv2.COLOR_RGB2BGR)),cv2.COLOR_BGR2RGB)
            if patch.shape != ((ry-ly)*scale,(rx-lx)*scale,3):
                raise ValueError('FSRCNN returned unexpected native dimensions')
            result[y*scale:y1*scale,x*scale:x1*scale] = patch[(y-ly)*scale:(y1-ly)*scale,(x-lx)*scale:(x1-lx)*scale]
    return result


def upscale(source, output, *, model_path=None, cv_runtime=None, sharpen=False,
            diagnostics=False, write_record=True):
    source = _source_output(source, output)
    model = Path(model_path or PROJECT/'.tools/fsrcnn/FSRCNN_x2.pb').expanduser().resolve(strict=True)
    recipe = {'source': stamp(source), 'model': stamp(model), 'scale': 2,
              'sharpen': bool(sharpen), 'diagnostics': bool(diagnostics)}
    def perform(out):
        image = rgb(source)
        result = Image.fromarray(fsrcnn_array(image, model, 2, cv_runtime=cv_runtime))
        outputs = []
        if diagnostics and sharpen:
            _save_image(result, out/'upscaled.png', icc_profile=ICC)
            outputs.append(info(out/'upscaled.png'))
        if sharpen:
            result = result.filter(ImageFilter.UnsharpMask(radius=1.2,percent=80,threshold=2))
        png, jpg = out/'final.png', out/'final.jpg'
        _save_image(result, png, icc_profile=ICC)
        _save_image(result, jpg, quality=96, subsampling=0, icc_profile=ICC)
        outputs.extend([info(png), info(jpg)])
        return {'final_path': str(png), 'jpeg_path': str(jpg), 'output': info(png),
                'input_dimensions': list(image.size), 'dimensions': list(result.size),
                'scale': 2, 'native_model_scale': True, 'tile_size': 512,
                'context_pixels': 32, 'outputs': outputs}
    return _run('upscale', output, recipe, write_record, perform)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    p = commands.add_parser('prepare')
    p.add_argument('source'); p.add_argument('--output', required=True)
    p.add_argument('--overlap', type=float, default=.12)
    p = commands.add_parser('merge')
    p.add_argument('--prepared', required=True); p.add_argument('--output', required=True)
    for key in KEYS:
        p.add_argument('--'+key.lower(), required=True)
    p.add_argument('--corner-tolerance', type=float, default=.025)
    p.add_argument('--diagnostics', action='store_true')
    p.add_argument('--cv-runtime', default=str(PROJECT/'.tools/fsrcnn-python') if (PROJECT/'.tools/fsrcnn-python').is_dir() else None)
    p = commands.add_parser('upscale')
    p.add_argument('source'); p.add_argument('--output', required=True)
    p.add_argument('--model', dest='model_path'); p.add_argument('--cv-runtime', default=str(PROJECT/'.tools/fsrcnn-python') if (PROJECT/'.tools/fsrcnn-python').is_dir() else None)
    p.add_argument('--sharpen', action='store_true'); p.add_argument('--diagnostics', action='store_true')
    args = vars(parser.parse_args(argv))
    command = args.pop('command')
    try:
        if command == 'merge':
            args['tiles'] = {key: args.pop(key.lower()) for key in KEYS}
            result = merge(**args)
        elif command == 'prepare':
            result = prepare(**args)
        else:
            result = upscale(**args)
    except (ValueError, OSError, ImportError) as exc:
        parser.exit(2, f'Error: {exc}\n')
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
