"""Build the trial and full packs, each with a manifest that tools/verify_pack.py checks.

    python -X utf8 tools/build_pack.py --out <folder>

trial: what a user needs to run the skill (e.g. copied into a WorkBuddy skills folder).
full : everything shipped, including tests and validation evidence.
Both skip caches, run outputs and backups; the manifest lists every file packed.
"""
from __future__ import annotations
import argparse
import fnmatch
import os
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT))
NAME = 'office-programme'
SKIP_DIRS = {'__pycache__', '.pytest_cache', 'output_mpp', 'output', 'out', '.git', 'logs', 'experiments', 'dist'}
SKIP_SUFFIX = ('.pyc', '.orig', '.mutbak', '.bak')
SKIP_NAMES = {'.env', '.DS_Store', 'Thumbs.db', '.gitignore', 'PACK_MANIFEST.txt', 'REVISION'}
# Not needed to run the skill; kept in the full pack as evidence and history.
TRIAL_EXCLUDE_DIRS = {'tests', 'history', 'docs_and_sops', 'scripts', 'guide'}
# Older intake versions, launchers and release history stay in the full pack only.
TRIAL_EXCLUDE_GLOBS = ('templates/PROJECT_INTAKE_TEMPLATE_V*', 'references/history/*', '*.bat')
TRIAL_EXCLUDE_PREFIX = ('validation_',)
TRIAL_EXCLUDE_FILES = {'DERIVATION_SIGNOFF.xlsx', 'DERIVATION_SIGNOFF_PM_filled_20260923.xlsx',
                       'constraints-validated.txt', 'requirements-test.txt', 'modular_release.md',
                       'VALIDATION_RESULT.json', 'VERIFICATION.md', 'matrix_validation.json',
                       'original_deck_text.txt', 'out_utf8.txt', 'slide_runs_details.txt', 'slide_shapes_details.txt'}
# Developer tools that only drive the regression gate.
TRIAL_EXCLUDE_TOOLS = {'run_regressions.py', 'mutation_check.py', 'gate_meta_check.py', 'make_signoff_anchor.py',
                       'make_signoff_record.py', 'build_release_docs.py', 'verify_real_case_round.py',
                       'build_guide.py', 'generate_competition_deck.py', 'dump_tasks.py'}


def version() -> str:
    for line in (ROOT / 'SKILL.md').read_text(encoding='utf-8').splitlines():
        if line.strip().startswith('version:'):
            return line.split(':', 1)[1].strip().strip('"')
    raise SystemExit('SKILL.md has no version')


def files(kind: str) -> list[Path]:
    picked = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        rel_dir = Path(dirpath).relative_to(ROOT)
        top = rel_dir.parts[0] if rel_dir.parts else ''
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        if kind == 'trial' and (top in TRIAL_EXCLUDE_DIRS or top.startswith(TRIAL_EXCLUDE_PREFIX)):
            dirnames[:] = []
            continue
        for fn in sorted(filenames):
            if fn in SKIP_NAMES or fn.endswith(SKIP_SUFFIX):
                continue
            rel = rel_dir / fn
            try:
                rel.as_posix().encode('utf-8')
            except UnicodeEncodeError:
                continue
            if kind == 'trial' and any(fnmatch.fnmatch(rel.as_posix(), g) for g in TRIAL_EXCLUDE_GLOBS):
                continue
            if kind == 'trial' and (str(rel) in TRIAL_EXCLUDE_FILES
                                    or (top == 'tools' and fn in TRIAL_EXCLUDE_TOOLS)):
                continue
            if (ROOT / rel).stat().st_size == 0:
                continue
            picked.append(rel)
    return sorted(picked)


def build(kind: str, out_dir: Path) -> Path:
    ver = version()
    import hashlib
    digest = hashlib.sha256()
    for path in sorted(files(kind)):
        digest.update(path.as_posix().encode('utf-8'))
        digest.update((ROOT / path).read_bytes())
    rev = 'v' + ver + '-' + digest.hexdigest()[:12]
    entries = [f'{NAME}/{p.as_posix()}' for p in files(kind)]
    manifest = (f'version={ver}\npack={kind}\nrevision={rev}\nfiles={len(entries) + 2}\n\n'
                + '\n'.join(sorted(entries + [f'{NAME}/PACK_MANIFEST.txt', f'{NAME}/REVISION'])) + '\n')
    target = out_dir / f'{NAME}-{ver}-{kind}.zip'
    out_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as zf:
        for entry in entries:
            zf.write(ROOT / entry.split('/', 1)[1], entry)
        zf.writestr(f'{NAME}/PACK_MANIFEST.txt', manifest.encode('utf-8'))
        zf.writestr(f'{NAME}/REVISION', (rev + '\n').encode('utf-8'))
    return target


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', required=True)
    ap.add_argument('--kind', choices=['trial', 'full', 'both'], default='both')
    args = ap.parse_args()
    for kind in (['trial', 'full'] if args.kind == 'both' else [args.kind]):
        path = build(kind, Path(args.out))
        print(kind, path, path.stat().st_size)
