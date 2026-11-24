#!/usr/bin/env python3
"""testflow 幂等防护缺失扫描器。

这是"人肉 code review"的自动化版本:扫描有副作用的入口,检查四类防护是否
存在,产出带文件行号的缺失清单。

## 它做什么

找出所有**会产生不可逆副作用**的入口(支付、发货、结算、领取、存档…),
对每个入口检查:
  1. 幂等键      请求是否带唯一订单号/请求 id
  2. 状态守卫    状态流转前是否有前置状态校验
  3. 回调去重    外部回调处理前是否查"这笔处理过没"
  4. 重放去重    本地队列重放前是否检查该操作已成功

## 它不做什么(重要)

- **不判断防护是否正确。** 只判断"有没有"。找到幂等键不等于幂等键有效。
  所以静态扫描必须与故障注入(scripts/run_case.py 的 fault 动作)配合:
  静态找缺失,动态验有效。任何一层单用都会漏。
- **不提供期望。** 代码只回答"查哪里",不回答"应该是什么"。
- 启发式规则会有误判:防护可能写在别的层(基类、中间件、服务端)。
  所以产出必须标"待人工确认",**不能直接当缺陷开单**。

纯标准库,零依赖。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------- 规则

# 副作用入口的命名特征(默认)。可用 --entry-pattern 覆盖
DEFAULT_ENTRY_PATTERN = (
    r"(pay|purchase|buy|charge|order|settle|deliver|grant|reward|claim|"
    r"submit|deduct|consume|refund|payout|withdraw|exchange|redeem|"
    r"receive|apply|save|sync|upload)"
)

# 四类防护的识别特征
GUARD_PATTERNS = {
    "幂等键": [
        r"\bidem\w*\b", r"\brequestId\b", r"\breqId\b", r"\borderId\b",
        r"\border_id\b", r"\btradeNo\b", r"\boutTradeNo\b", r"\bnonce\b",
        r"\bclientToken\b", r"\buniqueKey\b", r"\btransactionId\b",
        r"\btransId\b", r"\bserialNo\b", r"\bbizId\b",
    ],
    "状态守卫": [
        r"if\s*\([^)]*\b(?:status|state|stage|phase)\b\s*(?:==|!=|===|!==|<|>)",
        r"switch\s*\([^)]*\b(?:status|state|stage|phase)\b",
        r"\b(?:require|assert|ensure|check|verify)(?:Status|State)\w*\s*\(",
        r"\b(?:status|state)\s*(?:==|!=|===|!==)\s*\w*(?:Pending|Init|Created|Paid|Success)\w*",
        r"\b(?:is|can)\w*(?:Pay|Deliver|Submit|Claim)\w*\s*\(",
    ],
    "回调去重": [
        r"\b(?:exists|existsBy|findOne|findFirst|findUnique|findBy|queryOne|selectOne|getOne)\w*\s*\(",
        r"\b(?:has|is)(?:Processed|Handled|Consumed|Notified)\b",
        r"\balreadyProcessed\b", r"\bprocessedSet\b", r"\bdedup\w*\b",
        r"\bcount\w*\s*\([^)]*\)\s*(?:>|>=|==|!=)",
    ],
    "重放去重": [
        r"\bappliedOps?\b", r"\bappliedOpIds\b", r"\bprocessedOps?\b",
        r"\breplay\w*Guard\b", r"\blastOpId\b", r"\bappliedSet\b",
        r"\bdedup\w*\b", r"\bseen\w*Ids?\b", r"\bidempotencyKey\b",
    ],
}

# 副作用调用的识别特征(用于说明"为什么认为这是副作用入口")
SIDE_EFFECT_PATTERNS = [
    r"\b(?:Pay|Purchase|Charge|Deduct|Consume|Refund)\w*\s*\(",
    r"\b(?:Add|Grant|Deliver|Reward|Give)\w*(?:Coin|Gem|Item|Reward|Gold|Currency)\w*\s*\(",
    r"\b(?:Update|Insert|Upsert|Save|Commit|Write)\w*\s*\(",
    r"\b(?:Send|Post|Request|Http|Call|Invoke|Rpc)\w*\s*\(",
    r"\bsendRequest\b", r"\bwx\.request\b", r"\btt\.request\b",
]

LANG_SUFFIXES = {
    ".cs": "cstyle", ".js": "cstyle", ".jsx": "cstyle", ".ts": "cstyle",
    ".tsx": "cstyle", ".java": "cstyle", ".kt": "cstyle", ".cpp": "cstyle",
    ".cc": "cstyle", ".h": "cstyle", ".hpp": "cstyle", ".c": "cstyle",
    ".m": "cstyle", ".mm": "cstyle", ".go": "cstyle", ".swift": "cstyle",
    ".py": "python", ".pyw": "python",
}

SKIP_DIRS = {".git", "node_modules", "Library", "Temp", "obj", "bin",
             "__pycache__", ".vs", "Build", "build", "Packages", "dist",
             ".idea", "venv", ".venv", "Pods"}


# ---------------------------------------------------------------- 去注释与字符串

def mask_noncode(text: str, lang: str) -> str:
    """把注释与字符串字面量替换成空格,保留换行与字符位置。

    这样括号计数与正则匹配不会被注释/字符串里的花括号和关键字干扰。
    """
    out: list[str] = []
    i, n = 0, len(text)
    state = "code"
    while i < n:
        c = text[i]
        if state == "code":
            if lang == "python" and text[i:i + 3] in ('"""', "'''"):
                state = "triple"
                out.append("   ")
                i += 3
                continue
            if c == "/" and text[i + 1:i + 2] == "/":
                state = "line"
                out.append("  ")
                i += 2
                continue
            if c == "/" and text[i + 1:i + 2] == "*":
                state = "block"
                out.append("  ")
                i += 2
                continue
            if c == '"':
                state = "str"
                out.append(" ")
                i += 1
                continue
            if c == "'":
                state = "char"
                out.append(" ")
                i += 1
                continue
            if c == "#" and lang == "python":
                state = "line"
                out.append(" ")
                i += 1
                continue
            out.append(c)
            i += 1
        elif state == "line":
            if c == "\n":
                state = "code"
                out.append("\n")
            else:
                out.append(" ")
            i += 1
        elif state == "block":
            if c == "*" and text[i + 1:i + 2] == "/":
                state = "code"
                out.append("  ")
                i += 2
                continue
            out.append("\n" if c == "\n" else " ")
            i += 1
        elif state == "triple":
            if lang == "python" and text[i:i + 3] in ('"""', "'''"):
                state = "code"
                out.append("   ")
                i += 3
                continue
            out.append("\n" if c == "\n" else " ")
            i += 1
        else:  # str / char
            if c == "\\":
                out.append("  ")
                i += 2
                continue
            if (state == "str" and c == '"') or (state == "char" and c == "'"):
                state = "code"
            out.append("\n" if c == "\n" else " ")
            i += 1
    return "".join(out)


