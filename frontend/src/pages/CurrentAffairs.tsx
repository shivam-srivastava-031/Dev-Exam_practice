import { useEffect, useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { api, useAsync } from '../api';
import { Markup } from '../components/Markup';
import { fmtDate, fmtDateTime, fmtNum, parseUtc } from '../lib/format';
import { currentAffairsUrl, newsQuizUrl, practiceUrl, questionUrl, setPageTitle } from '../lib/urls';
import type { CurrentAffairs as Digest, NewsStory } from '../types';

/** 'YYYY-MM-DD' moved by whole days, without the browser's time zone getting involved. */
function shiftDay(iso: string, days: number): string {
  const d = new Date(`${iso}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

const longDate = (iso: string) =>
  new Date(`${iso}T00:00:00`).toLocaleDateString('en-IN', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' });

const timeOf = (utc: string) => parseUtc(utc).toLocaleTimeString('en-IN', { hour: 'numeric', minute: '2-digit' });

export default function CurrentAffairs() {
  const { day: dayParam } = useParams();
  const [query] = useSearchParams();
  const navigate = useNavigate();
  const category = query.get('category') ?? '';
  const digest = useAsync(() => api.currentAffairs(dayParam), [dayParam]);
  // The stored stories show at once; if the sources are due another read, that happens behind them.
  const [fresh, setFresh] = useState<Digest | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [failed, setFailed] = useState<string[]>([]);

  useEffect(() => {
    const d = digest.data;
    setFailed([]);
    if (!d?.needs_refresh) return;
    let alive = true;
    setRefreshing(true);
    api.refreshCurrentAffairs(d.day).then(
      (r) => alive && (setFresh(r), setFailed(r.failed_sources ?? [])),
      (e: Error) => alive && setFailed([e.message]),
    ).finally(() => alive && setRefreshing(false));
    return () => {
      alive = false;
    };
  }, [digest.data]);

  const data = fresh && fresh.day === digest.data?.day ? fresh : digest.data;
  setPageTitle(data ? `Current affairs · ${fmtDate(data.day)}` : 'Current affairs');

  if (digest.error) {
    return (
      <div className="card empty">
        <h1 className="h2">No current affairs for that day</h1>
        <p className="muted">{digest.error}</p>
        <Link to={currentAffairsUrl()}>Today's current affairs</Link>
      </div>
    );
  }
  if (!data) return <p className="muted">Loading the news…</p>;

  const isToday = data.day === data.today;
  const earlier = data.day > data.oldest ? shiftDay(data.day, -1) : null;
  const later = isToday ? null : shiftDay(data.day, 1);
  const dayUrl = (d: string) => currentAffairsUrl(d === data.today ? undefined : d);
  const names = Object.fromEntries(data.categories.map((c) => [c.code, c.name]));
  const shown = category ? data.stories.filter((s) => s.category === category) : data.stories;
  const latest = data.days.find((d) => d.day < data.day);

  return (
    <div className={`stack-lg${digest.loading ? ' is-stale' : ''}`}>
      <section className="row between wrap gap">
        <div className="stack-sm">
          <h1>Current affairs</h1>
          <p className="muted">{longDate(data.day)}{isToday ? ' · today' : ''}</p>
        </div>
        <nav className="day-nav" aria-label="Choose a day">
          {earlier ? <Link className="btn btn-sm" to={dayUrl(earlier)}>← Earlier</Link>
            : <span className="btn btn-sm" aria-disabled="true">← Earlier</span>}
          <input type="date" aria-label="Day" value={data.day} min={data.oldest} max={data.today}
            onChange={(e) => e.target.value && navigate(dayUrl(e.target.value))} />
          {later ? <Link className="btn btn-sm" to={dayUrl(later)}>Later →</Link>
            : <span className="btn btn-sm" aria-disabled="true">Later →</span>}
        </nav>
      </section>

      <section className="card notice row between wrap gap">
        <div className="stack-sm">
          <h2 className="h3">Daily quiz · {data.quiz_size} questions</h2>
          <p className="small">
            {data.quiz_linked
              ? `${data.quiz_linked} real SSC question${data.quiz_linked > 1 ? 's' : ''} on topics in ${isToday ? "today's" : "this day's"} news, then recent current-affairs PYQs.`
              : 'Recent current-affairs questions from real SSC papers.'}{' '}
            Your answers update your learner model like any practice.
          </p>
        </div>
        <Link className="btn btn-primary" to={newsQuizUrl(data.day)}>Start the quiz →</Link>
      </section>

      <section className="stack">
        <div className="row between wrap gap">
          <p className="muted small">
            {fmtNum(data.stories.length)} stor{data.stories.length === 1 ? 'y' : 'ies'}, most exam-relevant first
            {data.updated_at && <> · checked {fmtDateTime(data.updated_at)}</>}
            {refreshing && <span className="typing"> · checking for new stories…</span>}
          </p>
          {data.categories.length > 1 && (
            <div className="chips" role="group" aria-label="Category">
              <Link className={`chip${!category ? ' is-active' : ''}`} to={currentAffairsUrl(dayParam)} replace>
                All {data.stories.length}
              </Link>
              {data.categories.map((c) => (
                <Link key={c.code} className={`chip${category === c.code ? ' is-active' : ''}`}
                  to={currentAffairsUrl(dayParam, c.code)} replace>
                  {c.name} {c.count}
                </Link>
              ))}
            </div>
          )}
        </div>
        {failed.length > 0 && (
          <p className="warn-text small">Could not reach {failed.join(', ')}. Showing what was saved earlier; it is tried again on your next visit.</p>
        )}

        {shown.length > 0 ? (
          <ol className="news-list">
            {shown.map((s) => <Story key={s.id} s={s} category={names[s.category] ?? s.category} />)}
          </ol>
        ) : refreshing ? (
          <div className="card empty"><p className="typing">Fetching the news…</p></div>
        ) : category && data.stories.length ? (
          <div className="card empty">
            <p className="muted">No {names[category] ?? category} stories on this day.</p>
            <Link to={currentAffairsUrl(dayParam)}>Show all {data.stories.length}</Link>
          </div>
        ) : (
          <div className="card empty">
            <h2 className="h3">No stories for {isToday ? 'today' : 'this day'} yet</h2>
            <p className="muted">
              {isToday
                ? "Wikipedia opens each day's page at 5:30 am IST and the newspapers' feeds fill up through the day."
                : 'The newspapers’ feeds only reach back about a week, so older days hold what was saved while they were recent, plus Wikipedia’s summary of the day.'}
            </p>
            {latest && <Link className="btn" to={dayUrl(latest.day)}>{fmtDate(latest.day)}: {latest.stories} stories →</Link>}
          </div>
        )}
      </section>

      <p className="muted small">
        Sources: Wikipedia's <a href="https://en.wikipedia.org/wiki/Portal:Current_events" target="_blank" rel="noopener noreferrer">Current
        events</a> portal (CC BY-SA 4.0), The Hindu and The Indian Express. Stories are chosen and sorted for SSC on this
        server, from keyword rules and closeness to past SSC current-affairs questions; nothing here is written by AI.{' '}
        <Link to={practiceUrl({ subject: 'GK', chapter: 'current-affairs' })}>Practise every current-affairs PYQ →</Link>
      </p>
    </div>
  );
}

function Story({ s, category }: { s: NewsStory; category: string }) {
  return (
    <li className="card news-item">
      <div className="q-meta">
        <span className="pill pill-soft">{category}</span>
        <span className="muted small">{s.source}{s.published ? ` · ${timeOf(s.published)}` : ''}</span>
      </div>
      <a className="news-title" href={s.url} target="_blank" rel="noopener noreferrer">{s.title}</a>
      {s.summary && <p className="muted small clamp">{s.summary}</p>}
      {s.related.length > 0 && (
        <div className="news-related">
          <span className="small-heading">Related questions from past SSC papers</span>
          {s.related.map((r) => (
            <Link key={r.id} className="news-pyq" to={questionUrl(r.id)}>
              <Markup text={r.question} inline />
              <span className="muted small"> · {r.paper_title}</span>
            </Link>
          ))}
        </div>
      )}
    </li>
  );
}
