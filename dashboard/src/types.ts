export interface Composition {
  bytes: Record<string, number>
  total_bytes: number
  fractions: Record<string, number>
  message_count: number
  tool_count: number
  tool_results: { tool: string; bytes: number }[]
}

export interface CostUsd {
  uncached_input: number
  cache_writes: number
  cache_reads: number
  output: number
  total: number
  no_cache_total: number
  cache_savings: number
}

/** Live A/B shadow: what Headroom (real compress(), sidecar worker) would
 * have done to this exact request. Absent when the sidecar is unavailable. */
export interface HeadroomLens {
  tokens_before: number
  tokens_after: number
  savings_pct: number
  fired: boolean
  transforms: string[]
  /** Per-block diff of the whole-conversation pass (matched by tool_use_id) —
   * NOT isolated scoring, which always reads as recency-protected. */
  tool_results: {
    tool: string
    bytes: number
    tokens: number
    savings_pct: number
    decision: 'compressed' | 'untouched'
  }[]
  /** Ground truth from the API's count_tokens endpoint (Claude's real
   * tokenizer, sampled every Nth scored turn). The lens's tiktoken counts
   * are a proxy — this measures the residual drift. */
  spot_check?: {
    claude_tokens_before: number
    claude_tokens_after: number
    savings_pct_claude: number
    count_drift_pct: number | null
  } | null
}

export interface Metrics {
  composition: Composition
  /** Directory the client session was launched from (parsed from Claude
   * Code's system prompt; null for housekeeping calls / other clients). */
  workdir?: string | null
  headroom?: HeadroomLens | null
  cache: {
    total_input_tokens: number
    cache_hit_ratio: number
    cost_usd: CostUsd | null
  }
  delta: {
    first_turn: boolean
    system_prompt_stable?: boolean
    tools_stable?: boolean
    shared_message_prefix?: number
    messages_added?: number
    resent_bytes?: number
    new_bytes?: number
    resent_fraction?: number
  }
}

export interface Turn {
  id: number
  ts: number
  sessionId: string | null
  agentId: string | null
  model: string | null
  status: number
  stopReason: string | null
  inputTokens: number
  outputTokens: number
  cacheRead: number
  cacheCreation: number
  ttftS: number | null
  totalS: number | null
  metrics: Metrics | null
}

/** Context categories in canonical stack order — the palette slot assignment
 * is fixed to this order (the ordering is the CVD-safety mechanism). */
export const CATEGORIES = [
  { key: 'system', label: 'System prompt' },
  { key: 'tool_definitions', label: 'Tool definitions' },
  { key: 'user_text', label: 'User text' },
  { key: 'assistant_text', label: 'Assistant text' },
  { key: 'tool_use', label: 'Tool calls' },
  { key: 'tool_results', label: 'Tool results' },
  { key: 'thinking', label: 'Thinking' },
] as const

export const OTHER_KEYS = ['images', 'other'] as const
