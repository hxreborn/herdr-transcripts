#!/usr/bin/env python3

import json
import os
import re
import shutil
import subprocess
import sys

import harness

FAILED = []


def box_is_intact(term):
    screen = term.screen.display
    tops = [y for y, line in enumerate(screen) if "╭" in line and "╮" in line]
    bottoms = [y for y, line in enumerate(screen) if "╰" in line and "╯" in line]
    if not tops or not bottoms:
        return False, "no panel on screen"
    top, bottom = tops[0], bottoms[-1]
    left, right = screen[top].index("╭"), screen[top].index("╮")
    if screen[bottom].index("╰") != left or screen[bottom].index("╯") != right:
        return False, "the bottom border does not line up with the top"
    for y in range(top + 1, bottom):
        line = screen[y].ljust(right + 1)
        if line[left] != "│" or line[right] != "│":
            return False, f"row {y} breaks the border: {screen[y].rstrip()!r}"
    return True, ""


def check(name, ok, detail=""):
    print(("  ok   " if ok else "  FAIL ") + name + (f"  {detail}" if detail and not ok else ""))
    if not ok:
        FAILED.append(name)


def cell(term, needle):
    for y, line in enumerate(term.screen.display):
        x = line.find(needle)
        if x >= 0:
            return term.screen.buffer[y][x]
    return None


def unit_tests():
    print("units")
    sys.path.insert(0, harness.PLUGIN)
    import importlib.util
    spec = importlib.util.spec_from_file_location("transcripts", os.path.join(harness.PLUGIN, "transcripts.py"))
    vb = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(vb)
    now = vb.time.time()
    cases = [(5, "just now"), (60, "1 minute ago"), (120, "2 minutes ago"), (3600, "1 hour ago"),
             (7200, "2 hours ago"), (100000, "yesterday"), (3 * 86400, "3 days ago"),
             (8 * 86400, "8 days ago"), (15 * 86400, "2 weeks ago"), (70 * 86400, "2 months ago")]
    for delta, want in cases:
        got = vb.relative(now - delta)
        check(f"relative({delta}s) == {want}", got == want, f"got {got}")
    check("image placeholder", vb.normalize("look [Image #3] here", 80) == "look [image] here")
    reminder = ("<system-reminder>\nThe user sent a new message while you were working:\n"
                "fix the gutter please\n\nThis is how Claude Code surfaces messages")
    found = vb.MIDTURN.search(reminder)
    check("a mid-turn prompt is lifted out of its system reminder",
          found is not None and found.group(1) == "fix the gutter please")
    check("tabs stripped", "\t" not in vb.normalize("a\tb", 80))
    check("scope nth for the conversation", vb.scope_nth("conversation") == "1,2,3")
    check("scope nth for everything", vb.scope_nth("everything") == "1,2,3,4")


