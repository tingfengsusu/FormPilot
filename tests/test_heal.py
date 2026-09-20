"""异常自愈测试（不联网、不起浏览器）—— 对齐设计文档第九节的验收标准。

1. **命中并自愈**：已知故障 → 自动处置 → 重试成功 → DONE + 日报记自愈
2. **不猜**：库里没有的故障 → 未命中 → 记为新案例（禁用以待人工确认）+ 告警
3. **只给建议**：manual 类案例（如补别名表）→ 不执行动作，告警里带 hint
4. **防死循环**：同一条任务自愈次数达上限后仍失败 → 直接告警
5. **可追溯**：每次尝试都写 heal_attempt（案例、相似度、处置、结果）
6. 特征提取 / 白名单拦截 / 相似检索 都有独立用例
"""
from __future__ import annotations

import contextlib
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gateway.console import ConsoleChannel
from gateway.models import InboundMessage
from heal import Healer, HealStore
from heal.features import (extract_feature, feature_similarity, signature_of,
                           similarity)
from heal.models import (REMEDY_ADJUST_PERIOD_FLOOR, REMEDY_DISMISS_OVERLAY,
                         REMEDY_MANUAL, REMEDY_RELOAD_PAGE, REMEDY_RETRY_AFTER,
                         REMEDY_SWITCH_LOCATOR, HealCase, HealDecision)
from heal.remedies import (HealContext, apply_remedy, describe_remedy,
                            get_remedy, is_auto, validate_params)
from orchestrator import state as st
from orchestrator.queue import TaskQueue
from orchestrator.worker import ExecResult, Worker
from report.daily import build_daily_report, format_daily_report

#: 真机踩过的错误文本（与种子案例的 symptom 同源）
DATE_ERROR = "日期选择器结构不识别或日期不可选（目标 2026-09-21）"
LIMIT_ERROR = "Page.goto: net::ERR_ABORTED 页面加载被中断"
ALIAS_ERROR = "「清花园」不在页面选项/别名表中（需人工确认后补别名，不猜）"
UNKNOWN_ERROR = "识别失败：云 OCR 返回 401 未授权（app_id 无效）"


@contextlib.contextmanager
def _env(tmp_dir: str, healer_config=None):
    """独立库 + 已装种子的 healer（每次测试互不影响）。"""
    path = os.path.join(tmp_dir, "gateway.db")
    queue = TaskQueue(path)
    store = HealStore(path)
    healer = Healer(store, healer_config or {})
    healer.seed()
    try:
        yield queue, healer
    finally:
        store.close()
        queue.close()


def _enqueue(queue: TaskQueue, msg_id: str = "m1", text: str = "学平险"):
    return queue.enqueue(InboundMessage(channel="console", msg_id=msg_id,
                                        chat_id="c1", text=text),
                         variety="学平险")


class _ScriptedExecutor:
    """按脚本返回结果，并记下被调用的次数（模拟同一条任务重试）。"""

    def __init__(self, results):
        self.results = list(results)
        self.calls = 0

    def __call__(self, task, note_step):
        self.calls += 1
        note_step("OCR", ok=True, detail="识别 6 字段")
        note_step("MAPPING", ok=True, detail="映射 5 属性")
        result = self.results[min(self.calls - 1, len(self.results) - 1)]
        if result.ok:
            note_step("FILLING", ok=True, detail="填写 5 字段")
            note_step("VERIFYING", ok=True, detail="回读 0 差异")
        else:
            note_step("FILLING", ok=False, detail=result.error)
        return result


# ----------------------------------------------------------------------
# 特征提取与相似度
# ----------------------------------------------------------------------
def test_signature_strips_concrete_values():
    """签名要抹掉具体值（日期/数字/路径/URL），只留形状。"""
    assert signature_of(DATE_ERROR) == "日期选择器结构不识别或日期不可选 目标 date"
    assert signature_of("等 30000ms 超时") == "等 # ms 超时"
    assert "path" in signature_of(r"打开 C:\Users\a\b.html 失败")
    assert "url" in signature_of("跳转 https://x.com/y?mId=123 失败")


