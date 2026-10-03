// Optional real-browser acceptance, driven by tests/test_tools.py.
/* global process, document, innerWidth, console */
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";

const cfg = JSON.parse(readFileSync(process.argv[2], "utf8"));
const require = createRequire(import.meta.url);
const { chromium } = require(cfg.playwright);
const browser = await chromium.launch({ executablePath: cfg.chrome, headless: true });
try {
  const context = await browser.newContext();
  await context.addCookies(cfg.cookies);
  let page = await context.newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto(`${cfg.url}/#signin=${cfg.secret}`);
  await page.getByRole("button", { name: "Tools", exact: true }).click();
  await page.getByLabel("Name", { exact: true }).fill("Browser echo");
  await page.getByLabel("MCP address").fill(cfg.mcp);
  await page.getByRole("button", { name: "Add server" }).click();
  await page.getByRole("heading", { name: "Browser echo" }).waitFor();
  await page.getByRole("button", { name: "Check connection" }).click();
  await page.getByText("Available tools: echo").waitFor();
  await page.getByRole("button", { name: "Back to chat" }).click();
  await page.getByTestId("new-chat").click();
  await page.getByRole("button", { name: "This chat's settings" }).click();
  await page.getByRole("checkbox", { name: "Browser echo" }).check();
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await page.getByText("Saved. The next answer uses these.").waitFor();
  await page.getByRole("button", { name: "Close", exact: true }).click();
  await page.getByTestId("model-picker").selectOption(cfg.model);
  await page.getByTestId("composer").fill("Please use echo.");
  await page.getByTestId("send").click();
  await page.getByRole("button", { name: "Approve call" }).waitFor();
  const chat = page.url();
  await page.reload();
  await page.getByRole("button", { name: "Approve call" }).click();
  await page.close();
  page = await context.newPage();
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto(chat);
  await page.getByText("Hello from the model.", { exact: true }).waitFor();
  await page.getByText("Finished", { exact: true }).waitFor();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "Chats", exact: true }).click();
  await page.getByRole("button", { name: "Please use echo.", exact: true }).click();
  await page.screenshot({ path: cfg.screenshot, fullPage: true });
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth);
  if (overflow) throw new Error("Chat overflows the phone viewport");
  if (errors.length) throw new Error(errors.join("\n"));
  console.log(
    "PASS: add server, discover, select, reload pending approval, approve, close tab, resume, phone layout",
  );
} finally {
  await browser.close();
}
