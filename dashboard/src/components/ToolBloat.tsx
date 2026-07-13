import type { Turn } from '../types'
import { fmtBytes } from '../format'
import { useTooltip } from '../tooltip'
import { Card } from './Card'

const W = 560
const ROW_H = 26
const PAD = { left: 130, right: 70 }

/* Latest request per session already carries the whole conversation's tool
 * results, so aggregate from each session's most recent turn (not a sum over
 * turns, which would count every result once per re-send). */
function latestPerSession(turns: Turn[]): Turn[] {
  const bySession = new Map<string, Turn>()
  for (const t of turns) bySession.set(t.sessionId ?? `solo-${t.id}`, t)
  return [...bySession.values()]
}

export function ToolBloat({ turns }: { turns: Turn[] }) {
  const tooltip = useTooltip()
  const agg = new Map<string, number>()
  for (const turn of latestPerSession(turns)) {
    for (const r of turn.metrics?.composition.tool_results ?? []) {
      agg.set(r.tool, (agg.get(r.tool) ?? 0) + r.bytes)
    }
  }
  const rows = [...agg.entries()].sort((a, b) => b[1] - a[1]).slice(0, 10)
  const max = Math.max(...rows.map(([, b]) => b), 1)
  const H = rows.length * ROW_H + 6

  const chart = rows.length ? (
    <div className="chart">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Bytes of tool results in context, by tool">
        {rows.map(([tool, bytes], i) => {
          const barW = Math.max(((W - PAD.left - PAD.right) * bytes) / max, 2)
          const cy = i * ROW_H + ROW_H / 2 + 3
          return (
            <g
              key={tool}
              onMouseMove={(e) =>
                tooltip.show({
                  x: e.clientX, y: e.clientY, title: 'tool results in live context',
                  rows: [{ name: tool, value: fmtBytes(bytes) }],
                })
              }
              onMouseLeave={tooltip.hide}
            >
              <rect x={0} y={i * ROW_H} width={W} height={ROW_H} fill="transparent" />
              <text className="dlabel" x={PAD.left - 8} y={cy + 4} textAnchor="end">
                {tool.length > 18 ? tool.slice(0, 17) + '…' : tool}
              </text>
              <path
                d={`M ${PAD.left} ${cy - 8} H ${PAD.left + barW - 4} q 4 0 4 4 v 8 q 0 4 -4 4 H ${PAD.left} Z`}
                fill="var(--cat-system)"
              />
              <text className="dlabel" x={PAD.left + barW + 6} y={cy + 4}>
                {fmtBytes(bytes)}
              </text>
            </g>
          )
        })}
        <line className="baseline" x1={PAD.left} x2={PAD.left} y1={0} y2={H - 4} />
      </svg>
    </div>
  ) : (
    <div className="empty">No tool results captured yet</div>
  )

  const table = (
    <table className="data">
      <thead><tr><th>tool</th><th>bytes in live context</th></tr></thead>
      <tbody>
        {rows.map(([tool, bytes]) => (
          <tr key={tool}><td>{tool}</td><td>{bytes.toLocaleString()}</td></tr>
        ))}
      </tbody>
    </table>
  )

  return (
    <Card
      title="Tool-result bloat"
      hint="who is filling the window (current context per session)"
      chart={chart}
      table={table}
    />
  )
}
