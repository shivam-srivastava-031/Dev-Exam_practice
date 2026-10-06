import { useEffect, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { api, streamText, useMeta } from '../api';
import { Markup } from '../components/Markup';
import { fmtNum, fmtPct } from '../lib/format';
import type { SearchResponse } from '../types';

const EXAMPLES = ['who built the Red Fort', 'boat upstream downstream', 'successive discounts shortcut',
  'fundamental duties article 51A', 'synonym of benevolent'];

/** "[1, 4]" in the AI answer becomes links that jump to the cited PYQ cards. */
function linkCitations(text: string): string {
  return text.replace(/\[(\d+(?:\s*,\s*\d+)*)\]/g, (_, nums: string) =>
    nums.split(',').map((n) => `[[${n.trim()}]](#src-${n.trim()})`).join(' '));
}

export default function Search() {
  const { meta } = useMeta();
  const [params, setParams] = useSearchParams();
  const q = params.get('q') ?? '';
  const exam = params.get('exam') ?? '';
  const subject = params.get('subject') ?? '';
  const [draft, setDraft] = useState(q);
  const [res, setRes] = useState<SearchResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => setDraft(q), [q]);
  useEffect(() => {
    if (q.trim().length < 2) {
      setRes(null);
      return;
    }
    let alive = true;
    setLoading(true);
    setError(null);
    api.search(q, { exam: exam || undefined, subject: subject || undefined }).then(
      (r) => alive && (setRes(r), setLoading(false)),
      (e: Error) => alive && (setError(e.message), setLoading(false)),
    );
    return () => {
      alive = false;
    };
  }, [q, exam, subject]);

  function submit(text = draft) {
    const next = new URLSearchParams(params);
    next.set('q', text.trim());
    setParams(next);
  }

  function setFilter(key: string, value: string) {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next, { replace: true });
  }

  const practiseAll = new URLSearchParams({ semantic: q, ...(exam ? { exam } : {}), ...(subject ? { subject } : {}) });

  return (
    <div className="stack-lg search-page">
      <section className="stack-sm">
        <h1>Search</h1>
        <p className="muted">
          Finds previous-year questions by meaning as well as keywords, across {meta ? fmtNum(meta.total_questions) : 'all'} PYQs.
          Paste a whole question to find its twins and its topic.
        </p>
      </section>

      <form className="card search-box" onSubmit={(e) => { e.preventDefault(); submit(); }}>
        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault();
              submit();
            }
          }}
          rows={draft.length > 80 ? 4 : 2}
          placeholder="e.g. who built the Red Fort · train crossing a platform · or paste a full question"
          aria-label="Search question bank"
        />
        <div className="row gap wrap between">
          <div className="row gap wrap">
            <select value={exam} onChange={(e) => setFilter('exam', e.target.value)} aria-label="Exam">
              <option value="">All exams</option>
              {meta?.exams.map((x) => <option key={x.code} value={x.code}>{x.name}</option>)}
            </select>
            <select value={subject} onChange={(e) => setFilter('subject', e.target.value)} aria-label="Subject">
              <option value="">All subjects</option>
              {meta?.subjects.filter((s) => s.count).map((s) => <option key={s.code} value={s.code}>{s.name}</option>)}
            </select>
          </div>
          <button type="submit" className="btn btn-primary" disabled={draft.trim().length < 2}>Search</button>
        </div>
        {!q && (
          <div className="chips">
            {EXAMPLES.map((ex) => <button key={ex} type="button" className="chip" onClick={() => submit(ex)}>{ex}</button>)}
          </div>
        )}
      </form>

      {error && <p className="error-text">{error}</p>}
      {loading && !res && <p className="muted">Searching…</p>}

      {res && (
        <div className={`stack${loading ? ' is-stale' : ''}`}>
          {res.classification && (
            <div className="card topic-guess">
              <span className="muted small">Looks like a question on</span>
              <div className="row gap wrap">
                {res.classification.map((g, i) => (
                  <Link key={g.chapter} className={`pill ${i === 0 ? 'pill-soft' : ''}`}
                    to={`/practice?subject=${g.subject}&chapter=${g.chapter}`}>
                    {g.label} · {fmtPct(g.probability)}
                  </Link>
                ))}
              </div>
              <span className="muted small">predicted by the topic model trained on the PYQ bank</span>
            </div>
          )}

          {meta?.ai_enabled && res.results.length > 0 && (
            <AskPanel key={res.query + exam + subject} query={res.query} sourceIds={res.results.slice(0, 8).map((r) => r.id)} />
          )}

          <div className="row between wrap gap">
            <span className="muted">
              {res.results.length} closest matches{!res.dense_index && ' (keyword only: the vector index is not built yet)'}
            </span>
            {res.results.length > 0 && (
              <Link className="btn btn-sm" to={`/practice?${practiseAll}`}>Practise these →</Link>
            )}
          </div>

          {res.results.length === 0 && <div className="card empty"><p>No matches. Try other words.</p></div>}
          {res.results.map((r, i) => (
            <article key={r.id} id={`src-${i + 1}`} className="card result">
              <header className="q-head">
                <div className="q-meta">
                  <span className="cite-num">{i + 1}</span>
                  <span className="pill">{meta?.subjects.find((s) => s.code === r.subject)?.name ?? r.subject}</span>
                  {r.chapter && <span className="pill pill-soft">{r.chapter_label}</span>}
                  {r.via?.map((v) => <span key={v} className="via">{v === 'meaning' ? 'meaning match' : 'keyword match'}</span>)}
                </div>
                <div className="row gap">
                  <Link className="btn btn-sm btn-ghost" to={`/practice?similar=${r.id}`}>Similar</Link>
                  <Link className="btn btn-sm" to={`/practice?ids=${r.id}`}>Practise</Link>
                </div>
              </header>
              <p className="q-source muted small">{r.paper_title}</p>
              <Markup text={r.question} className="q-text clamp" />
            </article>
          ))}
        </div>
      )}
    </div>
  );
}

