import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { api, del, post } from "../lib/api";
import { Folders, FolderSelection } from "./Folders";

it("shows the permission refusal's console link", async () => {
  vi.mocked(api).mockImplementation(async (path) =>
    path === "/api/folders/people"
      ? { people: [{ sub: "ada", name: "Ada" }] }
      : { available: true, grants: [] },
  );
  vi.mocked(post).mockRejectedValue(
    Object.assign(new Error("Workbench's account cannot open D:\\Projects."), {
      detail: { giveAccessUrl: "https://eugene.test/apps/app?path=project" },
    }),
  );
  render(<Folders owner />);
  await screen.findByRole("option", { name: "Ada" });
  fireEvent.change(screen.getByLabelText("Folder name"), { target: { value: "Project" } });
  fireEvent.change(screen.getByLabelText(/Full folder path/), {
    target: { value: "D:\\Projects" },
  });
  fireEvent.change(screen.getByLabelText("Person who can use it"), { target: { value: "ada" } });
  fireEvent.submit(screen.getByRole("form", { name: "Grant folder access" }));
  expect(await screen.findByRole("link", { name: /Give access in Eugene/ })).toHaveAttribute(
    "href",
    "https://eugene.test/apps/app?path=project",
  );
});

vi.mock("../lib/api", () => ({ api: vi.fn(), post: vi.fn(), del: vi.fn() }));

const grant = { id: "notes", name: "Project notes", subject: "ada", writable: false, usable: true };

it("keeps a release failure visible and links to removing access in Eugene", async () => {
  let removed = false;
  vi.mocked(api).mockImplementation(async (path) =>
    path === "/api/folders/people"
      ? { people: [{ sub: "ada", name: "Ada" }] }
      : { available: true, grants: removed ? [] : [grant] },
  );
  vi.mocked(del).mockImplementation(async () => {
    removed = true;
    return {
      warning:
        "Workbench no longer uses the project, but its access stays: connection refused. Remove it in Eugene.",
      giveAccessUrl: "https://eugene.test/apps/app",
    };
  });
  render(<Folders owner />);
  fireEvent.click(await screen.findByRole("button", { name: "Remove grant for Project notes" }));
  fireEvent.click(screen.getByRole("button", { name: "Remove access to Project notes" }));
  expect(await screen.findByText(/its access stays: connection refused/)).toBeTruthy();
  expect(screen.getByRole("link", { name: "Remove access in Eugene" })).toHaveAttribute(
    "href",
    "https://eugene.test/apps/app",
  );
  expect(screen.queryByRole("button", { name: "Remove grant for Project notes" })).toBeNull();
});

it("directs an empty chat folder picker to Workbench's folder grants", async () => {
  vi.mocked(api).mockResolvedValue({ available: true, grants: [] });
  render(<FolderSelection selected={[]} onChange={() => undefined} />);
  expect(await screen.findByText(/Grant an existing folder/)).toHaveTextContent(
    "Toolbox · Tools → Folders · File tools",
  );
  expect(screen.queryByText(/People page/)).toBeNull();
});

it("lets a central host select online node folders and clear offline selections", async () => {
  vi.mocked(api).mockResolvedValue({
    available: true,
    localAvailable: false,
    reason: "Local file accounts unavailable",
    grants: [
      { ...grant, id: "node:online", source: "node", label: "Desktop", available: true },
      {
        ...grant,
        id: "node:offline",
        source: "node",
        label: "Laptop",
        available: false,
        reason: "Offline",
      },
    ],
  });
  const change = vi.fn();
  render(<FolderSelection selected={["node:offline"]} onChange={change} />);
  const online = await screen.findByRole("checkbox", { name: /Desktop.*Project notes/ });
  expect(online).toBeEnabled();
  fireEvent.click(online);
  expect(change).toHaveBeenLastCalledWith(["node:offline", "node:online"]);
  const offline = screen.getByRole("checkbox", { name: /Laptop.*Project notes/ });
  expect(offline).toBeChecked();
  fireEvent.click(offline);
  expect(change).toHaveBeenLastCalledWith([]);
});

beforeEach(() => {
  vi.resetAllMocks();
});

