# AGENTS.md - FormPilot 项目工作约定

## 项目卡片自动收尾

- 本项目对应卡片：`C:/Users/19845/Documents/resume-knowledge/projects/formpilot.md`
  （旧路径 `C:/Users/19845/.zcode/workspace/default/resume_work/projects/formpilot.md` 只保留为历史副本，不要写它）
- 完成实质代码、架构、接口、算法、部署变更，或修复非平凡 bug 后，结束任务前必须执行
  `C:/Users/19845/.agents/skills/project-card/SKILL.md` 的工作流 A，不需要用户再说“更新卡片”。
- 先读现有卡片查重；只写代码、测试、文件、日志或用户口述能证实的事实；未落地标 `【设计中】`，
  不确定标 `【待补】`/`【待确认】`。
- 卡片改完后运行：

```bash
python "C:/Users/19845/.agents/skills/project-card/scripts/build_index.py" --if-stale
```

- 错别字、格式、注释、纯文案和无行为变化的小重构不触发。
