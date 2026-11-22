/**
 * One assistant answer: text with clickable [n] citations, its sources,
 * the separate "general context" note, timings, and the chunks panel.
 */
import { BookOpen, Info, Lightbulb } from "lucide-react"
import { Fragment, useState, type ReactNode } from "react"

import { RetrievedChunksPanel } from "@/components/chat/RetrievedChunksPanel"
import type { AskResponse, Citation } from "@/lib/api"
import { formatPages, formatSection } from "@/lib/format"
import { cn } from "@/lib/utils"

/** How long a clicked source stays highlighted. */
const HIGHLIGHT_MS = 1500

/** The DOM id of source n in this answer (unique per answer). */
const sourceId = (answerId: string, n: number) => `${answerId}-source-${n}`

/**
 * Split the answer on "[n]" markers and turn each valid one into a button.
 *
 * Only numbers present in `citations` become links; the backend already
 * removed markers that point to no retrieved chunk.
 */
function renderWithCitations(text: string, citations: Citation[], onCite: (n: number) => void): ReactNode[] {
  const valid = new Set(citations.map((c) => c.n))
  // split() with a capture group keeps the captured numbers in the result:
  // "a [1] b" -> ["a ", "1", " b"], so odd indexes are the numbers.
  return text.split(/\[(\d+)\]/g).map((part, i) => {
    if (i % 2 === 0) return <Fragment key={i}>{part}</Fragment>
    const n = Number(part)
    if (!valid.has(n)) return <Fragment key={i}>[{part}]</Fragment>
    return (
      <button
        key={i}
        type="button"
        onClick={() => onCite(n)}
        className="mx-0.5 rounded bg-primary/10 px-1 align-baseline font-mono text-xs font-semibold text-primary hover:bg-primary/20"
        aria-label={`Go to source ${n}`}
      >
        [{n}]
      </button>
    )
  })
}

/** The numbered list of sources behind the answer. */
function SourceList({ answerId, citations, highlighted }: { answerId: string; citations: Citation[]; highlighted: number | null }) {
  return (
    <div className="space-y-1.5">
      <p className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
        <BookOpen className="size-3.5" /> Sources
      </p>
      <ol className="space-y-1">
        {citations.map((c) => (
          <li
            key={c.n}
            id={sourceId(answerId, c.n)}
            className={cn(
              "rounded px-2 py-1 text-xs transition-colors",
              highlighted === c.n ? "bg-primary/15" : "bg-muted/60",
            )}
          >
            <span className="font-mono font-semibold text-primary">[{c.n}]</span>{" "}
            <span className="break-all font-medium">{c.filename}</span>
            <span className="text-muted-foreground"> · {formatPages(c.page_start, c.page_end)}</span>
            {c.section_path.length > 0 && (
              <span className="text-muted-foreground"> · {formatSection(c.section_path)}</span>
            )}
          </li>
        ))}
      </ol>
    </div>
  )
}

/** Time per pipeline stage, in seconds, in one small line. */
function TimingsLine({ timings }: { timings: AskResponse["timings_ms"] }) {
  const s = (ms: number) => `${(ms / 1000).toFixed(1)} s`
  return (
    <p className="text-[11px] text-muted-foreground">
      {s(timings.total)} total · retrieval {s(timings.retrieval)} · rerank {s(timings.rerank)} · generation{" "}
      {s(timings.generation)}
    </p>
  )
}

/** A full answer card for one AskResponse. */
export function AnswerCard({ response }: { response: AskResponse }) {
  const [highlighted, setHighlighted] = useState<number | null>(null)
  const answerId = response.request_id

  function goToSource(n: number) {
    document.getElementById(sourceId(answerId, n))?.scrollIntoView({ behavior: "smooth", block: "nearest" })
    setHighlighted(n)
    window.setTimeout(() => setHighlighted(null), HIGHLIGHT_MS)
  }

  return (
    <div className="space-y-3">
      {response.answerable ? (
        <p dir="auto" className="whitespace-pre-wrap text-sm leading-relaxed">
          {renderWithCitations(response.answer, response.citations, goToSource)}
        </p>
      ) : (
        // A refusal looks different on purpose: the user must not mistake it for an answer.
        <div className="flex gap-2 rounded-md border border-dashed bg-muted/50 p-3 text-sm text-muted-foreground">
          <Info className="mt-0.5 size-4 shrink-0" />
          <p dir="auto" className="whitespace-pre-wrap">{response.answer}</p>
        </div>
      )}

      {response.citations.length > 0 && (
        <SourceList answerId={answerId} citations={response.citations} highlighted={highlighted} />
      )}

      {response.general_context && (
        <div className="rounded-md border border-amber-200 bg-amber-50 p-3 text-sm">
          <p className="mb-1 flex items-center gap-1.5 text-xs font-medium text-amber-800">
            <Lightbulb className="size-3.5" /> General context (not from your documents, not cited)
          </p>
          <p dir="auto" className="whitespace-pre-wrap text-amber-950">{response.general_context}</p>
        </div>
      )}

      <RetrievedChunksPanel
        chunks={response.retrieved_chunks}
        citedNumbers={new Set(response.citations.map((c) => c.n))}
      />
      <TimingsLine timings={response.timings_ms} />
    </div>
  )
}
