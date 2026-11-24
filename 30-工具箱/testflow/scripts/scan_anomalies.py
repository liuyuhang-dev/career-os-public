#!/usr/bin/env python3
"""testflow 异常扫描(确定性信号)。

**为什么需要它**:`explore` 模式的产出原本只能靠"模型说好像有问题" —— 主观、不可复现。
而崩溃、白屏黑屏、画面卡死这三类异常**根本不需要模型判断**:

  - logcat 里有没有 `FATAL EXCEPTION` / `ANR in` / native 崩溃信号 —— 文本匹配
  - 截图是不是整屏单色(白屏/黑屏) —— 像素统计
  - 相邻两张截图是不是一模一样、间隔多久 —— 字节与时间戳比对

把这些做成确定性信号,explore 的 bug 候选才有依据,而不是靠模型的印象。

**它能判什么、不能判什么:**

  能:  崩溃(logcat)/ 白屏黑屏(截图)/ 画面卡死(相邻截图逐一比对)
  不能: 内部状态一致性(要 `state-report`,见 7-instrumentation.md)
        启动耗时(需客户端打点,未实现)
        多指与帧级时序(见 8-platform-limits.md)

未实现的能力一律写"**无法判断**",绝不输出"无异常"来充数。

**三条输出纪律:**

1. **发现是候选,不是判定。** 崩溃是高置信信号;白屏/卡死必须人复核(加载页、静态菜单
   都会误报)。所以扫描器**不开 bug 单**,只出报告 —— 开单仍由 `file_bugs.py` 按
   `assert` 失败来做。
2. **没证据 ≠ 没异常。** 声明了 logcat 却没采到,要写"证据缺失,无法判断"。
3. **每条发现都带证据路径与命中的原始行/像素统计**,人自己能复核。

纯标准库,零依赖。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from coordmap import PngUnsupported, read_png  # noqa: E402

SKILL_DIR = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------- 崩溃信号

# 高置信 = 系统层明确报了崩溃/ANR;中置信 = 可疑,但可能是应用自己打的日志或良性回收
CRASH_PATTERNS: list[tuple[re.Pattern, str, str]] = [
    (re.compile(r"FATAL EXCEPTION"), "high", "Java 未捕获异常"),
    (re.compile(r"-{5,}\s*beginning of crash"), "high", "logcat 崩溃段起始标记"),
    (re.compile(r"Fatal signal \d+ \(SIG"), "high", "native 崩溃(libc 报的)"),
    (re.compile(r"signal \d+ \(SIG(SEGV|ABRT|BUS|ILL)\)"), "high", "native 崩溃信号"),
    (re.compile(r"\bANR in\b"), "high", "ANR:应用无响应"),
    (re.compile(r"Force finishing activity"), "medium", "系统强制结束界面(常伴随 ANR/崩溃)"),
    (re.compile(r"E/AndroidRuntime"), "medium", "Java 异常日志(可能是被捕获后打出来的)"),
    (re.compile(r"tombstone|DEBUG\s+pid:|backtrace:"), "medium", "native 崩溃现场"),
    (re.compile(r"lowmemorykiller|LowMemoryKiller|Killing '"), "medium", "被系统因内存压力杀掉"),
]
CRASH_HINT_MARKERS = {"ActivityManager", "AndroidRuntime", "libc", "DEBUG", "ANR"}
NEAR_WHITE, NEAR_BLACK = 245, 10

# 启动耗时:系统自己会在 logcat 里打出来,不需要客户端配合。
# 形如 `ActivityTaskManager: Displayed com.tencent.mm/.ui.LauncherUI: +1s234ms`
# (老版本是 ActivityManager + Fully drawn)。这是确定性数字,可直接做版本对比基线。
DISPLAYED_RE = re.compile(
    r"(?:ActivityTaskManager|ActivityManager):\s*(?:Displayed|Fully drawn)\s+"
    r"([^\s:]+/[^\s:]+):\s*\+([0-9hms]+)")

# 时长解析必须按 token 走:"m" 的匹配会吞掉 "ms" 里的 m(regex 回溯陷阱),
# 而 "1234ms" 被当成 1234 分钟会让启动耗时看起来差 6 万倍。
_DUR_TOKEN_RE = re.compile(r"(\d+)(ms|s|m|h)")
_DUR_FACTOR = {"h": 3_600_000, "m": 60_000, "s": 1_000, "ms": 1}


def parse_duration_ms(text: str) -> int | None:
    """把 `1h2m3s456ms` / `1m2s300ms` / `1s234ms` / `234ms` 解析成毫秒。

    有无法解释的残留就返回 None —— 不猜。
    """
    text = (text or "").strip()
    if not text:
        return None
    tokens = _DUR_TOKEN_RE.findall(text)
    if not tokens or "".join(f"{n}{u}" for n, u in tokens) != text:
        return None
    return sum(int(n) * _DUR_FACTOR[u] for n, u in tokens)


def scan_logcat(path: Path, slow_start_ms: int = 3000) -> list[dict]:
    """扫 logcat 文本:崩溃信号 + 启动耗时。

    每条发现都带行号与原文,便于人工复核。
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return [{"type": "read_error", "confidence": "high", "evidence": str(path),
                 "detail": f"读不了 logcat: {type(exc).__name__}: {exc}"}]
    findings: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for lineno, line in enumerate(text.splitlines(), 1):
        for pattern, confidence, detail in CRASH_PATTERNS:
            if not pattern.search(line):
                continue
            key = (pattern.pattern, line.strip()[:120])
            if key in seen:
                continue
            seen.add(key)
            findings.append({
                "type": "crash" if confidence == "high" else "crash_suspect",
                "confidence": confidence,
                "evidence": str(path),
                "line": lineno,
                "matched": pattern.pattern,
                "detail": detail,
                "raw": line.strip()[:300],
            })
            break
        m = DISPLAYED_RE.search(line)
        if m:
            component, ms = m.group(1), parse_duration_ms(m.group(2))
            if ms is not None:
                slow = ms >= slow_start_ms
                findings.append({
                    "type": "launch_slow" if slow else "launch_time",
                    "confidence": "medium" if slow else "high",
                    "evidence": str(path),
                    "line": lineno,
                    "detail": (f"{component} 启动耗时 {ms}ms"
                               + (f"(超过阈值 {slow_start_ms}ms)" if slow else "")),
                    "stats": {"component": component, "ms": ms,
                              "thresholdMs": slow_start_ms},
                    "raw": line.strip()[:300],
                    "reviewHint": ("单次采样会受设备状态影响 —— 建议多轮取中位数,"
                                   "并与基线对比(见 --baseline)") if slow else None,
                })
    return findings


