import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { api, post } from "../lib/api";
import type { Message } from "../lib/types";
import { JobSites } from "./JobSites";
import { MessageView } from "./MessageView";

vi.mock("../lib/api", () => ({ api: vi.fn(), post: vi.fn(), del: vi.fn() }));

const site = {
  node: "desk",
  enabled: true,
  online: true,
  ready: true,
  supported: true,
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
  const people = await screen.findByDisplayValue("bo (write)");
  fireEvent.change(people, { target: { value: "ada, bo (write)" } });
  fireEvent.click(screen.getByRole("button", { name: "Save who may use it" }));
  await waitFor(() =>
    expect(post).toHaveBeenCalledWith("/api/job-sites/desk/folders/f1/people", {
      people: [
        { name: "ada", writable: false },
        { name: "bo", writable: true },
      ],
    }),
  );
  expect(screen.getByText(/NT SERVICE/)).toBeInTheDocument();
});

it("makes the join command for a machine of the person's own, and asks for no password", async () => {
  vi.mocked(api).mockResolvedValue({ sites: [], canInvite: true });
  vi.mocked(post).mockResolvedValue({
    expiresAt: "2026-10-05T12:15:00Z",
    nodeName: "laptop",
    commands: { windows: "WINDOWS-COMMAND -JobSite", posix: "POSIX-COMMAND --job-site" },
  });
  render(<JobSites onClose={() => undefined} />);
  fireEvent.change(await screen.findByLabelText("Machine name (optional)"), {
    target: { value: "laptop" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Make the command" }));
  expect(await screen.findByText("WINDOWS-COMMAND -JobSite")).toBeInTheDocument();
  expect(post).toHaveBeenCalledWith("/api/job-sites/invite", { nodeName: "laptop" });
  expect(screen.getByTestId("job-site-invite")).toHaveTextContent(/asks for your Eugene password/);
});

it("says why nobody can add a machine before the owner opens the route", async () => {
  vi.mocked(api).mockResolvedValue({ sites: [], canInvite: false });
  render(<JobSites onClose={() => undefined} />);
  expect(await screen.findByText(/has not opened a route/)).toBeInTheDocument();
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
