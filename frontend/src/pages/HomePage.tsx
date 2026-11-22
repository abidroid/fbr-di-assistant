/** The main page: a header with the user, then documents and chat. */
import { FileSearch, LogOut } from "lucide-react"

import { Button } from "@/components/ui/button"
import { useAuth } from "@/context/AuthContext"

/** Header bar shared by the documents and chat sections. */
function Header() {
  const { user, logout } = useAuth()
  return (
    <header className="border-b bg-background">
      <div className="mx-auto flex h-14 max-w-7xl items-center justify-between px-4">
        <div className="flex items-center gap-2 font-semibold">
          <FileSearch className="size-5 text-primary" />
          FBR Digital Invoicing Assistant
        </div>
        <div className="flex items-center gap-3 text-sm">
          <span className="hidden text-muted-foreground sm:inline">{user?.full_name ?? "..."}</span>
          <Button variant="outline" size="sm" onClick={logout}>
            <LogOut />
            Log out
          </Button>
        </div>
      </div>
    </header>
  )
}

/** The page a logged-in user sees. */
export function HomePage() {
  return (
    <div className="min-h-svh bg-muted/40">
      <Header />
      <main className="mx-auto max-w-7xl p-4">
        <p className="text-muted-foreground">Documents and chat come next.</p>
      </main>
    </div>
  )
}