def picker_tests():
    print("picker")
    sb = harness.sandbox("tui", live=[(0, "working"), (3, "blocked")])
    t = harness.run(sb, cols=100, rows=30)
    t.wait_idle()
    screen = t.text()
    check("header shows the search scopes",
          all(word in screen for word in ("search", "conversation", "titles", "prompts",
                                          "replies", "tools", "everything")))
    check("header shows the resume flags",
          all(word in screen for word in ("resume", "skip-permissions", "chrome")))
    check("footer offers resume and close", "↵ resume" in screen and "esc close" in screen)
    check("header shows the sort and time state", "sort recent" in screen and "times relative" in screen)
    check("footer names what each key does", "tab scope" in screen and "ctrl-o sort" in screen)
    check("no fzf gutter glyph", "▌" not in screen)
    check("rows carry cwd and branch", "checkout-api  ·  fix/webhook-retry" in screen)
    check("running session is marked", "● working" in screen)
    check("relative time is never '1 hours ago'", "1 hours ago" not in screen)
    check("a /clear leftover with no conversation is not listed", "untitled session" not in screen)

    t.send("queuedprompt")
    check("a prompt that was queued but never echoed as a message is searchable",
          "Retry the checkout webhook" in t.text() and "you › queuedprompt" in t.text())
    t.send("\x15")
    t.send("task-notification")
    check("task notifications never become prompts", "0/" in t.text().split("\n")[2], t.text().split("\n")[2])
    t.send("\x15")
    t.send("toolnoise")
    check("claude tool results are not indexed", "0/" in t.text().split("\n")[2], t.text().split("\n")[2])
    t.send("\x15")

    t.send("kafka")
    check("blocked session says blocked", "● blocked" in t.text())
    t.send("\x15")

    t.send("tmux")
    check("untitled session falls back to its first prompt",
          "why is my tmux status bar showing the wrong hostname" in t.text())
    t.send("\x15")

    t.send("kafka")
    screen = t.text()
    check("search narrows the list", "Kafka consumer stalls on rebalance" in screen
          and "Port the auth middleware" not in screen)
    check("preview counts matching messages", "messages match" in screen)
    check("preview labels who spoke", "you ›" in screen)

    t.send("\x15")
    t.send("\t")
    check("tab moves the scope on", "titles" in t.text())
    t.send("\t\t\t\t")
    check("scope cycles to everything", cell(t, "everything").bg == "ffb2ba",
          str(cell(t, "everything")))
    t.send("\t")
    check("scope wraps back to the conversation", cell(t, "conversation").bg == "ffb2ba",
          str(cell(t, "conversation")))

    t.send("\x0f")
    screen = t.text()
    check("sort cycles to oldest", "sort oldest" in screen and "Move the staging database" in screen)
    t.send("\x0f\x0f")
    check("sort cycles to cwd", "sort cwd" in t.text())
    t.send("\x0f")
    check("sort wraps back to recent", "sort recent" in t.text())

    t.send("\x14")
    check("ctrl-t switches to absolute time",
          re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", t.text()) is not None and "minutes ago" not in t.text())
    t.send("\x14")
    check("ctrl-t switches back", "ago" in t.text())

    t.send("\x10")
    screen = t.text()
    check("ctrl-p keeps only the current cwd", "checkout-api" in screen and "atlas-web" not in screen)
    check("ctrl-p offers the way back", "ctrl-p all directories" in t.text())
    t.send("\x10")
    check("ctrl-p restores every cwd", "atlas-web" in t.text())

    t.send("\x18")
    check("ctrl-x arms skip-permissions", "skip-permissions ✓" in t.text())
    check("an armed dangerous flag is filled with the warning colour",
          cell(t, "skip-permissions").bg == "ffdeab", str(cell(t, "skip-permissions")))
    t.send("\x18")
    check("ctrl-x disarms skip-permissions", "skip-permissions ✓" not in t.text())
    check("an idle flag stays flat and dim", cell(t, "skip-permissions").bg == "default"
          and cell(t, "skip-permissions").fg == "8b8b8b", str(cell(t, "skip-permissions")))

    t.send("\x1bOP")
    screen = t.text()
    check("f1 opens the key overlay", "│ keys" in screen and "next search scope" in screen)
    check("the key overlay offers a way out", "esc close" in screen)
    intact, why = box_is_intact(t)
    check("the key overlay keeps its borders", intact, why)
    t.send("q")
    check("q closes the overlay", "next search scope" not in t.text())

    t.send("\x04")
    screen = t.text()
    check("ctrl-d opens diagnostics", "│ diagnostics" in screen and "Claude Code retention" in screen)
    check("diagnostics reports the index", "indexed sessions" in screen)
    intact, why = box_is_intact(t)
    check("the diagnostics panel keeps its borders", intact, why)
    t.send("q")
    check("q closes diagnostics", "Claude Code retention" not in t.text())

    t.send("\x1b")
    check("esc closes transcripts", t.wait(timeout=5) == 0)
    t.close()


def layout_tests():
    print("layouts")
    sb = harness.sandbox("layout", live=[(0, "working")])
    for cols, rows, label in ((80, 24, "narrow"), (100, 14, "short"), (62, 20, "very narrow"),
                              (160, 45, "large")):
        t = harness.run(sb, cols=cols, rows=rows)
        t.wait_idle()
        screen = t.text()
        check(f"{label} {cols}x{rows} renders rows", "Retry the checkout webhook" in screen)
        check(f"{label} {cols}x{rows} keeps the resume and close buttons",
              "↵ resume" in screen and "esc close" in screen)
        check(f"{label} {cols}x{rows} never wraps the footer",
              all(len(line) <= cols for line in t.screen.display))
        if cols < 76:
            check(f"{label} {cols}x{rows} compacts the header", "titles  prompts" not in screen)
        if rows < 18:
            check(f"{label} {cols}x{rows} hides the preview to keep rows", "you ›" not in screen)
        t.close()


def state_tests():
    print("states")
    sb = harness.sandbox("states")
    shutil.rmtree(os.path.join(sb.root, ".claude", "projects"))
    shutil.rmtree(os.path.join(sb.root, ".codex", "sessions"))
    shutil.rmtree(os.path.join(sb.root, ".codex", "archived_sessions"))
    shutil.rmtree(os.path.join(sb.root, ".local", "share", "opencode"))
    shutil.rmtree(os.path.join(sb.root, ".factory", "sessions"))
    shutil.rmtree(os.path.join(sb.root, ".copilot", "session-state"))
    shutil.rmtree(os.path.join(sb.root, ".gemini"))
    shutil.rmtree(os.path.join(sb.root, ".qwen"))
    shutil.rmtree(os.path.join(sb.root, ".kimi"))
    os.makedirs(os.path.join(sb.root, ".claude", "projects"))
    t = harness.run(sb, cols=90, rows=24)
    t.wait_idle()
    check("empty state explains where transcripts live", "No sessions yet" in t.text()
          and ".claude/projects" in t.text() and ".codex/sessions" in t.text()
          and ".local/share/opencode" in t.text())
    t.send("x")
    check("empty state closes on a keypress", t.wait(timeout=5) is not None)
    t.close()

    sb2 = harness.sandbox("deps")
    bare = os.path.join(sb2.root, "bare-bin")
    os.makedirs(bare, exist_ok=True)
    for tool in ("bash", "sh", "python3", "env", "dirname", "readlink", "claude"):
        found = shutil.which(tool)
        if found and not os.path.exists(os.path.join(bare, tool)):
            os.symlink(found, os.path.join(bare, tool))
    t = harness.Term([harness.PLUGIN + "/transcripts"], dict(sb2.env(), PATH=bare), cols=90, rows=22)
    t.wait_idle()
    check("missing fzf is explained", "fzf is missing" in t.text() and "0.66.0" in t.text())
    t.close()

    stale = os.path.join(sb2.root, "stale-bin")
    os.makedirs(stale, exist_ok=True)
    with open(os.path.join(stale, "fzf"), "w") as fh:
        fh.write('#!/bin/sh\necho "0.44.1 (debian)"\n')
    os.chmod(os.path.join(stale, "fzf"), 0o755)
    t = harness.Term([harness.PLUGIN + "/transcripts"], dict(sb2.env(), PATH=stale + ":" + bare), cols=90, rows=22)
    t.wait_idle()
    check("old fzf names both versions", "0.44.1" in t.text() and "0.66.0" in t.text())
    t.close()

    nopy = os.path.join(sb2.root, "nopy-bin")
    os.makedirs(nopy, exist_ok=True)
    for tool in ("bash", "sh", "dirname", "readlink", "fzf"):
        found = shutil.which(tool)
        if found and not os.path.exists(os.path.join(nopy, tool)):
            os.symlink(found, os.path.join(nopy, tool))
    t = harness.Term([harness.PLUGIN + "/transcripts"], dict(sb2.env(), PATH=nopy), cols=90, rows=22)
    t.wait_idle()
    check("missing python3 is explained by the launcher", "needs python3" in t.text())
    t.close()


def resume_tests():
    print("resume")
    sb = harness.sandbox("resume", live=[(2, "working")])
    idle_sid, idle_cwd = sb.sessions[0]
    live_sid, live_cwd = sb.sessions[2]

    def dry(sid, cwd, **kw):
        env = dict(sb.env(**kw), TRANSCRIPTS_DRY_RUN="1")
        done = subprocess.run([harness.PLUGIN + "/transcripts", "resume", cwd, sid],
                              env=env, capture_output=True, text=True, cwd=env["HOME"])
        return done.stdout.split()[0] if done.stdout.strip() else done.stderr.strip()

    check("a plugin run opens a Herdr tab", dry(idle_sid, idle_cwd) == "tab")
    check("a running session is focused instead", dry(live_sid, live_cwd) == "focus")
    check("without Herdr it resumes in place", dry(idle_sid, idle_cwd, herdr=False, plugin=False) == "exec")
    check("outside a plugin pane it resumes in place", dry(idle_sid, idle_cwd, plugin=False) == "exec")

    sb.clear_calls()
    env = sb.env()
    subprocess.run([harness.PLUGIN + "/transcripts", "resume", idle_cwd, idle_sid],
                   env=env, capture_output=True, text=True, cwd=env["HOME"])
    calls = sb.calls()
    check("the new tab is created in the session's directory",
          any(c.startswith(f"tab create --cwd {idle_cwd} --label checkout-api --no-focus") for c in calls))
    check("claude is started in that pane with --resume",
          any(c.startswith("pane send-text") and f"claude --resume {idle_sid}" in c for c in calls))
    # `agent focus` cannot resolve a pane Herdr does not track yet, and the
    # provider has not started at this point, so the tab is focused instead.
    check("the new tab is focused", any(c.startswith("tab focus") for c in calls))

    sb.clear_calls()
    subprocess.run([harness.PLUGIN + "/transcripts", "resume", live_cwd, live_sid],
                   env=env, capture_output=True, text=True, cwd=env["HOME"])
    calls = sb.calls()
    check("a running session is never started twice",
          not any("send-text" in c or "tab create" in c for c in calls))
    check("a running session is focused where it already lives",
          any(c == "agent focus w1:p2" for c in calls))

    sb.clear_calls()
    env = sb.env()
    config = os.path.join(sb.root, ".config", "herdr", "plugins", "config", "transcripts", "config.toml")
    os.makedirs(os.path.dirname(config), exist_ok=True)
    with open(config, "w") as fh:
        fh.write("skip_permissions = true\nchrome = true\n")
    subprocess.run([harness.PLUGIN + "/transcripts", "resume", idle_cwd, idle_sid],
                   env=env, capture_output=True, text=True, cwd=env["HOME"])
    check("armed flags reach the claude command",
          any("--dangerously-skip-permissions" in c and "--chrome" in c for c in sb.calls()))
    check("the resume never waits for Herdr to detect the agent",
          not any("agent start" in c for c in sb.calls()))


def binding_tests():
    print("bindings")
    sb = harness.sandbox("bindings")
    env = sb.env()
    import importlib.util
    spec = importlib.util.spec_from_file_location("tr", os.path.join(harness.PLUGIN, "transcripts.py"))
    tr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tr)
    commands = [bind.split(":", 1)[1] for bind in tr.bindings()]
    commands += [f"transform({tr.Q} click header)", f"transform({tr.Q} click footer)",
                 f"reload({tr.Q} list)", tr.refresh_bars(), f"{tr.Q} preview x ''", f"start({tr.Q} warm)"]
    for command in commands:
        for call in re.findall(r"[a-z-]+\(([^)]*transcripts\.py[^)]*)\)", command) or []:
            if " overlay " in call:
                continue
            args = call.split()[1:]
            done = subprocess.run([harness.PLUGIN + "/transcripts", *args], env=env,
                                  capture_output=True, text=True, cwd=env["HOME"])
            check(f"`{' '.join(args) or '(picker)'}` is a command the plugin answers",
                  done.returncode == 0 and "usage:" not in done.stderr, done.stderr.strip()[:80])
    index = os.path.join(sb.root, ".cache", "herdr-transcripts", "index.bin")
    check("warm builds the index without printing anything", os.path.exists(index) and not subprocess.run(
        [harness.PLUGIN + "/transcripts", "warm"], env=env, capture_output=True, text=True, cwd=env["HOME"]).stdout)


