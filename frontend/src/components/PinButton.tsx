import { useState } from "react";
import { api } from "../lib/api";

/**
 * Pin a check to the top of its stage.
 *
 * The pin is workspace-wide, not personal - there is no user table to hang a
 * per-viewer pin off - so the title says so. A control that silently changes
 * what a colleague sees is worse than one that admits it.
 */
export function PinButton({
  checkId,
  pinned,
  checkName,
  onDone,
}: {
  checkId: string;
  pinned: boolean;
  checkName?: string;
  onDone?: () => void;
}) {
  const [busy, setBusy] = useState(false);

  async function toggle() {
    setBusy(true);
    try {
      await api.setCheckPin(checkId, !pinned);
      onDone?.();
    } catch (error) {
      alert(`Could not update pin: ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setBusy(false);
    }
  }

  const label = pinned
    ? `Unpin ${checkName ?? "this check"} — pinned for everyone`
    : `Pin ${checkName ?? "this check"} to the top, for everyone`;

  return (
    <button
      type="button"
      onClick={toggle}
      disabled={busy}
      title={label}
      aria-label={label}
      aria-pressed={pinned}
      className={`px-1 text-base leading-none transition-colors disabled:opacity-40 ${
        pinned ? "text-accent" : "text-zinc-600 hover:text-zinc-400"
      }`}
    >
      {pinned ? "★" : "☆"}
    </button>
  );
}
