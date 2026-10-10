import { useState } from "react";

import { post } from "../lib/api";
import type { ToolServer } from "../lib/types";

const inputClass = "rounded-plexus border border-line bg-soft p-2";

export function LocalToolForm({ onAdded }: { onAdded: (server: ToolServer) => void }) {
  const [name, setName] = useState("");
  const [command, setCommand] = useState("");
  const [args, setArgs] = useState("[]");
  const [environment, setEnvironment] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  return (
    <form
      className="flex flex-col gap-3 rounded-plexus border border-line p-3"
      onSubmit={async (event) => {
        event.preventDefault();
        setProblem(null);
        let parsedArgs: unknown;
        try {
          parsedArgs = JSON.parse(args);
        } catch {
          setProblem(
            'Arguments are not valid JSON. Write a JSON array of strings, for example ["-m", "my_server"].',
          );
          return;
        }
        if (!Array.isArray(parsedArgs) || parsedArgs.some((a) => typeof a !== "string")) {
          setProblem('Arguments must be a JSON array of strings, for example ["-m", "my_server"].');
          return;
        }
        let parsedEnvironment: unknown;
        try {
          parsedEnvironment = JSON.parse(environment || "{}");
        } catch {
          setProblem(
            'Environment values are not valid JSON. Write a JSON object, for example {"API_TOKEN":"your token"}.',
          );
          return;
        }
        if (
          typeof parsedEnvironment !== "object" ||
          parsedEnvironment === null ||
          Array.isArray(parsedEnvironment)
        ) {
          setProblem(
            'Environment values must be a JSON object, for example {"API_TOKEN":"your token"}.',
          );
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
          setProblem(error instanceof Error ? error.message : String(error));
        } finally {
          setBusy(false);
        }
      }}
    >
      <h2 className="font-semibold">Add a local server (owner only)</h2>
      <p className="text-sm text-muted">
        Install the server and its dependencies on Workbench&apos;s machine first, where
        Workbench&apos;s OS account can run them. Saving this form does not start it.
      </p>
      <p className="text-sm text-muted">
        Only add programs you trust. They run with Workbench&apos;s file access, including
        everyone&apos;s chats and the app&apos;s credentials. A separate working folder does not
        isolate those files.
      </p>
      <label className="flex flex-col gap-1 text-sm">
        Local server name
        <input
          required
          maxLength={80}
          value={name}
          onChange={(e) => setName(e.target.value)}
          className={inputClass}
        />
      </label>
      <label className="flex flex-col gap-1 text-sm">
        Full executable path
        <input
          required
          maxLength={4096}
          value={command}
          onChange={(e) => setCommand(e.target.value)}
          className={inputClass}
          spellCheck={false}
        />
      </label>
      <p className="text-sm text-muted">
        Use an executable such as python.exe or node.exe on Windows, or /usr/bin/python3 on Linux.
        Put the script path in Arguments. Shell command strings are not run.
      </p>
      <label className="flex flex-col gap-1 text-sm">
        Arguments (JSON array)
        <textarea
          rows={3}
          value={args}
          maxLength={32768}
          onChange={(e) => setArgs(e.target.value)}
          className={inputClass}
          spellCheck={false}
        />
      </label>
      <p className="text-sm text-muted">
        Each string is one argument, for example [&quot;-m&quot;, &quot;my_server&quot;]. Use
        environment values for secrets; process lists can show arguments.
      </p>
      <label className="flex flex-col gap-1 text-sm">
        Environment values (JSON object, optional)
        <input
          type="password"
          autoComplete="new-password"
          value={environment}
          maxLength={65536}
          onChange={(e) => setEnvironment(e.target.value)}
          className={inputClass}
        />
      </label>
      <p className="text-sm text-muted">
        For example, {'{"API_TOKEN":"your token"}'}. Stored values are never shown again. Each
        server gets a working and cache folder inside Workbench&apos;s data directory.
      </p>
      {problem && (
        <p role="alert" className="text-error">
          {problem}
        </p>
      )}
      <button
        disabled={busy}
        type="submit"
        className="self-end rounded-plexus bg-accent px-3 py-2 text-on-accent"
      >
        Save local server
      </button>
    </form>
  );
}
