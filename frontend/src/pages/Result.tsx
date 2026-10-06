import { useMemo, useRef, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { api, streamText, useAsync, useMeta } from '../api';
import { AiTutor } from '../components/AiTutor';
import { Markup } from '../components/Markup';
import { OptionList } from '../components/OptionList';
import { fmtDateTime, fmtDuration, fmtPct, fmtScore } from '../lib/format';
import { mockUrl, mocksUrl, setPageTitle } from '../lib/urls';
import type { MockResult, QuestionStatus } from '../types';
import { Tile } from './Dashboard';

type Filter = 'all' | QuestionStatus | 'marked';

export default function Result() {
  const { id = '' } = useParams();
  const { meta } = useMeta();
  const res = useAsync(() => api.result(id), [id]);

  if (res.error) return <p className="error-text">{res.error}</p>;
  if (!res.data) return <p className="muted">Loading result…</p>;
  setPageTitle(`Result: ${res.data.title}`);
  return <ResultView r={res.data} aiEnabled={!!meta?.ai_enabled} />;
}

function ResultView({ r, aiEnabled }: { r: MockResult; aiEnabled: boolean }) {
  const navigate = useNavigate();
  const s = r.result;
  const [filter, setFilter] = useState<Filter>('all');
  const [subject, setSubject] = useState('');
  const [tutorFor, setTutorFor] = useState<number | null>(null);
  const [starting, setStarting] = useState(false);

  const items = useMemo(() => {
    let n = 0;
    return r.parts.flatMap((p) => p.sections.flatMap((sec) => sec.question_ids.map((qid) => ({
      num: ++n, qid, section: sec, q: r.questions[qid], qr: s.questions[qid],
    }))));
  }, [r, s]);

  const counts = {
    all: items.length,
    correct: s.correct,
    wrong: s.wrong,
    skipped: s.skipped,
    marked: items.filter((it) => it.qr.marked).length,
  };
  const shown = items.filter((it) => (!subject || it.section.subject === subject)
    && (filter === 'all' || (filter === 'marked' ? it.qr.marked : it.qr.status === filter)));
  const lostToNegative = items.reduce((sum, it) => sum + (it.qr.status === 'wrong' ? -it.qr.marks : 0), 0);

  async function again() {
    setStarting(true);
    try {
      const { id } = await api.createMock(r.paper_id ? { paper_id: r.paper_id } : { pattern_id: r.pattern.id, fresh_only: true });
      navigate(mockUrl(id));
    } catch {
      setStarting(false);
    }
  }

  return (
    <div className="stack-lg">
      <section className="stack-sm">
        <Link to={mocksUrl(r.pattern.exam, r.pattern.stage)} className="muted small">← {r.pattern.name} mock tests</Link>
        <h1>{r.title}</h1>
        <p className="muted">{r.pattern.name} · submitted {fmtDateTime(r.submitted_at)}</p>
      </section>

      <section className="card score-card">
        <div className="hero-figure">
          <span className="hero-value">{fmtScore(s.score)}</span>
          <span className="hero-of">/ {fmtScore(s.max_score)} marks</span>
          <span className="muted">{fmtPct(s.max_score ? s.score / s.max_score : 0, 1)} of maximum</span>
        </div>
        <div className="tiles tiles-compact">
          <Tile label="Correct" value={String(s.correct)} sub={`of ${s.total}`} />
          <Tile label="Wrong" value={String(s.wrong)} sub={lostToNegative ? `−${fmtScore(lostToNegative)} marks lost` : 'no marks lost'} />
          <Tile label="Not attempted" value={String(s.skipped)} />
          <Tile label="Accuracy" value={fmtPct(s.accuracy)} sub="of attempted" />
          <Tile label="Time taken" value={fmtDuration(r.elapsed_sec ?? 0)} sub={`of ${r.minutes} min`} />
        </div>
        <div className="row gap wrap">
          <button type="button" className="btn btn-primary" disabled={starting} onClick={() => void again()}>
            {r.paper_id ? 'Retake this paper' : 'Take another mock like this'}
          </button>
          <Link className="btn" to="/progress">See overall progress</Link>
        </div>
      </section>

      <section className="card table-card">
        <table className="table">
          <thead>
            <tr>
              <th>Section</th><th className="num">Attempted</th><th className="num">Correct</th><th className="num">Wrong</th>
              <th className="num">Score</th><th className="num">Accuracy</th><th className="num">Time</th>
            </tr>
          </thead>
          <tbody>
            {s.sections.map((sec) => (
              <tr key={sec.part + sec.subject}>
                <td>{sec.name}{r.parts.length > 1 && <span className="muted small"> · {sec.part}</span>}</td>
                <td className="num">{sec.attempted}/{sec.total}</td>
                <td className="num">{sec.correct}</td>
                <td className="num">{sec.wrong}</td>
                <td className="num"><strong>{fmtScore(sec.score)}</strong> / {fmtScore(sec.max_score)}</td>
                <td className="num">{fmtPct(sec.accuracy)}</td>
                <td className="num">{fmtDuration(sec.ms / 1000)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      {(aiEnabled || r.ai_review) && <AiCoach mockId={r.id} initial={r.ai_review} enabled={aiEnabled} />}

      <section className="stack">
        <div className="row between wrap gap">
          <h2>Review answers</h2>
          <select value={subject} onChange={(e) => setSubject(e.target.value)} aria-label="Section">
            <option value="">All sections</option>
            {r.parts.flatMap((p) => p.sections).map((sec) => <option key={sec.subject} value={sec.subject}>{sec.name}</option>)}
          </select>
        </div>
        <div className="chips" role="tablist">
          {(['all', 'wrong', 'skipped', 'correct', 'marked'] as Filter[]).map((f) => (
            <button key={f} type="button" role="tab" aria-selected={filter === f}
              className={`chip${filter === f ? ' is-active' : ''}`} onClick={() => setFilter(f)}>
              {{ all: 'All', wrong: 'Wrong', skipped: 'Not attempted', correct: 'Correct', marked: 'Marked for review' }[f]} ({counts[f]})
            </button>
          ))}
        </div>

        {shown.length === 0 && <p className="muted">Nothing here.</p>}
        {shown.map(({ num, qid, section, q, qr }) => (
          <article key={qid} className={`card review-item review-${qr.status}`}>
            <header className="q-head">
              <div className="q-meta">
                <span className="q-num">Q{num}</span>
                <span className="pill">{section.name}</span>
                {q.chapter && <span className="pill pill-soft">{q.chapter_label}</span>}
              </div>
              <div className="q-meta">
                <span className={`pill ${qr.status === 'correct' ? 'pill-good' : qr.status === 'wrong' ? 'pill-bad' : ''}`}>
                  {qr.status === 'correct' ? `✓ +${fmtScore(qr.marks)}` : qr.status === 'wrong' ? `✗ ${fmtScore(qr.marks)}` : 'Not attempted'}
                </span>
                <span className="muted small" title="Time spent on this question">⏱ {fmtDuration(qr.ms / 1000)}</span>
              </div>
            </header>
            <Markup text={q.question} className="q-text" />
            <OptionList name={`rev-${qid}`} options={q.options} selected={qr.chosen} answer={qr.answer} />
            {q.solution && (
              <details className="solution">
                <summary>Solution</summary>
                <Markup text={q.solution} />
              </details>
            )}
            {aiEnabled && tutorFor !== qid && (
              <button type="button" className="btn btn-ai btn-sm" onClick={() => setTutorFor(qid)}>✦ Ask the AI tutor</button>
            )}
            {tutorFor === qid && <AiTutor questionId={qid} chosen={qr.chosen} />}
          </article>
        ))}
      </section>
    </div>
  );
}

function AiCoach({ mockId, initial, enabled }: { mockId: string; initial: string | null; enabled: boolean }) {
  const [text, setText] = useState(initial ?? '');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abort = useRef<AbortController | null>(null);

  async function run(refresh: boolean) {
    abort.current?.abort();
    const controller = new AbortController();
    abort.current = controller;
    setBusy(true);
    setError(null);
    setText('');
    try {
      await streamText(`/api/ai/mock-review/${mockId}${refresh ? '?refresh=true' : ''}`, {}, setText, controller.signal);
    } catch (e) {
      if (!controller.signal.aborted) setError((e as Error).message);
    } finally {
      if (abort.current === controller) setBusy(false);
    }
  }

  return (
    <section className="card coach">
      <div className="row between wrap gap">
        <div>
          <h2 className="h3"><span className="tutor-badge">AI coach</span> Performance analysis</h2>
          <p className="muted small">Where you lost marks, what to fix first, and a 7-day plan — based on this attempt.</p>
        </div>
        {enabled && (
          <button type="button" className="btn btn-ai" disabled={busy} onClick={() => void run(!!text)}>
            {busy ? 'Analysing…' : text ? 'Regenerate' : '✦ Analyse my attempt'}
          </button>
        )}
      </div>
      {error && <p className="error-text">{error}</p>}
      {(text || busy) && <div className="coach-text">{text ? <Markup text={text} ai /> : <span className="typing">Thinking…</span>}</div>}
    </section>
  );
}
