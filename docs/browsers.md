# Browser checks and known limits

Chrome, Edge and Firefox. Safari: checked with WebKit only.

Evidence: CI run 39683248442, 2026-10-21, Linux, Playwright 1.63.0,
Firefox **155.0** and WebKit **26.6**. The supplied logs and screenshots
cover all six rich-content scripts. These changes await another CI run;
browsers could not run in the builder's sandbox. Real Safari and iOS remain unchecked.

| Failure                                                                     | Browser                                        | Classification and change                                                                                                                                                                                                                                                                                                                                                                                                       |
| --------------------------------------------------------------------------- | ---------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Reasoning click; missing bullet/rule/prose colours                          | Both                                           | Check: reasoning now has metrics and its own Markdown. Use its summary and inspect the main answer outside the fold.                                                                                                                                                                                                                                                                                                            |
| Draft scope count; footnote focus timeout                                   | Both                                           | Check: reasoning was counted as the draft. Select the actual draft and main answer; retain Enter, Tab, return-focus and unchanged-address assertions.                                                                                                                                                                                                                                                                           |
| Dark pictures identical to light; theme-switch timeout                      | Firefox 155.0                                  | Harness: Workbench sends `Cross-Origin-Opener-Policy: same-origin`, and Playwright's Firefox then drops the context's colour scheme (reproduced against a bare server with only that header). A real Firefox follows the person's setting. The six checks state the scheme again after the first page opens (`keepScheme`). The app's reconcile-on-subscribe change stays: it is harmless and tested, but it was not the cause. |
| Formula deadline reports “layout is too large”                              | WebKit 26.6                                    | Check: the macro completes before the deadline. Stall that fixture job inside the real module worker; still require the exact 1.5 s timeout and successful subsequent formulas.                                                                                                                                                                                                                                                 |
| Stylesheet CSP refusals at empty source, line 5                             | WebKit 26.6                                    | Harness: Playwright inserts a no-op `body {}` stylesheet per screenshot. Exempt only a counted matching insertion at that source location; retain app CSP events across navigation and all worker refusals.                                                                                                                                                                                                                     |
| Phone document overflow, without wide answer/chart/diagram elements         | WebKit 26.6                                    | App: contain horizontal extent at the app viewport with `overflow-x: clip`; retain inner scroll boxes and all existing width assertions.                                                                                                                                                                                                                                                                                        |
| Dark diagram section labels below 4.5:1                                     | WebKit 26.6; Firefox once its dark theme works | App: darken only the two offending diagram fills in these engines. Vitest checks contrast and preserves Chromium's exact palette. Status colours elsewhere stay unchanged.                                                                                                                                                                                                                                                      |
| Service worker called “allowed [object Promise]”                            | Firefox 155.0                                  | Check: registration rejects asynchronously (also visible in the screenshot's error note). Observe the real registration promise; resolution fails the check. No sandbox permission changes.                                                                                                                                                                                                                                     |
| Invalid chart reason differs                                                | Both                                           | Check: pie charts are now supported; this fixture has no series. Require that exact reason and its original JSON source.                                                                                                                                                                                                                                                                                                        |
| Busy preview hangs manual and inline runs                                   | Both                                           | Engine limit; an unfinished first run now waits for Run when shown again. Live loop recovery is still skipped; reload recovery is checked without freezing the browser. See below.                                                                                                                                                                                                                                              |
| Send never enables in the live-answer step                                  | Both                                           | Check: New chat opens the chat's own address and replaces the composer; a slower engine filled the first one, which was then empty. Wait for the address, then fill.                                                                                                                                                                                                                                                            |
| Page errors "... /api/... due to access control checks" at the end of a run | WebKit 26.6                                    | Engine report: WebKit gives that text for a request the page abandons at the next navigation, for the same origin too. Only that text for the app's own `/api` is ignored in WebKit (`ignorablePageError`); every other page error still fails.                                                                                                                                                                                 |
| Two comboboxes in the artifact dialog                                       | Firefox 155.0                                  | Check: the dialog has Revision and Compare with revision. Select by the name "Revision".                                                                                                                                                                                                                                                                                                                                        |
| A page that tries to leave shows no "was stopped" note                      | WebKit 26.6                                    | Engine difference, recorded below.                                                                                                                                                                                                                                                                                                                                                                                              |

An infinite loop in a preview can block the chat's event loop in these Firefox
and WebKit builds. The person sees a frozen chat: Stop and the watchdog cannot
run, and other previews cannot recover. Closing the tab through the browser
is necessary. A sandbox controls access, not a separate thread or process;
changing its attributes cannot provide that guarantee. A different execution
architecture would be needed to fix this in Workbench (workbench#19).

In Firefox and Safari (WebKit), Workbench records each preview in `begin()`,
before creating its frame. These engines persist localStorage writes through
another process asynchronously; waiting until the run message is sent lets a
loop freeze the page before the marker is durable. In memory, this page tracks
each attempt as **begun**, then **sent** immediately before posting the run
message. Sending changes no storage. If a sent run never reports finishing
before the page is closed or reloaded, its next showing waits for **Run** and
says: "This preview didn't finish last time and may have frozen the page. Run it?"
Every other artifact
still runs by itself. Run records a fresh attempt before its frame exists; a
matching first-run success or failure clears it. Unmounting or ending a run
while the page still responds also clears it, except after the start timeout
or the person's Stop. This applies to both begun and sent attempts. Replacing
a silent frame keeps the same attempt and its early marker.

One module-level `pagehide` listener clears only this page's begun markers:
a preview whose run was never sent cannot have looped. It leaves sent markers
and markers from previous pages alone, since a browser may dispatch `pagehide`
after terminating a loop. A begun marker retained by Stop is also cleared on
pagehide. Closing or reloading after sending but before completion keeps the
marker; that window is normally milliseconds. The identity includes the chat,
message and content, so a corrected revision can run by itself. Chrome and Edge
do not use these markers.

Only the 200 newest unfinished identities are kept, under
`workbench.previews.unfinished` in localStorage; no artifact source is stored.
Blocked storage keeps the previous behaviour. This is evidence of an unfinished
start, not proof of a loop: closing the page after the run was sent can also
leave a marker. A loop that begins after the first run finished leaves no marker.

Only infinite-loop responsiveness and live recovery after that loop are skipped
in these two engines. Chromium still runs them. The inline browser script checks
reload recovery by intercepting only the looping fixture's run message before
execution. The real preview records its start and gets no completion reply;
after reload only that artifact waits for Run, and Run records its next attempt.
The saved tool-artifact check also holds its bootstrap before ready, then
reloads: pagehide must clear the begun marker so revision 2 runs by itself.
No actual loop executes in that check. Both engines still check ordinary Stop,
sandbox isolation, network and form refusals, navigation, components, and inline
capacity. A deliberately
silent bootstrap separately proves the 3 s ready rule, its waiting message,
replacement and successful startup without hanging the browser.

The screenshot exemption follows [Playwright's screenshot implementation](https://github.com/microsoft/playwright/blob/v1.63.0/packages/playwright-core/src/server/screenshotter.ts).
Its source location, inserted text and count are checked; it is not a general
exception for inline styles. The [Firefox process model](https://firefox-source-docs.mozilla.org/dom/ipc/process_model.html)
also distinguishes sandbox permissions from content-process selection.

WebKit applies the chat page's `frame-src` to the preview frame's own move, and
refuses it before the page can say it is leaving (no `beforeunload`, no
`pagehide`). The frame stays where it was, the address is never asked for, and
no "tried to go to another page" note appears. Only that note is skipped in
WebKit, with a printed reason; the check there is that the frame stayed and
the address was never requested. Chromium and Firefox still check the note.

The phone-size checks passed in WebKit on Linux in CI. WebKit on Windows
measures a 79 px overflow there; it is not the CI engine and is not treated as
a finding.

31i local verification, 2026-10-25, Windows: the two new recovery assertions
passed in WebKit 26.6 with Playwright 1.63.0, using the existing fixture servers.
The full inline script still failed its height and phone-layout checks on this
Windows engine. Firefox 155.0 could launch but failed to create even a blank
page (`Cannot read properties of undefined (reading '_page')`), reproduced
without Workbench. The Linux browser matrix remains to be checked in CI.

31i marker timing and cleanup fix, 2026-10-26, Windows: WebKit 26.6 with
Playwright 1.63.0 passed both recovery assertions and all tool-artifact checks,
including the saved edit running after reload. The full inline script still
failed the same two height checks and phone-layout check described above.
Firefox 155.0 again failed to create its first page with the same `_page`
error, before reaching Workbench. The Linux matrix remains for CI.

31i early-write restoration, 2026-10-26: Linux CI run 40317981422 failed reload
recovery in both engines on d304658; the write in ready was not durable before
the frame could run. The marker is now written in begin, with begun/sent stages
in memory and cleanup of begun markers on pagehide. Locally on Windows,
WebKit 26.6 with Playwright 1.63.0 passed both recovery assertions and all
tool-artifact checks, including a deliberately silent bootstrap followed by
reload: revision 2 ran by itself again. The full inline script failed the
existing short/tall frame-height check (tall 504 px, expected 630 px) and
phone-layout check. Firefox 155.0 failed to create its first page with the
same `_page` error above. Linux CI is the judge of this fix.
