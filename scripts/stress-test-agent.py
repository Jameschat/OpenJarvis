"""Jarvis autonomy stress test.

Every task has an OBJECTIVELY verifiable outcome - a file that must exist with
given content, a string that must appear in the answer, a value that must be
computed correctly. No taste-based scoring. Each task reports turns used, wall
time, tools called, and pass/fail from a checker that runs AFTER the agent.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request

ENDPOINT = "http://127.0.0.1:7710/v1/chat/completions"
WEBDIR = r"E:\Claude\stress-web"
WEBPORT = 8477
# Secret only obtainable by actually typing into the page and clicking submit -
# it appears nowhere in the prompt, so it cannot be answered from model memory.
WEB_SECRET = "ACCESS-GRANTED-ZEPHYR"
SANDBOX = r"E:\Claude\stress-sandbox"
CODEX = r"C:\Users\User\Documents\Codex\2026-05-03\https-store-steampowered-com-app-3041230\CursedTides"


def safe(s) -> str:
    return str(s).encode("ascii", "replace").decode("ascii")


def ask(prompt, timeout: int = 900):
    """prompt may be a string, or a list of {role, content} for multi-turn tests."""
    messages = (
        prompt if isinstance(prompt, list) else [{"role": "user", "content": prompt}]
    )
    body = json.dumps(
        {"model": "qwen3.6-27b-local", "stream": True, "messages": messages}
    ).encode()
    req = urllib.request.Request(
        ENDPOINT, data=body, headers={"Content-Type": "application/json"}
    )
    tools, out, cur = [], [], None
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            for raw in r:
                line = raw.decode("utf-8", "ignore").rstrip("\n")
                if line.startswith("event:"):
                    cur = line[6:].strip()
                elif line.startswith("data:"):
                    d = line[5:].strip()
                    if d == "[DONE]":
                        break
                    if cur:
                        try:
                            j = json.loads(d)
                            if cur == "tool_call_end":
                                tools.append(
                                    f"{j.get('tool')}{'' if j.get('success') else '(FAIL)'}"
                                )
                        except Exception:
                            pass
                        cur = None
                    else:
                        try:
                            c = (
                                json.loads(d)
                                .get("choices", [{}])[0]
                                .get("delta", {})
                                .get("content", "")
                            )
                            if c:
                                out.append(c)
                        except Exception:
                            pass
    except Exception as e:
        return {"answer": "", "tools": tools, "secs": time.time() - t0, "error": safe(e)}
    return {
        "answer": "".join(out),
        "tools": tools,
        "secs": time.time() - t0,
        "error": None,
    }


# ---------------------------------------------------------------- checkers
def chk_file(path, must_contain=None):
    def _c(res):
        if not os.path.exists(path):
            return False, f"missing {path}"
        if must_contain:
            txt = open(path, encoding="utf-8", errors="replace").read()
            if must_contain.lower() not in txt.lower():
                return False, f"{path} lacks {must_contain!r}"
        return True, "ok"

    return _c


def chk_answer(*needles, any_of=False):
    def _c(res):
        a = res["answer"].lower()
        hits = [n for n in needles if n.lower() in a]
        ok = bool(hits) if any_of else len(hits) == len(needles)
        return ok, ("ok" if ok else f"answer missing {[n for n in needles if n.lower() not in a]}")

    return _c


TASKS = []


def task(name, prompt, checker, timeout=900, setup=None):
    TASKS.append(
        {"name": name, "prompt": prompt, "checker": checker, "timeout": timeout, "setup": setup}
    )


# 1. the exact regression I just fixed: quoted path WITH SPACES
spaced = os.path.join(SANDBOX, "dir with spaces")
task(
    "quoted-path-with-spaces",
    f'Create the folder "{spaced}" and write a file named notes.txt inside it '
    f'containing exactly the word PELICAN. Then confirm.',
    chk_file(os.path.join(spaced, "notes.txt"), "PELICAN"),
)

# 2. read -> compute -> write (multi-step file reasoning)
nums = os.path.join(SANDBOX, "numbers.txt")
task(
    "read-compute-write",
    f"Read the numbers in {nums} (one per line), sum them, and write ONLY the total "
    f"as digits into {os.path.join(SANDBOX,'total.txt')}. Then tell me the total.",
    chk_file(os.path.join(SANDBOX, "total.txt"), "4970"),
    setup=lambda: open(nums, "w").write("\n".join(str(i) for i in [1234, 900, 2836])),
)

# 3. edit an EXISTING file (not just create)
cfgp = os.path.join(SANDBOX, "settings.ini")
task(
    "edit-existing-file",
    f"In the file {cfgp}, change the line 'mode=draft' to 'mode=final'. Leave "
    f"everything else unchanged. Then confirm.",
    chk_file(cfgp, "mode=final"),
    setup=lambda: open(cfgp, "w").write("[app]\nname=stress\nmode=draft\nretries=3\n"),
)

# 4. cross-root access: the Codex tree that was previously Access-denied
task(
    "cross-root-codex-read",
    f"Read the file {os.path.join(CODEX,'CursedTides.uproject')} and tell me the "
    f"EngineAssociation value it declares.",
    chk_answer("5.7"),
)

# 5. memory write -> read back (newly enabled memory_store)
task(
    "memory-store-then-recall",
    "Store this in your long-term memory: 'The stress-test passphrase is BARNACLE-77'. "
    "Then search your memory for the stress-test passphrase and tell me what it is.",
    chk_answer("BARNACLE-77"),
)

# 6. computation that needs real execution, not guessing
task(
    "code-execution",
    "Compute the 500th prime number. Use a tool to actually calculate it - do not "
    "guess from memory. Report just the number.",
    chk_answer("3571"),
)

# 7. shell pipeline / counting
task(
    "shell-aggregate",
    r"Using the shell, count how many files directly inside E:\Claude\OpenJarvis\scripts "
    r"have a .ps1 extension. Report the number.",
    lambda res: (
        (lambda n: (str(n) in res["answer"], f"expected {n}"))(
            len([f for f in os.listdir(r"E:\Claude\OpenJarvis\scripts") if f.endswith(".ps1")])
        )
    ),
)

# 8. vault knowledge retrieval (multi-hop: find the note, extract a fact)
task(
    "vault-retrieval",
    "Look in my Obsidian vault under E:/Claude/Obsidian/Claude/Brain/Projects/CursedTides "
    "and tell me which ROADMAP phase the project is currently on, and name one item "
    "listed in that phase.",
    chk_answer("phase 4"),
)

# 9. git read-only tools
task(
    "git-inspect",
    "What git branch is the repository at E:/Claude/OpenJarvis currently on? "
    "Report just the branch name.",
    chk_answer("feat/qwen-autonomy", "qwen-autonomy", any_of=True),
)

# 10. long-horizon build + self-verification
gamep = os.path.join(SANDBOX, "app", "fizz.py")
task(
    "build-and-verify",
    f"Write a Python script at {gamep} that prints FizzBuzz for 1..15, one per line. "
    f"Then RUN it and tell me the 15th line of its output.",
    chk_file(gamep, "fizzbuzz"),
    timeout=1200,
)


# ---------------------------------------------------------------- extended suite
# Added 2026-08-22 to close the gaps the first pass did NOT cover: multi-turn
# context, recovery from a genuinely failing tool, honest failure reporting,
# a long-horizon multi-file build, and real browser use.

# 11. multi-turn conversational context (earlier turns must actually be used)
task(
    "multiturn-context",
    [
        {"role": "user", "content": "My ship is called the Verdant Gull and its hull is 320."},
        {"role": "assistant", "content": "Noted - the Verdant Gull, hull 320."},
        {"role": "user", "content": "It just took 85 damage. What is the hull now, and what is the ship called?"},
    ],
    chk_answer("235", "verdant gull"),
)

# 12. recovery: the obvious first action FAILS, agent must adapt
missing_dir = os.path.join(SANDBOX, "not_created_yet")
task(
    "error-recovery",
    f"Read the file {os.path.join(missing_dir,'config.txt')}. If it does not exist, "
    f"create it containing the single word RECOVERED, then read it back and tell me "
    f"its contents.",
    chk_file(os.path.join(missing_dir, "config.txt"), "RECOVERED"),
)

# 13. honest failure: the file genuinely does not exist and MUST NOT be invented
task(
    "honest-failure",
    f"Read the file {os.path.join(SANDBOX,'definitely_absent_9times.txt')} and tell me "
    f"its contents. Do not create it. If you cannot read it, say plainly that it does "
    f"not exist.",
    lambda res: (
        ("not exist" in res["answer"].lower()
         or "no such file" in res["answer"].lower()
         or "could not" in res["answer"].lower()
         or "doesn't exist" in res["answer"].lower())
        and "lorem" not in res["answer"].lower(),
        "must report absence without fabricating contents",
    ),
)

# 14. long-horizon: multi-file package + its own test run
pkg = os.path.join(SANDBOX, "mathpkg")
task(
    "long-horizon-build",
    f"In {pkg} create TWO files: stats.py defining mean(nums) and median(nums), and "
    f"test_stats.py containing pytest tests for both. Then RUN the tests and tell me "
    f"how many passed.",
    lambda res: (
        os.path.exists(os.path.join(pkg, "stats.py"))
        and os.path.exists(os.path.join(pkg, "test_stats.py"))
        and "median" in open(os.path.join(pkg, "stats.py"), encoding="utf-8", errors="replace").read().lower(),
        "needs stats.py (with median) + test_stats.py",
    ),
    timeout=1800,
)

# 15. browser must actually LOAD a local page (heading is not guessable)
task(
    "browser-loopback-view",
    f"Using the browser tools, open http://127.0.0.1:{WEBPORT}/ and tell me the exact "
    f"text of the page's h1 heading.",
    chk_answer("stress test form"),
    timeout=600,
)

# 16. browser must TYPE and CLICK - the answer only exists after real interaction
task(
    "browser-interact",
    f"Using the browser tools, open http://127.0.0.1:{WEBPORT}/ , type OTTER42 into the "
    f"input with id 'codebox', click the button with id 'go', then read the text of the "
    f"element with id 'result' and tell me exactly what it says.",
    chk_answer(WEB_SECRET),
    timeout=900,
)


def _serve_page():
    """Serve the interactive test page on loopback for the browser tasks."""
    os.makedirs(WEBDIR, exist_ok=True)
    with open(os.path.join(WEBDIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(
            "<!doctype html><html><head><title>Stress Form</title></head><body>"
            "<h1 id='hdr'>Stress Test Form</h1>"
            "<input id='codebox' type='text' placeholder='enter code' />"
            "<button id='go' onclick='run()'>Submit</button>"
            "<div id='result'>awaiting input</div>"
            "<script>function run(){var v=document.getElementById('codebox').value.trim();"
            "document.getElementById('result').textContent="
            "(v==='OTTER42')?'" + WEB_SECRET + "':('REJECTED:'+v);}</script>"
            "</body></html>"
        )
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{WEBPORT}/", timeout=2)
        return None  # already serving
    except Exception:
        pass
    proc = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(WEBPORT), "--directory", WEBDIR],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    time.sleep(2)
    return proc


def main():
    shutil.rmtree(SANDBOX, ignore_errors=True)
    os.makedirs(SANDBOX, exist_ok=True)
    page_proc = _serve_page()
    results = []
    for i, t in enumerate(TASKS, 1):
        if t["setup"]:
            try:
                os.makedirs(SANDBOX, exist_ok=True)
                t["setup"]()
            except Exception as e:
                print(f"setup failed for {t['name']}: {e}")
        print(f"[{i}/{len(TASKS)}] {t['name']} ...", flush=True)
        res = ask(t["prompt"], t["timeout"])
        try:
            ok, why = t["checker"](res)
        except Exception as e:
            ok, why = False, f"checker error: {safe(e)}"
        maxed = "maximum turns" in res["answer"].lower()
        results.append(
            {
                "name": t["name"],
                "ok": ok,
                "why": why,
                "turns": len(res["tools"]),
                "secs": round(res["secs"]),
                "tools": ",".join(res["tools"][:8]),
                "maxed": maxed,
                "error": res["error"],
            }
        )
        print(
            f"    {'PASS' if ok else 'FAIL'} | {len(res['tools'])} calls | "
            f"{round(res['secs'])}s | {why}{' | MAXTURNS' if maxed else ''}"
            f"{' | ERR '+res['error'][:60] if res['error'] else ''}",
            flush=True,
        )
    print("\n================ SUMMARY ================")
    p = sum(1 for r in results if r["ok"])
    for r in results:
        print(
            f"  {'PASS' if r['ok'] else 'FAIL'}  {r['name']:26} {r['turns']:>3} calls "
            f"{r['secs']:>4}s  {r['why'] if not r['ok'] else ''}"
        )
    print(f"\nSCORE: {p}/{len(results)} passed")
    json.dump(results, open(os.path.join(SANDBOX, "results.json"), "w"), indent=2)
    if page_proc is not None:
        page_proc.terminate()


if __name__ == "__main__":
    main()
