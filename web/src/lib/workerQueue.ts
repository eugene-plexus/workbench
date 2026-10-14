/**
 * Work the page must never wait on, in a worker that can be stopped
 * (workbench-rich-content-roadmap.md RC1, RC2). Formulas and diagrams each
 * have one: a job runs alone, one that takes longer than its limit is
 * stopped by ending the worker, says so, and a new worker takes the next one.
 * A job nobody on the page still shows is skipped when its turn comes. A
 * worker that would not start, or stopped, fails the job for now (`again`):
 * shown again, it is tried again.
 *
 * The worker says `{ ready: true }` once it has loaded, then answers each
 * `{ id, ...request }` with `{ id, ...reply }`.
 */

/** What a job came to; `again` when the next try may differ. */
export type Outcome<R> = R | { error: string; again: true };

export interface Queue<Q, R> {
  /** The job's outcome, or null when it was skipped because nobody wants it. */
  run(request: Q, wanted?: () => boolean): Promise<Outcome<R> | null>;
}

/** How the queue names its work in what it says. */
export type Words = {
  /** "the formula worker" */
  worker: string;
  /** "formulas cannot be laid out in this browser" */
  unavailable: string;
  /** "to lay out", as in "it took longer than 1.5 s to lay out" */
  doing: string;
};

type Job<Q, R> = {
  id: number;
  request: Q;
  wanted?: () => boolean;
  resolve: (result: Outcome<R> | null) => void;
};

type Reply = { ready?: boolean; id?: number; [key: string]: unknown };

function seconds(ms: number): string {
  return `${(ms / 1000).toLocaleString("en")} s`;
}

/**
 * A queue on workers made by `make`. `read` makes a reply into the job's
 * result; `limits.job` is how long one job may take once the worker is
 * running, `limits.start` how long the worker may take to start.
 */
export function workerQueue<Q extends object, R>(
  make: () => Worker,
  limits: { job: number; start: number },
  words: Words,
  read: (reply: Reply) => R,
): Queue<Q, R> {
  const queue: Job<Q, R>[] = [];
  let worker: Worker | null = null;
  let ready = false;
  let running: Job<Q, R> | null = null;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let next = 1;

  function end(): void {
    clearTimeout(timer);
    worker?.terminate();
    worker = null;
    ready = false;
  }

  /**
   * The running job fails with `reason` (`again` if the worker, not the job,
   * was at fault); a new worker takes the rest.
   */
  function failRunning(reason: string, again: boolean): void {
    const job = running;
    running = null;
    end();
    // Not the worker's fault: the job's own failure, as the worker would say it.
    job?.resolve(again ? { error: reason, again } : read({ error: reason }));
    pump();
  }

  /** The worker never started: every job waiting fails with `reason`, for now. */
  function failStart(reason: string): void {
    end();
    for (const job of queue.splice(0)) job.resolve({ error: reason, again: true });
  }

  function start(): void {
    try {
      worker = make();
    } catch (error) {
      failStart(`${words.unavailable}: ${String(error)}`);
      return;
    }
    // A worker that was ended may still have a message or an error on its way:
    // it speaks for nothing now.
    const mine = worker;
    worker.onmessage = (event: MessageEvent<Reply>) => {
      if (worker !== mine) return;
      const reply = event.data;
      if (reply.ready) {
        clearTimeout(timer);
        ready = true;
        pump();
        return;
      }
      const job = running;
      if (!job || reply.id !== job.id) return;
      clearTimeout(timer);
      running = null;
      job.resolve(read(reply));
      pump();
    };
    worker.onerror = (event: ErrorEvent) => {
      event.preventDefault();
      if (worker !== mine) return;
      const reason = `${words.worker} stopped: ${event.message || "no reason was given"}`;
      if (running) failRunning(reason, true);
      else failStart(reason);
    };
    timer = setTimeout(
      () => failStart(`${words.worker} did not start within ${seconds(limits.start)}`),
      limits.start,
    );
  }

  function pump(): void {
    if (running) return;
    if (!worker) {
      if (queue.length) start();
      return;
    }
    if (!ready) return;
    let job = queue.shift();
    while (job?.wanted && !job.wanted()) {
      job.resolve(null);
      job = queue.shift();
    }
    if (!job) return;
    running = job;
    worker.postMessage({ id: job.id, ...job.request });
    timer = setTimeout(
      () => failRunning(`it took longer than ${seconds(limits.job)} ${words.doing}`, false),
      limits.job,
    );
  }

  return {
    run(request, wanted) {
      return new Promise((resolve) => {
        queue.push({ id: next++, request, wanted, resolve });
        pump();
      });
    },
  };
}
