import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { api } from "../lib/api";
import type { Me } from "../lib/types";
import { Sidebar } from "./Sidebar";

vi.mock("../lib/api", () => ({ api: vi.fn() }));

const me: Me = { sub: "ada", name: "Ada", owner: false, ownerReadsChats: false, username: null };
const owner: Me = { ...me, owner: true, ownerReadsChats: true };

function sidebar(who: Me, chatsLoaded?: boolean) {
  render(
    <Sidebar
      me={who}
      chats={[]}
      chatsLoaded={chatsLoaded}
      current={null}
      onOpen={vi.fn()}
      onOpenMedia={vi.fn()}
      onClose={vi.fn()}
      onNew={vi.fn()}
      onSignOut={vi.fn()}
      creating={false}
      error={null}
      onRetry={vi.fn()}
    />,
  );
}

beforeEach(() => vi.mocked(api).mockReset());

it("says Loading chats before the first list, and never No chats yet", () => {
  sidebar(me, false);
  expect(screen.getByRole("status")).toHaveTextContent("Loading chats…");
  expect(screen.queryByText(/No chats yet/)).toBeNull();
});

it("says what to do once the list has loaded empty", () => {
  sidebar(me, true);
  expect(screen.getByText("No chats yet. Choose New chat to start one.")).toBeInTheDocument();
  expect(screen.queryByText("Loading chats…")).toBeNull();
});

it("shows people loading, then a person's chats with aria-expanded, and No chats for none", async () => {
  let release: (v: unknown) => void = () => undefined;
  vi.mocked(api).mockImplementation((path: string) => {
    if (path === "/api/people") {
      return new Promise((resolve) => {
        release = resolve;
      }) as never;
    }
    return Promise.resolve({ chats: [] }) as never;
  });
  sidebar(owner, true);
  const details = screen.getByText(/People's chats and media/).closest("details")!;
  details.open = true;
  fireEvent(details, new Event("toggle"));
  expect(screen.getByText("Loading people…")).toHaveAttribute("role", "status");
  release({ people: [{ sub: "bo", name: "Bo", chats: 0, media: 0 }] });
  const person = await screen.findByRole("button", { name: "Bo (0)" });
  expect(screen.queryByText("Loading people…")).toBeNull();
  expect(person).toHaveAttribute("aria-expanded", "false");
  fireEvent.click(person);
  await waitFor(() => expect(person).toHaveAttribute("aria-expanded", "true"));
  expect(screen.getByText("No chats.")).toBeInTheDocument();
});
