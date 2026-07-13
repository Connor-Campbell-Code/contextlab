import type { ReactNode } from 'react'
import type { Turn } from '../types'
import { CAT_COLORS } from '../palette'
import { fmtBytes, fmtPct } from '../format'

/* The learning module: one section per dashboard tile — how the number is
 * computed, what it means, why it's worth tracking. Live values from the
 * reader's own traffic are woven in wherever they exist, because a concept
 * pinned to your own 100 KB is stickier than one pinned to a hypothetical. */

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="card learn-section">
      <div className="card-head"><h2>{title}</h2></div>
      {children}
    </section>
  )
}

function HowWhatWhy({ how, what, why }: { how: ReactNode; what: ReactNode; why: ReactNode }) {
  return (
    <dl className="hww">
      <dt>How it's computed</dt><dd>{how}</dd>
      <dt>What it means</dt><dd>{what}</dd>
      <dt>Why track it</dt><dd>{why}</dd>
    </dl>
  )
}

function Live({ children }: { children: ReactNode }) {
  return <p className="live-note">📍 {children}</p>
}

function Swatch({ cat }: { cat: string }) {
  return <span className="swatch" style={{ background: CAT_COLORS[cat] }} />
}

export function Learn({ turns }: { turns: Turn[] }) {
  // Ground examples in the reader's own most recent instrumented request.
  const latest = [...turns].reverse().find((t) => t.metrics?.composition)
  const comp = latest?.metrics?.composition
  const bytes = comp?.bytes ?? {}
  const scored = turns.filter((t) => t.metrics?.headroom)
  let hrBefore = 0, hrAfter = 0
  for (const t of scored) {
    hrBefore += t.metrics!.headroom!.tokens_before
    hrAfter += t.metrics!.headroom!.tokens_after
  }

  return (
    <div className="learn">
      <p className="learn-intro">
        Every number below is measured, not estimated: token counts and dollars come from
        the API's own <code>usage</code> block (billing ground truth), composition is measured
        in bytes from the raw request, and Headroom results come from actually running its{' '}
        <code>compress()</code> on your requests. Where a section shows a 📍 line, that value
        is from your live traffic right now.
      </p>

      <Section title="KPI · Requests">
        <HowWhatWhy
          how={<>A count of <code>/v1/messages</code> calls the proxy forwarded. Nothing is sampled — every call from every session pointed at the proxy is one row in the stream.</>}
          what={<>One request ≈ one agent turn, but not one “message you typed”: a single prompt can fan out into many requests (tool-use loops, subagents, and housekeeping calls like title generation, which share your session id but run a different system prompt — the proxy separates those into their own conversation for delta tracking).</>}
          why={<>The request count is the multiplier on everything else. An agent conversation re-sends its entire history on every request, so a 40-request session pays for its context 40 times. That re-send loop is why caching and composition matter at all.</>}
        />
      </Section>

      <Section title="KPI · Spend">
        <HowWhatWhy
          how={<>Sum over requests of a four-part price: <code>uncached input × rate</code> + <code>cache writes × 1.25×rate</code> + <code>cache reads × 0.1×rate</code> + <code>output × output-rate</code>. The token splits come from the API's usage block; rates are per-model (e.g. sonnet $3/$15 per MTok in/out). Unknown models show “unpriced” rather than a wrong number.</>}
          what={<>What this traffic actually cost, respecting cache tiers — not the naive <code>tokens × price</code> figure, which can be off by ~10× in either direction.</>}
          why={<>Dollars are the only unit in which context decisions compose honestly. Notebook 04's core result: a 34% token cut is only a 34% dollar cut if it doesn't fight the cache — and the cache, not compression, is the first-order lever.</>}
        />
      </Section>

      <Section title="KPI · Saved by caching">
        <HowWhatWhy
          how={<>For each request, the counterfactual price of the same call with no cache (all input tokens at full rate) minus the actual tiered price. Summed.</>}
          what={<>The dollar value the prompt cache is delivering on your traffic — usually the largest single “optimization” in play, and it's on by default.</>}
          why={<>It sets the bar for every other optimization. Any middleware promising X% savings has to beat what caching already gives you, without breaking it. If a tool's savings are smaller than your cache savings, protecting the cache is the priority.</>}
        />
      </Section>

      <Section title="KPI · Cache hit ratio">
        <HowWhatWhy
          how={<><code>cache_read_tokens ÷ (cache_read + cache_creation + uncached input)</code>, from the API's usage block.</>}
          what={<>The fraction of your input tokens billed at 0.1× because they were an exact byte-prefix match with a recent request. High (&gt;80%) is the healthy steady state for agent sessions; the remainder is genuinely new content plus one-time writes.</>}
          why={<>Dips are diagnoses. Every drop has a cause: 5-minute cache expiry (you walked away), context compaction (history rewritten), or a prefix change — the turn stream flags the last kind with ⚠ when the tool list or system prompt changed between requests.</>}
        />
      </Section>

      <Section title="KPI · Headroom would save">
        <HowWhatWhy
          how={<>A sidecar process runs the real <code>headroom.compress()</code> (v0.30.0) on every request's message list, off the request path. The tile is token-weighted: <code>(Σ tokens_before − Σ tokens_after) ÷ Σ tokens_before</code> across scored turns, counted with tiktoken — not Headroom's own <code>len//4</code> estimator, which experiment 01 showed is up to 12× off. One honest caveat: tiktoken is OpenAI's o200k tokenizer, and Anthropic has never published Claude's — so even these counts are estimates. The bias mostly cancels in a same-tokenizer ratio; to measure what's left, every 25th scored request is spot-checked against the API's <code>count_tokens</code> endpoint (Claude's real tokenizer), and the Headroom lens card on the Live view reports the observed drift in its calibration line. Once 3+ spot-checks exist, the tile applies the correction: the tiktoken figure is scaled by the savings ratio measured under Claude's tokenizer on the sampled turns, and the raw figure moves to the tile's note. The factor is multiplicative, so a session where Headroom never fires stays honestly at 0%.</>}
          what={<>A live shadow A/B: the compression savings you would be getting if this traffic ran through Headroom, measured on your actual payloads instead of its benchmark corpus.</>}
          why={<>This is the payload-realism experiment running continuously. Headroom's README claims 60–95% on JSON; on short real coding sessions we measured 0.0% (its router refuses to touch code, diffs, and file reads) — but on long sessions it does fire: its <code>read_lifecycle</code> transform guts earlier <code>Read</code> results once the same file is later edited or re-read, and one live spot-checked request measured 24.3% would-be savings by <code>count_tokens</code> ground truth. So the fire rate is session-length-dependent, not zero. The Headroom lens card on the Live view names the tool responsible per payload (bars + the "router said" column); whether answers survive losing those "stale" reads is still the untested half of the ledger.</>}
        />
        <Live>
          {scored.length
            ? <>{scored.length} turns scored so far; token-weighted would-be savings {fmtPct(hrBefore ? (hrBefore - hrAfter) / hrBefore : 0)} (raw tiktoken — the KPI tile shows this scaled by the count_tokens calibration factor, disclosed in its note).</>
            : <>no turns scored yet — the tile appears once the sidecar sees traffic.</>}
        </Live>
      </Section>

      <Section title="Live turn stream — the composition bar">
        <p>
          Each row is one request; the bar is its full context in <strong>bytes</strong>, split by
          category. Bytes, not tokens, on purpose: the request is JSON we can measure exactly,
          but only Anthropic's server knows how it tokenizes — pretending otherwise is how
          Headroom's <code>len//4</code> problem happens (notebook 03). Byte fractions are a faithful
          answer to “where is my window going” without fake precision.
        </p>
        <dl className="cat-list">
          <dt><Swatch cat="system" />System prompt</dt>
          <dd>
            The request's <code>system</code> field: Claude Code's operating instructions, your
            CLAUDE.md, memory index, and environment info. Re-sent verbatim every request, sits
            at the very front of the cacheable prefix — stable by design, ~free (0.1×) once cached.
            {bytes.system ? <> On your latest request: <strong>{fmtBytes(bytes.system)}</strong>.</> : null}
          </dd>

          <dt><Swatch cat="tool_definitions" />Tool definitions</dt>
          <dd>
            The JSON schema of <em>every tool the agent could call</em> — name, description,
            parameter spec for Bash, Read, Edit, the skills, any MCP servers — sent with{' '}
            <em>every single request</em>, used or not. That's why it's huge: the model can only
            call tools it can currently see; there is no server-side tool memory.
            {comp ? <> On your latest request: <strong>{fmtBytes(bytes.tool_definitions ?? 0)}</strong> for <strong>{comp.tool_count} tools</strong> — likely your #1 category.</> : null}{' '}
            Why it's tolerable: it never changes mid-session, so after the first request it's a
            0.1× cache read. Why it's still worth watching: it sits in front of everything, so a
            single tool added or removed mid-session (an MCP server connecting late, Headroom's
            conditional retrieval tool) rewrites byte one of the prefix and busts the{' '}
            <em>entire conversation's</em> cache — that's the ⚠ flag, and experiment 04's
            proxy-mode finding.
          </dd>

          <dt><Swatch cat="user_text" />User text</dt>
          <dd>
            What you typed, plus what the harness injects as user-role text: system reminders,
            recalled memories, skill instructions. If this is large without you writing essays,
            the harness is doing the writing.
            {bytes.user_text ? <> Latest: <strong>{fmtBytes(bytes.user_text)}</strong>.</> : null}
          </dd>

          <dt><Swatch cat="assistant_text" />Assistant text</dt>
          <dd>
            The model's own previous replies, replayed back to it — the model has no memory
            between requests, so its past words are just more input you pay for. Verbose answers
            literally cost twice: once as output (5× rate), then forever as replayed input.
            {bytes.assistant_text ? <> Latest: <strong>{fmtBytes(bytes.assistant_text)}</strong>.</> : null}
          </dd>

          <dt><Swatch cat="tool_use" />Tool calls</dt>
          <dd>
            The model's tool invocations with their full arguments (every file path, every shell
            command). Usually small — unless the agent writes files through tool arguments, which
            puts entire file contents here.
            {bytes.tool_use ? <> Latest: <strong>{fmtBytes(bytes.tool_use)}</strong>.</> : null}
          </dd>

          <dt><Swatch cat="tool_results" />Tool results</dt>
          <dd>
            Everything the tools returned — file contents, command output, search hits —
            accumulated over the whole session and re-sent every request. In long sessions this
            is the growth category and the main compaction target; the Tool-result bloat card
            breaks it down by which tool produced it.
            {bytes.tool_results ? <> Latest: <strong>{fmtBytes(bytes.tool_results)}</strong>.</> : null}
          </dd>

          <dt><Swatch cat="thinking" />Thinking</dt>
          <dd>
            Extended-thinking blocks from previous turns riding along in the replayed
            conversation (including redacted ones). Reasoning isn't free even after it's done —
            it becomes context.
            {bytes.thinking ? <> Latest: <strong>{fmtBytes(bytes.thinking)}</strong>.</> : null}
          </dd>

          <dt><Swatch cat="other" />Other / Images</dt>
          <dd>Anything else: image blocks (screenshots are byte-expensive), unrecognized block types.</dd>
        </dl>
        <p>
          The row's right-hand columns are the API-billed input tokens, the tiered dollar cost,
          and TTFT. The ⚠ flag means the prompt-cache prefix broke this turn: the tool list or
          system prompt changed since the previous request in this conversation.
        </p>
        {comp && latest ? (
          <Live>
            your latest request carried {fmtBytes(comp.total_bytes)} of context across{' '}
            {comp.message_count} messages; largest category:{' '}
            {Object.entries(bytes).sort((a, b) => b[1] - a[1])[0]?.[0].replace('_', ' ')}.
          </Live>
        ) : null}
      </Section>

      <Section title="Context growth & cache tiers">
        <HowWhatWhy
          how={<>Input tokens per request (API usage block), stacked by price tier: cache reads (0.1×), cache writes (1.25×), uncached input (1×).</>}
          what={<>The shape of a session. Healthy agent work is a staircase that's almost all cache-read with a thin write stripe on top — each turn re-reads history cheaply and writes only the new tail. A tall write or uncached bar mid-session means the prefix broke and the conversation was re-cached from the break point.</>}
          why={<>This chart is where cache pathologies are visible before they're expensive: expiry gaps (&gt;5 min between requests), compaction cliffs (total suddenly drops, then a big write), and prefix busts (⚠ turns). Same token count, 10× price spread — composition of the <em>bars</em>, not their height, is the cost story.</>}
        />
      </Section>

      <Section title="Time to first token">
        <HowWhatWhy
          how={<>Per request: time from the proxy receiving your request to the first byte of the streamed response (<code>t_first_byte − t_start</code>). Measured at the proxy, so it includes the upstream round-trip but not your terminal's rendering.</>}
          what={<>How long the model made you wait before it started answering. Correlates with uncached input size — the tooltip shows input tokens so you can eyeball that relationship on your own traffic.</>}
          why={<>Latency is the third price of context, after dollars and attention. A cache hit doesn't just save money; prefix reuse is also faster to process. And it's the claim-registry metric for Headroom's self-contradictory latency numbers (52ms docs vs 1–5ms author claim) — experiment 05, still open.</>}
        />
      </Section>

      <Section title="Tool-result bloat">
        <HowWhatWhy
          how={<>From each session's most recent request (which carries the whole conversation), tool_result bytes grouped by the tool that produced them. Latest-request-only, deliberately: summing across requests would count every result once per re-send.</>}
          what={<>Which tools are filling your window. A read-heavy exploration session looks completely different from an edit loop.</>}
          why={<>“Context is full” is never the whole diagnosis — full <em>of what</em> is. This card answers it, and it's the target list for any pruning/compaction strategy: the top bar here is where a byte saved pays off most, because it's re-sent every remaining turn of the session.</>}
        />
      </Section>

      <Section title="Headroom lens">
        <HowWhatWhy
          how={<>The sidecar compresses each request's full message list, then diffs every tool_result block before vs after, matched by <code>tool_use_id</code>. In-context diffing matters: scored in isolation, any payload reads as “recent” and Headroom protects recent turns — a method bug this dashboard exposed in our own experiment 02 (notebook 05).</>}
          what={<>Per tool: bytes in context, how many of its payloads compress() actually shrank, and by how much. “Untouched” is Headroom's router declining — for code and file reads, correctly.</>}
          why={<>It attributes the KPI. If “Headroom would save” ever moves off 0%, this card names the tool and payload shape responsible — turning a marketing-number dispute into a per-payload, reproducible observation on your own work.</>}
        />
      </Section>

      <p className="learn-footer">
        Deeper dives, with the experiments behind each number: <code>notebook/</code> — 01 anatomy
        of one prompt · 03 what is a token anyway · 04 cache dollars, not tokens · 05 payload
        realism. Claim verdicts: <code>src/contextlab/eval/claims.yaml</code>.
      </p>
    </div>
  )
}
