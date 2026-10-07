"""Translate bounded photographic controls into a RawTherapee processing profile."""
from configparser import RawConfigParser
from io import StringIO
import math

from .grade import _recipe


def _finite(value, low, high, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f'{name} must be finite within [{low}, {high}]')
    return float(value)


def _tone_curve(highlights, shadows):
    points = ['1']  # RawTherapee DCT_Spline: x,y control points.
    for x in (0., .08, .18, .35, .5, .65, .82, .94, 1.):
        hi = max(0., (x-.35)/.65)**2
        lo = max(0., (.65-x)/.65)**2
        gain = 2.**(highlights*hi + shadows*lo)
        y = gain*x/(1+(gain-1)*x)
        points.extend((f'{x:.9g}', f'{y:.9g}'))
    return ';'.join(points) + ';'


def processing_profile(profile_text, recipe=None, preview_edge=None):
    """Use native RAW controls before sRGB output; local ellipses are returned separately."""
    recipe = dict(recipe or {})
    automatic = recipe.pop('automatic_exposure', False)
    if type(automatic) is not bool:
        raise ValueError('automatic_exposure must be boolean')
    kelvin = recipe.pop('white_balance_kelvin', None)
    green = recipe.pop('wb_green', None)
    if green is not None and kelvin is None:
        raise ValueError('wb_green requires an explicit white_balance_kelvin')
    if kelvin is not None:
        kelvin = _finite(kelvin, 2000, 15000, 'white_balance_kelvin')
        green = _finite(1. if green is None else green, .5, 2., 'wb_green')
    normalized, effective = _recipe(recipe)
    if preview_edge is not None and (type(preview_edge) is not int or preview_edge < 1):
        raise ValueError('preview_edge must be a positive integer')
    profile = RawConfigParser(interpolation=None, strict=False)
    profile.optionxform = str
    profile.read_string(profile_text)
    matching = profile.getboolean('Exposure', 'HistogramMatching', fallback=False)
    if automatic and (matching or any(effective[key] for key in ('exposure_ev','contrast','highlights','shadows'))):
        raise ValueError('Automatic exposure cannot combine camera matching or manual exposure/tone adjustments')
    # RT 5.13 simpleprocess.cc clears contrast and Curve2 during matching.
    # Do not report these recipe controls as applied when the engine discards them.
    if matching and any(effective[key] for key in ('contrast', 'highlights', 'shadows')):
        raise ValueError('Camera curve matching overrides contrast/highlights/shadows; use a neutral recipe for these controls or apply them after matched RAW development')
    def set_value(section, key, value):
        if not profile.has_section(section):
            profile.add_section(section)
        profile.set(section, key, str(value).lower() if isinstance(value, bool) else str(value))
    def existing(section, key, default=0.):
        return profile.getfloat(section, key, fallback=default)
    # Auto exposure and camera-JPEG tone-curve matching are separate controls.
    # Preserve an explicitly selected matching baseline; neutral stays neutral.
    set_value('Exposure', 'Auto', automatic)
    set_value('Exposure', 'HistogramMatching', matching)
    set_value('Exposure', 'Compensation', existing('Exposure','Compensation') + effective['exposure_ev'])
    set_value('Exposure', 'Contrast', round(max(-100,min(100,existing('Exposure','Contrast')+100*effective['contrast']))))
    set_value('Exposure', 'Saturation', round(max(-100,min(100,existing('Exposure','Saturation')+100*(effective['saturation']-1)))))
    if effective['highlights'] or effective['shadows']:
        set_value('Exposure', 'Curve2', _tone_curve(effective['highlights'], effective['shadows']))
        set_value('Exposure', 'CurveMode2', 'Luminance')
    if kelvin is not None:
        set_value('White Balance', 'Enabled', True)
        set_value('White Balance', 'Setting', 'Custom')
        set_value('White Balance', 'Temperature', round(kelvin))
        set_value('White Balance', 'Green', green)
        normalized.update(white_balance_kelvin=kelvin, wb_green=green)
    gains = [1+effective['temperature'], 1+effective['tint'], 1-effective['temperature']]
    # PP3 >=338 stores mixer percentages in tenths: unity is 1000, not 100.
    # RawTherapee 5.13 procparams.cc upgrades older profiles by multiplying by 10.
    mixer_unit = 1000 if profile.getint('Version','Version',fallback=352) >= 338 else 100
    if effective['temperature'] or effective['tint']:
        set_value('Channel Mixer', 'Enabled', True)
        for channel, gain, index in zip(('Red','Green','Blue'), gains, range(3)):
            values = [0,0,0]
            values[index] = round(mixer_unit*gain)
            set_value('Channel Mixer',channel,';'.join(map(str,values))+';')
    if preview_edge is not None:
        for key,value in {'Enabled':True,'AppliesTo':'Full image','Method':'Lanczos','DataSpecified':3,
                          'Width':preview_edge,'Height':preview_edge,'AllowUpscaling':False}.items():
            set_value('Resize',key,value)
    buffer = StringIO()
    profile.write(buffer, space_around_delimiters=False)
    if automatic:
        normalized['automatic_exposure'] = True
    parameters = {**effective, 'subject_lighting':normalized['subject_lighting'],
                  'white_balance_kelvin':kelvin, 'wb_green':green,
                  'raw_exposure_compensation':None if automatic else profile.getfloat('Exposure','Compensation'),
                  'exposure_mode':'engine_automatic' if automatic else 'manual',
                  'camera_tone_matching_requested':profile.getboolean('Exposure','HistogramMatching'),
                  'relative_color_gains': [round(g*mixer_unit)/mixer_unit for g in gains],
                  'tone_controls':'RawTherapee luminance spline before JPEG encoding',
                  'local_light_method':'feathered ellipse after RAW global adjustments; no segmentation'}
    return buffer.getvalue(), normalized, parameters
