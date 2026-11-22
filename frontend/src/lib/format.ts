/** Small display helpers shared by the documents and chat components. */

/** 1843220 -> "1.8 MB". */
export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

/** (108, 108) -> "p. 108"; (12, 14) -> "pp. 12-14". */
export function formatPages(start: number, end: number = start): string {
  return start === end ? `p. ${start}` : `pp. ${start}-${end}`
}

/** A score to 3 decimals, or a dash when the backend sent null. */
export function formatScore(score: number | null): string {
  return score === null ? "—" : score.toFixed(3)
}

/** ["Chapter XIV-H", "150V. Extension"] -> "Chapter XIV-H > 150V. Extension". */
export function formatSection(path: string[]): string {
  return path.join(" > ")
}

/** An ISO timestamp as a short local date and time. */
export function formatDateTime(iso: string): string {
  return new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })
}
