import { useState } from "react";
import { api } from "../lib/api";

export function EnabledToggle({
  checkId,
  enabled,
  onDone,
}: {
  checkId: string;
  enabled: boolean;
  onDone?: () => void;
}) {
  const [busy, setBusy] = useState(false);

  async function toggle() {
    setBusy(true);
    try {
      await api.updateCheck(checkId, { enabled: !enabled });
      onDone?.();
    } catch (error) {
      alert(`Update failed: ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <button
      onClick={toggle}
      disabled={busy}
      className="rounded-md border border-border px-3 py-1.5 text-sm font-medium text-zinc-700 transition-colors disabled:opacity-50 dark:text-zinc-200 dark:hover:bg-zinc-800"
    >
      {enabled ? "Disable" : "Enable"}
    </button>
  );
}
