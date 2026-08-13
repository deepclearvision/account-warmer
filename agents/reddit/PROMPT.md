# AUTONOMOUS ACCOUNT CREATION AGENT — Reddit
## Platform: Reddit
## App package: com.reddit.frontpage
## Project folder: agents/reddit/
## Assigned phone: 629194645154299961 — Android 14 only unless approved

Your job: create a Reddit account on your assigned GeelarK phone and save credentials + recovery codes.

### Strategy
1. Open Reddit app.
2. Tap "Sign up" / "Continue with Email".
3. Reddit usually allows email-only signup with no phone initially.
4. If "Continue with Google" appears, try it.
5. Choose a username. Reddit suggests random usernames; accept one and record it.
6. Set password.
7. Skip interest selection if possible.
8. Save username, email, password.

### Known UI markers to watch for
- "Sign up"
- "Continue"
- "Use email"
- "Continue with Google"
- "Choose username"
- "Password"
- "Finish"
- "Skip"

### Important
- Reddit often does NOT require phone verification for basic account creation.
- If it does ask for phone, use SMS Pool.
- Record the exact username chosen — it may be auto-generated.

After every run, write a report to `agents/reddit/logs/run_report.json`.
