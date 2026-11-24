#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DeepSeek「共用 key 变慢」诊断基准脚本

测什么：
  1. TTFT（首 token 时间）—— 排队 / 服务端拥堵的直接指标
  2. 输出吞吐 tok/s —— (总时长 - TTFT) / 输出 token 数
  3. 429 / 503 计数 —— 账号级并发超限 / 服务端过载的证据
  4. 并发梯度 —— 1 / 4 / 16 并发下 TTFT 与 tok/s 怎么变
  5. 缓存命中 —— 同一 prompt 重复请求：第 1 次冷启动，之后命中缓存（更快）

用法：
  # 同一台机器、同一时段分别跑两遍，才具备可比性
  python deepseek-bench.py --key sk-xxx --label 团队key
  # 对照官方直连与自建中转
  python deepseek-bench.py --key <子key>   --base-url https://<中转域名>/v1 --label 经中转
  python deepseek-bench.py --key <官方key> --base-url https://api.deepseek.com   --label 官方直连
  # 冷启动对比（推荐 A/B 时加）、关闭思考（量 TTFT 更干净）
  python deepseek-bench.py --key sk-xxx --fresh --no-thinking --concurrency 1,4,16

脚本会自动适配 API 方言：默认发 max_completion_tokens + stream_options + thinking(可选)，
遇 400 报错逐项回退（max_completion_tokens→max_tokens、去 thinking、去 stream_options）。

