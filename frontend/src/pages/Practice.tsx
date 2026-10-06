import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { api, useMeta, type QuestionFilters } from '../api';
import { AiTutor } from '../components/AiTutor';
import { Markup } from '../components/Markup';
import { OptionList } from '../components/OptionList';
import { fmtNum, fmtPct } from '../lib/format';
import type { AnswerResult, PracticeQuestion } from '../types';

const FILTER_KEYS = ['exam', 'stage', 'subject', 'chapter', 'year', 'status', 'order', 'search', 'paper',
  'origin', 'semantic', 'similar', 'ids'] as const;
const PAGE = 20;

interface Answered {
  chosen: number | null;
  result: AnswerResult;
}

export default function Practice() {
  const { meta } = useMeta();
  const [params, setParams] = useSearchParams();
  const filters = useMemo(() => {
    const f: QuestionFilters = {};
    for (const k of FILTER_KEYS) {
      const v = params.get(k);
      if (v) f[k] = v;
    }
    if (f.paper) f.order = 'paper';
    return f;
  }, [params]);
  const smart = params.get('mode') === 'smart';
  const filterKey = JSON.stringify(filters) + (smart ? ':smart' : '');
  // Modes that come from elsewhere in the app replace the filter bar with a banner.
  const special = smart || !!(filters.paper || filters.semantic || filters.similar || filters.ids || filters.origin === 'ai');

  const [items, setItems] = useState<PracticeQuestion[]>([]);
  const [total, setTotal] = useState(0);
  const [nextAfter, setNextAfter] = useState<number | null>(null);
  const [idx, setIdx] = useState(0);
  const [answers, setAnswers] = useState<Record<number, Answered>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tutorFor, setTutorFor] = useState<number | null>(null);
  const [searchDraft, setSearchDraft] = useState(filters.search ?? '');
  const seed = useMemo(() => Math.floor(Math.random() * 2 ** 31), [filterKey]); // eslint-disable-line react-hooks/exhaustive-deps
  const loadingMore = useRef(false);
  const shownAt = useRef(Date.now());

  // Smart practice asks the adaptive engine for the next batch each time, so later
  // batches already reflect what the learner model learned from this session.
  const load = useCallback(async (after: number | null, loaded: PracticeQuestion[]) => {
    if (smart) {
      const r = await api.smart(10, filters.subject, loaded.map((it) => it.id));
      return { total: 0, items: r.items, next_after: r.items.length ? 1 : null };
    }
    return api.questions({ ...filters, seed, after, limit: PAGE });
  }, [filterKey, seed]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError(null);
    setItems([]);
    setIdx(0);
    setAnswers({});
    setTutorFor(null);
    load(null, []).then(
      (page) => {
        if (!alive) return;
        setItems(page.items);
        setTotal(page.total);
        setNextAfter(page.next_after);
        setLoading(false);
      },
      (e: Error) => alive && (setError(e.message), setLoading(false)),
    );
    return () => {
      alive = false;
    };
  }, [filterKey, seed]); // eslint-disable-line react-hooks/exhaustive-deps

  // Keep a few questions buffered ahead of the learner.
  useEffect(() => {
    if (loadingMore.current || nextAfter === null || idx < items.length - 3) return;
    loadingMore.current = true;
    load(nextAfter, items).then((page) => {
      setItems((prev) => {
        const seen = new Set(prev.map((q) => q.id));
        return [...prev, ...page.items.filter((q) => !seen.has(q.id))];
      });
      setNextAfter(page.next_after);
    }).finally(() => {
      loadingMore.current = false;
    });
  }, [idx, items.length, nextAfter]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    shownAt.current = Date.now();
  }, [idx]);

  const q = items[idx];
  const answered = q ? answers[q.id] : undefined;

  const choose = useCallback(async (i: number | null) => {
    if (!q || answers[q.id]) return;
    try {
      const result = await api.answer(q.id, i, Date.now() - shownAt.current);
      setAnswers((a) => ({ ...a, [q.id]: { chosen: i, result } }));
    } catch (e) {
      setError((e as Error).message);
    }
  }, [q, answers]);

  const go = useCallback((delta: number) => {
    setIdx((i) => Math.max(0, Math.min(items.length - 1, i + delta)));
    setTutorFor(null);
  }, [items.length]);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const tag = (e.target as HTMLElement).tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || e.metaKey || e.ctrlKey || e.altKey) return;
      if (q && /^[1-5]$/.test(e.key) && Number(e.key) <= q.options.length) void choose(Number(e.key) - 1);
      else if (e.key === 'ArrowRight') go(1);
      else if (e.key === 'ArrowLeft') go(-1);
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [q, choose, go]);

  function setFilter(key: (typeof FILTER_KEYS)[number], value: string) {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    if (key === 'subject') next.delete('chapter');
    if (key === 'exam') next.delete('stage');
    setParams(next, { replace: true });
  }

  async function toggleBookmark() {
    if (!q) return;
    const { bookmarked } = await api.toggleBookmark(q.id);
    setItems((list) => list.map((it) => (it.id === q.id ? { ...it, bookmarked } : it)));
  }

  const session = Object.values(answers).filter((a) => a.chosen !== null);
  const correct = session.filter((a) => a.result.is_correct).length;
  const exam = meta?.exams.find((e) => e.code === filters.exam);
  const chapters = meta?.chapters.filter((c) => c.subject === filters.subject) ?? [];

  return (
    <div className="practice">
      {special ? (
        <div className="card notice row between gap">
          <span>
            {smart ? <>Smart practice: questions picked for you by the adaptive engine. Each one says why,
              and every answer updates your learner model. <Link to="/coach">How it decides →</Link></>
              : filters.paper ? <>Practising one previous-year paper, untimed{q ? <>: <strong>{q.paper_title}</strong></> : null}</>
                : filters.semantic ? <>Questions matching “<strong>{filters.semantic}</strong>” (<Link to={`/search?q=${encodeURIComponent(filters.semantic)}`}>back to search</Link>)</>
                  : filters.similar ? <>Questions most similar in meaning to the one you were on</>
                    : filters.origin === 'ai' || (items.length > 0 && items.every((it) => it.origin === 'ai'))
                      ? <>AI-generated questions: written by Gemini in the style of real PYQs and kept only
                      when an independent solve agreed with the answer. They are not from real papers.</>
                      : <>Selected questions</>}
          </span>
          <Link className="btn btn-sm" to="/practice">All questions</Link>
        </div>
      ) : (
        <div className="card filters">
          <Select label="Exam" value={filters.exam} onChange={(v) => setFilter('exam', v)}
            options={[['', 'All exams'], ...(meta?.exams.map((e) => [e.code, e.name] as [string, string]) ?? [])]} />
          {exam && exam.stages.length > 1 && (
            <Select label="Stage" value={filters.stage} onChange={(v) => setFilter('stage', v)}
              options={[['', 'All stages'], ...exam.stages.map((s) => [s.code, s.name] as [string, string])]} />
          )}
          <Select label="Subject" value={filters.subject} onChange={(v) => setFilter('subject', v)}
            options={[['', 'All subjects'], ...(meta?.subjects.filter((s) => s.count).map((s) => [s.code, s.name] as [string, string]) ?? [])]} />
          <Select label="Topic" value={filters.chapter} onChange={(v) => setFilter('chapter', v)} disabled={!filters.subject}
            options={[['', filters.subject ? 'All topics' : 'Pick a subject first'],
              ...chapters.map((c) => [c.chapter, `${c.label} (${fmtNum(c.n)})`] as [string, string])]} />
          <Select label="Year" value={filters.year} onChange={(v) => setFilter('year', v)}
            options={[['', 'Any year'], ...(meta?.years.map((y) => [String(y), String(y)] as [string, string]) ?? [])]} />
          <Select label="Show" value={filters.status} onChange={(v) => setFilter('status', v)}
            options={[['', 'All questions'], ['unattempted', 'Not attempted yet'], ['incorrect', 'Got wrong last time'], ['bookmarked', 'Bookmarked']]} />
          <Select label="Order" value={filters.order} onChange={(v) => setFilter('order', v)}
            options={[['', 'Shuffled'], ['sequential', 'In order']]} />
          <form className="field field-grow" onSubmit={(e) => { e.preventDefault(); setFilter('search', searchDraft.trim()); }}>
            <span className="field-label">Search text</span>
            <input type="search" value={searchDraft} onChange={(e) => setSearchDraft(e.target.value)}
              onBlur={() => searchDraft.trim() !== (filters.search ?? '') && setFilter('search', searchDraft.trim())}
              placeholder="e.g. Harappa, simple interest" />
          </form>
        </div>
      )}

      <div className="row between wrap session-bar">
        <span className="muted">
          {loading ? 'Loading questions…' : smart ? 'Adapts after every answer'
            : `${fmtNum(total)} matching question${total === 1 ? '' : 's'}`}
        </span>
        {session.length > 0 && (
          <span className="session-tally">
            This session: <strong>{correct}</strong> / {session.length} correct · {fmtPct(correct / session.length)}
          </span>
        )}
      </div>

      {error && <p className="error-text">{error}</p>}

      {!loading && !q && !error && (
        <div className="card empty">
          <h2 className="h3">No questions match these filters</h2>
          <p className="muted">Try a different topic, or switch “Show” back to all questions.</p>
        </div>
      )}

      {q && (
        <article className="card question-card">
          <header className="q-head">
            <div className="q-meta">
              <span className="q-num">Q {idx + 1}{total && !smart ? ` of ${fmtNum(total)}` : ''}</span>
              <span className="pill">{meta?.subjects.find((s) => s.code === q.subject)?.name ?? q.subject}</span>
              {q.chapter && <span className="pill pill-soft">{q.chapter_label}</span>}
              {q.last_correct === false && !answered && <span className="pill pill-bad">Wrong last time</span>}
              {q.origin === 'ai' && <span className="pill pill-ai">AI-generated</span>}
            </div>
            <button type="button" className={`icon-btn${q.bookmarked ? ' is-on' : ''}`} onClick={() => void toggleBookmark()}
              aria-pressed={q.bookmarked} title={q.bookmarked ? 'Remove bookmark' : 'Bookmark for revision'}>
              {q.bookmarked ? '★' : '☆'} <span className="small">{q.bookmarked ? 'Saved' : 'Save'}</span>
            </button>
          </header>
          {q.reason && <p className={`reason reason-${q.kind}`}>{q.reason}</p>}
          <p className="q-source muted small">
            {q.paper_title}
            {!answered && <span title="Your learner model's forecast, made before you answer"> · model predicts {fmtPct(q.predicted)} chance you get this right</span>}
          </p>

          <Markup text={q.question} className="q-text" />
          <OptionList
            name={`q${q.id}`}
            options={q.options}
            selected={answered?.chosen ?? null}
            answer={answered ? answered.result.answer : null}
            onSelect={answered ? undefined : (i) => void choose(i)}
          />

          {answered && (
            <div className={`verdict ${answered.result.is_correct ? 'verdict-good' : answered.chosen === null ? 'verdict-neutral' : 'verdict-bad'}`}>
              {answered.result.is_correct
                ? '✓ Correct!'
                : answered.chosen === null
                  ? `Answer: option ${answered.result.answer + 1}`
                  : `✗ Not quite — the answer is option ${answered.result.answer + 1}`}
              {answered.result.predicted !== null && (
                <span className="verdict-note"> · the model had predicted {fmtPct(answered.result.predicted)}</span>
              )}
            </div>
          )}

          {answered?.result.solution && (
            <details className="solution" open>
              <summary>Solution</summary>
              <Markup text={answered.result.solution} />
            </details>
          )}

          {answered && (
            <div className="row gap wrap">
              {meta?.ai_enabled && tutorFor !== q.id && (
                <button type="button" className="btn btn-ai" onClick={() => setTutorFor(q.id)}>
                  ✦ Ask the AI tutor about this question
                </button>
              )}
              <Link className="btn btn-ghost" to={`/practice?similar=${q.id}`}>Similar questions →</Link>
            </div>
          )}
          {tutorFor === q.id && <AiTutor key={q.id} questionId={q.id} chosen={answered?.chosen ?? null} />}

          <footer className="q-actions">
            <button type="button" className="btn" onClick={() => go(-1)} disabled={idx === 0}>← Previous</button>
            {!answered && <button type="button" className="btn btn-ghost" onClick={() => void choose(null)}>Show answer</button>}
            <button type="button" className="btn btn-primary" onClick={() => go(1)} disabled={idx >= items.length - 1}>
              Next →
            </button>
          </footer>
          <p className="muted small kbd-hint">Keys: <kbd>1</kbd>–<kbd>4</kbd> answer · <kbd>←</kbd> <kbd>→</kbd> move</p>
        </article>
      )}
    </div>
  );
}

function Select({ label, value, onChange, options, disabled }: {
  label: string;
  value: string | undefined;
  onChange: (v: string) => void;
  options: [string, string][];
  disabled?: boolean;
}) {
  return (
    <label className="field">
      <span className="field-label">{label}</span>
      <select value={value ?? ''} onChange={(e) => onChange(e.target.value)} disabled={disabled}>
        {options.map(([v, text]) => <option key={v} value={v}>{text}</option>)}
      </select>
    </label>
  );
}
