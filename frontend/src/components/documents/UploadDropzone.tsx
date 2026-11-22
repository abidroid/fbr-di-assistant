/**
 * Drag-and-drop (or click) upload area.
 *
 * Files are uploaded one at a time because each upload is ingested inside
 * the request (up to ~2 minutes on CPU); parallel uploads would only compete
 * for the same CPU on the server.
 */
import { Loader2, Upload } from "lucide-react"
import { useRef, useState, type DragEvent } from "react"
import { toast } from "sonner"

import { useUploadDocument } from "@/hooks/useDocuments"
import { ApiError, type UploadResult } from "@/lib/api"
import { cn } from "@/lib/utils"

// Same limits as the backend (settings.max_upload_mb and the allowed types).
// Checking here only saves a pointless upload; the server checks the real
// content (magic bytes), so a renamed .exe is still rejected with 415.
const MAX_UPLOAD_MB = 25
const ALLOWED_EXTENSIONS = [".pdf", ".docx", ".pptx", ".txt", ".md"]

/** Errors stay longer than the default 5 s: their description explains what to fix. */
const ERROR_TOAST_MS = 8000

/** Titles for each error status the upload endpoint documents. */
const ERROR_TITLES: Record<number, string> = {
  400: "Could not read this file",
  409: "Already uploaded",
  413: "File too large",
  415: "Unsupported file type",
  503: "Service temporarily unavailable",
}

/** Return a reason the file cannot be uploaded, or null when it looks fine. */
function precheck(file: File): string | null {
  const name = file.name.toLowerCase()
  if (!ALLOWED_EXTENSIONS.some((ext) => name.endsWith(ext))) {
    return `Allowed types: ${ALLOWED_EXTENSIONS.join(", ")}.`
  }
  if (file.size === 0) return "The file is empty."
  if (file.size > MAX_UPLOAD_MB * 1024 * 1024) return `The limit is ${MAX_UPLOAD_MB} MB.`
  return null
}

/** Toast the outcome of a successful upload (201 ready or 202 still processing). */
function toastSuccess({ document, httpStatus }: UploadResult): void {
  if (httpStatus === 202) {
    toast.info(`${document.filename} is being processed`, {
      description: "It is large, so embedding continues in the background. Its status updates below.",
    })
  } else {
    toast.success(`${document.filename} is ready`, {
      description: `${document.chunk_count} chunks indexed.`,
    })
  }
}

/** Toast a failed upload with a title for its status and the server's message. */
function toastError(file: File, error: Error): void {
  const title = error instanceof ApiError ? ERROR_TITLES[error.status] : undefined
  toast.error(title ? `${title}: ${file.name}` : `Upload failed: ${file.name}`, {
    description: error.message,
    duration: ERROR_TOAST_MS,
  })
}

/** The upload area at the top of the documents section. */
export function UploadDropzone() {
  const upload = useUploadDocument()
  const inputRef = useRef<HTMLInputElement>(null)
  const [isDragging, setIsDragging] = useState(false)
  const [current, setCurrent] = useState<string | null>(null)

  async function uploadAll(files: File[]) {
    for (const file of files) {
      const problem = precheck(file)
      if (problem) {
        toast.error(`Cannot upload ${file.name}`, { description: problem, duration: ERROR_TOAST_MS })
        continue
      }
      setCurrent(file.name)
      try {
        toastSuccess(await upload.mutateAsync(file))
      } catch (error) {
        toastError(file, error as Error)
      }
    }
    setCurrent(null)
  }

  function onDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault()
    setIsDragging(false)
    if (current === null) void uploadAll(Array.from(event.dataTransfer.files))
  }

  const busy = current !== null
  return (
    <div
      role="button"
      tabIndex={0}
      aria-disabled={busy}
      onClick={() => !busy && inputRef.current?.click()}
      onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && !busy && inputRef.current?.click()}
      onDragOver={(e) => {
        // Without preventDefault the browser would open the file instead of dropping it here.
        e.preventDefault()
        setIsDragging(true)
      }}
      onDragLeave={() => setIsDragging(false)}
      onDrop={onDrop}
      className={cn(
        "flex cursor-pointer flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed p-6 text-center transition-colors",
        isDragging ? "border-primary bg-primary/5" : "border-border hover:bg-muted/60",
        busy && "cursor-wait opacity-80",
      )}
    >
      {busy ? (
        <>
          <Loader2 className="size-6 animate-spin text-primary" />
          <p className="text-sm font-medium">Uploading and ingesting {current}...</p>
          <p className="text-xs text-muted-foreground">Extracting, chunking and embedding can take up to 2 minutes.</p>
        </>
      ) : (
        <>
          <Upload className="size-6 text-muted-foreground" />
          <p className="text-sm font-medium">Drop files here or click to choose</p>
          <p className="text-xs text-muted-foreground">PDF, DOCX, PPTX, TXT or MD, up to {MAX_UPLOAD_MB} MB</p>
        </>
      )}
      <input
        ref={inputRef}
        type="file"
        multiple
        accept={ALLOWED_EXTENSIONS.join(",")}
        className="hidden"
        onChange={(e) => {
          void uploadAll(Array.from(e.target.files ?? []))
          // Reset so choosing the same file again still fires onChange.
          e.target.value = ""
        }}
      />
    </div>
  )
}
