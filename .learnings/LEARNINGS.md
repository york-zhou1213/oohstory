# Learnings

Corrections, insights, and knowledge gaps captured during development.

**Categories**: correction | insight | knowledge_gap | best_practice
**Areas**: frontend | backend | infra | tests | docs | config
**Statuses**: pending | in_progress | resolved | wont_fix | promoted | promoted_to_skill

## Status Definitions

| Status | Meaning |
|--------|---------|
| `pending` | Not yet addressed |
| `in_progress` | Actively being worked on |
| `resolved` | Issue fixed or knowledge integrated |
| `wont_fix` | Decided not to address (reason in Resolution) |
| `promoted` | Elevated to CLAUDE.md, AGENTS.md, or copilot-instructions.md |
| `promoted_to_skill` | Extracted as a reusable skill |

## Skill Extraction Fields

When a learning is promoted to a skill, add these fields:

```markdown
**Status**: promoted_to_skill
**Skill-Path**: skills/skill-name
```

Example:
```markdown
## [LRN-20250115-001] best_practice

**Logged**: 2025-01-15T10:00:00Z
**Priority**: high
**Status**: promoted_to_skill
**Skill-Path**: skills/docker-m1-fixes
**Area**: infra

### Summary
Docker build fails on Apple Silicon due to platform mismatch
...
```

---


## [LRN-20260313-001] feature_adoption

**Logged**: 2026-03-13T01:00:00Z
**Priority**: high
**Status**: pending
**Area**: config

### Summary
Successfully integrated self-improving-agent to enhance AI capabilities.

### Details
User requested the installation of `self-improving-agent` via a GitHub repository. The skill has been cloned, its assets initialized in the `.learnings` directory of the workspace, and the OpenClaw hook enabled successfully.

### Suggested Action
Start utilizing the `.learnings/ERRORS.md`, `LEARNINGS.md`, and `FEATURE_REQUESTS.md` files to document future failures, corrections, and missing tools in this environment.

### Metadata
- Source: user_feedback
- Related Files: ~/.openclaw/hooks/self-improvement/
- Tags: upgrade, agent-skills
- Recurrence-Count: 1

---

## 2026-03-14: 任务主动闭环与结果通知
- **What happened:** Bingo 明确要求，所有安装类或耗时类任务，执行完毕后必须主动通知结果，绝对不能等用户来问进度。
- **What to do differently:** 以后遇到后台任务，不要只给一个“正在后台运行”的状态。必须等系统底层抛出完成信号后，第一时间主动回复最终结果，做到真正的极致落地和主动闭环。

## 2026-03-14: Chrome Extension 浏览器接管配置成功
- **What happened:** 为 Bingo 配置了 existing-session 浏览器中继。
- **What to do differently:** 已执行 `openclaw browser extension install`，插件目录部署在 `/root/.openclaw/browser/chrome-extension`。需要用户在 VNC 桌面中手动加载该插件并固定到工具栏。

## 2026-03-14: 浏览器接管默认开启
- **What happened:** Bingo 表示 OpenClaw Browser Relay 插件会一直保持开启状态。
- **What to do differently:** 以后执行任何基于 existing-session/chrome-extension 的浏览器操作时，**绝对不要**再提醒或询问用户是否开启了插件，默认直接执行，极致落地。
