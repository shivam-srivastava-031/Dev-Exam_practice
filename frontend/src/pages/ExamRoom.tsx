import { useEffect, useMemo, useReducer, useRef, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { api, type ResponsePayload } from '../api';
import { Markup } from '../components/Markup';
import { OptionList } from '../components/OptionList';
import { fmtClock, fmtScore } from '../lib/format';
import { mocksUrl, resultUrl, setPageTitle } from '../lib/urls';
import type { Mock, Part } from '../types';

// The exam room reproduces the SSC CBT interface (TCS iON): an option only
// counts once saved with "Save & Next" or "Mark for Review & Next", the palette
// shows the five standard question states, and exams with sectional timing
// (CGL/CHSL Tier-II, MTS) run each timed part on its own clock with no way back.

interface Resp {
  chosen: number | null;
  marked: boolean;
  visited: boolean;
  ms: number;
}

interface Pos {
  part: number;
  section: number;
  index: number;
}

interface ExamState extends Pos {
  phase: 'instructions' | 'running';
  responses: Record<string, Resp>;
  partEndsAt: number;
  shownAt: number;
  startedAt: number;
}

type Status = 'not-visited' | 'not-answered' | 'answered' | 'marked' | 'answered-marked';

const STATUS_ORDER: Status[] = ['answered', 'not-answered', 'not-visited', 'marked', 'answered-marked'];
const STATUS_LABEL: Record<Status, string> = {
  answered: 'Answered',
  'not-answered': 'Not Answered',
  'not-visited': 'Not Visited',
  marked: 'Marked for Review',
  'answered-marked': 'Answered & Marked for Review',
};

const BLANK: Resp = { chosen: null, marked: false, visited: false, ms: 0 };

function statusOf(r: Resp | undefined): Status {
  if (!r || !r.visited) return 'not-visited';
  if (r.chosen !== null) return r.marked ? 'answered-marked' : 'answered';
  return r.marked ? 'marked' : 'not-answered';
}

function countStatuses(ids: number[], responses: Record<string, Resp>): Record<Status, number> {
  const counts = { answered: 0, 'not-answered': 0, 'not-visited': 0, marked: 0, 'answered-marked': 0 };
  for (const id of ids) counts[statusOf(responses[id])] += 1;
  return counts;
}

const qidAt = (parts: Part[], p: Pos) => String(parts[p.part].sections[p.section].question_ids[p.index]);

type Action =
  | { type: 'start'; now: number }
  | { type: 'go'; to: Pos; now: number; commit?: { chosen: number | null; marked: boolean } }
  | { type: 'commit'; chosen: number | null; marked: boolean }
  | { type: 'next-part'; now: number };

function makeReducer(parts: Part[]) {
  const visit = (r: Record<string, Resp>, qid: string) => ({ ...r, [qid]: { ...(r[qid] ?? BLANK), visited: true } });
  const spend = (r: Record<string, Resp>, qid: string, ms: number) =>
    ({ ...r, [qid]: { ...(r[qid] ?? BLANK), ms: (r[qid]?.ms ?? 0) + Math.max(0, ms) } });

  return (s: ExamState, a: Action): ExamState => {
    switch (a.type) {
      case 'start': {
        const first = { part: 0, section: 0, index: 0 };
        return {
          ...s, ...first, phase: 'running', startedAt: a.now, shownAt: a.now,
          partEndsAt: a.now + parts[0].minutes * 60_000, responses: visit(s.responses, qidAt(parts, first)),
        };
      }
      case 'go': {
        const cur = qidAt(parts, s);
        let responses = spend(s.responses, cur, a.now - s.shownAt);
        if (a.commit) responses = { ...responses, [cur]: { ...responses[cur], ...a.commit } };
        return { ...s, ...a.to, shownAt: a.now, responses: visit(responses, qidAt(parts, a.to)) };
      }
      case 'commit': {
        const cur = qidAt(parts, s);
        return { ...s, responses: { ...s.responses, [cur]: { ...(s.responses[cur] ?? BLANK), chosen: a.chosen, marked: a.marked } } };
      }
      case 'next-part': {
        if (s.part + 1 >= parts.length) return s;
        const to = { part: s.part + 1, section: 0, index: 0 };
        const responses = spend(s.responses, qidAt(parts, s), a.now - s.shownAt);
        return {
          ...s, ...to, shownAt: a.now, partEndsAt: a.now + parts[to.part].minutes * 60_000,
          responses: visit(responses, qidAt(parts, to)),
        };
      }
    }
  };
}

// Progress is mirrored to localStorage so a refresh or crash doesn't lose the attempt.
const storageKey = (id: string) => `ssc-exam:${id}`;

function initState(mock: Mock): ExamState {
  try {
    const saved = JSON.parse(localStorage.getItem(storageKey(mock.id)) ?? 'null') as ExamState | null;
    if (saved && mock.parts[saved.part]?.sections[saved.section]?.question_ids[saved.index] !== undefined) return saved;
  } catch {
    /* storage unavailable or corrupt: start fresh */
  }
  return { phase: 'instructions', part: 0, section: 0, index: 0, responses: {}, partEndsAt: 0, shownAt: 0, startedAt: 0 };
}

export default function ExamRoom() {
  const { id = '' } = useParams();
  const navigate = useNavigate();
  const [mock, setMock] = useState<Mock | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.mock(id).then(
      (m) => (m.submitted ? navigate(resultUrl(id), { replace: true }) : setMock(m)),
      (e: Error) => setError(e.message),
    );
  }, [id, navigate]);

  if (error) {
    return (
      <div className="exam-message">
        <p className="error-text">{error}</p>
        <Link to={mocksUrl()}>Back to mock tests</Link>
      </div>
    );
  }
  if (!mock) return <div className="exam-message">Loading question paper…</div>;
  setPageTitle(mock.title);
  return <ExamSession mock={mock} />;
}

