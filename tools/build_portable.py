"""Build an isolated Windows x64 folder using the already installed runtime.

Run with the project's Python 3.11. Creates a new destination, never overwrites.
No downloads, user logs, browser settings, credentials, or autorun registrations.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(4 * 1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    dest = args.output.resolve()
    if os.name != 'nt' or sys.version_info[:2] != (3, 11) or struct.calcsize('P') != 8:
        raise SystemExit('Build with the project Windows x64 Python 3.11 environment.')
    if dest.exists():
        raise SystemExit('Destination already exists; choose a new folder: ' + str(dest))
    base = Path(sys.base_prefix)
    packages = ROOT / '.venv/Lib/site-packages'
    upstream = ROOT / 'third_party/PhishIntention'
    checkpoint = Path.home() / '.cache/torch/hub/checkpoints/mobilenet_v3_large-5c1a4163.pth'
    if not checkpoint.is_file():
        raise SystemExit('The existing local MobileNet checkpoint is required.')
    if shutil.disk_usage(dest.parent).free < 3 * 1024**3:
        raise SystemExit('At least 3 GiB free space is required for this build.')
    dest.mkdir()

    def copy_file(src, relative):
        target = dest / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)

    def copy_tree(src, relative, extra=()):
        shutil.copytree(src, dest / relative,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '.git', *extra))

    print('Copying isolated Python runtime and installed dependencies...', flush=True)
    for name in ('python.exe', 'python3.dll', 'python311.dll', 'vcruntime140.dll', 'vcruntime140_1.dll', 'LICENSE.txt'):
        copy_file(base / name, Path('runtime') / name)
    copy_tree(base / 'Lib', 'runtime/Lib', ('site-packages', 'test', 'idlelib', 'tkinter', 'ensurepip'))
    copy_tree(base / 'DLLs', 'runtime/DLLs')
    copy_tree(packages, 'runtime/Lib/site-packages')
    # App-local C++ runtime already distributed with the installed sklearn wheel.
    copy_file(packages / 'sklearn/.libs/msvcp140.dll', 'runtime/msvcp140.dll')
    (dest / 'runtime/python311._pth').write_text('.\nLib\nDLLs\nLib/site-packages\nimport site\n', encoding='ascii')
    copy_file(checkpoint, 'runtime/model-cache/hub/checkpoints/' + checkpoint.name)

    print('Copying application and inference models (no history or training data)...', flush=True)
    for name in ('app.py', 'reliability.py', 'cn_brand_matcher.py', 'portable_assets.py', 'visual_brand.py'):
        copy_file(ROOT / 'server' / name, Path('server') / name)
    copy_tree(ROOT / 'browser-extension', 'browser-extension')
    copy_tree(ROOT / 'brand_library/logos', 'brand_library/logos')
    copy_tree(ROOT / 'brand_library/logos_web', 'brand_library/logos_web')
    copy_tree(ROOT / 'brand_library/siamese', 'brand_library/siamese')
    copy_file(ROOT / 'brand_library/logo_feats_mnet.npy', 'brand_library/logo_feats_mnet.npy')
    brands = json.loads((ROOT / 'brand_library/brands.json').read_text(encoding='utf-8'))
    for brand in brands:
        # Metadata must not point back to the developer's Windows user profile.
        web = Path('logos_web') / brand['id'] / 'logo_web.png'
        app = Path('logos') / brand['id'] / 'logo_appstore.png'
        brand['logo_path'] = (web if (ROOT / 'brand_library' / web).exists() else app).as_posix()
    (dest / 'brand_library/brands.json').write_text(json.dumps(brands, ensure_ascii=False, indent=2), encoding='utf-8')
    for folder in ('src', 'configs', 'models/expand_targetlist'):
        copy_tree(upstream / folder, Path('third_party/PhishIntention') / folder)
    for name in ('layout_detector.pth', 'ocr_siamese.pth.tar', 'ocr_pretrained.pth.tar', 'domain_map.pkl'):
        copy_file(upstream / 'models' / name, Path('third_party/PhishIntention/models') / name)
    for name in ('LOGO_FEATS.npy', 'LICENSE', 'README.md'):
        copy_file(upstream / name, Path('third_party/PhishIntention') / name)

    # Match the upstream enumeration exactly once, then persist it independently
    # of filesystem order on the destination USB drive.
    references = []
    targetlist = upstream / 'models/expand_targetlist'
    for brand in os.listdir(targetlist):
        if brand.startswith('.'):
            continue
        for logo in os.listdir(targetlist / brand):
            if logo.endswith(('.png', '.jpeg', '.jpg', '.PNG', '.JPG', '.JPEG')) and not logo.startswith(('loginpage', 'homepage')):
                references.append((Path('models/expand_targetlist') / brand / logo).as_posix())
    import numpy as np
    if len(np.load(upstream / 'LOGO_FEATS.npy', allow_pickle=False)) != len(references):
        raise RuntimeError('Reference cache does not match the source logo list')
    index = {'features_sha256': sha(upstream / 'LOGO_FEATS.npy'), 'files': references}
    (dest / 'third_party/PhishIntention/LOGO_FILES.json').write_text(json.dumps(index, ensure_ascii=False), encoding='utf-8')
    for path in (ROOT / 'testset/fake').iterdir():
        if path.suffix.lower() in ('.html', '.png'):
            copy_file(path, Path('testset/fake') / path.name)
    for name in ('manage_project.ps1', 'check_portable.py'):
        copy_file(ROOT / 'tools' / name, Path('tools') / name)
    for name in ('LICENSE', 'THIRD_PARTY_LICENSES.md'):
        copy_file(ROOT / name, name)
    copy_file(ROOT / 'tools/portable_readme.txt', '使用说明.txt')

    for english, chinese, action in (('start_project.bat', '启动项目.bat', 'Start -OpenDemo'),
                                      ('stop_project.bat', '停止项目.bat', 'Stop')):
        content = ('@echo off\r\npowershell.exe -NoProfile -ExecutionPolicy Bypass -File '
                   '"%~dp0tools\\manage_project.ps1" -Action ' + action + '\r\n'
                   'set "PHISHLENS_EXIT=%ERRORLEVEL%"\r\npause\r\nexit /b %PHISHLENS_EXIT%\r\n')
        (dest / english).write_bytes(content.encode('ascii'))
        (dest / chinese).write_bytes(('@echo off\r\ncall "%~dp0' + english + '"\r\n').encode('ascii'))
    (dest / '检查环境.bat').write_bytes(b'@echo off\r\n"%~dp0runtime\\python.exe" -B "%~dp0tools\\check_portable.py"\r\npause\r\n')
    (dest / 'portable.json').write_text(json.dumps({
        'name': 'PhishLens Portable',
        'version': json.loads((ROOT / 'browser-extension/manifest.json').read_text(encoding='utf-8'))['version'],
        'platform': 'Windows x64',
        'python': sys.version.split()[0], 'extension_first_install_required': True,
        'scope': 'local demonstration; model accuracy is not validated'
    }, indent=2), encoding='utf-8')
    print('Writing integrity manifest...', flush=True)
    files = [p for p in dest.rglob('*') if p.is_file()]
    manifest = {'files': {p.relative_to(dest).as_posix(): sha(p) for p in files},
                'bytes': sum(p.stat().st_size for p in files), 'file_count': len(files)}
    (dest / 'integrity.json').write_text(json.dumps(manifest, ensure_ascii=False), encoding='utf-8')
    print(json.dumps({'output': str(dest), 'files': len(files), 'size_gib': round(manifest['bytes']/1024**3, 2)}), flush=True)


if __name__ == '__main__':
    main()
