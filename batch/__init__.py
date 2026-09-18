"""批量投保录入（学平险场景）。

把「手写台账 → Excel → 逐条人工录入」的流程改造为：

    Excel/CSV → 导入 + 校验 → 任务列表（状态机）
              → 串行自动录入（复用 page/filler.py）→ 待复核（带截图）
              → 人工复核 → 人工支付

模块划分：
  models      状态机与数据类
  db          SQLite 存储（batch / applicant / status_log / validation）
  crypto      身份证号加密存储（Fernet）与脱敏显示
  id_card     身份证号校验（校验位 / 出生日期 / 年龄）
  dictionaries 学校/年级/班级字典与归一化匹配
  validator   校验规则集合（导入即校验、可单独跑）
  importer    Excel / CSV → 落库 → 逐条校验
  runner      批量执行：串行填充 + 截图 + 重试 + 中断恢复
  reviewer    复核：通过 / 打回 / 批量通过
  exporter    导出：状态汇总 / 失败清单 / 支付清单 / 提效统计
  recipe      业务配方（一份 YAML 描述一条业务链路）
  probe       页面探针（P-1）：抓下拉选项作为字典权威值
"""
