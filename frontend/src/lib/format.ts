export const SUBJECT_SHORT: Record<string, string> = {
  REAS: 'Reasoning',
  GK: 'General Awareness',
  MATH: 'Quant',
  ENG: 'English',
  COMPUTER: 'Computer',
};

export const fmtNum = (n: number) => n.toLocaleString('en-IN');

const minus = (s: string) => s.replace(/^-/, '−'); // typographic minus for negative marks

export const fmtPct = (x: number | null | undefined, digits = 0) =>
  x === null || x === undefined || Number.isNaN(x) ? '–' : minus(`${(x * 100).toFixed(digits)}%`);

/** Marks keep their halves and quarters but drop a trailing ".0". */
export const fmtScore = (x: number) => minus(String(Math.round(x * 100) / 100));

export function fmtDuration(totalSec: number): string {
  const s = Math.max(0, Math.round(totalSec));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h) return `${h}h ${String(m).padStart(2, '0')}m`;
  if (m) return `${m}m ${String(sec).padStart(2, '0')}s`;
  return `${sec}s`;
}

export function fmtClock(ms: number): string {
  const s = Math.max(0, Math.ceil(ms / 1000));
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${pad(Math.floor(s / 3600))}:${pad(Math.floor((s % 3600) / 60))}:${pad(s % 60)}`;
}

/** SQLite's datetime('now') is UTC without a zone marker. */
export const parseUtc = (s: string) => new Date(s.replace(' ', 'T') + (s.endsWith('Z') ? '' : 'Z'));

export const fmtDate = (iso: string | null | undefined) =>
  iso ? new Date(iso.length === 10 ? `${iso}T00:00:00` : iso).toLocaleDateString('en-IN', {
    day: 'numeric', month: 'short', year: 'numeric',
  }) : '–';

export const fmtDateTime = (utc: string | null | undefined) =>
  utc ? parseUtc(utc).toLocaleString('en-IN', {
    day: 'numeric', month: 'short', year: 'numeric', hour: 'numeric', minute: '2-digit',
  }) : '–';

export function paperLabel(p: { held_on: string | null; shift: string | null }): string {
  return [fmtDate(p.held_on), p.shift ? `Shift ${p.shift}` : null].filter(Boolean).join(' · ');
}
