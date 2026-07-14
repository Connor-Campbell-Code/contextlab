export function fmtBytes(n: number): string {
  if (n >= 1_048_576) return `${(n / 1_048_576).toFixed(1)} MB`
  if (n >= 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${n} B`
}

export function fmtCompact(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 10_000) return `${(n / 1000).toFixed(0)}K`
  if (n >= 1000) return `${(n / 1000).toFixed(1)}K`
  return `${n}`
}

export function fmtUsd(n: number): string {
  if (n >= 1) return `$${n.toFixed(2)}`
  if (n >= 0.01) return `$${n.toFixed(3)}`
  return `$${n.toFixed(4)}`
}

export function fmtUsdSigned(n: number): string {
  // Explicit sign: a shadow-ledger net can legitimately be negative
  // (compression that busts cache costs money).
  return n >= 0 ? `+${fmtUsd(n)}` : `-${fmtUsd(-n)}`
}

export function fmtPct(n: number): string {
  return `${(n * 100).toFixed(1)}%`
}

export function fmtTime(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}

export function shortModel(model: string | null): string {
  if (!model) return '?'
  return model.replace(/^claude-/, '').replace(/-\d{8}$/, '')
}

export function shortSession(id: string | null): string {
  return id ? id.slice(0, 8) : 'unknown'
}
