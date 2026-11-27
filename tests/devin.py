#!/usr/bin/env python3

import json
import os
import re
import sqlite3
import subprocess
import sys

import harness

FAILED = []
PLUGIN_CMD = os.path.join(harness.PLUGIN, "transcripts")
ANSI = re.compile(r"\x1b\[[0-9;]*m")
MAIN = "dddddddd-0000-4000-8000-000000000001"
HIDDEN = "dddddddd-0000-4000-8000-000000000002"
BRANCHED = "dddddddd-0000-4000-8000-000000000003"


def check(name, ok, detail=""):
    print(("  ok   " if ok else "  FAIL ") + name + ("" if ok or not detail else f"\n       {detail}"))
    if not ok:
        FAILED.append(name)


def run(sb, *args, **env):
    proc = subprocess.run([PLUGIN_CMD, *args], env=sb.env(**env), capture_output=True, text=True, timeout=60)
    proc.stdout = ANSI.sub("", proc.stdout)
    return proc


def set_scope(sb, scope):
    cfg_dir = os.path.join(sb.root, ".config", "herdr", "plugins", "config", "transcripts")
    os.makedirs(cfg_dir, exist_ok=True)
    with open(os.path.join(cfg_dir, "config.toml"), "w") as fh:
        fh.write(f'scope = "{scope}"\n')


def msg(role, content, **extra):
    record = {"message_id": "m", "role": role, "content": content, "metadata": {"is_user_input": None}}
    record.update(extra)
    return json.dumps(record)


def build(sb):
    cwd = os.path.join(sb.root, "code", "platypus app")
    os.makedirs(cwd, exist_ok=True)
    path = os.path.join(sb.root, ".local", "share", "devin", "cli")
    os.makedirs(path, exist_ok=True)
    db = sqlite3.connect(os.path.join(path, "sessions.db"))
    db.execute("create table sessions (id text primary key, working_directory text, backend_type text, model text,"
               " agent_mode text, created_at integer, last_activity_at integer, title text, main_chain_id integer,"
               " shell_last_seen_index integer, cogs_json text, workspace_dirs text, hidden integer, metadata text)")
    db.execute("create table message_nodes (row_id integer primary key, session_id text, node_id integer,"
               " parent_node_id integer, chat_message text, created_at integer, metadata text)")
    db.execute("create index idx_message_nodes_session on message_nodes(session_id)")
    now = 1795600000
    db.executemany("insert into sessions (id, working_directory, created_at, last_activity_at, title, hidden)"
                   " values (?, ?, ?, ?, ?, ?)",
                   [(MAIN, cwd, now - 60, now, "Platypus billing fix", 0),
                    (HIDDEN, cwd, now - 60, now, "Hidden thing", 1)])
    rows = [
        (MAIN, msg("system", "system prompt platypussystem")),
        (MAIN, msg("user", "fix the platypus billing rounding", metadata={"is_user_input": True})),
        (MAIN, msg("user", "injected platypusreminder", metadata={"is_user_input": False})),
        (MAIN, msg("assistant", "Rounding now uses banker's rounding for platypus invoices.",
                   tool_calls=[{"id": "t1", "function": {"name": "run_command",
                                                         "arguments": json.dumps({"command": "pytest platypusdir"})}}])),
        (MAIN, msg("tool", "platypustooloutput 3 passed", tool_call_id="t1")),
        (MAIN, "not json at all"),
        (HIDDEN, msg("user", "hidden numbatprompt", metadata={"is_user_input": True})),
    ]
    db.executemany("insert into message_nodes (session_id, node_id, chat_message, created_at) values (?, ?, ?, ?)",
                   [(sid, i, m, now - 50 + i) for i, (sid, m) in enumerate(rows)])
    db.execute("insert into sessions (id, working_directory, created_at, last_activity_at, title, main_chain_id, hidden)"
               " values (?, ?, ?, ?, ?, ?, ?)", (BRANCHED, cwd, now - 60, now - 1, "Kookaburra retry", 3, 0))
    branched = [
        (1, None, msg("user", "kookaburra question", metadata={"is_user_input": True})),
        (2, 1, msg("assistant", "abandoned kookaburraretry answer")),
        (3, 1, msg("assistant", "kept kookaburra answer",
                   tool_calls=[{"id": "t2", "name": "read", "index": 0, "kind": "function",
                                "arguments": {"file_path": os.path.join(cwd, "kookaburraflat.py")}}])),
    ]
    db.executemany("insert into message_nodes (session_id, node_id, parent_node_id, chat_message, created_at)"
                   " values (?, ?, ?, ?, ?)", [(BRANCHED, n, parent, m, now - 40 + n) for n, parent, m in branched])
    db.commit()
    db.close()
    return cwd, os.path.join(path, "sessions.db")


def main():
    print("devin")
    sb = harness.sandbox("devin")
    cwd, db_path = build(sb)
    rows = run(sb, "list").stdout
    check("a devin session is listed under its title", "Platypus billing fix" in rows, rows[:300])
    check("devin rows say which tool they belong to", "devin  ·  platypus app" in rows, rows[:400])
    check("the folder comes from working_directory", f"\t{cwd}\tdevin:{MAIN}" in rows)
    check("hidden sessions stay hidden", "Hidden thing" not in rows and "numbatprompt" not in rows)
    check("system messages are not indexed", "platypussystem" not in rows)
    check("injected user messages are not prompts", "platypusreminder" not in rows)
    set_scope(sb, "everything")
    rows_all = run(sb, "list").stdout
    check("tool calls are indexed as tools", "run_command" in rows_all and "platypusdir" in rows_all)
    check("tool results are indexed as tools", "platypustooloutput" in rows_all)
    check("tools stay out of the conversation scope", "platypusdir" not in rows)
    check("flat tool calls are indexed as tools", "read" in rows_all and "kookaburraflat.py" in rows_all, rows_all[:600])
    check("the active chain is kept", "kept kookaburra answer" in rows_all)
    check("abandoned branches are not indexed", "kookaburraretry" not in rows_all)
    set_scope(sb, "conversation")
    preview = run(sb, "preview", "devin:" + MAIN, "").stdout
    check("the preview labels who asked", "you ›" in preview and "billing rounding" in preview, preview[:400])
    check("the preview labels who answered", "devin ›" in preview and "banker's rounding" in preview, preview[:400])
    db = sqlite3.connect(db_path)
    db.execute("insert into message_nodes (session_id, node_id, chat_message, created_at) values (?, 99, ?, 1795600100)",
               (MAIN, msg("user", "and the platypus refund path", metadata={"is_user_input": True})))
    db.execute("update sessions set last_activity_at = 1795600100 where id = ?", (MAIN,))
    db.commit()
    db.close()
    preview = run(sb, "preview", "devin:" + MAIN, "").stdout
    run(sb, "list")
    preview = run(sb, "preview", "devin:" + MAIN, "").stdout
    check("a new message reaches the index", "refund path" in preview, preview[:400])
    db = sqlite3.connect(db_path)
    db.execute("update sessions set title = 'Platypus refunds' where id = ?", (MAIN,))
    db.commit()
    db.close()
    rows = run(sb, "list").stdout
    check("a later title reaches the index", "Platypus refunds" in rows, rows[:300])
    plan = run(sb, "resume", cwd, "devin:" + MAIN, TRANSCRIPTS_DRY_RUN="1", HERDR_PLUGIN_ROOT="").stdout.strip()
    check("devin resumes with --resume", plan.endswith(f"devin --resume {MAIN}"), plan)
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
