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
      className="whitespace-nowrap border border-border px-3 py-1.5 text-sm font-medium text-zinc-700 transition-colors disabled:opacity-50 dark:text-zinc-200 dark:hover:border-accent dark:hover:bg-zinc-800 dark:hover:text-accent"
    >
      {isRunning ? "Running…" : "Run now"}
    </button>
  );
}
