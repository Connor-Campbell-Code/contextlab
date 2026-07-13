import { useMemo, useState } from 'react'
import { useEvents } from './useEvents'
import { TooltipProvider } from './tooltip'
import { TurnStream } from './components/TurnStream'
import { CacheChart } from './components/CacheChart'
import { ToolBloat } from './components/ToolBloat'
import { HeadroomLensCard } from './components/HeadroomLens'
import { TTFTChart } from './components/TTFTChart'
import { Learn } from './components/Learn'
import { fmtPct, fmtUsd, shortSession } from './format'

function StatTile({ label, value, note, good }: {
  label: string
  value: string
  note?: string
  good?: boolean
}) {
  return (
    <div className="tile">
      <div className="label">{label}</div>
      <div className="value">{value}</div>
      {note && <div className={`note${good ? ' good' : ''}`}>{note}</div>}
    </div>
  )
}

export default function App() {
  const { turns, connected } = useEvents()
  const [session, setSession] = useState<string>('all')
  const [view, setView] = useState<'live' | 'learn'>('live')

  const sessions = useMemo(() => {
    const counts = new Map<string, number>()
    for (const t of turns) {
      const k = t.sessionId ?? 'unknown'
      counts.set(k, (counts.get(k) ?? 0) + 1)
    }
    return [...counts.entries()].sort((a, b) => b[1] - a[1])
  }, [turns])

  const filtered = useMemo(
    () => (session === 'all' ? turns : turns.filter((t) => (t.sessionId ?? 'unknown') === session)),
    [turns, session],
  )

  const kpi = useMemo(() => {
    let spend = 0, savings = 0, reads = 0, input = 0, priced = 0
    let hrBefore = 0, hrAfter = 0, hrScored = 0
    let scTikB = 0, scTikA = 0, scClB = 0, scClA = 0, spotN = 0
    for (const t of filtered) {
      const cache = t.metrics?.cache
      reads += t.cacheRead
      input += t.cacheRead + t.cacheCreation + t.inputTokens
      if (cache?.cost_usd) {
        spend += cache.cost_usd.total
        savings += cache.cost_usd.cache_savings
        priced++
      }
      const h = t.metrics?.headroom
      if (h) {
        hrBefore += h.tokens_before
        hrAfter += h.tokens_after
        hrScored++
        if (h.spot_check?.claude_tokens_before) {
          spotN++
          scTikB += h.tokens_before
          scTikA += h.tokens_after
          scClB += h.spot_check.claude_tokens_before
          scClA += h.spot_check.claude_tokens_after
        }
      }
    }
    const hrSavings = hrBefore ? (hrBefore - hrAfter) / hrBefore : 0
    // Drift calibration (mirrored in tui/model.py aggregate): on spot-checked
    // turns, the savings ratio under Claude's tokenizer vs tiktoken's scales
    // the population figure. Multiplicative — a 0% session stays 0%. Below
    // MIN_SPOT_SAMPLES the factor is noisier than the bias it fixes.
    const MIN_SPOT_SAMPLES = 3
    let hrFactor: number | null = null
    let hrCalibrated: number | null = null
    if (spotN >= MIN_SPOT_SAMPLES && scTikB && scClB) {
      const tik = (scTikB - scTikA) / scTikB
      const claude = (scClB - scClA) / scClB
      if (tik > 0) {
        hrFactor = claude / tik
        hrCalibrated = Math.min(hrSavings * hrFactor, 1)
      }
    }
    return {
      spend, savings, hitRatio: input ? reads / input : 0, priced,
      hrScored, hrSavings, hrCalibrated, hrFactor, spotN,
    }
  }, [filtered])

  return (
    <TooltipProvider>
      <div className="app">
        <header className="app-header">
          <h1>Context Lab</h1>
          <span className="sub">what every prompt actually carries</span>
          <button className="toggle" onClick={() => setView((v) => (v === 'live' ? 'learn' : 'live'))}>
            {view === 'live' ? 'Learn' : 'Live'}
          </button>
          <span className={`conn${connected ? ' live' : ''}`}>
            <span className="dot" />
            {connected ? 'live' : 'reconnecting…'}
          </span>
        </header>

        {view === 'learn' ? (
          <Learn turns={filtered} />
        ) : (
        <>
        <div className="filters">
          <label htmlFor="session">Session</label>
          <select id="session" value={session} onChange={(e) => setSession(e.target.value)}>
            <option value="all">All sessions ({turns.length} requests)</option>
            {sessions.map(([id, count]) => (
              <option key={id} value={id}>
                {shortSession(id)} ({count})
              </option>
            ))}
          </select>
        </div>

        <div className="kpis">
          <StatTile label="Requests" value={String(filtered.length)} />
          <StatTile
            label="Spend"
            value={fmtUsd(kpi.spend)}
            note={kpi.priced < filtered.length ? `${filtered.length - kpi.priced} unpriced` : 'API-billed usage'}
          />
          <StatTile label="Saved by caching" value={fmtUsd(kpi.savings)} note="vs the same calls uncached" good />
          <StatTile label="Cache hit ratio" value={fmtPct(kpi.hitRatio)} note="of input tokens read at 0.1×" />
          {kpi.hrScored > 0 && (
            <StatTile
              label="Headroom would save"
              value={fmtPct(kpi.hrCalibrated ?? kpi.hrSavings)}
              note={
                kpi.hrCalibrated != null
                  ? `calibrated ×${kpi.hrFactor!.toFixed(2)} via ${kpi.spotN} count_tokens samples (raw ${fmtPct(kpi.hrSavings)})`
                  : `on your traffic (${kpi.hrScored} turns scored, token-weighted)`
              }
            />
          )}
        </div>

        <TurnStream turns={filtered} />
        <div className="grid-2">
          <CacheChart turns={filtered} />
          <TTFTChart turns={filtered} />
        </div>
        <div className="grid-2">
          <ToolBloat turns={filtered} />
          <HeadroomLensCard turns={filtered} />
        </div>
        </>
        )}
      </div>
    </TooltipProvider>
  )
}
