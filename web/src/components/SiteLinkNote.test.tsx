import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { api, post } from "../lib/api";
import { SiteLinkNote } from "./SiteLinkNote";
import { Tools } from "./Tools";

vi.mock("../lib/api", () => ({ api: vi.fn(), post: vi.fn(), del: vi.fn() }));
vi.mock("./Folders", () => ({ Folders: () => null }));

const SITE = "s-deskdeskdeskdeskdeskdeskde";

beforeEach(() => {
  vi.resetAllMocks();
});

it("tells a person who is not linked how to link, and what runs until then", () => {
  render(
    <SiteLinkNote
      site={SITE}
      label="desk"
      linking={{ linked: false, account: "DESK\\owner", linkPage: "http://127.0.0.1:8079/link" }}
      onChanged={() => undefined}
    />,
  );
  expect(screen.getByText("Runs as DESK\\owner")).toBeInTheDocument();
  expect(
    screen.getByText(
      "To work as yourself there, link your own account: on desk, open http://127.0.0.1:8079/link and sign in. Until then your calls run as the machine's owner, inside the folders shared with you.",
    ),
  ).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Remove my link" })).toBeNull();
});

it("removes a person's own link and asks the page to reload", async () => {
  vi.mocked(post).mockResolvedValue(null);
  const changed = vi.fn();
  render(
    <SiteLinkNote
      site={SITE}
      label="desk"
      linking={{ linked: true, account: "DESK\\ada" }}
      onChanged={changed}
    />,
  );
  expect(screen.getByText("Runs as DESK\\ada")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Remove my link on desk" }));
  await waitFor(() => expect(changed).toHaveBeenCalled());
  expect(post).toHaveBeenCalledWith(`/api/job-sites/${SITE}/links/remove`, {});
});

it("shows Eugene's own words when removing is refused", async () => {
  vi.mocked(post).mockRejectedValue(new Error("This machine links with the elevated one-liner."));
  render(
    <SiteLinkNote
      site={SITE}
      label="desk"
      linking={{ linked: true, account: "DESK\\ada" }}
      onChanged={() => undefined}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "Remove my link on desk" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("elevated one-liner");
});

it("says nothing when Eugene says nothing of linking", () => {
  const { container } = render(
    <SiteLinkNote site={SITE} label="desk" linking={{}} onChanged={() => undefined} />,
  );
  expect(container).toBeEmptyDOMElement();
});

it("shows the link prompt on a machine's tool in Tools", async () => {
  vi.mocked(api).mockResolvedValue({
    servers: [
      {
        id: `site:${SITE}:notes-tool`,
        name: "desk · Notes tool",
        transport: "site",
        site: SITE,
        label: "desk",
        server: "notes-tool",
        available: true,
        reason: null,
        jobSite: true,
        linked: false,
        account: "DESK\\owner",
        linkPage: "http://127.0.0.1:8079/link",
      },
    ],
    localProcesses: { available: false, reason: null },
  });
  render(<Tools owner={false} onClose={() => undefined} />);
  expect(await screen.findByText("Runs as DESK\\owner")).toBeInTheDocument();
  expect(screen.getByText(/open http:\/\/127.0.0.1:8079\/link and sign in/)).toBeInTheDocument();
});

it("offers the link page to copy", () => {
  render(
    <SiteLinkNote
      site={SITE}
      label="desk"
      linking={{ linked: false, account: "DESK\\owner", linkPage: "http://127.0.0.1:8079/link" }}
      onChanged={() => undefined}
    />,
  );
  expect(screen.getByRole("button", { name: "Copy the link page for desk" })).toBeInTheDocument();
});