# ---------------------------------------------------------------- 函数提取

def line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


KEYWORDS = {"if", "for", "while", "switch", "catch", "using", "lock",
            "foreach", "return", "new", "sizeof", "do", "else", "try",
            "finally", "throw", "case", "default", "typeof", "nameof"}


def extract_cstyle_functions(masked: str) -> list[dict]:
    """提取 C 风格语言的函数体(靠花括号配对)。

    同时支持 K&R(花括号同行)与 Allman(花括号下一行,C# 惯例):
    匹配完参数表后跳过空白,**先遇到 `{` 才算函数定义,先遇到 `;` 则不是**
    (后者是普通调用语句)。这一条区分是必需的 —— 否则
    `Pay(item.Price, orderId);` 会被误认成函数定义。
    """
    out: list[dict] = []
    header = re.compile(
        r"(?m)^[ \t]*(?:[A-Za-z_][\w<>\[\],\.\?]*[ \t]+)+"
        r"([A-Za-z_]\w*)[ \t]*\(([^()]*(?:\([^()]*\)[^()]*)*)\)"
        r"[ \t]*(?::[^{;]*)?"
    )
    n = len(masked)
    for m in header.finditer(masked):
        name = m.group(1)
        if name in KEYWORDS:
            continue
        j = m.end()
        while j < n and masked[j] in " \t\r\n":
            j += 1
        if j >= n or masked[j] != "{":
            continue
        brace = j
        depth, i = 0, brace
        while i < n:
            ch = masked[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        out.append({
            "name": name,
            "line": line_of(masked, m.start()),
            "body": masked[brace:i + 1],
            "params": m.group(2),
        })
    return out


def extract_python_functions(masked: str) -> list[dict]:
    """提取 Python 函数体(靠缩进)。"""
    lines = masked.split("\n")
    out: list[dict] = []
    pat = re.compile(r"^([ \t]*)def[ \t]+([A-Za-z_]\w*)[ \t]*\(([^)]*)\)")
    for idx, ln in enumerate(lines):
        m = pat.match(ln)
        if not m:
            continue
        indent, name, params = m.group(1), m.group(2), m.group(3)
        body: list[str] = []
        for j in range(idx + 1, len(lines)):
            nxt = lines[j]
            if nxt.strip() == "":
                body.append(nxt)
                continue
            cur_indent = len(nxt) - len(nxt.lstrip())
            if cur_indent <= len(indent):
                break
            body.append(nxt)
        out.append({
            "name": name,
            "line": idx + 1,
            "body": "\n".join(body),
            "params": params,
        })
    return out


def extract_functions(masked: str, lang: str) -> list[dict]:
    return extract_python_functions(masked) if lang == "python" \
        else extract_cstyle_functions(masked)


# ---------------------------------------------------------------- 扫描

def find_guards(params: str, body: str) -> tuple[dict[str, list[str]], list[str]]:
    """返回 (命中四类防护的规则, 命中副作用调用的规则)。参数也算防护位置:
    幂等键常常以参数形式传入。匹配不区分大小写以覆盖 PascalCase 命名。"""
    guards: dict[str, list[str]] = {}
    scope = params + "\n" + body
    for guard, pats in GUARD_PATTERNS.items():
        hits = []
        for p in pats:
            m = re.search(p, scope, re.IGNORECASE)
            if m:
                hits.append(m.group(0).strip()[:60])
        if hits:
            guards[guard] = hits
    side: list[str] = []
    for p in SIDE_EFFECT_PATTERNS:
        m = re.search(p, body, re.IGNORECASE)
        if m:
            side.append(m.group(0).strip()[:60])
    return guards, side


def iter_source_files(src: Path, langs: set[str]) -> list[Path]:
    out = []
    for p in sorted(src.rglob("*")):
        if not p.is_file():
            continue
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        if p.suffix.lower() in LANG_SUFFIXES:
            if langs and LANG_SUFFIXES[p.suffix.lower()] not in langs:
                continue
            out.append(p)
    return out


def scan(src: Path, entry_pattern: str, langs: set[str],
         only_side_effect: bool) -> dict:
    entry_re = re.compile(entry_pattern, re.IGNORECASE)
    entries: list[dict] = []
    files = iter_source_files(src, langs)

    for f in files:
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        lang = LANG_SUFFIXES[f.suffix.lower()]
        masked = mask_noncode(text, lang)
        for fn in extract_functions(masked, lang):
            if not entry_re.search(fn["name"]):
                continue
            guards, side = find_guards(fn["params"], fn["body"])
            if only_side_effect and not side:
                continue
            missing = [g for g in GUARD_PATTERNS if g not in guards]
            entries.append({
                "name": fn["name"],
                "file": str(f),
                "line": fn["line"],
                "guards": guards,
                "missing": missing,
                "side_effects": side,
            })

    return {
        "scanned_dir": str(src),
        "files_scanned": len(files),
        "entries": entries,
        "entries_with_missing": [e for e in entries if e["missing"]],
    }


# ---------------------------------------------------------------- 输出

def render_markdown(result: dict, entry_pattern: str) -> str:
    lines: list[str] = []
    add = lines.append
    add("# 幂等防护缺失扫描")
    add("")
    add(f"- 扫描目录: `{result['scanned_dir']}`")
    add(f"- 扫描文件数: {result['files_scanned']}")
    add(f"- 识别的副作用入口: {len(result['entries'])} 个")
    add(f"- **存在防护缺失的入口: {len(result['entries_with_missing'])} 个**")
    add("")

    add("> **本清单是启发式的,必须人工确认后才能当缺陷。**")
    add("> 防护可能写在别的层(基类、中间件、服务端)。")
    add("> 而且**找到防护不等于防护有效** —— 静态扫描只能发现『缺失』,")
    add("> 发现不了『写错了』,那需要故障注入实测(`run_case.py` 的 `fault` 动作)。")
    add("")

    missing_entries = result["entries_with_missing"]
    if missing_entries:
        add("## 一、防护缺失清单")
        add("")
        for i, e in enumerate(missing_entries, 1):
            add(f"### {i}. `{e['name']}`")
            add("")
            add(f"- 文件: `{e['file']}:{e['line']}`")
            add(f"- **缺失**: {', '.join(e['missing'])}")
            if e["guards"]:
                present = "; ".join(f"{g}({v[0]})" for g, v in e["guards"].items())
                add(f"- 已有防护: {present}")
            else:
                add("- 已有防护: **一个都没有**")
            if e["side_effects"]:
                add(f"- 副作用调用: {', '.join(e['side_effects'])}")
            add(f"- 置信度: **待人工确认**")
            add("")
    else:
        add("## 一、防护缺失清单")
        add("")
        add("_(未发现缺失。注意:这不等于防护有效,仍需故障注入实测)_")
        add("")

    add("## 二、入口覆盖地图")
    add("")
    add("| 入口 | 位置 | 幂等键 | 状态守卫 | 回调去重 | 重放去重 |")
    add("|---|---|---|---|---|---|")
    for e in sorted(result["entries"], key=lambda x: (x["file"], x["line"])):
        cells = []
        for g in ("幂等键", "状态守卫", "回调去重", "重放去重"):
            cells.append("有" if g in e["guards"] else "**缺**")
        add(f"| `{e['name']}` | {Path(e['file']).name}:{e['line']} | "
            + " | ".join(cells) + " |")
    add("")

    add("## 三、下一步")
    add("")
    add("1. 人工确认缺失清单(防护是否写在别处)")
    add("2. 对确认缺失的入口,写故障注入用例制造真实的重复场景")
    add("3. 判定必须走 `state-report` + 幂等不变量(增量精确等于单次值)")
    add("4. **不要**用「连续点击 N 次」验证 —— 那是伪覆盖")
    add("")
    add(f"入口识别正则: `{entry_pattern}`")
    add("")
    return "\n".join(lines)


# ---------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser(
        description="扫描幂等防护缺失(支付/发货/结算等副作用入口)")
    ap.add_argument("--src", type=Path, required=True, help="源码目录")
    ap.add_argument("--entry-pattern", default=DEFAULT_ENTRY_PATTERN,
                    help="副作用入口的命名正则")
    ap.add_argument("--langs", default="",
                    help="限定语言,逗号分隔(cstyle,python);默认全部")
    ap.add_argument("--only-side-effect", action="store_true", default=True,
                    help="只报告函数体内确实有副作用调用的入口(默认开)")
    ap.add_argument("--include-name-only", dest="only_side_effect",
                    action="store_false",
                    help="名字匹配即报告,不要求函数体内有副作用调用")
    ap.add_argument("--out", type=Path, help="markdown 输出路径")
    ap.add_argument("--json", type=Path, help="同时写出 JSON")
    args = ap.parse_args()

    if not args.src.is_dir():
        print(f"错误: --src 不是目录: {args.src}", file=sys.stderr)
        return 2

    langs = {s.strip() for s in args.langs.split(",") if s.strip()}
    result = scan(args.src, args.entry_pattern, langs, args.only_side_effect)
    md = render_markdown(result, args.entry_pattern)

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(md, encoding="utf-8")
        print(f"缺失清单已写出: {args.out}")
    else:
        print(md)

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"JSON 已写出: {args.json}")

    n = len(result["entries_with_missing"])
    print(f"扫描 {result['files_scanned']} 个文件,"
          f"识别 {len(result['entries'])} 个副作用入口,"
          f"其中 {n} 个存在防护缺失(待人工确认)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
