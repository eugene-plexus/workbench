import { fireEvent, render, screen, waitFor } from "@testing-library/react";

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
  folders: [
    {
      id: "f1",
      name: "Notes",
      path: "C:\\Notes",
      writable: true,
      people: [{ person: "p-bo", name: "bo", writable: true }],
    },
  ],
};

beforeEach(() => {
  vi.resetAllMocks();
});

it("shows a site's folders and who may use them, and saves the list its owner writes", async () => {
  vi.mocked(api).mockResolvedValue({ sites: [site], canInvite: true });
  vi.mocked(post).mockResolvedValue({});
  render(<JobSites onClose={() => undefined} />);
  const bo = await screen.findByLabelText("What bo may do in Notes");
  expect(bo).toHaveValue("change");
  expect(screen.getByRole("option", { name: "May change files without asking you" })).toBeTruthy();
  fireEvent.change(screen.getByLabelText("Add a person (how they sign in)"), {
    target: { value: "ada" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Add" }));
  fireEvent.change(bo, { target: { value: "read" } });
  fireEvent.click(screen.getByRole("button", { name: "Save who may use it" }));
  await waitFor(() =>
    expect(post).toHaveBeenCalledWith(
      "/api/job-sites/s-deskdeskdeskdeskdeskdeskde/folders/f1/people",
      {
        people: [
          { name: "bo", writable: false },
          { name: "ada", writable: false },
        ],
      },
    ),
  );
  expect(screen.getByText(/NT SERVICE/)).toBeInTheDocument();
});

it("offers only reading in a read-only folder", async () => {
  const readOnly = { ...site, folders: [{ ...site.folders[0], writable: false }] };
  vi.mocked(api).mockResolvedValue({ sites: [readOnly], canInvite: true });
  render(<JobSites onClose={() => undefined} />);
  await screen.findByLabelText("What bo may do in Notes");
  expect(screen.queryByRole("option", { name: "May change files without asking you" })).toBeNull();
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
  expect(screen.getByRole("option", { name: "Yes, without asking you" })).toBeTruthy();
  fireEvent.change(tidy, { target: { value: "yes" } });
  fireEvent.click(screen.getByRole("button", { name: "Save who may use Notes tool" }));
  await waitFor(() =>
    expect(post).toHaveBeenCalledWith(
      "/api/job-sites/s-deskdeskdeskdeskdeskdeskde/servers/notes-tool/access",
      {
        people: [
          {
            name: "bo",
            tools: [
              { name: "search", standing: false },
              { name: "tidy", standing: true },
            ],
          },
        ],
      },
    ),
  );
  fireEvent.click(screen.getByLabelText(/while Eugene is in dev mode/));
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
      last
      busy={false}
      readOnly
      onChanged={() => undefined}
    />,
  );
  expect(screen.getByTestId("redacted-message")).toHaveTextContent(
    "The rest of this chat used files on desk. It is private in production mode.",
  );
});
