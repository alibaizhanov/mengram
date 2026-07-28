#!/usr/bin/env python3
"""
Procedural memory demo: an agent that stops repeating its own mistakes.

Two runs of the same deploy task:

  Run 1  — no memory of past failures. The agent pushes before running
           migrations, the database breaks. It records the failure.
  Run 2  — the agent recalls the procedure it learned, sees the precondition
           ("run migrations before push"), verifies it first, and ships clean.

This is the part a static CLAUDE.md / instructions file can't do: it doesn't
just store what to do, it learns what NOT to repeat — from evidence.

Run it:
  python deploy_agent.py --offline        # no account, runs in ~10s
  MENGRAM_API_KEY=om-... python deploy_agent.py --cloud   # real Mengram

Free key at https://mengram.io
"""
import argparse
import os
import sys
import time

TASK = "deploy the app to production"


# --- The "world": a deploy that fails if migrations weren't run first ---------
def attempt_deploy(ran_migrations_first: bool) -> tuple[bool, str]:
    if ran_migrations_first:
        return True, "migrations applied, then pushed — deploy healthy ✅"
    return False, "pushed before migrations — schema mismatch, DB crashed ❌"


def p(msg: str, pause: float = 0.4):
    print(msg)
    time.sleep(pause)


# --- Offline memory: a tiny local stand-in so anyone can run without a key ----
class OfflineMemory:
    """Mimics the shape of Mengram's procedural memory, locally, for the demo."""
    def __init__(self):
        self._procedures = []

    def recall(self, task):
        return [pr for pr in self._procedures if task.split()[0] in pr["name"]]

    def learn_failure(self, name, what_went_wrong, precondition):
        for pr in self._procedures:
            if pr["name"] == name:
                pr["fail_count"] += 1
                pr["preconditions"].append(precondition)
                pr["version"] += 1
                return
        self._procedures.append({
            "name": name, "version": 2, "success_count": 0, "fail_count": 1,
            "preconditions": [precondition], "last_failure": what_went_wrong,
        })

    def record_success(self, name):
        for pr in self._procedures:
            if pr["name"] == name:
                pr["success_count"] += 1


# --- Cloud memory: the real Mengram client ------------------------------------
class CloudMemory:
    def __init__(self, user_id="demo"):
        try:
            from mengram import Mengram
        except ImportError:
            sys.exit("Install the client first:  pip install mengram-ai")
        key = os.environ.get("MENGRAM_API_KEY")
        if not key:
            sys.exit("Set MENGRAM_API_KEY (free key at https://mengram.io)")
        self.m = Mengram(api_key=key)
        self.user_id = user_id

    def recall(self, task):
        procs = self.m.procedures(query=task, user_id=self.user_id)
        out = []
        for pr in procs:
            out.append({
                "name": pr.get("name", "?"),
                "version": pr.get("version", 1),
                "success_count": pr.get("success_count", 0),
                "fail_count": pr.get("fail_count", 0),
                "preconditions": (pr.get("metadata") or {}).get("preconditions", []),
            })
        return out

    def learn_failure(self, name, what_went_wrong, precondition):
        # Saving a failure event lets server-side extraction evolve the procedure.
        self.m.add_text(
            f"Tried to {name}: {what_went_wrong}. Lesson: {precondition}.",
            user_id=self.user_id,
        )
        p("   (Mengram is extracting the failure into a procedure — give it a few seconds)", 3.0)

    def record_success(self, name):
        self.m.add_text(f"Successfully completed {name} after verifying preconditions.",
                        user_id=self.user_id)


def run(memory, label):
    p(f"\n{'='*60}\n  {label}\n{'='*60}")
    p(f"→ Agent task: {TASK}")

    known = memory.recall(TASK)
    checks_migrations = False
    if known:
        pr = known[0]
        pre = ", ".join(str(x) for x in pr["preconditions"]) or "(none recorded)"
        p(f"🧠 Recalled procedure '{pr['name']}' v{pr['version']} "
          f"({pr['success_count']} ok / {pr['fail_count']} failed)")
        p(f"   Precondition to verify first: {pre}")
        if pr["preconditions"]:
            p("→ Agent verifies migrations BEFORE pushing.")
            checks_migrations = True
    else:
        p("🧠 No memory of this task. Agent proceeds on instinct.")

    ok, detail = attempt_deploy(ran_migrations_first=checks_migrations)
    p(f"→ Result: {detail}")

    if ok:
        memory.record_success("deploy to production")
    else:
        memory.learn_failure(
            "deploy to production",
            what_went_wrong=detail,
            precondition="run database migrations before pushing",
        )
        p("💾 Failure recorded. The agent will remember this next time.")
    return ok


def main():
    ap = argparse.ArgumentParser(description="Procedural memory demo")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--offline", action="store_true", help="local stand-in, no account (default)")
    g.add_argument("--cloud", action="store_true", help="real Mengram (needs MENGRAM_API_KEY)")
    args = ap.parse_args()

    cloud = args.cloud
    memory = CloudMemory() if cloud else OfflineMemory()
    mode = "CLOUD (real Mengram)" if cloud else "OFFLINE (local stand-in)"
    p(f"\nProcedural memory demo — {mode}")
    p("Same agent, same task, run twice. Watch run 2.\n", 0.6)

    r1 = run(memory, "RUN 1 — first time, no memory")
    r2 = run(memory, "RUN 2 — same task, now with memory")

    p(f"\n{'='*60}")
    if not r1 and r2:
        p("  ✅ The agent failed once, learned, and did NOT repeat the mistake.")
        p("     That's procedural memory. A static instructions file can't do this.")
    else:
        p(f"  Run 1 ok={r1}, Run 2 ok={r2} (try again if the cloud extraction was slow)")
    p(f"{'='*60}\n")
    p("Build this into your own agent:  pip install mengram-ai  ·  https://mengram.io")


if __name__ == "__main__":
    main()
