# Git 与临时分支工作流

本文档是项目级 Agent 和协作者的 Git 执行规则。目标是让每项改动可隔离、可审阅、可回滚，并避免不同 Agent 互相覆盖工作。

## 强制规则

- 每个代码、测试或文档改动都必须创建独立的临时分支和 worktree。
- 任务开始时记录当前分支作为基线，通常是 `master`；不得直接在基线分支或其它功能分支上编辑。
- 临时分支使用 `codex/<简短任务名>`；其它 Agent 可使用 `agent/<简短任务名>`。
- 开始前先检查基线工作区。已有未提交改动不得重置、覆盖或自动混入新任务。独立任务从当前已提交 `HEAD` 创建 worktree；确实依赖已有未提交改动时，应先单独识别并处理这些改动。
- 代码、测试和文档在临时分支中完成，并使用中文提交信息。一个完整竖切或一个完整修复对应一个提交。
- 合并前把最新基线同步到临时分支，在临时分支处理全部冲突并重新执行适用检查。
- 检查通过后，只允许使用 fast-forward 合并回基线分支。
- 合并成功后删除临时分支并归档或移除 worktree。未合并、检查未通过或仍有冲突时，禁止删除分支和 worktree。
- 禁止使用 `reset --hard`、强制 checkout、强制推送或删除文件来绕过冲突。语义冲突无法判断时，保留现场并请求确认。

## 单次改动流程

```text
检查当前分支和工作区
        ↓
确定基线分支
        ↓
创建临时分支和独立 worktree
        ↓
实现、测试、提交
        ↓
将最新基线同步到临时分支并解决冲突
        ↓
再次执行适用检查
        ↓
fast-forward 合并到基线分支
        ↓
删除临时分支
        ↓
归档或移除 worktree
        ↓
确认基线工作区状态
```

## Codex 与普通 Git CLI

Codex 优先使用 managed worktree。其它 Agent 或无法使用 managed worktree 时，使用等价的 Git CLI。以下命令中的 `<base>` 是任务开始时记录的基线分支，`<tmp>` 是仓库外的临时目录：

```powershell
git status --short
git branch --show-current
git worktree add -b codex/<task> <tmp> <base>
```

在 `<tmp>` 中完成工作后，提交并同步基线。同步方式可以使用 rebase；必须在临时分支中解决冲突并重新检查：

```powershell
git fetch --all
git rebase <base>
# 解决冲突后：git add <files>，git rebase --continue
# 重新执行适用检查并确认工作树干净
```

回到基线工作区完成合并和清理：

```powershell
git switch <base>
git merge --ff-only codex/<task>
git branch -d codex/<task>
git worktree remove <tmp>
```

如果使用 Codex managed worktree，按同样的生命周期完成提交、审阅、合并和归档。不要把当前工作区的未提交改动复制到临时任务，也不要为了创建 worktree 擅自 stash、reset 或清理用户改动。

## 并行 Agent 与异常恢复

- 并行任务必须一任务一分支一 worktree；不同 Agent 不得共享同一个可写 checkout。
- 合并冲突先在临时分支解决。机械冲突可以由 Agent 处理；涉及产品行为、数据兼容或取舍的语义冲突必须保留冲突现场并请求用户决定。
- 合并前检查失败时留在临时分支修复。合并失败时不得删除临时分支；修复后重新同步基线、检查并合并。
- 如果终端中断，先用 `git worktree list` 和 `git branch --merged <base>` 找回现场，再继续或明确恢复；不得用强制命令掩盖未完成状态。
