"""
Browser checks for the website.

Loads the real built page in a real browser and fails on anything the console
complains about. This is what catches the failures that are invisible from the
outside: a font blocked by the Content-Security-Policy, a JavaScript error that
leaves the page blank, an API call that never returns.

Run through tests/run_ui_check.sh, which starts a copy of the site on localhost
without the password and tears it down afterwards.

Exits non-zero on any console error, page error, or failed request.
"""

import sys

from playwright.sync_api import sync_playwright

SITE = "http://localhost:8099"
SHOTS = "/out"

# Phone first: it is the width that breaks, and the one least often looked at.
VIEWPORTS = [("phone", 390, 844), ("desktop", 1280, 900)]
THEMES = ["light", "dark"]

problems: list[str] = []
checks: list[tuple[bool, str]] = []
# Checks that could not run. Printed loudly: a check that quietly does not run
# looks exactly like a check that passed, which is how real breakage gets missed.
skips: list[str] = []


def check(ok: bool, name: str) -> None:
    checks.append((bool(ok), name))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}", flush=True)


def run():
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])
        for theme in THEMES:
            for label, width, height in VIEWPORTS:
                page_errors: list[str] = []
                console_errors: list[str] = []
                failed_requests: list[str] = []

                context = browser.new_context(
                    viewport={"width": width, "height": height},
                    color_scheme=theme,
                )
                page = context.new_page()
                page.on("console", lambda m: console_errors.append(m.text)
                        if m.type == "error" else None)
                page.on("pageerror", lambda e: page_errors.append(str(e)))
                page.on("requestfailed", lambda r: failed_requests.append(
                    f"{r.url} ({r.failure})"))

                print(f"\n--- {theme} / {label} ({width}x{height})", flush=True)
                page.goto(SITE, wait_until="networkidle", timeout=45000)
                page.wait_for_timeout(1500)   # let the first job poll settle

                name = f"{theme}-{label}"
                page.screenshot(path=f"{SHOTS}/{name}.png", full_page=True)

                check(not page_errors, "no JavaScript errors")
                for e in page_errors:
                    problems.append(f"[{name}] page error: {e}")
                check(not console_errors, "no console errors")
                for e in console_errors:
                    problems.append(f"[{name}] console: {e}")
                check(not failed_requests, "no blocked or failed requests")
                for r in failed_requests:
                    problems.append(f"[{name}] request failed: {r}")

                # The page must actually have rendered something, not just not errored.
                body_text = page.inner_text("body")
                check(len(body_text) > 50, "page rendered visible text")
                check("Lecture Transcription" in body_text, "title is present")

                # A horizontal scrollbar at phone width is the classic layout bug.
                overflow = page.evaluate(
                    "() => document.documentElement.scrollWidth - "
                    "document.documentElement.clientWidth")
                check(overflow <= 0, f"no sideways scrolling (overflow {overflow}px)")

                # Text must not be the same colour as what is behind it.
                contrast_ok = page.evaluate("""() => {
                    const b = getComputedStyle(document.body);
                    return b.color !== b.backgroundColor
                        && b.backgroundColor !== 'rgba(0, 0, 0, 0)';
                }""")
                check(contrast_ok, "body has a real background and a different text colour")

                # The bundled fonts must actually be in use. "No blocked requests"
                # does not prove this: a font that is never referenced is never
                # requested, so it cannot fail - it just silently does nothing.
                # document.fonts.check() returns false unless the font really loaded.
                fonts = page.evaluate("""async () => {
                    await document.fonts.ready;
                    const ui = getComputedStyle(document.body).fontFamily;
                    return {
                        declared: ui,
                        inter: document.fonts.check('1rem "Inter Variable"'),
                        // Force the Malayalam face to load: it is subsetted by
                        // unicode-range, so it only downloads once Malayalam text
                        // is on the page, and an empty page has none.
                        malayalam: await document.fonts
                            .load('1rem "Noto Sans Malayalam"', 'മലയാളം')
                            .then(f => f.length > 0),
                    };
                }""")
                check("Inter" in fonts["declared"],
                      f"interface font is Inter (got {fonts['declared'][:40]})")
                check(fonts["inter"], "Inter actually loaded (not a system fallback)")
                check(fonts["malayalam"], "Noto Sans Malayalam actually loaded")

                # A failed fetch here sets an error message on the page rather
                # than logging to the console, so the checks above would not see it.
                spellings = page.evaluate("""() => {
                    const h = [...document.querySelectorAll('h2')]
                        .find(e => e.textContent.includes('English spellings'));
                    if (!h) return 'section missing';
                    const card = h.closest('.card');
                    const err = card.querySelector('.error');
                    if (err) return 'error: ' + err.textContent;
                    if (card.textContent.includes('Loading')) return 'still loading';
                    return 'ok';
                }""")
                check(spellings == "ok", f"English spellings section loaded ({spellings})")

                context.close()

        check_security_headers(browser)
        check_both_transcripts(browser)
        browser.close()


