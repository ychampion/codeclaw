"""Layered privacy engine with deterministic + optional ML detection."""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from typing import Any

from .anonymizer import Anonymizer
from .secrets import redact_custom_strings, redact_session, redact_text, scan_text

__all__ = [
    "Anonymizer",
    "RedactionFinding",
    "RedactionEngine",
    "redact_text",
    "redact_session",
    "redact_custom_strings",
    "scan_text",
    "redact_all_sessions",
    "redact_session_with_findings",
]


@dataclass
class RedactionFinding:
    source: str  # "regex" | "ml"
    category: str
    text: str
    score: float
    start: int | None = None
    end: int | None = None


class RedactionEngine:
    """Privacy scan/redaction pipeline with optional ML entity detection."""

    def __init__(
        self,
        engine: str = "auto",
        model_size: str = "small",
        confidence_threshold: float = 0.55,
    ) -> None:
        self.engine = engine
        self.model_size = model_size
        self.confidence_threshold = confidence_threshold
        self._ml_ready = False
        self._analyzer = None
        self._init_ml_backend()

    def _init_ml_backend(self) -> None:
        if self.engine == "regex":
            return
        try:
            from presidio_analyzer import AnalyzerEngine
        except ImportError:
            self._ml_ready = False
            return

        try:
            self._analyzer = AnalyzerEngine()
            self._ml_ready = True
        except Exception:
            self._ml_ready = False
            self._analyzer = None

    @property
    def ml_available(self) -> bool:
        return self._ml_ready and self._analyzer is not None

    def scan(self, text: str) -> list[RedactionFinding]:
        findings: list[RedactionFinding] = []
        if not text:
            return findings

        for item in scan_text(text):
            findings.append(
                RedactionFinding(
                    source="regex",
                    category=str(item.get("type", "sensitive")),
                    text=str(item.get("match", "")),
                    score=1.0,
                    start=item.get("start"),
                    end=item.get("end"),
                )
            )

        if self.engine == "regex":
            return _dedupe_findings(findings)
        findings.extend(self._scan_ml(text))
        return _dedupe_findings(findings)

    def _scan_ml(self, text: str) -> list[RedactionFinding]:
        if not self.ml_available:
            return []
        assert self._analyzer is not None
        try:
            results = self._analyzer.analyze(text=text, language="en")
        except Exception:
            return []

        findings: list[RedactionFinding] = []
        for result in results:
            score = float(getattr(result, "score", 0.0) or 0.0)
            if score < self.confidence_threshold:
                continue
            start = int(getattr(result, "start", 0) or 0)
            end = int(getattr(result, "end", 0) or 0)
            if end <= start:
                continue
            snippet = text[start:end]
            if not snippet.strip():
                continue
            findings.append(
                RedactionFinding(
                    source="ml",
                    category=str(getattr(result, "entity_type", "PII")),
                    text=snippet,
                    score=score,
                    start=start,
                    end=end,
                )
            )
        return findings

    def redact_text(self, text: str, custom_strings: list[str] | None = None) -> tuple[str, int, list[RedactionFinding]]:
        findings = self.scan(text)
        redacted, baseline = redact_text(text)
        custom_count = 0
        if custom_strings:
            redacted, custom_count = redact_custom_strings(redacted, custom_strings)

        ml_applied = 0
        for finding in findings:
            if finding.source != "ml":
                continue
            if finding.text and finding.text in redacted:
                redacted = redacted.replace(finding.text, "[REDACTED:ML_PII]")
                ml_applied += 1
        return redacted, baseline + custom_count + ml_applied, findings


def _dedupe_findings(findings: list[RedactionFinding]) -> list[RedactionFinding]:
    seen: set[tuple[str, str, str]] = set()
    out: list[RedactionFinding] = []
    for finding in findings:
        key = (finding.source, finding.category, finding.text)
        if key in seen:
            continue
        seen.add(key)
        out.append(finding)
    return out


def _iter_text_fields(session: dict[str, Any]) -> list[tuple[str, str]]:
    fields: list[tuple[str, str]] = []
    for i, message in enumerate(session.get("messages", [])):
        content = str(message.get("content", "") or "")
        if content:
            fields.append((f"messages[{i}].content", content))
        thinking = str(message.get("thinking", "") or "")
        if thinking:
            fields.append((f"messages[{i}].thinking", thinking))
        for j, tool_use in enumerate(message.get("tool_uses", [])):
            tool_input = str(tool_use.get("input", "") or "")
            if tool_input:
                fields.append((f"messages[{i}].tool_uses[{j}].input", tool_input))
    return fields


def redact_session_with_findings(
    session: dict[str, Any],
    custom_strings: list[str] | None = None,
    engine: RedactionEngine | None = None,
) -> tuple[dict[str, Any], int, list[dict[str, Any]]]:
    """Redact a session and return merged scan findings for review UX."""
    redacted_session, base_count = redact_session(copy.deepcopy(session), custom_strings=custom_strings)
    if engine is None:
        return redacted_session, base_count, []

    merged_findings: list[dict[str, Any]] = []
    extra_count = 0
    for field_path, text in _iter_text_fields(session):
        _, count, findings = engine.redact_text(text, custom_strings=custom_strings)
        extra_count += max(0, count - len(scan_text(text)))
        for finding in findings:
            merged_findings.append(
                {
                    "field": field_path,
                    "source": finding.source,
                    "category": finding.category,
                    "score": round(float(finding.score), 3),
                    "text": finding.text[:120],
                }
            )
    return redacted_session, base_count + extra_count, merged_findings


def redact_all_sessions(
    sessions: list[dict],
    custom_strings: list[str] | None = None,
    engine: RedactionEngine | None = None,
) -> tuple[list[dict], int]:
    """Redact secrets from a list of sessions."""
    total_redactions = 0
    redacted = []
    for session in sessions:
        session, count, _ = redact_session_with_findings(
            session,
            custom_strings=custom_strings,
            engine=engine,
        )
        total_redactions += count
        redacted.append(session)
    return redacted, total_redactions


def extract_context_snippets(text: str, finding_text: str, window: int = 60) -> list[str]:
    """Return compact snippets around matches for diff/review UX."""
    if not text or not finding_text:
        return []
    snippets: list[str] = []
    for match in re.finditer(re.escape(finding_text), text, re.IGNORECASE):
        start = max(0, match.start() - window)
        end = min(len(text), match.end() + window)
        snippets.append(text[start:end].replace("\n", " "))
        if len(snippets) >= 5:
            break
    return snippets
