/**
 * Zod form schemas that mirror the backend's Pydantic rules (app/schemas.py).
 *
 * They only give instant feedback in the browser; the API still validates
 * everything and its error message is shown if something slips through.
 */
import { z } from "zod"

/** The backend's question limit (AskRequest.question, after trimming). */
export const MAX_QUESTION_CHARS = 1000

export const loginSchema = z.object({
  email: z.email("Enter a valid email"),
  password: z.string().min(1, "Enter your password"),
})
export type LoginValues = z.infer<typeof loginSchema>

// Mirrors UserCreate: full_name 1-200 after trimming, password 8-128.
export const signupSchema = z.object({
  email: z.email("Enter a valid email"),
  full_name: z.string().trim().min(1, "Enter your name").max(200, "At most 200 characters"),
  password: z.string().min(8, "At least 8 characters").max(128, "At most 128 characters"),
})
export type SignupValues = z.infer<typeof signupSchema>
