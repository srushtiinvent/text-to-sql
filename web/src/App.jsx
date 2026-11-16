import { useEffect, useState } from 'react'
import Ask from './Ask.jsx'
import Evaluation from './Evaluation.jsx'
import Decor, { Logo, ThemeIcon } from './Decor.jsx'

const TICKER = ['5 candidates per question', 'grouped by what they return', 'a tiny example settles disagreements', 'asks instead of guessing when unsure', 'read-only by construction']

function initialTheme() {
  try {
    const saved = localStorage.getItem('t2s-theme')
    if (saved === 'day' || saved === 'night') return saved
  } catch { /* storage unavailable */ }
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'night' : 'day'
}

export default function App() {
  const [theme, setTheme] = useState(initialTheme)
  const [tab, setTab] = useState('ask')

  useEffect(() => {
    try { localStorage.setItem('t2s-theme', theme) } catch { /* storage unavailable */ }
  }, [theme])

  return (
    <div className={`t2s ${theme}`}>
      <Decor />
      <div className="wrap">
        <header className="row" style={{ gap: 16, alignItems: 'center' }}>
          <div className="row" style={{ gap: 12, alignItems: 'center', flex: '1 1 280px' }}>
            <Logo />
            <div>
              <div className="px" style={{ fontSize: 26, fontWeight: 700, lineHeight: 1.1 }}>Verified Text-to-SQL</div>
              <div className="lab" style={{ marginTop: 2 }}>queries that check each other</div>
            </div>
          </div>
          <nav aria-label="Pages" className="row" style={{ gap: 8 }}>
            <button className={tab === 'ask' ? 'on' : ''} onClick={() => setTab('ask')}>Ask</button>
            <button className={tab === 'eval' ? 'on' : ''} onClick={() => setTab('eval')}>Evaluation</button>
          </nav>
          <button onClick={() => setTheme(theme === 'day' ? 'night' : 'day')} aria-label="Switch between day and night theme"
            style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <ThemeIcon theme={theme} />
            {theme === 'day' ? 'Night' : 'Day'}
          </button>
        </header>

        <div className="win ticker" aria-hidden="true">
          <div className="tick px">
            {[...TICKER, ...TICKER].map((t, i) => <span key={i}>{t}</span>)}
          </div>
        </div>

        {tab === 'ask' ? <Ask /> : <Evaluation />}

        <footer className="muted" style={{ textAlign: 'center' }}>Runs on my LLM Gateway</footer>
      </div>
    </div>
  )
}
