"""Prepare a distributable copy from a verified onedir build and legal pack.

Never reads user stores or modifies the original build/environment. The legal
pack must provide licenses/, license-map.json, sources/, source-index.json and
SOURCE_AND_REPLACEMENT.md; corresponding upstream source archives stay bundled.
"""
from pathlib import Path
import argparse
import hashlib
import json
import runpy
import shutil
import zipfile


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def prepare(build, legal, output, inno_license):
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((build / 'build-manifest.json').read_text(encoding='utf-8'))
    result = json.loads((build / 'build-result.json').read_text(encoding='utf-8-sig'))
    if result['exit_code'] != 0:
        raise ValueError('Build failed')
    version = manifest['product_version']
    source_rows = json.loads((legal / 'source-index.json').read_text(encoding='utf-8'))
    source_rows = [r for r in source_rows if r['path'].startswith('sources/')]
    if len(source_rows) != 5:
        raise ValueError('Five corresponding Qt/PySide source archives are required')
    for row in source_rows:
        if '..' in Path(row['path']).parts or sha(legal / row['path']) != row['sha256']:
            raise ValueError('Corresponding source integrity failure')
    notices = json.loads((legal / 'license-map.json').read_text(encoding='utf-8'))
    for row in notices:
        rel = Path(row['bundle_path']).relative_to('third_party')
        if '..' in rel.parts or sha(legal / rel) != row['sha256']:
            raise ValueError('License integrity failure')
    policy = runpy.run_path(str(root / 'packaging/native_policy.py'))
    bundle = build / 'dist/RoxyPlan'
    for row in manifest['files']:
        if sha(bundle / row['path']) != row['sha256']:
            raise ValueError('Build file integrity failure')
    output.mkdir(parents=True, exist_ok=False)
    binary = [(r['path'], str(bundle/r['path']), 'BINARY') for r in manifest['files']
              if Path(r['path']).suffix.lower() in {'.exe', '.dll', '.pyd'}]
    closure_report = output.parent / (version + '-native-closure.json')
    kept = policy['trim_qt_binaries'](binary, closure_report)
    omitted = {r[0] for r in binary} - {r[0] for r in kept}
    for row in manifest['files']:
        if row['path'] in omitted:
            continue
        dest = output / row['path']
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(bundle / row['path'], dest)
    target = output / 'third_party'
    target.mkdir()
    shutil.copy2(inno_license, target / 'INNO-LICENSE.txt')
    for name in ('licenses', 'sources'):
        shutil.copytree(legal / name, target / name)
    shutil.copy2(legal / 'license-map.json', target / 'license-map.json')
    (target/'source-index.json').write_text(json.dumps(source_rows, indent=2), encoding='utf-8')
    replacement = (legal/'SOURCE_AND_REPLACEMENT.md').read_text(encoding='utf-8')
    # Preserve ABI/replacement guidance; replace the old product identifier and
    # access instructions with this delivery's actual self-contained locations.
    replacement = replacement.replace('0.9.0-aic.20261026.1', version)
    replacement = replacement.replace('完整对应版本源码已实际附于同行交付的\n“第三方许可证及来源佐证.zip”内sources/',
                                      '完整对应版本源码已附于本应用包third_party/sources/')
    replacement = replacement.replace('分发软件时应同时提供该源码/通知包的等效访问；若另建下载页，应将两个ZIP并列。',
                                      '分发时保留本包third_party目录，提供同等源码访问。')
    (target/'SOURCE_AND_REPLACEMENT.md').write_text(replacement, encoding='utf-8')
    for name in ('LICENSE', 'NOTICE', 'ASSET_LICENSE.md', 'THIRD_PARTY_RESOURCES.md'):
        content = (root/name).read_text(encoding='utf-8')
        content = content.replace('assets/pet/UPSTREAM-PERMISSION.md', 'third_party/ARTWORK_PERMISSION.md')
        content = content.replace('docs/verification/windows-release.json',
            'https://github.com/pluto525748/-RoxyPlan/blob/v0.9.0-aic2026-rc.1/docs/verification/windows-release.json')
        content = content.replace('tests/README.md',
            'https://github.com/pluto525748/-RoxyPlan/blob/v0.9.0-aic2026-rc.1/tests/README.md')
        content = content.replace('tests/fixtures/beta_interaction_regressions.index.json',
            'https://github.com/pluto525748/-RoxyPlan/blob/v0.9.0-aic2026-rc.1/tests/fixtures/beta_interaction_regressions.index.json')
        (output/name).write_text(content, encoding='utf-8')
    shutil.copy2(root/'assets/pet/UPSTREAM-PERMISSION.md', target/'ARTWORK_PERMISSION.md')
    for name in ('THIRD_PARTY_NOTICES.txt', 'DISTRIBUTION_TERMS.txt', 'QUICK_START.txt'):
        shutil.copy2(root/'packaging'/name, output/name)
    rows = []
    forbidden = {'memory.json','pet_config.json','llm_secrets.json','.env','runtime_metadata.json'}
    for p in sorted(output.rglob('*')):
        if p.is_file():
            if p.is_symlink() or p.name.lower() in forbidden:
                raise ValueError('Unexpected user/runtime file')
            rows.append({'path':p.relative_to(output).as_posix(),'sha256':sha(p),'bytes':p.stat().st_size})
    report = {'version':version, 'profile':manifest['profile'], 'files':rows,
              'source_content_sha256':manifest['source']['content_sha256'],
              'omitted_binaries':sorted(omitted), 'runtime_acceptance':'not_yet_performed'}
    (output/'release-manifest.json').write_text(json.dumps(report,indent=2), encoding='utf-8')
    archive = output.parent / ('RoxyPlan-'+version+'-Windows-x64.zip')
    with zipfile.ZipFile(archive, 'x', zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for p in sorted(output.rglob('*')):
            if p.is_file():z.write(p, 'RoxyPlan/'+p.relative_to(output).as_posix())
    with zipfile.ZipFile(archive) as z:
        if z.testzip() is not None:raise ValueError('ZIP CRC failure')
    print(json.dumps({'version':version, 'files':len(rows), 'zip':str(archive), 'sha256':sha(archive)}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-root',type=Path,required=True)
    parser.add_argument('--legal-root',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--inno-license',type=Path,required=True)
    args = parser.parse_args()
    prepare(args.build_root.resolve(), args.legal_root.resolve(), args.output.resolve(), args.inno_license.resolve())
