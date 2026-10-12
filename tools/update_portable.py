"""Apply the tested source delta to a portable folder, with recoverable backups.

Stop that folder's API before calling. Existing changed files must still match
the previous package manifest. Never deletes files or rewrites browser settings.
"""
import argparse
from datetime import datetime
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'server'))
from visual_brand import BrandIndex, sha256


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--target',type=Path,required=True)
    args=parser.parse_args()
    target=args.target.resolve()
    package=json.loads((target/'portable.json').read_text(encoding='utf-8'))
    if package.get('name') != 'PhishLens Portable' or target == ROOT:
        raise SystemExit('Not the intended portable package')
    BrandIndex.load(ROOT)
    gallery=json.loads((ROOT/'brand_library/siamese/references.json').read_text(encoding='utf-8'))
    manifest=json.loads((target/'integrity.json').read_text(encoding='utf-8'))
    files={
        'server/app.py','server/reliability.py','server/visual_brand.py',
        'browser-extension/content.js','browser-extension/background.js','browser-extension/manifest.json',
        'brand_library/siamese/references.json', 'brand_library/siamese/'+gallery['feature_file'],
        'testset/fake/fake_payment_badge.html', 'testset/fake/fake_multiple_brands.html',
    }
    # Every referenced byte must match the rebuilt gallery, including copied logos.
    files.update(item['path'] for item in gallery['records'])
    files.update(item['path'] for item in gallery['models'])
    changed=[]
    for relative in sorted(files):
        source=(ROOT/relative).resolve(); destination=(target/relative).resolve()
        if not source.is_relative_to(ROOT) or not destination.is_relative_to(target):
            raise ValueError('Path outside project/package')
        desired=sha256(source)
        if destination.exists():
            actual=sha256(destination)
            if actual==desired:
                continue
            expected=manifest['files'].get(relative)
            if actual!=expected:
                raise ValueError('Local package edits require review: '+relative)
        changed.append((relative,source,destination,desired))
    backup=ROOT/'.maintenance'/('portable-backup-'+datetime.now().strftime('%Y%m%d-%H%M%S'))
    backup.mkdir(parents=True,exist_ok=False)
    for name in ('integrity.json','portable.json'):
        shutil.copy2(target/name,backup/name)
    for relative,source,destination,desired in changed:
        if destination.exists():
            old=backup/relative;old.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(destination,old)
        destination.parent.mkdir(parents=True,exist_ok=True)
        temp=destination.with_name(destination.name+'.update-tmp')
        shutil.copy2(source,temp)
        temp.replace(destination)
        manifest['files'][relative]=desired
    package['version']=json.loads((ROOT/'browser-extension/manifest.json').read_text(encoding='utf-8'))['version']
    package['reference_version']=gallery['features_sha256'][:16]
    (target/'portable.json').write_text(json.dumps(package,ensure_ascii=False,indent=2),encoding='utf-8')
    manifest['files']['portable.json']=sha256(target/'portable.json')
    manifest['file_count']=len(manifest['files'])
    manifest['bytes']=sum((target/p).stat().st_size for p in manifest['files'])
    (target/'integrity.json').write_text(json.dumps(manifest,ensure_ascii=False),encoding='utf-8')
    BrandIndex.load(target)
    report={'target':str(target),'backup':str(backup),'changed_files':[c[0] for c in changed],
            'reference_version':package['reference_version']}
    (backup/'update.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False),flush=True)


if __name__=='__main__':
    main()
