/**
 * The chat: message list, question box, and the call to POST /ask.
 *
 * The conversation lives in React state only (a reload starts a new chat);
 * the backend is stateless and receives the recent turns as `history`, which
 * it uses to rewrite a follow-up like "and if I miss it?" into a full question.
 */
import { useMutation } from "@tanstack/react-query"
import { Loader2, MessageSquareText, RotateCcw, SendHorizontal, ShieldAlert, TriangleAlert } from "lucide-react"
import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react"

import { AnswerCard } from "@/components/chat/AnswerCard"
import { Button } from "@/components/ui/button"
import { Card, CardAction, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Textarea } from "@/components/ui/textarea"
import * as api from "@/lib/api"
import { MAX_QUESTION_CHARS } from "@/lib/schemas"

// Backend limits (AskRequest.history max_length=6, HistoryTurn.content max 4000).
const MAX_HISTORY_TURNS = 6
const MAX_TURN_CHARS = 4000

const EXAMPLE_QUESTIONS = [
  "What does error code 0052 mean?",
  "How long can the Commissioner extend the date for integration?",
  "Which fields are mandatory in the invoice JSON?",
]

type Message =
  | { id: string; kind: "question"; text: string }
  | { id: string; kind: "answer"; response: api.AskResponse }
  | { id: string; kind: "error"; error: api.ApiError | Error }

/** Build `history` from earlier questions and answers (errors are left out). */
function toHistory(messages: Message[]): api.HistoryTurn[] {
  const turns: api.HistoryTurn[] = []
  for (const m of messages) {
    if (m.kind === "question") turns.push({ role: "user", content: m.text })
    if (m.kind === "answer") turns.push({ role: "assistant", content: m.response.answer.slice(0, MAX_TURN_CHARS) })
  }
  return turns.slice(-MAX_HISTORY_TURNS)
}

/** A user's question, right-aligned like a chat bubble. */
function QuestionBubble({ text }: { text: string }) {
  return (
    <div className="flex justify-end">
      <p dir="auto" className="max-w-[85%] whitespace-pre-wrap rounded-lg bg-primary px-3 py-2 text-sm text-primary-foreground">
        {text}
      </p>
    </div>
  )
}

/** A failed ask: a guardrail block, rate limit, outage or validation error. */
function ErrorBubble({ error }: { error: api.ApiError | Error }) {
  const status = error instanceof api.ApiError ? error.status : 0
  const blocked = error instanceof api.ApiError && error.code === "blocked_by_guardrail"
  const title = blocked
    ? "Blocked by a guardrail"
    : status === 429
      ? "Too many questions"
      : status === 503
        ? "Service temporarily unavailable"
        : "Could not get an answer"
  const Icon = blocked ? ShieldAlert : TriangleAlert
  return (
    <div className="flex gap-2 rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm">
      <Icon className="mt-0.5 size-4 shrink-0 text-destructive" />
      <div>
        <p className="font-medium text-destructive">{title}</p>
        <p className="text-muted-foreground">{error.message}</p>
      </div>
    </div>
  )
}

/** Shown before the first question: what to do, plus clickable examples. */
function EmptyState({ onPick }: { onPick: (q: string) => void }) {
  return (
    <div className="flex flex-col items-center gap-3 py-12 text-center">
      <MessageSquareText className="size-8 text-muted-foreground" />
      <div>
        <p className="font-medium">Ask about your documents</p>
        <p className="text-sm text-muted-foreground">Answers cite the exact pages they come from.</p>
      </div>
      <div className="flex flex-wrap justify-center gap-2">
        {EXAMPLE_QUESTIONS.map((q) => (
          <Button key={q} variant="outline" size="sm" className="h-auto whitespace-normal py-1.5" onClick={() => onPick(q)}>
            {q}
          </Button>
        ))}
      </div>
    </div>
  )
}

/** The whole chat card. */
export function ChatPanel() {
  const [messages, setMessages] = useState<Message[]>([])
  const [question, setQuestion] = useState("")
  const endRef = useRef<HTMLDivElement>(null)
  const ask = useMutation({ mutationFn: api.ask })

  // Keep the newest message (or the "thinking" line) in view.
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" })
  }, [messages, ask.isPending])

  const trimmed = question.trim()
  const tooLong = trimmed.length > MAX_QUESTION_CHARS
  const canSend = trimmed.length > 0 && !tooLong && !ask.isPending

  function send(event?: FormEvent) {
    event?.preventDefault()
    if (!canSend) return
    // History is built before the new question is added: it is the context, not the question.
    const history = toHistory(messages)
    setMessages((m) => [...m, { id: crypto.randomUUID(), kind: "question", text: trimmed }])
    setQuestion("")
    ask.mutate(
      { question: trimmed, history },
      {
        onSuccess: (response) =>
          setMessages((m) => [...m, { id: response.request_id, kind: "answer", response }]),
        onError: (error) => setMessages((m) => [...m, { id: crypto.randomUUID(), kind: "error", error }]),
      },
    )
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    // Enter sends; Shift+Enter adds a new line, as in most chat apps.
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault()
      send()
    }
  }

  return (
    <Card className="flex h-[calc(100svh-6rem)] min-h-[32rem] flex-col">
      <CardHeader>
        <CardTitle>Ask</CardTitle>
        <CardDescription>Grounded in your documents only; it says so when the answer is not there.</CardDescription>
        {messages.length > 0 && (
          <CardAction>
            <Button variant="ghost" size="sm" onClick={() => setMessages([])} disabled={ask.isPending}>
              <RotateCcw /> New chat
            </Button>
          </CardAction>
        )}
      </CardHeader>

      <CardContent className="min-h-0 flex-1 px-0">
        <ScrollArea className="h-full px-4">
          {messages.length === 0 && !ask.isPending ? (
            <EmptyState onPick={setQuestion} />
          ) : (
            <div className="space-y-4 pb-2">
              {messages.map((m) =>
                m.kind === "question" ? (
                  <QuestionBubble key={m.id} text={m.text} />
                ) : m.kind === "answer" ? (
                  <AnswerCard key={m.id} response={m.response} />
                ) : (
                  <ErrorBubble key={m.id} error={m.error} />
                ),
              )}
              {ask.isPending && (
                <p className="flex items-center gap-2 text-sm text-muted-foreground">
                  <Loader2 className="size-4 animate-spin" /> Searching your documents and writing an answer...
                </p>
              )}
              <div ref={endRef} />
            </div>
          )}
        </ScrollArea>
      </CardContent>

      <CardFooter className="border-t pt-4">
        <form onSubmit={send} className="flex w-full flex-col gap-2">
          <Textarea
            dir="auto"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder="Ask a question about FBR Digital Invoicing..."
            rows={2}
            aria-label="Your question"
            aria-invalid={tooLong}
            className="max-h-40 resize-none"
          />
          <div className="flex items-center justify-between">
            <span className={tooLong ? "text-xs text-destructive" : "text-xs text-muted-foreground"}>
              {trimmed.length}/{MAX_QUESTION_CHARS} · Enter to send, Shift+Enter for a new line
            </span>
            <Button type="submit" size="sm" disabled={!canSend}>
              {ask.isPending ? <Loader2 className="animate-spin" /> : <SendHorizontal />}
              Ask
            </Button>
          </div>
        </form>
      </CardFooter>
    </Card>
  )
}
