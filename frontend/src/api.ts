import { useCallback, useEffect, useState } from 'react';
import type {
  AnswerResult, GenerateReport, LabStatus, LearnerOverview, Meta, Mock, MockResult, MockSummary, Paper, Pattern,
  PracticeQuestion, QuestionPage, SearchResponse, Stats, TopicGuess, TrainingReport,
} from './types';

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...init?.headers },
  });
  if (!res.ok) {
    let message = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (typeof body.detail === 'string') message = body.detail;
    } catch {
      /* non-JSON error body: keep the status line */
    }
    throw new Error(message);
  }
  return res.json() as Promise<T>;
}

const post = <T>(path: string, body?: unknown) =>
  request<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) });

function query(params: Record<string, string | number | null | undefined>): string {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== null && v !== undefined && v !== '') qs.set(k, String(v));
  }
  return qs.toString();
}

export interface QuestionFilters {
  exam?: string;
  stage?: string;
  subject?: string;
  chapter?: string;
  year?: string;
  paper?: string;
  status?: string;
  search?: string;
  order?: string;
  origin?: string;
  semantic?: string;
  similar?: string;
  ids?: string;
}

export interface ResponsePayload {
  chosen: number | null;
  marked: boolean;
  ms: number;
}

export const api = {
  meta: () => request<Meta>('/api/meta'),
  questions: (f: QuestionFilters & { seed?: number; after?: number | null; limit?: number }) =>
    request<QuestionPage>(`/api/questions?${query({ ...f })}`),
  answer: (question_id: number, chosen: number | null, time_ms?: number) =>
    post<AnswerResult>('/api/practice/answer', { question_id, chosen, time_ms }),
  toggleBookmark: (id: number) => post<{ bookmarked: boolean }>(`/api/bookmarks/${id}`),
  patterns: () => request<Pattern[]>('/api/patterns'),
  papers: (exam: string, stage?: string) => request<Paper[]>(`/api/papers?${query({ exam, stage })}`),
  createMock: (body: { pattern_id?: string; paper_id?: string; fresh_only?: boolean; adaptive?: boolean }) =>
    post<{ id: string }>('/api/mocks', body),
  mocks: () => request<MockSummary[]>('/api/mocks'),
  mock: (id: string) => request<Mock>(`/api/mocks/${id}`),
  submitMock: (id: string, responses: Record<string, ResponsePayload>, elapsed_sec: number) =>
    post<{ id: string; score: number; max_score: number }>(`/api/mocks/${id}/submit`, { responses, elapsed_sec }),
  result: (id: string) => request<MockResult>(`/api/mocks/${id}/result`),
  discardMock: (id: string) => request<{ deleted: boolean }>(`/api/mocks/${id}`, { method: 'DELETE' }),
  stats: () => request<Stats>('/api/stats'),
  search: (q: string, f: { exam?: string; subject?: string } = {}) =>
    request<SearchResponse>(`/api/search?${query({ q, ...f })}`),
  similar: (id: number, k = 6) => request<PracticeQuestion[]>(`/api/questions/${id}/similar?k=${k}`),
  smart: (n: number, subject?: string, exclude: number[] = []) =>
    request<{ items: PracticeQuestion[]; target: { exam: string; stage: string } }>(
      `/api/smart?${query({ n, subject, exclude: exclude.join(',') })}`),
  learner: () => request<LearnerOverview>('/api/learner'),
  setTarget: (exam: string, stage: string) =>
    request<LearnerOverview>('/api/learner/target', { method: 'PUT', body: JSON.stringify({ exam, stage }) }),
  retrain: () => post<TrainingReport>('/api/learner/retrain'),
  lab: () => request<LabStatus>('/api/lab'),
  classify: (text: string) => post<{ predictions: TopicGuess[]; similar: PracticeQuestion[] }>('/api/lab/classify', { text }),
  exportFinetune: (max_examples: number) => post<NonNullable<LabStatus['finetune']>>('/api/lab/finetune', { max_examples }),
  generate: (body: { exam: string; stage: string; subject: string; chapter: string; count: number }) =>
    post<GenerateReport>('/api/ai/generate', body),
};

/** POST and stream a plain-text body, reporting the accumulated text as it arrives. */
export async function streamText(
  path: string,
  body: unknown,
  onText: (soFar: string) => void,
  signal?: AbortSignal,
): Promise<string> {
  const res = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok || !res.body) {
    let message = `${res.status} ${res.statusText}`;
    try {
      const err = await res.json();
      if (typeof err.detail === 'string') message = err.detail;
    } catch {
      /* keep status line */
    }
    throw new Error(message);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let text = '';
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    text += decoder.decode(value, { stream: true });
    onText(text);
  }
  return text;
}

let metaPromise: Promise<Meta> | null = null;

/** Catalogue data changes only on re-import, so fetch it once per page load. */
export function useMeta(): { meta: Meta | null; error: string | null } {
  const [meta, setMeta] = useState<Meta | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    metaPromise ??= api.meta();
    metaPromise.then(
      (m) => alive && setMeta(m),
      (e: Error) => {
        metaPromise = null;
        if (alive) setError(e.message);
      },
    );
    return () => {
      alive = false;
    };
  }, []);
  return { meta, error };
}

export function useAsync<T>(fn: () => Promise<T>, deps: unknown[]) {
  const [state, setState] = useState<{ data: T | null; error: string | null; loading: boolean }>({
    data: null, error: null, loading: true,
  });
  const [tick, setTick] = useState(0);
  const reload = useCallback(() => setTick((t) => t + 1), []);
  useEffect(() => {
    let alive = true;
    setState((s) => ({ ...s, loading: true, error: null }));
    fn().then(
      (data) => alive && setState({ data, error: null, loading: false }),
      (e: Error) => alive && setState((s) => ({ ...s, error: e.message, loading: false })),
    );
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);
  return { ...state, reload };
}