def mouse_tests():
    print("mouse")
    sb = harness.sandbox("mouse", live=[(0, "working")])
    t = harness.run(sb, cols=120, rows=26)
    t.wait_idle()

    def lit(needle):
        return t.cell(needle).bg != "default"

    t.click("tools", into=2)
    check("clicking a scope chip switches the scope", lit("tools") and not lit("conversation"))
    t.click("conversation", into=2)
    check("clicking another scope chip switches back", lit("conversation") and not lit("tools"))

    t.click("skip-permissions", into=2)
    check("clicking a flag chip arms it", "skip-permissions ✓" in t.text())
    check("an armed dangerous flag is filled with the warning colour",
          t.cell("skip-permissions").bg == "ffdeab", str(t.cell("skip-permissions")))
    t.click("skip-permissions", into=2)
    check("clicking it again disarms it", "skip-permissions ✓" not in t.text())

    t.click("recent", into=2)
    check("clicking the sort value cycles it", "sort oldest" in t.text())
    t.click("sort", into=1, after=t.find("ctrl-o")[0])
    check("clicking the sort key in the footer cycles it too", "sort size" in t.text())

    t.click("checks", into=2)
    check("clicking checks opens diagnostics", "Claude Code retention" in t.text())
    t.send("q")
    t.click("keys", into=1)
    check("clicking keys opens the key overlay", "next search scope" in t.text())
    t.send("q")

    t.click("Tighten the exporter retry budget", into=4)
    check("clicking a row selects it", f"codex  ·  {sb.codex[0][0]}" in t.text())

    t.click("close", into=1)
    check("clicking esc close exits", t.wait(timeout=6) == 0)
    t.close()


