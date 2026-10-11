import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { api, del, post } from "../lib/api";
import { Tools, ToolSelection } from "./Tools";

vi.mock("../lib/api", () => ({ api: vi.fn(), post: vi.fn(), del: vi.fn() }));
vi.mock("./Folders", () => ({ Folders: () => null }));

const server = {
  id: "one",
  name: "Shared search",
  transport: "http",
  url: "https://example.org/mcp",
  hasToken: true,
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api).mockResolvedValue({ servers: [server] });
});

it("members see stored status and a connection check, without credential controls", async () => {
  render(<Tools owner={false} onClose={() => undefined} />);
  expect(await screen.findByText("Shared search")).toBeInTheDocument();
  expect(screen.getByText("A credential is stored.")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Add server" })).toBeNull();
  expect(screen.queryByRole("button", { name: /^Remove/ })).toBeNull();
  vi.mocked(post).mockResolvedValue({ tools: [{ name: "search" }] });
  fireEvent.click(screen.getByRole("button", { name: "Check connection to Shared search" }));
  expect(await screen.findByText("Available tools: search")).toBeInTheDocument();
});

it("reflects saved selection and lets a removed connection be cleared", async () => {
  const change = vi.fn();
  render(<ToolSelection selected={["one", "removed"]} onChange={change} />);
  expect(await screen.findByRole("checkbox", { name: "Shared search" })).toBeChecked();
  fireEvent.click(screen.getByRole("checkbox", { name: /Removed or unavailable server/ }));
  expect(change).toHaveBeenCalledWith(["one"]);
});

it("explains the missing account and offers no local form", async () => {
  vi.mocked(api).mockResolvedValue({
    servers: [],
    localProcesses: { available: false, reason: "This install has no app account." },
  });
  render(<Tools owner onClose={() => undefined} />);
  expect(await screen.findByText("This install has no app account.")).toBeInTheDocument();
  expect(screen.queryByLabelText("Full executable path")).toBeNull();
});

it("shows no empty box where local servers are unavailable and Eugene gave no reason", async () => {
  vi.mocked(api).mockResolvedValue({
    servers: [],
    localProcesses: { available: false, reason: null },
  });
  const { container } = render(<Tools owner onClose={() => undefined} />);
  expect(await screen.findByText("No tool servers yet.")).toBeInTheDocument();
  expect(container.querySelector(".notice")).toBeNull();
  expect(screen.queryByLabelText("Full executable path")).toBeNull();
});

it("saves literal argument arrays, clears secrets, and starts only on check", async () => {
  vi.mocked(api).mockResolvedValue({
    servers: [],
    localProcesses: { available: true, reason: null },
  });
  const local = {
    id: "local",
    transport: "stdio",
    name: "Local echo",
    command: "C:\\Tools\\python.exe",
    args: ["server.py", "literal & spaces"],
    environmentKeys: ["API_TOKEN"],
    access: "owner",
  };
  vi.mocked(post).mockResolvedValue(local);
  render(<Tools owner onClose={() => undefined} />);
  fireEvent.change(await screen.findByLabelText("Local server name"), {
    target: { value: local.name },
  });
  fireEvent.change(screen.getByLabelText("Full executable path"), {
    target: { value: local.command },
  });
  fireEvent.change(screen.getByLabelText("Arguments (JSON array)"), {
    target: { value: JSON.stringify(local.args) },
  });
  const environment = screen.getByLabelText("Environment values (JSON object, optional)");
  fireEvent.change(environment, { target: { value: '{"API_TOKEN":"private"}' } });
  fireEvent.click(screen.getByRole("button", { name: "Save local server" }));
  await waitFor(() => expect(environment).toHaveValue(""));
  expect(post).toHaveBeenCalledTimes(1);
  expect(post).toHaveBeenCalledWith("/api/tools/servers", {
    transport: "stdio",
    name: local.name,
    command: local.command,
    args: local.args,
    environment: { API_TOKEN: "private" },
  });
  expect(await screen.findByText("Stored environment names: API_TOKEN")).toBeInTheDocument();
  vi.mocked(post).mockResolvedValue({ tools: [{ name: "echo" }] });
  fireEvent.click(screen.getByRole("button", { name: "Start and check Local echo" }));
  expect(await screen.findByText("Available tools: echo")).toBeInTheDocument();
});

it("reports malformed local arguments before saving", async () => {
  vi.mocked(api).mockResolvedValue({
    servers: [],
    localProcesses: { available: true, reason: null },
  });
  render(<Tools owner onClose={() => undefined} />);
  fireEvent.change(await screen.findByLabelText("Local server name"), {
    target: { value: "Broken" },
  });
  fireEvent.change(screen.getByLabelText("Full executable path"), {
    target: { value: "/usr/bin/python3" },
  });
  fireEvent.change(screen.getByLabelText("Arguments (JSON array)"), {
    target: { value: "not JSON" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Save local server" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Arguments are not valid JSON");
  expect(post).not.toHaveBeenCalled();
});

it("clears the credential input after adding a server", async () => {
  vi.mocked(post).mockResolvedValue({ ...server, id: "two", name: "New server" });
  render(<Tools owner onClose={() => undefined} />);
  await screen.findByText("Shared search");
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "New server" } });
  fireEvent.change(screen.getByLabelText("MCP address"), {
    target: { value: "https://example.org/mcp" },
  });
  const token = screen.getByLabelText("Bearer credential (optional)");
  fireEvent.change(token, { target: { value: "test-credential" } });
  fireEvent.click(screen.getByRole("button", { name: "Add server" }));
  await waitFor(() => expect(token).toHaveValue(""));
  expect(await screen.findByText("New server")).toBeInTheDocument();
});

