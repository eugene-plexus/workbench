import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { api, post } from "../lib/api";
import type { RemovedPeoplePage, RemovedPerson } from "../lib/removal";
import { noticeWords } from "../lib/removal";
import { RemovedPeople, RemovedPeopleNotice } from "./RemovedPeople";

vi.mock("../lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../lib/api")>()),
  api: vi.fn(),
  post: vi.fn(),
  fileOf: vi.fn(),
}));

const DAY = 86_400;
const NOW = Date.UTC(2026, 9, 21, 12, 0, 0) / 1000;

function person(over: Partial<RemovedPerson> = {}): RemovedPerson {
  return {
    sub: "p-jessie",
    name: "Jessie",
    state: "hidden",
    removedAt: NOW,
    deleteAt: NOW + 30 * DAY,
    purgedAt: null,
    problem: null,
    ...over,
  };
}

function served(over: Partial<RemovedPeoplePage> = {}): RemovedPeoplePage {
  return {
    retention: "days",
    days: 30,
    people: [person()],
    brake: null,
    checkedAt: NOW,
    problem: null,
    exportLog: [],
    ...over,
  };
}

let page: RemovedPeoplePage;

beforeEach(() => {
  page = served();
  vi.mocked(api).mockImplementation((async (path: string) => {
    if (path === "/api/removed-people") return page;
    throw new Error(`not served: ${path}`);
  }) as never);
});

afterEach(() => vi.clearAllMocks());

test("a removed person shows when their data is deleted, and Delete now asks first", async () => {
  vi.mocked(post).mockResolvedValue(undefined as never);
  render(<RemovedPeople />);
  const row = await screen.findByTestId("removed-person");
  expect(row.textContent).toContain("Jessie · removed");
  expect(row.textContent).toContain("deleted on");
  fireEvent.click(within(row).getByRole("button", { name: "Delete now" }));
  expect(row.textContent).toContain(
    "Delete Jessie's chats, files, notes and documents now? This cannot be undone.",
  );
  expect(post).not.toHaveBeenCalled();
  fireEvent.click(within(row).getByRole("button", { name: "Delete now" }));
  await waitFor(() =>
    expect(post).toHaveBeenCalledExactlyOnceWith("/api/removed-people/p-jessie/delete"),
  );
});

test("the page says which retention is in effect", async () => {
  page = served({ retention: "none", people: [] });
  render(<RemovedPeople />);
  expect(
    await screen.findByText(/Workbench deletes their chats, files, notes and documents at once/),
  ).toBeTruthy();
  expect(screen.getByText("Nobody has been removed.")).toBeTruthy();
});

test("the brake hides nobody until the owner says they were removed", async () => {
  page = served({
    people: [],
    brake: { count: 5, names: ["A", "B", "C", "D", "E"], dismissed: false, otherEugene: false },
  });
  vi.mocked(post).mockResolvedValue(served({ brake: null }) as never);
  render(<RemovedPeople />);
  const brake = await screen.findByTestId("removed-people-brake");
  expect(brake.textContent).toContain(
    "Eugene no longer knows 5 people who use Workbench. Nothing was hidden.",
  );
  fireEvent.click(within(brake).getByRole("button", { name: "They were removed" }));
  await waitFor(() =>
    expect(post).toHaveBeenCalledExactlyOnceWith("/api/removed-people/brake", { removed: true }),
  );
});

test("a held person waits for the owner, and kept says so", async () => {
  page = served({
    people: [
      person({ sub: "a", name: "Ann", state: "held", deleteAt: null }),
      person({ sub: "b", name: "Ben", state: "kept", deleteAt: null }),
    ],
  });
  render(<RemovedPeople />);
  const [ann, ben] = (await screen.findAllByTestId("removed-person")) as [HTMLElement, HTMLElement];
  expect(ann.textContent).toContain(
    "Ann · removed before this update · nothing is deleted until you choose",
  );
  expect(ben.textContent).toContain("Ben · kept until you delete it");
  expect(within(ben).getByRole("button", { name: "Delete in 30 days" })).toBeTruthy();
});

test("the owner's notice names the soonest delete date", async () => {
  expect(noticeWords(served({ people: [] }))).toBeNull();
  expect(noticeWords(served())).toMatch(/^1 removed person's data will be deleted on /);
  page = served({ people: [person(), person({ sub: "x", deleteAt: NOW + 2 * DAY })] });
  const review = vi.fn();
  render(<RemovedPeopleNotice onReview={review} />);
  const notice = await screen.findByTestId("removed-people-notice");
  expect(notice.textContent).toMatch(/2 removed people's data will be deleted on /);
  fireEvent.click(within(notice).getByRole("button", { name: "Review" }));
  expect(review).toHaveBeenCalled();
});
