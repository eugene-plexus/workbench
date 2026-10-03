import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { api, post } from "../lib/api";
import { Tools, ToolSelection } from "./Tools";

vi.mock("../lib/api", () => ({ api: vi.fn(), post: vi.fn(), del: vi.fn() }));

const server = { id: "one", name: "Shared search", url: "https://example.org/mcp", hasToken: true };

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
  fireEvent.click(screen.getByRole("checkbox", { name: /Removed server/ }));
  expect(change).toHaveBeenCalledWith(["one"]);
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
