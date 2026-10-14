/**
 * What a tool returned beyond its words (workbench-rich-content-roadmap.md
 * RC3): a picture, a sound, a video, a file, data, an address it named, and
 * what was not kept, with why. The words stay in the call's Result.
 *
 * Every file is fetched from Workbench with the person's own session each
 * time it is shown (lib/api.ts `fileUrl`) and is the chat's own: a picture
 * an answer names by address is still never fetched (Markdown.tsx), and an
 * address a tool named is only a link, opened by the person.
 */

import { Download, FileText, Film, Image as ImageIcon, Link2, Music } from "lucide-react";
import { useEffect, useState } from "react";

import { fileUrl, releaseFileUrl } from "../lib/api";
import type { ContentBlock, ToolFile } from "../lib/types";
import { safeHref } from "./Markdown";

/** A size as a person reads it. */
export function sizeWords(size: number): string {
  if (size < 1024) return `${size} bytes`;
  if (size < 1024 * 1024) return `${Math.round(size / 1024)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1).replace(/\.0$/, "")} MB`;
}

/** A file's blob: address while `wanted`, released when it is no longer shown. */
function useFileUrl(fileId: string, wanted: boolean): { url: string | null; problem: string } {
  const [state, setState] = useState<{ url: string | null; problem: string }>({
    url: null,
    problem: "",
  });
  useEffect(() => {
    if (!wanted) return;
    let made: string | null = null;
    let gone = false;
    fileUrl(fileId).then(
      (url) => {
        made = url;
        if (gone) releaseFileUrl(url);
        else setState({ url, problem: "" });
      },
      (error: unknown) => {
        if (!gone) {
          setState({
            url: null,
            problem: error instanceof Error ? error.message : String(error),
          });
        }
      },
    );
    return () => {
      gone = true;
      if (made) releaseFileUrl(made);
    };
  }, [fileId, wanted]);
  return state;
}

function described(file: ToolFile): string {
  const said = file.declaredType ? `, said to be ${file.declaredType}` : "";
  return `${file.mediaType}, ${sizeWords(file.size)}${said}`;
}

async function save(file: ToolFile): Promise<void> {
  const url = await fileUrl(file.fileId);
  const link = document.createElement("a");
  link.href = url;
  link.download = file.name;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => releaseFileUrl(url), 10_000);
}

function DownloadButton({ file }: { file: ToolFile }) {
  const [problem, setProblem] = useState("");
  return (
    <>
      <button
        type="button"
        className="act"
        aria-label={`Download ${file.name}`}
        onClick={() => {
          setProblem("");
          save(file).catch((error: unknown) =>
            setProblem(
              `It could not be downloaded: ${error instanceof Error ? error.message : String(error)}`,
            ),
          );
        }}
      >
        <Download size={14} aria-hidden />
        Download
      </button>
      {problem && (
        <span role="alert" className="text-sm text-error">
          {problem}
        </span>
      )}
    </>
  );
}

function Picture({ file }: { file: ToolFile }) {
  const { url, problem } = useFileUrl(file.fileId, true);
  return (
    <figure className="tool-file" data-testid="tool-picture">
      {url ? (
        <a
          href={url}
          target="_blank"
          rel="noopener noreferrer"
          title={`Open ${file.name} full size`}
        >
          <img
            src={url}
            alt={file.name}
            width={file.width}
            height={file.height}
            className="tool-picture"
          />
        </a>
      ) : (
        <p className="text-sm text-muted" role={problem ? "alert" : undefined}>
          {problem ? `The picture could not be shown: ${problem}` : "Loading the picture…"}
        </p>
      )}
      <figcaption className="tool-file-head">
        <ImageIcon size={14} aria-hidden className="shrink-0" />
        <span className="truncate">{file.name}</span>
        <span className="text-muted">{described(file)}</span>
        <DownloadButton file={file} />
      </figcaption>
    </figure>
  );
}

/** A sound or a video: loaded when the person asks, played when they press play. */
function Playable({ file }: { file: ToolFile }) {
  const [wanted, setWanted] = useState(false);
  const { url, problem } = useFileUrl(file.fileId, wanted);
  const Icon = file.type === "video" ? Film : Music;
  const noun = file.type === "video" ? "video" : "sound";
  return (
    <figure className="tool-file" data-testid={`tool-${file.type}`}>
      <figcaption className="tool-file-head">
        <Icon size={14} aria-hidden className="shrink-0" />
        <span className="truncate">{file.name}</span>
        <span className="text-muted">{described(file)}</span>
        {!wanted && (
          <button
            type="button"
            className="act"
            aria-label={`Load the ${noun} ${file.name}`}
            onClick={() => setWanted(true)}
          >
            Load the {noun}
          </button>
        )}
        <DownloadButton file={file} />
      </figcaption>
      {problem && (
        <p role="alert" className="text-sm text-error">
          The {noun} could not be loaded: {problem}
        </p>
      )}
      {url &&
        (file.type === "video" ? (
          <video src={url} controls preload="metadata" className="tool-video">
            <track kind="captions" />
          </video>
        ) : (
          <audio src={url} controls preload="metadata" className="w-full" />
        ))}
    </figure>
  );
}

function FileCard({ file }: { file: ToolFile }) {
  return (
    <div className="tool-file" data-testid="tool-file">
      <div className="tool-file-head">
        <FileText size={14} aria-hidden className="shrink-0" />
        <span className="truncate">{file.name}</span>
        <span className="text-muted">{described(file)}</span>
        <DownloadButton file={file} />
      </div>
    </div>
  );
}

function Shown({ block }: { block: ContentBlock }) {
  switch (block.type) {
    case "image":
      return <Picture file={block} />;
    case "audio":
    case "video":
      return <Playable file={block} />;
    case "file":
      return <FileCard file={block} />;
    case "link": {
      // An address the tool named: never fetched here; a link only if it is a web one.
      const href = safeHref(block.uri);
      const name = block.title || block.name || block.uri;
      return (
        <p className="tool-link" data-testid="tool-link">
          <Link2 size={14} aria-hidden className="shrink-0" />
          {href ? (
            <a href={href} target="_blank" rel="noopener noreferrer">
              {name}
            </a>
          ) : (
            <span>{name}</span>
          )}
          <span className="text-muted wrap-anywhere">{block.uri}</span>
        </p>
      );
    }
    case "data":
      return (
        <details className="tool-data">
          <summary className="disclosure cursor-pointer text-muted hover:text-fg">Data</summary>
          <pre className="mt-1 max-h-64 overflow-auto whitespace-pre-wrap break-all rounded-plexus border border-line bg-soft p-2 font-mono text-[0.8125rem] leading-5">
            {JSON.stringify(block.data, null, 2)}
          </pre>
        </details>
      );
    case "unsupported":
      return (
        <p className="text-sm text-muted" data-testid="tool-not-kept">
          Not kept: {block.what}. {block.reason.charAt(0).toUpperCase() + block.reason.slice(1)}.
        </p>
      );
    default:
      return null;
  }
}

/** A call's blocks other than its words, in the order the tool returned them. */
export function ToolContent({ blocks }: { blocks: ContentBlock[] }) {
  const shown = blocks.filter((block) => block.type !== "text");
  if (!shown.length) return null;
  return (
    <div className="tool-content" data-testid="tool-content">
      {shown.map((block) => (
        <Shown key={block.id} block={block} />
      ))}
    </div>
  );
}
