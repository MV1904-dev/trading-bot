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
      <div>
        <p className="mt-3 text-sm text-muted">
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
    <div>
      <div className="mt-3 h-56 w-full">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={data} margin={{ top: 4, right: 4, bottom: 0, left: 0 }}>
            <defs>
              <linearGradient id="eq" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="var(--pos)" stopOpacity={0.35} />
                <stop offset="100%" stopColor="var(--pos)" stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid stroke="var(--line)" vertical={false} />
            <XAxis
              dataKey="t"
              type="number"
              domain={["dataMin", "dataMax"]}
              scale="time"
              tick={{ fill: "var(--faint)", fontSize: 11 }}
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
              tick={{ fill: "var(--faint)", fontSize: 11 }}
              tickFormatter={(v) => money(v, 0)}
            />
            <Tooltip
              contentStyle={{
                background: "var(--solid)",
                border: "1px solid var(--line)",
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
              wrapperStyle={{ fontSize: 12, color: "var(--muted)" }}
            />
            <Area
              type="monotone"
              dataKey="equity"
              stroke="var(--pos)"
              fill="url(#eq)"
              strokeWidth={2}
              dot={false}
            />
            <Area
              type="monotone"
              dataKey="balance"
              stroke="var(--faint)"
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