依赖：pip install requests
"""

import argparse
import json
import statistics
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import requests

DEFAULT_BASE = "https://api.deepseek.com"

# 会随 400 报错自动回退的特性开关
_FEATURES = {"usage": True, "thinking": True, "mct": True}


def build_prompt(approx_tokens: int, fresh: bool = False) -> str:
    """构造约 approx_tokens token 的输入（英文约 4 字符/token）。
    fresh=True 时注入随机编号，保证缓存未命中——A/B 对比两个 key 时用。"""
    sentence = "The quick brown fox jumps over the lazy dog and keeps running. "
    n = max(1, approx_tokens // 11)
    prompt = sentence * n + "\n\n用一句话概括上面这段话。"
    if fresh:
        prompt = f"任务编号：{uuid.uuid4().hex[:12]}\n\n" + prompt
    return prompt


def _build_body(model, prompt, max_tokens, no_thinking):
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": True,
    }
    if _FEATURES["mct"]:
        body["max_completion_tokens"] = max_tokens
    else:
        body["max_tokens"] = max_tokens
    if no_thinking and _FEATURES["thinking"]:
        body["thinking"] = {"type": "disabled"}
        body["reasoning_effort"] = "low"
    if _FEATURES["usage"]:
        body["stream_options"] = {"include_usage": True}
    return body


def _post(base, key, model, prompt, max_tokens, no_thinking):
    return requests.post(
        base.rstrip("/") + "/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json=_build_body(model, prompt, max_tokens, no_thinking),
        stream=True,
        timeout=(15, 600),
    )


def _negotiate_400(text):
    """依据 400 报错回退当前接口不支持的参数，返回是否发生了回退。"""
    t = text.lower()
    if _FEATURES["usage"] and ("stream_options" in t or "include_usage" in t):
        _FEATURES["usage"] = False
        return True
    if _FEATURES["thinking"] and ("thinking" in t or "reasoning_effort" in t):
        _FEATURES["thinking"] = False
        return True
    if _FEATURES["mct"] and "max_completion_tokens" in t:
        _FEATURES["mct"] = False
        return True
    return False


def one_request(base, key, model, prompt, max_tokens, tag, no_thinking=False):
    t0 = time.perf_counter()
    res = {
        "tag": tag, "status": None, "ttft": None, "total": None, "tok_s": None,
        "out_tokens": None, "cache_hit_tokens": None, "cache_miss_tokens": None,
        "first_keepalive": None, "error": None,
    }
    try:
        r = _post(base, key, model, prompt, max_tokens, no_thinking)
        for _ in range(3):  # 400 时逐项回退参数并重试
            if r.status_code != 400:
                break
            text = r.text
            r.close()
            if not _negotiate_400(text):
                break
            r = _post(base, key, model, prompt, max_tokens, no_thinking)
        res["status"] = r.status_code
        if r.status_code != 200:
            res["error"] = r.text[:300]
            res["total"] = time.perf_counter() - t0
            r.close()
            return res

        out_tokens = 0
        usage = None
        for line in r.iter_lines(decode_unicode=True):
            if not line:
                continue
            if line.startswith(":"):  # SSE keep-alive：已连接、在排队等推理开始
                if res["first_keepalive"] is None:
                    res["first_keepalive"] = time.perf_counter() - t0
                continue
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            try:
                obj = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if obj.get("usage"):
                usage = obj["usage"]
            for ch in obj.get("choices") or []:
                if (ch.get("delta") or {}).get("content"):
                    if res["ttft"] is None:
                        res["ttft"] = time.perf_counter() - t0
                    out_tokens += 1  # 兜底计数（约等于 token 数）
        r.close()
        total = time.perf_counter() - t0
        res["total"] = total
        if usage:
            out_tokens = usage.get("completion_tokens") or usage.get("output_tokens") or out_tokens
            res["cache_hit_tokens"] = usage.get("prompt_cache_hit_tokens") or usage.get("cacheReadTokens")
            res["cache_miss_tokens"] = usage.get("prompt_cache_miss_tokens")
        res["out_tokens"] = out_tokens
        if res["ttft"] is not None and total > res["ttft"] and out_tokens:
            res["tok_s"] = out_tokens / (total - res["ttft"])
    except Exception as e:  # noqa: BLE001
        res["error"] = f"{type(e).__name__}: {e}"
        res["total"] = time.perf_counter() - t0
    return res


def fmt(x, nd=2):
    return "-" if x is None else f"{x:.{nd}f}"


def summarize(rows):
    ok = [r for r in rows if r["status"] == 200]
    ttfts = [r["ttft"] for r in ok if r["ttft"] is not None]
    toks = [r["tok_s"] for r in ok if r["tok_s"] is not None]
    totals = [r["total"] for r in ok if r["total"] is not None]
    codes = {}
    for r in rows:
        k = r["status"] if r["status"] else "网络错误"
        codes[k] = codes.get(k, 0) + 1
    return {
        "n": len(rows), "n_ok": len(ok),
        "codes": codes,
        "ttft_p50": statistics.median(ttfts) if ttfts else None,
        "tok_s_p50": statistics.median(toks) if toks else None,
        "total_p50": statistics.median(totals) if totals else None,
        "cache_hit": sum(r["cache_hit_tokens"] or 0 for r in ok) or None,
        "cache_miss": sum(r["cache_miss_tokens"] or 0 for r in ok) or None,
    }


def main():
    ap = argparse.ArgumentParser(description="DeepSeek 共用 key 慢速诊断")
    ap.add_argument("--key", required=True, help="API key（不会打印）")
    ap.add_argument("--base-url", default=DEFAULT_BASE)
    ap.add_argument("--model", default="deepseek-flash")
    ap.add_argument("--label", default="未命名")
    ap.add_argument("--concurrency", default="1,4,16", help="逗号分隔，如 1,4,16")
    ap.add_argument("--repeats", type=int, default=3, help="每个并发档重复几批")
    ap.add_argument("--prompt-tokens", type=int, default=2000, help="输入规模（约多少 token）")
    ap.add_argument("--prompt-text", default=None, help="自定义提示词（替代内置构造）")
    ap.add_argument("--max-tokens", type=int, default=256)
    ap.add_argument("--fresh", action="store_true",
                    help="注入随机编号、强制缓存未命中（A/B 对比两个 key 时必须加）")
    ap.add_argument("--no-thinking", action="store_true",
                    help="关闭思考（若接口支持），TTFT 更干净、便于对比")
    ap.add_argument("--json-out", default=None, help="原始结果落盘路径")
    args = ap.parse_args()

    levels = [int(x) for x in args.concurrency.split(",") if x.strip()]
    if args.prompt_text:
        prompt = args.prompt_text
        if args.fresh:
            prompt = f"任务编号：{uuid.uuid4().hex[:12]}\n\n" + prompt
    else:
        prompt = build_prompt(args.prompt_tokens, args.fresh)
    print(f"[{args.label}] base={args.base_url} model={args.model} "
          f"prompt≈{args.prompt_tokens}tok max_tokens={args.max_tokens} "
          f"concurrency={levels} repeats={args.repeats} fresh={args.fresh} no_thinking={args.no_thinking}")
    if args.fresh:
        print(f"[{args.label}] 已开启冷启动模式：每次运行都是缓存未命中，两个 key 的对比才公平")
    else:
        print(f"[{args.label}] 提示：同一 prompt 会被复用，第 1 批是冷启动，之后是缓存命中")

    all_rows = []
    for c in levels:
        for rep in range(args.repeats):
            batch_tag = f"c{c}-r{rep + 1}"
            with ThreadPoolExecutor(max_workers=c) as ex:
                futs = [ex.submit(one_request, args.base_url, args.key, args.model,
                                  prompt, args.max_tokens, f"{batch_tag}#{i + 1}", args.no_thinking)
                        for i in range(c)]
                rows = [f.result() for f in futs]
            all_rows.extend(rows)
            s = summarize(rows)
            line = (f"[{args.label}] {batch_tag:8s} 并发{c:>3d}  "
                    f"成功{s['n_ok']}/{s['n']}  状态{s['codes']}  "
                    f"TTFT_p50={fmt(s['ttft_p50'])}s  tok/s_p50={fmt(s['tok_s_p50'])}  "
                    f"总时长_p50={fmt(s['total_p50'])}s")
            print(line)
            if args.repeats == 1 or c == 1:
                for r in rows:
                    if r["error"]:
                        print(f"           ! {r['tag']}: {r['error']}")

    print(f"\n===== [{args.label}] 汇总 =====")
    for c in levels:
        rows = [r for r in all_rows if r["tag"].startswith(f"c{c}-")]
        s = summarize(rows)
        print(f"并发 {c:>3d}: 成功 {s['n_ok']}/{s['n']}  状态码 {s['codes']}  "
              f"TTFT_p50 {fmt(s['ttft_p50'])}s  tok/s_p50 {fmt(s['tok_s_p50'])}  "
              f"缓存命中输入 {s['cache_hit'] or 0} tok / 未命中 {s['cache_miss'] or 0} tok")

    print("\n怎么读结果：")
    print("  1) TTFT 很高（比如 >10s）但 tok/s 正常 → 请求在排队：高峰拥堵或撞了并发上限")
    print("  2) tok/s 很低 → 服务端繁忙 / 输出太长 / 在用长思考模型")
    print("  3) 出现 429 → 账号级并发超限（与用哪个 key 无关，需要扩容+user_id 隔离）")
    print("  4) 出现 503 → 服务端过载，官方建议稍后重试；错峰（北京时间 12-14 点、18 点后、周末）明显更快")
    print("  5) 冷启动慢、重复请求快 → 缓存命中差异，固定前缀能显著提速")
    print("  对比两个 key 时：必须同一台机器、同一时段、同一参数各跑一遍。")

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump({"label": args.label, "model": args.model,
                       "prompt_tokens": args.prompt_tokens, "rows": all_rows},
                      f, ensure_ascii=False, indent=2)
        print(f"\n原始结果已写入 {args.json_out}")


if __name__ == "__main__":
    sys.exit(main())
