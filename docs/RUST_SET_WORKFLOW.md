# Rust 考题数据集：标准构建与审计流程

本文档是 `corpus/rust-set.json` + `golden-rust/` 数据集的**标准工作流程**，适用于任何经手该数据集的人或模型。配套的可执行约束见项目 skill `rust-set-workflow`。评测流水线（跑题/判卷）见 [scripts/readme.md](../scripts/readme.md)。

## 1. 目录与事实源

| 路径 | 角色 | git 归属 |
|---|---|---|
| `rust-set-work/findings_spec.json` | **唯一事实源**：人工审定的全部条目与 findings | 内层仓库跟踪 |
| `rust-set-work/ledger.jsonl` | append-only 台账：每个候选 PR 的筛选/入选/拒绝记录 | 内层仓库跟踪 |
| `rust-set-work/scripts/`、评审记录、备份、日志 | 构建脚本与工作痕迹 | 内层仓库跟踪 |
| `rust-set-work/cache/` | GitHub 原始数据缓存（prs/、files/、compare/），可再生 | 内层 gitignored |
| `rust-set-work/.env` | GitHub token（secret） | 内层 gitignored，**严禁入库** |
| `corpus/rust-set.json` | 考题清单（manifest），由 generate.py 从 spec 生成 | 外层仓库跟踪 |
| `golden-rust/*.json` | 标准答案，由 generate.py 从 spec 生成 | 外层仓库跟踪 |

**git 维护约定**：`rust-set-work/` 是独立的**本地 git 仓库**（外层仓库通过 .gitignore 忽略整个目录，二者互不影响；不设 remote、不上传云端）。工作区内任何改动（spec、脚本、台账、报告）完成后必须在内层仓库 commit 留痕，便于用 `git log`/`git diff` 追溯修改过程与归因。`.env` 与 `cache/` 永远不入库。

**规则：只改 spec，绝不手改 manifest/golden。** 改完必须 `generate.py` 重新生成 + `selfcheck.py` 全量自检。修改 spec 前先备份（`findings_spec.backup_*.json`）。

条目按 `(nwo, pr_number, exam_head)` 计数：同一 PR 的不同 head 快照是不同条目。

## 2. 核心概念：head 与 base 的口径（硬性约束）

- **exam_head**：必须等于某条真实人工 inline 评论的 `original_commit_id`（评论发表时所在的提交，通常是中间提交而非最终合并 head）。这是 GitHub 记录的外部事实，selfcheck 逐字比对。
- **exam_base**：必须是 `merge-base(meta.base.sha, exam_head)`，即 PR 分支与目标分支的**分叉点**，用 compare API 计算：

  ```
  GET /repos/{nwo}/compare/{meta.base.sha}...{exam_head}
  → merge_base_commit.sha
  ```

  **禁止直接抄 `meta.base.sha`**（那是建 PR 时目标分支的 tip，不是分叉点）。跑题器 `local_run.py` 用三点 diff（`git diff base...head`，git 自动取 merge-base），所以填错 base 当前不会立刻暴露，但两点 diff 或直接树比较的消费方会混入目标分支的无关改动——这是必须杜绝的隐患。
- compare API 的证据由 `fix_base.py` 缓存到 `cache/compare/<owner_repo>_<pr>_<head8>.json`，selfcheck 据此做 base 的**外部校验**（base 必须等于证据中的 `merge_base_commit_sha`，且证据的 exam_head/meta_base_sha 与当前数据一致）。

## 3. 准入标准（语义审查）

一条 finding 必须**同时**满足：

1. 实质性的正确性 / 契约 / soundness 缺陷（不是风格、命名、文档、设计偏好）；
2. 由**本 PR 引入**（pre-existing 问题拒绝）；
3. 缺陷在 **exam_head 处仍然存在**（后续提交已修复的快照拒绝）；
4. 来自人工 inline 评审评论，message/author/created_at/original_commit 逐字保留；bot 评论、被作者澄清反驳的评论拒绝。

