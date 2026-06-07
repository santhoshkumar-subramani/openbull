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
  const [userid, setUserid] = useState("");
  const [password, setPassword] = useState("");
  const [totp, setTotp] = useState("");
  const [error, setError] = useState("");
  const navigate = useNavigate();

  const loginMutation = useMutation({
    mutationFn: () =>
      shoonyaLogin({
        userid: userid.trim(),
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
    if (!userid.trim() || !password.trim() || !totp.trim()) {
      setError("User ID, Password and TOTP are all required.");
      return;
    }
    loginMutation.mutate();
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4">
      <Card className="w-full max-w-md border border-primary/20 shadow-lg shadow-primary/5 transition-all duration-300 hover:shadow-xl">
        <CardHeader className="space-y-1">
          <CardTitle className="text-2xl font-bold tracking-tight text-center">Login with Shoonya</CardTitle>
          <CardDescription className="text-center text-muted-foreground">
            Shoonya (Finvasia) uses your trading credentials. Enter your User ID, Password and TOTP.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="space-y-4">
            {error && (
              <div className="rounded-md bg-destructive/10 p-3 text-sm font-medium text-destructive animate-in fade-in slide-in-from-top-1">
                {error}
              </div>
            )}

            <div className="space-y-2">
              <Label htmlFor="userid">User ID</Label>
              <Input
                id="userid"
                type="text"
                value={userid}
                onChange={(e) => setUserid(e.target.value)}
                placeholder="e.g. FA12345"
                autoComplete="username"
                className="transition-colors focus-visible:ring-primary/50"
                required
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="password">Password</Label>
              <Input
                id="password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="Your Shoonya trading password"
                autoComplete="current-password"
                className="transition-colors focus-visible:ring-primary/50"
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
                className="transition-colors focus-visible:ring-primary/50"
                required
              />
              <p className="text-xs text-muted-foreground">
                From your authenticator app (Google Authenticator, Authy, etc.).
              </p>
            </div>

            <Button 
              type="submit" 
              className="w-full font-medium" 
              disabled={loginMutation.isPending}
            >
              {loginMutation.isPending ? (
                <div className="flex items-center gap-2">
                  <div className="h-4 w-4 animate-spin rounded-full border-2 border-primary-foreground border-t-transparent" />
                  Authenticating...
                </div>
              ) : (
                "Login to Shoonya"
              )}
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
