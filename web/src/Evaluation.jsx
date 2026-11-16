import { useEffect, useState } from 'react'
import { getJSON } from './api.js'

const pct = (x) => (x === null || x === undefined ? '—' : `${(100 * x).toFixed(1)}%`)

function Bar({ label, value, main }) {
  return (
    <div className="row" style={{ gap: 10, alignItems: 'center' }}>
      <span style={{ flex: '0 0 210px', fontWeight: 700, fontSize: 14.5 }}>{label}</span>
      <div className="track"><div className={`fill${main ? '' : ' alt'}`} style={{ width: `${Math.round(100 * (value || 0))}%` }} /></div>
      <span className="px" style={{ flex: '0 0 64px', fontSize: 18, fontWeight: 700 }}>{pct(value)}</span>
    </div>
  )
}

// Same categories as the error analysis in docs/evaluation.md.
function breakdown(records) {
  const n = {}
  const add = (k) => { n[k] = (n[k] || 0) + 1 }
  records.filter((r) => !('skipped' in r)).forEach((r) => {
    const j = r.judge || {}
    if (r.error) add('Pipeline error')
    else if (!r.any_candidate_correct) add(r.n_valid === 0 ? 'No candidate produced a result' : 'Every candidate wrote wrong SQL')
    else if (!r.correct) add(j.mode === 'pick' && r.majority_correct ? 'Judge overruled a correct majority' : j.mode === 'pick' ? 'Judge picked the wrong option' : 'Right answer was in the minority')
    else add(r.majority_correct ? 'Correct' : 'Judge rescued a wrong majority')
  })
  return Object.entries(n).sort((a, b) => b[1] - a[1])
}

export default function Evaluation() {
  const [runs, setRuns] = useState(null)   // null = loading, [] = none / API offline
  const [name, setName] = useState('')
  const [run, setRun] = useState(null)

  useEffect(() => {
    getJSON('/api/runs')
      .then((rs) => { setRuns(rs); if (rs.length) setName(rs[0].name); else return getJSON('/eval-sample.json').then(setRun) })
      .catch(() => { setRuns([]); getJSON('/eval-sample.json').then(setRun).catch(() => setRun(null)) })
  }, [])

  useEffect(() => {
    if (!name) return
    getJSON(`/api/runs/${encodeURIComponent(name)}`)
      .then((d) => setRun({ name: d.name, headline: d.summary.headline, summary: d.summary, breakdown: breakdown(d.records), ablation: d.ablation, pending: (d.summary.infra_failures_pending || []).length }))
      .catch(() => setRun(null))
  }, [name])

  if (!run) return <main className="stack"><section className="win"><div className="pad muted">{runs === null ? 'Loading evaluation runs…' : 'No evaluation runs found yet. Run `python -m eval.run` to create one.'}</div></section></main>
  const h = run.headline
  const judge = run.summary?.verification?.judge
  const cost = run.summary?.cost

  return (
    <main className="stack">
      <section className="win pop" aria-label="Benchmark headline">
        <div className="bar"><span>scoreboard · {run.name} · {h.questions} questions</span><span className="dots" aria-hidden="true"><i /><i /><i /></span></div>
        <div className="pad" style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          {runs && runs.length > 1 ? (
            <label style={{ maxWidth: 320 }}>
              <span className="lab">Run</span>
              <select value={name} onChange={(e) => setName(e.target.value)}>{runs.map((r) => <option key={r.name} value={r.name}>{r.name}</option>)}</select>
            </label>
          ) : null}
          <Bar label="First candidate only" value={h.first_candidate_accuracy} />
          <Bar label="Majority vote" value={h.majority_vote_accuracy} />
          <Bar label="Verified pipeline" value={h.execution_accuracy} main />
          <Bar label="Oracle (any candidate right)" value={h.oracle_accuracy} />
          {run.note ? <div className="notice">{run.note}</div> : null}
          {run.pending ? <div className="notice">{run.pending} questions hit model-quota failures and are not scored yet. Rerun the same command to retry them.</div> : null}
          {run.snapshot ? <div className="muted">Saved snapshot shown because the API has no runs to serve.</div> : null}
        </div>
      </section>

      <div className="row" style={{ gap: 22 }}>
        <section className="win pop" aria-label="Selective answering" style={{ flex: '1 1 320px' }}>
          <div className="bar"><span>knows when it is unsure</span></div>
          <div className="pad row" style={{ gap: 16 }}>
            <div style={{ flex: '1 1 120px' }}>
              <div className="px" style={{ fontSize: 44, fontWeight: 700, lineHeight: 1 }}>{pct(h.execution_accuracy)}</div>
              <div style={{ fontSize: 14, marginTop: 6 }}>accuracy when it answers every question</div>
            </div>
            <div style={{ flex: '1 1 120px' }}>
              <div className="px" style={{ fontSize: 44, fontWeight: 700, lineHeight: 1, color: 'var(--ok)' }}>{pct(h.accuracy_at_threshold)}</div>
              <div style={{ fontSize: 14, marginTop: 6 }}>accuracy when it answers only the {pct(h.coverage_at_threshold)} it is most confident about</div>
            </div>
          </div>
        </section>
        {run.breakdown && run.breakdown.length ? (
          <section className="win pop" aria-label="Where questions went" style={{ flex: '1 1 320px' }}>
            <div className="bar"><span>where the questions went</span></div>
            <div className="pad scroll">
              <table><tbody>{run.breakdown.map(([k, v]) => <tr key={k}><td>{k}</td><td>{v}</td></tr>)}</tbody></table>
            </div>
          </section>
        ) : null}
      </div>

      {judge || cost ? (
        <section className="win pop" aria-label="Judge and cost">
          <div className="bar"><span>the judge, measured</span></div>
          <div className="pad row" style={{ gap: 6 }}>
            {judge?.pick?.n ? <span className="tag">picks correctly: {pct(judge.pick.accuracy)} of {judge.pick.n}</span> : null}
            {judge?.verify?.n ? <span className="tag">false alarms: {pct(judge.verify.false_alarm_rate)}</span> : null}
            {judge?.verify?.n ? <span className="tag">catches wrong answers: {pct(judge.verify.catches_wrong_rate)}</span> : null}
            {judge?.self_preference?.comparable_decisions ? <span className="tag">picks its own model: {pct(judge.self_preference.picks_own_model_rate)}</span> : null}
            {cost ? <span className="tag">{cost.avg_llm_calls} model calls per question</span> : null}
            {cost ? <span className="tag">p50 {(cost.latency_p50_ms / 1000).toFixed(1)} s</span> : null}
          </div>
        </section>
      ) : null}

      <section className="win pop" aria-label="Ablations">
        <div className="bar"><span>what each part is worth</span></div>
        <div className="pad">
          {run.ablation ? <pre>{run.ablation}</pre> : <div style={{ fontSize: 14.5, lineHeight: 1.5 }}>The ablation table (accuracy with each component switched off) appears here after <code>python -m eval.ablate</code> has been run.</div>}
        </div>
      </section>
    </main>
  )
}