def write_transcript(sb, name, lines, cwd=None):
    cwd = cwd or os.path.join(sb.root, "code", "odd")
    os.makedirs(cwd, exist_ok=True)
    project = os.path.join(sb.root, ".claude", "projects", cwd.replace("/", "-"))
    os.makedirs(project, exist_ok=True)
    path = os.path.join(project, name + ".jsonl")
    with open(path, "w") as fh:
        for line in lines:
            fh.write(json.dumps(line, separators=(",", ":")) + "\n")
    return path


def hostile_data_tests():
    print("hostile data")
    sb = harness.sandbox("hostile")
    tabbed = os.path.join(sb.root, "code", "with\ta\ttab")
    write_transcript(sb, "11111111-1111-4111-8111-111111111111", [
        {"type": "ai-title", "aiTitle": "A tabbed\tproject"},
        {"type": "user", "cwd": tabbed, "gitBranch": "main",
         "message": {"role": "user", "content": "a session in a tabbed directory"}}], cwd=tabbed)
    write_transcript(sb, "22222222-2222-4222-8222-222222222222", [
        {"type": "ai-title", "aiTitle": "Control\u0000characters\u0001here"},
        {"type": "user", "cwd": os.path.join(sb.root, "code", "odd"), "gitBranch": "main",
         "message": {"role": "user", "content": "a prompt with a \u0000 nul in it"}}])
    write_transcript(sb, "33333333-3333-4333-8333-333333333333", [
        {"type": "user", "cwd": os.path.join(sb.root, "code", "odd"),
         "message": {"role": "user", "content": [42, None, {"type": "text", "text": "after the junk"}]}},
        {"type": "assistant", "cwd": os.path.join(sb.root, "code", "odd"),
         "message": {"role": "assistant",
                     "content": [{"type": "tool_use", "name": "Bash", "input": "not an object"}]}}])
    with open(os.path.join(sb.root, ".claude", "projects",
                           os.path.join(sb.root, "code", "odd").replace("/", "-"),
                           "44444444-4444-4444-8444-444444444444.jsonl"), "w") as fh:
        fh.write('{"type":"user","cwd":"/nope"\n{ broken\n')

    t = harness.run(sb, cols=100, rows=26)
    t.wait_idle()
    screen = t.text()
    check("a directory with tabs in its name still lists", "with\ta\ttab" in screen.expandtabs(1)
          or "tabbed" in screen)
    check("control characters never reach the list", "\x00" not in screen and "\x01" not in screen)
    check("a transcript with junk content parts still indexes", "after the junk" in screen)
    counted = re.search(r"(\d+)/(\d+)\s*$", screen.split("\n")[2])
    check("a malformed transcript does not stop the others",
          counted is not None and int(counted.group(2)) >= 20, screen.split("\n")[2])
    t.send("junk")
    check("the odd session is searchable", "after the junk" in t.text())
    t.send("\x15")
    t.close()


def negation_tests():
    print("queries")
    sb = harness.sandbox("queries")
    t = harness.run(sb, cols=100, rows=26)
    t.wait_idle()
    t.send("retry !idempotency")
    screen = t.text()
    check("an excluded word is not highlighted as a match",
          "messages match" not in screen or "idempotency" not in screen.split("messages match")[1])
    t.send("\x15")
    t.send("kafka")
    check("a plain word still counts as a match", "messages match" in t.text())
    t.close()


def settings_tests():
    print("claude settings")
    sb = harness.sandbox("settings")
    path = os.path.join(sb.root, ".claude", "settings.json")
    with open(path, "w") as fh:
        fh.write('{"cleanupPeriodDays": 14, "env": {"KEEP": "me"} ')
    t = harness.run(sb, cols=100, rows=28)
    t.wait_idle()
    t.send("\x04")
    t.send("\r")
    with open(path) as fh:
        after = fh.read()
    check("a broken settings.json is left alone", '"KEEP": "me"' in after and "3650" not in after)
    check("and the panel says so", "not valid JSON" in t.text())
    t.send("q")
    t.close()

    with open(path, "w") as fh:
        json.dump({"cleanupPeriodDays": 14, "env": {"KEEP": "me"}}, fh)
    t = harness.run(sb, cols=100, rows=28)
    t.wait_idle()
    t.send("\x04")
    t.send("\r")
    with open(path) as fh:
        after = json.load(fh)
    check("a valid settings.json keeps its other keys",
          after.get("env") == {"KEEP": "me"} and after["cleanupPeriodDays"] == 3650, str(after))
    t.send("q")
    t.close()


def click_tests():
    print("clicks")
    sb = harness.sandbox("clicks")

    def click(where, word):
        env = dict(sb.env())
        env["FZF_CLICK_HEADER_WORD" if where == "header" else "FZF_CLICK_FOOTER_WORD"] = word
        done = subprocess.run([harness.PLUGIN + "/transcripts", "click", where],
                              env=env, capture_output=True, text=True, cwd=env["HOME"])
        return done.stdout.strip()

    check("clicking a scope chip switches the scope", "change-nth(4)" in click("header", "tools"))
    check("clicking a flag chip toggles it", click("header", "chrome").startswith("transform-header"))
    check("clicking the header label does nothing", click("header", "scope") == "ignore")
    check("clicking a sort value resorts", "reload" in click("header", "recent"))
    check("clicking enter in the footer accepts", click("footer", "resume") == "accept")
    check("clicking keys in the footer opens help", "overlay help" in click("footer", "keys"))
    check("clicking plain footer text does nothing", click("footer", "·") == "ignore")