it("says it is loading, then that there are no tool servers", async () => {
  vi.mocked(api).mockResolvedValue({
    servers: [],
    localProcesses: { available: false, reason: "No." },
  });
  render(<Tools owner={false} onClose={() => undefined} />);
  expect(screen.getByRole("status")).toHaveTextContent("Loading tool servers…");
  expect(await screen.findByText("No tool servers yet.")).toBeInTheDocument();
  expect(screen.queryByText("Loading tool servers…")).toBeNull();
});

it("says Back to chat, names each server on its buttons, and copies its address", async () => {
  const other = { ...server, id: "two", name: "Docs search", url: "https://example.org/docs" };
  vi.mocked(api).mockResolvedValue({ servers: [server, other] });
  const onClose = vi.fn();
  render(<Tools owner onClose={onClose} />);
  await screen.findByText("Docs search");
  fireEvent.click(screen.getByRole("button", { name: "Back to chat" }));
  expect(onClose).toHaveBeenCalled();
  expect(
    screen.getByRole("button", { name: "Check connection to Docs search" }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "Copy the address of Shared search" }),
  ).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Remove Docs search" }));
  expect(screen.getByText(/^Remove Docs search\?/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Remove connection Docs search" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /^Keep Docs search/ })).toBeInTheDocument();
});

it("says Checking only on the server being checked", async () => {
  const other = { ...server, id: "two", name: "Docs search" };
  vi.mocked(api).mockResolvedValue({ servers: [server, other] });
  let finish: (value: { tools: { name: string }[] }) => void = () => undefined;
  vi.mocked(post).mockReturnValue(new Promise((resolve) => (finish = resolve)));
  render(<Tools owner={false} onClose={() => undefined} />);
  await screen.findByText("Docs search");
  fireEvent.click(screen.getByRole("button", { name: "Check connection to Docs search" }));
  const mine = screen.getByText("Docs search").closest("article") as HTMLElement;
  const theirs = screen.getByText("Shared search").closest("article") as HTMLElement;
  expect(await within(mine).findByText("Checking…")).toBeInTheDocument();
  expect(within(theirs).queryByText("Checking…")).toBeNull();
  finish({ tools: [{ name: "x" }] });
  expect(await within(mine).findByText("Available tools: x")).toBeInTheDocument();
  expect(within(mine).queryByText("Checking…")).toBeNull();
});

it("names which JSON is wrong in the local server form", async () => {
  vi.mocked(api).mockResolvedValue({
    servers: [],
    localProcesses: { available: true, reason: null },
  });
  render(<Tools owner onClose={() => undefined} />);
  fireEvent.change(await screen.findByLabelText("Local server name"), { target: { value: "A" } });
  fireEvent.change(screen.getByLabelText("Full executable path"), { target: { value: "/bin/x" } });
  fireEvent.change(screen.getByLabelText("Arguments (JSON array)"), {
    target: { value: '{"a":1}' },
  });
  fireEvent.click(screen.getByRole("button", { name: "Save local server" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Arguments must be a JSON array of strings",
  );
  fireEvent.change(screen.getByLabelText("Arguments (JSON array)"), { target: { value: "[]" } });
  fireEvent.change(screen.getByLabelText("Environment values (JSON object, optional)"), {
    target: { value: "[1]" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Save local server" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Environment values must be a JSON object",
  );
  fireEvent.change(screen.getByLabelText("Environment values (JSON object, optional)"), {
    target: { value: "{oops" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Save local server" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Environment values are not valid JSON",
  );
  expect(post).not.toHaveBeenCalled();
});

describe("a check that fails", () => {
  it.each([
    [
      "a refused credential",
      "The MCP server refused this credential. Replace the connection in Tools.",
    ],
    ["a redirect", "The MCP address redirected. Enter its final address in Tools."],
    ["a reply that was too large", "The MCP response exceeded 2 MiB. Ask for a smaller result."],
    [
      "a connection that failed",
      "Could not connect to the MCP server. Check its address and TLS certificate.",
    ],
  ])(
    "says %s in the words the check returned, and never that it could not reach a server that answered",
    async (_cause, words) => {
      vi.mocked(post).mockRejectedValue(new Error(words));
      render(<Tools owner={false} onClose={() => undefined} />);
      fireEvent.click(
        await screen.findByRole("button", { name: "Check connection to Shared search" }),
      );
      const alert = await screen.findByRole("alert");
      expect(alert).toHaveTextContent("The check failed");
      expect(alert).toHaveTextContent(words);
      expect(alert).not.toHaveTextContent(/could not reach/i);
    },
  );

  it("is an alert in the card of the server it is about, and clears when checked again", async () => {
    const other = { ...server, id: "two", name: "Docs search" };
    vi.mocked(api).mockResolvedValue({ servers: [server, other] });
    vi.mocked(post).mockRejectedValueOnce(new Error("The MCP address redirected."));
    render(<Tools owner={false} onClose={() => undefined} />);
    await screen.findByText("Docs search");
    fireEvent.click(screen.getByRole("button", { name: "Check connection to Docs search" }));
    const mine = screen.getByText("Docs search").closest("article") as HTMLElement;
    const theirs = screen.getByText("Shared search").closest("article") as HTMLElement;
    expect(await within(mine).findByRole("alert")).toHaveTextContent("The MCP address redirected.");
    expect(within(theirs).queryByRole("alert")).toBeNull();
    vi.mocked(post).mockResolvedValue({ tools: [{ name: "search" }] });
    fireEvent.click(screen.getByRole("button", { name: "Check connection to Docs search" }));
    expect(await within(mine).findByText("Available tools: search")).toBeInTheDocument();
    expect(within(mine).queryByRole("alert")).toBeNull();
  });

  it("is not a failure when the server answered and offers no tools", async () => {
    vi.mocked(post).mockResolvedValue({ tools: [] });
    render(<Tools owner={false} onClose={() => undefined} />);
    fireEvent.click(
      await screen.findByRole("button", { name: "Check connection to Shared search" }),
    );
    expect(await screen.findByText("This server offers no tools.")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();
  });
});

describe("removing a connection", () => {
  const other = { ...server, id: "two", name: "Docs search" };
  beforeEach(() => {
    vi.mocked(api).mockResolvedValue({ servers: [server, other] });
  });

  it("asks first: the safe answer has focus, Escape declines, and focus goes back to Remove", async () => {
    render(<Tools owner onClose={() => undefined} />);
    await screen.findByText("Docs search");
    fireEvent.click(screen.getByRole("button", { name: "Remove Docs search" }));
    const group = screen.getByRole("group", { name: "Remove Docs search?" });
    const keep = within(group).getByRole("button", { name: /^Keep Docs search/ });
    expect(keep).toHaveFocus();
    // The question is on its own line, above the two answers.
    expect(group).toHaveTextContent(/^Remove Docs search\? Pending calls will not run/);
    fireEvent.keyDown(keep, { key: "Escape" });
    expect(screen.queryByRole("group", { name: "Remove Docs search?" })).toBeNull();
    expect(del).not.toHaveBeenCalled();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Remove Docs search" })).toHaveFocus(),
    );
  });

  it("removes it only when the danger answer is taken, and the card goes", async () => {
    vi.mocked(del).mockResolvedValue(undefined);
    render(<Tools owner onClose={() => undefined} />);
    await screen.findByText("Docs search");
    fireEvent.click(screen.getByRole("button", { name: "Remove Docs search" }));
    expect(del).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Remove connection Docs search" }));
    await waitFor(() => expect(del).toHaveBeenCalledWith("/api/tools/servers/two"));
    await waitFor(() => expect(screen.queryByText("Docs search")).toBeNull());
  });

  it("names the failure when the server will not remove it, and keeps the card", async () => {
    vi.mocked(del).mockRejectedValue(new Error("Only the install owner can remove servers."));
    render(<Tools owner onClose={() => undefined} />);
    await screen.findByText("Docs search");
    fireEvent.click(screen.getByRole("button", { name: "Remove Docs search" }));
    fireEvent.click(screen.getByRole("button", { name: "Remove connection Docs search" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Only the install owner can remove servers.",
    );
    expect(screen.getByText("Docs search")).toBeInTheDocument();
  });
});

it("says what could not be read when the list of servers fails, in the server's own words", async () => {
  vi.mocked(api).mockRejectedValue(new Error("Not Found"));
  render(<Tools owner={false} onClose={() => undefined} />);
  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent("Could not read the tool servers");
  expect(alert).toHaveTextContent("Not Found");
  expect(screen.queryByText("Loading tool servers…")).toBeNull();
});

it("offers to read the list of servers again, and shows it when the second read works", async () => {
  vi.mocked(api).mockRejectedValueOnce(new Error("Not Found"));
  render(<Tools owner={false} onClose={() => undefined} />);
  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent("Not Found");
  fireEvent.click(within(alert).getByRole("button", { name: "Try again" }));
  expect(await screen.findByText("Shared search")).toBeInTheDocument();
  expect(screen.queryByRole("alert")).toBeNull();
  expect(api).toHaveBeenCalledTimes(2);
});

it("has one title, the page's heading, which App moves focus to when the page opens", () => {
  render(<Tools owner={false} onClose={() => undefined} />);
  const title = screen.getByRole("heading", { level: 1, name: "Toolbox · Tools" });
  expect(title).toHaveAttribute("id", "view-heading");
  // Focusable by script, and not a stop of the Tab key.
  expect(title).toHaveAttribute("tabindex", "-1");
  expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
});

describe("the local server form", () => {
  beforeEach(() => {
    vi.mocked(api).mockResolvedValue({
      servers: [],
      localProcesses: { available: true, reason: null },
    });
  });

  it("warns, ahead of its fields, that what it adds runs with Workbench's access", async () => {
    render(<Tools owner onClose={() => undefined} />);
    const title = await screen.findByText("Runs with Workbench's access");
    const warning = title.closest(".notice") as HTMLElement;
    expect(warning).toHaveClass("notice--warn");
    expect(warning).toHaveTextContent("Only add programs you trust.");
    // A warning to read, not an alert that interrupts: no role.
    expect(warning).not.toHaveAttribute("role");
    expect(
      warning.compareDocumentPosition(screen.getByLabelText("Local server name")) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("marks the field a refusal named, ties it to the alert, and leaves the others alone", async () => {
    render(<Tools owner onClose={() => undefined} />);
    fireEvent.change(await screen.findByLabelText("Local server name"), { target: { value: "A" } });
    fireEvent.change(screen.getByLabelText("Full executable path"), {
      target: { value: "/bin/x" },
    });
    fireEvent.change(screen.getByLabelText("Arguments (JSON array)"), {
      target: { value: "not JSON" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save local server" }));
    const alert = await screen.findByRole("alert");
    const args = screen.getByLabelText("Arguments (JSON array)");
    const environment = screen.getByLabelText("Environment values (JSON object, optional)");
    expect(args).toHaveAttribute("aria-invalid", "true");
    expect(args).toHaveClass("border-error-line");
    expect(args).toHaveAccessibleDescription(
      expect.stringContaining("Arguments are not valid JSON"),
    );
    expect(alert).toHaveTextContent("Arguments are not valid JSON");
    expect(screen.getByLabelText("Full executable path")).not.toHaveAttribute("aria-invalid");
    expect(environment).not.toHaveAttribute("aria-invalid");
    // Mending the arguments and saving again moves the mark to what is wrong now.
    fireEvent.change(args, { target: { value: "[]" } });
    fireEvent.change(environment, { target: { value: "[1]" } });
    fireEvent.click(screen.getByRole("button", { name: "Save local server" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Environment values must be");
    expect(args).not.toHaveAttribute("aria-invalid");
    expect(environment).toHaveAttribute("aria-invalid", "true");
  });

  it("describes each field by the help under it, so the help is read with the field", async () => {
    render(<Tools owner onClose={() => undefined} />);
    expect(await screen.findByLabelText("Full executable path")).toHaveAccessibleDescription(
      /Put the script path in Arguments/,
    );
    expect(screen.getByLabelText("Arguments (JSON array)")).toHaveAccessibleDescription(
      /Each string is one argument/,
    );
  });
});

describe("choosing tools for a chat", () => {
  it("names a local server by its kind, set apart from its name", async () => {
    vi.mocked(api).mockResolvedValue({
      servers: [
        server,
        {
          id: "loc",
          transport: "stdio",
          name: "Local echo",
          command: "x",
          args: [],
          environmentKeys: [],
          access: "owner",
        },
        {
          id: "site:s:t",
          transport: "site",
          name: "desk · Notes tool",
          site: "s",
          label: "desk",
          server: "t",
          available: false,
          reason: null,
          jobSite: true,
        },
      ],
    });
    render(<ToolSelection selected={[]} onChange={() => undefined} />);
    expect(
      await screen.findByRole("checkbox", { name: "Local echo · Local, owner only" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("checkbox", { name: "desk · Notes tool · Unavailable now" }),
    ).toBeInTheDocument();
    // The kind is in a muted span of its own; the name is not.
    expect(screen.getByText("· Local, owner only")).toHaveClass("text-muted");
    expect(screen.getByRole("checkbox", { name: "Shared search" })).toBeInTheDocument();
  });

  it("says when the list cannot be read, in the server's words", async () => {
    vi.mocked(api).mockRejectedValue(new Error("Workbench answered 500."));
    render(<ToolSelection selected={[]} onChange={() => undefined} />);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Could not read the tool servers");
    expect(alert).toHaveTextContent("Workbench answered 500.");
  });

  it("offers to read the list again, and shows it when the second read works", async () => {
    vi.mocked(api).mockRejectedValueOnce(new Error("Workbench answered 500."));
    render(<ToolSelection selected={[]} onChange={() => undefined} />);
    fireEvent.click(
      within(await screen.findByRole("alert")).getByRole("button", { name: "Try again" }),
    );
    expect(await screen.findByRole("checkbox", { name: "Shared search" })).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();
  });
});

/**
 * The classes the other screens write, read from their sources: a class the
 * stylesheet does not name styles nothing, and Tailwind finds a utility only
 * as a whole word, so one glued to a template's `${` is never generated.
 */
describe("the classes the other screens write", () => {
  const components = import.meta.dirname;
  const css = readFileSync(join(components, "..", "styles.css"), "utf8");
  const defined = new Set([...css.matchAll(/\.([a-z][\w-]*)/g)].map((m) => m[1]!));
  const SCREENS = [
    "ChatSettings",
    "Tools",
    "LocalToolForm",
    "Folders",
    "Media",
    "AudioForms",
    "VideoForm",
    "JobSites",
    "SiteLinkNote",
    "SignIn",
    "Workspaces",
  ];
  /** Every className a source writes, as its text: a plain string or a template. */
  const classLists = (source: string) =>
    [...source.matchAll(/className=\{?(?:`([^`]*)`|"([^"]*)")/g)].map((m) => m[1] ?? m[2] ?? "");
  /** The classes of the stylesheet's own families that a list writes and the stylesheet lacks. */
  const unnamed = (lists: string[]) =>
    lists
      .flatMap((list) => list.replace(/\$\{[^}]*\}/g, " ").split(/\s+/))
      .filter((token) => /^(notice|badge|btn|card|title|field|control|sheet|group)-/.test(token))
      .filter((token) => !defined.has(token));
  /** A word that runs into a template's `${`, which Tailwind reads as one unknown word. */
  const glued = (lists: string[]) =>
    lists
      .flatMap((list) => list.split(/\s+/))
      .filter((token) => /[\w-]\$\{/.test(token))
      .filter((token) => !/^(control|card|badge|notice)\$\{/.test(token));

  it("are all named by the stylesheet, among the families it defines", () => {
    for (const name of SCREENS) {
      const lists = classLists(readFileSync(join(components, `${name}.tsx`), "utf8"));
      expect(lists.length, name).toBeGreaterThan(0);
      expect(unnamed(lists), name).toEqual([]);
    }
  });

  it("never run a utility into a template's expression", () => {
    for (const name of SCREENS) {
      const lists = classLists(readFileSync(join(components, `${name}.tsx`), "utf8"));
      expect(glued(lists), name).toEqual([]);
    }
  });

  it("can tell a class the stylesheet lacks, and a utility run into an expression", () => {
    expect(unnamed(["notice notice--warn", "notice notice--warning btn-ghost"])).toEqual([
      "notice--warning",
      "btn-ghost",
    ]);
    expect(glued(["control w-24${edge}", "control w-24 ${edge}", "card${on ? ' x' : ''}"])).toEqual(
      ["w-24${edge}"],
    );
  });
});
