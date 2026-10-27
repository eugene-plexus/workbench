import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { api, ApiError, patch, upload } from "../lib/api";
import type { Skill } from "../lib/types";
import { Skills, SkillSelection } from "./Skills";

vi.mock("../lib/api", async (original) => ({
  ...(await original<typeof import("../lib/api")>()),
  api: vi.fn(),
  post: vi.fn(),
  patch: vi.fn(),
  del: vi.fn(),
  upload: vi.fn(),
}));

const base: Skill = {
  id: "s1",
  name: "pdf-forms",
  description: "Fill in PDF forms.",
  source: "upload",
  bytes: 2048,
  files: [{ path: "references/style.md", size: 120 }],
  createdAt: 1,
  updatedAt: 1,
  on: false,
  everyone: false,
  fromEugene: false,
  canEdit: true,
  listed: false,
  notListed: "Off.",
  hasScripts: true,
};
const shared: Skill = {
  ...base,
  id: "s2",
  name: "house-style",
  description: "The house style.",
  source: "written",
  files: [],
  on: true,
  everyone: true,
  fromEugene: true,
  canEdit: false,
  listed: true,
  notListed: null,
  hasScripts: false,
};
const skillMd = "---\nname: pdf-forms\ndescription: Fill in PDF forms.\n---\n\nRead the form.\n";

function answer(skills: Skill[]) {
  vi.mocked(api).mockImplementation(async (path: string) => {
    if (path === "/api/skills") return { skills };
    const found = skills.find((s) => path.endsWith(s.id));
    return { ...found, skillMd, instructions: "Read the form.", others: [], problem: null };
  });
}

beforeEach(() => vi.clearAllMocks());

it("shows an uploaded skill's whole SKILL.md before it can be turned on", async () => {
  answer([base]);
  render(<Skills owner={false} onClose={() => undefined} />);
  expect(await screen.findByLabelText("SKILL.md of pdf-forms")).toHaveTextContent("Read the form.");
  const toggle = screen.getByRole("checkbox", { name: "Use in chats: Off" });
  await waitFor(() => expect(toggle).toBeEnabled());
  vi.mocked(patch).mockResolvedValue({ ...base, on: true });
  fireEvent.click(toggle);
  await waitFor(() => expect(patch).toHaveBeenCalledWith("/api/skills/s1", { on: true }));
  expect(screen.getByText("Has scripts")).toBeInTheDocument();
});

it("marks a skill from Eugene and leaves it read-only, with a switch for oneself", async () => {
  answer([shared]);
  render(<Skills owner={false} onClose={() => undefined} />);
  expect(await screen.findByText("From Eugene")).toBeInTheDocument();
  expect(screen.getByRole("checkbox", { name: "Use in chats: On" })).toBeChecked();
  expect(screen.queryByRole("button", { name: "Edit" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Delete house-style" })).toBeNull();
  expect(screen.queryByText(/Give to everyone/)).toBeNull();
});

it("asks Replace or Keep both when the name is taken", async () => {
  answer([]);
  vi.mocked(upload).mockRejectedValueOnce(
    new ApiError("You already have a skill named pdf-forms.", 409, {
      conflict: { id: "s1", name: "pdf-forms" },
    }),
  );
  render(<Skills owner={false} onClose={() => undefined} />);
  const file = new File(["x"], "skill.zip");
  fireEvent.change(await screen.findByLabelText("Skill file"), { target: { files: [file] } });
  expect(await screen.findByText("You already have a skill named pdf-forms.")).toBeInTheDocument();
  vi.mocked(upload).mockResolvedValueOnce({ ...base, name: "pdf-forms-2" });
  fireEvent.click(screen.getByRole("button", { name: "Keep both" }));
  await waitFor(() => expect(upload).toHaveBeenCalledTimes(2));
  const form = vi.mocked(upload).mock.calls[1]![1];
  expect(form.get("onConflict")).toBe("both");
  expect(await screen.findByText(/Added pdf-forms-2, off/)).toBeInTheDocument();
});

it("lets a chat use all its person's skills, or a pick of them", async () => {
  answer([{ ...base, on: true }, shared]);
  const change = vi.fn();
  const { rerender } = render(<SkillSelection selected={null} onChange={change} />);
  expect(await screen.findByRole("radio", { name: "All my skills" })).toBeChecked();
  fireEvent.click(screen.getByRole("radio", { name: "Only these" }));
  expect(change).toHaveBeenCalledWith(["s1", "s2"]);
  rerender(<SkillSelection selected={["s2"]} onChange={change} />);
  expect(screen.getByRole("checkbox", { name: "pdf-forms" })).not.toBeChecked();
  expect(screen.getByRole("checkbox", { name: "house-style · From Eugene" })).toBeChecked();
  fireEvent.click(screen.getByRole("radio", { name: "All my skills" }));
  expect(change).toHaveBeenLastCalledWith(null);
});
