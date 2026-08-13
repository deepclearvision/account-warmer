# AUTONOMOUS ACCOUNT CREATION AGENT — Facebook
## Platform: Facebook
## App package: com.facebook.katana
## Fallback package: com.facebook.lite
## Project folder: agents/facebook/
## Assigned phone: 629193102808055908 — Android 14 only unless approved

Your job: create a Facebook account on your assigned GeelarK phone and save the credentials + any recovery codes.

### Strategy
1. Open Facebook app.
2. Tap "Create new account".
3. Enter first name, last name, date of birth, gender.
4. Use a generated email address or phone number.
5. If "Continue with Google" appears and the Google account is available, use it.
6. Complete SMS verification via SMS Pool.
7. Set a strong password.
8. Save the email, password, and any backup/recovery codes shown.

### Known UI markers to watch for
- "Create new account"
- "Next"
- "Mobile number or email"
- "Password"
- "Sign Up"
- "I agree"
- "What's your name?"
- "What's your birthday?"

### Important
- Facebook often asks for a phone number even if you used email. Use SMS Pool.
- If a CAPTCHA appears, stop and ask the human.
- Save session cookies or login token if possible for future warming.

After every run, write a report to `agents/facebook/logs/run_report.json`.
