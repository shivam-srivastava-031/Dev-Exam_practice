import DOMPurify from 'dompurify';
import { marked } from 'marked';

// Dataset text is light Markdown (**bold**, __underline__, tables) mixed with
// [IMAGE: url] figure markers, [NOTE: ...] asides and the odd HTML fragment.
// Everything is sanitised before it reaches the DOM.

marked.setOptions({ gfm: true, breaks: true });

DOMPurify.addHook('afterSanitizeAttributes', (node) => {
  if (node.tagName === 'IMG') {
    node.setAttribute('loading', 'lazy');
    node.setAttribute('referrerpolicy', 'no-referrer');
    node.removeAttribute('width');
    node.removeAttribute('height');
  }
  // External links open in a new tab; in-page links (citations like "#src-2") stay put.
  if (node.tagName === 'A' && !(node.getAttribute('href') ?? '').startsWith('#')) {
    node.setAttribute('target', '_blank');
    node.setAttribute('rel', 'noopener noreferrer');
  }
});

const IMAGE = /\[IMAGE:\s*([^\]\s]+)\s*\]/g;
const NOTE = /\[NOTE:\s*([\s\S]*?)\]/g;

const escapeAttr = (s: string) => s.replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;');

const cache = new Map<string, string>();

export function toHtml(src: string, inline = false): string {
  const key = (inline ? 'i:' : 'b:') + src;
  const hit = cache.get(key);
  if (hit !== undefined) return hit;

  // A marker on a line of its own is a figure; one inside a sentence is a small
  // glyph image ("which symbol is opposite to '[IMAGE]'?") and must stay inline.
  const md = src
    .replace(IMAGE, (match: string, url: string, offset: number, whole: string) => {
      const before = whole.slice(whole.lastIndexOf('\n', offset - 1) + 1, offset);
      const lineEnd = whole.indexOf('\n', offset + match.length);
      const after = whole.slice(offset + match.length, lineEnd === -1 ? undefined : lineEnd);
      const ownLine = !inline && !before.trim() && !after.trim();
      const tag = `<img src="${escapeAttr(url)}" alt="${ownLine ? 'figure' : 'symbol'}">`;
      return ownLine ? `\n\n${tag}\n\n` : tag;
    })
    .replace(NOTE, (_, note: string) => (inline ? ` (${note.trim()})` : `\n\n> **Note:** ${note.trim()}\n\n`));
  const html = (inline ? marked.parseInline(md) : marked.parse(md)) as string;
  const clean = DOMPurify.sanitize(html, { FORBID_ATTR: ['style', 'class'], ADD_ATTR: ['referrerpolicy'] })
    .replace(/<img ([^>]*?)alt="figure"/g, '<img class="fig" $1alt="figure"')
    .replace(/<img (?![^>]*class=)/g, '<img class="fig-inline" ');

  if (cache.size > 2000) cache.clear();
  cache.set(key, clean);
  return clean;
}

// Gemini sometimes answers in LaTeX despite being asked not to; the UI has no TeX
// renderer, so AI text is rewritten into the plain Unicode maths the dataset uses.
const LATEX: [RegExp, string][] = [
  [/\\left|\\right/g, ''],
  [/\\(?:text|mathrm|mathbf|textbf|operatorname)\{([^{}]*)\}/g, '$1'],
  [/\\d?frac\{([^{}]*)\}\{([^{}]*)\}/g, '($1)/($2)'],
  [/\\sqrt\{([^{}]*)\}/g, '√($1)'],
  [/\\times/g, '×'], [/\\div/g, '÷'], [/\\cdot/g, '·'], [/\\pm/g, '±'],
  [/\\(?:Rightarrow|implies)/g, '⇒'], [/\\(?:rightarrow|to)\b/g, '→'],
  [/\\leq?\b/g, '≤'], [/\\geq?\b/g, '≥'], [/\\neq\b/g, '≠'], [/\\approx/g, '≈'], [/\\infty/g, '∞'],
  [/\\pi\b/g, 'π'], [/\\theta/g, 'θ'], [/\\alpha/g, 'α'], [/\\beta/g, 'β'],
  [/\^\{?\\circ\}?|\\degree/g, '°'], [/\\%/g, '%'], [/\\[,;:! ]/g, ' '],
  [/\^\{?2\}?(?!\d)/g, '²'], [/\^\{?3\}?(?!\d)/g, '³'],
  [/\$\$?/g, ''],
];

export function cleanAi(text: string): string {
  let out = text;
  for (let pass = 0; pass < 3; pass++) {  // nested \frac{\sqrt{..}}{..} unwrap from the inside out
    for (const [re, rep] of LATEX) out = out.replace(re, rep);
  }
  return out;
}
