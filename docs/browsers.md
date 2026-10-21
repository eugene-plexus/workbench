# Browser checks and known limits

Chrome, Edge and Firefox. Safari: checked with WebKit only.

Evidence: CI run 39683248442, 2026-10-21, Linux, Playwright 1.63.0,
Firefox **155.0** and WebKit **26.6**. The supplied logs and screenshots
cover all six rich-content scripts. These changes await another CI run;
browsers could not run in the builder's sandbox. Real Safari and iOS remain unchecked.

| Failure                                                             | Browser                                        | Classification and change                                                                                                                                                                                                                    |
| ------------------------------------------------------------------- | ---------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Reasoning click; missing bullet/rule/prose colours                  | Both                                           | Check: reasoning now has metrics and its own Markdown. Use its summary and inspect the main answer outside the fold.                                                                                                                         |
| Draft scope count; footnote focus timeout                           | Both                                           | Check: reasoning was counted as the draft. Select the actual draft and main answer; retain Enter, Tab, return-focus and unchanged-address assertions.                                                                                        |
| Dark pictures identical to light; theme-switch timeout              | Firefox 155.0                                  | App: screenshots labelled dark are light. Reconcile the system preference when the first subscriber mounts, covering the gap after module initialization. Vitest reproduces that gap. The browser diagnosis remains an inference pending CI. |
| Formula deadline reports “layout is too large”                      | WebKit 26.6                                    | Check: the macro completes before the deadline. Stall that fixture job inside the real module worker; still require the exact 1.5 s timeout and successful subsequent formulas.                                                              |
| Stylesheet CSP refusals at empty source, line 5                     | WebKit 26.6                                    | Harness: Playwright inserts a no-op `body {}` stylesheet per screenshot. Exempt only a counted matching insertion at that source location; retain app CSP events across navigation and all worker refusals.                                  |
| Phone document overflow, without wide answer/chart/diagram elements | WebKit 26.6                                    | App: contain horizontal extent at the app viewport with `overflow-x: clip`; retain inner scroll boxes and all existing width assertions.                                                                                                     |
| Dark diagram section labels below 4.5:1                             | WebKit 26.6; Firefox once its dark theme works | App: darken only the two offending diagram fills in these engines. Vitest checks contrast and preserves Chromium's exact palette. Status colours elsewhere stay unchanged.                                                                   |
| Service worker called “allowed [object Promise]”                    | Firefox 155.0                                  | Check: registration rejects asynchronously (also visible in the screenshot's error note). Observe the real registration promise; resolution fails the check. No sandbox permission changes.                                                  |
| Invalid chart reason differs                                        | Both                                           | Check: pie charts are now supported; this fixture has no series. Require that exact reason and its original JSON source.                                                                                                                     |
| Busy preview hangs manual and inline runs                           | Both                                           | Engine limit; only loop-specific scenarios are skipped, with a printed reason. See below.                                                                                                                                                    |

An infinite loop in a preview can block the chat's event loop in these Firefox
and WebKit builds. The person sees a frozen chat: Stop and the watchdog cannot
run, and other previews cannot recover. Closing the tab through the browser
is necessary. A sandbox controls access, not a separate thread or process;
changing its attributes cannot provide that guarantee. A different execution
architecture would be needed to fix this in Workbench.

Only infinite-loop responsiveness, recovery after that loop, and remembering
that loop across reload are skipped in these two engines. Chromium still runs
them. Both engines still check ordinary Stop, sandbox isolation, network and
form refusals, navigation, components, and inline capacity. A deliberately
silent bootstrap separately proves the 3 s ready rule, its waiting message,
replacement and successful startup without hanging the browser.

The screenshot exemption follows [Playwright's screenshot implementation](https://github.com/microsoft/playwright/blob/v1.63.0/packages/playwright-core/src/server/screenshotter.ts).
Its source location, inserted text and count are checked; it is not a general
exception for inline styles. The [Firefox process model](https://firefox-source-docs.mozilla.org/dom/ipc/process_model.html)
also distinguishes sandbox permissions from content-process selection.