def test_similarity_behaviour():
    same = "（目标 2026-09-21）日期不可选"
    other = "（目标 2026-10-05）日期不可选"
    assert similarity(DATE_ERROR, "日期选择器结构不识别或日期不可选（目标 2026-09-22）") > 0.8
    assert similarity(DATE_ERROR, LIMIT_ERROR) < 0.3
    assert similarity("", DATE_ERROR) == 0.0
    assert similarity(other, same) > 0.5            # 只差具体值 → 仍相似


def test_extract_feature_shape():
    feature = extract_feature(DATE_ERROR, step="FILLING",
                              property_name="保险期间起", variety="学平险")
    assert feature.signature.startswith("日期选择器")
    assert feature.step == "FILLING" and feature.property_name == "保险期间起"
    assert feature.to_dict()["property"] == "保险期间起"      # 落库键名保持 property


# ----------------------------------------------------------------------
# 白名单与处置执行
# ----------------------------------------------------------------------
def test_remedy_whitelist():
    assert is_auto(REMEDY_ADJUST_PERIOD_FLOOR) and is_auto(REMEDY_RETRY_AFTER)
    assert is_auto(REMEDY_DISMISS_OVERLAY) and is_auto(REMEDY_RELOAD_PAGE)
    # 需要改配置/改代码的动作绝不自动执行
    assert not is_auto(REMEDY_SWITCH_LOCATOR) and not is_auto(REMEDY_MANUAL)
    assert get_remedy("不存在的动作") is None
    assert "整数" in validate_params(REMEDY_ADJUST_PERIOD_FLOOR, {"days": "两天"})
    assert validate_params(REMEDY_ADJUST_PERIOD_FLOOR, {"days": 1}) == ""
    assert "不支持参数" in validate_params(REMEDY_RETRY_AFTER, {"seconds": 5,
                                                              "extra": 1})


def test_apply_remedy_requires_capability():
    """上下文给不出能力时不能硬执行（降级为人工建议）。"""
    case = HealCase(id=1, remedy=REMEDY_ADJUST_PERIOD_FLOOR,
                    remedy_params={"days": 1})
    decision = HealDecision(case=case, similarity=0.9, auto=True)
    outcome = apply_remedy(decision, HealContext())          # 没给能力
    assert outcome.applied is False and "上下文不支持" in outcome.detail


def test_apply_remedy_auto_paths():
    calls = {}
    case = HealCase(id=1, remedy=REMEDY_ADJUST_PERIOD_FLOOR,
                    remedy_params={"days": 2})
    context = HealContext(adjust_period_floor=lambda days: calls.setdefault(
        "days", days) or True)
    outcome = apply_remedy(HealDecision(case=case, auto=True), context)
    assert outcome.applied and outcome.ok and calls["days"] == 2

    slept = []
    outcome2 = apply_remedy(
        HealDecision(case=HealCase(id=2, remedy=REMEDY_RETRY_AFTER,
                                   remedy_params={"seconds": 3}), auto=True),
        HealContext(sleep=slept.append))
    assert outcome2.applied and slept == [3.0]

    outcome3 = apply_remedy(
        HealDecision(case=HealCase(id=3, remedy=REMEDY_MANUAL,
                                   remedy_params={"hint": "补别名表"}), auto=False),
        HealContext())
    assert outcome3.applied is False and "补别名表" in outcome3.detail
    assert describe_remedy(REMEDY_RELOAD_PAGE)


# ----------------------------------------------------------------------
# 案例库
# ----------------------------------------------------------------------
def test_store_upsert_and_similarity():
    with tempfile.TemporaryDirectory() as td:
        with _env(td) as (queue, healer):
            _ = queue
            cases = healer.cases()
            assert len(cases) == 6                      # 种子装进去了
            seeded = healer.seed()                      # 重复装不会翻倍
            assert len(healer.cases()) == 6 and seeded == 6

            matches = healer.store.find_similar(
                extract_feature(DATE_ERROR, step="FILLING"), top_k=2,
                threshold=0.45)
            assert matches and matches[0][0].signature.startswith("日期选择器")
            assert matches[0][1] > 0.9

            best = matches[0][0]
            healer.store.record_seen(best.id)
            healer.store.record_hit(best.id, success=True)
            refreshed = healer.store.get(best.id)
            assert refreshed.occurrences == 1 and refreshed.hits == 1
            assert refreshed.successes == 1 and refreshed.hit_rate == 100.0

            healer.store.set_enabled(best.id, False)
            after = healer.store.find_similar(
                extract_feature(DATE_ERROR), threshold=0.45)
            assert all(case.id != best.id for case, _ in after)   # 禁用即不再命中


