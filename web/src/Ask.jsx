import { useEffect, useRef, useState } from 'react'
import { askStream, getJSON } from './api.js'
import Trace from './Trace.jsx'

// Recorded examples are revealed step by step so the page shows how an answer is reached.
const STEPS = ['retrieval', 'candidates', 'clusters', 'distinguishing', 'judge', 'confidence']
const STATUS = ['finding tables', 'writing candidates', 'running and grouping', 'building a tiny example', 'asking the judge', 'scoring confidence']

function reveal(trace, stage) {
  const out = {}
  STEPS.slice(0, stage).forEach((k) => { out[k] = trace[k] })
  if (stage >= 5) out.judge_error = trace.judge_error
  if (stage >= 6) Object.assign(out, { answer: trace.answer, clarifying_question: trace.clarifying_question, usage: trace.usage, timings_ms: trace.timings_ms })
  return out
}

export default function Ask() {
  const [mode, setMode] = useState('examples')
  const [examples, setExamples] = useState([])
  const [picked, setPicked] = useState(0)
  const [stage, setStage] = useState(0)
  const [databases, setDatabases] = useState([])
  const [database, setDatabase] = useState('shop')
  const [question, setQuestion] = useState('')
  const [evidence, setEvidence] = useState('')
  const [live, setLive] = useState(null)       // partial trace of a live run
  const [busy, setBusy] = useState(false)
  const [status, setStatus] = useState('')
  const [error, setError] = useState('')
  const timer = useRef(null)
  const abort = useRef(null)

  useEffect(() => {
    getJSON('/examples.json').then(setExamples).catch(() => setExamples([]))
    getJSON('/api/databases')
      .then((dbs) => {
        const ok = dbs.filter((d) => d.available)
        setDatabases(ok)
        if (ok.length && !ok.some((d) => d.name === 'shop')) setDatabase(ok[0].name)
      })
      .catch(() => setDatabases([]))
    return () => { clearInterval(timer.current); abort.current?.abort() }
  }, [])

  function play() {
    clearInterval(timer.current)
    setStage(0)
    timer.current = setInterval(() => {
      setStage((s) => {
        if (s >= 6) { clearInterval(timer.current); return s }
        return s + 1
      })
    }, 700)
  }

  // replay whenever the examples load or another one is picked
  useEffect(() => { if (mode === 'examples' && examples.length) play() }, [examples, picked, mode])

  async function runLive() {
    if (busy || question.trim().length < 3) return
    abort.current?.abort()
    abort.current = new AbortController()
    setBusy(true); setError(''); setLive({}); setStatus(STATUS[0])
    try {
      await askStream({ database, question: question.trim(), evidence }, (kind, data) => {
        if (kind === 'error') { setError(data.message); return }
        if (kind === 'retrieval') { setLive((t) => ({ ...t, retrieval: data })); setStatus(STATUS[1]) }
        else if (kind === 'candidate') setLive((t) => ({ ...t, candidates: [...(t.candidates || []), data].sort((a, b) => a.id - b.id) }))
        else if (kind === 'clusters') {
          setLive((t) => {
            const of = {}
            data.clusters.forEach((k) => k.members.forEach((id) => { of[id] = k.id }))
            return { ...t, clusters: data.clusters, candidates: (t.candidates || []).map((c) => ({ ...c, cluster: of[c.id] ?? null })) }
          })
          setStatus(STATUS[4])
        }
        else if (kind === 'distinguishing') setLive((t) => ({ ...t, distinguishing: data.example }))
        else if (kind === 'judge') { setLive((t) => ({ ...t, judge: data })); setStatus(STATUS[5]) }
        else if (kind === 'final') setLive((t) => ({ ...t, ...data }))
        else if (kind === 'trace') setLive(data)
      }, abort.current.signal)
    } catch (e) {
      if (e.name !== 'AbortError') setError('Could not reach the server. The recorded examples keep working.')
    } finally {
      setBusy(false); setStatus('')
    }
  }

  const example = examples[picked]
  const shown = mode === 'examples' ? (example ? reveal(example, stage) : null) : live
  const replaying = mode === 'examples' && example && stage < 6

  return (
    <main className="stack">
      <section className="win" aria-label="Ask a question">
        <div className="bar"><span>ask.exe</span><span className="dots" aria-hidden="true"><i /><i /><i /></span></div>
        <div className="pad" style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          <div className="row" style={{ gap: 8, alignItems: 'center' }}>
            <button className={mode === 'examples' ? 'on' : ''} onClick={() => setMode('examples')}>Recorded examples</button>
            <button className={mode === 'live' ? 'on' : ''} onClick={() => setMode('live')}>Live</button>
            <span className="tag" style={{ marginLeft: 'auto' }}>database: {mode === 'examples' ? (example?.database || 'shop') : database}</span>
          </div>

          {mode === 'examples' ? (
            <>
              {examples.length === 0 ? <div className="muted">No recorded examples found (web/public/examples.json).</div> : null}
              <div className="row" style={{ gap: 10 }}>
                {examples.map((ex, i) => (
                  <button key={i} className={`chip${i === picked ? ' on' : ''}`} onClick={() => { setPicked(i); if (i === picked) play() }}>
                    {ex.question}
                  </button>
                ))}
              </div>
              {example?.sample ? <div className="muted">Sample trace on the demo shop database, recorded with scripted model replies. Replays instantly and uses no model quota.</div> : null}
              {example?.evidence ? <div className="muted">Hint given with the question: {example.evidence}</div> : null}
              <div className="row" style={{ alignItems: 'center' }}>
                <button className="go" onClick={play}>Replay <span aria-hidden="true">▶</span></button>
                {replaying ? <span className="px muted" role="status" style={{ fontSize: 15 }}>{STATUS[Math.min(stage, 5)]}<span className="blink">_</span></span> : null}
              </div>
            </>
          ) : (
            <>
              <div className="notice">
                Live questions use today's free model quota (about six model calls each). When it runs out, the recorded examples keep working.
              </div>
              <div className="row" style={{ alignItems: 'end' }}>
                <label style={{ flex: '0 1 200px' }}>
                  <span className="lab">Database</span>
                  <select value={database} onChange={(e) => setDatabase(e.target.value)}>
                    {(databases.length ? databases : [{ name: database }]).map((d) => <option key={d.name} value={d.name}>{d.name}</option>)}
                  </select>
                </label>
                <label style={{ flex: '1 1 420px' }}>
                  <span className="lab">Question</span>
                  <input type="text" value={question} maxLength={500} placeholder="Which city has the most Corporate customers?"
                    onChange={(e) => setQuestion(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') runLive() }} />
                </label>
              </div>
              <div className="row" style={{ alignItems: 'end' }}>
                <label style={{ flex: '1 1 420px' }}>
                  <span className="lab">Hint (optional)</span>
                  <input type="text" value={evidence} maxLength={500} placeholder="e.g. last month refers to October 2026"
                    onChange={(e) => setEvidence(e.target.value)} style={{ fontWeight: 500, fontSize: 14 }} />
                </label>
                <button className="go" onClick={runLive} disabled={busy || question.trim().length < 3} style={{ minHeight: 48 }}>
                  {busy ? 'Running' : 'Run'} <span aria-hidden="true">▶</span>
                </button>
              </div>
              {busy && status ? <div className="px muted" role="status" style={{ fontSize: 15 }}>{status}<span className="blink">_</span></div> : null}
              {error ? <div className="notice" role="alert">{error}</div> : null}
            </>
          )}
        </div>
      </section>

      <Trace t={shown} />
    </main>
  )
}