it("says a job site's rules decide what asks, and that Workbench's own folders ask each time", async () => {
  vi.mocked(api).mockResolvedValue({
    available: true,
    reason: null,
    grants: [
      { ...grant, id: "node:notes", source: "node", label: "desk", available: true },
      { ...grant, id: "local", source: "local", name: "Shared" },
    ],
  });
  render(<FolderSelection selected={[]} onChange={() => undefined} />);
  expect(
    await screen.findByText(
      "Workbench's own folders ask before each file operation. On a job site, your rules there " +
        "say which operations run without asking. Read contents go to this chat's model.",
    ),
  ).toBeTruthy();
  expect(screen.queryByText(/Each file operation waits for approval/)).toBeNull();
});

it("says each file operation waits where only Workbench's own folders are offered", async () => {
  vi.mocked(api).mockResolvedValue({ available: true, reason: null, grants: [grant] });
  render(<FolderSelection selected={[]} onChange={() => undefined} />);
  expect(
    await screen.findByText(
      "Each file operation waits for approval. Read contents go to this chat's model.",
    ),
  ).toBeTruthy();
});

it("defaults a new grant to read-only and assigns it to an explicitly chosen person", async () => {
  vi.mocked(api).mockImplementation(async (path) =>
    path === "/api/folders/people"
      ? { people: [{ sub: "ada", name: "Ada", username: "ada" }] }
      : { grants: [], available: true, reason: null },
  );
  render(<Folders owner />);
  await screen.findByRole("option", { name: "Ada (ada)" });
  expect(screen.getByLabelText("Allow creating and editing text files")).not.toBeChecked();
  fireEvent.change(screen.getByLabelText("Folder name"), { target: { value: "Project notes" } });
  fireEvent.change(screen.getByLabelText("Full folder path on Workbench's host"), {
    target: { value: "/srv/notes" },
  });
  fireEvent.change(screen.getByLabelText("Person who can use it"), { target: { value: "ada" } });
  fireEvent.click(screen.getByRole("button", { name: "Grant folder access" }));
  await waitFor(() =>
    expect(post).toHaveBeenCalledWith("/api/folders", {
      name: "Project notes",
      path: "/srv/notes",
      subject: "ada",
      writable: false,
    }),
  );
});

it("shows members their grant without administration controls", async () => {
  vi.mocked(api).mockResolvedValue({ grants: [grant], available: true, reason: null });
  render(<Folders owner={false} />);
  expect(await screen.findByText("Project notes")).toBeInTheDocument();
  expect(screen.getByText("Read only")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Grant|Remove/ })).toBeNull();
  expect(api).not.toHaveBeenCalledWith("/api/folders/people");
});

it("offers only usable grants and allows a removed selection to be cleared", async () => {
  vi.mocked(api).mockResolvedValue({
    grants: [grant, { ...grant, id: "bo", name: "Bo's files", usable: false }],
    available: true,
    reason: null,
  });
  const change = vi.fn();
  render(<FolderSelection selected={["notes", "gone"]} onChange={change} />);
  expect(await screen.findByRole("checkbox", { name: "Project notes · Read only" })).toBeChecked();
  expect(screen.queryByText("Bo's files")).toBeNull();
  fireEvent.click(screen.getByRole("checkbox", { name: /Removed or unavailable folder/ }));
  expect(change).toHaveBeenCalledWith(["notes"]);
});

it("explains missing app-account access and disables adding a folder", async () => {
  vi.mocked(api).mockImplementation(async (path) =>
    path === "/api/folders/people"
      ? { people: [] }
      : { grants: [], available: false, reason: "Use a service install." },
  );
  render(<Folders owner />);
  expect(await screen.findByText("Use a service install.")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Grant folder access" })).toBeDisabled();
});

it("says it is loading folders, then names the folder on removal and copies its path", async () => {
  vi.mocked(api).mockImplementation(async (path) =>
    path === "/api/folders/people"
      ? { people: [] }
      : {
          grants: [{ ...grant, source: "local", path: "D:\\notes" }],
          available: true,
          reason: null,
        },
  );
  render(<Folders owner />);
  expect(screen.getByRole("status")).toHaveTextContent("Loading folders…");
  await screen.findByText("Project notes");
  expect(screen.queryByText("Loading folders…")).toBeNull();
  expect(
    screen.getByRole("button", { name: "Copy the path of Project notes" }),
  ).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Remove grant for Project notes" }));
  expect(screen.getByText(/^Remove access to Project notes\?/)).toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "Remove access to Project notes" }),
  ).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Keep access to Project notes" })).toBeInTheDocument();
});

