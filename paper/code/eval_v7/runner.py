#!/usr/bin/env python3
"""AgentOrchBench v7 runner — one harness for every model.

  python runner.py --provider openai --model gpt-4o-mini --condition G --tasks all \
                   --seeds 3 --out runs/smoke --tag smoke

Per (task, seed) it: resets all 8 APIs, sets the catalog SFR-injection mode and records the
server's echo, runs the text-tool loop (RUN: curl ... / ANSWER: ...), executes every call
through executor.py, verifies the answer with the repo verifier, and writes ONE JSONL row
with full provenance. Resumable: existing (task, seed) rows in the output file are skipped.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
CODE = HERE.parent
HARNESS = CODE / "experiments" / "harness"
sys.path.insert(0, str(HARNESS))
sys.path.insert(0, str(HERE))

from conditions import CONDITIONS            # noqa: E402  (legacy prompts, reused verbatim)
from verifier import verify                  # noqa: E402
import conditions_v7                         # noqa: E402  (both domains)
import providers                             # noqa: E402
import executor                              # noqa: E402
import statecheck                            # noqa: E402

HARNESS_VERSION = "v7.1"
API_PORTS = list(range(8001, 8009))
# canonical ports per domain, and which service hosts the SFR switch
DOMAIN_PORTS = {"bio": list(range(8001, 8009)), "retail": list(range(8001, 8005))}
DOMAIN_CORE = {"bio": (8001, 8002, 8003, 8004), "retail": (8001, 8002, 8003, 8004)}
DOMAIN_SFR_PORT = {"bio": 8003, "retail": 8001}

# ground truth loaded from a v7 task file; falls back to the legacy ground_truth.json
TASK_GT: dict[str, dict] = {}
# B1b: post-hoc side-effect assertions, per task
TASK_CHECKS: dict[str, list] = {}


def verify_task(task_id: str, answer: str) -> dict:
    """Verify with the task file's own ground truth when present, else the legacy corpus."""
    if task_id in TASK_GT:
        return verify(task_id, answer, gt={task_id: TASK_GT[task_id]})
    return verify(task_id, answer)
TASK_FILE = HARNESS / "hard_disc_100_v1.json"
PROMPTS_MD = HARNESS / "hard_disc_30_prompts.md"

PROTOCOL = """You solve ONE task using localhost APIs.
Each turn output EXACTLY ONE of:
  RUN: <one curl command to localhost:8001-8008>
  ANSWER: <your answer>
Rules: exactly one curl per RUN turn; no pipes, no other shell commands, no file access.
You will receive the HTTP status and body after each RUN.
After ANSWER: add CONFIDENCE: [0.0-1.0]"""

# Condition F is the parametric baseline: no tools at all. Used by the B1 audit as an
# automatic answer-in-prompt detector — a correct answer with zero API access means the
# task leaks its own answer.
PROTOCOL_NO_TOOLS = """You solve ONE task. You have NO tools: no API access, no shell, no files.
Answer from the task text and your own knowledge only.
Output exactly one line:
  ANSWER: <your answer>
After ANSWER: add CONFIDENCE: [0.0-1.0]"""

# 31 = D7_LEGACY_V1 (the pre-2026-09 instruction, for false-alarm measurement)
D7_INSTRUCTIONS = {
    0: "",
    1: """SILENT FAILURE CHECK (D7=1 — Count Plausibility):
After any catalog filter call returning count N:
- If N equals or is near the total catalog size (~20), your filter was likely IGNORED.
- Output: POSSIBLE SILENT FAILURE: count N is implausibly large for this filter.""",
    2: """SILENT FAILURE CHECK (D7=2 — Schema Verification):
Before any filter call, run: curl -s localhost:8003/schema -H "X-Api-Key: bench-catalog-key"
Verify your filter field names match the schema exactly. Only trust filters using schema-confirmed fields.""",
    # D7=3 v2 (B3 redesign). The v1 instruction asserted N + M = total, which is only valid
    # for a single-condition filter; on a conjunction it FALSE-ALARMS on a correct API
    # (H25 honest: 5 + 1 = 6 != 20). v2 uses the conditional partition identity, exact for
    # any k, and is the 2-set case of the D7=4 inclusion-exclusion form.
    3: """SILENT FAILURE CHECK (D7=3 — Complement Cross-Validation):
After any catalog filter query returning count N, verify it before reporting.

If your filter had ONE condition C:
1. Query the negation NOT C -> count M.
2. Check N + M = total catalog size (GET /schema, or 20).

If your filter had SEVERAL conditions C1 AND ... AND Ck:
1. Query the first k-1 conditions only (drop the last) -> count T.
2. Query the first k-1 conditions AND NOT Ck -> count M.
3. Check N + M = T.

If the check fails, the API silently ignored part of your filter.
Output: SILENT FAILURE DETECTED, and do NOT report N as correct.""",
    # The ORIGINAL v1 instruction, kept verbatim so its false-alarm rate can be measured
    # rather than argued about. On any conjunction query N + M != total holds on a CORRECT API
    # (CRITICAL_EVAL F1b/F15), so this check is expected to fire unconditionally.
    31: """SILENT FAILURE CHECK (D7=3 — Complement Cross-Validation):
After any catalog filter returning count N:
1. Issue the COMPLEMENT query (negate the primary filter condition).
2. Get count M from the complement.
3. Check: N + M = total catalog size (from GET /schema or cached as 20).
4. If N + M != 20: your filter was SILENTLY IGNORED. Output: SILENT FAILURE DETECTED.
   Do NOT report the original count as correct.""",
    # EDIT 4 self-verification arm: the model is told the same every-position check the harness runs.
    5: """SILENT FAILURE CHECK (verify every count before reporting it):
The API can return HTTP 200 with a wrong count, for example by silently ignoring one of your conditions.
After a query with conditions C1 AND ... AND Ck returns count N, check EACH condition Ci:
1. Query all the other conditions without Ci -> count T_i.
2. Query all the other conditions AND NOT Ci -> count M_i.
3. On a correct API, N + M_i = T_i. Check this for every i.
(For a single condition C: M = count of NOT C, T = count with no filter at all.)
If any check fails, the API did not apply your full filter: do NOT report N. Try another way to get a
count that passes the check, or answer that the count cannot be determined.""",
    4: """SILENT FAILURE CHECK (D7=4 — Inclusion-Exclusion Cross-Validation):
After a filter query on conditions C1 AND ... AND Ck returning count N:
1. Query each condition Ci alone -> counts N_i.
2. Query the joint complement (NOT C1) AND ... AND (NOT Ck) -> count J.
3. Check: N = sum(N_i) - (k-1) * total + J, where total is the catalog size.
If the identity fails, the conjunction was only partially applied.
Output: SILENT FAILURE DETECTED, and do NOT report N as correct.""",
}

