/* global process, console, parent, top, document, localStorage, fetch, Image, window, URL, location, MessageEvent, violations, notices, request, results */
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import assert from "node:assert/strict";
const cfg = JSON.parse(readFileSync(process.argv[2], "utf8"));
const { chromium } = createRequire(import.meta.url)(cfg.playwright);
const browser = await chromium.launch({ executablePath: cfg.chrome, headless: true });
try {
  const context = await browser.newContext({
    colorScheme: "dark",
    viewport: { width: 1280, height: 900 },
  });
  await context.addCookies(cfg.ada.cookies);
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto(`${cfg.url}/chats/${cfg.appChat}#signin=${cfg.ada.secret}`);
  await page.getByRole("button", { name: "Open app", exact: true }).click();
  const outer = page.frameLocator('iframe[title="Isolated MCP app"]');
  const inner = outer.frameLocator('iframe[title="App content"]');
  await inner.getByText("Ready", { exact: true }).waitFor();
  assert.equal(
    await page.locator('iframe[title="Isolated MCP app"]').getAttribute("sandbox"),
    "allow-scripts allow-forms",
  );
  assert.equal(await outer.locator("iframe").getAttribute("sandbox"), "allow-scripts allow-forms");
  const app = page.frames().find((f) => f.url() === "about:srcdoc");
  assert.ok(app);
  const isolation = await app.evaluate(
    async ({ origin, probeUrl }) => {
      const refused = (fn) => {
        try {
          fn();
          return false;
        } catch {
          return true;
        }
      };
      const value = {
        parent: refused(() => parent.document.body),
        top: refused(() => top.document.body),
        cookie: refused(() => document.cookie),
        storage: refused(() => localStorage.getItem("workbench-secret")),
      };
      value.fetch = await fetch(probeUrl).then(
        () => false,
        () => true,
      );
      const image = new Image();
      image.src = `${origin}/api/me`;
      document.body.append(image);
      value.popup = window.open("https://example.test") === null;
      return value;
    },
    { origin: cfg.url, probeUrl: cfg.probeUrl },
  );
  assert.deepEqual(isolation, {
    parent: true,
    top: true,
    cookie: true,
    storage: true,
    fetch: true,
    popup: true,
  });
  await app.waitForFunction(
    () => violations.includes("connect-src") && violations.includes("img-src"),
  );
  assert.deepEqual(await app.evaluate(() => notices.slice(0, 2)), [
    "ui/notifications/tool-input",
    "ui/notifications/tool-result",
  ]);
  // A forged host message from a sibling window must not become an action.
  const channel = await page
    .locator('iframe[title="Isolated MCP app"]')
    .evaluate((f) => new URL(f.src).hash.slice(1));
  await app.evaluate(
    (channel) =>
      top.postMessage(
        {
          channel,
          message: {
            jsonrpc: "2.0",
            id: "wrong-source",
            method: "tools/call",
            params: { name: "increment", arguments: { value: 9 } },
          },
        },
        "*",
      ),
    channel,
  );
  await page.evaluate(() => {
    const target = document.querySelector('iframe[title="Isolated MCP app"]');
    const channel = new URL(target.src).hash.slice(1);
    for (const [origin, key] of [
      [location.origin, channel],
      ["null", "wrong-channel"],
    ]) {
      window.dispatchEvent(
        new MessageEvent("message", {
          source: target.contentWindow,
          origin,
          data: {
            channel: key,
            message: {
              jsonrpc: "2.0",
              id: `forged-${key}`,
              method: "tools/call",
              params: { name: "increment", arguments: { value: 9 } },
            },
          },
        }),
      );
    }
    window.postMessage(
      {
        channel: new URL(target.src).hash.slice(1),
        message: {
          jsonrpc: "2.0",
          id: "forged",
          method: "tools/call",
          params: { name: "increment", arguments: { value: 9 } },
        },
      },
      location.origin,
    );
    target.contentWindow.postMessage(
      {
        channel: "wrong",
        message: {
          jsonrpc: "2.0",
          method: "ui/notifications/sandbox-resource-ready",
          params: { html: "<script>parent.postMessage('bad','*')</script>" },
        },
      },
      "*",
    );
  });
  await inner.getByRole("button", { name: "Increment", exact: true }).click();
  await page.getByRole("button", { name: "Allow once", exact: true }).waitFor();
  assert.equal(
    await page.getByRole("button", { name: "Allow once", exact: true }).count(),
    1,
    "forged messages created proposals",
  );
  assert.equal(await inner.getByText("Ready", { exact: true }).count(), 1);
  await page.getByRole("button", { name: "Allow once", exact: true }).click();
  await inner.getByText("Increment completed", { exact: true }).waitFor();
  await app.evaluate(() =>
    request("tools/call", { name: "increment", arguments: { value: 2 } }, 2),
  );
  await app.evaluate(() => request("ui/open-link", { url: "https://example.test" }, 3));
  await app.waitForFunction(() => results[3]);
  assert.equal(await app.evaluate(() => results[3].error.code), -32601);
  await page.emulateMedia({ colorScheme: "light" });
  await inner.getByText("light", { exact: true }).waitFor();
  await page.screenshot({ path: cfg.screenshot, fullPage: true });
  await app.evaluate(() =>
    window.setTimeout(() => {
      while (true) {
        /* deliberate untrusted loop */
      }
    }, 20),
  );
  await page
    .getByRole("alert")
    .filter({ hasText: "app stopped responding" })
    .waitFor({ timeout: 15000 });
  assert.equal(await page.locator('iframe[title="Isolated MCP app"]').count(), 0);
  await page.getByRole("button", { name: "Stop app", exact: true }).click();
  assert.equal(await page.locator('iframe[title="Isolated MCP app"]').count(), 0);
  await page.reload();
  await page.getByRole("button", { name: "Open app", exact: true }).waitFor();
  assert.equal(await page.locator('iframe[title="Isolated MCP app"]').count(), 0);

  await page.goto(`${cfg.url}/chats/${cfg.formChat}`);
  const name = page.getByRole("textbox", { name: "Name", exact: true });
  await name.fill("Grace");
  await page.getByRole("checkbox", { name: "Confirmed", exact: true }).check();
  await page.getByRole("button", { name: "Save changes", exact: true }).click();
  await page
    .getByRole("button", { name: "Save changes", exact: true })
    .waitFor({ state: "detached" });
  const save = page.getByRole("button", { name: "Save", exact: true });
  await save.focus();
  await page.keyboard.press("Enter");
  await page.getByRole("button", { name: "Allow once", exact: true }).click();
  await page.getByText("a2ui/action · completed", { exact: true }).waitFor();
  await page.reload();
  await name.waitFor();
  assert.equal(await name.inputValue(), "Grace");

  const owners = await browser.newContext();
  await owners.addCookies(cfg.owner.cookies);
  const admin = await owners.newPage();
  await admin.goto(`${cfg.url}/chats/${cfg.appChat}#signin=${cfg.owner.secret}`);
  await admin.getByTestId("read-only").waitFor();
  await admin
    .locator(".interactive-surface")
    .getByText(/Readable fallback: hello/)
    .waitFor();
  assert.equal(await admin.getByRole("button", { name: "Open app", exact: true }).count(), 0);
  assert.equal(await admin.getByRole("button", { name: "Allow once", exact: true }).count(), 0);
  await admin.goto(`${cfg.url}/chats/${cfg.formChat}`);
  await admin.getByLabel("Name", { exact: true }).waitFor();
  assert.equal(await admin.getByLabel("Name", { exact: true }).isDisabled(), true);
  await admin.getByTestId("new-chat").click();
  await admin.getByText("Manage agent connections", { exact: true }).click();
  await admin.getByLabel("Agent name", { exact: true }).fill("Reference agent");
  await admin.getByLabel("Agent URL", { exact: true }).fill(cfg.agentUrl);
  await admin.getByLabel("Bearer token (optional)", { exact: true }).fill("fixture-agent-token");
  await admin.getByRole("checkbox", { name: "Ada", exact: true }).check();
  await admin.getByRole("button", { name: "Add agent connection", exact: true }).click();
  await admin.getByRole("button", { name: "Remove Reference agent", exact: true }).waitFor();
  assert.equal(await admin.getByLabel("Bearer token (optional)", { exact: true }).inputValue(), "");

  await page.goto(`${cfg.url}/chats/${cfg.appChat}`);
  await page.getByRole("button", { name: "Open app", exact: true }).click();
  await inner.getByText("Ready", { exact: true }).waitFor();
  assert.equal(
    await admin.evaluate(
      async (connection) =>
        (
          await fetch(`/api/tools/servers/${connection}`, {
            method: "DELETE",
            headers: { "X-Workbench-Secret": localStorage.getItem("workbench-secret") },
          })
        ).status,
      cfg.connection,
    ),
    204,
  );
  await page.locator('iframe[title="Isolated MCP app"]').waitFor({ state: "detached" });
  await page
    .locator(".interactive-surface")
    .getByText(/Readable fallback: hello/)
    .waitFor();

  async function chat(source) {
    await page.getByTestId("new-chat").click();
    await page
      .getByRole("combobox", { name: "Answer with", exact: true })
      .selectOption({ label: source });
  }
  async function send(text) {
    await page.getByRole("textbox", { name: "Message to agent", exact: true }).fill(text);
    await page.getByRole("button", { name: "Send to agent", exact: true }).click();
  }
  async function done() {
    await page
      .getByRole("status")
      .filter({ hasText: /agent · done$/ })
      .waitFor();
  }
  await chat("Workbench agent (AG-UI)");
  await send("Native hello");
  await done();
  await page.getByTestId("answer").filter({ hasText: "Hello from the model." }).waitFor();
  await chat("Reference agent");
  await send("tools");
  await done();
  await page.reload();
  await done();
  await send("normal");
  await done();
  await chat("Reference agent");
  await send("interrupt");
  await page
    .getByRole("textbox", { name: "Response for approval (JSON)", exact: true })
    .fill("true");
  await page.getByRole("button", { name: "Send responses", exact: true }).click();
  await done();
  await chat("Reference agent");
  await send("hold");
  await page.getByRole("button", { name: "Stop agent", exact: true }).click();
  await page.getByRole("alert").filter({ hasText: "last result is unknown" }).waitFor();
  assert.deepEqual(errors, []);
  console.log(
    "PASS: isolated MCP app, CSP, origins/channel, reviewed effect and replay; A2UI labels, keyboard, saved form; AG-UI GUI configuration, native/remote continuation, interrupt and stop; light/dark and reload.",
  );
} finally {
  await browser.close();
}
