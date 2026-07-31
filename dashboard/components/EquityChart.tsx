"use client";

import {
  Area,
  AreaChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { money } from "@/lib/format";
import type { EquityPoint } from "@/lib/types";

export default function EquityChart({ points }: { points: EquityPoint[] }) {
  if (points.length === 0) {
    return (
      <div className="card">
        <span className="card-title">Equity</span>
        <p className="mt-3 text-sm text-zinc-400">
          Zatiaľ žiadne snapshoty — bot ich zapisuje každých 5 minút.
        </p>
      </div>
    );
  }

  const data = points.map((p) => ({
    t: new Date(p.ts).getTime(),
    balance: p.balance,
    equity: p.equity,
  }));

  return (
    <div className="card">
      <span className="card-title">Equity — realizovaná vs. s floatingom</span>
      <div className="mt-3 h-56 w-full">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={data} margin={{ top: 4, right: 4, bottom: 0, left: 0 }}>
            <defs>
              <linearGradient id="eq" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#34d399" stopOpacity={0.35} />
                <stop offset="100%" stopColor="#34d399" stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid stroke="#27272a" vertical={false} />
            <XAxis
              dataKey="t"
              type="number"
              domain={["dataMin", "dataMax"]}
              scale="time"
              tick={{ fill: "#71717a", fontSize: 11 }}
              tickFormatter={(v) =>
                new Date(v).toLocaleDateString("sk-SK", {
                  day: "2-digit",
                  month: "2-digit",
                })
              }
            />
            <YAxis
              domain={["auto", "auto"]}
              width={56}
              tick={{ fill: "#71717a", fontSize: 11 }}
              tickFormatter={(v) => money(v, 0)}
            />
            <Tooltip
              contentStyle={{
                background: "#18181b",
                border: "1px solid #3f3f46",
                borderRadius: 8,
                fontSize: 12,
              }}
              labelFormatter={(v) => new Date(Number(v)).toLocaleString("sk-SK")}
              formatter={(v, n) => [
                money(Number(v)),
                n === "equity" ? "s floatingom" : "realizovaná",
              ]}
            />
            <Legend
              formatter={(v) => (v === "equity" ? "s floatingom" : "realizovaná")}
              wrapperStyle={{ fontSize: 12, color: "#a1a1aa" }}
            />
            <Area
              type="monotone"
              dataKey="equity"
              stroke="#34d399"
              fill="url(#eq)"
              strokeWidth={2}
              dot={false}
            />
            <Area
              type="monotone"
              dataKey="balance"
              stroke="#71717a"
              fill="none"
              strokeWidth={1.5}
              strokeDasharray="4 3"
              dot={false}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
