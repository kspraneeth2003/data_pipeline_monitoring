import { useState } from "react";

export function DeleteButton({
  confirmMessage,
  onDelete,
}: {
  confirmMessage: string;
  onDelete: () => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);

  async function handleClick() {
    if (!confirm(confirmMessage)) return;
    setBusy(true);
    try {
      await onDelete();
    } catch (error) {
      alert(`Delete failed: ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <button
      onClick={handleClick}
      disabled={busy}
      className="rounded-md border border-red-300 px-3 py-1.5 text-sm font-medium text-red-700 hover:bg-red-50 disabled:opacity-50 dark:border-red-900 dark:text-red-400 dark:hover:bg-red-950"
    >
      {busy ? "Deleting…" : "Delete"}
    </button>
  );
}
