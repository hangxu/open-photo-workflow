"""Direct camera RAW rendering through an explicit processing profile."""
from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

from PIL import Image

from .converter import _atomic_json, _inside, _target_path, _valid_output, discover_inputs
from .artifacts import stamp
from .resources import rawtherapee_profile
import json
_VERSION_CACHE = []


def _engine_version(binary_path, size, mtime_ns):
    # A process-level probe avoids starting the CLI twice for every photograph.
    identity = (binary_path, size, mtime_ns)
    for previous, version in _VERSION_CACHE:
        if previous == identity:
            return version
    with tempfile.TemporaryDirectory(prefix='auto-photo-rt-version-') as directory:
        result = subprocess.run([binary_path], capture_output=True, text=True, timeout=30,
                                env=_environment(directory))
    if result.returncode or not result.stdout.strip():
        raise ValueError('RawTherapee version probe failed')
    version = result.stdout.strip().splitlines()[0]
    _VERSION_CACHE.append((identity, version))
    del _VERSION_CACHE[:-4]
    return version


def render_photo(source_path, output_dir, recipe=None, *, profile=None, executable=None,
                 preview_edge=None, write_record=False, quality=96, timeout=300):
    """Apply the photographic recipe during RAW development, then encode one JPEG.

    Important global exposure, WB and tonal work runs inside RawTherapee. Optional
    elliptical subject exposure uses a temporary lossless sRGB image afterwards;
    it does not perform segmentation, skin whitening or geometric changes.
    """
    from .grade import grade_image, _safe_path
    from .rawstyle import processing_profile
    if type(quality) is not int or not 1 <= quality <= 100 or timeout <= 0:
        raise ValueError('quality must be 1..100 and timeout positive')
    source = Path(source_path).expanduser().resolve(strict=True)
    directory = Path(output_dir).expanduser().absolute()
    _safe_path(directory)
    if directory == source or directory in source.parents:
        raise ValueError('Output must not contain the source')
    profile_path = Path(profile).expanduser().resolve(strict=True) if profile else rawtherapee_profile()
    profile_stamp = stamp(profile_path)
    compiled, normalized, parameters = processing_profile(profile_path.read_text(encoding='utf-8-sig'), recipe, preview_edge)
    binary = _executable(executable)
    binary_stamp, source_stamp = stamp(binary), stamp(source)
    identity = {'engine':'rawtherapee-photographic-1','source_stamp':source_stamp,
                'executable_stamp':binary_stamp,'profile_stamp':profile_stamp,'profile':compiled,
                'recipe':normalized,'quality':quality,'preview_edge':preview_edge}
    manifest = directory/'record.json'
    if directory.exists() and any(directory.iterdir()):
        try:
            record = json.loads(manifest.read_text())
            target = directory/'final.jpg'
            if (record.get('identity')==identity and record.get('status')=='completed' and
                    not target.is_symlink() and stamp(target)==record.get('output_stamp') and
                    {p.name for p in directory.iterdir()}=={'record.json','final.jpg'}):
                return dict(record,cached=True)
        except (OSError, ValueError, TypeError, AttributeError):
            pass
        raise FileExistsError('Output differs or is historical; choose a new output directory')
    directory.mkdir(parents=True,exist_ok=True)
    started = time.monotonic()
    version = _engine_version(str(binary), binary_stamp['size'], binary_stamp['mtime_ns'])
    local = normalized['subject_lighting']
    with tempfile.TemporaryDirectory(prefix='.raw-style-',dir=directory) as temporary:
        temporary = Path(temporary)
        pp3 = temporary/'processing.pp3'
        pp3.write_text(compiled,encoding='utf-8')
        rendered = temporary/('developed.png' if local else 'developed.jpg')
        format_args = ['-n'] if local else [f'-j{quality}','-js3']
        command = [str(binary),'-q','-o',str(rendered),'-p',str(pp3),*format_args,'-c',str(source)]
        result = subprocess.run(command,capture_output=True,text=True,timeout=timeout,env=_environment(temporary))
        if result.returncode:
            raise RuntimeError(f'RawTherapee exited {result.returncode}: {result.stderr[-2000:]}')
        with Image.open(rendered) as image:
            image.load()
            if image.format != ('PNG' if local else 'JPEG'):
                raise ValueError('Unexpected RawTherapee output format')
            dimensions = list(image.size)
        local_record = None
        if local:
            local_record = grade_image(rendered,temporary/'local',{'preset':'neutral','subject_lighting':local},
                                       keep_master=False,write_record=False,quality=quality)
            rendered = Path(local_record['outputs']['final']['path'])
        if stamp(source)!=source_stamp or stamp(profile_path)!=profile_stamp:
            raise ValueError('Source or processing profile changed during rendering')
        target = directory/'final.jpg'
        os.link(rendered,target)
    record = {'status':'completed','engine':'rawtherapee','engine_version':version,'identity':identity,
              'source_stamp':source_stamp,'recipe':normalized,'effective_parameters':parameters,
              'final_path':str(target),'jpeg_path':str(target),'dimensions':dimensions,
              'output_stamp':stamp(target),'technical_pass':True,
              'qa':{'technical_pass':True,'raw_global_adjustments_before_output':True,
                    'local_subject_lighting':bool(local),'final_jpeg_encodings':1,
                    'visual_review':'not_performed'},
              'seconds':time.monotonic()-started}
    if local_record:
        record['qa']['local_geometry']=local_record['qa']['geometry']
    if write_record:
        _atomic_json(manifest,record)
    return record


