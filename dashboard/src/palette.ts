/* Category → categorical slot, fixed order (never cycled, never rank-based). */
export const CAT_COLORS: Record<string, string> = {
  system: 'var(--cat-system)',
  tool_definitions: 'var(--cat-tools)',
  user_text: 'var(--cat-user)',
  assistant_text: 'var(--cat-assistant)',
  tool_use: 'var(--cat-tooluse)',
  tool_results: 'var(--cat-toolresults)',
  thinking: 'var(--cat-thinking)',
  other: 'var(--cat-other)',
}

/* Cache-economics series: distinct entities, own fixed slots. */
export const CACHE_SERIES = [
  { key: 'cacheRead', label: 'Cache reads (0.1×)', color: 'var(--cat-system)' },
  { key: 'cacheCreation', label: 'Cache writes (1.25×)', color: 'var(--cat-tools)' },
  { key: 'inputTokens', label: 'Uncached input (1×)', color: 'var(--cat-user)' },
] as const
