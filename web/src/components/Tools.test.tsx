import { fireEvent, render, screen, waitFor } from "@testing-library/react";

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
  expect(screen.queryByRole("button", { name: "Remove" })).toBeNull();
  vi.mocked(post).mockResolvedValue({ tools: [{ name: "search" }] });
  fireEvent.click(screen.getByRole("button", { name: "Check connection" }));
  expect(await screen.findByRole("status")).toHaveTextContent("Available tools: search");
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
  fireEvent.click(screen.getByRole("button", { name: "Start and check" }));
  expect(await screen.findByRole("status")).toHaveTextContent("Available tools: echo");
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
  expect(await screen.findByRole("alert")).toHaveTextContent("Arguments must be a JSON array");
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
