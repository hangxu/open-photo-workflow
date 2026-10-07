"""Small experimental LLM control surface for engine-native RAW development.

This module never invokes a model. A model supplies JSON after seeing color-managed
previews; these controls compile to PP3 and run on the original RAW. It is separate
from the batch plan schema until the controls have broader photographic validation.
"""
import argparse
from configparser import RawConfigParser
from io import StringIO
import json
from pathlib import Path

from .rawstyle import _finite, processing_profile
from .rawtherapee import render_photo
from .resources import rawtherapee_profile


def _object(value, allowed, name):
    if not isinstance(value, dict) or set(value) - set(allowed):
        raise ValueError(f'Unsupported {name} fields')
    return value


def compile_control(control):
    """Return a reproducible base profile, RAW recipe and compiled control evidence."""
    _object(control, ('baseline', 'recipe', 'gradient', 'denoise', 'tone_curve', 'capture_sharpening'), 'RAW control')
    baseline = control.get('baseline')
    if baseline not in ('neutral', 'camera_matched', 'automatic'):
        raise ValueError('baseline must be neutral, camera_matched or automatic')
    recipe = control.get('recipe', {})
    if not isinstance(recipe, dict) or recipe.get('subject_lighting'):
        raise ValueError('Use native controls here; subject_lighting is a post-development operation')
    recipe = dict(recipe)
    if 'automatic_exposure' in recipe:
        raise ValueError('Select automatic exposure through baseline, not recipe')
    if baseline == 'automatic':
        recipe['automatic_exposure'] = True
    profile = RawConfigParser(interpolation=None)
    profile.optionxform = str
    profile.read_string(rawtherapee_profile().read_text(encoding='utf-8'))
    profile.set('Exposure', 'HistogramMatching', str(baseline == 'camera_matched').lower())
    compiled = {'baseline': baseline, 'post_development_pixel_edits': False}
    sharpening = control.get('capture_sharpening', False)
    if type(sharpening) is not bool:
        raise ValueError('capture_sharpening must be boolean')
    if sharpening:
        profile.add_section('PostDemosaicSharpening')
        profile.set('PostDemosaicSharpening','Enabled','true')
    compiled['capture_sharpening'] = sharpening
    tone = control.get('tone_curve')
    if tone is not None:
        _object(tone, ('points', 'highlight_compression', 'highlight_threshold'), 'tone_curve')
        if baseline != 'neutral':
            raise ValueError('An explicit tone_curve requires neutral baseline; camera matching replaces it')
        points = tone.get('points')
        if not isinstance(points, list) or not 2 <= len(points) <= 10:
            raise ValueError('tone_curve needs 2..10 normalized [input,output] points')
        pairs=[]
        for point in points:
            if not isinstance(point,list) or len(point)!=2:
                raise ValueError('tone_curve points must be [input,output]')
            pairs.append([_finite(v,0,1,'tone_curve point') for v in point])
        if pairs[0]!=[0,0] or pairs[-1]!=[1,1] or any(b[0]<=a[0] for a,b in zip(pairs,pairs[1:])):
            raise ValueError('tone_curve must span [0,0] to [1,1] with increasing input coordinates')
        profile.set('Exposure','CurveMode','FilmLike')
        profile.set('Exposure','Curve','1;'+';'.join(f'{v:g}' for pair in pairs for v in pair)+';')
        compression=round(_finite(tone.get('highlight_compression',0),0,100,'highlight_compression'))
        threshold=round(_finite(tone.get('highlight_threshold',75),0,100,'highlight_threshold'))
        profile.set('Exposure','HighlightCompr',str(compression))
        profile.set('Exposure','HighlightComprThreshold',str(threshold))
        compiled['tone_curve']={'mode':'FilmLike','points':pairs,'highlight_compression':compression,'highlight_threshold':threshold}
    gradient = control.get('gradient')
    if gradient is not None:
        _object(gradient, ('side', 'exposure_ev', 'center', 'feather_percent'), 'gradient')
        side = gradient.get('side')
        if side not in ('top', 'bottom'):
            raise ValueError('gradient side must be top or bottom of the upright image')
        center = gradient.get('center', [.5, .5])
        if not isinstance(center, list) or len(center) != 2:
            raise ValueError('gradient center must be [x,y] in upright normalized coordinates')
        x, y = [_finite(v, 0, 1, 'gradient center') for v in center]
        exposure = _finite(gradient.get('exposure_ev'), -2, 2, 'gradient exposure_ev')
        feather = _finite(gradient.get('feather_percent', 25), 1, 100, 'gradient feather_percent')
        values = {'Enabled': 'true', 'Degree': 0 if side == 'top' else 180,
                  'Strength': -exposure, 'CenterX': round((x-.5)*200),
                  'CenterY': round((y-.5)*200), 'Feather': round(feather)}
        profile.add_section('Gradient')
        for key, value in values.items():
            profile.set('Gradient', key, str(value))
        compiled['gradient'] = {**values, 'coordinates': 'upright normalized image',
                                'center_resolution': '0.005 of image width/height (native integer units)',
                                'feather_unit': 'percent of image diagonal',
                                'stage': 'RawTherapee native floating-point RGB processing'}
    denoise = control.get('denoise')
    if denoise is not None:
        _object(denoise, ('luma', 'chroma', 'detail'), 'denoise')
        values = {'Enabled': 'true', 'Luma': _finite(denoise.get('luma', 0), 0, 100, 'denoise luma'),
                  'Chroma': _finite(denoise.get('chroma', 15), 0, 100, 'denoise chroma'),
                  'Ldetail': _finite(denoise.get('detail', 50), 0, 100, 'denoise detail'),
                  'LMethod': 'SLI', 'CMethod': 'MAN', 'AutoGain': 'true', 'Gamma': 1.7}
        profile.add_section('Directional Pyramid Denoising')
        for key, value in values.items():
            profile.set('Directional Pyramid Denoising', key, str(value))
        compiled['denoise'] = values
    buffer = StringIO(); profile.write(buffer, space_around_delimiters=False)
    text = buffer.getvalue()
    # Exercise the actual compiler now, including matching/curve incompatibilities.
    _, _, parameters = processing_profile(text, recipe)
    compiled['photographic_parameters'] = parameters
    return text, recipe, compiled


def render_control(source, output, control, preview_edge=None, *, include_record=False, executable=None):
    profile, recipe, compiled = compile_control(control)
    root = Path(output).expanduser().absolute()
    root.mkdir(parents=True, exist_ok=False)
    pp3 = root / 'profile.pp3'; pp3.write_text(profile)
    evidence = {'requested_controls': control, 'compiled_engine_parameters': compiled,
                'observed_effects': None}
    (root / 'control.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2))
    record = render_photo(source, root / 'render', recipe, profile=pp3,
                          preview_edge=preview_edge, write_record=True, executable=executable)
    result = {'final_path': record['final_path'], 'dimensions': record['dimensions'],
            'seconds': record['seconds'], 'control_record': str(root / 'control.json'),
            'visual_review': 'pending', 'post_development_pixel_edits': False}
    if include_record:
        result['engine_record'] = record
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source'); parser.add_argument('control'); parser.add_argument('output')
    parser.add_argument('--preview-edge', type=int)
    args = parser.parse_args()
    result = render_control(args.source, args.output, json.loads(Path(args.control).read_text()), args.preview_edge)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
