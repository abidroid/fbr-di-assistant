/** The user's documents with status, pages, chunks, OCR and warning badges. */
import { AlertTriangle, FileText, Loader2, ScanText } from "lucide-react"

import { ConfirmDeleteDialog } from "@/components/documents/ConfirmDeleteDialog"
import { Badge } from "@/components/ui/badge"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { useDocuments } from "@/hooks/useDocuments"
import type { DocumentInfo, DocumentStatus } from "@/lib/api"
import { formatBytes, formatDateTime } from "@/lib/format"

/** A coloured badge for the ingestion status. */
function StatusBadge({ status, error }: { status: DocumentStatus; error: string | null }) {
  if (status === "ready") return <Badge>Ready</Badge>
  if (status === "failed") {
    return (
      <Badge variant="destructive" title={error ?? undefined}>
        Failed
      </Badge>
    )
  }
  return (
    <Badge variant="secondary">
      <Loader2 className="animate-spin" />
      {status === "queued" ? "Queued" : "Processing"}
    </Badge>
  )
}

/** Name, size and the notes a user should know before trusting answers from it. */
function FileCell({ doc }: { doc: DocumentInfo }) {
  return (
    <div className="min-w-0 space-y-1">
      <div className="flex items-start gap-2">
        <FileText className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
        <span className="break-all font-medium" title={doc.filename}>
          {doc.filename}
        </span>
      </div>
      <div className="flex flex-wrap items-center gap-1.5 pl-6 text-xs text-muted-foreground">
        <span>
          {doc.file_type.toUpperCase()} · {formatBytes(doc.size_bytes)} · {formatDateTime(doc.created_at)}
        </span>
        {doc.ocr_pages > 0 && (
          // OCR and screenshot captions can misread text, so flag them.
          <Badge variant="outline" title="These pages were read by OCR or image captioning; check citations.">
            <ScanText />
            OCR {doc.ocr_pages} {doc.ocr_pages === 1 ? "page" : "pages"}
          </Badge>
        )}
        {doc.warnings.length > 0 && (
          <Badge variant="outline" className="border-amber-300 text-amber-700" title={doc.warnings.join("\n")}>
            <AlertTriangle />
            {doc.warnings.length} {doc.warnings.length === 1 ? "warning" : "warnings"}
          </Badge>
        )}
        {doc.status === "failed" && doc.error && <span className="text-destructive">{doc.error}</span>}
      </div>
    </div>
  )
}

/** The documents table with loading and empty states. */
export function DocumentTable() {
  const { data: documents, isPending, isError } = useDocuments()

  if (isPending) {
    return (
      <div className="flex items-center justify-center gap-2 py-10 text-sm text-muted-foreground">
        <Loader2 className="size-4 animate-spin" /> Loading documents...
      </div>
    )
  }
  if (isError) {
    return <p className="py-10 text-center text-sm text-destructive">Could not load your documents.</p>
  }
  if (documents.length === 0) {
    return (
      <p className="py-10 text-center text-sm text-muted-foreground">
        No documents yet. Upload FBR DI files above to start asking questions.
      </p>
    )
  }

  return (
    <Table className="table-fixed">
      <TableHeader>
        <TableRow>
          <TableHead>File</TableHead>
          <TableHead className="w-24">Status</TableHead>
          <TableHead className="w-14 text-right">Pages</TableHead>
          <TableHead className="w-16 text-right">Chunks</TableHead>
          <TableHead className="w-10">
            <span className="sr-only">Actions</span>
          </TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {documents.map((doc) => (
          <TableRow key={doc.id}>
            {/* whitespace-normal: long FBR file names must wrap, not overflow. */}
            <TableCell className="whitespace-normal">
              <FileCell doc={doc} />
            </TableCell>
            <TableCell className="align-top">
              <StatusBadge status={doc.status} error={doc.error} />
            </TableCell>
            <TableCell className="text-right align-top tabular-nums">{doc.pages ?? "—"}</TableCell>
            <TableCell className="text-right align-top tabular-nums">{doc.chunk_count}</TableCell>
            <TableCell className="align-top">
              <ConfirmDeleteDialog document={doc} />
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}
