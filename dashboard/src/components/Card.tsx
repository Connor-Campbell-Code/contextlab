import { useState } from 'react'
import type { ReactNode } from 'react'

/* Every chart card ships its table-view twin — tooltips enhance, never gate. */
export function Card({
  title,
  hint,
  chart,
  table,
}: {
  title: string
  hint?: string
  chart: ReactNode
  table: ReactNode
}) {
  const [showTable, setShowTable] = useState(false)
  return (
    <section className="card">
      <div className="card-head">
        <h2>{title}</h2>
        {hint && <span className="hint">{hint}</span>}
        <button className="toggle" onClick={() => setShowTable((v) => !v)}>
          {showTable ? 'Chart' : 'Table'}
        </button>
      </div>
      {showTable ? table : chart}
    </section>
  )
}

export function Legend({
  items,
  lineKeys = false,
}: {
  items: { label: string; color: string }[]
  lineKeys?: boolean
}) {
  return (
    <div className="legend">
      {items.map((item) => (
        <span className="item" key={item.label}>
          <span
            className={lineKeys ? 'linekey' : 'swatch'}
            style={{ background: item.color }}
          />
          {item.label}
        </span>
      ))}
    </div>
  )
}
