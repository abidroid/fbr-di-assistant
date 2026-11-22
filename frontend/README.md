# FBR DI Assistant — frontend

React + Vite + TypeScript, Tailwind CSS v4, shadcn/ui, TanStack Query, React Router,
react-hook-form + zod. It talks only to the FastAPI backend over HTTP.

## Run

```powershell
cd frontend
copy .env.example .env      # VITE_API_URL=http://127.0.0.1:8000
npm install
npm run dev                 # http://localhost:5173 (the API must be running)
```

`npm run build` type-checks and writes static files to `dist/` (served by Caddy in deployment);
`npm run lint` runs oxlint.

## Structure

| Path | Purpose |
| --- | --- |
| `src/lib/api.ts` | Types mirroring `/openapi.json` and one function per endpoint; Bearer header, error shape, 401 handling |
| `src/context/AuthContext.tsx` | JWT in localStorage, current user from `/auth/me`, logout on 401 |
| `src/components/RouteGuards.tsx` | `RequireAuth` / `GuestOnly` route wrappers |
| `src/pages/` | Login, Signup, Home (documents + chat) |
| `src/hooks/useDocuments.ts` | Documents list (polls while processing), upload, delete |
| `src/components/documents/` | Drag-and-drop upload, documents table, delete confirmation |
| `src/components/chat/` | Chat, answer with `[n]` citations, retrieved chunks panel with both scores |
| `src/components/ui/` | shadcn/ui components (generated, not edited) |
