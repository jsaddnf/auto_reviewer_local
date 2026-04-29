# autoreviewer

> 基于 **Claude Code** (`claude`) 的本地自动代码评审工具：每次 git 提交后，针对单个仓库后台触发一次 AI Code Review。
> 兼容 **所有** git 客户端（CLI、Fork、SourceTree、各类 IDE）。评审在后台运行，完成后通过 macOS 通知弹出，点击即可打开报告。

🌏 **English docs**: [README.md](README.md)

## 一句话上手

```bash
# 1. 安装工具本体（每台机器一次）
git clone https://github.com/jsaddnf/auto_reviewer_local.git ~/.autoreviewer-src
~/.autoreviewer-src/install.sh

# 1b. 之后随时升级
autoreviewer update                  # 拉最新源码 + 重新安装

# 2. 按仓库分别开启
cd /path/to/your/repo
autoreviewer install

# 3. 之后该仓库每次提交都会触发后台评审
git commit -m "feat: add login"
# → 提交立刻返回，不阻塞
# → 提交瞬间就弹一条「开始评审」通知
# → Claude Code 在后台跑 review
# → 评审结束后再弹一条带严重级别 + 点击即打开的通知

# 其他常用命令
autoreviewer status                  # 查看当前仓库的安装状态
autoreviewer update                  # 拉最新源码 + 重新安装
autoreviewer run                     # 手动同步执行一次 review
autoreviewer log                     # 浏览历史
autoreviewer show a3f8c21            # 查看某次评审
autoreviewer install --uninstall     # 把当前仓库的开关关闭
autoreviewer disable                 # 全局暂停（不卸载）
AUTOREVIEWER_SKIP=1 git commit ...   # 单次提交跳过评审
```

## 为什么用 Claude Code 作为底层引擎？

`claude` 是 Anthropic 官方的 Claude Code 命令行工具。autoreviewer 把 commit
diff 通过 `claude -p` 以非交互模式喂给它，要求严格按 JSON schema 输出评审结果。
之所以选 Claude Code，是因为它在仓库内可以原生使用 `Read` / `Grep` / `Glob`
/ `Bash` 等工具：评审不再局限于 diff，还能顺藤摸瓜去看调用方、相关单测、
配置文件，从而更准确地判断 **影响范围**。

如果你想换成别的 LLM 命令行工具，只要在 `~/.autoreviewer/config.json` 改
`command` 一行即可——只要它支持「从 stdin 读 prompt、把回复打到 stdout」就行。

## 为什么是「按仓库 (per-repo)」？

git 在设计上就把 hooks 配置交给每个仓库自己管理。很多项目会主动设置
`core.hooksPath`（Husky、Lefthook、项目自带 `.githooks/`）来约束团队的
提交规则。如果一上来就「全局接管所有仓库」，会和这些项目冲突。

按仓库开启的好处：

- **可预测**：`autoreviewer status` 会清清楚楚告诉你这个仓库会发生什么
- **不打扰**：原本有自己 hook 体系的项目继续工作
- **可逆**：`autoreviewer install --uninstall` 能干净地还原
- **轻量**：每个仓库一条命令，只在你真正想 review 的项目里启用

## 安装

### 前置依赖

- macOS（通知机制依赖 `terminal-notifier`）
- git ≥ 2.9
- python3（任意 3.x）
- Homebrew（安装 `jq` + `terminal-notifier` 用）
- **Claude Code** —— 命令 `claude` 必须在 PATH 中。
  安装方式参考官方文档：<https://docs.claude.com/en/docs/claude-code>。
  装好之后请先在终端里跑一次 `claude` 完成登录授权。

### 安装工具本体

```bash
git clone https://github.com/jsaddnf/auto_reviewer_local.git ~/.autoreviewer-src
~/.autoreviewer-src/install.sh
```

（路径只是约定——你想克隆到哪儿都行；`install.sh` 会把自己所在目录写到
`~/.autoreviewer/config.json` 的 `source_dir` 字段，`autoreviewer update`
会读回来用。）

安装脚本会：

1. 检查依赖；如果缺失会询问是否 `brew install jq terminal-notifier`
2. 把文件安装到 `~/.autoreviewer/`
3. 把 `autoreviewer` CLI 安装到 `~/.local/bin/`
4. 把源码克隆位置记录下来，方便日后 `autoreviewer update`
5. **不会动你的 git 配置** —— 我们走的是按仓库开启的方式

### 升级工具本体

```bash
autoreviewer update
```

它会在记录的 `source_dir` 里跑 `git pull --ff-only`，然后帮你重新跑一次
`install.sh --force`。`--no-pull` 表示跳过 pull，仅基于当前磁盘上的代码重装
（在你自己改 autoreviewer 时方便）。

