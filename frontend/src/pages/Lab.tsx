import { useState } from 'react';
import { Link } from 'react-router-dom';
import { api, useAsync } from '../api';
import { RatioBars } from '../components/charts';
import { Markup } from '../components/Markup';
import { SUBJECT_SHORT, fmtNum, fmtPct } from '../lib/format';
import type { LabStatus, PracticeQuestion, TopicGuess } from '../types';
import { Tile } from './Dashboard';

export default function Lab() {
  const lab = useAsync(() => api.lab(), []);
  if (lab.error) return <p className="error-text">{lab.error}</p>;
  if (!lab.data) return <p className="muted">Loading…</p>;
  const d = lab.data;
  return (
    <div className="stack-lg">
      <section className="stack-sm">
        <h1>AI Lab</h1>
        <p className="muted">The four models behind the app, what each one does, and how well it is doing.</p>
      </section>
      <div className="lab-flow" aria-label="How the parts connect">
        <span>145k PYQs</span><span aria-hidden>→</span><span>RAG search</span><span aria-hidden>→</span>
        <span>LLM + trained models</span><span aria-hidden>→</span><span>Exam engine</span><span aria-hidden>⇄</span><span>Self-learning learner model</span>
      </div>
      <RagCard d={d} />
      <TopicCard d={d} />
      <LearnerCard d={d} />
      <LlmCard d={d} onExported={lab.reload} />
    </div>
  );
}

function Status({ ok, children, neutral = false }: { ok: boolean; children: string; neutral?: boolean }) {
  const tone = ok ? 'status-strong' : neutral ? 'status-new' : 'status-weak';
  return <span className={`status ${tone}`}>{ok ? '● ' : '○ '}{children}</span>;
}

function RagCard({ d }: { d: LabStatus }) {
  return (
    <section className="card stack">
      <div className="row between wrap gap">
        <h2 className="h3">1 · RAG search (retrieval)</h2>
        <Status ok={d.rag.ready}>{d.rag.ready ? 'Index ready' : 'Index not built'}</Status>
      </div>
      <p className="small">
        Every PYQ is embedded locally with <code>{d.rag.model}</code> (a static embedding model, no API quota) and
        searched alongside SQLite FTS5 keyword ranking; the two rankings are merged with Reciprocal Rank Fusion. It powers
        <Link to="/search"> Search</Link>, "Similar questions", and the grounding of every Gemini answer.
      </p>
      {d.rag.ready ? (
        <div className="tiles tiles-compact">
          <Tile label="Vectors" value={fmtNum(d.rag.count ?? 0)} />
          <Tile label="Dimensions" value={String(d.rag.dim)} />
          <Tile label="Build time" value={`${d.rag.seconds}s`} sub={d.rag.built_at} />
        </div>
      ) : <p className="muted small">Build it with <code>python -m app.rag</code> (about 30 s).</p>}
    </section>
  );
}

function TopicCard({ d }: { d: LabStatus }) {
  const t = d.topic_model;
  const [text, setText] = useState('');
  const [out, setOut] = useState<{ predictions: TopicGuess[]; similar: PracticeQuestion[] } | null>(null);
  const [err, setErr] = useState<string | null>(null);
  async function classify() {
    setErr(null);
    try {
      setOut(await api.classify(text));
    } catch (e) {
      setErr((e as Error).message);
    }
  }
  return (
    <section className="card stack">
      <div className="row between wrap gap">
        <h2 className="h3">2 · Topic model (trained on this dataset)</h2>
        <Status ok={t.ready}>{t.ready ? `Trained ${t.trained_at}` : 'Not trained'}</Status>
      </div>
      <p className="small">
        A TF-IDF + linear classifier fitted on {t.train_size ? fmtNum(t.train_size) : 'the'} labelled PYQs to predict
        subject and chapter ({t.classes ?? '–'} chapters). Scores below are on {t.test_size ? fmtNum(t.test_size) : 'held-out'} questions
        it never saw. It labels pasted questions, checks AI-generated ones, and flags dataset rows whose labels look wrong
        (<code>python -m app.topic_model audit</code>).
      </p>
      {t.ready && (
        <>
          <div className="tiles tiles-compact">
            <Tile label="Subject accuracy" value={fmtPct(t.subject_accuracy, 1)} />
            <Tile label="Chapter accuracy" value={fmtPct(t.chapter_accuracy, 1)} sub={`top-3: ${fmtPct(t.chapter_top3_accuracy, 1)}`} />
            <Tile label="Macro F1" value={String(t.chapter_macro_f1)} sub="rare chapters weigh equally" />
            <Tile label="Training time" value={`${t.seconds}s`} sub="CPU" />
          </div>
          {t.chapter_accuracy_by_subject && (
            <div>
              <h3 className="small-heading">Chapter accuracy by subject</h3>
              <RatioBars rows={Object.entries(t.chapter_accuracy_by_subject).map(([s, v]) => ({
                key: s, label: SUBJECT_SHORT[s] ?? s, value: v, detail: 'held-out chapter accuracy',
              }))} />
            </div>
          )}
          {t.top_confusions && t.top_confusions.length > 0 && (
            <details>
              <summary className="small">Most common mix-ups</summary>
              <table className="table compact">
                <thead><tr><th>Labelled</th><th>Predicted</th><th className="num">Times</th></tr></thead>
                <tbody>{t.top_confusions.map((c) => (
                  <tr key={c.true + c.predicted}><td>{c.true}</td><td>{c.predicted}</td><td className="num">{c.count}</td></tr>
                ))}</tbody>
              </table>
            </details>
          )}
          <form className="stack-sm" onSubmit={(e) => { e.preventDefault(); void classify(); }}>
            <h3 className="small-heading">Try it: paste any question</h3>
            <textarea rows={3} value={text} onChange={(e) => setText(e.target.value)}
              placeholder="e.g. A sum doubles in 8 years at simple interest. In how many years will it triple?" />
            <button type="submit" className="btn btn-sm btn-primary" disabled={text.trim().length < 5}>Classify</button>
          </form>
          {err && <p className="error-text small">{err}</p>}
          {out && (
            <div className="stack-sm">
              <RatioBars rows={out.predictions.map((p) => ({ key: p.chapter, label: p.label, fullLabel: `${p.subject} · ${p.label}`,
                value: p.probability, detail: 'model probability' }))} />
              {out.similar[0] && (
                <p className="small muted">Closest PYQ: <Link to={`/practice?ids=${out.similar[0].id}`}>{out.similar[0].paper_title}</Link></p>
              )}
            </div>
          )}
        </>
      )}
    </section>
  );
}

