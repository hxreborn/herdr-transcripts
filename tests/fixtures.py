#!/usr/bin/env python3

import json
import os
import random
import shutil
import sys
import time

MINUTE, HOUR, DAY = 60, 3600, 86400

SESSIONS = [
    {
        "title": "Retry the checkout webhook before it lands in the dead letter queue",
        "project": "checkout-api", "branch": "fix/webhook-retry", "age": 4 * MINUTE,
        "turns": [
            ("you", "The checkout webhook drops events when Stripe answers slowly. Can you add a retry?"),
            ("claude", "The handler acknowledges before the database write, so a slow Stripe reply loses the event. I moved the ack after the commit and wrapped the call in three retries with backoff."),
            ("you", "Does the retry reuse the idempotency key?"),
            ("claude", "Yes. The key is derived from the event id, so a retry that follows a partial write is a no-op on the Stripe side."),
            ("you", "Good. Add a test that fails without the retry."),
            ("claude", "Added tests/webhook_retry_test.py: it stubs two timeouts then a success, and asserts one row in payments and one dead letter queue entry when all three attempts fail."),
        ],
        "tools": [("Bash", "pytest tests/webhook_retry_test.py -x"), ("Edit", "src/webhooks/checkout.py")],
        "queued": "queuedprompt also bump the retry ceiling",
        "notification": "<task-notification>\n<task-id>bfj0qkcz2</task-id>\n<status>completed</status>\n</task-notification>",
    },
    {
        "title": "Port the auth middleware to Axum",
        "project": "atlas-web", "branch": "feat/axum-auth", "age": 40 * MINUTE,
        "turns": [
            ("you", "Port the auth middleware from actix to axum, keep the same cookie names."),
            ("claude", "Done. The tower layer reads the same session cookie, and rejects with 401 instead of the actix error type. Refresh tokens still rotate on every request."),
            ("you", "What happens to a request with no cookie at all?"),
            ("claude", "It falls through to the anonymous branch, the same as before: public routes serve, private routes redirect to /login."),
        ],
        "tools": [("Read", "src/middleware/auth.rs"), ("Bash", "cargo test -p atlas-web auth")],
    },
    {
        "title": "Find where the p99 on /search went",
        "project": "atlas-web", "branch": "main", "age": 3 * HOUR,
        "turns": [
            ("you", "p99 on /search doubled after Tuesday's deploy. Find it."),
            ("claude", "The new spell-check path runs a second Elasticsearch query for every request instead of only for zero-hit queries. At p99 that is two round trips."),
            ("you", "Can we gate it on the hit count?"),
            ("claude", "Yes, and that restores the old shape: the suggestion query now runs only when the first search returns fewer than three hits."),
            ("claude", "Local benchmark over the recorded query log: p99 1.9s before, 0.7s after."),
        ],
        "tools": [("Grep", "spellcheck src/search"), ("Bash", "hey -n 2000 http://localhost:8080/search?q=kafka")],
    },
    {
        "title": "Kafka consumer stalls on rebalance",
        "project": "infra", "branch": "main", "age": 9 * HOUR,
        "turns": [
            ("you", "The kafka consumer stops reading after a rebalance and only a restart fixes it."),
            ("claude", "The consumer commits offsets from a worker thread that does not stop on revoke. After a rebalance it commits an offset it no longer owns and the broker fences it."),
            ("you", "So the fix is to drain on revoke?"),
            ("claude", "Drain, then commit, then release. I added the revoke handler and a lag alert so the next stall pages instead of going quiet."),
        ],
        "tools": [("Bash", "kubectl logs -n data consumer-7b9 --tail=200"), ("Edit", "consumers/orders.py")],
    },
    {
        "title": "Rename the billing tables without downtime",
        "project": "checkout-api", "branch": "chore/billing-rename", "age": 26 * HOUR,
        "turns": [
            ("you", "Rename billing_v2 to invoices. It has to stay online during the migration."),
            ("claude", "Expand and contract: create a view named invoices over billing_v2, ship the readers, then swap the table and drop the view. Three deploys, no lock."),
            ("you", "Write the migrations."),
            ("claude", "migrations/0142_invoices_view.sql and 0143_invoices_swap.sql are in place, each with a down step that is safe to run twice."),
        ],
        "tools": [("Write", "migrations/0142_invoices_view.sql")],
    },
    {
        "title": None,
        "project": "dotfiles", "branch": "main", "age": 2 * DAY,
        "turns": [
            ("you", "why is my tmux status bar showing the wrong hostname over ssh"),
            ("claude", "The status line is built once when the server starts, so it keeps the hostname of the machine that started it. Making the format string call #H at draw time fixes it."),
        ],
        "tools": [("Read", ".tmux.conf")],
    },
    {
        "title": "Ship the release notes for 2.4",
        "project": "atlas-web", "branch": "main", "age": 3 * DAY,
        "turns": [
            ("you", "Draft release notes for 2.4 from the merged PRs."),
            ("claude", "Grouped 31 merged PRs into four sections: search latency, auth, the new export job, and fixes. Two PRs had no description, so I described them from the diff and flagged them for you."),
            ("you", "Which two?"),
            ("claude", "#812 (drops a dead feature flag) and #829 (widens a timeout in the export worker)."),
        ],
        "tools": [("Bash", "gh pr list --state merged --limit 40")],
    },
    {
        "title": "Screenshot diff keeps failing on CI only",
        "project": "atlas-web", "branch": "fix/ci-fonts", "age": 5 * DAY,
        "turns": [
            ("you", "[Image #1] the screenshot test passes here and fails on CI, look at the diff"),
            ("claude", "The CI image has no Inter font, so the runner falls back to DejaVu and every glyph shifts by a fraction. Pinning the font in the test container makes the two images identical."),
            ("you", "Add the font to the Dockerfile then."),
            ("claude", "Added fonts-inter to the test image and rebuilt the baseline snapshots on that image."),
        ],
        "tools": [("Bash", "docker build -f Dockerfile.test ."), ("Edit", "Dockerfile.test")],
    },
    {
        "title": "Trim the docker image from 1.9GB to 300MB",
        "project": "infra", "branch": "perf/slim-image", "age": 8 * DAY,
        "turns": [
            ("you", "Our API image is 1.9GB. Get it down."),
            ("claude", "Most of it is the build toolchain and the pip cache. A two stage build with a slim runtime base lands at 301MB, and the app starts in the same time."),
            ("you", "Does it still have curl for the healthcheck?"),
            ("claude", "No, and it does not need it: the healthcheck now uses the python that is already in the image."),
        ],
        "tools": [("Bash", "docker images --format '{{.Size}} {{.Repository}}'")],
    },
    {
        "title": "Backfill the search index without a full reindex",
        "project": "infra", "branch": "main", "age": 15 * DAY,
        "turns": [
            ("you", "We need the new language field on 40M documents. A full reindex takes 9 hours."),
            ("claude", "An update-by-query with a script fills the field in place and keeps the index searchable. Batched at 5000 documents it finished in 50 minutes on the staging copy."),
            ("you", "What about documents written during the backfill?"),
            ("claude", "The ingest pipeline already writes the field, so anything new is correct and the backfill only touches documents where it is missing."),
        ],
        "tools": [("Bash", "curl -XPOST localhost:9200/docs/_update_by_query")],
    },
    {
        "title": "Make the onboarding email stop going to spam",
        "project": "checkout-api", "branch": "main", "age": 24 * DAY,
        "turns": [
            ("you", "Our onboarding email lands in spam for gmail users."),
            ("claude", "DKIM signs with a key that is not in DNS any more, so gmail sees a broken signature. Publishing the current selector and adding a DMARC record fixes the alignment."),
            ("you", "Anything else that helps?"),
            ("claude", "Sending from a subdomain keeps the marketing reputation away from the transactional one."),
        ],
        "tools": [("Bash", "dig +short selector1._domainkey.example.com TXT")],
    },
    {
        "title": "Teach the CLI to read config from a file",
        "project": "dotfiles", "branch": "feat/config-file", "age": 40 * DAY,
        "turns": [
            ("you", "The CLI only takes flags. Add a config file with the same names."),
            ("claude", "Flags now fall back to ~/.config/tool/config.toml, and a flag always wins over the file. Unknown keys are an error so a typo is visible."),
            ("you", "Print where the value came from in --help?"),
            ("claude", "--help now marks each default with (file) or (built-in)."),
        ],
        "tools": [("Write", "src/config.rs")],
    },
]

