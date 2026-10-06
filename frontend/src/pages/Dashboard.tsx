import { Link } from 'react-router-dom';
import { api, useAsync, useMeta } from '../api';
import { fmtDateTime, fmtNum, fmtPct, fmtScore } from '../lib/format';

const SUBJECT_BLURB: Record<string, string> = {
  REAS: 'Series, analogy, coding-decoding, puzzles, non-verbal',
  GK: 'Polity, history, geography, economy, science, current affairs',
  MATH: 'Arithmetic, algebra, geometry, mensuration, DI',
  ENG: 'Vocabulary, grammar, cloze test, comprehension',
  COMPUTER: 'Fundamentals, MS Office, internet (CGL/CHSL Tier-II)',
};

export default function Dashboard() {
  const { meta, error } = useMeta();
  const stats = useAsync(() => api.stats(), []);
  const mocks = useAsync(() => api.mocks(), []);

  if (error) return <p className="error-text">Could not reach the server: {error}</p>;
  if (!meta) return <p className="muted">Loading…</p>;

  const t = stats.data?.totals;
  const inProgress = (mocks.data ?? []).filter((m) => !m.submitted_at);
  const recent = (mocks.data ?? []).filter((m) => m.submitted_at).slice(0, 5);

  return (
    <div className="stack-lg">
      <section className="hero">
        <h1>Previous-year SSC questions, the way the exam asks them</h1>
        <p className="lead">
          {fmtNum(meta.total_questions)} questions from {fmtNum(meta.total_papers)} real papers (2019–2026) across{' '}
          {meta.exams.length} SSC exams. Practise subject-wise with instant solutions, or sit a timed mock on the real CBT
          interface.
        </p>
        <div className="row gap wrap">
          <Link className="btn btn-primary btn-lg" to="/practice?mode=smart">Smart practice</Link>
          <Link className="btn btn-lg" to="/mocks">Take a mock test</Link>
          <Link className="btn btn-lg" to="/search">Search by meaning</Link>
        </div>
        <p className="muted small">
          Smart practice picks each question with a learner model that updates on every answer. See <Link to="/coach">your coach</Link>.
        </p>
      </section>

      {inProgress.length > 0 && (
        <section className="card notice">
          <h2 className="h3">Unfinished mock{inProgress.length > 1 ? 's' : ''}</h2>
          <ul className="plain-list">
            {inProgress.map((m) => (
              <li key={m.id} className="row between">
                <span>{m.title}</span>
                <Link className="btn btn-primary btn-sm" to={`/exam/${m.id}`}>Resume</Link>
              </li>
            ))}
          </ul>
        </section>
      )}

      {t && t.attempts > 0 && (
        <section className="tiles">
          <Tile label="Questions practised" value={fmtNum(t.questions)} />
          <Tile label="Accuracy" value={fmtPct(t.correct / t.attempts)} />
          <Tile label="Mocks taken" value={fmtNum(t.mocks)} />
          <Tile label="Answered today" value={fmtNum(t.today)} />
        </section>
      )}

      <section className="stack">
        <h2>Practise by subject</h2>
        <div className="grid-cards">
          {meta.subjects.filter((s) => s.count > 0).map((s) => (
            <Link key={s.code} to={`/practice?subject=${s.code}`} className="card card-link">
              <span className="card-title">{s.name}</span>
              <span className="muted small">{SUBJECT_BLURB[s.code]}</span>
              <span className="card-foot">{fmtNum(s.count)} questions</span>
            </Link>
          ))}
        </div>
      </section>

      <section className="stack">
        <h2>Mock tests by exam</h2>
        <div className="grid-cards">
          {meta.exams.map((e) => (
            <Link key={e.code} to={`/mocks?exam=${e.code}`} className="card card-link">
              <span className="card-title">{e.name}</span>
              {e.stages.map((s) => (
                <span key={s.code} className="muted small">
                  {s.name}: {fmtNum(s.papers)} papers · {fmtNum(s.questions)} questions
                </span>
              ))}
            </Link>
          ))}
        </div>
      </section>

      {recent.length > 0 && (
        <section className="stack">
          <div className="row between">
            <h2>Recent mocks</h2>
            <Link to="/analytics">All progress →</Link>
          </div>
          <div className="card table-card">
            <table className="table">
              <thead><tr><th>Mock</th><th>Submitted</th><th className="num">Score</th></tr></thead>
              <tbody>
                {recent.map((m) => (
                  <tr key={m.id}>
                    <td><Link to={`/result/${m.id}`}>{m.title}</Link></td>
                    <td className="muted">{fmtDateTime(m.submitted_at)}</td>
                    <td className="num">{fmtScore(m.score ?? 0)} / {fmtScore(m.max_score ?? 0)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </div>
  );
}

export function Tile({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="tile">
      <span className="tile-label">{label}</span>
      <span className="tile-value">{value}</span>
      {sub && <span className="tile-sub">{sub}</span>}
    </div>
  );
}
