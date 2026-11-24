#!/usr/bin/env python3
"""testflow 判定轮(阶段 6 的判定者)。

**为什么单独一个脚本**:规矩一(玩与判分离)。`run_case.py` 只产出 actual 与
判定请求,永不输出通过与否;本脚本是**另一个独立的调用**,只吃判定请求 + 截图,
吐出结论。同一个视觉智能体既玩又判,就是自己出题自己答题自己判卷。

契约:

    输入  run-<ts>.json(含 evidenceIndex 与 pending_judgment 判定项)
          + 该区域的截图(真的按 region 裁出来再送)
    输出  回填到 run json 的 verdict
          + <runId>.judgment.jsonl 判定日志(模型名/端点/原始响应/截图哈希/耗时)

三条硬约束,违反任何一条结论都不可信:

1. **严格 yes/no。** 模型必须答出明确的 yes/no;含糊、跑题、答不出 -> blocked。
   把"大概是吧"变成 pass,等于用模型的犹豫当结论。
2. **证据缺失 -> blocked。** 判定项绑定的截图文件不存在(计划里写了路径但没落盘)
   -> blocked,绝不按"看起来没问题"判通过。
3. **端点不通 -> 全部 blocked。** 第一次连接失败即停止后续请求,余下全部记
   blocked: judge_endpoint_unreachable。绝不降级成"没发现问题"。

判定轮**看不到执行轮的结论**:prompt 只用该判定项自身的封闭问题 + region + 截图,
不包含执行计划的说明、产物 notes、其它判定项结果 —— 否则等于把答案抄给它。

端点必须是 OpenAI 兼容的 /chat/completions(见 assets/judge-config.json)。
纯标准库,零依赖。
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from coordmap import PngUnsupported, crop_png_region, encode_png_8bit  # noqa: E402
from validate_cases import VERDICTS  # noqa: E402

SKILL_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = SKILL_DIR / "assets" / "judge-config.json"

IMAGE_MIME = {".png": "image/png", ".jpg": "image/jpeg",
              ".jpeg": "image/jpeg", ".webp": "image/webp"}

# 只有这两类请求会产出 verdict。locate_anchor / wait_for / convergence 是
# **动作级**请求(给执行轮轮询或定位用的),不是判定项,本脚本不消费它们。
JUDGED_PURPOSES = {"vision-closed", "judgment"}

SYSTEM_PROMPT = (
    "你是手机游戏截图的判定器。你只回答截图里**可见**的事实,不做推测、"
    "不评价游戏设计、不补充截图以外的信息。必须严格按格式输出两行,不要输出别的:\n"
    "ANSWER: YES|NO|UNCERTAIN\n"
    "EVIDENCE: 一句话说明你在图中哪个位置看到了什么\n"
    "看不清、被遮挡、信息不足时回答 UNCERTAIN。猜错比说不知道更糟。"
)

HEDGE_WORDS = ("可能", "大概", "似乎", "好像", "也许", "或许", "不确定", "无法确定",
               "不能确定", "无法判断", "难以判断", "看不清", "看不清楚", "部分",
               "maybe", "perhaps", "probably", "unclear", "not sure", "unsure",
               "hard to tell", "cannot tell", "can't tell", "partially", "might be",
               "unable to")

_YES_TOKENS = {"yes", "true", "y", "是", "有", "出现", "可见", "存在", "看到了"}
_NO_TOKENS = {"no", "false", "n", "否", "无", "没有", "未出现", "不存在", "未看到"}
_UNCERTAIN_TOKENS = {"uncertain", "unknown", "unsure", "不确定", "无法判断",
                     "无法确定", "不能确定", "看不清", "n/a", "na"}

_ANSWER_FIELD_RE = re.compile(
    r"(?im)^[ \t>*_`]*(?:ANSWER|答案|结论)[ \t]*[:：]?[ \t]*"
    r"([A-Za-z\u4e00-\u9fff]+)([^\n]*)")
_FIRST_TOKEN_RE = re.compile(r"^[ \t>*_`]*([A-Za-z\u4e00-\u9fff]+)([^\n]*)")


# ---------------------------------------------------------------- 严格解析

def parse_strict(raw: str | None) -> tuple[bool | None, str, str]:
    """严格解析视觉端点的响应。返回 (answer, kind, reason)。

    answer: True(yes) / False(no) / None(不算数 —— 按 blocked 处理)
    kind:   yes / no / uncertain / ambiguous / unparseable
    """
    text = (raw or "").strip()
    if not text:
        return None, "unparseable", "空响应"
    text = re.sub(r"^```[A-Za-z]*[ \t]*\n?", "", text)
    text = re.sub(r"\n?```[ \t]*$", "", text).strip()
    if not text:
        return None, "unparseable", "响应只有代码围栏"

    m = _ANSWER_FIELD_RE.search(text)
    if m:
        token, tail = m.group(1), m.group(2)
        line = m.group(0).strip()
    else:
        # 没有 ANSWER 字段:只认第一行的首个词。整段里随便找一个 yes/no
        # 会把"没有看到结算按钮"这类句子里的"没有"当成答案,那是猜。
        first = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
        m2 = _FIRST_TOKEN_RE.match(first)
        if not m2:
            return None, "unparseable", "响应里没有可解析的答案字段"
        token, tail, line = m2.group(1), m2.group(2), first

    tok = token.strip().lower().rstrip(".,;:!，。!:：")
    if tok in _UNCERTAIN_TOKENS:
        return None, "uncertain", "模型自述不确定"
    if tok in _YES_TOKENS:
        answer, kind = True, "yes"
    elif tok in _NO_TOKENS:
        answer, kind = False, "no"
    else:
        return None, "unparseable", f"答案字段不是一个明确的 yes/no: {token!r}"

    if kind == "yes" and re.search(r"\b(no|false)\b|否|没有", tail, re.I):
        return None, "ambiguous", f"答案字段自相矛盾: {line!r}"
    if kind == "no" and re.search(r"\b(yes|true)\b|是", tail, re.I):
        return None, "ambiguous", f"答案字段自相矛盾: {line!r}"
    hedges = [w for w in HEDGE_WORDS if w in line.lower()]
    if hedges:
        return None, "ambiguous", f"答案字段含含糊表述 {hedges}: {line!r}"
    return answer, kind, "ok"


# ---------------------------------------------------------------- 视觉端点

class EndpointDown(Exception):
    """端点不可达。一旦发生,本批次后续请求一律 blocked,不再逐个重试。"""


class JudgeCallError(Exception):
    """本次调用失败(HTTP 错误 / 响应不可解析)。只影响这一条判定。"""


def chat_completions_url(endpoint: str) -> str:
    e = endpoint.strip().rstrip("/")
    if not e:
        return ""
    if e.endswith("/chat/completions"):
        return e
    if re.search(r"/v\d+(?:beta)?$", e):
        return e + "/chat/completions"
    return e + "/v1/chat/completions"


def extract_content(obj: dict) -> str | None:
    choices = obj.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    first = choices[0] if isinstance(choices[0], dict) else {}
    content = (first.get("message") or {}).get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):        # 部分实现返回分段 content
        parts = [p.get("text", "") for p in content if isinstance(p, dict)]
        return "".join(parts) if parts else None
    if isinstance(first.get("text"), str):   # completions 风格兼容
        return first["text"]
    return None


def reasoning_ate_budget(obj: dict, max_tokens: int) -> str | None:
    """识别「想了但没落笔」的响应：推理内容吃光 max_tokens，content 为空。

    2026-09-20 在内网端点实测(deepseek-flash)：max_tokens=64 时 64 个 completion_tokens
    全是 reasoning_tokens，content 返回空字符串 —— 模型其实已经得出答案，只是没来得及输出。
    这种响应若只记一句「空响应」，排查的人看不出该调哪个参数；这里把该调的东西直接写进原因。
    """
    choices = obj.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return None
    first = choices[0]
    msg = first.get("message") or {}
    content = msg.get("content")
    empty = content is None or (isinstance(content, str) and not content.strip())
    reasoning = msg.get("reasoning_content") or msg.get("reasoning")
    if not (empty and isinstance(reasoning, str) and reasoning.strip()):
        return None
    usage = obj.get("usage") or {}
    spent = usage.get("completion_tokens")
    return (f"模型只输出了推理过程、没有给出结论（finish_reason={first.get('finish_reason')!r}，"
            f"completion_tokens={spent}，max_tokens={max_tokens}）：推理内容吃光了 token 预算。"
            f"把 max_tokens 调大，或把判定问题问得更封闭。")



class VisionClient:
    """OpenAI 兼容的视觉端点客户端。"""

    def __init__(self, opts: "JudgeOptions", log=print) -> None:
        self.endpoint = opts.endpoint
        self.url = chat_completions_url(opts.endpoint)
        self.model = opts.model
        self.api_key = opts.api_key
        self.timeout = opts.timeout
        self.temperature = opts.temperature
        self.max_tokens = opts.max_tokens
        self.max_image_bytes = opts.max_image_bytes
        self.calls = 0
        self.log = log

    def _request(self, payload: dict) -> tuple[int, bytes]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(
            self.url, data=json.dumps(payload).encode("utf-8"), headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as fp:  # noqa: S310
                return fp.status, fp.read()
        except urllib.error.HTTPError as exc:        # 注意:它也是 URLError 的子类
            return exc.code, exc.read() or b""
        except (urllib.error.URLError, socket.timeout, TimeoutError, OSError) as exc:
            raise EndpointDown(f"{type(exc).__name__}: "
                               f"{getattr(exc, 'reason', exc)}") from exc

    def chat(self, prompt: str, images: list[tuple[str, bytes]]) -> tuple[str, dict]:
        content: list[dict] = [{"type": "text", "text": prompt}]
        for mime, blob in images:
            b64 = base64.b64encode(blob).decode("ascii")
            content.append({"type": "image_url",
                            "image_url": {"url": f"data:{mime};base64,{b64}"}})
        messages = [{"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": content}]
        payload = {"model": self.model, "messages": messages,
                   "temperature": self.temperature, "max_tokens": self.max_tokens}

        self.calls += 1
        status, raw = self._request(payload)
        minimal = False
        if status == 400:
            # 有些 OpenAI 兼容实现不认 temperature/max_tokens。
            # 只降请求参数,不降判定标准。
            minimal = True
            status, raw = self._request({"model": self.model, "messages": messages})

        text = raw.decode("utf-8", "replace")
        if status in (401, 403):
            # 认证失败不是"这一条判不了",是整批都判不了 —— 快速失败,别拿同一个 401
            # 重试剩下所有请求(白等还可能触发风控)。密钥问题必须立刻让人看到。
            raise EndpointDown(f"HTTP {status}(认证失败,检查 TESTFLOW_JUDGE_API_KEY):"
                               f" {text[:200]}")
        if status >= 400:
            raise JudgeCallError(f"HTTP {status}: {text[:600]}")
        try:
            obj = json.loads(text)
        except json.JSONDecodeError as exc:
            raise JudgeCallError(f"响应不是 JSON({exc}): {text[:300]}") from exc
        content_text = extract_content(obj)
        if content_text is None or not content_text.strip():
            hint = reasoning_ate_budget(obj, self.max_tokens)
            if hint:
                raise JudgeCallError(hint)
            if content_text is None:
                raise JudgeCallError(f"响应里没有 choices[0].message.content: {text[:300]}")
        return content_text, {"httpStatus": status, "minimalPayload": minimal}


# ---------------------------------------------------------------- 配置

@dataclass
class JudgeOptions:
    run_path: Path | None = None
    requests_path: Path | None = None
    log_path: Path | None = None
    evidence_dir: Path | None = None
    endpoint: str = ""
    model: str = ""
    api_key: str | None = None
    timeout: float = 60.0
    temperature: float = 0.0
    max_tokens: int = 512
    max_images: int = 1
    max_image_bytes: int = 8_000_000
    crop: bool = True
    quiet: bool = False
    config_path: Path | None = field(default=DEFAULT_CONFIG)


def load_config(path: Path | None) -> dict:
    cfg = {
        "endpoint": "",              # 空 = 未配置 -> 全部 blocked,不是猜
        "model": "",
        "api_key": "",               # 别往这里填密钥(这个文件随 skill 入库)
        "api_key_env": "TESTFLOW_JUDGE_API_KEY",
        "timeout_s": 60,
        "temperature": 0,
        "max_tokens": 512,
        "max_images": 1,
        "max_image_bytes": 8_000_000,
        "crop_region": True,
        "_note": ("端点必须是 OpenAI 兼容的 /chat/completions。"
                  "密钥优先取 api_key_env 指向的环境变量。"),
    }
    if path is not None and Path(path).exists():
        try:
            loaded = json.loads(Path(path).read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise SystemExit(f"配置文件 JSON 解析失败: {path}: {exc}")
        if isinstance(loaded, dict):
            cfg.update({k: v for k, v in loaded.items() if not k.startswith("_")})
    return cfg


def resolve_config(args) -> JudgeOptions:
    cfg = load_config(args.config)
    opts = JudgeOptions(
        run_path=args.run,
        requests_path=args.requests,
        log_path=args.log_path,
        evidence_dir=args.evidence_dir,
        # 优先级:命令行 > 环境变量 > 配置文件。
        # 环境变量这一层是 HANDOFF §五 的口径(TESTFLOW_JUDGE_ENDPOINT/MODEL);
        # 之前只认配置文件,导致"照文档设了环境变量却依然全 blocked"。
        endpoint=(args.endpoint or os.environ.get("TESTFLOW_JUDGE_ENDPOINT")
                  or cfg.get("endpoint") or "").strip(),
        model=(args.model or os.environ.get("TESTFLOW_JUDGE_MODEL")
               or cfg.get("model") or "").strip(),
        timeout=float(args.timeout if args.timeout is not None else cfg.get("timeout_s", 60)),
        temperature=float(cfg.get("temperature", 0)),
        max_tokens=int(cfg.get("max_tokens", 512)),
        max_images=int(args.max_images if args.max_images is not None
                       else cfg.get("max_images", 1)),
        max_image_bytes=int(cfg.get("max_image_bytes", 8_000_000)),
        crop=(not args.no_crop) and bool(cfg.get("crop_region", True)),
        config_path=args.config,
    )
    key = args.api_key
    if not key:
        env_name = cfg.get("api_key_env") or "TESTFLOW_JUDGE_API_KEY"
        key = os.environ.get(env_name) or None
    if not key and (cfg.get("api_key") or "").strip():
        print("警告: 密钥写在配置文件里,而该文件随 skill 走、会被提交。"
              "请改用环境变量。", file=sys.stderr)
        key = cfg["api_key"].strip()
    opts.api_key = key
    if key and not key.isascii():
        raise SystemExit("错误: 密钥含非 ASCII 字符。HTTP 头只能放 latin-1,"
                         "多半是把说明文字一起复制进去了 —— 检查环境变量 "
                         f"{cfg.get('api_key_env')} 或 --api-key")
    return opts


# ---------------------------------------------------------------- 证据绑定

def find_pending_check(run: dict, request: dict) -> tuple[dict, dict] | None:
    """找到这条判定请求对应的、尚未判定的判定项。"""
    cid = request.get("caseId")
    for case in run.get("cases") or []:
        if case.get("id") != cid:
            continue
        for chk in case.get("checks") or []:
            if chk.get("source") != request.get("source"):
                continue
            if chk.get("verdict") == "pending_judgment":
                return case, chk
    return None


def resolve_evidence(request: dict, run: dict, evidence_dir: Path | None,
                     max_images: int) -> tuple[list[Path], dict]:
    """找出这次判定该看的截图。返回 (图片路径, 说明)。

    顺序:判定项上绑定的证据(run_case 按步骤精确绑定)> 按步骤名匹配的截图产物。
    **绑定不到就返回空**,上层判 blocked —— 不许拿同一用例的别的截图充数。
    """
    info: dict = {"source": None, "all": [], "nonImage": [], "missing": []}
    candidates: list[str] = []
    bound = find_pending_check(run, request)
    if bound:
        case, chk = bound
        candidates += [str(p) for p in (chk.get("evidence") or [])]
        info["source"] = "check.evidence"
        step = chk.get("after")
    else:
        step = request.get("step")
    if not candidates and step:
        for case in run.get("cases") or []:
            if case.get("id") != request.get("caseId"):
                continue
            for ent in case.get("evidenceIndex") or []:
                if ent.get("step") == step and ent.get("path"):
                    candidates.append(str(ent["path"]))
            if candidates:
                info["source"] = (info["source"] or "evidenceIndex.step")
    if evidence_dir and not candidates:
        stem = f"{request.get('caseId')}_{step}" if step else str(request.get("caseId"))
        if stem:
            candidates += [str(p) for p in sorted(Path(evidence_dir).glob(f"{stem}*"))]

    images: list[Path] = []
    for c in candidates:
        p = Path(c)
        if not p.exists():
            info["missing"].append(str(p))
            continue
        info["all"].append(str(p))
        if p.suffix.lower() in IMAGE_MIME:
            images.append(p)
        else:
            info["nonImage"].append(str(p))
    if len(images) > max_images:
        # 多张同一步骤的截图:取最后一张(那才是这一步执行完的画面)
        info["droppedByLimit"] = [str(p) for p in images[:-max_images]]
        images = images[-max_images:]
    return images, info


def build_prompt(request: dict, crop_note: str | None) -> str:
    question = request.get("question") or "(判定请求缺 question)"
    region = request.get("region")
    lines = [
        "判定任务",
        f"问题(封闭式,只能回答是或否):{question}",
    ]
    if region:
        lines.append(f"关注区域(归一化 [x0,y0,x1,y1]):{list(region)}")
    lines.append(crop_note or "图为整屏截图,只考虑关注区域内的内容。")
    lines += [
        "要求:",
        "- 只依据图中可见像素回答,不要依据常识或游戏应该长什么样来推断",
        "- 看不清 / 被遮挡 / 图里没有相关信息 -> ANSWER: UNCERTAIN",
        "输出格式(严格两行):",
        "ANSWER: YES|NO|UNCERTAIN",
        "EVIDENCE: 一句话",
    ]
    return "\n".join(lines)


def prepare_images(paths: list[Path], region, crop: bool) -> tuple[list[tuple[str, bytes]], list[dict]]:
    """读截图,按 region 裁剪。返回 (给端点的 (mime, bytes), 元信息)。"""
    out: list[tuple[str, bytes]] = []
    meta: list[dict] = []
    for p in paths:
        mime = IMAGE_MIME[p.suffix.lower()]
        raw = p.read_bytes()
        item = {"path": str(p), "sha256": hashlib.sha256(raw).hexdigest(),
                "bytes": len(raw), "sentBytes": len(raw), "cropped": False}
        blob = raw
        if crop and mime == "image/png":
            if not region:
                item["cropNote"] = "该请求没有 region,未裁剪"
            else:
                try:
                    blob, info = crop_png_region(p, region)
                    item.update({"cropped": True, "sentBytes": len(blob),
                                 "cropPx": info["cropPx"], "cropSize": info["cropSize"],
                                 "fullSize": info["full"],
                                 "sentSha256": hashlib.sha256(blob).hexdigest()})
                except PngUnsupported as exc:
                    # 裁不了就明确报错退出判定,不偷偷用整屏顶替
                    item["cropError"] = str(exc)
        elif crop and mime != "image/png":
            item["cropNote"] = "非 PNG,未裁剪"
        out.append((mime, blob))
        meta.append(item)
    return out, meta


# ---------------------------------------------------------------- 单条判定

def judge_request(client: VisionClient, request: dict, run: dict,
                  opts: JudgeOptions, endpoint_dead: bool) -> dict:
    """判定一条请求。返回判定记录(不含回填)。"""
    record: dict = {
        "purpose": request.get("purpose"),
        "caseId": request.get("caseId"),
        "source": request.get("source"),
        "step": request.get("step"),
        "question": request.get("question"),
        "region": request.get("region"),
        "expectedAnswer": request.get("expectedAnswer"),
        "observedAnswer": None,
        "verdict": "blocked",
        "reason": "",
        "model": client.model,
        "endpoint": client.url,
        "evidence": {"sent": [], "all": [], "nonImage": [], "missing": []},
    }

    if request.get("purpose") not in JUDGED_PURPOSES:
        record["verdict"] = "skipped"
        record["reason"] = (f"动作级请求(purpose={request.get('purpose')!r}),"
                            f"不是判定项 —— 判定轮不消费")
        return record

    bound = find_pending_check(run, request) if run else None
    if run and bound is None:
        record["verdict"] = "skipped"
        record["reason"] = "找不到对应的 pending_judgment 判定项(可能已判定过)"
        return record
    case, chk = bound if bound else (None, {})
    if record["expectedAnswer"] is None:
        record["expectedAnswer"] = (chk.get("expect") or {}).get("answer")
    record["checkKind"] = chk.get("kind")

    if endpoint_dead:
        record["reason"] = (f"judge_endpoint_unreachable: {opts.endpoint} "
                            f"不可达,后续请求一律 blocked")
        return record

    images, ev_info = resolve_evidence(request, run, opts.evidence_dir, opts.max_images)
    record["evidence"] = {"sent": [], "all": ev_info["all"],
                          "nonImage": ev_info["nonImage"], "missing": ev_info["missing"]}
    if not images:
        if ev_info["nonImage"] and not ev_info["missing"]:
            record["reason"] = (f"判定项只绑定了非图片证据({ev_info['nonImage']})。"
                                f"视觉端点看不见上报数据/日志 —— 这类判定项只能由人"
                                f"或别的通道判,判定轮不猜")
        elif ev_info["missing"]:
            record["reason"] = (f"证据文件不存在(计划里写了路径但没落盘):"
                                f"{ev_info['missing'][:3]}。证据缺失只能判 blocked")
        else:
            record["reason"] = ("判定项没有绑定任何截图证据。证据缺失只能判 blocked"
                                "(加 screenshot 动作,并确认它真的执行了)")
        return record

    try:
        blobs, metas = prepare_images(images, request.get("region"), opts.crop)
    except (OSError, PngUnsupported) as exc:
        record["reason"] = f"截图读取失败: {type(exc).__name__}: {exc}"
        return record

    for m in metas:
        if m.get("cropError"):
            record["reason"] = (f"region 裁剪失败({m['cropError']})。"
                                f"不拿整屏顶替 —— 那会把区域外的东西当成答案")
            return record
        if m["sentBytes"] > opts.max_image_bytes:
            record["reason"] = (f"截图过大({m['sentBytes']} 字节 > "
                                f"max_image_bytes={opts.max_image_bytes}),未发送。"
                                f"缩小 region 或调小截图分辨率")
            return record

    record["evidence"]["sent"] = [m["path"] for m in metas]
    record["evidence"]["images"] = metas
    record["evidenceError"] = ev_info.get("source")

    crop_note = None
    if metas and metas[0].get("cropped"):
        crop_note = (f"图是关注区域裁剪后的结果(原始 {metas[0]['fullSize']} -> "
                     f"裁剪 {metas[0]['cropSize']}),只判图内内容。")
    prompt = build_prompt(request, crop_note)

    started = time.monotonic()
    try:
        raw, call_meta = client.chat(prompt, blobs)
    except EndpointDown as exc:
        record["reason"] = f"judge_endpoint_unreachable: {exc}"
        record["endpointDown"] = True
        record["deadReason"] = str(exc)      # 后续记录复用它,避免前缀叠前缀
        return record
    except JudgeCallError as exc:
        record["reason"] = f"判定调用失败: {exc}"
        return record
    record["latencyMs"] = int((time.monotonic() - started) * 1000)
    record["callMeta"] = call_meta
    record["rawResponse"] = raw
    record["prompt"] = prompt

    answer, kind, why = parse_strict(raw)
    record["parse"] = {"kind": kind, "reason": why}
    if answer is None:
        record["reason"] = f"响应不能给出明确 yes/no({kind}: {why}) —— 不许猜,判 blocked"
        return record
    record["observedAnswer"] = "yes" if answer else "no"

    expected_raw = (record.get("expectedAnswer") or "").strip().lower()
    if expected_raw in ("yes", "no"):
        expected = expected_raw == "yes"
        record["verdict"] = "pass" if answer == expected else "fail"
        record["reason"] = (f"模型答 {record['observedAnswer']}({kind}),"
                            f"期望 {expected_raw} -> {record['verdict']}")
    elif request.get("purpose") == "judgment" or chk.get("kind") == "judgment":
        # judgment 判定项没有声明期望答案时,模型给的只是一个观测结论。
        # 期望不能来自观察(规矩二),所以停在这里,由人确认。
        record["verdict"] = "pending_manual"
        record["reason"] = (f"judgment 判定项未声明期望答案,判定轮只产出观测结论 "
                            f"observedAnswer={record['observedAnswer']},交人确认")
    else:
        record["verdict"] = "blocked"
        record["reason"] = "判定项没有声明期望答案(answer),无法机械比对 -> blocked"

    if record["verdict"] not in VERDICTS:
        raise RuntimeError(f"内部错误: 产出了未定义的 verdict {record['verdict']!r}")
    return record


def apply_verdict(run: dict, request: dict, record: dict) -> str:
    """把判定结论回填到 run json。返回实际写入的 verdict(explore 会改写)。"""
    bound = find_pending_check(run, request)
    if bound is None:
        return "not_found"
    case, chk = bound
    verdict = record["verdict"]
    note = record.get("reason", "")
    sent = (record.get("evidence") or {}).get("sent") or []

    if case.get("exploreMode") and verdict in ("pass", "fail"):
        # 与 run_case 的 explore 规则一致:探索模式不产出 pass/fail,只出 ok/anomaly
        verdict = "ok" if verdict == "pass" else "anomaly"
        note += "(explore 用例:按设计换算为 " + verdict + ",不计入通过率)"

    chk["verdict"] = verdict
    chk["note"] = note
    if record.get("observedAnswer") is not None:
        chk["actual"] = {
            "observedAnswer": record["observedAnswer"],
            "expectedAnswer": record.get("expectedAnswer"),
            "parseKind": (record.get("parse") or {}).get("kind"),
            "rawResponse": record.get("rawResponse"),
            "screenshot": record["evidence"]["sent"],
            "screenshotSha256": {m["path"]: m["sha256"]
                                 for m in record["evidence"].get("images") or []},
            "screenshotSentSha256": {m["path"]: m.get("sentSha256")
                                     for m in record["evidence"].get("images") or []},
            "crop": {m["path"]: m.get("cropPx")
                     for m in record["evidence"].get("images") or [] if m.get("cropped")},
            "judgedBy": {"model": record.get("model"), "endpoint": record.get("endpoint")},
            "note": "视觉判定结论。实际值来自模型对截图的观察,"
                    "截图与哈希已记录,可复核",
        }
    elif verdict in ("fail", "pass"):
        chk["actual"] = {"note": "判定轮未产出观测值"}

    if record["evidence"]["sent"]:
        chk["evidence"] = list(sent)
    chk["judgedBy"] = {"engine": "judge.py", "model": record.get("model"),
                       "endpoint": record.get("endpoint"),
                       "verdictFrom": record.get("verdict"),
                       "rawVerdict": record["verdict"]}
    return verdict


# ---------------------------------------------------------------- 请求装载

def load_requests(run_path: Path | None, requests_path: Path | None,
                  run: dict | None) -> tuple[list[dict], str]:
    if requests_path is not None:
        data = json.loads(Path(requests_path).read_text(encoding="utf-8"))
        return list(data), str(requests_path)
    if run_path is not None:
        sibling = Path(run_path).with_name(
            Path(run_path).name.replace(".json", ".judge-requests.json"))
        if sibling.exists():
            data = json.loads(sibling.read_text(encoding="utf-8"))
            return list(data), str(sibling)
    if run is not None:
        return requests_from_run(run), "(由 run json 现场推导)"
    return [], "(无)"


def requests_from_run(run: dict) -> list[dict]:
    """没有 judge-requests 文件时,从 run json 的待判项现场推导请求。"""
    out: list[dict] = []
    for case in run.get("cases") or []:
        for chk in case.get("checks") or []:
            if chk.get("verdict") != "pending_judgment":
                continue
            expect = chk.get("expect") or {}
            kind = chk.get("kind")
            if kind == "manual":
                continue
            out.append({
                "purpose": "judgment" if kind == "judgment" else "vision-closed",
                "caseId": case.get("id"),
                "source": chk.get("source"),
                "after": chk.get("after"),
                "question": expect.get("question"),
                "region": expect.get("region"),
                "expectedAnswer": expect.get("answer"),
                "mode": chk.get("mode") or case.get("judge"),
                "exploreMode": bool(case.get("exploreMode")),
                "note": "由 run json 现场推导(没有 judge-requests 文件)",
                "synthesized": True,
            })
    return out


# ---------------------------------------------------------------- 主流程

def run_judgment(opts: JudgeOptions) -> tuple[int, dict]:
    run: dict | None = None
    if opts.run_path is not None:
        if not Path(opts.run_path).exists():
            raise SystemExit(f"错误: 找不到 run 结果 {opts.run_path}")
        try:
            run = json.loads(Path(opts.run_path).read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise SystemExit(f"错误: run json 解析失败: {exc}")
    if opts.run_path is None and opts.requests_path is None:
        raise SystemExit("错误: 至少要给 --run 或 --requests")

    requests, req_source = load_requests(opts.run_path, opts.requests_path, run)
    log = (lambda *a: None) if opts.quiet else print

    summary: dict = {
        "runId": (run or {}).get("runId"),
        "requestSource": req_source,
        "requests": len(requests),
        "endpoint": opts.endpoint,
        "url": chat_completions_url(opts.endpoint),
        "model": opts.model,
        "apiKeyProvided": bool(opts.api_key),
        "counts": {},
        "records": [],
    }

    if not requests:
        log("没有任何判定请求 —— 执行轮没有产出待判项,判定轮无事可做")
        return 1, summary

    if not opts.endpoint or not opts.model:
        # 端点没配 -> 全部 blocked。这是配置缺失,不是"没发现问题"。
        for req in requests:
            rec = {
                "purpose": req.get("purpose"), "caseId": req.get("caseId"),
                "source": req.get("source"), "question": req.get("question"),
                "region": req.get("region"), "expectedAnswer": req.get("expectedAnswer"),
                "observedAnswer": None, "verdict": "blocked", "model": None,
                "endpoint": None, "evidence": {"sent": [], "all": [], "nonImage": [],
                                               "missing": []},
                "reason": "judge 端点未配置(endpoint/model 为空)—— 全部 blocked。"
                          "见 assets/judge-config.json",
            }
            summary["records"].append(rec)
            if run is not None and rec["purpose"] in JUDGED_PURPOSES:
                rec["writtenVerdict"] = apply_verdict(run, req, rec)
            log(f"  {str(rec.get('caseId')):20s} {str(rec.get('source')):4s} "
                f"{rec['verdict']:16s} {rec.get('reason', '')[:80]}")
        summary["counts"] = {"blocked": len(requests)}
        summary["configMissing"] = True
        _finalise(run, opts, summary, log)
        return 1, summary

    client = VisionClient(opts, log=log)
    log(f"判定轮: {len(requests)} 个请求 -> {client.url}(model={opts.model}, "
        f"密钥={'有' if opts.api_key else '无'})")

    dead = False
    dead_reason = ""
    for req in requests:
        if dead and req.get("purpose") in JUDGED_PURPOSES:
            rec = {
                "purpose": req.get("purpose"), "caseId": req.get("caseId"),
                "source": req.get("source"), "question": req.get("question"),
                "region": req.get("region"), "expectedAnswer": req.get("expectedAnswer"),
                "observedAnswer": None, "verdict": "blocked", "model": opts.model,
                "endpoint": client.url,
                "reason": f"judge_endpoint_unreachable: {dead_reason}",
                "evidence": {"sent": [], "all": [], "nonImage": [], "missing": []},
            }
        else:
            rec = judge_request(client, req, run or {}, opts, dead)
            if rec.get("endpointDown"):
                dead = True
                dead_reason = rec.get("deadReason") or rec.get("reason", "")
        summary["records"].append(rec)
        if run is not None and rec.get("purpose") in JUDGED_PURPOSES:
            written = apply_verdict(run, req, rec)
            rec["writtenVerdict"] = written
            if written not in ("not_found", rec["verdict"]):
                summary["exploreRemappedTo"] = summary.get("exploreRemappedTo", 0) + 1
        log(f"  {str(rec.get('caseId')):20s} {str(rec.get('source')):4s} "
            f"{rec['verdict']:16s} {rec.get('reason', '')[:80]}")

    counts: dict = {}
    for rec in summary["records"]:
        counts[rec["verdict"]] = counts.get(rec["verdict"], 0) + 1
    summary["counts"] = counts
    if dead:
        summary["endpointUnreachable"] = dead_reason

    _finalise(run, opts, summary, log)
    judged = counts.get("pass", 0) + counts.get("fail", 0)
    return (0 if judged else 1), summary


def _finalise(run: dict | None, opts: JudgeOptions, summary: dict, log) -> None:
    counts = summary["counts"]
    judged = counts.get("pass", 0) + counts.get("fail", 0)

    log_path = opts.log_path
    if log_path is None and opts.run_path is not None:
        log_path = Path(opts.run_path).with_name(
            Path(opts.run_path).name.replace(".json", ".judgment.jsonl"))
    if log_path is not None:
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        with Path(log_path).open("w", encoding="utf-8") as fh:
            for rec in summary["records"]:
                fh.write(json.dumps({"at": stamp, **rec}, ensure_ascii=False) + "\n")
        summary["logPath"] = str(log_path)

    if run is not None and opts.run_path is not None:
        run["judgment"] = {
            "at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            "engine": "judge.py",
            "model": opts.model,
            "endpoint": chat_completions_url(opts.endpoint),
            "apiKeyProvided": summary["apiKeyProvided"],
            "counts": counts,
            "logPath": summary.get("logPath"),
            "note": ("本字段由判定轮(独立的另一次调用)写入。执行轮只产出 actual;"
                     "play/judge 分离见 SKILL.md 规矩一。"),
        }
        Path(opts.run_path).write_text(
            json.dumps(run, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        log(f"run 结果已回填: {opts.run_path}")

    log("")
    log(f"判定统计: {counts}")
    log(f"产出结论(pass/fail): {judged} 条")
    if summary.get("endpointUnreachable"):
        log(f"⚠ 端点不可达: {summary['endpointUnreachable']} —— 全部判 blocked,不是猜")
    if summary.get("logPath"):
        log(f"判定日志: {summary['logPath']}")
    log("blocked 反映的是证据缺失或判定能力缺口,不是产品缺陷,因此不开 bug 单。")


# ---------------------------------------------------------------- 预检

def preflight(opts: JudgeOptions) -> int:
    run = json.loads(Path(opts.run_path).read_text(encoding="utf-8"))
    requests, source = load_requests(opts.run_path, opts.requests_path, run)
    if not requests:
        print("没有任何待判请求(判定项都已判过,或执行轮没产出待判项)")
        return 1
    print(f"判定请求 {len(requests)} 个(来自 {source})")
    print(f"端点: {chat_completions_url(opts.endpoint) or '(未配置)'} / "
          f"model={opts.model or '(未配置)'} / 密钥={'有' if opts.api_key else '无'}")
    print()
    missing = 0
    for req in requests:
        images, info = resolve_evidence(req, run, opts.evidence_dir, opts.max_images)
        if req.get("purpose") not in JUDGED_PURPOSES:
            state = "跳过(动作级请求)"
        elif images:
            state = f"可判({len(images)} 张截图)"
        else:
            state = "blocked: " + (
                f"证据文件不存在 {info['missing'][:2]}" if info["missing"]
                else f"未绑定截图证据 {info['nonImage']}" if info["nonImage"]
                else "没有任何证据绑定")
            missing += 1
        print(f"  {str(req.get('caseId')):20s} {str(req.get('source')):4s} "
              f"{str(req.get('purpose')):14s} {state}")
    print()
    if missing:
        print(f"预检结果: {missing} 个请求缺证据 —— 直接跑判定只会得到 blocked。"
              f"先补齐截图(run_case.py 加 screenshot 动作并真的执行)")
        return 1
    print("预检结果: 全部请求都有截图可判")
    return 0


# ---------------------------------------------------------------- 人工复核

def list_judgments(run_path: Path, quiet: bool = False) -> int:
    """列出所有判定项与结论,供人快速复核(6-triage「复核失败的判定」用)。"""
    run = json.loads(Path(run_path).read_text(encoding="utf-8"))
    rows: list[tuple[str, str, str, str]] = []
    for case in run.get("cases") or []:
        for chk in case.get("checks") or []:
            rows.append((str(case.get("id")), str(chk.get("source")),
                         str(chk.get("verdict")), str(chk.get("note") or "")[:90]))
    if not quiet:
        print(f"判定项 {len(rows)} 条({run.get('runId')}):")
        for cid, src, verdict, note in rows:
            print(f"  {cid:22s} {src:4s} {verdict:17s} {note}")
        print()
        print("复核入口: --override <用例ID>/<判定项> --verdict <结论> "
              "--by <复核人> --reason <为什么>")
        print("规矩(见 references/6-triage.md):判定轮给出 pass 但证据不足以支撑时,"
              "标 blocked 并写明「判定证据不足」,**不要直接改成 fail** —— "
              "那会失去可追溯性。")
    return 0


def apply_override(run_path: Path, target: str, verdict: str | None, by: str | None,
                   reason: str | None, quiet: bool = False) -> int:
    """人工复核:改一条判定项的结论,并把「谁、何时、为什么、原来是啥」记进去。"""
    if not verdict or not by or not reason:
        print("错误: --override 需要 --verdict、--by、--reason(缺一不可)",
              file=sys.stderr)
        return 2
    case_id, _, source = target.partition("/")
    if not case_id or not source:
        print("错误: --override 的格式是 <用例ID>/<判定项>,例如 REQ-NET-C01/E3",
              file=sys.stderr)
        return 2
    run = json.loads(Path(run_path).read_text(encoding="utf-8"))
    hit = None
    for case in run.get("cases") or []:
        if str(case.get("id")) != case_id:
            continue
        for chk in case.get("checks") or []:
            if str(chk.get("source")) == source:
                hit = (case, chk)
    if not hit:
        print(f"错误: 在 {run_path} 里找不到 {case_id}/{source}", file=sys.stderr)
        return 2
    case, chk = hit
    previous = chk.get("verdict")
    final = verdict
    note = reason
    if case.get("exploreMode") and final in ("pass", "fail"):
        final = "ok" if final == "pass" else "anomaly"
        note += "(explore 用例:按设计换算为 " + final + ")"
    if verdict == "fail" and previous == "pass":
        print("提醒: 6-triage 明确写了「判定轮给出 pass 但证据不足时标 blocked,"
              "不要直接改成 fail」—— 直接改 fail 会失去可追溯性。"
              "如果你确认这是真缺陷,请连同证据一起写进 --reason。",
              file=sys.stderr)
    chk["verdict"] = final
    chk["note"] = f"[人工复核] {note}"
    chk["judgedBy"] = {
        "engine": "judge.py(人工复核)",
        "overriddenBy": by,
        "overriddenAt": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "previousVerdict": previous,
        "overrideReason": reason,
        "appliedVerdict": final,
    }
    if isinstance(chk.get("actual"), dict):
        chk["actual"]["humanReview"] = {"by": by, "reason": reason,
                                        "previousVerdict": previous}
    Path(run_path).write_text(json.dumps(run, ensure_ascii=False, indent=2) + "\n",
                              encoding="utf-8")
    if not quiet:
        print(f"已复核: {case_id}/{source} {previous} -> {final}(by {by})")
        print(f"原因已记入 run json:`{reason}`")
        print("可追溯性: previousVerdict / overriddenBy / overriddenAt / overrideReason "
              "都在 judgedBy 里,重跑提 bug 会把这条复核写进 bug 单。")
    return 0


# ---------------------------------------------------------------- 自测

def selftest() -> int:
    import http.server
    import tempfile
    import threading

    # 假密钥在运行时拼出来:仓库里不留"密钥形状"的字面量,
    # 否则 check_secrets.py 会把本文件自己报成泄露。
    fake_key = "-".join(["sk", "test", "key", "0123456789"])

    fails: list[str] = []

    def ck(name: str, cond: bool, detail: str = "") -> None:
        if cond:
            print(f"  ok   {name}")
        else:
            print(f"  FAIL {name}" + (f" — {detail}" if detail else ""))
            fails.append(name)

    print("== 严格解析(含糊即 blocked,绝不猜) ==")
    parse_cases = [
        ("ANSWER: YES\nEVIDENCE: 屏幕底部可见「结算」按钮", True, "yes"),
        ("ANSWER: NO\nEVIDENCE: 没看到", False, "no"),
        ("ANSWER: UNCERTAIN\nEVIDENCE: 图太糊", None, "uncertain"),
        ("ANSWER: 是\nEVIDENCE: 有", True, "yes"),
        ("答案:否", False, "no"),
        ("结论：yes", True, "yes"),
        ("```\nANSWER: YES\nEVIDENCE: x\n```", True, "yes"),
        ("YES", True, "yes"),
        ("  no  ", False, "no"),
        ("", None, "unparseable"),
        ("可能吧,看不太清", None, "unparseable"),
        ("ANSWER: 可能 YES", None, None),
        ("ANSWER: YES 但也可能是 NO", None, None),
        ("ANSWER: NO，不过 YES 也有可能", None, None),
        ("图中有结算按钮", None, "unparseable"),
    ]
    for raw, want_answer, want_kind in parse_cases:
        got, kind, why = parse_strict(raw)
        label = raw.replace("\n", "⏎")[:38]
        ok = got is want_answer and (want_kind is None or kind == want_kind)
        ck(f"parse {label!r} -> {got}/{kind}", ok,
           f"期望 {want_answer}/{want_kind},实为 {got}/{kind} ({why})")

    print("\n== 端点不可达 -> 全部 blocked ==")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        def make_run(case_extra: dict | None = None, evidence: str | None = None) -> dict:
            shot = str(tmp / "evidence" / "C1_S3_01.png")
            case = {"id": "C1", "exploreMode": False, "checks": [
                {"source": "E1", "after": "S3", "kind": "assert", "mode": "vision-closed",
                 "expect": {"question": "底部是否出现「结算」按钮?", "region": [0.0, 0.7, 1.0, 1.0],
                            "answer": "yes"},
                 "verdict": "pending_judgment",
                 "evidence": [shot] if evidence else []},
            ], "evidenceIndex": [{"step": "S3", "path": shot}]}
            case.update(case_extra or {})
            return {"runId": "run-selftest", "cases": [case], "judgeRequests": []}

        def write(run: dict) -> Path:
            p = tmp / "run-selftest.json"
            p.write_text(json.dumps(run, ensure_ascii=False), encoding="utf-8")
            return p

        def shot_path() -> Path:
            p = tmp / "evidence" / "C1_S3_01.png"
            p.parent.mkdir(parents=True, exist_ok=True)
            px = bytes([200, 30, 30, 30, 200, 30, 30, 30, 200, 255, 255, 255])
            p.write_bytes(encode_png_8bit(4, 1, 3, px))
            return p

        # 1) 截图没落盘 -> blocked
        run = make_run(evidence=True)
        p = write(run)
        code, summary = run_judgment(JudgeOptions(
            run_path=p, quiet=True, model="stub", endpoint="http://127.0.0.1:1/v1"))
        rec = summary["records"][0]
        ck("证据文件不存在 -> blocked", code == 1 and rec["verdict"] == "blocked",
           f"code={code} verdict={rec['verdict']} reason={rec.get('reason')}")
        ck("blocked 原因写明文件不存在", "不存在" in rec.get("reason", ""), rec.get("reason", ""))
        ck("未调用端点(不该为空证据花 token)", "judge_endpoint_unreachable" not in rec.get("reason", ""),
           rec.get("reason", ""))

        # 2) 完全没有证据绑定 -> blocked
        run = make_run(evidence=False)
        run["cases"][0]["evidenceIndex"] = []
        p = write(run)
        code, summary = run_judgment(JudgeOptions(
            run_path=p, quiet=True, model="stub", endpoint="http://127.0.0.1:1/v1"))
        rec = summary["records"][0]
        ck("未绑定证据 -> blocked", code == 1 and rec["verdict"] == "blocked",
           f"code={code} verdict={rec['verdict']}")

        # 3) 端点未配置 -> 全部 blocked(不是"没发现问题")
        run = make_run(evidence=True)
        p = write(run)
        code, summary = run_judgment(JudgeOptions(run_path=p, quiet=True, endpoint="", model=""))
        ck("端点未配置 -> blocked", code == 1
           and summary["records"][0]["verdict"] == "blocked", str(summary["counts"]))

        # 4) 端点不可达(端口无人监听)-> blocked,且原因写明不可达
        shot_path()
        run = make_run(evidence=True)
        p = write(run)
        code, summary = run_judgment(JudgeOptions(
            run_path=p, quiet=True, model="stub", endpoint="http://127.0.0.1:1/v1",
            timeout=5))
        rec = summary["records"][0]
        ck("端点不可达 -> blocked", code == 1 and rec["verdict"] == "blocked",
           f"code={code} verdict={rec['verdict']}")
        ck("原因写明 unreachable", "unreachable" in rec.get("reason", ""), rec.get("reason", ""))

        # 5) 本地桩端点:YES -> pass;NO -> fail;含糊 -> blocked
        replies = {"text": "ANSWER: YES\nEVIDENCE: 桩", "reasoning": None, "finishReason": None}
        seen: list[dict] = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):                      # noqa: N802
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
                seen.append({"body": body, "auth": self.headers.get("Authorization"),
                             "path": self.path})
                message = {"content": replies["text"]}
                if replies.get("reasoning"):        # 桩会推理的模型:答案是空的,只有推理内容
                    message["reasoning_content"] = replies["reasoning"]
                choice = {"message": message}
                if replies.get("finishReason"):
                    choice["finish_reason"] = replies["finishReason"]
                out = json.dumps({"choices": [choice],
                                  "usage": {"completion_tokens": 512}}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

            def log_message(self, *a):              # 静音
                return

        httpd = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        port = httpd.server_address[1]
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        endpoint = f"http://127.0.0.1:{port}/v1"
        try:
            # 5a YES -> pass
            replies["text"] = "ANSWER: YES\nEVIDENCE: 底部可见按钮"
            run = make_run(evidence=True)
            p = write(run)
            code, summary = run_judgment(JudgeOptions(
                run_path=p, quiet=True, model="stub-model", endpoint=endpoint,
                api_key=fake_key))
            rec = summary["records"][0]
            ck("端点 YES -> pass", code == 0 and rec["verdict"] == "pass",
               f"code={code} verdict={rec['verdict']} reason={rec.get('reason')}")
            ck("回填进 run json", json.loads(p.read_text(encoding="utf-8"))
               ["cases"][0]["checks"][0]["verdict"] == "pass")
            ck("判定日志记录了模型/原始响应/截图哈希",
               all(k in (summary.get("logPath") and
                         Path(summary["logPath"]).read_text(encoding="utf-8") or "")
                   for k in ("stub-model", "ANSWER: YES", "sha256")),
               str(summary.get("logPath")))
            ck("prompt 不含执行轮结论", "notes" not in rec.get("prompt", "")
               and "plan" not in rec.get("prompt", ""), rec.get("prompt", "")[:120])
            ck("请求了裁剪后的图片与 region 文案",
               "裁剪" in rec.get("prompt", "") and seen[-1]["body"]["messages"][1]
               ["content"][1]["image_url"]["url"].startswith("data:image/png;base64,"))
            ck("Authorization 头带上密钥",
               seen[-1]["auth"] == f"Bearer {fake_key}", str(seen[-1]["auth"]))
            ck("端点 URL 补全为 /v1/chat/completions",
               seen[-1]["path"] == "/v1/chat/completions", seen[-1]["path"])

            # 5b NO -> fail(期望是 yes)
            replies["text"] = "ANSWER: NO\nEVIDENCE: 没有看到"
            run = make_run(evidence=True)
            p = write(run)
            code, summary = run_judgment(JudgeOptions(
                run_path=p, quiet=True, model="stub-model", endpoint=endpoint))
            rec = summary["records"][0]
            ck("端点 NO 且期望 yes -> fail", code == 0 and rec["verdict"] == "fail",
               f"code={code} verdict={rec['verdict']}")
            chk_back = json.loads(p.read_text(encoding="utf-8"))["cases"][0]["checks"][0]
            ck("fail 写入了观测值(rawResponse/截图哈希)",
               chk_back["actual"]["observedAnswer"] == "no"
               and chk_back["actual"]["rawResponse"].startswith("ANSWER: NO")
               and chk_back["actual"]["screenshotSha256"], str(chk_back["actual"])[:200])
            ck("fail 绑定了证据(fail 必须带证据)", bool(chk_back.get("evidence")))

            # 5c 含糊 -> blocked(即使端点通了)
            replies["text"] = "ANSWER: 可能是的吧"
            run = make_run(evidence=True)
            p = write(run)
            code, summary = run_judgment(JudgeOptions(
                run_path=p, quiet=True, model="stub-model", endpoint=endpoint))
            rec = summary["records"][0]
            ck("含糊响应 -> blocked", code == 1 and rec["verdict"] == "blocked",
               f"code={code} verdict={rec['verdict']}")
            ck("含糊响应不覆盖原判定项为 pass",
               json.loads(p.read_text(encoding="utf-8"))["cases"][0]["checks"][0]
               ["verdict"] == "blocked")

            # 5c-2 推理吃光 token 预算(实测会推理的模型):content 空、只有 reasoning_content
            #      -> blocked,且原因必须直接点出该调 max_tokens,否则排查的人无从下手
            replies["text"] = ""
            replies["reasoning"] = "让我想想……屏幕上确实有 Xcode Cloud。答案"
            replies["finishReason"] = "length"
            run = make_run(evidence=True)
            p = write(run)
            code, summary = run_judgment(JudgeOptions(
                run_path=p, quiet=True, model="stub-model", endpoint=endpoint))
            rec = summary["records"][0]
            ck("推理吃光预算 -> blocked", code == 1 and rec["verdict"] == "blocked",
               f"code={code} verdict={rec['verdict']} reason={rec.get('reason')}")
            ck("原因写明该调 max_tokens(可照着修)",
               "max_tokens" in rec.get("reason", ""), rec.get("reason", ""))
            ck("不会把模型的推理内容当成结论",
               "pass" not in str(rec.get("verdict")), str(rec.get("verdict")))
            replies["text"] = "ANSWER: YES\nEVIDENCE: 桩"
            replies["reasoning"] = None
            replies["finishReason"] = None

            # 5d explore 用例 -> 换算成 ok/anomaly,不产出 pass/fail            replies["text"] = "ANSWER: YES\nEVIDENCE: 有"
            run = make_run({"exploreMode": True}, evidence=True)
            p = write(run)
            code, summary = run_judgment(JudgeOptions(
                run_path=p, quiet=True, model="stub-model", endpoint=endpoint))
            back = json.loads(p.read_text(encoding="utf-8"))["cases"][0]["checks"][0]
            ck("explore 用例换算为 ok", back["verdict"] == "ok", back["verdict"])
            ck("explore 仍保留原始判定以便追溯",
               back["judgedBy"]["rawVerdict"] == "pass", str(back.get("judgedBy")))
            replies["text"] = "ANSWER: NO\nEVIDENCE: 没有"
            run = make_run({"exploreMode": True}, evidence=True)
            p = write(run)
            run_judgment(JudgeOptions(run_path=p, quiet=True, model="stub-model",
                                      endpoint=endpoint))
            back = json.loads(p.read_text(encoding="utf-8"))["cases"][0]["checks"][0]
            ck("explore 不通过换算为 anomaly", back["verdict"] == "anomaly", back["verdict"])

            # 5e judgment 判定项无期望 -> pending_manual + 观测结论
            run = make_run(evidence=True)
            chk = run["cases"][0]["checks"][0]
            chk.update({"kind": "judgment", "mode": None,
                        "expect": {"question": "断线提示是否说明了原因?"}})
            p = write(run)
            replies["text"] = "ANSWER: NO\nEVIDENCE: 只有重试按钮"
            code, summary = run_judgment(JudgeOptions(
                run_path=p, quiet=True, model="stub-model", endpoint=endpoint))
            back = json.loads(p.read_text(encoding="utf-8"))["cases"][0]["checks"][0]
            ck("judgment 无期望 -> pending_manual", back["verdict"] == "pending_manual",
               back["verdict"])
            ck("judgment 保留观测结论供人确认",
               back["actual"]["observedAnswer"] == "no", str(back.get("actual"))[:160])

            # 5f 幂等:同一请求重复判定不应再改动已判定的项
            run = make_run(evidence=True)
            p = write(run)
            replies["text"] = "ANSWER: YES\nEVIDENCE: 有"
            run_judgment(JudgeOptions(run_path=p, quiet=True, model="stub-model",
                                      endpoint=endpoint))
            code2, summary2 = run_judgment(JudgeOptions(
                run_path=p, quiet=True, model="stub-model", endpoint=endpoint))
            ck("已判定的项不会被重复判定",
               code2 == 1 and summary2["requests"] == 0,
               f"code={code2} requests={summary2['requests']} "
               f"records={summary2['records'][:1]}")

            # 人工复核(6-triage「复核失败的判定」):改结论 + 留痕
            replies["text"] = "ANSWER: YES\nEVIDENCE: 有"
            run = make_run(evidence=True)
            p = write(run)
            run_judgment(JudgeOptions(run_path=p, quiet=True, model="stub-model",
                                      endpoint=endpoint))
            rc = apply_override(p, "C1/E1", "blocked", "liuyuhang",
                                "判定证据不足(截图看不出底部区域)", quiet=True)
            back = json.loads(p.read_text(encoding="utf-8"))["cases"][0]["checks"][0]
            ck("人工复核: 改结论并留痕(原结论/复核人/原因都记下)",
               rc == 0 and back["verdict"] == "blocked"
               and back["judgedBy"]["previousVerdict"] == "pass"
               and back["judgedBy"]["overriddenBy"] == "liuyuhang"
               and back["judgedBy"]["overrideReason"].startswith("判定证据不足"),
               str(back["judgedBy"]))
            ck("人工复核: 缺 --reason 被拒绝",
               apply_override(p, "C1/E1", "blocked", "x", None, quiet=True) == 2)
            ck("人工复核: 找不到判定项被拒绝",
               apply_override(p, "C1/E9", "blocked", "x", "r", quiet=True) == 2)
            ck("人工复核: --list 可用", list_judgments(p, quiet=True) == 0)
        finally:
            httpd.shutdown()
            httpd.server_close()

    # 端点/模型的配置优先级:命令行 > 环境变量 > 配置文件(环境变量是 HANDOFF §五 的口径)
    import argparse as _ap

    saved = {k: os.environ.get(k) for k in ("TESTFLOW_JUDGE_ENDPOINT",
                                            "TESTFLOW_JUDGE_MODEL")}
    base_ns = dict(config=None, api_key=None, timeout=None, max_images=None,
                   no_crop=False, run=None, requests=None, log_path=None,
                   evidence_dir=None)
    try:
        os.environ["TESTFLOW_JUDGE_ENDPOINT"] = "http://env-host:1234/v1"
        os.environ["TESTFLOW_JUDGE_MODEL"] = "env-model"
        o_env = resolve_config(_ap.Namespace(endpoint=None, model=None, **base_ns))
        ck("环境变量能配端点与模型(照 HANDOFF §五 设了就生效)",
           o_env.endpoint == "http://env-host:1234/v1" and o_env.model == "env-model",
           f"{o_env.endpoint} / {o_env.model}")
        o_cli = resolve_config(_ap.Namespace(endpoint="http://cli/v1", model="cli-model",
                                             **base_ns))
        ck("命令行优先于环境变量",
           o_cli.endpoint == "http://cli/v1" and o_cli.model == "cli-model",
           f"{o_cli.endpoint} / {o_cli.model}")
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    print()
    if fails:
        print(f"自测失败 {len(fails)} 项:")
        for f in fails:
            print("  ✗", f)
        return 1
    print(f"自测通过(严格解析 {len(parse_cases)} 例;证据缺失/端点不可达/含糊 -> blocked;"
          f"YES->pass、NO->fail;explore 换算;judgment 无期望 -> pending_manual;重复判定幂等)")
    return 0


# ---------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser(
        description="testflow 判定轮:读判定请求 + 截图 -> 视觉端点 -> 严格 yes/no "
                    "-> 回填 verdict(与执行轮分开调用,见 SKILL.md 规矩一)")
    ap.add_argument("--run", type=Path,
                    help="run-<ts>.json;verdict 会回填到这个文件")
    ap.add_argument("--requests", type=Path,
                    help="判定请求 json;默认取 run 同名的 .judge-requests.json")
    ap.add_argument("--out", dest="log_path", type=Path,
                    help="判定日志 jsonl 路径;默认 <run>.judgment.jsonl")
    ap.add_argument("--evidence-dir", type=Path,
                    help="截图目录;证据没绑定时按 <用例ID>_<步骤ID>* 兜底查找")
    ap.add_argument("--config", type=Path, default=DEFAULT_CONFIG,
                    help=f"端点配置;默认 {DEFAULT_CONFIG}")
    ap.add_argument("--endpoint", default=None, help="覆盖配置里的端点")
    ap.add_argument("--model", default=None, help="覆盖配置里的模型名")
    ap.add_argument("--api-key", default=None,
                    help="密钥;默认取配置里的 api_key_env 环境变量")
    ap.add_argument("--timeout", type=float, default=None, help="单次调用超时秒数")
    ap.add_argument("--max-images", type=int, default=None,
                    help="每条判定最多送几张截图(默认 1,取最后一张)")
    ap.add_argument("--no-crop", action="store_true",
                    help="不按 region 裁剪,整屏送端点(默认裁剪)")
    ap.add_argument("--preflight", action="store_true",
                    help="只检查证据齐不齐,不调用端点、不花 token")
    ap.add_argument("--list", action="store_true",
                    help="列出该 run 里所有判定项与结论(人工复核用)")
    ap.add_argument("--override", metavar="用例ID/判定项",
                    help="人工复核:改某条判定项的结论(需配 --verdict --by --reason)")
    ap.add_argument("--verdict", choices=sorted(VERDICTS),
                    help="人工复核后的结论")
    ap.add_argument("--by", metavar="复核人")
    ap.add_argument("--reason", help="为什么改(必须写,会记进 run json 与 bug 单)")
    ap.add_argument("--selftest", action="store_true", help="跑内置自测")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    if args.list or args.override:
        if args.run is None:
            print("错误: --list / --override 需要 --run", file=sys.stderr)
            return 2
        if args.override:
            return apply_override(args.run, args.override, args.verdict,
                                  args.by, args.reason, quiet=args.quiet)
        return list_judgments(args.run, quiet=args.quiet)

    opts = resolve_config(args)
    if args.preflight:
        if opts.run_path is None:
            print("错误: --preflight 需要 --run", file=sys.stderr)
            return 2
        return preflight(opts)

    code, summary = run_judgment(opts)
    if args.quiet:
        print(json.dumps({k: v for k, v in summary.items() if k != "records"},
                         ensure_ascii=False))
    return code


if __name__ == "__main__":
    sys.exit(main())
