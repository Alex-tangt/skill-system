from __future__ import annotations

import re
from dataclasses import dataclass, field

from skill_engine.models.skill import (
    SkillDefinition,
    StepDefinition,
    Criteria,
    RetryPolicy,
)


@dataclass
class SubStepBlueprint:
    id: str
    name: str
    description: str
    depends_on: list[str] = field(default_factory=list)
    input_fields: dict[str, str] = field(default_factory=dict)  # field -> description
    output_fields: dict[str, str] = field(default_factory=dict)
    is_verification: bool = False
    tool: str = "echo"


SEQUENTIAL_ZH = r"(?:首先|第一步|先|然后|接着|其次|之后|再|最后|最终)"
SEQUENTIAL_EN = r"(?:first|firstly|step\s*1|start\s*by|then|next|secondly|step\s*2|after\s*that|thirdly|step\s*3|finally|lastly|end\s*with|step\s*4)"

SEQUENTIAL_MARKERS = [
    rf"(?:{SEQUENTIAL_EN}|{SEQUENTIAL_ZH})",
    rf"(?:then|next|secondly|step\s*2|after\s*that|接着|其次|然后)",
    rf"(?:then|next|thirdly|step\s*3|after\s*that|之后|再|接着)",
    rf"(?:finally|lastly|end\s*with|step\s*4|最后|最终)",
]

PARALLEL_MARKERS = [
    r"(?:同时|独立地?|并行|in\s*parallel|independently|separately)",
    r"(?:一边|while|at\s+the\s+same\s+time)",
]

VERIFICATION_MARKERS = [
    r"(?:验证|检查|确认|确保|校验|测试|check|verify|validate|ensure|confirm|test|assert)",
]


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9-]", "-", text.lower()).strip("-")


def _extract_phrases(text: str) -> list[str]:
    """Split description into sub-task phrases using all applicable rules."""
    results = _split_text(text)
    if len(results) > 1:
        # Apply splitting recursively to each fragment
        final = []
        for fragment in results:
            sub = _split_text(fragment)
            if len(sub) > 1:
                final.extend(sub)
            else:
                final.append(fragment)
        return [p.strip().rstrip(".") for p in final if p.strip()]
    return [text.strip()]


def _split_text(text: str) -> list[str]:
    """Single-pass split of text into sub-task phrases."""
    # Numbered items: "1. do X\n2. do Y"
    numbered = re.split(r"(?:^|\n)\s*(?:\d+[\.\)]\s*)", text, flags=re.MULTILINE)
    if len(numbered) > 1:
        return [p.strip() for p in numbered if p.strip()]

    # "and independently", "和...独立" — parallel tasks
    parts = re.split(
        r"\s+and\s+(?:independently|separately|in\s+parallel)\s+"
        r"|和(?:\S*)(?:独立|并行|同时|分别)",
        text, flags=re.IGNORECASE,
    )
    if len(parts) > 1:
        return [p.strip() for p in parts if p.strip()]

    # Comma/punctuation + sequential markers: ", then", ". Then", "，然后", "；接着", etc.
    phrases = re.split(
        r"(?:,\s*|，\s*|\.\s+|。\s*|;\s*|；\s*)"
        r"(?=(?:then|next|after\s+that|finally|lastly|secondly|thirdly|first|firstly"
        r"|然后|接着|之后|再|最后|最终|其次|首先))",
        text, flags=re.IGNORECASE,
    )
    if len(phrases) > 1:
        return [p.strip() for p in phrases if p.strip()]

    # Chinese sequential markers without punctuation: "然后", "接着", "最后"
    phrases = re.split(
        r"(?<=[\w\S])\s*(?=(?:然后|接着|之后|再|最后|最终|其次))",
        text,
    )
    if len(phrases) > 1:
        return [p.strip() for p in phrases if p.strip()]

    # ". " or "。" sentence boundaries
    sentences = re.split(r"\.\s+|。\s*", text)
    sentences = [s.strip() for s in sentences if s.strip()]
    if len(sentences) > 1:
        return sentences

    return [text]