**审查方法要求：必须读完整 PR patch 再下结论。** 局部文件摘录可能显示"检查已存在"，但真实缺陷在另一个函数里（教训：#2388 的 ns ownership 检查缺失在 `set_mount_propagation`，而摘录只展示了 `move_mount_to`）。

## 4. 标准构建步骤

```bash
cd rust-set-work/scripts

# 1) 爬取 + 机械筛选（结论自动写入 ledger.jsonl）
python3 crawl.py --repo <owner/repo> --target-pass N --max-pages M

# 2) 人工语义审查：对 mech_pass 候选读完整 patch + 评论上下文，
#    用 verdict.py 记录入选/拒绝（含 exam_head、justification、severity/category/difficulty）
python3 verdict.py --repo <owner/repo> --pr N --status selected \
  --exam-head <sha> --exam-base <merge-base-sha> ...

# 3) 拉取 exam_head 处的文件内容，逐条核对缺陷行号
python3 fetch_batch2_heads.py   # 或对应的 head 拉取脚本
python3 check_lines.py          # 行号 vs exam_head 文件

# 4) 审查结论写入 findings_spec.json（verified=true, lines_confirmed=true）

# 5) 统一/验证 base 口径（调 compare API，缓存证据，修正 exam_base）
python3 fix_base.py             # 可先 --dry-run 看差异

# 6) 生成 manifest + golden
python3 generate.py

# 7) 全量自检（必须 ALL CHECKS PASSED，0 跳过才算完成）
python3 selfcheck.py
```

## 5. selfcheck 覆盖项（不得绕过）

- manifest ↔ golden ↔ spec 三方一致（含 base/head/行号/severity/category）；
- base/head 为 40 位 hex；**base == spec.exam_base == compare 证据的 merge_base_commit_sha**；
- title/body 逐字来自 meta.json；message/author/created_at/original_commit 逐字来自缓存评论；
- head 锚定：spec 条目按 `(nwo, pr_number, exam_head)` 匹配（多快照 PR 不得只按 PR 号匹配）；
- 评论锚点与缺陷行范围对齐；行范围 ≤ exam_head 文件实际行数（文件未缓存则 WARN 跳过——**交付时不允许有跳过**）；
- 计数从 spec 动态推导（禁止硬编码条目/finding 数量）。

**负向测试**：对校验逻辑本身的改动，要故意制造一处错误（如篡改一个 base）确认 selfcheck 报错，再恢复重跑。

## 6. 接管他人产出时的审计流程

1. **归因**：外层 `git status`/`git log` 看工程与 manifest/golden 改动；内层（rust-set-work）`git log`/`git diff` 看 spec/脚本/台账的逐次修改（2026-11-17 基线之前的历史无 git 记录，用文件 mtime + ledger 时间戳归因）；确认工程源码（pr_agent/、scripts/eval/ 等）未被改动。
2. **机械复核**：跑 selfcheck；注意旧脚本可能含硬编码计数等历史假设，先修工具再下结论。
3. **语义复审**：按第 3 节标准逐条复审（读完整 patch），不信任前任的 justification。
4. **处置留痕**：剔除/修正的条目写入审计 verdict 文件（如 `batch2_audit_verdicts.json`），spec 备份可回滚。
5. 重新生成 + 全量自检 + 向委托方如实汇报错误率与可信度。

## 7. 已知教训

- batch2/pilot 曾直接抄 `meta.base.sha` 当 exam_base（图省事跳过 compare API），与 batch1 的 merge-base 口径不一致；因 selfcheck 当时不校验 base 语义而未被发现。2026-11-17 已统一为 merge-base 并加外部校验。
- selfcheck 曾硬编码 batch1 计数（25/38），导致"自检通过"的声明对全量数据不成立。
- 只看局部文件摘录差点误杀有效条目（#2388）；语义判断必须基于完整 patch。
- ghost 用户（已注销账号）评论的 `user` 为 null，取 login 需兜底 `"ghost"`。