EXTRA = [
    ("Split the monolith test suite into shards", "checkout-api", "ci/shards", 2 * HOUR),
    ("Turn the flaky login test green", "atlas-web", "fix/flaky-login", 5 * HOUR),
    ("Drop the unused feature flags", "checkout-api", "chore/flags", 7 * HOUR),
    ("Write a runbook for the pager rotation", "infra", "main", 11 * HOUR),
    ("Cache the avatar thumbnails on the edge", "atlas-web", "perf/avatars", 14 * HOUR),
    ("Make the CLI respect NO_COLOR", "dotfiles", "feat/no-color", 20 * HOUR),
    ("Add a health endpoint the load balancer trusts", "infra", "main", 28 * HOUR),
    ("Stop the nightly job from double sending", "checkout-api", "fix/nightly", 34 * HOUR),
    ("Rewrite the search filters as one query", "atlas-web", "perf/filters", 2 * DAY),
    ("Pin the toolchain so CI stops drifting", "infra", "ci/pin", 4 * DAY),
    ("Give the settings page real validation", "atlas-web", "feat/validation", 6 * DAY),
    ("Collapse the three logging paths into one", "infra", "chore/logging", 7 * DAY),
    ("Make the importer resumable", "checkout-api", "feat/resume-import", 9 * DAY),
    ("Delete the dead admin templates", "atlas-web", "chore/cleanup", 12 * DAY),
    ("Teach the linter about our test helpers", "dotfiles", "main", 16 * DAY),
    ("Batch the webhook fan-out", "checkout-api", "perf/fan-out", 19 * DAY),
    ("Replace the cron job with a systemd timer", "dotfiles", "main", 23 * DAY),
    ("Move the staging database to the new region", "infra", "main", 27 * DAY),
    ("Audit the third party scripts on checkout", "checkout-api", "main", 35 * DAY),
    ("Document the deploy rollback path", "infra", "main", 44 * DAY),
]


