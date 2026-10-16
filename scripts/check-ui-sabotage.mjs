/* global process, console */
// Mutate copies only. A parse/import failure does not prove a guard is observed.
import {cpSync, mkdtempSync, readFileSync, writeFileSync, rmSync, symlinkSync, unlinkSync, existsSync} from "node:fs";
import {tmpdir} from "node:os";
import {dirname, resolve, join} from "node:path";
import {fileURLToPath} from "node:url";
import {spawnSync} from "node:child_process";
const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const web = join(root,"web");
const cases = [
  ["frame source", "components/MCPApp.tsx", "event.source !== frame.current?.contentWindow ||", "false ||"],
  ["message origin", "components/MCPApp.tsx", 'event.origin !== "null" ||', "false ||"],
  ["mount channel", "components/MCPApp.tsx", "event.data?.channel !== channel", "false"],
  ["duplicate request", "components/MCPApp.tsx", "if (seen.has(id))", "if (false)"],
  ["form revision", "components/A2UIForm.tsx", "editRevision ?? surface.revision", "surface.revision"],
  ["saved continuation", "lib/agui.ts", "{ parentRunId: previous.runId }", '{ parentRunId: "wrong-run" }'],
];
const only = process.argv.slice(2);
const selected = cases.filter(c => !only.length || only.includes(c[0]));
if (!selected.length) throw new Error("No sabotage cases selected");
const originals = new Map(selected.map(([,file])=>[file,readFileSync(join(web,"src",file))]));
const temporary = mkdtempSync(join(tmpdir(),"workbench-ui-sabotage-"));
try {
  symlinkSync(join(web,"node_modules"),join(temporary,"node_modules"),process.platform === "win32" ? "junction" : "dir");
  cpSync(join(web,"src"),join(temporary,"src"),{recursive:true});
  for (const file of ["package.json","tsconfig.json","vite.config.ts"]) cpSync(join(web,file),join(temporary,file));
  const run = () => spawnSync(process.execPath,[join(web,"node_modules/vitest/vitest.mjs"),"run","src/lib/interactions.test.tsx","--reporter=dot"],{cwd:temporary,encoding:"utf8",timeout:60000});
  const control = run();
  if (control.status !== 0) throw new Error(control.stdout+control.stderr);
  console.log("PASS unmodified UI control");
  for (const [label,file,anchor,replacement] of selected) {
    const path=join(temporary,"src",file), original=originals.get(file).toString("utf8");
    if (original.split(anchor).length !== 2) throw new Error(`Ambiguous anchor: ${label}`);
    try {
      writeFileSync(path,original.replace(anchor,replacement));
      const result=run();
      if (result.status !== 1 || !(result.stdout+result.stderr).includes("AssertionError")) throw new Error(`Missed ${label}:\n${result.stdout}\n${result.stderr}`);
      console.log(`PASS caught ${label}`);
    } finally {writeFileSync(path,originals.get(file));}
  }
} finally {
  if (dirname(resolve(temporary)) !== resolve(tmpdir())) throw new Error("Unexpected temporary path");
  if (existsSync(join(temporary,"node_modules"))) unlinkSync(join(temporary,"node_modules"));
  rmSync(temporary,{recursive:true,force:true});
  for (const [file,bytes] of originals) if (!readFileSync(join(web,"src",file)).equals(bytes)) throw new Error("Working source changed");
}