function AskPanel({ query, sourceIds }: { query: string; sourceIds: number[] }) {
  const [turns, setTurns] = useState<{ role: 'user' | 'model'; text: string }[]>([]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abort = useRef<AbortController | null>(null);

  useEffect(() => () => abort.current?.abort(), []);

  async function ask(text: string) {
    const history = turns;
    const next = [...history, { role: 'user' as const, text }];
    setTurns([...next, { role: 'model', text: '' }]);
    setBusy(true);
    setError(null);
    setInput('');
    abort.current?.abort();
    const controller = new AbortController();
    abort.current = controller;
    try {
      await streamText('/api/ai/ask', { query: text, source_ids: sourceIds, messages: history }, (soFar) => {
        if (!controller.signal.aborted) setTurns([...next, { role: 'model', text: soFar }]);
      }, controller.signal);
    } catch (e) {
      if (controller.signal.aborted) return;
      setError((e as Error).message);
      setTurns(history);
    } finally {
      if (abort.current === controller) setBusy(false);
    }
  }

  if (!turns.length) {
    return (
      <button type="button" className="btn btn-ai ask-cta" onClick={() => void ask(query)}>
        ✦ Ask AI — an answer grounded in these previous-year questions
      </button>
    );
  }
  return (
    <section className="tutor" aria-label="Ask AI">
      <header className="tutor-head">
        <span className="tutor-badge">Ask AI</span>
        <span className="muted small">Gemini, answering from the PYQs below · numbers in [ ] jump to the cited question</span>
      </header>
      <div className="tutor-log">
        {turns.map((t, i) => (
          <div key={i} className={`bubble bubble-${t.role}`}>
            {t.role === 'model' && !t.text ? <span className="typing">Reading the PYQs…</span>
              : <Markup text={t.role === 'model' ? linkCitations(t.text) : t.text} ai={t.role === 'model'} />}
          </div>
        ))}
        {error && <p className="error-text">{error}</p>}
      </div>
      <form className="tutor-input" onSubmit={(e) => { e.preventDefault(); if (input.trim()) void ask(input.trim()); }}>
        <input value={input} onChange={(e) => setInput(e.target.value)} placeholder="Ask a follow-up…" maxLength={2000}
          aria-label="Follow-up question" />
        <button type="submit" className="btn btn-primary" disabled={busy || !input.trim()}>Ask</button>
      </form>
    </section>
  );
}