CODEX = [
    {
        "title": "Tighten the exporter retry budget", "project": "checkout-api", "age": 12 * MINUTE,
        "turns": [
            ("you", "The nightly exporter retries forever when S3 throttles. Cap it."),
            ("codex", "Capped at five attempts with jittered backoff; the sixth failure raises and the job exits non-zero so the scheduler alerts."),
            ("you", "Log the attempt number too."),
            ("codex", "Each retry now logs the attempt and the delay at warning level."),
        ],
        "tools": [("shell", '{"command":["bash","-lc","pytest tests/exporter_test.py -x"]}'),
                  ("exec", 'text(await tools.exec_command({cmd:"rg retry src/exporter"}));')],
    },
    {
        "title": None, "project": "atlas-web", "age": 55 * MINUTE, "image": True,
        "turns": [
            ("you", "walk me through the websocket reconnect logic in the dashboard"),
            ("codex", "The client reconnects with exponential backoff capped at thirty seconds and replays the last cursor, so a dropped connection loses no events."),
        ],
        "tools": [("exec", 'text(await tools.exec_command({cmd:"rg reconnect src/ws"}));')],
    },
    {
        "title": "Nightly lint sweep", "project": "infra", "age": 3 * HOUR, "source": "exec",
        "turns": [("you", "run the lintsweep over every service and report"),
                  ("codex", "Lintsweep finished with no findings.")],
        "tools": [],
    },
    {
        "title": "Audit the cache headers", "project": "infra", "age": 4 * HOUR, "subagent": True,
        "turns": [("you", "cacheaudit: list every route missing cache headers"),
                  ("codex", "Three routes lack cache headers.")],
        "tools": [],
    },
    {
        "title": None, "project": "infra", "age": 3 * DAY, "legacy": True,
        "turns": [("you", "Generate a contributor guide for the infra repo"),
                  ("codex", "Wrote AGENTS.md covering the layout, the commands and the conventions.")],
        "tools": [("shell", '{"command":["bash","-lc","ls -la"]}')],
    },
]