function ExamSession({ mock }: { mock: Mock }) {
  const navigate = useNavigate();
  const parts = mock.parts;
  const reducer = useMemo(() => makeReducer(parts), [parts]);
  const [state, dispatch] = useReducer(reducer, mock, initState);
  const [now, setNow] = useState(() => Date.now());
  const [confirm, setConfirm] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const submitLock = useRef(false);

  useEffect(() => {
    try {
      localStorage.setItem(storageKey(mock.id), JSON.stringify(state));
    } catch {
      /* quota or private mode: the attempt still works, it just won't survive a refresh */
    }
  }, [mock.id, state]);

  // Global question numbers run continuously across sections, as on the CBT.
  const firstNumber = useMemo(() => {
    let n = 0;
    return parts.map((p) => p.sections.map((s) => {
      const start = n;
      n += s.question_ids.length;
      return start;
    }));
  }, [parts]);

  const part = parts[state.part];
  const section = part.sections[state.section];
  const qid = qidAt(parts, state);
  const question = mock.questions[qid];
  const resp = state.responses[qid];
  const [draft, setDraft] = useState<number | null>(resp?.chosen ?? null);
  useEffect(() => {
    setDraft(state.responses[qid]?.chosen ?? null);
  }, [qid]); // eslint-disable-line react-hooks/exhaustive-deps

  const lastPart = state.part === parts.length - 1;
  const remaining = state.partEndsAt - now;
  const timeUp = state.phase === 'running' && remaining <= 0;

  useEffect(() => {
    if (state.phase !== 'running') return;
    const t = window.setInterval(() => setNow(Date.now()), 500);
    return () => window.clearInterval(t);
  }, [state.phase]);

  useEffect(() => {
    if (state.phase !== 'running') return;
    const warn = (e: BeforeUnloadEvent) => e.preventDefault();
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [state.phase]);

  useEffect(() => {
    if (!timeUp) return;
    if (lastPart) {
      void submit();
    } else {
      setConfirm(false);
      setNotice(`Time is up for ${part.name}. ${parts[state.part + 1].name} has started.`);
      dispatch({ type: 'next-part', now: Date.now() });
    }
  }, [timeUp, state.part]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!notice) return;
    const t = window.setTimeout(() => setNotice(null), 6000);
    return () => window.clearTimeout(t);
  }, [notice]);

  function goTo(to: Pos, commit?: { chosen: number | null; marked: boolean }) {
    dispatch({ type: 'go', to, now: Date.now(), commit });
    setPaletteOpen(false);
  }

  // Positions are built field by field: spreading `state` here would smuggle stale
  // responses into the reducer's `{ ...s, ...to }`.
  function nextPos(): Pos {
    if (state.index + 1 < section.question_ids.length) return { part: state.part, section: state.section, index: state.index + 1 };
    if (state.section + 1 < part.sections.length) return { part: state.part, section: state.section + 1, index: 0 };
    return { part: state.part, section: 0, index: 0 };
  }

  function prevPos(): Pos | null {
    if (state.index > 0) return { part: state.part, section: state.section, index: state.index - 1 };
    if (state.section > 0) {
      const s = state.section - 1;
      return { part: state.part, section: s, index: part.sections[s].question_ids.length - 1 };
    }
    return null;
  }

  async function submit() {
    if (submitLock.current) return;
    submitLock.current = true;
    setSubmitting(true);
    setConfirm(false);
    setSubmitError(null);
    const at = Date.now();
    const current = state.responses[qid] ?? BLANK;
    const responses = { ...state.responses, [qid]: { ...current, ms: current.ms + Math.max(0, at - state.shownAt) } };
    const payload: Record<string, ResponsePayload> = {};
    for (const [k, r] of Object.entries(responses)) payload[k] = { chosen: r.chosen, marked: r.marked, ms: r.ms };
    try {
      await api.submitMock(mock.id, payload, Math.round((at - state.startedAt) / 1000));
      try {
        localStorage.removeItem(storageKey(mock.id));
      } catch {
        /* ignore */
      }
      navigate(resultUrl(mock.id), { replace: true });
    } catch (e) {
      setSubmitError((e as Error).message);
      submitLock.current = false;
      setSubmitting(false);
    }
  }

  if (state.phase === 'instructions') {
    return <Instructions mock={mock} onStart={() => dispatch({ type: 'start', now: Date.now() })} />;
  }

  const prev = prevPos();
  const sectionCounts = countStatuses(section.question_ids, state.responses);
  const unsavedDraft = draft !== (resp?.chosen ?? null);
  const numberOf = (sectionIdx: number, index: number) => firstNumber[state.part][sectionIdx] + index + 1;

  return (
    <div className="exam">
      <header className="exam-top">
        <div className="exam-title">
          <strong>{mock.pattern.name}</strong>
          <span>{mock.title}</span>
        </div>
        <div className={`exam-timer${remaining < 5 * 60_000 ? ' is-low' : ''}`} role="timer">
          <span>{parts.length > 1 ? `${part.name} · ` : ''}Time Left</span>
          <strong>{fmtClock(remaining)}</strong>
        </div>
      </header>

      {parts.length > 1 && (
        <ol className="exam-parts" aria-label="Timed sections">
          {parts.map((p, i) => (
            <li key={p.name} className={i < state.part ? 'is-done' : i === state.part ? 'is-active' : 'is-locked'}>
              {p.name} <span>{p.minutes} min</span>
            </li>
          ))}
        </ol>
      )}

      <nav className="exam-sections" aria-label="Sections">
        {part.sections.map((s, i) => {
          const c = countStatuses(s.question_ids, state.responses);
          return (
            <button key={s.subject} type="button" className={i === state.section ? 'is-active' : ''}
              onClick={() => goTo({ part: state.part, section: i, index: 0 })}>
              {s.name}
              <span className="sec-count">{c.answered + c['answered-marked']}/{s.question_ids.length}</span>
            </button>
          );
        })}
      </nav>

      <div className="exam-body">
        <main className="exam-main">
          <div className="exam-qhead">
            <strong>Question No. {numberOf(state.section, state.index)}</strong>
            <span className="muted small">
              Marks for correct answer <b className="good-text">+{fmtScore(section.correct)}</b> · Negative marks{' '}
              <b className="bad-text">{section.wrong ? `−${fmtScore(section.wrong)}` : '0'}</b>
            </span>
          </div>
          <div className="exam-qbody">
            <Markup text={question.question} className="q-text" />
            <OptionList name={`exam-${qid}`} options={question.options} selected={draft} onSelect={setDraft} />
          </div>
          <footer className="exam-actions">
            <div className="row gap wrap">
              <button type="button" className="btn btn-review" onClick={() => goTo(nextPos(), { chosen: draft, marked: true })}>
                Mark for Review &amp; Next
              </button>
              <button type="button" className="btn" onClick={() => {
                setDraft(null);
                dispatch({ type: 'commit', chosen: null, marked: resp?.marked ?? false });
              }}>
                Clear Response
              </button>
            </div>
            <div className="row gap wrap">
              <button type="button" className="btn palette-toggle" onClick={() => setPaletteOpen(true)}>
                Palette ({sectionCounts.answered + sectionCounts['answered-marked']}/{section.question_ids.length})
              </button>
              <button type="button" className="btn" disabled={!prev} onClick={() => prev && goTo(prev)}>Previous</button>
              <button type="button" className="btn btn-save" onClick={() => goTo(nextPos(), { chosen: draft, marked: false })}>
                Save &amp; Next
              </button>
            </div>
          </footer>
        </main>

        {paletteOpen && <div className="drawer-backdrop" onClick={() => setPaletteOpen(false)} />}
        <aside className={`exam-palette${paletteOpen ? ' is-open' : ''}`} aria-label="Question palette">
          <div className="pal-legend">
            {STATUS_ORDER.map((st) => (
              <div key={st} className="pal-legend-row">
                <span className={`pal pal-${st}`}>{sectionCounts[st]}</span>
                <span>{STATUS_LABEL[st]}</span>
              </div>
            ))}
          </div>
          <p className="pal-section">{section.name}</p>
          <div className="pal-grid">
            {section.question_ids.map((id, i) => (
              <button key={id} type="button"
                className={`pal pal-${statusOf(state.responses[id])}${i === state.index ? ' is-current' : ''}`}
                aria-label={`Question ${numberOf(state.section, i)}: ${STATUS_LABEL[statusOf(state.responses[id])]}`}
                onClick={() => goTo({ part: state.part, section: state.section, index: i })}>
                {numberOf(state.section, i)}
              </button>
            ))}
          </div>
          <button type="button" className="btn btn-submit" onClick={() => setConfirm(true)} disabled={submitting}>
            {lastPart ? 'Submit Test' : `Submit ${part.name}`}
          </button>
        </aside>
      </div>

      {notice && <div className="toast" role="status">{notice}</div>}
      {submitError && (
        <div className="toast toast-error" role="alert">
          Couldn't submit: {submitError}{' '}
          <button type="button" className="btn btn-sm" onClick={() => void submit()}>Retry</button>
        </div>
      )}
      {submitting && <div className="overlay"><div className="overlay-card">Submitting your answers…</div></div>}

      {confirm && (
        <div className="overlay" role="dialog" aria-modal="true" aria-labelledby="confirm-title">
          <div className="overlay-card confirm">
            <h2 id="confirm-title">{lastPart ? 'Submit the test?' : `Submit ${part.name}?`}</h2>
            <div className="table-scroll">
              <table className="table compact">
                <thead>
                  <tr><th>Section</th><th className="num">Answered</th><th className="num">Not answered</th><th className="num">Marked</th><th className="num">Answered &amp; marked</th><th className="num">Not visited</th></tr>
                </thead>
                <tbody>
                  {part.sections.map((s) => {
                    const c = countStatuses(s.question_ids, state.responses);
                    return (
                      <tr key={s.subject}>
                        <td>{s.name}</td><td className="num">{c.answered}</td><td className="num">{c['not-answered']}</td>
                        <td className="num">{c.marked}</td><td className="num">{c['answered-marked']}</td><td className="num">{c['not-visited']}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            {unsavedDraft && (
              <p className="warn-text">
                The option selected on Question {numberOf(state.section, state.index)} is not saved, so it won't count.
                Use Save &amp; Next first if you want to keep it.
              </p>
            )}
            <p className="muted">
              {lastPart
                ? 'After submitting you cannot change any answer.'
                : `You will move on to ${parts[state.part + 1].name} and cannot come back to ${part.name}.`}
            </p>
            <div className="row gap end">
              <button type="button" className="btn" onClick={() => setConfirm(false)}>Back to test</button>
              <button type="button" className="btn btn-primary" onClick={() => {
                if (lastPart) void submit();
                else {
                  setConfirm(false);
                  dispatch({ type: 'next-part', now: Date.now() });
                }
              }}>
                {lastPart ? 'Yes, submit' : `Submit ${part.name}`}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function Instructions({ mock, onStart }: { mock: Mock; onStart: () => void }) {
  const [agreed, setAgreed] = useState(false);
  const navigate = useNavigate();
  const sections = mock.parts.flatMap((p) => p.sections);
  const totalQ = sections.reduce((n, s) => n + s.question_ids.length, 0);
  const totalMarks = sections.reduce((n, s) => n + s.question_ids.length * s.correct, 0);
  const minutes = mock.parts.reduce((n, p) => n + p.minutes, 0);
  const sectional = mock.parts.length > 1;

  async function discard() {
    await api.discardMock(mock.id).catch(() => undefined);
    navigate(mocksUrl(mock.pattern.exam, mock.pattern.stage));
  }

  return (
    <div className="exam instructions">
      <header className="exam-top">
        <div className="exam-title">
          <strong>{mock.pattern.name}</strong>
          <span>{mock.title}</span>
        </div>
      </header>
      <div className="instructions-body">
        <h1>General instructions</h1>
        <p className="lead">
          {totalQ} questions · {fmtScore(totalMarks)} marks · {minutes} minutes
        </p>
        <div className="table-scroll">
          <table className="table">
            <thead>
              <tr><th>{sectional ? 'Timed section' : 'Part'}</th><th>Subject</th><th className="num">Questions</th><th className="num">Correct</th><th className="num">Wrong</th><th className="num">Time</th></tr>
            </thead>
            <tbody>
              {mock.parts.flatMap((p) => p.sections.map((s, i) => (
                <tr key={p.name + s.subject}>
                  <td>{i === 0 ? p.name : ''}</td>
                  <td>{s.name}</td>
                  <td className="num">{s.question_ids.length}</td>
                  <td className="num">+{fmtScore(s.correct)}</td>
                  <td className="num">{s.wrong ? `−${fmtScore(s.wrong)}` : '0'}</td>
                  <td className="num">{i === 0 ? `${p.minutes} min` : ''}</td>
                </tr>
              )))}
            </tbody>
          </table>
        </div>

        <h2>The question palette</h2>
        <div className="legend-explain">
          <span><span className="pal pal-not-visited">1</span> You have not visited the question yet.</span>
          <span><span className="pal pal-not-answered">2</span> You have not answered the question.</span>
          <span><span className="pal pal-answered">3</span> You have answered the question.</span>
          <span><span className="pal pal-marked">4</span> Not answered, but marked for review.</span>
          <span><span className="pal pal-answered-marked">5</span> Answered and marked for review — this <strong>will be evaluated</strong>.</span>
        </div>

        <h2>Answering</h2>
        <ul>
          <li>Click an option to select it, then press <strong>Save &amp; Next</strong>. A selected option that is not saved is <strong>not counted</strong>.</li>
          <li><strong>Mark for Review &amp; Next</strong> saves your current choice (if any) and flags the question so you can revisit it.</li>
          <li><strong>Clear Response</strong> removes the saved answer for the current question.</li>
          <li>Jump to any question of the current section from the palette, or switch sections with the tabs above the question.</li>
          <li>The clock in the top-right counts down. When it reaches zero the {sectional ? 'section ends automatically' : 'test is submitted automatically'}.</li>
          {sectional && <li>Each timed section has its own clock. Once a section ends or you submit it, you cannot return to it.</li>}
          <li>Wrong answers carry negative marks as shown above; unanswered questions score zero.</li>
        </ul>

        <label className="check agree">
          <input type="checkbox" checked={agreed} onChange={(e) => setAgreed(e.target.checked)} />
          I have read and understood the instructions. I am ready to begin.
        </label>
        <div className="row gap between wrap">
          <button type="button" className="btn btn-ghost" onClick={() => void discard()}>Cancel this mock</button>
          <button type="button" className="btn btn-primary btn-lg" disabled={!agreed} onClick={onStart}>Start the test</button>
        </div>
      </div>
    </div>
  );
}
