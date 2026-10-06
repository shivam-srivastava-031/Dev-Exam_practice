import { useMemo, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { api, useAsync, useMeta } from '../api';
import { fmtNum, fmtScore, paperLabel } from '../lib/format';
import type { Paper, Pattern } from '../types';

export default function Mocks() {
  const { meta, error: metaError } = useMeta();
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const exam = meta?.exams.find((e) => e.code === params.get('exam')) ?? meta?.exams[0];
  const stage = exam?.stages.find((s) => s.code === params.get('stage')) ?? exam?.stages[0];
  const patterns = useAsync(() => api.patterns(), []);
  const papers = useAsync(
    () => (exam && stage ? api.papers(exam.code, stage.code) : Promise.resolve([] as Paper[])),
    [exam?.code, stage?.code],
  );
  const [freshOnly, setFreshOnly] = useState(true);
  const [starting, setStarting] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const pattern = patterns.data?.find((p) => p.exam === exam?.code && p.stage === stage?.code && !p.legacy);
  const byYear = useMemo(() => {
    const groups = new Map<string, Paper[]>();
    for (const p of papers.data ?? []) {
      const key = p.year ? String(p.year) : 'Other';
      groups.set(key, [...(groups.get(key) ?? []), p]);
    }
    return [...groups.entries()].sort((a, b) => b[0].localeCompare(a[0]));
  }, [papers.data]);

  async function start(key: string, body: { pattern_id?: string; paper_id?: string; fresh_only?: boolean; adaptive?: boolean }) {
    setStarting(key);
    setError(null);
    try {
      const { id } = await api.createMock(body);
      navigate(`/exam/${id}`);
    } catch (e) {
      setError((e as Error).message);
      setStarting(null);
    }
  }

  if (metaError) return <p className="error-text">{metaError}</p>;
  if (!meta || !exam || !stage) return <p className="muted">Loading…</p>;

  const pick = (examCode: string, stageCode?: string) => {
    const next = new URLSearchParams({ exam: examCode });
    if (stageCode) next.set('stage', stageCode);
    setParams(next, { replace: true });
  };

  return (
    <div className="stack-lg">
      <div className="stack">
        <h1>Mock tests</h1>
        <div className="tabs" role="tablist" aria-label="Exam">
          {meta.exams.map((e) => (
            <button key={e.code} role="tab" aria-selected={e.code === exam.code}
              className={`tab${e.code === exam.code ? ' is-active' : ''}`} onClick={() => pick(e.code)}>
              {e.name.replace('SSC ', '')}
            </button>
          ))}
        </div>
        {exam.stages.length > 1 && (
          <div className="segmented" role="tablist" aria-label="Stage">
            {exam.stages.map((s) => (
              <button key={s.code} role="tab" aria-selected={s.code === stage.code}
                className={s.code === stage.code ? 'is-active' : ''} onClick={() => pick(exam.code, s.code)}>
                {s.code === 'pre' ? 'Pre' : 'Mains'} · {s.name}
              </button>
            ))}
          </div>
        )}
      </div>

      {error && <p className="error-text">{error}</p>}

      {pattern && <PatternCard pattern={pattern} freshOnly={freshOnly} setFreshOnly={setFreshOnly}
        busy={starting === 'random'} onStart={() => void start('random', { pattern_id: pattern.id, fresh_only: freshOnly })}
        busyAdaptive={starting === 'adaptive'}
        onAdaptive={() => void start('adaptive', { pattern_id: pattern.id, adaptive: true })} />}

      <section className="stack">
        <div className="row between wrap">
          <h2>Previous-year papers</h2>
          <span className="muted">{fmtNum(papers.data?.length ?? 0)} papers · actual shifts, original question order</span>
        </div>
        {papers.loading && !papers.data && <p className="muted">Loading papers…</p>}
        {papers.error && <p className="error-text">{papers.error}</p>}
        {byYear.map(([year, list], gi) => (
          <details key={year} className="card year-group" open={gi === 0}>
            <summary><span className="h3">{year}</span><span className="muted"> · {list.length} paper{list.length > 1 ? 's' : ''}</span></summary>
            <ul className="paper-list">
              {list.map((p) => (
                <li key={p.id} className="paper-row">
                  <div className="paper-info">
                    <span className="paper-date">{paperLabel(p)}</span>
                    <span className="muted small">{p.question_count} questions · {fmtScore(p.max_marks)} marks · {p.duration_min ?? '–'} min</span>
                  </div>
                  <div className="paper-status">
                    {p.last_mock?.submitted_at ? (
                      <Link to={`/result/${p.last_mock.id}`} className="pill pill-good">
                        Scored {fmtScore(p.last_mock.score ?? 0)}/{fmtScore(p.last_mock.max_score ?? 0)}
                      </Link>
                    ) : p.last_mock ? (
                      <Link to={`/exam/${p.last_mock.id}`} className="pill pill-warn">In progress · resume</Link>
                    ) : null}
                  </div>
                  <div className="paper-actions">
                    <Link className="btn btn-sm btn-ghost" to={`/practice?paper=${p.id}`}>Practise</Link>
                    <button type="button" className="btn btn-sm btn-primary" disabled={starting !== null}
                      onClick={() => void start(p.id, { paper_id: p.id })}>
                      {starting === p.id ? 'Starting…' : p.last_mock?.submitted_at ? 'Retake' : 'Attempt'}
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          </details>
        ))}
      </section>
    </div>
  );
}

function PatternCard({ pattern, freshOnly, setFreshOnly, busy, onStart, busyAdaptive, onAdaptive }: {
  pattern: Pattern;
  freshOnly: boolean;
  setFreshOnly: (v: boolean) => void;
  busy: boolean;
  onStart: () => void;
  busyAdaptive: boolean;
  onAdaptive: () => void;
}) {
  const sectional = pattern.parts.length > 1;
  const short = pattern.parts.flatMap((p) => p.sections).filter((s) => (pattern.pool[s.subject] ?? 0) < s.count);
  return (
    <section className="card pattern-card">
      <div className="row between wrap gap">
        <div className="stack-sm">
          <h2>Full-length mock: {pattern.name}</h2>
          <p className="muted">
            A fresh paper drawn from {pattern.exam_name} previous-year questions, laid out exactly like the real CBT.
          </p>
        </div>
        <div className="summary-nums">
          <span><strong>{pattern.questions}</strong> questions</span>
          <span><strong>{fmtScore(pattern.max_marks)}</strong> marks</span>
          <span><strong>{pattern.minutes}</strong> min</span>
        </div>
      </div>
      <div className="table-scroll">
        <table className="table compact">
          <thead>
            <tr><th>{sectional ? 'Timed section' : 'Part'}</th><th>Subject</th><th className="num">Questions</th><th className="num">Marking</th><th className="num">Time</th></tr>
          </thead>
          <tbody>
            {pattern.parts.flatMap((part) => part.sections.map((s, i) => (
              <tr key={part.name + s.subject}>
                <td>{i === 0 ? part.name : ''}</td>
                <td>{s.name}</td>
                <td className="num">{s.count}</td>
                <td className="num">+{fmtScore(s.correct)} / {s.wrong ? `−${fmtScore(s.wrong)}` : 'no negative'}</td>
                <td className="num">{i === 0 ? `${part.minutes} min` : ''}</td>
              </tr>
            )))}
          </tbody>
        </table>
      </div>
      {sectional && <p className="muted small">Each timed section runs on its own clock; once it ends you move on and cannot return, as in the real exam.</p>}
      {short.length > 0 && <p className="muted small">Some sections have fewer questions in the bank than the pattern needs; those sections will be shorter.</p>}
      <div className="row between wrap gap">
        <label className="check">
          <input type="checkbox" checked={freshOnly} onChange={(e) => setFreshOnly(e.target.checked)} />
          Prefer questions I haven't attempted
        </label>
        <div className="row gap wrap">
          <button type="button" className="btn btn-lg" onClick={onAdaptive} disabled={busyAdaptive || busy}
            title="Same pattern and timing, with questions tilted towards your weak topics">
            {busyAdaptive ? 'Personalising…' : 'Personalised mock'}
          </button>
          <button type="button" className="btn btn-primary btn-lg" onClick={onStart} disabled={busy || busyAdaptive}>
            {busy ? 'Preparing paper…' : 'Start full mock'}
          </button>
        </div>
      </div>
    </section>
  );
}
