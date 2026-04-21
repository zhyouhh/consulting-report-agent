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
