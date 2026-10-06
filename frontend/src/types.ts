export interface StageMeta {
  code: 'pre' | 'mains';
  name: string;
  subjects: Record<string, number>;
  questions: number;
  papers: number;
}

export interface ExamMeta {
  code: string;
  name: string;
  stages: StageMeta[];
}

export interface ChapterMeta {
  subject: string;
  chapter: string;
  label: string;
  n: number;
}

export interface Meta {
  exams: ExamMeta[];
  subjects: { code: string; name: string; count: number }[];
  chapters: ChapterMeta[];
  years: number[];
  total_questions: number;
  total_papers: number;
  ai_questions: number;
  ai_enabled: boolean;
}

export interface Question {
  id: number;
  exam: string;
  stage: string;
  subject: string;
  chapter: string | null;
  chapter_label: string;
  year: number | null;
  question: string;
  options: string[];
}

export interface PracticeQuestion extends Question {
  paper_title: string;
  bookmarked: boolean;
  last_correct: boolean | null;
  origin: 'pyq' | 'ai';
  /** Learner model's P(correct) for this question, before answering. */
  predicted: number;
  /** Why the adaptive engine picked it (smart practice). */
  reason?: string;
  kind?: 'review' | 'weak' | 'new' | 'weightage';
  /** Which retriever found it (search). */
  via?: ('meaning' | 'keyword')[];
  similarity?: number | null;
}

export interface QuestionPage {
  total: number;
  items: PracticeQuestion[];
  next_after: number | null;
}

export interface AnswerResult {
  answer: number;
  solution: string | null;
  is_correct: boolean | null;
  predicted: number | null;
}

export interface Section {
  subject: string;
  name: string;
  question_ids: number[];
  correct: number;
  wrong: number;
}

export interface Part {
  name: string;
  minutes: number;
  sections: Section[];
}

export interface PatternRef {
  id: string;
  name: string;
  exam: string;
  stage: string;
}

export type MockKind = 'paper' | 'random' | 'adaptive';

export interface Mock {
  id: string;
  title: string;
  kind: MockKind;
  pattern: PatternRef;
  parts: Part[];
  created_at: string;
  submitted: boolean;
  questions: Record<string, Question>;
}

export interface Pattern {
  id: string;
  exam: string;
  stage: string;
  name: string;
  exam_name: string;
  stage_name: string;
  legacy: boolean;
  minutes: number;
  questions: number;
  max_marks: number;
  parts: { name: string; minutes: number; sections: { subject: string; name: string; count: number; correct: number; wrong: number }[] }[];
  pool: Record<string, number>;
}

export interface MockSummary {
  id: string;
  kind: MockKind;
  pattern_id: string;
  paper_id: string | null;
  title: string;
  created_at: string;
  submitted_at: string | null;
  score: number | null;
  max_score: number | null;
  exam: string;
}

export interface Paper {
  id: string;
  exam: string;
  stage: string;
  title: string;
  year: number | null;
  held_on: string | null;
  shift: string | null;
  duration_min: number | null;
  question_count: number;
  max_marks: number;
  last_mock: { id: string; submitted_at: string | null; score: number | null; max_score: number | null } | null;
}

export type QuestionStatus = 'correct' | 'wrong' | 'skipped';

export interface QuestionResult {
  chosen: number | null;
  answer: number;
  status: QuestionStatus;
  marks: number;
  ms: number;
  marked: boolean;
}

export interface SectionResult {
  part: string;
  subject: string;
  name: string;
  total: number;
  attempted: number;
  correct: number;
  wrong: number;
  skipped: number;
  score: number;
  max_score: number;
  ms: number;
  accuracy: number | null;
}

export interface ReviewQuestion extends Question {
  answer: number;
  solution: string | null;
  paper_title: string;
  bookmarked: boolean;
}

export interface MockResult {
  id: string;
  title: string;
  kind: MockKind;
  pattern: PatternRef;
  paper_id: string | null;
  parts: Part[];
  submitted_at: string;
  elapsed_sec: number | null;
  minutes: number;
  result: {
    score: number;
    max_score: number;
    total: number;
    attempted: number;
    correct: number;
    wrong: number;
    skipped: number;
    accuracy: number | null;
    sections: SectionResult[];
    questions: Record<string, QuestionResult>;
  };
  questions: Record<string, ReviewQuestion>;
  ai_review: string | null;
}

export interface Stats {
  totals: { attempts: number; correct: number; questions: number; today: number; mocks: number };
  subjects: { subject: string; name: string; attempts: number; correct: number }[];
  weak_chapters: { subject: string; subject_name: string; chapter: string; label: string; attempts: number; correct: number }[];
  activity: { day: string; attempts: number; correct: number }[];
  mocks: { id: string; title: string; kind: string; pattern_id: string; submitted_at: string; score: number; max_score: number; exam: string }[];
}

export interface TopicGuess {
  subject: string;
  chapter: string;
  label: string;
  probability: number;
}

export interface SearchResponse {
  query: string;
  results: PracticeQuestion[];
  classification: TopicGuess[] | null;
  dense_index: boolean;
}

export interface MasteryRow {
  subject: string;
  subject_name: string;
  chapter: string;
  label: string;
  n: number;
  share: number;
  predicted: number;
  attempts: number;
  correct: number;
  status: 'new' | 'weak' | 'ok' | 'strong';
  confident: boolean;
  priority: number;
}

export interface Calibration {
  n: number;
  buckets: { lo: number; hi: number; n: number; predicted: number; actual: number }[];
  brier?: number;
  logloss?: number;
  mean_predicted?: number;
  mean_actual?: number;
  window?: number;
}

export interface TrainingReport {
  status: 'trained' | 'waiting';
  attempts: number;
  needed?: number;
  trained_at?: string;
  candidates?: number;
  seconds?: number;
  logloss?: number;
  brier?: number;
  previous_logloss?: number;
  baseline_logloss?: number;
  baseline_brier?: number;
  skill_vs_baseline?: number;
  hparams?: Record<string, number>;
}

export interface LearnerOverview {
  target: { exam: string; stage: string; exam_name: string; stage_name: string; pattern_id: string };
  attempts: number;
  reviews_due: number;
  reviews_total: number;
  ability: { global: number; subjects: Record<string, number> };
  mastery: MasteryRow[];
  predicted_score: {
    pattern_id: string;
    name: string;
    expected: number;
    max: number;
    sections: { name: string; subject: string; count: number; p_correct: number; expected: number; max: number; guess_value: number }[];
  };
  calibration: Calibration;
  training: TrainingReport | null;
  hparams: Record<string, number>;
}

export interface TopicModelStatus {
  ready: boolean;
  trained_at?: string;
  seconds?: number;
  train_size?: number;
  test_size?: number;
  classes?: number;
  chapter_accuracy?: number;
  chapter_top3_accuracy?: number;
  chapter_macro_f1?: number;
  subject_accuracy?: number;
  chapter_accuracy_by_subject?: Record<string, number>;
  top_confusions?: { true: string; predicted: string; count: number }[];
}

export interface LabStatus {
  rag: { ready: boolean; model: string; count?: number; dim?: number; built_at?: string; seconds?: number };
  topic_model: TopicModelStatus;
  learner: TrainingReport | null;
  finetune: { ready: boolean; examples?: number; train?: number; val?: number; eligible?: number; exported_at?: string; out?: string };
  ai_questions: number;
  gemini: { enabled: boolean; models: string[] };
}

export interface GenerateReport {
  requested: number;
  generated: number;
  verified: number;
  ids: number[];
  dropped: { question: string; reason: string }[];
  examples_used: number;
  topic: string;
}
