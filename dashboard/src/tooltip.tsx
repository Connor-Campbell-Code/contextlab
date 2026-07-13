import { createContext, useCallback, useContext, useState } from 'react'
import type { ReactNode } from 'react'

export interface TooltipRow {
  color?: string
  name: string
  value: string
}
interface TooltipState {
  x: number
  y: number
  title: string
  rows: TooltipRow[]
}
interface TooltipApi {
  show: (tt: TooltipState) => void
  hide: () => void
}

const Ctx = createContext<TooltipApi>({ show: () => {}, hide: () => {} })

export const useTooltip = () => useContext(Ctx)

export function TooltipProvider({ children }: { children: ReactNode }) {
  const [tt, setTt] = useState<TooltipState | null>(null)
  const show = useCallback((next: TooltipState) => setTt(next), [])
  const hide = useCallback(() => setTt(null), [])
  return (
    <Ctx.Provider value={{ show, hide }}>
      {children}
      {tt && (
        <div
          className="tooltip"
          style={{
            left: Math.min(tt.x + 14, window.innerWidth - 240),
            top: Math.min(tt.y + 14, window.innerHeight - 40 - tt.rows.length * 20),
          }}
        >
          <div className="tt-title">{tt.title}</div>
          {/* React text interpolation escapes names — labels are untrusted data */}
          {tt.rows.map((row, i) => (
            <div className="tt-row" key={i}>
              {row.color && <span className="tt-key" style={{ background: row.color }} />}
              <span className="tt-val">{row.value}</span>
              <span className="tt-name">{row.name}</span>
            </div>
          ))}
        </div>
      )}
    </Ctx.Provider>
  )
}
