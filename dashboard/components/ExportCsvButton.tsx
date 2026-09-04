"use client";

/** Downloads a pre-built CSV string as a file -- purely client-side, no
 * server round trip, so it exports exactly whatever's currently filtered/
 * sorted on screen. */
export function ExportCsvButton({ csv, filename }: { csv: string; filename: string }) {
  function handleClick() {
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
  }

  return (
    <button
      type="button"
      onClick={handleClick}
      className="rounded-lg border border-[color:var(--border-hairline)] bg-[color:var(--surface-1)] px-3 py-1.5 text-sm font-medium text-[color:var(--text-secondary)] hover:bg-[color:var(--gridline)] hover:text-[color:var(--text-primary)]"
    >
      Export CSV
    </button>
  );
}
