"""
Trust Check Activity
Tests the account's reCAPTCHA trust scores.

check_type:
  v2   — reCAPTCHA v2 checkbox test: 'pass' (no challenge) or 'challenge'
  v3   — reCAPTCHA v3 score: float 0.0–1.0 (higher = more trusted)
  both — runs v2 then v3
"""

import re
import asyncio
from playwright.async_api import Page
from activities.base_activity import BaseActivity

RECAPTCHA_V2_URL = "https://www.google.com/recaptcha/api2/demo"

# antcpt.com/score_detector is purpose-built to show the reCAPTCHA v3 score.
# The page auto-fires scoreCheck() after a 2-second countdown — do NOT click
# "Refresh score now!" because that calls location.reload() which destroys the
# page context and causes a "Cannot read properties of null" crash.
RECAPTCHA_V3_URL_PRIMARY = "https://antcpt.com/score_detector/"


class TrustCheckActivity(BaseActivity):

    def __init__(self, page: Page, account: dict, behaviour_cfg: dict):
        super().__init__(page, account, behaviour_cfg)

    async def run(self, check_type: str = "both") -> dict:
        result = {}
        if check_type in ("v2", "both"):
            result["v2"] = await self._check_v2()
        if check_type in ("v3", "both"):
            result["v3"] = await self._check_v3()
        self.log.info(f"Trust check complete: {result}")
        return result

    # ── reCAPTCHA v2 ──────────────────────────────────────────────────────────

    async def _check_v2(self) -> str:
        """
        Navigate to the reCAPTCHA v2 demo, click the checkbox, and return:
          'pass'      — checkbox ticked without any image challenge
          'challenge' — image/audio challenge was shown (account is suspicious)
          'error'     — page failed to load or checkbox not found
        """
        self.log.info("reCAPTCHA v2 check starting")
        try:
            if self.page.is_closed():
                self.log.warning("v2: page is closed before navigation")
                return "error"
            await self.page.goto(RECAPTCHA_V2_URL, wait_until="domcontentloaded", timeout=20000)
            await self.humaniser.pause(2000, 3500)

            # The checkbox lives inside an iframe from Google
            anchor = self.page.frame_locator('iframe[src*="recaptcha"][src*="anchor"]')
            checkbox = anchor.locator('#recaptcha-anchor')

            try:
                await checkbox.wait_for(state="visible", timeout=10000)
            except Exception:
                self.log.warning("v2: reCAPTCHA checkbox iframe not found")
                return "error"

            await self.humaniser.pause(500, 1200)

            # Move the mouse naturally to the iframe before clicking.
            # The checkbox lives inside a cross-origin iframe so we target
            # the iframe element on the main page to get a realistic position.
            try:
                iframe_el = self.page.locator('iframe[src*="recaptcha"][src*="anchor"]').first
                box = await iframe_el.bounding_box()
                if box:
                    # Aim for the checkbox itself (left ~24px, vertically centred)
                    tx = box["x"] + min(24, box["width"] * 0.15)
                    ty = box["y"] + box["height"] * 0.5
                    await self.humaniser.move_to(self.page, tx, ty)
                    await self.humaniser.pause(200, 500)
            except Exception:
                pass

            await checkbox.click()
            self.log.info("v2: checkbox clicked — polling for outcome")

            # Detect the outcome by polling from the MAIN page context.
            # Avoids cross-origin iframe content issues entirely:
            #
            #  - Challenge shown: the bframe iframe becomes visible and tall
            #    (typically 450-560px). We measure it via getBoundingClientRect()
            #    which works from the outer page regardless of cross-origin policy.
            #
            #  - Clean pass: Google populates the hidden g-recaptcha-response
            #    textarea with a token (length > 20 chars) and the bframe stays
            #    hidden (height 0 or very small).
            #
            # We do NOT rely on .recaptcha-checkbox-checked because that class
            # can be set while a challenge is still pending.

            _js_state = """
                () => {
                    // Dump ALL iframes so we can see exactly what's on the page
                    var frames = Array.from(document.querySelectorAll('iframe'));
                    var iframeInfo = frames.map(function(f) {
                        var r = f.getBoundingClientRect();
                        return {
                            src: (f.src || '').substring(0, 120),
                            w: Math.round(r.width),
                            h: Math.round(r.height),
                            visible: r.width > 0 && r.height > 0
                        };
                    });

                    // Is any iframe taller than 100px (challenge popup)?
                    var bigFrame = frames.find(function(f) {
                        return f.getBoundingClientRect().height > 100;
                    });
                    var bigH = bigFrame ? Math.round(bigFrame.getBoundingClientRect().height) : 0;
                    var bigSrc = bigFrame ? (bigFrame.src || '').substring(0, 80) : '';

                    // Has a reCAPTCHA token been issued? (clean pass)
                    var resp = document.querySelector('[name="g-recaptcha-response"]');
                    var tokenLen = resp ? resp.value.length : 0;

                    return {
                        iframes: iframeInfo,
                        bigH: bigH,
                        bigSrc: bigSrc,
                        tokenLen: tokenLen
                    };
                }
            """

            for tick in range(24):   # 24 × 0.5 s = 12 s max
                await asyncio.sleep(0.5)
                try:
                    state = await self.page.evaluate(_js_state)
                except Exception as e:
                    self.log.debug(f"v2 poll error: {e}")
                    continue

                big_h     = state.get("bigH", 0)
                big_src   = state.get("bigSrc", "")
                token_len = state.get("tokenLen", 0)
                iframes   = state.get("iframes", [])

                # Log every poll so we can diagnose from the log file
                self.log.info(
                    f"v2 poll #{tick+1}: bigH={big_h} src={big_src!r} "
                    f"tokenLen={token_len} "
                    f"allFrames={[(f['src'][-40:], f['h']) for f in iframes]}"
                )

                if big_h > 100:
                    self.log.info(f"v2: challenge iframe visible (h={big_h}) → 'challenge'")
                    return "challenge"

                if token_len > 20:
                    self.log.info(f"v2: token issued (len={token_len}), no challenge → 'pass'")
                    return "pass"

            self.log.warning("v2: timed out waiting for result → 'error'")
            return "error"

        except Exception as e:
            self.log.error(f"v2 check error: {e}")
            return "error"

    # ── reCAPTCHA v3 ──────────────────────────────────────────────────────────

    async def _check_v3(self) -> float | None:
        """
        Attempt v3 score check using a fallback chain:

          1. antcpt.com/score_detector/ — purpose-built checker.
             The page auto-fires scoreCheck() after a 2-second countdown when
             grecaptcha is ready. We must NOT click "Refresh score now!" because
             that calls location.reload() which destroys the page and crashes the
             subsequent evaluate() call. Instead we wait for vm.$data.score to
             become a number via polling.

          2. Google Maps network interception — if antcpt fails (DNS or network
             error), navigate to Google Maps and intercept the reCAPTCHA v3 API
             response to extract the score. This works because the proxy can
             always reach google.com.
        """
        self.log.info("reCAPTCHA v3 check starting")

        # ── Attempt 1: antcpt.com ─────────────────────────────────────────────
        score = await self._check_v3_antcpt()
        if score is not None:
            return score

        # ── Attempt 2: Google Maps network interception ───────────────────────
        self.log.info("v3: antcpt failed, falling back to Google Maps interception")
        score = await self._check_v3_maps_intercept()
        if score is not None:
            return score

        self.log.warning("v3: all methods failed, returning None")
        return None

    async def _check_v3_antcpt(self) -> float | None:
        """
        Navigate to antcpt.com/score_detector/ and wait for the Vue app to
        automatically execute the reCAPTCHA v3 check (countdown: 2 seconds).

        KEY: do NOT click "Refresh score now!" — that calls location.reload()
        which destroys the page context and crashes any subsequent evaluate().
        Instead, poll vm.$data.score until it becomes a valid float.
        """
        try:
            if self.page.is_closed():
                self.log.warning("v3/antcpt: page is closed")
                return None

            self.log.info("v3: trying antcpt.com/score_detector/")
            await self.page.goto(
                RECAPTCHA_V3_URL_PRIMARY,
                wait_until="domcontentloaded",
                timeout=20000,
            )

            # Wait for the grecaptcha script to load (it's loaded from google.com)
            # and for Vue to mount. The countdown starts at 2 and ticks every 1s,
            # so scoreCheck fires ~2s after grecaptcha.ready(). Allow up to 30s
            # for the full round-trip (execute → POST verify.php → response).
            score = await self._poll_antcpt_score(timeout_ms=30000)
            if score is not None:
                self.log.info(f"v3 (antcpt): score = {score}")
                return score

            # Fallback: parse the rendered DOM text in case Vue already updated it
            score = await self._extract_score_from_page()
            if score is not None:
                self.log.info(f"v3 (antcpt DOM): score = {score}")
                return score

            self.log.warning("v3/antcpt: could not extract score")
            return None

        except Exception as e:
            self.log.warning(f"v3/antcpt error: {e}")
            return None

    async def _poll_antcpt_score(self, timeout_ms: int = 30000) -> float | None:
        """
        Poll the Vue instance's score data property every 500 ms.

        antcpt uses a global `vm` Vue instance. After the countdown the app
        calls scoreCheck(), which:
          1. Sets vm.$data.score = 0  (detecting...)
          2. POSTs the reCAPTCHA token to verify.php
          3. On success sets vm.$data.score = <float>

        We poll until score is a finite number between 0 and 1.
        If score becomes 'xz' (error from verify.php) we return None.
        """
        deadline = timeout_ms / 1000
        elapsed = 0.0
        interval = 0.5

        while elapsed < deadline:
            try:
                val = await self.page.evaluate("""() => {
                    if (typeof vm === 'undefined' || !vm || !vm.$data) return '__not_ready__';
                    var s = vm.$data.score;
                    if (s === null || s === undefined) return '__null__';
                    return String(s);
                }""")
            except Exception as e:
                # Page may have navigated away (e.g. location.reload triggered)
                self.log.debug(f"v3 poll evaluate error: {e}")
                return None

            if val == '__not_ready__' or val == '__null__':
                # Still loading
                await asyncio.sleep(interval)
                elapsed += interval
                continue

            if val == '0':
                # scoreCheck() started — waiting for AJAX response
                await asyncio.sleep(interval)
                elapsed += interval
                continue

            if val == 'xz':
                # verify.php returned an error (invalid token / rate limit)
                self.log.warning("v3/antcpt: verify.php returned error ('xz')")
                return None

            # Try to parse as float
            try:
                score = float(val)
                if 0.0 <= score <= 1.0:
                    return score
            except (ValueError, TypeError):
                pass

            await asyncio.sleep(interval)
            elapsed += interval

        self.log.debug(f"v3/antcpt: polling timed out after {timeout_ms}ms")
        return None

    async def _extract_score_from_page(self) -> float | None:
        """
        Fallback: try to extract a score float from the page's visible text or HTML.
        Works for any page that renders the score as plain text.
        """
        score_patterns = [
            r'"score"\s*:\s*(\d+\.\d+)',        # JSON:  "score": 0.9
            r'[Ss]core\s*[:\-=]\s*(\d+\.\d+)', # Text:  Score: 0.9
            r'\b(0\.\d+)\b',                    # bare decimal: 0.9
            r'(\d+\.\d+)\s*/\s*1\.0',           # ratio: 0.9 / 1.0
        ]
        try:
            page_text = await self.page.evaluate("() => document.body.innerText")
            self.log.debug(f"v3 page text snippet: {page_text[:300]}")
            for pattern in score_patterns:
                for m in re.finditer(pattern, page_text):
                    score = float(m.group(1))
                    if 0.0 <= score <= 1.0:
                        return score

            page_html = await self.page.content()
            for pattern in score_patterns:
                for m in re.finditer(pattern, page_html):
                    score = float(m.group(1))
                    if 0.0 <= score <= 1.0:
                        return score
        except Exception as e:
            self.log.debug(f"v3 DOM extraction error: {e}")

        return None

    async def _check_v3_maps_intercept(self) -> float | None:
        """
        Fallback v3 score check using Playwright network interception on Google Maps.

        Google Maps loads reCAPTCHA v3 internally. We intercept responses from
        Google's reCAPTCHA API endpoints and look for a score in the JSON body.
        This works even when third-party sites are unreachable via the proxy,
        because google.com is always accessible.

        reCAPTCHA v3 token verification responses come back from:
          https://www.google.com/recaptcha/api2/userverify  (older endpoint)
        or encoded in:
          https://www.google.com/recaptcha/api2/reload       (score embedded)

        The score appears in a JSON-like structure such as:
          ["uvresp", "...", 0.9, ...]
        or plain:
          "score": 0.9
        """
        try:
            if self.page.is_closed():
                self.log.warning("v3/maps: page is closed")
                return None

            score_result: list[float | None] = [None]

            def _parse_score_from_body(body: str) -> float | None:
                patterns = [
                    r'"score"\s*:\s*(\d+\.\d+)',
                    r'\[\s*"uvresp"[^\]]*,\s*(\d+\.\d+)',
                    r'\b(0\.\d+)\b',
                ]
                for pat in patterns:
                    for m in re.finditer(pat, body):
                        try:
                            s = float(m.group(1))
                            if 0.0 <= s <= 1.0:
                                return s
                        except (ValueError, TypeError):
                            pass
                return None

            async def on_response(response):
                if score_result[0] is not None:
                    return
                url = response.url
                if "recaptcha" not in url:
                    return
                try:
                    body = await response.text()
                    s = _parse_score_from_body(body)
                    if s is not None:
                        self.log.debug(f"v3/maps intercept: score {s} from {url}")
                        score_result[0] = s
                except Exception:
                    pass

            self.page.on("response", on_response)

            try:
                await self.page.goto(
                    "https://www.google.co.uk/maps",
                    wait_until="domcontentloaded",
                    timeout=25000,
                )
                # Wait up to 15s for a reCAPTCHA response to come through
                waited = 0.0
                while waited < 15.0 and score_result[0] is None:
                    await asyncio.sleep(0.5)
                    waited += 0.5

            finally:
                self.page.remove_listener("response", on_response)

            if score_result[0] is not None:
                self.log.info(f"v3 (maps intercept): score = {score_result[0]}")
                return score_result[0]

            self.log.warning("v3/maps: no reCAPTCHA score found in network traffic")
            return None

        except Exception as e:
            self.log.warning(f"v3/maps intercept error: {e}")
            return None