it("says it is loading folders in a chat's picker", () => {
  vi.mocked(api).mockReturnValue(new Promise(() => undefined));
  render(<FolderSelection selected={[]} onChange={() => undefined} />);
  expect(screen.getByRole("status")).toHaveTextContent("Loading folders…");
});

describe("removing a grant", () => {
  const local = { ...grant, source: "local", path: "D:\\notes" };
  beforeEach(() => {
    vi.mocked(api).mockImplementation(async (path) =>
      path === "/api/folders/people"
        ? { people: [{ sub: "ada", name: "Ada", username: "ada" }] }
        : { grants: [local], available: true, reason: null },
    );
  });

  it("asks first: the safe answer has focus, Escape declines, and focus goes back to Remove grant", async () => {
    render(<Folders owner />);
    fireEvent.click(await screen.findByRole("button", { name: "Remove grant for Project notes" }));
    const group = screen.getByRole("group", { name: "Remove access to Project notes?" });
    const keep = within(group).getByRole("button", { name: "Keep access to Project notes" });
    expect(keep).toHaveFocus();
    fireEvent.keyDown(keep, { key: "Escape" });
    expect(screen.queryByRole("group", { name: "Remove access to Project notes?" })).toBeNull();
    expect(del).not.toHaveBeenCalled();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Remove grant for Project notes" })).toHaveFocus(),
    );
  });

  it("takes access away only when the danger answer is taken", async () => {
    vi.mocked(del).mockResolvedValue(undefined);
    render(<Folders owner />);
    fireEvent.click(await screen.findByRole("button", { name: "Remove grant for Project notes" }));
    fireEvent.click(screen.getByRole("button", { name: "Remove access to Project notes" }));
    await waitFor(() => expect(del).toHaveBeenCalledWith("/api/folders/notes"));
  });

  it("names the person a grant is for, to its owner", async () => {
    render(<Folders owner />);
    const card = (await screen.findByText("Project notes")).closest("article") as HTMLElement;
    expect(within(card).getByText("Ada")).toBeInTheDocument();
    expect(within(card).getByText("Person")).toBeInTheDocument();
  });
});

describe("what a grant allows", () => {
  it("is a badge in words: read only plain, read and write in the warning tint", async () => {
    vi.mocked(api).mockResolvedValue({
      grants: [grant, { ...grant, id: "wr", name: "Drafts", writable: true }],
      available: true,
      reason: null,
    });
    render(<Folders owner={false} />);
    await screen.findByText("Drafts");
    expect(screen.getByText("Read only")).toHaveClass("badge");
    expect(screen.getByText("Read only")).not.toHaveClass("badge--warn");
    expect(screen.getByText("Read and write text")).toHaveClass("badge", "badge--warn");
  });

  it("says in the form that edits are not undone, as a warning beside the choice to allow them", async () => {
    vi.mocked(api).mockImplementation(async (path) =>
      path === "/api/folders/people"
        ? { people: [] }
        : { grants: [], available: true, reason: null },
    );
    render(<Folders owner />);
    const warning = (await screen.findByText("Edits are not automatically undone.")).closest(
      ".notice",
    );
    expect(warning).toHaveClass("notice--warn");
    // The limits stay as help: the one thing that can hurt is the warning.
    expect(screen.getByText(/Lists and UTF-8 text files only/)).toHaveClass("help");
  });
});

