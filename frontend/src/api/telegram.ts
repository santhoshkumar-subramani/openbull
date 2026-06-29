import api from "@/config/api";

export interface TelegramConfig {
  id?: number;
  user_id?: number;
  bot_token: string;
  chat_id: string;
  is_active: boolean;
  created_at?: string;
  updated_at?: string;
}

export const getTelegramConfig = async (): Promise<TelegramConfig> => {
  const { data } = await api.get<TelegramConfig>("/api/telegram/config");
  return data;
};

export const saveTelegramConfig = async (
  config: Omit<TelegramConfig, "id" | "user_id" | "created_at" | "updated_at">
): Promise<TelegramConfig> => {
  const { data } = await api.post<TelegramConfig>("/api/telegram/config", config);
  return data;
};

export const toggleTelegramBot = async (
  isActive: boolean
): Promise<TelegramConfig> => {
  const { data } = await api.patch<TelegramConfig>(
    `/api/telegram/toggle?is_active=${isActive}`
  );
  return data;
};

export const testTelegramAlert = async (): Promise<{ message: string }> => {
  const { data } = await api.post<{ message: string }>("/api/telegram/test");
  return data;
};
