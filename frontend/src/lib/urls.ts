// Every page's URL is built and read here, so links and routes can't drift apart.
//
//   /practice                         all questions
//   /practice/quant                   a subject
//   /practice/quant/profit-and-loss   subject + topic
//   /practice/smart | /practice/ai    smart practice | AI-generated questions
//   /practice/paper/<paper-slug>      one previous-year paper, untimed
//   /practice/similar/<question-id>   questions like that one
//   /practice/news/2026-10-06         that day's current-affairs quiz
//   /question/<id>                    one question, shareable
//   ?exam=ssc-cgl&stage=mains&year=2023&show=wrong&order=in-order&search=...&q=<current question id>
//   /mocks/ssc-cgl[/mains]            mock tests for an exam
//   /mock/<id>, /mock/<id>/result     exam room, result
//   /current-affairs[/2026-10-06]     today's (or that day's) news, sorted for the exam; ?category=sports
//   /search?q=...  /coach  /progress  /ai-lab

export const SUBJECT_SLUGS: Record<string, string> = {
  REAS: 'reasoning', GK: 'gk', MATH: 'quant', ENG: 'english', COMPUTER: 'computer',
};
const SUBJECT_CODES = Object.fromEntries(Object.entries(SUBJECT_SLUGS).map(([code, slug]) => [slug, code]));

// "show" values in the URL, mapped to the API's status filter.
export const SHOW_TO_STATUS: Record<string, string> = { new: 'unattempted', wrong: 'incorrect', saved: 'bookmarked' };
const STATUS_TO_SHOW = Object.fromEntries(Object.entries(SHOW_TO_STATUS).map(([show, status]) => [status, show]));

// Names the UI can show before the catalogue (/api/meta) arrives, so the first paint
// of a deep link already has the right filters and tab title.
export const EXAM_NAMES: Record<string, string> = {
  'SSC-CGL': 'SSC CGL', 'SSC-CHSL': 'SSC CHSL', 'SSC-CPO': 'SSC CPO', 'SSC-GD': 'SSC GD Constable',
  'SSC-MTS': 'SSC MTS', 'SSC-Selection-Post': 'SSC Selection Post', 'SSC-Stenographer': 'SSC Stenographer',
};
export const SUBJECT_NAMES: Record<string, string> = {
  REAS: 'General Intelligence & Reasoning', GK: 'General Awareness', MATH: 'Quantitative Aptitude',
  ENG: 'English Language & Comprehension', COMPUTER: 'Computer Knowledge',
};
export const EXAM_CODES = Object.keys(EXAM_NAMES);

/** 'profit-and-loss' -> 'Profit And Loss' until the catalogue's own label is available. */
export const roughLabel = (slug: string) => slug.split('-').map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(' ');

export const examSlug = (code: string) => code.toLowerCase();
export const subjectCode = (slug: string | undefined) => (slug ? SUBJECT_CODES[slug.toLowerCase()] : undefined);
export const subjectSlug = (code: string) => SUBJECT_SLUGS[code] ?? code.toLowerCase();

/** 'ssc-cgl' -> 'SSC-CGL', 'ssc-selection-post' -> 'SSC-Selection-Post'. */
export function examCode(slug: string | null | undefined): string | undefined {
  if (!slug) return undefined;
  return EXAM_CODES.find((c) => c.toLowerCase() === slug.toLowerCase());
}

export interface PracticeTarget {
  subject?: string;   // subject code, e.g. MATH
  chapter?: string;
  exam?: string;      // exam code, e.g. SSC-CGL
  stage?: string;
  year?: string;
  status?: string;    // API status: unattempted | incorrect | bookmarked
  order?: string;     // API order: sequential
  search?: string;
  about?: string;     // meaning-based search query
  q?: number;         // the question on screen
}

export function practiceUrl(t: PracticeTarget = {}): string {
  let path = '/practice';
  if (t.subject) path += `/${subjectSlug(t.subject)}`;
  if (t.subject && t.chapter) path += `/${t.chapter}`;
  return withQuery(path, practiceQuery(t));
}

function practiceQuery(t: PracticeTarget): Record<string, string | number | undefined> {
  return {
    exam: t.exam ? examSlug(t.exam) : undefined,
    stage: t.stage,
    year: t.year,
    show: t.status ? STATUS_TO_SHOW[t.status] : undefined,
    order: t.order === 'sequential' ? 'in-order' : undefined,
    search: t.search,
    about: t.about,
    q: t.q,
  };
}

export const smartUrl = () => '/practice/smart';
export const aiPracticeUrl = (ids?: number[]) => withQuery('/practice/ai', { ids: ids?.length ? ids.join(',') : undefined });
export const paperPracticeUrl = (slug: string) => `/practice/paper/${slug}`;
export const similarUrl = (id: number) => `/practice/similar/${id}`;
export const questionUrl = (id: number) => `/question/${id}`;
export const mocksUrl = (exam?: string, stage?: string) =>
  exam ? `/mocks/${examSlug(exam)}${stage && stage !== 'pre' ? `/${stage}` : ''}` : '/mocks';
export const mockUrl = (id: string) => `/mock/${id}`;
export const resultUrl = (id: string) => `/mock/${id}/result`;
export const searchUrl = (q?: string) => withQuery('/search', { q });
/** No day means today, so the plain URL always opens the latest news. */
export const currentAffairsUrl = (day?: string, category?: string) =>
  withQuery(day ? `/current-affairs/${day}` : '/current-affairs', { category });
export const newsQuizUrl = (day: string) => `/practice/news/${day}`;

export function withQuery(path: string, params: Record<string, string | number | undefined | null>): string {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== '') qs.set(k, String(v));
  const s = qs.toString();
  return s ? `${path}?${s}` : path;
}

export function setPageTitle(title?: string) {
  document.title = title ? `${title} · SSC Practice` : 'SSC Practice · previous-year questions and mock tests';
}
