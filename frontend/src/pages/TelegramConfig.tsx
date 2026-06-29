import { useEffect, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { 
  getTelegramConfig, 
  saveTelegramConfig, 
  toggleTelegramBot, 
  testTelegramAlert 
} from "@/api/telegram";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { toast } from "sonner";
import { Bot, Send, Save, Loader2 } from "lucide-react";

export default function TelegramConfig() {
  const queryClient = useQueryClient();
  const [botToken, setBotToken] = useState("");
  const [chatId, setChatId] = useState("");
  const [isActive, setIsActive] = useState(false);

  const { data: config, isLoading } = useQuery({
    queryKey: ["telegramConfig"],
    queryFn: getTelegramConfig,
    retry: false, // Don't retry if 404 (not configured)
  });

  useEffect(() => {
    if (config) {
      setBotToken(config.bot_token || "");
      setChatId(config.chat_id || "");
      setIsActive(config.is_active || false);
    }
  }, [config]);

  const saveMutation = useMutation({
    mutationFn: saveTelegramConfig,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["telegramConfig"] });
      toast.success("Telegram configuration saved!");
    },
    onError: (err: any) => {
      toast.error(err.response?.data?.detail || "Failed to save configuration");
    },
  });

  const toggleMutation = useMutation({
    mutationFn: toggleTelegramBot,
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ["telegramConfig"] });
      setIsActive(data.is_active);
      toast.success(`Telegram bot ${data.is_active ? "enabled" : "disabled"}`);
    },
    onError: (err: any) => {
      toast.error(err.response?.data?.detail || "Failed to toggle bot");
      setIsActive(!isActive); // Revert switch on error
    },
  });

  const testMutation = useMutation({
    mutationFn: testTelegramAlert,
    onSuccess: () => {
      toast.success("Test alert sent! Check your Telegram.");
    },
    onError: (err: any) => {
      toast.error(err.response?.data?.detail || "Failed to send test alert");
    },
  });

  const handleSave = () => {
    if (!botToken || !chatId) {
      toast.error("Bot Token and Chat ID are required.");
      return;
    }
    saveMutation.mutate({ bot_token: botToken, chat_id: chatId, is_active: isActive });
  };

  const handleToggle = (checked: boolean) => {
    if (!config) {
      toast.error("Please save your Bot Token and Chat ID first.");
      return;
    }
    setIsActive(checked);
    toggleMutation.mutate(checked);
  };

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-full min-h-[400px]">
        <Loader2 className="w-8 h-8 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div className="container max-w-2xl py-8">
      <div className="mb-8">
        <h1 className="text-3xl font-bold tracking-tight">Telegram Integration</h1>
        <p className="text-muted-foreground mt-2">
          Receive real-time trade alerts and control OpenBull via Telegram.
        </p>
      </div>

      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <div>
              <CardTitle className="flex items-center gap-2">
                <Bot className="w-5 h-5" />
                Bot Configuration
              </CardTitle>
              <CardDescription className="mt-1">
                Configure your Telegram bot credentials to start receiving alerts.
              </CardDescription>
            </div>
            <div className="flex items-center space-x-2">
              <Switch
                id="bot-active"
                checked={isActive}
                onCheckedChange={handleToggle}
                disabled={toggleMutation.isPending || !config}
              />
              <Label htmlFor="bot-active" className="font-medium">
                {isActive ? "Active" : "Inactive"}
              </Label>
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-6">
          <div className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="bot-token">Bot Token</Label>
              <Input
                id="bot-token"
                type="password"
                placeholder="e.g. 123456789:ABCdefGHIjklMNOpqrsTUVwxyz"
                value={botToken}
                onChange={(e) => setBotToken(e.target.value)}
                autoComplete="off"
              />
              <p className="text-xs text-muted-foreground">
                Get this from the BotFather on Telegram.
              </p>
            </div>
            
            <div className="space-y-2">
              <Label htmlFor="chat-id">Chat ID</Label>
              <Input
                id="chat-id"
                placeholder="e.g. 987654321"
                value={chatId}
                onChange={(e) => setChatId(e.target.value)}
                autoComplete="off"
              />
              <p className="text-xs text-muted-foreground">
                The ID of the chat or group where you want to receive notifications.
              </p>
            </div>
          </div>

          <div className="flex items-center justify-between pt-4">
            <Button
              variant="outline"
              onClick={() => testMutation.mutate()}
              disabled={testMutation.isPending || !config || !isActive}
            >
              <Send className="w-4 h-4 mr-2" />
              Send Test Alert
            </Button>
            
            <Button 
              onClick={handleSave} 
              disabled={saveMutation.isPending}
            >
              {saveMutation.isPending ? (
                <Loader2 className="w-4 h-4 mr-2 animate-spin" />
              ) : (
                <Save className="w-4 h-4 mr-2" />
              )}
              Save Configuration
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
