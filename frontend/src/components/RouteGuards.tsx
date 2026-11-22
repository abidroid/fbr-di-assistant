/**
 * Route wrappers that decide who may see a page.
 *
 * They only improve the UI: the real protection is the backend, which answers
 * 401 to any document or ask request without a valid token.
 */
import { Navigate, Outlet } from "react-router-dom"

import { useAuth } from "@/context/AuthContext"

/** Show the nested route only when logged in; otherwise go to /login. */
export function RequireAuth() {
  const { isLoggedIn } = useAuth()
  return isLoggedIn ? <Outlet /> : <Navigate to="/login" replace />
}

/** Login and signup pages: centred card, and skipped when already logged in. */
export function GuestOnly() {
  const { isLoggedIn } = useAuth()
  if (isLoggedIn) return <Navigate to="/" replace />
  return (
    <main className="flex min-h-svh items-center justify-center bg-muted/40 p-4">
      <Outlet />
    </main>
  )
}
