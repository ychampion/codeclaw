"""Session trajectory classifier heuristics with weighted signal scoring."""

from __future__ import annotations

from dataclasses import dataclass


CORRECTION_SIGNALS = (
    "wrong",
    "error",
    "that's not",
    "fix",
    "broken",
    "failed",
    "doesn't work",
    "incorrect",
    "bug",
    "not what i",
    "that won't",
    "not right",
    "regression",
)

DEBUG_SIGNALS = (
    "error",
    "traceback",
    "stack trace",
    "failing",
    "failed test",
    "exception",
    "segfault",
    "assertionerror",
    "lint failed",
    "test failed",
)

REFACTOR_SIGNALS = (
    "refactor",
    "clean up",
    "rewrite",
    "simplify",
    "restructure",
    "reorganize",
    "consolidate",
    "tech debt",
)

BUILD_SIGNALS = (
    "implement",
    "add feature",
    "create",
    "build",
    "scaffold",
    "ship",
)

DEBUG_TOOLS = {
    "bash",
    "python",
    "execute",
    "pytest",
    "ruff",
    "mypy",
}

EDIT_TOOLS = {
    "read",
    "write",
    "edit",
    "glob",
    "grep",
}


@dataclass
class _Score:
    correction_loop: float = 0.0
    debugging_trace: float = 0.0
    iterative_build: float = 0.0
    refactor: float = 0.0
    sft_clean: float = 0.0


def _lower(value: object) -> str:
    return str(value or "").lower()


def _contains_any(text: str, signals: tuple[str, ...]) -> bool:
    return any(signal in text for signal in signals)


def _tool_names(messages: list[dict]) -> list[str]:
    names: list[str] = []
    for message in messages:
        for tool_use in message.get("tool_uses", []):
            names.append(_lower(tool_use.get("tool")))
    return names


def classify_trajectory(session: dict) -> str:
    """Assign a trajectory label from weighted conversational/tool signals."""
    messages = session.get("messages", [])
    if not isinstance(messages, list) or not messages:
        return "sft_clean"

    score = _Score(sft_clean=0.2)
    tool_names = _tool_names(messages)
    has_debug_tool = any(name in DEBUG_TOOLS for name in tool_names)
    has_edit_tool = any(name in EDIT_TOOLS for name in tool_names)

    # User correction loops: user follows assistant with correction intent.
    for idx, message in enumerate(messages):
        if _lower(message.get("role")) != "user" or idx == 0:
            continue
        previous_role = _lower(messages[idx - 1].get("role"))
        if previous_role != "assistant":
            continue
        content = _lower(message.get("content"))
        if _contains_any(content, CORRECTION_SIGNALS):
            score.correction_loop += 2.0
            score.debugging_trace += 0.5

    # Textual error/debug context from any role.
    for message in messages:
        content = _lower(message.get("content"))
        if _contains_any(content, DEBUG_SIGNALS):
            score.debugging_trace += 1.5
        if _contains_any(content, CORRECTION_SIGNALS):
            score.correction_loop += 0.5

    # Intent signals from the first user request.
    first_user = next((m for m in messages if _lower(m.get("role")) == "user"), None)
    if first_user:
        prompt = _lower(first_user.get("content"))
        if _contains_any(prompt, REFACTOR_SIGNALS):
            score.refactor += 2.2
        if _contains_any(prompt, BUILD_SIGNALS):
            score.iterative_build += 1.0

    # Tool-based shaping.
    if has_debug_tool:
        score.debugging_trace += 1.2
        score.iterative_build += 0.4
    if has_edit_tool:
        score.iterative_build += 0.8
        score.refactor += 0.3

    # Long sessions with many tool calls tend to iterative build.
    tool_events = len(tool_names)
    if len(messages) >= 8:
        score.iterative_build += 1.0
    if tool_events >= 6:
        score.iterative_build += 1.1

    # Strong priority guards for high-confidence classes.
    if score.correction_loop >= 2.0:
        return "correction_loop"
    if has_debug_tool and score.debugging_trace >= 1.8:
        return "debugging_trace"
    if score.refactor >= 2.0:
        return "refactor"
    if score.iterative_build >= 2.0:
        return "iterative_build"

    return "sft_clean"
