# Nightly tick — the prompt the cron session runs (carried over from densely)

ENGINEERING MACHINE TICK (nightly R&D, authorized by the user "делай инженерную
машину"). Working dir ~/Projects/mengram/experiments/.
1. Read QUEUE.md. Pick the FIRST "[ ]" experiment whose gate (if any) is met.
   Mark it "[~]".
2. Build and run it exactly per its Method, honoring its pre-registered Verify
   criterion. Rules: slope rule — always run S/M/L and report the trend; never
   change the criterion after seeing numbers; a harness that cannot run on all
   three scales is not a result.
3. Write one line to RESULTS.jsonl: {"exp","date","verdict","numbers","notes"}.
   Mark the experiment "[x]" or "[-]" with the reason in QUEUE.md.
4. Never publish, never post, never email. Never touch ~/.mengram, the real
   ~/.claude/settings.json or the production database: experiments use a
   throwaway account or a local folder (MENGRAM_MEMORY_DIR), and MENGRAM_HOME
   points inside experiments/.
5. Stop after one experiment. Leave a two-line summary for the morning.
