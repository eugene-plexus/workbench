import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { api, post } from "../lib/api";
import type { Message } from "../lib/types";
import { JobSites } from "./JobSites";
import { MessageView } from "./MessageView";

vi.mock("../lib/api", () => ({ api: vi.fn(), post: vi.fn(), del: vi.fn() }));

const site = {
  id: "s-deskdeskdeskdeskdeskdeskde",
  label: "desk",
  online: true,
  ready: true,
  reason: null,
  account: "NT SERVICE\\eugene-plexus-app-node-files",
  lastContactAt: new Date().toISOString(),
  workspaces: [
    {
      id: "f1",
      name: "Notes",
      writable: true,
      rules: { read: "allow", change: "ask" },
      people: [{ person: "p-bo", name: "bo", read: "allow", change: "allow" }],
    },
  ],
};

beforeEach(() => {
  vi.resetAllMocks();
  // Each owner's page reads its workspaces from the machine when it opens.
  vi.mocked(post).mockResolvedValue({ workspaces: [] });
});

const linked = {
  ...site,
  links: [
    { subject: "p-ada", accountName: "DESK\\ada", available: true },
    { subject: "p-bo", accountName: "DESK\\bo", available: false, reason: "bo is not signed in" },
  ],
  linkPage: "http://127.0.0.1:8079/link",
  sharing: true,
};

it("says who runs as whom, and lets the owner remove anyone's link", async () => {
  vi.mocked(api).mockResolvedValue({ sites: [linked], canInvite: true });
  vi.mocked(post).mockResolvedValue(null);
  render(<JobSites onClose={() => undefined} sub="p-ada" />);
  const box = await screen.findByTestId(`linking-${site.id}`);
  expect(box).toHaveTextContent("Your calls here run as DESK\\ada");
  expect(box).toHaveTextContent("bo runs as DESK\\bo · bo is not signed in");
  expect(box).toHaveTextContent(
    "People link their own account at the machine: on desk, open http://127.0.0.1:8079/link and sign in.",
  );
  fireEvent.click(screen.getByRole("button", { name: /Remove link for bo/ }));
  await waitFor(() =>
    expect(post).toHaveBeenCalledWith(`/api/job-sites/${site.id}/links/remove`, {
      person: "p-bo",
    }),
  );
  fireEvent.click(screen.getByRole("button", { name: "Remove my link on desk" }));
  await waitFor(() =>
    expect(post).toHaveBeenCalledWith(`/api/job-sites/${site.id}/links/remove`, {}),
  );
});

it("says when the owner's own account is linked but not signed in there", async () => {
  const away = {
    ...linked,
    links: [{ subject: "p-ada", accountName: "DESK\\ada", available: false, reason: null }],
  };
  vi.mocked(api).mockResolvedValue({ sites: [away], canInvite: true });
  render(<JobSites onClose={() => undefined} sub="p-ada" />);
  expect(await screen.findByTestId(`linking-${site.id}`)).toHaveTextContent(
    "Your calls here run as DESK\\ada · not signed in there now",
  );
});

it("says the owner has not linked yet, and how a Linux machine links", async () => {
  const none = { ...site, links: [], linkPage: null, sharing: true };
  vi.mocked(api).mockResolvedValue({ sites: [none], canInvite: true });
  render(<JobSites onClose={() => undefined} sub="p-ada" />);
  const box = await screen.findByTestId(`linking-${site.id}`);
  expect(box).toHaveTextContent("You have not linked your own account on desk yet.");
  expect(box).toHaveTextContent("On a Linux machine, people link with:");
  expect(box).toHaveTextContent("sudo sh -s -- --site-link --person NAME");
  expect(screen.queryByRole("button", { name: /Remove my link/ })).toBeNull();
});

it("serves only the owner where there is no folder boundary", async () => {
  const mac = { ...site, links: [], linkPage: null, sharing: false };
  vi.mocked(api).mockResolvedValue({ sites: [mac], canInvite: true });
  render(<JobSites onClose={() => undefined} sub="p-ada" />);
  expect(await screen.findByText(/This machine serves only you/)).toBeInTheDocument();
  expect(screen.queryByLabelText("Add a person (how they sign in)")).toBeNull();
  expect(screen.queryByText(/On a Linux machine/)).toBeNull();
});

