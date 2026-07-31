export type BotState = {
  id: string;
  running: boolean;
  paused: boolean;
  paused_until: string | null;
  blocked_reason: string | null;
  failsafe: boolean;
  broker_connected: boolean;
  symbol: string | null;
  last_price: number | null;
  band_low: number | null;
  band_high: number | null;
  balance: number | null;
  equity: number | null;
  floating_pnl: number | null;
  config: Record<string, unknown> | null;
  deposits_net: number | null;
  deposits_count: number | null;
  used_margin: number | null;
  free_margin: number | null;
  margin_level: number | null;
  swap_long: number | null;
  swap_short: number | null;
  swap_rollover_3days: number | null;
  heartbeat_at: string;
  updated_at: string;
};

export type Position = {
  id: number;
  broker_position_id: number | null;
  strategy: string;
  symbol: string;
  side: "long" | "short";
  qty: number;
  entry_price: number;
  tp_price: number | null;
  opened_at: string;
  funding_usd: number;
  commission_usd: number;
  pnl_float: number | null;
  spread_at_entry: number | null;
  spread_cost_usd: number | null;
};

export type Trade = {
  id: number;
  strategy: string;
  symbol: string;
  side: "long" | "short";
  qty: number;
  entry_price: number;
  tp_price: number | null;
  close_price: number | null;
  opened_at: string;
  closed_at: string | null;
  pnl_usd: number | null;
  gross_pnl_usd: number | null;
  commission_usd: number;
  funding_usd: number;
  spread_cost_usd: number | null;
  manual_close: boolean;
};

export type EquityPoint = {
  ts: string;
  balance: number;
  equity: number;
  floating_pnl: number;
  open_positions: number;
};

export type DailyCycle = {
  day: string;
  strategy: string;
  cycles: number;
  pnl_usd: number;
  commission_usd: number;
  funding_usd: number;
  wins: number;
  losses: number;
};

export type Command = {
  id: string;
  action: "close" | "pause" | "start";
  position_id: number | null;
  status: "pending" | "running" | "done" | "failed";
  result: string | null;
  created_at: string;
  executed_at: string | null;
};

export type CalendarEvent = {
  ts: string;
  currency: string;
  title: string;
  impact: string;
};
