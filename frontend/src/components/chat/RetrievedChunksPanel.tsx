/**
 * Collapsible "Retrieved chunks (N)" list under an answer.
 *
 * It shows exactly what the model was given, with both scores the backend
 * computed: cosine similarity (how close the chunk's embedding is to the
 * question's) and the reranker score (a cross-encoder reading question and
 * chunk together, which decides the final order).
 */
import { ChevronDown } from "lucide-react"
import { useState } from "react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import { Progress } from "@/components/ui/progress"
import type { RetrievedChunk } from "@/lib/api"
import { formatPages, formatScore, formatSection } from "@/lib/format"
import { cn } from "@/lib/utils"

/** A label, the score as a number, and a bar (scores below 0 show as empty). */
function ScoreBar({ label, score, hint }: { label: string; score: number | null; hint: string }) {
  const percent = score === null ? 0 : Math.max(0, Math.min(1, score)) * 100
  return (
    <div className="space-y-1" title={hint}>
      <div className="flex justify-between text-xs">
        <span className="text-muted-foreground">{label}</span>
        <span className="font-mono tabular-nums">{formatScore(score)}</span>
      </div>
      <Progress value={percent} className="h-1.5" />
    </div>
  )
}

/** One chunk: where it came from, both scores, and its text (expandable). */
function ChunkItem({ chunk, cited }: { chunk: RetrievedChunk; cited: boolean }) {
  const [expanded, setExpanded] = useState(false)
  return (
    <li className="space-y-2 rounded-md border bg-background p-3">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <Badge variant="secondary" className="font-mono">
          [{chunk.n}]
        </Badge>
        <span className="break-all font-medium">{chunk.filename}</span>
        <span className="text-muted-foreground">{formatPages(chunk.page_start)}</span>
        {cited && <Badge variant="outline">cited</Badge>}
      </div>
      {chunk.section_path.length > 0 && (
        <p className="text-xs text-muted-foreground">{formatSection(chunk.section_path)}</p>
      )}
      <div className="grid gap-3 sm:grid-cols-2">
        <ScoreBar label="Cosine similarity" score={chunk.similarity} hint="Dense embedding similarity, -1 to 1" />
        <ScoreBar label="Rerank score" score={chunk.rerank_score} hint="Cross-encoder relevance, 0 to 1" />
      </div>
      <p className={cn("whitespace-pre-wrap text-xs leading-relaxed", !expanded && "line-clamp-4")}>{chunk.text}</p>
      <Button variant="link" size="xs" className="h-auto px-0" onClick={() => setExpanded((v) => !v)}>
        {expanded ? "Show less" : "Show full text"}
      </Button>
    </li>
  )
}

interface RetrievedChunksPanelProps {
  chunks: RetrievedChunk[]
  /** The [n] numbers the answer actually cites, to mark them. */
  citedNumbers: Set<number>
}

/** The collapsible panel; closed by default so the answer stays readable. */
export function RetrievedChunksPanel({ chunks, citedNumbers }: RetrievedChunksPanelProps) {
  return (
    <Collapsible className="rounded-md border bg-muted/40">
      <CollapsibleTrigger asChild>
        <button className="group flex w-full items-center justify-between px-3 py-2 text-sm font-medium">
          Retrieved chunks ({chunks.length})
          <ChevronDown className="size-4 transition-transform group-data-[state=open]:rotate-180" />
        </button>
      </CollapsibleTrigger>
      <CollapsibleContent className="px-3 pb-3">
        {chunks.length === 0 ? (
          <p className="text-xs text-muted-foreground">
            No chunk scored above the refusal threshold, so nothing was sent to the model.
          </p>
        ) : (
          <ol className="space-y-2">
            {chunks.map((chunk) => (
              <ChunkItem key={chunk.chunk_id} chunk={chunk} cited={citedNumbers.has(chunk.n)} />
            ))}
          </ol>
        )}
      </CollapsibleContent>
    </Collapsible>
  )
}
