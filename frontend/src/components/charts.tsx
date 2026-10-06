import { useEffect, useRef, useState, type PointerEvent, type ReactNode, type RefObject } from 'react';

// Small hand-rolled SVG/HTML charts, all single-series in the chart blue
// (--series-1). Thin marks, 4px rounded data-ends, hairline grid, and a hover
// tooltip on every mark; values a tooltip shows are also reachable as text.

interface TipState {
  x: number;
  y: number;
  content: ReactNode;
}

const pct = (v: number) => `${Math.round(v * 100)}%`.replace(/^-/, '−');

/** Keep a tooltip inside its card: anchor it left/right near the edges. */
function tipStyle(fraction: number) {
  const shift = fraction > 0.8 ? '-100%' : fraction < 0.2 ? '0%' : '-50%';
  return { left: `${fraction * 100}%`, top: 0, transform: `translate(${shift}, calc(-100% - 8px))` };
}

function Tooltip({ tip }: { tip: TipState | null }) {
  if (!tip) return null;
  return (
    <div className="chart-tip" style={{ left: tip.x, top: tip.y }} role="status">
      {tip.content}
    </div>
  );
}

/** Horizontal bars of a 0..1 ratio, value at the tip. */
export function RatioBars({ rows }: { rows: { key: string; label: string; fullLabel?: string; value: number; detail: string }[] }) {
  const [tip, setTip] = useState<TipState | null>(null);
  return (
    <div className="ratio-bars" onPointerLeave={() => setTip(null)}>
      {rows.map((r) => (
        <div
          key={r.key}
          className="ratio-row"
          tabIndex={0}
          onPointerMove={(e) => {
            const box = e.currentTarget.parentElement!.getBoundingClientRect();
            setTip({ x: e.clientX - box.left, y: e.clientY - box.top, content: <><strong>{pct(r.value)}</strong><span>{r.fullLabel ?? r.label} · {r.detail}</span></> });
          }}
          onFocus={(e) => {
            const box = e.currentTarget.parentElement!.getBoundingClientRect();
            const row = e.currentTarget.getBoundingClientRect();
            setTip({ x: row.right - box.left - 40, y: row.top - box.top, content: <><strong>{pct(r.value)}</strong><span>{r.fullLabel ?? r.label} · {r.detail}</span></> });
          }}
          onBlur={() => setTip(null)}
        >
          <span className="ratio-label">{r.label}</span>
          <span className="ratio-track">
            <span className="ratio-bar" style={{ width: `${Math.max(r.value * 100, 0.5)}%` }} />
            <span className="ratio-value">{pct(r.value)}</span>
          </span>
        </div>
      ))}
      <Tooltip tip={tip} />
    </div>
  );
}

/** Track the rendered width so the SVG draws 1:1 and its text stays 11px at any size. */
function useWidth(): [RefObject<HTMLDivElement | null>, number] {
  const ref = useRef<HTMLDivElement | null>(null);
  const [width, setWidth] = useState(640);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => setWidth(Math.max(240, Math.round(entry.contentRect.width))));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, width];
}

/** Whole-number ticks on a 1/2/5 step, at most about four intervals. */
function niceTicks(max: number): number[] {
  const raw = Math.max(1, max) / 4;
  const pow = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 5, 10].map((m) => Math.max(1, m * pow)).find((st) => st >= raw) ?? 10 * pow;
  const top = Math.ceil(Math.max(1, max) / step) * step;
  const ticks: number[] = [];
  for (let t = 0; t <= top + 1e-9; t += step) ticks.push(t);
  return ticks;
}

