"""Health checks must find video binaries in GUI launches with a minimal PATH."""
import os
import sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from dependencies import media_binary
import dependencies
media_binary('ffmpeg')
with patch.object(dependencies._resolver, '_find_binary', return_value='/installed/ffmpeg'):
    assert media_binary('ffmpeg') == '/installed/ffmpeg'
with patch.object(dependencies._resolver, '_find_binary', return_value=None):
    assert media_binary('ffmpeg') is None, 'Stale discovery result persisted'
from first_run import _ffmpeg_ok
with patch('dependencies.media_binary', return_value='/installed/ffmpeg'):
    assert _ffmpeg_ok()
print('DEPENDENCY CHECKS PASSED')
