# The prompt to give the Windows session

Written 2026-09-16 11:07 on the Linux machine, after closing E18.4, E18.5 and E18.7 and
pushing the day's 28 commits to `origin/main`. Paste everything inside the fence into the
Windows Claude Code session, once. It schedules its own successor from then on.

Keep this file updated when the brief changes — it is the one place the Windows brief is
written down outside a chat window.

---

```
Kryova continuation — you are the WINDOWS session, and from now on you hold the chain.

The Linux machine stopped scheduling on 2026-09-16 11:07 at the user's instruction. Every
cron job there is deleted. You are the only machine still moving the master plan forward,
and you are also the only machine that can close what is left, because almost all of it
needs CATIA, a GUI, or a test run.

FIVE STANDING RULES. These are the user's, given 2026-09-16 11:07, and they override habit.

1. SCHEDULE THE NEXT TURN YOURSELF, EVERY TURN. One `CronCreate`, `recurring: false`, at
   least 2 h 30 min out. Get the time with
   `(Get-Date).AddMinutes(151).ToString('mm HH dd MM')` and write the cron as
   "<M> <H> <DoM> <Mon> *"; add a minute if the minute lands on 00 or 30, because a
   one-shot job on those minutes can fire up to 90 s early. Run `CronList` first and
   `CronDelete` any earlier continuation job so exactly one exists. A turn that ends
   without scheduling the next one stops the project.
2. DO NOT OPEN A NEW CONVERSATION FOR EACH TURN. The cron job fires into the session that
   created it, and that session's loaded context is the whole point — it already holds
   CLAUDE.md, the plan's shape and what the last turn learned. Starting fresh each time
   throws that away and pays to reload it. Keep ONE session open and let the job wake it.
   Closing the editor is what ends the chain; finishing a turn does not.
3. KEEP CODING UNTIL THE MASTER PLAN IS FINISHED. You are not only a gate. THE QUEUE's
   section E in docs/WINDOWS_VERIFICATION.md is unwritten CODE, not measurement, and it is
   yours. Work the order CLAUDE.md gives: tasks IN PROGRESS, then PARTIAL ones this machine
   can close, then the next phase in plan order.
4. AT LEAST SEVEN TASKS PER TURN. Commit each as it closes, with its master-plan status
   line and `python -m scripts.plan_progress --write` in the SAME commit, so a turn cut off
   by the usage limit loses only the task in flight. `git add` by path, never -A.
5. USE CATIA AND THE GUI ONLY WHERE THE CLAIM NEEDS THEM. A seat prompt is four to seven
   minutes; pytest is not. Reserve the seat and the browser for claims that cannot be
   settled any other way — a COM operation, a real document, an end-to-end path through the
   chatbot. UNLIKE LINUX, YOU MAY AND MUST RUN `pytest`, `ruff` AND `mypy`.

0. Guard. Do no work and edit nothing if a pytest is already running
   (`Get-Process python -ErrorAction SilentlyContinue | Where-Object CommandLine -match
   'pytest'`) or the last commit is younger than 30 minutes (`git log -1 --format=%ct`). In
   that case reschedule this same prompt 2 h 30 min out, change only the "Next continuation
   fires" line in KRYOVA_BUILD_PLAN.md's *Now*, and end the turn. Once the guard passes,
   create a one-shot fallback job at +5 h carrying this same prompt, and CronDelete it at
   the end of the turn.

1. First, and before anything else: `git pull`. The Linux machine pushed 28 commits on
   2026-09-16 including three new mission rungs, and your checkout is behind. Then read, by
   line range and never whole: the progress block (`python -m scripts.plan_progress`), the
   top of KRYOVA_BUILD_PLAN.md's *Now*, and THE QUEUE at the top of
   docs/WINDOWS_VERIFICATION.md. The master plan is ~430 KB and the build plan ~300 KB, so
   never open either whole. If the plan disagrees with this prompt, the plan wins. Do not
   re-read CLAUDE.md: the session loaded it at start.

   Three things already true that are not yours to fix:
   - `python -m app.verify.recorded --check` FAILS. That is expected and it is YOUR last
     step, after the suite is green — not a defect to chase.
   - 143 tests were written on Linux this stretch and have never been executed anywhere:
     tests/test_mission_m4.py (38), tests/test_mission_m5.py (54), tests/test_mission_m7.py
     (51). Their numbers were measured against the real OCCT kernel first, so they should
     pass — but nobody has run them.
   - `scripts/scan_secrets.py` is a blocking CI step and will catch credential-shaped test
     fixtures. Build those from concatenated pieces ("AKIA" + "IOSFODNN7EXAMPLE").

2. Targets, in order, AT LEAST SEVEN. Commit each as it closes.

   (1) RUN THE SUITE, and treat it as target one rather than as setup. `python -m pytest`,
       then `ruff check app/ tests/` and `mypy app/`. Both linters are clean on Linux, so
       anything they print is real. Expect the three mission files above to be the
       interesting part. If a mission test fails, read the number it measured against the
       number in its docstring — every one was transcribed from a real kernel run, so a
       mismatch is a real difference between the machines and is worth writing down rather
       than editing away. Serialise this: never start a second pytest while one is running,
       because the teardown drops the kryova_test schema out from under it.
   (2) RE-RECORD V&V once the suite is green: `python -m app.verify.recorded`, then
       `--check`. It hashes source, so it is the LAST step of the turn's code work, not a
       middle one. This unblocks THE QUEUE A6 and the trust page.
   (3) THE QUEUE E1 — sheet metal on the CATIA side (master plan E17.3 task 3). This is
       unwritten code and it is the biggest single gap left: there is deliberately no
       sheet-metal operation in the CATIA registry because the COM half could not be
       written blind. M5's rung now depends on this being honest — it declares its guard an
       "open-kernel claim" and names THE QUEUE E1 as the reason. Closing it closes E17.3's
       residual and lets M5 drop a caveat.
   (4) THE QUEUE E3 — the conduction analysis through the GUI and against CalculiX. E3 is
       the phase at 92% with one PARTIAL; closing this makes it a complete phase. Its
       stated precondition is already met: A1 and A2 are both ticked (ccx 2.23, the seat,
       2026-09-09), so writing a `*HEAT TRANSFER` deck and pointing `app/solve/oracle.py`
       at a thermal case is unblocked TODAY. That turns the in-house conduction solver
       from verified-against-mathematics into cross-checked-against-another-implementation,
       which is why `CONDUCTION_BACKEND` was given its own setting.
   (5) E7 TASK 7, THEN THE QUEUE E2 — the four gates (master plan Part 2). READ THIS
       BEFORE BOOKING A SEAT: G1 ran on 2026-09-06 and did NOT pass, and the 2026-09-10
       night ladder run reached L3 and failed L4 for the same reason. THE QUEUE E2 names
       the blocker outright — master plan E7 task 7: the agent states a pass/fail verdict
       against the user's stress limit from a single-grid solve whose own record says
       `converged: false`. That is a code fix and most of it is not seat work, so do it
       FIRST; a gate attempted before it re-measures a known failure and spends an hour
       proving something already written down. Then run the gates with
       docs/GUI_PROMPT_LADDER.md as the METHOD, not a script: one prompt per level, a
       screenshot every time a prompt finishes, and do not move to the next level until the
       current one passes properly. G1 alone is a good turn; four gates is not.
   (6) E18.8 — M8, the motorcycle chassis and swingarm. The LAST task in E18, and the
       machinery is now there: `MovingDesign` landed with M7 on 2026-09-16 and M8 is its
       second user. The swingarm is the one part on the ladder that is genuinely a mechanism
       AND a fatigue case, so E8's duty-cycle counting and E9's reactions meet on it. THE
       TRAP, and it is written in app/fatigue/duty.py: NEVER SUM PER-MODE COUNTS OVER A
       DUTY CYCLE — the largest cycle runs from one mode's trough to another's peak and is
       in no mode's count. A brute-force count of the expanded history is the oracle;
       tests/test_fatigue_duty.py is the shape to copy. Read tests/test_mission_m7.py first
       for how a moving rung is declared.
   (7) THE QUEUE E6 — E9 task 5, CATIA DMU Kinematics as a second mechanism backend. E9 is
       at 75% with this its only BLOCKED task, so closing it closes the phase. Note what
       `app/dynamics/engine.py` already decided: `engines()` keeps Chrono behind
       `KinematicEngine` permanently, because where both can answer the kinematic engine is
       exact and Chrono integrates. A DMU backend joins that ordering rather than replacing
       it.

   Substitutes, in order, if one of the above is done or genuinely blocked: THE QUEUE E7
   (E17 tasks 1-2 on the seat: FTA annotations and export from CATIA, which would take E17
   past 83%), THE QUEUE E4 (E15 task 1's `as_catscript` on the seat), THE QUEUE E5 (E15
   task 4, crash recovery against a real seat), then P6 and P7's remaining PARTIALs — P6 is
   6 tasks all PARTIAL and its fps and first-paint targets need a GPU and a browser, which
   you have and Linux did not (THE QUEUE G).

3. What NOT to take: E21 and E23's open items are document fetches, vendor forms and
   questions for counsel — they need a person, not a machine. Do not re-take them.

4. End the turn per CLAUDE.md's "Ending every turn", which now has a Windows block saying
   all of the above. Documents FIRST, then the job:
   - KRYOVA_MASTER_PLAN.md: every status line the turn moved, in the supersede shape — the
     new "> DONE ..." block, a blank line, an UNQUOTED `   <!-- superseded <date> -->`,
     then the old block. Add "> PHASE COMPLETE" if a phase closed. Then
     `python -m scripts.plan_progress --write` and `--check`.
   - KRYOVA_BUILD_PLAN.md: one line per closed task in *Done*, and REWRITE the handoff at
     the top of *Now* so it names the next targets and the fire time.
   - docs/WINDOWS_VERIFICATION.md: tick the boxes you closed, and add a row for anything
     that stopped you.
   - CLAUDE.md: add what the turn learned that would have saved an hour; delete what it
     found untrue.
   - Then write a FRESH prompt of this same shape — never a copy of this one — naming AT
     LEAST SEVEN targets, repeating the five standing rules above verbatim, and stating the
     seven-task rule in its own step 4, so the rule survives a session that reads no
     CLAUDE.md. Schedule it per rule 1 and write the fire time into the *Now* block's "Next
     continuation fires" line.

5. Commit locally by path and PUSH at the end of the turn. The Linux machine is no longer
   pushing, so origin/main is how this work reaches anywhere else. If a push is rejected as
   non-fast-forward, FETCH AND MERGE rather than forcing — on 2026-09-16 the remote had a
   commit ("fix(startup): the server starts local Postgres") that no local branch had, and
   a force push would have destroyed it.

Two lessons this chain keeps re-learning, both earned again on 2026-09-16:

- RUN THE THING. M5's crank pin was drawn as a block and weighed as a cylinder, and the
  disagreement was 0.04% — invisible by eye, caught instantly by building it. M7's motion
  payload published keys that read correctly and resolved to nothing. The restore drill had
  been reviewed for six days and was wrong in four ways; one run found all four.
- CHECK A PREMISE BEFORE BUILDING ON IT. M5's tonnage claim was going to cite a shear
  strength; there is none in the repository, and `shear_strength_mpa` is not even a property
  the material vocabulary can name. Checking first turned a fabricated number into the
  rung's sharpest finding.
```
