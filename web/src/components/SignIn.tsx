import { CircleAlert, Info, LogIn, TriangleAlert } from "lucide-react";

import { Mascot } from "./Mascot";

/** What App.tsx says when a person chooses Sign out: an acknowledgement, not a
 * warning. Every other message is Eugene's own, saying why a sign-in ended. */
export const SIGNED_OUT_BY_YOU = "You signed out.";

export function SignIn({
  message,
  unavailable,
}: {
  message: string | null;
  unavailable: string | null;
}) {
  return (
    <main className="flex min-h-full items-center justify-center p-6">
      <div className="flex w-full max-w-md flex-col items-center gap-5 text-center">
        <Mascot pose={message || unavailable ? "curious" : "welcome"} />
        <h1 className="text-2xl font-semibold tracking-[-0.015em]">Workbench</h1>
        <div className="flex flex-col gap-1.5">
          <p className="text-balance">Chat with the models your Eugene install serves.</p>
          <p className="text-balance text-sm text-muted">
            Sign in with your Eugene account, or with Eugene&apos;s passphrase if you are its owner.
          </p>
        </div>
        {message &&
          (message === SIGNED_OUT_BY_YOU ? (
            <p role="status" className="notice w-full text-left">
              <Info size={16} aria-hidden />
              {message}
            </p>
          ) : (
            <p role="alert" className="notice notice--warn w-full text-left">
              <TriangleAlert size={16} aria-hidden />
              {message}
            </p>
          ))}
        {unavailable ? (
          <>
            <p role="alert" className="notice notice--error w-full text-left">
              <CircleAlert size={16} aria-hidden />
              {unavailable}
            </p>
            <button
              type="button"
              onClick={() => window.location.reload()}
              className="btn min-h-11 px-5"
            >
              Check again
            </button>
          </>
        ) : (
          <a
            href="/signin"
            data-testid="sign-in"
            className="btn btn-primary min-h-11 px-5 text-base"
          >
            <LogIn size={18} aria-hidden />
            Sign in with Eugene
          </a>
        )}
      </div>
    </main>
  );
}