def retention_tests():
    print("retention")
    sb = harness.sandbox("retention")
    settings = os.path.join(sb.root, ".claude", "settings.json")
    with open(settings, "w") as fh:
        fh.write('{"cleanupPeriodDays": 14}\n')
    t = harness.run(sb, cols=100, rows=30)
    t.wait_idle()
    t.send("\x04")
    check("diagnostics warns about a short retention", "14 days" in t.text()
          and "deletes transcripts older than" in t.text())
    check("diagnostics offers the fix as a button", "↵ keep 3650 days" in t.text())
    t.send("\r")
    with open(settings) as fh:
        after = fh.read()
    check("enter raises cleanupPeriodDays", '"cleanupPeriodDays": 3650' in after, after.strip())
    check("diagnostics refreshes after the fix", "3650 days" in t.text())
    t.send("q")
    t.close()

    missing = harness.sandbox("resume-gone")
    sid, cwd = missing.sessions[0]
    shutil.rmtree(cwd)
    env = missing.env()
    t = harness.Term([harness.PLUGIN + "/transcripts", "resume", cwd, sid], env, cols=90, rows=22)
    t.wait_idle()
    check("a session whose directory is gone says so", "session directory missing" in t.text())
    t.close()


def diagnostics_tests():
    print("diagnostics")
    sb = harness.sandbox("diagnostics")
    project = os.path.join(sb.root, ".claude", "projects",
                           os.path.join(sb.root, "code", "odd").replace("/", "-"))
    os.makedirs(os.path.join(project, "55555555-5555-4555-8555-555555555555.jsonl"))

    t = harness.run(sb, cols=100, rows=70)
    t.wait_idle()
    t.send("\x04")
    screen = t.text()
    check("an unreadable transcript is reported as skipped", "skipped" in screen
          and "left out of the list" in screen, screen)
    check("the skipped transcript is named", "55555555" in screen, screen)
    check("a healthy herdr reports its agents", "live agents" in screen, screen)
    check("diagnostics counts each provider", "claude sessions" in screen and "codex sessions" in screen, screen)
    t.send("q")
    t.close()

    index = os.path.join(sb.root, ".cache", "herdr-transcripts", "index.bin")
    with open(index, "w") as fh:
        fh.write("{ not json\n")
    t = harness.Term([harness.PLUGIN + "/transcripts", "overlay", "diagnostics"],
                     sb.env(), cols=100, rows=70)
    t.wait_idle()
    check("a corrupt index is not called missing", "not built yet" not in t.text(), t.text())
    check("a corrupt index says so", "is unreadable" in t.text(), t.text())
    t.close()

    with open(sb.herdr, "w") as fh:
        fh.write("#!/bin/sh\necho 'herdr: socket not found' >&2\nexit 1\n")
    os.chmod(sb.herdr, 0o755)
    t = harness.Term([harness.PLUGIN + "/transcripts", "overlay", "diagnostics"],
                     sb.env(), cols=100, rows=70)
    t.wait_idle()
    check("a failing herdr is not shown as healthy", "live agents" not in t.text(), t.text())
    check("a failing herdr reports the reason", "socket not found" in t.text(), t.text())
    t.close()


def codex_tests():
    print("codex")
    sb = harness.sandbox("codex", live=[(0, "working"), (0, "blocked", "codex")])
    t = harness.run(sb, cols=110, rows=30)
    t.wait_idle()
    screen = t.text()
    check("a named codex session lists under its thread name", "Tighten the exporter retry budget" in screen)
    check("codex rows say which tool they belong to", "codex  ·  checkout-api" in screen)
    check("codex rows carry the branch from the state database",
          "codex  ·  checkout-api  ·  perf/exporter-retry" in screen)
    check("an image-first codex session is titled by its first words, not [image]",
          "walk me through the websocket reconnect logic" in screen and "  [image]" not in screen)
    check("a live codex agent is marked", "● blocked" in screen)
    t.send("codexarchived")
    check("an archived codex session is listed", "Archive the flaky pager smoke test" in t.text(), t.text())
    t.send("\x15")
    t.send("websocket")
    screen = t.text()
    check("an unnamed codex session falls back to its first prompt",
          "walk me through the websocket reconnect logic" in screen)
    check("the codex preview labels who asked", "you ›" in screen)
    check("codex instruction and environment blocks never become prompts",
          "AGENTS.md instructions" not in screen and "environment_context" not in screen)
    t.send("\x15")
    t.send("exponential")
    check("the codex preview labels who answered", "codex ›" in t.text())
    t.send("\x15")
    t.send("lintsweep")
    check("codex exec runs stay hidden", "0/" in t.text().split("\n")[2], t.text().split("\n")[2])
    t.send("\x15")
    t.send("cacheaudit")
    check("codex subagent threads stay hidden", "0/" in t.text().split("\n")[2], t.text().split("\n")[2])
    t.send("\x15")
    t.send("toolnoise")
    check("codex tool output is not indexed", "0/" in t.text().split("\n")[2], t.text().split("\n")[2])
    t.send("\x15")
    t.send("contributor guide")
    screen = t.text()
    check("a 2025 codex transcript lists with the directory from its environment block",
          "Generate a contributor guide" in screen and "codex  ·  infra" in screen)
    t.send("\x15")
    t.send("exporter_test")
    check("codex tool calls stay out of the conversation scope",
          "0/" in t.text().split("\n")[2], t.text().split("\n")[2])
    t.send("\t\t\t\t")
    check("tools scope finds the codex shell call", "Tighten the exporter retry budget" in t.text()
          and "0/" not in t.text().split("\n")[2], t.text().split("\n")[2])
    t.send("\x1b")
    t.close()

    named_sid, named_cwd = sb.codex[0]
    env = sb.env()
    dry = dict(env, TRANSCRIPTS_DRY_RUN="1")
    done = subprocess.run([harness.PLUGIN + "/transcripts", "resume", named_cwd, "codex:" + named_sid],
                          env=dry, capture_output=True, text=True, cwd=env["HOME"])
    check("a live codex session is focused, not restarted", done.stdout.startswith("focus codex:"), done.stdout)
    unnamed_sid, unnamed_cwd = sb.codex[1]
    done = subprocess.run([harness.PLUGIN + "/transcripts", "resume", unnamed_cwd, "codex:" + unnamed_sid],
                          env=dry, capture_output=True, text=True, cwd=env["HOME"])
    check("an idle codex session opens a tab with codex resume",
          done.stdout.startswith("tab codex:") and f"codex resume {unnamed_sid}" in done.stdout, done.stdout)

    sb.clear_calls()
    config = os.path.join(sb.root, ".config", "herdr", "plugins", "config", "transcripts", "config.toml")
    os.makedirs(os.path.dirname(config), exist_ok=True)
    with open(config, "w") as fh:
        fh.write("skip_permissions = true\nchrome = true\n")
    subprocess.run([harness.PLUGIN + "/transcripts", "resume", unnamed_cwd, "codex:" + unnamed_sid],
                   env=env, capture_output=True, text=True, cwd=env["HOME"])
    sent = [c for c in sb.calls() if c.startswith("pane send-text")]
    check("codex is resumed in its own directory",
          any(c.startswith(f"tab create --cwd {unnamed_cwd} --label atlas-web") for c in sb.calls()))
    check("skip-permissions becomes --yolo for codex", any("--yolo" in c for c in sent), str(sent))
    check("claude flags never reach codex",
          not any("--dangerously-skip-permissions" in c or "--chrome" in c for c in sent), str(sent))