OPENCODE = [
    {
        "title": "Stop the uploader retrying a rejected chunk", "project": "infra", "age": 25 * MINUTE,
        "turns": [
            ("you", "the uploader keeps retrying when the bucket rejects a chunk, cap it"),
            ("opencode", "Capped it at four attempts with backoff, and the fifth failure raises so"
                         " the job stops instead of spinning."),
        ],
        "tools": [("write", "src/uploader_chunks.py")],
        "injected": [("<file>\n00001| syntheticnoise\n</file>", True),
                     ("<system-reminder>\nremindernoise\n</system-reminder>", False)],
    },
    {
        "title": "Trace the dropped websocket frames", "project": "atlas-web", "age": 35 * MINUTE,
        "parent": 0,
        "turns": [("you", "childprompt: why does the reader drop frames"),
                  ("opencode", "The buffer overruns whenever the reader lags behind the socket.")],
        "tools": [],
    },
    {
        "title": "", "project": "notebook", "age": 50 * MINUTE,
        "turns": [("opencode", None)], "tools": [("read", "notebook/scratch.md")],
    },
]

ENVIRONMENT = "<environment_context>\n  <cwd>{cwd}</cwd>\n  <approval_policy>on-request</approval_policy>\n</environment_context>"
INSTRUCTIONS = "# AGENTS.md instructions for {cwd}\n\n<INSTRUCTIONS>\nKeep changes small.\n</INSTRUCTIONS>"


def codex_record(kind, payload, ordinal):
    return {"timestamp": "2026-09-17T10:00:00.000Z", "ordinal": ordinal, "type": kind, "payload": payload}


def codex_message(role, text):
    part = {"type": "input_text" if role == "user" else "output_text", "text": text}
    return {"type": "message", "role": role, "content": [part]}


def write_codex(home, index, spec):
    cwd = os.path.join(home, "code", spec["project"])
    os.makedirs(cwd, exist_ok=True)
    sid = session_id(100 + index)
    day = os.path.join(home, ".codex", "sessions", "2026", "09", "17")
    os.makedirs(day, exist_ok=True)
    path = os.path.join(day, f"rollout-2026-09-17T10-00-{index:02d}-{sid}.jsonl")
    tools = list(spec.get("tools", []))
    lines = []
    if spec.get("legacy"):
        lines.append({"id": sid, "timestamp": "2025-08-21T23:05:00.550Z", "instructions": None})
        lines.append({"record_type": "state"})
        lines.append(codex_message("user", f"<environment_context>\nCurrent working directory: {cwd}\n"
                                           "Approval policy: on-request\n</environment_context>"))
        for who, text in spec["turns"]:
            lines.append(codex_message("user" if who == "you" else "assistant", text))
            if tools and who != "you":
                name, arg = tools.pop(0)
                lines.append({"type": "function_call", "name": name, "arguments": arg, "call_id": "call_1"})
    else:
        meta = {"id": sid, "session_id": sid, "timestamp": "2026-09-17T10:00:00.000Z", "cwd": cwd,
                "originator": "codex-tui", "cli_version": "0.154.0",
                "source": spec.get("source", "cli"), "thread_source": "user"}
        if spec.get("subagent"):
            meta["source"] = {"subagent": {"thread_spawn": {"parent_thread_id": session_id(99), "depth": 1}}}
            meta["thread_source"] = "subagent"
        records = [("session_meta", meta),
                   ("response_item", codex_message("user", INSTRUCTIONS.format(cwd=cwd))),
                   ("response_item", codex_message("user", ENVIRONMENT.format(cwd=cwd))),
                   ("turn_context", {"cwd": cwd, "approval_policy": "on-request"})]
        for n, (who, text) in enumerate(spec["turns"]):
            if who == "you":
                records.append(("event_msg", {"type": "user_message", "message": text}))
                message = codex_message("user", text)
                if spec.get("image") and n == 0:
                    message["content"].insert(0, {"type": "input_image", "image_url": "data:image/png;base64,AAAA"})
                records.append(("response_item", message))
            else:
                records.append(("response_item", codex_message("assistant", text)))
                records.append(("event_msg", {"type": "agent_message", "message": text}))
                if tools:
                    name, arg = tools.pop(0)
                    if name == "shell":
                        records.append(("response_item", {"type": "function_call", "name": name,
                                                          "arguments": arg, "call_id": "call_1"}))
                        records.append(("response_item", {"type": "function_call_output", "call_id": "call_1",
                                                          "output": "toolnoise: 3 passed"}))
                    else:
                        records.append(("response_item", {"type": "custom_tool_call", "name": name,
                                                          "input": arg, "call_id": "call_2"}))
                        records.append(("response_item", {"type": "custom_tool_call_output", "call_id": "call_2",
                                                          "output": [{"type": "input_text", "text": "toolnoise"}]}))
            records.append(("response_item", {"type": "reasoning", "summary": [], "encrypted_content": "gAAAA"}))
        lines = [codex_record(kind, payload, n) for n, (kind, payload) in enumerate(records)]
    with open(path, "w") as fh:
        for line in lines:
            fh.write(json.dumps(line, separators=(",", ":")) + "\n")
    when = time.time() - spec["age"]
    os.utime(path, (when, when))
    return sid, cwd


