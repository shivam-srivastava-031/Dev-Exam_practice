import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, Navigate, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { api, useMeta, type QuestionFilters } from '../api';
import { AiTutor } from '../components/AiTutor';
import { Markup } from '../components/Markup';
import { OptionList } from '../components/OptionList';
import { fmtDate, fmtNum, fmtPct } from '../lib/format';
import {
  EXAM_NAMES, SHOW_TO_STATUS, SUBJECT_NAMES, currentAffairsUrl, examCode, practiceUrl, questionUrl, roughLabel, searchUrl,
  setPageTitle, similarUrl, subjectCode, type PracticeTarget,
} from '../lib/urls';
import type { AnswerResult, PracticeQuestion } from '../types';

const PAGE = 20;

/** Where the questions come from; each mode has its own URL (see lib/urls.ts). */
export type PracticeMode = 'filter' | 'smart' | 'ai' | 'paper' | 'similar' | 'question' | 'news';

interface Answered {
  chosen: number | null;
  result: AnswerResult;
}

// Query keys from before URLs were cleaned up: /practice?subject=MATH&status=incorrect...
const LEGACY_KEYS = ['subject', 'chapter', 'status', 'mode', 'paper', 'similar', 'semantic', 'origin'];

export default function Practice({ mode = 'filter' }: { mode?: PracticeMode }) {
  const { meta } = useMeta();
  const navigate = useNavigate();
  const params = useParams();
  const [query] = useSearchParams();

  // What the URL asks for, as the page's own filter object.
  const target: PracticeTarget = useMemo(() => ({
    subject: subjectCode(params.subject),
    chapter: params.chapter,
    exam: examCode(query.get('exam')),
    stage: query.get('stage') ?? undefined,
    year: query.get('year') ?? undefined,
    status: SHOW_TO_STATUS[query.get('show') ?? ''],
    order: query.get('order') === 'in-order' ? 'sequential' : undefined,
    search: query.get('search') ?? undefined,
    about: query.get('about') ?? undefined,
  }), [params.subject, params.chapter, query]);

  // ...and what the API is asked for.
  const filters: QuestionFilters = useMemo(() => {
    switch (mode) {
      case 'paper': return { paper: params.paper, order: 'paper' };
      case 'similar': return { similar: params.id };
      case 'question': return { ids: params.id };
      case 'ai': return query.get('ids') ? { ids: query.get('ids')! } : { origin: 'ai', order: 'sequential' };
      case 'smart': return { subject: target.subject };
      case 'news': return {}; // the day's quiz comes from its own endpoint (see load)
      default: return {
        exam: target.exam, stage: target.stage, subject: target.subject, chapter: target.chapter, year: target.year,
        status: target.status, order: target.order, search: target.search, semantic: target.about,
      };
    }
  }, [mode, params.paper, params.id, query, target]);
  const filterKey = mode + JSON.stringify(filters) + (mode === 'news' ? params.day : '');
  const currentId = Number(query.get('q')) || null;

  const [items, setItems] = useState<PracticeQuestion[]>([]);
  const [total, setTotal] = useState(0);
  const [nextAfter, setNextAfter] = useState<number | null>(null);
  const [idx, setIdx] = useState(0);
  const [answers, setAnswers] = useState<Record<number, Answered>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tutorFor, setTutorFor] = useState<number | null>(null);
  const [searchDraft, setSearchDraft] = useState(target.search ?? '');
  const seed = useMemo(() => Math.floor(Math.random() * 2 ** 31), [filterKey]); // eslint-disable-line react-hooks/exhaustive-deps
  const loadingMore = useRef(false);
  const shownAt = useRef(Date.now());

  // Smart practice asks the adaptive engine for the next batch each time, so later
  // batches already reflect what the learner model learned from this session.
  const load = useCallback(async (after: number | null, loaded: PracticeQuestion[]) => {
    if (mode === 'smart') {
      const r = await api.smart(10, filters.subject, loaded.map((it) => it.id));
      return { total: 0, items: r.items, next_after: r.items.length ? 1 : null };
    }
    if (mode === 'news') {
      const r = await api.newsQuiz(params.day ?? '');
      return { total: r.items.length, items: r.items, next_after: null };
    }
    return api.questions({ ...filters, seed, after, limit: PAGE });
  }, [filterKey, seed]); // eslint-disable-line react-hooks/exhaustive-deps

  /** Show question `id` and record it in the URL (a history entry, so Back returns to the previous one). */
  const showQuestion = useCallback((id: number, replace = false) => {
    const next = new URLSearchParams(query);
    next.set('q', String(id));
    navigate({ search: next.toString() }, { replace });
  }, [query, navigate]);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError(null);
    setItems([]);
    setIdx(0);
    setAnswers({});
    setTutorFor(null);
    // A ?q= in the URL (a refresh or a shared link) puts that question first.
    const pinned = currentId && mode !== 'question' ? api.questions({ ids: String(currentId) }) : Promise.resolve(null);
    Promise.all([pinned, load(null, [])]).then(
      ([pin, page]) => {
        if (!alive) return;
        const first = pin?.items[0];
        const list = first ? [first, ...page.items.filter((it) => it.id !== first.id)] : page.items;
        setItems(list);
        setTotal(page.total);
        setNextAfter(page.next_after);
        setLoading(false);
        if (list[0] && list[0].id !== currentId && mode !== 'question') showQuestion(list[0].id, true);
      },
      (e: Error) => alive && (setError(e.message), setLoading(false)),
    );
    return () => {
      alive = false;
    };
  }, [filterKey, seed]); // eslint-disable-line react-hooks/exhaustive-deps

  // Back / Forward (or an edited ?q=) moves within the loaded session.
  useEffect(() => {
    if (!currentId || !items.length) return;
    const at = items.findIndex((it) => it.id === currentId);
    if (at >= 0 && at !== idx) {
      setIdx(at);
      setTutorFor(null);
    } else if (at < 0) {
      void api.questions({ ids: String(currentId) }).then((page) => {
        if (!page.items[0]) return;
        setItems((list) => [...list.slice(0, idx + 1), page.items[0], ...list.slice(idx + 1)]);
        setIdx(idx + 1);
      });
    }
  }, [currentId, items]); // eslint-disable-line react-hooks/exhaustive-deps

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
    const next = items[Math.max(0, Math.min(items.length - 1, idx + delta))];
    if (next && next.id !== q?.id) showQuestion(next.id);
  }, [items, idx, q, showQuestion]);

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

  const subjectName = (code?: string) => (code ? SUBJECT_NAMES[code] ?? code : undefined);
  const chapterName = target.chapter
    ? meta?.chapters.find((c) => c.subject === target.subject && c.chapter === target.chapter)?.label ?? roughLabel(target.chapter)
    : undefined;
  const examName = target.exam ? EXAM_NAMES[target.exam] : undefined;

  useEffect(() => {
    const titles: Record<PracticeMode, string | undefined> = {
      filter: [chapterName, target.subject ? `${subjectName(target.subject)} practice` : 'Practice', examName]
        .filter(Boolean).join(' · '),
      smart: 'Smart practice',
      ai: 'AI-generated questions',
      paper: q ? `${q.paper_title} (untimed)` : 'Previous-year paper',
      similar: 'Similar questions',
      question: q ? `${subjectName(q.subject)} question #${q.id}` : `Question #${params.id}`,
      news: `Current-affairs quiz · ${fmtDate(params.day)}`,
    };
    setPageTitle(titles[mode]);
  }); // eslint-disable-line react-hooks/exhaustive-deps

  // Old /practice?subject=MATH&mode=smart... links land on their clean URL.
  const legacy = mode === 'filter' && LEGACY_KEYS.some((k) => query.has(k));
  if (legacy) return <Navigate replace to={legacyTarget(query)} />;
  if (params.subject && !target.subject) {
    return <div className="card empty"><h1 className="h2">No such subject</h1><Link to={practiceUrl()}>All questions</Link></div>;
  }

  function setFilter(changes: Partial<PracticeTarget>) {
    const next = { ...target, ...changes };
    if ('subject' in changes) next.chapter = undefined;
    if ('exam' in changes) next.stage = undefined;
    navigate(practiceUrl(next));
  }

  async function toggleBookmark() {
    if (!q) return;
    const { bookmarked } = await api.toggleBookmark(q.id);
    setItems((list) => list.map((it) => (it.id === q.id ? { ...it, bookmarked } : it)));
  }

  const session = Object.values(answers).filter((a) => a.chosen !== null);
  const correct = session.filter((a) => a.result.is_correct).length;
  const exam = meta?.exams.find((e) => e.code === target.exam);
  // Until the catalogue arrives, the options are the static lists plus whatever the URL selected.
  const chapters = meta?.chapters.filter((c) => c.subject === target.subject)
    ?? (target.chapter ? [{ chapter: target.chapter, label: roughLabel(target.chapter), n: 0, subject: target.subject ?? '' }] : []);
  const examOptions = meta?.exams.map((e) => [e.code, e.name] as [string, string]) ?? Object.entries(EXAM_NAMES);
  const subjectOptions = meta?.subjects.filter((s) => s.count).map((s) => [s.code, s.name] as [string, string])
    ?? Object.entries(SUBJECT_NAMES);
  const years = meta?.years.map(String) ?? (target.year ? [target.year] : []);
  const aiOnly = mode === 'ai' || (items.length > 0 && items.every((it) => it.origin === 'ai'));
  const showFilters = mode === 'filter' && !target.about;
  const single = mode === 'question';

  return (
    <div className="practice">
      {!showFilters ? (
        <div className="card notice row between gap">
          <span>
            {mode === 'smart' ? <>Smart practice: questions picked for you by the adaptive engine. Each one says why,
              and every answer updates your learner model. <Link to="/coach">How it decides →</Link></>
              : mode === 'news' ? <>Current-affairs quiz for <strong>{fmtDate(params.day)}</strong>: real SSC questions on
                topics in that day's news, then recent current-affairs PYQs.{' '}
                <Link to={currentAffairsUrl(params.day)}>Back to the news</Link></>
              : mode === 'paper' ? <>Practising one previous-year paper, untimed{q ? <>: <strong>{q.paper_title}</strong></> : null}</>
                : target.about ? <>Questions matching “<strong>{target.about}</strong>” (<Link to={searchUrl(target.about)}>back to search</Link>)</>
                  : mode === 'similar' ? <>Questions most similar in meaning to <Link to={questionUrl(Number(params.id))}>question #{params.id}</Link></>
                    : single ? <>Question #{params.id}{q?.chapter && <> · <Link to={practiceUrl({ subject: q.subject, chapter: q.chapter })}>more {q.chapter_label}</Link></>}</>
                      : aiOnly ? <>AI-generated questions: written by Gemini in the style of real PYQs and kept only
                        when an independent solve agreed with the answer. They are not from real papers.</>
                        : <>Selected questions</>}
          </span>
          <Link className="btn btn-sm" to={practiceUrl()}>All questions</Link>
        </div>
      ) : (
        <div className="card filters">
          <Select label="Exam" value={target.exam} onChange={(v) => setFilter({ exam: v || undefined })}
            options={[['', 'All exams'], ...examOptions]} />
          {exam && exam.stages.length > 1 && (
            <Select label="Stage" value={target.stage} onChange={(v) => setFilter({ stage: v || undefined })}
              options={[['', 'All stages'], ...exam.stages.map((s) => [s.code, s.name] as [string, string])]} />
          )}
          <Select label="Subject" value={target.subject} onChange={(v) => setFilter({ subject: v || undefined })}
            options={[['', 'All subjects'], ...subjectOptions]} />
          <Select label="Topic" value={target.chapter} onChange={(v) => setFilter({ chapter: v || undefined })} disabled={!target.subject}
            options={[['', target.subject ? 'All topics' : 'Pick a subject first'],
              ...chapters.map((c) => [c.chapter, c.n ? `${c.label} (${fmtNum(c.n)})` : c.label] as [string, string])]} />
          <Select label="Year" value={target.year} onChange={(v) => setFilter({ year: v || undefined })}
            options={[['', 'Any year'], ...years.map((y) => [y, y] as [string, string])]} />
          <Select label="Show" value={target.status} onChange={(v) => setFilter({ status: v || undefined })}
            options={[['', 'All questions'], ['unattempted', 'Not attempted yet'], ['incorrect', 'Got wrong last time'], ['bookmarked', 'Bookmarked']]} />
          <Select label="Order" value={target.order} onChange={(v) => setFilter({ order: v || undefined })}
            options={[['', 'Shuffled'], ['sequential', 'In order']]} />
          <form className="field field-grow" onSubmit={(e) => { e.preventDefault(); setFilter({ search: searchDraft.trim() || undefined }); }}>
            <span className="field-label">Search text</span>
            <input type="search" value={searchDraft} onChange={(e) => setSearchDraft(e.target.value)}
              onBlur={() => searchDraft.trim() !== (target.search ?? '') && setFilter({ search: searchDraft.trim() || undefined })}
              placeholder="e.g. Harappa, simple interest" />
          </form>
        </div>
      )}

      {!single && (
        <div className="row between wrap session-bar">
          <span className="muted">
            {loading ? 'Loading questions…' : mode === 'smart' ? 'Adapts after every answer'
              : `${fmtNum(total)} ${mode === 'news' ? '' : 'matching '}question${total === 1 ? '' : 's'}`}
          </span>
          {session.length > 0 && (
            <span className="session-tally">
              This session: <strong>{correct}</strong> / {session.length} correct · {fmtPct(correct / session.length)}
            </span>
          )}
        </div>
      )}

      {error && <p className="error-text">{error}</p>}

      {!loading && !q && !error && (
        <div className="card empty">
          <h2 className="h3">{single ? 'Question not found' : 'No questions match these filters'}</h2>
          <p className="muted">{single ? 'The link may be from an older question bank.' : 'Try a different topic, or switch “Show” back to all questions.'}</p>
        </div>
      )}

      {q && (
        <article className="card question-card">
          <header className="q-head">
            <div className="q-meta">
              <span className="q-num">{single ? `#${q.id}` : `Q ${idx + 1}${total && mode !== 'smart' ? ` of ${fmtNum(total)}` : ''}`}</span>
              <Link className="pill" to={practiceUrl({ subject: q.subject })}>{subjectName(q.subject)}</Link>
              {q.chapter && <Link className="pill pill-soft" to={practiceUrl({ subject: q.subject, chapter: q.chapter })}>{q.chapter_label}</Link>}
              {q.last_correct === false && !answered && <span className="pill pill-bad">Wrong last time</span>}
              {q.origin === 'ai' && <span className="pill pill-ai">AI-generated</span>}
            </div>
            <div className="row gap">
              {!single && <Link className="icon-btn" to={questionUrl(q.id)} title="Link to just this question">🔗 <span className="small">Link</span></Link>}
              <button type="button" className={`icon-btn${q.bookmarked ? ' is-on' : ''}`} onClick={() => void toggleBookmark()}
                aria-pressed={q.bookmarked} title={q.bookmarked ? 'Remove bookmark' : 'Bookmark for revision'}>
                {q.bookmarked ? '★' : '☆'} <span className="small">{q.bookmarked ? 'Saved' : 'Save'}</span>
              </button>
            </div>
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
              <Link className="btn btn-ghost" to={similarUrl(q.id)}>Similar questions →</Link>
            </div>
          )}
          {tutorFor === q.id && <AiTutor key={q.id} questionId={q.id} chosen={answered?.chosen ?? null} />}

          {!single && (
            <>
              <footer className="q-actions">
                <button type="button" className="btn" onClick={() => go(-1)} disabled={idx === 0}>← Previous</button>
                {!answered && <button type="button" className="btn btn-ghost" onClick={() => void choose(null)}>Show answer</button>}
                <button type="button" className="btn btn-primary" onClick={() => go(1)} disabled={idx >= items.length - 1}>
                  Next →
                </button>
              </footer>
              <p className="muted small kbd-hint">Keys: <kbd>1</kbd>–<kbd>4</kbd> answer · <kbd>←</kbd> <kbd>→</kbd> move</p>
            </>
          )}
          {single && !answered && (
            <footer className="q-actions">
              <span />
              <button type="button" className="btn btn-ghost" onClick={() => void choose(null)}>Show answer</button>
            </footer>
          )}
        </article>
      )}
    </div>
  );
}

function legacyTarget(query: URLSearchParams): string {
  const get = (k: string) => query.get(k) ?? undefined;
  if (get('mode') === 'smart') return '/practice/smart';
  if (get('paper')) return `/practice/paper/${get('paper')}`;
  if (get('similar')) return similarUrl(Number(get('similar')));
  if (get('origin') === 'ai' || get('ids')) return `/practice/ai${get('ids') ? `?ids=${get('ids')}` : ''}`;
  const status = get('status');
  return practiceUrl({
    subject: get('subject'), chapter: get('chapter'), exam: get('exam'), stage: get('stage'), year: get('year'),
    status, order: get('order'), search: get('search'), about: get('semantic'),
  });
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