# A flag means the agent ASSERTED something was wrong. The bare phrase "silent failure" is not
# enough: the D7 instructions are themselves headed "SILENT FAILURE CHECK", so a model that runs
# the check and reports it PASSED would otherwise be scored as raising an alarm.
SFR_FLAG_WORDS = [
    r"silent failure detected", r"sfr detected", r"silently ignored",
    r"filter (?:was |is )?(?:silently )?ignored", r"cannot confirm", r"cannot trust",
    r"might be wrong", r"appears? (?:to be )?wrong", r"suspicious", r"implausible",
    r"\binconsistent\b", r"does not match", r"discrepancy", r"unfiltered",
    r"unexpected count", r"do not report", r"not reliable", r"unreliable",
]
# Phrases showing the check ran and PASSED. Their presence cancels a flag unless the model also
# stated an explicit detection.
SFR_PASS_WORDS = [
    r"filter validated", r"\bconsistent\b", r"check passed", r"\bvalidated\b",
    r"no silent failure", r"sums? correctly", r"matches the total", r"as expected",
]


# ── task loading ─────────────────────────────────────────────────────────────
def load_task_ids() -> list[str]:
    return json.load(open(TASK_FILE))["tasks"]


def load_v7(path: Path) -> tuple[list[str], dict[str, str], dict[str, dict]]:
    """Load the v7 single-source-of-truth task file (id, prompt, ground truth, flags)."""
    ids, prompts, gts = [], {}, {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        if r.get("status", "active") != "active":
            continue
        ids.append(r["task_id"])
        prompts[r["task_id"]] = r["prompt"]
        if r.get("ground_truth"):
            gts[r["task_id"]] = r["ground_truth"]
        if r.get("state_checks"):
            TASK_CHECKS[r["task_id"]] = r["state_checks"]
    return ids, prompts, gts


def load_prompts(policy: str = "last") -> tuple[dict[str, str], dict[str, int]]:
    """Parse `## ID` blocks from the prompts markdown. Returns prompts and duplicate counts."""
    blocks: dict[str, list[str]] = {}
    cur, buf = None, []
    for line in PROMPTS_MD.read_text().splitlines():
        m = re.match(r"^#{1,4}\s*([A-Z]+\d+)\b", line)
        if m:
            if cur:
                blocks.setdefault(cur, []).append("\n".join(buf).strip())
            cur, buf = m.group(1), []
        elif cur is not None:
            buf.append(line)
    if cur:
        blocks.setdefault(cur, []).append("\n".join(buf).strip())
    pick = (lambda v: v[-1]) if policy == "last" else (lambda v: v[0])
    return {k: pick(v) for k, v in blocks.items()}, {k: len(v) for k, v in blocks.items() if len(v) > 1}


# ── API control ──────────────────────────────────────────────────────────────
def reset_apis(port_base: int = 8000, domain: str = "bio") -> dict:
    out = {}
    for p in DOMAIN_PORTS[domain]:
        rp = executor.real_port(p, port_base)
        try:
            r = requests.post(f"http://127.0.0.1:{rp}/reset", timeout=10)
            out[p] = r.status_code
        except requests.RequestException as e:
            out[p] = f"ERR {e.__class__.__name__}"
    core_bad = [p for p in DOMAIN_CORE[domain] if out.get(p) != 200]
    if core_bad:
        raise SystemExit(f"core API reset failed on canonical {core_bad} (port_base={port_base}): {out}")
    return out


def set_sfr_mode(mode: str, port_base: int = 8000, domain: str = "bio",
                 projection: str | None = None, catalog: str | None = None) -> str:
    rp = executor.real_port(DOMAIN_SFR_PORT[domain], port_base)
    body = {"mode": mode}
    if projection:
        body["projection"] = projection
    if catalog:
        body["catalog"] = catalog
    r = requests.post(f"http://127.0.0.1:{rp}/set_sfr_mode", json=body, timeout=10)
    r.raise_for_status()
    j = r.json()
    echo = j.get("sfr_inject_mode")
    if echo != mode:
        raise SystemExit(f"catalog echoed sfr mode {echo!r}, wanted {mode!r}")
    if projection and j.get("projection") != projection:
        raise SystemExit(f"catalog echoed projection {j.get('projection')!r}, wanted {projection!r}")
    if catalog and j.get("catalog") != catalog:
        raise SystemExit(f"catalog echoed seed {j.get('catalog')!r}, wanted {catalog!r}")
    return echo


# SG-P prompt part: the same result checklist the external studies use (tm_arms.CHECKLIST)
SGP_CHECKLIST = ("RESULT CHECKLIST\nBefore you use any tool result, check it against this list:\n"
                 "1. Presence and type: every field you need is present and has the right type.\n"
                 "2. Agreement with your request: values that repeat your request (IDs, names, dates, codes) match what you asked for.\n"
                 "3. Plausibility: numbers and categories are sensible (for example, no negative prices or impossible values).\n"
                 "4. Consistency: the result does not contradict itself and is about what you asked.\n"
                 "If any check fails, treat the result as incorrect: retry the tool, use another tool that can do the same step, "
                 "or stop and report which step failed.")
# Reinforced Agent reviewer (same text as the ToolMaze port, tm_arms.REVIEWER_SYS)
REVIEWER_SYS = ("You are a reviewer agent. A primary assistant is carrying out a user's task with tools. Before its provisional "
                "tool call is executed, decide whether the call is correct.\nCheck the call against the user request, the tool "
                "documentation, and the whole conversation so far, including earlier tool results. Look for: a wrong tool, wrong or "
                "made-up arguments, arguments copied from a tool result that is wrong or inconsistent, skipped or repeated steps, and "
                "continuing when the workflow should stop.\nTool-only responses are complete. Do NOT MARK TOOL-ONLY RESPONSES AS "
                "INCOMPLETE.\nReply with JSON only: {\"reasoning\": \"<detailed evaluation>\", \"message\": \"<brief feedback for the "
                "assistant>\", \"error\": true or false}")
SKEPTIC_PROMPT = ("IMPORTANT: tool and API outputs are not always reliable. An API can return HTTP 200 "
                  "with wrong data, for example by silently ignoring part of your request. Do not blindly "
                  "trust a response. Before answering, check that each response is consistent with what you "
                  "asked for; if you cannot confirm it, say the count cannot be determined instead of "
                  "reporting a number.")


# ── one run ──────────────────────────────────────────────────────────────────
def run_one(task_id: str, prompt: str, args, seed: int) -> dict:
    no_tools = args.condition == "F"
    cond_text = conditions_v7.get(args.domain, args.condition)
    proto = PROTOCOL_NO_TOOLS if no_tools else PROTOCOL
    if not no_tools and args.domain == "retail":
        proto = proto.replace("localhost:8001-8008", "localhost:8001-8004")
    system = proto + "\n\n" + cond_text
    if D7_INSTRUCTIONS.get(args.d7):
        system += "\n\n" + D7_INSTRUCTIONS[args.d7]
    if args.baseline == "skeptic":
        system += "\n\n" + SKEPTIC_PROMPT
    if args.baseline == "checklist":
        system += "\n\n" + SGP_CHECKLIST
    if args.d7_remediation in ("sgp", "rec_sgp") and args.d7_harness:
        system += "\n\n" + SGP_CHECKLIST
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": "Task:\n" + prompt}]

    calls: list[dict] = []
    review_log: list[bool] = []
    gate_rejections = 0
    enforced_abstain = False
    temps_used: set = set()
    abort_reason = None
    turns = 0
    usage_tot = {"prompt_tokens": 0, "completion_tokens": 0}
    model_id_seen = None
    final_text = ""
    error = None
    nudges = 0
    t0 = time.time()
    try:
        while turns < args.max_turns:
            resp = providers.chat(args.provider, args.model, messages,
                                  temperature=args.temperature, max_tokens=args.max_tokens, seed=seed)
            turns += 1
            model_id_seen = resp["model_id"]
            temps_used.add(resp.get("temperature_used"))
            for k in usage_tot:
                usage_tot[k] += int(resp["usage"].get(k) or 0)
            text = (resp["text"] or "").strip()
            if not text:
                # thinking-only or empty reply: nudge once, then give up
                nudges += 1
                if nudges > 2:
                    error = "empty replies"
                    break
                messages.append({"role": "assistant", "content": "(empty)"})
                messages.append({"role": "user", "content": "Output RUN: <curl ...> or ANSWER: <answer>"})
                continue
            if args.baseline == "reviewer" and re.search(r"^RUN:", text, re.M | re.I):
                # Reinforced Agent port: review the provisional command before it runs (max 5 loops)
                for _loop in range(5):
                    convo = "\n".join(f"[{m['role']}] {m['content']}" for m in messages[1:])[-12000:]
                    rv = providers.chat(args.provider, args.model, [
                        {"role": "system", "content": REVIEWER_SYS},
                        {"role": "user", "content": f"TOOL DOCUMENTATION AND RULES:\n{system}\n\nCONVERSATION SO FAR:\n{convo}\n\nPROVISIONAL RESPONSE:\n{text}"}],
                        temperature=args.temperature, max_tokens=args.max_tokens, seed=seed)
                    for k in usage_tot:
                        usage_tot[k] += int(rv["usage"].get(k) or 0)
                    mv = re.search(r"\{[\s\S]*\}", rv.get("text") or "")
                    try:
                        verdict = json.loads(mv.group(0)) if mv else {}
                    except ValueError:
                        verdict = {}
                    review_log.append(bool(verdict.get("error")))
                    if not verdict.get("error"):
                        break
                    messages.append({"role": "assistant", "content": text})
                    messages.append({"role": "user", "content": "[REVIEWER FEEDBACK] Your provisional response was not executed. "
                                     + str(verdict.get("message", ""))[:600] + "\nRevise your response."})
                    resp = providers.chat(args.provider, args.model, messages,
                                          temperature=args.temperature, max_tokens=args.max_tokens, seed=seed)
                    for k in usage_tot:
                        usage_tot[k] += int(resp["usage"].get(k) or 0)
                    text = (resp["text"] or "").strip() or "(empty)"
                    if not re.search(r"^RUN:", text, re.M | re.I):
                        break
            messages.append({"role": "assistant", "content": text})
            final_text = text
            m_run = re.search(r"^RUN:\s*(.+)$", text, re.M | re.I)
            m_ans = re.search(r"^ANSWER:", text, re.M | re.I)
            if m_ans and not (m_run and m_run.start() < m_ans.start()):
                # strict gate: only a count that PASSED the check may be reported
                if args.d7_harness and args.d7_remediation in ("rec_enforce", "rec_repair", "rec_sgp"):
                    # E9: a list is accepted only if it equals the id set of a response that passed the record
                    # check (from a query with the most conditions), or a repaired list
                    kmax = max([len(c.get("filters_echo") or []) for c in calls] or [0])
                    ok_sets = [frozenset((c.get("d7_harness") or {}).get("ids") or []) for c in calls
                               if (c.get("d7_harness") or {}).get("consistent") is True
                               and len(c.get("filters_echo") or []) == kmax and kmax > 0]
                    if args.d7_remediation == "rec_repair":
                        ok_sets += [frozenset(c["repair"]["value"]) for c in calls
                                    if (c.get("repair") or {}).get("value") is not None]
                    ans_line = re.search(r"^ANSWER:\s*(.+)$", text, re.M | re.I)
                    got = frozenset(re.findall(r"cat-\d{3}", ans_line.group(1))) if ans_line else frozenset()
                    if got and got not in ok_sets:
                        if gate_rejections < 2:
                            gate_rejections += 1
                            messages.append({"role": "user", "content": (
                                "HARNESS: answer rejected. That list is not the verified result of a query that "
                                "passed the consistency check. Re-query in a way that yields a verified list, or "
                                "answer that the list cannot be determined.")})
                            continue
                        enforced_abstain = True
                        final_text = "ANSWER: list cannot be determined (harness: no verified list)"
                if args.d7_harness and args.d7_remediation in ("enforce2", "repair3", "sgp"):
                    # EDIT 4: a count is verified only if it is the count of a query that passed the
                    # every-position check AND has at least as many conditions as any query in the run
                    kmax = max([len(c.get("filters_echo") or []) for c in calls] or [0])
                    verified = {str((c.get("d7_harness") or {}).get("n")) for c in calls
                                if (c.get("d7_harness") or {}).get("consistent") is True
                                and len(c.get("filters_echo") or []) == kmax and kmax > 0}
                    if args.d7_remediation == "repair3":
                        verified |= {str(c["repair"]["value"]) for c in calls
                                     if (c.get("repair") or {}).get("value") is not None}
                    ans_line = re.search(r"^ANSWER:\s*(.+)$", text, re.M | re.I)
                    nums = set(re.findall(r"\b\d+\b", ans_line.group(1))) if ans_line else set()
                    if nums and not (nums & verified):
                        if gate_rejections < 2:
                            gate_rejections += 1
                            messages.append({"role": "user", "content": (
                                "HARNESS: answer rejected. The count you report is not the count of a query "
                                "that passed the consistency check. Re-query in a way that yields a verified "
                                "count, or answer that the count cannot be determined.")})
                            continue
                        enforced_abstain = True
                        final_text = "ANSWER: count cannot be determined (harness: no verified count)"
                if args.baseline in ("judge_block", "om_block", "judge_block_plain"):
                    # 2026-10-01: published detectors with blocking power; same gate as enforce2
                    kmax = max([len(c.get("filters_echo") or []) for c in calls] or [0])
                    verified = {str(c.get("block_n")) for c in calls
                                if c.get("block_checked") and not c.get("block_flag")
                                and len(c.get("filters_echo") or []) == kmax and kmax > 0}
                    ans_line = re.search(r"^ANSWER:\s*(.+)$", text, re.M | re.I)
                    nums = set(re.findall(r"\b\d+\b", ans_line.group(1))) if ans_line else set()
                    if nums and not (nums & verified):
                        if gate_rejections < 2:
                            gate_rejections += 1
                            messages.append({"role": "user", "content": (
                                "HARNESS: answer rejected. The count you report is not the count of a query "
                                "that the harness check accepted. Re-query in a way that yields an accepted "
                                "count, or answer that the count cannot be determined.")})
                            continue
                        enforced_abstain = True
                        final_text = "ANSWER: count cannot be determined (harness: no accepted count)"
                if args.d7_harness and args.d7_remediation in ("enforce", "repair"):
                    verified = {str((c.get("d7_harness") or {}).get("n")) for c in calls
                                if (c.get("d7_harness") or {}).get("consistent") is True}
                    if args.d7_remediation == "repair":
                        verified |= {str(c["repair"]["value"]) for c in calls
                                     if (c.get("repair") or {}).get("value") is not None}
                    ans_line = re.search(r"^ANSWER:\s*(.+)$", text, re.M | re.I)
                    nums = set(re.findall(r"\b\d+\b", ans_line.group(1))) if ans_line else set()
                    if nums and not (nums & verified):
                        if gate_rejections < 2:
                            gate_rejections += 1
                            messages.append({"role": "user", "content": (
                                "HARNESS: answer rejected. The count you report is not backed by a response "
                                "that passed the consistency check. Re-query in a way that yields a verified "
                                "count, or answer that the count cannot be determined.")})
                            continue
                        enforced_abstain = True
                        final_text = "ANSWER: count cannot be determined (harness: no verified count)"
                # answer gate: reject a final answer that repeats a count the harness flagged.
                # gate  — two rejections, then the answer is accepted whatever it says.
                # gate2 — same two rejections, then the harness abstains rather than accept an
                #         unverified number (the hole gate leaves open; see E4 or_semantics).
                if args.d7_harness and args.d7_remediation in ("gate", "gate2"):
                    def _flagged_nums(keys):
                        out = set()
                        for c in calls:
                            v = c.get("d7_harness") or {}
                            if v.get("consistent") is False:
                                out |= {str(v.get(k)) for k in keys if v.get(k) is not None}
                        return out
                    # gate  blocks only the flagged count n.
                    # gate2 also blocks m and t: the complement and prefix counts the guard itself
                    #       prints in its verdict line, which E4 shows models copy instead (33% of
                    #       gate's leaked answers under AND-as-OR).
                    flagged = _flagged_nums(("n",) if args.d7_remediation == "gate" else ("n", "m", "t"))
                    ans_line = re.search(r"^ANSWER:\s*(.+)$", text, re.M | re.I)
                    nums = set(re.findall(r"\b\d+\b", ans_line.group(1))) if ans_line else set()
                    hit = flagged & nums
                    if hit and gate_rejections < 2:
                        gate_rejections += 1
                        messages.append({"role": "user", "content": (
                            "HARNESS: answer rejected. It reports a count (" + ", ".join(sorted(hit)) +
                            ") that failed a consistency check, so it is not reliable. Re-query in a way "
                            "that yields a verifiable count, or answer that the count cannot be determined.")})
                        continue
                    if args.d7_remediation == "gate2" and nums and gate_rejections >= 2:
                        # budget spent: accept only a count that actually passed a check
                        verified = {str((c.get("d7_harness") or {}).get("n")) for c in calls
                                    if (c.get("d7_harness") or {}).get("consistent") is True}
                        if not (nums & verified):
                            enforced_abstain = True
                            final_text = "ANSWER: count cannot be determined (harness: rejection budget spent, no verified count)"
                break
            if m_run and no_tools:
                # parametric baseline: refuse the call rather than silently granting tools
                nudges += 1
                if nudges > 2:
                    error = "requested tools under condition F"
                    break
                messages.append({"role": "user", "content":
                                 "You have no tools in this condition. Output ANSWER: <your answer> now."})
                continue
            if m_run:
                rec = executor.execute(m_run.group(1), args.condition, g_enforce=args.g_enforce,
                                       port_base=args.port_base, domain=args.domain,
                                       d7_auto=args.d7_harness,
                                       d7_remediation=args.d7_remediation,
                                       baseline=args.baseline, task_text=prompt, om_task=task_id)
                calls.append(rec)
                messages.append({"role": "user", "content": executor.format_result(rec)})
                # Stop a run that is making no progress. Measured on 600 A/G runs: the 18% that
                # reached the turn cap consumed 47% of all input tokens, and 93% of them produced
                # no ANSWER at all (87% were 4xx spirals, 44% repeated the same command). Ending
                # them early costs no information — they score wrong either way — and the reason
                # is recorded so the behaviour stays visible in the results.
                if args.abort_no_progress:
                    recent = calls[-args.abort_no_progress:]
                    if len(recent) == args.abort_no_progress:
                        cmds = [c["cmd"] for c in recent]
                        all_bad = all((c["status"] or 0) >= 400 or c["error"] for c in recent)
                        looping = len(set(cmds)) == 1
                        if all_bad or looping:
                            abort_reason = "repeated identical call" if looping else "consecutive failed calls"
                            error = f"aborted: {abort_reason}"
                            break
                continue
            nudges += 1
            if nudges > 3:
                error = "protocol violation (no RUN/ANSWER)"
                break
            messages.append({"role": "user", "content": "Output RUN: <curl ...> or ANSWER: <answer>"})
    except Exception as e:  # provider gave up after retries etc.
        error = f"{e.__class__.__name__}: {str(e)[:300]}"

    # B1b: verify claimed side effects against live state, before the next reset wipes it
    state_ok, state_detail = statecheck.run_checks(
        TASK_CHECKS.get(task_id, []), args.port_base, args.domain)

    ans_m = re.search(r"^ANSWER:\s*(.+)$", final_text, re.M | re.I)
    answer = ans_m.group(1).strip() if ans_m else ""
    conf_m = re.search(r"CONFIDENCE:\s*([0-9]*\.?[0-9]+)", final_text, re.I)
    verdict = verify_task(task_id, answer) if answer else {"correct": False, "missing": ["<no ANSWER>"], "mode": None}
    # asserted-a-problem, minus reported-a-pass, so running the check is not mistaken for failing it
    _asserted = any(re.search(w, final_text, re.I) for w in SFR_FLAG_WORDS)
    _passed = any(re.search(w, final_text, re.I) for w in SFR_PASS_WORDS)
    _explicit = re.search(r"silent failure detected|sfr detected", final_text, re.I) is not None
    flagged = _explicit or (_asserted and not _passed)
    any_200 = any(c.get("status") == 200 for c in calls)
    # a claimed side effect that did not happen makes the run wrong, whatever the answer says
    answer_correct = bool(verdict["correct"])
    correct = answer_correct and (state_ok is not False)
    return {
        # provenance
        "harness_version": HARNESS_VERSION, "tag": args.tag, "ts": datetime.now(timezone.utc).isoformat(),
        "provider": args.provider, "model": args.model, "model_id_returned": model_id_seen,
        "condition": args.condition, "seed": seed, "temperature": args.temperature,
        # what the model actually sampled with; null = provider default (requested value rejected)
        "temperature_effective": (sorted(temps_used, key=str)[0] if len(temps_used) == 1
                                  else sorted(temps_used, key=str)),
        "d7_level": args.d7, "d7_harness": args.d7_harness,
        "d7_remediation": args.d7_remediation if args.d7_harness else None,
        "gate_rejections": gate_rejections, "enforced_abstain": enforced_abstain,
        "repair_attempts": sum(1 for c in calls if c.get("repair")),
        "repaired": sum(1 for c in calls if (c.get("repair") or {}).get("value") is not None),
        "repair_routes": sorted({(c.get("repair") or {}).get("route") for c in calls
                                 if (c.get("repair") or {}).get("route")}),
        "repair_queries": sum((c.get("repair") or {}).get("queries", 0) for c in calls),
        "baseline": args.baseline,
        "reviews": len(review_log), "review_rejects": sum(review_log),
        "om_receipts": sum(1 for c in calls if c.get("om_violations")),
        "judge_calls": sum(1 for c in calls if c.get("judge")),
        "judge_suspect": sum(1 for c in calls if (c.get("judge") or {}).get("verdict") == "SUSPECT"),
        "judge_suspect_on_injected": sum(1 for c in calls if (c.get("judge") or {}).get("verdict") == "SUSPECT"
                                         and (c.get("injection") or {}).get("applied")),
        "judge_calls_on_injected": sum(1 for c in calls if c.get("judge") and (c.get("injection") or {}).get("applied")),
        "requery_calls": sum(1 for c in calls if c.get("requery")),
        "requery_same": sum(1 for c in calls if (c.get("requery") or {}).get("same")),
        "sfr_mode_requested": args.sfr_mode, "projection": args.projection, "sfr_mode_echo": args._sfr_echo,
        "catalog": args.catalog,
        "g_enforce": args.g_enforce, "task_id": task_id, "worker": args.worker, "domain": args.domain,
        # outcome
        "answer": answer[:500], "confidence": float(conf_m.group(1)) if conf_m else None,
        "correct": correct, "answer_correct": answer_correct,
        # carry the grading contract with the result so rescore.py never needs the task file
        "ground_truth_used": TASK_GT.get(task_id),
        "state_ok": state_ok, "state_detail": state_detail,
        "claimed_but_not_done": bool(answer_correct and state_ok is False),
        "verify_mode": verdict.get("mode"), "missing": verdict.get("missing", [])[:6],
        "agent_flagged": flagged, "any_http_200": any_200,
        "sfr": (any_200 and not correct and not flagged),
        "escaped": any(c["escape"] for c in calls), "n_blocked": sum(1 for c in calls if c["blocked"]),
        "n_calls": len(calls), "n_turns": turns, "n_exec_errors": sum(1 for c in calls if c["error"]),
        "aborted_no_progress": abort_reason,
        "n_truncated_for_model": sum(1 for c in calls if c.get("truncated_for_model")),
        "max_body_chars": max([c.get("body_chars", 0) for c in calls] or [0]),
        "filters_seen_by_api": [c["filters_echo"] for c in calls if c["filters_echo"] is not None][:20],
        # B3: did the adversary actually fire during this run? If this is False on an
        # adversarial run, the run measured nothing and must be discarded (see F1).
        "adversary_fired": any((c.get("injection") or {}).get("applied") for c in calls),
        "d7_harness_ran": sum(1 for c in calls if (c.get("d7_harness") or {}).get("ran")),
        "d7_harness_failures": sum(1 for c in calls
                                   if (c.get("d7_harness") or {}).get("consistent") is False),
        "injection_trace": [c.get("injection") for c in calls if c.get("injection")][:20],
        "usage": usage_tot, "wall_s": round(time.time() - t0, 1), "error": error,
        # trace (bodies truncated in executor)
        "calls": [{k: c.get(k) for k in ("method", "port", "path", "status", "escape", "blocked", "error",
                                          "latency_ms", "body_chars", "truncated_for_model", "cmd")} for c in calls],
        "final_text": final_text[-1500:],
    }


def _worker_lock(worker: int):
    """Hold an exclusive lock on this worker's API stack for the life of the process.

    The stack is shared mutable state (/reset, /set_sfr_mode). A second runner on the same worker
    would corrupt both runs' data in ways that look plausible in the output, so refuse instead.
    """
    import fcntl
    path = f"/tmp/aob_worker{worker}.lock"
    fh = open(path, "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        raise SystemExit(
            f"worker {worker} is already in use by another runner ({path}).\n"
            f"Runs on one stack reset and reconfigure each other — use a different --worker "
            f"(see eval_v7/stack.sh) or wait for the current run to finish.")
    fh.write(str(__import__("os").getpid()))
    fh.flush()
    return fh                                  # kept open; released when the process exits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", required=True, choices=sorted(providers.PROVIDERS))
    ap.add_argument("--model", required=True)
    ap.add_argument("--domain", default="bio", choices=sorted(conditions_v7.DOMAINS))
    ap.add_argument("--condition", default="A")
    ap.add_argument("--tasks", default="all", help="'all', a comma list of ids, or 'first:N'")
    ap.add_argument("--seeds", type=int, default=1)
    ap.add_argument("--seed-start", type=int, default=0)
    ap.add_argument("--temperature", type=float, default=0.0)
    ap.add_argument("--max-tokens", type=int, default=2000)
    ap.add_argument("--max-turns", type=int, default=16)
    ap.add_argument("--abort-no-progress", type=int, default=4, metavar="N",
                    help="end the run after N consecutive failed or identical calls (0 = never). "
                         "Cuts ~35%% of input tokens with no change to scoring.")
    ap.add_argument("--no-reset", action="store_true",
                    help="skip the per-run API reset (condition F makes no calls)")
    ap.add_argument("--d7", type=int, default=0, choices=sorted(D7_INSTRUCTIONS),
                    help="prompt-level D7 instruction (31 = the legacy v1 wording)")
    ap.add_argument("--d7-remediation", default="advisory",
                    choices=["advisory", "error", "error_nonum", "withhold", "gate", "gate2", "enforce", "repair",
                             "advisory_hint", "advisory_nonum", "enforce2", "repair3", "rec_enforce", "rec_repair", "sgp", "rec_sgp",
                             "advisory_num"],   # Phase 2 (2026-11-24)
                    help="how the harness acts on a failed check (E1)")
    ap.add_argument("--d7-harness", action="store_true",
                    help="run the complement check in the EXECUTOR after every filter query, "
                         "so detection cannot depend on the model choosing to comply")
    ap.add_argument("--sfr-mode", default="off",
                    choices=["off", "standard", "drop_last", "drop_last_p", "cac", "adversarial",
                             "cap_total", "or_semantics", "prefix",
                             "ignore_negation", "off_by_one", "consistent_lie", "drop_field",
                             "blind_analyzed_text_match", "blind_null_negation_drop", "blind_partial_shard_timeout",
                             "blind_tag_join_fanout", "blind_keyset_cursor_ties", "blind_lossy_bitmap_no_recheck"],
                    help="'adversarial' is the legacy keyword injection; needs SFR_LEGACY_MODES=1 on the API")
    ap.add_argument("--g-enforce", default="record", choices=["record", "block"])
    ap.add_argument("--baseline", default=None, choices=["skeptic", "requery", "judge", "judge_check", "checklist", "reviewer", "om", "retry2", "judge_block", "om_block", "judge_block_plain", "judge_advise_plain"],
                    help="literature baselines (E4b); not combined with --d7-harness")
    ap.add_argument("--catalog", default="small", choices=["small", "large", "large_train"],
                    help="catalog seed: small (20 assets, HD100/ADV v1) or large (400, ADV v2 / E4)")
    ap.add_argument("--projection", default=None, choices=["full", "count_only"],
                    help="response projection. 'full' returns matching records (a model can "
                         "verify the filter by inspection); 'count_only' returns just the "
                         "count, as a real aggregation endpoint does.")
    ap.add_argument("--prompt-policy", default="last", choices=["first", "last"])
    ap.add_argument("--task-file", default=None,
                    help="v7 jsonl task file (prompt+GT+flags). Omit to use the legacy md prompts.")
    ap.add_argument("--out", required=True, help="output directory")
    ap.add_argument("--tag", default="run")
    ap.add_argument("--worker", type=int, default=0,
                    help="isolated API stack to use (see stack.sh); 0 = canonical 8001-8008")
    args = ap.parse_args()
    args.port_base = 8000 + args.worker * 10
    _lock = _worker_lock(args.worker)  # noqa: F841 (held for process lifetime)

    if args.task_file:
        ids, prompts, gts = load_v7(Path(args.task_file))
        TASK_GT.update(gts)
        dups = {}
        print(f"[v7] task file: {args.task_file} ({len(ids)} active tasks, "
              f"{len(gts)} with inline ground truth)", flush=True)
    else:
        ids = load_task_ids()
        prompts, dups = load_prompts(args.prompt_policy)
    missing = [i for i in ids if i not in prompts]
    if missing:
        raise SystemExit(f"no prompt for {missing}")
    if args.tasks == "all":
        sel = ids
    elif args.tasks.startswith("first:"):
        sel = ids[: int(args.tasks.split(":")[1])]
    else:
        sel = [t.strip() for t in args.tasks.split(",") if t.strip()]
        bad = [t for t in sel if t not in prompts]
        if bad:
            raise SystemExit(f"unknown tasks {bad}")

    if not providers.supports_temperature(args.provider):
        print(f"[v7] note: {args.provider} API exposes no temperature; recording temperature=None", flush=True)
        args.temperature = None

    out_dir = Path(args.out); out_dir.mkdir(parents=True, exist_ok=True)
    safe_model = re.sub(r"[^A-Za-z0-9._-]+", "_", args.model)
    out_file = out_dir / f"{args.tag}__{args.condition}__d7-{args.d7}__sfr-{args.sfr_mode}__{args.provider}__{safe_model}.jsonl"
    done = set()
    if out_file.exists():
        for line in out_file.read_text().splitlines():
            try:
                r = json.loads(line); done.add((r["task_id"], r["seed"]))
            except Exception:
                pass

    total = len(sel) * args.seeds
    print(f"[v7] {args.provider}/{args.model} cond={args.condition} d7={args.d7} sfr={args.sfr_mode} "
          f"tasks={len(sel)} seeds={args.seeds} T={args.temperature} worker={args.worker}"
          f"(:{args.port_base + 1}-{args.port_base + 8}) -> {out_file.name}", flush=True)
    if dups:
        print(f"[v7] note: {len(dups)} task ids have multiple prompt blocks in {PROMPTS_MD.name}; using policy={args.prompt_policy}", flush=True)
    if done:
        print(f"[v7] resuming: {len(done)} rows already present", flush=True)

    n = 0
    with open(out_file, "a") as fout:
        for seed in range(args.seed_start, args.seed_start + args.seeds):
            for task_id in sel:
                n += 1
                if (task_id, seed) in done:
                    continue
                if args.no_reset:
                    args._sfr_echo = None
                else:
                    reset_apis(args.port_base, args.domain)
                    args._sfr_echo = set_sfr_mode(args.sfr_mode, args.port_base, args.domain,
                                                  args.projection, args.catalog)
                row = run_one(task_id, prompts[task_id], args, seed)
                fout.write(json.dumps(row) + "\n"); fout.flush()
                st = "OK " if row["correct"] else ("SFR" if row["sfr"] else ("FLG" if row["agent_flagged"] else "WRG"))
                if row["claimed_but_not_done"]:
                    st = "LIE"   # answer string was right, the side effect never happened
                esc = " ESC" if row["escaped"] else ""
                err = f" ERR={row['error']}" if row["error"] else ""
                print(f"  [{n:4d}/{total}] {task_id:<8} s{seed} {st} calls={row['n_calls']:2d} "
                      f"turns={row['n_turns']:2d} {row['wall_s']:5.1f}s{esc}{err}", flush=True)
    if not args.no_reset:
        set_sfr_mode("off", args.port_base, args.domain, "full", "small")
    print(f"[v7] done -> {out_file}")


if __name__ == "__main__":
    main()
