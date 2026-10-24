import { useEffect, useState } from "react";

import { api, fileOf } from "../lib/api";
import { failureWords, saveFile } from "../lib/memory";

export interface RenderedPage {
  image: string;
  width: number;
  height: number;
  boxes: number[][];
  pageCount: number;
  message?: string;
}

export function PageView({
  document,
  name,
  initialPage,
  passage,
  onClose,
}: {
  document: string;
  name: string;
  initialPage: number;
  passage?: number;
  onClose: () => void;
}) {
  const [number, setNumber] = useState(initialPage);
  const [page, setPage] = useState<RenderedPage | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const base = `/api/documents/${encodeURIComponent(document)}`;

  useEffect(() => {
    let current = true;
    void api<RenderedPage>(`${base}/pages/${number}${passage == null ? "" : `?passage=${passage}`}`)
      .then((next) => {
        if (current) {
          setPage(next);
          setProblem(null);
          setLoading(false);
        }
      })
      .catch((failure) => {
        if (current) {
          setProblem(failureWords(failure));
          setLoading(false);
        }
      });
    return () => {
      current = false;
    };
  }, [base, number, passage]);

  function move(next: number) {
    setNumber(next);
    setPage(null);
    setLoading(true);
    setProblem(null);
  }

  async function original() {
    try {
      const file = await fileOf(`${base}/original`);
      saveFile(file.blob, file.name ?? name);
    } catch (failure) {
      setProblem(failureWords(failure));
    }
  }

  return (
    <aside
      aria-label={`${name}, page ${number}`}
      className="fixed inset-y-2 right-2 z-50 flex w-[min(48rem,95vw)] flex-col gap-3 overflow-auto rounded-plexus border border-line bg-panel p-4 shadow-xl"
    >
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="mr-auto font-medium">
          {name} · Page {number}
          {page ? ` of ${page.pageCount}` : ""}
        </h2>
        <button type="button" data-testid="pdf-page-close" className="btn" onClick={onClose}>
          Close page
        </button>
      </div>
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          data-testid="pdf-page-previous"
          className="btn"
          disabled={loading || number <= 1}
          onClick={() => move(number - 1)}
        >
          Previous
        </button>
        <button
          type="button"
          data-testid="pdf-page-next"
          className="btn"
          disabled={loading || !page || number >= page.pageCount}
          onClick={() => move(number + 1)}
        >
          Next
        </button>
        <button
          type="button"
          data-testid="pdf-page-original"
          className="btn"
          onClick={() => void original()}
        >
          Open original
        </button>
      </div>
      {loading && <p role="status">Loading page {number}…</p>}
      {problem && <p role="alert">{problem}</p>}
      {page?.message && <p role="status">{page.message}</p>}
      {page && (
        <div className="relative">
          <img
            src={page.image}
            width={page.width}
            height={page.height}
            alt={`${name}, page ${number}`}
            className="h-auto w-full"
          />
          <svg
            aria-label="Marked passage"
            className="pointer-events-none absolute inset-0 h-full w-full"
            viewBox={`0 0 ${page.width} ${page.height}`}
          >
            {page.boxes.map(([x, y, w, h], at) => (
              <rect key={at} x={x} y={y} width={w} height={h} fill="#ffcc00" fillOpacity="0.35" />
            ))}
          </svg>
        </div>
      )}
    </aside>
  );
}