def build_codex(home):
    root = os.path.join(home, ".codex")
    shutil.rmtree(root, ignore_errors=True)
    made = [write_codex(home, index, spec) for index, spec in enumerate(CODEX)]
    with open(os.path.join(root, "session_index.jsonl"), "w") as fh:
        for (sid, _), spec in zip(made, CODEX):
            if spec["title"]:
                fh.write(json.dumps({"id": sid, "thread_name": spec["title"],
                                     "updated_at": "2026-09-17T10:00:00Z"}) + "\n")
    import sqlite3
    db = sqlite3.connect(os.path.join(root, "state_5.sqlite"))
    db.execute("create table threads (id text primary key, name text, git_branch text)")
    db.execute("insert into threads values (?, ?, ?)", (made[0][0], CODEX[0]["title"], "perf/exporter-retry"))
    db.commit()
    db.close()
    return made


def opencode_sid(index):
    return "ses_" + session_id(200 + index).replace("-", "")


def write_opencode(db, home, index, spec):
    cwd = os.path.join(home, "code", spec["project"])
    os.makedirs(cwd, exist_ok=True)
    sid = opencode_sid(index)
    stamp = int((time.time() - spec["age"]) * 1000)
    parent = None if spec.get("parent") is None else opencode_sid(spec["parent"])
    db.execute("insert into session values (?,?,?,?,?,?,?,?,?)",
               (sid, "prj_1", parent, spec["project"], cwd, spec["title"], "1.2.0", stamp, stamp))
    tools = list(spec.get("tools", []))
    for clock, (who, text) in enumerate(spec["turns"]):
        mid = f"msg_{sid}_{clock}"
        role = "user" if who == "you" else "assistant"
        message = {"role": role, "time": {"created": stamp}}
        if role == "assistant":
            message["path"] = {"cwd": cwd, "root": cwd}
        db.execute("insert into message values (?,?,?,?,?)",
                   (mid, sid, clock, clock, json.dumps(message, separators=(",", ":"))))
        parts = [] if text is None else [{"type": "text", "text": text}]
        if role == "user":
            for injected, synthetic in spec.get("injected", []):
                part = {"type": "text", "text": injected}
                if synthetic:
                    part["synthetic"] = True
                parts.append(part)
        else:
            parts = [{"type": "step-start"}, {"type": "reasoning", "text": "reasoningnoise"}] + parts
            if tools:
                name, arg = tools.pop(0)
                key = "filePath" if name in ("read", "write") else "command"
                parts.append({"type": "tool", "callID": "tool_1", "tool": name,
                              "state": {"status": "completed",
                                        "input": {key: arg, "content": "toolnoise in the content key"},
                                        "output": "toolnoise: 3 passed"}})
                parts.append({"type": "step-finish", "reason": "tool-calls"})
        for n, part in enumerate(parts):
            db.execute("insert into part values (?,?,?,?,?,?)",
                       (f"prt_{mid}_{n}", mid, sid, n, n, json.dumps(part, separators=(",", ":"))))
    return sid, cwd


