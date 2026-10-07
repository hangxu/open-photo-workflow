"""Read-only source analysis and non-destructive, color-managed previews."""
from pathlib import Path
import os
import tempfile
from PIL import Image, ImageCms, ImageFilter, ImageStat
from .artifacts import stamp
from .color import to_srgb


def optional_local_candidates(path):
    from .detection import detect_candidates
    return detect_candidates(path)


def analyze_image(path, preview_path=None, max_edge=1280):
    source = Path(path).resolve()
    source_stamp = stamp(source)
    destination = Path(preview_path).resolve() if preview_path is not None else None
    if destination is not None and (source == destination or (destination.exists() and os.path.samefile(source, destination))):
        raise ValueError('preview must not overwrite source')
    if isinstance(max_edge, bool) or not isinstance(max_edge, int) or max_edge < 1:
        raise ValueError('max_edge must be a positive integer')
    if destination is not None and destination.exists():
        raise FileExistsError(f'preview already exists: {destination}')
    with Image.open(source) as original:
        exif = original.getexif()
        capture_time = exif.get(36867) or exif.get(306)
        try:
            detail = exif.get_ifd(34665)
            capture_time = detail.get(36867) or capture_time
        except (KeyError, TypeError):
            pass
        camera_model = exif.get(272)
        orientation = exif.get(274,1)
        upright, profile = to_srgb(original)
        srgb = ImageCms.createProfile('sRGB')
        srgb_bytes = ImageCms.ImageCmsProfile(srgb).tobytes()
        width,height = upright.size
        preview = upright.copy()
        preview.thumbnail((max_edge,max_edge),Image.Resampling.LANCZOS)
        gray = preview.convert('L')
        histogram = gray.histogram()
        total = sum(histogram)
        def percentile(q):
            target, cumulative = q * (total-1), 0
            for index,count in enumerate(histogram):
                cumulative += count
                if cumulative > target:
                    return index / 255
            return 1.0
        # An inexpensive relative sharpness hint, not a calibrated blur verdict.
        edge = gray.filter(ImageFilter.FIND_EDGES)
        if edge.width > 2 and edge.height > 2:
            edge = edge.crop((1,1,edge.width-1,edge.height-1))
        stats = {'luminance_mean':ImageStat.Stat(gray).mean[0]/255,
                 'shadow_clip_fraction':sum(histogram[:3])/total,
                 'highlight_clip_fraction':sum(histogram[253:])/total,
                 'luminance_p01':percentile(.01),'luminance_p05':percentile(.05),
                 'luminance_p50':percentile(.5),'luminance_p95':percentile(.95),'luminance_p99':percentile(.99),
                 'focus_score':ImageStat.Stat(edge).var[0],
                 'analysis_width':preview.width,'analysis_height':preview.height,
                 'focus_method':'preview_luminance_find_edges_variance'}
        if stamp(source) != source_stamp:
            raise ValueError('Source changed during analysis')
        if destination is not None:
            destination.parent.mkdir(parents=True, exist_ok=True)
            fd,temp = tempfile.mkstemp(prefix='.preview-',suffix='.jpg',dir=destination.parent)
            os.close(fd)
            try:
                preview.save(temp,format='JPEG',quality=90,icc_profile=srgb_bytes)
                # Link gives exclusive publication, even if another process creates the path.
                os.link(temp,destination)
            finally:
                Path(temp).unlink(missing_ok=True)
    return {'width':width,'height':height,'stats':stats,'source_profile':profile,
            'time':{'captured_at':str(capture_time) if capture_time else None,'camera_model':str(camera_model) if camera_model else None,'source_mtime_ns':source.stat().st_mtime_ns},
            'source_orientation':orientation,'source_stamp':source_stamp,
            'preview_path':str(destination) if destination else None,
            'preview_stamp':stamp(destination) if destination else None}
