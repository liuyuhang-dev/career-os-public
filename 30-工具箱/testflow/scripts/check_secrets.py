#!/usr/bin/env python3
"""密钥卫生检查:源码/证据/配置里有没有凭据,敏感文件有没有被 git 跟踪。

**为什么需要它**:这个项目里凭据出过一次事(为查证读配置时把明文密钥打进了对话记录),
而仓库里同时存在 App Store Connect 密钥、Play 会话导出、浏览器配置文件这类东西。
`.gitignore` 挡住了大部分,但"挡住了"这件事从来没有被验证过。

规矩:

1. **工具自己不许泄露密钥。** 报告只给文件、行号、命中类型与**打码片段**(前 4 后 2),
   绝不打印完整值 —— 否则报告本身就成了新的泄露渠道。
2. **命中是候选,不是结论。** 匹配到 `sk-…` 说明"这里像凭据",要人确认。
   `--allow` 用来排除 fixture、测试样本这类故意的假凭据。
3. **只看文本。** 二进制文件跳过(不猜)。跳过的东西会写进备注,不是"没问题"。
4. **写测试夹具时用运行时拼装**(`"sk-" + "proj-..."`),别在仓库里留"密钥形状"的
   字面量 —— 否则本工具会把夹具本身报成泄露(fixture 必须含真实样本时用 `--allow`)。

```bash
python3 "$SKILL_DIR/scripts/check_secrets.py" --path . --out secret-scan.md
```

退出码:`0` = 没有命中;`1` = 有(可能含真凭据,或被跟踪的敏感文件);`2` = 用法错误。

纯标准库,零依赖。
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent

# (类型, 正则, 级别) —— 级别 high 基本就是真凭据,medium 要人确认
PATTERNS: list[tuple[str, re.Pattern, str]] = [
    # 新格式是 sk-proj-…,键体里带连字符,只按字母数字写会漏
    ("openai_key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_\-]{16,}"), "high"),
    ("anthropic_key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{16,}"), "high"),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), "high"),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"), "high"),
    ("github_token", re.compile(
        r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36}\b"
        r"|\bgithub_pat_[A-Za-z0-9_]{22,}\b"), "high"),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"), "high"),
    ("private_key_block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "high"),
    ("bearer_header", re.compile(
        r"[Aa]uthorization\s*[:=]\s*[\"']?Bearer\s+[A-Za-z0-9._\-]{20,}"), "high"),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"
                       r"\.[A-Za-z0-9_\-]{10,}\b"), "medium"),
    ("assigned_secret", re.compile(
        r"(?i)\b(?:api[_\-]?key|secret|password|passwd|token|access[_\-]?key)\b"
        r"\s*[:=]\s*[\"'][^\"'\s]{8,}[\"']"), "medium"),
]

# 敏感文件名(按名字就能判定,不必读内容)
SENSITIVE_NAMES = {
    "credentials.json": "凭据文件",
    "storage_state.json": "浏览器会话导出",
    "auth.json": "鉴权信息",
    ".env": "环境变量(常放密钥)",
    "id_rsa": "SSH 私钥",
    "secrets.json": "密钥文件",
}
SENSITIVE_SUFFIXES = {
    ".p8": "Apple 私钥", ".pem": "私钥/证书", ".key": "私钥",
    ".p12": "证书容器", ".jks": "Java 密钥库", ".keystore": "Android 签名库",
    ".mobileprovision": "描述文件",
}

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv",
             "dist", "build", ".next", "tmp_edit"}
MAX_BYTES = 2_000_000


def redact(text: str) -> str:
    """打码。**报告里永远不出现完整值。**"""
    t = text.strip().strip("\"'")
    if len(t) <= 8:
        return "…"
    return f"{t[:4]}…{t[-2:]}(共 {len(t)} 字符)"


def tracked_files(root: Path) -> set[str]:
    """git 跟踪的文件(相对仓库根,正斜杠)。非 git 仓库返回空集。"""
    try:
        r = subprocess.run(["git", "ls-files"], cwd=str(root), capture_output=True,
                           text=True, timeout=120)  # noqa: S603
    except (OSError, subprocess.SubprocessError):
        return set()
    if r.returncode != 0:
        return set()
    return {ln.strip().replace("\\", "/") for ln in r.stdout.splitlines() if ln.strip()}


def is_binary(path: Path) -> bool:
    try:
        with path.open("rb") as fh:
            return b"\x00" in fh.read(4096)
    except OSError:
        return True


CACHE_HINT = ("profile", "cache", "cache_data")


def scan_file(path: Path, tracked: set[str], root: Path) -> list[dict]:
    findings: list[dict] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return [{"type": "read_error", "level": "info", "path": str(path),
                 "detail": f"读不了: {type(exc).__name__}"}]
    try:
        rel = path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        rel = path.as_posix()
    is_tracked = rel in tracked
    impact = ("**会随仓库分发**(已入库)" if is_tracked
              else "未入库(风险在共享/备份/截图/日志外发,不在提交)")
    lowered = str(path).lower()
    cache_note = (";疑似浏览器缓存或配置文件,内容可能来自网页,需人工确认"
                  if any(h in lowered for h in CACHE_HINT) else "")
    for lineno, line in enumerate(text.splitlines(), 1):
        for name, pattern, level in PATTERNS:
            m = pattern.search(line)
            if m:
                findings.append({"type": name, "level": level, "path": str(path),
                                 "line": lineno, "sample": redact(m.group(0)),
                                 "tracked": is_tracked,
                                 "detail": impact + cache_note})
    return findings


def sensitive_file_finding(path: Path, root: Path, tracked: set[str]) -> dict | None:
    kind = None
    if path.name in SENSITIVE_NAMES:
        kind = SENSITIVE_NAMES[path.name]
    elif path.suffix.lower() in SENSITIVE_SUFFIXES:
        kind = SENSITIVE_SUFFIXES[path.suffix.lower()]
    elif path.name.startswith("id_rsa"):
        kind = "SSH 私钥"
    if not kind:
        return None
    try:
        rel = path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        rel = path.as_posix()
    is_tracked = rel in tracked
    return {
        "type": "tracked_sensitive_file" if is_tracked else "sensitive_file",
        "level": "high" if is_tracked else "info",
        "path": str(path),
        "detail": (f"{kind} —— **被 git 跟踪了**(会随仓库分发!)"
                   if is_tracked else
                   f"{kind} —— 存在于工作区;确认它被 .gitignore 覆盖(当前未跟踪)"),
    }


def scan_tree(root: Path, allow: list[str]) -> tuple[list[dict], list[str]]:
    findings: list[dict] = []
    notes: list[str] = []
    tracked = tracked_files(root)
    if not tracked:
        notes.append("拿不到 git 跟踪列表(不是仓库或 git 不可用)—— "
                     "「敏感文件是否被跟踪」这一项没查")
    allow_abs = [Path(a).resolve() for a in allow]
    skipped_binary = skipped_large = skipped_allow = 0

    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        resolved = path.resolve()
        if any(str(resolved).startswith(str(a)) for a in allow_abs):
            skipped_allow += 1
            continue
        f = sensitive_file_finding(path, root, tracked)
        if f:
            findings.append(f)
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size > MAX_BYTES:
            skipped_large += 1
            continue
        if is_binary(path):
            skipped_binary += 1
            continue
        findings += scan_file(path, tracked, root)

    notes.append(f"跳过: 二进制 {skipped_binary} 个、超大(>{MAX_BYTES // 1000}KB)"
                 f" {skipped_large} 个、allow 名单 {skipped_allow} 个 —— "
                 f"这些**没扫**,不是「干净」")
    return findings, notes


def render_report(roots: list[Path], findings: list[dict], notes: list[str],
                  fail_on: str = "any") -> str:
    now = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    high = [f for f in findings if f["level"] == "high"]
    med = [f for f in findings if f["level"] == "medium"]
    info = [f for f in findings if f["level"] == "info"]
    lines = [
        "# 密钥卫生检查",
        "",
        f"- 生成时间: {now}",
        f"- 扫描范围: " + ", ".join(f"`{r}`" for r in roots),
        f"- 命中: **高危 {len(high)}**、可疑 {len(med)}、提示 {len(info)}",
        f"- 其中**已入库** {sum(1 for f in findings if f.get('tracked'))} 条"
        f"(只要有一条,就等于会随仓库分发)",
        f"- 退出码口径: --fail-on {fail_on}",
        "",
        "> **命中是候选,不是结论。** 打码片段只给前 4 后 2 字符 —— "
        "报告本身不再成为新的泄露渠道。",
        "> 真凭据泄露的处理顺序:**先轮换密钥,再清理文件与历史**,最后才修 .gitignore。",
        "",
    ]

    def block(title: str, items: list[dict]) -> None:
        lines.append(f"## {title}")
        if not items:
            lines.append("- 无")
            lines.append("")
            return
        for f in items:
            loc = f"`{f['path']}`" + (f" 第 {f['line']} 行" if f.get("line") else "")
            sample = f" —— 片段 `{f['sample']}`" if f.get("sample") else ""
            detail = f" —— {f['detail']}" if f.get("detail") else ""
            lines.append(f"- **[{f['type']}]** {loc}{sample}{detail}")
        lines.append("")

    block("高危(像真凭据 / 被跟踪的敏感文件)", high)
    block("可疑(需要人确认)", med)
    block("提示(存在敏感文件但未跟踪)", info)
    lines.append("## 扫描备注(跳过什么、没查什么)")
    lines.append("")
    for n in notes:
        lines.append(f"- {n}")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="密钥卫生检查:文本里的凭据模式 + 敏感文件是否被 git 跟踪")
    ap.add_argument("--path", type=Path, action="append", default=[],
                    help="扫描目录(可重复);默认当前目录")
    ap.add_argument("--allow", action="append", default=[],
                    help="排除的路径前缀(fixture/测试样本);可重复")
    ap.add_argument("--out", type=Path, help="报告 markdown 路径")
    ap.add_argument("--json", type=Path, help="机器可读结果")
    ap.add_argument("--fail-on", choices=["any", "tracked"], default="any",
                    help="any=有任何命中就退出 1(默认);tracked=只在凭据已入库时才退出 1")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    roots = args.path or [Path.cwd()]
    findings: list[dict] = []
    notes: list[str] = []
    for root in roots:
        if not root.exists():
            print(f"错误: 找不到 {root}", file=sys.stderr)
            return 2
        f, n = scan_tree(root, args.allow)
        findings += f
        notes += n

    if args.fail_on == "tracked":
        code = 1 if any(f.get("tracked") for f in findings) else 0
    else:
        code = 0 if not findings else 1
    report = render_report(roots, findings, notes, args.fail_on)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(report, encoding="utf-8")
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(
            {"roots": [str(r) for r in roots], "findings": findings, "notes": notes},
            ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not args.quiet:
        high = [f for f in findings if f["level"] == "high"]
        print(f"密钥卫生检查: 命中 {len(findings)}(高危 {len(high)})")
        for f in findings:
            loc = f"{f['path']}" + (f":{f['line']}" if f.get("line") else "")
            print(f"  [{f['level']:6s}] {f['type']:22s} {loc}"
                  + (f" 片段 {f['sample']}" if f.get("sample") else ""))
        for n in notes:
            print(f"  · {n}")
        if args.out:
            print(f"报告已写出: {args.out}")
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
        root = Path(td)
        (root / "src").mkdir()
        # 假凭据一律运行时拼装:本文件自己也要经得起扫描(见模块开头第三条规矩)
        fake_key = "sk-" + "proj-" + "A1b2C3d4E5f6G7h8I9j0"
        key_name = "API_" + "KEY"
        (root / "src" / "conf.py").write_text(
            '{0} = "{1}"\n'.format(key_name, fake_key)
            + 'note = "password 字段这里是说明文字,不是值"\n'
            + "ok = 1\n", encoding="utf-8")
        (root / "keys").mkdir()
        (root / "keys" / "AuthKey_ABC123.p8").write_text(
            "-----BEGIN " + "PRIVATE KEY-----\n", encoding="utf-8")
        (root / "a.env").write_text("TO" + 'KEN="abcdef1234567890"\n', encoding="utf-8")
        (root / "appstoreconnect").mkdir()
        (root / "appstoreconnect" / "credentials.json").write_text("{}", encoding="utf-8")
        (root / "clean.py").write_text("print('hello')\n", encoding="utf-8")
        (root / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00binary")
        (root / "fixtures").mkdir()
        (root / "fixtures" / "sample.txt").write_text(
            f"sample key {fake_key}\n", encoding="utf-8")

        findings, notes = scan_tree(root, allow=[])
        kinds = {f["type"] for f in findings}
        ck("抓到 API key 模式", "openai_key" in kinds, str(sorted(kinds)))
        ck("抓到私钥块与 .p8/.env 文件",
           "private_key_block" in kinds and "sensitive_file" in kinds, str(sorted(kinds)))
        ck("凭据 json 被识别为敏感文件", any(
            f.get("path", "").endswith("credentials.json") for f in findings))
        ck("散文里的 password 不误报(那一行没有值)",
           not any(f["type"] == "assigned_secret"
                   and f["path"].endswith("conf.py") and f.get("line") == 2
                   for f in findings),
           str([f for f in findings if f["type"] == "assigned_secret"]))
        ck("二进制文件被跳过而不是乱报",
           any("二进制" in n for n in notes), str(notes))
        ck("备注写清「跳过的没扫,不是干净」", any("没扫" in n for n in notes), str(notes))

        # 打码:报告与输出里绝不能出现完整密钥
        report = render_report([root], findings, notes)
        ck("报告不泄露完整密钥", fake_key not in report, "报告里出现了完整 key!")
        ck("报告给出打码片段", "sk-p…0(" in report or "…" in report)

        # allow 名单
        f2, _ = scan_tree(root, allow=[str(root / "fixtures")])
        ck("allow 名单能排除 fixture",
           all("fixtures" not in f.get("path", "") for f in f2))

        # git 跟踪的敏感文件 -> high
        import subprocess as sp
        sp.run(["git", "init", "-q", str(root)], check=False)  # noqa: S603
        sp.run(["git", "-C", str(root), "add", "-f",  # noqa: S603
                "appstoreconnect/credentials.json"], check=False)
        f3, _ = scan_tree(root, allow=[])
        tracked = [f for f in f3 if f["type"] == "tracked_sensitive_file"]
        ck("被 git 跟踪的敏感文件 -> 高危并说明会随仓库分发",
           bool(tracked) and tracked[0]["level"] == "high"
           and "分发" in tracked[0]["detail"], str(tracked))
        ck("退出码语义: 有命中 -> 1,无命中 -> 0",
           (0 if not f3 else 1) == 1)

    print()
    if fails:
        print(f"自测失败 {len(fails)} 项:")
        for x in fails:
            print("  ✗", x)
        return 1
    print("自测通过(凭据模式/.p8/.env/凭据 json;散文不误报;打码不泄露;allow 名单;"
          "被跟踪的敏感文件标高危;跳过项如实记备注)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