/** Score % across mocks, oldest to newest, with a snapping crosshair. */
export function TrendLine({ points }: { points: { label: string; sub: string; value: number }[] }) {
  const [hover, setHover] = useState<number | null>(null);
  const [wrap, W] = useWidth();
  const H = 220, L = 40, R = 44, T = 14, B = 12;
  const iw = W - L - R, ih = H - T - B;
  const x = (i: number) => L + (points.length === 1 ? iw / 2 : (i / (points.length - 1)) * iw);
  const y = (v: number) => T + ih - (Math.max(0, Math.min(1, v)) * ih);
  const path = points.map((p, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(p.value).toFixed(1)}`).join('');
  const last = points.length - 1;

  function onMove(e: PointerEvent<SVGRectElement>) {
    const box = e.currentTarget.getBoundingClientRect();
    const px = ((e.clientX - box.left) / box.width) * iw;
    const i = points.length === 1 ? 0 : Math.round((px / iw) * (points.length - 1));
    setHover(Math.max(0, Math.min(last, i)));
  }

  const h = hover !== null ? points[hover] : null;
  return (
    <div className="chart-wrap" ref={wrap}>
      <svg viewBox={`0 0 ${W} ${H}`} width={W} height={H} className="chart" role="img"
        aria-label={`Mock scores: latest ${pct(points[last].value)} across ${points.length} mocks`}>
        {[0, 0.25, 0.5, 0.75, 1].map((t) => (
          <g key={t}>
            <line x1={L} x2={W - R} y1={y(t)} y2={y(t)} className={t === 0 ? 'axis' : 'grid'} />
            <text x={L - 8} y={y(t) + 4} className="tick" textAnchor="end">{t * 100}%</text>
          </g>
        ))}
        <path d={path} className="series-line" />
        {h !== null && hover !== null && <line x1={x(hover)} x2={x(hover)} y1={T} y2={T + ih} className="crosshair" />}
        {points.map((p, i) => (i === last || i === hover) && (
          <circle key={i} cx={x(i)} cy={y(p.value)} r={4.5} className="series-dot" />
        ))}
        <text x={x(last) + 10} y={y(points[last].value) + 4} className="end-label">{pct(points[last].value)}</text>
        <rect x={L} y={T} width={iw} height={ih} fill="transparent"
          onPointerMove={onMove} onPointerLeave={() => setHover(null)} />
      </svg>
      {h !== null && hover !== null && (
        <div className="chart-tip" style={tipStyle(x(hover) / W)}>
          <strong>{pct(h.value)}</strong>
          <span>{h.label}</span>
          <span className="muted">{h.sub}</span>
        </div>
      )}
    </div>
  );
}

/** Daily counts as columns; one slot per day, empty days stay empty. */
export function DailyColumns({ days }: { days: { day: string; label: string; value: number; detail: string }[] }) {
  const [hover, setHover] = useState<number | null>(null);
  const [wrap, W] = useWidth();
  const H = 180, L = 40, R = 8, T = 10, B = 24;
  const iw = W - L - R, ih = H - T - B;
  const ticks = niceTicks(Math.max(...days.map((d) => d.value)));
  const max = ticks[ticks.length - 1];
  const slot = iw / days.length;
  const bw = Math.max(2, Math.min(24, slot - 2));
  const y = (v: number) => T + ih - (v / max) * ih;

  return (
    <div className="chart-wrap" ref={wrap}>
      <svg viewBox={`0 0 ${W} ${H}`} width={W} height={H} className="chart" role="img" aria-label="Questions answered per day, last 30 days">
        {ticks.map((t) => (
          <g key={t}>
            <line x1={L} x2={W - R} y1={y(t)} y2={y(t)} className={t === 0 ? 'axis' : 'grid'} />
            <text x={L - 8} y={y(t) + 4} className="tick" textAnchor="end">{t.toLocaleString('en-IN')}</text>
          </g>
        ))}
        {days.map((d, i) => {
          const cx = L + slot * i + slot / 2;
          const h = (d.value / max) * ih;
          const r = Math.min(4, h / 2, bw / 2);
          const x0 = cx - bw / 2, x1 = cx + bw / 2, top = T + ih - h, base = T + ih;
          return (
            <g key={d.day}>
              {d.value > 0 && (
                <path className={`series-bar${hover === i ? ' is-hover' : ''}`}
                  d={`M${x0},${base}V${top + r}Q${x0},${top} ${x0 + r},${top}H${x1 - r}Q${x1},${top} ${x1},${top + r}V${base}Z`} />
              )}
              {((days.length - 1 - i) % (W < 480 ? 10 : 7) === 0) && (
                <text x={cx} y={H - 6} className="tick" textAnchor="middle">{d.label}</text>
              )}
              <rect x={L + slot * i} y={T} width={slot} height={ih} fill="transparent" tabIndex={0}
                onPointerEnter={() => setHover(i)} onPointerLeave={() => setHover(null)}
                onFocus={() => setHover(i)} onBlur={() => setHover(null)} />
            </g>
          );
        })}
      </svg>
      {hover !== null && (
        <div className="chart-tip" style={tipStyle((L + slot * hover + slot / 2) / W)}>
          <strong>{days[hover].value.toLocaleString('en-IN')}</strong>
          <span>{days[hover].detail}</span>
        </div>
      )}
    </div>
  );
}

/**
 * Calibration plot for the learner model: each dot is a band of forecasts, placed at
 * (average forecast, share actually answered correctly). Dots on the diagonal mean the
 * model's probabilities can be taken at face value. Dot area grows with the answers in it.
 */
export function CalibrationPlot({ buckets }: { buckets: { lo: number; hi: number; n: number; predicted: number; actual: number }[] }) {
  const [hover, setHover] = useState<number | null>(null);
  const [wrap, W] = useWidth();
  const H = 240, L = 44, R = 16, T = 12, B = 34;
  const iw = W - L - R, ih = H - T - B;
  const x = (v: number) => L + ((v - 0.2) / 0.8) * iw;
  const y = (v: number) => T + ih - v * ih;
  const maxN = Math.max(1, ...buckets.map((b) => b.n));
  const r = (n: number) => 4 + 6 * Math.sqrt(n / maxN);
  const h = hover !== null ? buckets[hover] : null;
  return (
    <div className="chart-wrap" ref={wrap}>
      <svg viewBox={`0 0 ${W} ${H}`} width={W} height={H} className="chart" role="img"
        aria-label="Calibration: predicted chance of a correct answer against the actual rate">
        {[0, 0.25, 0.5, 0.75, 1].map((t) => (
          <g key={`y${t}`}>
            <line x1={L} x2={W - R} y1={y(t)} y2={y(t)} className={t === 0 ? 'axis' : 'grid'} />
            <text x={L - 8} y={y(t) + 4} className="tick" textAnchor="end">{t * 100}%</text>
          </g>
        ))}
        {[0.25, 0.5, 0.75, 1].map((t) => (
          <text key={`x${t}`} x={x(t)} y={H - 16} className="tick" textAnchor="middle">{t * 100}%</text>
        ))}
        <text x={L + iw / 2} y={H - 2} className="tick" textAnchor="middle">predicted chance of a correct answer</text>
        <line x1={x(0.25)} y1={y(0.25)} x2={x(1)} y2={y(1)} className="reference" />
        <text x={x(0.98)} y={y(0.98) + 16} className="tick" textAnchor="end">perfectly calibrated</text>
        {buckets.map((b, i) => (
          <g key={b.lo} onPointerEnter={() => setHover(i)} onPointerLeave={() => setHover(null)}
            onFocus={() => setHover(i)} onBlur={() => setHover(null)} tabIndex={0}>
            <circle cx={x(b.predicted)} cy={y(b.actual)} r={Math.max(12, r(b.n))} fill="transparent" />
            <circle cx={x(b.predicted)} cy={y(b.actual)} r={r(b.n)} className="series-dot" />
          </g>
        ))}
      </svg>
      {h !== null && hover !== null && (
        <div className="chart-tip" style={{ ...tipStyle(x(h.predicted) / W), top: `${y(h.actual) - 6}px` }}>
          <strong>{pct(h.actual)} correct</strong>
          <span>when the model predicted {pct(h.predicted)}</span>
          <span className="muted">{h.n} answer{h.n === 1 ? '' : 's'}</span>
        </div>
      )}
    </div>
  );
}
