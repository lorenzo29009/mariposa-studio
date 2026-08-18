#!/usr/bin/env python3
"""Regenerate docs/INDEX.md — every public symbol in the app, with file:line.

    ./venv/bin/python scripts/gen_index.py          # write docs/INDEX.md
    ./venv/bin/python scripts/gen_index.py --check  # fail if it is out of date

The index exists so a reader (human or AI) can find where something lives
without grepping or opening whole modules. It is generated from the source, so
it cannot drift silently — `--check` is what keeps it honest.

Stdlib only, no Qt: it parses with `ast` and never imports the app.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
OUT = APP_DIR / "docs" / "INDEX.md"

# Directories scanned, in the order they appear in the index.
ROOTS = [
    ("src", "The app (`src/`)"),
    ("scripts", "Build & test scripts (`scripts/`)"),
    ("tools", "Bundled tool scripts (`tools/`) — separate processes, not imported"),
]


def first_line(node: ast.AST) -> str:
    """The first sentence of a docstring, flattened to one line."""
    doc = ast.get_docstring(node) or ""
    line = doc.strip().split("\n", 1)[0].strip()
    return line[:110]


def module_summary(tree: ast.Module) -> str:
    doc = ast.get_docstring(tree) or ""
    para = doc.strip().split("\n\n", 1)[0].replace("\n", " ").strip()
    return para[:160]


def is_qt_override(name: str) -> bool:
    """Qt's own API is camelCase; everything this app writes is snake_case.

    So a camelCase method is a reimplemented Qt virtual (`paintEvent`,
    `sizeHint`, `eventFilter`…) — Qt's documentation, not this app's, and pure
    noise in an index of what the app itself offers."""
    return any(ch.isupper() for ch in name)


def symbols(tree: ast.Module) -> list[tuple[str, int, str, str]]:
    """(kind, lineno, name, summary) for every public top-level definition."""
    out = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
            out.append(("class", node.lineno, node.name, first_line(node)))
            for sub in node.body:
                if not isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if sub.name.startswith("_") or is_qt_override(sub.name):
                    continue
                out.append(("method", sub.lineno,
                            f"{node.name}.{sub.name}", first_line(sub)))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and not node.name.startswith("_"):
            out.append(("def", node.lineno, node.name, first_line(node)))
    return out


def build() -> str:
    lines = [
        "# Symbol index",
        "",
        "Generated — do not edit by hand. Refresh with:",
        "",
        "```bash",
        "./venv/bin/python scripts/gen_index.py",
        "```",
        "",
        "Public top-level classes, methods and functions only; anything named "
        "with a leading `_` is internal to its module. Line numbers are a "
        "starting point, not a promise.",
        "",
    ]
    for root, title in ROOTS:
        lines += [f"## {title}", ""]
        for path in sorted((APP_DIR / root).rglob("*.py")):
            if any(p in {"venv", "__pycache__", "dist"} for p in path.parts):
                continue
            rel = path.relative_to(APP_DIR)
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
            syms = symbols(tree)
            n = len(path.read_text(encoding="utf-8").splitlines())
            lines.append(f"### `{rel}` — {n} lines")
            summary = module_summary(tree)
            if summary:
                lines.append(f"{summary}")
            lines.append("")
            if not syms:
                lines += ["_No public symbols._", ""]
                continue
            # Names + line numbers only. The one-line summary of a symbol is in
            # its own docstring, one Read away; repeating it here would double
            # the index and give it a second place to go stale.
            lines.append(" · ".join(f"`{name}`:{lineno}"
                                    for _kind, lineno, name, _doc in syms))
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    text = build()
    if "--check" in sys.argv:
        current = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if current != text:
            print("docs/INDEX.md is out of date — run scripts/gen_index.py")
            return 1
        print("INDEX OK")
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUT.relative_to(APP_DIR)} "
          f"({len(text.splitlines())} lines, ~{len(text) // 4} tokens)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
