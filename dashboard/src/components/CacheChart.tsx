import type { Turn } from '../types'
import { CACHE_SERIES } from '../palette'
import { fmtCompact, fmtTime } from '../format'
import { useTooltip } from '../tooltip'
import { Card, Legend } from './Card'

const W = 560
const H = 190
const PAD = { left: 44, right: 8, top: 8, bottom: 20 }
const MAX_COLS = 40

function niceMax(n: number): number {
  if (n <= 0) return 1
  const pow = 10 ** Math.floor(Math.log10(n))
  for (const m of [1, 2, 5, 10]) if (m * pow >= n) return m * pow
  return 10 * pow
}

export function CacheChart({ turns }: { turns: Turn[] }) {
  const tooltip = useTooltip()
  const data = turns.slice(-MAX_COLS)
  const totals = data.map((t) => t.cacheRead + t.cacheCreation + t.inputTokens)
  const yMax = niceMax(Math.max(...totals, 1))
  const plotW = W - PAD.left - PAD.right
  const plotH = H - PAD.top - PAD.bottom
  const band = plotW / Math.max(data.length, 1)
  const barW = Math.min(band * 0.7, 24)
  const y = (v: number) => PAD.top + plotH * (1 - v / yMax)

  const chart = data.length ? (
    <div className="chart">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Input tokens per request, split by cache pricing tier">
        {[0.5, 1].map((f) => (
          <g key={f}>
            <line className="gridline" x1={PAD.left} x2={W - PAD.right} y1={y(yMax * f)} y2={y(yMax * f)} />
            <text className="tick" x={PAD.left - 6} y={y(yMax * f) + 3} textAnchor="end">
              {fmtCompact(yMax * f)}
            </text>
          </g>
        ))}
        <line className="baseline" x1={PAD.left} x2={W - PAD.right} y1={y(0)} y2={y(0)} />
        <text className="tick" x={PAD.left - 6} y={y(0) + 3} textAnchor="end">0</text>

        {data.map((t, i) => {
          const x = PAD.left + band * i + (band - barW) / 2
          let acc = 0
          const segs = CACHE_SERIES.map((s) => {
            const v = t[s.key]
            const y1 = y(acc + v)
            const h = y(acc) - y1
            acc += v
            return { ...s, v, y1, h }
          }).filter((s) => s.v > 0)
          return (
            <g
              key={t.id}
              onMouseMove={(e) =>
                tooltip.show({
                  x: e.clientX,
                  y: e.clientY,
                  title: `${fmtTime(t.ts)} · ${fmtCompact(totals[i])} input tokens`,
                  rows: CACHE_SERIES.map((s) => ({
                    color: s.color,
                    name: s.label,
                    value: fmtCompact(t[s.key]),
                  })),
                })
              }
              onMouseLeave={tooltip.hide}
            >
              {/* hit target wider than the mark */}
              <rect x={PAD.left + band * i} y={PAD.top} width={band} height={plotH} fill="transparent" />
              {segs.map((s, j) => (
                <rect
                  key={s.key}
                  x={x}
                  width={barW}
                  y={s.y1 + (j < segs.length - 1 ? 2 : 0) /* 2px surface gap */}
                  height={Math.max(s.h - (j < segs.length - 1 ? 2 : 0), 0)}
                  rx={j === segs.length - 1 ? 3 : 0}
                  fill={s.color}
                />
              ))}
            </g>
          )
        })}
      </svg>
      <Legend items={CACHE_SERIES.map((s) => ({ label: s.label, color: s.color }))} />
    </div>
  ) : (
    <div className="empty">No requests yet</div>
  )

  const table = (
    <table className="data">
      <thead>
        <tr>
          <th>time</th>
          {CACHE_SERIES.map((s) => <th key={s.key}>{s.label}</th>)}
          <th>total input</th>
        </tr>
      </thead>
      <tbody>
        {[...data].reverse().map((t) => (
          <tr key={t.id}>
            <td>{fmtTime(t.ts)}</td>
            {CACHE_SERIES.map((s) => <td key={s.key}>{t[s.key].toLocaleString()}</td>)}
            <td>{(t.cacheRead + t.cacheCreation + t.inputTokens).toLocaleString()}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )

  return (
    <Card
      title="Context growth & cache tiers"
      hint="same token, 10× price difference depending on tier"
      chart={chart}
      table={table}
    />
  )
}