def build_opencode(home):
    root = os.path.join(home, ".local", "share", "opencode")
    shutil.rmtree(root, ignore_errors=True)
    os.makedirs(root, exist_ok=True)
    import sqlite3
    db = sqlite3.connect(os.path.join(root, "opencode.db"))
    db.execute("create table session (id text primary key, project_id text, parent_id text, slug text,"
               " directory text, title text, version text, time_created integer, time_updated integer)")
    db.execute("create table message (id text primary key, session_id text, time_created integer,"
               " time_updated integer, data text)")
    db.execute("create table part (id text primary key, message_id text, session_id text,"
               " time_created integer, time_updated integer, data text)")
    made = [write_opencode(db, home, index, spec) for index, spec in enumerate(OPENCODE)]
    db.commit()
    db.close()
    return made


DROID_SIDS = ("a3f1c0d2-1f4e-4b77-9c01-5d2e8ab41c60", "b7e4d9aa-2c85-4f13-8e6a-0c9b7d3f2a14")
DROID_REMINDER = ("<system-reminder>\n\nUser system info (linux 6.17.7-2-cachyos)\nToday's date: 2026-09-17\n\n"
                  "# The commands below were executed at the start of all sessions.\n\n"
                  "% pwd\n{cwd}\n\n% ls\nREADME.md\nsrc\nreminderleak\n</system-reminder>")
DROID_TITLE = "Move the droid picker off the deprecated tiles endpoint"


def droid_message(role, content):
    return {"type": "message", "id": DROID_SIDS[0], "timestamp": "2026-09-17T10:00:00.000Z",
            "message": {"role": role, "content": content}}


def write_droid(path, lines, age):
    with open(path, "w") as fh:
        for line in lines:
            fh.write(json.dumps(line, separators=(",", ":")) + "\n")
    when = time.time() - age
    os.utime(path, (when, when))


def build_droid(home):
    root = os.path.join(home, ".factory", "sessions")
    shutil.rmtree(os.path.join(home, ".factory"), ignore_errors=True)
    os.makedirs(root, exist_ok=True)
    cwd = os.path.join(home, "code", "atlas-web")
    os.makedirs(cwd, exist_ok=True)
    lines = [{"type": "session_start", "id": DROID_SIDS[0], "title": DROID_TITLE,
              "owner": "rafa", "version": 2},
             droid_message("user", [{"type": "text", "text": DROID_REMINDER.format(cwd=cwd)},
                                    {"type": "text", "text": DROID_TITLE}]),
             droid_message("assistant", [{"type": "thinking", "thinking": "reminderleak about the endpoint"},
                                         {"type": "text", "text": "The old endpoint answers 410 now, so the"
                                                                  " picker reads the tiles service instead."},
                                         {"type": "tool_use", "name": "Edit",
                                          "input": {"file_path": "src/picker/tiles.py"}}]),
             droid_message("user", [{"type": "tool_result", "tool_use_id": "toolu_1",
                                     "content": "toolnoise 12 tiles"}]),
             {"type": "todo_state", "id": DROID_SIDS[0], "timestamp": "2026-09-17T10:01:00.000Z",
              "todos": {"todos": [{"id": "1", "content": "toolnoise the cache", "status": "pending"}]}},
             droid_message("user", [{"type": "text", "text": "Does it still work when the cache is cold?"}]),
             droid_message("assistant", [{"type": "text", "text": "Yes. A cold cache falls through to the"
                                                                 " service and fills itself on the way back."}])]
    write_droid(os.path.join(root, DROID_SIDS[0] + ".jsonl"), lines, 70 * MINUTE)
    write_droid(os.path.join(root, DROID_SIDS[1] + ".jsonl"),
                [{"type": "session_start", "id": DROID_SIDS[1], "title": "New Session", "owner": "rafa"}],
                2 * HOUR)
    return [(DROID_SIDS[0], cwd), (DROID_SIDS[1], "")]


