/** "Are you sure?" dialog shown before a document is deleted. */
import { Loader2, Trash2 } from "lucide-react"
import { useState } from "react"
import { toast } from "sonner"

import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog"
import { Button } from "@/components/ui/button"
import { useDeleteDocument } from "@/hooks/useDocuments"
import type { DocumentInfo } from "@/lib/api"

/** A trash button that opens the confirmation and deletes on "Delete". */
export function ConfirmDeleteDialog({ document }: { document: DocumentInfo }) {
  const remove = useDeleteDocument()
  const [open, setOpen] = useState(false)

  function onConfirm() {
    remove.mutate(document.id, {
      onSuccess: () => {
        setOpen(false)
        toast.success(`Deleted ${document.filename}`)
      },
      onError: (error) => toast.error("Delete failed", { description: error.message }),
    })
  }

  return (
    <AlertDialog open={open} onOpenChange={setOpen}>
      <AlertDialogTrigger asChild>
        <Button variant="ghost" size="icon-sm" aria-label={`Delete ${document.filename}`}>
          <Trash2 />
        </Button>
      </AlertDialogTrigger>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Delete this document?</AlertDialogTitle>
          <AlertDialogDescription>
            <strong className="break-all">{document.filename}</strong> and its {document.chunk_count} chunks will be
            removed from the search index, so answers can no longer cite it. This cannot be undone.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={remove.isPending}>Cancel</AlertDialogCancel>
          {/* A plain Button, not AlertDialogAction, so the dialog stays open until the delete finishes. */}
          <Button variant="destructive" onClick={onConfirm} disabled={remove.isPending}>
            {remove.isPending && <Loader2 className="animate-spin" />}
            Delete
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
