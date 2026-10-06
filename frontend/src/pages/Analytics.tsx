import { Link } from 'react-router-dom';
import { api, useAsync } from '../api';
import { DailyColumns, RatioBars, TrendLine } from '../components/charts';
import { SUBJECT_SHORT, fmtDateTime, fmtNum, fmtPct, fmtScore, parseUtc } from '../lib/format';
import { Tile } from './Dashboard';

function lastNDays(n: number): string[] {
  const out: string[] = [];
  const d = new Date();
  for (let i = n - 1; i >= 0; i--) {
    const day = new Date(d.getFullYear(), d.getMonth(), d.getDate() - i);
    out.push(`${day.getFullYear()}-${String(day.getMonth() + 1).padStart(2, '0')}-${String(day.getDate()).padStart(2, '0')}`);
  }
  return out;
}

export default function Analytics() {
  const stats = useAsync(() => api.stats(), []);
  if (stats.error) return <p className="error-text">{stats.error}</p>;
  if (!stats.data) return <p className="muted">Loading…</p>;
  const { totals, subjects, weak_chapters, activity, mocks } = stats.data;

  if (totals.attempts === 0) {
    return (
      <div className="card empty">
        <h1 className="h2">No progress yet</h1>
        <p className="muted">Answer a few questions or take a mock test, and your accuracy, weak topics and score trend will appear here.</p>
        <div className="row gap center">
          <Link className="btn btn-primary" to="/practice">Start practising</Link>
          <Link className="btn" to="/mocks">Take a mock</Link>
        </div>
      </div>
    );
  }

  const byDay = new Map(activity.map((a) => [a.day, a]));
  const days = lastNDays(30).map((day) => {
    const a = byDay.get(day);
    const date = new Date(`${day}T00:00:00`);
    const label = date.toLocaleDateString('en-IN', { day: 'numeric', month: 'short' });
    return {
      day, label, value: a?.attempts ?? 0,
      detail: a ? `${label} · ${a.correct} correct (${fmtPct(a.correct / a.attempts)})` : `${label} · no practice`,
    };
  });

  return (
    <div className="stack-lg">
      <h1>Your progress</h1>
      <section className="tiles">
        <Tile label="Questions practised" value={fmtNum(totals.questions)} sub={`${fmtNum(totals.attempts)} answers in total`} />
        <Tile label="Overall accuracy" value={fmtPct(totals.correct / totals.attempts)} />
        <Tile label="Mocks taken" value={fmtNum(totals.mocks)} />
        <Tile label="Answered today" value={fmtNum(totals.today)} />
      </section>

      <div className="grid-2">
        <section className="card chart-card">
          <h2 className="h3">Accuracy by subject</h2>
          <p className="muted small">Share of your answers that were correct, practice and mocks combined.</p>
          <RatioBars rows={subjects.map((s) => ({
            key: s.subject, label: SUBJECT_SHORT[s.subject] ?? s.name, fullLabel: s.name, value: s.correct / s.attempts,
            detail: `${fmtNum(s.correct)} of ${fmtNum(s.attempts)} correct`,
          }))} />
        </section>

        <section className="card chart-card">
          <h2 className="h3">Mock scores</h2>
          <p className="muted small">Score as a share of maximum marks, oldest to newest.</p>
          {mocks.length ? (
            <TrendLine points={mocks.map((m) => ({
              value: m.max_score ? m.score / m.max_score : 0,
              label: `${fmtScore(m.score)} / ${fmtScore(m.max_score)} · ${m.title}`,
              sub: fmtDateTime(m.submitted_at),
            }))} />
          ) : <p className="muted">Take a mock test to start your score trend.</p>}
        </section>
      </div>

      <section className="card chart-card">
        <h2 className="h3">Questions answered per day</h2>
        <p className="muted small">Last 30 days.</p>
        <DailyColumns days={days} />
      </section>

      {weak_chapters.length > 0 && (
        <section className="card table-card">
          <div className="card-pad">
            <h2 className="h3">Topics to work on</h2>
            <p className="muted small">Your lowest-accuracy topics with at least 5 answers.</p>
          </div>
          <table className="table">
            <thead><tr><th>Topic</th><th>Subject</th><th className="num">Accuracy</th><th className="num">Answers</th><th /></tr></thead>
            <tbody>
              {weak_chapters.map((w) => (
                <tr key={w.subject + w.chapter}>
                  <td>{w.label}</td>
                  <td className="muted">{w.subject_name}</td>
                  <td className="num">{fmtPct(w.correct / w.attempts)}</td>
                  <td className="num">{w.attempts}</td>
                  <td className="num"><Link to={`/practice?subject=${w.subject}&chapter=${w.chapter}&status=unattempted`}>Practise →</Link></td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      {mocks.length > 0 && (
        <section className="card table-card">
          <div className="card-pad"><h2 className="h3">Mock history</h2></div>
          <table className="table">
            <thead><tr><th>Mock</th><th>Submitted</th><th className="num">Score</th><th className="num">%</th></tr></thead>
            <tbody>
              {[...mocks].sort((a, b) => +parseUtc(b.submitted_at) - +parseUtc(a.submitted_at)).map((m) => (
                <tr key={m.id}>
                  <td><Link to={`/result/${m.id}`}>{m.title}</Link></td>
                  <td className="muted">{fmtDateTime(m.submitted_at)}</td>
                  <td className="num">{fmtScore(m.score)} / {fmtScore(m.max_score)}</td>
                  <td className="num">{fmtPct(m.max_score ? m.score / m.max_score : 0)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </div>
  );
}
