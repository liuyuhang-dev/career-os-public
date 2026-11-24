#!/usr/bin/env python3
"""配置表静态交叉(L1 静态自洽)。

**为什么这是投入产出比最高的一段**:纯静态、零设备、不需要模型判断,出包前就能跑。
它发现的缺陷(悬空引用、重复主键、必填空值、类型与范围错误、枚举越界、孤立行)
在黑盒里要靠精确构造运行时条件才能撞到 —— 静态检查的运行成本是零。

设计见 `references/10-upstreams.md`(阶段 0「静态交叉」)。

    输入  --config <表> × N   CSV(utf-8 / utf-8-sig)或 JSON(数组 / {"rows": [...]})
          --spec <字段说明.json>   表 → 列 → 类型/范围/必填/枚举/外键/资源目录
    输出  不一致清单(markdown + json)

**没有字段说明就不知道该查什么**,所以缺说明的列会明确记成"未检查",
而不是安静地跳过、让报告看起来"没发现问题"。

三条纪律:

1. **只做确定性检查。** 语义层的一致性(需求说 500、配置写 300)不在本工具范围内。
2. **未声明的列不猜。** 报告单列「未检查(字段说明缺失)」一节。
3. **不产出期望。** 本工具产出的是"不一致清单"(缺陷候选),
   期望仍然只能来自需求 / 配置 / 通用不变量 / 源间一致性(见 10-upstreams.md 第四节)。

纯标准库,零依赖,xlsx 请先导出成 CSV(本项目不允许第三方依赖)。
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent

TYPES = {"string", "int", "float", "bool", "enum"}
NUMERIC = {"int", "float"}
INT_RE = re.compile(r"^[+-]?\d+$")
FLOAT_RE = re.compile(r"^[+-]?(?:\d+\.?\d*|\.\d+)$")
BOOL_TRUE = {"1", "true", "yes", "y", "是", "真"}
BOOL_FALSE = {"0", "false", "no", "n", "否", "假"}


# ---------------------------------------------------------------- 读表

def load_table(path: Path) -> tuple[list[str], list[dict], list[int], str | None]:
    """读一张配置表。返回 (列名, 行, 行号, 错误)。

    行号按文件里的行计(CSV 表头算第 1 行),便于让人直接跳到那一行。
    """
    if not path.exists():
        return [], [], [], f"文件不存在: {path}"
    try:
        text = path.read_text(encoding="utf-8-sig")     # Excel 导出的 CSV 常带 BOM
    except OSError as exc:
        return [], [], [], f"读不了: {type(exc).__name__}: {exc}"
    if not text.strip():
        return [], [], [], f"文件是空的: {path}"

    if path.suffix.lower() == ".json":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            return [], [], [], f"JSON 解析失败: {exc}"
        if isinstance(data, dict):
            data = data.get("rows") or data.get("data") or []
        if not isinstance(data, list) or not data:
            return [], [], [], "JSON 必须是数组或 {\"rows\": [...]} 且非空"
        columns: list[str] = []
        for row in data:
            for k in row:
                if k not in columns:
                    columns.append(k)
        rows = [{c: ("" if row.get(c) is None else str(row.get(c))) for c in columns}
                for row in data]
        return columns, rows, list(range(1, len(rows) + 1)), None

    try:
        reader = csv.reader(text.splitlines())
        header = next(reader, None)
        if not header:
            return [], [], [], "CSV 没有表头"
        columns = [h.strip() for h in header]
        rows, numbers = [], []
        for i, raw in enumerate(reader, start=2):
            if not any(cell.strip() for cell in raw):
                continue                      # 空行跳过,但行号照实记
            row = {columns[j]: (raw[j].strip() if j < len(raw) else "")
                   for j in range(len(columns))}
            rows.append(row)
            numbers.append(i)
        return columns, rows, numbers, None
    except (csv.Error, UnicodeDecodeError) as exc:
        return [], [], [], f"CSV 解析失败: {type(exc).__name__}: {exc}"


def load_spec(path: Path) -> tuple[dict, str | None]:
    if not path.exists():
        return {}, f"字段说明不存在: {path}"
    try:
        spec = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        return {}, f"字段说明 JSON 解析失败: {exc}"
    if not isinstance(spec, dict) or not isinstance(spec.get("tables"), dict):
        return {}, "字段说明必须是 {\"tables\": {...}} 结构"
    return spec, None


# ---------------------------------------------------------------- 单值校验

def check_value(value: str, rule: dict) -> tuple[str, str] | None:
    """校验一个单元格。返回 (kind, detail) 或 None(通过)。"""
    kind = rule.get("type", "string")
    if kind not in TYPES:
        return "bad_rule", f"字段说明里的 type={kind!r} 不合法"
    if kind in NUMERIC:
        if kind == "int" and not INT_RE.match(value):
            return "bad_type", f"期望整数,实际 {value!r}"
        if kind == "float" and not FLOAT_RE.match(value):
            return "bad_type", f"期望数值,实际 {value!r}"
        num = float(value)
        lo, hi = rule.get("min"), rule.get("max")
        if lo is not None and num < lo:
            return "out_of_range", f"{num:g} < min {lo}"
        if hi is not None and num > hi:
            return "out_of_range", f"{num:g} > max {hi}"
        if kind == "int" and rule.get("integer", False) and float(value) != int(float(value)):
            return "bad_type", f"期望整数,实际 {value!r}"
    elif kind == "bool":
        if value.lower() not in BOOL_TRUE | BOOL_FALSE:
            return "bad_type", f"期望布尔(0/1/true/false/是/否),实际 {value!r}"
    elif kind == "enum":
        values = rule.get("values")
        if not isinstance(values, list) or not values:
            return "bad_rule", "enum 字段缺 values"
        if value not in [str(v) for v in values]:
            return "bad_enum", f"{value!r} 不在 {values}"
    return None


# ---------------------------------------------------------------- 检查

def check_tables(tables: dict[str, dict], spec: dict, root: Path) -> tuple[list, list, list]:
    """跑全部确定性检查。返回 (findings, gaps, notes)。"""
    findings: list[dict] = []
    gaps: list[dict] = []
    notes: list[dict] = []
    table_specs: dict = spec.get("tables") or {}

    def add(level: str, kind: str, table: str, detail: str, **extra) -> None:
        findings.append({"level": level, "kind": kind, "table": table,
                         "detail": detail, **extra})

    # ---- 字段说明覆盖
    for name in sorted(set(tables) - set(table_specs)):
        gaps.append({"kind": "unspecced_table", "table": name,
                     "note": "字段说明里没有这张表 —— 它的列全部未检查"})
    for name in sorted(set(table_specs) - set(tables)):
        notes.append({"kind": "spec_table_not_given", "table": name,
                      "note": "字段说明里有这张表,但这次没给对应的 --config"})

    # ---- 外键:先把手头的键收集齐
    key_values: dict[str, set[str]] = {}
    for name, t in tables.items():
        ts = table_specs.get(name) or {}
        kcol = ts.get("key")
        if kcol and kcol in t["columns"]:
            key_values[name] = {r.get(kcol, "").strip() for r in t["rows"]}

    # ---- 逐表逐列
    for name in sorted(tables):
        t = tables[name]
        ts = table_specs.get(name)
        if not ts:
            continue
        cols_spec = ts.get("columns") or {}
        for col in sorted(set(t["columns"]) - set(cols_spec)):
            gaps.append({"kind": "unspecced_column", "table": name, "column": col,
                         "note": f"列 {col!r} 没有字段说明 —— 未检查"})
        for col in sorted(set(cols_spec) - set(t["columns"])):
            rule = cols_spec[col] or {}
            level = "error" if rule.get("required") else "warn"
            add(level, "missing_column", name,
                f"字段说明要求的列 {col!r} 在表里不存在", column=col)

        key_col = ts.get("key")
        seen: dict[str, int] = {}
        for row, lineno in zip(t["rows"], t["numbers"]):
            for col in sorted(t["columns"]):
                rule = cols_spec.get(col)
                if not rule:
                    continue
                value = row.get(col, "")
                if value == "":
                    if rule.get("required"):
                        add("error", "required_empty", name,
                            f"必填列 {col!r} 为空", column=col, row=lineno)
                    continue
                bad = check_value(value, rule)
                if bad:
                    add("error", bad[0], name, f"{col!r}: {bad[1]}",
                        column=col, row=lineno, value=value)

                ref = rule.get("ref")
                if ref:
                    target_table, _, target_col = str(ref).partition(".")
                    if target_table not in tables:
                        gaps.append({"kind": "ref_table_missing", "table": name,
                                     "column": col,
                                     "note": f"引用 {ref},但这次没给表 {target_table} —— "
                                             f"无法检查该外键"})
                    elif target_table not in key_values:
                        gaps.append({"kind": "ref_key_missing", "table": target_table,
                                     "column": target_col,
                                     "note": f"表 {target_table} 的字段说明没声明 key "
                                             f"({target_col}) —— 无法检查引用"})
                    elif value not in key_values[target_table]:
                        add("error", "dangling_ref", name,
                            f"{col!r}={value!r} 在 {ref} 里不存在",
                            column=col, row=lineno, value=value, ref=ref)

                ref_dir = rule.get("ref_dir")
                if ref_dir:
                    exts = rule.get("ext") or [".png"]
                    base = (root / ref_dir)
                    if not base.exists():
                        gaps.append({"kind": "ref_dir_missing", "table": name,
                                     "column": col,
                                     "note": f"资源目录 {ref_dir} 不存在 —— 无法检查悬空资源"})
                    elif not any((base / f"{value}{e}").exists() for e in exts):
                        tried = ", ".join(f"{value}{e}" for e in exts)
                        add("error", "dangling_asset", name,
                            f"{col!r}={value!r} 在 {ref_dir} 下找不到({tried})",
                            column=col, row=lineno, value=value, ref_dir=ref_dir)

            # 条件必填:列 A 非空时列 B 必须填。
            # 典型场景:"含额外掉落的链必须选一种满盘策略"(需求 §6.3 禁止默认静默少发)
            # —— 普通行不填不算错,含掉落的行不填就是错,用 required 表达不了。
            for c2, r2 in sorted(cols_spec.items()):
                cond = (r2 or {}).get("required_if")
                if not cond:
                    continue
                ctl = cond.get("column") if isinstance(cond, dict) else str(cond)
                if ctl not in t["columns"]:
                    gaps.append({"kind": "required_if_unknown_column",
                                 "table": name, "column": c2,
                                 "note": f"required_if 依赖的列 {ctl!r} 不在表里"
                                         f" —— 这条规则没法检查"})
                elif (row.get(ctl) or "").strip() and not (row.get(c2) or "").strip():
                    add("error", "required_if_empty", name,
                        f"{ctl} 非空时 {c2!r} 必填(现在为空)", column=c2,
                        row=lineno, depends_on=ctl)

            if key_col:
                kv = row.get(key_col, "")
                if kv:
                    if kv in seen:
                        add("error", "duplicate_key", name,
                            f"主键 {key_col}={kv!r} 重复(第 {seen[kv]} 行已出现)",
                            column=key_col, row=lineno, value=kv)
                    else:
                        seen[kv] = lineno

        # ---- 孤立行:声明了 referenced_by 才查
        for ref in ts.get("referenced_by") or []:
            rtab, _, rcol = str(ref).partition(".")
            if rtab not in tables or rcol not in tables[rtab]["columns"]:
                gaps.append({"kind": "referenced_by_missing", "table": name,
                             "note": f"referenced_by={ref} 的表/列不存在 —— 无法查孤立行"})
                continue
            used = {r.get(rcol, "").strip() for r in tables[rtab]["rows"]}
            for row, lineno in zip(t["rows"], t["numbers"]):
                kv = row.get(key_col, "") if key_col else ""
                if kv and kv not in used:
                    add("warn", "orphan_row", name,
                        f"{key_col}={kv!r} 没有被 {ref} 引用(废弃配置或漏接,需人判断)",
                        column=key_col, row=lineno, value=kv, referenced_by=ref)

    # ---- 源可信度优先级:冲突裁决只能由人定
    if not spec.get("source_precedence"):
        notes.append({"kind": "no_source_precedence",
                      "note": "字段说明里没有 source_precedence —— 源冲突(需求 vs 配置 vs 代码)"
                              "无法裁决,见 10-upstreams.md 第八节。建议人确认后写进去"})
    return findings, gaps, notes


# ---------------------------------------------------------------- 模板生成

def build_template(tables: dict[str, dict]) -> dict:
    """从表里推断一份起步用的字段说明。

    推断的是**形态**(类型/是否为空/是否唯一/枚举候选),不是业务规则 ——
    范围与外键必须人补。目的是把"写字段说明"的成本降到几分钟。
    """
    out: dict = {"tables": {}, "_comment": [
        "由 check_config.py --dump-spec-template 生成。",
        "推断的只有形态:类型/必填/唯一/枚举候选。",
        "**min/max/ref/ref_dir 必须人补** —— 工具不知道业务约束,也不会猜。",
        "source_precedence 也要人确认(见 references/10-upstreams.md 第八节)。"]}
    for name, t in sorted(tables.items()):
        cols: dict[str, dict] = {}
        nonempty: dict[str, list[str]] = {}
        for row in t["rows"]:
            for col, v in row.items():
                if v != "":
                    nonempty.setdefault(col, []).append(v)
        for col in t["columns"]:
            vals = nonempty.get(col, [])
            rule: dict = {"type": "string", "required": len(vals) == len(t["rows"])
                          and len(t["rows"]) > 0}
            # 类型推断:全数值 -> 数值;绝大多数是数值也判数值(剩下的脏值正好让
            # 检查去报 bad_type —— 那才是我们要的)。只有成不了数值的列才看枚举。
            if vals and all(INT_RE.match(v) for v in vals):
                rule["type"] = "int"
            elif vals and all(FLOAT_RE.match(v) for v in vals):
                rule["type"] = "float"
            elif vals and sum(1 for v in vals if INT_RE.match(v)) / len(vals) >= 0.8:
                rule["type"] = "int"
            elif vals and all(v.lower() in BOOL_TRUE | BOOL_FALSE for v in vals):
                rule["type"] = "bool"
            else:
                distinct = sorted(set(vals))
                # 枚举要有"重复出现"这个特征:每行都不同的列是标识符,不是枚举。
                # 名字像 id 的列同样直接判标识符 —— 重复 id 是缺陷,不该被当成枚举候选。
                looks_like_id = col == "id" or col.endswith("_id")
                if (vals and not looks_like_id
                        and 1 < len(distinct) < len(t["rows"])
                        and len(distinct) / len(t["rows"]) <= 0.6
                        and len(distinct) <= 12 and all(len(v) <= 24 for v in distinct)):
                    rule["type"] = "enum"
                    rule["values"] = distinct
            if vals and len(set(vals)) == len(vals) and len(vals) == len(t["rows"]):
                rule["unique"] = True
            cols[col] = rule
        out["tables"][name] = {
            "path": t["path"], "key": "", "columns": cols,
            "_key_hint": "选出主键列填到 key(通常 id);唯一且必填的列是候选",
            "_referenced_by_hint": "若这张表被别的表引用,填 referenced_by 才能查孤立行",
        }
    return out


# ---------------------------------------------------------------- 报告

def render_report(spec_path: Path, tables: dict, findings: list, gaps: list,
                  notes: list) -> str:
    now = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    errors = [f for f in findings if f["level"] == "error"]
    warns = [f for f in findings if f["level"] == "warn"]
    lines = [
        "# 配置表静态交叉(L1)",
        "",
        f"- 生成时间: {now}",
        f"- 字段说明: `{spec_path}`",
        f"- 检查的表: " + ", ".join(
            f"`{n}`({len(t['rows'])} 行)" for n, t in sorted(tables.items())),
        f"- 不一致: **错误 {len(errors)}**、提示 {len(warns)};未检查项 {len(gaps)}",
        "",
        "> 这是**缺陷候选清单**,不是判定:每一条都指向具体的表、行、列与值,可逐条复核。",
        "> 静态交叉只判「自洽」,判不了「是否符合需求」—— 那需要需求 × 配置的语义比对。",
        "",
    ]

    def render(items: list, title: str) -> None:
        lines.append(f"## {title}")
        if not items:
            lines.append("- 无")
            lines.append("")
            return
        for f in items:
            loc = f"`{f['table']}`"
            if f.get("row"):
                loc += f" 第 {f['row']} 行"
            if f.get("column"):
                loc += f" / 列 `{f['column']}`"
            lines.append(f"- **[{f['kind']}]** {loc} — {f['detail']}")
        lines.append("")

    render(errors, "不一致(确定性错误)")
    render(warns, "提示(需人判断)")

    lines.append("## 未检查(字段说明缺失 —— 不是「没问题」)")
    if not gaps:
        lines.append("- 无")
    else:
        for g in gaps:
            where = f"`{g.get('table')}`" + (f" / `{g['column']}`" if g.get("column") else "")
            lines.append(f"- [{g['kind']}] {where} — {g['note']}")
    lines.append("")
    if notes:
        lines.append("## 备注")
        for n in notes:
            where = f"`{n.get('table')}` — " if n.get("table") else ""
            lines.append(f"- {where}{n['note']}")
        lines.append("")
    lines += [
        "## 本工具判不了的",
        "",
        "- **需求 × 配置**:需求要求的功能没配 / 数值不符 → 需要语义比对(AI + 人复核)",
        "- **配置 × 代码**:硬编码覆盖配置、读错字段名、死配置 → L2,需源码",
        "- **配置 × 运行时**:配置未生效 → L3,需真机 + 状态上报",
        "",
    ]
    return "\n".join(lines)


def exit_code(findings: list, gaps: list) -> int:
    if any(f["level"] == "error" for f in findings):
        return 1
    if gaps:
        return 3
    return 0


# ---------------------------------------------------------------- main

def run_check(config_paths: list[Path], spec_path: Path, out: Path | None,
              json_out: Path | None, root: Path | None, quiet: bool) -> tuple[int, dict]:
    spec, err = load_spec(spec_path)
    if err:
        print(f"错误: {err}", file=sys.stderr)
        return 2, {}

    tables: dict[str, dict] = {}
    problems: list[str] = []
    table_specs = spec.get("tables") or {}
    # 表名:优先用字段说明里的名字(把 --config 路径对上),否则用文件名
    by_path = {str((root or Path.cwd()) / (ts.get("path") or "")): name
               for name, ts in table_specs.items()}
    for p in config_paths:
        name = by_path.get(str(p)) or p.stem
        if name in tables:
            problems.append(f"表名 {name!r} 撞了(两个 --config 指向同一个表名)")
            continue
        columns, rows, numbers, err = load_table(p)
        if err:
            problems.append(f"{p}: {err}")
            continue
        tables[name] = {"columns": columns, "rows": rows, "numbers": numbers,
                        "path": str(p)}
    if problems:
        for p in problems:
            print(f"错误: {p}", file=sys.stderr)
        return 2, {}

    findings, gaps, notes = check_tables(tables, spec, (root or Path.cwd()))
    report = render_report(spec_path, tables, findings, gaps, notes)
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(report, encoding="utf-8")
    code = exit_code(findings, gaps)
    summary = {"tables": {n: len(t["rows"]) for n, t in tables.items()},
               "errors": sum(1 for f in findings if f["level"] == "error"),
               "warns": sum(1 for f in findings if f["level"] == "warn"),
               "gaps": len(gaps), "notes": len(notes),
               "findings": findings, "unchecked": gaps, "noteList": notes}
    if json_out:
        json_out.parent.mkdir(parents=True, exist_ok=True)
        json_out.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
    if not quiet:
        sizes = ", ".join(f"{n}×{len(t['rows'])}" for n, t in sorted(tables.items()))
        print(f"配置静态交叉: 表 {len(tables)} 张({sizes})")
        for f in findings:
            loc = f"{f['table']}" + (f":{f['row']}" if f.get("row") else "")
            print(f"  [{f['level']:5s}] {f['kind']:16s} {loc} — {f['detail']}")
        if gaps:
            print(f"未检查 {len(gaps)} 项(字段说明缺失,不等于没问题):")
            for g in gaps[:8]:
                print(f"  ? {g.get('table')}"
                      f"{'/' + g['column'] if g.get('column') else ''} — {g['note']}")
        if out:
            print(f"报告已写出: {out}")
    return code, summary


def selftest() -> int:
    import tempfile

    fails: list[str] = []

    def ck(name: str, cond: bool, detail: str = "") -> None:
        if cond:
            print(f"  ok   {name}")
        else:
            print(f"  FAIL {name}" + (f" — {detail}" if detail else ""))
            fails.append(name)

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "configs").mkdir()
        (root / "assets" / "icons").mkdir(parents=True)
        (root / "assets" / "icons" / "icon_sword.png").write_bytes(b"\x89PNG")

        # 道具表:故意埋 5 类问题
        (root / "configs" / "item.csv").write_text(
            "id,name,icon,quality,kind,price\n"
            "it_sword,铁剑,icon_sword,3,weapon,100\n"
            "it_shield,木盾,icon_missing,3,armor,200\n"      # 悬空资源 + 枚举越界(quality 无表,看 kind)
            "it_potion,药水,icon_sword,9,consumable,50\n"     # 超范围 quality>5
            "it_sword,重复ID,icon_sword,1,weapon,10\n"        # 主键重复
            "it_empty,,icon_sword,2,weapon,\n"                # 必填为空(name)+ 必填为空? price 非必填
            "it_bad,坏价格,icon_sword,x,weapon,abc\n"          # 类型错(quality 非数字)+ 类型错(price)
            "it_ring,戒指,icon_sword,2,ring,30\n",              # 枚举越界(kind=ring 不在词汇表)
            encoding="utf-8-sig")
        (root / "configs" / "shop.csv").write_text(
            "id,item_id,count\n"
            "sh_1,it_sword,1\n"
            "sh_2,it_nope,1\n",                               # 悬空引用
            encoding="utf-8")

        spec = {
            "source_precedence": ["requirement", "config", "code"],
            "tables": {
                "item": {
                    "path": "configs/item.csv", "key": "id",
                    "referenced_by": ["shop.item_id"],
                    "columns": {
                        "id": {"type": "string", "required": True, "unique": True},
                        "name": {"type": "string", "required": True},
                        "icon": {"type": "string", "required": True,
                                 "ref_dir": "assets/icons", "ext": [".png"]},
                        "quality": {"type": "int", "min": 1, "max": 5},
                        "kind": {"type": "enum",
                                 "values": ["weapon", "armor", "consumable"]},
                        "price": {"type": "int", "min": 0},
                    },
                },
                "shop": {
                    "path": "configs/shop.csv", "key": "id",
                    "columns": {
                        "id": {"type": "string", "required": True, "unique": True},
                        "item_id": {"type": "string", "required": True, "ref": "item.id"},
                        "count": {"type": "int", "min": 1},
                    },
                },
            },
        }
        (root / "spec.json").write_text(json.dumps(spec, ensure_ascii=False),
                                        encoding="utf-8")

        code, summary = run_check([root / "configs" / "item.csv",
                                   root / "configs" / "shop.csv"],
                                  root / "spec.json", root / "report.md",
                                  root / "report.json", root, quiet=True)
        kinds = sorted({f["kind"] for f in summary["findings"]})
        ck("退出码: 有确定性错误 -> 1", code == 1, str(code))
        for expect, label in [
            ("duplicate_key", "主键重复"),
            ("required_empty", "必填为空(含 BOM 文件的表头解析)"),
            ("bad_type", "类型错误"),
            ("out_of_range", "超出范围"),
            ("bad_enum", "枚举越界"),
            ("dangling_ref", "表间悬空引用"),
            ("dangling_asset", "资源目录悬空引用"),
        ]:
            ck(f"抓到: {label}", expect in kinds, str(kinds))
        ck("报告写出且注明未检查项", (root / "report.md").exists()
           and "未检查" in (root / "report.md").read_text(encoding="utf-8"))
        ck("JSON 结果含 findings 与 unchecked",
           "findings" in summary and "unchecked" in summary)

        # 字段说明缺失 -> 走"未检查",不是"没问题"
        (root / "configs" / "clean2.csv").write_text("id,n\nit_a,1\nit_b,2\n",
                                                    encoding="utf-8")
        thin = {"source_precedence": ["requirement", "config", "code"],
                "tables": {"clean2": {"path": "configs/clean2.csv", "key": "id",
                                      "columns": {"id": {"type": "string"}}}}}
        (root / "thin.json").write_text(json.dumps(thin, ensure_ascii=False),
                                       encoding="utf-8")
        code2, sum2 = run_check([root / "configs" / "clean2.csv"], root / "thin.json",
                                None, None, root, quiet=True)
        ck("字段说明不全 -> 未检查列进 gaps 且退出码 3(不是「没问题」)",
           code2 == 3 and any(g["kind"] == "unspecced_column" for g in sum2["unchecked"]),
           f"code={code2} gaps={[g['kind'] for g in sum2['unchecked']]}")

        # 干净表 + 完整说明 -> 0
        (root / "configs" / "clean.csv").write_text(
            "id,n\nit_a,1\nit_b,2\n", encoding="utf-8")
        clean_spec = {"source_precedence": ["requirement", "config"],
                      "tables": {"clean": {"path": "configs/clean.csv", "key": "id",
                                           "columns": {
                                               "id": {"type": "string", "required": True,
                                                      "unique": True},
                                               "n": {"type": "int", "min": 0}}}}}
        (root / "clean.json").write_text(json.dumps(clean_spec, ensure_ascii=False),
                                         encoding="utf-8")
        code3, sum3 = run_check([root / "configs" / "clean.csv"], root / "clean.json",
                                None, None, root, quiet=True)
        ck("干净表 -> 退出码 0", code3 == 0 and not sum3["findings"],
           f"code={code3} findings={sum3['findings']}")

        # 条件必填(required_if):含额外掉落的链必须选一种满盘策略
        (root / "configs" / "chain.csv").write_text(
            "species,extra,drops,strategy\n"
            "s_a,,,,\n"                                  # 没有掉落 -> 不要求策略
            "s_b,,d_1,,\n"                               # 有掉落、没策略 -> 报
            "s_c,,d_2,reward_queue\n"                    # 有掉落、有策略 -> 干净
            "s_d,,d_3,,\n",                              # 又一条报
            encoding="utf-8")
        cond_spec = {"tables": {"chain": {
            "path": "configs/chain.csv", "key": "species",
            "columns": {"species": {"type": "string", "required": True},
                        "drops": {"type": "string"},
                        "strategy": {"type": "enum",
                                     "values": ["reject_if_no_room", "reward_queue"],
                                     "required_if": "drops"}}}}}
        (root / "cond.json").write_text(json.dumps(cond_spec, ensure_ascii=False),
                                        encoding="utf-8")
        code5, sum5 = run_check([root / "configs" / "chain.csv"], root / "cond.json",
                                None, None, root, quiet=True)
        hits = [f for f in sum5["findings"] if f["kind"] == "required_if_empty"]
        ck("条件必填: 有掉落没策略的行被点名(普通行不报)",
           code5 == 1 and len(hits) == 2, f"code={code5} {hits}")
        ck("条件必填: 报出来的行号指到具体那一行",
           {h.get("row") for h in hits} == {3, 5}, str([h.get("row") for h in hits]))
        bad_spec = {"tables": {"chain": {
            "path": "configs/chain.csv", "key": "species",
            "columns": {"strategy": {"type": "enum",
                                     "values": ["reward_queue"],
                                     "required_if": "nope"}}}}}
        (root / "cond2.json").write_text(json.dumps(bad_spec, ensure_ascii=False),
                                         encoding="utf-8")
        code6, sum6 = run_check([root / "configs" / "chain.csv"], root / "cond2.json",
                                None, None, root, quiet=True)
        ck("条件必填: 依赖的列不存在 -> 记成「这条规则没法检查」而不是静默通过",
           any(g["kind"] == "required_if_unknown_column" for g in sum6["unchecked"]),
           str(sum6["unchecked"]))

        # 孤立行:声明 referenced_by 才查
        (root / "configs" / "shop2.csv").write_text("id,item_id,count\nsh_1,it_a,1\n",
                                                    encoding="utf-8")
        code4, sum4 = run_check([root / "configs" / "clean.csv",
                                 root / "configs" / "shop2.csv"],
                                root / "clean.json", None, None, root, quiet=True)
        ck("孤立行只在有 referenced_by 时提示(本次未声明 -> 无提示)",
           not any(f["kind"] == "orphan_row" for f in sum4["findings"]), str(sum4))

        # 模板生成
        columns, rows, numbers, err = load_table(root / "configs" / "item.csv")
        tpl = build_template({"item": {"columns": columns, "rows": rows,
                                       "numbers": numbers,
                                       "path": "configs/item.csv"}})
        cols = tpl["tables"]["item"]["columns"]
        ck("模板: id 判标识符(不是枚举)、price 判 int、kind 判 enum 候选",
           cols["id"]["type"] == "string" and cols["price"]["type"] == "int"
           and cols["kind"]["type"] == "enum", str(cols))
        ck("模板: 有重复的 id 不标 unique(重复本身留给检查去报)",
           "unique" not in cols["id"], str(cols["id"]))
        ck("模板: 全不重复的列标 unique",
           build_template({"c": {"columns": ["id"], "rows": [{"id": "a"}, {"id": "b"}],
                                 "numbers": [2, 3], "path": "x.csv"}})
           ["tables"]["c"]["columns"]["id"].get("unique") is True)
        ck("模板: 脏值列按多数值判类型(quality 里的 x 交给检查去报)",
           cols["quality"]["type"] == "int", str(cols["quality"]))
        ck("模板: 明确写了 min/max/ref 要人补",
           any("min/max/ref" in c for c in tpl["_comment"]), str(tpl["_comment"]))
        ck("模板: required 按是否出现过空值推断",
           cols["name"].get("required") is False, str(cols["name"]))

        # JSON 表也能读
        (root / "configs" / "j.json").write_text(
            json.dumps([{"id": "a", "v": "1"}, {"id": "b", "v": "2"}]),
            encoding="utf-8")
        jcols, jrows, _, jerr = load_table(root / "configs" / "j.json")
        ck("JSON 表可读", jerr is None and jcols == ["id", "v"] and len(jrows) == 2,
           f"{jerr} {jcols}")

    print()
    if fails:
        print(f"自测失败 {len(fails)} 项:")
        for x in fails:
            print("  ✗", x)
        return 1
    print("自测通过(悬空引用/资源/重复主键/必填空/类型/范围/枚举/孤立行;"
          "字段说明缺失走「未检查」;BOM 与 JSON 表;模板生成;退出码 0/1/2/3)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="配置表静态交叉(L1):悬空引用/重复主键/必填空/类型范围/枚举越界/"
                    "孤立行 —— 确定性检查,零设备")
    ap.add_argument("--config", type=Path, action="append", default=[],
                    help="配置表(CSV/JSON);可重复给多张")
    ap.add_argument("--spec", type=Path, help="字段说明 json(表→列→类型/范围/外键)")
    ap.add_argument("--root", type=Path, default=Path.cwd(),
                    help="相对路径的基准目录(默认当前目录)")
    ap.add_argument("--out", type=Path, help="不一致清单 markdown 输出路径")
    ap.add_argument("--json", type=Path, help="机器可读结果输出路径")
    ap.add_argument("--dump-spec-template", type=Path,
                    help="从给定表生成起步用的字段说明(推断形态,范围/外键要人补)")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    if args.dump_spec_template:
        if not args.config:
            print("错误: --dump-spec-template 需要 --config", file=sys.stderr)
            return 2
        tables = {}
        for p in args.config:
            columns, rows, numbers, err = load_table(p)
            if err:
                print(f"错误: {err}", file=sys.stderr)
                return 2
            tables[p.stem] = {"columns": columns, "rows": rows,
                              "numbers": numbers, "path": str(p)}
        args.dump_spec_template.parent.mkdir(parents=True, exist_ok=True)
        args.dump_spec_template.write_text(
            json.dumps(build_template(tables), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
        print(f"字段说明模板已写出: {args.dump_spec_template}"
              f"(含 {len(tables)} 张表;min/max/ref 需要人工补)")
        return 0

    if not args.config or not args.spec:
        print("错误: 需要 --config 与 --spec(或用 --selftest / --dump-spec-template)",
              file=sys.stderr)
        return 2
    code, _ = run_check(args.config, args.spec, args.out or (args.root / "config-check.md"),
                        args.json, args.root, args.quiet)
    return code


if __name__ == "__main__":
    sys.exit(main())
