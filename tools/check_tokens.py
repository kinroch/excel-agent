#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
设计令牌一致性校验脚本（M0 验收 AC 的自动化判据）

功能说明
--------
将 frontend/src/styles/tokens.css 中定义的 CSS 变量，与「UI/UX 设计文档 V1.0.1 第 4 章」
（并经高保真原型 :root 实测核验）的基线令牌**逐项比对**，输出差异报告：

    1. 文档有、CSS 缺失        → 失败（漏落地）
    2. 文档有、CSS 值不一致    → 失败（落错值）
    3. CSS 有、文档未列        → 提示（额外令牌，不判失败）

用法
----
    python tools/check_tokens.py            # 在 excel-agent 根目录或任意目录执行
    # 退出码：0 = 全部一致；1 = 存在缺失或取值不一致

异常说明
--------
- tokens.css 不存在 → 退出码 2
- 基线表由本脚本内维护，来源已在注释中逐项标注，修改令牌须同步修订 UI/UX 第 4 章。
"""

import re
import sys
from pathlib import Path

# 仓库根目录（tools/ 的上一级）
ROOT_DIR = Path(__file__).resolve().parents[1]
TOKENS_CSS = ROOT_DIR / "frontend" / "src" / "styles" / "tokens.css"

# ============================================================
# 基线令牌表（来源：UI/UX 设计文档 V1.0.1 第 4 章；值与高保真原型 :root 一致）
# ============================================================
BASELINE: dict[str, str] = {
    # ---- 4.1 色彩令牌 · 品牌主色（蓝色五级）----
    "--primary": "#1E6FE8",
    "--primary-light": "#4D94FF",
    "--primary-lighter": "#9CC5FF",
    "--primary-strong": "#1558BC",
    "--primary-text": "#1D4ED8",
    # ---- 4.1 背景与表面 ----
    "--bg": "#F3F7FD",
    "--surface": "#FFFFFF",
    "--surface-2": "#E9F0FA",
    "--nav-bg": "rgba(255, 255, 255, .86)",
    # ---- 4.1 文字色阶（5 级）----
    "--text-1": "#0E2A4E",
    "--text-2": "#2C4A73",
    "--text-3": "#4E6E96",
    "--text-4": "#5A7195",
    "--text-5": "#5A7195",
    # ---- 4.1 边框与语义色 ----
    "--border": "#D3E0F1",
    "--divider": "#E4EDF8",
    "--accent-green": "#00A86E",
    "--semantic-up": "#C62828",
    "--semantic-down": "#00794F",
    "--status-warn": "#C62828",
    # ---- 4.1 渐变 ----
    "--gradient-text": "linear-gradient(270deg, #1E6FE8 0%, #4D94FF 50%, #9CC5FF 100%)",
    "--gradient-brand": "linear-gradient(135deg, #4D94FF, #1E6FE8)",
    # ---- 4.2 字体 ----
    "--ff-heading": "'Hanken Grotesk', sans-serif",
    "--ff-body": "Inter, sans-serif",
    "--ff-num": "'Rajdhani', 'Hanken Grotesk', sans-serif",
    # ---- 4.2 字号阶梯（11 档）----
    "--fs-hero": "56px",
    "--fs-h1": "48px",
    "--fs-h2": "42px",
    "--fs-h3": "24px",
    "--fs-h3-card": "18px",
    "--fs-subtitle": "18px",
    "--fs-lead": "20px",
    "--fs-body": "16px",
    "--fs-card-desc": "14px",
    "--fs-caption": "13px",
    "--fs-label": "11px",
    # ---- 4.2 字重（四档）----
    "--fw-regular": "400",
    "--fw-medium": "500",
    "--fw-semibold": "600",
    "--fw-bold": "700",
    # ---- 4.3 间距阶梯（10 档，4px 基准）----
    "--sp-1": "4px",
    "--sp-2": "8px",
    "--sp-3": "12px",
    "--sp-4": "16px",
    "--sp-5": "24px",
    "--sp-6": "32px",
    "--sp-7": "48px",
    "--sp-8": "60px",
    "--sp-9": "80px",
    "--sp-10": "100px",
    # ---- 4.3 区块节奏与容器宽度 ----
    "--section-py": "80px",
    "--container": "1440px",
    "--content-wide": "1100px",
    # ---- 4.3 圆角阶梯 ----
    "--r-xs": "12px",
    "--r-sm": "16px",
    "--r-md": "20px",
    "--r-lg": "24px",
    "--r-pill": "28px",
    "--r-full": "50%",
    # ---- 4.4 阴影与动效 ----
    "--shadow-glow": "0 4px 24px rgba(30, 111, 232, .12)",
    "--shadow-lift": "0 8px 24px rgba(14, 42, 78, .10)",
    "--ease-out-expo": "cubic-bezier(.16, 1, .3, 1)",
    "--dur-hover": ".2s",
    "--dur-enter": "1.2s",
}


def parse_css_variables(css_text: str) -> dict[str, str]:
    """解析 CSS 文本中的自定义属性定义。

    参数：
        css_text (str): tokens.css 全文
    返回：
        dict[str, str]: 变量名 → 取值（值已去除首尾空白与行内注释）
    说明：
        仅匹配 `--name: value;` 形式；值为渐变等含逗号的内容同样可正确截取。
    """
    result: dict[str, str] = {}
    # 去掉 /* ... */ 注释，避免注释内容干扰
    without_comments = re.sub(r"/\*.*?\*/", "", css_text, flags=re.S)
    for match in re.finditer(r"(--[\w-]+)\s*:\s*([^;]+);", without_comments):
        name = match.group(1)
        value = " ".join(match.group(2).split())  # 归一化空白，忽略排版差异
        result[name] = value
    return result


def normalize(value: str) -> str:
    """归一化令牌取值，消除大小写与空白差异后再比较。

    参数：
        value (str): 原始取值
    返回：
        str: 归一化结果（小写、压缩空白）
    """
    return " ".join(value.split()).lower()


def main() -> int:
    """脚本主入口。

    返回：
        int: 0 一致；1 存在差异；2 文件缺失
    """
    if not TOKENS_CSS.exists():
        print(f"未找到令牌文件：{TOKENS_CSS}")
        return 2

    actual = parse_css_variables(TOKENS_CSS.read_text(encoding="utf-8"))

    missing: list[str] = []
    mismatched: list[tuple[str, str, str]] = []

    for name, expected in BASELINE.items():
        if name not in actual:
            missing.append(name)
        elif normalize(actual[name]) != normalize(expected):
            mismatched.append((name, expected, actual[name]))

    extra = sorted(set(actual) - set(BASELINE))

    print("=" * 68)
    print("设计令牌一致性校验（tokens.css ↔ UI/UX 设计文档 V1.0.1 第 4 章）")
    print(f"  基线令牌数：{len(BASELINE)}   实际令牌数：{len(actual)}")
    print("=" * 68)

    if missing:
        print(f"\n[失败] 文档有但 CSS 缺失（{len(missing)} 项）：")
        for name in missing:
            print(f"  - {name}")

    if mismatched:
        print(f"\n[失败] 取值不一致（{len(mismatched)} 项）：")
        for name, expected, got in mismatched:
            print(f"  - {name}\n      文档：{expected}\n      CSS ：{got}")

    if extra:
        print(f"\n[提示] CSS 中的额外令牌（{len(extra)} 项，文档第 4 章未列，不判失败）：")
        for name in extra:
            print(f"  - {name} = {actual[name]}")

    print("-" * 68)
    if not missing and not mismatched:
        print(f"校验通过：{len(BASELINE)} 项基线令牌全部一致。")
        return 0

    print("校验失败：请核对 frontend/src/styles/tokens.css。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