def agent_only(t, label):
    for _ in range(10):
        if label in t.text():
            return True
        t.send("\x01")
    return label in t.text()


def dry_resume(sb, cwd, uid):
    env = dict(sb.env(), TRANSCRIPTS_DRY_RUN="1")
    done = subprocess.run([harness.PLUGIN + "/transcripts", "resume", cwd, uid],
                          env=env, capture_output=True, text=True, cwd=env["HOME"])
    return done.stdout.strip() or done.stderr.strip()


def arm_skip_permissions(sb):
    config = os.path.join(sb.root, ".config", "herdr", "plugins", "config", "transcripts", "config.toml")
    os.makedirs(os.path.dirname(config), exist_ok=True)
    with open(config, "w") as fh:
        fh.write("skip_permissions = true\nchrome = true\n")


def opencode_tests():
    print("opencode")
    sb = harness.sandbox("opencode")
    t = harness.run(sb, cols=110, rows=30)
    t.wait_idle()
    screen = t.text()
    check("an opencode session lists under its title",
          "Stop the uploader retrying a rejected chunk" in screen)
    check("opencode rows say which tool they belong to", "opencode  ·  infra" in screen)
    t.send("bucket")
    screen = t.text()
    check("an opencode prompt is searchable and the preview labels who asked",
          "Stop the uploader retrying a rejected chunk" in screen and "you ›" in screen)
    t.send("\x15")
    t.send("spinning")
    check("the opencode preview labels who answered", "opencode ›" in t.text())
    t.send("\x15")
    t.send("syntheticnoise")
    check("opencode synthetic parts never become prompts",
          "0/" in t.text().split("\n")[2], t.text().split("\n")[2])
    t.send("\x15")
    t.send("remindernoise")
    check("opencode system reminders never become prompts",
          "0/" in t.text().split("\n")[2], t.text().split("\n")[2])
    t.send("\x15")
    t.send("reasoningnoise")
    check("opencode reasoning is not indexed", "0/" in t.text().split("\n")[2], t.text().split("\n")[2])
    t.send("\x15")
    t.send("toolnoise")
    check("opencode tool output is not indexed", "0/" in t.text().split("\n")[2], t.text().split("\n")[2])
    t.send("\x15")
    t.send("uploader_chunks")
    check("opencode tool calls stay out of the conversation scope",
          "0/" in t.text().split("\n")[2], t.text().split("\n")[2])
    t.send("\t\t\t\t")
    check("tools scope finds the opencode tool call",
          "Stop the uploader retrying a rejected chunk" in t.text()
          and "0/" not in t.text().split("\n")[2], t.text().split("\n")[2])
    t.send("\x15")
    t.send("\t\t")

    check("ctrl-a narrows to opencode", agent_only(t, "opencode only"), t.text())
    screen = t.text()
    check("the opencode filter drops the other agents",
          "Stop the uploader retrying a rejected chunk" in screen
          and "Retry the checkout webhook" not in screen
          and "Tighten the exporter retry budget" not in screen)
    check("a child opencode session is never listed", "Trace the dropped websocket frames" not in screen)
    check("an opencode session without a title or a prompt stays out", "notebook" not in screen)
    t.send("\x1b")
    t.close()

    sid, cwd = sb.opencode[0]
    plan = dry_resume(sb, cwd, "opencode:" + sid)
    check("an idle opencode session opens a tab with opencode --session",
          plan.strip() == f"tab opencode:{sid} {cwd} opencode --session {sid}", plan)

    arm_skip_permissions(sb)
    plan = dry_resume(sb, cwd, "opencode:" + sid)
    check("resume flags never reach opencode",
          plan.strip().endswith(f"opencode --session {sid}"), plan)


