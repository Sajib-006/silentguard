"""Harness fix (2026-09-26): keep only the FIRST tool call of a reply in the conversation history.

Some models (gpt-oss-120b in 85-99% of runs) write several tool calls and made-up tool results in
one reply. The harness only ever ran the first call, but the rest stayed in the history, so the
model later read its own fake results. After this fix, a reply that contains a tool call is stored
up to the end of that call. Nothing else changes: same prompts, same parser, same arms.

Import this module BEFORE creating agents; it patches tm_arms.ArmAgent.step in place. The frozen
files are not edited; this file has its own hash in EXTERNAL_FREEZE.sha256.
"""
from __future__ import annotations

import json

import tm_arms as T

FIX_TAG = "first-call-only-v1"


def first_call_span(text):
    """End index (exclusive) of the first balanced {...} that parses as a tool call, else None."""
    i = 0
    while True:
        i = text.find("{", i)
        if i < 0:
            return None
        depth, j, in_str, esc = 0, i, False, False
        while j < len(text):
            ch = text[j]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        if depth != 0:
            return None
        try:
            obj = json.loads(text[i:j + 1])
            if isinstance(obj, dict) and "tool" in obj:
                return j + 1
        except json.JSONDecodeError:
            pass
        i += 1


def cut_after_first_call(text):
    end = first_call_span(text or "")
    if end is None:
        return text
    head = text[:end]
    if head.count("```") % 2 == 1:          # close an open code fence
        head += "\n```"
    return head


_orig_step = T.ArmAgent.step


def _step(self, user_message=None):
    act = _orig_step(self, user_message)
    if act.type == "tool_call" and self.messages and self.messages[-1].get("role") == "assistant":
        self.messages[-1]["content"] = cut_after_first_call(self.messages[-1]["content"])
    return act


T.ArmAgent.step = _step
