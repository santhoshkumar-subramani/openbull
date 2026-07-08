import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { CirclePlus, Trash2, ChevronDown, ChevronRight } from "lucide-react";

import { getPositions } from "@/api/dashboard";
import {
  getPositionGroups,
  createPositionGroup,
  deletePositionGroup,
  assignPositionToGroup,
  unassignPosition,
  updatePositionGroupRisk,
  closePositionGroupNow,
  type PositionGroup,
} from "@/api/positionGroups";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { useLivePrice } from "@/hooks/useLivePrice";
import { useMarketData } from "@/hooks/useMarketData";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

import type { PositionItem } from "@/types/order";

function getPnlColor(value: number): string {
  if (value > 0) return "text-green-600 dark:text-green-400";
  if (value < 0) return "text-red-600 dark:text-red-400";
  return "";
}

function formatSymbol(symbol: string) {
  // Try matching symbols with an explicit DDMMMYY or YYMDD date format (e.g. 02JUL26 or 24O17)
  let match = symbol.match(/^(.*?)(\d{2}[A-Za-z]{1,4}\d{2})(\d+)(PE|CE)$/i);
  if (match) {
    return (
      <span>
        {match[1]}{match[2]}
        <strong className="font-extrabold">{match[3]}</strong>
        {match[4]}
      </span>
    );
  }

  // Fallback for symbols where the date format might just be YYMMM (e.g. 26JUL) or no date
  match = symbol.match(/^(.*?[A-Za-z])(\d+)(PE|CE)$/i);
  if (match) {
    // Only highlight if the digits aren't excessively long, minimizing risk of highlighting the year
    if (match[2].length <= 5) {
      return (
        <span>
          {match[1]}
          <strong className="font-extrabold">{match[2]}</strong>
          {match[3]}
        </span>
      );
    }
  }

  return <span>{symbol}</span>;
}

function isCloseable(p: PositionItem): boolean {
  return (p.quantity ?? 0) !== 0;
}

function formatError(err: any): string {
  const detail = err?.response?.data?.detail;
  if (Array.isArray(detail)) {
    return detail[0]?.msg || "An error occurred";
  }
  if (typeof detail === "string") {
    return detail;
  }
  return err?.message || "An unknown error occurred";
}

const INDEX_SYMBOLS = [
  { symbol: "INDIAVIX", exchange: "NSE_INDEX", label: "India Vix" },
  { symbol: "NIFTY", exchange: "NSE_INDEX", label: "Nifty 50" },
  { symbol: "BANKNIFTY", exchange: "NSE_INDEX", label: "Bank Nifty" },
  { symbol: "SENSEX", exchange: "BSE_INDEX", label: "Sensex" },
];