it("turns a local server on and says who may use which of its tools", async () => {
  const withServer = {
    ...site,
    ownerInDevMode: false,
    servers: [
      {
        server: {
          id: "notes-tool",
          name: "Notes tool",
          kind: "local",
          system: false,
          enabled: true,
          available: true,
          reason: null,
          tools: [
            { name: "search", readOnly: true, destructive: false },
            { name: "tidy", readOnly: false, destructive: true },
          ],
        },
        people: [{ person: "p-bo", name: "bo", tools: [{ name: "search", standing: false }] }],
      },
    ],
  };
  vi.mocked(api).mockResolvedValue({ sites: [withServer], canInvite: true });
  vi.mocked(post).mockResolvedValue({});
  render(<JobSites onClose={() => undefined} />);
  const tidy = await screen.findByLabelText("bo may use tidy");
  expect(screen.getAllByRole("option", { name: "Yes, without asking" }).length).toBeGreaterThan(0);
  // J78: a tool that can change things is asked about unless the owner says not.
  fireEvent.change(tidy, { target: { value: "ask" } });
  fireEvent.click(screen.getByRole("button", { name: "Save who may use Notes tool" }));
  await waitFor(() =>
    expect(post).toHaveBeenCalledWith(
      "/api/job-sites/s-deskdeskdeskdeskdeskdeskde/servers/notes-tool/access",
      {
        people: [
          {
            name: "bo",
            tools: [
              { name: "search", decision: "allow" },
              { name: "tidy", decision: "ask" },
            ],
          },
        ],
      },
    ),
  );
  fireEvent.click(screen.getByLabelText(/while Eugene is in developer mode/));
  await waitFor(() =>
    expect(post).toHaveBeenCalledWith("/api/job-sites/s-deskdeskdeskdeskdeskdeskde/settings", {
      ownerInDevMode: true,
    }),
  );
});

it("shows the machine's own audit log to its owner", async () => {
  vi.mocked(api).mockResolvedValue({ sites: [site], canInvite: true });
  vi.mocked(post).mockResolvedValue({
    entries: [
      {
        at: "2026-10-05T12:00:00Z",
        subject: "p-bo",
        kind: "mcp",
        tool: "read_text",
        decision: "refused",
        reason: "Not on the list.",
      },
    ],
  });
  render(<JobSites onClose={() => undefined} />);
  fireEvent.click(await screen.findByRole("button", { name: "Show what desk was asked" }));
  expect(await screen.findByLabelText("What desk was asked")).toHaveTextContent(
    /p-bo · read_text · refused · Not on the list\./,
  );
  expect(post).toHaveBeenCalledWith("/api/job-sites/s-deskdeskdeskdeskdeskdeskde/audit", {
    limit: 50,
  });
});

