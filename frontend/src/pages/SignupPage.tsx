/** Create an account, log straight in, then open the main page. */
import { zodResolver } from "@hookform/resolvers/zod"
import { useMutation } from "@tanstack/react-query"
import { useForm } from "react-hook-form"
import { Link, useNavigate } from "react-router-dom"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card"
import { Field, FieldDescription, FieldError, FieldGroup, FieldLabel } from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import { useAuth } from "@/context/AuthContext"
import * as api from "@/lib/api"
import { signupSchema, type SignupValues } from "@/lib/schemas"

/** The signup form. "Email taken" (409) is shown on the email field itself. */
export function SignupPage() {
  const { login } = useAuth()
  const navigate = useNavigate()
  const form = useForm<SignupValues>({
    resolver: zodResolver(signupSchema),
    defaultValues: { email: "", full_name: "", password: "" },
  })
  const { errors } = form.formState

  const mutation = useMutation({
    // Signing up and then logging in saves the user typing the password twice.
    mutationFn: async (values: SignupValues) => {
      await api.signup(values)
      await login(values.email, values.password)
    },
    onSuccess: () => {
      toast.success("Account created")
      navigate("/", { replace: true })
    },
    onError: (error) => {
      if (error instanceof api.ApiError && error.code === "email_taken") {
        form.setError("email", { message: error.message })
      }
    },
  })
  // The email error is already on its field; anything else goes under the form.
  const formError =
    mutation.error && !(mutation.error instanceof api.ApiError && mutation.error.code === "email_taken")
      ? mutation.error.message
      : null

  return (
    <Card className="w-full max-w-sm">
      <CardHeader>
        <CardTitle>Create an account</CardTitle>
        <CardDescription>Your documents are private to your account.</CardDescription>
      </CardHeader>
      <form onSubmit={form.handleSubmit((values) => mutation.mutate(values))} noValidate>
        <CardContent>
          <FieldGroup>
            <Field data-invalid={!!errors.full_name}>
              <FieldLabel htmlFor="full_name">Full name</FieldLabel>
              <Input id="full_name" autoComplete="name" {...form.register("full_name")} />
              <FieldError errors={[errors.full_name]} />
            </Field>
            <Field data-invalid={!!errors.email}>
              <FieldLabel htmlFor="email">Email</FieldLabel>
              <Input id="email" type="email" autoComplete="email" {...form.register("email")} />
              <FieldError errors={[errors.email]} />
            </Field>
            <Field data-invalid={!!errors.password}>
              <FieldLabel htmlFor="password">Password</FieldLabel>
              <Input id="password" type="password" autoComplete="new-password" {...form.register("password")} />
              <FieldDescription>8 to 128 characters.</FieldDescription>
              <FieldError errors={[errors.password]} />
            </Field>
            {formError && (
              <p role="alert" className="text-sm text-destructive">
                {formError}
              </p>
            )}
          </FieldGroup>
        </CardContent>
        <CardFooter className="mt-6 flex-col gap-3">
          <Button type="submit" className="w-full" disabled={mutation.isPending}>
            {mutation.isPending ? "Creating account..." : "Sign up"}
          </Button>
          <p className="text-sm text-muted-foreground">
            Already have an account?{" "}
            <Link to="/login" className="text-primary underline-offset-4 hover:underline">
              Log in
            </Link>
          </p>
        </CardFooter>
      </form>
    </Card>
  )
}