# ---------------------------------------------------------------- 画面异常

def frame_stats(w: int, h: int, ch: int, px: bytes, step: int = 4) -> tuple[Counter, int]:
    """抽样统计像素颜色。step 控制抽样步长(默认每 4 像素取 1)。"""
    counter: Counter = Counter()
    total = 0
    for y in range(0, h, step):
        base = y * w * ch
        for x in range(0, w, step):
            i = base + x * ch
            if ch >= 3:
                counter[(px[i], px[i + 1], px[i + 2])] += 1
            else:                                   # 灰度
                counter[(px[i], px[i], px[i])] += 1
            total += 1
    return counter, total


def scan_frame(path: Path, blank_ratio: float = 0.98, step: int = 4) -> list[dict]:
    """判白屏/黑屏/整屏单色。**这是候选**:加载页、白底弹窗都会命中。"""
    try:
        w, h, ch, px = read_png(path)
    except (PngUnsupported, OSError) as exc:
        return [{"type": "read_error", "confidence": "high", "evidence": str(path),
                 "detail": f"读不了截图: {type(exc).__name__}: {exc}"}]
    counter, total = frame_stats(w, h, ch, px, step)
    if not total:
        return []
    top_color, top_n = counter.most_common(1)[0]
    uniformity = top_n / total
    near_white = sum(n for c, n in counter.items() if min(c) >= NEAR_WHITE) / total
    near_black = sum(n for c, n in counter.items() if max(c) <= NEAR_BLACK) / total
    stats = {"size": [w, h], "sampleStep": step, "uniformity": round(uniformity, 4),
             "nearWhite": round(near_white, 4), "nearBlack": round(near_black, 4),
             "dominantColor": list(top_color)}

    out: list[dict] = []
    if near_white >= blank_ratio:
        out.append({"type": "white_screen", "confidence": "medium", "evidence": str(path),
                    "detail": f"{near_white:.1%} 的采样像素接近纯白((245,245,245) 以上)",
                    "stats": stats,
                    "reviewHint": "可能是加载页/白底弹窗 —— 要人复核,别直接当缺陷"})
    elif near_black >= blank_ratio:
        out.append({"type": "black_screen", "confidence": "medium", "evidence": str(path),
                    "detail": f"{near_black:.1%} 的采样像素接近纯黑",
                    "stats": stats,
                    "reviewHint": "可能是渲染失败,也可能是过场黑屏 —— 要人复核"})
    elif uniformity >= 0.995:
        out.append({"type": "blank_screen", "confidence": "medium", "evidence": str(path),
                    "detail": f"{uniformity:.1%} 的采样像素是同一个颜色 {list(top_color)}",
                    "stats": stats,
                    "reviewHint": "整屏单色通常意味着渲染没出来 —— 要人复核"})
    else:
        out.append({"type": "frame_ok", "confidence": "high", "evidence": str(path),
                    "detail": "画面不是单色(未命中白屏/黑屏/单色)",
                    "stats": stats})
    return out


def frozen_findings(shots: list[dict], min_secs: float) -> list[dict]:
    """同一用例的相邻截图:字节完全相同且间隔 >= min_secs -> 画面卡死候选。

    字节完全相同在 adb 截图上是可靠的像素级等价(zlib 编码确定),
    所以这里不需要解码像素。间隔取文件 mtime(截图落盘时刻)。
    """
    out: list[dict] = []
    for prev, cur in zip(shots, shots[1:]):
        a, b = Path(prev["path"]), Path(cur["path"])
        if not (a.exists() and b.exists()):
            continue
        try:
            ha = hashlib.sha256(a.read_bytes()).hexdigest()
            hb = hashlib.sha256(b.read_bytes()).hexdigest()
            mt_a, mt_b = a.stat().st_mtime, b.stat().st_mtime
        except OSError:
            continue
        if ha != hb:
            continue
        gap = abs(mt_b - mt_a)
        out.append({
            "type": "frozen_screen",
            "confidence": "medium" if gap >= min_secs else "low",
            "evidence": str(b),
            "detail": (f"{prev['step']} → {cur['step']} 两张截图逐字节完全相同,"
                       f"间隔 {gap:.1f}s"),
            "stats": {"gapSecs": round(gap, 2), "sha256": ha[:16],
                      "prevStep": prev["step"], "step": cur["step"]},
            "reviewHint": ("间隔够长而画面一字未变,可能是卡死;"
                           "但静态菜单/暂停界面也会这样 —— 要人复核")
            if gap >= min_secs else
            ("两张截图完全一致,但间隔只有 {:.1f}s,不足 {:.1f}s,"
             "按低置信记录,不作为卡死候选").format(gap, min_secs),
        })
    return out


# ---------------------------------------------------------------- 性能(帧率/内存)

GFX_TOTAL_RE = re.compile(r"Total frames rendered:\s*(\d+)")
GFX_JANKY_RE = re.compile(r"Janky frames:\s*(\d+)\s*\(([0-9.]+)%\)")
GFX_PCT_RE = re.compile(r"(\d+)(?:st|nd|rd|th) percentile:\s*(\d+)ms")
MEM_TOTAL_RE = re.compile(r"TOTAL PSS:\s*(\d+)")
PKG_RE = re.compile(r"\[([A-Za-z0-9_.]+)\]")


