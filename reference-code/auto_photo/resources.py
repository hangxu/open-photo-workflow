"""Paths to the profiles included in source and normal wheel installations."""
from pathlib import Path


def rawtherapee_profile(name="neutral"):
    """Return an installed PP3 path suitable for RawTherapee and artifact stamps."""
    if name not in {"neutral", "camera-matched", "auto-exposure", "controlled-lift"}:
        raise ValueError(f"Unknown bundled RawTherapee profile: {name}")
    return Path(__file__).resolve().parent / "profiles" / "rawtherapee" / f"{name}.pp3"
