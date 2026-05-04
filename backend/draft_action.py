"""XML tag parser for draft action signals (begin / continue / section / replace).

Mirrors backend.stage_ack pattern. Parsed events drive _gate_canonical_draft_tool_call
in chat.py. See spec docs/superpowers/specs/2026-05-04-context-signal-and-intent-tag-design.md §4.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal


@dataclass
class DraftActionEvent:
    raw: str
    intent: Literal["begin", "continue", "section", "replace"]
    section_label: str | None = None
    old_text: str | None = None
    new_text: str | None = None
    start: int = 0
    end: int = 0
    executable: bool = True
    ignored_reason: str | None = None


SIMPLE_PATTERN = re.compile(
    r'<draft-action>(begin|continue|section:[^<\n]{1,80})</draft-action>',
    re.IGNORECASE,
)

REPLACE_PATTERN = re.compile(
    r'<draft-action-replace>\s*'
    r'<old>([\s\S]{1,1000}?)</old>\s*'
    r'<new>([\s\S]{1,1000}?)</new>\s*'
    r'</draft-action-replace>',
    re.IGNORECASE,
)


class DraftActionParser:
    STRIP_PATTERN = re.compile(
        r'<draft-action>[^<\n]+</draft-action>'
        r'|<draft-action-replace>[\s\S]*?</draft-action-replace>',
        re.IGNORECASE,
    )

    FENCED_RE = re.compile(r"^( {0,3})(```|~~~)", re.MULTILINE)

    def parse_raw(self, content: str) -> list[DraftActionEvent]:
        events: list[DraftActionEvent] = []
        if not content:
            return events
        # simple tags
        for m in SIMPLE_PATTERN.finditer(content):
            payload = m.group(1)
            if payload.startswith("section:"):
                intent = "section"
                section_label = payload[len("section:"):].strip()
            else:
                intent = payload  # begin / continue
                section_label = None
            events.append(DraftActionEvent(
                raw=m.group(0),
                intent=intent,
                section_label=section_label,
                start=m.start(), end=m.end(),
            ))
        # replace blocks
        for m in REPLACE_PATTERN.finditer(content):
            events.append(DraftActionEvent(
                raw=m.group(0),
                intent="replace",
                old_text=m.group(1),
                new_text=m.group(2),
                start=m.start(), end=m.end(),
            ))
        events.sort(key=lambda e: e.start)
        return events

    def parse(self, content: str) -> list[DraftActionEvent]:
        events = self.parse_raw(content)
        if not events:
            return []
        fenced_spans = self._fenced_spans(content)
        tail_anchor = self._tail_anchor(content, events)
        for event in events:
            reason = self._classify_position(content, event, fenced_spans, tail_anchor)
            if reason is not None:
                event.executable = False
                event.ignored_reason = reason
        return events

    def strip(self, content: str) -> str:
        if not content or "draft-action" not in content.lower():
            return content
        result = self.STRIP_PATTERN.sub("", content)
        return re.sub(r"\n{3,}", "\n\n", result)

    def _classify_position(self, content, event, fenced_spans, tail_anchor):
        for s, e in fenced_spans:
            if s <= event.start < e:
                return "in_fenced_code"
        line_start = content.rfind("\n", 0, event.start) + 1
        # 对 multi-line replace block，end 也要看 line_end
        line_end_nl = content.find("\n", event.start)
        line_end = line_end_nl if line_end_nl != -1 else len(content)
        before = content[line_start:event.start]
        # event.end 跨多行（replace）特殊处理：取 event.end 之后的行
        after_end_nl = content.find("\n", event.end)
        after_end = after_end_nl if after_end_nl != -1 else len(content)
        after = content[event.end:after_end]

        if re.match(r"^\s*>", before):
            return "in_blockquote"
        if before.count("`") % 2 == 1:
            return "in_inline_code"
        if before.strip() or after.strip():
            return "not_independent_line"
        if event.start < tail_anchor:
            return "not_tail"
        return None

    def _fenced_spans(self, content):
        spans = []
        open_start = None
        open_fence = None
        for m in self.FENCED_RE.finditer(content):
            fence = m.group(2)
            if open_start is None:
                open_start = m.start()
                open_fence = fence
            elif fence == open_fence:
                line_end_nl = content.find("\n", m.end())
                close_end = line_end_nl + 1 if line_end_nl != -1 else len(content)
                spans.append((open_start, close_end))
                open_start = None
                open_fence = None
        if open_start is not None:
            spans.append((open_start, len(content)))
        return spans

    def _tail_anchor(self, content, events):
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
