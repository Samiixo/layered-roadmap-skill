#!/usr/bin/env python3
"""Упаковка скилла в .skill (zip с папкой скилла внутри).

    python3 pack.py <папка-скилла> <куда-писать.skill>
"""
import sys
import zipfile
from pathlib import Path

SKIP_DIRS = {"__pycache__", ".git", "node_modules", ".DS_Store", ".idea", ".vscode"}
SKIP_FILES = {".DS_Store", "Thumbs.db"}


def main():
    if len(sys.argv) < 3:
        print("использование: python3 pack.py <папка-скилла> <куда.skill>", file=sys.stderr)
        return 2
    src = Path(sys.argv[1]).resolve()
    dst = Path(sys.argv[2]).resolve()
    if not (src / "SKILL.md").exists():
        print(f"в {src} нет SKILL.md — это не папка скилла", file=sys.stderr)
        return 2

    dst.parent.mkdir(parents=True, exist_ok=True)
    root = src.name
    count = 0
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for path in sorted(src.rglob("*")):
            if any(part in SKIP_DIRS for part in path.parts):
                continue
            if path.name in SKIP_FILES or not path.is_file():
                continue
            arcname = f"{root}/{path.relative_to(src).as_posix()}"
            zi = zipfile.ZipInfo(arcname, date_time=(2026, 1, 1, 0, 0, 0))
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = 0o644 << 16
            if path.name in ("pre-commit",) or path.suffix == ".py":
                zi.external_attr = 0o755 << 16  # исполняемые
            z.writestr(zi, path.read_bytes())
            count += 1
    size_kb = dst.stat().st_size / 1024
    print(f"собрано: {dst}")
    print(f"файлов: {count} · размер: {size_kb:.1f} КБ · корень в архиве: {root}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
