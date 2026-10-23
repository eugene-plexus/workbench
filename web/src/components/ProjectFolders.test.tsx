import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { api, post } from "../lib/api";
import { LIMITS, choice, waitingFolder } from "../test-folders";
import { ProjectFolders } from "./ProjectFolders";

vi.mock("../lib/api", async (original) => ({
  ...(await original<typeof import("../lib/api")>()),
  api: vi.fn(),
  post: vi.fn(),
}));

it("adds a granted folder to this project", async () => {
  vi.mocked(api).mockResolvedValue({
    available: true,
    reason: null,
    enabled: true,
    folders: [],
    grants: [choice()],
    limits: LIMITS,
  });
  vi.mocked(post).mockResolvedValue(waitingFolder());
  render(<ProjectFolders projectId="garden" />);
  fireEvent.click(await screen.findByRole("button", { name: "Add folder" }));
  await waitFor(() => expect(api).toHaveBeenCalledWith("/api/documents/folders?projectId=garden"));
  await waitFor(() =>
    expect(post).toHaveBeenCalledWith(
      "/api/documents/folders",
      expect.objectContaining({ projectId: "garden" }),
    ),
  );
});
