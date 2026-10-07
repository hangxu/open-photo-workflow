"""Camera JPEG preparation and creative RAW development; no image generation."""
import argparse
import json
from pathlib import Path
import shutil
import tempfile
import time

from PIL import Image, ImageCms

from .artifacts import atomic_json, stamp
from .camera_input import normalize_jpeg, paired_candidate, raw_source, read_raw_preview
from .raw_control import render_control
from .sources import _read_metadata


def inspect_raw(source):
    return read_raw_preview(source)[0]


def camera_base_control(probe):
    """A deterministic starting image for downstream AI, without an artistic look."""
    return {'baseline':'camera_matched' if probe['embedded_camera_preview'] else 'automatic',
            'recipe':{'preset':'neutral'}, 'capture_sharpening':True,
            'denoise':{'luma':0,'chroma':15,'detail':50}}


def render_camera_base(source, output, preview_edge=None, executable=None, *,
                       paired_jpeg=None, force_raw=False, min_edge=3840, write_record=True):
    """Prefer a usable camera JPEG; develop RAW only when needed or requested.

    paired_jpeg=None discovers an unambiguous sibling; False disables discovery;
    a path requests a particular pair (conflicting EXIF still rejects it).
    preview_edge creates a separate small preview, never a reduced final input.
    """
    if type(min_edge) is not int or min_edge < 1:
        raise ValueError('min_edge must be a positive integer')
    if preview_edge is not None and (type(preview_edge) is not int or preview_edge < 1):
        raise ValueError('preview_edge must be a positive integer')
    if type(force_raw) is not bool or type(write_record) is not bool:
        raise ValueError('force_raw and write_record must be boolean')
    started = time.monotonic()
    source = raw_source(source)
    before = stamp(source)
    root = Path(output).expanduser().absolute()
    if root.exists():
        raise FileExistsError('Use a new camera-base output directory')
    metadata = _read_metadata(source)
    reasons, probe, selected, details = [], None, None, None
    selected_source, selected_stamp, source_kind = None, None, None
    if not force_raw:
        candidate, attempt = paired_candidate(source, paired_jpeg, metadata)
        if candidate:
            try:
                candidate_stamp = stamp(candidate)
                selected = normalize_jpeg(candidate.read_bytes(), min_edge)
                details = selected[2]
                selected_source, selected_stamp, source_kind = str(candidate), candidate_stamp, 'paired_jpeg'
                attempt.update(accepted=True, dimensions=details['dimensions'])
            except (OSError, ValueError) as exc:
                attempt.update(accepted=False, reason=str(exc))
        reasons.append(attempt)
    else:
        reasons.append({'stage':'selection', 'reason':'explicit force_raw override'})
    if selected is None:
        probe, embedded = read_raw_preview(source)
        if not force_raw and embedded is not None:
            try:
                selected = normalize_jpeg(embedded, min_edge,
                    inherited_color=metadata.get('color'), raw_flip=probe['raw_flip'],
                    raw_dimensions=probe['raw_metadata_dimensions'])
                details = selected[2]
                selected_source, selected_stamp, source_kind = str(source), before, 'embedded_jpeg'
                reasons.append({'stage':'embedded_jpeg','accepted':True,
                                'dimensions':details['dimensions']})
            except (OSError, ValueError) as exc:
                reasons.append({'stage':'embedded_jpeg','accepted':False,'reason':str(exc)})
        elif not force_raw:
            reasons.append({'stage':'embedded_jpeg','accepted':False,
                            'reason':probe.get('embedded_unavailable_reason','No usable embedded JPEG')})
    root.mkdir(parents=True, exist_ok=False)
    target = root / 'final.jpg'
    if selected is not None:
        pixels, original_bytes, details = selected
        if stamp(source) != before or stamp(selected_source) != selected_stamp:
            raise ValueError('Source changed during camera input preparation')
        if original_bytes is not None:
            target.write_bytes(original_bytes)
        else:
            pixels.save(target, 'JPEG', quality=96, subsampling=0,
                        icc_profile=ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes())
        record = {'status':'completed','engine':'camera-jpeg-preparation',
                  'source_stamp':before,'dimensions':details['dimensions'],
                  'technical_pass':True,'effective_parameters':{},
                  'qa':{'technical_pass':True,'visual_review':'not_performed',
                        'final_jpeg_encodings':details['new_jpeg_encodings']}}
    else:
        source_kind, selected_source, selected_stamp = 'raw_render', str(source), before
        control = camera_base_control(probe)
        # Keep only the final JPEG and useful controls, not per-stage records.
        with tempfile.TemporaryDirectory(prefix='.raw-fallback-',dir=root) as temporary:
            workspace = Path(temporary)/'development'
            result = render_control(source,workspace,control,None,
                                    include_record=True,executable=executable)
            record = result['engine_record']
            if record.get('status') != 'completed':
                raise ValueError(record.get('reason') or record.get('error') or 'RAW fallback failed')
            if stamp(source) != before:
                raise ValueError('Source changed during camera input preparation')
            shutil.copyfile(record.get('jpeg_path') or record['final_path'], target)
            if 'identity' in record:
                # The complete compiled PP3 is already stored in identity.profile.
                # Do not advertise a temporary file as a surviving artifact.
                record['identity'].pop('profile_stamp', None)
                record['identity']['profile_source'] = 'inline identity.profile'
            if write_record:
                for name, destination in (('profile.pp3','raw-profile.pp3'),('control.json','control.json')):
                    item = workspace/name
                    if item.exists():
                        shutil.copyfile(item,root/destination)
                # The engine compiles a full profile into its record; retain it
                # inline while removing references to a temporary profile file.
                if 'identity' in record:
                    record['identity']['profile_stamp'] = stamp(root/'raw-profile.pp3')
                    record['identity']['profile_source'] = 'raw-profile.pp3 and inline identity.profile'
        reasons.append({'stage':'raw_render','accepted':True,'baseline':control['baseline']})
        details = {'dimensions':record['dimensions'],'dimensions_basis':'raw_engine_output',
                   'original_jpeg_bytes_preserved':False,'upscaled':False}
    record.update(final_path=str(target),jpeg_path=str(target),output_stamp=stamp(target),
                  seconds=time.monotonic()-started)
    record['camera_base'] = {
        'policy':'camera-input-v2','source_kind':source_kind,'raw_rendered':source_kind=='raw_render',
        'minimum_camera_jpeg_long_edge':min_edge,'force_raw':force_raw,
        'provenance':{'source':selected_source,'source_stamp':selected_stamp,**details},
        'reasons':reasons,'probe':probe,'next_stage':'imagegen if requested',
        'exact_sony_jpeg_replication':None if source_kind!='raw_render' else False}
    if preview_edge:
        with Image.open(target) as image:
            image.load()
            image.thumbnail((preview_edge,preview_edge),Image.Resampling.LANCZOS)
            image.save(root/'preview.jpg','JPEG',quality=90,
                       icc_profile=ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes())
            record['planning_preview'] = {'path':str(root/'preview.jpg'), 'dimensions':list(image.size)}
    if write_record:
        atomic_json(root/'record.json',record)
    return record


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest='mode',required=True)
    base=commands.add_parser('camera-base',help='Prefer a usable 4K camera JPEG; otherwise develop RAW')
    base.add_argument('source');base.add_argument('output');base.add_argument('--preview-edge',type=int)
    paired=base.add_mutually_exclusive_group()
    paired.add_argument('--paired-jpeg');paired.add_argument('--no-paired-jpeg',action='store_true')
    base.add_argument('--force-raw',action='store_true');base.add_argument('--min-edge',type=int,default=3840)
    direct=commands.add_parser('develop',help='Render model-authored creative controls directly from RAW')
    direct.add_argument('source');direct.add_argument('control');direct.add_argument('output')
    direct.add_argument('--preview-edge',type=int)
    args=parser.parse_args()
    if args.mode=='camera-base':
        record=render_camera_base(args.source,args.output,args.preview_edge,
            paired_jpeg=False if args.no_paired_jpeg else args.paired_jpeg,
            force_raw=args.force_raw,min_edge=args.min_edge)
        result={key:record[key] for key in ('final_path','dimensions','seconds','camera_base')}
    else:
        probe=inspect_raw(args.source)
        result=render_control(probe['source'],args.output,json.loads(Path(args.control).read_text()),args.preview_edge)
        result['external_jpeg_used']=False
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
