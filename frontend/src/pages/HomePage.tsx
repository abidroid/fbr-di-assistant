/** The main page: a header with the user, then documents and chat. */
import { FileSearch, LogOut } from "lucide-react"

import { ChatPanel } from "@/components/chat/ChatPanel"
import { DocumentTable } from "@/components/documents/DocumentTable"
import { UploadDropzone } from "@/components/documents/UploadDropzone"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
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

/** Upload area plus the list of the user's documents. */
function DocumentsSection() {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Documents</CardTitle>
        <CardDescription>Only you can see and search the files you upload.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <UploadDropzone />
        <DocumentTable />
      </CardContent>
    </Card>
  )
}

/** The page a logged-in user sees: documents on the left, chat on the right. */
export function HomePage() {
  return (
    <div className="min-h-svh bg-muted/40">
      <Header />
      {/* Stacked on small screens; side by side from the lg breakpoint. */}
      <main className="mx-auto grid max-w-7xl gap-4 p-4 lg:grid-cols-5">
        <div className="lg:col-span-2">
          <DocumentsSection />
        </div>
        <div className="lg:col-span-3">
          <ChatPanel />
        </div>
      </main>
    </div>
  )
}
