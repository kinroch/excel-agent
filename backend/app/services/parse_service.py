# -*- coding: utf-8 -*-
"""
文件解析引擎（依据《开发技术文档 V1.0.1》5.5 F1/F4.2、3.6 异步任务、8.1 脱敏合规）

功能说明
--------
本模块是「把用户上传的 xlsx / csv 变成可分析结构化数据」这一能力的**唯一实现处**，
对外只暴露两个入口：

    parse_file(...)        解析上传原件，产出「Sheet 结构 + 字段说明 + CSV 副本」
    read_preview(...)      按页读取 CSV 副本（F7 数据预览）
    mask_value(...)        对敏感值做掩码（F6 字段说明展示层复用）

对应功能点与验收标准
--------------------
    F-4.2-3 / AC-4.2-2  多 Sheet 识别
    F-4.2-4             表头识别与数据类型推断
    F-4.2-5 / AC-4.2-4  合并单元格识别与展开
    F-4.2-6 / AC-4.2-5  缺失值识别（缺失率统计）
    F-4.2-7             数据预览（分页）
    F-4.7-2 / AC-4.7-2  敏感字段正则识别 + 样本脱敏

设计要点（评审重点）
--------------------
① **本模块不查库、不写库**：只做「读文件 → 算结构 → 落 CSV 副本」。
   数据库落库（data_sheets / files.parse_*）由 `services/file_service` 负责。
   这样解析逻辑可以脱离 Web 与数据库单独做单元测试（见 tests/test_m2_files.py）。
   仅有的文件系统写入（CSV 副本）也统一走 `infra.storage` 的路径约定函数。

② **解析失败用专用异常 ParseFailure，不用 BizError**：
   解析会在**后台线程**中执行（技术文档 3.6「超大文件自动转后台协程解析」），
   此时已无 HTTP 响应可返回，失败原因必须落库到 files.parse_error。
   若直接在解析层抛 BizError，会与「请求内业务失败」混淆。
   因此解析层抛 ParseFailure（携带可读原因枚举），由 file_service 决定：
   同步路径 → BizError(2004, 原因)；异步路径 → 写入 parse_error 字段。

③ **解析产物统一落 CSV 副本**（uploads/_parsed/{file_id}/sheet_{i}.csv）：
   - F7 预览读该副本，保证「预览所见 = 分析所用」（口径一致，避免两套解析逻辑）；
   - M3 代码沙箱按技术文档 3.4 要求「仅挂载脱敏后的数据副本（CSV 形态）」，
     可直接复用该文件，无需二次转换；
   - 该路径可由 (file_id, sheet_index) 推导，因此**无需改动已冻结的建表脚本**。

④ **失败原因枚举**（PRD 4.2「边界与异常」/ AC-4.2-3 红色警示条）：
       文件损坏 / 文件已加密受保护 / 未读取到任何有效数据 / 超出行列上限
   全部通过 ParseFailure.reason 透出，前端直接展示，不做二次翻译。

⑤ **合并单元格展开策略 = 填充法**（UI/UX 6.3「合并单元格向下填充展开」）：
   取合并区域左上角的值，填充到区域覆盖的每一个格子。
   这样既不产生空列错位，也让后续按行统计（pandas）能正确取到分组维度值。

异常说明
--------
parse_file 只抛 ParseFailure（可预期失败）与 OSError（磁盘写入失败）。
其余异常（如 openpyxl 的 BadZipFile）在本模块内部被翻译为 ParseFailure。
"""

from __future__ import annotations

import csv
import datetime as dt
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core.logging import get_logger
from app.infra import storage

logger = get_logger(__name__)


# ============================================================
# 一、常量与阈值（评审可调项集中在此处，避免散落魔法数字）
# ============================================================

# 支持的输入格式（与 F1 校验链、错误码 2001 文案一致）
SUPPORTED_FORMATS: frozenset[str] = frozenset({"xlsx", "csv"})

# 行列上限：超出即判定「超出行列上限」（PRD 4.2 边界场景）
MAX_ROW_LIMIT = 200_000
MAX_COL_LIMIT = 512

# 表头识别扫描范围：在数据区前 N 行中寻找表头。
# 取值理由：真实业务表的「标题行 + 说明行 + 空行」通常不超过 10 行；
# 再大反而容易把数据行误判为表头。
HEADER_SCAN_ROWS = 10

# 表头判定阈值：非空单元格占比达到该值，且非空值全为文本时，才认为是表头行。
HEADER_NON_EMPTY_RATIO = 0.6

# 高缺失率阈值：字段缺失率达到该值即视为「高缺失字段」
# （UI/UX 6.3 字段说明表「高缺失率红色标注」；F6 响应中一并下发给前端）。
# 取值 0.3 为业界常用经验值（超过三成缺失，统计口径需谨慎）。
HIGH_MISSING_THRESHOLD = 0.3

# 示例值采集个数：技术文档 5.5 F6「samples[3]」
SAMPLE_SIZE = 3

# 敏感字段「值形态」判定所需的最小样本数（样本太少不足以判断，避免误判）
SENSITIVE_MIN_SAMPLES = 3

# CSV 编码探测候选顺序（技术文档 5.5 F1：UTF-8 / GBK / GB18030 自动识别，AC-4.2-6）。
# GB18030 是 GBK/GB2312 的超集，故排在 GBK 之前即可覆盖两者，无需单列 GBK。
# big5 仅在前两者都无法 strict 解码时兜底（GB18030 覆盖范围极广，实际极少触发），
# 保留它是为避免「繁体 Big5 文件被当作二进制而整体解析失败」这一更差的结果。
ENCODING_CANDIDATES: tuple[str, ...] = ("utf-8", "gb18030", "big5")

# 编码探测采样长度：64KB 足以覆盖中文统计特征，又不至于为超大文件白读一遍
ENCODING_SAMPLE_BYTES = 64 * 1024

# 日期文本形态（用于从字符串推断「日期」类型）
_DATE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^\d{4}-\d{1,2}-\d{1,2}([ T]\d{1,2}:\d{2}(:\d{2})?)?$"), "样本含 - 分隔日期"),
    (re.compile(r"^\d{4}/\d{1,2}/\d{1,2}([ T]\d{1,2}:\d{2}(:\d{2})?)?$"), "样本含 / 分隔日期"),
    (re.compile(r"^\d{4}年\d{1,2}月\d{1,2}日$"), "样本含中文年月日"),
    (re.compile(r"^\d{1,2}:\d{2}(:\d{2})?$"), "样本为时间格式"),
)

# 「整数文本」形态：可选正负号 + 纯数字（不含千分位、不含小数点）
_INT_TEXT = re.compile(r"^[+-]?\d+$")

# 「小数文本」形态：可选正负号 + 数字 + 小数点 + 数字（兼容 .5 / 5. 形态）
_DECIMAL_TEXT = re.compile(r"^[+-]?(\d+\.\d*|\.\d+)$")

# 布尔文本形态（中英文兼容）
_BOOL_TEXTS = frozenset({"true", "false", "是", "否", "y", "n", "yes", "no"})


# ============================================================
# 二、敏感字段脱敏规则（技术文档 5.5 F-4.7-2 / 8.1）
# ============================================================
# 说明：技术文档要求「解析期正则识别（手机号/身份证/邮箱/银行卡，规则可在 configs 扩展）」。
# 本期把规则以**字面量**内置于本模块（不查库），原因：
#   ① 解析器保持无数据库依赖，可脱离 Web 单独跑（设计要点 ①）；
#   ② configs 表扩展留待 M3（配置管理接口随 LLM 网关一并提供），届时改为
#      「启动时从 configs 载入到进程内规则表」，本模块接口不变。
# 识别分两路，命中任一即判为敏感：
#   A. 字段名关键词（如「手机号」「邮箱」「身份证」）——业务命名约定；
#   B. 值形态正则（如 11 位手机号、18 位身份证）——命名不规范时的兜底。