function LearnerCard({ d }: { d: LabStatus }) {
  const r = d.learner;
  return (
    <section className="card stack">
      <div className="row between wrap gap">
        <h2 className="h3">3 · Self-learning learner model</h2>
        <Status ok={r?.status === 'trained'} neutral>
          {r?.status === 'trained' ? `Tuned on ${fmtNum(r.attempts)} answers` : 'Learning online · first tuning after 100 answers'}
        </Status>
      </div>
      <p className="small">
        An item-response model updated after every answer:
        P(correct) = 0.25 + 0.75·σ(ability + subject skill + chapter skill − exam difficulty − question difficulty).
        Each forecast is stored before the answer is known, so its calibration is measured honestly; every 100 answers it
        replays your history under several learning rates and keeps the best. It drives Smart practice, personalised mocks,
        spaced-repetition reviews and the predicted score.
      </p>
      {r?.status === 'trained' && (
        <div className="tiles tiles-compact">
          <Tile label="Log-loss" value={String(r.logloss)} sub={`baseline ${r.baseline_logloss}`} />
          <Tile label="Better than baseline" value={fmtPct(r.skill_vs_baseline ?? 0)} />
          <Tile label="Settings tried" value={String(r.candidates)} sub={`${r.seconds}s`} />
        </div>
      )}
      <p className="small"><Link to="/coach">Open your coach →</Link></p>
    </section>
  );
}

function LlmCard({ d, onExported }: { d: LabStatus; onExported: () => void }) {
  const [max, setMax] = useState(20000);
  const [busy, setBusy] = useState(false);
  async function exportData() {
    setBusy(true);
    try {
      await api.exportFinetune(max);
      onExported();
    } finally {
      setBusy(false);
    }
  }
  const ft = d.finetune;
  return (
    <section className="card stack">
      <div className="row between wrap gap">
        <h2 className="h3">4 · LLM and fine-tuning</h2>
        <Status ok={d.gemini.enabled}>{d.gemini.enabled ? 'Gemini connected' : 'No Gemini key'}</Status>
      </div>
      <p className="small">
        Gemini ({d.gemini.models.join(' → ')}, tried in order) writes tutor explanations, grounded answers in Search, mock
        coaching, and new practice questions. Generated questions are kept only when an independent solve agrees with the
        answer key. {fmtNum(d.ai_questions)} verified AI question{d.ai_questions === 1 ? '' : 's'} so far
        {d.ai_questions > 0 && <> · <Link to="/practice?origin=ai">practise them</Link></>}.
      </p>
      <div className="notice-inline small">
        <strong>About fine-tuning:</strong> this API key cannot tune Gemini models (Google's tuning endpoint returns
        "not enabled") and there is no GPU here. So the app fine-tunes its own topic model (above) and exports the PYQ bank as
        a supervised dataset, question → answer + worked solution, ready to upload to Vertex AI Gemini tuning or to LoRA-tune
        an open model.
      </div>
      <div className="row gap wrap">
        <label className="field">
          <span className="field-label">Examples</span>
          <input type="number" min={100} max={200000} step={1000} value={max} onChange={(e) => setMax(Number(e.target.value))} />
        </label>
        <button type="button" className="btn" onClick={() => void exportData()} disabled={busy}>
          {busy ? 'Exporting…' : 'Export fine-tuning dataset'}
        </button>
      </div>
      {ft.ready && (
        <p className="small muted">
          Last export {ft.exported_at}: {fmtNum(ft.train ?? 0)} train + {fmtNum(ft.val ?? 0)} validation examples
          (of {fmtNum(ft.eligible ?? 0)} eligible text-only questions) in <code>{ft.out}</code>, in Vertex Gemini and chat-message formats.
        </p>
      )}
      <details>
        <summary className="small">Sample training example</summary>
        <div className="small"><Markup text={'**User:** SSC CGL Tier-I · Quantitative Aptitude · Profit and Loss\n\nA shopkeeper… \n1) … 2) … 3) … 4) …\n\n**Model:** Answer: option 2 (…)\n\nStep-by-step solution…'} /></div>
      </details>
    </section>
  );
}