def _executable(value):
    found = value or shutil.which("rawtherapee-cli")
    if not found:
        found = "/Applications/RawTherapee.app/Contents/MacOS/rawtherapee-cli"
    binary = Path(found).expanduser().resolve()
    if not binary.is_file() or not os.access(binary, os.X_OK):
        raise ValueError(f"RawTherapee CLI executable not found: {binary}")
    return binary


def _environment(directory):
    env = os.environ.copy()
    settings = Path(directory) / "settings"
    settings.mkdir()
    env["RT_SETTINGS"] = str(settings)
    cache = Path(directory) / "cache"
    cache.mkdir()
    env["RT_CACHE"] = str(cache)
    return env


def convert_batch(input_path, output_dir, recursive=False, quality=95, max_size=None,
                  progress=None, *, profile, executable=None, timeout=300, write_record=True):
    """Render serially; use a fresh output directory when changing a recipe."""
    if not 1 <= quality <= 100:
        raise ValueError("quality must be between 1 and 100")
    if max_size is not None:
        raise ValueError("--max-size is not supported by RawTherapee; configure Resize in the PP3 profile")
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    if profile is None:
        raise ValueError("RawTherapee requires --profile with an explicit PP3 file")
    profile_path = Path(profile).expanduser().resolve()
    profile_bytes = profile_path.read_bytes()
    profile_text = profile_bytes.decode("utf-8-sig")
    if not profile_text.strip():
        raise ValueError("PP3 profile must not be empty")
    binary = _executable(executable)
    source_root = Path(input_path).expanduser().resolve()
    output_root = Path(output_dir).expanduser().resolve()
    if output_root == source_root or (source_root.is_dir() and _inside(source_root, output_root)):
        raise ValueError("Output must not equal or contain the input directory")
    sources = discover_inputs(source_root, recursive, output_root)
    if not sources:
        raise ValueError("No supported RAW inputs found")
    output_root.mkdir(parents=True, exist_ok=True)
    manifest_path = output_root / "manifest-rawtherapee.json"
    if manifest_path.is_symlink():
        raise ValueError("Refusing a symlink manifest path")
    try:
        with tempfile.TemporaryDirectory(prefix=".rt-version-", dir=output_root) as temporary:
            # RawTherapee 5.13 has no -v flag. No arguments prints its version and exits 0.
            version_result = subprocess.run([str(binary)], capture_output=True, text=True,
                                            timeout=min(timeout, 30), env=_environment(temporary))
    except subprocess.TimeoutExpired as exc:
        raise ValueError("RawTherapee version probe timed out") from exc
    if version_result.returncode:
        raise ValueError(f"RawTherapee version probe failed ({version_result.returncode}): {version_result.stderr.strip()}")
    version = version_result.stdout.strip().splitlines()[0] if version_result.stdout.strip() else ""
    if not version:
        raise ValueError("RawTherapee version probe returned no version")
    engine = {"name": "rawtherapee", "version": version,
              "executable": str(binary), "executable_stamp": stamp(binary)}
    recipe = {"version": 2, "engine": engine, "quality": quality, "max_size": None,
              "profile": {"path": str(profile_path), "content": profile_text,
                          "stamp": stamp(profile_path)},
              "settings": "isolated RT_SETTINGS and RT_CACHE", "jpeg_subsampling": "4:4:4", "default_profile": False,
              "sidecar_profile": False}
    try:
        previous = json.loads(manifest_path.read_text(encoding="utf-8"))
        old_items = {item["source"]: item for item in previous.get("items", [])}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        old_items = {}
    if old_items and previous.get('schema_version') != 2:
        raise ValueError('Historical conversion records are read-only; use a new output directory')
    manifest = {"schema_version": 2, "manifest": str(manifest_path) if write_record else None,
                "created_at": datetime.now(timezone.utc).isoformat(), "input": str(source_root),
                "output_dir": str(output_root), "engine": engine, "recipe": recipe,
                "items": [dict(old_items[str(source)]) if old_items.get(str(source), {}).get('source_stamp') else {"source": str(source), "status": "pending"}
                          for source in sources]}
    reserved = {Path(item['output']).name for item in old_items.values() if item.get('output')}
    for index, source in enumerate(sources):
        start = time.monotonic()
        old = old_items.get(str(source), {})
        target = _target_path(output_root, source, old, reserved, '.rawtherapee.jpg')
        item = {"source": str(source), "output": str(target), "recipe": recipe}
        try:
            item["source_stamp"] = stamp(source)
            if target.exists() or target.is_symlink():
                if (not target.is_symlink() and old.get("status") in {"converted", "cached"}
                        and old.get("output") == str(target) and old.get("recipe") == recipe
                        and old.get("source_stamp") == item["source_stamp"]
                        and _valid_output(target, old)):
                    item.update({key: old[key] for key in ("width", "height", "output_stamp")})
                    item["status"] = "cached"
                else:
                    raise ValueError("Output metadata or recipe differs; use a new output directory")
            else:
                with tempfile.TemporaryDirectory(prefix=".rt-render-", dir=output_root) as temporary:
                    temporary = Path(temporary)
                    snapshot = temporary / "profile.pp3"
                    snapshot.write_bytes(profile_bytes)
                    rendered = temporary / "rendered.jpg"
                    command = [str(binary), "-q", "-o", str(rendered), "-p", str(snapshot),
                               f"-j{quality}", "-js3", "-c", str(source)]
                    item["command"] = command
                    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout,
                                            env=_environment(temporary))
                    item["engine_stdout"] = result.stdout[-8000:]
                    item["engine_stderr"] = result.stderr[-8000:]
                    if result.returncode:
                        raise RuntimeError(f"RawTherapee exited {result.returncode}: {result.stderr[-2000:]}")
                    with Image.open(rendered) as image:
                        if image.format != "JPEG":
                            raise ValueError("RawTherapee output is not a JPEG")
                        image.verify()
                    with Image.open(rendered) as image:
                        image.load()
                        item.update(width=image.width, height=image.height)
                    if stamp(source) != item["source_stamp"]:
                        raise ValueError("Source changed during rendering; refusing to publish output")
                    os.link(rendered, target)
                    item["output_stamp"] = stamp(target)
                    item["status"] = "converted"
        except Exception as exc:
            item.update(status="error", error=f"{type(exc).__name__}: {exc}")
        item["elapsed_seconds"] = round(time.monotonic() - start, 4)
        manifest["items"][index] = item
        manifest["summary"] = {status: sum(i["status"] == status for i in manifest["items"])
                               for status in ("converted", "cached", "error", "pending")}
        manifest["summary"]["total"] = len(sources)
        if write_record:
            _atomic_json(manifest_path, manifest)
        if progress:
            progress(index + 1, len(sources), item)
    return manifest