def droid_tests():
    print("droid")
    sb = harness.sandbox("droid")
    sid, cwd = harness.fixtures.DROID_SIDS[0], os.path.join(sb.root, "code", "atlas-web")
    t = harness.run(sb, cols=110, rows=30)
    t.wait_idle()
    check("ctrl-a cycles to droid only", agent_only(t, "droid only"), t.text())
    screen = t.text()
    check("a droid session lists under the title from its session start",
          "Move the droid picker off the deprecated tiles endpoint" in screen, screen)
    check("droid rows say which tool they belong to", "droid  ·  atlas-web" in screen, screen)
    check("droid only hides the other agents",
          "claude  ·" not in screen and "codex  ·" not in screen, screen)
    check("a droid session that holds nothing but a header stays out of the list",
          "New Session" not in screen, screen)
    check("the droid preview labels who spoke", "you ›" in screen and "droid ›" in screen, screen)
    check("the droid system reminder never reaches the preview",
          "<system-reminder>" not in screen and "% pwd" not in screen, screen)

    t.send("reminderleak")
    check("droid system reminders never become prompts",
          "0/" in t.text().split("\n")[2], t.text().split("\n")[2])
    t.send("\x15")
    t.send("toolnoise")
    check("droid tool results and todo lists are not indexed",
          "0/" in t.text().split("\n")[2], t.text().split("\n")[2])
    t.send("\x15")
    t.send("\x1b")
    t.close()

    plan = dry_resume(sb, cwd, "droid:" + sid)
    check("an idle droid session opens a tab with droid --resume",
          plan.startswith("tab droid:") and f"droid --resume {sid}" in plan, plan)
    arm_skip_permissions(sb)
    plan = dry_resume(sb, cwd, "droid:" + sid)
    check("skip-permissions becomes --auto high for droid", "--auto high" in plan, plan)
    check("claude flags never reach droid", "--dangerously-skip-permissions" not in plan
          and "--chrome" not in plan, plan)


def copilot_tests():
    print("copilot")
    sb = harness.sandbox("copilot")
    sid, cwd = harness.fixtures.COPILOT_SIDS[0], os.path.join(sb.root, "code", "infra")
    t = harness.run(sb, cols=110, rows=30)
    t.wait_idle()
    check("ctrl-a cycles to copilot only", agent_only(t, "copilot only"), t.text())
    screen = t.text()
    check("a copilot session lists under the summary from its workspace file",
          "Cache the rendered tiles on disk" in screen, screen)
    check("copilot rows carry the directory and branch from the session start",
          "copilot  ·  infra  ·  feat/tile-cache" in screen, screen)
    check("a copilot session without a session start falls back to its workspace file",
          "copilot  ·  dotfiles" in screen and "explain what the prompt hook rewrites" in screen, screen)
    check("copilot only hides the other agents",
          "claude  ·" not in screen and "codex  ·" not in screen, screen)
    check("the copilot preview labels who spoke", "you ›" in screen and "copilot ›" in screen, screen)
    check("the injected copilot prompt text never reaches the preview",
          "<current_datetime>" not in screen and "<reminder>" not in screen, screen)

    t.send("transformedleak")
    check("the transformed copilot prompt never becomes a prompt",
          "0/" in t.text().split("\n")[2], t.text().split("\n")[2])
    t.send("\x15")
    t.send("toolnoise")
    check("copilot tool output and notifications are not indexed",
          "0/" in t.text().split("\n")[2], t.text().split("\n")[2])
    t.send("\x15")
    t.send("\x1b")
    t.close()

    plan = dry_resume(sb, cwd, "copilot:" + sid)
    check("an idle copilot session opens a tab with copilot --resume",
          plan.startswith("tab copilot:") and f"copilot --resume={sid}" in plan, plan)
    arm_skip_permissions(sb)
    plan = dry_resume(sb, cwd, "copilot:" + sid)
    check("skip-permissions becomes --allow-all-tools for copilot", "--allow-all-tools" in plan, plan)
    check("claude flags never reach copilot", "--dangerously-skip-permissions" not in plan
          and "--chrome" not in plan, plan)


def empty_query(t, query, name):
    t.send(query)
    check(name, "0/" in t.text().split("\n")[2], t.text().split("\n")[2])
    t.send("\x15")


def gemini_tests():
    print("gemini")
    sb = harness.sandbox("gemini")
    t = harness.run(sb, cols=110, rows=30)
    t.wait_idle()
    t.send("changelog")
    screen = t.text()
    check("a summarised gemini session lists under its summary",
          "Stop the changelog from repeating entries" in screen)
    check("gemini rows say which tool they belong to", "gemini  ·  checkout-api" in screen)
    t.send("\x15")

    t.send("heartbeat")
    screen = t.text()
    check("an unsummarised gemini session falls back to its first prompt",
          "walk me through the presence heartbeat" in screen)
    check("a gemini session in a hashed directory finds its cwd", "gemini  ·  atlas-web" in screen)
    check("the gemini preview labels who asked", "you ›" in screen)
    check("a message recorded twice is previewed once", "1 of 2 messages match" in screen, screen)
    t.send("\x15")
    t.send("beats")
    check("the gemini preview labels who answered", "gemini ›" in t.text())
    t.send("\x15")

    empty_query(t, "geminicontextnoise", "the gemini session context block never becomes a prompt")
    empty_query(t, "geminisystemnoise", "gemini continuation prompts never become prompts")
    empty_query(t, "geminiinfonoise", "gemini info messages are not indexed")
    empty_query(t, "geminithoughtnoise", "gemini reasoning is not indexed")
    empty_query(t, "geminitoolnoise", "gemini tool output is not indexed")
    empty_query(t, "geminiorphan", "a gemini session with no resolvable directory stays hidden")
    empty_query(t, "geminidelegate", "gemini subagent threads stay hidden")

    t.send("changelog_test")
    check("gemini tool calls stay out of the conversation scope",
          "0/" in t.text().split("\n")[2], t.text().split("\n")[2])
    t.send("\t\t\t\t")
    check("tools scope finds the gemini tool call", "Stop the changelog from repeating entries" in t.text()
          and "0/" not in t.text().split("\n")[2], t.text().split("\n")[2])
    t.send("\t\t")
    t.send("\x15")
    check("ctrl-a narrows to gemini", agent_only(t, "gemini only"))
    check("no other agent is left in the list", "claude  ·" not in t.text()
          and "codex  ·" not in t.text(), t.text())
    t.send("\x1b")
    t.close()

    sid, cwd = harness.fixtures.MADE["gemini"][0]
    plan = dry_resume(sb, cwd, "gemini:" + sid)
    check("an idle gemini session opens a tab with gemini --resume",
          plan.startswith("tab gemini:") and f"gemini --resume {sid}" in plan, plan)
    arm_skip_permissions(sb)
    check("skip-permissions becomes --yolo for gemini", "--yolo" in dry_resume(sb, cwd, "gemini:" + sid))
    check("claude flags never reach gemini",
          "--chrome" not in dry_resume(sb, cwd, "gemini:" + sid))

    sid, cwd = harness.fixtures.MADE["gemini"][-1]
    shutil.rmtree(os.path.join(sb.root, ".cache", "herdr-transcripts"), ignore_errors=True)
    rows = [subprocess.run([harness.PLUGIN + "/transcripts", "list"], env=sb.env(), capture_output=True,
                           text=True, cwd=sb.root).stdout for _ in range(2)]
    check("a hashed gemini directory known only from another agent's sessions is hidden on a cold index",
          sid not in rows[0])
    check("the next index pass resolves it from the other agent's directory", sid in rows[1])


