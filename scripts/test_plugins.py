"""Package boundary and installation checks; no third-party code executed."""
import json
import sys
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import plugins
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    package = root / 'test.mariposa-plugin'
    def write(extra=None):
        with zipfile.ZipFile(package, 'w') as archive:
            archive.writestr('plugin.json', json.dumps({'api':1,'id':'test_plugin','name':'Test'}))
            archive.writestr('__init__.py', 'raise RuntimeError("must not execute during install")')
            if extra: archive.writestr(extra, 'bad')
    for name in ('../escape', '/absolute', 'a\\escape', 'C:/escape', 'CON.py', 'dir/file.', 'dir/../file', './file', 'dir//file'):
        write(name)
        try: plugins.inspect_package(package)
        except ValueError: pass
        else: raise AssertionError(name)
    write()
    with patch.object(plugins, 'plugin_root', return_value=root / 'installed'):
        plugins.install_package(package)
        assert (root/'installed/test_plugin/__init__.py').exists()
        assert len(plugins.installed_specs()) == 1
        try: plugins.install_package(package)
        except ValueError: pass
        else: raise AssertionError('Duplicate installation accepted')
print('PLUGIN CHECKS PASSED')

with tempfile.TemporaryDirectory() as directory:
    with patch.object(plugins, 'plugin_root', return_value=Path(directory)):
        assert plugins.installed_specs() == []
        assert plugins.installed_manifests() == []
for value in (None, [], {'api': 1, 'id': '../bad', 'name': 'Bad'},
              {'api': 1, 'id': 'valid', 'name': '<b>' * 30}):
    try:
        plugins._manifest(value)
    except ValueError:
        pass
    else:
        raise AssertionError('Invalid manifest accepted')
print('EMPTY HOST AND MANIFEST CHECKS PASSED')