def _identify_data_flow(phrases: list[str]) -> list[SubStepBlueprint]:
    """Identify input/output fields for each sub-step based on the description text."""
    blueprints = []

    for i, phrase in enumerate(phrases):
        step_id = f"step-{i + 1}"
        words = phrase.lower()
        is_verification = any(
            re.search(m, words) for m in VERIFICATION_MARKERS
        )

        # Extract likely inputs from the phrase
        inputs: dict[str, str] = {}
        outputs: dict[str, str] = {}

        # Common patterns for inputs
        input_patterns = [
            (r"(?:the|a|an)\s+(\w+)\s+(?:file|code|text|data|input|source)", "input_data"),
            (r"(?:from|using|with|given|based\s+on)\s+(?:the\s+)?(\w+)", "context"),
            (r"(?:read|load|fetch|get|receive)\s+(?:the\s+)?(\w+)", "source"),
            (r"(\w+)\s+(?:as\s+input|provided|supplied|given)", "input"),
        ]

        for pattern, default_name in input_patterns:
            m = re.search(pattern, words)
            if m:
                val = m.group(1) if m.lastindex and m.lastindex >= 1 else m.group(0)
                field_name = _slugify(val)
                if len(field_name) > 3:
                    inputs.setdefault(default_name, val)

        # Extract outputs
        output_patterns = [
            (r"(?:generate|produce|create|output|return|write)\s+(?:a\s+)?(\w+)", "result"),
            (r"(?:report|summary|analysis|review|fix|patch|result)", "output"),
        ]

        for pattern, default_name in output_patterns:
            m = re.search(pattern, words)
            if m:
                val = m.group(1) if m.lastindex and m.lastindex >= 1 else m.group(0)
                outputs.setdefault(default_name, val)

        # If no specific IO detected, use generic placeholders
        if not inputs:
            inputs["input"] = "Skill input data"
        if not outputs:
            if is_verification:
                outputs["status"] = "Verification status (ok/failed)"
            else:
                outputs["result"] = f"Result of step {i+1}"

        # Determine dependencies — default: sequential chain
        depends = []
        if i > 0:
            depends.append(f"step-{i}")

        bp = SubStepBlueprint(
            id=step_id,
            name=_derive_step_name(phrase, i),
            description=phrase.strip().rstrip("."),
            depends_on=depends,
            input_fields=inputs,
            output_fields=outputs,
            is_verification=is_verification,
        )
        blueprints.append(bp)

    return blueprints


def _derive_step_name(phrase: str, index: int) -> str:
    """Derive a concise step name from the phrase."""
    words = phrase.strip().split()
    if len(words) <= 4:
        return phrase.strip().rstrip(".")
    # Take first 4-5 meaningful words
    meaningful = [w for w in words if w.lower() not in {"the", "a", "an", "to", "of", "for", "and"}]
    name = " ".join(meaningful[:5])
    if len(name) > 40:
        name = name[:37] + "..."
    return name if name else f"Step {index + 1}"


def _detect_parallel_groups(blueprints: list[SubStepBlueprint]) -> list[SubStepBlueprint]:
    """Detect steps that can run in parallel (no dependency between them)."""
    for i, bp in enumerate(blueprints):
        if i == 0:
            continue
        words = bp.description.lower()

        # Explicit parallel markers override everything
        implicitly_parallel = any(
            re.search(m, words) for m in PARALLEL_MARKERS
        )
        if implicitly_parallel:
            bp.depends_on = []
            continue

        # Explicit sequential markers: KEEP the dependency
        has_sequential_marker = any(
            re.match(m, words) for m in SEQUENTIAL_MARKERS[1:]
        )
        if has_sequential_marker:
            continue  # Keep dependency

        # No explicit markers: check if there's actual data dependency
        if bp.depends_on and bp.depends_on[0] == f"step-{i}":
            prev_bp = blueprints[i - 1]
            prev_outputs = set(prev_bp.output_fields.keys())
            my_inputs = set(bp.input_fields.keys())
            if not prev_outputs & my_inputs and not prev_bp.is_verification:
                bp.depends_on = []

    return blueprints


def decompose_task(description: str, skill_name: str = "") -> SkillDefinition:
    """Analyze a natural language task description and produce a modular skill definition.

    The decomposition follows the core principle: each sub-step must be independently
    verifiable (success/failure can be determined in isolation).
    """
    phrases = _extract_phrases(description)
    blueprints = _identify_data_flow(phrases)
    blueprints = _detect_parallel_groups(blueprints)

    if not skill_name:
        skill_name = "Generated Skill"
    skill_id = _slugify(skill_name) if skill_name != "Generated Skill" else _slugify(description[:30])

    steps = []
    for bp in blueprints:
        # Build input_schema fields
        input_mapping = {}
        input_properties = {}
        for field_name, field_desc in bp.input_fields.items():
            input_mapping[field_name] = f"$input.{field_name}"
            input_properties[field_name] = {"type": "string", "description": field_desc}

        # Build output for this step
        output_properties = {}
        for field_name, field_desc in bp.output_fields.items():
            output_properties[field_name] = {"type": "string", "description": field_desc}

        # Success criteria: verification steps need explicit check
        if bp.is_verification:
            success = Criteria(type="always")
        else:
            success = Criteria(type="always")

        step = StepDefinition(
            id=bp.id,
            name=bp.name,
            description=bp.description,
            tool=bp.tool,
            depends_on=bp.depends_on,
            input_mapping=input_mapping,
            success_criteria=success,
            failure_criteria=Criteria(type="exception"),
            retry=RetryPolicy(max_attempts=1),
            timeout_seconds=60,
        )
        steps.append(step)

    # Build top-level input/output schemas from all step IOs
    all_inputs: dict[str, dict] = {}
    all_outputs: dict[str, dict] = {}
    for bp in blueprints:
        for fname, fdesc in bp.input_fields.items():
            if fname not in all_inputs:
                all_inputs[fname] = {"type": "string", "description": fdesc}
        for fname, fdesc in bp.output_fields.items():
            if fname not in all_outputs:
                all_outputs[fname] = {"type": "string", "description": fdesc}

    return SkillDefinition(
        id=skill_id,
        name=skill_name,
        description=description,
        tags=["generated", "modular"],
        input_schema={
            "type": "object",
            "properties": all_inputs,
            "required": list(all_inputs.keys()),
        },
        output_schema={
            "type": "object",
            "properties": all_outputs,
        },
        steps=steps,
    )
