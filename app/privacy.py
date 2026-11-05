# -*- coding: utf-8 -*-
"""脱敏工具：日志里不该出现完整发票号、手机号、身份证号。

原则：
1. 只脱敏"落盘/可被管理员批量查看"的地方（agent_logs、审计导出）；
2. 用户自己看到的对话内容不脱敏（他要核对自己填的号码）；
3. 脱敏是单向的——写进日志就不可逆，所以必须在写之前做，不能靠展示层过滤。
"""

from __future__ import annotations

import re
from typing import Any

# 发票号保留前 4 位：既能人工比对"是不是同一张票"，又不泄露完整号码
_INVOICE_KEEP = 4
_INVOICE_KEYS = {"invoice_no", "invoice_number", "invoiceNo", "发票号"}

_PHONE_RE = re.compile(r"(?<!\d)(1[3-9]\d)\d{4}(\d{4})(?!\d)")
_ID_CARD_RE = re.compile(r"(?<!\w)(\d{6})\d{8}(\d{4})(?!\w)")


def mask_invoice_no(value: Any) -> str:
    """发票号脱敏：保留前 4 位，其余用 **** 代替。"""
    text = str(value).strip()
    if len(text) <= _INVOICE_KEEP + 2:
        return text
    return text[:_INVOICE_KEEP] + "****"


def mask_text(text: str) -> str:
    """对自由文本里的手机号、身份证号做二次打码。"""
    if not text:
        return text
    text = _PHONE_RE.sub(r"\1****\2", text)
    text = _ID_CARD_RE.sub(r"\1********\2", text)
    return text


def mask_args(obj: Any) -> Any:
    """递归脱敏工具入参：按 key 命中发票号，其余字符串过手机号/身份证正则。"""
    if isinstance(obj, dict):
        masked = {}
        for key, value in obj.items():
            if key in _INVOICE_KEYS or (isinstance(key, str) and "invoice" in key.lower()):
                masked[key] = mask_invoice_no(value)
            else:
                masked[key] = mask_args(value)
        return masked
    if isinstance(obj, (list, tuple)):
        return [mask_args(item) for item in obj]
    if isinstance(obj, str):
        return mask_text(obj)
    return obj