it("makes the join command for a machine of the person's own, and asks for no password", async () => {
  vi.mocked(api).mockResolvedValue({ sites: [], canInvite: true });
  vi.mocked(post).mockResolvedValue({
    expiresAt: "2026-10-05T12:15:00Z",
    label: "laptop",
    commands: { windows: "WINDOWS-COMMAND -JobSite", posix: "POSIX-COMMAND --job-site" },
  });
  render(<JobSites onClose={() => undefined} />);
  fireEvent.change(await screen.findByLabelText("Machine name (optional)"), {
    target: { value: "laptop" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Make the command" }));
  expect(await screen.findByText("WINDOWS-COMMAND -JobSite")).toBeInTheDocument();
  expect(post).toHaveBeenCalledWith("/api/job-sites/invite", { label: "laptop" });
  expect(screen.getByTestId("job-site-invite")).toHaveTextContent(/asks for your Eugene password/);
});

it("says why nobody can add a machine before the owner opens the route", async () => {
  vi.mocked(api).mockResolvedValue({ sites: [], canInvite: false });
  render(<JobSites onClose={() => undefined} />);
  expect(await screen.findByText(/does not know an address/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Make the command" })).toBeNull();
});

it("shows the owner only which machine the rest of a chat used", () => {
  const message = {
    id: "m",
    seq: 3,
    role: "assistant",
    content: "",
    reasoning: "",
    attachments: [],
    status: "done",
    error: null,
    sources: [],
    searches: 0,
    search: false,
    model: null,
    finish: null,
    createdAt: 0,
    finishedAt: 0,
    toolRounds: [],
    redacted: { site: "desk" },
  } as unknown as Message;
  render(
    <MessageView
      chatId="c"
      message={message}
      progress={null}
      busy={false}
      readOnly
      onChanged={() => undefined}
    />,
  );
  expect(screen.getByTestId("redacted-message")).toHaveTextContent(
    "The rest of this chat used files on desk. It is private in production mode.",
  );
});

const approvePage = "http://127.0.0.1:8079/link/approve";

it("says no tool runs until the owner adds a key at the machine (J14a)", async () => {
  const unsigned = { ...linked, signing: { state: "unsigned", held: 0, approvePage } };
  vi.mocked(api).mockResolvedValue({ sites: [unsigned], canInvite: true });
  render(<JobSites onClose={() => undefined} sub="p-ada" />);
  expect(await screen.findByTestId(`signing-${site.id}`)).toHaveTextContent(
    `No tool runs on desk until you add your own key there. On desk, open ${approvePage} and make a key.`,
  );
});

it("says an install that cannot take a key yet, without pointing at a page it lacks", async () => {
  const later = { ...site, signing: { state: "unsigned", held: 0, approvePage: null } };
  vi.mocked(api).mockResolvedValue({ sites: [later], canInvite: true });
  render(<JobSites onClose={() => undefined} sub="p-ada" />);
  const words = await screen.findByTestId(`signing-${site.id}`);
  expect(words).toHaveTextContent("cannot take a key yet");
  expect(words).not.toHaveTextContent("open");
});

it("asks for the rules to be approved, and counts what is waiting once signed", async () => {
  const unconfirmed = { ...site, signing: { state: "unconfirmed", held: 0, approvePage } };
  vi.mocked(api).mockResolvedValue({ sites: [unconfirmed], canInvite: true });
  const { unmount } = render(<JobSites onClose={() => undefined} sub="p-ada" />);
  expect(await screen.findByTestId(`signing-${site.id}`)).toHaveTextContent(
    "No tool runs on desk until you approve its rules with your key.",
  );
  unmount();
  const signed = { ...site, signing: { state: "signed", held: 2, approvePage } };
  vi.mocked(api).mockResolvedValue({ sites: [signed], canInvite: true });
  render(<JobSites onClose={() => undefined} sub="p-ada" />);
  expect(await screen.findByTestId(`signing-${site.id}`)).toHaveTextContent(
    `2 changes are waiting: open ${approvePage} there.`,
  );
});

it("shows a held change as waiting at the machine, not as a refusal", async () => {
  vi.mocked(api).mockResolvedValue({ sites: [site], canInvite: true });
  const words = `Waiting for your approval on desk, at ${approvePage}.`;
  vi.mocked(post).mockImplementation(async (path: string) =>
    path.endsWith("/workspaces/list") ? { workspaces: [] } : { held: true, message: words },
  );
  render(<JobSites onClose={() => undefined} />);
  await screen.findByLabelText("bo changes files");
  fireEvent.click(screen.getByRole("button", { name: /^Save sharing/ }));
  expect(await screen.findByTestId("job-site-held")).toHaveTextContent(words);
  expect(screen.queryByRole("alert")).toBeNull();
});

describe("passkeys from here (J14a.3)", () => {
  const linux = {
    ...site,
    linkPage: null,
    signing: { state: "unsigned", held: 0, approvePage: null, passkeys: true },
  };
  const context = { rpId: "workbench.example", person: "p-ada", name: "ada" };
  const passkey = {
    id: "c".repeat(32),
    credentialId: "Y3JlZA",
    alg: -7,
    rpId: "workbench.example",
    label: "Passkey from Workbench, added 2026-10-07",
    addedAt: "2026-10-07T12:00:00+00:00",
  };
  const restore: Array<() => void> = [];

  function webauthn(): { create: ReturnType<typeof vi.fn>; get: ReturnType<typeof vi.fn> } {
    const create = vi.fn().mockResolvedValue({
      rawId: new Uint8Array([1, 2, 3]).buffer,
      response: {
        getPublicKey: () => new Uint8Array([4, 5, 6]).buffer,
        getPublicKeyAlgorithm: () => -7,
      },
    });
    const get = vi.fn().mockResolvedValue({
      rawId: new Uint8Array([9]).buffer,
      response: {
        authenticatorData: new Uint8Array([7]).buffer,
        clientDataJSON: new Uint8Array([8]).buffer,
        signature: new Uint8Array([6]).buffer,
      },
    });
    const secure = Object.getOwnPropertyDescriptor(window, "isSecureContext");
    Object.defineProperty(window, "isSecureContext", { value: true, configurable: true });
    vi.stubGlobal("PublicKeyCredential", function PublicKeyCredential() {});
    Object.defineProperty(navigator, "credentials", {
      value: { create, get },
      configurable: true,
    });
    restore.push(() => {
      if (secure) Object.defineProperty(window, "isSecureContext", secure);
      vi.unstubAllGlobals();
    });
    return { create, get };
  }

  afterEach(() => {
    while (restore.length) restore.pop()?.();
    window.localStorage.clear();
  });

  it("points a Linux system install at a passkey, and a plain address at https", async () => {
    vi.mocked(api).mockResolvedValue({
      sites: [linux],
      canInvite: true,
      passkeys: { ...context, rpId: null },
    });
    render(<JobSites onClose={() => undefined} sub="p-ada" />);
    expect(await screen.findByTestId(`signing-${site.id}`)).toHaveTextContent(
      "Pair a passkey with it below",
    );
    expect(screen.getByTestId(`passkeys-${site.id}`)).toHaveTextContent(
      "open Workbench at its https address",
    );
  });

  it("offers nothing for a machine that does not take passkeys", async () => {
    vi.mocked(api).mockResolvedValue({ sites: [site], canInvite: true, passkeys: context });
    render(<JobSites onClose={() => undefined} sub="p-ada" />);
    await screen.findByTestId(`job-site-${site.id}`);
    expect(screen.queryByTestId(`passkeys-${site.id}`)).toBeNull();
  });

  it("pairs a passkey with the MAC, and never sends the code", async () => {
    const { create } = webauthn();
    vi.mocked(api).mockResolvedValue({ sites: [linux], canInvite: true, passkeys: context });
    vi.mocked(post).mockImplementation(async (path: string) => {
      if (path.endsWith("/held")) return { subject: "p-ada", keys: [], passkeys: [], items: [] };
      if (path.endsWith("/passkeys")) return passkey;
      return {};
    });
    render(<JobSites onClose={() => undefined} sub="p-ada" />);
    fireEvent.click(await screen.findByRole("button", { name: "Add a passkey for desk" }));
    // Where to get the code, in plain words: no page at a Linux system install.
    expect(screen.getByTestId(`passkeys-${site.id}`)).toHaveTextContent(
      "run Eugene's installer on desk again with --site-pair",
    );
    const pairButton = screen.getByRole("button", { name: "Make and pair a passkey for desk" });
    expect(pairButton).toBeDisabled();
    fireEvent.change(screen.getByTestId("passkey-code"), { target: { value: "k7qf3-mzd9t" } });
    fireEvent.click(pairButton);
    await waitFor(
      () =>
        expect(vi.mocked(post).mock.calls.some(([p]) => String(p).endsWith("/passkeys"))).toBe(
          true,
        ),
      { timeout: 10_000 },
    );
    const options = create.mock.calls[0]?.[0].publicKey;
    expect(options.rp.id).toBe("workbench.example");
    expect(options.authenticatorSelection.userVerification).toBe("required");
    const sent = vi.mocked(post).mock.calls.find(([p]) => String(p).endsWith("/passkeys"))?.[1];
    expect(sent).toMatchObject({ credentialId: "AQID", publicKey: "BAUG", alg: -7 });
    expect((sent as { mac: string }).mac).toMatch(/^[A-Za-z0-9_-]{43}$/);
    for (const [, body] of vi.mocked(post).mock.calls) {
      expect(JSON.stringify(body ?? {})).not.toContain("MZD9T");
      expect(JSON.stringify(body ?? {}).toUpperCase()).not.toContain("K7QF3");
    }
    expect(window.localStorage.getItem(`eugene.passkey.${site.id}`)).toBe(passkey.id);
  }, 20_000);

  it("approves a held change with the passkey over the machine's envelope", async () => {
    const { get } = webauthn();
    const envelope = '{"act":"rules.confirm"}';
    const unconfirmed = { ...linux, signing: { ...linux.signing, state: "unconfirmed" } };
    vi.mocked(api).mockResolvedValue({ sites: [unconfirmed], canInvite: true, passkeys: context });
    vi.mocked(post).mockImplementation(async (path: string, body?: unknown) => {
      if (path.endsWith("/held")) {
        const key = (body as { key: string | null } | undefined)?.key;
        return {
          subject: "p-ada",
          keys: [passkey.id],
          passkeys: [passkey],
          items: [
            {
              id: "rules",
              action: "rules.confirm",
              words: ["Notes: you"],
              heldAt: null,
              envelope: key ? envelope : null,
            },
          ],
        };
      }
      return {};
    });
    render(<JobSites onClose={() => undefined} sub="p-ada" />);
    expect(await screen.findByTestId(`signing-${site.id}`)).toHaveTextContent(
      "Approve them here with your passkey.",
    );
    fireEvent.click(screen.getByRole("button", { name: "Review changes waiting on desk" }));
    expect(await screen.findByTestId("held-rules")).toHaveTextContent("Notes: you");
    fireEvent.click(
      screen.getByRole("button", { name: "Approve with your passkey: desk's rules" }),
    );
    await waitFor(() =>
      expect(
        vi.mocked(post).mock.calls.some(([p]) => String(p).endsWith("/held/rules/approve")),
      ).toBe(true),
    );
    const request = get.mock.calls[0]?.[0].publicKey;
    expect(request.userVerification).toBe("required");
    const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(envelope));
    expect(new Uint8Array(request.challenge)).toEqual(new Uint8Array(digest));
    const sent = vi.mocked(post).mock.calls.find(([p]) => String(p).endsWith("/approve"))?.[1];
    expect(sent).toEqual({
      envelope,
      key: passkey.id,
      credentialId: "CQ",
      authenticatorData: "Bw",
      clientDataJSON: "CA",
      signature: "Bg",
    });
  });

  it("says where the code is on a machine with a page", async () => {
    webauthn();
    const withPage = { ...linux, linkPage: "http://127.0.0.1:8079/link" };
    vi.mocked(api).mockResolvedValue({ sites: [withPage], canInvite: true, passkeys: context });
    render(<JobSites onClose={() => undefined} sub="p-ada" />);
    fireEvent.click(await screen.findByRole("button", { name: "Add a passkey for desk" }));
    expect(screen.getByTestId(`passkeys-${site.id}`)).toHaveTextContent(
      "open http://127.0.0.1:8079/link on desk and choose Show a code for a passkey",
    );
  });

  it.each([
    { keys: ["c".repeat(32)], says: "It is your last key for desk" },
    { keys: ["c".repeat(32), "d".repeat(32)], says: "What it approved stays." },
  ])("removes a lost passkey without signing ($says)", async ({ keys, says }) => {
    const { get } = webauthn();
    vi.mocked(api).mockResolvedValue({ sites: [linux], canInvite: true, passkeys: context });
    vi.mocked(post).mockImplementation(async (path: string) => {
      if (path.endsWith("/held")) return { subject: "p-ada", keys, passkeys: [passkey], items: [] };
      return {};
    });
    render(<JobSites onClose={() => undefined} sub="p-ada" />);
    fireEvent.click(await screen.findByRole("button", { name: "Review changes waiting on desk" }));
    const row = await screen.findByTestId(`passkey-${passkey.id}`);
    fireEvent.click(within(row).getByRole("button", { name: /^Remove passkey/ }));
    expect(row).toHaveTextContent(says);
    fireEvent.click(within(row).getByRole("button", { name: /^Remove it: / }));
    await waitFor(() =>
      expect(
        vi
          .mocked(post)
          .mock.calls.some(([p]) => String(p).endsWith(`/passkeys/${passkey.id}/remove`)),
      ).toBe(true),
    );
    expect(get).not.toHaveBeenCalled();
  });
});

const people = { state: "signed", held: 0, approvePage: null, passkeys: true };

it("shows a site you are linked to with only your own: your workspaces and your keys", async () => {
  const linked = {
    id: site.id,
    label: "desk",
    role: "linked",
    online: true,
    ready: true,
    reason: null,
    account: null,
    lastContactAt: new Date().toISOString(),
    servers: [],
    workspaces: [
      {
        id: "a".repeat(32),
        name: "Code",
        writable: true,
        rules: { read: "allow", change: "ask" },
        people: [],
      },
    ],
    links: [
      { subject: "p-jo", accountName: "PC\\jo", available: true, signing: "unconfirmed", held: 1 },
    ],
    signing: { ...people, state: "unconfirmed", held: 1 },
  };
  vi.mocked(api).mockResolvedValue({ sites: [linked], canInvite: true });
  vi.mocked(post).mockImplementation(async (path: string) =>
    path.endsWith("/workspaces/list")
      ? {
          workspaces: [{ ...linked.workspaces[0], path: "D:\\code", deny: [".env"], people: [] }],
        }
      : {},
  );
  render(<JobSites onClose={() => undefined} sub="p-jo" />);
  expect(await screen.findByText("desk · linked")).toBeTruthy();
  expect(screen.getByTestId(`signing-${site.id}`)).toHaveTextContent(
    "Nothing of yours runs on desk until you approve your rules with your key. 1 change is waiting.",
  );
  expect(screen.queryByRole("form", { name: "Add a folder on desk" })).toBeNull();
  expect(screen.queryByText("Take this machine out")).toBeNull();
  // The path and hidden patterns are read live from the machine (J76).
  expect(await screen.findByText("D:\\code")).toBeTruthy();
  const hide = screen.getByLabelText("Paths to hide in Code");
  await waitFor(() => expect(hide).toHaveValue(".env"));
  fireEvent.change(hide, { target: { value: ".env\nsecrets/" } });
  fireEvent.change(screen.getAllByLabelText("Change files")[0]!, { target: { value: "deny" } });
  fireEvent.click(screen.getByRole("button", { name: /^Save rules/ }));
  await waitFor(() =>
    expect(post).toHaveBeenCalledWith(
      `/api/job-sites/${site.id}/workspaces/${"a".repeat(32)}/rules`,
      { rules: { read: "allow", change: "deny", command: "deny" }, deny: [".env", "secrets/"] },
    ),
  );
});

it("shows the rules in effect after a change held for the key, never the change asked for", async () => {
  const workspace = {
    id: "c".repeat(32),
    name: "Notes",
    writable: true,
    rules: { read: "allow", change: "deny" },
    people: [{ person: "p-bo", name: "bo", read: "allow", change: "deny" }],
  };
  const owned = { ...site, workspaces: [workspace], signing: people };
  vi.mocked(api).mockResolvedValue({ sites: [owned], canInvite: true });
  const live = { ...workspace, path: "D:\\notes", deny: [".env"] };
  vi.mocked(post).mockImplementation(async (path: string) =>
    path.endsWith("/workspaces/list")
      ? { workspaces: [live] }
      : { held: true, message: "Waiting for your approval." },
  );
  render(<JobSites onClose={() => undefined} />);
  const box = await screen.findByTestId(`workspace-${"c".repeat(32)}`);
  const hide = within(box).getByLabelText("Paths to hide in Notes");
  await waitFor(() => expect(hide).toHaveValue(".env"));
  // Giving more (J68): change asked about, a hidden path shown again.
  fireEvent.change(within(box).getByLabelText("Change files"), { target: { value: "ask" } });
  fireEvent.change(hide, { target: { value: "" } });
  fireEvent.click(within(box).getByRole("button", { name: /^Save rules/ }));
  expect(await screen.findByTestId("job-site-held")).toHaveTextContent(
    "Waiting for your approval.",
  );
  await waitFor(() => expect(within(box).getByLabelText("Change files")).toHaveValue("deny"));
  expect(hide).toHaveValue(".env");
  // Sharing with someone new waits too: the list stays whom it is shared with now.
  const sharing = within(box).getByTestId(`sharing-${"c".repeat(32)}`);
  fireEvent.change(within(sharing).getByLabelText("Share with (how they sign in)"), {
    target: { value: "cy" },
  });
  fireEvent.click(within(sharing).getByRole("button", { name: /^Add to those sharing/ }));
  expect(within(sharing).getByText("cy")).toBeTruthy();
  fireEvent.click(within(sharing).getByRole("button", { name: /^Save sharing/ }));
  await waitFor(() => expect(within(sharing).queryByText("cy")).toBeNull());
  expect(within(sharing).getByText("bo")).toBeTruthy();
});

it("adds a workspace by its path, with its rules and paths to hide", async () => {
  const owned = { ...site, workspaces: [], signing: people };
  vi.mocked(api).mockResolvedValue({ sites: [owned], canInvite: true });
  vi.mocked(post).mockResolvedValue({ held: true, message: "Waiting for your approval." });
  render(<JobSites onClose={() => undefined} />);
  const form = await screen.findByRole("form", { name: "Add a workspace on desk" });
  fireEvent.change(within(form).getByLabelText("Name"), { target: { value: "Code" } });
  fireEvent.change(within(form).getByLabelText("Path on desk"), { target: { value: "D:\\code" } });
  fireEvent.change(within(form).getByLabelText("Read and search"), { target: { value: "ask" } });
  fireEvent.change(within(form).getByLabelText("Paths to hide, one a line (optional)"), {
    target: { value: "*.pem\n\n*.pem\n.env" },
  });
  fireEvent.click(within(form).getByRole("button", { name: "Add workspace" }));
  await waitFor(() =>
    expect(post).toHaveBeenCalledWith(`/api/job-sites/${site.id}/workspaces`, {
      name: "Code",
      path: "D:\\code",
      writable: true,
      // This machine reports no commands: the choice was not shown, so denied.
      rules: { read: "ask", change: "ask", command: "deny" },
      deny: ["*.pem", ".env"],
    }),
  );
  expect(await screen.findByTestId("job-site-held")).toHaveTextContent(
    "Waiting for your approval.",
  );
});

it("shares an owner's workspace with each person's rules there (J69)", async () => {
  const workspace = {
    id: "b".repeat(32),
    name: "Notes",
    writable: false,
    rules: { read: "allow", change: "deny" },
    people: [{ person: "p-bo", name: "bo", read: "allow", change: "deny" }],
  };
  const owned = { ...site, workspaces: [workspace], signing: people };
  vi.mocked(api).mockResolvedValue({ sites: [owned], canInvite: true });
  vi.mocked(post).mockResolvedValue({ workspaces: [] });
  render(<JobSites onClose={() => undefined} />);
  const sharing = await screen.findByTestId(`sharing-${"b".repeat(32)}`);
  // A read-only workspace takes no change rule.
  expect(within(sharing).getByLabelText("bo changes files")).toBeDisabled();
  fireEvent.change(within(sharing).getByLabelText("Share with (how they sign in)"), {
    target: { value: "cy" },
  });
  fireEvent.click(within(sharing).getByRole("button", { name: /^Add to those sharing/ }));
  fireEvent.change(within(sharing).getByLabelText("bo reads and searches"), {
    target: { value: "ask" },
  });
  fireEvent.click(within(sharing).getByRole("button", { name: /^Save sharing/ }));
  await waitFor(() =>
    expect(post).toHaveBeenCalledWith(
      `/api/job-sites/${site.id}/workspaces/${"b".repeat(32)}/people`,
      {
        people: [
          { name: "bo", read: "ask", change: "deny" },
          { name: "cy", read: "allow", change: "deny" },
        ],
      },
    ),
  );
});

const commands = { allowed: true, consentedAt: "2026-10-08T12:00:00Z" };

it("adds a workspace whose commands ask for a signature, where the machine runs them", async () => {
  const owned = { ...site, workspaces: [], signing: people, commands };
  vi.mocked(api).mockResolvedValue({ sites: [owned], canInvite: true });
  vi.mocked(post).mockResolvedValue({ held: true, message: "Waiting for your approval." });
  render(<JobSites onClose={() => undefined} />);
  const form = await screen.findByRole("form", { name: "Add a workspace on desk" });
  const run = within(form).getByLabelText("Run commands");
  expect(run).toHaveValue("ask");
  // Never "without asking" for a command (J47).
  expect(within(run).queryByRole("option", { name: "Without asking" })).toBeNull();
  fireEvent.change(within(form).getByLabelText("Name"), { target: { value: "Code" } });
  fireEvent.change(within(form).getByLabelText("Path on desk"), { target: { value: "D:\\code" } });
  fireEvent.click(within(form).getByRole("button", { name: "Add workspace" }));
  await waitFor(() =>
    expect(post).toHaveBeenCalledWith(`/api/job-sites/${site.id}/workspaces`, {
      name: "Code",
      path: "D:\\code",
      writable: true,
      rules: { read: "allow", change: "ask", command: "ask" },
      deny: [],
    }),
  );
});

it("shows your open window, closes it, and lets the owner turn commands off", async () => {
  const until = new Date(Date.now() + 30 * 60_000).toISOString();
  const owned = {
    ...site,
    workspaces: [],
    signing: people,
    commands,
    links: [{ subject: "p-ada", accountName: "HOST/ada", available: true, windowUntil: until }],
  };
  vi.mocked(api).mockResolvedValue({ sites: [owned], canInvite: true });
  vi.mocked(post).mockResolvedValue(undefined);
  render(<JobSites onClose={() => undefined} sub="p-ada" />);
  const panel = await screen.findByRole("region", { name: "Signed calls on desk" });
  expect(panel).toHaveTextContent("Your window is open until");
  fireEvent.click(within(panel).getByRole("button", { name: "Close it now: your window on desk" }));
  await waitFor(() => expect(post).toHaveBeenCalledWith(`/api/job-sites/${site.id}/window/close`));
  fireEvent.click(within(panel).getByRole("button", { name: "Turn commands off on desk" }));
  expect(panel).toHaveTextContent("Only an administrator at desk can turn them back on.");
  fireEvent.click(within(panel).getByRole("button", { name: "Turn them off on desk" }));
  await waitFor(() =>
    expect(post).toHaveBeenCalledWith(`/api/job-sites/${site.id}/commands/withdraw`),
  );
});

it("says how an administrator allows commands where they are not allowed", async () => {
  const reason = "An administrator has not allowed commands on desk.";
  const owned = {
    ...site,
    workspaces: [],
    signing: people,
    commands: { allowed: false, reason },
  };
  vi.mocked(api).mockResolvedValue({ sites: [owned], canInvite: true });
  render(<JobSites onClose={() => undefined} sub="p-ada" />);
  const panel = await screen.findByRole("region", { name: "Signed calls on desk" });
  expect(panel).toHaveTextContent(reason);
  expect(within(panel).queryByRole("button", { name: "Turn commands off on desk" })).toBeNull();
});

it("says it is loading, and Back to chat", () => {
  vi.mocked(api).mockReturnValue(new Promise(() => undefined));
  const onClose = vi.fn();
  render(<JobSites onClose={onClose} />);
  expect(screen.getByRole("status")).toHaveTextContent("Loading job sites…");
  fireEvent.click(screen.getByRole("button", { name: "Back to chat" }));
  expect(onClose).toHaveBeenCalled();
});

it("copies the Linux link command and names each join command", async () => {
  const none = { ...site, links: [], linkPage: null, sharing: true };
  vi.mocked(api).mockResolvedValue({ sites: [none], canInvite: true });
  render(<JobSites onClose={() => undefined} sub="p-ada" />);
  expect(
    await screen.findByRole("button", { name: "Copy the Linux link command" }),
  ).toBeInTheDocument();
});

it("names the machine on each of its buttons", async () => {
  vi.mocked(api).mockResolvedValue({
    sites: [{ ...site, links: [], sharing: true }],
    canInvite: true,
  });
  render(<JobSites onClose={() => undefined} sub="p-ada" />);
  fireEvent.click(await screen.findByRole("button", { name: "Take this machine out: desk" }));
  expect(screen.getByRole("button", { name: "Take it out: desk" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Keep it: desk" })).toBeInTheDocument();
});

it("says how many audit entries it shows and its cap, then offers Refresh", async () => {
  vi.mocked(api).mockResolvedValue({ sites: [site], canInvite: true });
  vi.mocked(post).mockResolvedValue({
    entries: [
      { at: "2026-10-05T12:00:00Z", subject: "p-bo", kind: "mcp", tool: "a", decision: "allowed" },
      { at: "2026-10-05T12:01:00Z", subject: "p-bo", kind: "mcp", tool: "b", decision: "allowed" },
    ],
  });
  render(<JobSites onClose={() => undefined} />);
  fireEvent.click(await screen.findByRole("button", { name: "Show what desk was asked" }));
  expect(await screen.findByText(/Showing the latest 2 entries/)).toHaveTextContent(
    "This list shows at most the latest 50.",
  );
  fireEvent.click(screen.getByRole("button", { name: "Refresh what desk was asked" }));
  await waitFor(() =>
    expect(
      vi.mocked(post).mock.calls.filter(([path]) => String(path).endsWith("/audit")),
    ).toHaveLength(2),
  );
});

it("names the workspace on its buttons, copies its path and alerts on a bad pattern", async () => {
  const live = { id: "f1", path: "D:\\notes", deny: [], rules: { read: "allow", change: "ask" } };
  vi.mocked(api).mockResolvedValue({ sites: [site], canInvite: true });
  vi.mocked(post).mockImplementation(async (path: string) =>
    path.endsWith("/workspaces/list") ? { workspaces: [live] } : {},
  );
  render(<JobSites onClose={() => undefined} />);
  const box = await screen.findByTestId("workspace-f1");
  expect(
    await within(box).findByRole("button", { name: "Copy the path of Notes" }),
  ).toBeInTheDocument();
  expect(within(box).getByRole("button", { name: "Save rules for Notes" })).toBeInTheDocument();
  expect(within(box).getByRole("button", { name: "Remove workspace Notes" })).toBeInTheDocument();
  fireEvent.change(within(box).getByLabelText("Paths to hide in Notes"), {
    target: { value: "!keep" },
  });
  expect(within(box).getByRole("alert")).toHaveTextContent("!keep cannot be used");
});

it("says sharing edits are not saved yet, and the button says Save sharing", async () => {
  vi.mocked(api).mockResolvedValue({ sites: [site], canInvite: true });
  render(<JobSites onClose={() => undefined} />);
  const sharing = await screen.findByTestId("sharing-f1");
  expect(within(sharing).queryByText("Not saved yet.")).toBeNull();
  fireEvent.change(within(sharing).getByLabelText("Share with (how they sign in)"), {
    target: { value: "cy" },
  });
  fireEvent.click(within(sharing).getByRole("button", { name: "Add to those sharing Notes" }));
  expect(within(sharing).getByRole("status")).toHaveTextContent("Not saved yet.");
  expect(within(sharing).getByRole("button", { name: "Save sharing for Notes" })).toHaveTextContent(
    "Save sharing",
  );
});