def parse_gfxinfo(text: str) -> dict | None:
    """从 `dumpsys gfxinfo <pkg>` 输出里取帧数与 janky 比例、分位帧耗时。"""
    total = GFX_TOTAL_RE.search(text)
    janky = GFX_JANKY_RE.search(text)
    pcts = {int(n): int(v) for n, v in GFX_PCT_RE.findall(text)}
    if not total and not janky:
        return None
    pkg = PKG_RE.search(text)
    return {
        "package": pkg.group(1) if pkg else None,
        "totalFrames": int(total.group(1)) if total else None,
        "jankyFrames": int(janky.group(1)) if janky else None,
        "jankyPct": float(janky.group(2)) if janky else None,
        "p50Ms": pcts.get(50), "p90Ms": pcts.get(90),
        "p95Ms": pcts.get(95), "p99Ms": pcts.get(99),
    }


def parse_meminfo(text: str) -> dict | None:
    """从 `dumpsys meminfo <pkg>` 输出里取 TOTAL PSS。"""
    m = MEM_TOTAL_RE.search(text)
    if not m:
        return None
    kb = int(m.group(1))
    pkg = PKG_RE.search(text)
    return {"package": pkg.group(1) if pkg else None,
            "totalPssKb": kb, "totalPssMb": round(kb / 1024, 1)}