COPILOT_SIDS = ("c2a8b641-7d30-4e92-a15b-6f8c0d47e3b9", "d5c93e07-4a61-48be-b072-91af2e6c5d38")
COPILOT_NOISE = ("<current_datetime>2026-09-17T10:00:00.000Z</current_datetime>\n\n{content}\n\n"
                 "<reminder>\n<sql_tables>No tables exist yet, transformedleak.</sql_tables>\n</reminder>")


def copilot_event(kind, data):
    return {"type": kind, "data": data, "id": COPILOT_SIDS[0],
            "timestamp": "2026-09-17T10:00:00.000Z", "parentId": COPILOT_SIDS[1]}


def copilot_message(role, text, tools=()):
    if role == "you":
        return copilot_event("user.message", {"content": text, "attachments": [],
                                              "transformedContent": COPILOT_NOISE.format(content=text)})
    return copilot_event("assistant.message", {"messageId": COPILOT_SIDS[0], "content": text,
                                               "reasoningOpaque": "transformedleak/gAAAA",
                                               "toolRequests": [{"toolCallId": "tooluse_1", "name": name,
                                                                 "arguments": {"path": arg},
                                                                 "type": "function"}
                                                                for name, arg in tools]})


def write_copilot(root, sid, events, workspace, age):
    folder = os.path.join(root, sid)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "events.jsonl")
    with open(path, "w") as fh:
        for event in events:
            fh.write(json.dumps(event, separators=(",", ":")) + "\n")
    with open(os.path.join(folder, "workspace.yaml"), "w") as fh:
        fh.write(f"id: {sid}\nsummary_count: 0\ncreated_at: 2026-09-17T10:00:00.000Z\n")
        for key, value in workspace.items():
            fh.write(f"{key}: {value}\n")
    when = time.time() - age
    os.utime(path, (when, when))


def build_copilot(home):
    root = os.path.join(home, ".copilot", "session-state")
    shutil.rmtree(os.path.join(home, ".copilot"), ignore_errors=True)
    os.makedirs(root, exist_ok=True)
    cwd = os.path.join(home, "code", "infra")
    os.makedirs(cwd, exist_ok=True)
    events = [
        copilot_event("session.start", {"sessionId": COPILOT_SIDS[0], "producer": "copilot-agent",
                                        "context": {"cwd": cwd, "gitRoot": cwd,
                                                    "branch": "feat/tile-cache"}}),
        copilot_event("session.info", {"infoType": "model", "message": "Model changed to: gpt-5"}),
        copilot_message("you", "Cache the rendered tiles on disk"),
        copilot_event("assistant.turn_start", {"turnId": "0"}),
        copilot_message("copilot", "", [("view", "src/tiles.py")]),
        copilot_event("tool.execution_start", {"toolCallId": "tooluse_1", "toolName": "view",
                                               "arguments": {"path": "src/tiles.py"}}),
        copilot_event("tool.execution_complete", {"toolCallId": "tooluse_1",
                                                  "result": "toolnoise 40 lines read"}),
        copilot_message("copilot", "The renders land in a content-addressed cache now, so a repeat request never re-renders."),
        copilot_event("assistant.turn_end", {"turnId": "0"}),
        copilot_event("system.notification", {"message": "toolnoise compaction ahead"}),
        copilot_event("session.shutdown", {}),
    ]
    write_copilot(root, COPILOT_SIDS[0], events,
                  {"cwd": cwd, "branch": "feat/tile-cache", "summary": "Cache the rendered tiles on disk"},
                  80 * MINUTE)
    other = os.path.join(home, "code", "dotfiles")
    os.makedirs(other, exist_ok=True)
    write_copilot(root, COPILOT_SIDS[1],
                  [copilot_message("you", "explain what the prompt hook rewrites"),
                   copilot_message("copilot", "It runs before every prompt and can replace the text that reaches the model.")],
                  {"cwd": other, "branch": "main"}, 3 * HOUR)
    return [(COPILOT_SIDS[0], cwd), (COPILOT_SIDS[1], other)]


