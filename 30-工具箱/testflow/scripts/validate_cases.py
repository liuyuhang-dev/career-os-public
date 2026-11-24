#!/usr/bin/env python3
"""testflow 三道闸门校验器。

纯标准库,零依赖。校验内容:
  1. 需求覆盖率  每条需求子句 -> >=1 条用例
  2. 期望覆盖率  每条 E-N -> 恰好 1 条判定项
  3. schema 与期望来源合法性

退出码: 0 = 通过(可能有警告); 1 = 有错误; 2 = 用法错误。

"没证据不许判通过"在这里体现为:校验不通过就不许进入执行阶段。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------- 常量

CASE_TYPES = {"device-android", "device-ios", "device-cloud",
              "unity-headless", "backend-api", "manual"}
PRIORITIES = {"P0", "P1", "P2", "P3"}

# 期望来源。注意这里永远不能有 code / observed —— 见 references/10-upstreams.md
EXPECT_SOURCES = {"requirement", "invariant", "config", "consistency"}
EXPECT_SOURCE_REFS = {
    "requirement": "req_refs",
    "config": "config_refs",
    "consistency": "consistency_refs",
}
FORBIDDEN_SOURCES = {
    "code": "代码只回答『测哪里』,不回答『应该是什么』。用实现推期望会让用例永远通过",
    "implementation": "同上:实现不是应然判断",
    "observed": "观察到的现有行为是事实,不是应然判断。会把现有 bug 固化成期望",
    "observation": "同上",
    "runtime": "运行时数据是实然,不是应然",
}
ALL_REF_FIELDS = ("req_refs", "config_refs", "consistency_refs")

JUDGE_MODES = {"explore", "vision-closed", "replay", "state-report"}
REVIEW_STATES = {"draft", "approved", "rejected"}
CHECK_KINDS = {"assert", "judgment", "manual"}
ASSERT_MODES = {"vision-closed", "state-report", "replay"}
OPS = {"eq", "ne", "gt", "gte", "lt", "lte", "in", "contains", "matches",
       "exists", "nonempty_string", "within_seconds", "unique_by"}
OPS_NEEDING_VALUE = OPS - {"exists", "nonempty_string"}
EVIDENCE_TYPES = {"screenshot", "trace", "logcat", "state_report", "perf", "crash"}

# 运行时判定结果 —— 这是"输出的那一半"的契约。
# 权威文档是 references/6-triage.md 的「结果状态」表,check_docs.py 会机械对账:
#   pass / fail        结论明确(且必须绑定证据)
#   blocked            证据缺失或前置条件不满足 —— 绝不是 pass
#   skipped            执行器未实现或平台不支持
#   pending_judgment   待判定轮(独立调用,judge.py)评估
#   pending_manual     待人工确认
#   ok / anomaly       explore 用例专用,不计入通过率
VERDICTS = {"pass", "fail", "blocked", "skipped", "pending_judgment",
            "pending_manual", "ok", "anomaly"}
# 只有 assert 判定失败才开 bug 单;其余状态反映我们自己的能力缺口,不是产品缺陷
BUG_OPENING_VERDICTS = {"fail"}

ACTIONS = {"launch_host", "open_entry", "launch_app", "stop_app", "tap", "swipe",
           "wait", "wait_for", "screenshot", "logcat", "perf", "network", "reset_network",
           "replay", "adb_shell", "fault"}

# 故障注入。用于制造"真实但难以撞到"的时序条件(重复支付、回调重投、队列重放)
FAULT_KINDS = {
    "drop_response":       "请求发出后、响应到达前切断下行 —— 制造客户端重试",
    "delay_response":      "延迟响应直到客户端超时 —— 制造超时重试",
    "kill_after_step":     "在某步之后杀掉客户端 —— 制造未确认订单重启重放",
    "offline_during_step": "在某步前后离线一段时间 —— 制造本地待处理队列",
    "replay_callback":     "重复投递外部回调 —— 需服务端或代理配合",
    "duplicate_request":   "重复发送同一请求(同请求 id) —— 验证服务端幂等",
}
FAULT_REQUIRED_FIELDS = {
    "drop_response": ["after_step", "window_ms"],
    "delay_response": ["after_step", "delay_ms"],
    "kill_after_step": ["after_step"],
    "offline_during_step": ["around_step", "duration_ms"],
    "replay_callback": ["target", "times"],
    "duplicate_request": ["target_step", "times"],
}
# 这些故障的效果在界面上不可见,必须配 state-report 判定
FAULT_NEEDS_STATE_REPORT = {"drop_response", "delay_response", "kill_after_step",
                            "replay_callback", "duplicate_request"}

# perf 动作采集什么(帧率 / 内存),由 scan_anomalies.py 解析
PERF_KINDS = {"gfxinfo", "meminfo"}

# 伪覆盖:用"连续点击"冒充重复支付验证
DUPLICATE_KEYWORDS = ["重复支付", "重复扣款", "重复发货", "重复结算", "重复入账",
                      "重复领取", "双倍", "幂等", "重投", "重复提交",
                      "duplicate payment", "idempotent", "double charge"]

# 用例 ID 前缀。权威文档是 references/3-cases.md 的「用例 ID 约定」表 ——
# 两处不一致时,人会照着文档写出一个过不了闸门的 ID(第三方会话已经踩过:
# 文档里的 CONS- 示例被 ID_RE 拒掉,只能把三条一致性用例改名成 INV-,
# 而「来源」这一维在复核时正是要能一眼分辨的)。check_docs.py 对账这两处。
CASE_ID_PREFIXES = ("REQ", "INV", "EXP", "CONS")
ID_RE = re.compile(rf"^({'|'.join(CASE_ID_PREFIXES)})-[A-Za-z0-9_.-]+$")
ID_PREFIX_HINT = "/".join(f"{p}-*" for p in CASE_ID_PREFIXES)
SRC_RE = re.compile(r"^E([0-9]+)$")
ITEM_RE = re.compile(r"^\s*-\s*([A-Za-z]+)-?(\d+)\s*[:：]\s*(.+?)\s*$")
META_RE = re.compile(r"^\s*-\s*\*\*(.+?)\*\*\s*[:：]\s*(.*?)\s*$")
KV_RE = re.compile(r"^\s*-\s*([A-Za-z_][A-Za-z0-9_]*)\s*[:：]\s*(.+?)\s*$")
HEADING_RE = re.compile(r"^##\s+(.+?)\s*$")

# 疑似开放提问:这些词没法判定真假,除非同一句里引用了屏幕上的具体文案
VAGUE_WORDS = ["正常", "合理", "友好", "良好", "恰当", "合适", "完善",
               "流畅", "美观", "清楚", "清晰", "明确"]
# 主观期望:真机自动化判不了手感/观感,这类只能标 manual(见 8-platform-limits.md)
SUBJECTIVE_WORDS = ["手感", "打击感", "爽感", "体感", "观感", "好看", "漂亮",
                    "舒服", "流畅度", "体验好"]
QUOTED_RE = re.compile(r"[「『\"'【](.+?)[」』\"'】]")

REQUIRED_CASE_FIELDS = ["id", "spec", "title", "type", "priority",
                        "expect_source", "judge", "review", "steps", "checks"]


class Report:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.notes: list[str] = []
        self._seen_notes: set[str] = set()

    def err(self, code: str, msg: str) -> None:
        self.errors.append(f"[{code}] {msg}")

    def warn(self, code: str, msg: str) -> None:
        self.warnings.append(f"[{code}] {msg}")

    def note(self, msg: str) -> None:
        # 同一条提示只说一次 —— 逐用例重复会把真正要看的东西淹掉
        if msg in self._seen_notes:
            return
        self._seen_notes.add(msg)
        self.notes.append(msg)


# ---------------------------------------------------------------- 自然语言用例解析

def strip_comments(text: str) -> str:
    """去掉 HTML 注释(模板里的使用规则),避免被当成正文解析。"""
    return re.sub(r"<!--.*?-->", "", text, flags=re.S)


def parse_spec_file(path: Path) -> dict[str, dict]:
    """解析一份自然语言用例文件,返回 {case_id: {title, meta, items, sections}}。"""
    raw = strip_comments(path.read_text(encoding="utf-8"))
    # 按一级标题切分多条用例
    blocks = re.split(r"^#\s+用例\s*[:：]", raw, flags=re.M)
    out: dict[str, dict] = {}
    for block in blocks[1:]:
        lines = block.split("\n")
        title = lines[0].strip() if lines else ""
        meta: dict[str, str] = {}
        items: dict[str, str] = {}
        sections: dict[str, list[str]] = {}
        section_kv: dict[str, dict[str, str]] = {}
        current: str | None = None

        for ln in lines[1:]:
            m = HEADING_RE.match(ln)
            if m:
                current = m.group(1).strip()
                sections.setdefault(current, [])
                section_kv.setdefault(current, {})
                continue

            m = META_RE.match(ln)
            if m:
                meta[m.group(1).strip()] = m.group(2).strip()
                continue

            m = ITEM_RE.match(ln)
            if m:
                key = f"{m.group(1).upper()}{m.group(2)}"
                items[key] = m.group(3).strip()
                continue

            m = KV_RE.match(ln)
            if m and current:
                section_kv[current][m.group(1)] = m.group(2).strip()
                continue

            if current and ln.strip():
                sections[current].append(ln.strip())

        case_id = meta.get("ID", "").strip()
        if not case_id:
            continue
        out[case_id] = {
            "title": title,
            "meta": meta,
            "items": items,
            "sections": sections,
            "section_kv": section_kv,
            "path": str(path),
        }
    return out


def collect_specs(target: Path) -> dict[str, dict]:
    files: list[Path] = []
    if target.is_dir():
        files = sorted(target.rglob("*.md"))
    elif target.is_file():
        files = [target]
    index: dict[str, dict] = {}
    for f in files:
        try:
            parsed = parse_spec_file(f)
        except Exception as exc:  # noqa: BLE001
            print(f"警告: 无法解析 {f}: {exc}", file=sys.stderr)
            continue
        for cid, info in parsed.items():
            index[cid] = info
    return index


def collect_case_files(target: Path) -> list[Path]:
    if target.is_dir():
        return sorted(target.rglob("*.json"))
    if target.is_file():
        return [target]
    return []


# ---------------------------------------------------------------- 校验

def validate_case(case: dict, src: Path, spec_index: dict, report: Report) -> set[str]:
    """校验单条用例。返回该用例声明的 req_refs 集合。"""
    tag = case.get("id") or src.name

    for field in REQUIRED_CASE_FIELDS:
        if field not in case:
            report.err("MISSING_FIELD", f"{tag}: 缺必填字段 `{field}`")
    if "id" not in case:
        return set()

    if not ID_RE.match(str(case.get("id", ""))):
        report.err("BAD_ID", f"{tag}: id 命名不符 `{ID_PREFIX_HINT}`,实为 {case.get('id')!r}")

    if case.get("type") not in CASE_TYPES:
        report.err("BAD_TYPE", f"{tag}: type={case.get('type')!r} 不在 {sorted(CASE_TYPES)}")
    if case.get("priority") not in PRIORITIES:
        report.err("BAD_PRIORITY", f"{tag}: priority={case.get('priority')!r} 不在 {sorted(PRIORITIES)}")
    if case.get("judge") not in JUDGE_MODES:
        report.err("BAD_JUDGE", f"{tag}: judge={case.get('judge')!r} 不在 {sorted(JUDGE_MODES)}")

    # ---- 期望来源合法性(两条红线:观察到的行为不能当期望,代码实现也不能)
    es = case.get("expect_source")
    req_refs = case.get("req_refs") or []
    if es in FORBIDDEN_SOURCES:
        report.err(
            "ILLEGAL_EXPECT_SOURCE",
            f"{tag}: expect_source={es!r} —— {FORBIDDEN_SOURCES[es]}",
        )
    elif es not in EXPECT_SOURCES:
        report.err(
            "ILLEGAL_EXPECT_SOURCE",
            f"{tag}: expect_source={es!r}。只能是 "
            f"{sorted(EXPECT_SOURCES)};`code` 与 `observed` 是明确禁止的",
        )
    else:
        # 该来源必须带的引用字段不能为空
        need = EXPECT_SOURCE_REFS.get(es)
        if need and not (case.get(need) or []):
            report.err(
                f"MISSING_{need.upper()}",
                f"{tag}: expect_source={es} 但 {need} 为空,无法回溯到上游源",
            )
        # 不该出现的引用字段不能填 —— 防止把不变量伪装成需求、或把配置伪装成需求
        for field in ALL_REF_FIELDS:
            if field == need:
                continue
            if case.get(field):
                report.err(
                    "SPURIOUS_REFS",
                    f"{tag}: expect_source={es} 却填了 {field}={case[field]}。"
                    f"来源与引用字段必须对应,不要混淆来源类型",
                )

    # ---- 复核状态
    review = case.get("review")
    if not isinstance(review, dict):
        report.err("BAD_REVIEW", f"{tag}: review 必须是对象")
    else:
        state = review.get("state")
        if state not in REVIEW_STATES:
            report.err("BAD_REVIEW_STATE",
                       f"{tag}: review.state={state!r} 不在 {sorted(REVIEW_STATES)}")
        elif state != "approved":
            report.note(f"{tag}: review.state={state} —— 执行时会返回 blocked: not_reviewed")

    # ---- 动作
    step_ids = {a.get("id") for a in (case.get("steps") or [])
                if isinstance(a, dict) and a.get("id")}
    # 判定项的 after 可以指向 precondition 或 step —— 两者都会落证据
    place_ids = step_ids | {a.get("id") for a in (case.get("preconditions") or [])
                            if isinstance(a, dict) and a.get("id")}
    has_fault = False
    for group in ("preconditions", "steps"):
        actions = case.get(group) or []
        if not isinstance(actions, list):
            report.err("BAD_ACTIONS", f"{tag}: {group} 必须是数组")
            continue
        for i, act in enumerate(actions):
            if not isinstance(act, dict):
                report.err("BAD_ACTION", f"{tag}: {group}[{i}] 不是对象")
                continue
            name = act.get("action")
            if name not in ACTIONS:
                report.err("BAD_ACTION_NAME",
                           f"{tag}: {group}[{i}].action={name!r} 不在动作词汇表内")
                continue
            if name == "tap":
                tgt = act.get("target") or {}
                if not (tgt.get("norm") or tgt.get("anchor")):
                    report.err("TAP_NO_TARGET",
                               f"{tag}: {group}[{i}] tap 缺 target.norm 或 target.anchor")
                norm = tgt.get("norm")
                if isinstance(norm, list) and norm and any(
                        isinstance(v, (int, float)) and v > 1 for v in norm):
                    report.warn("PIXEL_COORDS",
                                f"{tag}: {group}[{i}] norm={norm} 出现 >1 的值,"
                                f"坐标必须归一化到 0..1")
            if name == "swipe" and not (act.get("from") and act.get("to")):
                report.err("SWIPE_NO_COORDS", f"{tag}: {group}[{i}] swipe 缺 from/to")
            if name == "wait_for" and not act.get("question"):
                report.err("WAITFOR_NO_QUESTION",
                           f"{tag}: {group}[{i}] wait_for 缺 question")
            if name == "wait" and not isinstance(act.get("ms"), int):
                report.err("WAIT_NO_MS", f"{tag}: {group}[{i}] wait 缺整数 ms")

            if name == "fault":
                has_fault = True
                fk = act.get("kind")
                if fk not in FAULT_KINDS:
                    report.err("BAD_FAULT_KIND",
                               f"{tag}: {group}[{i}] fault.kind={fk!r} 不在 "
                               f"{sorted(FAULT_KINDS)}")
                    continue
                if group == "preconditions":
                    report.warn("FAULT_IN_PRECONDITION",
                                f"{tag}: {group}[{i}] 故障注入放在 preconditions,"
                                f"时序语义无意义,应放在 steps 中紧贴目标步骤")
                for req in FAULT_REQUIRED_FIELDS[fk]:
                    if act.get(req) in (None, ""):
                        report.err("FAULT_MISSING_FIELD",
                                   f"{tag}: {group}[{i}] fault kind={fk} 缺 {req}")
                for ref_field in ("after_step", "around_step", "target_step"):
                    ref = act.get(ref_field)
                    if ref and ref not in step_ids:
                        report.err("FAULT_BAD_STEP_REF",
                                   f"{tag}: {group}[{i}].{ref_field}={ref!r} "
                                   f"在 steps 里不存在(可用: {sorted(step_ids)})")

            if act.get("action") == "perf":
                what = act.get("kind")
                if what not in PERF_KINDS:
                    report.err("PERF_BAD_KIND",
                               f"{tag}: {group}[{i}].kind={what!r} 不在 "
                               f"{sorted(PERF_KINDS)}(perf 采集什么)")

    # ---- 判定项
    checks = case.get("checks")
    if not isinstance(checks, list) or not checks:
        report.err("NO_CHECKS", f"{tag}: checks 必须是非空数组")
        return set(req_refs)

    sources: list[str] = []
    for i, chk in enumerate(checks):
        where = f"{tag}: checks[{i}]"
        if not isinstance(chk, dict):
            report.err("BAD_CHECK", f"{where} 不是对象")
            continue
        src_id = chk.get("source")
        if not isinstance(src_id, str) or not SRC_RE.match(src_id):
            report.err("BAD_CHECK_SOURCE",
                       f"{where}.source={src_id!r} 必须形如 E1")
        else:
            sources.append(src_id)

        kind = chk.get("kind")
        if kind not in CHECK_KINDS:
            report.err("BAD_CHECK_KIND", f"{where}.kind={kind!r} 不在 {sorted(CHECK_KINDS)}")
            continue

        if kind == "assert":
            mode = chk.get("mode")
            if mode not in ASSERT_MODES:
                report.err("BAD_ASSERT_MODE",
                           f"{where}.mode={mode!r} 不在 {sorted(ASSERT_MODES)}")
                continue
            if mode == "vision-closed":
                q = chk.get("question")
                if not q:
                    report.err("VISION_NO_QUESTION", f"{where} vision-closed 缺 question")
                elif not QUOTED_RE.search(q):
                    if any(w in q for w in VAGUE_WORDS):
                        report.warn(
                            "OPEN_QUESTION",
                            f"{where} 疑似开放提问:{q!r} —— "
                            f"封闭问题应引用屏幕上的具体文案(逐字取自 UI 元素词汇表)",
                        )
                region = chk.get("region")
                if not (isinstance(region, list) and len(region) == 4):
                    report.err("VISION_NO_REGION",
                               f"{where} vision-closed 缺 region(需 4 个归一化数值)")
                elif any(not isinstance(v, (int, float)) or v < 0 or v > 1 for v in region):
                    report.err("VISION_BAD_REGION",
                               f"{where} region={region} 必须是 0..1 归一化坐标")
                if chk.get("answer") not in ("yes", "no"):
                    report.err("VISION_NO_ANSWER",
                               f"{where} vision-closed 的 answer 必须是 yes 或 no")
                if not chk.get("after"):
                    report.err("VISION_NO_AFTER",
                               f"{where} vision-closed 缺 after —— 证据按 after 绑到步骤上,"
                               f"缺了它截图永远绑不上,判定只会是 blocked")
            elif mode == "state-report":
                if not chk.get("path"):
                    report.err("STATE_NO_PATH", f"{where} state-report 缺 path")
                op = chk.get("op")
                if op not in OPS:
                    report.err("STATE_BAD_OP", f"{where}.op={op!r} 不在 {sorted(OPS)}")
                elif op in OPS_NEEDING_VALUE and "value" not in chk:
                    report.err("STATE_NO_VALUE", f"{where} op={op} 需要 value")
            elif mode == "replay":
                if not case.get("recorded_trace"):
                    report.err("REPLAY_NO_TRACE",
                               f"{where} judge/mode 为 replay 但用例缺 recorded_trace")

        elif kind == "judgment":
            if not chk.get("question"):
                report.err("JUDGMENT_NO_QUESTION", f"{where} judgment 缺 question")
            else:
                q = chk["question"]
                if not QUOTED_RE.search(q) and any(w in q for w in VAGUE_WORDS):
                    report.warn("OPEN_QUESTION",
                                f"{where} 疑似开放提问:{q!r} —— 需要是/否可答的封闭问题")
            if not chk.get("after"):
                report.err("JUDGMENT_NO_AFTER",
                           f"{where} judgment 缺 after —— 判定轮靠它把截图绑到这条判定项上;"
                           f"缺了它只能判 blocked(见 references/6-triage.md)")

        elif kind == "manual":
            if not chk.get("reason"):
                report.err("MANUAL_NO_REASON",
                           f"{where} manual 必须给出 reason(为什么必须人工)")

        # after 必须指向真实存在的步骤/前置 —— 写错了不会报错,只会让证据永远绑不上,
        # 判定静静退化成 blocked,覆盖看起来是满的。这类"静默降级"必须在这里拦住。
        after = chk.get("after")
        if isinstance(after, str) and after and place_ids and after not in place_ids:
            report.err("CHECK_AFTER_UNKNOWN",
                       f"{where}.after={after!r} 在 steps/preconditions 里不存在"
                       f"(可用: {sorted(place_ids)})")

    # ---- 覆盖率(期望):与实际存在的 E 编号比对
    spec = spec_index.get(case.get("id", ""))
    if spec is None:
        report.err(
            "SPEC_SOURCE_NOT_FOUND",
            f"{tag}: 在 specs 里找不到 ID={case.get('id')!r} 的自然语言用例。"
            f"期望覆盖率无法校验 —— 禁止跳过这一步",
        )
    else:
        e_ids = {k for k in spec["items"] if k.startswith("E")}
        declared = set(sources)
        missing = sorted(e_ids - declared, key=lambda s: int(s[1:]))
        orphan = sorted(declared - e_ids, key=lambda s: int(s[1:]))
        if missing:
            report.err(
                "MISSING_EXPECTATION_COVERAGE",
                f"{tag}: 期望 {', '.join(missing)} 没有对应的判定项 —— "
                f"这通常意味着最难验证的那条期望被丢掉了",
            )
        if orphan:
            report.err(
                "ORPHAN_CHECK",
                f"{tag}: 判定项 source={', '.join(orphan)} 在自然语言用例里不存在",
            )
        if len(declared) != len(sources):
            report.err("DUPLICATE_CHECK_SOURCE",
                       f"{tag}: 同一个 E 编号被多条判定项引用")

    # ---- 弱网 + 确定性
    net = case.get("network") or {}
    has_state_report_any = any(
        c.get("kind") == "assert" and c.get("mode") == "state-report"
        for c in checks if isinstance(c, dict))

    if net.get("assert_convergence"):
        if not net.get("reconnect"):
            report.warn("CONVERGENCE_NO_RECONNECT",
                        f"{tag}: assert_convergence=true 但 reconnect 未开,收敛断言无意义")
        # 声明要断言收敛,就必须真有对应断言。否则只是声明而已。
        if not has_state_report_any:
            report.err(
                "CONVERGENCE_WITHOUT_ASSERTION",
                f"{tag}: network.assert_convergence=true,但没有任何 state-report 判定项。"
                f"收敛是内部一致性问题,界面上看不出来 —— 只有状态上报能断言它。"
                f"要么补一条针对收敛信号的断言(如 session.state / pendingOps),"
                f"要么去掉 assert_convergence",
            )

    determinism = case.get("determinism") or {}
    if has_state_report_any and not determinism:
        report.warn("NO_DETERMINISM",
                    f"{tag}: 用 state-report 判定但未声明 determinism,结果可能抖动")
    if determinism:
        # 诚实标注:这个字段目前只是被记录,没有任何机制把它传给游戏。
        report.note(
            f"{tag}: determinism 已声明,但**尚未实现传递到游戏**(seed / 固定时间步"
            f"目前只是元数据)。意味着当前无法真正固定随机性,涉及随机逻辑的断言仍可能抖动。"
        )
    if not any(c.get("kind") == "assert" for c in checks if isinstance(c, dict)):
        report.warn("NO_ASSERT",
                    f"{tag}: 全条用例没有任何 assert 判定项 —— 没有任何自动可判定的东西")

    # ---- 幂等与重复执行类的两个护栏(见 references/11-idempotency.md)
    has_state_report = any(
        c.get("kind") == "assert" and c.get("mode") == "state-report"
        for c in checks if isinstance(c, dict))

    if has_fault and not has_state_report:
        report.warn(
            "FAULT_WITHOUT_STATE_REPORT",
            f"{tag}: 用了故障注入但没有任何 state-report 判定。"
            f"故障注入的后果在界面上通常不可见(重复扣款、重复发货、"
            f"扣两次只发一次货),用视觉判定等于没判 —— 必须配状态上报",
        )

    expect_texts = " ".join((spec["items"].values() if spec else []))
    blob = f"{case.get('title', '')} {expect_texts} {case.get('notes') or ''}"
    hit = [k for k in DUPLICATE_KEYWORDS if k in blob]
    if hit and not has_fault:
        report.warn(
            "PSEUDO_DUPLICATE_COVERAGE",
            f"{tag}: 期望涉及重复执行类缺陷({', '.join(hit)}),"
            f"但没有任何故障注入(fault)动作。真实场景来自响应丢失重试、"
            f"回调重投、队列重放、进程重启,用「连续点击 N 次」模拟是伪覆盖 —— "
            f"它会制造『已覆盖』的虚假信心,比不测更糟",
        )

    # ---- 期望本身能不能验?
    # 这两类期望要到用例生成阶段才暴露问题,太晚了:那时候"不可验证"已经被写成
    # 一条模糊的判定项(或一条 judgment),后面谁也没法判。在规格层面就提醒。
    for key, text in sorted((spec["items"].items() if spec else [])):
        if not key.startswith("E"):
            continue
        vague = [w for w in VAGUE_WORDS if w in text]
        if vague and not re.search(r"\d", text):
            report.warn(
                "SPEC_VAGUE_EXPECTATION",
                f"{tag}: {key} 用了模糊词 {vague} 且没有可量化的数值 —— "
                f"这种期望无法转成是/否问题,大概率会变成一条判不了的判定项。"
                f"拆成可观测的具体条件(哪一屏、出现什么文案、数值是多少)",
            )
        subj = [w for w in SUBJECTIVE_WORDS if w in text]
        # 人工用例本来就该装主观期望(手感/观感),它旁边写着 reason 和谁来确认 ——
        # 上面那句提示要的是"挪进 type=manual",用例已经是了就别再提醒一遍。
        spec_is_manual = bool(spec) and spec["meta"].get("类型") == "manual"
        if subj and not spec_is_manual:
            report.warn(
                "SPEC_SUBJECTIVE_EXPECTATION",
                f"{tag}: {key} 含主观期望 {subj} —— 真机自动化判不了手感/观感。"
                f"这类期望应写成 type=manual 的用例并写明 reason(为什么必须人工),"
                f"而不是留在需求用例里等判定轮猜",
            )

    # ---- 证据
    ev = case.get("evidence") or []
    bad_ev = [e for e in ev if e not in EVIDENCE_TYPES]
    if bad_ev:
        report.warn("UNKNOWN_EVIDENCE",
                    f"{tag}: evidence 含未知类型 {bad_ev}")

    return set(req_refs)


def check_requirement_coverage(requirements: dict, seen_refs: set[str], report: Report) -> None:
    clauses = requirements.get("clauses") or []
    if not clauses:
        report.warn("NO_CLAUSES", "需求文件里没有 clauses,需求覆盖率无法计算")
        return
    clause_ids = set()
    for c in clauses:
        cid = c.get("id")
        if not cid:
            report.err("CLAUSE_NO_ID", f"需求子句缺 id: {c!r}")
            continue
        if cid in clause_ids:
            report.err("DUPLICATE_CLAUSE_ID", f"需求子句 id 重复: {cid}")
        clause_ids.add(cid)

    uncovered = sorted(clause_ids - seen_refs)
    if uncovered:
        report.err(
            "MISSING_REQUIREMENT_COVERAGE",
            f"以下需求子句没有任何用例覆盖: {', '.join(uncovered)}",
        )
    unknown = sorted(seen_refs - clause_ids)
    if unknown:
        report.err(
            "UNKNOWN_REQ_REF",
            f"用例引用了不存在的需求子句: {', '.join(unknown)}",
        )


# ---------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser(description="testflow 三道闸门校验器")
    ap.add_argument("--requirements", type=Path,
                    help="01-requirements.json;不给则跳过需求覆盖率")
    ap.add_argument("--specs", type=Path, required=True,
                    help="自然语言用例文件或目录")
    ap.add_argument("--cases", type=Path, required=True,
                    help="派生用例 json 文件或目录")
    ap.add_argument("--strict-warnings", action="store_true",
                    help="把警告也当作错误(退出码 1)")
    ap.add_argument("--format", choices=["text", "json"], default="text")
    args = ap.parse_args()

    report = Report()

    if not args.specs.exists():
        print(f"错误: --specs 路径不存在: {args.specs}", file=sys.stderr)
        return 2
    if not args.cases.exists():
        print(f"错误: --cases 路径不存在: {args.cases}", file=sys.stderr)
        return 2

    spec_index = collect_specs(args.specs)
    case_files = collect_case_files(args.cases)
    if not case_files:
        print(f"错误: {args.cases} 下没有 .json 用例", file=sys.stderr)
        return 2

    requirements: dict | None = None
    if args.requirements:
        if not args.requirements.exists():
            print(f"错误: 需求文件不存在: {args.requirements}", file=sys.stderr)
            return 2
        try:
            requirements = json.loads(args.requirements.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            report.err("REQUIREMENTS_JSON_INVALID", f"{args.requirements}: JSON 解析失败: {exc}")

    seen_ids: set[str] = set()
    seen_refs: set[str] = set()
    loaded = 0

    for cf in case_files:
        try:
            case = json.loads(cf.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            report.err("CASE_JSON_INVALID", f"{cf}: JSON 解析失败: {exc}")
            continue
        if not isinstance(case, dict):
            report.err("CASE_NOT_OBJECT", f"{cf}: 顶层必须是对象")
            continue
        loaded += 1
        cid = case.get("id")
        if cid in seen_ids:
            report.err("DUPLICATE_CASE_ID", f"{cf}: 用例 ID 重复: {cid}")
        if cid:
            seen_ids.add(cid)
        seen_refs |= validate_case(case, cf, spec_index, report)

    if requirements is not None:
        check_requirement_coverage(requirements, seen_refs, report)
    else:
        report.note("未提供 --requirements,需求覆盖率已跳过(无需求文档时属正常)")

    # ---- 输出
    if args.format == "json":
        print(json.dumps({
            "cases_loaded": loaded,
            "spec_cases_found": len(spec_index),
            "errors": report.errors,
            "warnings": report.warnings,
            "notes": report.notes,
            "ok": not report.errors,
        }, ensure_ascii=False, indent=2))
    else:
        print(f"用例载入 {loaded} 条;specs 中发现 {len(spec_index)} 条自然语言用例")
        print()
        if report.errors:
            print(f"错误 {len(report.errors)} 条:")
            for e in report.errors:
                print(f"  ✗ {e}")
            print()
        if report.warnings:
            print(f"警告 {len(report.warnings)} 条:")
            for w in report.warnings:
                print(f"  ! {w}")
            print()
        if report.notes:
            print("提示:")
            for n in report.notes:
                print(f"  · {n}")
            print()

        if report.errors:
            print("结论: 闸门未通过,禁止进入执行阶段。")
        elif report.warnings and args.strict_warnings:
            print("结论: 仅有警告,但 --strict-warnings 生效,视为未通过。")
        else:
            print("结论: 三道闸门通过。")

    if report.errors:
        return 1
    if args.strict_warnings and report.warnings:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
