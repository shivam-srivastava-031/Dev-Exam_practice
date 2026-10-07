import { useEffect, useMemo, useState } from 'react';
import { Link, Navigate, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { api, useAsync, useMeta } from '../api';
import { SUBJECT_SHORT, fmtNum, fmtPct } from '../lib/format';
import {
  EXAM_NAMES, SUBJECT_NAMES, examCode, practiceUrl, setPageTitle, subjectCode, topicsUrl, type PracticeTarget,
} from '../lib/urls';
import type { SubjectTopics, TopicEntry } from '../types';

type Sort = 'asked' | 'az' | 'weak';
const SORTS: [Sort, string][] = [['asked', 'Most asked'], ['az', 'A–Z'], ['weak', 'Weakest first']];

/** Every subject's previous-year questions, topic by topic, with how often each is asked and your progress. */
export default function Topics() {
  const { meta } = useMeta();
  const params = useParams();
  const [query] = useSearchParams();
  const navigate = useNavigate();
  const exam = examCode(query.get('exam'));
  const stageParam = query.get('stage');
  const stage = exam && (stageParam === 'pre' || stageParam === 'mains') ? stageParam : undefined;
  const index = useAsync(() => api.topics(exam, stage), [exam, stage]);
  const [sort, setSort] = useState<Sort>('asked');
  const [find, setFind] = useState('');

  const wanted = subjectCode(params.subject);
  const subjects = index.data?.subjects ?? [];
  const subject = wanted ? subjects.find((s) => s.code === wanted) : subjects[0];
  const examMeta = meta?.exams.find((e) => e.code === exam);
  const stageMeta = examMeta?.stages.find((s) => s.code === stage);
  const scopeName = [examMeta?.name ?? (exam ? EXAM_NAMES[exam] : undefined), stageMeta?.name].filter(Boolean).join(' ');
  const needle = find.trim().toLowerCase();

  const visible = useMemo(() => {
    if (!subject) return [];
    const hit = (label: string) => label.toLowerCase().includes(needle);
    const list = subject.topics.filter((t) => !needle || hit(t.label) || t.concepts.some((c) => hit(c.label)));
    const accuracy = (t: TopicEntry) => (t.attempts ? t.correct / t.attempts : 2); // untried topics last
    if (sort === 'az') return [...list].sort((a, b) => a.label.localeCompare(b.label));
    if (sort === 'weak') return [...list].sort((a, b) => accuracy(a) - accuracy(b) || b.wrong - a.wrong || b.n - a.n);
    return list;
  }, [subject, needle, sort]);

  useEffect(() => {
    const what = subject ? `${SUBJECT_SHORT[subject.code] ?? subject.name} topic-wise PYQs` : 'Topic-wise PYQs';
    setPageTitle(scopeName ? `${what} · ${scopeName}` : what);
  }, [subject, scopeName]);

  if (params.subject && !wanted) return <Navigate replace to={topicsUrl(undefined, exam, stage)} />;

  const go = (code: string | undefined, e = exam, s = stage) => navigate(topicsUrl(code, e, s));
  const total = subjects.reduce((n, s) => n + s.n, 0);
  const papers = !meta ? null
    : stageMeta ? stageMeta.papers
      : examMeta ? examMeta.stages.reduce((n, s) => n + s.papers, 0)
        : exam ? null : meta.total_papers;
  const examOptions = meta?.exams.map((e) => [e.code, e.name] as const) ?? Object.entries(EXAM_NAMES);
  // Switching exam keeps the previous counts on screen until the new ones arrive.
  const stale = index.loading && index.data ? ' is-stale' : '';

  return (
    <div className="stack-lg">
      <section className="stack">
        <div className="row between wrap gap topics-head">
          <div className="stack-sm">
            <h1>Topic-wise PYQs</h1>
            <p className={`muted${stale}`}>
              {index.data ? <>{fmtNum(total)} previous-year questions{papers ? <> from {fmtNum(papers)} papers</> : null}</> : 'Previous-year questions'}
              {scopeName ? <> of {scopeName}</> : null}, by subject and topic. Pick a topic or sub-topic to practise
              it with full solutions.
            </p>
          </div>
          <div className="row gap wrap">
            <label className="field">
              <span className="field-label">Exam</span>
              <select value={exam ?? ''} onChange={(e) => go(wanted, e.target.value || undefined, undefined)}>
                <option value="">All exams</option>
                {examOptions.map(([code, name]) => <option key={code} value={code}>{name}</option>)}
              </select>
            </label>
            {examMeta && examMeta.stages.length > 1 && (
              <label className="field">
                <span className="field-label">Stage</span>
                <select value={stage ?? ''} onChange={(e) => go(wanted, exam, e.target.value || undefined)}>
                  <option value="">All stages</option>
                  {examMeta.stages.map((s) => <option key={s.code} value={s.code}>{s.name}</option>)}
                </select>
              </label>
            )}
          </div>
        </div>
        {subjects.length > 0 && (
          <div className={`tabs${stale}`} role="tablist" aria-label="Subject">
            {subjects.map((s) => (
              <button key={s.code} type="button" role="tab" aria-selected={s.code === subject?.code}
                className={`tab${s.code === subject?.code ? ' is-active' : ''}`} onClick={() => go(s.code)}>
                {SUBJECT_SHORT[s.code] ?? s.name}<span className="tab-count">{fmtNum(s.n)}</span>
              </button>
            ))}
          </div>
        )}
      </section>

      {index.error && (
        <div className="card empty">
          <p className="error-text">{index.error}</p>
          <button type="button" className="btn" onClick={index.reload}>Try again</button>
        </div>
      )}
      {!index.data && index.loading && <p className="muted">Loading topics…</p>}
      {index.data && wanted && !subject && (
        <div className="card empty">
          <h2 className="h3">No {SUBJECT_NAMES[wanted]} questions in {scopeName || 'the bank'}</h2>
          <p className="muted">Pick another subject above, or <Link to={topicsUrl(wanted)}>see every exam</Link>.</p>
        </div>
      )}

      {subject && (
        <div className={`stack-lg${stale}`}>
          <SubjectCard s={subject} base={{ subject: subject.code, exam, stage }} years={index.data?.years ?? []} />

          <section className="stack">
            <div className="row between wrap gap">
              <h2>{subject.topics.length} topics</h2>
              <div className="topic-tools">
                <input type="search" value={find} onChange={(e) => setFind(e.target.value)}
                  placeholder="Find a topic or sub-topic" aria-label="Find a topic or sub-topic" />
                <div className="segmented" role="group" aria-label="Sort topics">
                  {SORTS.map(([key, label]) => (
                    <button key={key} type="button" aria-pressed={sort === key} className={sort === key ? 'is-active' : ''}
                      onClick={() => setSort(key)}>{label}</button>
                  ))}
                </div>
              </div>
            </div>
            {visible.length > 0 ? (
              <ul className="topic-list">
                {visible.map((t) => (
                  <TopicRow key={t.chapter} t={t} s={subject} base={{ subject: subject.code, exam, stage }}
                    years={index.data?.years ?? []} needle={needle} />
                ))}
              </ul>
            ) : (
              <p className="muted">No topic or sub-topic matches “{find.trim()}”.</p>
            )}
          </section>
        </div>
      )}
    </div>
  );
}

function SubjectCard({ s, base, years }: { s: SubjectTopics; base: PracticeTarget; years: number[] }) {
  const short = SUBJECT_SHORT[s.code] ?? s.name;
  const byYear = years
    .map((y, i) => [y, s.topics.reduce((n, t) => n + t.years[i], 0)] as const)
    .filter(([, n]) => n > 0)
    .reverse();
  return (
    <section className="card subject-card">
      <div className="row between wrap gap">
        <div className="stack-sm">
          <h2>{s.name}</h2>
          <p className="muted">{fmtNum(s.n)} questions from {fmtNum(s.papers)} papers · {s.topics.length} topics</p>
        </div>
        <div className="summary-nums">
          <span><strong>{fmtNum(s.done)}</strong> of {fmtNum(s.n)} practised</span>
          {s.attempts > 0 && <span><strong>{fmtPct(s.correct / s.attempts)}</strong> accuracy</span>}
        </div>
      </div>
      <div className="row gap wrap">
        <Link className="btn btn-primary" to={practiceUrl(base)}>Practise all {short}</Link>
        {s.done > 0 && s.done < s.n && (
          <Link className="btn" to={practiceUrl({ ...base, status: 'unattempted' })}>Only new questions</Link>
        )}
        {s.wrong > 0 && <Link className="btn" to={practiceUrl({ ...base, status: 'incorrect' })}>Retry {fmtNum(s.wrong)} mistakes</Link>}
      </div>
      {byYear.length > 1 && (
        <div className="stack-sm">
          <span className="field-label">Year-wise</span>
          <div className="chips">
            {byYear.map(([y, n]) => (
              <Link key={y} className="chip" to={practiceUrl({ ...base, year: String(y) })}>{y} · {fmtNum(n)}</Link>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}

function TopicRow({ t, s, base, years, needle }: {
  t: TopicEntry;
  s: SubjectTopics;
  base: PracticeTarget;
  years: number[];
  needle: string;
}) {
  const at: PracticeTarget = { ...base, chapter: t.chapter };
  const top = s.topics[0]?.n || 1; // bars are scaled to the subject's most-asked topic, whatever the filter
  const hit = (label: string) => !!needle && label.toLowerCase().includes(needle);
  const yearly = years.map((y, i) => [y, t.years[i]] as const).filter(([, n]) => n > 0).reverse();
  return (
    <li className="card topic-row">
      <div className="topic-main">
        <div className="topic-head">
          <Link className="topic-title" to={practiceUrl(at)}>{t.label}</Link>
          {t.wrong > 0 && (
            <Link className="pill pill-bad" to={practiceUrl({ ...at, status: 'incorrect' })}>{fmtNum(t.wrong)} to retry</Link>
          )}
          {t.done > 0 && t.done >= t.n && <span className="pill pill-good">All done</span>}
        </div>
        <div className="meter-row small">
          <span className="meter" aria-hidden title={`${fmtPct(t.n / s.n, 1)} of ${s.name} PYQs`}>
            <span className="meter-fill" style={{ width: `${(t.n / top) * 100}%` }} />
          </span>
          <span><strong>{fmtNum(t.n)}</strong> PYQs</span>
          <span className="muted" title={`Average number of questions from this topic in a paper with a ${s.name} section`}>
            {perPaper(t.per_paper)}
          </span>
        </div>
        <span className="small muted">
          {t.done ? <>You: {fmtNum(t.done)} done · {fmtPct(t.correct / t.attempts)} accuracy</> : 'Not started'}
        </span>
      </div>
      <div className="topic-actions">
        {t.done > 0 && t.done < t.n && (
          <Link className="btn btn-sm btn-ghost" to={practiceUrl({ ...at, status: 'unattempted' })}>New only</Link>
        )}
        <Link className="btn btn-sm btn-primary" to={practiceUrl(at)}>Practise</Link>
      </div>
      {(t.concepts.length > 0 || yearly.length > 1) && (
        // Opens by itself when the search matched only a sub-topic.
        <details className="topic-more" open={(!hit(t.label) && t.concepts.some((c) => hit(c.label))) || undefined}>
          <summary>
            {[t.concepts.length ? `${t.concepts.length} sub-topic${t.concepts.length > 1 ? 's' : ''}` : '',
              yearly.length > 1 ? 'year-wise' : ''].filter(Boolean).join(' · ')}
          </summary>
          <div className="topic-more-body">
            {t.concepts.length > 0 && <span className="field-label">Sub-topics</span>}
            {t.concepts.length > 0 && (
              <ul className="subtopic-list">
                {t.concepts.map((c) => (
                  <li key={c.concept}>
                    <Link className={`subtopic${hit(c.label) ? ' is-match' : ''}`} to={practiceUrl({ ...at, concept: c.concept })}>
                      <span>{c.label}</span>
                      <span className="subtopic-n">{c.done ? `${fmtNum(c.done)} / ` : ''}{fmtNum(c.n)}</span>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
            {yearly.length > 1 && <span className="field-label">Year-wise</span>}
            {yearly.length > 1 && (
              <div className="chips">
                {yearly.map(([y, n]) => (
                  <Link key={y} className="chip" to={practiceUrl({ ...at, year: String(y) })}>{y} · {fmtNum(n)}</Link>
                ))}
              </div>
            )}
          </div>
        </details>
      )}
    </li>
  );
}

/** 2.7 -> "~2.7 per paper"; 0.2 -> "~1 in 5 papers". */
function perPaper(x: number): string {
  if (x >= 0.95) return `~${x < 9.95 ? x.toFixed(1) : Math.round(x)} per paper`;
  return x > 0 ? `~1 in ${fmtNum(Math.round(1 / x))} papers` : '';
}
