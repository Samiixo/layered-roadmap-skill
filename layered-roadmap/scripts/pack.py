#!/usr/bin/env python3
"""Упаковка скилла в .skill (zip с папкой скилла внутри).

    python3 pack.py <папка-скилла> <куда-писать.skill>
"""
import sys
import zipfile
from pathlib import Path

SKIP_DIRS = {"__pycache__", ".git", "node_modules", ".DS_Store", ".idea", ".vscode"}
SKIP_FILES = {".DS_Store", "Thumbs.db"}
DESC_LIMIT = 1024  # лимит описания в диагностике скиллов


def check_frontmatter(skill_md: Path) -> list:
    """Проверки, из-за которых скилл не загрузится или будет ругаться в диагностике."""
    problems = []
    text = skill_md.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return ["SKILL.md: нет YAML-шапки (frontmatter) в начале файла"]
    parts = text.split("---", 2)
    block = parts[1] if len(parts) >= 3 else ""
    for field in ("name", "description"):
        if f"\n{field}:" not in "\n" + block.strip() + "\n":
            problems.append(f"SKILL.md: в шапке нет поля {field}")
    desc, name = "", ""
    for line in block.splitlines():
        if line.startswith("description:"):
            desc = line[len("description:"):].strip()
        if line.startswith("name:"):
            name = line[len("name:"):].strip()
    if not desc:
        problems.append("SKILL.md: пустое описание")
    else:
        if len(desc) > DESC_LIMIT:
            problems.append(f"описание длиннее лимита: {len(desc)} из {DESC_LIMIT} символов — "
                            f"сократи на {len(desc) - DESC_LIMIT}")
        if ": " in desc:
            problems.append("в описании есть «: » — незакавыченные двоеточие и пробел ломают YAML; замени на тире")
    if name and name != skill_md.parent.name:
        problems.append(f"имя скилла «{name}» не совпадает с именем папки «{skill_md.parent.name}»")
    return problems


def main():
    if len(sys.argv) < 3:
        print("использование: python3 pack.py <папка-скилла> <куда.skill>", file=sys.stderr)
        return 2
    src = Path(sys.argv[1]).resolve()
    dst = Path(sys.argv[2]).resolve()
    if not (src / "SKILL.md").exists():
        print(f"в {src} нет SKILL.md — это не папка скилла", file=sys.stderr)
        return 2

    issues = check_frontmatter(src / "SKILL.md")
    if issues:
        print("скилл не готов к упаковке:", file=sys.stderr)
        for i in issues:
            print(f"  ✗ {i}", file=sys.stderr)
        return 1

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
