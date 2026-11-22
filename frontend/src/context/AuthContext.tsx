/**
 * Login state for the whole app.
 *
 * The JWT is kept in localStorage (through `tokenStorage`) so a reload keeps
 * the user logged in; the user's details come from GET /auth/me. The backend
 * decides who the user is from the token on every request; this context only
 * mirrors that for the UI.
 */
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react"
import { useNavigate } from "react-router-dom"

import * as api from "@/lib/api"

interface AuthContextValue {
  /** The logged-in user, or undefined while loading or when logged out. */
  user: api.User | undefined
  /** True when a token is stored, even before /auth/me has answered. */
  isLoggedIn: boolean
  login: (email: string, password: string) => Promise<void>
  logout: () => void
}

const AuthContext = createContext<AuthContextValue | null>(null)

/** Provide login state to every component below it. Must sit inside the router. */
export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const [token, setToken] = useState<string | null>(() => api.tokenStorage.get())

  const { data: user } = useQuery({
    // The token is part of the key, so a new login never shows the old user.
    queryKey: ["me", token],
    queryFn: api.getMe,
    enabled: token !== null,
  })

  const login = useCallback(async (email: string, password: string) => {
    const { access_token } = await api.login(email, password)
    api.tokenStorage.set(access_token)
    setToken(access_token)
  }, [])

  const logout = useCallback(() => {
    api.tokenStorage.clear()
    setToken(null)
    // Drop every cached answer: documents and the user belong to this account only.
    queryClient.clear()
  }, [queryClient])

  // An expired or invalid token makes the API answer 401: log out and send
  // the user to the login page instead of leaving them on a broken screen.
  useEffect(() => {
    api.setUnauthorizedHandler(() => {
      logout()
      navigate("/login", { replace: true })
    })
  }, [logout, navigate])

  return (
    <AuthContext.Provider value={{ user, isLoggedIn: token !== null, login, logout }}>
      {children}
    </AuthContext.Provider>
  )
}

/** Read the login state; only works inside `AuthProvider`. */
// The hook lives next to its provider on purpose; fast refresh still works for the provider.
// oxlint-disable-next-line react/only-export-components
export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext)
  if (value === null) throw new Error("useAuth must be used inside AuthProvider")
  return value
}
