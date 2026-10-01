import { LogIn } from "lucide-react";

import { Mascot } from "./Mascot";

export function SignIn({
  message,
  unavailable,
}: {
  message: string | null;
  unavailable: string | null;
}) {
  return (
    <main className="flex min-h-full items-center justify-center p-6">
      <div className="flex max-w-md flex-col items-center gap-5 text-center">
        <Mascot pose={message || unavailable ? "curious" : "welcome"} />
        <h1 className="text-2xl font-semibold">Workbench</h1>
        <p className="text-muted">
          Chat with the models your Eugene install serves. Sign in with your Eugene account, or with
          Eugene&apos;s passphrase if you are its owner.
        </p>
        {message && (
          <p
            role="alert"
            className="rounded-plexus border border-warn-line bg-warn-bg px-3 py-2 text-warn"
          >
            {message}
          </p>
        )}
        {unavailable ? (
          <p
            role="alert"
            className="rounded-plexus border border-error-line bg-error-bg px-3 py-2 text-error"
          >
            {unavailable}
          </p>
        ) : (
          <a
            href="/signin"
            data-testid="sign-in"
            className="inline-flex items-center gap-2 rounded-plexus bg-accent px-5 py-2.5 font-medium text-on-accent hover:opacity-90"
          >
            <LogIn size={18} aria-hidden />
            Sign in with Eugene
          </a>
        )}
      </div>
    </main>
  );
}
