// Original pixel-art decorations (sparkles, clouds, hearts, a planet, a moon). Purely decorative.

const SPARK = 'M5 0h1v4h4v1H6v1H5V5H1V4h4zM5 6h1v4H5z'
const HEART = 'M1 0h3v1h2V0h3v1h1v3H9v1H8v1H7v1H6v1H4V7H3V6H2V5H1V4H0V1h1z'
const MOON = 'M4 0h4v1H6v1H5v2H4v3h1v2h1v1h2v1H4v-1H2V9H1V7H0V4h1V2h1V1h2z'

function Px({ d, fill, size, box, cls, style }) {
  return (
    <svg className={cls} width={size[0]} height={size[1]} viewBox={box} shapeRendering="crispEdges" style={style}>
      <path fill={fill} d={d} />
    </svg>
  )
}

function Cloud({ cls, size, style }) {
  return (
    <svg className={cls} width={size[0]} height={size[1]} viewBox="0 0 16 8" shapeRendering="crispEdges" style={style}>
      <path fill="var(--line)" d="M3 1h6v1h3v1h3v5H0V4h1V3h2z" />
      <path fill="#FFFFFF" d="M4 2h4v1h3v1h3v3H1V5h1V4h2z" />
    </svg>
  )
}

export function Heart({ size = 22, style }) {
  return <Px d={HEART} fill="var(--heart)" size={[size, Math.round(size * 0.9)]} box="0 0 10 9" style={style} />
}

export default function Decor() {
  const spark = '0 0 11 11'
  return (
    <div className="decor" aria-hidden="true">
      <Px cls="tw" d={SPARK} fill="var(--deco)" size={[22, 22]} box={spark} style={{ left: '4%', top: 120 }} />
      <Px cls="tw b" d={SPARK} fill="var(--deco2)" size={[16, 16]} box={spark} style={{ right: '6%', top: 210 }} />
      <Px cls="tw c" d={SPARK} fill="var(--deco3)" size={[26, 26]} box={spark} style={{ left: '7%', top: 760 }} />
      <Px cls="tw" d={SPARK} fill="var(--deco)" size={[18, 18]} box={spark} style={{ right: '3%', top: 1040 }} />
      <Px cls="tw b" d={SPARK} fill="var(--deco3)" size={[20, 20]} box={spark} style={{ left: '2%', top: 1420 }} />

      <Cloud cls="only-day drift" size={[112, 56]} style={{ left: 0, top: 70, animationDelay: '-6s' }} />
      <Cloud cls="only-day drift b" size={[80, 40]} style={{ left: 0, top: 880 }} />
      <Px cls="only-day bob" d={HEART} fill="var(--heart)" size={[30, 27]} box="0 0 10 9" style={{ right: '4%', top: 560 }} />
      <Px cls="only-day rise" d={HEART} fill="var(--heart)" size={[30, 27]} box="0 0 10 9" style={{ left: '3%', top: 620 }} />
      <Px cls="only-day rise b" d={HEART} fill="var(--heart)" size={[22, 20]} box="0 0 10 9" style={{ right: '2%', top: 980 }} />
      <Px cls="only-day rise c" d={HEART} fill="var(--heart)" size={[26, 23]} box="0 0 10 9" style={{ left: '5%', top: 1500 }} />

      <svg className="only-night bob" width="72" height="72" viewBox="0 0 12 12" shapeRendering="crispEdges" style={{ right: '3%', top: 520 }}>
        <path fill="#5F8FC4" d="M4 1h4v1h2v2h1v4h-1v2H8v1H4v-1H2V8H1V4h1V2h2z" />
        <path fill="#3F6898" d="M4 3h2v2H4zM7 6h2v2H7zM3 7h1v1H3z" />
      </svg>
      <Px cls="only-night tw c" d={MOON} fill="var(--deco)" size={[44, 44]} box={spark} style={{ left: '3%', top: 430 }} />
      <Px cls="only-night rise" d={SPARK} fill="var(--deco)" size={[22, 22]} box={spark} style={{ left: '3%', top: 980 }} />
      <Px cls="only-night rise b" d={SPARK} fill="var(--deco)" size={[18, 18]} box={spark} style={{ right: '2%', top: 1500 }} />
    </div>
  )
}

export function Logo() {
  return (
    <svg aria-hidden="true" className="bob" width="48" height="48" viewBox="0 0 12 12" shapeRendering="crispEdges">
      <path fill="var(--line)" d="M1 1h10v10H1z" />
      <path fill="var(--panel)" d="M2 2h8v8H2z" />
      <path fill="var(--btn)" d="M3 3h6v1H3zM3 5h4v1H3zM3 7h5v1H3z" />
      <path fill="var(--line)" d="M8 6h1v1h1v1H9v1H8V8H7V7h1z" />
    </svg>
  )
}

export function Scales() {
  return (
    <svg aria-hidden="true" className="bob" width="64" height="64" viewBox="0 0 16 16" shapeRendering="crispEdges" style={{ flex: '0 0 auto' }}>
      <path fill="var(--line)" d="M7 1h2v2h5v1h-1v1h1v3h-4V5h1V4H9v8h3v2H4v-2h3V4H5v1h1v3H2V5h1V4H2V3h5z" />
      <path fill="var(--btn)" d="M3 6h2v1H3zM11 6h2v1h-2z" />
    </svg>
  )
}

export function ThemeIcon({ theme }) {
  const d = theme === 'day' ? MOON : 'M5 0h1v2H5zM5 9h1v2H5zM0 5h2v1H0zM9 5h2v1H9zM3 3h5v5H3z'
  return (
    <svg aria-hidden="true" width="20" height="20" viewBox="0 0 11 11" shapeRendering="crispEdges">
      <path fill="currentColor" d={d} />
    </svg>
  )
}
