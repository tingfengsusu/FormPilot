"""填表引擎：只懂「怎么操作页面」，不懂任何业务。

规格见 ``docs/引擎规格-FillEngine.md``；边界由 ``tests/test_architecture.py`` 守卫。

对业务层的全部接口：

    from engine import FillEngine, FillSession, FillResult, SubmitSpec
    from engine.runner import missing_mappings

    engine = FillEngine(product, mock=False)
    with engine.session() as session:
        result = session.fill(pool, submit=False, screenshot_path="...")
"""
from .annotate import (ANNOTATION_ID, annotate_expected, annotate_page,
                       build_annotations, clear_annotations, is_annotated)
from .models import (BAD_STATUSES, ErrorClass, FieldKind, FillEvent, FillResult,
                     OpenSpec, SubmitSpec, VerifyTier)
from .runner import FillEngine, missing_mappings
from .session import FillSession, capture_page, problems_of, safe_filename

__all__ = [
    "FillEngine", "FillSession", "FillResult", "FillEvent", "FieldKind",
    "VerifyTier", "ErrorClass", "OpenSpec", "SubmitSpec",
    "missing_mappings", "problems_of", "safe_filename", "capture_page",
    "BAD_STATUSES",
    # 页面内批注层（复核用）
    "annotate_expected", "annotate_page", "build_annotations",
    "clear_annotations", "is_annotated", "ANNOTATION_ID",
]
