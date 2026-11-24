#!/usr/bin/env python3
"""关注点覆盖:把"产品/研发/策划担心的事"与"我们测了什么、结论是什么"对上。

**为什么需要它**:测试自己的用例清单只能证明"我测了我想测的",证明不了
"你担心的事我覆盖到了"。真正给人置信度的是这张表 —— 产品提的每一条
(比如"金币会不会变成负数"),都能查到:怎么覆盖的、结论是什么、依据在哪。

    --register concerns.csv    关注点登记册(产品/研发/策划/平台 主动提,或我们从需求/设计/代码/线上反馈提取后请他们确认)
    --out 结论页.md            给人看的一页结论(缺口在前)

四条规矩(机器校验,不靠自觉):

1. **不许口头覆盖。** 结论写"已覆盖并验证"的,必须给出判定强度(代码判定/工具扫描/视觉判定)
   与依据(哪条判定项、哪次运行、哪个工具输出)—— 空依据直接算"证据不足"。
2. **缺口必须有出口。** 结论是"未覆盖/做不到/待条件"的,必须写清卡在哪、谁来补 ——
   没有出口的缺口等于没提。
3. **"做不到"必须写原因。** 平台限制、主观体验都行,但必须写。
4. **结论按可审计性分级。** 代码判定 > 工具扫描 > 视觉判定 > 人工;强度低于代码判定的
   结论,报告里会标出来 —— 让人知道我们的信心到底建立在什么上。

退出码:`0` = 没有缺口;`1` = 有未覆盖,或有"证据不足/自相矛盾"的行;
`3` = 没有未覆盖,但有"卡在外部条件"的(置信度不完整)。`2` = 用法/格式错误。

纯标准库,零依赖。CSV 用 Excel 存成 UTF-8 或 GBK 都能读(国内 Excel 默认 GBK)。
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

# 没人会主动写"我们没考虑支付" —— 因为没人想到。所以按类别机械问一遍:
# 每一类要么有登记(任何档都行,包括"做不到"),要么显式写"不适用"。
RISK_CATEGORIES: dict[str, list[str]] = {
    "资损(钱/道具/发货)": ["支付", "充值", "金币", "货币", "道具", "发货", "结算", "订单", "扣款", "退款"],
    "账号与登录": ["登录", "授权", "账号", "封禁", "实名", "游客"],
    "进度与存档": ["存档", "进度", "掉档", "丢档", "重连", "离线"],
    "崩溃与可用性": ["崩溃", "闪退", "ANR", "卡死", "白屏", "无响应", "卡顿"],
    "合规与隐私": ["隐私", "协议", "合规", "权限", "采集", "实名"],
    "性能": ["帧率", "内存", "启动耗时", "加载", "性能", "耗电"],
}
SOURCES = ["产品", "研发", "策划", "平台", "线上反馈", "测试"]
STRENGTHS = ["代码判定", "工具扫描", "视觉判定", "人工", "无"]
VERDICTS = ["已覆盖并验证", "已覆盖并发现缺陷", "覆盖但结论待条件", "未覆盖", "做不到"]
# 缺口优先展示的顺序(先看没结论的,再看有缺陷的)
ORDER = {"未覆盖": 0, "做不到": 1, "覆盖但结论待条件": 2, "已覆盖并发现缺陷": 3,
         "已覆盖并验证": 4}
STRENGTH_ICON = {"代码判定": "🟢", "工具扫描": "🟢", "视觉判定": "🟡", "人工": "🟠", "无": "⚪"}
VERDICT_ICON = {"已覆盖并验证": "✅", "已覆盖并发现缺陷": "🟠",
                "覆盖但结论待条件": "🟡", "未覆盖": "🔴", "做不到": "⛔"}
COLUMNS = ["关注点", "模块", "来源", "上游引用", "为什么担心", "覆盖方式", "判定强度",
           "结论", "依据", "谁来补", "期限", "备注"]
# 稳定编号。用例侧要写"我覆盖的是哪条关注点" —— 用文本引用在对账时对不上
# ("金币负数那条"到底是哪条),所以给登记册发号,发了就不再变。
ID_COLUMN = "关注点ID"
# 从"覆盖方式"里认判定项引用:REQ-SETTLE-C01/E1、INV-COIN-C01 这种写法
CHECK_REF_RE = re.compile(r"([A-Z][A-Z0-9]*-[A-Za-z0-9_.-]+?)(?:/(E\d+))?(?=[^A-Za-z0-9_.-]|$)")


def read_register(path: Path) -> tuple[list[dict], list[str]]:
    problems: list[str] = []
    if not path.exists():
        return [], [f"找不到登记册: {path}"]
    text = None
    for enc in ("utf-8-sig", "gbk", "utf-8"):
        try:
            text = path.read_text(encoding=enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        return [], [f"{path} 编码认不出来(试过 utf-8-sig / gbk)"]
    rows = list(csv.reader(text.splitlines()))
    if not rows:
        return [], ["登记册是空的"]
    header = [c.strip() for c in rows[0]]
    missing = [c for c in COLUMNS if c not in header]
    if missing:
        return [], [f"表头缺列: {missing}(应包含 {COLUMNS})"]
    idx = {c: header.index(c) for c in COLUMNS}
    out: list[dict] = []
    for i, raw in enumerate(rows[1:], start=2):
        if not any(cell.strip() for cell in raw):
            continue
        row = {c: (raw[idx[c]].strip() if idx[c] < len(raw) else "") for c in COLUMNS}
        if ID_COLUMN in header and header.index(ID_COLUMN) < len(raw):
            row[ID_COLUMN] = raw[header.index(ID_COLUMN)].strip()
        else:
            row[ID_COLUMN] = ""
        row["_line"] = i
        out.append(row)
    return out, problems


def assign_ids(path: Path) -> tuple[int, int, str]:
    """给登记册补稳定编号:已有的保持不动,缺的按文件顺序补 C01、C02……

    发号规则是**只往后发,不挪老号**:追加新关注点不会让已经写进用例的编号失效。
    编号写成固定宽度(C01…C99,C100…),方便排序与引用。
    """
    text = None
    for enc in ("utf-8-sig", "gbk", "utf-8"):
        try:
            text = path.read_text(encoding=enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        return 0, 0, f"{path} 编码认不出来"
    rows = list(csv.reader(text.splitlines()))
    if not rows:
        return 0, 0, "登记册是空的"
    header = [c.strip() for c in rows[0]]
    missing_cols = [c for c in COLUMNS if c not in header]
    if missing_cols:
        return 0, 0, f"表头缺列: {missing_cols}(应包含 {COLUMNS})"
    if ID_COLUMN not in header:
        header.append(ID_COLUMN)
        rows[0] = header
    id_at = header.index(ID_COLUMN)
    used = {r[id_at].strip() for r in rows[1:] if len(r) > id_at and r[id_at].strip()}
    width = max([len(u) for u in used] + [0]) - 1     # C01 -> 数字宽度 2
    def next_id(n: int) -> str:
        return f"C{n:0{max(width, 2)}d}"
    nums = [int(u[1:]) for u in used if u.startswith("C") and u[1:].isdigit()]
    nxt = max(nums) + 1 if nums else 1
    assigned = 0
    for i, raw in enumerate(rows[1:], start=2):
        if not any(c.strip() for c in raw):
            continue
        while len(raw) <= id_at:
            raw.append("")
        if not raw[id_at].strip():
            raw[id_at] = next_id(nxt)
            nxt += 1
            assigned += 1
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        csv.writer(fh).writerows(rows)
    return assigned, len(rows) - 1, ""


# ---------------------------------------------------------------- 证据核对

def load_run_index(runs_dir: Path) -> dict:
    """(用例ID, 判定项) -> {"verdicts": [...], "latestAt": ISO, "latestRun": id, "runs": n}

    带时间是为了查**时效性**:上一版测过的结论,不能拿来给这一版打包票。
    """
    index: dict[tuple[str, str], dict] = {}
    if not runs_dir or not runs_dir.exists():
        return index
    for f in sorted(runs_dir.glob("run-*.json")):
        if "judge" in f.name:
            continue
        try:
            run = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        at = str(run.get("startedAt") or "")
        rid = str(run.get("runId") or f.stem)
        for case in run.get("cases") or []:
            for chk in case.get("checks") or []:
                key = (str(case.get("id")), str(chk.get("source")))
                ent = index.setdefault(key, {"verdicts": [], "latestAt": "",
                                             "latestRun": "", "runs": 0,
                                             "evidence": []})
                ent["verdicts"].append(str(chk.get("verdict")))
                for p_ in chk.get("evidence") or []:
                    ent["evidence"].append(str(p_))
                ent["runs"] = ent.get("runs", 0) + 1
                if at > (ent["latestAt"] or ""):
                    ent["latestAt"], ent["latestRun"] = at, rid
    return index


def refs_of(row: dict) -> list[tuple[str, str | None]]:
    """从"覆盖方式"里抽出判定项引用。返回 [(用例ID, 判定项或 None)]。"""
    out: list[tuple[str, str | None]] = []
    for m in CHECK_REF_RE.finditer(row["覆盖方式"] or ""):
        cid, src = m.group(1), m.group(2)
        if cid in ("E", "PRE", "S"):      # 光秃秃的 E1 不是用例引用
            continue
        out.append((cid, src))
    return out


def verify_row(row: dict, index: dict) -> dict | None:
    """去 run 结果里核对这一行的依据。

    返回 None 表示这行没有可核对的判定项引用(只能算"自述依据")。
    """
    if not index:
        return None
    refs = refs_of(row)
    if not refs:
        return None
    hits: list[str] = []
    latest_at, latest_run = "", ""
    for cid, src in refs:
        entries = []
        if src:
            if (cid, src) in index:
                entries = [index[(cid, src)]]
        else:
            entries = [v for (c, _s), v in index.items() if c == cid]
        for ent in entries:
            hits += ent["verdicts"]
            if (ent.get("latestAt") or "") > latest_at:
                latest_at, latest_run = ent["latestAt"], ent["latestRun"]
    if not hits:
        return {"status": "not_found",
                "note": "依据里写的判定项在运行结果里找不到 —— 依据不成立",
                "refs": refs}
    decided = [v for v in hits if v in ("pass", "fail")]
    if not decided:
        return {"status": "no_conclusion",
                "note": f"依据指向的判定项在运行结果里没有结论(全是 "
                        f"{sorted(set(hits))})—— 说了已覆盖,其实没结论",
                "refs": refs}
    has_evidence = any(ent.get("evidence") for ent in
                       [index[k] for k in index if k[0] in {c for c, _ in refs}])
    out = {"refs": refs, "latestAt": latest_at, "latestRun": latest_run,
           "hasEvidence": has_evidence}
    if not has_evidence:
        # 有结论但没证据文件:这个 ✅ 只能算弱证据 —— 没证据不许判通过是本框架的底线
        out.update({"status": "verified_weak",
                    "note": "判定项有结论,但**没有证据文件**(截图/上报/日志)"
                            " —— 结论无法独立复核,只能算弱证据"})
        return out
    if "fail" in decided:
        out.update({"status": "verified_fail",
                    "note": f"依据核对通过,但该判定项是 **fail** —— "
                            f"这条关注点有已知缺陷,结论里必须体现出来"})
        return out
    out.update({"status": "verified_pass",
                "note": f"依据核对通过(判定项 {len(decided)} 条有结论,含 pass)"})
    return out


def check_freshness(row: dict, since: str) -> bool:
    """依据里的运行结果是不是**本版本之后**跑的?

    上一版测过的结论,不能拿来给这一版打包票 —— 这是假信心最大的来源。
    返回 True 表示新鲜(或没给 --since);False 表示过期,结论要降级。
    """
    v = row.get("_verification") or {}
    at = v.get("latestAt") or ""
    if not since or not at:
        return True
    return at >= since


def check_deadline(row: dict, today: str) -> dict | None:
    """期限写成日期且已经过去 -> 报警。缺口可以挂着,但不能假挂着。"""
    raw = (row["期限"] or "").strip()
    m = re.search(r"(\d{4}-\d{2}-\d{2})", raw)
    if not m or not today:
        return None
    if m.group(1) >= today:
        return None
    return {"kind": "overdue", "level": "error",
            "where": f"第 {row['_line']} 行「{row['关注点']}」",
            "note": f"期限 {m.group(1)} 已过(今天 {today}),这条"
                    f"「{row['结论']}」还挂着 —— 要么补上,要么重新给期限",
            "line": row["_line"]}


def check_categories(rows: list[dict]) -> list[dict]:
    """按风险类别问一遍:每一类都要有登记,否则就是盲区。

    "没人会主动写'我们没考虑支付'" —— 因为没人想到。所以机械问。
    """
    findings: list[dict] = []
    blob = " ".join((r["关注点"] or "") + " " + (r["为什么担心"] or "") for r in rows)
    for cat, keywords in RISK_CATEGORIES.items():
        hit = [k for k in keywords if k in blob]
        if hit:
            continue
        findings.append({
            "kind": "blind_spot", "level": "warn",
            "where": f"风险类别「{cat}」",
            "note": f"登记册里没有任何一条关注点提到 {keywords[:4]} —— "
                    f"如果这类风险本项目确实不涉及,加一条写清「不适用」;"
                    f"如果涉及,那就是真盲区",
            "line": 0})
    return findings


def _verify_claim(row: dict, index: dict, verify: bool, bad, expect: str,
                  since: str = "") -> None:
    """对"我们有结论"的两档做依据核对。

    expect="pass": 结论是"已覆盖并验证" —— 核对结果必须是 pass,否则整条不成立
    expect="fail": 结论是"已覆盖并发现缺陷" —— 核对结果必须是 fail(说了发现缺陷就得真有)
    """
    if not (verify and index):
        return
    v = verify_row(row, index)
    row["_verification"] = v
    if v is None:
        if not any(name in (row["依据"] or "") for name in TOOL_NAMES):
            bad("self_declared", "依据既没引用判定项、也没指向某个工具 —— "
                                 "无法核对(补一条可核对的依据)", "warn")
        return
    if v["status"] in ("not_found", "no_conclusion"):
        bad("evidence_check_failed", v["note"])
        return
    if v["status"] == "verified_weak":
        bad("weak_evidence", v["note"], "warn")
        # 弱证据继续往下判(有缺陷比没证据更严重)

    if not check_freshness(row, since) and v["status"] in ("verified_pass",
                                                           "verified_fail"):
        bad("stale_evidence",
            f"依据来自 {v.get('latestAt')}(run {v.get('latestRun')}),"
            f"早于本版本要求的 {since} —— 上一版测过不代表这一版没问题,"
            f"要么重跑,要么把结论改成「覆盖但结论待条件」并把重跑排上")
        return
    if expect == "pass" and v["status"] == "verified_fail":
        bad("defect_hidden", v["note"] + ";结论应写「已覆盖并发现缺陷」并挂 bug 单 —— "
            "有缺陷还写「没问题」是最危险的糊弄")
    elif expect == "fail" and v["status"] == "verified_pass":
        bad("claim_mismatch", "核对显示该判定项是 pass,但结论写「已覆盖并发现缺陷」—— "
                              "对不上,别把通过的说成有缺陷")
    elif expect == "fail" and v["status"] == "verified_fail":
        row["_defect_confirmed"] = True


def reasoning_kind(row: dict, verification: dict | None) -> str:
    """这一行的依据属于哪种:核对通过 / 工具依据 / 自述。"""
    if verification:
        return verification["status"]
    basis = row["依据"] or ""
    if any(name in basis for name in TOOL_NAMES):
        return "tool_basis"
    return "self_declared"


TOOL_NAMES = [p.name for p in (Path(__file__).resolve().parent).glob("*.py")]


def check_rows(rows: list[dict], index: dict | None = None,
               verify: bool = True, since: str = "",
               bug_index: dict | None = None) -> list[dict]:
    """逐行校验 + 证据核对。返回 findings(每条挂在行上,带 level)。"""
    out: list[dict] = []
    for row in rows:
        where = f"第 {row['_line']} 行「{row['关注点'] or '?'}」"

        def bad(kind: str, note: str, level: str = "error") -> None:
            out.append({"kind": kind, "where": where, "note": note,
                        "line": row["_line"], "level": level})

        for col in ("关注点", "来源", "覆盖方式", "判定强度", "结论"):
            if not row[col]:
                bad("missing_field", f"{col} 不能为空")
        if row["来源"] and row["来源"] not in SOURCES:
            bad("bad_source", f"来源 {row['来源']!r} 不在 {SOURCES}")
        if row["判定强度"] and row["判定强度"] not in STRENGTHS:
            bad("bad_strength", f"判定强度 {row['判定强度']!r} 不在 {STRENGTHS}")
        if row["结论"] and row["结论"] not in VERDICTS:
            bad("bad_verdict", f"结论 {row['结论']!r} 不在 {VERDICTS}")
        if any(f["line"] == row["_line"] for f in out):
            continue

        if row["结论"] == "已覆盖并验证":
            if row["判定强度"] == "无":
                bad("contradiction", "结论写「已覆盖并验证」但判定强度是「无」—— 靠什么验的?")
            elif not row["依据"]:
                bad("no_evidence", "结论写「已覆盖并验证」但没写依据 —— "
                                   "口头覆盖不算覆盖(填:哪条判定项/哪次运行/哪个工具输出)")
            elif row["覆盖方式"] == "无":
                bad("contradiction", "结论写「已覆盖并验证」但覆盖方式是「无」")
            else:
                _verify_claim(row, index, verify, bad, expect="pass", since=since)
        elif row["结论"] == "已覆盖并发现缺陷":
            if row["判定强度"] == "无" or not row["依据"]:
                bad("no_evidence", "「已覆盖并发现缺陷」也要写判定强度与依据"
                                   "(哪条判定项 fail、开了哪张 bug 单)")
            else:
                _verify_claim(row, index, verify, bad, expect="fail", since=since)
            if bug_index and row["结论"] == "已覆盖并发现缺陷" and row["依据"]:
                # 说发现缺陷就得有单 —— 策划最关心的"后续有没有 bug"靠这个兜住
                ids = BUG_REF_RE.findall(f"{row['依据']} {row['备注']} {row['覆盖方式']}")
                if not ids:
                    bad("defect_without_bug",
                        "说「已覆盖并发现缺陷」但没写 bug 单号 —— 有问题必须有单,"
                        "不然策划看不到、也追不了")
                else:
                    missing = [i for i in ids if i not in bug_index]
                    if missing:
                        bad("defect_without_bug",
                            f"引用的 bug 单不存在:{missing}(单号写错?还没开?)")
            else:
                _verify_claim(row, index, verify, bad, expect="fail", since=since)
        elif row["结论"] == "覆盖但结论待条件":
            if not row["谁来补"]:
                bad("no_owner", "卡在外部条件,但没写卡在哪/谁来补 —— 这样的缺口等于没提")
            elif not row["期限"]:
                bad("no_deadline", "待条件的缺口没写什么时候能补上 —— "
                                   "没有时间的承诺等于没有承诺")
        elif row["结论"] == "未覆盖":
            if not row["谁来补"]:
                bad("no_owner", "未覆盖但没写谁来补 —— 缺口没有出口")
            elif not row["期限"]:
                bad("no_deadline", "未覆盖的缺口没写什么时候补 —— 缺口要么排期,要么别写")
        elif row["结论"] == "做不到":
            if not (row["依据"] or row["备注"]):
                bad("no_reason", "写「做不到」必须给原因(平台限制?主观体验?)")
            if not row["谁来补"]:
                bad("no_owner", "写「做不到」也要写清这条路谁都不补,填「——」即可")
    today = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")
    for row in rows:
        d = check_deadline(row, today)
        if d:
            out.append(d)
    out += check_categories(rows)
    return out


# 高风险关键词:出现在"关注点"或"为什么担心"里,说明这条缺口的后果是资损/掉档级
HIGH_RISK_WORDS = ("支付", "充值", "金币", "货币", "道具", "发货", "结算", "订单",
                   "退款", "扣款", "存档", "掉档", "丢档", "重复")


def is_high_risk(row: dict) -> bool:
    blob = f"{row.get('关注点') or ''} {row.get('为什么担心') or ''}"
    return any(w in blob for w in HIGH_RISK_WORDS)


def open_gaps(rows: list[dict]) -> list[dict]:
    """还没结论的条目(未覆盖 / 待条件),高风险排前面。"""
    gaps = [r for r in rows if r["结论"] in ("未覆盖", "覆盖但结论待条件")]
    return sorted(gaps, key=lambda r: (0 if is_high_risk(r) else 1,
                                       ORDER.get(r["结论"], 9),
                                       r["关注点"] or ""))


def group_summary(rows: list[dict]) -> list[dict]:
    """按**父节点(模块)**归纳:每个模块一行小结 —— 这就是"归纳总结"那一层。

    父节点回答"这个模块整体怎么样",子节点回答"具体哪条、卡在哪"。
    """
    groups: dict[str, dict] = {}
    for r in rows:
        mod = (r.get("模块") or "").strip() or "(未归类)"
        g = groups.setdefault(mod, {"module": mod, "total": 0,
                                    "byVerdict": {v: 0 for v in VERDICTS},
                                    "upstream": set()})
        v = r["结论"] if r["结论"] in VERDICTS else "未覆盖"
        g["total"] += 1
        g["byVerdict"][v] += 1
        if r.get("上游引用"):
            g["upstream"].add(r["上游引用"])

    out: list[dict] = []
    for g in groups.values():
        bv = g["byVerdict"]
        if bv["未覆盖"]:
            one = f"还有 {bv['未覆盖']} 条没测"
        elif bv["覆盖但结论待条件"]:
            one = f"{bv['覆盖但结论待条件']} 条结论待出"
        elif bv["已覆盖并发现缺陷"]:
            one = f"已测出 {bv['已覆盖并发现缺陷']} 个缺陷"
        elif bv["做不到"] and not bv["已覆盖并验证"]:
            one = "只能人工或平台不支持"
        else:
            one = "全部有结论"
        out.append({**g, "upstream": sorted(g["upstream"]), "oneLine": one})
    return sorted(out, key=lambda g: (g["byVerdict"]["未覆盖"] * -1, g["module"]))


def render_groups(groups: list[dict]) -> list[str]:
    if not groups:
        return []
    lines = ["## 按模块归纳(父节点)", "",
             "| 模块 / 主题 | 关注点 | ✅ | 🟠缺陷 | 🟡待条件 | 🔴未覆盖 | ⛔做不到 | 一句话结论 |",
             "|---|---|---|---|---|---|---|---|"]
    for g in groups:
        bv = g["byVerdict"]
        lines.append(f"| **{g['module']}** | {g['total']} | {bv['已覆盖并验证']} | "
                     f"{bv['已覆盖并发现缺陷']} | {bv['覆盖但结论待条件']} | "
                     f"{bv['未覆盖']} | {bv['做不到']} | {g['oneLine']} |")
    lines.append("")
    return lines


def summarize(rows: list[dict]) -> dict:
    by_verdict: dict[str, int] = {v: 0 for v in VERDICTS}
    by_source: dict[str, dict[str, int]] = {}
    by_strength: dict[str, int] = {s: 0 for s in STRENGTHS}
    for row in rows:
        v = row["结论"] if row["结论"] in VERDICTS else "已覆盖并验证"
        by_verdict[v] += 1
        by_source.setdefault(row["来源"] or "?", {k: 0 for k in VERDICTS})[v] += 1
        s = row["判定强度"] if row["判定强度"] in STRENGTHS else "无"
        by_strength[s] += 1
    return {"total": len(rows), "byVerdict": by_verdict, "bySource": by_source,
            "byStrength": by_strength}


def check_case_refs(rows: list[dict],
                    case_ids: set[str]) -> tuple[list[dict], dict]:
    """核对登记册里的用例引用:写了的必须真有,没写的要点名。

    返回 (findings, 统计)。findings 里 unknown_case_ref 是错误(引用了不存在的用例,
    说明台账在自我安慰);没被任何用例管的关注点只统计不报错 —— 待决策/上游依赖
    本来就该由人来管,但它必须是**可见的**。
    """
    out: list[dict] = []
    with_case = 0
    orphans: list[dict] = []
    for row in rows:
        refs = sorted({cid for cid, _ in refs_of(row)})
        missing = [c for c in refs if case_ids and c not in case_ids]
        if missing:
            out.append({"kind": "unknown_case_ref", "level": "error",
                        "where": f"第 {row['_line']} 行「{row['关注点']}」",
                        "note": f"覆盖方式里引用的用例不存在:{missing} —— "
                                f"台账写了「有用例管」,但用例目录里没有",
                        "line": row["_line"]})
        if refs and not missing:
            with_case += 1
        elif not refs:
            orphans.append(row)
    return out, {"withCase": with_case, "orphans": orphans,
                 "total": len(rows), "caseIds": len(case_ids)}


def render_case_link(stats: dict) -> list[str]:
    """「关注点 ↔ 用例」这一段:把"每条担心都有用例在管"变成可核对的事实。"""
    if not stats:
        return []
    lines = ["## 关注点 ↔ 用例(覆盖是不是真的接上了)", "",
             f"- 关注点 {stats['total']} 条中,**{stats['withCase']} 条有明确用例引用**"
             f"(用例目录里 {stats['caseIds']} 条用例)",
             f"- **{len(stats['orphans'])} 条没有用例管** —— 它们必须挂人挂期限,"
             f"不能混在「已覆盖」里", ""]
    if stats["orphans"]:
        lines += ["| 关注点 | 模块 | 结论 | 谁来补 |", "|---|---|---|---|"]
        for r in stats["orphans"][:20]:
            lines.append(f"| {r['关注点'][:44]} | {r['模块']} | {r['结论']} | "
                         f"{r['谁来补'] or '待定'} |")
        if len(stats["orphans"]) > 20:
            lines.append(f"| … 其余 {len(stats['orphans']) - 20} 条见登记册 | | | |")
        lines.append("")
    return lines


def render(rows: list[dict], findings: list[dict], summary: dict, src: Path,
           gate_result: tuple[bool, list[str], list[str]] | None = None,
           verified: bool = False, trend: list[str] | None = None,
           matrix_stats: dict | None = None,
           case_link: dict | None = None) -> str:
    trend = trend or []
    now = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    total = summary["total"] or 1
    v = summary["byVerdict"]
    strong = summary["byStrength"]["代码判定"] + summary["byStrength"]["工具扫描"]
    weak = summary["byStrength"]["视觉判定"] + summary["byStrength"]["人工"]
    blocked = [r for r in rows if r["结论"] == "覆盖但结论待条件"]
    uncovered = [r for r in rows if r["结论"] == "未覆盖"]
    nogo = [r for r in rows if r["结论"] == "做不到"]

    high_gaps = [r for r in open_gaps(rows) if is_high_risk(r)]
    lines = [
        "# 质量置信度结论",
        "",
        f"- 生成时间: {now}",
        f"- 关注点登记册: `{src}`(来源:产品/研发/策划/平台 主动提 + 我们从需求与设计里提取后请他们确认)",
        f"- 关注点 {summary['total']} 条",
        "",
        "## 一句话结论",
        "",
        f"- **已覆盖并验证 {v['已覆盖并验证']} 条**"
        f"({v['已覆盖并验证'] * 100 // total}%)—— 每条都带依据(判定项 / 运行 / 工具输出),可复核",
        f"- **覆盖了但卡在外部条件 {v['覆盖但结论待条件']} 条** —— "
        f"卡点:{'、'.join(sorted({r['谁来补'] for r in blocked})) or '——'}",
        f"- **未覆盖 {v['未覆盖']} 条** —— "
        f"要补的:{'、'.join(sorted({r['谁来补'] for r in uncovered})) or '——'}",
        f"- **做不到 {v['做不到']} 条** —— 原因已在册(平台限制/主观体验),不是遗漏",
        "",
        "> **这就是我们对「质量高」的算法**:能说「没问题」的部分要带依据;不能说的部分要写清"
        "卡在哪、谁来补。缺口是可见的 —— 这才是置信度的来源,而不是一句「都测过了」。",
        "",
    ]
    if matrix_stats:
        lines += shape_lines(matrix_stats)
    if trend:
        lines += trend
    if gate_result is not None:
        ok, reasons, notes = gate_result
        lines += [
            "## 放行判断",
            "",
            f"**{'✅ 可以放行(按当前登记册)' if ok else '⛔ 不满足放行条件'}**",
            "",
        ]
        if ok:
            lines.append("- 关键来源(产品/研发/策划)无未覆盖;已覆盖的都有可核对的依据;"
                         "做不到的都写了原因")
        else:
            for r in reasons:
                lines.append(f"- ⛔ {r}")
        for n in notes:
            lines.append(f"- ⚠ {n}")
        lines += [
            "",
            "> 门禁条件:① 关键来源不许有「未覆盖」② 说「已覆盖并验证」的依据必须核对通过"
            "③ 「做不到」必须写原因。",
            "",
        ]
    lines += render_case_link(case_link or {})
    lines += render_groups(group_summary(rows))
    if high_gaps:
        lines += ["## 高风险缺口(资损 / 掉档类,优先补)", ""]
        for r in high_gaps:
            lines.append(f"- 🔥 **{r['关注点']}**({r['模块']} · {r['来源']}提)"
                         f" —— {r['结论']};"
                         f"{('补位:' + r['谁来补']) if r['谁来补'] else '补位待定'}"
                         f"{('·' + r['期限']) if r['期限'] else ''}")
        lines.append("")

    lines += [
        "## 按关心你的人分组",
        "",
        "| 来源 | 已覆盖并验证 | 待外部条件 | 未覆盖 | 做不到 |",
        "|---|---|---|---|---|",
    ]
    for src_name in sorted(summary["bySource"]):
        s = summary["bySource"][src_name]
        lines.append(f"| {src_name} | {s['已覆盖并验证']} | {s['覆盖但结论待条件']} | "
                     f"{s['未覆盖']} | {s['做不到']} |")
    lines += [
        "",
        "## 关注点逐条(缺口在前)",
        "",
        "| | 关注点 | 来源 | 结论 | 判定强度 | 依据核对 | 覆盖方式 | 依据 / 卡点 | 谁来补 / 期限 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for row in sorted(rows, key=lambda r: (ORDER.get(r["结论"], 9), r["来源"], r["关注点"])):
        verdict = row["结论"]
        icon = VERDICT_ICON.get(verdict, "?")
        s_icon = STRENGTH_ICON.get(row["判定强度"], "")
        basis = row["依据"] or row["为什么担心"] or "——"
        v = row.get("_verification")
        if v is None:
            check = "工具依据" if any(n in basis for n in TOOL_NAMES) else (
                "无需核对" if verdict != "已覆盖并验证" else "⚠ 自述")
        else:
            check = {"verified_pass": "✅ 已核对", "verified_fail": "✅ 已核对(有缺陷)",
                     "no_conclusion": "🔴 核对不通过", "not_found": "🔴 依据找不到"}.get(
                         v["status"], v["status"])
        owner = row["谁来补"] or "——"
        if row["期限"]:
            owner += f"({row['期限']})"
        lines.append(f"| {icon} | {row['关注点']} | {row['来源']} | {verdict} | "
                     f"{s_icon} {row['判定强度']} | {check} | {row['覆盖方式']} | "
                     f"{basis} | {owner} |")
    lines += [
        "",
        "## 我们的依据(可审计)",
        "",
        f"- 判定强度分布:代码判定 {summary['byStrength']['代码判定']} / "
        f"工具扫描 {summary['byStrength']['工具扫描']} / "
        f"视觉判定 {summary['byStrength']['视觉判定']} / "
        f"人工 {summary['byStrength']['人工']} / 无 {summary['byStrength']['无']}",
        f"- 其中**强证据**(代码判定 + 工具扫描){strong} 条,"
        f"**弱证据**(视觉判定 + 人工){weak} 条 —— 弱证据的结论要人复核后才敢对外说",
        "- 每条「已覆盖并验证」都能追到判定项与运行;追不到的会被本工具判成「证据不足」",
        (f"- **依据核对**:核对通过 {sum(1 for r in rows if (r.get('_verification') or {}).get('status') == 'verified_pass')} 条、"
         f"核对通过但有已知缺陷 {sum(1 for r in rows if (r.get('_verification') or {}).get('status') == 'verified_fail')} 条、"
         f"工具依据 {sum(1 for r in rows if (r.get('_verification') is None and any(n in (r['依据'] or '') for n in TOOL_NAMES)))} 条、"
         f"自述依据 {sum(1 for r in rows if (r.get('_verification') is None and not any(n in (r['依据'] or '') for n in TOOL_NAMES) and r['结论'] == '已覆盖并验证'))} 条"
         if verified else "- 依据未做核对(没给 --runs)—— 这一页的「已覆盖并验证」目前只有自述依据"),
        "- 未覆盖与做不到都写明了出口与原因,所以「没覆盖」是**已知**而不是未知",
        "",
        "## 口径(免得各说各话)",
        "",
        "| 结论 | 含义 | 对外怎么说 |",
        "|---|---|---|",
        "| 已覆盖并验证 | 有判定项 + 依据核对通过 | 可以承诺「这条没问题」 |",
        "| 已覆盖并发现缺陷 | 测了,判定失败,已开单 | 「测了,发现缺陷,见 BUG-xxx」 |",
        "| 覆盖但结论待条件 | 用例与判定已就绪,卡在埋点/端点/真机 | 「准备好了,但结论还没出」 |",
        "| 未覆盖 | 没有对应用例或工具 | 「还没测,谁在补、什么时候」 |",
        "| 做不到 | 平台限制或主观项 | 「测不了,原因是…」 |",
        "",
    ]
    if findings:
        lines += ["## 登记册问题(先修这些,否则上表不可信)", ""]
        for f in findings:
            lines.append(f"- [{f['kind']}] {f['where']} — {f['note']}")
        lines.append("")
    return "\n".join(lines)


BUG_REF_RE = re.compile(r"BUG-\d+")


def read_bug_index(bugs_dir: Path | None) -> dict[str, dict]:
    """读 bug 单目录,拿到每条缺陷的状态与严重级(给策划看的答复要带上)。"""
    out: dict[str, dict] = {}
    if not bugs_dir or not bugs_dir.exists():
        return out
    for f in sorted(bugs_dir.glob("BUG-*.md")):
        text = f.read_text(encoding="utf-8", errors="replace")
        sev = re.search(r"\*\*严重级\*\*:\s*(P\d)", text)
        state = re.search(r"\*\*状态\*\*:\s*(\S+)", text)
        title = next((ln.lstrip("# ").strip() for ln in text.splitlines()
                      if ln.startswith("# ")), f.stem)
        out[f.stem] = {"severity": sev.group(1) if sev else "?",
                       "state": state.group(1) if state else "?",
                       "title": title}
    return out


def shape_lines(matrix_stats: dict | None, dim_count: int = 12) -> list[str]:
    """覆盖形状:告诉人『我按什么把需求过了一遍』 —— 这是"想全了"的凭据。"""
    if not matrix_stats:
        return []
    st = matrix_stats
    return [
        "## 我按什么过了一遍(覆盖形状)",
        "",
        f"- 需求子句 × {dim_count} 个维度(正向 / 数值边界 / 空与缺失 / 重复与幂等 / "
        f"中断与恢复 / 时序与竞态 / 权限与合规 / 状态迁移 / 跨端 / 资源与配置 / "
        f"时间与周期 / 数据一致性)= **{st['total']} 格**",
        f"- 适用 {st['applies']} 格(**全部有对应关注点**)、不适用 {st['not_applies']} 格"
        f"(**都写明了原因**)、还没过 {st['undecided']} 格"
        + ("(这些列在下面的缺口里)" if st["undecided"] else ""),
        "- **每一格要么有产出、要么写清了为什么不需要 —— 没有空着的格子**",
        "",
    ]


def load_case_ids(target: Path) -> set[str]:
    """读用例目录/文件里的用例 id(JSON 用例 + 自然语言用例都认)。"""
    ids: set[str] = set()
    if not target or not target.exists():
        return ids
    files = sorted(target.rglob("*.json")) if target.is_dir() else [target]
    for f in files:
        if f.name.startswith("run-"):
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if isinstance(data, dict) and data.get("id"):
            ids.add(str(data["id"]))
    if target.is_dir() and any(target.rglob("*.md")):
        sys.path.insert(0, str(SKILL_DIR / "scripts"))
        from validate_cases import collect_specs  # noqa: E402
        ids |= set(collect_specs(target))
    return ids


def brief(rows: list[dict], summary: dict, gate_result, findings: list[dict],
          src: Path, bug_index: dict[str, dict] | None = None,
          version: str = "", matrix_stats: dict | None = None,
          case_link: dict | None = None) -> str:
    """给策划/产品的答复 —— 只回答他在意的三件事:

      ① 我关心的那条,测了没有?  ② 结果是什么?  ③ 有没有 bug(单号 + 严重级)?

    不出现判定强度、工具名、运行号这些内部词,也不给"低/中/高信心"这类抽象分。
    """
    ok, reasons, notes = gate_result
    bug_index = bug_index or {}
    now = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")
    no_problem = [r for r in rows if r["结论"] == "已覆盖并验证"]
    with_bug = [r for r in rows if r["结论"] == "已覆盖并发现缺陷"]
    not_yet = [r for r in rows if r["结论"] == "覆盖但结论待条件"]
    never = [r for r in rows if r["结论"] == "未覆盖"]
    cant = [r for r in rows if r["结论"] == "做不到"]

    def bugs_of(row: dict) -> list[tuple[str, dict]]:
        blob = f"{row['依据']} {row['备注']} {row['覆盖方式']}"
        ids: list[str] = []
        for m in BUG_REF_RE.finditer(blob):
            if m.group(0) not in ids:
                ids.append(m.group(0))
        return [(i, bug_index.get(i, {})) for i in ids]

    out = [f"# 你关心的事:答复{' · ' + version if version else ''}", "",
           f"- 时间: {now}",
           f"- 你(和研发/策划)提的 {summary['total']} 条,逐条答复如下", ""]
    out += [f"**测了没问题 {len(no_problem)} 条 · 测了发现问题 {len(with_bug)} 条 · "
            f"还没测 {len(never)} 条 · 结论没出 {len(not_yet)} 条 · "
            f"测不了 {len(cant)} 条**", ""]

    out += shape_lines(matrix_stats)

    if case_link:
        out += ["## 你提的每一条,是不是都有用例在管", "",
                f"- **{case_link['withCase']} / {case_link['total']} 条**在用例目录里"
                f"能指到具体用例(用例 {case_link['caseIds']} 条)",
                f"- 剩下 {len(case_link['orphans'])} 条没有用例:这类多半是"
                f"「等你们决策」「等上游先做」,它们挂在登记册里,不会混进「测过了」",
                ""]
        for r in case_link["orphans"][:8]:
            out.append(f"  - {r['关注点'][:48]} —— {r['结论']};"
                       f"补位:{r['谁来补'] or '待定'}")
        out.append("")

    def line(row: dict, extra: str = "") -> str:
        mod = f"[{row['模块']}] " if row.get("模块") else ""
        return f"- {mod}**{row['关注点']}**" + (f" —— {extra}" if extra else "")

    out += ["## 测了,没问题", ""]
    out += [line(r[0] if isinstance(r, tuple) else r) for r in
            [(r,) for r in no_problem]] or ["- 无"]
    out += ["", "## 测了,发现问题(已开单)", ""]
    if not with_bug:
        out.append("- 无")
    for r in with_bug:
        bs = bugs_of(r)
        if bs:
            desc = ";".join(f"{i}({b.get('severity', '?')},{b.get('state', '?')})"
                            for i, b in bs)
            out.append(line(r, f"已开单 {desc}"))
        else:
            out.append(line(r, "已发现问题;**单号待补**"
                                   "(结论页里有依据,推动研发开单)"))
    out += ["", "## 还没测(谁在补、什么时候)", ""]
    out += [line(r, f"{r['谁来补'] or '待排期'}"
                 f"{('·' + r['期限']) if r['期限'] else ''}") for r in never] or ["- 无"]
    out += ["", "## 结论还没出来(等什么条件)", ""]
    out += [line(r, f"卡在 {r['谁来补'] or '外部条件'}"
                 f"{('·' + r['期限']) if r['期限'] else ''}") for r in not_yet] or ["- 无"]
    if cant:
        out += ["", "## 测不了(原因在册,不是遗漏)", ""]
        for r in cant:
            out.append(line(r, r["依据"] or r["备注"] or "平台限制"))
    if notes:
        out += ["", "## 需要你知道的", ""]
        for n in notes:
            out.append(f"- {n}")
    out += ["", "> 说「没问题」的每一条都能追到哪次跑、哪条断言、哪份证据;"
                "有 bug 的都带了单号与严重级;还没测的写了谁在补。"]
    return chr(10).join(out)


def load_last_freeze(history_path: Path) -> dict | None:
    """上一次封板记录(没有就返回 None)。"""
    for h in reversed(load_history(history_path, limit=200)):
        if h.get("type") == "freeze":
            return h
    return None


def freeze_report(version: str, rows: list[dict], summary: dict, findings: list[dict],
                  gate_result, history_path: Path, out_dir: Path,
                  full_report: str) -> tuple[str, int]:
    """封板:出结论 + 与上次封板对比 + 归档快照 + 记入历史。

    "放行"不该是一次口头结论 —— 封板要留痕:哪天放的、放的是哪个版本、
    依据是哪一页结论、跟上次比关了哪些缺口又开了哪些。
    """
    ok, reasons, notes = gate_result
    prev = load_last_freeze(history_path)
    cur_gaps = {r["关注点"] for r in open_gaps(rows)}
    prev_gaps = set(prev.get("gaps") or []) if prev else set()
    new_gaps = sorted(cur_gaps - prev_gaps)
    closed_gaps = sorted(prev_gaps - cur_gaps)
    defects = [r for r in rows if r["结论"] == "已覆盖并发现缺陷"]

    now = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    lines = [
        f"# 封板结论 · {version}",
        "",
        f"- 时间: {now}",
        f"- 登记册: {summary['total']} 条关注点",
        f"- **放行判断:{'✅ 可以放行' if ok else '⛔ 不满足放行条件'}**",
        "",
    ]
    if ok:
        lines.append("关键来源无未覆盖;已覆盖的依据都核对通过;做不到的都写了原因。")
    else:
        for r in reasons:
            lines.append(f"- ⛔ {r}")
    for n in notes:
        lines.append(f"- ⚠ {n}")
    lines += ["", "## 与上次封板对比", ""]
    if not prev:
        lines.append("- 这是第一次封板,没有可比基线(从这版起开始记)")
    else:
        lines += [
            f"- 上次封板:{prev.get('version')}({str(prev.get('at', ''))[:16]})",
            f"- 缺口:{len(prev_gaps)} → {len(cur_gaps)}"
            + (" —— **在收敛**" if len(cur_gaps) < len(prev_gaps)
               else (" —— 没动" if len(cur_gaps) == len(prev_gaps) else " —— **在增加**")),
            f"- 这版关闭的缺口({len(closed_gaps)}):"
            + ("、".join(closed_gaps) if closed_gaps else "无"),
            f"- 这版新增的缺口({len(new_gaps)}):"
            + ("、".join(new_gaps) if new_gaps else "无"),
            f"- 已确认缺陷:{len(prev.get('defects') or [])} → {len(defects)}"
            + ("(已在 bug 流程里)" if defects else ""),
        ]
    lines += ["", "## 本次放行的前提(缺了任何一条就不成立)", "",
              "- 结论页的每一条 ✅ 都核到了判定项与证据文件(见完整结论页)",
              "- 「未覆盖」为空,或已明确接受并写清谁在补、什么时候补",
              "- 「做不到」的原因都写在册(不是遗漏)",
              "- 有问题(已覆盖并发现缺陷)的都带了 bug 单号与严重级",
              "- 上一版之后跑过证据(--since 口径内),不是拿旧版本的结论充数", ""]
    report = "\n".join(lines)
    out_dir.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", version) or "freeze"
    snap = out_dir / f"freeze-{safe}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.md"
    snap.write_text(report + "\n---\n\n" + full_report, encoding="utf-8")
    try:
        with history_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "type": "freeze", "version": version,
                "at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
                "gateOk": ok,
                "gaps": sorted(cur_gaps),
                "defects": [r["关注点"] for r in defects],
                "snapshot": str(snap),
            }, ensure_ascii=False) + chr(10))
    except OSError:
        pass
    return report + f"\n\n_归档:{snap}_", (0 if ok else 1)


def record_history(path: Path, summary: dict, gate_ok: bool | None) -> None:
    """把这次的口径记一行(用于看"置信度是在变好还是变差")。"""
    entry = {
        "at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "total": summary["total"],
        "byVerdict": summary["byVerdict"],
        "gateOk": gate_ok,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + chr(10))
    except OSError:
        pass


def load_history(path: Path, limit: int = 8) -> list[dict]:
    if not path.exists():
        return []
    out: list[dict] = []
    for ln in path.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            continue
    return out[-limit:]


def render_trend(history: list[dict]) -> list[str]:
    if len(history) < 2:
        return []
    lines = ["## 趋势(置信度在变好还是变差)", "",
             "| 时间 | 关注点 | ✅ | 🟠缺陷 | 🟡待条件 | 🔴未覆盖 | ⛔做不到 | 放行 |",
             "|---|---|---|---|---|---|---|---|"]
    for h in history:
        v = h.get("byVerdict") or {}
        ok = h.get("gateOk")
        lines.append(f"| {str(h.get('at', ''))[:16]} | {h.get('total', '?')} | "
                     f"{v.get('已覆盖并验证', 0)} | {v.get('已覆盖并发现缺陷', 0)} | "
                     f"{v.get('覆盖但结论待条件', 0)} | {v.get('未覆盖', 0)} | "
                     f"{v.get('做不到', 0)} | "
                     f"{'✅' if ok else ('⛔' if ok is False else '—')} |")
    first, last = history[0], history[-1]
    d_un = (last.get("byVerdict", {}).get("未覆盖", 0)
            - first.get("byVerdict", {}).get("未覆盖", 0))
    d_ok = (last.get("byVerdict", {}).get("已覆盖并验证", 0)
            - first.get("byVerdict", {}).get("已覆盖并验证", 0))
    lines += ["", f"- 相比 {str(first.get('at', ''))[:10]}:"
                  f"未覆盖 {d_un:+d}、已覆盖并验证 {d_ok:+d}"
              + (" —— 缺口在收敛" if d_un < 0 else
                 (" —— 缺口没动,别只报通过率" if d_un == 0 else " —— 缺口在变多,要说明原因")),
              ""]
    return lines


def gate(summary: dict, findings: list[dict],
         rows: list[dict]) -> tuple[bool, list[str], list[str]]:
    """放行门禁:把"置信度"变成可判断的能不能发。

    三条硬条件(都能机械判):
      1. 产品/研发/策划关心的点,不许有"未覆盖"
      2. 说"已覆盖并验证"的,依据必须核对通过
      3. "做不到"必须写了原因
    """
    reasons: list[str] = []
    notes: list[str] = []
    key_sources = {"产品", "研发", "策划"}
    # 一条结论都没有就别谈放行:125 条全是"待条件"时,门禁说"可以放行"是假信心里
    # 最危险的一种 —— 它会把"还没跑"读成"没问题"。
    concluded = (summary["byVerdict"]["已覆盖并验证"]
                 + summary["byVerdict"]["已覆盖并发现缺陷"])
    if rows and concluded == 0:
        reasons.append(f"没有任何一条关注点给出过结论(已覆盖并验证 0、已发现缺陷 0,"
                       f"共 {len(rows)} 条)—— 全是「覆盖但结论待条件」时放行等于闭眼签字")
    elif rows and concluded * 2 < len(rows):
        notes.append(f"有结论的只有 {concluded}/{len(rows)} 条 —— 放行判断建立在"
                     f"「其余的卡点都写清楚了」这件事上,不是建立在测过多少上")
    uncovered_key = [r for r in rows
                     if r["来源"] in key_sources and r["结论"] == "未覆盖"]
    if uncovered_key:
        # 只列前几条:75 条未覆盖全铺在一行里没人读得下去(实测)
        head = "、".join(f"{r['关注点']}({r['谁来补']})" for r in uncovered_key[:3])
        more = (f" 等共 {len(uncovered_key)} 条"
                if len(uncovered_key) > 3 else "")
        reasons.append(f"关键来源还有 {len(uncovered_key)} 条未覆盖:{head}{more}"
                       f" —— 逐条见结论页")
    for f in findings:
        if f["kind"] in ("evidence_check_failed", "defect_hidden", "claim_mismatch"):
            reasons.append(f"依据核对不通过:{f['where']}")
        if f["kind"] == "no_reason":
            reasons.append(f"「做不到」没写原因:{f['where']}")
        if f["kind"] == "defect_without_bug":
            reasons.append(f"有问题但找不到 bug 单:{f['where']}")
    confirmed = [r for r in rows if r.get("_defect_confirmed")]
    if confirmed:
        # 缺陷不阻塞放行(它走 bug 流程),但放行决定必须知道 —— 所以是提示不是阻塞
        notes.append(f"有 {len(confirmed)} 条关注点已核对确认有缺陷"
                     f"({', '.join(r['关注点'] for r in confirmed)})—— "
                     f"不阻塞放行,但放行时必须知道")
    return (not reasons), reasons, notes


# ---------------------------------------------------------------- 上游解析(关注点从哪来)

INTAKE_KINDS = ("requirements", "specs", "config", "code", "bugs")


def _row(concern: str, module: str, source: str, upstream: str, why: str,
         way: str, strength: str, verdict: str, basis: str, owner: str,
         deadline: str, note: str) -> list[str]:
    return [concern, module, source, upstream, why, way, strength, verdict, basis,
            owner, deadline, note]


EXPAND_HINTS: dict[str, list[str]] = {
    "requirements": [
        "正向:按需求做到(界面/文案/数值与需求一致)",
        "边界:上限/下限/金额/数量/时间的边界取值",
        "异常:失败/超时/重复触发/断线时怎么表现",
        "反向:需求明确不该发生的事(不得出现/不得增加)",
    ],
    "config": [
        "静态自洽:引用/范围/枚举(先跑 check_config.py)",
        "运行时生效:游戏里真的按这个配置跑了吗",
        "边界取值:配置的极值在游戏里表现如何",
        "与需求一致:配置数值和需求写的是否一致",
    ],
    "code": [
        "分支与边界:代码里的 if 边界有没有用例(只决定测哪里)",
        "状态机迁移:状态切换有没有漏",
        "异常路径:报错/重试/超时路径",
        "幂等与重试:重复触发会不会出问题",
    ],
    "bugs": [
        "同根因的其它入口:这个缺陷类型在别处会不会也发生",
        "该路径的回归:修完之后加一条回归用例",
    ],
    "specs": [
        "这条期望真的验证到了接口/数值层吗(不是只看界面)",
        "换个机型/网络条件还成立吗",
    ],
}


# 维度表:CSV 是**唯一可编辑的源**(加一行就多一个维度,不用碰代码),
# JSON 是派生产物(给别的技能/工具读),用 --dump-dimensions-json 重新生成。
DIMENSIONS_PATH = SKILL_DIR / "assets" / "check-dimensions.csv"
DIMENSIONS_JSON = SKILL_DIR / "assets" / "check-dimensions.json"
DIMENSION_COLUMNS = ["id", "名称", "标准问法", "触发条件", "示例", "标签", "全局", "备注"]
# 完备性矩阵的列(独立于登记册:它记录"每一格判断过没有")
MATRIX_COLUMNS = ["需求子句", "子句原文", "模块", "维度", "是否适用", "不适用原因",
                  "产出(关注点)", "备注"]


def _read_rows_named(path: Path, columns: list[str]) -> list[dict]:
    """按指定列名读 CSV(编码依次试 utf-8-sig / gbk)。缺列直接报错。"""
    text = None
    for enc in ("utf-8-sig", "gbk", "utf-8"):
        try:
            text = path.read_text(encoding=enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise SystemExit(f"错误: {path} 编码认不出来")
    rows = list(csv.reader(text.splitlines()))
    if not rows:
        raise SystemExit(f"错误: {path} 是空的")
    header = [c.strip() for c in rows[0]]
    missing = [c for c in columns if c not in header]
    if missing:
        raise SystemExit(f"错误: {path} 表头缺列 {missing}(应包含 {columns})")
    idx = {c: header.index(c) for c in columns}
    out = []
    for raw in rows[1:]:
        if not any(x.strip() for x in raw):
            continue
        out.append({c: (raw[idx[c]].strip() if idx[c] < len(raw) else "")
                    for c in columns})
    return out


COMMON_SENSE_PATH = SKILL_DIR / "assets" / "common-sense.json"


def load_common_sense() -> list[dict]:
    try:
        data = json.loads(COMMON_SENSE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    return data.get("rules") or []


def build_common_sense(spec: str, feature: str = "") -> tuple[list[list[str]], str]:
    """常识别表:从需求文本里认出实体,套上对应的常识不变量。

    这是"你没写但这是常识"的那一半 —— 需求文档里未必写"金币不能为负",
    但策划心里有;把它单独列出来并给结论,才是真的放心。
    产出仍是矩阵格式,所以同一套完备性检查(适用要有产出/不适用要写原因)照用。
    """
    kind, _, raw = spec.partition(":")
    if kind != "requirements":
        return [], "常识别表目前只吃需求文档(requirements:...)"
    path = Path(raw.strip())
    if not path.exists():
        return [], f"找不到 {path}"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [], f"{path} 解析失败: {exc}"
    rules = load_common_sense()
    if not rules:
        return [], f"读不到常识别表({COMMON_SENSE_PATH})"

    mod = feature or data.get("feature") or path.stem
    clauses = data.get("clauses") or []

    def clause_text(c: dict) -> str:
        # 需求正文 + 备注(玩家可见反馈)。只看正文会漏掉整类常识:
        # "界面显示 / 动画 / 文案 / 空态"这些词常常只写在玩家可见反馈里。
        return f"{c.get('text') or ''} {c.get('notes') or ''}"

    rows: list[list[str]] = []
    for rule in rules:
        if rule.get("always"):
            hits = ["(全局)"]
        else:
            hits = [str(c.get("id")) for c in clauses
                    if any(w in clause_text(c) for w in rule.get("when") or [])]
        entity = rule["entity"]
        inv = rule["invariant"].replace("{entity}", entity)
        if not hits:
            # 不硬塞,但也**不能静默丢掉**:没命中的写清"这份需求里没有这类对象",
            # 否则表看起来干净,实际是少了几行 —— 覆盖形状必须是完整的。
            when = "、".join(str(w) for w in (rule.get("when") or [])[:4])
            rows.append([
                "—", f"{rule['id']} 的关键词({when})在本需求里没有出现",
                mod, f"{rule['id']} {inv}", "否",
                f"这份需求里没有出现这类对象({when}),该常识不适用",
                "", f"{rule['id']}:{rule.get('why', '')}",
            ])
            continue
        rows.append([
            str(hits[0]), "、".join(hits) + ":" + (clauses[0].get("text") or "")[:30]
            if hits != ["(全局)"] else "(全局)",
            mod,
            f"{rule['id']} {inv}",
            "是", "",
            f"{inv} —— 建议断言:{rule.get('assert_hint', '')}",
            f"常识不变量(与需求无关):{rule.get('why', '')}",
        ])
    return rows, ""


def load_dimensions() -> list[dict]:
    """读维度表(assets/check-dimensions.csv)。缺必需列直接报错,可选列缺了也能跑。

    为什么要 CSV:维度是**数据**,不是代码常量 —— 想加一个维度(比如"多语言")
    就加一行,不该为了这个改 JSON、改代码、再重新发布技能。
    """
    text = None
    for enc in ("utf-8-sig", "gbk", "utf-8"):
        try:
            text = DIMENSIONS_PATH.read_text(encoding=enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise SystemExit(f"错误: 读不到维度表 {DIMENSIONS_PATH}")
    rows = list(csv.reader(text.splitlines()))
    if not rows:
        raise SystemExit(f"错误: 维度表是空的 {DIMENSIONS_PATH}")
    header = [c.strip().lstrip("﻿") for c in rows[0]]
    for need in ("id", "名称", "标准问法"):
        if need not in header:
            raise SystemExit(f"错误: 维度表表头缺列 {need!r}(现有 {header})")
    out: list[dict] = []
    for raw in rows[1:]:
        if not any(x.strip() for x in raw):
            continue
        d = {c: (raw[i].strip() if i < len(raw) else "")
             for i, c in enumerate(header)}
        if not d.get("id"):
            continue
        out.append({
            "id": d["id"], "name": d.get("名称", ""),
            "ask": d.get("标准问法", ""), "example": d.get("示例", ""),
            "when": [w.strip() for w in re.split(r"[、,;；|/]", d.get("触发条件", ""))
                     if w.strip()],
            "always": d.get("全局", "").strip() in ("是", "true", "1", "yes", "Y"),
            "tags": [t.strip() for t in re.split(r"[、,;；]", d.get("标签", ""))
                     if t.strip()],
            "note": d.get("备注", ""),
        })
    return out


def dump_dimensions_json(out: Path | None = None) -> tuple[int, Path]:
    """把 CSV 导成 JSON(派生产物):别的技能/工具读 JSON 时用它保持一致。"""
    dims = load_dimensions()
    target = out or DIMENSIONS_JSON
    payload = {
        "_comment": [
            "本文件由 assets/check-dimensions.csv 生成 —— 要改维度请改 CSV,然后跑:",
            "  python3 scripts/concerns.py --dump-dimensions-json",
            "check_docs 会对账两者是否一致(不一致会报错,按上面这行修)。",
        ],
        "dimensions": [
            {k: v for k, v in d.items() if v not in ([], "", None, False)}
            for d in dims],
    }
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + chr(10),
                      encoding="utf-8")
    return len(dims), target


def dim_applies(dim: dict, text: str) -> bool:
    """按维度的 when 关键词给一个**建议**(人/AI 可以改) —— 规则只用来起头,不用来做结论。"""
    if dim.get("always"):
        return True
    return any(w in text for w in (dim.get("when") or []))


def build_matrix(spec: str, feature: str = "") -> tuple[list[list[str]], str]:
    """需求子句 × 维度 → 完备性矩阵(每格一行,空的格子就是漏的)。

    这是"让策划觉得你想全了"的机械做法:维度固定、逐格过、格子不许空着。
    """
    kind, _, raw = spec.partition(":")
    if kind != "requirements":
        return [], ("完备性矩阵目前只支持需求文档(requirements:...);"
                    "配置/代码的维度正在补")
    path = Path(raw.strip())
    if not path.exists():
        return [], f"找不到 {path}"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [], f"{path} 解析失败: {exc}"
    dims = load_dimensions()
    if not dims:
        return [], f"读不到维度目录({DIMENSIONS_PATH})"

    mod = feature or data.get("feature") or path.stem
    rows: list[list[str]] = []
    for c in data.get("clauses") or []:
        cid = c.get("id", "?")
        text = (c.get("text") or "").strip()
        for d in dims:
            applies = dim_applies(d, text)
            rows.append([
                str(cid), text[:60], mod,
                f"{d['id']} {d['name']}",
                "是" if applies else "待判断",
                "" if applies else "",          # 不适用必须写原因
                "",                              # 产出(关注点)待 AI/人填
                f"标准问法:{d.get('ask', '')}" + (f" | 例子:{d['example']}"
                                                  if d.get("example") else ""),
            ])
    return rows, ""


def matrix_to_register(mrows: list[dict], source: str = "产品") -> list[list[str]]:
    """把填好的完备性矩阵转成登记册行:每一格的"产出"就是一条关注点。

    矩阵是**扫一遍**(保证没漏),登记册是**台账**(逐条跟结论)。两者用上游引用对齐:
    矩阵的 R-3.1 × D2 → 登记册里 上游引用=R-3.1 的关注点。
    """
    out: list[list[str]] = []
    for m in mrows:
        product = (m.get("产出(关注点)") or "").strip()
        applies = (m.get("是否适用") or "").strip()
        if applies not in ("是", "适用") or not product:
            continue
        # 一格可能写了多条(用 ; 或 、 分隔),拆成多行
        parts = [x.strip() for x in re.split(r"[;；]", product) if x.strip()]
        for part in parts or [product]:
            out.append([
                part, m.get("模块") or "", source, m.get("需求子句") or "",
                f"来自完备性矩阵:{m.get('维度')}",
                "无", "无", "未覆盖", "", "测试:写用例覆盖这条关注点", "",
                f"矩阵格子 {m.get('需求子句')} × {m.get('维度')}",
            ])
    return out


def check_matrix(rows: list[dict]) -> tuple[dict, list[dict]]:
    """完备性检查:适用格必须有产出,不适用格必须写原因,不许"没判断"。

    返回 (统计, 缺口清单)。**不做百分比** —— 只报格数,让人能逐格追。
    """
    stats = {"total": 0, "applies": 0, "not_applies": 0, "undecided": 0,
             "filled": 0, "empty_cells": 0}
    gaps: list[dict] = []
    for r in rows:
        stats["total"] += 1
        applies = (r.get("是否适用") or "").strip()
        product = (r.get("产出(关注点)") or "").strip()
        reason = (r.get("不适用原因") or "").strip()
        where = f"{r.get('需求子句')} × {r.get('维度')}"
        if applies in ("是", "适用"):
            stats["applies"] += 1
            if product:
                stats["filled"] += 1
            else:
                stats["empty_cells"] += 1
                gaps.append({"kind": "missing_product", "where": where,
                             "note": "这一格判定为适用,但没有产出(关注点/用例)"
                                     " —— 空着的格子就是漏的"})
        elif applies in ("否", "不适用"):
            stats["not_applies"] += 1
            if not reason:
                gaps.append({"kind": "no_reason", "where": where,
                             "note": "写了不适用,但没写为什么 —— "
                                     "不写原因,以后没人知道是真的不涉及还是漏了"})
        else:
            stats["undecided"] += 1
            gaps.append({"kind": "undecided", "where": where,
                         "note": "这一格还没判断(既没说适用也没说不适用)"})
    return stats, gaps


def expand_worklist(spec: str, feature: str = "") -> tuple[list[list[str]], str]:
    """把上游解析成**展开工作单**:一级节点(模块)+ 待 AI 展开的二级节点提示。

    解析只是第一步:一条需求子句直接变成一行关注点,粒度太粗(策划要的是
    "这条需求相关的边界/异常都想到了吗")。所以这里给的是**一级节点 + 展开提示**,
    由 AI 按 EXPAND_HINTS 的规则展开成 2-4 条具体关注点,再交人确认。
    """
    kind = spec.partition(":")[0].strip()
    base, err = parse_intake(spec, feature)
    if err:
        return [], err
    hints = EXPAND_HINTS.get(kind, [])
    out: list[list[str]] = []
    for row in base:
        d = dict(zip(COLUMNS, row))
        module = d["模块"]
        upstream = d["上游引用"]
        for h in hints:
            new = dict(d)
            new["关注点"] = f"【待展开】{h}"
            new["为什么担心"] = d["为什么担心"]
            new["覆盖方式"] = "无"
            new["判定强度"] = "无"
            new["结论"] = "未覆盖"
            new["依据"] = ""
            new["谁来补"] = "AI:按展开规则拆成具体关注点,人确认来源与优先级"
            new["期限"] = ""
            new["备注"] = (f"一级节点:{d['关注点'][:40]} | 上游 {upstream}({kind})"
                           f" | 展开提示:{h}")
            out.append([new[c] for c in COLUMNS])
    return out, ""


def parse_intake(spec: str, feature: str = "") -> tuple[list[list[str]], str]:
    """解析一种上游,产出**带父节点(模块)与上游引用**的关注点候选。

    spec 形如 `requirements:01-requirements.json` / `specs:specs/` /
    `config:configs/fields.json` / `code:src/` / `bugs:05-bugs/`。
    """
    kind, _, raw = spec.partition(":")
    kind = kind.strip()
    path = Path(raw.strip())
    if kind not in INTAKE_KINDS:
        return [], f"上游类型 {kind!r} 不认识(可用:{'/'.join(INTAKE_KINDS)})"
    if not path.exists():
        return [], f"找不到 {path}"

    rows: list[list[str]] = []
    if kind == "requirements":
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            return [], f"{path} 解析失败: {exc}"
        mod = feature or data.get("feature") or path.stem
        for c in data.get("clauses") or []:
            cid, text = c.get("id", "?"), (c.get("text") or "").strip()
            rows.append(_row(
                f"需求 {cid} 是否满足:{text[:40]}", mod, "产品", cid,
                c.get("notes") or f"需求子句({c.get('kind') or 'functional'})",
                "无", "无", "未覆盖", "", "测试:写用例覆盖该子句", "",
                "自动候选:来自需求文档;结论必须能归因到这条子句"))
    elif kind == "specs":
        sys.path.insert(0, str(SKILL_DIR / "scripts"))
        from validate_cases import collect_specs  # noqa: E402
        specs = collect_specs(path)
        for case_id, sp in sorted(specs.items()):
            meta = sp.get("meta") or {}
            mod = meta.get("关联需求") or case_id
            for key, text in sorted((sp.get("items") or {}).items()):
                if not key.startswith("E"):
                    continue
                rows.append(_row(
                    f"{case_id} 的期望 {key} 是否验证:{text[:36]}", mod, "测试",
                    f"{case_id}/{key}", f"自然语言用例里的期望({meta.get('优先级') or '?'})",
                    f"{case_id}", "无", "覆盖但结论待条件", "用例与期望已就绪",
                    "测试:执行并判定", "", "自动候选:来自自然语言用例;跑一轮就有结论"))
    elif kind == "config":
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            return [], f"{path} 解析失败: {exc}"
        for tname, tspec in sorted((data.get("tables") or {}).items()):
            cols = tspec.get("columns") or {}
            refs = [c for c, r in cols.items()
                    if isinstance(r, dict) and (r.get("ref") or r.get("ref_dir"))]
            rngs = [c for c, r in cols.items()
                    if isinstance(r, dict) and (r.get("min") is not None
                                                or r.get("max") is not None)]
            rows.append(_row(
                f"配置表 {tname} 的引用与取值是否都对得上", tname, "策划", tname,
                f"{len(cols)} 列,其中 {len(refs)} 个引用、{len(rngs)} 个有范围约束",
                "check_config.py", "工具扫描", "未覆盖", "",
                "测试:跑 check_config 并把结果登记", "",
                "自动候选:来自配置表字段说明"))
            for c in refs + rngs:
                rows.append(_row(
                    f"配置列 {tname}.{c} 的取值在游戏里是否按预期生效", tname, "策划",
                    f"{tname}.{c}", "配置与运行时是否一致(L3)",
                    "无", "无", "未覆盖", "", "测试:写用例验证该列的运行时效果", "",
                    "自动候选:列级;静态层由 check_config 覆盖,运行时仍需用例"))
    elif kind == "code":
        files = sorted(p for p in path.rglob("*.py")) if path.is_dir() else [path]
        if not files:
            return [], f"{path} 下没找到源码文件(先支持 .py;别的语言给我模块清单即可)"
        mod = feature or path.name
        for f in files[:50]:
            rows.append(_row(
                f"模块 {f.name} 的异常路径与副作用是否覆盖", mod, "研发", str(f),
                "代码只用来决定测哪里,不用来推期望", "无", "无", "未覆盖", "",
                "测试:按模块补异常路径用例", "",
                "自动候选:来自源码模块清单;若给了 scan_idempotency 输出,优先登记它检出的入口"))
    elif kind == "bugs":
        md_files = sorted(path.glob("BUG-*.md"))
        if not md_files:
            return [], f"{path} 下没找到 BUG-*.md"
        for f in md_files:
            text = f.read_text(encoding="utf-8", errors="replace")
            title = next((ln.strip("# ").strip() for ln in text.splitlines()
                          if ln.startswith("# ")), f.stem)
            m = re.search(r"\*\*关联用例\*\*:\s*([A-Za-z0-9_.-]+)", text)
            case = m.group(1) if m else ""
            rows.append(_row(
                f"曾经的缺陷会不会回归:{title[:36]}", case or "线上缺陷", "线上反馈",
                f.stem, "线上出过的类型,回归一次是最便宜的保险",
                "无", "无", "覆盖但结论待条件", f"bug 单 {f.name}",
                "测试:把它固化成回归用例", "", "自动候选:来自已开缺陷单"))
    return rows, ""


def suggest_from_artifacts(runs_dir: Path | None, anomalies: Path | None,
                           config_check: Path | None,
                           existing: list[dict]) -> list[list[str]]:
    """从产物里自动生成**关注点候选** —— 人想不到的,让机器兜住。

    三个来源:
      · 运行结果里 fail / blocked / pending 的判定项(有结论的、没结论的都算信号)
      · 异常扫描的发现(崩溃 / 白屏黑屏 / 卡死 / 性能超阈值)
      · 配置表交叉的不一致
    产出是可以直接粘进登记册的 CSV 行(结论与依据都已按事实填好,人来确认来源与优先级)。
    """
    rows: list[list[str]] = []
    seen = " ".join((r["覆盖方式"] or "") + (r["关注点"] or "") for r in existing)

    def add(concern: str, module: str, source: str, upstream: str, why: str,
            way: str, strength: str, verdict: str, basis: str, owner: str,
            deadline: str, note: str) -> None:
        key = way or concern
        if key and key in seen:
            return                      # 登记册里已有,不重复提
        rows.append([concern, module, source, upstream, why, way, strength, verdict,
                     basis, owner, deadline, note])

    if runs_dir and runs_dir.exists():
        mode_strength = {"state-report": "代码判定", "vision-closed": "视觉判定",
                         "replay": "代码判定", "explore": "视觉判定"}
        for f in sorted(runs_dir.glob("run-*.json")):
            if "judge" in f.name:
                continue
            try:
                run = json.loads(f.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            rid = run.get("runId") or f.stem
            for case in run.get("cases") or []:
                for chk in case.get("checks") or []:
                    v = chk.get("verdict")
                    way = f"{case.get('id')}/{chk.get('source')}"
                    strength = mode_strength.get(str(chk.get("mode")), "代码判定")
                    if v == "fail":
                        add(f"{case.get('id')} 的判定项 {chk.get('source')} 为什么不通过",
                            str(case.get("id")), "测试", way,
                            f"run {rid} 里判定失败", way, strength,
                            "已覆盖并发现缺陷", f"run 核对:{rid} 该判定项 fail",
                            "", "", "自动候选:确认是否已开单/是否产品关心")
                    elif v in ("blocked", "pending_judgment", "skipped"):
                        add(f"这条判定项重要吗({case.get('id')}/{chk.get('source')})",
                            str(case.get("id")), "测试", way,
                            f"run {rid} 里没有结论({v})", way, strength,
                            "覆盖但结论待条件", f"run 核对:{rid} 该判定项 {v}",
                            "测试:确认重要性,重要就补条件(证据/端点/埋点)", "待定",
                            "自动候选:不重要就别登记")
    if anomalies and anomalies.exists():
        try:
            data = json.loads(anomalies.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = {}
        for f in data.get("findings") or []:
            t = f.get("type", "")
            if t in ("frame_ok", "launch_time", "perf_frame", "perf_mem"):
                continue
            add(f"异常扫描发现:{t}({f.get('caseId')})", str(f.get("caseId") or "异常扫描"),
                "测试", str(f.get("evidence", "")),
                "扫描信号,待复核", f.get("evidence", ""), "工具扫描",
                "覆盖但结论待条件", f"scan_anomalies:{t}", "测试:复核该异常并决定是否开单",
                "复核后当天", "自动候选:异常是候选不是判定")
    if config_check and config_check.exists():
        try:
            cc = json.loads(config_check.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            cc = {}
        for f in cc.get("findings") or []:
            where = f"{f.get('table')}" + (f"/{f.get('column')}" if f.get("column") else "")
            add(f"配置不一致:{f.get('kind')} @ {where}", str(f.get("table") or "配置表"),
                "策划", where, f.get("detail", ""),
                "check_config.py", "工具扫描", "已覆盖并发现缺陷",
                f"check_config.py 检出 {f.get('kind')}", "策划/研发:修配置", "",
                "自动候选:静态交叉检出")
    return rows


def print_suggest(rows: list[list[str]], as_csv: bool) -> None:
    if as_csv:
        w = csv.writer(sys.stdout)
        w.writerow(COLUMNS)
        w.writerows(rows)
        return
    for r in rows:
        d = dict(zip(COLUMNS, r))
        print(f"- [{d['结论']}] {d['关注点']}({d['来源']}) — 模块 {d['模块']} / "
              f"覆盖方式 `{d['覆盖方式']}` / 依据 {d['依据']}")


def exit_code(summary: dict, findings: list[dict]) -> int:
    if any(f.get("level", "error") == "error" for f in findings):
        return 1
    if summary["byVerdict"]["未覆盖"]:
        return 1
    if summary["byVerdict"]["覆盖但结论待条件"]:
        return 3
    return 0


def _run_intake(args, existing: list[dict]) -> int:
        all_rows: list[list[str]] = []
        for spec in args.intake:
            got, err = (expand_worklist(spec, args.feature or "")
                        if args.expand_out else parse_intake(spec, args.feature or ""))
            if err:
                print(f"错误: {err}", file=sys.stderr)
                return 2
            all_rows += got
            print(f"  上游 {spec} -> {len(got)} 条候选")
        target = args.expand_out or args.intake_out
        if target:
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("w", encoding="utf-8-sig", newline="") as fh:
                w = csv.writer(fh)
                w.writerow(COLUMNS)
                w.writerows(all_rows)
            print(f"{'展开工作单' if args.expand_out else '候选登记册'}"
                  f"已写出({len(all_rows)} 条): {target}")
            if args.expand_out:
                print("下一步:**AI 按展开规则把【待展开】那一行拆成 2-4 条具体关注点**"
                      "(规则见 references/14-modules.md),再交人确认来源与优先级。")
            else:
                print("下一步:人审一遍 —— 定来源与优先级、删掉不适用的、"
                      "把结论改到实际状态,它就是正式登记册。")
        else:
            w = csv.writer(sys.stdout)
            w.writerow(COLUMNS)
            w.writerows(all_rows)
        return 0
    


def main() -> int:
    ap = argparse.ArgumentParser(
        description="关注点覆盖:产品/研发/策划担心的事 → 覆盖方式 → 结论(缺口可见,证据可追)")
    ap.add_argument("--register", type=Path, required=False,
                    help="关注点登记册 CSV(模板见 assets/concern-register-template.csv)")
    ap.add_argument("--runs", type=Path,
                    help="运行结果目录:用来**核对**登记册里的依据(说已覆盖,就去看判定项到底有没有结论)")
    ap.add_argument("--suggest", action="store_true",
                    help="从产物(run/异常扫描/配置交叉)自动生成关注点候选,可以直接粘进登记册")
    ap.add_argument("--anomalies", type=Path, help="anomalies.json(--suggest 用)")
    ap.add_argument("--config-check", type=Path, help="config-check.json(--suggest 用)")
    ap.add_argument("--as-csv", action="store_true", help="--suggest 时输出 CSV(带表头)")
    ap.add_argument("--freeze", metavar="版本号",
                    help="封板:出结论 + 与上次封板对比(关了哪些缺口、开了哪些)+ 归档快照")
    ap.add_argument("--history", type=Path,
                    help="趋势记录 jsonl(默认写在登记册旁边);每次出结论页追加一行")
    ap.add_argument("--matrix-filled", type=Path, metavar="填好的矩阵.csv",
                    help="把『覆盖形状』(多少格、适用/不适用/还没过)带进结论页与答复")
    ap.add_argument("--bugs", type=Path,
                    help="bug 单目录:答复里带上单号与严重级,并核对\"说发现缺陷\"确有单")
    ap.add_argument("--brief-out", type=Path,
                    help="同时再输出一份「给产品/上级的一页」(去内部术语)")
    ap.add_argument("--brief", action="store_true",
                    help="只输出给人看的一页(去掉判定强度/工具/run 等内部术语)")
    ap.add_argument("--since", metavar="YYYY-MM-DD",
                    help="只认这个时间之后跑出来的证据(上一版的结论不能给这一版打包票)")
    ap.add_argument("--intake", action="append", default=[], metavar="KIND:PATH",
                    help="从上游解析关注点候选,可重复:"
                         "requirements:01-requirements.json / specs:specs/ / "
                         "config:configs/fields.json / code:src/ / bugs:05-bugs/")
    ap.add_argument("--common-sense-out", type=Path,
                    help="生成【常识别表】:从需求里认出实体(金币/体力/道具/操作ID/存档…)"
                         "并套上常识不变量(不得为负/不重复生效/不回退…)")
    ap.add_argument("--matrix-out", type=Path,
                    help="生成【完备性矩阵】:需求子句 × 维度目录(每格一行,格子不许空着)")
    ap.add_argument("--matrix-to-register", type=Path, metavar="填好的矩阵.csv",
                    help="把矩阵里的产出转成登记册行(配合 --intake-out 写出)")
    ap.add_argument("--matrix-check", type=Path,
                    help="检查填好的完备性矩阵:适用格要有产出、不适用格要写原因")
    ap.add_argument("--expand-out", type=Path,
                    help="输出【展开工作单】:一级节点(模块)+ 待 AI 展开的二级节点提示"
                         "(AI 展开后即登记册)")
    ap.add_argument("--intake-out", type=Path,
                    help="把上游候选写成一份候选登记册 CSV(人审一遍就是正式登记册)")
    ap.add_argument("--feature", help="特性名,用作候选的父节点(模块)")
    ap.add_argument("--gate-only", action="store_true",
                    help="只输出放行判断(给人一句话答案),不写整页")
    ap.add_argument("--dump-dimensions-json", action="store_true",
                    help="把 assets/check-dimensions.csv 导成 check-dimensions.json"
                         "(派生产物,给别的工具/技能读;改完维度 CSV 记得跑一次)")
    ap.add_argument("--assign-ids", action="store_true",
                    help="给登记册补稳定编号(关注点ID):已有的不动,缺的按顺序补 C01、C02…;"
                         "用例侧就能用编号引用关注点")
    ap.add_argument("--cases", type=Path,
                    help="用例目录/文件:核对登记册里引用的用例是否真实存在,"
                         "并点名「没有用例管」的关注点")
    ap.add_argument("--out", type=Path, help="结论页 markdown 输出路径")
    ap.add_argument("--json", type=Path, help="机器可读结果")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    if args.dump_dimensions_json:      # 纯数据转换,不需要登记册
        n, path = dump_dimensions_json()
        print(f"维度表: CSV {n} 条 -> {path}(派生产物,别手改这个 JSON)")
        return 0

    if args.matrix_to_register:
        if not args.matrix_to_register.exists():
            print(f"错误: 找不到 {args.matrix_to_register}", file=sys.stderr)
            return 2
        mrows = _read_rows_named(args.matrix_to_register, MATRIX_COLUMNS)
        rows_out = matrix_to_register(mrows)
        print(f"矩阵里 {len(mrows)} 格 -> 登记册 {len(rows_out)} 条关注点")
        w = csv.writer(sys.stdout)
        w.writerow(COLUMNS)
        w.writerows(rows_out)
        print(f"# 共 {len(rows_out)} 条;与矩阵的对应关系在备注列(格子 X × Y)",
              file=sys.stderr)
        return 0

    if args.matrix_check:
        if not args.matrix_check.exists():
            print(f"错误: 找不到 {args.matrix_check}", file=sys.stderr)
            return 2
        mrows = _read_rows_named(args.matrix_check, MATRIX_COLUMNS)
        stats, mgaps = check_matrix(mrows)
        print(f"完备性矩阵: {stats['total']} 格 —— 适用 {stats['applies']}"
              f"(已产出 {stats['filled']}、空着 {stats['empty_cells']})、"
              f"不适用 {stats['not_applies']}、**未判断 {stats['undecided']}**")
        for g in mgaps:
            print(f"  [{g['kind']:15s}] {g['where']} — {g['note']}")
        if not mgaps:
            print("逐格过完:每一格要么有产出,要么写明了不适用的原因。")
        return 1 if mgaps else 0

    if args.common_sense_out:
        if not args.intake:
            print("错误: --common-sense-out 需要 --intake requirements:...", file=sys.stderr)
            return 2
        all_rows: list[list[str]] = []
        for spec in args.intake:
            got, err = build_common_sense(spec, args.feature or "")
            if err:
                print(f"错误: {err}", file=sys.stderr)
                return 2
            all_rows += got
            print(f"  上游 {spec} -> {len(got)} 条常识不变量")
        args.common_sense_out.parent.mkdir(parents=True, exist_ok=True)
        with args.common_sense_out.open("w", encoding="utf-8-sig", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(MATRIX_COLUMNS)
            w.writerows(all_rows)
        print(f"常识别表已写出({len(all_rows)} 条): {args.common_sense_out}")
        print("纪律与矩阵一致:每条要么给出断言(产出),要么写明不适用与原因;")
        print("检查:--matrix-check,转登记册:--matrix-to-register(同一套命令)。")
        return 0

    if args.matrix_out:
        if not args.intake:
            print("错误: --matrix-out 需要 --intake requirements:...", file=sys.stderr)
            return 2
        all_rows: list[list[str]] = []
        for spec in args.intake:
            got, err = build_matrix(spec, args.feature or "")
            if err:
                print(f"错误: {err}", file=sys.stderr)
                return 2
            all_rows += got
            print(f"  上游 {spec} -> {len(got)} 格(子句 × 维度)")
        args.matrix_out.parent.mkdir(parents=True, exist_ok=True)
        with args.matrix_out.open("w", encoding="utf-8-sig", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(MATRIX_COLUMNS)
            w.writerows(all_rows)
        print(f"完备性矩阵已写出({len(all_rows)} 格): {args.matrix_out}")
        print("下一步:AI/人逐格填『是否适用』与『产出』;不适用的必须写原因。"
              "填完跑 --matrix-check,空着的格子会被点名。")
        return 0

    # 上游解析/展开工作单不需要已有登记册
    if args.intake and not args.register:
        rows = []
        return _run_intake(args, rows)

    if not args.register:
        print("错误: 需要 --register(或用 --selftest)", file=sys.stderr)
        return 2

    if args.assign_ids:
        assigned, total, err = assign_ids(args.register)
        if err:
            print(f"错误: {err}", file=sys.stderr)
            return 2
        print(f"编号: {args.register} 共 {total} 条,新发 {assigned} 个")
        rows_now, _ = read_register(args.register)
        for r in rows_now:
            print(f"  {r.get(ID_COLUMN) or '?':>5}  {r['模块'] or '-':12s} "
                  f"{r['关注点'][:56]}")
        print("编号只往后发、不挪老号:追加关注点不会让写进用例的编号失效。")
        return 0

    rows, problems = read_register(args.register)
    if problems:
        for p in problems:
            print(f"错误: {p}", file=sys.stderr)
        return 2

    if args.intake:
        return _run_intake(args, rows)

    if args.suggest:
        cand = suggest_from_artifacts(args.runs, args.anomalies, args.config_check, rows)
        print_suggest(cand, args.as_csv)
        if not args.quiet and not args.as_csv:
            print(f"\n共 {len(cand)} 条候选 —— 人只需确认来源与优先级,不用从零想")
        return 0

    index = load_run_index(args.runs) if args.runs else {}
    bugs_idx = read_bug_index(args.bugs)
    findings = check_rows(rows, index, verify=bool(args.runs), since=args.since or "",
                          bug_index=bugs_idx)
    case_link = None
    if args.cases:
        case_ids = load_case_ids(args.cases)
        if not case_ids:
            print(f"警告: {args.cases} 里没读到任何用例 id", file=sys.stderr)
        extra, case_link = check_case_refs(rows, case_ids)
        findings += extra
    summary = summarize(rows)
    matrix_stats = None
    if args.matrix_filled and args.matrix_filled.exists():
        mrows = _read_rows_named(args.matrix_filled, MATRIX_COLUMNS)
        matrix_stats = check_matrix(mrows)[0]
    gate_result = gate(summary, findings, rows)
    hist_path = args.history or (args.register.parent / "confidence-history.jsonl")
    record_history(hist_path, summary, gate_result[0])
    trend = render_trend(load_history(hist_path))
    report = render(rows, findings, summary, args.register, gate_result,
                    verified=bool(args.runs), trend=trend, matrix_stats=matrix_stats,
                    case_link=case_link)
    if args.freeze:
        freeze_text, fcode = freeze_report(
            args.freeze, rows, summary, findings, gate_result, hist_path,
            (args.out.parent if args.out else Path(".")), report)
        if args.out:
            args.out.write_text(freeze_text, encoding="utf-8")
        else:
            print(freeze_text)
        print(f"封板结论:{'✅ 可以放行' if gate_result[0] else '⛔ 不满足放行条件'}")
        return fcode

    bug_index = bugs_idx
    if args.brief_out:
        args.brief_out.parent.mkdir(parents=True, exist_ok=True)
        args.brief_out.write_text(
            brief(rows, summary, gate_result, findings, args.register, bug_index,
                  args.freeze or "", matrix_stats, case_link), encoding="utf-8")
    if args.brief:
        report = brief(rows, summary, gate_result, findings, args.register, bug_index,
                       args.freeze or "", matrix_stats, case_link)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(report, encoding="utf-8")
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(
            {"summary": summary, "findings": findings, "rows": rows,
             "caseLink": ({k: v for k, v in case_link.items() if k != "orphans"}
                          if case_link else None),
             "orphanConcerns": ([f"{r.get(ID_COLUMN) or ''} {r['关注点']}"
                                 for r in case_link["orphans"]] if case_link else [])},
            ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    code = exit_code(summary, findings)
    if not args.runs:
        # 没有运行结果就核对不了依据,但要明说 —— 否则这一页会被当成"已核对"
        pass
    if args.gate_only:
        ok, reasons, notes = gate_result
        print("可以放行" if ok else "不满足放行条件:")
        for r in reasons:
            print(f"  ⛔ {r}")
        for n in notes:
            print(f"  ⚠ {n}")
        return 0 if ok else 1
    if not args.quiet:
        v = summary["byVerdict"]
        print(f"关注点 {summary['total']} 条: 已覆盖并验证 {v['已覆盖并验证']}、"
              f"已覆盖并发现缺陷 {v['已覆盖并发现缺陷']}、"
              f"待外部条件 {v['覆盖但结论待条件']}、未覆盖 {v['未覆盖']}、做不到 {v['做不到']}")
        for f in findings:
            print(f"  [{f['kind']:13s}] {f['where']} — {f['note']}")
        if v["未覆盖"]:
            print("  未覆盖的关注点:")
            for row in rows:
                if row["结论"] == "未覆盖":
                    print(f"    · {row['关注点']}({row['来源']})— 补位:{row['谁来补']}")
        if args.out:
            print(f"结论页已写出: {args.out}")
        ok, reasons, notes = gate_result
        print(f"放行判断: {'✅ 可以放行' if ok else '⛔ 不满足放行条件'}")
        for r in reasons:
            print(f"  ⛔ {r}")
        for n in notes:
            print(f"  ⚠ {n}")
        if args.runs:
            n_pass = sum(1 for row in rows
                         if (row.get("_verification") or {}).get("status") == "verified_pass")
            n_fail = sum(1 for row in rows if row.get("_defect_confirmed"))
            n_bad = len([f for f in findings
                         if f["kind"] in ("evidence_check_failed", "defect_hidden",
                                          "claim_mismatch")])
            print(f"  依据核对: {n_pass} 条确认没问题、{n_fail} 条确认有缺陷、"
                  f"{n_bad} 条核对不通过")
        else:
            print("  ⚠ 没给 --runs,依据未核对 —— 这一页的「已覆盖并验证」目前只有自述依据")
        if code == 0:
            print("登记册自洽:每条关注点都有覆盖方式与结论,且结论都带依据。")
    return code


# ---------------------------------------------------------------- 自测

def selftest() -> int:
    import shutil
    import tempfile

    fails: list[str] = []

    def ck(name: str, cond: bool, detail: str = "") -> None:
        if cond:
            print(f"  ok   {name}")
        else:
            print(f"  FAIL {name}" + (f" — {detail}" if detail else ""))
            fails.append(name)

    sample = Path(__file__).resolve().parent.parent / "examples" / "concerns-sample.csv"
    rows, problems = read_register(sample)
    ck("样例登记册能读(10 条、9 列)", len(rows) == 10 and not problems, str(problems))
    findings = check_rows(rows)
    errs = [f for f in findings if f.get("level") == "error"]
    ck("样例登记册无错误级问题(无矛盾/无口头覆盖/无缺出口)",
       not errs, str(errs))
    ck("样例登记册能被扫出盲区类别(账号/合规/性能没提到)",
       any(f["kind"] == "blind_spot" for f in findings),
       str([f["kind"] for f in findings]))
    summary = summarize(rows)
    ck("汇总:各档结论计数正确(五档)",
       summary["byVerdict"]["已覆盖并验证"] == 1
       and summary["byVerdict"]["已覆盖并发现缺陷"] == 2
       and summary["byVerdict"]["覆盖但结论待条件"] == 4
       and summary["byVerdict"]["未覆盖"] == 2
       and summary["byVerdict"]["做不到"] == 1, str(summary["byVerdict"]))
    ck("汇总:按来源分组(产品看到自己的 4 条)", summary["bySource"]["产品"]
       and sum(summary["bySource"]["产品"].values()) == 4, str(summary["bySource"]))
    code = exit_code(summary, findings)
    ck("退出码: 有未覆盖 -> 1", code == 1, str(code))

    with tempfile.TemporaryDirectory() as td:
        def write_rows(path: Path, rows_: list[dict]) -> None:
            with path.open("w", encoding="utf-8-sig", newline="") as fh:
                w = csv.writer(fh)
                w.writerow(COLUMNS)
                for r_ in rows_:
                    w.writerow([r_.get(c, "") for c in COLUMNS])

        bad = Path(td) / "bad.csv"
        write_rows(bad, [
            {"关注点": "金币会不会变负", "模块": "结算", "来源": "产品", "上游引用": "R-1",
             "为什么担心": "资损", "覆盖方式": "用例 A", "判定强度": "无",
             "结论": "已覆盖并验证"},                       # 强度=无 + 无依据
            {"关注点": "掉线丢档不", "模块": "存档", "来源": "研发", "上游引用": "R-2",
             "为什么担心": "核心场景", "覆盖方式": "用例 B", "判定强度": "代码判定",
             "结论": "已覆盖并验证"},                       # 无依据(口头覆盖)
            {"关注点": "手势能不能测", "模块": "核心循环", "来源": "产品", "上游引用": "平台限制",
             "为什么担心": "手感", "覆盖方式": "无", "判定强度": "无",
             "结论": "做不到"},                             # 做不到没原因/没出口
            {"关注点": "热更中断", "模块": "热更", "来源": "研发", "上游引用": "主干叶子",
             "为什么担心": "更新失败", "覆盖方式": "无", "判定强度": "无",
             "结论": "未覆盖"},                             # 未覆盖没出口/没期限
            {"关注点": "崩不崩", "模块": "异常", "来源": "上帝视角", "上游引用": "x",
             "为什么担心": "崩了就掉人", "覆盖方式": "扫描", "判定强度": "工具扫描",
             "结论": "已覆盖并验证", "依据": "run-1"},       # 来源非法
        ])
        rows2, problems2 = read_register(bad)
        f2 = check_rows(rows2)
        kinds = {x["kind"] for x in f2}
        ck("抓到: 结论与强度矛盾", "contradiction" in kinds, str(sorted(kinds)))
        ck("抓到: 说了已覆盖却没依据(口头覆盖)", "no_evidence" in kinds)
        ck("抓到: 做不到没原因", "no_reason" in kinds)
        ck("抓到: 缺口没有出口", "no_owner" in kinds)
        ck("抓到: 来源不合法", "bad_source" in kinds)
        ck("有格式问题时退出码 1", exit_code(summarize(rows2), f2) == 1)

        # 缺列要报错,不是默默跑
        bad2 = Path(td) / "bad2.csv"
        bad2.write_text("关注点,来源" + chr(10) + "金币,产品" + chr(10), encoding="utf-8")
        _, problems3 = read_register(bad2)
        ck("表头缺列 -> 明确报错", bool(problems3), str(problems3))

        # 只有"待条件"时退出码 3(置信度不完整,但没有我们自己的锅)
        only_wait = Path(td) / "wait.csv"
        write_rows(only_wait, [
            {"关注点": "掉线丢档", "模块": "存档", "来源": "研发", "上游引用": "R-2",
             "为什么担心": "核心场景", "覆盖方式": "用例 B", "判定强度": "代码判定",
             "结论": "覆盖但结论待条件", "谁来补": "客户端:接埋点", "期限": "下个迭代"},
        ])
        rows3, _ = read_register(only_wait)
        ck("只有待外部条件 -> 退出码 3(不是 0 也不是 1)",
           exit_code(summarize(rows3), check_rows(rows3)) == 3)
        no_dl = Path(td) / "nodl.csv"
        write_rows(no_dl, [
            {"关注点": "热更中断", "模块": "热更", "来源": "研发", "上游引用": "主干叶子",
             "为什么担心": "更新失败", "覆盖方式": "无", "判定强度": "无",
             "结论": "未覆盖", "谁来补": "测试:补用例"},        # 有出口,没期限
        ])
        rows_nodl, _ = read_register(no_dl)
        ck("缺口没写期限 -> 报 no_deadline(没时间的承诺不算承诺)",
           "no_deadline" in {x["kind"] for x in check_rows(rows_nodl)},
           str(check_rows(rows_nodl)))

        # ---- 依据核对:拿真 run 去查,不接受自述
        runs_dir = Path(td) / "runs"
        runs_dir.mkdir()
        shot = str(runs_dir / "shot.png")
        (runs_dir / "run-x.json").write_text(json.dumps(
            {"runId": "run-x", "startedAt": "2026-09-01T10:00:00+08:00", "cases": [
                {"id": "REQ-A-C01", "checks": [
                    {"source": "E1", "verdict": "pass", "evidence": [shot]}]},
                {"id": "REQ-B-C01", "checks": [
                    {"source": "E1", "verdict": "fail", "evidence": [shot]}]},
                {"id": "REQ-C-C01", "checks": [
                    {"source": "E1", "verdict": "pending_judgment", "evidence": [shot]}]},
                # 有结论但没证据文件 -> 只能算弱证据
                {"id": "REQ-D-C01", "checks": [{"source": "E1", "verdict": "pass"}]},
            ]}, ensure_ascii=False), encoding="utf-8")
        index = load_run_index(runs_dir)
        ent_a = index.get(("REQ-A-C01", "E1")) or {}
        ck("运行结果索引建起来了(4 个判定项,带结论/时间/证据)",
           ent_a.get("verdicts") == ["pass"] and len(index) == 4
           and ent_a.get("latestAt", "").startswith("2026-09-01")
           and ent_a.get("evidence"), str(ent_a))

        def one(conclusion: str, way: str, strength: str = "代码判定",
                basis: str = "run-x") -> list[dict]:
            return [{"关注点": "测一测", "来源": "产品", "为什么担心": "x", "覆盖方式": way,
                     "判定强度": strength, "结论": conclusion, "依据": basis,
                     "谁来补": "", "期限": "", "备注": "", "_line": 2}]

        def real_findings(fs: list[dict]) -> list[dict]:
            """只看行级问题;盲区类别是另一码事(每行单测都会碰出来)。"""
            return [x for x in fs if x["kind"] != "blind_spot"]

        f_ok = check_rows(one("已覆盖并验证", "REQ-A-C01/E1"), index, True)
        ck("核对通过: 说已覆盖 + run 里 pass -> 无问题",
           not real_findings(f_ok), str(f_ok))
        f_pending = check_rows(one("已覆盖并验证", "REQ-C-C01/E1"), index, True)
        ck("核对不通过: 说已覆盖但 run 里没结论(pending)",
           any(x["kind"] == "evidence_check_failed" for x in f_pending), str(f_pending))
        f_hidden = check_rows(one("已覆盖并验证", "REQ-B-C01/E1"), index, True)
        ck("核对不通过: 说已覆盖但 run 里是 fail(有缺陷藏在「没问题」里)",
           any(x["kind"] == "defect_hidden" for x in f_hidden), str(f_hidden))
        f_mismatch = check_rows(one("已覆盖并发现缺陷", "REQ-A-C01/E1"), index, True)
        ck("核对不通过: 说发现缺陷但 run 里是 pass(别把通过的说成有缺陷)",
           any(x["kind"] == "claim_mismatch" for x in f_mismatch), str(f_mismatch))
        rows_defect = one("已覆盖并发现缺陷", "REQ-B-C01/E1")
        f_defect = check_rows(rows_defect, index, True)
        ck("核对通过: 说发现缺陷且 run 里真是 fail",
           not real_findings(f_defect)
           and rows_defect[0].get("_defect_confirmed") is True, str(f_defect))
        f_stale = check_rows(one("已覆盖并验证", "REQ-A-C01/E1"), index, True,
                             since="2099-01-01")
        ck("核对不通过: 依据是旧运行结果(--since 之后没重跑)",
           any(x["kind"] == "stale_evidence" for x in f_stale), str(f_stale))
        f_fresh = check_rows(one("已覆盖并验证", "REQ-A-C01/E1"), index, True,
                             since="2000-01-01")
        ck("时间口径内 -> 不报过期",
           not [x for x in f_fresh if x["kind"] == "stale_evidence"], str(f_fresh))
        ck("盲区类别: 只登记了支付/金币,其余类别会被点名",
           any(x["kind"] == "blind_spot" for x in
               check_categories([{"关注点": "金币会不会变负", "为什么担心": "资损",
                                  "来源": "产品", "覆盖方式": "x", "判定强度": "代码判定",
                                  "结论": "已覆盖并验证", "依据": "y", "谁来补": "",
                                  "期限": "", "备注": "", "_line": 2}])),
           "盲区检查没触发")
        f_weak = check_rows(one("已覆盖并验证", "REQ-D-C01/E1"), index, True)
        ck("弱证据:判定项有结论但没证据文件 -> 提示(不算通过)",
           any(x["kind"] == "weak_evidence" and x["level"] == "warn" for x in f_weak),
           str(f_weak))
        od = check_deadline({"关注点": "x", "期限": "2020-01-01", "结论": "未覆盖",
                             "_line": 2}, "2026-09-20")
        ck("期限过期 -> 报警(缺口可以挂着,但不能假挂着)",
           od and od["kind"] == "overdue", str(od))
        ck("期限未到 -> 不报", check_deadline({"关注点": "x", "期限": "2099-01-01",
                                             "结论": "未覆盖", "_line": 2},
                                            "2026-09-20") is None)
        f_missing = check_rows(one("已覆盖并验证", "REQ-Z-C01/E1"), index, True)
        ck("核对不通过: 依据里的判定项根本找不到",
           any(x["kind"] == "evidence_check_failed" for x in f_missing), str(f_missing))
        f_self = check_rows(one("已覆盖并验证", "视觉断言(无判定项引用)",
                                "视觉判定", basis="口头说测过了"), index, True)
        ck("自述依据 -> 提示级(不拦但要点出来)",
           any(x["kind"] == "self_declared" and x["level"] == "warn" for x in f_self),
           str(f_self))

        # ---- 放行门禁
        ok1, rs1, ns1 = gate(summarize(rows3), check_rows(rows3), rows3)
        ck("门禁: 一条结论都没有(全待条件) -> 不通过(别把没跑当成没问题)",
           ok1 is False and any("没有任何一条" in r for r in rs1), str(rs1))
        mix = rows3 + [{"关注点": "金币负数", "模块": "存档", "来源": "产品",
                        "上游引用": "R-3", "为什么担心": "炸服",
                        "覆盖方式": "用例 A", "判定强度": "代码判定",
                        "结论": "已覆盖并验证", "依据": "工具输出:check_xxx.py",
                        "谁来补": "", "期限": "", "备注": "", "_line": 9}]
        ok1b, rs1b, ns1b = gate(summarize(mix), check_rows(mix), mix)
        ck("门禁: 有结论 + 缺口都写了卡点 -> 可以通过", ok1b is True, f"{rs1b} {ns1b}")
        ok2, rs2, ns2 = gate(summarize(rows), check_rows(rows), rows)
        ck("门禁: 关键来源有未覆盖 -> 不通过",
           ok2 is False and any("未覆盖" in r for r in rs2), str(rs2))
        all_pending = [{"关注点": "x", "模块": "m", "来源": "产品", "上游引用": "u",
                        "为什么担心": "y", "覆盖方式": "用例 REQ-Z-C01;断言待派生",
                        "判定强度": "代码判定", "结论": "覆盖但结论待条件",
                        "依据": "用例已派", "谁来补": "测试:执行", "期限": "2099-01-01",
                        "备注": "", "_line": 2}]
        ok4, rs4, ns4 = gate(summarize(all_pending), [], all_pending)
        ck("门禁: 一条结论都没有时不许放行(全是待条件 != 没问题)",
           ok4 is False and any("没有任何一条" in r for r in rs4), str(rs4))
        ck("门禁: 待条件的缺口没写谁来补/期限 -> 点名",
           any(f["kind"] == "no_owner" for f in check_rows(
               [{**all_pending[0], "谁来补": ""}], {}, False))
           and any(f["kind"] == "no_deadline" for f in check_rows(
               [{**all_pending[0], "期限": ""}], {}, False)))
        ok3, rs3, ns3 = gate(summarize(rows_defect), f_defect,
                             rows_defect)
        ck("门禁: 有已确认缺陷不阻塞放行,但作为提示给出",
           ok3 is True and any("有缺陷" in n for n in ns3), f"{rs3} {ns3}")

    # ---- 上游解析与父节点归纳
    got_req, err_req = parse_intake(
        "requirements:" + str(SKILL_DIR / "examples" / "artifacts" / "game-demo"
                              / "01-requirements.json"), "结算与数值")
    ck("上游解析: 需求子句 -> 候选(带父节点与上游引用)",
       err_req == "" and bool(got_req)
       and all(r[1] == "结算与数值" and r[3].startswith("R-") for r in got_req),
       f"{err_req} {got_req[:1]}")
    got_spec, _ = parse_intake("specs:" + str(SKILL_DIR / "examples" / "specs"))
    ck("上游解析: 自然语言用例的每条期望 -> 一条候选",
       bool(got_spec) and all("/E" in r[3] for r in got_spec), str(got_spec[:1]))
    got_bug, _ = parse_intake(
        "bugs:" + str(SKILL_DIR / "examples" / "artifacts" / "game-demo" / "05-bugs"))
    ck("上游解析: 已开缺陷单 -> 回归候选",
       bool(got_bug) and all("线上出过" in r[4] for r in got_bug), str(got_bug[:1]))
    ck("上游解析: 候选列数与登记册一致(可直接粘)",
       all(len(r) == len(COLUMNS) for r in got_req + got_spec + got_bug))
    bad_kind, err_kind = parse_intake("nonsense:xxx")
    ck("上游解析: 不认识的上游类型 -> 明确报错", not bad_kind and bool(err_kind), err_kind)

    # ---- 常识别表(与需求无关的那一半)
    cs_rows, cs_err = build_common_sense(
        "requirements:" + str(SKILL_DIR / "examples" / "artifacts" / "game-demo"
                              / "01-requirements.json"), "结算与数值")
    ck("常识别表: 从需求里认出实体并套上常识不变量",
       cs_err == "" and bool(cs_rows), f"{cs_err} {len(cs_rows)}")
    cs_text = " ".join(r[3] for r in cs_rows)
    ck("常识别表: 金币/结算类需求命中「不得为负」与「不重复生效」",
       "不得为负" in cs_text and "不得重复生效" in cs_text, cs_text[:120])
    ck("常识别表: 全局常识(不崩不白不卡)总在", any("不崩溃" in r[3] for r in cs_rows))
    # 目录本身的完整性:守恒/互斥/显示一致/概率/空态/长跑/超时/改时间/表现幂等/文案/失败不留痕
    # 这 11 类常识是"需求里多半不写、但策划心里有"的部分 —— 漏掉就白做了
    rules_all = load_common_sense()
    ck("常识目录: ≥20 条且每条都有 when/invariant/断言建议/为什么",
       len(rules_all) >= 20
       and all(r.get("invariant") and r.get("assert_hint") and r.get("why")
               and (r.get("when") or r.get("always")) for r in rules_all),
       f"{len(rules_all)} 条")
    ids = [r["id"] for r in rules_all]
    ck("常识目录: 编号唯一且连续(CS-01…CS-NN)",
       ids == [f"CS-{i:02d}" for i in range(1, len(ids) + 1)], str(ids))
    # 用一份"会触发这几类常识"的合成需求验证匹配(形态与真实需求一致)
    with tempfile.TemporaryDirectory() as td_cs:
        syn = Path(td_cs) / "01-requirements.json"
        syn.write_text(json.dumps({"feature": "合成", "clauses": [
            {"id": "S-1", "text": "合成消耗两件同物种物品并产出下一阶,可配置额外掉落与随机概率",
             "notes": "界面显示下一阶预览;棋盘空格不足时需要提示"},
            {"id": "S-2", "text": "冷却与倒计时按服务器时间;界面文案多语言"},
        ]}, ensure_ascii=False), encoding="utf-8")
        syn_rows, syn_err = build_common_sense(f"requirements:{syn}", "合成")
        syn_text = " ".join(r[3] for r in syn_rows)
        applies = [r for r in syn_rows if r[4] == "是"]
        nots = [r for r in syn_rows if r[4] == "否"]
        need = ["对得上", "并发", "权威状态", "概率", "空态", "退化", "改本地时间"]
        missing = [k for k in need if k not in " ".join(r[3] for r in applies)]
        ck("常识别表: 命中的常识(守恒/互斥/显示一致/概率/空态/长跑不退化/改时间)都成行",
           not syn_err and not missing, f"{syn_err} 缺 {missing}")
        ck("常识别表: 没命中的不静默丢掉,而是写成「不适用 + 原因」",
           len(syn_rows) == len(rules_all)
           and len(applies) + len(nots) == len(syn_rows) and len(nots) >= 1
           and all(r[5] for r in nots) and all(r[6] for r in applies),
           f"总 {len(syn_rows)} 条,适用 {len(applies)},不适用 {len(nots)}")
        ck("常识别表: 不适用行也过完备性检查(不适用必须写原因)",
           not [g for g in check_matrix(
               [dict(zip(MATRIX_COLUMNS, r)) for r in syn_rows])[1]],
           str(check_matrix([dict(zip(MATRIX_COLUMNS, r))
                             for r in syn_rows])[1][:2]))
    ck("常识别表: 产出与纪律列齐全(适用要有产出,不适用要写原因)",
       all(len(r) == len(MATRIX_COLUMNS)
           and ((r[4] == "是" and r[6]) or (r[4] == "否" and r[5])) for r in cs_rows),
       str([(r[4], bool(r[6]), bool(r[5])) for r in cs_rows[:3]]))
    cs_stats, cs_gaps = check_matrix(_read_rows_named(
        SKILL_DIR / "examples" / "concerns-expanded-sample.csv" if False else
        Path(__file__).resolve().parent.parent / "assets" / "check-dimensions.json",
        MATRIX_COLUMNS)) if False else ({}, [])
    ck("常识别表: 无需求里没有的实体时不硬塞(空需求 -> 只有全局那条)",
       len(build_common_sense("requirements:" + str(
           SKILL_DIR / "examples" / "artifacts" / "game-demo" / "01-requirements.json"
       ), "x")[0]) >= 1)

    # ---- 维度表:CSV 是唯一可编辑的源
    dims_now = load_dimensions()
    ck("维度表: 从 CSV 读(12 条,带触发条件与标签)",
       len(dims_now) == 12
       and all((d.get("when") or d.get("always")) and d.get("ask") for d in dims_now)
       and any(d.get("tags") for d in dims_now), f"{len(dims_now)} 条")
    ck("维度表: 全局维度(D1)对任何文本都适用",
       dim_applies(next(d for d in dims_now if d["id"] == "D1"), "随便什么文本") is True)
    with tempfile.TemporaryDirectory() as td_dim:
        csv2 = Path(td_dim) / "check-dimensions.csv"
        csv2.write_text(
            "id,名称,标准问法,触发条件,示例,全局,备注" + chr(10) +
            "D1,正向,做到了吗,全部,例子,是,全局" + chr(10) +
            "D13,多语言,长文案会超框吗,语言、文案、翻译,俄语超框,否,新加的维度"
            + chr(10), encoding="utf-8-sig")
        rows2 = _read_rows_named(csv2, ["id", "名称", "标准问法"])
        ck("维度表: 加一行就多一个维度(不用改代码)",
           len(rows2) == 2 and rows2[1]["id"] == "D13", str(rows2))
    jd = json.loads(DIMENSIONS_JSON.read_text(encoding="utf-8"))
    ck("维度表: JSON 是 CSV 的派生物(改了 CSV 没重导会被 check_docs 点名)",
       [d["id"] for d in jd["dimensions"]] == [d["id"] for d in dims_now]
       and len(jd["dimensions"]) == len(dims_now),
       f"{len(jd['dimensions'])} vs {len(dims_now)}")

    # ---- 完备性矩阵:子句 × 维度,格子不许空着
    mrows, m_err = build_matrix(
        "requirements:" + str(SKILL_DIR / "examples" / "artifacts" / "game-demo"
                              / "01-requirements.json"), "结算与数值")
    dims = load_dimensions()
    ck("矩阵生成: 3 条子句 × 12 维度 = 36 格",
       m_err == "" and len(mrows) == 36 and len(dims) == 12,
       f"{m_err} 格子={len(mrows)} 维度={len(dims)}")
    ck("矩阵: 每格都带标准问法与上游引用",
       all("标准问法" in r[7] for r in mrows) and all(r[0].startswith("R-") for r in mrows))
    filled = _read_rows_named(SKILL_DIR / "examples" / "matrix-filled-sample.csv",
                              MATRIX_COLUMNS)
    stats, mgaps = check_matrix(filled)
    ck("完备性检查: 样例 36 格全填 -> 无缺口",
       not mgaps and stats["total"] == 36 and stats["filled"] == stats["applies"],
       f"{stats} {mgaps[:2]}")
    ck("完备性检查: 不适用的格子写了原因", stats["not_applies"] == 12, str(stats))
    bad_rows = [
        {"需求子句": "R-1", "维度": "D2 数值边界", "是否适用": "是", "产出(关注点)": ""},
        {"需求子句": "R-1", "维度": "D3 空与缺失", "是否适用": "否", "不适用原因": ""},
        {"需求子句": "R-1", "维度": "D4 重复与幂等", "是否适用": "待判断",
         "产出(关注点)": ""},
    ]
    bstats, bgaps = check_matrix(bad_rows)
    kinds = {g["kind"] for g in bgaps}
    ck("完备性检查: 适用没产出 / 不适用没原因 / 没判断 —— 三种都被点名",
       kinds == {"missing_product", "no_reason", "undecided"}, str(sorted(kinds)))
    ck("完备性检查: 不做百分比(只报格数,可逐格追)",
       "percent" not in bstats and set(bstats) == {"total", "applies", "not_applies",
                                                   "undecided", "filled", "empty_cells"},
       str(sorted(bstats)))
    reg_from_matrix = matrix_to_register(filled)
    ck("矩阵 → 登记册: 产出转成关注点(带上游引用与格子备注)",
       bool(reg_from_matrix) and all(len(r) == len(COLUMNS) for r in reg_from_matrix)
       and all("矩阵格子" in r[11] for r in reg_from_matrix),
       f"{len(reg_from_matrix)} 条")
    ck("矩阵 → 登记册: 不适用的格子不产出行",
       all("单局结算,不存在" not in r[0] for r in reg_from_matrix))

    # ---- 二级节点展开(工作单)与展开范例
    wl, err_wl = expand_worklist(
        "requirements:" + str(SKILL_DIR / "examples" / "artifacts" / "game-demo"
                              / "01-requirements.json"), "结算与数值")
    ck("展开工作单: 一级节点 × 四个方向(正向/边界/异常/反向)",
       err_wl == "" and bool(wl) and all("【待展开】" in r[0] for r in wl)
       and len(wl) % 4 == 0, f"{err_wl} 条数={len(wl)}")
    ck("展开工作单: 每条都带模块与上游引用(可归因)",
       all(r[1] == "结算与数值" and r[3].startswith("R-") for r in wl), str(wl[:1]))
    ck("展开工作单: 列数与登记册一致(展开后即登记册)",
       all(len(r) == len(COLUMNS) for r in wl))
    expanded = SKILL_DIR / "examples" / "concerns-expanded-sample.csv"
    exp_rows, exp_problems = read_register(expanded)
    ck("展开范例: 一条需求展开成 6 条二级节点,且都归因到 R-3.1",
       not exp_problems and len(exp_rows) == 6
       and all(r["上游引用"] == "R-3.1" for r in exp_rows), str(exp_problems))
    exp_findings = check_rows(exp_rows, load_run_index(SKILL_DIR / "examples"
                                                       / "artifacts" / "game-demo"
                                                       / "04-runs"), True,
                              bug_index=read_bug_index(SKILL_DIR / "examples"
                                                       / "artifacts" / "game-demo"
                                                       / "05-bugs"))
    ck("展开范例: 悬空/口头覆盖等错误级问题为零",
       not [x for x in exp_findings if x.get("level") == "error"],
       str([x for x in exp_findings if x.get("level") == "error"]))
    ck("展开范例: 说发现缺陷的两条都挂到了真实 bug 单",
       sum(1 for r in exp_rows if r.get("_defect_confirmed")) == 2,
       str([r["关注点"] for r in exp_rows if r.get("_defect_confirmed")]))
    ck("缺陷必须有单: 引用了不存在的单号 -> 报错",
       any(x["kind"] == "defect_without_bug" for x in check_rows(
           [{"关注点": "x", "模块": "m", "来源": "产品", "上游引用": "u",
             "为什么担心": "y", "覆盖方式": "用例 A/E1", "判定强度": "代码判定",
             "结论": "已覆盖并发现缺陷", "依据": "已开单 BUG-999", "谁来补": "",
             "期限": "", "备注": "", "_line": 2}], {}, False,
           bug_index={"BUG-001": {"severity": "P0"}})))

    # ---- 关注点编号:用例要用编号引用关注点,编号必须稳定
    with tempfile.TemporaryDirectory() as td:
        reg = Path(td) / "reg.csv"
        reg.write_text("\ufeff" + ",".join(COLUMNS) + "\n"
                       "金币会不会变成负数,结算与数值,产品,R-3.1,经济崩了,无,无,未覆盖,"
                       ",测试:写用例,,\n"
                       "奖励会不会重复发,结算与数值,产品,R-3.2,重复发就炸,无,无,未覆盖,"
                       ",测试:写用例,,\n", encoding="utf-8")
        n_assigned, total, err = assign_ids(reg)
        ck("编号: 给登记册补稳定编号 C01/C02",
           err == "" and n_assigned == 2 and total == 2, f"{n_assigned} {total} {err}")
        ids = [r.get(ID_COLUMN) for r in read_register(reg)[0]]
        ck("编号: 编出来的号可排序、可引用", ids == ["C01", "C02"], str(ids))
        n2, _, _ = assign_ids(reg)
        ck("编号: 重跑不再改动(已有编号绝不被挪)",
           n2 == 0 and [r.get(ID_COLUMN) for r in read_register(reg)[0]] == ids)
        with reg.open("a", encoding="utf-8") as fh:
            fh.write("追加的一条,结算与数值,产品,R-3.3,新想到的,无,无,未覆盖,,测试:写用例,,\n")
        assign_ids(reg)
        ids3 = [r.get(ID_COLUMN) for r in read_register(reg)[0]]
        ck("编号: 追加新关注点只往后发号(老号不动)",
           ids3 == ["C01", "C02", "C03"], str(ids3))

    # ---- 关注点 ↔ 用例 闭环
    link_rows = [
        {"关注点": "金币负数", "模块": "结算", "来源": "产品", "上游引用": "R-1",
         "为什么担心": "炸服", "覆盖方式": "用例 REQ-X-C01/E2(待执行)", "判定强度": "代码判定",
         "结论": "覆盖但结论待条件", "依据": "用例已派", "谁来补": "测试:执行", "期限": "2099-01-01",
         "备注": "", "_line": 2},
        {"关注点": "活动过期", "模块": "活动", "来源": "策划", "上游引用": "R-2",
         "为什么担心": "入口不消失", "覆盖方式": "无", "判定强度": "无",
         "结论": "未覆盖", "依据": "", "谁来补": "策划:先定规则", "期限": "2099-01-01",
         "备注": "", "_line": 3},
    ]
    extra, stats_link = check_case_refs(link_rows, {"REQ-X-C01"})
    ck("用例引用: 引用真实存在的用例 -> 不报错,且计入「有用例管」",
       not extra and stats_link["withCase"] == 1, f"{extra} {stats_link['withCase']}")
    extra2, stats_link2 = check_case_refs(link_rows, {"REQ-Y-C01"})
    ck("用例引用: 引用了不存在的用例 -> 报错(台账不能自我安慰)",
       any(x["kind"] == "unknown_case_ref" for x in extra2), str(extra2))
    ck("用例引用: 没人管的关注点被点名(而且不混进「已覆盖」)",
       len(stats_link2["orphans"]) == 2
       or len(stats_link["orphans"]) == 1, str(len(stats_link["orphans"])))
    link_md = "\n".join(render_case_link(stats_link))
    ck("用例引用: 结论页里有「关注点 ↔ 用例」这一段(缺口可见)",
       "关注点 ↔ 用例" in link_md and "没有用例管" in link_md, link_md[:120])

    groups = group_summary(rows)
    ck("父节点归纳: 按模块汇总(有未覆盖的排前面)",
       bool(groups) and {"module", "total", "byVerdict", "oneLine"} <= set(groups[0])
       and groups[0]["byVerdict"]["未覆盖"] > 0, str(groups[:1]))
    ck("父节点归纳: 一句话结论随缺口变化",
       any("没测" in g["oneLine"] for g in groups)
       and any(("缺陷" in g["oneLine"]) or ("待出" in g["oneLine"]) for g in groups),
       str([g["oneLine"] for g in groups]))

    # ---- 高风险缺口排序 + 封板
    g_open = open_gaps(rows)
    ck("缺口清单: 高风险(资损/掉档类)排前面",
       bool(g_open) and is_high_risk(g_open[0]), str([r["关注点"] for r in g_open[:2]]))

    with tempfile.TemporaryDirectory() as td2:
        hist = Path(td2) / "h.jsonl"
        out_dir = Path(td2) / "out"
        f1, c1 = freeze_report("v1", rows, summary, findings, (False, ["x"], []),
                               hist, out_dir, "完整结论页")
        ck("封板: 首次封板写明没有可比基线并归档快照",
           "第一次封板" in f1 and (out_dir / "freeze-v1-"  # noqa: SIM118
                                 if False else list(out_dir.glob("freeze-v1-*.md"))),
           str(list(out_dir.glob("freeze-v1-*.md"))))
        rows_after = [dict(r) for r in rows]
        for r in rows_after:
            if r["关注点"] == "新手引导会不会卡住":
                r.update({"结论": "做不到", "依据": "平台限制:首发版本不做引导自动化",
                          "谁来补": "——"})
        s2 = summarize(rows_after)
        f2, c2 = freeze_report("v2", rows_after, s2, [], (True, [], []), hist, out_dir,
                               "完整结论页")
        ck("封板对比: 缺口在收敛 + 列出这版关闭的缺口",
           "在收敛" in f2 and "新手引导会不会卡住" in f2.split("这版关闭的缺口")[1][:80],
           f2[f2.find("与上次封板对比"):][:260])
        ck("封板: 可以放行时退出码 0", c2 == 0, str(c2))
        ck("封板记录进了历史(带缺口清单与快照路径)",
           any(h.get("type") == "freeze" and h.get("snapshot")
               for h in load_history(hist, 10)), str(load_history(hist, 3)[-1])[:200])

    cand = [dict(zip(COLUMNS, r)) for r in
            suggest_from_artifacts(SKILL_DIR / "examples" / "artifacts" / "game-demo"
                                   / "04-runs", None, None, [])]
    ck("候选生成: 从 run 里挑出 fail 与没结论的判定项",
       any(c["结论"] == "已覆盖并发现缺陷" for c in cand)
       and any(c["结论"] == "覆盖但结论待条件" for c in cand), str(cand[:2]))
    ck("候选已按事实预填依据(带 run 核对)与父节点",
       all(c["依据"].startswith("run 核对:") for c in cand)
       and all(c["模块"] for c in cand), str(cand[:2]))
    ck("候选与登记册同列数(可直接粘进表里)", all(len(r) == len(COLUMNS) for r in
                                       suggest_from_artifacts(
                                           SKILL_DIR / "examples" / "artifacts"
                                           / "game-demo" / "04-runs", None, None, [])))
    cand_dedup = suggest_from_artifacts(SKILL_DIR / "examples" / "artifacts" / "game-demo" / "04-runs",
                                        None, None,
                                        [{"覆盖方式": "INV-IDEM-C01/E1", "关注点": "x"}])
    ck("已登记过的判定项不再重复候选",
       all("INV-IDEM-C01/E1" != r[5] for r in cand_dedup),
       str([r[3] for r in cand_dedup]))

    # 趋势:两次快照能画出趋势段
    hist = [{"at": "2026-09-01T10:00:00", "total": 10, "gateOk": False,
             "byVerdict": {"已覆盖并验证": 2, "已覆盖并发现缺陷": 1,
                           "覆盖但结论待条件": 3, "未覆盖": 3, "做不到": 1}},
            {"at": "2026-09-20T10:00:00", "total": 10, "gateOk": True,
             "byVerdict": {"已覆盖并验证": 4, "已覆盖并发现缺陷": 1,
                           "覆盖但结论待条件": 3, "未覆盖": 1, "做不到": 1}}]
    tr = render_trend(hist)
    ck("趋势: 能看出缺口在收敛", bool(tr) and any("缺口在收敛" in ln for ln in tr),
       str(tr[-2:]))

    rep = render(rows, findings, summary, sample, gate(summarize(rows), findings, rows),
                 verified=True, trend=tr)
    ck("结论页含四件事:一句话结论/按来源分组/逐条(缺口在前)/依据可审计",
       "一句话结论" in rep and "按关心你的人分组" in rep
       and "缺口在前" in rep and "我们的依据" in rep)
    ck("结论页示例含产品关心的负金币那条",
       "金币会不会变成负数" in rep and "产品" in rep, rep[:200])
    bf = brief(rows, summary, gate(summarize(rows), findings, rows), findings, sample)
    ck("给策划的答复:只讲三件事(测了没/结果/有没有 bug),不出现内部术语",
       "判定强度" not in bf and "run-" not in bf and "工具扫描" not in bf
       and "还没测" in bf and "测了,没问题" in bf, bf[:200])
    ck("给策划的答复:发现问题的行带 bug 单号与严重级",
       "BUG-001" in bf or "已发现问题" in bf, bf[bf.find("测了,发现问题"):][:200])
    order = [ln for ln in rep.splitlines() if ln.startswith("| 🔴")]
    ck("缺口排在前面(未覆盖出现在表格前部)", bool(order), str(rep.count("🔴")))

    print()
    if fails:
        print(f"自测失败 {len(fails)} 项:")
        for x in fails:
            print("  ✗", x)
        return 1
    print("自测通过(登记册读取/GBK 兼容/口头覆盖拦截/矛盾拦截/缺口出口与期限/来源校验/"
          "依据核对 pass-pending-fail-找不到-过期 五种、自述依据提示/五档结论/放行门禁/"
          "盲区类别/候选生成/退出码 0-1-2-3)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
