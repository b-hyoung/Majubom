"""Refresh/check the file inventory in docs/PROJECT_STRUCTURE.md (stdlib only)."""
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'docs/PROJECT_STRUCTURE.md'
START = '<!-- BEGIN GENERATED TREE -->'
END = '<!-- END GENERATED TREE -->'
SKIP_DIRS = {'.git', '.venv', 'venv', '__pycache__', '.pio', 'node_modules', '.pytest_cache', '.vscode', '.idea'}
SKIP_SUFFIXES = {'.pyc', '.pyo', '.log'}


def tree(directory, prefix=''):
    entries = sorted((p for p in directory.iterdir()
                      if p.name not in SKIP_DIRS and p.suffix not in SKIP_SUFFIXES),
                     key=lambda p: (not p.is_dir(), p.name.casefold()))
    lines = []
    for i, path in enumerate(entries):
        last = i == len(entries) - 1
        lines.append(prefix + ('└── ' if last else '├── ') + path.name + ('/' if path.is_dir() else ''))
        if path.is_dir():
            if path.name.endswith('.lance'):
                lines.append(prefix + ('    ' if last else '│   ') + '└── [LanceDB 자동 생성 내부 파일 생략]')
            else:
                lines.extend(tree(path, prefix + ('    ' if last else '│   ')))
    return lines


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--check', action='store_true')
    args = ap.parse_args()
    old = OUTPUT.read_text(encoding='utf-8')
    before, body = old.split(START, 1)
    _, after = body.split(END, 1)
    inventory = '\n```text\nMajubom/\n' + '\n'.join(tree(ROOT)) + '\n```\n'
    new = before + START + inventory + END + after
    if args.check:
        if new != old:
            raise SystemExit('구조 문서가 오래되었습니다: python3 tools/update_project_structure.py')
        print('구조 문서 최신 상태')
    else:
        OUTPUT.write_text(new, encoding='utf-8')
        print('갱신: docs/PROJECT_STRUCTURE.md')


if __name__ == '__main__':
    main()
