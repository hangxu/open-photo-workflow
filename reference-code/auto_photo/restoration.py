"""Locally registered original-pixel restoration. Failed gates require review."""
from __future__ import annotations
import time
from pathlib import Path
from PIL import Image, ImageOps
from .superres import runtime, safe_output, cached, save_record, load_srgb, SRGB_BYTES, versions, publish_image, image_artifact
from .artifacts import stamp

VERSION = 'local-restore-6'
DEFAULTS = dict(reference_max=1800, min_inliers=8, min_inlier_ratio=.5,
                min_coverage=.02, max_median_residual_px=3., max_p95_residual_px=6.,
                min_scale=.85, max_scale=1.15, max_rotation_deg=10.,
                max_displacement_fraction=.08, ratio_test=.75, ransac_px=2.,
                feather_px=8., exclusion_margin_px=3., gain_min=.75, gain_max=1.85,
                max_boundary_delta_e_median=3., max_boundary_delta_e_p95=8.)

def boundary_metrics(candidate, base, support, *, feather_px):
    """CIE76 low-frequency boundary mismatch; thresholds remain review heuristics.

    A boundary band rather than the full region limits sensitivity to deliberately
    restored lettering. Compare unblended donor colors so feather cannot hide a
    large mismatch. Exclude image edges where no outside boundary exists.
    """
    cv2,np=runtime()
    dist=cv2.distanceTransform(np.pad(support,1),cv2.DIST_L2,3)[1:-1,1:-1]
    band=(dist>0)&(dist<=2*feather_px)
    edge=max(1,int(feather_px));band[:edge]=False;band[-edge:]=False;band[:,:edge]=False;band[:,-edge:]=False
    sigma=max(2.,feather_px)
    weight=(support>0).astype(np.float32)
    denom=np.maximum(cv2.GaussianBlur(weight,(0,0),sigma),1e-6)[:,:,None]
    ca=cv2.GaussianBlur(candidate.astype(np.float32)/255*weight[:,:,None],(0,0),sigma)/denom
    ba=cv2.GaussianBlur(base.astype(np.float32)/255*weight[:,:,None],(0,0),sigma)/denom
    delta=np.linalg.norm(cv2.cvtColor(ca,cv2.COLOR_RGB2LAB)-cv2.cvtColor(ba,cv2.COLOR_RGB2LAB),axis=2)
    if not np.any(band):return {'boundary_delta_e_median':None,'boundary_delta_e_p95':None,'boundary_sample_pixels':0}
    return {'boundary_delta_e_median':float(np.median(delta[band])),
            'boundary_delta_e_p95':float(np.percentile(delta[band],95)),
            'boundary_sample_pixels':int(band.sum())}

def validate(regions):
    from .plan import _regions
    if not regions: raise ValueError('At least one explicit restoration region is required')
    return _regions(regions)

