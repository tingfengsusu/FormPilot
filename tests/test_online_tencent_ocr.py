"""腾讯云 OCR 在线测试（真实 API 调用）。

覆盖：
  1. 身份证识别（strict 模式）：真实调用腾讯云，断言识别结果与合成测试图一致
  2. IdCardOCR 来源完整链路：返回真实数据而不是 Mock 回退
     —— 顺便守住历史上的 base64 编码 bug（编码错 → 调用失败 → 回退 Mock
     并带 `_fallback_reason`，本用例会捕捉到）

跳过条件（环境问题不计失败）：未配置密钥 / 未装 SDK 或 Pillow /
服务未开通 / 资源包耗尽 / 网络不可用。

注意：每次运行消耗 1-2 次腾讯云调用额度（有每月免费额度）。
不想跑在线用例时：pytest tests/ -q -k "not online"
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _helpers import skip_test

_EXPECTED_NAME = "张伟"
_EXPECTED_ID = "410305201509011235"

#: 环境类问题标记（命中则跳过而不是失败）
_ENV_MARKERS = (
    "UnOpenError",            # 服务未开通
    "ResourcePackageRunOut",  # 资源包耗尽
    "AuthFailure",            # 鉴权失败
    "RequestLimitExceeded",   # 超频
    "No module named",        # 依赖缺失
    "connect", "Connect", "timeout", "Timeout", "Network",
)


def _is_env_issue(message: str) -> bool:
    return any(m in message for m in _ENV_MARKERS)


def _prepare_image(tmp_dir) -> str | None:
    """生成合成身份证测试图；缺 Pillow 时跳过。"""
    try:
        from tools.generate_test_idcard import generate_test_idcard
    except ImportError as e:  # noqa: BLE001
        skip_test(f"未安装 Pillow（pip install pillow）：{e}")
        return None
    return generate_test_idcard(output_dir=str(tmp_dir))


def _reload_real_config():
    """强制从 settings.yaml 重载配置。

    pytest 单进程内，前面的用例可能把内存配置整体替换成 {"mock_mode": False}，
    会把腾讯云密钥段冲掉。这里恢复成磁盘上的真实配置，保证本文件独立可判。
    """
    from core import config as config_module
    config_module.config._data = None
    config_module.config.load()
    return config_module.config


def test_tencent_idcard_real(tmp_path):
    """真实腾讯云身份证识别：结果应与合成测试图一致。"""
    config = _reload_real_config()

    if not config.has_tencent_credentials():
        skip_test("未配置腾讯云密钥（config/settings.yaml）")
        return

    image = _prepare_image(tmp_path)
    if image is None:
        return

    from ocr.tencent_cloud import TencentCloudOCR

    try:
        result = TencentCloudOCR().recognize_idcard(image, strict=True)
    except ImportError as e:  # noqa: BLE001
        skip_test(f"未安装 tencentcloud-sdk-python-ocr：{e}")
        return
    except Exception as e:  # noqa: BLE001
        if _is_env_issue(str(e)):
            skip_test(f"腾讯云不可用（{str(e)[:80]}）")
            return
        raise

    assert result["姓名"] == _EXPECTED_NAME, f"姓名识别错误: {result}"
    assert result["身份证号"] == _EXPECTED_ID, f"身份证号识别错误: {result}"
    assert result["性别"] == "男"
    print(f"✓ 腾讯云身份证识别：{result['姓名']} / {result['身份证号']}")


def test_idcard_source_real_pipeline(tmp_path):
    """IdCardOCR 来源真实链路：必须返回真实数据，不允许静默回退 Mock。"""
    config = _reload_real_config()

    if not config.has_tencent_credentials():
        skip_test("未配置腾讯云密钥（config/settings.yaml）")
        return

    # 固定为非 Mock 模式（并保留密钥配置），让来源走真实 API
    from core import config as config_module
    config_module.config._data = {
        "mock_mode": False,
        "ocr": {"tencent": config.tencent_config},
    }

    image = _prepare_image(tmp_path)
    if image is None:
        return

    from sources.idcard_ocr import IdCardOCR

    try:
        result = IdCardOCR().extract(image)
    except ImportError as e:  # noqa: BLE001
        skip_test(f"未安装 tencentcloud-sdk-python-ocr：{e}")
        return
    except Exception as e:  # noqa: BLE001
        if _is_env_issue(str(e)):
            skip_test(f"腾讯云不可用（{str(e)[:80]}）")
            return
        raise

    reason = result.get("_fallback_reason")
    if reason:
        if _is_env_issue(reason):
            skip_test(reason[:100])
            return
        # 非环境问题却回退了 Mock：视为真实回归（例如 base64 编码又写错）
        raise AssertionError(f"真实 OCR 失败且非环境问题：{reason}")

    assert result.get("_source") != "mock", f"不应回退 Mock: {result}"
    assert result["姓名"] == _EXPECTED_NAME
    assert result["身份证号"] == _EXPECTED_ID
    print("✓ IdCardOCR 真实链路：未回退 Mock，字段正确")


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        test_tencent_idcard_real(td)
    with tempfile.TemporaryDirectory() as td:
        test_idcard_source_real_pipeline(td)
    print("test_online_tencent_ocr.py 全部通过 ✓")
