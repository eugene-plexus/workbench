import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { api, post } from "../lib/api";
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