def restore_regions(source_path, base_path, output_dir, regions, *, config=None, diagnostics=False, write_record=True):
    if type(diagnostics) is not bool or type(write_record) is not bool:
        raise ValueError('diagnostics and write_record must be booleans')
    regions = validate(regions)
    cfg=dict(DEFAULTS)
    if config:
        if set(config)-set(cfg): raise ValueError('Unknown restoration config keys')
        cfg.update(config)
    if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not __import__('math').isfinite(v) or v<=0 for v in cfg.values()): raise ValueError('Config values must be finite and positive')
    source=Path(source_path).resolve(strict=True); basefile=Path(base_path).resolve(strict=True)
    out=safe_output(output_dir,[source,basefile])
    recipe=dict(version=VERSION, runtime_versions=versions(), source_stamp=stamp(source),base_stamp=stamp(basefile),regions=regions,config=cfg,diagnostics=diagnostics)
    hit=cached(out,recipe)
    if hit:return hit
    cv2,np=runtime()
    src=np.asarray(load_srgb(source))
    base=np.asarray(load_srgb(basefile))
    h,w=base.shape[:2]; sh,sw=src.shape[:2]
    if abs((sw/sh)/(w/h)-1) > .02:
        raise ValueError('Source/base aspect ratios differ by more than 2%; review orientation and framing')
    factor=min(1,cfg['reference_max']/max(w,h)); rw,rh=max(1,round(w*factor)),max(1,round(h*factor))
    gs=cv2.cvtColor(cv2.resize(src,(rw,rh),interpolation=cv2.INTER_AREA),cv2.COLOR_RGB2GRAY)
    gt=cv2.cvtColor(cv2.resize(base,(rw,rh),interpolation=cv2.INTER_AREA),cv2.COLOR_RGB2GRAY)
    out.mkdir(parents=True,exist_ok=True); start=time.monotonic(); final=base.copy(); pure=base.copy() if diagnostics else None; union=np.zeros((h,w),np.uint8); records=[]
    sift=cv2.SIFT_create(nfeatures=12000,contrastThreshold=.008)
    def fill(mask,poly,value=255):cv2.fillPoly(mask,[np.round(poly).astype(np.int32)],value)
    for index,r in enumerate(regions):
        row=dict(id=r['id'],kind=r['kind'],status='needs_review',thresholds=cfg)
        poly=np.asarray(r['polygon'],np.float32)*[rw-1,rh-1]
        roi=r.get('roi') or [max(0,float(poly[:,0].min()/rw)-.04),max(0,float(poly[:,1].min()/rh)-.04),min(1,float(poly[:,0].max()/rw)+.04),min(1,float(poly[:,1].max()/rh)+.04)]
        mask=np.zeros((rh,rw),np.uint8); x1,y1,x2,y2=np.round(np.asarray(roi)*[rw,rh,rw,rh]).astype(int); mask[y1:y2,x1:x2]=255
        source_feature_mask=mask.copy(); target_feature_mask=mask.copy()
        for ep in r.get('exclude',[]): fill(source_feature_mask,np.asarray(ep)*[rw-1,rh-1],0)
        for ep in r.get('target_exclude',[]): fill(target_feature_mask,np.asarray(ep)*[rw-1,rh-1],0)
        k1,d1=sift.detectAndCompute(gs,source_feature_mask); k2,d2=sift.detectAndCompute(gt,target_feature_mask)
        good=[]
        if d1 is not None and d2 is not None and len(d2)>1:
            good=[pair[0] for pair in cv2.BFMatcher().knnMatch(d1,d2,k=2) if len(pair)==2 and pair[0].distance<cfg['ratio_test']*pair[1].distance]
        row['matches']=len(good); reasons=[]; T=None
        if len(good)>=3:
            p=np.float32([k1[m.queryIdx].pt for m in good]);q=np.float32([k2[m.trainIdx].pt for m in good])
            a,ins=cv2.estimateAffinePartial2D(p,q,method=cv2.RANSAC,ransacReprojThreshold=cfg['ransac_px'],maxIters=10000)
            if a is not None:
                T=np.vstack([a,[0,0,1]]); selected=ins.ravel().astype(bool); n=int(selected.sum())
                errors=(cv2.transform(p[None],a)[0]-q)*[w/rw,h/rh]; residual=np.linalg.norm(errors[selected],axis=1)
                coverage=float(cv2.contourArea(cv2.convexHull(p[selected]))/max(1,(x2-x1)*(y2-y1))) if n>=3 else 0.
                scale=float(np.hypot(a[0,0],a[1,0])); rotation=float(np.degrees(np.arctan2(a[1,0],a[0,0])))
                displacement=float(np.max(np.linalg.norm((cv2.transform(poly[None].astype(np.float32),a)[0]-poly)/[rw,rh],axis=1)))
                row.update(inliers=n,inlier_ratio=n/len(good),coverage=coverage,scale=scale,rotation_deg=rotation,median_residual_px=float(np.median(residual)),p95_residual_px=float(np.percentile(residual,95)),displacement_fraction=displacement,method='SIFT similarity RANSAC')
                checks={'inliers':n>=cfg['min_inliers'],'ratio':row['inlier_ratio']>=cfg['min_inlier_ratio'],'coverage':coverage>=cfg['min_coverage'],'median':row['median_residual_px']<=cfg['max_median_residual_px'],'p95':row['p95_residual_px']<=cfg['max_p95_residual_px'],'scale':cfg['min_scale']<=scale<=cfg['max_scale'],'rotation':abs(rotation)<=cfg['max_rotation_deg'],'displacement':displacement<=cfg['max_displacement_fraction']}
                reasons=[name for name,passed in checks.items() if not passed]
        if T is None: reasons=['insufficient stable feature correspondences']
        # Residual statistics are review hints once enough consistent features
        # establish bounded geometry. Missing/unstable registration still blocks
        # restoration; a visible seam is judged at the requested delivery scale.
        hard_reasons=[reason for reason in reasons if reason not in ('median','p95')]
        registration_warnings=[reason for reason in reasons if reason in ('median','p95')]
        if hard_reasons:
            row['failed_gates']=reasons;row['technical_pass']=False;records.append(row)
            if diagnostics:
                publish_image(Image.fromarray(mask),out/f'region-{index}-registration-roi.png')
            continue
        H=np.diag([w/rw,h/rh,1.])@T@np.diag([rw/sw,rh/sh,1.])
        # Exactly one geometric sample from full-resolution original; no sharpening or reconstruction.
        effective_scale=float(np.min(np.linalg.svd(H[:2,:2],compute_uv=False)))
        sigma=.5*float(np.sqrt(max(0.,1/effective_scale**2-1)))
        filtered=cv2.GaussianBlur(src,(0,0),sigma) if sigma>.01 else src
        donor=cv2.warpPerspective(filtered,H,(w,h),flags=cv2.INTER_LANCZOS4,borderMode=cv2.BORDER_CONSTANT)
        row.update(prefilter_sigma_source_px=sigma,sampling='source Gaussian antialias then one LANCZOS4 geometric sample')
        support=np.zeros((h,w),np.uint8)
        sourcepoly=np.asarray(r['polygon'],np.float32)*[sw-1,sh-1]
        fill(support,cv2.perspectiveTransform(sourcepoly[None].astype(np.float32),H)[0])
        validity=cv2.warpPerspective(np.full((sh,sw),255,np.uint8),H,(w,h),flags=cv2.INTER_NEAREST)
        support[validity==0]=0
        exclusion=np.zeros_like(support)
        for ep in r.get('exclude',[]):
            pts=np.asarray(ep,np.float32)*[sw-1,sh-1]; fill(exclusion,cv2.perspectiveTransform(pts[None].astype(np.float32),H)[0])
        for ep in r.get('target_exclude',[]): fill(exclusion,np.asarray(ep)*[w-1,h-1])
        radius=int(__import__('math').ceil(cfg['exclusion_margin_px'])); exclusion=cv2.dilate(exclusion,np.ones((2*radius+1,2*radius+1),np.uint8))
        support[exclusion>0]=0
        # A zero border ensures feathering remains inward even for edge-touching polygons.
        dist=cv2.distanceTransform(np.pad(support,1),cv2.DIST_L2,3)[1:-1,1:-1]
        feather=max(cfg['feather_px'],12*w/1280)
        alpha=np.minimum(dist/feather,1)[:,:,None]
        # Low-frequency RGB gain keeps original stroke contrast and blur.  The
        # historical B recipe used these bounded gains; v2's +/-10% cap left
        # conspicuous exposure/color rectangles when generated lighting changed.
        original=donor.astype(np.float32); photometric_sigma=max(3.,35*w/1280)
        weight=(support>0).astype(np.float32)
        denom=np.maximum(cv2.GaussianBlur(weight,(0,0),photometric_sigma),1e-6)[:,:,None]
        source_low=cv2.GaussianBlur(original*weight[:,:,None],(0,0),photometric_sigma)/denom
        base_low=cv2.GaussianBlur(base.astype(np.float32)*weight[:,:,None],(0,0),photometric_sigma)/denom
        gain=np.clip((base_low+8)/(source_low+8),cfg['gain_min'],cfg['gain_max'])
        proposed=original*gain
        corrected=np.where(proposed<=245,proposed,245+10*(-np.expm1(-(np.maximum(proposed,245)-245)/10)))
        corrected=np.minimum(corrected,np.where(original<255,254.,255.))
        corrected=np.where(original==0,0,np.maximum(corrected,1))
        metrics=boundary_metrics(corrected,base,support,feather_px=feather)
        row.update(metrics,photometric_sigma_px=photometric_sigma,feather_px=feather,
                   photometric='bounded low-frequency RGB multiplicative adaptation; black preservation and highlight shoulder')
        color_fail=list(registration_warnings)
        for metric,limit in [('boundary_delta_e_median','max_boundary_delta_e_median'),('boundary_delta_e_p95','max_boundary_delta_e_p95')]:
            if metrics[metric] is None or metrics[metric]>cfg[limit]:color_fail.append(metric)
        if diagnostics:
            pure=np.round(original*alpha+pure*(1-alpha)).astype(np.uint8)
        final=np.round(corrected*alpha+final*(1-alpha)).astype(np.uint8)
        union=np.maximum(union,support)
        if diagnostics:
            publish_image(Image.fromarray(support),out/f'region-{index}-mask.png')
        row.update(status='completed',source_to_output=H.tolist(),mask_pixels=int(np.count_nonzero(support)))
        row['technical_pass']=bool(row['mask_pixels'])
        if not row['mask_pixels']: color_fail.append('empty visible support')
        if color_fail:row.update(status='needs_review',failed_gates=color_fail)
        records.append(row)
    failed=[r['id'] for r in records if r['status']!='completed']
    status='needs_review' if failed else 'completed'
    if stamp(source)!=recipe['source_stamp'] or stamp(basefile)!=recipe['base_stamp']:
        raise ValueError('Input changed during processing; outputs cannot be published')
    # Failed gates remain needs_review, even when a partial result is available.
    arrays = [('final.png',final)]
    if diagnostics:
        arrays.extend([('pure.png',pure),('mask.png',union)])
    for name,array in arrays:
        publish_image(Image.fromarray(array),out/name,icc_profile=SRGB_BYTES if name!='mask.png' else None)
    publish_image(Image.fromarray(final),out/'final.jpg',quality=96,subsampling=0,icc_profile=SRGB_BYTES)
    artifacts={}
    for path in out.iterdir():
        if path.is_file():
            with Image.open(path) as image:
                artifacts[path.name]=image_artifact(path,image.size)
    record=dict(recipe,recipe=recipe,color_space='sRGB; outside-mask equality measured against ICC-converted upright base',status=status,failed_regions=failed,region_results=records,output_dir=str(out),final_path=str(out/'final.png'),jpeg_path=str(out/'final.jpg'),output_stamp=stamp(out/'final.png'),dimensions=[w,h],source_dimensions=[sw,sh],seconds=time.monotonic()-start,outside_mask_equal=bool(np.array_equal(final[union==0],base[union==0])),artifacts=artifacts,limitations=['JPEG is lossy; exact mask-outside equality applies to final.png only','Preserves source blur; no OCR or hidden-content reconstruction','Gate thresholds are recorded heuristics requiring visual review for documentary use'])
    record['technical_pass']=bool(records) and all(r.get('technical_pass',False) for r in records)
    record['review_warnings']=[{'id':r['id'],'failed_gates':r['failed_gates']} for r in records if r.get('failed_gates')]
    return save_record(out,record,write_record=write_record)
