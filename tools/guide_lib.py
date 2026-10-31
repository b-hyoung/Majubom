"""코드 가이드 생성 공용 도구 — 소스에서 코드 조각을 직접 잘라 와 줄 번호와 함께 HTML로 만든다.

설명 문단은 tools/build_code_guide.py에 있다. 코드 조각은 생성할 때마다 현재 소스에서 다시 잘라 오므로
코드가 바뀌어도 문서의 코드·줄 번호는 항상 최신이다(함수·패턴을 못 찾으면 생성이 멈춘다 → 설명도 같이 고친다).
"""
import ast
import html
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REL = ".."  # docs/ 기준 저장소 루트


def read(path):
    return open(os.path.join(ROOT, path), encoding="utf-8").read()


def lines(path):
    return read(path).splitlines()


def esc(s):
    return html.escape(str(s))


def line_of(path, pattern):
    rx = re.compile(pattern)
    for i, line in enumerate(lines(path), 1):
        if rx.search(line):
            return i
    raise SystemExit(f"[guide] 패턴 없음: {path} :: {pattern}")


def py_span(path, name):
    """파이썬 함수·클래스(또는 'Class.method')의 (시작줄, 끝줄). 데코레이터 포함."""
    tree = ast.parse(read(path))
    parts = name.split(".")
    found = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == parts[0]:
            found = node
            break
    if found is None:
        raise SystemExit(f"[guide] 함수 없음: {path} :: {name}")
    for p in parts[1:]:
        found = next((n for n in found.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                      and n.name == p), None)
        if found is None:
            raise SystemExit(f"[guide] 메서드 없음: {path} :: {name}")
    start = min([found.lineno] + [d.lineno for d in found.decorator_list])
    return start, found.end_lineno


def brace_span(path, pattern):
    """JS·C 함수: pattern 줄부터 중괄호가 닫힐 때까지."""
    src = lines(path)
    start = line_of(path, pattern)
    depth, opened = 0, False
    for i in range(start - 1, min(len(src), start + 400)):
        text = re.sub(r"'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"", "''", src[i])   # 문자열 안 중괄호 무시
        for ch in text:
            if ch == "{":
                depth += 1
                opened = True
            elif ch == "}":
                depth -= 1
        if opened and depth <= 0:
            return start, i + 1
    raise SystemExit(f"[guide] 중괄호 끝 없음: {path} :: {pattern}")


def between(path, start_pattern, end_pattern, include_end=True):
    start = line_of(path, start_pattern)
    src = lines(path)
    rx = re.compile(end_pattern)
    for i in range(start - 1, len(src)):   # 시작 줄 자신도 끝이 될 수 있다(한 줄짜리)
        if rx.search(src[i]):
            return start, (i + 1 if include_end else i)
    raise SystemExit(f"[guide] 끝 패턴 없음: {path} :: {end_pattern}")


def ref(path, pattern=None, label=None):
    n = line_of(path, pattern) if pattern else None
    text = label or (f"{path}:{n}" if n else path)
    return f'<a class="ref" href="{REL}/{esc(path)}" title="{esc(path)}">{esc(text)}</a>'


def code_box(path, span, title, note="", open_=False):
    """span: (시작, 끝). 줄 번호가 붙은 접이식 코드 상자."""
    s, e = span
    src = lines(path)[s - 1:e]
    width = len(str(e))
    body = "\n".join(f'<span class="ln">{str(s + i).rjust(width)}</span>{esc(t)}' for i, t in enumerate(src))
    note_html = f'<div class="code-note">{note}</div>' if note else ""
    return (f'<details class="code"{" open" if open_ else ""}><summary><span class="code-tag">코드</span> {esc(title)}'
            f' <span class="code-loc">{esc(path)}:{s}–{e}</span></summary>{note_html}<pre>{body}</pre></details>')


def py(path, name, title=None, note="", open_=False):
    return code_box(path, py_span(path, name), title or name + "()", note, open_)


def js(path, pattern, title, note="", open_=False):
    return code_box(path, brace_span(path, pattern), title, note, open_)


def block(path, start_pattern, end_pattern, title, note="", open_=False):
    return code_box(path, between(path, start_pattern, end_pattern), title, note, open_)
