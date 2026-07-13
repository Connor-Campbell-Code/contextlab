import type { Turn } from '../types'
import { fmtCompact, fmtTime } from '../format'
import { useTooltip } from '../tooltip'
import { Card } from './Card'

const W = 560
const H = 150
const PAD = { left: 38, right: 8, top: 8, bottom: 20 }
const MAX_COLS = 40

export function TTFTChart({ turns }: { turns: Turn[] }) {
  const tooltip = useTooltip()
  const data = turns.slice(-MAX_COLS).filter((t) => t.ttftS != null)
  const yMax = Math.max(...data.map((t) => t.ttftS ?? 0), 1)
  const plotW = W - PAD.left - PAD.right
  const plotH = H - PAD.top - PAD.bottom
  const band = plotW / Math.max(data.length, 1)
  const barW = Math.min(band * 0.7, 24)
  const y = (v: number) => PAD.top + plotH * (1 - v / yMax)

  const chart = data.length ? (
    <div className="chart">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Time to first token per request">
        {[0.5, 1].map((f) => (
          <g key={f}>
            <line className="gridline" x1={PAD.left} x2={W - PAD.right} y1={y(yMax * f)} y2={y(yMax * f)} />
            <text className="tick" x={PAD.left - 6} y={y(yMax * f) + 3} textAnchor="end">
              {(yMax * f).toFixed(1)}s
            </text>
          </g>
        ))}
        <line className="baseline" x1={PAD.left} x2={W - PAD.right} y1={y(0)} y2={y(0)} />
        {data.map((t, i) => {
          const v = t.ttftS ?? 0
          const x = PAD.left + band * i + (band - barW) / 2
          const inTok = t.cacheRead + t.cacheCreation + t.inputTokens
          return (
            <g
              key={t.id}
              onMouseMove={(e) =>
                tooltip.show({
                  x: e.clientX, y: e.clientY, title: fmtTime(t.ts),
                  rows: [
                    { color: 'var(--cat-system)', name: 'time to first token', value: `${v.toFixed(2)}s` },
                    { name: 'input tokens (context size)', value: fmtCompact(inTok) },
                  ],
                })
              }
              onMouseLeave={tooltip.hide}
            >
              <rect x={PAD.left + band * i} y={PAD.top} width={band} height={plotH} fill="transparent" />
              <rect x={x} y={y(v)} width={barW} height={Math.max(y(0) - y(v), 1)} rx={3} fill="var(--cat-system)" />
            </g>
          )
        })}
      </svg>
    </div>
  ) : (
    <div className="empty">No latency data yet</div>
  )

  const table = (
    <table className="data">
      <thead><tr><th>time</th><th>TTFT</th><th>total</th><th>input tokens</th></tr></thead>
      <tbody>
        {[...data].reverse().map((t) => (
          <tr key={t.id}>
            <td>{fmtTime(t.ts)}</td>
            <td>{t.ttftS?.toFixed(2)}s</td>
            <td>{t.totalS?.toFixed(2) ?? '—'}s</td>
            <td>{(t.cacheRead + t.cacheCreation + t.inputTokens).toLocaleString()}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )

  return (
    <Card
      title="Time to first token"
      hint="the latency price of a heavy context"
      chart={chart}
      table={table}
    />
  )
}
