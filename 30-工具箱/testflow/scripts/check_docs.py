#!/usr/bin/env python3
"""文档-实现对账。

**为什么存在**:这个项目里出现过的错误全部属于同一类 —— "写了但没验证":
声称的算子集合与实际不符、文档描述的代码行为与实际不符、声明式字段没人消费。
验证动作缺失,而不是设计错误。

所以这个脚本把"文档里说的"和"代码里做的"做机械对账。它检查的是可机械检查的
那一部分,不能替代人工判断,但能挡住最常见的那类漂移。

检查项:
  1. SKILL.md / references 里提到的 scripts/*.py 是否都存在
  2. references 目录里的每份文档是否都被 SKILL.md 引用(避免孤儿文档)
  3. derived-schema.json 的枚举 与 validate_cases.py 的常量是否一致
     (op / action / fault kind / expect_source / judge / kind / type / priority / review state)
  4. 4-conversion.md 的 op 表 与代码的 OPS 是否一致
  5. 各文档里的 CLI 示例引用的脚本是否存在
  6. 6-triage.md 的「结果状态」表(verdict 词汇表 + 谁会开 bug)与代码常量是否一致

退出码:0 = 对账通过;1 = 发现不一致;2 = 用法/路径错误。
纯标准库,零依赖。
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import validate_cases as VC  # noqa: E402

SKILL_DIR = Path(__file__).resolve().parent.parent


class Findings:
    def __init__(self) -> None:
        self.problems: list[str] = []
        self.checked: list[str] = []

    def bad(self, msg: str) -> None:
        self.problems.append(msg)

    def ok(self, msg: str) -> None:
        self.checked.append(msg)


def read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def all_docs() -> list[Path]:
    docs = [SKILL_DIR / "SKILL.md", SKILL_DIR / "HANDOFF.md"]
    docs += sorted((SKILL_DIR / "references").glob("*.md"))
    docs += sorted((SKILL_DIR / "assets").glob("*.md"))
    return [d for d in docs if d.exists()]


# ---------------------------------------------------------------- 1 & 2

def check_dimensions_sync(f: Findings) -> None:
    """维度表:CSV 是可编辑的源,JSON 是派生产物 —— 两者必须一致。

    "同一个事实有两个读者时,判据只能有一处。" 维度表正是这种情形:
    人改 CSV、别的技能读 JSON;如果改完忘了重导,两边就会给出两套维度,
    而且不会报错、只会某一天给出两个答案。
    """
    csv_path = SKILL_DIR / "assets" / "check-dimensions.csv"
    json_path = SKILL_DIR / "assets" / "check-dimensions.json"
    if not csv_path.exists() or not json_path.exists():
        f.bad("维度表: 缺 check-dimensions.csv 或 .json(CSV 是源,JSON 由 "
              "--dump-dimensions-json 生成)")
        return
    try:
        rows = list(csv.reader(csv_path.read_text(encoding="utf-8-sig").splitlines()))
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        f.bad(f"维度表: 读不出来({exc})")
        return
    header = [c.strip().lstrip("﻿") for c in rows[0]]
    items = []
    for raw in rows[1:]:
        if not any(x.strip() for x in raw) or not raw[0].strip():
            continue
        d = {c: (raw[i].strip() if i < len(raw) else "") for i, c in enumerate(header)}
        item = {"id": d.get("id", ""), "name": d.get("名称", ""),
                "ask": d.get("标准问法", "")}
        if d.get("触发条件"):
            item["when"] = [w.strip() for w in
                            re.split(r"[、,;；|/]", d["触发条件"]) if w.strip()]
        if d.get("示例"):
            item["example"] = d["示例"]
        if d.get("全局", "").strip() in ("是", "true", "1", "yes", "Y"):
            item["always"] = True
        if d.get("标签"):
            item["tags"] = [t.strip() for t in
                            re.split(r"[、,;；]", d["标签"]) if t.strip()]
        if d.get("备注"):
            item["note"] = d["备注"]
        items.append(item)
    if items == (data.get("dimensions") or []):
        f.ok(f"维度表: CSV 与派生的 JSON 一致({len(items)} 个维度)")
    else:
        f.bad(f"维度表: CSV 与 JSON 不一致(CSV {len(items)} 条 / "
              f"JSON {len(data.get('dimensions') or [])} 条)—— "
              f"改完 CSV 跑一次: python3 scripts/concerns.py --dump-dimensions-json")


def check_script_mentions(f: Findings) -> None:
    existing = {p.name for p in (SKILL_DIR / "scripts").glob("*.py")}
    mentioned: set[str] = set()
    for doc in all_docs():
        for m in re.finditer(r"scripts/([A-Za-z0-9_]+\.py)", read(doc)):
            mentioned.add(m.group(1))

    for name in sorted(mentioned):
        if name in existing:
            f.ok(f"脚本引用存在: {name}")
        else:
            f.bad(f"{name} 在文档里被引用,但 scripts/ 下不存在")
    # 反向:有脚本但从未被文档提到
    for name in sorted(existing - mentioned):
        f.bad(f"scripts/{name} 存在,但没有任何文档提到它(可能是孤儿脚本)")


def check_reference_orphans(f: Findings) -> None:
    skill = read(SKILL_DIR / "SKILL.md")
    refs = sorted((SKILL_DIR / "references").glob("*.md"))
    for r in refs:
        if r.name in skill:
            f.ok(f"reference 已被 SKILL.md 引用: {r.name}")
        else:
            f.bad(f"references/{r.name} 没有被 SKILL.md 引用 —— 孤儿文档,"
                  f"读不到就等于不存在")


# ---------------------------------------------------------------- 3

def check_schema_vs_code(f: Findings) -> None:
    schema_path = SKILL_DIR / "assets" / "derived-schema.json"
    try:
        schema = json.loads(read(schema_path))
    except json.JSONDecodeError as exc:
        f.bad(f"{schema_path.name} JSON 解析失败: {exc}")
        return

    def enum_of(node: dict, key: str) -> set[str] | None:
        n = (node or {}).get(key)
        if not isinstance(n, dict):
            return None
        if isinstance(n.get("enum"), list):
            return set(n["enum"])
        # evidence 这类写法: {"type":"array","items":{"enum":[...]}}
        items = n.get("items")
        if isinstance(items, dict) and isinstance(items.get("enum"), list):
            return set(items["enum"])
        return None

    props = schema.get("properties", {})
    defs = schema.get("definitions", {})
    action = defs.get("action", {}).get("properties", {})
    check = defs.get("check", {}).get("properties", {})

    pairs: list[tuple[str, set[str] | None, set[str]]] = [
        ("op", enum_of(check, "op"), set(VC.OPS)),
        ("action", enum_of(action, "action"), set(VC.ACTIONS)),
        ("fault.kind", enum_of(action, "kind"), set(VC.FAULT_KINDS)),
        ("expect_source", enum_of(props, "expect_source"), set(VC.EXPECT_SOURCES)),
        ("judge", enum_of(props, "judge"), set(VC.JUDGE_MODES)),
        ("check.kind", enum_of(check, "kind"), set(VC.CHECK_KINDS)),
        ("type", enum_of(props, "type"), set(VC.CASE_TYPES)),
        ("priority", enum_of(props, "priority"), set(VC.PRIORITIES)),
        ("review.state", enum_of(props.get("review", {}).get("properties", {}),
                                "state"), set(VC.REVIEW_STATES)),
        ("evidence", enum_of(props, "evidence"), set(VC.EVIDENCE_TYPES)),
    ]

    for name, from_schema, from_code in pairs:
        if from_schema is None:
            f.bad(f"schema 里找不到 {name} 的枚举 —— 对账无法进行")
            continue
        if from_schema == from_code:
            f.ok(f"{name}: schema 与代码一致({len(from_code)} 项)")
            continue
        only_schema = sorted(from_schema - from_code)
        only_code = sorted(from_code - from_schema)
        detail = []
        if only_schema:
            detail.append(f"只在 schema 里: {only_schema}")
        if only_code:
            detail.append(f"只在代码里: {only_code}")
        f.bad(f"{name} 枚举不一致 —— " + ";".join(detail))

    # 必填字段 / 依赖字段的对账
    req = set(schema.get("required") or [])
    code_req = set(VC.REQUIRED_CASE_FIELDS)
    if req == code_req:
        f.ok(f"必填字段: schema 与代码一致({len(req)} 项)")
    else:
        f.bad("必填字段不一致 —— "
              f"只在 schema: {sorted(req - code_req)};只在代码: {sorted(code_req - req)}")


# ---------------------------------------------------------------- 4

def id_table_rows(text: str, header_cells: list[str]) -> list[list[str]]:
    """取某张 markdown 表的全部数据行(每行是各格的原始文本)。

    header_cells 必须给**整行表头**而不只是首格 —— 因为同一份文档里
    可能有多张表首格都叫 "kind"(check.kind 与 fault.kind),只匹配首格会张冠李戴。
    """
    lines = text.splitlines()
    start = None
    for i, ln in enumerate(lines):
        cells = [c.strip().strip("` ") for c in ln.strip().strip("|").split("|")]
        if cells == header_cells:
            start = i + 2          # 跳过表头与分隔行
            break
    if start is None:
        return []
    rows: list[list[str]] = []
    for ln in lines[start:]:
        if not ln.strip().startswith("|"):
            break
        rows.append([c.strip() for c in ln.strip().strip("|").split("|")])
    return rows


def parse_md_table_first_col(text: str, header_cells: list[str]) -> set[str]:
    """取 markdown 表格第一列里的所有反引号标识符。定位规则见 id_table_rows。

    注意:这里**不要求结束反引号紧跟在标识符后面** —— 单元格里的内容可能带占位符
    (如 ``REQ-<需求号>-C<序>``),要求"标识符后面立刻是反引号"会让整格一个都取不到,
    而失败方式是静默的:对账照着空集合比,永远相等。
    """
    out: set[str] = set()
    for cells in id_table_rows(text, header_cells):
        for tok in re.findall(r"`([A-Za-z_][A-Za-z0-9_./-]*)", cells[0]):
            out.add(tok)
    return out


def check_md_tables(f: Findings) -> None:
    conv = SKILL_DIR / "references" / "4-conversion.md"
    if not conv.exists():
        f.bad("references/4-conversion.md 不存在 —— 动作/算子词汇表的权威文档缺失")
        return
    text = read(conv)

    ops_doc = parse_md_table_first_col(text, ["op", "含义", "value"])
    if not ops_doc:
        f.bad("4-conversion.md 里找不到 op 表")
    elif ops_doc == set(VC.OPS):
        f.ok(f"op 表: 文档与代码一致({len(ops_doc)} 项)")
    else:
        f.bad(f"op 表不一致 —— 只在文档: {sorted(ops_doc - set(VC.OPS))};"
              f"只在代码: {sorted(set(VC.OPS) - ops_doc)}")

    # 动作词汇表以 JSON 示例形式出现,这里抽 `"action": "xxx"`
    acts_doc = set(re.findall(r'"action"\s*:\s*"([a-z_]+)"', text))
    if not acts_doc:
        f.bad("4-conversion.md 里找不到任何 action 示例")
    else:
        missing = sorted(acts_doc - set(VC.ACTIONS))
        if missing:
            f.bad(f"4-conversion.md 使用了不在动作词汇表里的 action: {missing}")
        else:
            f.ok(f"动作示例: {len(acts_doc)} 个都在代码词汇表内")

    kinds_doc = parse_md_table_first_col(text, ["kind", "判定者", "用途"])
    if not kinds_doc:
        f.bad("4-conversion.md 里找不到 check.kind 表")
    elif kinds_doc == set(VC.CHECK_KINDS):
        f.ok(f"check.kind 表: 文档与代码一致({len(kinds_doc)} 项)")
    else:
        f.bad(f"check.kind 表不一致 —— 只在文档: "
              f"{sorted(kinds_doc - set(VC.CHECK_KINDS))};"
              f"只在代码: {sorted(set(VC.CHECK_KINDS) - kinds_doc)}")

    faults_doc = parse_md_table_first_col(text, ["kind", "制造的场景", "客户端能否独立注入"])
    if not faults_doc:
        f.bad("4-conversion.md 里找不到 fault kind 表")
    elif faults_doc == set(VC.FAULT_KINDS):
        f.ok(f"fault kind 表: 文档与代码一致({len(faults_doc)} 项)")
    else:
        f.bad(f"fault kind 表不一致 —— 只在文档: "
              f"{sorted(faults_doc - set(VC.FAULT_KINDS))};"
              f"只在代码: {sorted(set(VC.FAULT_KINDS) - faults_doc)}")


# ---------------------------------------------------------------- 5

def check_cli_examples(f: Findings) -> None:
    pat = re.compile(r'SKILL_DIR/"([^"]+)"|SKILL_DIR/([A-Za-z0-9_./-]+)')
    skill = read(SKILL_DIR / "SKILL.md")
    seen = 0
    for m in pat.finditer(skill):
        rel = m.group(1) or m.group(2)
        if not rel:
            continue
        seen += 1
        if (SKILL_DIR / rel).exists():
            f.ok(f"CLI 示例路径存在: {rel}")
        else:
            f.bad(f"SKILL.md 的 CLI 示例引用了不存在的路径: {rel}")
    if seen == 0:
        f.bad("SKILL.md 里没解析到任何 CLI 示例 —— 对账未生效,检查正则")


# ---------------------------------------------------------------- 6

VERDICT_TABLE_HEADER = ["状态", "含义", "计入通过率", "开 bug"]
OPEN_TRUE = {"是", "开", "yes", "y", "true"}
OPEN_FALSE = {"否", "不", "no", "n", "false"}


def check_verdict_table(f: Findings) -> None:
    """判定结果词汇表对账。

    判定轮(judge.py)会往 run 结果里写 verdict。如果文档里的状态表与代码常量
    不一致,使用者就会按文档去解释一个代码里不存在的状态 —— 这正是本项目
    反复出现的那类"写了但没对上"的错误。
    """
    triage = SKILL_DIR / "references" / "6-triage.md"
    if not triage.exists():
        f.bad("references/6-triage.md 不存在 —— 判定结果的权威文档缺失")
        return
    text = read(triage)

    rows: dict[str, str] = {}
    lines = text.splitlines()
    start = None
    for i, ln in enumerate(lines):
        cells = [c.strip().strip("` ") for c in ln.strip().strip("|").split("|")]
        if cells == VERDICT_TABLE_HEADER:
            start = i + 2
            break
    if start is None:
        f.bad("6-triage.md 里找不到「结果状态」表(表头应为 "
              f"{VERDICT_TABLE_HEADER})—— verdict 词汇表无法对账")
        return
    for ln in lines[start:]:
        if not ln.strip().startswith("|"):
            break
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        tok = re.findall(r"`([A-Za-z_][A-Za-z0-9_]*)`", cells[0])
        if not tok:
            continue
        rows[tok[0]] = cells[3] if len(cells) > 3 else ""

    doc_verdicts = set(rows)
    if doc_verdicts == VC.VERDICTS:
        f.ok(f"verdict 词汇表: 文档与代码一致({len(doc_verdicts)} 项)")
    else:
        f.bad("verdict 词汇表不一致 —— "
              f"只在 6-triage.md: {sorted(doc_verdicts - VC.VERDICTS)};"
              f"只在代码: {sorted(VC.VERDICTS - doc_verdicts)}")

    doc_bugging = {k for k, cell in rows.items()
                   if cell.strip().strip("*").strip() in OPEN_TRUE}
    unknown = {k: rows[k] for k in rows
               if rows[k].strip().strip("*").strip() not in OPEN_TRUE | OPEN_FALSE}
    if unknown:
        f.bad(f"6-triage.md 结果状态表的「开 bug」列无法解析: {unknown}")
    elif doc_bugging == VC.BUG_OPENING_VERDICTS:
        f.ok(f"开 bug 的判定结果: 文档与代码一致({sorted(doc_bugging)})")
    else:
        f.bad("开 bug 的判定结果不一致 —— "
              f"文档说 {sorted(doc_bugging)},代码说 {sorted(VC.BUG_OPENING_VERDICTS)}。"
              f"(blocked/skipped/pending 开成 bug 会污染缺陷库)")


# ---------------------------------------------------------------- 7

ID_TABLE_HEADER = ["前缀", "用途"]

# 文档里"给示例 ID"的位置:`- ID: XXX` / `- **ID**: XXX`
ID_EXAMPLE_RE = re.compile(r"^\s*[-*]?\s*(?:\*\*)?ID(?:\*\*)?\s*[:：]\s*(\S+)", re.M)


def id_example_findings(docs: dict[str, str], id_re,
                        id_hint: str) -> tuple[list[str], list[str]]:
    """文档里写在 `ID:` 位置上的示例,必须是闸门收的形状。

    这条是第三方会话踩出来的:references/10-upstreams.md 的示例写着 `CONS-CFG-C01`,
    而 ID_RE 只认 REQ/INV/EXP —— 文档本身就是错的,人照着它写用例,要写到
    第 30 条才会在闸门那里被拒,然后只能自己改名(顺手把"来源"那一维丢掉)。
    只扫 `ID:` 位置而不是所有反引号:文档里 `BUG-001`、`PRE-N` 这类不是用例 ID。
    """
    oks: list[str] = []
    bads: list[str] = []
    checked = 0
    skipped = 0
    for name, text in sorted(docs.items()):
        for m in ID_EXAMPLE_RE.finditer(text):
            tok = m.group(1).strip().strip("`")
            if "<" in tok or ">" in tok:
                skipped += 1          # `<版本需求号>` 这类占位符不是具体示例
                continue
            checked += 1
            if not id_re.match(tok):
                bads.append(f"{name}: 示例 ID `{tok}` 过不了闸门(当前收 "
                            f"`{id_hint}`)—— 照文档写出来的用例进不了流水线")
    if checked == 0:
        bads.append(f"文档里没解析到任何 `ID:` 示例(跳过的占位符 {skipped} 处)"
                    f"—— 对账未生效,检查正则")
    elif not bads:
        oks.append(f"文档 `ID:` 位置的示例都能过闸门({checked} 处)")
    return oks, bads


def case_id_prefix_findings(text: str, code_prefixes: tuple[str, ...], id_re,
                            schema_pattern: str | None) -> tuple[list[str], list[str]]:
    """用例 ID 前缀:参考文献表 / schema pattern / 代码常量,三处必须一致。

    为什么单列一道闸门:`REQ-/INV-/EXP-/CONS-` 这件事同时写在四个地方,而它们
    漂移起来是**静默**的 —— 文档里给了示例、闸门却拒收,人照着文档写出来的用例
    进不了流水线,只能自己改名,顺手把「来源」这一维丢掉(第三方会话已经踩过:
    三条一致性用例被从 CONS- 改名成 INV-,理由是 ID_RE 不收 CONS-)。
    这里连"文档里的示例 ID 能不能真的过闸门"一起验。

    抽成纯函数是为了让 selftest 能喂一份改坏了的文档进来,证明它会红 ——
    没红过的闸门不算验过。
    """
    oks: list[str] = []
    bads: list[str] = []
    rows = id_table_rows(text, ID_TABLE_HEADER)
    if not rows:
        bads.append("3-cases.md 里找不到「用例 ID 约定」表(表头应为 "
                    f"{ID_TABLE_HEADER})—— 前缀无法对账")
        return oks, bads

    doc_prefixes: set[str] = set()
    for cells in rows:
        for tok in re.findall(r"`([A-Za-z][A-Za-z0-9_.-]*)", cells[0]):
            doc_prefixes.add(tok.rstrip("-"))
    # 具体示例 ID:去掉 `REQ-<需求号>-C<序>` 这种模板 —— 模板取出来的 "REQ-"
    # 在后面没有内容,过不了"前缀 + 至少一段"这条形状判据。
    examples: list[str] = []
    for cells in rows:
        for cell in cells:
            for tok in re.findall(r"`([A-Za-z][A-Za-z0-9_.-]*)", cell):
                if any(re.fullmatch(rf"{p}-[A-Za-z0-9_.-]+", tok) for p in doc_prefixes):
                    examples.append(tok)

    code = set(code_prefixes)
    if not doc_prefixes:
        bads.append("「用例 ID 约定」表里没解析到任何前缀 —— 对账未生效,检查正则")
    elif doc_prefixes == code:
        oks.append(f"用例 ID 前缀: 文档与代码一致({len(code)} 项)")
    else:
        bads.append("用例 ID 前缀不一致 —— 只在 3-cases.md: "
                    f"{sorted(doc_prefixes - code)};只在代码: {sorted(code - doc_prefixes)}")

    if schema_pattern is None:
        bads.append("derived-schema.json 里找不到 id.pattern —— 前缀无法对账")
    else:
        m = re.search(r"\(([^()]*)\)", schema_pattern)
        schema_prefixes = {p.strip() for p in m.group(1).split("|")} if m else set()
        if not schema_prefixes:
            bads.append(f"schema 的 id.pattern 解不出前缀: {schema_pattern!r}")
        elif schema_prefixes == code:
            oks.append(f"用例 ID 前缀: schema pattern 与代码一致({len(code)} 项)")
        else:
            bads.append("schema 的 id.pattern 与代码不一致 —— 只在 schema: "
                        f"{sorted(schema_prefixes - code)};只在代码: "
                        f"{sorted(code - schema_prefixes)}")

    if not examples:
        bads.append("约定表里没解析到任何具体示例 ID —— 对账未生效,检查正则")
    else:
        rejected = [e for e in examples if not id_re.match(e)]
        if rejected:
            bads.append(f"文档里的示例 ID 会被闸门拒掉: {sorted(set(rejected))} —— "
                        f"照着文档写出来的用例进不了流水线")
        else:
            oks.append(f"约定表里的示例 ID 都能过闸门({len(examples)} 个)")
    return oks, bads


def check_case_id_prefixes(f: Findings) -> None:
    cases_md = SKILL_DIR / "references" / "3-cases.md"
    if not cases_md.exists():
        f.bad("references/3-cases.md 不存在 —— 用例 ID 约定的权威文档缺失")
        return
    pattern = None
    try:
        schema = json.loads(read(SKILL_DIR / "assets" / "derived-schema.json"))
        pattern = schema.get("properties", {}).get("id", {}).get("pattern")
    except json.JSONDecodeError as exc:
        f.bad(f"derived-schema.json JSON 解析失败: {exc}")
    oks, bads = case_id_prefix_findings(read(cases_md), VC.CASE_ID_PREFIXES,
                                        VC.ID_RE, pattern)
    # 文档正文里写在 `ID:` 位置上的示例也得是闸门收的形状
    oks2, bads2 = id_example_findings({p.name: read(p) for p in all_docs()},
                                      VC.ID_RE, VC.ID_PREFIX_HINT)
    for msg in (*oks, *oks2):
        f.ok(msg)
    for msg in (*bads, *bads2):
        f.bad(msg)


# ---------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser(description="testflow 文档-实现对账")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    if not (SKILL_DIR / "SKILL.md").exists():
        print(f"错误: 找不到 {SKILL_DIR / 'SKILL.md'}", file=sys.stderr)
        return 2

    f = Findings()
    check_script_mentions(f)
    check_reference_orphans(f)
    check_dimensions_sync(f)
    check_schema_vs_code(f)
    check_md_tables(f)
    check_cli_examples(f)
    check_verdict_table(f)
    check_case_id_prefixes(f)

    if not args.quiet:
        for c in f.checked:
            print(f"  ok  {c}")

    print()
    if f.problems:
        print(f"文档-实现对账失败,{len(f.problems)} 处不一致:")
        for p in f.problems:
            print(f"  ✗ {p}")
        print()
        print("这些是机械可查的漂移。文档说了但代码没做,或代码做了但文档没说,"
              "都必须修 —— 否则使用者会按文档行事而得到错误结果。")
        return 1

    print(f"文档-实现对账通过({len(f.checked)} 项检查)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