升级会保留你的 `~/.autoreviewer/config.json`：只重写 `source_dir` 字段，新版本
引入的新字段（`notify_start`、`language` 等）会用默认值补齐。**不会覆盖你已经
设过的值**（包括字面量 `false`）。各仓库的安装状态也保留。

### 给某个仓库开启评审

```bash
cd /path/to/repo
autoreviewer install
```

每个想要自动评审的仓库各跑一次即可。该命令是 **幂等** 的——重复执行是安全的，
会提示「已安装」。

随时查看状态：

```bash
autoreviewer status
```

### 关闭某个仓库的评审

```bash
cd /path/to/repo
autoreviewer install --uninstall
```

会干净地撤销 `autoreviewer install` 做过的改动：

- 普通仓库：取消本地 `core.hooksPath`
- Husky/Lefthook 仓库：只移除我们的标记块（如果文件因此空了，则一并删除）

### 完全卸载工具

```bash
./uninstall.sh    # 或：autoreviewer uninstall
```

会删除 `~/.autoreviewer/` 和 CLI。各仓库 `.git/reviews/` 下的评审历史不会
被动；各仓库的安装状态也保留（因为 hooksPath 是仓库本地的设置）。

## 命令一览

```
autoreviewer install [--repo PATH] [--uninstall|--upgrade]
                                             开启 / 关闭 / 升级当前仓库
autoreviewer enable [--repo PATH]            恢复评审（disable 的反操作）
autoreviewer disable [--repo PATH]           暂停评审（不卸载）
autoreviewer status                          显示安装状态、开关、计数
autoreviewer update [--no-pull]              拉最新源码 + 重新安装工具本体
autoreviewer run [<commit>] [options]        手动触发一次评审
                                               --async       后台运行
                                               --no-notify   不发通知
                                               --open        完成后打开 .md
autoreviewer log [-n N] [--severity LEVEL]   列出当前仓库最近的评审
autoreviewer show [<hash>] [--open] [--json] 查看一次评审（默认最新一次）
autoreviewer clean --all | --older-than 30d  清理评审文件
autoreviewer cancel                          杀掉正在跑的评审进程
autoreviewer uninstall                       完全卸载
```

### 同步 vs 异步

- `git commit` 触发 → **异步**（不挡你做事）
- `autoreviewer run` → 默认 **同步**（你手动让我跑，那就当面等结果）
- 可用 `--async` / `--sync` 显式覆盖

### install vs enable / disable —— 不是一回事

- **install / install --uninstall**：物理接线。配置或撤销 hook 触发器。
- **enable / disable**：临时开关。Hook 仍然会被调用，但开关关闭时直接早退。

短期暂停（比如连续提交一串 WIP）用 `disable`；想彻底从仓库里拆掉
autoreviewer 用 `install --uninstall`。

## 配置

### 全局：`~/.autoreviewer/config.json`

```json
{
  "enabled": true,
  "command": "claude",
  "command_args": ["-p"],
  "prompt_file": "~/.autoreviewer/prompts/default.txt",
  "notification": "terminal-notifier",
  "notify_threshold": "low",
  "notify_start": true,
  "language": "zh",
  "auto_open": "on_high",
  "timeout_seconds": 180,
  "disabled_repos": [],
  "source_dir": "/path/where/repo/was/cloned"
}
```

| 字段 | 取值 | 说明 |
|---|---|---|
| `enabled` | `true` / `false` | 全局总开关 |
| `command` | string | 调用的命令（默认 `claude`，即 Claude Code） |
| `command_args` | string[] | 追加给命令的参数。默认 `["-p"]`（Claude Code 的非交互模式） |
| `prompt_file` | path | 评审 prompt 模板路径 |
| `notification` | `terminal-notifier` / `osascript` / `none` | 通知方式 |
| `notify_threshold` | `ok` / `low` / `medium` / `high` | 低于此严重级别不发**完成**通知 |
| `notify_start` | `true` / `false` | hook 触发评审时是否弹「开始评审」通知（默认 `true`）。这条通知由 hook 在 fork python 之前用 shell 同步发出，避免 Python 启动延迟 |
| `language` | `zh` / `en` | 评审输出语言**和** .md / 通知文案的语言（默认 `zh`）。表头元数据（Commit / Author / Date / ...）在两种语言下都保持英文 |
| `auto_open` | `true` / `false` / `on_high` | 完成后是否自动打开 .md |
| `timeout_seconds` | number | 评审超时秒数。Claude Code 评小提交一般 30–60s 完成，diff 大请适当调高 |
| `disabled_repos` | string[] | 被排除的仓库绝对路径列表 |
| `source_dir` | path | 工具源码 repo 的克隆位置（`autoreviewer update` 用），由 `install.sh` 自动设置 |

