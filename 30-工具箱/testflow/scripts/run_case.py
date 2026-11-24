#!/usr/bin/env python3
"""testflow 真机执行底座 + 判定路由。

三条设计要点:

1. **默认 plan-only,不碰设备。** 执行真机是有副作用的动作,必须显式 --execute。
2. **玩与判分离。** 这个脚本只产出 actual(原始观测值)与判定请求,
   永不输出 vision-closed / judgment 的通过与否。判定由独立的判定轮完成。
   只有 state-report 由这里的代码机械判定(代码判代码,不涉及"自己判卷")。
3. **绝不伪装成 pass。** 未实现的能力 -> skipped;证据缺失 -> blocked;
   未复核 -> blocked: not_reviewed。

纯标准库,零依赖。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from coordmap import (  # noqa: E402
    PROFILES, DeviceProfile, fit_content_rect, norm_to_device_pixels, norm_to_tap,
)
from validate_cases import FAULT_KINDS, FAULT_NEEDS_STATE_REPORT  # noqa: E402

IMPLEMENTED_TYPES = {"device-android", "manual"}
NET_IFACE_HINT = "rmnet_data0"

# 需要运行时视觉定位的动作 —— 无法只靠坐标换算完成
VISION_DEPENDENT = {"wait_for", "tap_anchor"}


def now_id() -> str:
    return "run-" + datetime.now(timezone.utc).astimezone().strftime("%Y%m%d-%H%M%S")


def resolve_adb(explicit: str | None) -> str:
    """找 adb。Windows 上是 adb.exe,而且未必在 PATH 里,所以要试两个名字。"""
    if explicit:
        return explicit
    for cand in ("adb", "adb.exe"):
        found = shutil.which(cand)
        if found:
            return found
    return "adb.exe" if os.name == "nt" else "adb"


def build_profile(device: dict) -> DeviceProfile:
    name = device.get("profile")
    if name and name in PROFILES:
        return PROFILES[name]
    res = device.get("resolution")
    if res and len(res) == 2:
        return DeviceProfile(name or "custom", int(res[0]), int(res[1]),
                             int(device.get("dpi") or 0), 1.0, "pixels")
    return PROFILES["android-mid"]


# ---------------------------------------------------------------- 动作 -> adb 命令

def plan_actions(actions: list[dict], prof: DeviceProfile, adb: str,
                 evidence_dir: Path, case_id: str, host_pkg: str,
                 emit_judge, evidence_map: list[dict]) -> tuple[list[str], list[str]]:
    """把动作列表翻成 adb 命令序列。返回 (commands, notes)。

    evidence_map 会按步骤记录截图产物路径,供判定项绑定证据用 ——
    这样"哪张截图属于哪条判定项"是可追溯的,而不是靠文件名去猜。
    """
    cmds: list[str] = []
    notes: list[str] = []

    for act in actions or []:
        kind = act.get("action")
        aid = act.get("id", "?")
        prefix = f"# {aid}" + (f" — {act['note']}" if act.get("note") else "")

        if kind == "launch_host":
            cmds.append(f"{prefix}\n{adb} shell monkey -p {host_pkg} "
                        f"-c android.intent.category.LAUNCHER 1")
        elif kind == "open_entry":
            entry = act.get("entry")
            notes.append(
                f"{aid}: 打开游戏入口 entry={entry!r}。**adb 无法直接打开微信/抖音小游戏** —— "
                f"需要预先把游戏固定在宿主首页入口,或人工扫码。"
                f"（这是真机自动化最脆弱的一环,见 references/8-platform-limits.md）"
            )
            # 既然这一步自动化不了,那就把"我确实在宿主首页"变成一条可验证的判定请求:
            # 盲点在错误界面上继续点,后面所有步骤与截图都会白采。
            host = {"com.tencent.mm": "微信",
                    "com.ss.android.ugc.aweme": "抖音"}.get(host_pkg, "微信/抖音")
            notes.append(
                f"{aid}: 已生成「宿主首页是否可见」的判定请求 —— "
                f"入口这一步必须人工配合,但**可以标出来从哪一步开始不可信**;"
                f"若该请求答否,后面的步骤与证据都不算数"
            )
            emit_judge({
                "purpose": "wait_for",
                "step": aid,
                "question": f"屏幕上是否已处于{host}首页(可见底部导航栏)?",
                "region": [0.0, 0.85, 1.0, 1.0],
                "timeout_ms": act.get("timeout_ms", 30000),
                "note": "入口由人工/预置方式完成后,先确认已回到宿主首页再继续;"
                        "答否则后面步骤全不可信",
                "top_level": True,
            })
        elif kind == "launch_app":
            cmds.append(f"{prefix}\n{adb} shell monkey -p {act.get('package')} "
                        f"-c android.intent.category.LAUNCHER 1")
        elif kind == "stop_app":
            cmds.append(f"{prefix}\n{adb} shell am force-stop {act.get('package')}")
        elif kind == "tap":
            tgt = act.get("target") or {}
            if tgt.get("norm"):
                x, y = norm_to_device_pixels(tuple(tgt["norm"]), prof)
                cmds.append(f"{prefix}\n{adb} shell input tap {x} {y}")
            elif tgt.get("anchor"):
                notes.append(f"{aid}: 锚点定位需要运行时视觉确认,见判定请求")
                emit_judge({
                    "purpose": "locate_anchor",
                    "step": aid,
                    "question": f"屏幕上是否可见包含「{tgt['anchor'].get('text')}」字样的元素?",
                    "region": tgt["anchor"].get("region"),
                    "note": "定位后按其中心 + offset 换算点击坐标",
                    "top_level": True,
                })
            else:
                notes.append(f"{aid}: tap 缺坐标,已跳过")
        elif kind == "swipe":
            frm, to = act.get("from"), act.get("to")
            if frm and to:
                x1, y1 = norm_to_device_pixels(tuple(frm), prof)
                x2, y2 = norm_to_device_pixels(tuple(to), prof)
                dur = int(act.get("duration_ms", 300))
                cmds.append(f"{prefix}\n{adb} shell input swipe {x1} {y1} {x2} {y2} {dur}")
                notes.append(f"{aid}: `input swipe` 用合成插值,部分手势识别器会拒绝")
        elif kind == "wait":
            secs = int(act.get("ms", 0)) / 1000.0
            cmds.append(f"{prefix}\nsleep {secs:g}")
        elif kind == "wait_for":
            emit_judge({
                "purpose": "wait_for",
                "step": aid,
                "question": act.get("question"),
                "region": act.get("region"),
                "timeout_ms": act.get("timeout_ms", 20000),
                "note": "轮询此封闭问题直到为 yes 或超时",
                "top_level": True,
            })
            notes.append(f"{aid}: 等条件而非等固定时长,需判定轮轮询")
        elif kind == "screenshot":
            name = act.get("name", aid)
            out = evidence_dir / f"{case_id}_{aid}_{name}.png"
            cmds.append(f"{prefix}\n{adb} exec-out screencap -p > \"{out}\"")
            evidence_map.append({"step": aid, "name": name, "path": str(out)})
        elif kind == "logcat":
            # 崩溃/ANR 的确定性证据来源。交给 scan_anomalies.py 扫描,
            # 与判定轮一样:证据先落盘,结论后出。
            name = act.get("name", aid)
            out = evidence_dir / f"{case_id}_{aid}_{name}.txt"
            clear = " -c" if act.get("clear_first") else ""
            cmds.append(f"{prefix}\n{adb} logcat{clear} -d -v threadtime > \"{out}\"")
            evidence_map.append({"step": aid, "name": name, "path": str(out)})
        elif kind == "perf":
            # 帧率/内存:纯 adb 采集,不需要客户端配合。
            # 诚实提醒:小游戏跑在宿主进程里,gfxinfo/meminfo 量到的是**宿主**进程级
            # 数据,不是小游戏单独的数据。跨机型、跨版本对比仍然成立。
            what = act.get("kind") or "gfxinfo"
            pkg = act.get("package") or host_pkg
            name = act.get("name", aid)
            out = evidence_dir / f"{case_id}_{aid}_{name}.txt"
            if act.get("reset"):
                cmds.append(f"# {aid} 先清零计数,只统计这一步之后的帧\n"
                            f"{adb} shell dumpsys gfxinfo {pkg} reset")
            if what == "meminfo":
                cmds.append(f"{prefix}\n{adb} shell dumpsys meminfo {pkg} > \"{out}\"")
            else:
                cmds.append(f"{prefix}\n{adb} shell dumpsys gfxinfo {pkg} > \"{out}\"")
            evidence_map.append({"step": aid, "name": name, "path": str(out)})
            notes.append(f"{aid}: 采集 {what}(宿主进程 {pkg})—— 帧率/内存随时间波动,"
                         f"建议多轮取中位数,并与同机型基线比")
        elif kind == "network":
            prof_name = act.get("profile", "weak")
            if prof_name == "reset":
                cmds.append(f"{prefix}\n{adb} shell tc qdisc del dev {NET_IFACE_HINT} root")
            else:
                notes.append(
                    f"{aid}: 弱网 profile={prof_name} 需按用例 network 块生成 tc/netem 命令;"
                    f"**Android 通常需 root**,网卡名需实测确认(假设 {NET_IFACE_HINT})"
                )
        elif kind == "reset_network":
            cmds.append(f"{prefix}\n{adb} shell tc qdisc del dev {NET_IFACE_HINT} root")
        elif kind == "replay":
            notes.append(f"{aid}: 回放轨迹 {act.get('trace')}")
        elif kind == "fault":
            fk = act.get("kind")
            notes.append(f"{aid}: 故障注入 {fk} —— {FAULT_KINDS.get(fk, '未知')}")
            if fk == "kill_after_step":
                cmds.append(
                    f"# {aid} 在 {act.get('after_step')} 之后杀掉宿主,制造未确认订单重启\n"
                    f"{adb} shell am force-stop {host_pkg}")
                cmds.append(f"# {aid} 重新拉起宿主,观察是否重放\n"
                            f"{adb} shell monkey -p {host_pkg} "
                            f"-c android.intent.category.LAUNCHER 1")
            elif fk == "offline_during_step":
                dur = int(act.get("duration_ms", 3000)) / 1000.0
                # svc wifi/data 是 AOSP 官方维护的向后兼容路径(内部翻译成
                # cmd wifi set-wifi-enabled / cmd phone data),不需要 root。
                # 查证记录见 references/8-platform-limits.md 文首。
                cmds.append(
                    f"# {aid} 断网 {dur:g}s 制造本地待处理队列\n"
                    f"# 国产 ROM 若拒绝 svc,备选:cmd connectivity airplane-mode enable\n"
                    f"{adb} shell svc wifi disable && {adb} shell svc data disable")
                cmds.append(f"sleep {dur:g}")
                cmds.append(f"{adb} shell svc wifi enable && {adb} shell svc data enable")
            elif fk in ("drop_response", "delay_response"):
                notes.append(
                    f"{aid}: adb 无法按请求粒度切断下行。可行手段按优先级:"
                    f"①游戏内网络层注入(mockNetwork,最可靠) "
                    f"②代理层规则(mitmproxy/Charles 按 URL 丢包或延迟) "
                    f"③设备侧 tc + iptables 按端口限时切断。"
                    f"**这条是本用例的核心条件,不落实则用例无效**"
                )
            elif fk == "replay_callback":
                notes.append(
                    f"{aid}: 重复投递回调 {act.get('target')} × {act.get('times')} —— "
                    f"**客户端无法注入,必须服务端或代理配合**。"
                    f"若两侧都做不到,这条用例只能标 blocked,不要假装覆盖"
                )
            elif fk == "duplicate_request":
                notes.append(
                    f"{aid}: 重放同一请求 × {act.get('times')} —— "
                    f"需游戏内注入或代理层重放(必须带同一请求 id,"
                    f"否则测的是两次独立下单,不是幂等)"
                )
            if fk in FAULT_NEEDS_STATE_REPORT:
                notes.append(f"{aid}: 该故障的后果在界面上不可见,判定必须走 state-report")
        elif kind == "adb_shell":
            save_as = act.get("save_as")
            if save_as:
                # 任意 adb 输出也能当证据:包版本号、机型信息、系统设置…
                # 没有这一步,"界面显示的版本号与包一致"这类叶子只能靠眼看。
                out = evidence_dir / f"{case_id}_{aid}_{save_as}.txt"
                cmds.append(f"{prefix}\n{adb} shell {act.get('cmd')} > \"{out}\"")
                evidence_map.append({"step": aid, "name": save_as, "path": str(out)})
            else:
                cmds.append(f"{prefix}\n{adb} shell {act.get('cmd')}")
        else:
            notes.append(f"{aid}: 未知动作 {kind!r},已跳过")

    return cmds, notes


def network_commands(net: dict, adb: str) -> list[str]:
    """由用例的 network 块生成限速命令(仅作计划,不执行)。"""
    if not net or net.get("profile") in (None, "online"):
        return []
    latency = net.get("latency_ms")
    jitter = net.get("jitter_ms")
    loss = net.get("loss_rate")
    if latency is None and loss is None:
        return []
    parts = []
    if latency is not None:
        parts.append(f"delay {int(latency)}ms" + (f" {int(jitter)}ms" if jitter else ""))
    if loss:
        parts.append(f"loss {int(float(loss) * 100)}%")
    return [
        f"# 弱网: {net.get('profile')} —— Android 侧精确限速(通常需 root)",
        f"{adb} shell tc qdisc add dev {NET_IFACE_HINT} root netem " + " ".join(parts),
        f"# 收尾: {adb} shell tc qdisc del dev {NET_IFACE_HINT} root",
    ]


# ---------------------------------------------------------------- state-report 判定

_MISSING = object()


def resolve_state(state: dict, path: str):
    """取状态值。先按字面 dotted key 直接查(采集端最常用扁平键),
    再退回逐级嵌套遍历。返回 _MISSING 表示确实没有这个字段。"""
    if not isinstance(state, dict):
        return _MISSING
    if path in state:
        return state[path]
    cur = state
    for seg in path.split("."):
        if isinstance(cur, dict) and seg in cur:
            cur = cur[seg]
        else:
            return _MISSING
    return cur


def judge_state_report(chk: dict, state: dict | None) -> tuple[str, object]:
    """state-report 由代码机械判定。返回 (verdict, actual)。

    证据缺失(没有上报数据)-> blocked,绝不是 pass。
    """
    if state is None:
        return "blocked", None
    path = chk.get("path")
    actual = resolve_state(state, path)
    if actual is _MISSING:
        return "blocked", None
    op = chk.get("op")
    want = chk.get("value")
    try:
        if op == "eq":
            ok = actual == want
        elif op == "ne":
            ok = actual != want
        elif op == "gt":
            ok = actual is not None and actual > want
        elif op == "gte":
            ok = actual is not None and actual >= want
        elif op == "lt":
            ok = actual is not None and actual < want
        elif op == "lte":
            ok = actual is not None and actual <= want
        elif op == "in":
            ok = actual in (want or [])
        elif op == "contains":
            ok = actual is not None and want in actual
        elif op == "matches":
            import re
            ok = actual is not None and re.search(str(want), str(actual)) is not None
        elif op == "exists":
            ok = actual is not None
        elif op == "nonempty_string":
            ok = isinstance(actual, str) and len(actual) > 0
        elif op == "unique_by":
            # actual 必须是列表,且每个元素的 value 字段互不重复(背包重复条目类不变量)
            if not isinstance(actual, list):
                return "blocked", actual
            keys = [item.get(want) if isinstance(item, dict) else None for item in actual]
            if any(k is None for k in keys):
                return "blocked", actual
            ok = len(keys) == len(set(keys))
        elif op == "within_seconds":
            if isinstance(actual, (int, float)):
                ts = float(actual)
            else:
                from datetime import datetime as _dt
                ts = _dt.fromisoformat(str(actual).replace("Z", "+00:00")).timestamp()
            delta = ts - datetime.now(timezone.utc).timestamp()
            ok = 0 <= delta <= float(want)
        else:
            return "blocked", actual
    except Exception:  # noqa: BLE001  类型不匹配等一律视为无法判定
        return "blocked", actual
    return ("pass" if ok else "fail"), actual


# ---------------------------------------------------------------- 单条用例

def evidence_for_check(chk: dict, case: dict, evidence_map: list[dict],
                       state_source: str | None) -> list[str]:
    """把证据绑定到具体判定项。

    - state-report 的证据是状态上报文件
    - vision-closed 的证据是**该判定项所观测的那一步**的截图(chk["after"])
    - replay 的证据是轨迹文件

    绑定不到就返回空列表 —— 上层必须明确报"证据未定位",
    **不许拿同一用例的其他截图充数**。那会让证据链看起来完整但实际对不上。
    """
    if chk.get("kind") != "assert":
        return []
    mode = chk.get("mode")
    if mode == "state-report":
        return [state_source] if state_source else []
    if mode == "vision-closed":
        after = chk.get("after")
        if not after:
            return []
        exact = [e["path"] for e in evidence_map if e.get("step") == after]
        if exact:
            return exact
        # 没有精确匹配时,取观测步骤**之后最近**的那次截图 ——
        # 视觉断言问的是"这一步之后画面长什么样",所以它后面的截图才是证据。
        # 观测顺序:前置在前、步骤在后 —— 判定项的 after 可以指向两者
        order = [a.get("id") for a in (case.get("preconditions") or []) if a.get("id")]
        order += [a.get("id") for a in (case.get("steps") or []) if a.get("id")]
        if after not in order:
            return []
        cut = order.index(after)
        later = [e for e in evidence_map
                 if e.get("step") in order and order.index(e["step"]) > cut]
        if not later:
            return []
        nearest = min(later, key=lambda e: order.index(e["step"]))
        return [nearest["path"]]
    if mode == "replay":
        trace = case.get("recorded_trace")
        return [trace] if trace else []
    return []


def run_case(case: dict, args, run_id: str, judge_requests: list) -> dict:
    cid = case.get("id", "?")
    ctype = case.get("type")
    review_state = ((case.get("review") or {}).get("state") or "draft")
    evidence_dir = args.out / "evidence"
    result: dict = {
        "id": cid,
        "title": case.get("title"),
        "type": ctype,
        "priority": case.get("priority"),
        "expect_source": case.get("expect_source"),
        "req_refs": case.get("req_refs") or [],
        "judge": case.get("judge"),
        "review_state": review_state,
        "device": case.get("device") or {},
        "network": case.get("network") or {},
        "determinism": case.get("determinism") or {},
        "checks": [],
        "plan": {"commands": [], "notes": []},
    }

    # ---- 复核闸门
    if review_state != "approved":
        result["status"] = "blocked"
        result["reason"] = f"not_reviewed (review.state={review_state})"
        result["checks"] = [
            {"source": c.get("source"), "kind": c.get("kind"),
             "verdict": "blocked", "actual": None,
             "note": "用例未经人工复核,不执行"}
            for c in case.get("checks") or []
        ]
        return result

    # ---- 执行器路由
    if ctype not in IMPLEMENTED_TYPES:
        result["status"] = "skipped"
        result["reason"] = f"executor not implemented: {ctype}"
        result["checks"] = [
            {"source": c.get("source"), "kind": c.get("kind"),
             "verdict": "skipped", "actual": None,
             "note": f"executor not implemented: {ctype}"}
            for c in case.get("checks") or []
        ]
        return result

    prof = build_profile(case.get("device") or {})
    host_pkg = {"wechat": "com.tencent.mm",
                "douyin": "com.ss.android.ugc.aweme"}.get(
        (case.get("device") or {}).get("host"), "com.tencent.mm")

    if ctype == "manual":
        result["status"] = "pending_manual"
        result["reason"] = "type=manual,需人工执行"
        result["checks"] = [
            {"source": c.get("source"), "kind": c.get("kind"),
             "verdict": "pending_manual", "actual": None,
             "note": c.get("reason")}
            for c in case.get("checks") or []
        ]
        return result

    # ---- 生成执行计划
    case_judges: list[dict] = []
    evidence_map: list[dict] = []
    is_explore = case.get("judge") == "explore"

    def emit(j: dict) -> None:
        j = dict(j, caseId=cid, mode=case.get("judge"),
                 exploreMode=is_explore)
        case_judges.append(j)
        judge_requests.append(j)

    pre_cmds, pre_notes = plan_actions(case.get("preconditions") or [], prof,
                                       args.adb, evidence_dir, cid, host_pkg,
                                       emit, evidence_map)
    step_cmds, step_notes = plan_actions(case.get("steps") or [], prof,
                                         args.adb, evidence_dir, cid, host_pkg,
                                         emit, evidence_map)

    net_cmds = network_commands(case.get("network") or {}, args.adb)

    # assert_convergence:让执行真正等待收敛信号,而不是靠固定 sleep。
    # 收敛信号的定义见 references/7-instrumentation.md
    # (游戏上报 session.state == connected 且 pendingOps 为空)。
    if (case.get("network") or {}).get("assert_convergence"):
        emit({
            "purpose": "convergence",
            "question": "游戏是否已上报重连完成信号(session.state=connected 且 pendingOps 为空)?",
            "note": "等到该信号出现再继续;等到超时则整条用例的相关判定应为 blocked,"
                    "不能按固定时长 sleep 之后就当收敛了",
            "top_level": True,
        })
        step_notes.append(
            "assert_convergence=true:已生成收敛等待判定请求。"
            "注意该信号需要客户端上报(见 references/7-instrumentation.md),"
            "若未接入则相关判定只能是 blocked"
        )

    result["plan"] = {
        "deviceProfile": prof.name,
        "devicePixels": [prof.pixel_w, prof.pixel_h],
        "tapSpace": prof.tap_space,
        "host": (case.get("device") or {}).get("host"),
        "hostPackage": host_pkg,
        "preconditions": pre_cmds,
        "network": net_cmds,
        "steps": step_cmds,
        "notes": pre_notes + step_notes,
        "evidenceDir": str(evidence_dir),
    }
    result["status"] = "planned" if not args.execute else "executed"
    result["exploreMode"] = is_explore
    result["evidenceIndex"] = evidence_map

    # ---- 判定项处理
    state = args.state_data
    for chk in case.get("checks") or []:
        kind = chk.get("kind")
        entry = {
            "source": chk.get("source"),
            "after": chk.get("after"),
            "kind": kind,
            "mode": chk.get("mode"),
            "expect": {k: v for k, v in chk.items()
                       if k in ("question", "region", "answer", "path", "op", "value", "reason")},
        }

        if kind == "manual":
            entry["verdict"] = "pending_manual"
            entry["actual"] = None
        elif kind == "judgment":
            # 交给独立判定轮,这里绝不判
            entry["verdict"] = "pending_judgment"
            entry["actual"] = {"evidence": [], "note": "待判定轮评估"}
            emit({
                "purpose": "judgment",
                "source": chk.get("source"),
                "question": chk.get("question"),
                "evidenceRequired": case.get("evidence") or [],
            })
        elif kind == "assert":
            mode = chk.get("mode")
            if mode == "state-report":
                verdict, actual = judge_state_report(chk, state)
                entry["verdict"] = verdict
                entry["actual"] = actual
                if verdict == "blocked":
                    entry["note"] = ("未见状态上报数据 —— 证据缺失只能判 blocked。"
                                     "需客户端状态上报,见 references/7-instrumentation.md")
            elif mode == "vision-closed":
                entry["verdict"] = "pending_judgment"
                entry["actual"] = {"screenshots": [], "note": "待判定轮读截图"}
                emit({
                    "purpose": "vision-closed",
                    "source": chk.get("source"),
                    "question": chk.get("question"),
                    "region": chk.get("region"),
                    "expectedAnswer": chk.get("answer"),
                    "evidenceRequired": ["screenshot"],
                })
            elif mode == "replay":
                entry["verdict"] = "blocked"
                entry["actual"] = None
                entry["note"] = ("回放比对未实现:需要先录制轨迹。"
                                 "证据缺失只能判 blocked")
            else:
                entry["verdict"] = "blocked"
                entry["actual"] = None
                entry["note"] = f"未知判定模式 {mode!r}"
        else:
            entry["verdict"] = "blocked"
            entry["actual"] = None
            entry["note"] = f"未知 kind {kind!r}"

        # ---- 证据绑定:判定项与它自己的证据挂钩,绑定不到就是空
        entry["evidence"] = evidence_for_check(
            chk, case, evidence_map, str(args.state) if args.state else None)

        # ---- 探索模式:不产出 pass/fail,只产出 ok/anomaly。
        # 见 references/5-execution.md —— explore 的目标是发现率,不是通过率,
        # 所以它既不计入通过率,也不开 bug 单(只出异常候选,由人复核)。
        if is_explore and entry.get("verdict") in ("pass", "fail"):
            entry["verdict"] = "ok" if entry["verdict"] == "pass" else "anomaly"

        result["checks"].append(entry)

    if case_judges:
        result["judgeRequests"] = case_judges

    # ---- 证据缺失检查:声明的证据类型 vs 实际采集动作。
    # 这是"写了但没采集"的防线:报告里声明了 logcat/crash 证据却没有任何采集动作,
    # 扫描时只会得到空文件,看起来像"扫过了没问题"。
    declared_ev = set(case.get("evidence") or [])
    all_cmds = "\n".join(step_cmds + pre_cmds)
    gaps: list[dict] = []
    for ev in sorted(declared_ev):
        if ev == "state_report":
            if state is None:
                gaps.append({"evidence": ev, "kind": "no_input",
                             "note": "没有传 --state,state-report 判定项会全部退化为 blocked"})
            continue
        marker = {"screenshot": "screencap", "logcat": "logcat",
                  "perf": "dumpsys"}.get(ev)
        if marker is None:
            gaps.append({"evidence": ev, "kind": "no_collector",
                         "note": f"{ev} 目前没有采集通路(未实现),别以为采到了"})
        elif marker not in all_cmds:
            gaps.append({"evidence": ev, "kind": "no_action",
                         "note": f"声明了 {ev} 证据,但动作里没有采集步骤"})
    if gaps:
        result["evidenceGaps"] = gaps
        result["plan"]["notes"].extend(
            f"证据缺口: {g['evidence']} —— {g['note']}" for g in gaps)
    return result


# ---------------------------------------------------------------- execute

SCREENCAP_REDIRECT_RE = re.compile(r'^(?P<left>.+?)\s*>\s*"?(?P<path>.+?)"?\s*$')
PNG_SIG = b"\x89PNG\r\n\x1a\n"


def execute_commands(cmds: list[str], out_dir: Path) -> list[dict]:
    """执行命令序列。

    **截图由 Python 自己写字节,不走 shell 重定向。**
    因为 PowerShell 的 `>` 会做文本转换,会把 PNG 二进制写坏 ——
    而且这种坏法不报错,只会留下一个损坏的图片文件,后面判定时才发现,
    很难归因。这里额外校验 PNG 签名,写坏的情况会被明确报出来。
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    log: list[dict] = []
    for c in cmds:
        line = "\n".join(l for l in c.splitlines() if not l.strip().startswith("#"))
        if not line.strip():
            continue
        line = line.strip()

        m = SCREENCAP_REDIRECT_RE.match(line)
        if m and "screencap" in m.group("left"):
            target = Path(m.group("path").strip().strip('"'))
            fp = subprocess.run(m.group("left"), shell=True, capture_output=True)  # noqa: S602
            ok = fp.returncode == 0 and fp.stdout[:8] == PNG_SIG
            if ok:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(fp.stdout)
                log.append({"cmd": m.group("left").strip(), "returncode": 0,
                            "stdout": f"<binary {len(fp.stdout)} bytes -> {target}>",
                            "stderr": ""})
            else:
                note = "" if fp.stdout[:8] == PNG_SIG else (
                    " [截图输出不是 PNG,已丢弃 —— 可能是 adb 报错,"
                    "或重定向被文本模式破坏]")
                log.append({"cmd": m.group("left").strip(),
                            "returncode": fp.returncode or 1,
                            "stdout": "",
                            "stderr": (fp.stderr or b"").decode("utf-8", "replace")[:2000]
                                      + note})
            continue

        if m and m.group("path").strip().strip('"').lower().endswith(".txt"):
            # 文本证据同样由 Python 写字节:空输出要报出来,不能留下一个
            # 0 字节的 logcat/dumpsys/version 文件让后面的扫描误以为"扫过了没问题"。
            # 判定条件用"目标是 .txt"而不是"命令里有 logcat":任意 adb 输出
            # (包版本号、机型信息)都应该能当证据。
            target = Path(m.group("path").strip().strip('"'))
            fp = subprocess.run(m.group("left"), shell=True, capture_output=True)  # noqa: S602
            text = fp.stdout.decode("utf-8", "replace")
            if fp.returncode == 0 and text.strip():
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(fp.stdout)
                log.append({"cmd": m.group("left").strip(), "returncode": 0,
                            "stdout": f"<text {len(fp.stdout)} bytes "
                                      f"{len(text.splitlines())} 行 -> {target}>",
                            "stderr": ""})
            else:
                log.append({"cmd": m.group("left").strip(),
                            "returncode": fp.returncode or 1, "stdout": "",
                            "stderr": (fp.stderr or b"").decode("utf-8", "replace")[:2000]
                                      + " [命令输出为空或失败,未落盘]"})
            continue

        fp = subprocess.run(line, shell=True, capture_output=True, text=True)  # noqa: S602
        log.append({"cmd": line, "returncode": fp.returncode,
                    "stdout": fp.stdout[-4000:], "stderr": fp.stderr[-2000:]})
    return log


