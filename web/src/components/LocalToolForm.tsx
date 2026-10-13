import { CircleAlert, TriangleAlert } from "lucide-react";
import { useId, useState } from "react";

import { post } from "../lib/api";
import type { ToolServer } from "../lib/types";

/** The field a refusal is about, so it can be marked and tied to the alert. */
type Field = "args" | "environment";

export function LocalToolForm({ onAdded }: { onAdded: (server: ToolServer) => void }) {
  const uid = useId();
  const [name, setName] = useState("");
  const [command, setCommand] = useState("");
  const [args, setArgs] = useState("[]");
  const [environment, setEnvironment] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<{ message: string; field: Field | null } | null>(null);
  const alertId = `${uid}-problem`;
  /** What marks a field a refusal named: the danger edge, and the alert it is described by. */
  const marked = (field: Field) =>
    problem?.field === field
      ? ({ "aria-invalid": true, "aria-describedby": `${uid}-${field}-help ${alertId}` } as const)
      : { "aria-describedby": `${uid}-${field}-help` };
  return (
    <form
      aria-labelledby={`${uid}-title`}
      className="card card--add"
      onSubmit={async (event) => {
        event.preventDefault();
        setProblem(null);
        let parsedArgs: unknown;
        try {
          parsedArgs = JSON.parse(args);
        } catch {
          setProblem({
            message:
              'Arguments are not valid JSON. Write a JSON array of strings, for example ["-m", "my_server"].',
            field: "args",
          });
          return;
        }
        if (!Array.isArray(parsedArgs) || parsedArgs.some((a) => typeof a !== "string")) {
          setProblem({
            message: 'Arguments must be a JSON array of strings, for example ["-m", "my_server"].',
            field: "args",
          });
          return;
        }
        let parsedEnvironment: unknown;
        try {
          parsedEnvironment = JSON.parse(environment || "{}");
        } catch {
          setProblem({
            message:
              'Environment values are not valid JSON. Write a JSON object, for example {"API_TOKEN":"your token"}.',
            field: "environment",
          });
          return;
        }
        if (
          typeof parsedEnvironment !== "object" ||
          parsedEnvironment === null ||
          Array.isArray(parsedEnvironment)
        ) {
          setProblem({
            message:
              'Environment values must be a JSON object, for example {"API_TOKEN":"your token"}.',
            field: "environment",
          });
          return;
        }
        setBusy(true);
        try {
          const server = await post<ToolServer>("/api/tools/servers", {
            transport: "stdio",
            name,
            command,
            args: parsedArgs,
            environment: parsedEnvironment,
          });
          onAdded(server);
          setName("");
          setCommand("");
          setArgs("[]");
          setEnvironment("");
        } catch (error) {
          setProblem({
            message: error instanceof Error ? error.message : String(error),
            field: null,
          });
        } finally {
          setBusy(false);
        }
      }}
    >
      <h3 id={`${uid}-title`} className="title-card">
        Add a local server (owner only)
      </h3>
      <p className="help">
        Install the server and its dependencies where Workbench&apos;s OS account can run them.
        Saving this form does not start it.
      </p>
      <div className="notice notice--warn">
        <TriangleAlert size={16} aria-hidden />
        <div>
          <span className="notice-title">Runs with Workbench&apos;s access</span>
          Only add programs you trust. They run with Workbench&apos;s file access, including
          everyone&apos;s chats and the app&apos;s credentials. A separate working folder does not
          isolate those files.
        </div>
      </div>
      <div className="field">
        <label htmlFor={`${uid}-name`} className="field-label">
          Local server name
        </label>
        <input
          id={`${uid}-name`}
          required
          maxLength={80}
          value={name}
          onChange={(e) => setName(e.target.value)}
          className="control"
        />
      </div>
      <div className="field">
        <label htmlFor={`${uid}-command`} className="field-label">
          Full executable path
        </label>
        <input
          id={`${uid}-command`}
          required
          maxLength={4096}
          value={command}
          onChange={(e) => setCommand(e.target.value)}
          className="control"
          spellCheck={false}
          aria-describedby={`${uid}-command-help`}
        />
        <p id={`${uid}-command-help`} className="help">
          Use an executable such as python.exe or node.exe on Windows, or /usr/bin/python3 on Linux.
          Put the script path in Arguments. Shell command strings are not run.
        </p>
      </div>
      <div className="field">
        <label htmlFor={`${uid}-args`} className="field-label">
          Arguments (JSON array)
        </label>
        <textarea
          id={`${uid}-args`}
          rows={3}
          value={args}
          maxLength={32768}
          onChange={(e) => setArgs(e.target.value)}
          className={`control font-mono ${problem?.field === "args" ? "border-error-line border-2" : ""}`}
          spellCheck={false}
          {...marked("args")}
        />
        <p id={`${uid}-args-help`} className="help">
          Each string is one argument, for example [&quot;-m&quot;, &quot;my_server&quot;]. Use
          environment values for secrets; process lists can show arguments.
        </p>
      </div>
      <div className="field">
        <label htmlFor={`${uid}-environment`} className="field-label">
          Environment values (JSON object, optional)
        </label>
        <input
          id={`${uid}-environment`}
          type="password"
          autoComplete="new-password"
          value={environment}
          maxLength={65536}
          onChange={(e) => setEnvironment(e.target.value)}
          className={`control ${problem?.field === "environment" ? "border-error-line border-2" : ""}`}
          {...marked("environment")}
        />
        <p id={`${uid}-environment-help`} className="help">
          For example, {'{"API_TOKEN":"your token"}'}. Stored values are never shown again.
        </p>
      </div>
      {problem && (
        <p id={alertId} role="alert" className="notice notice--error">
          <CircleAlert size={16} aria-hidden />
          {problem.message}
        </p>
      )}
      <button disabled={busy} type="submit" className="btn btn-primary self-start">
        Save local server
      </button>
    </form>
  );
}
