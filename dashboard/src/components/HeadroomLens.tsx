import type { Turn } from '../types'
import { fmtBytes, fmtTime, shortSession } from '../format'
import { useTooltip } from '../tooltip'
import { Card } from './Card'

const W = 560
const ROW_H = 26
const PAD = { left: 130, right: 70 }

const DECISION_COLOR: Record<string, string> = {
  compressed: 'var(--cat-tools)',
  untouched: 'var(--cat-toolresults)',
}

/* Per-turn would-be savings, newest last: does compression ever fire on the
 * work you're actually doing? Each scored turn is one dot; the per-tool bars
 * below say who compresses and who the router protects. */
export function HeadroomLensCard({ turns }: { turns: Turn[] }) {
  const tooltip = useTooltip()
  const scored = turns.filter((t) => t.metrics?.headroom)

  // Aggregate tool_result decisions from each session's latest turn (the
  // full conversation), mirroring ToolBloat's dedup rationale.
  const bySession = new Map<string, Turn>()
  for (const t of scored) bySession.set(t.sessionId ?? `solo-${t.id}`, t)
  const byTool = new Map<string, { bytes: number; n: number; fired: number; savings: number }>()
  for (const turn of bySession.values()) {
    for (const r of turn.metrics!.headroom!.tool_results) {
      const agg = byTool.get(r.tool) ?? { bytes: 0, n: 0, fired: 0, savings: 0 }
      agg.bytes += r.bytes
      agg.n += 1
      if (r.decision === 'compressed') {
        agg.fired += 1
        agg.savings += r.savings_pct
      }
      byTool.set(r.tool, agg)
    }
  }
  const rows = [...byTool.entries()].sort((a, b) => b[1].bytes - a[1].bytes).slice(0, 10)
  const maxBytes = Math.max(...rows.map(([, r]) => r.bytes), 1)
  const H = rows.length * ROW_H + 6

  // The lens counts with tiktoken (a stand-in for Claude's unpublished
  // tokenizer); sampled turns carry count_tokens ground truth to bound it.
  const drifts = scored
    .map((t) => t.metrics!.headroom!.spot_check?.count_drift_pct)
    .filter((d): d is number => typeof d === 'number')
  const meanDrift = drifts.length
    ? drifts.reduce((a, b) => a + b, 0) / drifts.length
    : null
  const calibration = (
    <div className="calibration">
      {meanDrift !== null
        ? `tokenizer calibration: tiktoken counts run ${meanDrift >= 0 ? '+' : ''}${meanDrift.toFixed(1)}% vs the API's count_tokens on ${drifts.length} spot-check${drifts.length === 1 ? '' : 's'}${drifts.length >= 3 ? ' — correction applied to the KPI tile' : ' — KPI stays raw until 3 samples'}`
        : 'tokenizer calibration: no count_tokens spot-checks yet'}
    </div>
  )

  const chart = rows.length ? (
    <div className="chart">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Would-be Headroom outcome per tool">
        {rows.map(([tool, r], i) => {
          const barW = Math.max(((W - PAD.left - PAD.right) * r.bytes) / maxBytes, 2)
          const cy = i * ROW_H + ROW_H / 2 + 3
          const fired = r.fired > 0
          const label = fired
            ? `−${(r.savings / r.fired).toFixed(0)}% on ${r.fired}/${r.n}`
            : 'untouched'
          return (
            <g
              key={tool}
              onMouseMove={(e) =>
                tooltip.show({
                  x: e.clientX, y: e.clientY, title: `${tool} — would Headroom fire?`,
                  rows: [
                    { name: 'payloads scored', value: String(r.n) },
                    { name: 'compressed', value: String(r.fired) },
                    { name: 'bytes in context', value: fmtBytes(r.bytes) },
                  ],
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
                fill={fired ? DECISION_COLOR.compressed : DECISION_COLOR.untouched}
              />
              <text className="dlabel" x={PAD.left + barW + 6} y={cy + 4}>
                {label}
              </text>
            </g>
          )
        })}
        <line className="baseline" x1={PAD.left} x2={PAD.left} y1={0} y2={H - 4} />
      </svg>
      {calibration}
    </div>
  ) : (
    <div className="empty">
      {scored.length
        ? 'No tool results large enough to score yet'
        : 'No scored turns yet — is .venv-headroom present? (lens logs on proxy startup)'}
    </div>
  )

  const table = (
    <>
    <table className="data">
      <thead>
        <tr><th>turn</th><th>session</th><th>tokens</th><th>would save</th><th>router said</th></tr>
      </thead>
      <tbody>
        {scored.slice(-20).map((t) => {
          const h = t.metrics!.headroom!
          return (
            <tr key={t.id}>
              <td>{fmtTime(t.ts)}</td>
              <td>{shortSession(t.sessionId)}</td>
              <td>{h.tokens_before.toLocaleString()}</td>
              <td>{h.fired ? `−${h.savings_pct}%` : '0%'}</td>
              <td>
                {h.tool_results.map((r) => `${r.tool}:${r.decision}`).join(' ') || '—'}
              </td>
            </tr>
          )
        })}
      </tbody>
    </table>
    {calibration}
    </>
  )

  return (
    <Card
      title="Headroom lens"
      hint="live shadow A/B: what compress() would do to each real request"
      chart={chart}
      table={table}
    />
  )
}
