import { useMemo, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { api, useAsync, useMeta } from '../api';
import { CalibrationPlot } from '../components/charts';
import { fmtDateTime, fmtNum, fmtPct, fmtScore } from '../lib/format';
import { aiPracticeUrl, mockUrl, practiceUrl, setPageTitle, smartUrl } from '../lib/urls';
import type { LearnerOverview, MasteryRow } from '../types';
import { Tile } from './Dashboard';

const STATUS_LABEL: Record<MasteryRow['status'], string> = { new: 'Not started', weak: 'Weak', ok: 'Getting there', strong: 'Strong' };

export default function Coach() {
  const { meta } = useMeta();
  const overview = useAsync(() => api.learner(), []);
  const [data, setData] = useState<LearnerOverview | null>(null);
  const ov = data ?? overview.data;
  setPageTitle('Your coach');

  if (overview.error) return <p className="error-text">{overview.error}</p>;
  if (!ov || !meta) return <p className="muted">Loading your learner model…</p>;

  const targets = meta.exams.flatMap((e) => e.stages.map((s) => ({ value: `${e.code}|${s.code}`, label: `${e.name} · ${s.name}` })));
  async function changeTarget(value: string) {
    const [exam, stage] = value.split('|');
    setData(await api.setTarget(exam, stage));
  }

  return (
    <div className="stack-lg">
      <section className="row between wrap gap">
        <div className="stack-sm">
          <h1>Your coach</h1>
          <p className="muted">A model of what you know, learned from every answer you give. It decides what you practise next.</p>
        </div>
        <label className="field">
          <span className="field-label">Preparing for</span>
          <select value={`${ov.target.exam}|${ov.target.stage}`} onChange={(e) => void changeTarget(e.target.value)}>
            {targets.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
          </select>
        </label>
      </section>

      {ov.attempts < 30 && (
        <div className="card notice">
          The model has seen {ov.attempts} answer{ov.attempts === 1 ? '' : 's'} so far. Its estimates firm up after about 30–50;
          until then it leans on the exam's topic weightage.
        </div>
      )}

      <div className="grid-2">
        <ScoreCard ov={ov} />
        <NextSteps ov={ov} aiEnabled={meta.ai_enabled} />
      </div>

      <MasteryMap rows={ov.mastery} examName={`${ov.target.exam_name} ${ov.target.stage_name}`} />

      <ModelCard ov={ov} onRetrained={async () => setData(await api.learner())} />
    </div>
  );
}

function ScoreCard({ ov }: { ov: LearnerOverview }) {
  const ps = ov.predicted_score;
  return (
    <section className="card stack">
      <h2 className="h3">Predicted score today</h2>
      <div className="hero-figure">
        <span className="hero-value">{fmtScore(ps.expected)}</span>
        <span className="hero-of">/ {fmtScore(ps.max)}</span>
      </div>
      <p className="muted small">
        Expected marks on {ps.name} if you attempted every question now, from your predicted accuracy per topic and the
        exam's negative marking.
      </p>
      <div className="table-scroll">
        <table className="table compact">
          <thead><tr><th>Section</th><th className="num">Accuracy</th><th className="num">Expected</th><th className="num">Blind guess</th></tr></thead>
          <tbody>
            {ps.sections.map((s) => (
              <tr key={s.name + s.subject}>
                <td>{s.name}</td>
                <td className="num">{fmtPct(s.p_correct)}</td>
                <td className="num">{fmtScore(s.expected)} / {fmtScore(s.max)}</td>
                <td className="num" title="Average marks a random guess earns under this marking">
                  {s.guess_value > 0 ? `+${s.guess_value}` : s.guess_value === 0 ? '0' : fmtScore(s.guess_value)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="muted small">
        "Blind guess" is the average marks a random pick earns. Where it is positive, eliminating even one option and
        guessing beats leaving the question blank.
      </p>
    </section>
  );
}

function NextSteps({ ov, aiEnabled }: { ov: LearnerOverview; aiEnabled: boolean }) {
  const navigate = useNavigate();
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const focus = ov.mastery.find((m) => m.status === 'weak') ?? ov.mastery[0];

  async function personalisedMock() {
    setBusy('mock');
    try {
      const { id } = await api.createMock({ pattern_id: ov.target.pattern_id, adaptive: true });
      navigate(mockUrl(id));
    } catch (e) {
      setMessage((e as Error).message);
      setBusy(null);
    }
  }

  async function generate() {
    if (!focus) return;
    setBusy('gen');
    setMessage(null);
    try {
      const rep = await api.generate({ exam: ov.target.exam, stage: ov.target.stage, subject: focus.subject, chapter: focus.chapter, count: 5 });
      if (rep.ids.length) navigate(aiPracticeUrl(rep.ids));
      else setMessage(`None of the ${rep.generated} drafts passed verification; try again.`);
    } catch (e) {
      setMessage((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="card stack">
      <h2 className="h3">What to do next</h2>
      <Link className="step" to={smartUrl()}>
        <strong>Smart practice</strong>
        <span className="muted small">
          {ov.reviews_due ? `${ov.reviews_due} review${ov.reviews_due > 1 ? 's' : ''} due, then ` : ''}
          questions chosen from your weak and high-weightage topics. Each says why it was picked.
        </span>
      </Link>
      <button type="button" className="step" onClick={() => void personalisedMock()} disabled={busy !== null}>
        <strong>{busy === 'mock' ? 'Preparing…' : 'Personalised mock'}</strong>
        <span className="muted small">The real {ov.target.exam_name} {ov.target.stage_name} pattern and timing, tilted towards the topics you need.</span>
      </button>
      {aiEnabled && focus && (
        <button type="button" className="step" onClick={() => void generate()} disabled={busy !== null}>
          <strong>{busy === 'gen' ? 'Writing and verifying questions… (about 30 s)' : `5 fresh AI questions on ${focus.label}`}</strong>
          <span className="muted small">Gemini writes new questions in the style of real PYQs; only those an independent solve agrees with are kept.</span>
        </button>
      )}
      {message && <p className="error-text small">{message}</p>}
    </section>
  );
}

function MasteryMap({ rows, examName }: { rows: MasteryRow[]; examName: string }) {
  const subjects = useMemo(() => [...new Set(rows.map((r) => r.subject_name))], [rows]);
  const [subject, setSubject] = useState('');
  const [showAll, setShowAll] = useState(false);
  const filtered = rows.filter((r) => !subject || r.subject_name === subject);
  const shown = showAll ? filtered : filtered.slice(0, 12);
  return (
    <section className="card table-card">
      <div className="card-pad row between wrap gap">
        <div>
          <h2 className="h3">Topic mastery</h2>
          <p className="muted small">Ordered by priority: how often {examName} asks the topic × how unsure the model is about you.</p>
        </div>
        <div className="chips">
          <button type="button" className={`chip${!subject ? ' is-active' : ''}`} onClick={() => setSubject('')}>All</button>
          {subjects.map((s) => (
            <button key={s} type="button" className={`chip${subject === s ? ' is-active' : ''}`} onClick={() => setSubject(s)}>{s}</button>
          ))}
        </div>
      </div>
      <div className="table-scroll">
        <table className="table">
          <thead>
            <tr><th>Topic</th><th>Mastery (predicted accuracy)</th><th className="num">Weightage</th><th className="num">Answered</th><th /></tr>
          </thead>
          <tbody>
            {shown.map((r) => (
              <tr key={r.subject + r.chapter}>
                <td>
                  <span className="topic-name">{r.label}</span>
                  <span className="muted small"> · {r.subject_name}</span>
                </td>
                <td>
                  <span className="meter-row">
                    <span className="meter" aria-hidden><span className="meter-fill" style={{ width: `${r.predicted * 100}%` }} /></span>
                    <span className="meter-value">{fmtPct(r.predicted)}</span>
                    <span className={`status status-${r.status}`}>{STATUS_LABEL[r.status]}{!r.confident && r.status !== 'new' ? ' · few answers' : ''}</span>
                  </span>
                </td>
                <td className="num">{fmtPct(r.share, 1)}</td>
                <td className="num">{r.attempts ? `${r.correct}/${r.attempts}` : '–'}</td>
                <td className="num"><Link to={practiceUrl({ subject: r.subject, chapter: r.chapter, status: 'unattempted' })}>Practise →</Link></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {filtered.length > 12 && (
        <div className="card-pad">
          <button type="button" className="btn btn-sm btn-ghost" onClick={() => setShowAll(!showAll)}>
            {showAll ? 'Show fewer' : `Show all ${filtered.length} topics`}
          </button>
        </div>
      )}
    </section>
  );
}

function ModelCard({ ov, onRetrained }: { ov: LearnerOverview; onRetrained: () => Promise<void> }) {
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const cal = ov.calibration;
  const tr = ov.training;

  async function retrain() {
    setBusy(true);
    setNote(null);
    try {
      const rep = await api.retrain();
      if (rep.status === 'waiting') setNote(`Needs at least ${rep.needed} answers to tune itself (has ${rep.attempts}).`);
      await onRetrained();
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card stack">
      <div className="row between wrap gap">
        <div>
          <h2 className="h3">How well the model knows you</h2>
          <p className="muted small">
            Every answer is first predicted, then learned from. Comparing those forecasts with what happened shows whether
            the model is getting better at reading you.
          </p>
        </div>
        <button type="button" className="btn" onClick={() => void retrain()} disabled={busy}>
          {busy ? 'Tuning…' : 'Retrain now'}
        </button>
      </div>
      {note && <p className="muted small">{note}</p>}
      <div className="tiles tiles-compact">
        <Tile label="Answers learned from" value={fmtNum(ov.attempts)} />
        <Tile label="Forecast vs actual" value={cal.n ? `${fmtPct(cal.mean_predicted)} / ${fmtPct(cal.mean_actual)}` : '–'}
          sub={cal.window ? `last ${cal.window} answers` : undefined} />
        <Tile label="Brier score" value={cal.brier !== undefined ? String(cal.brier) : '–'} sub="lower is better; 0.25 = coin-flip" />
        <Tile label="Reviews scheduled" value={fmtNum(ov.reviews_total)} sub={`${ov.reviews_due} due now`} />
      </div>
      {cal.buckets.length > 1 ? <CalibrationPlot buckets={cal.buckets} />
        : <p className="muted">The calibration plot appears once you have answered questions across a range of difficulty.</p>}
      {tr?.status === 'trained' && (
        <p className="small">
          Last self-tuning {fmtDateTime(tr.trained_at)}: replayed {fmtNum(tr.attempts)} answers under {tr.candidates} learning-rate
          settings and kept the best. Its log-loss of <strong>{tr.logloss}</strong> is{' '}
          <strong>{fmtPct(tr.skill_vs_baseline ?? 0)}</strong> better than always predicting your average
          ({tr.baseline_logloss}). It retunes itself every 100 answers.
        </p>
      )}
      <p className="muted small">Details of every model: <Link to="/ai-lab">AI Lab →</Link></p>
    </section>
  );
}
