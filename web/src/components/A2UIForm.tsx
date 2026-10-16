import { useId, useState, type ReactNode } from "react";
import { post } from "../lib/api";
import {
  object,
  resolve,
  surfacePath,
  words,
  type Json,
  type ObjectValue,
  type Surface,
} from "../lib/interactions";

export function A2UIForm({
  chat,
  surface,
  disabled,
  onAction,
  onChanged,
}: {
  chat: string;
  surface: Surface;
  disabled: boolean;
  onAction: (name: string, args: ObjectValue) => Promise<unknown>;
  onChanged: () => void;
}) {
  const prefix = useId();
  const [drafts, setDrafts] = useState<Record<string, string | boolean>>({});
  const [busy, setBusy] = useState(false);
  const [editRevision, setEditRevision] = useState<number | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const view = surface.view;
  const data = view.dataModel ?? {};
  const nodes = new Map((view.components as ObjectValue[]).map((c) => [String(c.id), c]));
  const text = (v: Json | undefined) => String(resolve(v, data) ?? "");
  const dirty = Object.keys(drafts).length > 0;
  const reachable = new Set<string>();
  const remaining = ["root"];
  while (remaining.length) {
    const id = remaining.pop()!;
    if (reachable.has(id)) continue;
    reachable.add(id);
    const node = nodes.get(id);
    if (node?.child) remaining.push(String(node.child));
    else if (Array.isArray(node?.children)) remaining.push(...node.children.map(String));
  }
  const checks = [...nodes.values()]
    .filter((c) => reachable.has(String(c.id)))
    .flatMap((c) => (c.checks as ObjectValue[] | undefined) ?? []);
  let failures: string[];
  try {
    failures = checks
      .filter((c) => resolve(c.condition, data) !== true)
      .map((c) => text(c.message));
  } catch (error) {
    return <p role="alert">{words(error)}</p>;
  }

  async function save() {
    setBusy(true);
    setProblem(null);
    try {
      let revision = editRevision ?? surface.revision;
      for (const [id, value] of Object.entries(drafts)) {
        const result = await post<{ revision: number }>(
          `${surfacePath(chat, surface)}/a2ui/input`,
          {
            revision,
            componentId: id,
            value,
          },
        );
        revision = result.revision;
      }
      setDrafts({});
      setEditRevision(null);
      onChanged();
    } catch (error) {
      setProblem(words(error));
    } finally {
      setBusy(false);
    }
  }

  function caption(id: string, depth = 0): string {
    const node = nodes.get(id);
    if (depth > 32 || !node) throw new Error("This button is incomplete.");
    if (node.component === "Text") return text(node.text);
    if (node.component === "Divider") return " ";
    if (["Card", "Row", "Column"].includes(String(node.component)))
      return (node.child ? [String(node.child)] : (node.children as string[]))
        .map((id) => caption(id, depth + 1))
        .join(" ");
    throw new Error("A button label cannot contain another form control.");
  }
  function render(id: string, depth = 0, path = "root"): ReactNode {
    if (depth > 32) throw new Error("This form is too deep.");
    const c = nodes.get(id);
    if (!c) throw new Error("This form is incomplete.");
    const child = (key: Json | undefined, index = 0) =>
      render(String(key), depth + 1, `${path}-${index}-${String(key)}`);
    switch (c.component) {
      case "Text":
        return (
          <span className={`a2ui-text a2ui-text--${String(c.variant ?? "body")}`}>
            {text(c.text)}
          </span>
        );
      case "Divider":
        return <hr aria-orientation={c.axis === "vertical" ? "vertical" : "horizontal"} />;
      case "Card":
        return <div className="rounded-lg border border-line p-3">{child(c.child)}</div>;
      case "Row":
      case "Column":
        return (
          <div
            className={`a2ui-layout a2ui-${String(c.component).toLowerCase()} a2ui-align-${String(c.align ?? "stretch")} a2ui-justify-${String(c.justify ?? "start")}`}
          >
            {(c.children as string[]).map((key, index) => (
              <div key={`${key}-${index}`}>{child(key, index)}</div>
            ))}
          </div>
        );
      case "TextField":
      case "CheckBox": {
        const writable = typeof object(c.value).path === "string";
        const value = Object.hasOwn(drafts, id) ? drafts[id] : resolve(c.value, data);
        const change = (v: string | boolean) => {
          setEditRevision((old) => old ?? surface.revision);
          setDrafts((old) => ({ ...old, [id]: v }));
        };
        const field = `${prefix}-${path}`;
        return (
          <div className="flex flex-col gap-2">
            <label htmlFor={field}>{text(c.label)}</label>
            {c.component === "CheckBox" ? (
              <input
                id={field}
                type="checkbox"
                checked={value === true}
                disabled={disabled || busy || !writable}
                onChange={(e) => change(e.target.checked)}
              />
            ) : c.variant === "longText" ? (
              <textarea
                id={field}
                className="input"
                value={String(value ?? "")}
                maxLength={8192}
                disabled={disabled || busy || !writable}
                onChange={(e) => change(e.target.value)}
              />
            ) : (
              <input
                id={field}
                className="input"
                type={
                  c.variant === "obscured" ? "password" : c.variant === "number" ? "number" : "text"
                }
                value={String(value ?? "")}
                maxLength={8192}
                disabled={disabled || busy || !writable}
                onChange={(e) => change(e.target.value)}
              />
            )}
          </div>
        );
      }
      case "Button": {
        const event = object(object(c.action).event);
        return (
          <span className="flex flex-col gap-1">
            <button
              type="button"
              className={`btn ${c.variant === "primary" ? "btn-primary" : ""}`}
              disabled={disabled || busy || dirty || failures.length > 0 || !event.name}
              onClick={async () => {
                setBusy(true);
                setProblem(null);
                try {
                  const context = Object.fromEntries(
                    Object.entries(object(event.context)).map(([k, v]) => [k, resolve(v, data)]),
                  );
                  await onAction("a2ui/action", {
                    version: "v0.9.1",
                    action: {
                      surfaceId: String(view.surfaceId),
                      sourceComponentId: id,
                      name: String(event.name),
                      timestamp: new Date().toISOString(),
                      context,
                    },
                  });
                } catch (error) {
                  setProblem(words(error));
                } finally {
                  setBusy(false);
                }
              }}
            >
              {caption(String(c.child))}
            </button>
            {!event.name && <span>This button does not offer a supported server action.</span>}
          </span>
        );
      }
      default:
        throw new Error("This form uses an unsupported component.");
    }
  }
  let contents: ReactNode;
  try {
    contents = render("root");
  } catch (error) {
    return <p role="alert">{words(error)}</p>;
  }
  return (
    <div className="flex flex-col gap-3" aria-label="Interactive form">
      {contents}
      {dirty && (
        <button className="btn self-start" disabled={disabled || busy} onClick={() => void save()}>
          Save changes
        </button>
      )}
      {dirty && (
        <button
          className="btn self-start"
          disabled={busy}
          onClick={() => {
            setDrafts({});
            setEditRevision(null);
            setProblem(null);
            onChanged();
          }}
        >
          Discard unsaved changes
        </button>
      )}
      {dirty && <p role="status">Save changed fields before submitting this form.</p>}
      {failures.length > 0 && (
        <ul aria-label="Form requirements">
          {failures.map((message, i) => (
            <li key={i}>{message}</li>
          ))}
        </ul>
      )}
      {problem && <p role="alert">{problem}</p>}
    </div>
  );
}
