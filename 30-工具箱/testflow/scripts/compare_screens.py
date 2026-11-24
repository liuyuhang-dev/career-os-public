#!/usr/bin/env python3
"""截图比对(跨轮/跨机型的确定性视觉回归)。

**为什么需要它**:视觉判定轮(`judge.py`)只回答是/否封闭问题,判不了
"这一版的界面跟上一版比,哪里变了"。而这件事**不需要模型**:同一用例同一步骤的
截图按文件名就能配成对,像素差异是可算的。

    基线目录(上一版/上一轮)  ×  当前目录(这一版)  →  差异清单 + 差异图

配对的键是**文件名主干**(`<用例ID>_<步骤ID>_<名称>.png`),不含时间戳,
所以跨轮稳定。基线用 `--save` 从当前这轮建:

```bash
# 1) 把这一轮的截图存成基线
python3 "$SKILL_DIR/scripts/compare_screens.py" --save baselines/android-mid/shots --cur <run>.json
# 2) 下一轮与基线比
python3 "$SKILL_DIR/scripts/compare_screens.py" --base baselines/android-mid/shots \
  --cur test-artifacts/<f>/04-runs/run-<ts>.json --diff-dir test-artifacts/<f>/04-runs/diffs
```

**差异是候选,不是缺陷。** 倒计时、特效、随机数、服务器时间本来就会让画面变;
所以报告给出**变化比例 + 归一化变化区域 + 差异图**,由人一眼判断"这是不是该变的"。
工具不开 bug 单,也不替判定轮下结论。

退出码:`0` = 没有超阈值的差异;`1` = 有;`2` = 用法错误;`3` = 无法比对(缺配对/读不了),
**3 不是"没差异"**。

纯标准库,零依赖。
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from coordmap import PngUnsupported, encode_png_8bit, read_png  # noqa: E402

SKILL_DIR = Path(__file__).resolve().parent.parent
DIM = (70, 70, 70)          # 差异图里未变化部分的底色
HOT = (255, 60, 60)         # 变化区块


# ---------------------------------------------------------------- 取图

def collect_shots(src: Path) -> tuple[dict[str, Path], list[str]]:
    """从一个目录或 run json 里收集截图,按文件名主干索引。"""
    shots: dict[str, Path] = {}
    problems: list[str] = []
    if src.suffix.lower() == ".json":
        try:
            data = json.loads(src.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            return {}, [f"{src} 解析失败: {exc}"]
        paths: list[str] = []
        for case in data.get("cases") or []:
            for ent in case.get("evidenceIndex") or []:
                if ent.get("path"):
                    paths.append(str(ent["path"]))
            for chk in case.get("checks") or []:
                paths += [str(p) for p in (chk.get("evidence") or [])]
        for p in paths:
            f = Path(p)
            if f.suffix.lower() == ".png":
                shots.setdefault(f.stem, f)
        return shots, problems
    if not src.exists():
        return {}, [f"找不到: {src}"]
    for f in sorted(src.rglob("*.png")):
        shots.setdefault(f.stem, f)
    return shots, problems


def diff_frames(a: Path, b: Path, tol: int, step: int) -> dict:
    """比对两张图。返回差异统计(比例/归一化区域/6×6 网格/尺寸变化)。

    抽样步长 step 默认 2:判定轮要的是"哪里变了",不是逐像素统计精度。
    """
    wa, ha, ca, pa = read_png(a)
    wb, hb, cb, pb = read_png(b)
    if (wa, ha) != (wb, hb):
        return {"dimsChanged": True, "baseSize": [wa, ha], "curSize": [wb, hb]}

    grid = [[0, 0, 0, 0, 0, 0] for _ in range(6)]
    total = changed = 0
    x0 = y0 = 10 ** 9
    x1 = y1 = -1
    for y in range(0, ha, step):
        ia = y * wa * ca
        ib = y * wb * cb
        for x in range(0, wa, step):
            ja = ia + x * ca
            jb = ib + x * cb
            dra = abs(pa[ja] - pb[jb])
            dga = abs(pa[ja + (1 if ca >= 3 else 0)] - pb[jb + (1 if cb >= 3 else 0)])
            dba = abs(pa[ja + (2 if ca >= 3 else 0)] - pb[jb + (2 if cb >= 3 else 0)])
            total += 1
            if dra > tol or dga > tol or dba > tol:
                changed += 1
                x0, y0 = min(x0, x), min(y0, y)
                x1, y1 = max(x1, x), max(y1, y)
                grid[min(5, y * 6 // ha)][min(5, x * 6 // wa)] += 1
    if not changed:
        return {"dimsChanged": False, "changed": 0, "total": total,
                "ratio": 0.0, "region": None, "grid": grid}

    cx0 = min(5, x0 * 6 // wa)
    cx1 = min(5, x1 * 6 // wa)
    cy0 = min(5, y0 * 6 // ha)
    cy1 = min(5, y1 * 6 // ha)
    for gy in range(6):
        for gx in range(6):
            n = grid[gy][gx]
            grid[gy][gx] = round(n / max(1, (ha * wa) // (36 * step * step)), 4)
    return {
        "dimsChanged": False, "changed": changed, "total": total,
        "ratio": round(changed / max(1, total), 4),
        "region": [round(cx0 / 6, 3), round(cy0 / 6, 3),
                   round((cx1 + 1) / 6, 3), round((cy1 + 1) / 6, 3)],
        "regionPx": [x0, y0, x1, y1],
        "grid": grid,
        "size": [wa, ha],
    }


def write_diff_image(a: Path, b: Path, out: Path, tol: int, block: int = 4) -> bool:
    """把差异画成图:未变化处压暗,变化区块标红。给人看的,不是判定。

    按 block×block 分块判定(块内抽一个像素),整块涂红或涂暗 ——
    目的是"一眼看出哪里变了",不是精确掩膜。
    """
    wa, ha, ca, pa = read_png(a)
    wb, hb, cb, pb = read_png(b)
    if (wa, ha) != (wb, hb):
        return False
    out_px = bytearray(wa * ha * 3)
    for by in range(0, ha, block):
        for bx in range(0, wa, block):
            ja = (by * wa + bx) * ca
            jb = (by * wb + bx) * cb
            d = max(abs(pa[ja] - pb[jb]),
                    abs(pa[ja + (1 if ca >= 3 else 0)] - pb[jb + (1 if cb >= 3 else 0)]),
                    abs(pa[ja + (2 if ca >= 3 else 0)] - pb[jb + (2 if cb >= 3 else 0)]))
            color = HOT if d > tol else DIM
            for y in range(by, min(by + block, ha)):
                base = (y * wa + min(bx, wa - 1)) * 3
                row = bytes(color) * min(block, wa - bx)
                out_px[base:base + len(row)] = row
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(encode_png_8bit(wa, ha, 3, bytes(out_px)))
    return True


# ---------------------------------------------------------------- 报告

def render_report(base: Path | str, cur: Path | str, results: list[dict],
                  only_base: list[str], only_cur: list[str],
                  threshold: float, out: Path | None) -> str:
    now = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    hot = [r for r in results if r.get("status") == "changed"]
    dims = [r for r in results if r.get("status") == "dims_changed"]
    bad = [r for r in results if r.get("status") in ("read_error",)]
    same = [r for r in results if r.get("status") == "identical"]
    lines = [
        "# 截图比对(确定性视觉回归)",
        "",
        f"- 生成时间: {now}",
        f"- 基线: `{base}`",
        f"- 当前: `{cur}`",
        f"- 配对 {len(results)} 对: 一致 {len(same)}、**有差异 {len(hot)}**、"
        f"尺寸变化 {len(dims)}、读取失败 {len(bad)}",
        f"- 阈值: 单通道差 > {threshold} 记为变化点",
        "",
        "> **差异是候选,不是缺陷。** 倒计时、特效、随机数、服务器时间都会让画面变 ——",
        "> 所以每条都给出变化比例、归一化变化区域与 6×6 分布(左上→右下)。",
        "> 由人判断「这是不是该变的」;要开单,请用判定轮或状态上报,别拿比对结果直接开。",
        "",
    ]
    if hot:
        lines.append("## 有差异")
        lines.append("")
        for r in hot:
            lines.append(f"- `{r['stem']}` — 变化 {r['ratio'] * 100:.2f}%"
                         f"({r['changed']}/{r['total']} 采样点),"
                         f"区域(归一化)`{r['region']}`")
            if r.get("diffImage"):
                lines.append(f"  - 差异图: `{r['diffImage']}`")
            lines.append(f"  - 6×6 分布: {r['grid']}")
        lines.append("")
    if dims:
        lines.append("## 尺寸变了(基线与当前分辨率不一致)")
        lines.append("")
        for r in dims:
            lines.append(f"- `{r['stem']}` — 基线 {r['baseSize']} → 当前 {r['curSize']}"
                         f"(机型/分辨率或投屏窗口变了,先确认该不该比)")
        lines.append("")
    if bad:
        lines.append("## 读取失败")
        lines.append("")
        for r in bad:
            lines.append(f"- `{r['stem']}` — {r.get('detail')}")
        lines.append("")
    if same:
        lines.append("## 一致(逐采样点相同)")
        lines.append("")
        lines.append(f"- {len(same)} 对:" + ", ".join(f"`{r['stem']}`" for r in same[:20])
                     + (" …" if len(same) > 20 else ""))
        lines.append("")
    lines.append("## 无法比对(不是「没差异」)")
    lines.append("")
    if not only_base and not only_cur:
        lines.append("- 无")
    else:
        if only_base:
            lines.append(f"- 基线里有、当前这轮没采到({len(only_base)}):"
                         + ", ".join(f"`{s}`" for s in only_base[:12])
                         + (" …" if len(only_base) > 12 else ""))
        if only_cur:
            lines.append(f"- 当前这轮新增的截图(基线里没有,无从比,{len(only_cur)}):"
                         + ", ".join(f"`{s}`" for s in only_cur[:12])
                         + (" …" if len(only_cur) > 12 else ""))
    lines.append("")
    if out:
        lines.append(f"_报告: `{out}`_")
    return "\n".join(lines)


def exit_code(results: list[dict], only_base: list[str], only_cur: list[str]) -> int:
    if any(r.get("status") == "changed" for r in results):
        return 1
    if any(r.get("status") in ("read_error", "dims_changed") for r in results) \
            or only_base:
        return 3
    return 0


# ---------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser(
        description="截图比对:跨轮/跨机型找界面差异(确定性;差异是候选,不开单)")
    ap.add_argument("--base", help="基线:目录或 run json")
    ap.add_argument("--cur", help="当前:目录或 run json")
    ap.add_argument("--save", type=Path,
                    help="把 --cur 的截图存成基线(首次建基线用这个)")
    ap.add_argument("--out", type=Path, help="报告 markdown 路径")
    ap.add_argument("--diff-dir", type=Path,
                    help="差异图输出目录(只给有差异的图写)")
    ap.add_argument("--tolerance", type=int, default=12,
                    help="单通道差值超过多少算变化点(默认 12,抗压缩噪声)")
    ap.add_argument("--step", type=int, default=2,
                    help="抽样步长(默认 2;越小越准也越慢)")
    ap.add_argument("--threshold", type=float, default=0.005,
                    help="变化比例超过多少算「有差异」(默认 0.5%%)")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    if args.save:
        if not args.cur:
            print("错误: --save 需要 --cur(先给当前这轮)", file=sys.stderr)
            return 2
        shots, problems = collect_shots(Path(args.cur))
        if problems or not shots:
            print(f"错误: 没有可存的截图 {problems}", file=sys.stderr)
            return 2
        args.save.mkdir(parents=True, exist_ok=True)
        n = 0
        for stem, p in sorted(shots.items()):
            shutil.copy(p, args.save / f"{stem}.png")
            n += 1
        print(f"基线已建立: {args.save}({n} 张)")
        print("提醒: 基线要按机型存档,换机型或改分辨率要重建。")
        return 0

    if not args.base or not args.cur:
        print("错误: 需要 --base 与 --cur(或用 --save / --selftest)", file=sys.stderr)
        return 2

    base_shots, pb = collect_shots(Path(args.base))
    cur_shots, pc = collect_shots(Path(args.cur))
    if pb or pc:
        for m in pb + pc:
            print(f"错误: {m}", file=sys.stderr)
        return 2

    results: list[dict] = []
    for stem in sorted(set(base_shots) & set(cur_shots)):
        a, b = base_shots[stem], cur_shots[stem]
        try:
            d = diff_frames(a, b, args.tolerance, args.step)
        except (PngUnsupported, OSError) as exc:
            results.append({"stem": stem, "status": "read_error",
                            "detail": f"{type(exc).__name__}: {exc}"})
            continue
        if d["dimsChanged"]:
            results.append({"stem": stem, "status": "dims_changed", **d})
            continue
        item = {"stem": stem, **d}
        item["status"] = "changed" if d["ratio"] > args.threshold else "identical"
        if item["status"] == "changed" and args.diff_dir:
            out_img = args.diff_dir / f"{stem}.diff.png"
            if write_diff_image(a, b, out_img, args.tolerance):
                item["diffImage"] = str(out_img)
        results.append(item)

    only_base = sorted(set(base_shots) - set(cur_shots))
    only_cur = sorted(set(cur_shots) - set(base_shots))
    report = render_report(args.base, args.cur, results, only_base, only_cur,
                           args.tolerance, args.out)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(report, encoding="utf-8")

    changed = sum(1 for r in results if r["status"] == "changed")
    code = exit_code(results, only_base, only_cur)
    if not args.quiet:
        print(f"截图比对: 配对 {len(results)} 对;一致 "
              f"{sum(1 for r in results if r['status'] == 'identical')}、"
              f"有差异 {changed}、尺寸变化 "
              f"{sum(1 for r in results if r['status'] == 'dims_changed')}")
        for r in results:
            if r["status"] == "changed":
                print(f"  [{r['ratio'] * 100:6.2f}%] {r['stem']} 区域 {r['region']}"
                      + (f" 差异图 {r['diffImage']}" if r.get("diffImage") else ""))
        if only_base:
            print(f"  基线有、当前缺 {len(only_base)} 张 —— 这些**没法比**,不是没差异")
        if args.out:
            print(f"报告已写出: {args.out}")
        print("提醒: 差异是候选(倒计时/特效/随机本来就会变),由人判断该不该变。")
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

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        base_dir, cur_dir = tmp / "base", tmp / "cur"
        base_dir.mkdir(), cur_dir.mkdir()

        def frame(w: int, h: int, bg: tuple[int, int, int]) -> bytes:
            return encode_png_8bit(w, h, 3, bytes(bg) * (w * h))

        def with_block(w: int, h: int, bg, color, bx, by, bw, bh) -> bytes:
            px = bytearray(bytes(bg) * (w * h))
            for y in range(by, min(by + bh, h)):
                for x in range(bx, min(bx + bw, w)):
                    i = (y * w + x) * 3
                    px[i:i + 3] = bytes(color)
            return encode_png_8bit(w, h, 3, bytes(px))

        (base_dir / "C1_S1_a.png").write_bytes(frame(60, 60, (30, 30, 30)))
        (cur_dir / "C1_S1_a.png").write_bytes(frame(60, 60, (30, 30, 30)))       # 相同
        (base_dir / "C1_S2_b.png").write_bytes(frame(60, 60, (30, 30, 30)))
        (cur_dir / "C1_S2_b.png").write_bytes(
            with_block(60, 60, (30, 30, 30), (255, 0, 0), 10, 40, 20, 10))       # 右下变了
        (base_dir / "C1_S3_c.png").write_bytes(frame(40, 40, (10, 10, 10)))
        (cur_dir / "C1_S3_c.png").write_bytes(frame(80, 40, (10, 10, 10)))       # 尺寸变了
        (base_dir / "C1_S4_d.png").write_bytes(frame(30, 30, (0, 0, 0)))         # 当前缺这张

        args = argparse.Namespace(
            base=str(base_dir), cur=str(cur_dir), out=tmp / "r.md",
            diff_dir=tmp / "diffs", tolerance=12, step=2, threshold=0.005,
            quiet=True)
        base_shots, _ = collect_shots(base_dir)
        cur_shots, _ = collect_shots(cur_dir)
        ck("按文件名主干配对(4 对交集 + 1 张只在基线)", len(base_shots) == 4
           and len(cur_shots) == 3, f"{len(base_shots)}/{len(cur_shots)}")

        results = []
        for stem in sorted(set(base_shots) & set(cur_shots)):
            d = diff_frames(base_shots[stem], cur_shots[stem], args.tolerance,
                            args.step)
            if d["dimsChanged"]:
                results.append({"stem": stem, "status": "dims_changed", **d})
            else:
                st = "changed" if d["ratio"] > args.threshold else "identical"
                item = {"stem": stem, **d, "status": st}
                if st == "changed":
                    img = tmp / "diffs" / f"{stem}.diff.png"
                    if write_diff_image(base_shots[stem], cur_shots[stem], img,
                                        args.tolerance):
                        item["diffImage"] = str(img)
                results.append(item)
        by = {r["stem"]: r for r in results}
        ck("完全一致 -> identical", by["C1_S1_a"]["status"] == "identical",
           str(by["C1_S1_a"]))
        ck("局部改色 -> changed 且比例合理",
           by["C1_S2_b"]["status"] == "changed"
           and 0.02 < by["C1_S2_b"]["ratio"] < 0.12, str(by["C1_S2_b"]["ratio"]))
        ck("变化区域落在右下一格(归一化)",
           by["C1_S2_b"]["region"][1] >= 0.66 and by["C1_S2_b"]["region"][0] >= 0.16,
           str(by["C1_S2_b"]["region"]))
        ck("尺寸变化单独报(不按差异比例算)",
           by["C1_S3_c"]["status"] == "dims_changed"
           and by["C1_S3_c"]["baseSize"] == [40, 40]
           and by["C1_S3_c"]["curSize"] == [80, 40], str(by["C1_S3_c"]))
        ck("差异图写出且可解码",
           by["C1_S2_b"].get("diffImage")
           and read_png(Path(by["C1_S2_b"]["diffImage"]))[0:2] == (60, 60),
           str(by["C1_S2_b"].get("diffImage")))
        only_base = sorted(set(base_shots) - set(cur_shots))
        ck("基线有当前缺 -> 计入无法比对的缺口",
           only_base == ["C1_S4_d"], str(only_base))
        ck("退出码: 有差异 -> 1",
           exit_code(results, only_base, []) == 1, str(exit_code(results, only_base, [])))
        ck("退出码: 无差异且无缺口 -> 0",
           exit_code([{"status": "identical"}], [], []) == 0)
        ck("退出码: 只有缺口 -> 3(不是「没差异」)",
           exit_code([{"status": "identical"}], ["x"], []) == 3
           and exit_code([{"status": "dims_changed"}], [], []) == 3)
        rep = render_report(base_dir, cur_dir, results, only_base, [], 12, None)
        ck("报告写明差异是候选、并给变化区域",
           "候选,不是缺陷" in rep and "区域" in rep and "C1_S4_d" in rep)
        noisy = tmp / "noisy.png"
        noisy.write_bytes(with_block(60, 60, (30, 30, 30), (42, 30, 30), 0, 0, 1, 1))
        d_noisy = diff_frames(base_dir / "C1_S1_a.png", noisy, tol=12, step=2)
        ck("抗噪: 单点微差(≤tolerance)不算变化", d_noisy["changed"] == 0
           and d_noisy["ratio"] == 0.0, str(d_noisy))

    print()
    if fails:
        print(f"自测失败 {len(fails)} 项:")
        for x in fails:
            print("  ✗", x)
        return 1
    print("自测通过(配对/一致/局部变化比例与区域/尺寸变化/差异图/缺配对算缺口/"
          "抗噪与退出码 0/1/2/3 语义)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