### 单仓库：`<repo>/.git/autoreviewer.json`

字段同上，会覆盖全局。常见用法是单独换一份 prompt：

```json
{
  "enabled": true,
  "prompt_file": "./.autoreviewer-prompt.md"
}
```

## 输出

每次评审写出两个文件 + 一份索引：

```
<repo>/.git/reviews/
├── 2026-04-27_143205_a3f8c21.json   # 结构化数据
├── 2026-04-27_143205_a3f8c21.md     # 给人看的 Markdown
├── index.json                         # 给 `autoreviewer log` 用
├── .running.pid                       # 当前正在跑的评审进程 PID
└── .last-run.log                      # 上一次后台跑的 stderr
```

Markdown 里包含：

- **总结** + 整体严重级别徽章
- **问题清单** —— 带 `file:line` 的具体问题，每条带级别 + 修复建议
- **影响范围** —— 修改的文件、**修改的调用栈**（diff 触及的函数 / 方法 / 类，
  带 `kind` + `change` 标签）、**可能受影响的调用方**（带调用者符号名 + 影响
  说明）、**行为/逻辑变化**（线程 / 契约 / 默认值等不绑定到单一符号的变化）、
  风险等级
- **测试建议** —— 具体的 `unit` / `integration` / `regression` / `manual`
  用例，每条带 `why` 解释为什么这条测试值得加
- **改进建议** —— 与具体问题无关的整体改进点

如果模型输出无法解析，runner 会先尝试本地修复 JSON（基于栈的括号自动补全），
再尝试让模型自己修复一次；如果都失败，**仍然会写一份降级 .md**（含原始输出
预览），保证通知和 `--open` 始终能落到一个文件上 —— 详见下方常见问题。

## 与已有 hook 的兼容性

`autoreviewer install` **从不修改你项目里被追踪的任何文件**。所有状态都放
在 `<repo>/.git/` 下：

| 我们改的内容 | 位置 | 会被追踪吗？ |
|---|---|---|
| 本地 `core.hooksPath` | `<repo>/.git/config` | ❌ 不会 |
| 保存的 `chained_hooks_path` | `<repo>/.git/autoreviewer.json` | ❌ 不会 |
| 评审产物 | `<repo>/.git/reviews/` | ❌ 不会 |

对于使用 Husky / Lefthook / `.githooks/` 的项目，**你团队的所有 hook 仍会
照常触发**，我们的 shim 只是通过 `_chain` 转发过去。同事克隆仓库时不会看到
任何 autoreviewer 留下的痕迹。

### 老版本（chain 重构之前）的迁移

如果你在 chain 重构之前装过 autoreviewer，可能在 `<repo>/.husky/` 或
`<repo>/.githooks/` 里残留了一份手写的 `post-commit` shim。普通的
`autoreviewer install` 会检测到并拒绝执行，请改用：

```bash
autoreviewer install --upgrade
```

它会：
1. 把老 shim 备份成 `*.bak.<时间戳>`
2. 从 shim 里剥掉 autoreviewer 的触发部分（如果文件因此空了，就删掉）
3. 干净地切换到 chain 模式
4. **如果该 shim 文件被 git 追踪了，会发出警告**，提示你 `git rm` 并
   提交，否则同事们的工作副本里依然带着失效的 autoreviewer 引用

## 兼容图形化 Git 客户端

任何最终调用 `git commit` 的工具都会触发 hook：

- ✅ 命令行 `git commit`
- ✅ Fork
- ✅ SourceTree
- ✅ GitKraken
- ✅ GitHub Desktop
- ✅ Tower
- ✅ VS Code 源代码管理
- ✅ JetBrains 系列 IDE（IntelliJ / PyCharm / WebStorm / GoLand）
- ✅ Lazygit / Magit 等终端 Git 客户端

评审通过 `nohup ... & disown` 完全脱离父进程，所以 GUI 工具永远不会被卡住，
也不会在它的面板里看到我们的输出。

## 常见问题

**提交后没有触发评审。**
先跑 `autoreviewer status`，里面「Current repo」一段会告诉你安装状态。如果
是 `not installed ❌`，就执行 `autoreviewer install`。如果仓库覆盖了
`core.hooksPath` 但还没安装，提示也会明确指出。

**`.last-run.log` 里报 `claude: command not found`。**
说明 Claude Code 没装好，或不在 git hooks 能看到的 PATH 里。请先按
<https://docs.claude.com/en/docs/claude-code> 安装，然后用
`which claude` 确认能找到。如果你的 shell 能找到但 hook 找不到，把
`~/.autoreviewer/config.json` 的 `command` 改成绝对路径
（如 `/opt/homebrew/bin/claude`）。

