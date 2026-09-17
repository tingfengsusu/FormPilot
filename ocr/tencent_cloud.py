"""腾讯云身份证 OCR 封装。

优先使用云 API（准确率高），未配置密钥或调用失败时回退到 Mock 数据，
保证无凭证也能跑通「拖图 → 识别」链路。
"""
from __future__ import annotations

import base64
from typing import Any, Dict

from core.config import config
from core import mock_data


class TencentCloudOCR:
    """腾讯云身份证 OCR。

    真实实现依赖 `tencentcloud-sdk-python-ocr`；此处对 SDK 采用惰性导入，
    未安装 SDK 时自动回退 Mock，避免 Phase 2 骨架因缺依赖而启动失败。
    """

    def __init__(self, secret_id: str = "", secret_key: str = ""):
        cfg = config.tencent_config
        self.secret_id = secret_id or cfg.get("secret_id", "")
        self.secret_key = secret_key or cfg.get("secret_key", "")

    def recognize_idcard(self, image_path: str,
                         strict: bool = False) -> Dict[str, Any]:
        """识别身份证，返回结构化字段。

        :param strict: True 时调用失败直接抛异常（调试真实密钥用）；
                       False 时回退 Mock，并在结果里写入 `_fallback_reason`
        """
        # Mock 模式：直接返回测试数据
        if config.mock_mode:
            return mock_data.idcard_mock(image_path)

        # 未配置密钥：回退 Mock
        if not self.secret_id or not self.secret_key:
            return mock_data.idcard_mock(image_path)

        # 真实调用（惰性导入 SDK）
        try:
            return self._call_real(image_path)
        except Exception as e:  # noqa: BLE001
            if strict:
                raise
            result = mock_data.idcard_mock(image_path)
            result["_fallback_reason"] = f"腾讯云 OCR 失败，已回退 Mock：{e}"
            return result

    def _call_real(self, image_path: str) -> Dict[str, Any]:
        """调用腾讯云身份证 OCR API。

        需要 `pip install tencentcloud-sdk-python-ocr`，并正确配置密钥。
        """
        from tencentcloud.common import credential
        from tencentcloud.common.profile.client_profile import ClientProfile
        from tencentcloud.common.profile.http_profile import HttpProfile
        from tencentcloud.ocr.v20181119 import ocr_client, models

        with open(image_path, "rb") as f:
            image_base64 = base64.b64encode(f.read()).decode("ascii")

        cred = credential.Credential(self.secret_id, self.secret_key)
        http_profile = HttpProfile()
        http_profile.endpoint = "ocr.tencentcloudapi.com"
        client_profile = ClientProfile()
        client_profile.httpProfile = http_profile

        client = ocr_client.OcrClient(cred, "ap-guangzhou", client_profile)
        req = models.IDCardOCRRequest()
        req.ImageBase64 = image_base64

        resp = client.IDCardOCR(req)

        # 把腾讯云返回结构映射为统一字段名
        info = resp.IdCardInfo if getattr(resp, "IdCardInfo", None) else resp
        result = {
            "姓名": getattr(info, "Name", ""),
            "性别": getattr(info, "Sex", ""),
            "民族": getattr(info, "Nation", ""),
            "出生日期": getattr(info, "Birth", ""),
            "身份证号": getattr(info, "IdNum", ""),
            "地址": getattr(info, "Address", ""),
            "签发机关": getattr(info, "Authority", ""),
            "有效期": getattr(info, "ValidDate", ""),
        }
        # 告警码（如复印件告警）保留给调用方参考
        warns = getattr(resp, "WarnInfos", None)
        if warns:
            result["_warnings"] = [w.WarnCode for w in warns]
        return result