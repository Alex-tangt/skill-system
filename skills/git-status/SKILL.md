---
name: git-status
description: Show current git repository status — branch, changes, recent commits. Use when the user asks about repo state, what changed, or recent work.
license: MIT
---
# Git Status

Show the current state of the git repository.

## Workflow
1. Run `git status` to show working tree state
2. Run `git log --oneline -5` to show recent commits
3. Summarize: current branch, changed files, recent activity

## Input
- None

## Output
- Current branch name
- Modified/untracked files
- Last 5 commits
