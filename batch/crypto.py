"""敏感字段的加密存储与脱敏显示（设计文档第七节）。

客户身份证号、手机号属于敏感个人信息，本模块提供两条硬边界：

1. **加密存储**：身份证号在 SQLite 里以 ``enc:`` 前缀的 Fernet 密文保存
   （密钥在 ``config/.id_card.key``，首次使用自动生成，已在 .gitignore 中）
2. **脱敏显示**：界面/导出只显示前 6 后 4（``110101********1234``），
   ``status_log`` 里**不记录身份证号**，只记 applicant id

未安装 cryptography 时 :func:`encrypt` 会给出明确报错（附安装命令），
不会静默把明文写进数据库。
"""
from __future__ import annotations

import os
from typing import Optional

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEFAULT_KEY_PATH = os.path.join(_PROJECT_ROOT, "config", ".id_card.key")

#: 密文前缀 —— 用于区分「已加密」与「历史明文」。
ENC_PREFIX = "enc:"

_MASK_KEEP_HEAD = 6
_MASK_KEEP_TAIL = 4


class CryptoUnavailableError(RuntimeError):
    """未安装 cryptography，无法加密存储敏感字段。"""


def _key_path() -> str:
    return os.environ.get("FORMPILOT_ID_KEY", _DEFAULT_KEY_PATH)


def _load_or_create_key(path: Optional[str] = None) -> bytes:
    """读取 Fernet 密钥；不存在则生成并落盘（权限尽量收紧）。"""
    path = path or _key_path()
    if os.path.exists(path):
        with open(path, "rb") as f:
            return f.read().strip()

    try:
        from cryptography.fernet import Fernet
    except ImportError as e:  # pragma: no cover - 取决于环境
        raise CryptoUnavailableError(
            "未安装 cryptography，无法加密存储身份证号。"
            "请执行：pip install cryptography"
        ) from e

    key = Fernet.generate_key()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(key)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass  # Windows 下可能不支持，忽略
    return key


def _fernet(path: Optional[str] = None):
    try:
        from cryptography.fernet import Fernet
    except ImportError as e:  # pragma: no cover - 取决于环境
        raise CryptoUnavailableError(
            "未安装 cryptography，无法加解密身份证号。"
            "请执行：pip install cryptography"
        ) from e
    return Fernet(_load_or_create_key(path))


def is_encrypted(value: str) -> bool:
    """值是否已是本模块产出的密文。"""
    return bool(value) and str(value).startswith(ENC_PREFIX)


def encrypt(value: str, key_path: Optional[str] = None) -> str:
    """加密一个敏感字段值；空值原样返回。

    已加密的值重复调用不会二次加密。
    """
    if value is None:
        return ""
    text = str(value)
    if not text:
        return ""
    if is_encrypted(text):
        return text
    token = _fernet(key_path).encrypt(text.encode("utf-8")).decode("ascii")
    return ENC_PREFIX + token


def decrypt(value: str, key_path: Optional[str] = None) -> str:
    """解密字段值。历史明文（无 ``enc:`` 前缀）原样返回，兼容旧库。"""
    if not value:
        return ""
    text = str(value)
    if not is_encrypted(text):
        return text
    try:
        return _fernet(key_path).decrypt(
            text[len(ENC_PREFIX):].encode("ascii")).decode("utf-8")
    except CryptoUnavailableError:
        raise
    except Exception:  # noqa: BLE001 — 密钥丢失/损坏时不应让程序崩溃
        return ""


def mask_id_card(value: str, head: int = _MASK_KEEP_HEAD,
                 tail: int = _MASK_KEEP_TAIL) -> str:
    """脱敏显示：保留前 6 后 4，中间打码（不足长度则整体打码）。"""
    if not value:
        return ""
    text = str(value)
    if len(text) <= head + tail:
        return "*" * len(text)
    return f"{text[:head]}{'*' * (len(text) - head - tail)}{text[-tail:]}"


def mask_phone(value: str) -> str:
    """手机号脱敏：138****8000。"""
    if not value:
        return ""
    text = str(value)
    if len(text) < 7:
        return "*" * len(text)
    return f"{text[:3]}****{text[-4:]}"


def is_masked(value: str) -> bool:
    """值是否已脱敏（含打码字符 ``*``）。

    注意：**不能**用 SQL 在密文列上判断——Fernet 密文是 URL-safe base64，
    本身就可能含 ``-``/``_``，必须解密后再判断。
    """
    return "*" in str(value or "")


def has_crypto() -> bool:
    """当前环境是否具备加密能力（用于界面提示）。"""
    try:
        import cryptography  # noqa: F401
        return True
    except ImportError:
        return False
