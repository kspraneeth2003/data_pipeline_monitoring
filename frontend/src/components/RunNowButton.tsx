import { useState } from "react";
import { api } from "../lib/api";

export function RunNowButton({ checkId, onDone }: { checkId: string; onDone?: () => void }) {
  const [isRunning, setIsRunning] = useState(false);

  async function handleClick() {
    setIsRunning(true);
    try {
      await api.runCheck(checkId);
      onDone?.();
    } catch (error) {
      alert(`Run failed: ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setIsRunning(false);
    }
  }

  return (
    <button
      onClick={handleClick}
      disabled={isRunning}
      className="rounded-md border border-border px-3 py-1.5 text-sm font-medium text-zinc-700 transition-colors hover:bg-zinc-100 disabled:opacity-50 dark:text-zinc-200 dark:hover:bg-zinc-800"
    >
      {isRunning ? "Running…" : "Run now"}
    </button>
  );
}