_SENSITIVE_KINDS: tuple[str, ...] = ("手机号", "身份证", "邮箱", "银行卡")

# 值形态规则：命中后给出敏感类型
_VALUE_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("手机号", re.compile(r"^1[3-9]\d{9}$")),
    # 身份证：只认 18 位（末位可为 X），且**内嵌出生日期**必须合法
    #   ^\d{6}  地址码 + (19|20)\d{2} 年 + 合法月 + 合法日 + \d{3} 顺序码 + 校验位
    # 刻意不收录 15 位老版身份证：15 位纯数字与「订单号/流水号」完全同形，
    # 值形态判定会大面积误报；老版证件交由 _NAME_RULES 的字段名关键词兜底。
    ("身份证", re.compile(r"^\d{6}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx]$")),
    ("邮箱", re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")),
    # 银行卡：16~19 位纯数字（过宽，故仅在字段名同时含「卡/账号」类关键词时才启用，
    # 见 _BANK_NAME_HINT 逻辑，避免把「订单号」误判为银行卡）
    ("银行卡", re.compile(r"^\d{16,19}$")),
)

# 字段名关键词规则：命中后给出敏感类型（小写匹配）
_NAME_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("手机号", ("手机", "电话", "mobile", "phone", "tel")),
    ("身份证", ("身份证", "证件号", "idcard", "id_card")),
    ("邮箱", ("邮箱", "邮件", "email", "e-mail", "mail")),
    ("银行卡", ("银行卡", "卡号", "银行账号", "bankcard", "bank_card")),
)

# 银行卡「值形态」的额外约束：字段名必须含下列关键词之一才允许按 16~19 位数字判卡号。
# 目的：16~19 位纯数字与「订单号/流水号」高度同形，仅靠值形态会大面积误判。
_BANK_NAME_HINT = ("卡", "账号", "账户", "bank", "account")


# ============================================================
# 三、对外数据结构
# ============================================================


class ParseFailure(Exception):
    """解析失败（可预期失败），携带用户可读原因。

    属性：
        reason (str): 失败原因枚举文案，直接用于 F3 的 error 字段与红色警示条。
                      取值来自「失败原因枚举」（文件损坏/文件已加密受保护/
                      未读取到任何有效数据/超出行列上限）。
    """

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass
class FieldProfile:
    """单个字段（列）的画像，对应 F6 字段说明的一行。

    属性：
        name (str): 字段名
        dtype (str): 推断类型（字符串/整数/小数/日期/布尔）
        samples (list[str]): 最多 3 个示例值（敏感字段已脱敏）
        missing_rate (float): 缺失率 0~1
        infer_basis (str): 类型推断依据
        sensitive (bool): 是否敏感字段
        sensitive_kind (str | None): 敏感类型
    """

    name: str
    dtype: str
    samples: list[str] = field(default_factory=list)
    missing_rate: float = 0.0
    infer_basis: str = ""
    sensitive: bool = False
    sensitive_kind: str | None = None


@dataclass
class SheetParseResult:
    """单个 Sheet 的解析结果。

    属性：
        index (int): Sheet 顺序索引（0 起）
        name (str): Sheet 名（csv 取文件名主干）
        row_count (int): 数据行数（不含表头）
        col_count (int): 列数
        header_row (int): 表头所在原始行号（1 起，用于「表头不在第一行」提示）
        header_note (str): 解析处理说明（由 build_sheet_note 生成）
        fields (list[FieldProfile]): 字段画像
        csv_path (Path): CSV 副本落盘路径
        missing_cells (int): 该 Sheet 缺失单元格数
        merged_cells (int): 展开的合并区域数
        multi_level (bool): 是否为多级表头（已降级为「上级/下级」列名）
        dropped_rows (int): 剔除的全空行数
        dropped_cols (int): 剔除的全空列数
        extra_notes (list[str]): 额外说明（编码识别/容错提示等）
    """

    index: int
    name: str
    row_count: int
    col_count: int
    header_row: int
    header_note: str
    fields: list[FieldProfile]
    csv_path: Path
    missing_cells: int = 0
    merged_cells: int = 0
    multi_level: bool = False
    dropped_rows: int = 0
    dropped_cols: int = 0
    extra_notes: list[str] = field(default_factory=list)


@dataclass
class ParseOutcome:
    """整份文件的解析结果（可能含多个 Sheet）。

    属性：
        sheets (list[SheetParseResult]): 全部 Sheet（按原始顺序）
        encoding (str | None): CSV 实际识别编码（xlsx 恒为 None）
        row_total / col_total / missing_cells / high_missing_fields / merged_cells:
            数据质量聚合指标（对应 F3 的 quality 五项）
    """

    sheets: list[SheetParseResult] = field(default_factory=list)
    encoding: str | None = None
    row_total: int = 0
    col_total: int = 0
    missing_cells: int = 0
    high_missing_fields: int = 0
    merged_cells: int = 0


# 进度回调：(进度百分比 0~100, 阶段文字) -> None
# file_service 通过该回调把进度写入 files.parse_progress / 生成 stage_text。
ProgressCallback = Callable[[int, str], None]


# ============================================================
# 四、CSV 编码探测（AC-4.2-6）
# ============================================================


def _try_decode(sample: bytes, encoding: str) -> str | None:
    """尝试用指定编码解码样本；失败返回 None。

    参数：
        sample (bytes): 文件头部字节样本
        encoding (str): 候选编码名
    返回：
        str | None: 解码成功返回文本；失败返回 None

    说明（易错点）：
        采样会在**多字节字符中间**截断，直接 strict 解码会误报「编码不支持」。
        因此遇到 UnicodeDecodeError 时，只有当异常原因是
        `unexpected end of data`（即样本恰好断在一个字符中途）时才截到出错点重试；
        若是 `invalid start byte` / `invalid continuation byte`，说明该编码确实不匹配，
        必须返回 None。

        **为什么必须区分这两种原因**（实测踩坑）：
        若只按「出错位置是否靠近末尾」宽松放行，一个「绝大部分是 ASCII、
        末尾恰好带一个 GBK 汉字」的文件会被误判成 UTF-8；
        而 `unexpected end of data` 是 CPython 对截断的精确标记，用它判别零误报。

        BOM 处理：utf-8 解码带 BOM 的文件会在首字符产生 \\ufeff，此处统一剥离，
        让上层按纯文本处理（BOM 编码名的选择在 detect_csv_encoding 中完成）。
    """
    try:
        text = sample.decode(encoding)
    except UnicodeDecodeError as exc:
        if exc.reason != "unexpected end of data":
            return None
        try:
            text = sample[: exc.start].decode(encoding)
        except UnicodeDecodeError:
            return None
    except LookupError:  # 该 Python 环境不支持该编码名
        return None
    return text.lstrip("\ufeff")


