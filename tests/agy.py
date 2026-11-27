#!/usr/bin/env python3

import json
import os
import re
import sqlite3
import subprocess
import sys
import urllib.parse

import harness

FAILED = []
PLUGIN_CMD = os.path.join(harness.PLUGIN, "transcripts")


def check(name, ok, detail=""):
    print(("  ok   " if ok else "  FAIL ") + name + ("" if ok or not detail else f"\n       {detail}"))
    if not ok:
        FAILED.append(name)


ANSI = re.compile(r"\x1b\[[0-9;]*m")


def run(sb, *args, **env):
    full = sb.env(**env)
    proc = subprocess.run([PLUGIN_CMD, *args], env=full, capture_output=True, text=True, timeout=60)
    proc.stdout = ANSI.sub("", proc.stdout)
    return proc


def set_scope(sb, scope):
    cfg_dir = os.path.join(sb.root, ".config", "herdr", "plugins", "config", "transcripts")
    os.makedirs(cfg_dir, exist_ok=True)
    with open(os.path.join(cfg_dir, "config.toml"), "w") as fh:
        fh.write(f'scope = "{scope}"\n')


TITLED = "aaaaaaaa-0000-4000-8000-000000000001"
UNTITLED = "aaaaaaaa-0000-4000-8000-000000000002"
SUBAGENT = "aaaaaaaa-0000-4000-8000-000000000003"


def step(kind, source, **fields):
    record = {"step_index": 0, "source": source, "type": kind, "status": "DONE",
              "created_at": "2026-11-20T10:00:00Z"}
    record.update(fields)
    return record


def build(sb):
    base = os.path.join(sb.root, ".gemini", "antigravity-cli")
    cwd = os.path.join(sb.root, "code", "orbit lab")
    os.makedirs(cwd, exist_ok=True)
    convs = {
        TITLED: [step("SYSTEM_MESSAGE", "SYSTEM", content="system prompt noise quokkasystem"),
                 step("USER_INPUT", "USER_EXPLICIT", content="plan the quokka migration for the orbit lab"),
                 step("PLANNER_RESPONSE", "MODEL", content="The quokka migration has three steps.",
                      thinking="hidden quokkathought", tool_calls=[{"name": "run_command",
                                                                   "args": {"CommandLine": "ls quokkadir"}}]),
                 step("GENERIC", "MODEL", content="quokkatooloutput listing")],
        UNTITLED: [step("USER_INPUT", "USER_EXPLICIT", content="wombat checklist for the trip"),
                   step("PLANNER_RESPONSE", "MODEL", content="Here is the wombat checklist.")],
        SUBAGENT: [step("USER_INPUT", "USER_EXPLICIT", content="subagent numbatprompt"),
                   step("PLANNER_RESPONSE", "MODEL", content="numbat reply")],
    }
    for sid, steps in convs.items():
        logs = os.path.join(base, "brain", sid, ".system_generated", "logs")
        os.makedirs(logs, exist_ok=True)
        with open(os.path.join(logs, "transcript_full.jsonl"), "w") as fh:
            for i, record in enumerate(steps):
                record["step_index"] = i
                fh.write(json.dumps(record, separators=(",", ":")) + "\n")
    db = sqlite3.connect(os.path.join(base, "conversation_summaries.db"))
    db.execute("create table conversation_summaries (conversation_id text, title text not null default '', "
               "preview text not null default '', step_count integer not null default 0, "
               "last_modified_time datetime not null, workspace_uris text not null, "
               "nesting_depth integer not null default 0)")
    uri = json.dumps(["file://" + urllib.parse.quote(cwd)])
    db.executemany("insert into conversation_summaries values (?, ?, '', 3, '2026-11-20', ?, ?)",
                   [(TITLED, "Quokka migration plan", uri, 0), (UNTITLED, "", uri, 0), (SUBAGENT, "", uri, 1)])
    db.commit()
    db.close()
    return cwd


def main():
    print("agy")
    sb = harness.sandbox("agy")
    cwd = build(sb)
    rows = run(sb, "list").stdout
    check("a titled agy conversation is listed under its title", "Quokka migration plan" in rows, rows[:300])
    check("agy rows say which tool they belong to", "agy  ·  orbit lab" in rows, rows[:400])
    check("an untitled agy conversation falls back to its first prompt", "wombat checklist for the trip" in rows)
    check("subagent conversations stay hidden", "numbatprompt" not in rows and SUBAGENT not in rows)
    check("the folder comes from the workspace URI, percent-decoded", f"\t{cwd}\tagy:{TITLED}" in rows, rows[:600])
    check("system messages are not indexed", "quokkasystem" not in rows)
    check("thinking is not indexed", "quokkathought" not in rows)
    set_scope(sb, "everything")
    rows_all = run(sb, "list").stdout
    check("tool calls are indexed as tools", "run_command" in rows_all and "quokkadir" in rows_all)
    check("tool output is indexed as tools", "quokkatooloutput" in rows_all)
    check("tools stay out of the conversation scope", "quokkadir" not in rows)
    set_scope(sb, "conversation")
    preview = run(sb, "preview", "agy:" + TITLED, "").stdout
    check("the preview labels who asked", "you ›" in preview and "plan the quokka migration" in preview, preview[:400])
    check("the preview labels who answered", "agy ›" in preview and "three steps" in preview, preview[:400])
    late = "aaaaaaaa-0000-4000-8000-000000000004"
    base = os.path.join(sb.root, ".gemini", "antigravity-cli")
    logs = os.path.join(base, "brain", late, ".system_generated", "logs")
    os.makedirs(logs, exist_ok=True)
    with open(os.path.join(logs, "transcript_full.jsonl"), "w") as fh:
        fh.write('[1, 2]\n{"type":"USER_INPUT","content":["not","text"]}\n'
                 '{"type":"PLANNER_RESPONSE","tool_calls":"oops","content":"echidna reply"}\n'
                 '{"type":"PLANNER_RESPONSE","tool_calls":[{"name":7,"args":{}}],"content":"echidna count"}\n'
                 '{"type":"USER_INPUT","source":"USER_EXPLICIT","content":"echidna question"}\n')
    before = run(sb, "list").stdout
    check("a conversation without its summary row yet is not listed", "echidna question" not in before)
    db = sqlite3.connect(os.path.join(base, "conversation_summaries.db"))
    db.execute("insert into conversation_summaries values (?, 'Echidna notes', '', 2, '2026-11-20', ?, 0)",
               (late, json.dumps(["file://" + urllib.parse.quote(cwd)])))
    db.commit()
    db.close()
    after = run(sb, "list").stdout
    check("its summary row arriving later lists it without touching the transcript", "Echidna notes" in after,
          after[:300])
    check("malformed lines are skipped, the session kept", "echidna question" in after or "Echidna notes" in after)
    plan = run(sb, "resume", cwd, "agy:" + TITLED, TRANSCRIPTS_DRY_RUN="1", HERDR_PLUGIN_ROOT="").stdout.strip()
    check("agy resumes with --conversation", plan.endswith(f"agy --conversation {TITLED}"), plan)
    print()
    if FAILED:
        print(f"{len(FAILED)} failed:")
        for name in FAILED:
            print("  " + name)
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