function LiveIndices() {
  const { data } = useMarketData({
    symbols: INDEX_SYMBOLS,
    mode: "Quote",
  });

  return (
    <div className="flex flex-wrap items-center gap-6 rounded-md border bg-card p-4 shadow-sm">
      {INDEX_SYMBOLS.map((idx) => {
        const key = `${idx.exchange}:${idx.symbol}`;
        const tick = data.get(key)?.data;
        
        const ltp = tick?.ltp;
        let change = tick?.change;
        let pChange = tick?.change_percent;
        
        if (ltp != null && tick?.close != null && change == null) {
          change = ltp - tick.close;
        }
        if (ltp != null && tick?.close != null && pChange == null && tick.close !== 0) {
          pChange = ((ltp - tick.close) / tick.close) * 100;
        }

        const isPositive = change && change > 0;
        const isNegative = change && change < 0;
        
        const colorClass = isPositive 
          ? "text-green-600 dark:text-green-400" 
          : isNegative 
            ? "text-red-600 dark:text-red-400" 
            : "text-foreground";
            
        return (
          <div key={key} className="flex flex-col min-w-[140px]">
            <span className="text-sm font-medium text-muted-foreground">{idx.label}</span>
            <span className="text-lg font-bold text-foreground mt-0.5">
              {ltp != null ? ltp.toFixed(2) : "-"}
            </span>
            {(change != null && pChange != null) ? (
              <span className={`text-xs font-medium mt-0.5 ${colorClass}`}>
                {change > 0 ? "+" : ""}{change.toFixed(2)} ({change > 0 ? "+" : ""}{pChange.toFixed(2)}%)
              </span>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

export default function GroupedPositions() {
  const queryClient = useQueryClient();

  // State for creating a new group
  const [isCreateGroupOpen, setIsCreateGroupOpen] = useState(false);
  const [newGroupName, setNewGroupName] = useState("");

  // State for confirming group deletion
  const [groupToDelete, setGroupToDelete] = useState<PositionGroup | null>(null);

  // Folded state for tables
  const [foldedState, setFoldedState] = useState<Record<string, boolean>>({});
  const [riskDrafts, setRiskDrafts] = useState<
    Record<
      number,
      {
        stopLossEnabled: boolean;
        stopLossMtm: string;
        profitEnabled: boolean;
        profitMtm: string;
      }
    >
  >({});
  const [savingRiskGroupId, setSavingRiskGroupId] = useState<number | null>(null);
  const [closingNowGroupId, setClosingNowGroupId] = useState<number | null>(null);

  const toggleFold = (key: string) => {
    setFoldedState((prev) => ({ ...prev, [key]: !prev[key] }));
  };

  const foldAll = () => {
    const next: Record<string, boolean> = { master: true };
    groups?.forEach((g) => {
      next[g.id.toString()] = true;
    });
    setFoldedState(next);
  };

  const expandAll = () => {
    setFoldedState({});
  };

  // Queries
  const { data: positions, isLoading: isPositionsLoading } = useQuery({
    queryKey: ["positions"],
    queryFn: getPositions,
    refetchInterval: 15000,
  });

  const { data: groups, isLoading: isGroupsLoading } = useQuery({
    queryKey: ["positionGroups"],
    queryFn: getPositionGroups,
    refetchInterval: 3000,
  });

  useEffect(() => {
    if (!groups) return;
    const next: Record<
      number,
      {
        stopLossEnabled: boolean;
        stopLossMtm: string;
        profitEnabled: boolean;
        profitMtm: string;
      }
    > = {};
    for (const g of groups) {
      next[g.id] = {
        stopLossEnabled: !!g.stop_loss_enabled,
        stopLossMtm: g.stop_loss_mtm != null ? String(g.stop_loss_mtm) : "",
        profitEnabled: !!g.profit_target_enabled,
        profitMtm: g.profit_target_mtm != null ? String(g.profit_target_mtm) : "",
      };
    }
    setRiskDrafts(next);
  }, [groups]);

  const openPositions = useMemo(
    () => (positions ?? []).filter(isCloseable),
    [positions],
  );

  const { data: livePositions, isLive, isPaused } = useLivePrice(openPositions, {
    enabled: openPositions.length > 0,
  });

  // Mutations
  const createGroupMutation = useMutation({
    mutationFn: createPositionGroup,
    onSuccess: () => {
      toast.success("Group created");
      queryClient.invalidateQueries({ queryKey: ["positionGroups"] });
      setIsCreateGroupOpen(false);
      setNewGroupName("");
    },
    onError: (err: any) => {
      toast.error(formatError(err));
    },
  });

  const deleteGroupMutation = useMutation({
    mutationFn: deletePositionGroup,
    onSuccess: () => {
      toast.success("Group deleted");
      queryClient.invalidateQueries({ queryKey: ["positionGroups"] });
      setGroupToDelete(null);
    },
    onError: (err: any) => {
      toast.error(formatError(err));
    },
  });

  const assignMutation = useMutation({
    mutationFn: ({
      groupId,
      pos,
    }: {
      groupId: number;
      pos: PositionItem;
    }) => assignPositionToGroup(groupId, pos.symbol, pos.exchange, pos.product),
    onSuccess: () => {
      toast.success("Position assigned");
      queryClient.invalidateQueries({ queryKey: ["positionGroups"] });
    },
    onError: (err: any) => {
      toast.error(formatError(err));
    },
  });

  const unassignMutation = useMutation({
    mutationFn: (pos: PositionItem) =>
      unassignPosition(pos.symbol, pos.exchange, pos.product),
    onSuccess: () => {
      toast.success("Position unassigned");
      queryClient.invalidateQueries({ queryKey: ["positionGroups"] });
    },
    onError: (err: any) => {
      toast.error(formatError(err));
    },
  });

  const updateRiskMutation = useMutation({
    mutationFn: ({
      groupId,
      stopLossEnabled,
      stopLossMtm,
      profitEnabled,
      profitMtm,
    }: {
      groupId: number;
      stopLossEnabled: boolean;
      stopLossMtm: string;
      profitEnabled: boolean;
      profitMtm: string;
    }) =>
      updatePositionGroupRisk(groupId, {
        stop_loss_enabled: stopLossEnabled,
        stop_loss_mtm: stopLossEnabled ? Number(stopLossMtm) : null,
        profit_target_enabled: profitEnabled,
        profit_target_mtm: profitEnabled ? Number(profitMtm) : null,
      }),
    onMutate: ({ groupId }) => {
      setSavingRiskGroupId(groupId);
    },
    onSuccess: () => {
      toast.success("Risk settings saved");
      queryClient.invalidateQueries({ queryKey: ["positionGroups"] });
    },
    onError: (err: any) => {
      toast.error(formatError(err));
    },
    onSettled: () => {
      setSavingRiskGroupId(null);
    },
  });

  const closeNowMutation = useMutation({
    mutationFn: (groupId: number) => closePositionGroupNow(groupId),
    onMutate: (groupId) => {
      setClosingNowGroupId(groupId);
    },
    onSuccess: () => {
      toast.success("Manual close requested");
      queryClient.invalidateQueries({ queryKey: ["positionGroups"] });
    },
    onError: (err: any) => {
      toast.error(formatError(err));
    },
    onSettled: () => {
      setClosingNowGroupId(null);
    },
  });

  // Derived state
  const mappingMap = useMemo(() => {
    // Map: symbol-exchange-product -> group name
    const map = new Map<string, string>();
    if (groups) {
      groups.forEach((g) => {
        g.mappings.forEach((m) => {
          map.set(`${m.symbol}-${m.exchange}-${m.product}`, g.name);
        });
      });
    }
    return map;
  }, [groups]);

  const masterPnl = livePositions.reduce((acc, pos) => acc + pos.pnl, 0);

  const updateRiskDraft = (
    groupId: number,
    patch: Partial<{
      stopLossEnabled: boolean;
      stopLossMtm: string;
      profitEnabled: boolean;
      profitMtm: string;
    }>,
  ) => {
    setRiskDrafts((prev) => ({
      ...prev,
      [groupId]: {
        ...(prev[groupId] ?? {
          stopLossEnabled: false,
          stopLossMtm: "",
          profitEnabled: false,
          profitMtm: "",
        }),
        ...patch,
      },
    }));
  };

  const parsePositiveThreshold = (raw: string, label: string): number | null => {
    const value = Number(raw);
    if (!Number.isFinite(value) || value <= 0) {
      toast.error(`${label} must be greater than 0`);
      return null;
    }
    return value;
  };

  const submitRiskSettings = (groupId: number) => {
    const draft = riskDrafts[groupId];
    if (!draft) return;

    let slValue: number | null = null;
    let targetValue: number | null = null;
    if (draft.stopLossEnabled) {
      slValue = parsePositiveThreshold(draft.stopLossMtm, "Stop Loss");
      if (slValue == null) return;
    }
    if (draft.profitEnabled) {
      targetValue = parsePositiveThreshold(draft.profitMtm, "Profit Booking");
      if (targetValue == null) return;
    }

    updateRiskMutation.mutate({
      groupId,
      stopLossEnabled: draft.stopLossEnabled,
      stopLossMtm: slValue != null ? String(slValue) : "",
      profitEnabled: draft.profitEnabled,
      profitMtm: targetValue != null ? String(targetValue) : "",
    });
  };

  const riskStatusTone = (status: string) => {
    if (status === "closing" || status === "triggered") return "destructive" as const;
    if (status === "failed") return "destructive" as const;
    if (status === "monitoring") return "outline" as const;
    if (status === "succeeded") return "secondary" as const;
    return "outline" as const;
  };

  if (isPositionsLoading || isGroupsLoading) {
    return (
      <div className="flex items-center justify-center py-20">
        <div className="flex flex-col items-center gap-4">
          <div className="h-8 w-8 animate-spin rounded-full border-4 border-muted border-t-primary" />
          <p className="text-sm text-muted-foreground">Loading...</p>
        </div>
      </div>
    );
  }

  const handleCreateGroupSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!newGroupName.trim()) return;
    createGroupMutation.mutate(newGroupName.trim());
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Grouped Positions</h1>
          <p className="text-sm text-muted-foreground">Manage your positions by strategy</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="outline" onClick={foldAll}>Fold All</Button>
          <Button variant="outline" onClick={expandAll}>Expand All</Button>
          <Button onClick={() => setIsCreateGroupOpen(true)}>
            Create New Strategy Group
          </Button>
        </div>
      </div>

      {isPaused && (
        <div className="rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm text-amber-700 dark:text-amber-400">
          Live updates paused (tab inactive) — showing last fetched prices.
        </div>
      )}

      {/* Live Indices */}
      <LiveIndices />

      {/* Master Table */}
      <Card>
        <CardHeader 
          className="cursor-pointer select-none flex flex-row items-start justify-between space-y-0 pb-4 transition-colors hover:bg-muted/30"
          onClick={() => toggleFold('master')}
        >
          <div className="flex flex-col gap-1.5">
            <CardTitle className="flex items-center gap-2">
              {foldedState['master'] ? <ChevronRight className="h-5 w-5 text-muted-foreground shrink-0" /> : <ChevronDown className="h-5 w-5 text-muted-foreground shrink-0" />}
              Master Positions
              {isLive ? (
                <Badge
                  variant="outline"
                  className="gap-1 border-green-500/40 text-green-600 dark:text-green-400"
                >
                  <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-green-500" />
                  Live
                </Badge>
              ) : isPaused ? (
                <Badge variant="outline" className="text-muted-foreground">
                  Paused
                </Badge>
              ) : null}
            </CardTitle>
            <CardDescription>
              All open positions across your account.
            </CardDescription>
          </div>
          <div className="text-right">
            <p className="text-sm font-medium text-muted-foreground">Total P&L</p>
            <p className={`text-xl font-bold ${getPnlColor(masterPnl)}`}>
              {masterPnl >= 0 ? "+" : ""}
              {masterPnl.toFixed(2)}
            </p>
          </div>
        </CardHeader>
        {!foldedState['master'] && (
          <CardContent>
            {livePositions.length > 0 ? (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Symbol</TableHead>
                  <TableHead>Exchange</TableHead>
                  <TableHead>Product</TableHead>
                  <TableHead>Group</TableHead>
                  <TableHead className="text-right">Qty</TableHead>
                  <TableHead className="text-right">Avg Price</TableHead>
                  <TableHead className="text-right">LTP</TableHead>
                  <TableHead className="text-right">P&L</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {livePositions.map((pos, i) => {
                  const key = `${pos.symbol}-${pos.exchange}-${pos.product}`;
                  const groupName = mappingMap.get(key);

                  return (
                    <TableRow key={key} className={i % 2 === 0 ? "bg-muted/30" : ""}>
                      <TableCell className="font-medium">{formatSymbol(pos.symbol)}</TableCell>
                      <TableCell>{pos.exchange}</TableCell>
                      <TableCell>{pos.product}</TableCell>
                      <TableCell>
                        {groupName ? (
                          <Badge variant="secondary">{groupName}</Badge>
                        ) : (
                          <span className="text-muted-foreground text-xs">-</span>
                        )}
                      </TableCell>
                      <TableCell className="text-right">{pos.quantity}</TableCell>
                      <TableCell className="text-right">{pos.average_price.toFixed(2)}</TableCell>
                      <TableCell className="text-right">{pos.ltp.toFixed(2)}</TableCell>
                      <TableCell className={`text-right font-medium ${getPnlColor(pos.pnl)}`}>
                        {pos.pnl >= 0 ? "+" : ""}
                        {pos.pnl.toFixed(2)}
                      </TableCell>
                      <TableCell className="text-right">
                        <DropdownMenu>
                          <DropdownMenuTrigger
                            disabled={!!groupName}
                            render={
                              <Button 
                                variant="ghost" 
                                className={`h-8 w-8 p-0 ${!groupName ? 'text-green-600 hover:text-green-700 hover:bg-green-100 dark:text-green-500 dark:hover:bg-green-950/50' : 'text-muted-foreground opacity-50'}`}
                                disabled={!!groupName}
                              >
                                <span className="sr-only">Assign to group</span>
                                <CirclePlus className="h-5 w-5" />
                              </Button>
                            }
                          />
                          {!groupName && (
                            <DropdownMenuContent align="end">
                              <DropdownMenuGroup>
                                <DropdownMenuLabel>Assign to Group</DropdownMenuLabel>
                                <DropdownMenuSeparator />
                                {groups && groups.length > 0 ? (
                                  groups.map((g) => (
                                    <DropdownMenuItem
                                      key={g.id}
                                      onClick={() => assignMutation.mutate({ groupId: g.id, pos })}
                                    >
                                      {g.name}
                                    </DropdownMenuItem>
                                  ))
                                ) : (
                                  <DropdownMenuItem disabled>No groups available</DropdownMenuItem>
                                )}
                              </DropdownMenuGroup>
                            </DropdownMenuContent>
                          )}
                        </DropdownMenu>
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          ) : (
            <p className="py-8 text-center text-sm text-muted-foreground">
              No open positions.
            </p>
          )}
        </CardContent>
        )}
      </Card>

      {/* Group Tables */}
      {groups?.map((group) => {
        // Find the positions that belong to this group
        const assignedKeys = new Set(
          group.mappings.map((m) => `${m.symbol}-${m.exchange}-${m.product}`)
        );
        const groupPositions = livePositions.filter((pos) =>
          assignedKeys.has(`${pos.symbol}-${pos.exchange}-${pos.product}`)
        );

        if (groupPositions.length === 0) {
          const draft =
            riskDrafts[group.id] ??
            {
              stopLossEnabled: false,
              stopLossMtm: "",
              profitEnabled: false,
              profitMtm: "",
            };
           return (
            <Card key={group.id}>
              <CardHeader 
                className="cursor-pointer select-none flex flex-row items-start justify-between space-y-0 pb-4 transition-colors hover:bg-muted/30"
                onClick={() => toggleFold(group.id.toString())}
              >
                <div className="flex flex-col gap-1.5">
                  <CardTitle className="flex items-center gap-2">
                    {foldedState[group.id.toString()] ? <ChevronRight className="h-5 w-5 text-muted-foreground shrink-0" /> : <ChevronDown className="h-5 w-5 text-muted-foreground shrink-0" />}
                    {group.name}
                    <Button 
                      variant="ghost" 
                      size="sm" 
                      onClick={(e) => { e.stopPropagation(); setGroupToDelete(group); }} 
                      className="h-8 w-8 p-0 text-red-600 hover:bg-red-100 hover:text-red-700 dark:text-red-500 dark:hover:bg-red-950/50 ml-2"
                      title="Delete Group"
                    >
                      <span className="sr-only">Delete Group</span>
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </CardTitle>
                  <CardDescription>Empty Strategy Group</CardDescription>
                </div>
                <div className="text-right" onClick={(e) => e.stopPropagation()}>
                  <p className="text-sm font-medium text-muted-foreground">Group P&L</p>
                  <p className="text-xl font-bold text-muted-foreground">0.00</p>
                </div>
              </CardHeader>
              {!foldedState[group.id.toString()] && (
                <CardContent>
                  <div className="mb-4 rounded-md border p-3">
                    <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                      <p className="text-sm font-medium">Auto Risk Controls</p>
                      <Badge variant={riskStatusTone(group.risk_status)}>
                        {group.risk_status}
                      </Badge>
                    </div>
                    <div className="grid gap-3 sm:grid-cols-2">
                      <label className="flex items-center gap-2 text-sm">
                        <input
                          type="checkbox"
                          checked={draft.stopLossEnabled}
                          onChange={(e) =>
                            updateRiskDraft(group.id, { stopLossEnabled: e.target.checked })
                          }
                        />
                        Stop Loss (INR)
                      </label>
                      <Input
                        type="number"
                        min={1}
                        step={1}
                        placeholder="e.g. 6500"
                        value={draft.stopLossMtm}
                        disabled={!draft.stopLossEnabled}
                        onChange={(e) =>
                          updateRiskDraft(group.id, { stopLossMtm: e.target.value })
                        }
                      />
                      <label className="flex items-center gap-2 text-sm">
                        <input
                          type="checkbox"
                          checked={draft.profitEnabled}
                          onChange={(e) =>
                            updateRiskDraft(group.id, { profitEnabled: e.target.checked })
                          }
                        />
                        Profit Booking (INR)
                      </label>
                      <Input
                        type="number"
                        min={1}
                        step={1}
                        placeholder="e.g. 5000"
                        value={draft.profitMtm}
                        disabled={!draft.profitEnabled}
                        onChange={(e) =>
                          updateRiskDraft(group.id, { profitMtm: e.target.value })
                        }
                      />
                    </div>
                    <p className="mt-2 text-xs text-muted-foreground">
                      Stop loss value is positive input. Trigger happens when group P&amp;L is less than or equal to negative of this amount.
                    </p>
                    {group.risk_last_error ? (
                      <p className="mt-2 text-xs text-red-600 dark:text-red-400">
                        {group.risk_last_error}
                      </p>
                    ) : null}
                    <div className="mt-3 flex flex-wrap gap-2">
                      <Button
                        size="sm"
                        onClick={() => submitRiskSettings(group.id)}
                        disabled={savingRiskGroupId === group.id}
                      >
                        {savingRiskGroupId === group.id ? "Saving..." : "Save Risk Settings"}
                      </Button>
                      <Button
                        size="sm"
                        variant="destructive"
                        onClick={() => closeNowMutation.mutate(group.id)}
                        disabled={closingNowGroupId === group.id}
                      >
                        {closingNowGroupId === group.id ? "Requesting..." : "Close Group Now"}
                      </Button>
                    </div>
                  </div>
                  <p className="py-4 text-center text-sm text-muted-foreground">
                    No open positions in this group.
                  </p>
                </CardContent>
              )}
            </Card>
           )
        }

        const groupPnl = groupPositions.reduce((acc, pos) => acc + pos.pnl, 0);
        const draft =
          riskDrafts[group.id] ??
          {
            stopLossEnabled: false,
            stopLossMtm: "",
            profitEnabled: false,
            profitMtm: "",
          };

        return (
          <Card key={group.id}>
            <CardHeader 
              className="cursor-pointer select-none flex flex-row items-start justify-between space-y-0 pb-4 transition-colors hover:bg-muted/30"
              onClick={() => toggleFold(group.id.toString())}
            >
              <div className="flex flex-col gap-1.5">
                <CardTitle className="flex items-center gap-2">
                  {foldedState[group.id.toString()] ? <ChevronRight className="h-5 w-5 text-muted-foreground shrink-0" /> : <ChevronDown className="h-5 w-5 text-muted-foreground shrink-0" />}
                  {group.name}
                  <Badge variant={riskStatusTone(group.risk_status)}>{group.risk_status}</Badge>
                  <Button 
                    variant="ghost" 
                    size="sm" 
                    onClick={(e) => { e.stopPropagation(); setGroupToDelete(group); }} 
                    className="h-8 w-8 p-0 text-red-600 hover:bg-red-100 hover:text-red-700 dark:text-red-500 dark:hover:bg-red-950/50 ml-2"
                    title="Delete Group"
                  >
                    <span className="sr-only">Delete Group</span>
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </CardTitle>
                <CardDescription>{groupPositions.length} position{groupPositions.length !== 1 ? 's' : ''}</CardDescription>
              </div>
              <div className="text-right" onClick={(e) => e.stopPropagation()}>
                <p className="text-sm font-medium text-muted-foreground">Group P&L</p>
                <p className={`text-xl font-bold ${getPnlColor(groupPnl)}`}>
                  {groupPnl >= 0 ? "+" : ""}
                  {groupPnl.toFixed(2)}
                </p>
              </div>
            </CardHeader>
            {!foldedState[group.id.toString()] && (
              <CardContent>
                <div className="mb-4 rounded-md border p-3">
                  <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                    <p className="text-sm font-medium">Auto Risk Controls</p>
                    <p className="text-xs text-muted-foreground">
                      Retry: {group.risk_retry_count} / 20
                    </p>
                  </div>
                  <div className="grid gap-3 sm:grid-cols-2">
                    <label className="flex items-center gap-2 text-sm">
                      <input
                        type="checkbox"
                        checked={draft.stopLossEnabled}
                        onChange={(e) =>
                          updateRiskDraft(group.id, { stopLossEnabled: e.target.checked })
                        }
                      />
                      Stop Loss (INR)
                    </label>
                    <Input
                      type="number"
                      min={1}
                      step={1}
                      placeholder="e.g. 6500"
                      value={draft.stopLossMtm}
                      disabled={!draft.stopLossEnabled}
                      onChange={(e) =>
                        updateRiskDraft(group.id, { stopLossMtm: e.target.value })
                      }
                    />
                    <label className="flex items-center gap-2 text-sm">
                      <input
                        type="checkbox"
                        checked={draft.profitEnabled}
                        onChange={(e) =>
                          updateRiskDraft(group.id, { profitEnabled: e.target.checked })
                        }
                      />
                      Profit Booking (INR)
                    </label>
                    <Input
                      type="number"
                      min={1}
                      step={1}
                      placeholder="e.g. 5000"
                      value={draft.profitMtm}
                      disabled={!draft.profitEnabled}
                      onChange={(e) =>
                        updateRiskDraft(group.id, { profitMtm: e.target.value })
                      }
                    />
                  </div>
                  <p className="mt-2 text-xs text-muted-foreground">
                    Stop loss value is positive input. Trigger happens when group P&amp;L is less than or equal to negative of this amount.
                  </p>
                  {group.risk_last_error ? (
                    <p className="mt-2 text-xs text-red-600 dark:text-red-400">{group.risk_last_error}</p>
                  ) : null}
                  <div className="mt-3 flex flex-wrap gap-2">
                    <Button
                      size="sm"
                      onClick={() => submitRiskSettings(group.id)}
                      disabled={savingRiskGroupId === group.id}
                    >
                      {savingRiskGroupId === group.id ? "Saving..." : "Save Risk Settings"}
                    </Button>
                    <Button
                      size="sm"
                      variant="destructive"
                      onClick={() => closeNowMutation.mutate(group.id)}
                      disabled={closingNowGroupId === group.id}
                    >
                      {closingNowGroupId === group.id ? "Requesting..." : "Close Group Now"}
                    </Button>
                  </div>
                </div>
                <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Symbol</TableHead>
                    <TableHead>Exchange</TableHead>
                    <TableHead>Product</TableHead>
                    <TableHead className="text-right">Qty</TableHead>
                    <TableHead className="text-right">Avg Price</TableHead>
                    <TableHead className="text-right">LTP</TableHead>
                    <TableHead className="text-right">P&L</TableHead>
                    <TableHead className="text-right">Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {groupPositions.map((pos, i) => {
                    const key = `${pos.symbol}-${pos.exchange}-${pos.product}`;
                    return (
                      <TableRow key={key} className={i % 2 === 0 ? "bg-muted/30" : ""}>
                        <TableCell className="font-medium">{formatSymbol(pos.symbol)}</TableCell>
                        <TableCell>{pos.exchange}</TableCell>
                        <TableCell>{pos.product}</TableCell>
                        <TableCell className="text-right">{pos.quantity}</TableCell>
                        <TableCell className="text-right">{pos.average_price.toFixed(2)}</TableCell>
                        <TableCell className="text-right">{pos.ltp.toFixed(2)}</TableCell>
                        <TableCell className={`text-right font-medium ${getPnlColor(pos.pnl)}`}>
                          {pos.pnl >= 0 ? "+" : ""}
                          {pos.pnl.toFixed(2)}
                        </TableCell>
                        <TableCell className="text-right">
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => unassignMutation.mutate(pos)}
                            className="h-8 w-8 p-0 text-red-600 hover:bg-red-100 hover:text-red-700 dark:text-red-500 dark:hover:bg-red-950/50"
                            title="Remove from group"
                          >
                            <span className="sr-only">Remove</span>
                            <Trash2 className="h-4 w-4" />
                          </Button>
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </CardContent>
          )}
          </Card>
        );
      })}

      {/* Create Group Dialog */}
      <Dialog open={isCreateGroupOpen} onOpenChange={setIsCreateGroupOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Create Strategy Group</DialogTitle>
            <DialogDescription>
              Enter a name for the new strategy group to organize your positions.
            </DialogDescription>
          </DialogHeader>
          <form onSubmit={handleCreateGroupSubmit}>
            <div className="grid gap-4 py-4">
              <div className="grid grid-cols-4 items-center gap-4">
                <Label htmlFor="name" className="text-right">
                  Name
                </Label>
                <Input
                  id="name"
                  value={newGroupName}
                  onChange={(e) => setNewGroupName(e.target.value)}
                  className="col-span-3"
                  placeholder="e.g., Bull Call Spread Nifty"
                  autoFocus
                />
              </div>
            </div>
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => setIsCreateGroupOpen(false)}>
                Cancel
              </Button>
              <Button type="submit" disabled={createGroupMutation.isPending || !newGroupName.trim()}>
                {createGroupMutation.isPending ? "Creating..." : "Create Group"}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {/* Delete Group Dialog */}
      <ConfirmDialog
        open={groupToDelete !== null}
        onOpenChange={(o) => {
          if (!o && !deleteGroupMutation.isPending) {
            setGroupToDelete(null);
          }
        }}
        title="Delete Strategy Group"
        description={
          <>
            Are you sure you want to delete the group{" "}
            <strong>{groupToDelete?.name}</strong>? Any positions assigned to this group will be unassigned, but they will not be closed.
          </>
        }
        confirmLabel="Delete Group"
        cancelLabel="Cancel"
        variant="destructive"
        loading={deleteGroupMutation.isPending}
        onConfirm={() => {
          if (groupToDelete) {
            deleteGroupMutation.mutate(groupToDelete.id);
          }
        }}
      />
    </div>
  );
}
