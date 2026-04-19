# Claude Code Instructions

## Production Warmer

The **live production warmer** runs from a SEPARATE directory:
`C:\Users\Administrator\Desktop\Warmer-Production\`

This directory (`New Warming Script`) is the **development copy** only.

## Rules

- Do NOT run `python tray.py`, `python api/main.py`, or `uvicorn` — this would try to bind port 8000 which the production warmer already owns.
- Do NOT kill processes on port 8000.
- Do NOT modify anything in `logs/state/` — that is live scheduler state.
- You MAY freely edit .py files, config/, data/, static/, api/, core/, activities/.
- To test a specific script in isolation, run it with `--dry-run` where supported.

## Swapping to New Code

When development is ready to go live:
1. Stop production warmer (tray icon → Stop Server)
2. Copy code files (NOT logs/) from this directory into Warmer-Production
3. Restart production warmer (tray icon → Start Server)