# ---------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser(
        description="testflow 真机执行底座(默认只出计划,不执行)")
    ap.add_argument("--case", type=Path, action="append", required=True,
                    help="用例 json;可重复")
    ap.add_argument("--out", type=Path, required=True,
                    help="产出目录,通常是 test-artifacts/<feature>/04-runs/")
    ap.add_argument("--execute", action="store_true",
                    help="真正执行 adb 命令(有副作用,默认关闭)")
    ap.add_argument("--adb", default=None,
                    help="adb 可执行文件路径;默认自动查找 adb / adb.exe")
    ap.add_argument("--state", type=Path,
                    help="状态上报 json,用于让 state-report 判定项可机械判定")
    ap.add_argument("--json", action="store_true", help="把运行结果打到 stdout")
    args = ap.parse_args()

    args.adb = resolve_adb(args.adb)

    if args.execute and not (shutil.which(args.adb) or Path(args.adb).exists()):
        print(f"错误: --execute 需要 adb,但找不到 {args.adb!r}。"
              f"用 --adb 指定完整路径(Windows 上通常是 adb.exe)", file=sys.stderr)
        return 2

    args.state_data = None
    if args.state:
        if not args.state.exists():
            print(f"错误: --state 不存在: {args.state}", file=sys.stderr)
            return 2
        try:
            args.state_data = json.loads(args.state.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            print(f"错误: --state JSON 解析失败: {exc}", file=sys.stderr)
            return 2

    run_id = now_id()
    judge_requests: list[dict] = []
    results = []

    for cf in args.case:
        if not cf.exists():
            print(f"错误: 用例不存在: {cf}", file=sys.stderr)
            return 2
        try:
            case = json.loads(cf.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            print(f"错误: {cf} JSON 解析失败: {exc}", file=sys.stderr)
            return 2
        results.append(run_case(case, args, run_id, judge_requests))

    payload = {
        "runId": run_id,
        "startedAt": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "mode": "execute" if args.execute else "plan-only",
        "adb": args.adb,
        "stateSource": str(args.state) if args.state else None,
        "cases": results,
        "judgeRequests": judge_requests,
        "note": ("执行者只产出 actual 与判定请求,不产出通过与否。"
                 "vision-closed / judgment 必须由独立的判定轮完成。"),
    }

    if args.execute:
        cmds = [c for r in results for c in r["plan"]["steps"] + r["plan"]["preconditions"]]
        payload["executionLog"] = execute_commands(cmds, args.out)

    args.out.mkdir(parents=True, exist_ok=True)
    run_path = args.out / f"{run_id}.json"
    run_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")

    if judge_requests:
        jr_path = args.out / f"{run_id}.judge-requests.json"
        jr_path.write_text(json.dumps(judge_requests, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        tally: dict[str, int] = {}
        for r in results:
            tally[r["status"]] = tally.get(r["status"], 0) + 1
            for c in r["checks"]:
                k = c["verdict"]
                tally[k] = tally.get(k, 0) + 1

        print(f"运行 {run_id}  模式={payload['mode']}")
        print(f"用例 {len(results)} 条;判定请求 {len(judge_requests)} 个")
        print()
        for r in results:
            print(f"  {r['id']:24s} {r['status']:14s} {r.get('reason', '')}")
        print()
        print("状态统计:")
        for k in sorted(tally):
            print(f"  {k:18s} {tally[k]}")
        print()
        if payload["mode"] == "plan-only":
            print("注意: plan-only 模式未接触设备。加 --execute 才会真正执行。")
        print(f"结果已写出: {run_path}")
        if judge_requests:
            print(f"判定请求(交给独立判定轮): {jr_path}")
        print()
        print("提醒: vision-closed / judgment 的通过与否不由本脚本产出,")
        print("      必须由另一个独立的调用读截图与问题进行判定(玩判分离)。")

    return 0


if __name__ == "__main__":
    sys.exit(main())
