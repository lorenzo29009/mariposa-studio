"""Local, explicitly trusted extensions. No catalog or bundled private tools."""
import importlib.util
import json
import re
import shutil
import stat
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from PySide6.QtCore import QStandardPaths
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel


def plugin_root():
    return Path(QStandardPaths.writableLocation(QStandardPaths.AppLocalDataLocation)) / 'plugins'


def _manifest(value):
    if not isinstance(value, dict):
        raise ValueError('Invalid plugin manifest.')
    if value.get('api') != 1 or not isinstance(value.get('id'), str) or not re.fullmatch(r'[a-z][a-z0-9_]{2,60}', value['id']):
        raise ValueError('Unsupported plugin manifest.')
    if not isinstance(value.get('name'), str) or not 1 <= len(value['name']) <= 60:
        raise ValueError('Invalid plugin name.')
    for field, limit in (('description', 240), ('version', 30), ('icon', 60)):
        if field in value and (not isinstance(value[field], str) or len(value[field]) > limit):
            raise ValueError('Invalid plugin metadata.')
    return value


def inspect_package(path):
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        if len(entries) > 2000 or sum(x.file_size for x in entries) > 30_000_000:
            raise ValueError('Plugin package is too large.')
        names = set()
        files = set()
        for item in entries:
            name = PurePosixPath(item.filename)
            parts = item.filename.rstrip('/').split('/')
            mode = stat.S_IFMT(item.external_attr >> 16)
            if (name.is_absolute() or any(p in ('', '.', '..') for p in parts)
                    or '\\' in item.filename or ':' in item.filename
                    or mode not in (0, stat.S_IFREG, stat.S_IFDIR)
                    or any(p.endswith((' ', '.')) or re.fullmatch(
                        r'(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', p, re.I)
                           for p in parts)):
                raise ValueError('Unsafe plugin package.')
            key = '/'.join(parts).casefold()
            if key in names:
                raise ValueError('Duplicate package entry.')
            names.add(key)
            if not item.is_dir():
                files.add(key)
        if any('/'.join(n.split('/')[:i]) in files
               for n in names for i in range(1, len(n.split('/')))):
            raise ValueError('Conflicting package paths.')
        manifest = _manifest(json.loads(archive.read('plugin.json')))
        if '__init__.py' not in archive.namelist():
            raise ValueError('Plugin entry is missing.')
        return manifest


def install_package(path):
    manifest = inspect_package(path)
    root = plugin_root()
    root.mkdir(parents=True, exist_ok=True)
    destination = root / manifest['id']
    if destination.exists():
        raise ValueError('This plugin is already installed.')
    temporary = Path(tempfile.mkdtemp(prefix='.install-', dir=root))
    try:
        with zipfile.ZipFile(path) as archive:
            archive.extractall(temporary)
        temporary.rename(destination)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return manifest


def remove_package(plugin_id):
    """Recoverable uninstall; removed packages are never discovered as tools."""
    import uuid
    if not re.fullmatch(r'[a-z][a-z0-9_]{2,60}', plugin_id):
        raise ValueError('Invalid plugin ID.')
    root = plugin_root()
    folder = root / plugin_id
    if folder.is_symlink() or not folder.is_dir():
        raise ValueError('Plugin is not installed.')
    removed = root / '.removed'
    removed.mkdir(exist_ok=True)
    folder.rename(removed / (plugin_id + '-' + uuid.uuid4().hex))


def installed_manifests():
    """Read installed metadata without importing or executing plugin code."""
    import json
    root = plugin_root()
    result = []
    if not root.exists():
        return result
    for folder in sorted(root.iterdir()):
        if folder.name.startswith('.') or folder.is_symlink() or not folder.is_dir():
            continue
        try:
            manifest = _manifest(json.loads((folder / 'plugin.json').read_text(encoding='utf-8')))
            if (manifest.get('api') != 1 or manifest.get('id') != folder.name
                    or not isinstance(manifest.get('name'), str)):
                continue
            icon = manifest.get('icon', '')
            if (not isinstance(icon, str) or not re.fullmatch(r'[a-z0-9-]+', icon)
                    or not (Path(__file__).parent.parent / 'brand' / 'icons' / (icon + '.svg')).is_file()):
                manifest['icon'] = 'circle-check'
            result.append(manifest)
        except (OSError, ValueError, TypeError, AttributeError):
            continue
    return result


def installed_specs():
    result = []
    root = plugin_root()
    if not root.exists():
        return result
    for folder in sorted(root.iterdir()):
        if folder.name.startswith('.') or folder.is_symlink() or not folder.is_dir():
            continue
        try:
            manifest = _manifest(json.loads((folder / 'plugin.json').read_text(encoding='utf-8')))
            if manifest.get('api') != 1 or manifest['id'] != folder.name:
                continue
            key = 'plugin_' + folder.name
            from design import TOOL_ICONS
            from launcher import APP_TAGLINES, APP_DESCS
            icon = manifest.get('icon', '')
            if isinstance(icon, str) and re.fullmatch(r'[a-z0-9-]+', icon):
                if (Path(__file__).parent.parent / 'brand' / 'icons' / (icon + '.svg')).is_file():
                    TOOL_ICONS[key] = icon
            description = manifest.get('description', '')
            if isinstance(description, str):
                APP_TAGLINES[key] = description[:240]
                APP_DESCS[key] = description[:240]
            def factory(on_back, folder=folder, key=key):
                try:
                    spec = importlib.util.spec_from_file_location(key, folder / '__init__.py', submodule_search_locations=[str(folder)])
                    module = importlib.util.module_from_spec(spec)
                    sys.modules[key] = module
                    spec.loader.exec_module(module)
                    return module.create_page(on_back)
                except Exception:
                    from widgets import AppBar
                    page = QWidget()
                    layout = QVBoxLayout(page)
                    layout.addWidget(AppBar('Plugin unavailable', key, on_back))
                    label = QLabel('This plugin could not be loaded.')
                    label.setObjectName('Meta')
                    layout.addWidget(label)
                    layout.addStretch()
                    return page
            result.append((manifest['name'], key, factory, True))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return result