describe("a folder page that cannot do something", () => {
  it("says why the owner cannot grant a local folder, and ties the reason to the disabled button", async () => {
    const reason = "Local servers need Workbench's own OS account.";
    vi.mocked(api).mockImplementation(async (path) =>
      path === "/api/folders/people"
        ? { people: [] }
        : { grants: [], available: true, localAvailable: false, reason },
    );
    render(<Folders owner />);
    expect(await screen.findByText(reason)).toBeInTheDocument();
    const button = screen.getByRole("button", { name: "Grant folder access" });
    expect(button).toBeDisabled();
    expect(button).toHaveAccessibleDescription(reason);
  });

  it("does not show a local reason to a member, who cannot grant anyway", async () => {
    vi.mocked(api).mockResolvedValue({
      grants: [grant],
      available: true,
      localAvailable: false,
      reason: "Local servers need Workbench's own OS account.",
    });
    render(<Folders owner={false} />);
    await screen.findByText("Project notes");
    expect(screen.queryByText(/Local servers need/)).toBeNull();
  });

  it("names what could not be read, and what Workbench answered", async () => {
    vi.mocked(api).mockRejectedValue(new Error("Not Found"));
    render(<Folders owner={false} />);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Could not read the folder grants");
    expect(alert).toHaveTextContent("Not Found");
    expect(screen.queryByText("Loading folders…")).toBeNull();
  });

  it("offers to read the grants again, and shows them when the second read works", async () => {
    vi.mocked(api)
      .mockRejectedValueOnce(new Error("Not Found"))
      .mockResolvedValue({ grants: [grant], available: true, reason: null });
    render(<Folders owner={false} />);
    fireEvent.click(
      within(await screen.findByRole("alert")).getByRole("button", { name: "Try again" }),
    );
    expect(await screen.findByText("Project notes")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("says what could not be read in a chat's picker, with the words Workbench used, and offers to read it again", async () => {
    vi.mocked(api)
      .mockRejectedValueOnce(new Error("Workbench answered 500."))
      .mockResolvedValue({ grants: [grant], available: true, reason: null });
    render(<FolderSelection selected={[]} onChange={() => undefined} />);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Could not read the folders");
    expect(alert).toHaveTextContent("Workbench answered 500.");
    fireEvent.click(within(alert).getByRole("button", { name: "Try again" }));
    expect(
      await screen.findByRole("checkbox", { name: "Project notes · Read only" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("names a grant that is unavailable, in a warning with the reason Eugene gave", async () => {
    vi.mocked(api).mockResolvedValue({
      grants: [{ ...grant, available: false, reason: "The drive is not mounted." }],
      available: true,
      reason: null,
    });
    render(<Folders owner={false} />);
    const warning = (await screen.findByText("The drive is not mounted.")).closest(".notice");
    expect(warning).toHaveClass("notice--warn");
    expect(warning).toHaveTextContent("This folder is unavailable now");
  });

  it("shows no empty box where folders are unavailable and Eugene gave no reason, here and in a chat's picker", async () => {
    vi.mocked(api).mockImplementation(async (path) =>
      path === "/api/folders/people"
        ? { people: [] }
        : { grants: [], available: false, reason: null },
    );
    const page = render(<Folders owner={false} />);
    await screen.findByText("No folders granted yet.");
    expect(page.container.querySelector(".notice")).toBeNull();
    page.unmount();
    const picker = render(<FolderSelection selected={[]} onChange={() => undefined} />);
    await screen.findByText(/No folders assigned to you/);
    expect(picker.container.querySelector(".notice")).toBeNull();
  });

  it("says why folders are unavailable in a chat's picker, when Eugene says", async () => {
    vi.mocked(api).mockResolvedValue({
      grants: [],
      available: false,
      reason: "Use a service install.",
    });
    render(<FolderSelection selected={[]} onChange={() => undefined} />);
    expect((await screen.findByText("Use a service install.")).closest(".notice")).not.toBeNull();
  });

  it("says a grant is unavailable even when Eugene gave no reason", async () => {
    vi.mocked(api).mockResolvedValue({
      grants: [{ ...grant, available: false, reason: null }],
      available: true,
      reason: null,
    });
    render(<Folders owner={false} />);
    expect(await screen.findByText("This folder is currently unavailable.")).toBeInTheDocument();
  });
});

describe("choosing folders for a chat", () => {
  it("sets the kind of each folder apart from its name, so a name still reads as one", async () => {
    vi.mocked(api).mockResolvedValue({
      available: true,
      reason: null,
      grants: [
        { ...grant, id: "a", writable: true },
        {
          ...grant,
          id: "node:b",
          name: "Photos",
          source: "node",
          label: "Desktop",
          available: false,
          reason: "Offline",
        },
      ],
    });
    render(<FolderSelection selected={[]} onChange={() => undefined} />);
    expect(
      await screen.findByRole("checkbox", { name: "Project notes · Read and write text" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("checkbox", { name: "Desktop · Photos · Read only · Offline" }),
    ).toBeInTheDocument();
    expect(screen.getAllByText("· Read only")[0]).toHaveClass("text-muted");
  });
});