def session_id(n):
    rnd = random.Random(n)
    hexes = "%08x-%04x-4%03x-%04x-%012x" % (
        rnd.getrandbits(32), rnd.getrandbits(16), rnd.getrandbits(12),
        0x8000 | rnd.getrandbits(14), rnd.getrandbits(48))
    return hexes


def write_session(root, home, index, spec):
    cwd = os.path.join(home, "code", spec["project"])
    os.makedirs(cwd, exist_ok=True)
    project_dir = os.path.join(root, cwd.replace("/", "-"))
    os.makedirs(project_dir, exist_ok=True)
    sid = session_id(index)
    path = os.path.join(project_dir, sid + ".jsonl")
    lines = []
    if spec["title"]:
        lines.append({"type": "ai-title", "aiTitle": spec["title"]})
    tools = list(spec.get("tools", []))
    for n, (who, text) in enumerate(spec["turns"]):
        if who == "you":
            lines.append({"type": "user", "cwd": cwd, "gitBranch": spec["branch"],
                          "message": {"role": "user", "content": text}})
        else:
            lines.append({"type": "assistant", "cwd": cwd, "gitBranch": spec["branch"],
                          "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}})
        if tools and n % 2 == 1:
            name, arg = tools.pop(0)
            key = "command" if name == "Bash" else ("pattern" if name == "Grep" else "file_path")
            lines.append({"type": "assistant", "cwd": cwd, "gitBranch": spec["branch"],
                          "message": {"role": "assistant",
                                      "content": [{"type": "tool_use", "name": name, "input": {key: arg}}]}})
            lines.append({"type": "user", "cwd": cwd, "gitBranch": spec["branch"],
                          "message": {"role": "user", "content": [
                              {"type": "tool_result", "tool_use_id": "toolu_1", "content": "toolnoise 3 passed"}]}})
        if n == 1 and spec.get("queued"):
            lines.append({"type": "queue-operation", "operation": "enqueue", "sessionId": sid,
                          "content": spec["queued"]})
            lines.append({"type": "queue-operation", "operation": "remove", "sessionId": sid,
                          "content": spec["queued"]})
        if n == 1 and spec.get("notification"):
            lines.append({"type": "user", "cwd": cwd, "gitBranch": spec["branch"],
                          "message": {"role": "user", "content": spec["notification"]}})
    if spec.get("clear"):
        lines = [{"type": "user", "cwd": cwd, "gitBranch": spec["branch"],
                  "message": {"role": "user", "content": "<local-command-caveat>Caveat: the messages below"
                                                         " were generated by the user while running local commands."}},
                 {"type": "user", "cwd": cwd, "gitBranch": spec["branch"],
                  "message": {"role": "user", "content": "<command-name>/clear</command-name>"}}]
    with open(path, "w") as fh:
        for line in lines:
            fh.write(json.dumps(line, separators=(",", ":")) + "\n")
    when = time.time() - spec["age"]
    os.utime(path, (when, when))
    return sid, cwd


def build(home):
    root = os.path.join(home, ".claude", "projects")
    shutil.rmtree(root, ignore_errors=True)
    os.makedirs(root, exist_ok=True)
    specs = list(SESSIONS)
    for title, project, branch, age in EXTRA:
        specs.append({"title": title, "project": project, "branch": branch, "age": age,
                      "turns": [("you", title.lower() + "?"),
                                ("claude", "Done. " + title + " — the change is in place and the tests pass.")],
                      "tools": []})
    specs.append({"title": None, "project": "dotfiles", "branch": "main", "age": 6 * HOUR,
                  "turns": [], "tools": [], "clear": True})
    made = []
    for index, spec in enumerate(specs):
        made.append(write_session(root, home, index, spec))
    with open(os.path.join(home, ".claude", "settings.json"), "w") as fh:
        json.dump({"cleanupPeriodDays": 3650}, fh, indent=2)
    build_droid(home)
    build_copilot(home)
    return made


if __name__ == "__main__":
    for sid, cwd in build(sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser("~")):
        print(sid, cwd)