def qwen_tests():
    print("qwen")
    sb = harness.sandbox("qwen")
    t = harness.run(sb, cols=110, rows=30)
    t.wait_idle()
    t.send("weekend")
    screen = t.text()
    check("a qwen session lists under its first prompt",
          "why does the pager rotation skip the weekend shift" in screen)
    check("qwen rows carry the tool, the cwd and the branch", "qwen  ·  infra  ·  main" in screen)
    check("the qwen preview labels who asked", "you ›" in screen)
    t.send("\x15")
    t.send("Monday")
    check("the qwen preview labels who answered", "qwen ›" in t.text())
    t.send("\x15")

    empty_query(t, "qwentoolnoise", "qwen tool output is not indexed")
    empty_query(t, "qwendelegate", "qwen sidechain turns stay out of the list")

    t.send("runbooks/pager.md")
    check("qwen tool calls stay out of the conversation scope",
          "0/" in t.text().split("\n")[2], t.text().split("\n")[2])
    t.send("\t\t\t\t")
    check("tools scope finds the qwen tool call", "weekend shift" in t.text()
          and "0/" not in t.text().split("\n")[2], t.text().split("\n")[2])
    t.send("\t\t")
    t.send("\x15")
    check("ctrl-a narrows to qwen", agent_only(t, "qwen only"))
    check("no other agent is left in the list", "claude  ·" not in t.text()
          and "codex  ·" not in t.text(), t.text())
    t.send("\x1b")
    t.close()

    sid, cwd = harness.fixtures.MADE["qwen"][0]
    plan = dry_resume(sb, cwd, "qwen:" + sid)
    check("an idle qwen session opens a tab with qwen --resume",
          plan.startswith("tab qwen:") and f"qwen --resume {sid}" in plan, plan)
    arm_skip_permissions(sb)
    check("skip-permissions becomes --yolo for qwen", "--yolo" in dry_resume(sb, cwd, "qwen:" + sid))


def kimi_tests():
    print("kimi")
    sb = harness.sandbox("kimi")
    t = harness.run(sb, cols=110, rows=30)
    t.wait_idle()
    t.send("grafana")
    screen = t.text()
    check("a kimi session lists under its first prompt",
          "the grafana agent keeps restarting on the metrics box" in screen)
    check("kimi rows say which tool they belong to", "kimi  ·  infra" in screen)
    check("the kimi preview labels who asked", "you ›" in screen)
    t.send("\x15")
    t.send("kubernetes")
    check("a kimi directory the registry omits still finds its cwd", "kimi  ·  dotfiles" in t.text())
    t.send("\x15")
    t.send("MemoryMax")
    check("the kimi preview labels who answered", "kimi ›" in t.text())
    t.send("\x15")

    empty_query(t, "kimithoughtnoise", "kimi reasoning is not indexed")
    empty_query(t, "kimitoolnoise", "kimi tool output is not indexed")
    empty_query(t, "kiminoise", "kimi system turns never become prompts")
    empty_query(t, "kimiwirenoise", "the kimi wire log is not indexed")

    t.send("grafana-agent")
    check("kimi tool calls stay out of the conversation scope",
          "0/" in t.text().split("\n")[2], t.text().split("\n")[2])
    t.send("\t\t\t\t")
    check("tools scope finds the kimi tool call", "grafana agent keeps restarting" in t.text()
          and "0/" not in t.text().split("\n")[2], t.text().split("\n")[2])
    t.send("\t\t")
    t.send("\x15")
    check("ctrl-a narrows to kimi", agent_only(t, "kimi only"))
    check("no other agent is left in the list", "claude  ·" not in t.text()
          and "codex  ·" not in t.text(), t.text())
    t.send("\x1b")
    t.close()

    sid, cwd = harness.fixtures.MADE["kimi"][0]
    plan = dry_resume(sb, cwd, "kimi:" + sid)
    check("an idle kimi session opens a tab with kimi --session",
          plan.startswith("tab kimi:") and f"kimi --session {sid}" in plan, plan)
    arm_skip_permissions(sb)
    check("skip-permissions becomes --yolo for kimi", "--yolo" in dry_resume(sb, cwd, "kimi:" + sid))


def main():
    unit_tests()
    codex_tests()
    opencode_tests()
    gemini_tests()
    qwen_tests()
    kimi_tests()
    picker_tests()
    layout_tests()
    state_tests()
    resume_tests()
    binding_tests()
    mouse_tests()
    hostile_data_tests()
    negation_tests()
    settings_tests()
    click_tests()
    retention_tests()
    diagnostics_tests()
    droid_tests()
    copilot_tests()
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