def scan_perf(path: Path, janky_max_pct: float, mem_max_mb: float) -> list[dict]:
    """扫一份 dumpsys 性能快照。

    记忆:**小游戏跑在宿主进程里**,这里量到的是宿主进程级数据 ——
    跨机型/跨版本对比成立,但别把它当成小游戏自己的帧率。
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return [{"type": "read_error", "confidence": "high", "evidence": str(path),
                 "detail": f"读不了性能快照: {type(exc).__name__}: {exc}"}]

    gfx = parse_gfxinfo(text)
    if gfx:
        subject = gfx.get("package") or "(未知进程)"
        detail = (f"{subject} 帧数 {gfx['totalFrames']},"
                  f"janky {gfx['jankyPct']}%({gfx['jankyFrames']} 帧),"
                  f"p90 {gfx['p90Ms']}ms / p95 {gfx['p95Ms']}ms")
        out = [{"type": "perf_frame", "confidence": "high", "evidence": str(path),
                "detail": detail, "stats": {**gfx, "subject": subject}}]
        if gfx.get("jankyPct") is not None and gfx["jankyPct"] > janky_max_pct:
            out.append({"type": "perf_janky_high", "confidence": "medium",
                        "evidence": str(path),
                        "detail": (f"{subject} janky 帧占比 {gfx['jankyPct']}% "
                                   f"超过阈值 {janky_max_pct}%"),
                        "stats": {**gfx, "subject": subject,
                                  "thresholdPct": janky_max_pct},
                        "reviewHint": "帧率随场景波动大 —— 固定同一段操作再比,多轮取中位数"})
        return out

    mem = parse_meminfo(text)
    if mem:
        subject = mem.get("package") or "(未知进程)"
        out = [{"type": "perf_mem", "confidence": "high", "evidence": str(path),
                "detail": f"{subject} TOTAL PSS {mem['totalPssMb']}MB",
                "stats": {**mem, "subject": subject}}]
        if mem_max_mb and mem["totalPssMb"] > mem_max_mb:
            out.append({"type": "perf_mem_high", "confidence": "medium",
                        "evidence": str(path),
                        "detail": (f"{subject} 内存 {mem['totalPssMb']}MB "
                                   f"超过阈值 {mem_max_mb}MB"),
                        "stats": {**mem, "subject": subject,
                                  "thresholdMb": mem_max_mb}})
        return out

    return [{"type": "read_error", "confidence": "medium", "evidence": str(path),
             "detail": "这份文本既不像 gfxinfo 也不像 meminfo —— "
                       "无法判断(不是「没有异常」)"}]


def sniff_text_kind(path: Path) -> str:
    """按**内容**判断文本证据是哪一类,不靠文件名。

    文件名是用法约定的产物(S5 这种),内容才是事实。
    """
    try:
        head = path.read_text(encoding="utf-8", errors="replace")[:65536]
    except OSError:
        return "text"
    if "Total frames rendered" in head or "Janky frames" in head:
        return "gfxinfo"
    if "TOTAL PSS" in head or "** MEMINFO in pid" in head:
        return "meminfo"
    return "logcat"


# ---------------------------------------------------------------- 基线对比

# 只记录、不算异常的信号(扫到了、值正常)
INFORMATIONAL = {"frame_ok", "launch_time", "perf_frame", "perf_mem"}


def _baseline_key(f: dict) -> str | None:
    """基线键 = `指标:对象`。

    必须带指标前缀:同一个包名既要存启动耗时,也要存 janky 比例与内存,
    只用包名做键会互相覆盖。
    """
    s = f.get("stats") or {}
    if f["type"] in ("launch_time", "launch_slow", "launch_regression"):
        return f"launch_ms:{s.get('component')}"
    # 只取"实测值"那一条参与基线比对:perf_janky_high / perf_mem_high 是同一次测量的
    # 阈值标记,也算进来会对同一个数字报两次退化。
    if f["type"] in ("perf_frame", "perf_mem", "perf_regression"):
        key = s.get("subject") or s.get("package")
        if s.get("jankyPct") is not None:
            return f"janky_pct:{key}"
        if s.get("totalPssMb") is not None:
            return f"pss_mb:{key}"
    return None


def build_baseline(findings: list[dict]) -> dict:
    """把本次量到的指标压成基线:每个「指标:对象」取中位数。"""
    from statistics import median
    per: dict[str, list[float]] = {}
    for f in findings:
        key = _baseline_key(f)
        if not key:
            continue
        s = f["stats"]
        if "launch_ms:" in key:
            per.setdefault(key, []).append(s["ms"])
        elif "janky_pct:" in key:
            per.setdefault(key, []).append(s["jankyPct"])
        elif "pss_mb:" in key:
            per.setdefault(key, []).append(s["totalPssMb"])
    return {k: round(median(v), 2) for k, v in per.items()}


def baseline_findings(findings: list[dict], baseline: dict, tolerance: float) -> list[dict]:
    """与基线比:超过 baseline × (1 + tolerance) -> 退化候选。

    阈值口径与 `references/9-lifecycle.md` 的"启动耗时不超过基线 120%"一致。
    这几项指标都是**越大越差**,所以统一用同一个方向判断。
    """
    out: list[dict] = []
    for f in findings:
        key = _baseline_key(f)
        if not key or key not in (baseline or {}):
            continue
        base = float(baseline[key])
        s = f["stats"]
        value = (s.get("ms") if "launch_ms:" in key else
                 s.get("jankyPct") if "janky_pct:" in key else s.get("totalPssMb"))
        if value is None:
            continue
        limit = base * (1 + tolerance)
        if value <= limit:
            continue
        out.append({**f, "type": "launch_regression" if "launch_ms:" in key
                    else "perf_regression", "confidence": "medium",
                    "detail": (f"{key} 实测 {value} 超过基线 {base} 的 "
                               f"{(1 + tolerance) * 100:.0f}%(上限 {limit:.2f})"),
                    "stats": {**s, "baseline": base, "tolerance": tolerance,
                              "limit": round(limit, 2), "metric": key},
                    "reviewHint": "数字是确定性的,但单次采样会抖 —— 多轮取中位数再判退化"})
    return out


# ---------------------------------------------------------------- 证据收集

def classify(path: Path) -> str:
    s = path.suffix.lower()
    if s == ".png":
        return "screenshot"
    if s in (".json", ".jsonl"):
        return "json"
    if s in (".txt", ".log"):
        return sniff_text_kind(path)     # logcat / gfxinfo / meminfo,按内容判
    return "other"


def case_evidence(case: dict) -> list[dict]:
    """一个用例声明过 / 绑定过的全部证据(evidenceIndex + 判定项上的 evidence)。"""
    items: list[dict] = []
    seen: set[str] = set()
    for ent in case.get("evidenceIndex") or []:
        p = str(ent.get("path") or "")
        if p and p not in seen:
            seen.add(p)
            items.append({"step": ent.get("step"), "name": ent.get("name"), "path": p})
    for chk in case.get("checks") or []:
        for p in chk.get("evidence") or []:
            p = str(p)
            if p and p not in seen:
                seen.add(p)
                items.append({"step": chk.get("after"), "name": "check.evidence", "path": p})
    return items


def collect_gaps(case: dict, cases_dir: Path | None) -> list[dict]:
    """证据缺口:声明了要采、却没采到的东西。这不是"没有异常",是"没法判断"。"""
    gaps: list[dict] = []
    for g in case.get("evidenceGaps") or []:
        gaps.append({"caseId": case.get("id"), **g})

    items = case_evidence(case)
    existing = [it for it in items if Path(it["path"]).exists()]
    missing = [it for it in items if not Path(it["path"]).exists()]
    for it in missing:
        gaps.append({"caseId": case.get("id"), "evidence": classify(Path(it["path"])),
                     "kind": "file_missing",
                     "note": f"计划里的证据文件不存在: {it['path']}"})

    has_shot = any(classify(Path(it["path"])) == "screenshot" for it in existing)
    has_logcat = any(classify(Path(it["path"])) == "logcat" for it in existing)
    has_perf = any(classify(Path(it["path"])) in ("gfxinfo", "meminfo")
                   for it in existing)
    for chk in case.get("checks") or []:
        if chk.get("mode") == "vision-closed" and not has_shot:
            gaps.append({"caseId": case.get("id"), "evidence": "screenshot",
                         "kind": "no_evidence",
                         "note": f"视觉判定项 {chk.get('source')} 没有可用截图,无法判断"})

    declared: set[str] = set()
    if cases_dir and cases_dir.exists():
        f = cases_dir / f"{case.get('id')}.json"
        if f.exists():
            try:
                declared = set(json.loads(f.read_text(encoding="utf-8")).get("evidence") or [])
            except json.JSONDecodeError:
                declared = set()
    for ev in sorted(declared):
        if ev == "screenshot" and has_shot:
            continue
        if ev == "logcat" and has_logcat:
            continue
        if ev == "perf" and has_perf:
            continue
        if ev in ("trace", "crash"):
            gaps.append({"caseId": case.get("id"), "evidence": ev, "kind": "no_collector",
                         "note": f"{ev} 证据目前没有采集通路(未实现),无法判断"})
        elif ev == "screenshot" and not has_shot:
            gaps.append({"caseId": case.get("id"), "evidence": ev, "kind": "no_evidence",
                         "note": "声明了截图证据,但没有落盘的截图文件"})
        elif ev == "logcat" and not has_logcat:
            gaps.append({"caseId": case.get("id"), "evidence": ev, "kind": "no_evidence",
                         "note": "声明了 logcat 证据,但没有落盘的 logcat 文件"})
        elif ev == "perf" and not has_perf:
            gaps.append({"caseId": case.get("id"), "evidence": ev, "kind": "no_evidence",
                         "note": "声明了 perf 证据,但没有 gfxinfo/meminfo 采集文件"
                                 "(用例需要 perf 动作)"})
    return gaps


# ---------------------------------------------------------------- 报告

def render_report(run: dict, findings: list[dict], gaps: list[dict],
                  scanned: dict, opts) -> str:
    now = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    real = [f for f in findings if f["type"] not in INFORMATIONAL]
    crashes = [f for f in real if f["type"].startswith("crash")]
    screens = [f for f in real if f["type"].endswith("screen") and f["type"] != "frozen_screen"]
    frozen = [f for f in real if f["type"] == "frozen_screen"]
    errs = [f for f in real if f["type"] == "read_error"]
    launches = [f for f in findings if f["type"] in ("launch_time", "launch_slow")]
    slow = [f for f in launches if f["type"] == "launch_slow"]
    regressions = [f for f in findings if f["type"].endswith("regression")]
    perf = [f for f in findings if f["type"] in ("perf_frame", "perf_mem")]
    perf_high = [f for f in findings if f["type"] in ("perf_janky_high", "perf_mem_high")]

    lines = [
        f"# 异常扫描 {run.get('runId')}",
        "",
        f"- 生成时间: {now}",
        f"- 运行模式: `{run.get('mode')}`",
        f"- 扫描到的证据: 截图 {scanned.get('screenshot', 0)} 张、"
        f"logcat {scanned.get('logcat', 0)} 份、其它 {scanned.get('other', 0)} 个",
        f"- 严重信号: 崩溃 **{len(crashes)}**、白屏/黑屏/单色 **{len(screens)}**、"
        f"画面卡死候选 **{len(frozen)}**",
        f"- 启动耗时: 量到 **{len(launches)}** 条,其中超阈值 **{len(slow)}** 条",
        f"- 性能: 量到 **{len(perf)}** 条(janky/内存),超阈值 **{len(perf_high)}** 条;"
        f"与基线比退化 **{len(regressions)}** 条",
        "",
        "> **这些是候选,不是判定,所以本报告不开 bug 单。** 崩溃是高置信信号;"
        "白屏与卡死要人复核(加载页、静态菜单都会误报)。要开单,请让用例里有一条"
        "对应的 `assert` 判定项,由 `file_bugs.py` 按判定失败开。",
        "",
    ]

    def section(title: str, items: list[dict]) -> None:
        lines.append(f"## {title}")
        if not items:
            lines.append("- 无")
            lines.append("")
            return
        for f in items:
            lines.append(f"- **[{f['type']}]** `{f['confidence']}` — {f['detail']}")
            lines.append(f"  - 证据: `{f['evidence']}`"
                         + (f"(第 {f['line']} 行)" if f.get("line") else ""))
            if f.get("raw"):
                lines.append(f"  - 命中原文: `{f['raw']}`")
            if f.get("stats"):
                lines.append(f"  - 统计: `{json.dumps(f['stats'], ensure_ascii=False)}`")
            if f.get("reviewHint"):
                lines.append(f"  - 复核提示: {f['reviewHint']}")
        lines.append("")

    section("崩溃信号(高置信)", [f for f in crashes if f["confidence"] == "high"])
    section("崩溃可疑信号(中置信,可能是应用自己的日志)", 
            [f for f in crashes if f["confidence"] != "high"])
    section("白屏 / 黑屏 / 整屏单色(中置信)",
            [f for f in screens if f["confidence"] != "low"])
    section("画面卡死候选(相邻截图逐字节相同)", 
            [f for f in frozen if f["confidence"] != "low"])
    section("启动耗时退化(与基线比,中置信)",
            [f for f in regressions if f["type"] == "launch_regression"])
    section("性能退化(与基线比,中置信)",
            [f for f in regressions if f["type"] == "perf_regression"])
    section("启动耗时超阈值(中置信)", [f for f in slow if f not in regressions])
    section("启动耗时实测(供建基线/看趋势)",
            [f for f in launches if f["type"] == "launch_time"])
    section("性能实测(janky 比例 / 分位帧耗时 / 内存)", perf)
    section("性能超阈值(中置信)", [f for f in perf_high if f not in regressions])
    section("证据读取失败", errs)

    lines += ["## 证据缺口(不是「没有异常」,是「无法判断」)", ""]
    if not gaps:
        lines.append("- 无")
    else:
        for g in gaps:
            lines.append(f"- `{g.get('caseId')}` / {g.get('evidence')} "
                         f"({g.get('kind')}) — {g.get('note')}")
    lines += [
        "",
        "## 扫描器判不了的(别拿它当结论)",
        "",
        "- **内部状态一致性**(结算精确、存档一致、重复支付)→ 要 `state-report`,"
        "见 `references/7-instrumentation.md`",
        "- **帧级无响应 / 首帧时间** → 需要帧时间戳,未实现",
        "- **多指手势、帧级时序** → 真机做不了,见 `references/8-platform-limits.md`",
        "- **界面文案与元素是否存在** → 那是判定轮(`judge.py`)的活",
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------- 主流程

def scan_run(run: dict, cases_dir: Path | None, opts) -> tuple[list[dict], list[dict], dict]:
    findings: list[dict] = []
    gaps: list[dict] = []
    scanned: Counter = Counter()
    slow_ms = getattr(opts, "slow_start_ms", 3000)

    for case in run.get("cases") or []:
        cid = case.get("id")
        items = case_evidence(case)
        shots: list[dict] = []
        for it in items:
            p = Path(it["path"])
            if not p.exists():
                continue
            kind = classify(p)
            scanned[kind] += 1
            if kind == "screenshot":
                shots.append(it)
                for f in scan_frame(p, opts.blank_ratio):
                    findings.append({"caseId": cid, "step": it.get("step"), **f})
            elif kind == "logcat":
                for f in scan_logcat(p, slow_ms):
                    findings.append({"caseId": cid, "step": it.get("step"), **f})
            elif kind in ("gfxinfo", "meminfo"):
                for f in scan_perf(p, getattr(opts, "janky_pct_max", 10.0),
                                   getattr(opts, "mem_max_mb", 0.0)):
                    findings.append({"caseId": cid, "step": it.get("step"), **f})
        findings += [{"caseId": cid, **f} for f in frozen_findings(shots, opts.frozen_min_secs)]
        gaps += collect_gaps(case, cases_dir)

    baseline = getattr(opts, "baseline_data", None)
    if baseline:
        findings += baseline_findings(findings, baseline,
                                      getattr(opts, "tolerance", 0.20))

    # 同一文件被多条用例/多个判定项引用时去重
    dedup: dict[tuple, dict] = {}
    for f in findings:
        dedup[(f["type"], f["evidence"], f.get("line"), f.get("detail"))] = f
    return list(dedup.values()), gaps, dict(scanned)


def exit_code(findings: list[dict], gaps: list[dict]) -> int:
    real = [f for f in findings if f["type"] not in INFORMATIONAL]
    if real:
        return 1
    if gaps:
        return 3
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="testflow 异常扫描:崩溃(logcat)/ 白屏黑屏 / 画面卡死 —— "
                    "确定性信号,产出候选报告(不开 bug 单)")
    ap.add_argument("--run", type=Path, help="run-<ts>.json(必填,除 --selftest 外)")
    ap.add_argument("--out", type=Path, help="报告路径;默认 <run 同目录>/anomalies.md")
    ap.add_argument("--json", type=Path, help="机器可读结果路径")
    ap.add_argument("--cases", type=Path,
                    help="用例目录(03-cases/),给了才能核对声明的证据类型是否真采到了")
    ap.add_argument("--frozen-min-secs", type=float, default=3.0,
                    help="判定画面卡死所需的最小间隔秒数(默认 3)")
    ap.add_argument("--blank-ratio", type=float, default=0.98,
                    help="判定白屏/黑屏的像素占比阈值(默认 0.98)")
    ap.add_argument("--slow-start-ms", type=int, default=3000,
                    help="启动耗时超过多少毫秒算慢(默认 3000)")
    ap.add_argument("--janky-pct-max", type=float, default=10.0,
                    help="janky 帧占比超过多少算高(默认 10%%)")
    ap.add_argument("--mem-max-mb", type=float, default=0.0,
                    help="内存超过多少 MB 算高;0 = 不设阈值(默认)")
    ap.add_argument("--baseline", type=Path,
                    help="启动耗时基线 json(--save-baseline 生成);给了就做退化对比")
    ap.add_argument("--tolerance", type=float, default=0.20,
                    help="退化阈值,与 9-lifecycle 的『不超过基线 120%%』一致(默认 0.20)")
    ap.add_argument("--save-baseline", type=Path,
                    help="把本次量到的启动耗时(中位数)写成基线 json")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    if args.run is None:
        print("错误: 需要 --run(或用 --selftest 跑自测)", file=sys.stderr)
        return 2
    if not args.run.exists():
        print(f"错误: 找不到 run 结果 {args.run}", file=sys.stderr)
        return 2
    try:
        run = json.loads(args.run.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"错误: run json 解析失败: {exc}", file=sys.stderr)
        return 2

    args.baseline_data = None
    if args.baseline:
        if not args.baseline.exists():
            print(f"错误: 找不到基线文件 {args.baseline}", file=sys.stderr)
            return 2
        try:
            args.baseline_data = json.loads(args.baseline.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            print(f"错误: 基线 json 解析失败: {exc}", file=sys.stderr)
            return 2

    findings, gaps, scanned = scan_run(run, args.cases, args)
    report = render_report(run, findings, gaps, scanned, args)
    # 默认放到用例目录根,与 file_bugs.py 的 report.md 同级
    out = args.out or (args.run.parent.parent / "anomalies.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")

    code = exit_code(findings, gaps)
    if args.save_baseline:
        base = build_baseline(findings)
        args.save_baseline.parent.mkdir(parents=True, exist_ok=True)
        args.save_baseline.write_text(
            json.dumps(base, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if not args.quiet:
            print(f"启动耗时基线已写出({len(base)} 个组件): {args.save_baseline}")
    if not args.quiet:
        real = [f for f in findings if f["type"] not in INFORMATIONAL]
        print(f"异常扫描 {run.get('runId')}: 证据 截图×{scanned.get('screenshot', 0)} "
              f"logcat×{scanned.get('logcat', 0)}")
        for f in real:
            loc = f" 第{f['line']}行" if f.get("line") else ""
            print(f"  [{f['confidence']:6s}] {f['type']:14s} {f['caseId']} "
                  f"{f.get('step') or ''}{loc} — {f['detail'][:70]}")
        if gaps:
            print(f"证据缺口 {len(gaps)} 项(无法判断,不等于没有异常):")
            for g in gaps[:8]:
                print(f"  ! {g.get('caseId')} / {g.get('evidence')} — {g.get('note')}")
        print(f"报告已写出: {out}")
        print("提醒: 报告里的发现是候选,不开 bug 单;崩溃如需开单请补 assert 判定项。")
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(
            {"runId": run.get("runId"), "scanned": scanned,
             "findings": findings, "gaps": gaps},
            ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return code


# ---------------------------------------------------------------- 自测

def selftest() -> int:
    import tempfile

    fails: list[str] = []

    def ck(name: str, cond: bool, detail: str = "") -> None:
        if cond:
            print(f"  ok   {name}")
        else:
            print(f"  FAIL {name}" + (f" — {detail}" if detail else ""))
            fails.append(name)

    def make_png(w: int, h: int, color: tuple[int, int, int]) -> bytes:
        from coordmap import encode_png_8bit
        return encode_png_8bit(w, h, 3, bytes(color) * (w * h))

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)

        # ---- logcat
        crash = tmp / "C1_S3_logcat.txt"
        crash.write_text("\n".join([
            "09-18 10:00:00.000  1234  1234 I ActivityManager: Start proc com.tencent.mm",
            "09-18 10:00:01.000  1234  1250 D GameCore: loaded assets",
            "09-18 10:00:02.000  1234  1250 E AndroidRuntime: FATAL EXCEPTION: main",
            "09-18 10:00:02.100  1234  1250 E AndroidRuntime: java.lang.NullPointerException",
            "09-18 10:00:05.000  1234  1234 I libc: Fatal signal 11 (SIGSEGV), code 1",
            "09-18 10:00:09.000  1234  1234 E ActivityManager: ANR in com.tencent.mm",
            "09-18 10:00:20.000  1234  1250 I GameCore: 正常日志,包含单词 FATALITY 但不是崩溃",
        ]), encoding="utf-8")
        f = scan_logcat(crash)
        kinds = [x["type"] for x in f]
        ck("logcat: 认出 FATAL EXCEPTION", "crash" in kinds, str(kinds))
        ck("logcat: 认出 native 崩溃与 ANR",
           sum(1 for x in f if x["confidence"] == "high") >= 3,
           f"高置信 {sum(1 for x in f if x['confidence'] == 'high')} 条")
        ck("logcat: 记录行号供复核", all(x.get("line") for x in f))
        ck("logcat: 不误报 FATALITY 这类普通词",
           not any("FATALITY" in x.get("raw", "") for x in f),
           str([x.get("raw") for x in f]))
        clean = tmp / "clean_logcat.txt"
        clean.write_text("09-18 10:00:00 I GameCore: all good\n", encoding="utf-8")
        ck("logcat: 干净日志无发现", scan_logcat(clean) == [])

        # ---- 白屏 / 黑屏 / 单色 / 正常
        white, black = tmp / "w.png", tmp / "b.png"
        white.write_bytes(make_png(40, 40, (255, 255, 255)))
        black.write_bytes(make_png(40, 40, (0, 0, 0)))
        solid = tmp / "s.png"
        solid.write_bytes(make_png(40, 40, (12, 200, 90)))
        normal = tmp / "n.png"
        from coordmap import encode_png_8bit
        px = bytearray()
        for y in range(40):
            for x in range(40):
                px += bytes((x * 6 % 256, y * 6 % 256, 128))
        normal.write_bytes(encode_png_8bit(40, 40, 3, bytes(px)))

        ck("截图: 白屏命中", scan_frame(white)[0]["type"] == "white_screen",
           str(scan_frame(white)[0]))
        ck("截图: 黑屏命中", scan_frame(black)[0]["type"] == "black_screen")
        ck("截图: 整屏单色命中", scan_frame(solid)[0]["type"] == "blank_screen")
        ck("截图: 正常画面不误报", scan_frame(normal)[0]["type"] == "frame_ok",
           str(scan_frame(normal)[0]["stats"]))
        ck("截图: 白屏发现带复核提示(不当缺陷)",
           "人复核" in scan_frame(white)[0].get("reviewHint", ""))

        # ---- 画面卡死:逐字节相同 + 间隔
        import os
        a, b = tmp / "C1_S2_a.png", tmp / "C1_S3_b.png"
        a.write_bytes(make_png(20, 20, (10, 20, 30)))
        b.write_bytes(a.read_bytes())
        os.utime(a, (1000, 1000))
        os.utime(b, (1005, 1005))          # 间隔 5s
        shots = [{"step": "S2", "path": str(a)}, {"step": "S3", "path": str(b)}]
        fz = frozen_findings(shots, 3.0)
        ck("卡死: 相同且间隔够 -> 中置信候选",
           len(fz) == 1 and fz[0]["type"] == "frozen_screen"
           and fz[0]["confidence"] == "medium", str(fz))
        os.utime(b, (1001, 1001))          # 间隔 1s
        fz2 = frozen_findings(shots, 3.0)
        ck("卡死: 间隔不足 -> 降为低置信", len(fz2) == 1
           and fz2[0]["confidence"] == "low", str(fz2))
        # 不同画面不算卡死
        c2 = tmp / "C1_S4_c.png"
        c2.write_bytes(make_png(20, 20, (99, 99, 99)))
        os.utime(c2, (1010, 1010))
        ck("卡死: 画面不同不报",
           frozen_findings(shots[:1] + [{"step": "S4", "path": str(c2)}], 3.0) == [])

        # ---- 端到端:run json + 证据缺口
        run = {"runId": "run-selftest", "mode": "execute", "cases": [
            {"id": "C1", "evidenceIndex": [
                {"step": "S2", "name": "shot", "path": str(a)},
                {"step": "S3", "name": "shot", "path": str(b)},
                {"step": "S3", "name": "logcat", "path": str(crash)},
                {"step": "S4", "name": "shot", "path": str(tmp / "not-there.png")},
            ], "checks": []},
            {"id": "C2", "evidenceIndex": [], "evidenceGaps": [
                {"evidence": "logcat", "kind": "no_action", "note": "声明了 logcat 但动作里没有采集步骤"}],
             "checks": [{"source": "E1", "mode": "vision-closed", "verdict": "blocked"}]},
        ]}
        findings, gaps, scanned = scan_run(run, None, argparse.Namespace(
            frozen_min_secs=1.0, blank_ratio=0.98))
        types = {f["type"] for f in findings}
        ck("端到端: 扫到崩溃 + 卡死候选",
           "crash" in types and "frozen_screen" in types, str(sorted(types)))
        ck("端到端: 不存在的截图记成缺口而不是跳过",
           any(g["kind"] == "file_missing" for g in gaps), str(gaps))
        ck("端到端: 声明了 logcat 却没采 -> 缺口", any(
            g["caseId"] == "C2" and g.get("kind") == "no_action" for g in gaps), str(gaps))
        ck("端到端: 视觉判定项无截图 -> 无法判断",
           any(g["caseId"] == "C2" and g["kind"] == "no_evidence" for g in gaps))
        ck("端到端: 统计到截图与 logcat", scanned.get("logcat", 0) >= 1
           and scanned.get("screenshot", 0) >= 2, str(scanned))
        ck("退出码: 有异常 -> 1", exit_code(findings, gaps) == 1)
        ck("退出码: 干净 -> 0",
           exit_code([{"type": "frame_ok"}], []) == 0)
        ck("退出码: 只有缺口 -> 3(区分能力缺口与产品缺陷)",
           exit_code([{"type": "frame_ok"}], [{"kind": "no_action"}]) == 3)

        # 空 run(纯 plan-only):没有任何证据 -> 只能 3
        empty = {"runId": "run-empty", "cases": [
            {"id": "C1", "evidenceIndex": [{"step": "S1", "path": str(tmp / "x.png")}],
             "checks": []}]}
        f2, g2, s2 = scan_run(empty, None, argparse.Namespace(
            frozen_min_secs=3.0, blank_ratio=0.98))
        ck("plan-only: 没证据 -> 退出码 3 且报告明说缺证据",
           exit_code(f2, g2) == 3 and any(x["kind"] == "file_missing" for x in g2),
           f"exit={exit_code(f2, g2)} gaps={g2}")

        # ---- 启动耗时:解析各种时长写法 + 慢启动标记 + 基线退化
        ck("时长解析: 1m2s300ms", parse_duration_ms("1m2s300ms") == 62300,
           str(parse_duration_ms("1m2s300ms")))
        ck("时长解析: 1s234ms", parse_duration_ms("1s234ms") == 1234)
        ck("时长解析: 234ms", parse_duration_ms("234ms") == 234)
        ck("时长解析: 空串返回 None", parse_duration_ms("") is None)

        boot = tmp / "boot_logcat.txt"
        boot.write_text("\n".join([
            "09-18 10:00:00.000  1000  1000 I ActivityTaskManager: START u0 {act=android.intent.action.MAIN}",
            "09-18 10:00:01.234  1000  1000 I ActivityTaskManager: "
            "Displayed com.tencent.mm/.ui.LauncherUI: +1s234ms",
            "09-18 10:00:09.000  1000  1000 I ActivityTaskManager: "
            "Displayed com.tencent.mm/.plugin.appbrand.ui.AppBrandUI: +9s100ms",
            "09-18 10:01:00.000  1000  1000 I ActivityManager: "
            "Fully drawn com.tencent.mm/.ui.SnsUI: +800ms",
        ]), encoding="utf-8")
        lf = scan_logcat(boot, slow_start_ms=3000)
        times = {x["stats"]["component"]: x["stats"]["ms"] for x in lf
                 if x["type"] in ("launch_time", "launch_slow")}
        ck("启动耗时: 三种行格式都解析出来",
           times == {"com.tencent.mm/.ui.LauncherUI": 1234,
                     "com.tencent.mm/.plugin.appbrand.ui.AppBrandUI": 9100,
                     "com.tencent.mm/.ui.SnsUI": 800}, str(times))
        ck("启动耗时: 超过阈值标成 launch_slow",
           [x["type"] for x in lf if x["stats"]["component"].endswith("AppBrandUI")]
           == ["launch_slow"])
        ck("启动耗时: 正常值标成 launch_time(只记录不算异常)",
           "launch_time" in {x["type"] for x in lf}
           and exit_code([x for x in lf if x["type"] == "launch_time"], []) == 0
           and exit_code(lf, []) == 1,
           f"只有正常启动时应为 0,含慢启动时应为 1;实为 {exit_code(lf, [])}")

        base = build_baseline(lf)
        ck("基线: 键带指标前缀(launch_ms:组件)",
           base["launch_ms:com.tencent.mm/.ui.LauncherUI"] == 1234, str(base))
        reg = baseline_findings(lf, {"launch_ms:com.tencent.mm/.ui.LauncherUI": 1000}, 0.20)
        ck("基线退化: 1234ms 超 1000ms 的 120% -> 退化候选",
           len(reg) == 1 and reg[0]["type"] == "launch_regression"
           and reg[0]["stats"]["limit"] == 1200, str(reg))
        ck("基线退化: 未超阈值不报",
           baseline_findings(lf, {"launch_ms:com.tencent.mm/.ui.LauncherUI": 1100}, 0.20) == [])
        ck("基线退化: 没基线的组件不报",
           baseline_findings(lf, {"launch_ms:other/pkg.Act": 100}, 0.20) == [])

        # ---- 性能:gfxinfo / meminfo
        gfx_txt = tmp / "perf_snapshot.txt"
        gfx_txt.write_text("\n".join([
            "** Graphics info for pid 1234 [com.tencent.mm] **",
            "Total frames rendered: 1200",
            "Janky frames: 150 (12.50%)",
            "50th percentile: 5ms",
            "90th percentile: 9ms",
            "95th percentile: 14ms",
            "99th percentile: 33ms",
        ]), encoding="utf-8")
        mem_txt = tmp / "perf_snapshot_mem.txt"
        mem_txt.write_text("\n".join([
            "** MEMINFO in pid 1234 [com.tencent.mm] **",
            "                   Pss  Private  Private   SwapPss",
            "  TOTAL PSS:      512345       51200      0      0",
        ]), encoding="utf-8")

        pf = scan_perf(gfx_txt, janky_max_pct=10.0, mem_max_mb=0)
        pkinds = [x["type"] for x in pf]
        ck("性能: gfxinfo 解析出帧数/janky 比例/分位耗时",
           pf[0]["stats"]["totalFrames"] == 1200 and pf[0]["stats"]["jankyPct"] == 12.5
           and pf[0]["stats"]["p95Ms"] == 14
           and pf[0]["stats"]["package"] == "com.tencent.mm", str(pf[0]["stats"]))
        ck("性能: janky 超阈值 -> 候选", "perf_janky_high" in pkinds, str(pkinds))
        ck("性能: 阈值内只记录不报",
           [x["type"] for x in scan_perf(gfx_txt, 20.0, 0)] == ["perf_frame"])
        ck("性能: 正常 perf 不计入退出码",
           exit_code([pf[0]], []) == 0, str(exit_code([pf[0]], [])))
        mf = scan_perf(mem_txt, 10.0, 0)
        ck("性能: meminfo 解析 TOTAL PSS(换算成 MB)",
           mf[0]["stats"]["totalPssMb"] == 500.3, str(mf[0]["stats"]))
        ck("性能: 内存阈值默认关、可开",
           len(mf) == 1 and scan_perf(mem_txt, 10.0, 400)[1]["type"] == "perf_mem_high",
           str([x["type"] for x in scan_perf(mem_txt, 10.0, 400)]))
        ck("性能: 认不出的文本明确报『无法判断』而不是没异常",
           scan_perf(clean, 10.0, 0)[0]["type"] == "read_error",
           str(scan_perf(clean, 10.0, 0)))
        ck("性能: 文本分类按内容不按文件名",
           sniff_text_kind(gfx_txt) == "gfxinfo"
           and sniff_text_kind(mem_txt) == "meminfo"
           and sniff_text_kind(clean) == "logcat",
           f"{sniff_text_kind(gfx_txt)}/{sniff_text_kind(mem_txt)}/{sniff_text_kind(clean)}")
        both = build_baseline([pf[0]] + [x for x in lf if x["type"] == "launch_time"])
        ck("基线: 启动与性能键不互相覆盖",
           "janky_pct:com.tencent.mm" in both
           and "launch_ms:com.tencent.mm/.ui.LauncherUI" in both, str(both))
        pregr = baseline_findings(pf, {"janky_pct:com.tencent.mm": 5.0}, 0.20)
        ck("基线: 性能退化 -> perf_regression",
           len(pregr) == 1 and pregr[0]["type"] == "perf_regression", str(pregr))

    print()
    if fails:
        print(f"自测失败 {len(fails)} 项:")
        for x in fails:
            print("  ✗", x)
        return 1
    print("自测通过(logcat 崩溃/ANR 识别与不误报;白屏/黑屏/单色/正常画面;"
          "卡死候选含间隔门槛;启动耗时解析/慢启动阈值/基线退化;"
          "gfxinfo 帧率与 meminfo 内存解析/阈值/按内容分类;"
          "证据缺口如实报;退出码 0/1/2/3 语义)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
