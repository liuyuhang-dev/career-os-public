#!/usr/bin/env python3
"""用例骨架生成:自然语言用例 → 派生用例 JSON 的**起点**。

**它生成的是骨架,不是用例。** 步骤里的坐标、判定项的判定模式与断言值都必须人填;
`skeleton: true` 与 `review.state=draft` 会一直挂着,直到有人填完并复核。

```bash
python3 "$SKILL_DIR/scripts/scaffold_cases.py" --specs specs/ \\
  --out test-artifacts/<f>/03-cases/ --requirements test-artifacts/<f>/01-requirements.json
```

为什么还值得做:它保证**每条 `E-N` 都有一条判定项占位**,不会有人悄悄漏掉最难验证的那条
期望(这是 `validate_cases` 的期望覆盖率闸门想防的,但手工写文件时很容易漏);
同时把 ID / 来源 / 类型 / 优先级 / 判定模式 / req_refs 这些机械字段一次填好。

规矩:

- **已存在的用例文件绝不覆盖。** 人已经填过的内容比骨架值钱。
- **不编造断言。** 每个 `E-N` 生成一条占位判定项(`kind=judgment` + `TODO`),
  判定模式、region、期望值都由人定 —— 工具不替人挑判定模式。
- 输出仍是 `draft`,闸门会拦住它进入执行阶段。骨架可跑(plan-only),但**没结论**。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from validate_cases import (  # noqa: E402
    EXPECT_SOURCE_REFS,
    FAULT_KINDS,
    FAULT_REQUIRED_FIELDS,
    collect_specs,
)

SKILL_DIR = Path(__file__).resolve().parent.parent


def split_refs(raw: str) -> list[str]:
    """把自然语言里的上游引用拆成列表。

    源对按约定写成 `A × B`(见 references/10-upstreams.md),所以只认 `×` 作分隔 ——
    不能顺手按逗号/斜杠拆:源名里本身就可能带这些(`configs/item.csv`、`assets/icons/`)。
    需求引用(`R-3.1、R-3.2`)沿用顿号/逗号拆分。
    """
    text = str(raw or "").strip()
    if not text or text in ("—", "-"):
        return []
    parts = text.split("×") if "×" in text else re.split(r"[、,;；]", text)
    out = []
    for p in parts:
        p = p.strip(" 　")
        if p and p not in ("—", "-"):
            out.append(p)
    return out

DEFAULT_DEVICE = {"profile": "android-mid", "resolution": [1080, 2400],
                  "dpi": 480, "host": "wechat"}


def spec_items(spec: dict, prefix: str) -> list[tuple[str, str]]:
    """取 PRE / S / E 条目,按编号排序。"""
    out = []
    for key, text in (spec.get("items") or {}).items():
        if key.startswith(prefix) and not (prefix == "S" and key.startswith("PRE")):
            out.append((key, text))
    def num(k: str) -> int:
        digits = "".join(ch for ch in k if ch.isdigit())
        return int(digits) if digits else 0
    return sorted(out, key=lambda kv: num(kv[0]))


def build_case(spec: dict, case_id: str, device: dict) -> dict:
    meta = spec.get("meta") or {}
    pres = spec_items(spec, "PRE")
    steps = spec_items(spec, "S")
    expects = spec_items(spec, "E")
    extra_todo: list[str] = []

    pre_actions = []
    for i, (key, text) in enumerate(pres, 1):
        pid = f"PRE-{i}"
        pre_actions.append({
            "id": pid, "action": "wait", "ms": 1000,
            "note": f"TODO: 用真实前置动作替换(出处 {key}: {text})",
        })
    step_actions = []
    for i, (key, text) in enumerate(steps, 1):
        sid = f"S{i}"
        step_actions.append({
            "id": sid, "action": "wait", "ms": 1000,
            "note": f"TODO: 用真实操作替换(出处 {key}: {text});"
                    f"需要证据就加 screenshot / logcat 动作",
        })
    if not step_actions:
        step_actions = [{"id": "S1", "action": "wait", "ms": 1000,
                         "note": "TODO: 用例没有步骤,补真实操作"}]

    # 故障注入也是机械字段:规格里「## 故障注入要求」写了 kind/after_step/window_ms,
    # 就该原样变成一条 fault 动作。不抄的话,幂等类用例只剩"连续点击 N 次"这种
    # 伪覆盖(校验器会以 PSEUDO_DUPLICATE_COVERAGE 告警)——而这类用例(P0 支付/入账)
    # 恰恰是最不能靠假装覆盖的。
    fault = (spec.get("section_kv") or {}).get("故障注入要求") or {}
    fault_action = None
    if fault.get("kind"):
        note = ""
        need = FAULT_REQUIRED_FIELDS.get(fault["kind"])
        if need is None:
            note = (f"TODO: 规格里的故障注入 kind={fault['kind']!r} 不在词汇表 "
                    f"{sorted(FAULT_KINDS)} 里,先确认再补 fault 动作")
        elif any(not str(fault.get(f, "")).strip() for f in need):
            note = (f"TODO: 规格里的故障注入缺必填字段 "
                    f"{[f for f in need if not str(fault.get(f, '')).strip()]}"
                    f"(kind={fault['kind']}),补齐后补一条 fault 动作")
        elif "target" in need:
            # target 是锚点对象(tap 的目标),规格里那行是给人看的一串描述,机械抄不了。
            # 只挡这一个字段名 —— target_step 是个字符串,机械抄得了。
            note = (f"TODO: 按规格补 fault 动作(kind={fault['kind']},"
                    f"需要 target 锚点): {fault}")
        else:
            fault_action = {"id": "F1", "action": "fault", "kind": fault["kind"]}
            for f in need:
                v = str(fault.get(f, "")).strip()
                fault_action[f] = int(v) if v.isdigit() else v
            fault_action["note"] = (f"TODO: 确认注入点在真实步骤里成立"
                                    f"(出处 故障注入要求: {fault})")
        if note:
            extra_todo.append(note)

    if fault_action is not None:
        at = fault_action.get("after_step") or fault_action.get("around_step")
        if at and any(a["id"] == at for a in step_actions):
            step_actions.insert([a["id"] for a in step_actions].index(at) + 1,
                                fault_action)
        else:
            step_actions.append(fault_action)

    after_default = step_actions[-1]["id"]
    checks = []
    for i, (key, text) in enumerate(expects, 1):
        checks.append({
            "source": f"E{i}",
            "after": after_default,
            "kind": "judgment",
            "question": f"TODO: 把这条期望改写成是/否封闭问题(出处 {key}: {text})",
            "_todo": "选定判定模式:vision-closed(要 region+answer)/ "
                     "state-report(要 path+op+value);确定了就把 kind 改成 assert",
        })

    # 上游引用是机械字段,和 ID/类型一样照抄 —— 别让派生物缺一个文档里本来就写了的字段,
    # 那样人会卡在闸门上,然后手工补一遍本该自动填的东西。
    source = str(meta.get("来源") or "requirement")
    refs = split_refs(meta.get("关联需求") if source == "requirement"
                      else meta.get("源对"))
    ref_field = EXPECT_SOURCE_REFS.get(source)

    case = {
        "id": case_id,
        "spec": spec.get("path", "").replace("\\", "/"),
        "title": spec.get("title") or case_id,
        "type": meta.get("类型") or "device-android",
        "priority": meta.get("优先级") or "P2",
        "expect_source": source,
        "req_refs": refs if source == "requirement" else [],
        "judge": meta.get("判定模式") or "vision-closed",
        "review": {"state": "draft", "by": None, "at": None,
                   "note": "骨架:填完步骤与判定项后人工复核"},
        "device": dict(device),
        "preconditions": pre_actions,
        "steps": step_actions,
        "checks": checks,
        "evidence": [],
        "skeleton": True,
        "_todo": [
            "1) 把 preconditions/steps 换成真实动作(tap/swipe/wait_for/screenshot/logcat…)",
            "2) 每个判定项二选一:vision-closed(region+answer)或 state-report(path+op+value),"
            "并把 kind 改成 assert",
            "3) 按需要补 evidence: screenshot / logcat / perf / state_report",
            "4) 复核通过后把 review.state 改成 approved(否则执行阶段一律 blocked)",
            "5) 删掉 skeleton 与 _todo 字段",
        ] + extra_todo,
    }
    if ref_field and ref_field != "req_refs":
        # 来源要求的引用字段(consistency_refs / config_refs)。不填它闸门会拒,
        # 填了别的来源的又会撞 SPURIOUS_REFS —— 所以只在来源要求的那个字段上填。
        case[ref_field] = refs
    return case


def scaffold(specs_path: Path, out: Path, device: dict) -> tuple[list, list]:
    """生成骨架。返回 (新建, 跳过)。**已存在的文件绝不覆盖。**"""
    specs = collect_specs(specs_path)
    if not specs:
        raise SystemExit(f"错误: 在 {specs_path} 里没解析出任何用例")
    out.mkdir(parents=True, exist_ok=True)
    made, skipped = [], []
    for case_id, spec in sorted(specs.items()):
        target = out / f"{case_id}.json"
        if target.exists():
            skipped.append(case_id)
            continue
        case = build_case(spec, case_id, device)
        target.write_text(json.dumps(case, ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8")
        made.append((case_id, len(case["checks"])))
    return made, skipped


def main() -> int:
    ap = argparse.ArgumentParser(
        description="从自然语言用例生成派生用例骨架(每条 E 都有判定项占位;不覆盖已有文件)")
    ap.add_argument("--specs", type=Path, required=False,
                    help="自然语言用例:目录或单个文件")
    ap.add_argument("--out", type=Path, help="输出目录,通常是 03-cases/")
    ap.add_argument("--profile", default=DEFAULT_DEVICE["profile"],
                    help=f"默认机型档案(默认 {DEFAULT_DEVICE['profile']})")
    ap.add_argument("--host", default=DEFAULT_DEVICE["host"],
                    help=f"宿主(默认 {DEFAULT_DEVICE['host']}:wechat / douyin)")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    if not args.specs or not args.out:
        print("错误: 需要 --specs 与 --out(或用 --selftest)", file=sys.stderr)
        return 2
    if not args.specs.exists():
        print(f"错误: 找不到 {args.specs}", file=sys.stderr)
        return 2

    device = dict(DEFAULT_DEVICE, profile=args.profile, host=args.host)
    made, skipped = scaffold(args.specs, args.out, device)

    if not args.quiet:
        print(f"骨架生成: 新建 {len(made)} 个、跳过已存在 {len(skipped)} 个 → {args.out}")
        for cid, n in made:
            print(f"  + {cid}({n} 条判定项占位)")
        if skipped:
            print(f"  已存在(未覆盖):{', '.join(skipped)}")
        print()
        print("**这是骨架,不是用例。** 要做的事:")
        print("  1) preconditions/steps 换成真实动作(坐标、锚点、等待条件)")
        print("  2) 每条判定项选判定模式并填断言,kind 改成 assert")
        print("  3) 补 evidence(screenshot / logcat / perf / state_report)")
        print("  4) 复核后把 review.state 改成 approved —— 否则执行阶段一律 blocked")
        print("校验: python3 scripts/validate_cases.py --cases "
              f"{args.out} --specs {args.specs} ...")
    return 0


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

    specs_dir = SKILL_DIR / "examples" / "specs"
    specs = collect_specs(specs_dir)
    ck("能从 specs 解析出用例", len(specs) >= 5, str(list(specs)))

    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "03-cases"
        made, skipped0 = scaffold(specs_dir, out, dict(DEFAULT_DEVICE))
        ck("新建全部用例、无跳过", len(made) == len(specs) and not skipped0,
           f"{made} {skipped0}")
        files = sorted(p.name for p in out.glob("*.json"))
        ck("每个用例一个文件", len(files) == len(specs), f"{len(files)} vs {len(specs)}")

        case = json.loads((out / "REQ-SETTLE-C01.json").read_text(encoding="utf-8"))
        spec = specs["REQ-SETTLE-C01"]
        e_ids = {k for k in spec["items"] if k.startswith("E")}
        ck("每条 E 都有判定项占位(不会漏掉最难验的那条)",
           len(case["checks"]) == len(e_ids)
           and [c["source"] for c in case["checks"]] == sorted(
               (f"E{i}" for i in range(1, len(e_ids) + 1)),
               key=lambda s: int(s[1:])), str(case["checks"]))
        ck("机械字段照抄自然语言用例(类型/优先级/来源/判定模式)",
           case["type"] == spec["meta"]["类型"] and case["priority"] == "P1"
           and case["expect_source"] == "requirement" and case["judge"] == "state-report",
           str({k: case[k] for k in ("type", "priority", "expect_source", "judge")}))
        ck("req_refs 从「关联需求」抄来", case["req_refs"] == ["R-3.1"],
           str(case["req_refs"]))

        # 源对(consistency)也必须机械抄进 consistency_refs:否则派生出来的用例
        # 永远缺这个字段,卡在闸门上等人补一个文档里本来就写了的字段。
        cons_spec = {
            "title": "配置与运行时一致", "path": "x.md",
            "meta": {"ID": "CONS-CFG-C01", "来源": "consistency",
                     "源对": "商城配置表(价格 / 道具 id)× 运行时状态上报",
                     "类型": "device-android", "优先级": "P1", "判定模式": "state-report"},
            "items": {"E1": "标价与配置表一致", "S1": "进商城"},
            "sections": {}, "section_kv": {},
        }
        cons_case = build_case(cons_spec, "CONS-CFG-C01", dict(DEFAULT_DEVICE))
        ck("consistency 的「源对」→ consistency_refs(按 × 拆,不拆源名里的斜杠)",
           cons_case["consistency_refs"] == ["商城配置表(价格 / 道具 id)", "运行时状态上报"]
           and cons_case["req_refs"] == [], str(cons_case.get("consistency_refs")))
        ck("invariant 不带任何来源引用字段(不伪造上游)",
           "consistency_refs" not in build_case(
               {"title": "t", "path": "x.md",
                "meta": {"ID": "INV-X-C01", "来源": "invariant"},
                "items": {"E1": "e", "S1": "s"}, "sections": {}, "section_kv": {}},
               "INV-X-C01", dict(DEFAULT_DEVICE)),
           "invariant 不该出现 consistency_refs")

        # 故障注入:规格写了就抄成 fault 动作(幂等类用例靠它才不是伪覆盖)
        idem_spec = {
            "title": "支付响应丢失", "path": "x.md",
            "meta": {"ID": "INV-IDEM-X", "来源": "invariant",
                     "类型": "device-android", "优先级": "P0", "判定模式": "state-report"},
            "items": {"S1": "点购买", "S2": "等响应", "E1": "只扣一次"},
            "sections": {}, "section_kv": {"故障注入要求": {
                "kind": "drop_response", "after_step": "S2", "window_ms": "2000"}},
        }
        idem_case = build_case(idem_spec, "INV-IDEM-X", dict(DEFAULT_DEVICE))
        faults = [a for a in idem_case["steps"] if a["action"] == "fault"]
        ck("规格的「故障注入要求」→ fault 动作(落在 after_step 之后、窗口是数值)",
           len(faults) == 1 and faults[0]["kind"] == "drop_response"
           and faults[0]["window_ms"] == 2000
           and idem_case["steps"][-1]["action"] == "fault",
           str(idem_case["steps"]))

        # 规格没写全 -> 不许猜:留 TODO,不造一条半截的 fault
        half_spec = dict(idem_spec, section_kv={"故障注入要求": {"kind": "drop_response"}})
        half_case = build_case(half_spec, "INV-IDEM-X", dict(DEFAULT_DEVICE))
        ck("故障注入字段不全时不伪造 fault,只留 TODO",
           not [a for a in half_case["steps"] if a["action"] == "fault"]
           and any("故障注入缺必填字段" in t for t in half_case["_todo"]),
           str(half_case["_todo"]))
        ck("骨架标记为 draft 且带 skeleton/_todo(不许当成品用)",
           case["review"]["state"] == "draft" and case.get("skeleton") is True
           and case.get("_todo"), str(case["review"]))
        ck("判定项带 after 指向最后一步(校验器不会因 after 缺失而报错)",
           all(c.get("after") == case["steps"][-1]["id"] for c in case["checks"]),
           str([c.get("after") for c in case["checks"]]))
        ck("步骤动作合法(wait)且带 TODO 出处",
           all(a["action"] == "wait" and "TODO" in a["note"] for a in case["steps"]),
           str(case["steps"]))

        # 不覆盖已有文件
        (out / "REQ-SETTLE-C01.json").write_text('{"id": "REQ-SETTLE-C01", "human": true}',
                                                 encoding="utf-8")
        made2, skipped2 = scaffold(specs_dir, out, dict(DEFAULT_DEVICE))
        ck("已有文件不被覆盖(人填过的比骨架值钱)",
           "human" in (out / "REQ-SETTLE-C01.json").read_text(encoding="utf-8")
           and "REQ-SETTLE-C01" in skipped2 and not made2, f"{made2} {skipped2}")

        # 关键性质:骨架的期望覆盖率不该有洞(其它错误可以等,这个不能漏)
        sys.path.insert(0, str(SKILL_DIR / "scripts"))
        import subprocess
        r = subprocess.run([sys.executable,
                            str(SKILL_DIR / "scripts" / "validate_cases.py"),
                            "--specs", str(specs_dir), "--cases", str(out)],  # noqa: S603
                           capture_output=True, text=True)
        ck("骨架能过「期望覆盖率」这一关(只有待填的内容会报错)",
           "MISSING_EXPECTATION_COVERAGE" not in r.stdout, r.stdout[-400:])
        ck("骨架仍被闸门拦住(未复核 -> 不该执行)",
           r.returncode == 1 and "未通过" in r.stdout, r.stdout[-200:])

    print()
    if fails:
        print(f"自测失败 {len(fails)} 项:")
        for x in fails:
            print("  ✗", x)
        return 1
    print("自测通过(E 覆盖率无洞/机械字段照抄/after 合法/draft 骨架/不覆盖已有文件/"
          "闸门仍拦住)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
