import { useState } from "react";
import type { FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from "@/components/ui/card";
import { shoonyaLogin } from "@/api/broker";

export default function BrokerShoonyaLogin() {
  const [password, setPassword] = useState("");
  const [totp, setTotp] = useState("");
  const [error, setError] = useState("");
  const navigate = useNavigate();

  const loginMutation = useMutation({
    mutationFn: () =>
      shoonyaLogin({
        password: password.trim(),
        totp_code: totp.trim(),
      }),
    onSuccess: () => {
      navigate("/dashboard");
    },
    onError: (err: unknown) => {
      const axiosErr = err as { response?: { data?: { detail?: string } } };
      setError(axiosErr.response?.data?.detail ?? "Shoonya authentication failed.");
    },
  });

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    setError("");
    if (!password.trim() || !totp.trim()) {
      setError("Password and TOTP are both required.");
      return;
    }
    loginMutation.mutate();
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4">
      <Card className="w-full max-w-md">
        <CardHeader>
          <CardTitle>Login with Shoonya (Finvasia)</CardTitle>
          <CardDescription>
            Shoonya uses your trading account credentials with TOTP.
            Your User ID is configured in broker settings. Enter your
            password and the current TOTP from your authenticator app.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="space-y-4">
            {error && (
              <div className="rounded-md bg-destructive/10 p-3 text-sm text-destructive">
                {error}
              </div>
            )}

            <div className="space-y-2">
              <Label htmlFor="password">Password</Label>
              <Input
                id="password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="Your Shoonya login password"
                autoComplete="current-password"
                required
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="totp">TOTP Code</Label>
              <Input
                id="totp"
                type="text"
                inputMode="numeric"
                pattern="[0-9]*"
                value={totp}
                onChange={(e) => setTotp(e.target.value)}
                placeholder="6-digit code"
                maxLength={6}
                required
              />
              <p className="text-xs text-muted-foreground">
                From your Shoonya authenticator app (Google Authenticator, Authy etc.).
              </p>
            </div>

            <Button type="submit" className="w-full" disabled={loginMutation.isPending}>
              {loginMutation.isPending ? "Authenticating..." : "Login"}
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
