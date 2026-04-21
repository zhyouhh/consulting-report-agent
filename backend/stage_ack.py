"""XML tag parser for stage checkpoint acknowledgment signals.

Per 2026-04-21 design spec §2: assistant outputs <stage-ack>KEY</stage-ack>
or <stage-ack action="clear">KEY</stage-ack> at the tail of a reply to advance
or rollback a stage checkpoint. This module parses those tags out of assistant
content.

  raw_match -> classify (position + key) -> execute/ignore -> strip

Position judgment (fenced/inline/blockquote/non-tail) is in parse().
Chat runtime hookup (tag priority over keywords, prereq validation, soft
gate) is in `backend/chat.py`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


VALID_KEYS = frozenset({
    "s0_interview_done_at",
    "outline_confirmed_at",
    "review_started_at",
    "review_passed_at",
    "presentation_ready_at",
    "delivery_archived_at",
})

TAG_PATTERN = re.compile(
    r'<stage-ack(?:\s+action="(set|clear)")?>([a-z_0-9]+)</stage-ack>',
    re.IGNORECASE,
)


@dataclass
class StageAckEvent:
    raw: str
    action: str
    key: str
    start: int
    end: int
    executable: bool = True
    ignored_reason: str | None = None


class StageAckParser:
    """Parse and strip stage-ack tags from assistant content.

    - `parse_raw(content)` finds every well-formed <stage-ack>KEY</stage-ack>
      occurrence (including unknown keys), in order. Unknown keys yield events
      flagged executable=False / ignored_reason="unknown_key" so the caller
      can log a warning without dropping the strip obligation.
    - `parse(content)` runs parse_raw then position-classifies each event,
      setting executable=False with ignored_reason ∈ {unknown_key, in_fenced_code,
      in_inline_code, in_blockquote, not_independent_line, not_tail}.
    - `strip(content)` removes every tag span regardless of executable flag.
    """

    def parse_raw(self, content: str) -> list[StageAckEvent]:
        if not content:
            return []
        events: list[StageAckEvent] = []
        for match in TAG_PATTERN.finditer(content):
            action = match.group(1) or "set"
            key = match.group(2)
            if key in VALID_KEYS:
                executable = True
                ignored = None
            else:
                executable = False
                ignored = "unknown_key"
            events.append(StageAckEvent(
                raw=match.group(0),
                action=action,
                key=key,
                start=match.start(),
                end=match.end(),
                executable=executable,
                ignored_reason=ignored,
            ))
        return events

    FENCED_RE = re.compile(r"^( {0,3})(```|~~~)", re.MULTILINE)

    def parse(self, content: str) -> list[StageAckEvent]:
        if not content:
            return []
        events = self.parse_raw(content)
        if not events:
            return []

        fenced_spans = self._fenced_spans(content)
        tail_anchor = self._tail_anchor(content, events)

        for event in events:
            if event.ignored_reason == "unknown_key":
                continue  # already non-executable
            reason = self._classify_position(content, event, fenced_spans, tail_anchor)
            if reason is not None:
                event.executable = False
                event.ignored_reason = reason
        return events

    def _classify_position(
        self,
        content: str,
        event: StageAckEvent,
        fenced_spans: list[tuple[int, int]],
        tail_anchor: int,
    ) -> str | None:
        # Fenced code has highest precedence
        for start, end in fenced_spans:
            if start <= event.start < end:
                return "in_fenced_code"

        # Line-local context
        line_start = content.rfind("\n", 0, event.start) + 1
        line_end_nl = content.find("\n", event.end)
        line_end = line_end_nl if line_end_nl != -1 else len(content)
        before = content[line_start:event.start]
        after = content[event.end:line_end]

        # Blockquote: optional whitespace then `>`
        if re.match(r"^\s*>", before):
            return "in_blockquote"

        # Inline code: odd count of backticks before on same line
        if before.count("`") % 2 == 1:
            return "in_inline_code"

        # Independent line: only whitespace flanking on the same line
        if before.strip() or after.strip():
            return "not_independent_line"

        # Tail: event must start at or after the last non-tag non-whitespace
        if event.start < tail_anchor:
            return "not_tail"

        return None

    def _fenced_spans(self, content: str) -> list[tuple[int, int]]:
        spans: list[tuple[int, int]] = []
        open_start: int | None = None
        open_fence: str | None = None
        for match in self.FENCED_RE.finditer(content):
            fence = match.group(2)
            fence_line_start = match.start()
            if open_start is None:
                open_start = fence_line_start
                open_fence = fence
            elif fence == open_fence:
                line_end_nl = content.find("\n", match.end())
                close_end = line_end_nl + 1 if line_end_nl != -1 else len(content)
                spans.append((open_start, close_end))
                open_start = None
                open_fence = None
        if open_start is not None:
            spans.append((open_start, len(content)))
        return spans

    def _tail_anchor(
        self,
        content: str,
        events: list[StageAckEvent],
    ) -> int:
        """Return offset one past the last non-tag non-whitespace char.

        Any event starting >= this offset is at the tail.
        """
        tag_spans = [(e.start, e.end) for e in events]
        last_pos = -1
        i = 0
        while i < len(content):
            in_tag = False
            for ts, te in tag_spans:
                if ts <= i < te:
                    i = te
                    in_tag = True
                    break
            if in_tag:
                continue
            if not content[i].isspace():
                last_pos = i
            i += 1
        return last_pos + 1
