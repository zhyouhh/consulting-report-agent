"""Neutral streaming parsers shared between chat and the independent review agent.

This module exists to break a would-be import cycle: both ``backend.chat`` and
``backend.independent_review`` need ``ThinkingStreamParser`` to strip ``<think>``
blocks out of streamed provider deltas. ``chat`` re-exports the class for backward
compatibility; ``independent_review`` imports from here directly and never imports
``chat`` (which, post-C5, imports ``independent_review`` — importing ``chat`` back
would form ``chat -> independent_review -> chat``).
"""

from __future__ import annotations


class ThinkingStreamParser:
    NORMAL = "NORMAL"
    INSIDE_THINK = "INSIDE_THINK"
    OPEN_TAG = "<think>"
    CLOSE_TAG = "</think>"

    def __init__(self):
        self.state = self.NORMAL
        self._buffer = ""

    def feed(self, delta: str) -> list[dict]:
        if not delta:
            return []

        self._buffer += delta
        events = []

        while self._buffer:
            if self.state == self.NORMAL:
                open_idx = self._buffer.find(self.OPEN_TAG)
                if open_idx == -1:
                    self._emit_safe_prefix(events, "content", self.OPEN_TAG)
                    break

                self._append_event(events, "content", self._buffer[:open_idx])
                self._buffer = self._buffer[open_idx + len(self.OPEN_TAG):]
                self.state = self.INSIDE_THINK
                continue

            close_idx = self._buffer.find(self.CLOSE_TAG)
            if close_idx == -1:
                self._emit_safe_prefix(events, "thinking", self.CLOSE_TAG)
                break

            self._append_event(events, "thinking", self._buffer[:close_idx])
            self._buffer = self._buffer[close_idx + len(self.CLOSE_TAG):]
            self.state = self.NORMAL

        return events

    def flush(self) -> list[dict]:
        events = []
        if self._buffer:
            event_type = "thinking" if self.state == self.INSIDE_THINK else "content"
            events = [{"type": event_type, "data": self._buffer}]
        self._buffer = ""
        self.state = self.NORMAL
        return events

    def _emit_safe_prefix(self, events: list[dict], event_type: str, tag: str) -> None:
        hold_len = self._partial_tag_tail_len(self._buffer, tag)
        emit_len = len(self._buffer) - hold_len
        if emit_len <= 0:
            return

        self._append_event(events, event_type, self._buffer[:emit_len])
        self._buffer = self._buffer[emit_len:]

    @staticmethod
    def _append_event(events: list[dict], event_type: str, data: str) -> None:
        if data:
            events.append({"type": event_type, "data": data})

    @staticmethod
    def _partial_tag_tail_len(text: str, tag: str) -> int:
        max_len = min(len(tag) - 1, len(text))
        for length in range(max_len, 0, -1):
            if tag.startswith(text[-length:]):
                return length
        return 0