def detect_csv_encoding(sample: bytes) -> tuple[str, str | None]:
    """探测 CSV 文件的文本编码（AC-4.2-6）。

    参数：
        sample (bytes): 文件头部字节样本（建议 64KB）
    返回：
        tuple[str, str | None]:
            第 1 项为最终选用的编码名；
            第 2 项为提示信息（正常识别为 None；只能容错读取时给出说明文案）

    探测顺序（技术文档 5.5 F1）：
        1. BOM 优先：带 UTF-8 BOM 直接判定 utf-8-sig；带 UTF-16/32 BOM 判定对应编码；
        2. **UTF-8 strict 解码成功即判定 UTF-8**；
        3. 否则依次尝试 gb18030、big5，取第一个能 strict 解码者；
        4. 全部失败：退回 utf-8 + errors="replace" 容错读取，并给出提示信息，
           避免整份文件因个别字节损坏而被判定为「解析失败」。

    **为什么不能按「解出中文的个数」打分选优**（实测踩到的严重缺陷，务必保留此说明）
    ---------------------------------------------------------------------------
    最初的实现是「对每个候选编码 strict 解码，取中文字符最多的那个」。这在真实
    UTF-8 中文文件上会**系统性判错**：
        「部门」的 UTF-8 编码是 6 字节（每汉字 3 字节），GB18030 下同样这 6 字节
        会被解成 3 个汉字（每汉字 2 字节）。也就是说，对同一段字节，
        GB18030 解出的汉字数**总是**比 UTF-8 多，打分法必然选中 GB18030，
        结果中文全部变成乱码（如 `部门` → `閮ㄩ棬`）。
    正确做法利用「UTF-8 的编码约束远比 GB18030 严格」这一事实：
        中文 GBK 字节串几乎不可能恰好是合法 UTF-8；反之若能 strict 解开，
        几乎必然就是 UTF-8。故用「strict 可解性」而非「字符计数」做判据。
    """
    # ---- 第 1 步：BOM 优先判定（BOM 是文件自带的强信号，优先级最高） ----
    if sample.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig", None
    if sample.startswith((b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")):
        return "utf-32", None
    if sample.startswith((b"\xff\xfe", b"\xfe\xff")):
        return "utf-16", None

    # ---- 第 2 步 + 第 3 步：按候选顺序取第一个 strict 可解者 ----
    for encoding in ENCODING_CANDIDATES:
        if _try_decode(sample, encoding) is not None:
            return encoding, None

    # ---- 第 4 步：全部 strict 失败 → 容错读取并提示 ----
    return "utf-8", "文件编码无法识别，已按 UTF-8 容错读取，个别字符可能显示异常"


# ============================================================
# 五、敏感字段识别与脱敏（F-4.7-2 / AC-4.7-2）
# ============================================================


def _match_name_kind(name: str) -> str | None:
    """按字段名关键词判断敏感类型。

    参数：
        name (str): 字段名
    返回：
        str | None: 命中返回敏感类型（手机号/身份证/邮箱/银行卡）；未命中返回 None
    """
    lowered = (name or "").lower()
    for kind, keywords in _NAME_RULES:
        if any(keyword in lowered for keyword in keywords):
            return kind
    return None


def _match_value_kind(texts: list[str], name: str) -> str | None:
    """按取值形态判断敏感类型（字段名的兜底补充）。

    参数：
        texts (list[str]): 该字段的非空样本值（字符串形态）
        name (str): 字段名（用于银行卡的额外约束）
    返回：
        str | None: 命中返回敏感类型；样本不足或未命中返回 None

    说明：
        判定要求「全部（或极大多数）样本命中同一规则」，用 100% 命中作为门槛，
        避免把「备注」这类个别值恰好像手机号的字段误判为敏感字段。
    """
    if len(texts) < SENSITIVE_MIN_SAMPLES:
        return None
    for kind, pattern in _VALUE_RULES:
        # 银行卡的特殊约束：值形态（16~19 位数字）与订单号同形，须字段名配合才判定
        if kind == "银行卡" and not any(hint in (name or "").lower() for hint in _BANK_NAME_HINT):
            continue
        if all(pattern.match(text) for text in texts):
            return kind
    return None


def mask_value(value: Any, kind: str) -> str:
    """对敏感值做掩码（技术文档 5.5：`che***@example.com` 形态）。

    参数：
        value (Any): 原始值
        kind (str): 敏感类型（手机号/身份证/邮箱/银行卡）
    返回：
        str: 掩码后的文本；无法识别的形态返回「***」

    掩码规则（保留可辨识前缀/后缀，便于人工核对又不泄露完整信息）：
        手机号    13812345678      → 138****5678
        邮箱      chen@a.com       → chen***@a.com（保留域名，便于判断数据来源）
        身份证    110101199001011234 → 110101**********34
        银行卡    6222021234567890 → 6222********7890
    """
    text = "" if value is None else str(value)
    if kind == "邮箱":
        if "@" in text:
            local, _, domain = text.partition("@")
            head = local[:3] if len(local) > 3 else local[:1]
            return f"{head}***@{domain}"
        return "***"
    if kind == "手机号":
        return f"{text[:3]}****{text[-4:]}" if len(text) >= 7 else "***"
    if kind == "身份证":
        if len(text) <= 6:
            return "***"
        return f"{text[:6]}{'*' * (len(text) - 8)}{text[-2:]}"
    if kind == "银行卡":
        return f"{text[:4]}{'*' * (len(text) - 8)}{text[-4:]}" if len(text) > 8 else "***"
    return "***"


# ============================================================
# 六、类型推断（F-4.2-4）
# ============================================================


def _classify_value(value: Any) -> str:
    """把单个非空值归类到候选类型标签。

    参数：
        value (Any): 单元格原始值
    返回：
        str: 候选标签（布尔/整数/小数/日期/字符串之一）
    """
    # 原生类型优先（xlsx 场景，类型信息最可靠）
    if isinstance(value, bool):
        return "布尔"
    if isinstance(value, int):
        return "整数"
    if isinstance(value, float):
        # 浮点但取值为整（1.0）在 Excel 里极常见，仍算「小数」列更安全
        return "小数"
    if isinstance(value, dt.datetime | dt.date | dt.time):
        return "日期"

    text = str(value).strip()
    lowered = text.lower()
    if lowered in _BOOL_TEXTS:
        return "布尔"
    for pattern, _basis in _DATE_PATTERNS:
        if pattern.match(text):
            return "日期"
    if _INT_TEXT.match(text):
        return "整数"
    if _DECIMAL_TEXT.match(text):
        return "小数"
    return "字符串"


def infer_dtype(values: list[Any]) -> tuple[str, str]:
    """按整列取值推断数据类型并给出推断依据。

    参数：
        values (list[Any]): 该列**非空**取值列表
    返回：
        tuple[str, str]: (类型名, 推断依据文案)
    说明：
        汇总规则（从严到宽，避免把混杂列误标成数值类型导致后续统计口径错误）：
            全部 布尔                  → 布尔
            全部 整数                  → 整数
            全部 ∈ {整数, 小数} 且含小数 → 小数
            全部 日期                  → 日期
            其它（含文本或类型混杂）    → 字符串
    """
    if not values:
        return "字符串", "全部为空，无法推断"

    kinds = [_classify_value(v) for v in values]
    kind_set = set(kinds)

    if kind_set == {"布尔"}:
        return "布尔", "全部为真/假取值"
    if kind_set == {"整数"}:
        return "整数", "全部为整数"
    if kind_set <= {"整数", "小数"} and "小数" in kind_set:
        return "小数", "含小数的数值"
    if kind_set == {"日期"}:
        # 依据文案细化：优先说明命中的日期形态（更贴近人工核对习惯）
        first_text = str(values[0]).strip()
        for pattern, basis in _DATE_PATTERNS:
            if pattern.match(first_text):
                return "日期", basis
        return "日期", "样本为日期对象"
    if "字符串" in kind_set:
        return "字符串", "含非数值文本"
    return "字符串", "取值类型混杂，按文本处理"


# ============================================================
# 七、表头识别与多级表头降级
# ============================================================


def _is_blank_row(row: list[Any]) -> bool:
    """判断整行是否全空（None 或去空格后为空串）。

    参数：
        row (list[Any]): 一行单元格值
    返回：
        bool: 全空返回 True
    """
    return all(cell is None or (isinstance(cell, str) and not cell.strip()) for cell in row)


def _looks_like_header(row: list[Any]) -> bool:
    """判断某一行是否「像表头」。

    参数：
        row (list[Any]): 一行单元格值
    返回：
        bool: 像表头返回 True

    判据（三条同时满足）：
        ① 非空单元格占比 ≥ HEADER_NON_EMPTY_RATIO（表头通常填满）
        ② 非空值全部为文本（表头不会是纯数字/日期）
        ③ 非空值互不重复（重复列名更多出现在数据行）
    """
    filled = [cell for cell in row if cell is not None and str(cell).strip() != ""]
    if not filled:
        return False
    if len(filled) / max(len(row), 1) < HEADER_NON_EMPTY_RATIO:
        return False
    if not all(isinstance(cell, str) for cell in filled):
        return False
    texts = [str(cell).strip() for cell in filled]
    return len(set(texts)) == len(texts)


def _looks_like_upper_header(row: list[Any]) -> bool:
    """判断某一行是否「像多级表头的上级行」（宽松判据）。

    参数：
        row (list[Any]): 一行单元格值
    返回：
        bool: 像上级表头返回 True

    与 _looks_like_header 的差异（重要）：
        上级表头里被**合并的组名**在展开后会连续重复（如「销售维度」跨 2 列
        变成两个「销售维度」），因此这里**不能**用「取值互不重复」做判据，
        否则真实的二级表头永远识别不出来。仅要求「填充率高 + 非空值全为文本」。
    """
    filled = [cell for cell in row if cell is not None and str(cell).strip() != ""]
    if not filled:
        return False
    if len(filled) / max(len(row), 1) < HEADER_NON_EMPTY_RATIO:
        return False
    return all(isinstance(cell, str) for cell in filled)


def _detect_header_index(rows: list[list[Any]]) -> int:
    """在数据区前若干行中定位表头行下标。

    参数：
        rows (list[list[Any]]): 已展开合并单元格的整表行数据
    返回：
        int: 表头行下标（0 起）；未识别到时返回 0

    说明：
        只扫描前 HEADER_SCAN_ROWS 行，并跳过全空行。
        「表头不在第一行」属 PRD 4.2 明确的边界场景，识别到非 0 行时由
        header_note 明确提示「已跳过前 N 行说明性内容」，避免用户困惑。
    """
    limit = min(HEADER_SCAN_ROWS, len(rows))
    for idx in range(limit):
        if _is_blank_row(rows[idx]):
            continue
        if _looks_like_header(rows[idx]):
            return idx
    return 0


def _is_multi_level_header(rows: list[list[Any]], header_idx: int) -> bool:
    """判断表头是否为多级（上下两行均为表头形态）。

    参数：
        rows (list[list[Any]]): 整表行数据
        header_idx (int): 已识别的表头行下标
    返回：
        bool: 上一行构成合法的上级表头 → True

    判据（三条同时满足）：
        ① 上一行通过**宽松**表头判据（填充率高且全为文本）；
        ② 上一行的**去重后**取值个数 ≥ 2 —— 这一条用于把「整行合并的大标题」
           与「真正的上级表头」区分开：大标题展开后全行同值（去重后仅 1 个），
           而多级表头的上级至少有两组（如「销售维度 / 指标」）；
        ③ 上一行与表头行的取值集合不同（完全相同说明是重复标题行，不是分级）。

    说明：
        PRD 4.2 要求「多级表头」降级处理：本实现把上下两级用「上级/下级」拼接为
        单级列名（如「销售维度/部门」），既保留语义又可被 pandas 直接使用。
        已知取舍：若上级只有唯一一组名称（覆盖全部列），会被判为大标题而丢弃，
        此时仅用下级列名——这是极少见的表头形态，丢失的信息量很小。
    """
    if header_idx == 0:
        return False
    upper = rows[header_idx - 1]
    if not _looks_like_upper_header(upper):
        return False

    upper_texts = [str(c).strip() for c in upper if c is not None and str(c).strip()]
    lower_texts = [str(c).strip() for c in rows[header_idx] if c is not None and str(c).strip()]

    if len(set(upper_texts)) < 2:  # 判据 ②：全行同值 → 是合并大标题，不是上级表头
        return False
    return set(upper_texts) != set(lower_texts)  # 判据 ③


def _build_columns(rows: list[list[Any]], header_idx: int, multi_level: bool) -> tuple[list[str], int]:
    """构造列名数组，并返回「表头起始行下标」。

    参数：
        rows (list[list[Any]]): 整表行数据
        header_idx (int): 已识别的表头行下标
        multi_level (bool): 是否多级表头
    返回：
        tuple[list[str], int]: (列名数组, 表头起始行下标)

    列名规范化：
        - 空列名 → 用「列{i}」（i 为 1 起列号）占位，避免下游出现无名列；
        - 重名列 → 追加「_2」「_3」后缀，保证列名唯一（pandas 需要唯一列名）；
        - 多级表头 → 「上级/下级」拼接，上级为空时仅用下级。
    """
    primary_idx = header_idx - 1 if multi_level else header_idx
    primary = rows[primary_idx]
    secondary = rows[header_idx]

    columns: list[str] = []
    for col_idx in range(len(secondary)):
        lower = "" if secondary[col_idx] is None else str(secondary[col_idx]).strip()
        if multi_level:
            upper = "" if col_idx >= len(primary) or primary[col_idx] is None else str(primary[col_idx]).strip()
            # 上级与下级相同（纵向合并单元格被填充成同值）时只保留一份，避免「销售/销售」
            name = f"{upper}/{lower}" if upper and lower and upper != lower else (lower or upper)
        else:
            name = lower
        if not name:
            name = f"列{col_idx + 1}"
        columns.append(name)

    # 去重：同名列追加序号后缀
    seen: dict[str, int] = {}
    unique_columns: list[str] = []
    for name in columns:
        if name in seen:
            seen[name] += 1
            unique_columns.append(f"{name}_{seen[name]}")
        else:
            seen[name] = 1
            unique_columns.append(name)
    return unique_columns, primary_idx


# ============================================================
# 八、矩阵规整：合并单元格展开 / 全空行列剔除 / 尾随空值裁剪
# ============================================================


def _expand_merged(
    matrix: list[list[Any]], merged_ranges: list[tuple[int, int, int, int]]
) -> int:
    """就地展开合并单元格（把左上角值填充到区域内每个格子）。

    参数：
        matrix (list[list[Any]]): 单元格矩阵（行 × 列，0 起下标）
        merged_ranges (list[tuple]): 合并区域列表，元素为 (min_row, min_col, max_row, max_col)，
                                     均为 **0 起下标、闭区间**
    返回：
        int: 实际生效（成功填充）的合并区域数

    说明（对应 F-4.2-5 / AC-4.2-4）：
        采用「向下/向右填充」策略，保证展开后：
          - 不出现因合并而产生的空列/空位（每个格子都有值）；
          - 分组维度（如「销售部」跨 3 行）在每一行都能取到，便于后续按行统计。
        区域若超出矩阵边界（openpyxl 偶尔给出越界维度）则跳过，不计入总数。
    """
    applied = 0
    for min_row, min_col, max_row, max_col in merged_ranges:
        if min_row >= len(matrix) or min_col >= len(matrix[0] if matrix else []):
            continue
        anchor = matrix[min_row][min_col] if min_col < len(matrix[min_row]) else None
        if anchor is None or (isinstance(anchor, str) and not anchor.strip()):
            # 左上角为空 → 该合并区域无信息可填，跳过（保持为缺失值）
            continue
        for row_idx in range(min_row, min(max_row + 1, len(matrix))):
            for col_idx in range(min_col, min(max_col + 1, len(matrix[row_idx]))):
                matrix[row_idx][col_idx] = anchor
        applied += 1
    return applied


def _col_letter_to_index(letters: str) -> int:
    """把 Excel 列字母转为 0 起下标（A→0，Z→25，AA→26）。

    参数：
        letters (str): 列字母（大小写均可）
    返回：
        int: 0 起列下标

    说明：
        本质是 26 进制转换（无 0 位的双射进制），逐位累加即可。
    """
    index = 0
    for char in letters.upper():
        index = index * 26 + (ord(char) - ord("A") + 1)
    return index - 1


def _parse_range_ref(ref: str) -> tuple[int, int, int, int] | None:
    """解析合并区域的单元格引用（如 "A1:C3"）为 0 起闭区间下标。

    参数：
        ref (str): 形如 "A1:C3" 的区域引用
    返回：
        tuple[int, int, int, int] | None: (min_row, min_col, max_row, max_col)；
                                          无法解析返回 None

    说明：
        用正则整体匹配，避免 `$A$1` 这类绝对引用形态或异常字符造成解析错位。
    """
    match = re.match(r"^\$?([A-Za-z]{1,3})\$?(\d+)(?::\$?([A-Za-z]{1,3})\$?(\d+))?$", ref.strip())
    if match is None:
        return None
    start_col, start_row, end_col, end_row = match.groups()
    if end_col is None or end_row is None:
        return None  # 单单元格「合并」无意义，直接忽略
    return (
        int(start_row) - 1,
        _col_letter_to_index(start_col),
        int(end_row) - 1,
        _col_letter_to_index(end_col),
    )


def read_merged_ranges(path: Path) -> dict[str, list[tuple[int, int, int, int]]]:
    """从 xlsx 包内直接读取各 Sheet 的合并单元格区域。

    参数：
        path (Path): xlsx 文件路径
    返回：
        dict[str, list[tuple]]: {Sheet 名: [(min_row, min_col, max_row, max_col), ...]}
                                下标均为 0 起、闭区间

    为什么不用 openpyxl 的 API（实测结论，务必保留此注释）
    ---------------------------------------------------
    openpyxl 3.1.5 的 `ReadOnlyWorksheet` **完全没有 merged_cells 属性**（已实测：
    `hasattr(ws, "merged_cells") is False`）。而正常模式（read_only=False）会把整份
    工作簿构建成 DOM，5000 万单元格级的文件会吃掉数 GB 内存，与「大文件不进内存」
    的解析策略冲突。
    因此这里**直接读取 OOXML 包内的合并区域定义**：
        xl/workbook.xml           → 按顺序列出各 Sheet 名与 r:id
        xl/_rels/workbook.xml.rels → r:id ↔ 工作表 XML 部件路径
        xl/worksheets/sheetN.xml   → `<mergeCells><mergeCell ref="A1:C3"/>...`
    用 ElementTree 的 iterparse **流式**扫描（每读完一个元素即 clear），
    内存占用与文件大小无关。

    异常：
        本函数内部吞掉所有解析异常并返回已收集到的部分结果（返回空 dict 亦可）。
        理由：合并区域只是「增强信息」，读不到时解析仍应继续（退化为不展开），
        不应因为一个可选的增强项让整份文件的解析失败。
    """
    import xml.etree.ElementTree as ET
    import zipfile

    ns_main = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    ns_rel = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
    ns_pkg_rel = "{http://schemas.openxmlformats.org/package/2006/relationships}"

    result: dict[str, list[tuple[int, int, int, int]]] = {}

    try:
        with zipfile.ZipFile(path) as archive:
            # ---- 第 1 步：工作表顺序与 r:id（workbook.xml） ----
            ordered: list[tuple[str, str]] = []  # [(sheet_name, relationship_id), ...]
            with archive.open("xl/workbook.xml") as fp:
                for _event, element in ET.iterparse(fp, events=("end",)):
                    if element.tag == f"{ns_main}sheet":
                        name = element.get("name") or ""
                        rel_id = element.get(ns_rel + "id") or ""
                        if name and rel_id:
                            ordered.append((name, rel_id))
                    element.clear()

            # ---- 第 2 步：r:id → 部件路径（workbook.xml.rels） ----
            target_by_rel: dict[str, str] = {}
            with archive.open("xl/_rels/workbook.xml.rels") as fp:
                for _event, element in ET.iterparse(fp, events=("end",)):
                    if element.tag == f"{ns_pkg_rel}Relationship":
                        rel_id = element.get("Id") or ""
                        target = element.get("Target") or ""
                        if rel_id and target:
                            target_by_rel[rel_id] = target
                    element.clear()

            # ---- 第 3 步：逐个工作表抽取 mergeCells ----
            for sheet_name, rel_id in ordered:
                target = target_by_rel.get(rel_id)
                if not target:
                    continue
                # Target 可能是 "worksheets/sheet1.xml"（相对 xl/）或 "/xl/worksheets/..."（绝对）
                part = target.lstrip("/")
                if not part.startswith("xl/"):
                    part = f"xl/{part}"
                if part not in archive.namelist():
                    continue

                ranges: list[tuple[int, int, int, int]] = []
                with archive.open(part) as fp:
                    for _event, element in ET.iterparse(fp, events=("end",)):
                        if element.tag == f"{ns_main}mergeCell":
                            parsed = _parse_range_ref(element.get("ref") or "")
                            if parsed is not None:
                                ranges.append(parsed)
                            element.clear()
                        elif element.tag == f"{ns_main}sheetData":
                            # 合并区域定义在 sheetData 之后；此处不提前退出，
                            # 因为 iterparse 仍需读完剩余字节才能释放（流式 IO 无回退）
                            element.clear()
                if ranges:
                    result[sheet_name] = ranges
    except (zipfile.BadZipFile, KeyError, ET.ParseError, OSError) as exc:
        # 合并区域属增强信息：读取失败不阻断解析，仅记录
        logger.warning("读取合并区域失败（将不展开合并单元格）：%s", exc)
        return result

    return result


def _pad_rows(matrix: list[list[Any]], width: int) -> list[list[Any]]:
    """把所有行补齐到统一宽度（不足处补 None）。

    参数：
        matrix (list[list[Any]]): 单元格矩阵
        width (int): 目标列数
    返回：
        list[list[Any]]: 补齐后的矩阵

    说明：
        openpyxl 在 read_only 模式下按「本行最后一个非空单元格」截断行，
        列数不齐；不补齐会导致按列取值时索引错位（AC-4.2-4「不出现错位」）。
    """
    for row in matrix:
        if len(row) < width:
            row.extend([None] * (width - len(row)))
    return matrix


def _drop_empty_axis(matrix: list[list[Any]]) -> tuple[list[list[Any]], int, int]:
    """剔除全空行与全空列，并裁掉尾部空值。

    参数：
        matrix (list[list[Any]]): 单元格矩阵
    返回：
        tuple[list[list[Any]], int, int]: (规整后矩阵, 剔除的空行数, 剔除的空列数)

    说明：
        真实业务表常在右侧/下方带一大片空单元格（Excel 保存时留下的格式区域），
        若保留会：① 行数列数虚高；② 出现 100% 缺失的「列N」字段扰乱字段说明。
        故统一剔除，并把剔除数量写进 header_note 让用户知情。
    """
    if not matrix:
        return matrix, 0, 0

    total_rows = len(matrix)
    total_cols = max(len(r) for r in matrix)

    kept_rows = [row for row in matrix if not _is_blank_row(row)]
    dropped_rows = total_rows - len(kept_rows)

    if not kept_rows:
        return [], dropped_rows, 0

    # 判定哪些列非全空
    kept_col_indexes = [
        col_idx
        for col_idx in range(total_cols)
        if any(
            row[col_idx] is not None and str(row[col_idx]).strip() != ""
            for row in kept_rows
            if col_idx < len(row)
        )
    ]
    dropped_cols = total_cols - len(kept_col_indexes)

    if not kept_col_indexes:
        return [], dropped_rows, dropped_cols

    narrowed = [[row[col_idx] if col_idx < len(row) else None for col_idx in kept_col_indexes] for row in kept_rows]
    return narrowed, dropped_rows, dropped_cols


# ============================================================
# 九、CSV 副本读写
# ============================================================


def _stringify(value: Any) -> str:
    """把单元格值转为 CSV 文本（缺失值统一为空串）。

    参数：
        value (Any): 单元格原始值
    返回：
        str: 文本形态

    约定：
        None → ""（空）；布尔 → "True"/"False"（pandas 可正确识别为 bool）；
        日期时间 → ISO 8601（可被 pandas.to_datetime 直接解析）；其余 → str()。
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, dt.datetime | dt.date | dt.time):
        return value.isoformat(sep=" ") if isinstance(value, dt.datetime) else value.isoformat()
    if isinstance(value, float):
        # 去掉无意义的 .0（Excel 里 100.0 与 100 视觉一致，裸写会让 CSV 变噪声）
        if value.is_integer():
            return str(int(value))
        return repr(value)
    return str(value)


def write_csv_copy(dest: Path, columns: list[str], rows: list[list[Any]]) -> Path:
    """把解析结果写为 UTF-8 CSV 副本（供预览与 M3 沙箱复用）。

    参数：
        dest (Path): 目标路径（由 storage.build_parsed_sheet_path 生成）
        columns (list[str]): 列名
        rows (list[list[Any]]): 数据行
    返回：
        Path: 实际写入路径
    异常：
        OSError: 目录不可写

    说明：
        - 用 csv 模块的标准引用规则（QUOTE_MINIMAL），字段内含逗号/引号/换行时自动加引号；
        - 换行统一为 "\\n"（lineterminator 显式指定），避免 Windows 下产生 "\\r\\n\\r\\n"；
        - 编码固定 UTF-8（无 BOM），M3 沙箱内 pandas.read_csv(path) 默认可用。
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("w", encoding="utf-8", newline="") as fp:
        writer = csv.writer(fp, lineterminator="\n")
        writer.writerow(columns)
        for row in rows:
            writer.writerow([_stringify(cell) for cell in row])
    return dest


def _read_csv_rows(csv_path: Path) -> tuple[list[str], list[list[str | None]]]:
    """读取 CSV 副本（UTF-8），空串还原为 None。

    参数：
        csv_path (Path): CSV 副本路径
    返回：
        tuple[list[str], list[list[str | None]]]: (列名, 数据行)
    异常：
        ParseFailure: 副本缺失或不完整（通常意味着解析产物被清理，需重新上传）

    说明：
        预览值统一以「字符串 / None」下发：
          - 缺失语义明确（空串 → None → 前端「（缺失）」灰字）；
          - 不做事后数值嗅探，避免「工号 007」被还原成 7 这类截断错误。
        数值/日期等类型信息由 F6 字段说明（dtype 列）承载，二者职责分离。
    """
    if not csv_path.exists():
        raise ParseFailure("解析产物已被清理，请重新上传文件")
    with csv_path.open("r", encoding="utf-8", newline="") as fp:
        reader = csv.reader(fp)
        try:
            columns = next(reader)
        except StopIteration:
            return [], []
        rows: list[list[str | None]] = []
        for raw in reader:
            rows.append([None if cell == "" else cell for cell in raw])
    return columns, rows


def read_preview(csv_path: Path, page: int, per_page: int) -> tuple[list[str], list[list[str | None]], int]:
    """按页读取 CSV 副本（F7 数据预览）。

    参数：
        csv_path (Path): CSV 副本路径
        page (int): 页码（1 起）
        per_page (int): 每页行数
    返回：
        tuple[list[str], list[list[str | None]], int]: (列名, 当页行, 总行数)
    异常：
        ParseFailure: 副本缺失

    说明：
        逐行流式读取（itertools.islice 略过前若干行），不把整表载入内存，
        保证大表（20 万行）预览依然秒回——技术文档 3.6「大文件性能」要求。
    """
    if not csv_path.exists():
        raise ParseFailure("解析产物已被清理，请重新上传文件")
    skip = max(page - 1, 0) * per_page
    columns: list[str] = []
    page_rows: list[list[str | None]] = []
    total = 0
    with csv_path.open("r", encoding="utf-8", newline="") as fp:
        reader = csv.reader(fp)
        try:
            columns = next(reader)
        except StopIteration:
            return [], [], 0
        for index, raw in enumerate(reader):
            total += 1
            if index < skip or len(page_rows) >= per_page:
                continue  # 跳过前页；超出本页容量后仅继续计数
            page_rows.append([None if cell == "" else cell for cell in raw])
    return columns, page_rows, total


# ============================================================
# 十、字段画像与质量统计
# ============================================================


def _profile_field(name: str, column_values: list[Any], row_count: int) -> FieldProfile:
    """生成单个字段的画像（类型/示例/缺失率/敏感标记）。

    参数：
        name (str): 字段名
        column_values (list[Any]): 该列全部取值（含缺失）
        row_count (int): 数据行数（用于计算缺失率）
    返回：
        FieldProfile: 字段画像

    步骤：
        1. 统计缺失数 → 缺失率；
        2. 用非空值推断类型与依据；
        3. 采集最多 3 个示例值（去重，保留出现顺序，便于人工核对）；
        4. 敏感识别（字段名关键词优先 → 值形态兜底），命中则对示例值脱敏。
    """
    missing = sum(1 for v in column_values if v is None or (isinstance(v, str) and not v.strip()))
    missing_rate = (missing / row_count) if row_count else 0.0

    non_empty = [v for v in column_values if v is not None and not (isinstance(v, str) and not v.strip())]

    dtype, basis = infer_dtype(non_empty)

    # 示例值：去重后取前 SAMPLE_SIZE 个（保留原值文本，便于判断业务含义）
    samples: list[str] = []
    seen: set[str] = set()
    for value in non_empty:
        text = _stringify(value)
        if text in seen:
            continue
        seen.add(text)
        samples.append(text)
        if len(samples) >= SAMPLE_SIZE:
            break

    # ---- 敏感识别：字段名关键词优先（业务命名约定），值形态兜底 ----
    kind = _match_name_kind(name)
    if kind is None:
        kind = _match_value_kind([_stringify(v) for v in non_empty], name)
    if kind is not None:
        samples = [mask_value(text, kind) for text in samples]

        # 标识类字段强制按文本处理（重要，避免下游统计口径出错）：
        # 手机号/身份证/银行卡即使全是数字，也不是「可求和的度量」——
        # 若推断为整数，M3 分析期可能对它们做均值/求和，得出无意义的结论。
        # 邮箱本身是文本，无需处理。
        if kind in {"手机号", "身份证", "银行卡"}:
            dtype = "字符串"
            basis = f"敏感标识字段（{kind}），按文本处理"

    return FieldProfile(
        name=name,
        dtype=dtype,
        samples=samples,
        missing_rate=round(missing_rate, 4),
        infer_basis=basis,
        sensitive=kind is not None,
        sensitive_kind=kind,
    )


def build_sheet_note(
    header_row_no: int,
    multi_level: bool,
    merged_cells: int,
    dropped_rows: int,
    dropped_cols: int,
    extra_notes: list[str],
) -> str:
    """拼装「解析处理信息行」文案（UI/UX 6.3 区块 02）。

    参数：
        header_row_no (int): 表头原始行号（1 起）
        multi_level (bool): 是否多级表头
        merged_cells (int): 展开的合并区域数
        dropped_rows (int): 剔除的全空行数
        dropped_cols (int): 剔除的全空列数
        extra_notes (list[str]): 额外说明（如 CSV 编码、容错提示）
    返回：
        str: 以「；」分隔的说明文案；无任何处理时返回「未做特殊处理」

    设计说明（评审重点）：
        本函数是**纯函数**，且其全部入参都会被持久化到 files.quality_json 的
        sheet_meta 中。因此 F5 读取时可用同一函数**原样复现**该文案，
        无需为「解析处理信息」新增数据库列（不改动已冻结的建表脚本）。
    """
    notes: list[str] = []

    if header_row_no == 1:
        notes.append("表头位于第 1 行")
    else:
        notes.append(f"表头识别在第 {header_row_no} 行，已跳过前 {header_row_no - 1} 行说明性内容")

    if multi_level:
        notes.append("检测到多级表头，已按「上级/下级」合并为单级列名")

    if merged_cells:
        notes.append(f"合并单元格已向下填充展开（共 {merged_cells} 个区域）")

    if dropped_rows or dropped_cols:
        notes.append(f"已忽略 {dropped_rows} 个全空行、{dropped_cols} 个全空列")

    notes.extend(extra_notes)
    return "；".join(notes) if notes else "未做特殊处理"


# ============================================================
# 十一、单 Sheet / 单文件解析主流程
# ============================================================


def _parse_matrix(
    matrix: list[list[Any]],
    merged_ranges: list[tuple[int, int, int, int]],
    sheet_index: int,
    sheet_name: str,
    file_id: int,
    extra_notes: list[str],
) -> SheetParseResult:
    """把「原始单元格矩阵」规整为 SheetParseResult（xlsx 与 csv 共用）。

    参数：
        matrix (list[list[Any]]): 原始行数据（可能列数不齐）
        merged_ranges (list[tuple]): 合并区域（csv 传空列表）
        sheet_index (int): Sheet 索引
        sheet_name (str): Sheet 名
        file_id (int): 文件 ID（用于推导 CSV 副本路径）
        extra_notes (list[str]): 额外说明（编码/公式等）
    返回：
        SheetParseResult: 解析结果（含 CSV 副本已落盘）
    异常：
        ParseFailure: 无有效数据 / 超出行列上限
    """
    # ---- 0. 基础上限校验（在展开与规整之前做，避免超大表把内存吃光） ----
    if len(matrix) > MAX_ROW_LIMIT:
        raise ParseFailure(f"超出行列上限（单表最大 {MAX_ROW_LIMIT} 行）")
    width = max((len(r) for r in matrix), default=0)
    if width > MAX_COL_LIMIT:
        raise ParseFailure(f"超出行列上限（单表最大 {MAX_COL_LIMIT} 列）")

    # ---- 1. 补齐列宽 → 展开合并单元格（顺序重要：先补齐才不会漏填） ----
    matrix = _pad_rows(matrix, width)
    merged_applied = _expand_merged(matrix, merged_ranges)

    # ---- 2. 剔除全空行/列 ----
    matrix, dropped_rows, dropped_cols = _drop_empty_axis(matrix)

    if not matrix:
        raise ParseFailure("未读取到任何有效数据")

    # ---- 3. 表头识别（含多级表头降级） ----
    header_idx = _detect_header_index(matrix)
    multi_level = _is_multi_level_header(matrix, header_idx)
    columns, header_start = _build_columns(matrix, header_idx, multi_level)

    # ---- 4. 切出数据区 ----
    data_rows = matrix[header_idx + 1 :]
    if not data_rows:
        raise ParseFailure("未读取到任何有效数据")

    # 数据区若仍含全空行（表头下方空行），前面已剔除；此处做一次防御性过滤
    data_rows = [row for row in data_rows if not _is_blank_row(row)]
    if not data_rows:
        raise ParseFailure("未读取到任何有效数据")

    col_count = len(columns)
    row_count = len(data_rows)

    # ---- 5. 字段画像 ----
    fields = [
        _profile_field(
            columns[col_idx],
            [row[col_idx] if col_idx < len(row) else None for row in data_rows],
            row_count,
        )
        for col_idx in range(col_count)
    ]

    missing_cells = sum(1 for row in data_rows for cell in row if cell is None or (isinstance(cell, str) and not cell.strip()))

    # ---- 6. 落 CSV 副本（F7 预览与 M3 沙箱的共同数据源） ----
    csv_path = write_csv_copy(
        storage.build_parsed_sheet_path(file_id, sheet_index),
        columns,
        data_rows,
    )

    header_note = build_sheet_note(
        header_row_no=header_start + 1,
        multi_level=multi_level,
        merged_cells=merged_applied,
        dropped_rows=dropped_rows,
        dropped_cols=dropped_cols,
        extra_notes=extra_notes,
    )

    return SheetParseResult(
        index=sheet_index,
        name=sheet_name,
        row_count=row_count,
        col_count=col_count,
        header_row=header_start + 1,
        header_note=header_note,
        fields=fields,
        csv_path=csv_path,
        missing_cells=missing_cells,
        merged_cells=merged_applied,
        multi_level=multi_level,
        dropped_rows=dropped_rows,
        dropped_cols=dropped_cols,
        extra_notes=list(extra_notes),
    )


def _parse_xlsx(path: Path, file_id: int, on_progress: ProgressCallback | None) -> list[SheetParseResult]:
    """解析 xlsx（多 Sheet）。

    参数：
        path (Path): 上传原件路径
        file_id (int): 文件 ID
        on_progress (ProgressCallback | None): 进度回调
    返回：
        list[SheetParseResult]: 每个非空 Sheet 的结果（**按原始顺序**，index 连续）
    异常：
        ParseFailure: 文件损坏 / 加密受保护 / 无有效工作表 / 超出行列上限

    说明：
        用 `read_only=True` 流式读取（内存占用与文件大小解耦），
        `data_only=True` 取公式的**缓存计算值**（openpyxl 本身不计算公式；
        若文件从未被 Excel 打开过，缓存值为 None，此时在 header_note 中提示）。
    """
    import openpyxl
    from openpyxl.utils.exceptions import InvalidFileException

    # 加密的 OOXML 实为 OLE/CFB 复合文档（magic: D0 CF 11 E0），openpyxl 会报 zip 错误。
    # 提前嗅探可给出「文件已加密受保护」这一准确原因，而不是笼统的「文件损坏」。
    try:
        with path.open("rb") as probe:
            magic = probe.read(8)
        if magic.startswith(b"\xd0\xcf\x11\xe0"):
            raise ParseFailure("文件已加密受保护，请先取消密码后重新上传")
    except OSError as exc:
        raise ParseFailure("文件损坏，无法读取") from exc

    try:
        workbook = openpyxl.load_workbook(filename=str(path), read_only=True, data_only=True)
    except (InvalidFileException, KeyError) as exc:
        raise ParseFailure("文件损坏，无法读取") from exc
    except Exception as exc:  # noqa: BLE001 —— 第三方库异常类型庞杂，统一翻译为可读原因
        # zipfile.BadZipFile / OSError / ValueError 等均归入「损坏」
        logger.warning("xlsx 打开失败：file_id=%s err=%s", file_id, exc)
        raise ParseFailure("文件损坏，无法读取") from exc

    # 合并区域单独从 OOXML 包内读取（openpyxl 只读模式不提供该信息，见 read_merged_ranges 注释）
    merged_by_sheet = read_merged_ranges(path)

    try:
        sheet_names = list(workbook.sheetnames)
        if not sheet_names:
            raise ParseFailure("无有效工作表")

        results: list[SheetParseResult] = []
        total = len(sheet_names)
        for order, raw_name in enumerate(sheet_names):
            worksheet = workbook[raw_name]

            # 逐行取值（read_only 模式为生成器，一次性物化；上限校验在 _parse_matrix 内）
            matrix = [list(row) for row in worksheet.iter_rows(values_only=True)]

            merged_ranges = merged_by_sheet.get(str(raw_name), [])

            try:
                result = _parse_matrix(
                    matrix=matrix,
                    merged_ranges=merged_ranges,
                    # index 取「已成功解析的 Sheet 数」，保证跳过空 Sheet 后索引连续，
                    # 与 CSV 副本文件名 sheet_{index}.csv 一一对应。
                    sheet_index=len(results),
                    sheet_name=str(raw_name),
                    file_id=file_id,
                    extra_notes=[],
                )
            except ParseFailure as exc:
                # 单个 Sheet 为空不算整体失败：多 Sheet 文件里出现空表很常见
                logger.info("跳过空/无效 Sheet：file_id=%s sheet=%s 原因=%s", file_id, raw_name, exc.reason)
                _report_progress(on_progress, order + 1, total, str(raw_name))
                continue

            results.append(result)
            _report_progress(on_progress, order + 1, total, str(raw_name))

        if not results:
            raise ParseFailure("未读取到任何有效数据")
        return results
    finally:
        workbook.close()


def _parse_csv(
    path: Path, file_id: int, original_name: str, on_progress: ProgressCallback | None
) -> tuple[list[SheetParseResult], str, str | None]:
    """解析 csv（单 Sheet）。

    参数：
        path (Path): 上传原件路径
        file_id (int): 文件 ID
        original_name (str): 原始文件名（用于取 Sheet 名）
        on_progress (ProgressCallback | None): 进度回调
    返回：
        tuple[list[SheetParseResult], str, str | None]: (Sheet 结果列表, 识别出的编码, 提示信息)
    异常：
        ParseFailure: 无有效数据 / 超出行列上限

    说明：
        - 编码探测只采样文件头 64KB（AC-4.2-6），避免为探测把大文件读两遍；
        - 分隔符固定按逗号（本期不支持 TSV；若未来需要，可在此扩展 sniffer）。
    """
    try:
        with path.open("rb") as fp:
            sample = fp.read(ENCODING_SAMPLE_BYTES)
    except OSError as exc:
        raise ParseFailure("文件损坏，无法读取") from exc

    if not sample:
        raise ParseFailure("未读取到任何有效数据")

    encoding, warning = detect_csv_encoding(sample)

    try:
        with path.open("r", encoding=encoding, newline="", errors="replace") as fp:
            reader = csv.reader(fp)
            matrix = [list(row) for row in reader]
    except OSError as exc:
        raise ParseFailure("文件损坏，无法读取") from exc
    except UnicodeError as exc:
        logger.warning("csv 解码失败：file_id=%s encoding=%s err=%s", file_id, encoding, exc)
        raise ParseFailure("文件编码异常，无法解析，请另存为 UTF-8 后重试") from exc

    # Sheet 名取原始文件名主干（与 Excel 中打开 CSV 时的呈现一致）
    sheet_name = Path(original_name).stem or "数据"

    # 说明文案：正常识别时报告编码名；容错读取时只报问题（避免出现
    # 「编码自动识别为 UTF-8；编码无法识别」这种自相矛盾的组合）
    if warning:
        extra_notes = [warning]
    else:
        extra_notes = [f"编码自动识别为 {encoding.upper()}"]

    result = _parse_matrix(
        matrix=matrix,
        merged_ranges=[],  # CSV 无合并单元格概念
        sheet_index=0,
        sheet_name=sheet_name,
        file_id=file_id,
        extra_notes=extra_notes,
    )
    _report_progress(on_progress, 1, 1, sheet_name)
    return [result], encoding, warning


def _report_progress(
    on_progress: ProgressCallback | None, done: int, total: int, sheet_name: str
) -> None:
    """上报解析进度（技术文档 3.6「按 Sheet 粒度更新 parse_progress」）。

    参数：
        on_progress (ProgressCallback | None): 回调；为 None 时本函数为空操作
        done (int): 已完成的 Sheet 数
        total (int): 总 Sheet 数
        sheet_name (str): 刚完成的 Sheet 名（用于生成阶段文字）
    返回：无
    说明：
        进度按 Sheet 粒度线性映射到 5%~95%（0~5% 留给「已受理/读盘」，
        95%~100% 留给「写库与收尾」），使前端进度条不会长期卡在 100%。
    """
    if on_progress is None or total <= 0:
        return
    percent = int(5 + (done / total) * 90)
    on_progress(min(percent, 95), f"正在解析第 {done}/{total} 个 Sheet（{sheet_name}）")


def parse_file(
    path: Path,
    fmt: str,
    original_name: str,
    file_id: int,
    on_progress: ProgressCallback | None = None,
) -> ParseOutcome:
    """解析上传原件，产出结构化结果与 CSV 副本（M2 解析总入口）。

    参数：
        path (Path): 上传原件落盘路径
        fmt (str): 格式标识（xlsx / csv）
        original_name (str): 原始文件名
        file_id (int): 文件 ID（用于推导 CSV 副本目录）
        on_progress (ProgressCallback | None): 进度回调，可为 None
    返回：
        ParseOutcome: 含全部 Sheet 结果、识别编码与质量聚合指标
    异常：
        ParseFailure: 解析失败（携带可读原因枚举）

    质量聚合口径（F3 quality 五项）：
        row_total           全部 Sheet 数据行之和
        col_total           全部 Sheet 列数之和
        missing_cells       全部 Sheet 缺失单元格之和
        high_missing_fields 缺失率 ≥ HIGH_MISSING_THRESHOLD 的字段数
        merged_cells        展开的合并区域总数
    """
    fmt_normalized = (fmt or "").lower()
    if fmt_normalized not in SUPPORTED_FORMATS:
        raise ParseFailure("文件格式不受支持")

    # 重新解析前清空旧产物，避免上一次的 sheet_2.csv 残留被误读
    storage.remove_parsed_dir(file_id)

    if on_progress is not None:
        on_progress(2, "正在读取文件")

    if fmt_normalized == "csv":
        sheets, encoding, _warning = _parse_csv(path, file_id, original_name, on_progress)
    else:
        sheets = _parse_xlsx(path, file_id, on_progress)
        encoding = None

    all_fields = [f for sheet in sheets for f in sheet.fields]
    outcome = ParseOutcome(
        sheets=sheets,
        encoding=encoding,
        row_total=sum(s.row_count for s in sheets),
        col_total=sum(s.col_count for s in sheets),
        missing_cells=sum(s.missing_cells for s in sheets),
        high_missing_fields=sum(1 for f in all_fields if f.missing_rate >= HIGH_MISSING_THRESHOLD),
        merged_cells=sum(s.merged_cells for s in sheets),
    )

    if on_progress is not None:
        on_progress(96, "正在整理解析结果")

    logger.info(
        "解析完成：file_id=%s format=%s sheets=%d rows=%d cols=%d missing=%d high_missing=%d merged=%d",
        file_id, fmt_normalized, len(sheets), outcome.row_total,
        outcome.col_total, outcome.missing_cells, outcome.high_missing_fields, outcome.merged_cells,
    )
    return outcome


# ============================================================
# 十二、上传校验辅助（供 file_service 复用，保持校验口径单一）
# ============================================================


def detect_format(filename: str) -> str | None:
    """从文件名推断格式标识。

    参数：
        filename (str): 原始文件名
    返回：
        str | None: "xlsx" / "csv"；不在白名单时返回 None
    说明：
        仅按**扩展名**判定（技术文档 5.5 F1「扩展名/MIME（仅 .xlsx/.csv）」）。
        上传接口另做 MIME 交叉校验，二者在此与 api 层分别实现，职责不重叠。
    """
    suffix = Path(filename or "").suffix.lower().lstrip(".")
    return suffix if suffix in SUPPORTED_FORMATS else None
