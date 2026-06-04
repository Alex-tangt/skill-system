---
name: hello-world
description: A simple demonstration skill that echoes input through a two-step DAG pipeline. Use when testing the Skill Engine execution flow or verifying skill_execute is working correctly.
license: MIT
metadata:
  author: skill-engine
  version: "1.0"
---

# Hello World

A minimal two-step echo pipeline for testing Skill Engine execution.

## Workflow
1. Step `echo1` echoes the input text
2. Step `echo2` processes echo1's output
3. Results are stored in SQLite trace database

## Input
- `text`: Any string to echo

## Output
- Final echoed text from step `echo2`