def test_healer_diagnose_modes():
    with tempfile.TemporaryDirectory() as td:
        with _env(td) as (_queue, healer):
            auto = healer.diagnose_text(DATE_ERROR, step="FILLING",
                                        property_name="保险期间起")
            assert auto.matched and auto.auto and auto.case.id is not None

            manual = healer.diagnose_text(ALIAS_ERROR)
            assert manual.matched and not manual.auto and "别名表" in manual.hint

            miss = healer.diagnose_text(UNKNOWN_ERROR)
            assert not miss.matched and not miss.auto
            assert "未命中" in miss.describe()

            # 阈值卡住：相似度不够就当未命中（宁可转人工也不乱套处置）
            strict = Healer(healer.store, {"threshold": 0.99})
            assert not strict.diagnose_text(LIMIT_ERROR).matched

            # 关闭自愈
            off = Healer(healer.store, {"enabled": False})
            assert not off.diagnose_text(DATE_ERROR).matched


def test_learn_creates_disabled_case():
    with tempfile.TemporaryDirectory() as td:
        with _env(td) as (_queue, healer):
            case_id = healer.learn(UNKNOWN_ERROR, step="OCR")
            case = healer.store.get(case_id)
            assert case is not None and case.source == "auto"
            # 处置未验证过 → 先禁用，等人工确认后才生效（不猜）
            assert case.enabled is False
            assert not healer.diagnose_text(UNKNOWN_ERROR).matched


# ----------------------------------------------------------------------
# worker 集成（验收标准 1~5）
# ----------------------------------------------------------------------
def test_worker_heals_and_recovers():
    """① 命中并自愈：失败 → 自动处置 → 重试成功 → DONE + 台账 + 日报计数。"""
    with tempfile.TemporaryDirectory() as td:
        with _env(td) as (queue, healer):
            channel = ConsoleChannel(printer=lambda *_: None)
            executor = _ScriptedExecutor([
                ExecResult(ok=False, error=DATE_ERROR, error_step="FILLING",
                           error_property="保险期间起"),
                ExecResult(ok=True, summary="已填完 5 个字段", applicant="张伟",
                           filled_fields=5),
            ])
            floors = []
            worker = Worker(queue, executor, channel, healer=healer,
                            heal_context=HealContext(
                                adjust_period_floor=lambda days: floors.append(days)
                                or True),
                            max_retries=1, retry_backoff_seconds=0,
                            sleep=lambda _s: None)
            _enqueue(queue)
            report = worker.drain()

            assert executor.calls == 2 and report.done == 1    # 自愈后重试成功
            assert floors == [1]                               # 真的放宽了起期下限
            task = queue.list_tasks(limit=1)[0]
            assert task.status == st.DONE

            steps = {s["step"]: s for s in queue.steps(task.id)}
            assert "HEAL" in steps and steps["HEAL"]["ok"]
            assert "healed_by=case#" in steps["HEAL"]["detail"]

            # 案例命中率被更新（自愈成功）
            case = [c for c in healer.cases() if c.remedy == REMEDY_ADJUST_PERIOD_FLOOR][0]
            assert case.hits == 1 and case.successes == 1

            # 可追溯：heal_attempt 留痕
            attempts = healer.store.attempts(task.id)
            assert len(attempts) == 1
            assert attempts[0]["case_id"] == case.id
            assert attempts[0]["applied"] == 1 and attempts[0]["settled"] == 1

            # 日报里的自愈计数
            daily = build_daily_report(queue, ledger=None)
            assert daily.healed == 1 and daily.done == 1
            assert "自愈 1 次" in format_daily_report(daily)
            # 自愈成功 → 不该发失败告警
            assert not any("需要人工介入" in item["text"] for item in channel.sent)


