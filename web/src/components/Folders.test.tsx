import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { api, post } from "../lib/api";
import { Folders, FolderSelection } from "./Folders";

vi.mock("../lib/api", () => ({ api: vi.fn(), post: vi.fn(), del: vi.fn() }));

const grant = { id: "notes", name: "Project notes", subject: "ada", writable: false, usable: true };

it("lets a central host select online node folders and clear offline selections", async () => {
  vi.mocked(api).mockResolvedValue({
    available: true,
    localAvailable: false,
    reason: "Local file accounts unavailable",
    grants: [
      { ...grant, id: "node:online", source: "node", node: "Desktop", available: true },
      {
        ...grant,
        id: "node:offline",
        source: "node",
        node: "Laptop",
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
