# -*- coding: utf-8 -*-
"""
make_e2e_fixture.py —— 生成 M2 端到端验证所需的数据样本

功能说明
--------
为「浏览器端到端验证脚本」（tools/e2e_m2_files.mjs）准备**确定性**的上传样本。
之所以独立成本脚本而不是在 Node 里拼装，原因有两条：

    1. 多 Sheet、合并单元格、多级表头必须用真实的 OOXML 结构才能验证，
       在 Node 侧需要引入额外依赖；本仓库的 Python 环境已装 openpyxl；
    2. 样本的每一处「坑」都要能被人工复核——本文件是唯一的样本定义处，
       评审时对着这份脚本就能算清楚预期值（见下方「预期指标」）。

生成的样本
----------
    e2e_sales.xlsx     主体样本：3 个 Sheet（其中 1 个为空表，应被解析器跳过）
    e2e_cities_gbk.csv GBK 编码 CSV：用于验证编码探测链路
    e2e_invalid.txt    非法格式：用于验证前端/后端拒收链路

预期指标（与样本严格对应，E2E 断言即以此为据）
--------------------------------------------
    e2e_sales.xlsx
        Sheet「销售明细」（62 行 × 6 列原始矩阵）
            第 1 行  A1:F1 合并大标题「2026 年上半年销售明细表」
            第 2 行  A2:B2 合并「销售维度」、C2:F2 合并「指标」 —— 上级表头
            第 3 行  部门 | 负责人 | 销售额 | 订单数 | 客户手机号 | 备注 —— 下级表头
            第 4 行起 60 行数据；另有 A4:A5 纵向合并（验证「向下填充展开」）
            缺失：备注 27 个（45.0%，超 30% 阈值 → 记 1 个高缺失字段）
                  客户手机号 7 个（11.7%）
            ⇒ missing_cells = 34；merged_cells = 4（A1:F1 / A2:B2 / C2:F2 / A4:A5）
            ⇒ header_row = 2（多级表头时取上级行），multi_level = True
        Sheet「部门对照」（1 行表头 + 4 行数据 = 4 行 × 3 列）
            ⇒ header_row = 1，无缺失、无合并
        Sheet「空表」 —— 无任何单元格，解析器应跳过

        文件级聚合（parse_file 口径）
            row_total           = 60 + 4   = 64
            col_total           = 6  + 3   = 9
            missing_cells       = 27 + 7   = 34
            high_missing_fields = 1
            merged_cells        = 4

    e2e_cities_gbk.csv
        表头 城市 | 月份 | 销量 | 单价，10 行数据；中文内容刻意保留，
        否则纯 ASCII 文件会被 UTF-8 strict 解码成功，测不出 GBK 分支。

运行方式
--------
    <工作空间根>/.venv/Scripts/python.exe tools/fixtures/make_e2e_fixture.py --out <目录>

退出码
------
    0  生成成功
    1  生成失败（异常信息打到 stderr）
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# ------------------------------------------------------------
# 样本参数（集中在此，便于评审逐项核对预期指标）
# ------------------------------------------------------------

#: 主体 Sheet 的数据行数（>50 才能触发 F7 预览的第二页）
SALES_DATA_ROWS = 60

#: 每页固定 50 行（后端 PREVIEW_DEFAULT_PER_PAGE），故总页数为 2
PREVIEW_PER_PAGE = 50

#: 部门轮转取值；A4:A5 纵向合并的那一组单独指定，用于验证「向下填充」
DEPARTMENTS = ("华东大区", "华南大区", "华北大区", "西南大区")

#: 纵向合并区块的取值（写在 A4，A5 原始为空，由展开逻辑填充）
MERGED_DEPT = "华东大区"

#: 负责人轮转取值
OWNERS = ("张伟", "李娜", "王强", "刘敏", "陈静")

#: 城市对照表（Sheet 2）
CITY_ROWS = (
    ("华东大区", "上海", "张伟"),
    ("华南大区", "广州", "李娜"),
    ("华北大区", "北京", "王强"),
    ("西南大区", "成都", "刘敏"),
)

#: 主体 Sheet 的表头（下级）
SALES_HEADERS = ("部门", "负责人", "销售额", "订单数", "客户手机号", "备注")


def build_sales_xlsx(dest: Path) -> None:
    """生成主体样本 e2e_sales.xlsx。

    参数：
        dest (Path): 目标文件路径
    返回：
        None

    实现要点：
        - 合并单元格必须**先写值、再 merge**：openpyxl 合并后除左上角外的
          单元格会变成只读的 MergedCell，此时再赋值会抛 AttributeError；
        - 第 2 行两级表头用「两个合并区块」表达，展开后才会有重复组名，
          这正是检验多级表头判据是否用了「宽松判据」的关键结构。
    """
    import openpyxl
    from openpyxl.styles import Alignment, Font

    workbook = openpyxl.Workbook()

    # ---------- Sheet 1：销售明细 ----------
    sheet1 = workbook.active
    sheet1.title = "销售明细"

    # 第 1 行：合并大标题（展开后全行同值 → 必须不被误判为上级表头）
    sheet1["A1"] = "2026 年上半年销售明细表"
    sheet1.merge_cells("A1:F1")

    # 第 2 行：上级表头，两组（展开后「销售维度」×2、「指标」×4）
    sheet1["A2"] = "销售维度"
    sheet1.merge_cells("A2:B2")
    sheet1["C2"] = "指标"
    sheet1.merge_cells("C2:F2")

    # 第 3 行：下级表头（列为 6，且取值互不重复 → 严格判据命中）
    for column_index, header in enumerate(SALES_HEADERS, start=1):
        cell = sheet1.cell(row=3, column=column_index, value=header)
        cell.font = Font(bold=True)
        cell.alignment = Alignment(horizontal="center")

    # 第 4 行起：数据区
    for offset in range(SALES_DATA_ROWS):
        row_number = 4 + offset
        if offset == 0:
            # A4 写入合并区块锚点值；A5 留空，由「向下填充展开」补齐
            sheet1.cell(row=row_number, column=1, value=MERGED_DEPT)
        elif offset == 1:
            sheet1.cell(row=row_number, column=1, value=None)
        else:
            sheet1.cell(row=row_number, column=1, value=DEPARTMENTS[offset % len(DEPARTMENTS)])

        sheet1.cell(row=row_number, column=2, value=OWNERS[offset % len(OWNERS)])
        sheet1.cell(row=row_number, column=3, value=round(10000 + (offset * 1373.37) % 90000, 2))
        sheet1.cell(row=row_number, column=4, value=20 + (offset * 7) % 300)

        # 客户手机号：每 9 行空 1 行（offset % 9 == 4 → 4/13/22/31/40/49/58，共 7 个，11.7%），
        # 低于 30% 高缺失阈值。取值构造为 1[3-9] + 9 位数字，
        # 严格匹配 ^1[3-9]\d{9}$，确保命中敏感规则（值形态兜底路径）。
        if offset % 9 == 4:
            sheet1.cell(row=row_number, column=5, value=None)
        else:
            sheet1.cell(row=row_number, column=5, value=f"1{3 + offset % 7}{(offset * 1000003) % 10**9:09d}")

        # 备注：每 20 行中前 9 行为空 → 共 27 个缺失（45.0%，超阈值 → 高缺失字段）
        if offset % 20 < 9:
            sheet1.cell(row=row_number, column=6, value=None)
        else:
            sheet1.cell(row=row_number, column=6, value=f"第 {offset + 1} 期回款正常")

    # 纵向合并放在**数据写完之后**：先 merge 会让 A5 变为只读 MergedCell
    sheet1.merge_cells("A4:A5")

    # ---------- Sheet 2：部门对照 ----------
    sheet2 = workbook.create_sheet("部门对照")
    for column_index, header in enumerate(("部门", "城市", "负责人"), start=1):
        sheet2.cell(row=1, column=column_index, value=header).font = Font(bold=True)
    for row_offset, city_row in enumerate(CITY_ROWS, start=2):
        for column_index, value in enumerate(city_row, start=1):
            sheet2.cell(row=row_offset, column=column_index, value=value)

    # ---------- Sheet 3：空表（解析器应跳过，不出现在 Sheet 列表中） ----------
    workbook.create_sheet("空表")

    workbook.save(str(dest))


def build_cities_csv(dest: Path) -> None:
    """生成 GBK 编码的 CSV 样本 e2e_cities_gbk.csv。

    参数：
        dest (Path): 目标文件路径
    返回：
        None

    说明：
        - 内容包含中文，否则 UTF-8 strict 会先解码成功，测不出 GB18030 分支；
        - 显式用 gb18030 编码写出，与解析器候选顺序中的第二个编码一致；
        - **必须用 write_bytes 而不是 write_text**：write_text 以文本模式打开文件，
          在 Windows 上会把换行符 `\n` 再次翻译成 `\r\n`，使原本的 `\r\n` 变成
          `\r\r\n`，被 csv.reader 视作「每行后跟一个空行」——实测会让解析器报告
          「已忽略 11 个全空行」这一假象。二进制写入可完全规避换行翻译。
    """
    lines = ["城市,月份,销量,单价"]
    cities = ("上海", "广州", "北京", "成都", "杭州", "武汉", "西安", "南京", "重庆", "苏州")
    for index, city in enumerate(cities):
        month = f"2026-{(index % 12) + 1:02d}"
        lines.append(f"{city},{month},{120 + index * 13},{9.9 + index}")
    dest.write_bytes(("\r\n".join(lines) + "\r\n").encode("gb18030"))


def build_invalid_txt(dest: Path) -> None:
    """生成非法格式样本 e2e_invalid.txt（用于验证拒收链路）。

    参数：
        dest (Path): 目标文件路径
    返回：
        None
    """
    dest.write_text("这不是受支持的数据文件格式。\n", encoding="utf-8")


def main() -> int:
    """命令行入口。

    返回：
        int: 进程退出码（0 成功 / 1 失败）
    """
    parser = argparse.ArgumentParser(description="生成 M2 端到端验证样本")
    parser.add_argument("--out", required=True, help="样本输出目录（不存在会自动创建）")
    args = parser.parse_args()

    out_dir = Path(args.out).resolve()

    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        build_sales_xlsx(out_dir / "e2e_sales.xlsx")
        build_cities_csv(out_dir / "e2e_cities_gbk.csv")
        build_invalid_txt(out_dir / "e2e_invalid.txt")
    except Exception as exc:  # noqa: BLE001 —— CLI 边界统一兜底，把原因打给调用方
        print(f"[ERROR] 样本生成失败：{exc}", file=sys.stderr)
        return 1

    for name in ("e2e_sales.xlsx", "e2e_cities_gbk.csv", "e2e_invalid.txt"):
        target = out_dir / name
        print(f"[OK] {target}  ({target.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
