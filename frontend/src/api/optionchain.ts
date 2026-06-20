import api from "@/config/api";
import { getApiKey } from "@/api/apikey";
import type {
  ExpiryResponse,
  FnoExchange,
  OptionChainResponse,
  PlaceOrderRequest,
  PlaceOrderResponse,
  UnderlyingsResponse,
} from "@/types/optionchain";

let cachedApiKey: string | null = null;

async function resolveApiKey(): Promise<string> {
  if (cachedApiKey) return cachedApiKey;
  const { api_key } = await getApiKey();
  if (!api_key) {
    throw new Error("No API key found. Generate one on the API Key page first.");
  }
  cachedApiKey = api_key;
  return api_key;
}

export function clearCachedApiKey(): void {
  cachedApiKey = null;
}

export async function fetchOptionChain(params: {
  underlying: string;
  exchange: string;
  expiry_date: string;
  strike_count: number;
}): Promise<OptionChainResponse> {
  const apikey = await resolveApiKey();
  try {
    const response = await api.post<OptionChainResponse>("/api/v1/optionchain", {
      apikey,
      ...params,
    });
    return response.data;
  } catch (error: any) {
    if (error.response?.status === 404) {
      return { status: "error", message: "Not found" } as any;
    }
    throw error;
  }
}

export async function fetchExpiries(params: {
  symbol: string;
  exchange: string;
  instrumenttype?: string;
}): Promise<ExpiryResponse> {
  const apikey = await resolveApiKey();
  try {
    const response = await api.post<ExpiryResponse>("/api/v1/expiry", {
      apikey,
      instrumenttype: params.instrumenttype ?? "options",
      ...params,
    });
    return response.data;
  } catch (error: any) {
    if (error.response?.status === 404) {
      // Return a successful but empty response so the UI gracefully skips it
      return { status: "success", data: [] } as any;
    }
    throw error;
  }
}

export async function fetchUnderlyings(exchange: FnoExchange): Promise<UnderlyingsResponse> {
  const response = await api.get<UnderlyingsResponse>("/web/symbols/underlyings", {
    params: { exchange },
  });
  return response.data;
}

export async function placeOrder(req: Omit<PlaceOrderRequest, "apikey">): Promise<PlaceOrderResponse> {
  const apikey = await resolveApiKey();
  const response = await api.post<PlaceOrderResponse>("/api/v1/placeorder", {
    apikey,
    ...req,
  });
  return response.data;
}

export async function placeSmartOrder(req: Omit<PlaceOrderRequest & { position_size: number }, "apikey">): Promise<PlaceOrderResponse> {
  const apikey = await resolveApiKey();
  const response = await api.post<PlaceOrderResponse>("/api/v1/placesmartorder", {
    apikey,
    ...req,
  });
  return response.data;
}
