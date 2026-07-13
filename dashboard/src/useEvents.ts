import { useEffect, useRef, useState } from 'react'
import type { Turn } from './types'

const MAX_TURNS = 1000

/* /_contextlab/recent rows and websocket events carry the same data in
 * slightly different shapes; normalize both into Turn. */

function fromRecent(r: Record<string, unknown>): Turn {
  const ts = r.ts as number
  return {
    id: r.id as number,
    ts,
    sessionId: (r.session_id as string) ?? null,
    agentId: (r.agent_id as string) ?? null,
    model: (r.model as string) ?? null,
    status: (r.status as number) ?? 0,
    stopReason: (r.stop_reason as string) ?? null,
    inputTokens: (r.input_tokens as number) ?? 0,
    outputTokens: (r.output_tokens as number) ?? 0,
    cacheRead: (r.cache_read_tokens as number) ?? 0,
    cacheCreation: (r.cache_creation_tokens as number) ?? 0,
    ttftS: r.t_first_byte ? +((r.t_first_byte as number) - ts).toFixed(4) : null,
    totalS: r.t_done ? +((r.t_done as number) - ts).toFixed(4) : null,
    metrics: (r.metrics as Turn['metrics']) ?? null,
  }
}

function fromWs(e: Record<string, unknown>): Turn {
  const usage = (e.usage ?? {}) as Record<string, number>
  const latency = (e.latency ?? {}) as Record<string, number | null>
  return {
    id: e.id as number,
    ts: e.ts as number,
    sessionId: (e.session_id as string) ?? null,
    agentId: (e.agent_id as string) ?? null,
    model: (e.model as string) ?? null,
    status: (e.status as number) ?? 0,
    stopReason: (e.stop_reason as string) ?? null,
    inputTokens: usage.input_tokens ?? 0,
    outputTokens: usage.output_tokens ?? 0,
    cacheRead: usage.cache_read_input_tokens ?? 0,
    cacheCreation: usage.cache_creation_input_tokens ?? 0,
    ttftS: latency.ttft_s ?? null,
    totalS: latency.total_s ?? null,
    metrics: (e.metrics as Turn['metrics']) ?? null,
  }
}

export function useEvents(): { turns: Turn[]; connected: boolean } {
  const [turns, setTurns] = useState<Turn[]>([])
  const [connected, setConnected] = useState(false)
  const retry = useRef(1000)

  useEffect(() => {
    let ws: WebSocket | null = null
    let closed = false
    let timer: ReturnType<typeof setTimeout>

    fetch('/_contextlab/recent?limit=500')
      .then((r) => r.json())
      .then((rows: Record<string, unknown>[]) => {
        const backfill = rows.map(fromRecent).sort((a, b) => a.id - b.id)
        setTurns((live) => {
          const seen = new Set(backfill.map((t) => t.id))
          return [...backfill, ...live.filter((t) => !seen.has(t.id))]
        })
      })
      .catch(() => {})

    function connect() {
      const proto = location.protocol === 'https:' ? 'wss' : 'ws'
      ws = new WebSocket(`${proto}://${location.host}/_contextlab/ws`)
      ws.onopen = () => {
        retry.current = 1000
        setConnected(true)
      }
      ws.onmessage = (msg) => {
        const event = JSON.parse(msg.data)
        if (event.type !== 'request') return
        const turn = fromWs(event)
        setTurns((prev) =>
          [...prev.filter((t) => t.id !== turn.id), turn].slice(-MAX_TURNS),
        )
      }
      ws.onclose = () => {
        setConnected(false)
        if (!closed) {
          timer = setTimeout(connect, retry.current)
          retry.current = Math.min(retry.current * 2, 15000)
        }
      }
    }
    connect()

    return () => {
      closed = true
      clearTimeout(timer)
      ws?.close()
    }
  }, [])

  return { turns, connected }
}
