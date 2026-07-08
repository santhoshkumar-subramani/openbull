import api from "@/config/api";

export interface PositionGroupMapping {
  id: number;
  group_id: number;
  symbol: string;
  exchange: string;
  product: string;
  created_at: string;
}

export interface PositionGroup {
  id: number;
  name: string;
  created_at: string;
  updated_at: string;
  stop_loss_enabled: boolean;
  stop_loss_mtm: number | null;
  profit_target_enabled: boolean;
  profit_target_mtm: number | null;
  risk_status: string;
  risk_last_mtm: number | null;
  risk_last_trigger_reason: string | null;
  risk_last_triggered_at: string | null;
  risk_last_error: string | null;
  risk_retry_count: number;
  risk_pending_symbols: string[];
  risk_force_close_requested: boolean;
  mappings: PositionGroupMapping[];
}

export interface PositionGroupRiskUpdate {
  stop_loss_enabled: boolean;
  stop_loss_mtm: number | null;
  profit_target_enabled: boolean;
  profit_target_mtm: number | null;
}

export interface PositionGroupRiskState {
  stop_loss_enabled: boolean;
  stop_loss_mtm: number | null;
  profit_target_enabled: boolean;
  profit_target_mtm: number | null;
  risk_status: string;
  risk_last_mtm: number | null;
  risk_last_trigger_reason: string | null;
  risk_last_triggered_at: string | null;
  risk_last_error: string | null;
  risk_retry_count: number;
  risk_pending_symbols: string[];
  risk_force_close_requested: boolean;
}

export async function getPositionGroups(): Promise<PositionGroup[]> {
  const response = await api.get<PositionGroup[]>("/web/position-groups");
  return response.data;
}

export async function createPositionGroup(name: string): Promise<PositionGroup> {
  const response = await api.post<PositionGroup>("/web/position-groups", { name });
  return response.data;
}

export async function deletePositionGroup(groupId: number): Promise<void> {
  await api.delete(`/web/position-groups/${groupId}`);
}

export async function renamePositionGroup(groupId: number, name: string): Promise<PositionGroup> {
  const response = await api.patch<PositionGroup>(`/web/position-groups/${groupId}`, { name });
  return response.data;
}

export async function assignPositionToGroup(groupId: number, symbol: string, exchange: string, product: string): Promise<PositionGroupMapping> {
  const response = await api.post<PositionGroupMapping>(`/web/position-groups/${groupId}/positions`, {
    symbol,
    exchange,
    product,
  });
  return response.data;
}

export async function unassignPosition(symbol: string, exchange: string, product: string): Promise<void> {
  await api.post("/web/position-groups/unassign", {
    symbol,
    exchange,
    product,
  });
}

export async function updatePositionGroupRisk(
  groupId: number,
  payload: PositionGroupRiskUpdate,
): Promise<PositionGroupRiskState> {
  const response = await api.patch<PositionGroupRiskState>(
    `/web/position-groups/${groupId}/risk`,
    payload,
  );
  return response.data;
}

export async function closePositionGroupNow(
  groupId: number,
): Promise<PositionGroupRiskState> {
  const response = await api.post<PositionGroupRiskState>(
    `/web/position-groups/${groupId}/close-now`,
  );
  return response.data;
}
