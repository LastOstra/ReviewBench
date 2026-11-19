---
name: rust-set-workflow
description: Rust 考题数据集（corpus/rust-set.json + golden-rust）的构建、扩充、审计与自检标准流程。凡改动 findings_spec、新增/剔除考题条目、爬取 PR 评论入集、或接管他人数据集产出时使用。不用于跑题判卷评测本身（见 scripts/readme.md）。
---

# Rust 考题数据集工作流

详细流程与背景见 [docs/RUST_SET_WORKFLOW.md](../../../docs/RUST_SET_WORKFLOW.md)。本 skill 是硬性约束摘要，任何经手该数据集的会话都必须遵守。

## 事实源与生成链

- 唯一事实源：`rust-set-work/findings_spec.json`。**只改 spec，绝不手改 `corpus/rust-set.json` 或 `golden-rust/*.json`**。
- 改 spec 前先备份（`findings_spec.backup_*.json`）；改后必须依次执行：

```bash
python3 rust-set-work/scripts/fix_base.py    # 统一/验证 base（compare API 证据）
python3 rust-set-work/scripts/generate.py    # 重新生成 manifest + golden
python3 rust-set-work/scripts/selfcheck.py   # 必须 ALL CHECKS PASSED 且 0 行号跳过
```

- 条目按 `(nwo, pr_number, exam_head)` 计数；同一 PR 可有多个 head 快照条目。
- `rust-set-work/` 是独立本地 git 仓库（外层仓库 gitignore 整个目录；无 remote）。工作区内改动完成后必须在内层仓库 commit 留痕；`.env`（token）与 `cache/` 严禁入库。

## head / base 口径（不得违反）

- `exam_head` 必须逐字等于某条真实人工 inline 评论的 `original_commit_id`。
- `exam_base` 必须是 `merge-base(meta.base.sha, exam_head)`，由 compare API 的 `merge_base_commit.sha` 确定。**禁止直接抄 `meta.base.sha`**（那是目标分支 tip，不是分叉点；三点 diff 下不立刻暴露，但属隐患）。
- compare 证据缓存在 `rust-set-work/cache/compare/`，selfcheck 据此外部校验 base。

## 准入标准

finding 必须同时满足：实质正确性/契约/soundness 缺陷；本 PR 引入（拒 pre-existing）；缺陷在 exam_head 处仍存在（拒已修复快照）；人工 inline 评论 verbatim（拒 bot、拒风格/命名/文档/设计偏好、拒被作者澄清反驳）。

语义判断**必须读完整 PR patch**，不得只凭局部文件摘录下结论。

## 自检纪律

- selfcheck 计数必须从 spec 动态推导，禁止硬编码。
- 多快照 PR 匹配必须带 `exam_head` 条件。
- 改动校验逻辑后做负向测试：故意制造一处错误确认报错，再恢复重跑。
- 不得在存在 WARN 跳过或 ERROR 时宣称"自检通过"。

## 接管他人产出时

先归因（git tracked vs gitignored、文件 mtime、ledger 时间戳），确认工程源码未被改动；再机械复核 + 按准入标准逐条语义复审；剔除/修正留痕到审计 verdict 文件；最后重新生成 + 全量自检，并如实汇报错误率。