// Renders a (possibly partial) pipeline trace, one window per step.
// Fields appear as the pipeline produces them, so the same component serves
// recorded examples (revealed on a timer) and live runs (revealed by SSE events).
import { Heart, Scales } from './Decor.jsx'

function Win({ title, label, children }) {
  return (
    <section className="win pop" aria-label={label || title}>
      <div className="bar">
        <span>{title}</span>
        <span className="dots" aria-hidden="true"><i /><i /><i /></span>
      </div>
      <div className="pad">{children}</div>
    </section>
  )
}

function DataTable({ columns, rows, limit = 8 }) {
  if (!rows || rows.length === 0) return <div style={{ fontFamily: 'var(--mono)', fontSize: 13 }}>(no rows)</div>
  return (
    <div className="scroll">
      <table>
        <thead><tr>{columns.map((c, i) => <th key={i}>{c}</th>)}</tr></thead>
        <tbody>
          {rows.slice(0, limit).map((r, i) => (
            <tr key={i}>{r.map((v, j) => <td key={j}>{v === null ? 'NULL' : String(v)}</td>)}</tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

const PROMPTS = { direct: 'direct prompt', plan: 'plan first', decompose: 'step by step' }
const groupName = (i) => `group ${i + 1}`
const groupTag = (i) => `tag ${i === 0 ? 'k0' : 'k1'}`

function Retrieval({ r }) {
  return (
    <Win title="1 · find the right tables" label="Step 1: schema retrieval">
      <div className="row" style={{ gap: 18 }}>
        <div style={{ flex: '1 1 300px', display: 'flex', flexDirection: 'column', gap: 8 }}>
          <span className="lab">Tables used{r.mode === 'full' ? ' (small schema: all of it)' : ''}</span>
          <div className="row" style={{ gap: 6 }}>
            {r.tables.map((t) => (
              <span key={t} className="tag">{t}{(r.bridge_tables || []).includes(t) ? ' (join bridge)' : ''}</span>
            ))}
          </div>
        </div>
        <div style={{ flex: '1 1 300px', display: 'flex', flexDirection: 'column', gap: 8 }}>
          <span className="lab">Values matched in the data</span>
          {r.value_matches && r.value_matches.length ? (
            <div className="row" style={{ gap: 6 }}>
              {r.value_matches.slice(0, 6).map((m, i) => (
                <span key={i} className="tag">{m.table}.{m.column} = {m.value}</span>
              ))}
            </div>
          ) : <div className="muted">No literal values from the question were found in text columns.</div>}
        </div>
      </div>
    </Win>
  )
}

function Candidates({ list, clustered }) {
  return (
    <Win title={`2 · ${list.length === 5 ? 'five' : list.length} candidates write SQL`} label="Step 2: candidate queries">
      <div className="row" style={{ gap: 12 }}>
        {list.map((c) => (
          <article key={c.id} className="card pop" style={{ flex: '1 1 190px', display: 'flex', flexDirection: 'column', gap: 8 }}>
            <div className="row" style={{ gap: 8, alignItems: 'center' }}>
              <span className="px" style={{ fontSize: 22, fontWeight: 700 }}>{c.label}</span>
              {c.error
                ? <span className="tag warn">failed</span>
                : clustered && c.cluster !== null && c.cluster !== undefined
                  ? <span className={groupTag(c.cluster)}>{groupName(c.cluster)}</span>
                  : <span className="tag">ran</span>}
              {c.attempts && c.attempts.length > 1 && !c.error ? <span className="tag">self-repaired</span> : null}
            </div>
            <div className="muted" style={{ fontSize: 12.5 }}>
              {c.served_model || c.model}<br />
              {PROMPTS[c.prompt] || c.prompt}{c.temperature > 0 ? ', warmer' : ''}
            </div>
            {c.error ? <div className="muted">{String(c.error).slice(0, 140)}</div> : null}
            <details>
              <summary>show SQL</summary>
              <pre>{c.sql}</pre>
            </details>
          </article>
        ))}
      </div>
    </Win>
  )
}

function Clusters({ clusters, candidates }) {
  const byId = Object.fromEntries(candidates.map((c) => [c.id, c]))
  const total = clusters.reduce((n, k) => n + k.size, 0) || 1
  return (
    <Win title="3 · group by what they return" label="Step 3: group by result">
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        {clusters.length === 0 ? <div className="muted">No candidate produced a result.</div> : null}
        {clusters.map((k) => {
          const labels = k.members.map((id) => byId[id]?.label).filter(Boolean).join(', ')
          const rep = byId[k.representative]
          const n = rep?.result?.row_count
          return (
            <div key={k.id} className="row" style={{ gap: 10, alignItems: 'center' }}>
              <span className={groupTag(k.id)} style={{ flex: '0 0 auto' }}>{groupName(k.id)}</span>
              <div className="track">
                <div className={`fill${k.id === 0 ? '' : ' alt'}`} style={{ width: `${Math.round((100 * k.size) / total)}%` }} />
              </div>
              <span style={{ flex: '1 1 260px', fontSize: 14 }}>
                {labels} {k.size === 1 ? 'returns' : 'return'} {n === undefined ? 'the same result' : `${n} row${n === 1 ? '' : 's'}`}
              </span>
            </div>
          )
        })}
      </div>
    </Win>
  )
}

function Distinguishing({ ex, candidates }) {
  const clusterOf = Object.fromEntries(candidates.map((c) => [c.label, c.cluster]))
  return (
    <Win title="4 · the tiny example that tells them apart" label="Step 4: distinguishing example">
      <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
        <div style={{ fontSize: 14.5, lineHeight: 1.5 }}>
          The real tables were shrunk to the {ex.rows_total} row{ex.rows_total === 1 ? '' : 's'} below
          ({ex.tests_run} test runs). On this mini database the groups give different answers, so the difference is easy to see.
        </div>
        <div className="row">
          {Object.entries(ex.tables).map(([name, t]) => (
            <div key={name} style={{ flex: '1 1 300px', minWidth: 0 }}>
              <span className="lab">{name}</span>
              <DataTable columns={t.columns} rows={t.rows} limit={12} />
            </div>
          ))}
        </div>
        <div className="row">
          {Object.entries(ex.outputs).map(([label, out]) => {
            const k = clusterOf[label] ?? 0
            return (
              <div key={label} className={`card${k === 0 ? '' : ' dashed'}`} style={{ flex: '1 1 240px' }}>
                <span className={groupTag(k)}>{groupName(k)} returns</span>
                <div style={{ marginTop: 8 }}><DataTable columns={out.columns} rows={out.rows} limit={6} /></div>
              </div>
            )
          })}
        </div>
      </div>
    </Win>
  )
}

function Judge({ judge, error, candidates }) {
  let verdict = 'unavailable'
  let ok = false
  let text = error ? 'The judge could not be reached, so the answer rests on the vote alone.' : ''
  if (judge) {
    if (judge.mode === 'pick') {
      const c = candidates.find((x) => x.label === judge.choice)
      ok = Boolean(judge.choice)
      verdict = ok ? `picks ${groupName(c?.cluster ?? 0)}` : 'rejects both groups'
    } else {
      ok = Boolean(judge.choice)
      verdict = ok ? 'passes the answer' : 'raises a concern'
    }
    text = judge.reason || (judge.issues && judge.issues[0]) || (ok ? 'The query answers the question as asked.' : 'The judge was not convinced.')
  }
  return (
    <Win title="5 · the judge" label="Step 5: judge">
      <div className="row" style={{ alignItems: 'flex-start' }}>
        <Scales />
        <div className="card" style={{ flex: '1 1 320px', padding: '12px 14px', display: 'flex', flexDirection: 'column', gap: 8 }}>
          <div className="row" style={{ gap: 8, alignItems: 'center' }}>
            <span className={`tag ${ok ? 'ok' : 'warn'}`}>{verdict}</span>
            {judge ? <span className="lab">judge: {judge.model}</span> : null}
          </div>
          <div className="px" style={{ fontSize: 18, lineHeight: 1.45 }}>
            {text} <span className="blink" aria-hidden="true">▼</span>
          </div>
          {judge ? (
            <div className="muted">
              {judge.used_example
                ? 'The judge saw the tiny example, not just the two queries.'
                : 'The judge is one signal among several: its verdict changes the confidence score, and the evaluation measures how often it is wrong.'}
            </div>
          ) : null}
        </div>
      </div>
    </Win>
  )
}

function Answer({ t }) {
  const conf = t.confidence
  const a = t.answer
  const pips = Math.round(conf.score * 10)
  return (
    <Win title="answer" label="Answer">
      <div className="row" style={{ gap: 18 }}>
        <div style={{ flex: '1 1 260px', minWidth: 0, display: 'flex', flexDirection: 'column', gap: 10 }}>
          <span className="lab">Confidence</span>
          <div className="row" style={{ gap: 10, alignItems: 'center' }}>
            <span className="px" style={{ fontSize: 40, fontWeight: 700, lineHeight: 1 }}>{conf.score.toFixed(2)}</span>
            <span className={`tag ${conf.abstained ? 'warn' : 'ok'}`}>{conf.abstained ? 'not sure enough' : 'answered'}</span>
          </div>
          <div role="img" aria-label={`Confidence ${conf.score.toFixed(2)} out of 1`} className="row" style={{ gap: 4 }}>
            {Array.from({ length: 10 }, (_, i) => <Heart key={i} style={{ opacity: i < pips ? 1 : 0.2 }} />)}
          </div>
          {conf.abstained ? (
            <div className="notice">
              {t.clarifying_question || 'The checks did not agree strongly enough to answer. Try adding a detail to the question.'}
            </div>
          ) : null}
          <div className="row" style={{ gap: 6 }}>
            {t.usage ? <span className="tag">{t.usage.calls} model calls</span> : null}
            {t.timings_ms ? <span className="tag">{(t.timings_ms.total / 1000).toFixed(1)} s</span> : null}
          </div>
        </div>
        {a ? (
          <>
            <div style={{ flex: '1 1 220px', minWidth: 0, display: 'flex', flexDirection: 'column', gap: 8 }}>
              <span className="lab">{conf.abstained ? 'Best guess' : 'Result'}: {a.row_count} row{a.row_count === 1 ? '' : 's'}</span>
              <DataTable columns={a.columns} rows={a.rows} />
              {a.row_count > 8 ? <div className="muted">and {a.row_count - 8} more</div> : null}
            </div>
            <div style={{ flex: '2 1 360px', minWidth: 0, display: 'flex', flexDirection: 'column', gap: 8 }}>
              <span className="lab">SQL that was run (candidate {a.candidate})</span>
              <pre>{a.sql}</pre>
            </div>
          </>
        ) : (
          <div className="muted" style={{ flex: '2 1 360px' }}>No candidate produced a usable query for this question.</div>
        )}
      </div>
    </Win>
  )
}

export default function Trace({ t }) {
  if (!t) return null
  const cands = t.candidates || []
  return (
    <>
      {t.retrieval ? <Retrieval r={t.retrieval} /> : null}
      {cands.length ? <Candidates list={cands} clustered={Boolean(t.clusters)} /> : null}
      {t.clusters ? <Clusters clusters={t.clusters} candidates={cands} /> : null}
      {t.distinguishing ? <Distinguishing ex={t.distinguishing} candidates={cands} /> : null}
      {t.judge || t.judge_error ? <Judge judge={t.judge} error={t.judge_error} candidates={cands} /> : null}
      {t.confidence ? <Answer t={t} /> : null}
    </>
  )
}
