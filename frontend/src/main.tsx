/** Entry point: wires up data fetching, routing, login state and toasts. */
import { QueryCache, QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { StrictMode } from "react"
import { createRoot } from "react-dom/client"
import { BrowserRouter } from "react-router-dom"
import { toast } from "sonner"

import App from "@/App"
import { Toaster } from "@/components/ui/sonner"
import { AuthProvider } from "@/context/AuthContext"
import { ApiError } from "@/lib/api"
import "@/index.css"

const queryClient = new QueryClient({
  // A failed read (e.g. the documents list) has nowhere obvious to show its
  // error, so it becomes a toast. A 401 is skipped: the auth handler already
  // redirects to /login. Writes (upload, delete, ask) show their own errors
  // next to what the user did, so they are not toasted here.
  queryCache: new QueryCache({
    onError: (error) => {
      if (!(error instanceof ApiError && error.status === 401)) toast.error(error.message)
    },
  }),
  defaultOptions: {
    queries: {
      // Retrying a 4xx (401, 404, 422) cannot succeed, so only retry server or network errors.
      retry: (failureCount, error) =>
        !(error instanceof ApiError && error.status >= 400 && error.status < 500) && failureCount < 2,
      refetchOnWindowFocus: false,
    },
  },
})

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <AuthProvider>
          <App />
          {/* Top-centre so toasts never cover the header's Log out button. */}
          <Toaster theme="light" position="top-center" richColors toastOptions={{ duration: 5000 }} />
        </AuthProvider>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
)