def check_security_headers(browser):
    """The headers Caddy is supposed to send, asserted rather than assumed.

    These went missing once already without any visible symptom: a header block
    containing a deletion is deferred until the response is written, and deferred
    headers never reach error responses, so every 401 and 404 went out bare while
    the normal pages looked fine.
    """
    print("\n--- security headers", flush=True)
    context = browser.new_context()
    page = context.new_page()
    response = page.goto(SITE, wait_until="domcontentloaded", timeout=45000)
    head = {k.lower(): v for k, v in response.headers.items()}
    csp = head.get("content-security-policy", "")

    check(bool(csp), "a Content-Security-Policy is sent")
    # Fonts are bundled with the site; this is what makes a linked web font fail.
    check("font-src 'self'" in csp, "fonts are restricted to this site")
    check("frame-ancestors 'none'" in csp, "the page cannot be framed")
    check(head.get("x-content-type-options") == "nosniff",
          "browsers may not guess a file's type")
    check(head.get("x-frame-options") == "DENY", "X-Frame-Options is DENY")
    check("strict-transport-security" in head, "HTTPS-only is advertised")
    check("server" not in head,
          f"the web server is not named in headers (got {head.get('server')!r})")

    missing = page.request.get(SITE + "/no-such-file.css")
    missing_head = {k.lower(): v for k, v in missing.headers.items()}
    check(bool(missing_head.get("content-security-policy")),
          "error responses carry the security headers too")

    context.close()


def check_both_transcripts(browser):
    """A job transcribed with both engines must show BOTH transcripts in full.

    This is a regression guard. The local transcript used to be hidden whenever a
    comparison table existed, which is exactly when both transcripts exist - so
    the only mode that produces two transcripts was the only mode where the
    second could not be read or copied. Both Copy buttons must also copy their
    own pane's text, not the first pane's.
    """
    print("\n--- a job transcribed with both engines", flush=True)
    context = browser.new_context(
        viewport={"width": 1280, "height": 900},
        # localhost counts as a secure origin, so the clipboard API is available.
        permissions=["clipboard-read", "clipboard-write"],
    )
    page = context.new_page()
    page.goto(SITE, wait_until="networkidle", timeout=45000)

    jobs = page.evaluate("() => fetch('/api/jobs').then(r => r.json())")
    both = [j for j in jobs
            if j.get("engine") == "both" and j.get("status") == "done"]
    if not both:
        skips.append("no finished 'both' job on this server, so the two-pane "
                     "check could not run")
        print("  SKIP  no finished 'both' job to open", flush=True)
        context.close()
        return

    job = both[0]
    tab = "Training data" if job.get("purpose") == "training" else "Transcripts"
    page.locator(".tab", has_text=tab).first.click()
    page.wait_for_timeout(400)
    page.screenshot(path=f"{SHOTS}/job-list.png", full_page=True)

    page.locator(".job-card", has_text=job["title"][:20]).first.click()

    # The transcript loads after the job detail, so wait for the panes themselves.
    page.wait_for_selector(".transcript-text", timeout=30000)
    page.wait_for_timeout(1500)

    # The opened job sits above the job list but below the submit form, so
    # without scrolling it lands off-screen and looks like nothing happened.
    in_view = page.evaluate("""() => {
        const el = document.querySelector('.stages');
        if (!el) return false;
        const top = el.getBoundingClientRect().top;
        return top >= 0 && top < window.innerHeight;
    }""")
    check(in_view, "opening a job scrolls it into view")

    # Not full_page: a whole lecture transcript makes an unreadably tall image.
    page.screenshot(path=f"{SHOTS}/job-detail.png")

    panes = page.locator(".transcript-text")
    check(panes.count() == 2,
          f"both transcripts are shown in full (found {panes.count()} panes)")
    if panes.count() != 2:
        context.close()
        return

    first, second = panes.nth(0).inner_text(), panes.nth(1).inner_text()
    check(bool(first.strip()) and bool(second.strip()),
          "neither transcript pane is empty")
    check(first != second, "the two panes hold different text")

    copies = page.get_by_role("button", name="Copy text")
    check(copies.count() == 2, f"each pane has a Copy button (found {copies.count()})")

    if copies.count() == 2:
        copies.nth(0).click()
        page.wait_for_timeout(300)
        copied_first = page.evaluate("() => navigator.clipboard.readText()")
        copies.nth(1).click()
        page.wait_for_timeout(300)
        copied_second = page.evaluate("() => navigator.clipboard.readText()")
        check(copied_first.strip() == first.strip(),
              "the first Copy button copies the first transcript")
        check(copied_second.strip() == second.strip(),
              "the second Copy button copies the SECOND transcript")

    check(page.locator(".comparison").count() == 1,
          "the comparison table is still shown alongside both panes")

    context.close()


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        print(f"\nUI CHECK CRASHED: {type(e).__name__}: {e}", flush=True)
        sys.exit(2)

    failed = [c for c in checks if not c[0]]
    print("\n" + "=" * 60)
    print(f"{len(checks) - len(failed)} passed, {len(failed)} failed"
          + (f", {len(skips)} SKIPPED" if skips else ""))
    for s in skips:
        print(f"  SKIPPED: {s}")
    if problems:
        print("\nDETAIL:")
        for p in problems:
            print(f"  {p}")
    print(f"screenshots in {SHOTS}")
    print("=" * 60)
    sys.exit(1 if failed else 0)
