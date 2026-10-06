import { useEffect, useRef, useState } from 'react';
import { streamText } from '../api';
import { Markup } from './Markup';

interface Message {
  role: 'user' | 'model';
  text: string;
}

const QUICK_PROMPTS = [
  'Explain this step by step',
  'Give me a faster shortcut',
  'Explain the concept behind this',
  'Hindi mein samjhao',
  'Give me a similar question to practise',
];

/** Chat with the Gemini tutor about one question. Opens with an explanation. */
export function AiTutor({ questionId, chosen }: { questionId: number; chosen: number | null }) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  async function send(text: string, history: Message[] = messages) {
    const trimmed = text.trim();
    if (!trimmed) return;
    const next: Message[] = [...history, { role: 'user', text: trimmed }];
    setMessages([...next, { role: 'model', text: '' }]);
    setInput('');
    setBusy(true);
    setError(null);
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      await streamText('/api/ai/explain', { question_id: questionId, chosen, messages: next }, (soFar) => {
        if (!controller.signal.aborted) setMessages([...next, { role: 'model', text: soFar }]);
      }, controller.signal);
    } catch (e) {
      if (controller.signal.aborted) return;
      setError((e as Error).message);
      setMessages(history);
    } finally {
      // A newer request owns the busy flag once this one has been replaced.
      if (abortRef.current === controller) setBusy(false);
    }
  }

  useEffect(() => {
    void send(chosen === null
      ? 'Explain how to solve this question.'
      : 'Explain how to solve this question, and why my answer is right or wrong.', []);
    return () => abortRef.current?.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [questionId]);

  return (
    <section className="tutor" aria-label="AI tutor">
      <header className="tutor-head">
        <span className="tutor-badge">AI tutor</span>
        <span className="muted small">Powered by Gemini · can make mistakes, the official key above is final</span>
      </header>
      <div className="tutor-log">
        {messages.map((m, i) => (
          <div key={i} className={`bubble bubble-${m.role}`}>
            {m.role === 'model' && !m.text ? <span className="typing">Thinking…</span> : <Markup text={m.text} ai={m.role === 'model'} />}
          </div>
        ))}
        {error && <p className="error-text">{error}</p>}
      </div>
      <div className="chips">
        {QUICK_PROMPTS.map((p) => (
          <button key={p} type="button" className="chip" disabled={busy} onClick={() => void send(p)}>{p}</button>
        ))}
      </div>
      <form className="tutor-input" onSubmit={(e) => { e.preventDefault(); void send(input); }}>
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ask a follow-up doubt…"
          maxLength={2000}
          aria-label="Ask the AI tutor"
        />
        <button type="submit" className="btn btn-primary" disabled={busy || !input.trim()}>Ask</button>
      </form>
    </section>
  );
}
