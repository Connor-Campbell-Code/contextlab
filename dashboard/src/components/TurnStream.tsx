import { CATEGORIES, OTHER_KEYS } from '../types'
import type { Turn } from '../types'
import { CAT_COLORS } from '../palette'
import { fmtBytes, fmtCompact, fmtPct, fmtTime, fmtUsd, fmtUsdSigned, shortModel } from '../format'
import { useTooltip } from '../tooltip'
import { Card, Legend } from './Card'

function segments(turn: Turn) {
  const bytes = turn.metrics?.composition.bytes ?? {}
  const segs = CATEGORIES.map((c) => ({
    key: c.key as string,
    label: c.label as string,
    bytes: bytes[c.key] ?? 0,
  }))
  const other = OTHER_KEYS.reduce((sum, k) => sum + (bytes[k] ?? 0), 0)
  segs.push({ key: 'other', label: 'Other', bytes: other })
  return segs.filter((s) => s.bytes > 0)
}

function cacheBusted(turn: Turn): string | null {
  const delta = turn.metrics?.delta
  if (!delta || delta.first_turn) return null
  if (delta.tools_stable === false) return 'tool list changed — prompt cache busted'
  if (delta.system_prompt_stable === false) return 'system prompt changed — prompt cache busted'
  return null
}

function Row({ turn, maxBytes }: { turn: Turn; maxBytes: number }) {
  const tooltip = useTooltip()
  const segs = segments(turn)
  const total = turn.metrics?.composition.total_bytes ?? 0
  const cost = turn.metrics?.cache.cost_usd
  const busted = cacheBusted(turn)

  const showComposition = (e: React.MouseEvent) => {
    tooltip.show({
      x: e.clientX,
      y: e.clientY,
      title: `${fmtTime(turn.ts)} · ${fmtBytes(total)} context${busted ? ' · ⚠ ' + busted : ''}`,
      rows: segs.map((s) => ({
        color: CAT_COLORS[s.key],
        name: s.label,
        value: fmtBytes(s.bytes),
      })),
    })
  }

  return (
    <div className="turn-row">
      <span className="t">{fmtTime(turn.ts)}</span>
      <span className="model" title={turn.model ?? ''}>
        {busted && <span className="flag serious" title={busted}>⚠ </span>}
        {shortModel(turn.model)}
      </span>
      <div
        className="comp-bar"
        style={{ width: `${maxBytes ? Math.max((total / maxBytes) * 100, 2) : 0}%` }}
        onMouseMove={showComposition}
        onMouseLeave={tooltip.hide}
      >
        {segs.map((s) => (
          <div
            key={s.key}
            className="seg"
            style={{ flexGrow: s.bytes, background: CAT_COLORS[s.key] }}
          />
        ))}
      </div>
      <span className="num">{fmtCompact(turn.cacheRead + turn.cacheCreation + turn.inputTokens)}</span>
      <span className="num">{cost ? fmtUsd(cost.total) : '—'}</span>
      <span className="num">{turn.ttftS != null ? `${turn.ttftS.toFixed(1)}s` : '—'}</span>
    </div>
  )
}

export function TurnStream({ turns }: { turns: Turn[] }) {
  // Insert order is completion order; display strictly by start time.
  const recent = [...turns].sort((a, b) => a.ts - b.ts).slice(-30).reverse()
  const maxBytes = Math.max(...recent.map((t) => t.metrics?.composition.total_bytes ?? 0), 1)

  const chart = recent.length ? (
    <>
      <Legend
        items={[
          ...CATEGORIES.map((c) => ({ label: c.label, color: CAT_COLORS[c.key] })),
          { label: 'Other', color: CAT_COLORS.other },
        ]}
      />
      <div className="stream">
        <div className="turn-row stream-head">
          <span>time</span>
          <span>model</span>
          <span>context composition (bytes)</span>
          <span className="num">in tok</span>
          <span className="num">cost</span>
          <span className="num">TTFT</span>
        </div>
        {recent.map((t) => (
          <Row key={t.id} turn={t} maxBytes={maxBytes} />
        ))}
      </div>
    </>
  ) : (
    <div className="empty">
      No requests yet — point a session here: ANTHROPIC_BASE_URL=http://127.0.0.1:8484
    </div>
  )

  const table = (
    <table className="data">
      <thead>
        <tr>
          <th>time</th><th>model</th><th>context</th><th>largest category</th>
          <th>input tokens</th><th>cached</th><th>cost</th><th>TTFT</th><th>hr$</th>
        </tr>
      </thead>
      <tbody>
        {recent.map((t) => {
          const segs = segments(t).sort((a, b) => b.bytes - a.bytes)
          const cache = t.metrics?.cache
          // Shadow-ledger per-turn net (policy view): actual billed input cost
          // minus the simulated compressed-since-turn-1 cost. Negative =
          // compression would have cost money on this turn.
          const policy = t.metrics?.headroom?.shadow?.policy
          const cu = cache?.cost_usd
          const hrNet = policy?.cost_usd != null && cu
            ? cu.uncached_input + cu.cache_writes + cu.cache_reads - policy.cost_usd
            : null
          return (
            <tr key={t.id}>
              <td>{fmtTime(t.ts)}</td>
              <td>{shortModel(t.model)}</td>
              <td>{fmtBytes(t.metrics?.composition.total_bytes ?? 0)}</td>
              <td>{segs[0] ? `${segs[0].label} (${fmtBytes(segs[0].bytes)})` : '—'}</td>
              <td>{fmtCompact(t.cacheRead + t.cacheCreation + t.inputTokens)}</td>
              <td>{cache ? fmtPct(cache.cache_hit_ratio) : '—'}</td>
              <td>{cache?.cost_usd ? fmtUsd(cache.cost_usd.total) : '—'}</td>
              <td>{t.ttftS != null ? `${t.ttftS.toFixed(2)}s` : '—'}</td>
              <td>{hrNet != null ? fmtUsdSigned(hrNet) : '—'}</td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )

  return (
    <Card
      title="Live turn stream"
      hint="what each request actually carried — newest first"
      chart={chart}
      table={table}
    />
  )
}
