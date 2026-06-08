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
  mappings: PositionGroupMapping[];
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
