#!/usr/bin/env python3
"""判定轮真机自检:用一张真值已知的图,验证「端点收图 -> 严格解析 -> 回填」整条链。

**为什么需要它,而不是只跑 `judge.py --selftest`**:selftest 用的是本地桩端点,
它证明协议、严格解析、回填都对,但证明不了「真实端点收不收图、答得对不对」。
skill 换机器之后第一件该跑的就是这个 —— 2026-09-20 第一次真机跑之前,
判定轮从未接触过真实视觉端点(见 HANDOFF 第 1 节)。

图是现场生成的(纯标准库画 PNG,不依赖字体与第三方库):
    左半纯红 (220,30,30) / 右半纯蓝 (30,30,220) / 无任何文字
真值由此明确,四条判定分别是:
    左半是红的 -> 期望 yes (应 pass)
    右半是蓝的 -> 期望 yes (应 pass)
    图中有文字 -> 期望 no  (应 pass)
    图中是绿的 -> 期望 yes (真值是否,应 **fail**)
最后一条故意给「期望 yes」:只会说 yes 的判定轮等于没有判定,必须看到它敢判 fail。

端点与模型从环境变量取,不写进仓库(内网地址换机器就不通):
    TESTFLOW_JUDGE_ENDPOINT   http://host:port/v1
    TESTFLOW_JUDGE_MODEL      deepseek-flash
    TESTFLOW_JUDGE_API_KEY    密钥(Windows: setx 后重开终端)

退出码:0 = 四条判定全部与真值一致;1 = 有判定与真值不符(端点或判定能力问题);
        2 = 用法错误;3 = 端点未配置(**不要读成"没问题"**)。

纯标准库,零依赖。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from coordmap import encode_png_8bit                      # noqa: E402
from judge import JudgeOptions, chat_completions_url, run_judgment   # noqa: E402

RED = (220, 30, 30)
BLUE = (30, 30, 220)
WHITE = (255, 255, 255)
W, H = 640, 360

# (判定项, 问题, region, 期望答案, 该判定项应得的结论)
CHECKS = [
    ("E1", "图片左半部分是否可见一个红色圆形?", [0.0, 0.0, 0.5, 1.0], "yes", "pass"),
    ("E2", "图片右半部分是否可见一个蓝色圆形?", [0.5, 0.0, 1.0, 1.0], "yes", "pass"),
    ("E3", "图片中是否出现任何文字?", [0.0, 0.0, 1.0, 1.0], "no", "pass"),
    ("E4", "图片中是否可见绿色图形?", [0.0, 0.0, 1.0, 1.0], "yes", "fail"),
]


def draw_fixture(path: Path) -> None:
    """白底 + 左红圆 + 右蓝圆,没有文字 —— 真值靠构造保证,不靠人看。

    **不要改成整幅纯色块**(2026-09-20 实测):整幅纯色的图对视觉模型是分布外输入,
    实测把一整块纯蓝的裁剪图答成「整个画面为空白白色」——链路明明是对的
    (判定日志里 sentSha256 与本地裁出来的字节一致),是模型在瞎答。
    白底 + 图形更接近截图的样子,模型表现与真实截图一致。
    """
    cx1, cx2, cy, radius = W // 4, W * 3 // 4, H // 2, min(W, H) // 5
    px = bytearray()
    for y in range(H):
        for x in range(W):
            inside = ((x - cx1) ** 2 + (y - cy) ** 2 <= radius ** 2
                      or (x - cx2) ** 2 + (y - cy) ** 2 <= radius ** 2)
            if not inside:
                px += bytes(WHITE)
            elif x < W // 2:
                px += bytes(RED)
            else:
                px += bytes(BLUE)
    path.write_bytes(encode_png_8bit(W, H, 3, bytes(px)))


def build_run(run_path: Path, shot: Path) -> None:
    checks = [{
        "source": cid, "after": "S1", "kind": "assert", "mode": "vision-closed",
        "expect": {"question": q, "region": region, "answer": want},
        "verdict": "pending_judgment", "evidence": [str(shot)],
    } for cid, q, region, want, _ in CHECKS]
    run = {"runId": "run-judge-live", "cases": [{
        "id": "LIVE", "exploreMode": False, "checks": checks,
        "evidenceIndex": [{"step": "S1", "path": str(shot)}]}],
        "judgeRequests": []}
    run_path.write_text(json.dumps(run, ensure_ascii=False, indent=1), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="判定轮真机自检(真值已知的合成图)")
    ap.add_argument("--endpoint", default=os.environ.get("TESTFLOW_JUDGE_ENDPOINT", ""),
                    help="OpenAI 兼容端点;默认取 TESTFLOW_JUDGE_ENDPOINT")
    ap.add_argument("--model", default=os.environ.get("TESTFLOW_JUDGE_MODEL", ""),
                    help="模型名;默认取 TESTFLOW_JUDGE_MODEL")
    ap.add_argument("--api-key", default=None, help="默认取 TESTFLOW_JUDGE_API_KEY")
    ap.add_argument("--timeout", type=float, default=60.0)
    ap.add_argument("--workdir", default="", help="留证据的目录;默认用临时目录,跑完删")
    ap.add_argument("--keep", action="store_true", help="保留临时目录(排查用)")
    args = ap.parse_args()

    if not args.endpoint or not args.model:
        print("端点未配置 —— 无法验证判定轮(这不是『没问题』)。")
        print("设好 TESTFLOW_JUDGE_ENDPOINT 与 TESTFLOW_JUDGE_MODEL 再跑;"
              "密钥走 TESTFLOW_JUDGE_API_KEY。")
        return 3

    key = args.api_key if args.api_key is not None else os.environ.get("TESTFLOW_JUDGE_API_KEY", "")
    tmp = Path(args.workdir) if args.workdir else Path(tempfile.mkdtemp(prefix="judge-live-"))
    tmp.mkdir(parents=True, exist_ok=True)
    shot = tmp / "LIVE_S1_01.png"
    run_path = tmp / "run-judge-live.json"
    draw_fixture(shot)
    build_run(run_path, shot)

    print(f"== 判定轮真机自检: {chat_completions_url(args.endpoint)} (model={args.model})")
    print(f"   合成图: 左半红 {RED} / 右半蓝 {BLUE} / 无文字 ({W}x{H});证据目录 {tmp}")
    code, summary = run_judgment(JudgeOptions(
        run_path=run_path, endpoint=args.endpoint, model=args.model, api_key=key,
        timeout=args.timeout, quiet=not args.keep))

    got = {rec["source"]: rec for rec in summary["records"]}
    fails = 0
    print()
    for cid, question, _region, want, want_verdict in CHECKS:
        rec = got.get(cid)
        actual = rec["verdict"] if rec else "(未判定)"
        ok = actual == want_verdict
        fails += 0 if ok else 1
        print(f"  {'ok  ' if ok else 'FAIL'}  {cid}  期望答案 {want} -> 期望结论 {want_verdict},"
              f"实得 {actual}")
        if rec:
            print(f"        问题: {question}")
            print(f"        模型原话: {(rec.get('rawResponse') or rec.get('reason') or '')[:120]!r}")
            images = (rec.get("evidence") or {}).get("images") or []
            if images:
                print(f"        送出的图: {images[0].get('sentBytes')} 字节"
                      f"(裁剪={images[0].get('cropped')}, 原图哈希 {str(images[0].get('sha256'))[:12]}…)")

    print()
    if fails:
        print(f"判定轮真机自检: {fails}/{len(CHECKS)} 条与真值不符 —— 端点或判定能力有问题,"
              f"不要采信这一轮的结论。")
        return 1
    print(f"判定轮真机自检通过: {len(CHECKS)} 条判定全部与真值一致"
          f"(含一条必须判 fail 的对照)。")
    if not args.keep and not args.workdir:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
