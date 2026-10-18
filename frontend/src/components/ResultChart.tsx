import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { ChartSpec, ResultTable, Scalar } from "../types";

const COLORS = ["#4f46e5", "#16a34a", "#f59e0b", "#dc2626", "#0891b2", "#9333ea", "#db2777", "#65a30d"];

type Row = Record<string, Scalar>;

function toRecords(table: ResultTable): Row[] {
  return table.rows.map((row) => Object.fromEntries(table.columns.map((c, i) => [c, row[i]])));
}

/** Long → wide: one row per x value, one numeric column per series value. */
function pivot(rows: Row[], x: string, series: string, y: string): { data: Row[]; keys: string[] } {
  const byX = new Map<string, Row>();
  const keys = new Set<string>();
  for (const row of rows) {
    const xKey = String(row[x]);
    const key = String(row[series]);
    keys.add(key);
    const target = byX.get(xKey) ?? { [x]: row[x] };
    target[key] = row[y];
    byX.set(xKey, target);
  }
  return { data: [...byX.values()], keys: [...keys] };
}

export default function ResultChart({ spec, table }: { spec: ChartSpec; table: ResultTable }) {
  const records = toRecords(table);
  const { x, chart_type: type } = spec;
  const { data, keys } =
    spec.series && spec.y.length === 1 ? pivot(records, x, spec.series, spec.y[0]) : { data: records, keys: spec.y };

  const axes = (
    <>
      <CartesianGrid strokeDasharray="3 3" stroke="#e3e6ee" />
      <XAxis dataKey={x} tick={{ fontSize: 12 }} />
      <YAxis tick={{ fontSize: 12 }} />
      <Tooltip />
      {keys.length > 1 && <Legend />}
    </>
  );

  let chart;
  if (type === "line") {
    chart = (
      <LineChart data={data}>
        {axes}
        {keys.map((k, i) => (
          <Line key={k} dataKey={k} stroke={COLORS[i % COLORS.length]} strokeWidth={2} dot={false} />
        ))}
      </LineChart>
    );
  } else if (type === "pie") {
    chart = (
      <PieChart>
        <Tooltip />
        <Legend />
        <Pie data={data} dataKey={keys[0]} nameKey={x} outerRadius="80%">
          {data.map((_, i) => (
            <Cell key={i} fill={COLORS[i % COLORS.length]} />
          ))}
        </Pie>
      </PieChart>
    );
  } else if (type === "scatter") {
    chart = (
      <ScatterChart>
        <CartesianGrid strokeDasharray="3 3" stroke="#e3e6ee" />
        <XAxis dataKey={x} type="number" name={x} tick={{ fontSize: 12 }} />
        <YAxis dataKey={keys[0]} type="number" name={keys[0]} tick={{ fontSize: 12 }} />
        <Tooltip />
        <Scatter data={data} fill={COLORS[0]} />
      </ScatterChart>
    );
  } else {
    chart = (
      <BarChart data={data}>
        {axes}
        {keys.map((k, i) => (
          <Bar key={k} dataKey={k} fill={COLORS[i % COLORS.length]} stackId={spec.series ? "s" : undefined} />
        ))}
      </BarChart>
    );
  }

  return (
    <figure className="result-chart">
      {spec.title && <figcaption>{spec.title}</figcaption>}
      <ResponsiveContainer width="100%" height={280}>
        {chart}
      </ResponsiveContainer>
    </figure>
  );
}
