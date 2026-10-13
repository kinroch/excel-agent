# -*- coding: utf-8 -*-
"""
业务错误码与统一异常模块（依据技术文档 5.3「错误码段规划」与附录 A「错误码全表」）

功能说明
--------
1. 以「错误码段 + 编号」形式集中登记全部业务错误码，并绑定其 HTTP 状态与用户可读文案；
2. 提供统一业务异常 `BizError`：业务层只需 `raise BizError(2001)` 或
   `raise BizError(2004, "工作表为空")`，由 main.py 的异常处理器转换为统一响应体，
   从而杜绝各处手写 HTTP 状态码造成的口径不一致。

错误码段位（技术文档 5.3）
--------------------------
    1000~1099  认证与权限
    2000~2099  文件与解析
    3000~3099  会话
    4000~4099  对话分析
    5000~5099  报告
    6000~6099  管理后台
    9000+      系统级

异常说明
--------
- BizError 被抛出后必定被全局处理器捕获，不会穿透到 FastAPI 默认处理器；
- 若业务需要覆盖默认文案，在构造 BizError 时传入 message 即可（例如夹带具体失败原因）。
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ErrorDef:
    """单个错误码的定义。

    属性：
        code (int): 业务错误码（响应体 code 字段）
        http_status (int): 对应的 HTTP 状态码
        message (str): 默认用户可读文案（前端直接展示）
    """

    code: int
    http_status: int
    message: str


# ============================================================
# 错误码全表（技术文档 附录 A，逐行对应，禁止在别处另造错误码）
# ============================================================
ERRORS: dict[int, ErrorDef] = {
    # ---- 通用成功 ----
    0: ErrorDef(0, 200, "ok"),
    # ---- 1000 段：认证与权限 ----
    1001: ErrorDef(1001, 400, "该账号已注册，请直接登录"),
    1002: ErrorDef(1002, 400, "密码强度不足，需 8 位以上且同时包含字母与数字"),
    1003: ErrorDef(1003, 401, "账号或密码错误"),
    1004: ErrorDef(1004, 403, "账号已停用，请联系管理员"),
    1403: ErrorDef(1403, 403, "无权限访问"),
    # ---- 2000 段：文件与解析 ----
    2001: ErrorDef(2001, 400, "不支持的文件格式，仅支持 xlsx、csv"),
    2002: ErrorDef(2002, 400, "文件超出大小上限（50MB）"),
    2003: ErrorDef(2003, 400, "空文件，未读取到任何内容"),
    2004: ErrorDef(2004, 422, "文件解析失败"),
    2005: ErrorDef(2005, 403, "无权操作该文件"),
    # ---- 3000 段：会话 ----
    3001: ErrorDef(3001, 404, "会话不存在或已删除"),
    3002: ErrorDef(3002, 409, "会话处理中，请稍候"),
    3003: ErrorDef(3003, 429, "已有进行中的分析任务，请等待完成"),
    # ---- 4000 段：对话分析 ----
    4001: ErrorDef(4001, 400, "输入命中敏感词已阻断，请调整表述后重新提交"),
    4002: ErrorDef(4002, 400, "当前会话暂无可分析的数据文件"),
    4003: ErrorDef(4003, 400, "澄清回答不完整"),
    4004: ErrorDef(4004, 500, "分析执行重试超限失败"),
    4005: ErrorDef(4005, 400, "当前数据不支持该图表类型"),
    # ---- 5000 段：报告 ----
    5001: ErrorDef(5001, 404, "报告不存在"),
    5002: ErrorDef(5002, 500, "导出失败"),
    5003: ErrorDef(5003, 409, "任务尚未成功，无法生成报告"),
    # ---- 6000 段：管理后台 ----
    6001: ErrorDef(6001, 404, "目标用户不存在"),
    6002: ErrorDef(6002, 403, "不允许操作管理员账号"),
    # ---- 9000 段：系统 ----
    9000: ErrorDef(9000, 500, "系统内部错误"),
    9001: ErrorDef(9001, 502, "大模型服务暂不可用，已自动重试"),
    9002: ErrorDef(9002, 500, "分析执行环境异常"),
}


def get_error(code: int) -> ErrorDef:
    """按错误码取定义；未登记的码回退为 9000 系统内部错误。

    参数：
        code (int): 业务错误码
    返回：
        ErrorDef：错误码定义对象
    """
    return ERRORS.get(code, ERRORS[9000])


class BizError(Exception):
    """统一业务异常。

    用法：
        raise BizError(3001)                     # 使用错误码表默认文案
        raise BizError(2004, "第 3 个工作表为空")  # 覆盖文案，补充具体原因

    参数：
        code (int): 业务错误码，必须在 ERRORS 中登记
        message (str | None): 自定义文案；为 None 时取错误码表默认文案
    属性：
        code (int)、message (str)、http_status (int)
    """

    def __init__(self, code: int, message: str | None = None) -> None:
        definition = get_error(code)
        self.code: int = definition.code
        self.http_status: int = definition.http_status
        self.message: str = message or definition.message
        super().__init__(self.message)
