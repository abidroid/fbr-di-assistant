/**
 * TanStack Query hooks for documents, so components never call the API directly.
 *
 * The list is cached under one key; upload and delete invalidate it, which
 * makes every component that shows documents refetch automatically.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import * as api from "@/lib/api"

const DOCUMENTS_KEY = ["documents"] as const

/** How often to re-check while a large upload is still being embedded. */
const POLL_MS = 3000

/** The user's documents; polls while any of them is still being processed. */
export function useDocuments() {
  return useQuery({
    queryKey: DOCUMENTS_KEY,
    queryFn: api.listDocuments,
    // A 202 upload finishes in the background, so keep asking until every
    // document is ready or failed, then stop (false = no polling).
    refetchInterval: (query) =>
      query.state.data?.some((d) => d.status === "queued" || d.status === "processing") ? POLL_MS : false,
  })
}

/** Upload one file; refreshes the list whether it succeeded or failed. */
export function useUploadDocument() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: api.uploadDocument,
    onSettled: () => queryClient.invalidateQueries({ queryKey: DOCUMENTS_KEY }),
  })
}

/** Delete one document by id, then refresh the list. */
export function useDeleteDocument() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: api.deleteDocument,
    onSettled: () => queryClient.invalidateQueries({ queryKey: DOCUMENTS_KEY }),
  })
}
