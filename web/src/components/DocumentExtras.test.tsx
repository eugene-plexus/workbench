import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { api, post, put } from "../lib/api";
import { passage, record } from "../test-saved";
import { PageView } from "./PageView";
import { ReadScannedDocument, ScannedPages } from "./ScannedPages";
import { PassageList } from "./SourcePassages";
import { progressWords } from "../lib/words";

vi.mock("../lib/api", async (original) => ({
  ...(await original<typeof import("../lib/api")>()),
  api: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
}));

beforeEach(() => {
  vi.resetAllMocks();
});
afterEach(cleanup);

it("opens the cited PDF page with marked rectangles and navigates", async () => {
  vi.mocked(api).mockResolvedValue({
    image: "data:image/png;base64,fixture",
    width: 100,
    height: 200,
    boxes: [[10, 20, 30, 5]],
    pageCount: 8,
  });
  render(<PassageList passages={[passage()]} record={record()} />);
  fireEvent.click(screen.getByText("Show page 4"));
  expect(await screen.findByRole("img")).toHaveAttribute("alt", "harbour-survey.pdf, page 4");
  expect(screen.getByLabelText("Marked passage").querySelector("rect")).toHaveAttribute("x", "10");
  expect(api).toHaveBeenCalledWith("/api/documents/d1/pages/4?passage=17");
  fireEvent.click(screen.getByText("Next"));
  await waitFor(() => expect(api).toHaveBeenCalledWith("/api/documents/d1/pages/5?passage=17"));
  fireEvent.click(screen.getByText("Close page"));
  expect(screen.queryByRole("img")).not.toBeInTheDocument();
});

it("says why words were not marked and reports the observed failure", async () => {
  vi.mocked(api)
    .mockResolvedValueOnce({
      image: "data:image/png;base64,fixture",
      width: 100,
      height: 200,
      boxes: [],
      pageCount: 2,
      message: "The passage's words were not found on this page. It is shown whole.",
    })
    .mockRejectedValueOnce(new Error("The stored file is missing."));
  render(<PageView document="d1" name="Report.pdf" initialPage={1} onClose={() => undefined} />);
  expect(
    await screen.findByText("The passage's words were not found on this page. It is shown whole."),
  ).toBeInTheDocument();
  fireEvent.click(screen.getByText("Next"));
  expect(await screen.findByRole("alert")).toHaveTextContent("The stored file is missing.");
  expect(screen.queryByRole("img")).not.toBeInTheDocument();
});

it("shows the passage limitation and cost before starting a new whole answer", async () => {
  vi.mocked(post).mockResolvedValue({});
  render(
    <PassageList
      passages={[passage()]}
      record={record({
        whole: {
          state: "offered",
          line: "Answered from 5 passages, not the whole document.",
          cost: "12 parts, about 140,000 tokens.",
          action: "/whole",
        },
      })}
    />,
  );
  expect(screen.getByText("Answered from 5 passages, not the whole document.")).toBeInTheDocument();
  expect(screen.getByText("12 parts, about 140,000 tokens.")).toBeInTheDocument();
  expect(post).not.toHaveBeenCalled();
  fireEvent.click(screen.getByText("Read all of it"));
  await waitFor(() =>
    expect(post).toHaveBeenCalledWith("/whole", {
      documents: ["d1"],
      cost: "12 parts, about 140,000 tokens.",
    }),
  );
  expect(
    progressWords({
      stage: "documents_whole",
      cost: "12 parts, about 140,000 tokens.",
      message: "Reading part 3 of 12 of Annual report.pdf",
    }),
  ).toContain("Reading part 3 of 12 of Annual report.pdf");
});

it("shows the effective scan switch after a failed save", async () => {
  const effective = {
    enabled: false,
    outside: false,
    model: "fake-vision",
    maxPages: 200,
    wholeByParts: "always",
    message: "Scanned pages are read with fake-vision",
    pages: 48,
  };
  vi.mocked(api).mockResolvedValue(effective);
  vi.mocked(put).mockRejectedValue(new Error("The disk is full."));
  render(<ScannedPages />);
  const toggle = await screen.findByLabelText("Read scanned pages");
  expect(toggle).not.toBeChecked();
  expect(screen.getByText(/48 pages to read/)).toBeInTheDocument();
  fireEvent.click(toggle);
  expect(await screen.findByRole("alert")).toHaveTextContent("The disk is full.");
  await waitFor(() => expect(toggle).not.toBeDisabled());
  expect(toggle).not.toBeChecked();
});

it("shows the scanned-page count and model before sending a picture", async () => {
  vi.mocked(api).mockResolvedValue({ model: "fake-vision" });
  vi.mocked(post)
    .mockResolvedValueOnce({
      message: "48 pages to read with fake-vision",
      canRead: true,
      maxPages: 200,
    })
    .mockResolvedValueOnce({});
  const started = vi.fn();
  render(<ReadScannedDocument id="d1" onStarted={started} />);
  fireEvent.click(await screen.findByText("Read the pages with fake-vision"));
  expect(await screen.findByText("48 pages to read with fake-vision")).toBeInTheDocument();
  expect(post).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByText("Start reading pages"));
  await waitFor(() => expect(started).toHaveBeenCalledOnce());
});
