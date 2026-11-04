"""Dependency checks share the video pipeline's resolver, including GUI launches."""
import importlib.util
from pathlib import Path

_resolver = None


def media_binary(name: str):
    """Resolve afresh, so an installation is visible without restarting Settings."""
    if name not in ("ffmpeg", "ffprobe"):
        raise ValueError("Unsupported media dependency")
    global _resolver
    if _resolver is None:
        path = Path(__file__).resolve().parent.parent / "tools/clip-cutter/scripts/portable.py"
        spec = importlib.util.spec_from_file_location("_mariposa_dependencies", path)
        _resolver = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_resolver)
    resolver = getattr(_resolver, name)
    resolver.cache_clear()
    return resolver()