def test_worker_unknown_failure_records_case_and_alerts():
    """② 不猜：未命中 → 记新案例（禁用）+ 告警里说明。"""
    with tempfile.TemporaryDirectory() as td:
        with _env(td) as (queue, healer):
            channel = ConsoleChannel(printer=lambda *_: None)
            before = len(healer.cases())
            executor = _ScriptedExecutor([
                ExecResult(ok=False, error=UNKNOWN_ERROR, error_step="OCR")])
            worker = Worker(queue, executor, channel, healer=healer,
                            max_retries=0, retry_backoff_seconds=0,
                            sleep=lambda _s: None)
            _enqueue(queue)
            report = worker.drain()

            assert report.alerted == 1
            task = queue.list_tasks(limit=1)[0]
            assert task.status == st.ALERT
            # 没有自动动作（没有 HEAL 步骤）
            assert all(s["step"] != "HEAL" for s in queue.steps(task.id))
            # 新案例入库但**禁用**（人工确认前不生效）
            assert len(healer.cases()) == before + 1
            new_case = [c for c in healer.cases() if c.source == "auto"][0]
            assert new_case.enabled is False
            assert any("已记为新案例" in item["text"] for item in channel.sent)


def test_worker_manual_case_only_advises():
    """③ 只给建议：manual 类案例不执行动作，但告警带 hint。"""
    with tempfile.TemporaryDirectory() as td:
        with _env(td) as (queue, healer):
            channel = ConsoleChannel(printer=lambda *_: None)
            executor = _ScriptedExecutor([
                ExecResult(ok=False, error=ALIAS_ERROR, error_step="MAPPING",
                           error_property="school")])
            worker = Worker(queue, executor, channel, healer=healer,
                            max_retries=0, retry_backoff_seconds=0,
                            sleep=lambda _s: None)
            _enqueue(queue)
            report = worker.drain()

            assert report.alerted == 1
            task = queue.list_tasks(limit=1)[0]
            assert all(s["step"] != "HEAL" for s in queue.steps(task.id))
            texts = " ".join(item["text"] for item in channel.sent)
            assert "别名表" in texts and "人工确认" in texts
            # manual 案例不会被记成新案例（已经是库里的）
            assert len(healer.cases()) == 6


def test_worker_heal_budget_prevents_loop():
    """④ 防死循环：自愈次数用尽后仍失败 → 直接告警，不再自愈。"""
    with tempfile.TemporaryDirectory() as td:
        with _env(td) as (queue, healer):
            channel = ConsoleChannel(printer=lambda *_: None)
            executor = _ScriptedExecutor([
                ExecResult(ok=False, error=DATE_ERROR, error_step="FILLING")])
            worker = Worker(queue, executor, channel, healer=healer,
                            heal_context=HealContext(
                                adjust_period_floor=lambda days: True),
                            max_retries=0, retry_backoff_seconds=0,
                            sleep=lambda _s: None)
            _enqueue(queue)
            report = worker.drain()

            # 自愈一次 → 重试仍失败 → 直接告警（healer 预算 = 1）
            assert executor.calls == 2
            assert report.alerted == 1
            task = queue.list_tasks(limit=1)[0]
            heal_steps = [s for s in queue.steps(task.id) if s["step"] == "HEAL"]
            assert len(heal_steps) == 1
            daily = build_daily_report(queue)
            assert daily.healed == 0 and daily.heal_failed == 1
            assert "没救回来" in format_daily_report(daily)


if __name__ == "__main__":
    test_signature_strips_concrete_values()
    test_similarity_behaviour()
    test_extract_feature_shape()
    test_remedy_whitelist()
    test_apply_remedy_requires_capability()
    test_apply_remedy_auto_paths()
    test_store_upsert_and_similarity()
    test_healer_diagnose_modes()
    test_learn_creates_disabled_case()
    test_worker_heals_and_recovers()
    test_worker_unknown_failure_records_case_and_alerts()
    test_worker_manual_case_only_advises()
    test_worker_heal_budget_prevents_loop()
    print("test_heal.py 全部通过 ✓")
