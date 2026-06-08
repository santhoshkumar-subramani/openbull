import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { CirclePlus, Trash2 } from "lucide-react";

import { getPositions } from "@/api/dashboard";
import {
  getPositionGroups,
  createPositionGroup,
  deletePositionGroup,
  assignPositionToGroup,
  unassignPosition,
  type PositionGroup,
} from "@/api/positionGroups";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { useLivePrice } from "@/hooks/useLivePrice";
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

export default function GroupedPositions() {
  const queryClient = useQueryClient();

  // State for creating a new group
  const [isCreateGroupOpen, setIsCreateGroupOpen] = useState(false);
  const [newGroupName, setNewGroupName] = useState("");

  // State for confirming group deletion
  const [groupToDelete, setGroupToDelete] = useState<PositionGroup | null>(null);

  // Queries
  const { data: positions, isLoading: isPositionsLoading } = useQuery({
    queryKey: ["positions"],
    queryFn: getPositions,
    refetchInterval: 15000,
  });

  const { data: groups, isLoading: isGroupsLoading } = useQuery({
    queryKey: ["positionGroups"],
    queryFn: getPositionGroups,
  });

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

      {/* Master Table */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
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
        </CardHeader>
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
                      <TableCell className="font-medium">{pos.symbol}</TableCell>
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
              {/* Footer Row for Master P&L */}
              <TableBody>
                <TableRow className="bg-muted/50 font-semibold hover:bg-muted/50">
                  <TableCell colSpan={7} className="text-right">
                    Total Account P&L
                  </TableCell>
                  <TableCell className={`text-right ${getPnlColor(masterPnl)}`}>
                    {masterPnl >= 0 ? "+" : ""}
                    {masterPnl.toFixed(2)}
                  </TableCell>
                  <TableCell />
                </TableRow>
              </TableBody>
            </Table>
          ) : (
            <p className="py-8 text-center text-sm text-muted-foreground">
              No open positions.
            </p>
          )}
        </CardContent>
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
           return (
            <Card key={group.id}>
              <CardHeader className="flex flex-row items-center justify-between space-y-0">
                <div>
                  <CardTitle>{group.name}</CardTitle>
                  <CardDescription>Empty Strategy Group</CardDescription>
                </div>
                <Button variant="outline" size="sm" onClick={() => setGroupToDelete(group)} className="text-destructive">
                  Delete Group
                </Button>
              </CardHeader>
              <CardContent>
                <p className="py-4 text-center text-sm text-muted-foreground">
                  No open positions in this group.
                </p>
              </CardContent>
            </Card>
           )
        }

        const groupPnl = groupPositions.reduce((acc, pos) => acc + pos.pnl, 0);

        return (
          <Card key={group.id}>
            <CardHeader className="flex flex-row items-center justify-between space-y-0">
              <div>
                <CardTitle>{group.name}</CardTitle>
                <CardDescription>{groupPositions.length} position{groupPositions.length !== 1 ? 's' : ''}</CardDescription>
              </div>
              <Button variant="outline" size="sm" onClick={() => setGroupToDelete(group)} className="text-destructive hover:bg-destructive/10">
                Delete Group
              </Button>
            </CardHeader>
            <CardContent>
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
                        <TableCell className="font-medium">{pos.symbol}</TableCell>
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
                {/* Footer Row for Group P&L */}
                <TableBody>
                  <TableRow className="bg-muted/50 font-semibold hover:bg-muted/50">
                    <TableCell colSpan={6} className="text-right">
                      Group P&L
                    </TableCell>
                    <TableCell className={`text-right ${getPnlColor(groupPnl)}`}>
                      {groupPnl >= 0 ? "+" : ""}
                      {groupPnl.toFixed(2)}
                    </TableCell>
                    <TableCell />
                  </TableRow>
                </TableBody>
              </Table>
            </CardContent>
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
