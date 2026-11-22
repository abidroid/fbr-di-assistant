/** URL routes: login and signup for guests, the main page for logged-in users. */
import { Navigate, Route, Routes } from "react-router-dom"

import { GuestOnly, RequireAuth } from "@/components/RouteGuards"
import { HomePage } from "@/pages/HomePage"
import { LoginPage } from "@/pages/LoginPage"
import { SignupPage } from "@/pages/SignupPage"

export default function App() {
  return (
    <Routes>
      <Route element={<GuestOnly />}>
        <Route path="login" element={<LoginPage />} />
        <Route path="signup" element={<SignupPage />} />
      </Route>
      <Route element={<RequireAuth />}>
        <Route index element={<HomePage />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
