/** Log in with email and password, then open the main page. */
import { zodResolver } from "@hookform/resolvers/zod"
import { useMutation } from "@tanstack/react-query"
import { useForm } from "react-hook-form"
import { Link, useNavigate } from "react-router-dom"

import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card"
import { Field, FieldError, FieldGroup, FieldLabel } from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import { useAuth } from "@/context/AuthContext"
import { loginSchema, type LoginValues } from "@/lib/schemas"

/** The login form. A wrong email or password shows one message under the form. */
export function LoginPage() {
  const { login } = useAuth()
  const navigate = useNavigate()
  const form = useForm<LoginValues>({
    resolver: zodResolver(loginSchema),
    defaultValues: { email: "", password: "" },
  })
  const { errors } = form.formState

  const mutation = useMutation({
    mutationFn: (values: LoginValues) => login(values.email, values.password),
    onSuccess: () => navigate("/", { replace: true }),
  })

  return (
    <Card className="w-full max-w-sm">
      <CardHeader>
        <CardTitle>Log in</CardTitle>
        <CardDescription>Ask questions about your FBR Digital Invoicing documents.</CardDescription>
      </CardHeader>
      <form onSubmit={form.handleSubmit((values) => mutation.mutate(values))} noValidate>
        <CardContent>
          <FieldGroup>
            <Field data-invalid={!!errors.email}>
              <FieldLabel htmlFor="email">Email</FieldLabel>
              <Input id="email" type="email" autoComplete="email" {...form.register("email")} />
              <FieldError errors={[errors.email]} />
            </Field>
            <Field data-invalid={!!errors.password}>
              <FieldLabel htmlFor="password">Password</FieldLabel>
              <Input id="password" type="password" autoComplete="current-password" {...form.register("password")} />
              <FieldError errors={[errors.password]} />
            </Field>
            {mutation.error && (
              <p role="alert" className="text-sm text-destructive">
                {mutation.error.message}
              </p>
            )}
          </FieldGroup>
        </CardContent>
        <CardFooter className="mt-6 flex-col gap-3">
          <Button type="submit" className="w-full" disabled={mutation.isPending}>
            {mutation.isPending ? "Logging in..." : "Log in"}
          </Button>
          <p className="text-sm text-muted-foreground">
            No account?{" "}
            <Link to="/signup" className="text-primary underline-offset-4 hover:underline">
              Sign up
            </Link>
          </p>
        </CardFooter>
      </form>
    </Card>
  )
}
