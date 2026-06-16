---
name: pipeline-analyzer
description: Analyzes agent execution traces to assess skill effectiveness and produce actionable improvement diagnoses. Used by the Skill-System pipeline Phase A.
version: 1.0.0
---

# Pipeline Analyzer

Analyze an agent's execution trace to produce a clear, evidence-backed diagnosis of what happened and what should be improved.

## Input

You receive:
- **Task Context**: What the user asked for, plus prev/next messages
- **Skills Involved**: The SKILL.md content of any skills that may have been used
- **Execution Trace**: The full agent execution (assistant thoughts, tool calls, tool results)

## Analysis Method

1. **Task completion**: Was the user's request fulfilled? If not, what blocked it? Be specific — name the tool, error message, or step where things went wrong.

2. **Skill effectiveness**: For each skill involved:
   - Was it applied? Did the agent follow its guidance?
   - Did it help (saved time, prevented errors) or hinder (caused confusion, wasted iterations)?
   - What specific evidence supports your assessment? Quote tool call names, error messages, step numbers from the trace.

3. **Concrete problems**: Identify exactly what went wrong. Examples:
   - "Step 3 says use `--retry` but curl 8.x requires `--retries`"
   - "Skill assumes `requests` is installed, but sandbox has no network"
   - "Agent spent 2 iterations discovering that the API key format is wrong"

## Output Format

Write your analysis in natural language prose. Structure it as:

```
SUMMARY: One-line verdict. Did the task succeed? Was any skill helpful or harmful?

DETAIL:
- Specific observations with trace evidence (tool names, error messages, step numbers).
- For each skill: applied? helpful? where did it fall short?
- If no skills were used: what patterns or reusable approaches did the agent discover?

SUGGESTIONS:
- What should change? Be specific about which step, parameter, or approach.
- If a new skill should be captured, what pattern should it encode?
```

## Quality Standards

- **Evidence-first**: Every claim must be traceable to a specific tool call or error message in the execution trace. If the trace is ambiguous, say so.
- **Actionable**: Suggestions must be specific enough that someone could edit the SKILL.md without re-reading the full trace.
- **Honest about uncertainty**: If the trace is incomplete, or a skill failure could have multiple causes, acknowledge this instead of guessing.
- **Brief**: Focus on what matters. A 200-word diagnosis that correctly identifies one real problem is better than 2000 words of generic observations.

## Meta-Analysis Notes

This skill is itself subject to evolution by the Meta Signal Detector. After each analysis, the following signals are tracked:
- Did the diagnosis correctly identify problems later confirmed by validation?
- Were suggestions specific enough to produce usable patches?
- Did the analysis miss problems that later caused regression?

If this skill produces consistently low-signal analyses (no actionable suggestions, low-confidence diagnoses), the Meta Signal Detector will update this SKILL.md.
