// Thin client for the FastAPI backend (see api/main.py).

export async function getJSON(path) {
  const r = await fetch(path)
  if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`)
  return r.json()
}

/** Friendly text for the failures a visitor can actually hit. */
export function explainError(status, detail) {
  if (status === 429) return 'Too many questions in a minute. Wait a little and try again, or use the recorded examples.'
  if (status === 404) return 'That database is not configured on this server.'
  const d = String(detail || '')
  if (/429|quota|no_provider_available|Too Many Requests/i.test(d))
    return "Today's free model quota is used up. It resets at 5:30 AM IST. The recorded examples keep working."
  return d || 'Something went wrong while answering. The recorded examples keep working.'
}

/**
 * Ask a question and receive pipeline steps as they happen (Server-Sent Events over fetch).
 * onEvent(kind, data) is called for: retrieval, candidate, clusters, distinguishing, judge,
 * final, trace (the complete trace) and error.
 */
export async function askStream({ database, question, evidence }, onEvent, signal) {
  const r = await fetch('/api/ask/stream', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ database, question, evidence: evidence || '' }),
    signal,
  })
  if (!r.ok || !r.body) {
    let detail = ''
    try { detail = (await r.json()).detail } catch { /* not JSON */ }
    onEvent('error', { message: explainError(r.status, detail) })
    return
  }
  const reader = r.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    let cut
    while ((cut = buf.indexOf('\n\n')) !== -1) {
      const block = buf.slice(0, cut)
      buf = buf.slice(cut + 2)
      let kind = 'message'
      const data = []
      for (const line of block.split('\n')) {
        if (line.startsWith('event:')) kind = line.slice(6).trim()
        else if (line.startsWith('data:')) data.push(line.slice(5).trimStart())
      }
      if (!data.length) continue
      let payload
      try { payload = JSON.parse(data.join('\n')) } catch { continue }
      if (kind === 'error') payload = { message: explainError(0, payload.message) }
      onEvent(kind, payload)
    }
  }
}
