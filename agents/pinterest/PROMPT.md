# AUTONOMOUS ACCOUNT CREATION AGENT — Pinterest
## Platform: Pinterest
## App package: com.pinterest
## Project folder: agents/pinterest/
## Assigned phone: 629191237835948360 — Android 14 only unless approved

Your job: create a Pinterest account on your assigned GeelarK phone and save credentials + recovery codes.

### Strategy
1. Open Pinterest app.
2. Tap "Sign up".
3. If "Continue with Google" appears, use it — this is the preferred path.
4. Otherwise use email and set a password.
5. Pinterest may ask for age and interests. Enter plausible values.
6. Skip "personalize your home feed" if possible.
7. Save email, password, and any account recovery options shown.

### Known UI markers to watch for
- "Sign up"
- "Continue with Google"
- "Continue with email"
- "Email"
- "Password"
- "Age"
- "Next"
- "Skip"

### Important
- Pinterest is usually the easiest of the six. Try Google login first.
- If it asks for phone verification, use SMS Pool.
- Record the generated username (often derived from email).

After every run, write a report to `agents/pinterest/logs/run_report.json`.

---

## Account Health Check — `python create_pinterest.py --check`

Runs a standalone login health check that opens Pinterest and verifies the account state.

### Statuses returned

| Status | Meaning |
|--------|---------|
| `healthy` | Logged in, home feed with pins visible |
| `healthy_empty_feed` | Logged in, home feed visible but no pins (new account / slow load) |
| `healthy_no_pins` | Logged in, home feed visible but pins not yet loaded |
| `logged_out` | Not logged in — sign-up or login gate visible |
| `possible_flag` | Inside Pinterest but no home feed — account may be flagged/disabled/banned |
| `not_in_app` | Not inside Pinterest at all — app crashed or was redirected |

### Definitive logged-in markers (must have at least 2)

| Marker | Resource ID / Text | Reliability |
|--------|-------------------|-------------|
| `home_feed_container` | `com.pinterest:id/home_feed_container` | 100% — only exists when authenticated |
| `bottom_nav_bar` | `com.pinterest:id/bottom_nav_bar` with tabs: Home, Search, Create, Notifications, Saved | 100% — only when authenticated |
| `lego_pin_grid_cell_id` | `com.pinterest:id/lego_pin_grid_cell_id` with content-desc containing "Pin from..." | 100% — pin grid only loads with active account |
| `profile_user_avatar` + `content-desc="Avatar: …"` | Profile tab | 100% — personalised only when authenticated |

### Logged-out / flagged markers

| What you'd see | When |
|----------------|------|
| "Sign up" or "Log in" text nodes | Account not logged in |
| "Continue with Google" / "Continue with email" buttons | Sign-up wall |
| No `home_feed_container` anywhere | Session expired or app not authenticated |
| Pinterest app opens to a non-home screen with no nav | Possible shadowban / account disabled |

### Known pitfalls

1. **Notification permission dialog** — After app update/reinstall, Android shows "Allow Pinterest to send you notifications?" with `com.android.permissioncontroller` package. The `check_login()` function auto-dismisses this by tapping ALLOW, then relaunches.

2. **XML truncation** — uiautomator can truncate output for complex screens (profile page, long home feeds). If `home_feed_container` + pins are visible but `bottom_nav_bar` is missing, it's likely truncation — not a logged-out state. Check if the XML ends with `</hierarchy>` to confirm.

3. **Profile @username not extractable** — Pinterest uses a collapsing toolbar on Android 14. The header (display name + @handle) collapses under `user_profile_app_bar_layout` when scrolled and is invisible to uiautomator. Workaround: capture the username immediately after first signup before any scroll occurs.

4. **Cold phone start** — If the phone was stopped, allow extra time (up to 3 minutes) for it to boot before launching the app.

### Current account state

- **Account**: `pinterest_jacobbareethe`
- **Email**: `jacobbareethe@gmail.com` (Google sign-in)
- **Display name**: Jacob Bareethe
- **Created**: 2026-08-07
- **Last verified healthy**: 2026-08-09
- **Signup method**: Google sign-in — no SMS, no CAPTCHA, no manual password
- **Verification needed**: Birthday (date picker), Gender (Female), 5 interests (selected from grid)
