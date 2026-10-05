#!/usr/bin/env python3
"""
Layered Roadmap — сборка и проверка карты развития.

    python3 build_roadmap.py docs/roadmap.json            # собрать доску и сводку
    python3 build_roadmap.py docs/roadmap.json --check    # только проверка, без записи
    python3 build_roadmap.py docs/roadmap.json --next     # что делать дальше

Скрипт ничего не решает за автора: он проверяет карту и рисует её.
Зависимостей нет — только стандартная библиотека Python 3.8+.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# ── словари по умолчанию (переопределяются через meta.labels) ────────────────
ORDER = ["not_started", "paused", "in_progress", "assembled", "verified", "in_prod"]
LABELS = {
    "not_started": "не начат",
    "in_progress": "в работе",
    "assembled": "собран",
    "verified": "проверен",
    "in_prod": "в проде",
    "paused": "на паузе",
    "reserve": "резерв",
    "deferred": "перенесён",
    "beyond": "за горизонтом",
}
SURFACE_LABELS = {}
START = "<!-- ROADMAP:GENERATED:START -->"
END = "<!-- ROADMAP:GENERATED:END -->"


def plural(n, one, few, many):
    a, b = abs(n) % 100, abs(n) % 10
    if 10 < a < 20:
        return many
    if 1 < b < 5:
        return few
    if b == 1:
        return one
    return many


def surface_label(key, meta):
    labels = dict(SURFACE_LABELS)
    raw = meta.get("surfaces")
    if isinstance(raw, dict):
        labels.update(raw)
    return labels.get(key, key)


# ── проверки карты ───────────────────────────────────────────────────────────
def validate(rmap):
    problems, notes = [], []
    ids, levels_ids = set(), {l["id"] for l in rmap["levels"]}
    blocks_index = {}

    def check_status(node, node_id):
        if node.get("status") not in ORDER:
            problems.append(f"{node_id}: неизвестный статус «{node.get('status')}»")
        horizon = node.get("horizon")
        if horizon and horizon not in ("in_scope", "deferred", "beyond"):
            problems.append(f"{node_id}: неизвестный горизонт «{horizon}»")
        if node.get("status") != "not_started" and not node.get("evidence"):
            notes.append(f"{node_id}: нет доказательств при статусе «{LABELS.get(node.get('status'), node.get('status'))}»")
        if horizon == "deferred" and not node.get("deferTo"):
            problems.append(f"{node_id}: перенос без deferTo")

    for lvl in rmap["levels"]:
        if lvl["id"] in ids:
            problems.append(f"дубль id слоя: {lvl['id']}")
        ids.add(lvl["id"])
        for dep in lvl.get("dependsOn", []):
            if dep not in levels_ids:
                problems.append(f"{lvl['id']}: неизвестная зависимость слоя {dep}")
            if dep == lvl["id"]:
                problems.append(f"{lvl['id']}: слой зависит от себя")
        for b in lvl.get("blocks", []):
            if not b["id"].startswith(lvl["id"] + "."):
                problems.append(f"{b['id']}: id не совпадает со слоем {lvl['id']}")
            if b["id"] in ids:
                problems.append(f"дубль id блока: {b['id']}")
            ids.add(b["id"])
            blocks_index[b["id"]] = b
            if not b.get("modules"):
                problems.append(f"{b['id']}: нет модулей")
            if b.get("status") == "paused" and not b.get("blockedBy"):
                problems.append(f"{b['id']}: статус «на паузе» без blockedBy")
            check_status(b, b["id"])
            # block_status() пересчитывает статус из модулей и молча подменяет его на доске,
            # поэтому расхождение видно только здесь: JSON остаётся источником, который читает
            # агент без генератора.
            if (b.get("status") != "paused"
                    and b.get("horizon") not in ("deferred", "beyond")
                    and b.get("modules")):
                computed = rollup(b["modules"])
                if computed != b.get("status"):
                    notes.append(
                        f"{b['id']}: статус в JSON «{LABELS.get(b['status'], b['status'])}» не равняется "
                        f"свёртке модулей «{LABELS.get(computed, computed)}» — на доске блок показан "
                        f"как {LABELS.get(computed, computed)}, правь roadmap.json")
            for m in b.get("modules", []):
                if not m["id"].startswith(b["id"] + "."):
                    problems.append(f"{m['id']}: id не совпадает с блоком {b['id']}")
                if m["id"] in ids:
                    problems.append(f"дубль id модуля: {m['id']}")
                ids.add(m["id"])
                check_status(m, m["id"])

    for bid, b in blocks_index.items():
        for dep in b.get("dependsOn", []):
            if "." in dep and dep not in blocks_index and not dep.startswith("L"):
                problems.append(f"{bid}: зависимость от несуществующего блока {dep}")
            elif "." in dep and dep.startswith("L") and dep not in blocks_index:
                problems.append(f"{bid}: зависимость от несуществующего блока {dep}")

    # дубли названий модулей между блоками — почти всегда следствие копипаста
    seen_name = {}
    for lvl in rmap["levels"]:
        for b in lvl.get("blocks", []):
            for m in b.get("modules", []):
                name = m["name"]
                if name in seen_name and seen_name[name] != b["id"]:
                    notes.append(f"одно имя модуля в двух блоках: «{name}» ({seen_name[name]} и {b['id']})")
                seen_name[name] = b["id"]

    return problems, notes


# ── свёртка статусов ─────────────────────────────────────────────────────────
def rank(status):
    return ORDER.index(status) if status in ORDER else -1


def rollup(nodes):
    alive = [n for n in nodes if n.get("horizon") not in ("deferred", "beyond")]
    if not alive:
        return "not_started"
    if any(n.get("status") == "paused" for n in alive):
        return "paused"
    ranks = [rank(n.get("status")) for n in alive]
    low = min(ranks)
    if low <= 0:
        return "in_progress" if any(r > 0 for r in ranks) else "not_started"
    return ORDER[low]


def block_status(b):
    if b.get("horizon") in ("deferred", "beyond"):
        return b.get("status", "not_started")
    computed = rollup(b.get("modules", []))
    if b.get("status") != "paused" and computed != b.get("status"):
        return computed
    return b.get("status", "not_started")


def deferred_items(b):
    return [m for m in b.get("modules", []) if m.get("horizon") in ("deferred", "beyond")]


def block_progress(b):
    mods = [m for m in b.get("modules", []) if m.get("horizon") not in ("deferred", "beyond")]
    done = sum(1 for m in mods if rank(m.get("status")) >= rank("assembled"))
    pct = round(done / len(mods) * 100) if mods else 0
    return done, len(mods), pct


def level_stats(lvl):
    blocks = lvl.get("blocks", [])
    if lvl.get("reserve"):
        return {"status": "reserve", "closed": False, "blocks": 0, "blocks_done": 0,
                "paused": 0, "modules": 0, "modules_done": 0, "pct": 0}
    alive = [b for b in blocks if b.get("horizon") not in ("deferred", "beyond")]
    # пауза — причина, по которой слой не закроется, а не статус всего слоя:
    # заблокированные блоки считаем отдельным счётчиком
    alive_open = [b for b in alive if block_status(b) != "paused"]
    status = rollup([{"status": block_status(b), "horizon": b.get("horizon")} for b in alive_open])
    mods = [m for b in blocks for m in b.get("modules", []) if m.get("horizon") not in ("deferred", "beyond")]
    done = sum(1 for m in mods if rank(m.get("status")) >= rank("assembled"))
    return {
        "status": status,
        "closed": status == "in_prod",
        "blocks": len(blocks),
        "blocks_done": sum(1 for b in blocks if block_status(b) == "in_prod"),
        "paused": sum(1 for b in blocks if block_status(b) == "paused"),
        "modules": len(mods),
        "modules_done": done,
        "pct": round(done / len(mods) * 100) if mods else 0,
    }


def compute(rmap):
    stats = [{"lvl": l, **level_stats(l)} for l in rmap["levels"]]
    current = next((s for s in stats if not s["closed"] and not s["lvl"].get("reserve")), None)

    reserve_by = {l["id"]: [] for l in rmap["levels"] if l.get("reserve")}
    for lvl in rmap["levels"]:
        for b in lvl.get("blocks", []):
            if b.get("horizon") == "deferred" and b.get("deferTo") in reserve_by:
                reserve_by[b["deferTo"]].append({"id": b["id"], "name": b["name"], "from": lvl["id"],
                                                 "reason": b.get("deferReason", ""), "type": "блок"})
            for m in deferred_items(b):
                if m.get("horizon") == "deferred" and m.get("deferTo") in reserve_by:
                    reserve_by[m["deferTo"]].append({"id": m["id"], "name": m["name"], "from": b["id"],
                                                     "reason": m.get("deferReason", ""), "type": "модуль"})

    stages = []
    for st in rmap.get("meta", {}).get("stages", []):
        inside = [s for s in stats if s["lvl"]["id"] in st.get("levels", [])]
        mods = sum(s["modules"] for s in inside)
        done = sum(s["modules_done"] for s in inside)
        statuses = [s["status"] for s in inside]
        if statuses and all(s == "reserve" for s in statuses):
            status = "reserve"
        elif "paused" in statuses:
            status = "paused"
        elif all(s == "not_started" for s in statuses):
            status = "not_started"
        elif all(s == "in_prod" for s in statuses):
            status = "in_prod"
        else:
            status = "in_progress"
        stages.append({**st, "mods": mods, "done": done,
                       "pct": round(done / mods * 100) if mods else 0, "status": status})

    outside = [d for items in reserve_by.values() for d in items]
    all_outside = 0  # всё, что вне горизонта: перенесённое и за горизонтом, включая резерв
    for lvl in rmap["levels"]:
        for b in lvl.get("blocks", []):
            if b.get("horizon") in ("deferred", "beyond"):
                all_outside += 1
            all_outside += len(deferred_items(b))
    totals = {
        "levels": len(rmap["levels"]),
        "blocks": sum(s["blocks"] for s in stats),
        "modules": sum(s["modules"] for s in stats),
        "modules_done": sum(s["modules_done"] for s in stats),
        "outside": all_outside,
        "reserve": len(outside),
    }
    return stats, current, stages, reserve_by, totals, outside


# ── сводка в markdown ────────────────────────────────────────────────────────
def markdown_summary(rmap, stats, current, stages, reserve_by, totals, outside):
    meta = rmap.get("meta", {})
    L = [f"<!-- собирается автоматически: python3 build_roadmap.py -->", ""]
    L.append(f"**Состояние на {meta.get('updated', '—')}.** Слоёв: {totals['levels']} · блоков: {totals['blocks']} · "
             f"модулей в горизонте: {totals['modules']}. Собрано: {totals['modules_done']} из {totals['modules']} "
             f"({round(totals['modules_done'] / totals['modules'] * 100) if totals['modules'] else 0}%). "
             f"Вне горизонта: {totals['outside']} (из них в резерве: {totals['reserve']}).")
    L.append("")
    if current:
        L.append(f"**Текущий слой — {current['lvl']['id']}. {current['lvl']['name']}** "
                 f"({LABELS[current['status']]}, {current['blocks_done']} из {current['blocks']} блоков в проде).")
    else:
        L.append("**Все слои закрыты.**")
    L.append("")

    if stages:
        L.append("### Стадии: что получается на выходе")
        L.append("")
        L.append("| Стадия | Слои | Что это за продукт | Готово модулей |")
        L.append("|---|---|---|---|")
        for st in stages:
            L.append(f"| **{st['name']}** | {' + '.join(st.get('levels', []))} | {st.get('product', '')} | "
                     f"{st['done']}/{st['mods']} · {st['pct']}% |")
        L.append("")
        for st in stages:
            L.append(f"**{st['name']}** ({' + '.join(st.get('levels', []))}, {LABELS[st['status']]}, {st['pct']}%)")
            L.append("")
            for key, title in (("can", "Что умеет"), ("audience", "Кому"), ("missing", "Чего ещё нет"), ("exitMeaning", "Смысл ступени")):
                if st.get(key):
                    L.append(f"- *{title}:* {st[key]}")
            L.append("")

    L.append("| Слой | Название | Зависит от | Блоков | В проде | Модулей собрано | Статус |")
    L.append("|---|---|---|---|---|---|---|")
    for s in stats:
        if s["lvl"].get("reserve"):
            L.append(f"| {s['lvl']['id']} | {s['lvl']['name']} | — | — | — | позиций в резерве: {len(reserve_by.get(s['lvl']['id'], []))} | {LABELS['reserve']} |")
            continue
        deps = ", ".join(s["lvl"].get("dependsOn", [])) or "—"
        pause = f" · на паузе: {s['paused']}" if s["paused"] else ""
        mark = LABELS[s["status"]] + (" ← текущий" if current and s["lvl"]["id"] == current["lvl"]["id"] else "")
        L.append(f"| {s['lvl']['id']} | {s['lvl']['name']} | {deps} | {s['blocks']} | {s['blocks_done']} | "
                 f"{s['modules_done']}/{s['modules']} · {s['pct']}%{pause} | {mark} |")
    L.append("")

    for lvl in rmap["levels"]:
        L.append(f"### {lvl['id']}. {lvl['name']}")
        L.append("")
        if lvl.get("goal"):
            L.append(f"*Цель:* {lvl['goal']}")
            L.append("")
        if lvl.get("exit"):
            L.append(f"*Слой закрыт, когда:* {lvl['exit']}")
            L.append("")
        if lvl.get("reserve"):
            items = reserve_by.get(lvl["id"], [])
            L.append("| Что лежит в резерве | Откуда | Почему не обязательно |" if items else "Пока пусто.")
            if items:
                L.append("|---|---|---|")
                for r in items:
                    L.append(f"| `{r['id']}` {r['name']} ({r['type']}) | {r['from']} | {r['reason'] or '—'} |")
            L.append("")
            continue
        L.append("| Блок | Что закрывает | Поверхности | Собрано | Статус |")
        L.append("|---|---|---|---|---|")
        for b in lvl.get("blocks", []):
            done, total, pct = block_progress(b)
            st = LABELS["deferred"] + f" → {b.get('deferTo')}" if b.get("horizon") == "deferred" else LABELS[block_status(b)]
            extra = f" (перенесено модулей: {len(deferred_items(b))})" if deferred_items(b) else ""
            surfaces = ", ".join(surface_label(x, rmap.get("meta", {})) for x in b.get("surfaces", []))
            L.append(f"| `{b['id']}` {b['name']} | {b.get('why', '')} | {surfaces} | {done}/{total} · {pct}%{extra} | {st} |")
        L.append("")

    if outside:
        L.append("### Вне текущего горизонта")
        L.append("")
        L.append("| Что | Вид | Куда | Причина |")
        L.append("|---|---|---|---|")
        for d in outside:
            L.append(f"| `{d['id']}` {d['name']} | {LABELS['deferred']} | — | {d['reason'] or '—'} |")
        L.append("")
    return "\n".join(L)


# ── доска ────────────────────────────────────────────────────────────────────
CSS = """
:root{--bg:#090909;--surface:#121212;--elevated:#1B1B1B;--inset:#0F0F0F;--line:rgba(255,255,255,.06);--line-2:rgba(255,255,255,.10);
--t1:#F7F7F2;--t2:#A2A2A2;--t3:#8A8A8A;--accent:#D6FF00;--accent-dim:rgba(214,255,0,.10);--accent-border:rgba(214,255,0,.24);--accent-ink:#111;
--ok:#5FAE76;--warn:#E8C468;--err:#CF6B6B;--info:#7FA6D6;--util:#9097A3;--util-dim:rgba(126,135,149,.14);
--r:10px;--r-lg:16px;--ease:cubic-bezier(.22,1,.36,1);
--f-d:'Space Grotesk',system-ui,sans-serif;--f-b:'Geist','Inter',system-ui,sans-serif;--f-m:'JetBrains Mono',monospace}
html[data-theme="light"]{--bg:#F4F4F3;--surface:#FAFAF9;--elevated:#fff;--inset:#F0F0EE;--line:rgba(20,20,16,.10);--line-2:rgba(20,20,16,.16);
--t1:#141410;--t2:#4A4A44;--t3:#6E6E67;--accent:#141410;--accent-dim:rgba(20,20,16,.06);--accent-border:rgba(20,20,16,.22);--accent-ink:#fff;
--ok:#3F7D54;--warn:#8A6414;--err:#A24A4A;--info:#3C5F8A;--util:#6E6E67;--util-dim:rgba(20,20,16,.06)}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--t1);font:400 15px/1.6 var(--f-b);-webkit-font-smoothing:antialiased}
a{color:inherit;text-decoration:none}
code{font:400 12px/1.5 var(--f-m);background:var(--inset);border:1px solid var(--line);border-radius:5px;padding:1px 5px}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px;border-radius:4px}
.wrap{max-width:1240px;margin:0 auto;padding:0 28px 120px}
header.top{position:sticky;top:0;z-index:20;display:flex;align-items:center;gap:14px;padding:14px 0;background:var(--bg);border-bottom:1px solid var(--line);flex-wrap:wrap}
.logo{display:flex;align-items:center;gap:9px;font:600 15px var(--f-d);letter-spacing:.14em}
.logo i{width:26px;height:26px;border-radius:7px;background:var(--accent);color:var(--accent-ink);display:grid;place-items:center;font:700 13px var(--f-d);font-style:normal}
.top .meta{font:400 12px var(--f-m);color:var(--t3)}
.top .sp{margin-left:auto}
.top input{background:var(--surface);border:1px solid var(--line-2);border-radius:999px;padding:8px 14px;color:var(--t1);font:400 13px var(--f-b);width:250px}
.top input:focus{border-color:var(--accent);outline:none}
.top button{background:var(--surface);border:1px solid var(--line);color:var(--t2);border-radius:8px;padding:7px 11px;font:500 12px var(--f-b);cursor:pointer}
h1{font:700 clamp(26px,3.4vw,38px)/1.12 var(--f-d);letter-spacing:-.02em;margin:34px 0 10px}
.lead{color:var(--t2);max-width:76ch}
.kpis{display:flex;flex-wrap:wrap;gap:8px;margin:18px 0 26px}
.kpi{background:var(--surface);border:1px solid var(--line);border-radius:var(--r);padding:12px 16px;min-width:140px}
.kpi b{display:block;font:600 22px var(--f-d)}
.kpi span{font:400 11.5px var(--f-m);color:var(--t3);text-transform:uppercase;letter-spacing:.08em}
.kpi.hot{border-color:var(--accent-border);background:var(--accent-dim)}
.ladder{border:1px solid var(--line);border-radius:var(--r-lg);overflow:hidden;background:var(--surface);margin-bottom:34px}
.lrow{display:grid;grid-template-columns:46px minmax(0,1fr) 160px 64px 104px;gap:12px;align-items:center;padding:11px 16px;border-bottom:1px solid var(--line);font-size:13.5px}
.lrow:last-child{border-bottom:none}
.lrow:hover{background:var(--inset)}
.lrow.active{background:var(--accent-dim);box-shadow:inset 3px 0 0 var(--accent)}
.lid{font:600 12px var(--f-m);color:var(--t3)}
.lname b{font:600 14px var(--f-d);display:block}
.lname em{font-style:normal;font-size:12px;color:var(--t3)}
.lbar{height:6px;border-radius:999px;background:var(--util-dim);overflow:hidden}
.lbar i{display:block;height:100%;background:var(--info)}
.lrow.closed .lbar i{background:var(--ok)}
.lrow.active .lbar i{background:var(--accent)}
.lrow.reserve .lbar i{background:repeating-linear-gradient(90deg,var(--util-dim) 0 6px,transparent 6px 12px)}
.lnum{font:500 12px var(--f-m);color:var(--t3);text-align:right}
.st{font:500 11px var(--f-m);padding:3px 9px;border-radius:999px;white-space:nowrap;text-align:center}
.s-not_started{background:var(--util-dim);color:var(--t3)}
.s-in_progress{background:rgba(232,196,104,.16);color:var(--warn)}
.s-paused{background:rgba(207,107,107,.16);color:var(--err)}
.s-assembled{background:rgba(127,166,214,.16);color:var(--info)}
.s-verified{background:rgba(95,174,118,.16);color:var(--ok)}
.s-in_prod{background:var(--accent-dim);color:var(--accent);border:1px solid var(--accent-border)}
.s-deferred{background:var(--util-dim);color:var(--t3)}
.s-reserve{background:var(--util-dim);color:var(--t2);border:1px dashed var(--line-2)}
.stages>h2{margin:0 0 14px}
.stage{background:var(--surface);border:1px solid var(--line);border-left:2px solid var(--accent-border);border-radius:var(--r-lg);padding:16px 18px;margin-bottom:12px}
.stage.closed{border-left-color:var(--ok)}
.stage header{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:8px}
.stage header b{font:600 16.5px var(--f-d)}
.stage .lids{font:500 11.5px var(--f-m);color:var(--t3);display:flex;gap:6px;align-items:center}
.stage .lid{padding:2px 7px;border:1px solid var(--line-2);border-radius:6px}
.stage .sprod{color:var(--t1);font-size:14.5px;margin-bottom:8px}
.stage .srow{display:grid;grid-template-columns:110px minmax(0,1fr);gap:10px;font-size:13.5px;color:var(--t2);margin-top:5px}
.stage .srow span{font:500 10px var(--f-m);letter-spacing:.1em;text-transform:uppercase;color:var(--t3);padding-top:3px}
.stage .srow.miss{color:var(--warn)}
.level{margin:40px 0 0;scroll-margin-top:70px}
.level.hide,.block.hide{display:none}
.lhead{border-top:1px solid var(--line-2);padding:22px 0 14px}
.ltitle{display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.ltitle h2{font:600 clamp(20px,2.2vw,27px)/1.2 var(--f-d);letter-spacing:-.015em}
.lgoal{color:var(--t2);margin-top:8px;max-width:80ch}
.lexit{color:var(--t3);font-size:13.5px;margin-top:6px;max-width:90ch}
.blocks{display:grid;grid-template-columns:minmax(0,1fr);gap:14px}
.block{background:var(--surface);border:1px solid var(--line);border-radius:var(--r-lg);padding:16px 18px;min-width:0}
.bhead{display:flex;gap:14px;justify-content:space-between;align-items:flex-start}
.bid{font:600 11px var(--f-m);color:var(--t3)}
.block h4{font:600 17px/1.3 var(--f-d);margin-top:2px}
.bbadges{display:flex;flex-wrap:wrap;gap:6px;justify-content:flex-end}
.chip{font:500 11px var(--f-m);padding:3px 8px;border-radius:6px;background:var(--inset);border:1px solid var(--line);color:var(--t3);white-space:nowrap}
.chip.src{background:rgba(127,166,214,.14);color:var(--info);border-color:transparent}
.chip.stage{background:var(--accent-dim);color:var(--accent);border-color:var(--accent-border)}
.why{color:var(--t2);font-size:14px;margin-top:9px;max-width:88ch}
.block-note{margin-top:8px;font-size:13.5px;color:var(--err)}
.deps{margin-top:6px;font-size:12.5px;color:var(--t3)}
.bar{position:relative;height:6px;border-radius:999px;background:var(--util-dim);margin:12px 0 4px;overflow:hidden}
.bar i{display:block;height:100%;background:var(--info)}
.bar span{position:absolute;right:0;top:8px;font:500 11px var(--f-m);color:var(--t3)}
.dod{margin-top:18px;font-size:13.5px;color:var(--t2)}
.dod summary{cursor:pointer;color:var(--t3);font:500 12px var(--f-m);text-transform:uppercase;letter-spacing:.08em;list-style:none}
.dod summary::after{content:' ▾'}
.dod[open] summary::after{content:' ▴'}
.dod ul{margin:8px 0 0 18px}
.mods-wrap{overflow-x:auto;margin-top:14px}
table.mods{width:100%;border-collapse:collapse;font-size:13.5px}
.mods td{padding:9px 10px;border-top:1px solid var(--line);vertical-align:top}
.mods tr:hover{background:var(--inset)}
.mods .mid{white-space:nowrap;color:var(--t3);font:500 12px var(--f-m)}
.what{color:var(--t2);font-size:13.5px;margin-top:3px}
.ev{margin-top:5px;display:flex;flex-wrap:wrap;gap:5px}
.def{margin-top:6px;font-size:12.5px;color:var(--warn)}
.reserve-box .rnote{color:var(--t3);font-size:13.5px;max-width:80ch;margin-bottom:12px}
.noresults{display:none;padding:26px;border:1px dashed var(--line-2);border-radius:var(--r-lg);text-align:center;color:var(--t3)}
.noresults.show{display:block}
.filters{display:flex;flex-wrap:wrap;gap:8px;margin:20px 0}
.filters button{background:var(--surface);border:1px solid var(--line);color:var(--t2);border-radius:999px;padding:6px 12px;font:500 12.5px var(--f-b);cursor:pointer}
.filters button.on{background:var(--accent-dim);border-color:var(--accent-border);color:var(--accent)}
footer{border-top:1px solid var(--line);margin-top:40px;padding-top:18px;font-size:13px;color:var(--t3)}
@media (max-width:860px){
.wrap{padding:0 16px 80px}
.top .sp{display:none}.top .meta{order:3;width:100%}.top input{flex:1;min-width:0;width:auto}
.lrow{grid-template-columns:38px minmax(0,1fr) 84px;gap:8px}
.lrow .lbar,.lrow .lnum{display:none}
.mods .mid{white-space:normal;font-size:11px}
.bhead{flex-direction:column}.bbadges{justify-content:flex-start}
code{white-space:normal;word-break:break-word}
}
@media print{.top,.filters{display:none}.block{break-inside:avoid}.lrow{break-inside:avoid}}
"""

JS = """
'use strict';
(function(){
  function $(s,r){return (r||document).querySelector(s)}
  function $$(s,r){return Array.prototype.slice.call((r||document).querySelectorAll(s))}
  var blocks=$$('.block'), levels=$$('.level'), q='', st='all', sf='all';
  function apply(){
    var needle=q.trim().toLowerCase(), shown=0;
    blocks.forEach(function(b){
      var okText=!needle||b.dataset.text.indexOf(needle)>=0;
      var okStatus=st==='all'||b.dataset.status===st;
      var okSurface=sf==='all'||(b.dataset.surfaces||'').split(' ').indexOf(sf)>=0;
      var ok=okText&&okStatus&&okSurface;
      b.classList.toggle('hide',!ok);
      if(ok)shown++;
    });
    levels.forEach(function(l){l.classList.toggle('hide',!$$('.block:not(.hide)',l).length)});
    $('#noresults').classList.toggle('show',shown===0);
  }
  $('#q').addEventListener('input',function(e){q=e.target.value;apply()});
  $$('#filters button').forEach(function(btn){
    btn.addEventListener('click',function(){
      var group=btn.dataset.f?'#filters button[data-f]':'#filters button[data-s]';
      $$(group).forEach(function(x){x.classList.remove('on')});
      btn.classList.add('on');
      if(btn.dataset.f)st=btn.dataset.f; else sf=btn.dataset.s;
      apply();
    });
  });
  document.addEventListener('keydown',function(e){
    if(e.key==='/'&&document.activeElement!==$('#q')){e.preventDefault();$('#q').focus()}
    if(e.key==='Escape'){$('#q').value='';q='';apply();$('#q').blur()}
  });
  $('#theme').addEventListener('click',function(){
    var light=document.documentElement.getAttribute('data-theme')==='light';
    document.documentElement.setAttribute('data-theme',light?'dark':'light');
    $('#theme').textContent=light?'Тёмная тема':'Светлая тема';
  });
  apply();
})();
"""


def esc(s):
    return (str(s if s is not None else '')
            .replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            .replace('"', '&quot;').replace("'", '&#39;'))


def board_html(rmap, stats, current, stages, reserve_by, totals):
    meta = rmap.get("meta", {})
    title = meta.get("title", "Карта развития")
    surfaces = sorted({s for l in rmap["levels"] for b in l.get("blocks", []) for s in b.get("surfaces", [])})

    def status_chip(key):
        return f'<span class="st s-{key}">{esc(LABELS.get(key, key))}</span>'

    rows = []
    for s in stats:
        lvl = s["lvl"]
        if lvl.get("reserve"):
            n = len(reserve_by.get(lvl["id"], []))
            rows.append(f'<a class="lrow reserve" href="#{esc(lvl["id"])}"><span class="lid">{esc(lvl["id"])}</span>'
                        f'<span class="lname"><b>{esc(lvl["name"])}</b><em>{esc(lvl.get("goal", ""))}</em></span>'
                        f'<span class="lbar"><i style="width:100%"></i></span><span class="lnum">{n}</span>'
                        f'{status_chip("reserve")}</a>')
            continue
        cls = "closed" if s["closed"] else ("active" if current and lvl["id"] == current["lvl"]["id"] else "open")
        pause = f' · заблокировано блоков: {s["paused"]}' if s["paused"] else ""
        rows.append(f'<a class="lrow {cls}" href="#{esc(lvl["id"])}"><span class="lid">{esc(lvl["id"])}</span>'
                    f'<span class="lname"><b>{esc(lvl["name"])}</b><em>{esc(lvl.get("goal", ""))}{esc(pause)}</em></span>'
                    f'<span class="lbar"><i style="width:{s["pct"]}%"></i></span>'
                    f'<span class="lnum">{s["modules_done"]}/{s["modules"]}</span>{status_chip(s["status"])}</a>')

    stage_cards = []
    for st in stages:
        chips = " + ".join(f'<a class="lid" href="#{esc(l)}">{esc(l)}</a>' for l in st.get("levels", []))
        rows_html = "".join(
            f'<p class="srow{" miss" if k == "missing" else ""}"><span>{t}</span>{esc(st.get(k, ""))}</p>'
            for k, t in (("can", "что умеет"), ("audience", "кому"), ("missing", "чего ещё нет"), ("exitMeaning", "смысл"))
            if st.get(k)
        )
        stage_cards.append(f'<article class="stage{" closed" if st["status"] == "in_prod" else ""}">'
                           f'<header><b>{esc(st["name"])}</b><span class="lids">{chips}</span>{status_chip(st["status"])}</header>'
                           f'<p class="sprod">{esc(st.get("product", ""))}</p>{rows_html}'
                           f'<div class="bar"><i style="width:{st["pct"]}%"></i><span>{st["done"]}/{st["mods"]}</span></div></article>')

    levels_html = []
    for lvl in rmap["levels"]:
        s = next(x for x in stats if x["lvl"]["id"] == lvl["id"])
        stage = next((st for st in stages if lvl["id"] in st.get("levels", [])), None)
        stage_chip = f'<span class="chip stage">{esc(stage["name"])}</span>' if stage else ""
        head = (f'<header class="lhead"><div class="ltitle"><span class="lid">{esc(lvl["id"])}</span>'
                f'<h2>{esc(lvl["name"])}</h2>{stage_chip}'
                f'{status_chip("reserve" if lvl.get("reserve") else s["status"])}</div>'
                f'<p class="lgoal">{esc(lvl.get("goal", ""))}</p>'
                f'<p class="lexit"><b>Слой закрыт, когда:</b> {esc(lvl.get("exit", ""))}</p></header>')

        if lvl.get("reserve"):
            items = reserve_by.get(lvl["id"], [])
            body = ('<div class="reserve-box"><p class="rnote">Слой-накопитель: ничего не блокирует и не закрывается по общему правилу. '
                    'Собирается разом одной волной, когда решишь, что резерв пора разобрать.</p>')
            if items:
                rows_rsv = []
                for i in items:
                    reason_html = f'<div class="def">{esc(i["reason"])}</div>' if i["reason"] else ""
                    rows_rsv.append(
                        '<tr>'
                        f'<td class="mid">{esc(i["id"])}</td>'
                        f'<td><b>{esc(i["name"])}</b><div class="what">{esc(i["type"])} · из {esc(i["from"])}</div>{reason_html}</td>'
                        f'<td class="mid">{esc(i["from"])}</td><td class="mid">{esc(i["type"])}</td>'
                        '</tr>'
                    )
                body += '<div class="mods-wrap"><table class="mods"><tbody>' + "".join(rows_rsv) + '</tbody></table></div>'
            else:
                body += '<p class="rnote">Пока пусто.</p>'
            body += '</div>'
        else:
            cards = []
            for b in lvl.get("blocks", []):
                done, total, pct = block_progress(b)
                st_key = "deferred" if b.get("horizon") == "deferred" else block_status(b)
                chips = "".join(f'<span class="chip">{esc(surface_label(x, meta))}</span>' for x in b.get("surfaces", []))
                mods = []
                for m in b.get("modules", []):
                    ev = "".join(f'<code>{esc(e)}</code>' for e in m.get("evidence", [])[:4])
                    note = ""
                    if m.get("horizon") == "deferred":
                        note = f'<div class="def">перенесён → {esc(m.get("deferTo", "—"))}{": " + esc(m.get("deferReason", "")) if m.get("deferReason") else ""}</div>'
                    elif m.get("horizon") == "beyond":
                        note = f'<div class="def">за горизонтом{": " + esc(m.get("deferReason", "")) if m.get("deferReason") else ""}</div>'
                    mods.append(f'<tr><td class="mid">{esc(m["id"])}</td>'
                                f'<td><b>{esc(m["name"])}</b>' + (' <span class="chip src">ресёрч</span>' if m.get("source") in ("research", "ресёрч") else "") +
                                f'<div class="what">{esc(m.get("what", ""))}</div>'
                                f'{"<div class=ev>" + ev + "</div>" if ev else ""}{note}</td>'
                                f'<td class="mid">{esc(surface_label(m.get("surface", ""), meta))}</td>'
                                f'<td class="mid"><span class="st s-{m.get("status", "not_started")}">{esc(LABELS.get(m.get("status"), m.get("status")))}</span></td></tr>')
                dod = ("<details class=\"dod\"><summary>Критерии готовности</summary><ul>" +
                       "".join(f'<li>{esc(d)}</li>' for d in b.get("dod", [])) + "</ul></details>") if b.get("dod") else ""
                deps = ("<p class=\"deps\">зависит от: " + " ".join(f'<code>{esc(d)}</code>' for d in b.get("dependsOn", [])) + "</p>") if b.get("dependsOn") else ""
                blocked = f'<p class="block-note">⛔ {esc(b.get("blockedBy"))}</p>' if b.get("blockedBy") else ""
                text = esc((b["id"] + " " + b["name"] + " " + b.get("why", "") + " " +
                            " ".join(b.get("dod", [])) + " " +
                            " ".join(m["name"] + " " + m.get("what", "") for m in b.get("modules", []))).lower())
                cards.append(
                    f'<article class="block" data-status="{st_key}" data-surfaces="{" ".join(b.get("surfaces", []))}" data-text="{text}">'
                    f'<header class="bhead"><div><span class="bid">{esc(b["id"])}</span><h4>{esc(b["name"])}</h4></div>'
                    f'<div class="bbadges">{status_chip(st_key)}{chips}</div></header>'
                    f'<p class="why">{esc(b.get("why", ""))}</p>{blocked}{deps}'
                    f'<div class="bar"><i style="width:{pct}%"></i><span>{done}/{total}</span></div>{dod}'
                    f'<div class="mods-wrap"><table class="mods"><tbody>{"".join(mods)}</tbody></table></div></article>')
            body = f'<div class="blocks">{"".join(cards)}</div>'

        levels_html.append(f'<section class="level" id="{esc(lvl["id"])}">{head}{body}</section>')

    filters = ['<button data-f="all" class="on">Все статусы</button>']
    for key in ("in_prod", "verified", "assembled", "in_progress", "paused", "not_started"):
        filters.append(f'<button data-f="{key}">{esc(LABELS[key])}</button>')
    for s in surfaces:
        filters.append(f'<button data-s="{esc(s)}">{esc(surface_label(s, meta))}</button>')

    cur = f'<div class="kpi hot"><b>{esc(current["lvl"]["id"])} · {esc(current["lvl"]["name"])}</b><span>текущий слой · {esc(LABELS[current["status"]])}</span></div>' if current else ""
    lead = meta.get("lead", "Слои закрываются по порядку: пока слой не собран целиком, следующий не начинается. "
                            "Внутри слоя — блоки, внутри блока — модули. Источник правды — <code>roadmap.json</code>, "
                            "эта страница и сводка собираются скриптом.")

    return ("<!DOCTYPE html>\n<html lang=\"ru\" data-theme=\"dark\">\n<head>\n<meta charset=\"UTF-8\">\n"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">\n"
            f"<title>{esc(title)} — {totals['blocks']} блоков, {totals['modules']} модулей</title>\n"
            "<link rel=\"preconnect\" href=\"https://fonts.googleapis.com\">\n"
            "<link href=\"https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&family=Geist:wght@300;400;500;600&family=JetBrains+Mono:wght@400;500&display=swap\" rel=\"stylesheet\">\n"
            f"<style>{CSS}</style>\n</head>\n<body>\n<div class=\"wrap\">\n"
            f"<header class=\"top\"><div class=\"logo\"><i>◆</i> {esc(title)}</div>"
            f"<span class=\"meta\">обновлено {esc(meta.get('updated', '—'))}</span><span class=\"sp\"></span>"
            f"<input id=\"q\" type=\"search\" placeholder=\"Поиск по блокам и модулям…\" autocomplete=\"off\">"
            f"<button id=\"theme\">Светлая тема</button></header>\n"
            f"<h1>{esc(meta.get('headline', 'Карта развития: послойный порядок работ'))}</h1>\n"
            f"<p class=\"lead\">{lead}</p>\n"
            f"<div class=\"kpis\"><div class=\"kpi\"><b>{totals['levels']}</b><span>слоёв</span></div>"
            f"<div class=\"kpi\"><b>{totals['blocks']}</b><span>блоков</span></div>"
            f"<div class=\"kpi\"><b>{totals['modules_done']}/{totals['modules']}</b><span>модулей собрано</span></div>"
            f"<div class=\"kpi\"><b>{totals['outside']}</b><span>вне горизонта</span></div>{cur}</div>\n"
            f"<nav class=\"ladder\">{''.join(rows)}</nav>\n"
            f"<section class=\"stages\"><h2>Стадии: что получается на выходе</h2>{''.join(stage_cards)}</section>\n"
            f"<div class=\"filters\" id=\"filters\">{''.join(filters)}</div>\n"
            f"{''.join(levels_html)}\n"
            "<div class=\"noresults\" id=\"noresults\">Ничего не найдено — сбросьте фильтр или измените запрос.</div>\n"
            f"<footer>Слои, стадии, блоки и модули описаны в <code>roadmap.json</code>. Правила ведения карты — "
            f"в <code>ROADMAP.md</code>. Единица релиза — модуль или блок; слой закрывается целиком.</footer>\n"
            f"</div>\n<script>{JS}</script>\n</body>\n</html>\n")


# ── режим «что делать дальше» ────────────────────────────────────────────────
def print_next(rmap, stats, current, reserve_by):
    if not current:
        print("Все слои закрыты. Остался только резерв — его собирают одной волной по решению владельца.")
        return
    lvl = current["lvl"]
    print(f"\nСЛОЙ {lvl['id']} · {lvl['name']} — {LABELS[current['status']]}")
    print(f"модулей собрано {current['modules_done']} из {current['modules']} ({current['pct']}%), "
          f"блоков в проде {current['blocks_done']} из {current['blocks']}")
    print(f"слой закрыт, когда: {lvl.get('exit', '—')}")

    alive = [b for b in lvl.get("blocks", []) if b.get("horizon") not in ("deferred", "beyond")]

    work = [b for b in alive if any(m.get("status") in ("in_progress", "paused") for m in b.get("modules", []))]
    if work:
        print("\nВ РАБОТЕ (доводим до конца, не начиная нового):")
        for b in work:
            print(f"  {b['id']} {b['name']} — {LABELS[block_status(b)]}")
            for m in b.get("modules", []):
                if m.get("status") in ("in_progress", "paused"):
                    mark = "⏸" if m.get("status") == "paused" else "·"
                    by = f" — {m['blockedBy']}" if m.get("blockedBy") else ""
                    print(f"      {mark} {m['name']}{by}")

    done = {b["id"] for l in rmap["levels"] for b in l.get("blocks", []) if block_status(b) == "in_prod"}

    def missing_of(b):
        return [d for d in b.get("dependsOn", []) if "." in d and d not in done]

    candidates = sorted(
        [(b, missing_of(b)) for b in alive if block_status(b) == "not_started"],
        key=lambda pair: len(pair[1]),
    )
    ready = next((c for c in candidates if not c[1]), None)
    if ready:
        b = ready[0]
        print(f"\nСЛЕДУЮЩИЙ БЛОК: {b['id']} {b['name']}")
        print(f"  зачем: {b.get('why', '')}")
        print(f"  поверхности: {', '.join(surface_label(x, rmap.get('meta', {})) for x in b.get('surfaces', []))}")
        print("  порядок модулей:")
        for i, m in enumerate([m for m in b.get("modules", []) if m.get("horizon") not in ("deferred", "beyond")], 1):
            print(f"    {i}. {m['name']} — {m.get('what', '')[:90]}")
    elif candidates:
        b, lack = candidates[0]
        print(f"\nСЛЕДУЮЩИЙ БЛОК: {b['id']} {b['name']} — пока ждёт {', '.join(lack)}")
        print("  как только зависимости уедут в прод — берём этот блок.")

    paused = [b for b in alive if block_status(b) == "paused"]
    waiting = [(b, missing_of(b)) for b in alive if block_status(b) == "not_started" and missing_of(b)]
    if paused or waiting:
        print("\nЗАБЛОКИРОВАНО:")
        for b in paused:
            print(f"  ⏸ {b['id']} {b['name']} — {b.get('blockedBy', 'причина не указана')}")
        for b, lack in waiting:
            print(f"  … {b['id']} {b['name']} ждёт {', '.join(lack)}")

    reserve = sum(len(v) for v in reserve_by.values())
    print(f"\nРезерв: {reserve} {plural(reserve, 'позиция', 'позиции', 'позиций')} — не трогаем, пока слой не закрыт.")
    print("После работы: обновить roadmap.json (статус + evidence) и пересобрать карту.\n")


# ── точка входа ──────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="Сборка и проверка карты развития")
    ap.add_argument("map", nargs="?", default="docs/roadmap.json", help="путь к roadmap.json")
    ap.add_argument("--check", action="store_true", help="только проверка, без записи файлов")
    ap.add_argument("--next", action="store_true", help="показать, что делать дальше")
    ap.add_argument("--md", help="куда писать сводку (по умолчанию рядом с картой)")
    ap.add_argument("--html", help="куда писать доску (по умолчанию на уровень выше карты)")
    args = ap.parse_args()

    src = Path(args.map).resolve()
    if not src.exists():
        print(f"не найден файл карты: {src}", file=sys.stderr)
        return 2

    rmap = json.loads(src.read_text(encoding="utf-8"))
    global LABELS
    LABELS = {**LABELS, **rmap.get("meta", {}).get("labels", {})}

    problems, notes = validate(rmap)
    stats, current, stages, reserve_by, totals, outside = compute(rmap)

    if args.next:
        print_next(rmap, stats, current, reserve_by)

    if args.next or args.check:
        if problems:
            unique = sorted(set(problems))
            print(f"\nпроблемы карты ({len(unique)}):\n- " + "\n- ".join(unique), file=sys.stderr)
            return 1
        if notes:
            print("\nзаметки:\n- " + "\n- ".join(sorted(set(notes))[:10]))
        if args.check:
            print("карта прошла проверку")
        return 0

    # доска ложится в корень проекта, если карта в docs/, иначе рядом с картой;
    # сводка всегда рядом с картой
    parent = src.parent
    default_html = (parent.parent if parent.name in ("docs", "doc") else parent) / "ROADMAP.html"
    html_path = Path(args.html) if args.html else default_html
    md_path = Path(args.md) if args.md else parent / "ROADMAP.md"

    html_path.write_text(board_html(rmap, stats, current, stages, reserve_by, totals), encoding="utf-8")

    summary = markdown_summary(rmap, stats, current, stages, reserve_by, totals, outside)
    if md_path.exists():
        text = md_path.read_text(encoding="utf-8")
        a, b = text.find(START), text.find(END)
        if a != -1 and b != -1:
            text = text[:a + len(START)] + "\n" + summary + "\n" + text[b:]
            md_path.write_text(text, encoding="utf-8")
        else:
            notes.append(f"{md_path.name}: не найдены маркеры {START} / {END} — сводка не вставлена")
    else:
        md_path.write_text("# Карта развития\n\n" + START + "\n" + summary + "\n" + END + "\n", encoding="utf-8")

    done_pct = round(totals["modules_done"] / totals["modules"] * 100) if totals["modules"] else 0
    print(f"карта: слоёв {totals['levels']} · блоков {totals['blocks']} · модулей в горизонте {totals['modules']} "
          f"(собрано {totals['modules_done']}, {done_pct}%) · вне горизонта {totals['outside']}")
    print(f"текущий слой: {current['lvl']['id']} · {current['lvl']['name']} ({LABELS[current['status']]})"
          if current else "все слои закрыты")
    print(f"доска: {html_path} · сводка: {md_path}")
    if notes:
        print("\nзаметки:\n- " + "\n- ".join(sorted(set(notes))[:10]))

    if problems:
        unique = sorted(set(problems))
        print(f"\nпроблемы карты ({len(unique)}):\n- " + "\n- ".join(unique), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
