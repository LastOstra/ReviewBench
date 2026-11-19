## scripts 目录说明

本目录是 ReviewBench 本地评测流水线的脚本集合。评测分两步：

1. **跑题（local_run.py）**：对 manifest 中每个 PR，从 review-bench 镜像拉取代码并 checkout 到 head，生成 `base...head` 的 diff，调用 pr-agent 评审，把结果转成标准 findings JSON 写入 `findings/`。
2. **判卷（npm run judge → eval/eval.ts）**：用 LLM judge 将 findings 与 `golden/` 中的人工标注逐条匹配，计算 precision / recall 等指标，输出到 `scoring/`。

### 主要脚本

| 脚本 | 作用 |
|---|---|
| local_run.py | 免 Docker 的本地跑题器（pr-agent 适配） |
| eval/eval.ts | 判卷 CLI（`npm run judge` 的入口） |
| try-agent.sh | 官方 Docker 容器跑题器（提交排行榜用） |
| classifier/ | severity/category 分类器 prompt 与脚本 |
| validate-golden.ts | golden 标注格式校验 |

### 运行时产物目录（均为缓存/输出，可重建）

| 目录 | 由谁构建 | 内容 |
|---|---|---|
| .local-run/repos/ | local_run.py | 跑题用仓库缓存；同一仓库多 PR 复用，来回切 HEAD |
| .local-run/pr/ | local_run.py | 每个 PR 的 diff.patch |
| .local-run/review/ | local_run.py | pr-agent 原始评审 JSON |
| .reviewbench-repos/ | local_run.py 预建，judge 缺失时可自建 | 判卷用快照，布局 `<owner>__<repo>/<head前8位>`；经 `--shared` 借用 .local-run 对象库，几乎不占额外磁盘 |
| findings/ | local_run.py | 判卷输入（标准 findings 格式，见 docs/JUDGING_INPUT.md） |
| scoring/ | npm run judge | 评分结果 |

### 第一步：local_run 运行方式

```bash
# 先跑全量 25 个（可断点续跑，已存在的会跳过）：
conda activate pr-agent
cd /home/tian/桌面/AI_Review/pr-agent/ReviewBench

python3 scripts/local_run.py \
  --pr-agent /home/tian/下载/yes/envs/pr-agent/bin/pr-agent \
  --skip-existing
```

常用参数：

| 参数 | 默认 | 说明 |
|---|---|---|
| --set test/full | test | 25 个测试集或 219 个全量集 |
| --manifest <path> | 按 --set 推导 | 自定义数据集清单（见文末） |
| --pr <idx> | 全部 | 只跑指定下标，可重复传 |
| --limit N | - | 只跑前 N 个 |
| --out <dir> | ./findings | findings 输出目录 |
| --work <dir> | ./.local-run | 跑题工作区 |
| --seed-dir <dir> | ./.reviewbench-repos | 判卷快照目录 |
| --no-seed | - | 跳过种判卷快照 |
| --skip-existing | - | 已有 findings 的 PR 跳过（仍补种快照） |
| --mirror-org <org> | review-bench | 代码镜像组织 |
| --timeout <秒> | 1800 | 每个 PR 的 pr-agent 超时 |

### 第二步：judge 运行方式

```bash
# 先 --limit 1 验证链路（Key 在终端 export，不要写进文件）：
export DEEPSEEK_API_KEY=<你的 DeepSeek Key>

npm run judge -- \
  --candidate ./findings \
  --provider deepseek \
  --model deepseek-v4-flash \
  --repo-dir .reviewbench-repos \
  --output ./scoring/results.json \
  --limit 1

# --no-strict 不会修复 JSON 问题，它只是「失败就跳过」，分数会基于剩下的 PR 算——分母变小，指标会偏。所以它只能用于诊断，不能用于出成绩。
npm run judge -- \
  --candidate ./findings \
  --provider deepseek \
  --model deepseek-v4-flash \
  --repo-dir .reviewbench-repos \
  --output ./scoring/results-debug.json \
  --no-strict

# 通了以后去掉 --limit 1 跑完 25 个。
```
### 第三步：自己的小数据集的运行方式额外附录
# ① 自己 SSH 完整克隆（asterinas 几十 MB，SSH 稳）
python3 scripts/local_run.py \
  --manifest corpus/rust-set.json \
  --out findings-rust \
  --pr-agent pr-agent \
  --skip-existing \ 
  --limit 14 # (只跑前 14 个)

# ② 判卷
export ALIYUN_MAAS_API_KEY=你的阿里云百炼Key

npm run judge -- \
  --candidate ./findings-rust \
  --manifest corpus/rust-set.json \
  --golden golden-rust \
  --provider aliyun-maas \
  --model deepseek-v4.1-flash \
  --repo-dir .reviewbench-repos \
  --output ./scoring/results-rust-aliyun.json \
  --limit 1
常用参数：

| 参数 | 默认 | 说明 |
|---|---|---|
| --candidate <path> | 必填 | findings 文件或目录（递归读取） |
| --manifest <path> | corpus/manifest.json | PR 元信息清单，需与 golden 一致 |
| --golden <dir> | golden/ | 标准答案目录 |
| --repo-dir <dir> | /tmp/review-repos | 判卷快照目录，本地流程用 .reviewbench-repos |
| --output <path> | - | 评分结果 JSON |
| --limit N | - | 只评前 N 个 PR（验证链路用） |
| --no-strict | - | 失败跳过不报错，仅限诊断 |

注意：每个 PR 必须有对应的 `golden/<owner>_<repo>_<pr>-<head前8位>.json`，否则该 PR 被跳过（strict 模式下直接报错）。

### 添加自定义数据集

评测代码完全数据驱动，加新数据集无需改代码，准备三样东西：

1. **manifest**：仿照 `corpus/test/test.json` 建 `corpus/myset.json`，每条至少含 `repo / nwo / pr_number / base / head / title / body`（title/body 供判卷分类器读取，不可省）。
2. **golden**：每个 PR 一个 `golden/<pr_key>.json`，格式参照现有文件；意见的 file/行号必须以 head 快照为坐标系，且问题在 head 处仍存在；需人工审核。
3. **代码可达**：自有仓库不在 review-bench 镜像里，需提前克隆到缓存位置（prepare_repo 检测到 .git 存在会跳过镜像 clone）：

```bash
git clone https://github.com/owner/repo .local-run/repos/owner_repo
```

或镜像到自己的 GitHub 组织（命名 `<owner>_<repo>`），跑题加 `--mirror-org 你的组织`，判卷设环境变量 `MIRROR_ORG`。

运行（独立输出目录，与原 25 个互不干扰）：

```bash
python3 scripts/local_run.py \
  --manifest corpus/myset.json \
  --pr-agent /home/tian/下载/yes/envs/pr-agent/bin/pr-agent \
  --out ./findings-myset \
  --skip-existing

npm run judge -- \
  --candidate ./findings-myset \
  --manifest corpus/myset.json \
  --provider deepseek \
  --model deepseek-v4-flash \
  --repo-dir .reviewbench-repos \
  --output ./scoring/results-myset.json
```