**首次运行 Claude Code 要求登录。**
请先在普通终端里跑一次 `claude` 完成登录再依赖 hook —— hook 是非交互的，
没法回答登录提示。

**通知弹出来了，但点击没反应。**
确认 `terminal-notifier --help` 正常工作。缺失就
`brew install terminal-notifier`。点击打开依赖 terminal-notifier 的
`-execute` 选项，原生 `osascript` 不支持。

**日志里看到 JSON 解析失败。**
runner 在放弃之前有三层兜底：

1. `extract_json` 依次尝试：直接 parse → 拆 wrapper → 剥 markdown 围栏 →
   贪心地从首个 `{` 到末个 `}` → 基于栈的括号修复（能自动补齐被截断的
   `}` / `]`，且字符串感知不会误补转义里的字符）。
2. 上述都失败时，runner 会另起一轮便宜的对话，**让模型自己修复刚才的
   坏 JSON**（不带 diff，纯结构修复）。
3. 修复也失败时，**仍然会写一份降级 .md**，里面有每一阶段的错误提示 +
   原始输出前 5000 字预览，保证「点击通知打开 .md」的链路不断。

不论走到哪一步，模型的原始输出始终会被存到 `<reviews>/<...>.raw.txt`。

**不想看到「开始评审」通知。**
在 `~/.autoreviewer/config.json`（或某个仓库的
`<repo>/.git/autoreviewer.json`）里设 `"notify_start": false`。
完成通知不受影响，照常按 `notify_threshold` 触发。

**想要英文输出。**
任一份 config 里设 `"language": "en"`，会同时切换模型评审文本 + 渲染 .md /
通知文案。表头元数据（Commit / Author / Date / ...）两种语言下都保持英文。

**评审卡住了。**
`autoreviewer cancel`（或者直接 `kill $(cat .git/reviews/.running.pid)`）。

**我想换别的 LLM 工具。**
改 `~/.autoreviewer/config.json` 里的 `command`（必要时改 `command_args`），
任何「stdin 读 prompt、stdout 打回复」的命令都行。

**我之前装的是全局模式，现在怎么办？**
跑 `./install.sh` 时会有提示。新旧两种都能用。如果想迁到按仓库模式：

```bash
git config --global --unset core.hooksPath
# 然后在每个想要评审的仓库里：
autoreviewer install
```

## 工作原理

**绝不修改你项目的任何被追踪文件**。所有安装状态都放在 `<repo>/.git/` 下，
这部分是本地的、不会被提交。

`autoreviewer install` 会根据当前仓库自动选择两种模式之一：

### 普通仓库（没设置过 `core.hooksPath`）

```
git config --local core.hooksPath ~/.autoreviewer/hooks
```

把这个仓库的 hooks 路径指向我们的共享 hook 目录。

### 仓库已经覆盖了 `core.hooksPath`（Husky / Lefthook / 项目 `.githooks/`）

进入「链式模式（chain mode）」：

```
1. 把项目原本的 hooksPath（如 ".husky"）保存到：
     <repo>/.git/autoreviewer.json   ← 在 .git/ 下，不会被追踪
2. 把本地 core.hooksPath 改为 ~/.autoreviewer/hooks/
3. 我们的 hooks 目录里为每个标准 git hook 都生成了一个 shim
   （commit-msg、pre-commit、pre-push 等）。每个 shim 都会调用
   _chain，由它再去执行项目原本路径下的同名 hook。
4. post-commit 的 shim 还会先触发一次 autoreviewer 评审。
```

效果：你团队的 `commit-msg`、`pre-push` 等 hook 一切正常，外加每次提交都
有一份 review。其他同事毫无感知——他们 `git status` 里看不到任何新文件。

### 提交时的执行链路（不论哪种模式）

```
git commit（命令行 / 图形客户端 / IDE，任何方式）
      │
      ▼
~/.autoreviewer/hooks/post-commit
      │
      ├─→ 后台：python3 runner.py HEAD
      │       ├─→ claude -p   （完整 prompt + diff 经 stdin 传入）
      │       ├─→ 解析 JSON，写入 .git/reviews/<日期>_<hash>.{json,md}
      │       ├─→ 更新 index.json
      │       └─→ terminal-notifier 弹通知（点击即打开 .md）
      │
      └─→ exec _chain post-commit "$@"
              ├─→ chained_hooks_path/post-commit （Husky / Lefthook 场景）
              └─→ <repo>/.git/hooks/post-commit  （普通场景兜底）

# 其他 hook（如 commit-msg）由 git 直接调到我们的 shim：
git commit-msg "$1"
      │
      ▼
~/.autoreviewer/hooks/commit-msg
      │
      └─→ exec _chain commit-msg "$@"
              └─→ <chained-path>/commit-msg 或 <repo>/.git/hooks/commit-msg
```

## License

MIT
