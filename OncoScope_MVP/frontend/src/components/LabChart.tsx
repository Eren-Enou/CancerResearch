import type { LabSeries } from '../types';
import { formatNumber } from '../records';

const DAY = 86_400_000;

/** Plots one exact-unit series with a true date axis. Every point is labelled with its stored value. */
export function LabChart({ series }: { series: LabSeries | null }) {
  if (!series) return <p className="muted">Loading series…</p>;
  const data = [...series.points].sort((a, b) => a.date.localeCompare(b.date) || a.id - b.id);
  const excluded = series.excluded.length > 0 && <div className="unit-warning" role="note">
    <strong>{series.excluded.length} {series.test} result(s) in other units are not plotted</strong> (no conversion is performed):{' '}
    {series.excluded.map(l => `${l.date} ${formatNumber(l.value)} ${l.unit} (lab #${l.id})`).join('; ')}.
  </div>;
  if (!data.length) return <>{<p className="muted">No {series.test} values in {series.unit ?? 'any unit'} have been entered.</p>}{excluded}</>;
  const W = 850, H = 270, padX = 60, padTop = 34, padBottom = 45;
  const times = data.map(d => Date.parse(d.date + 'T00:00:00Z'));
  const t0 = Math.min(...times), t1 = Math.max(...times);
  const rawMin = Math.min(...data.map(x => x.value)), rawMax = Math.max(...data.map(x => x.value));
  const range = Math.max(rawMax - rawMin, Math.abs(rawMax) * 0.1, 1);
  const min = rawMin - range * .15, max = rawMax + range * .15;
  const x = (t: number) => t1 === t0 ? W / 2 : padX + (t - t0) / (t1 - t0) * (W - padX * 2);
  const y = (v: number) => padTop + (max - v) / (max - min) * (H - padTop - padBottom);
  const path = data.map((v, i) => `${i ? 'L' : 'M'} ${x(times[i])} ${y(v.value)}`).join(' ');
  const spanDays = Math.round((t1 - t0) / DAY);
  return <div className="chart-wrap">
    <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`${series.test} in ${series.unit}: ${data.map(d => `${d.date} ${formatNumber(d.value)}`).join(', ')}`}>
      {[0, .25, .5, .75, 1].map(f => <g key={f}>
        <line x1={padX} x2={W - padX} y1={padTop + f * (H - padTop - padBottom)} y2={padTop + f * (H - padTop - padBottom)} stroke="#2e3a4c" strokeDasharray="4 6" />
        <text x={padX - 12} y={padTop + f * (H - padTop - padBottom) + 5} textAnchor="end" fill="#8b9cb2" fontSize="13">{formatNumber(Number((max - (max - min) * f).toPrecision(3)))}</text>
      </g>)}
      <path d={path} stroke="#72d8cb" strokeWidth="3" fill="none" strokeLinecap="round" strokeLinejoin="round" />
      {data.map((v, i) => <g key={v.id} data-testid="chart-point" data-lab-id={v.id} data-date={v.date} data-value={v.value} data-unit={v.unit}>
        <circle cx={x(times[i])} cy={y(v.value)} r="5.5" stroke="#0c1525" strokeWidth="2" fill="#94efe0"><title>{v.date}: {formatNumber(v.value)} {v.unit} · lab #{v.id} · source #{v.source_id}</title></circle>
        <text x={x(times[i])} y={y(v.value) - 12} textAnchor="middle" fill="#cfe9e4" fontSize="12">{formatNumber(v.value)}</text>
        <text x={x(times[i])} y={H - 15} textAnchor="middle" fill="#8999ae" fontSize="12">{v.date.substring(5)}</text>
      </g>)}
    </svg>
    <p className="caption">{series.test} in {series.unit}, {data.length} value(s) over {spanDays} day(s); horizontal spacing follows calendar dates. Measured values only, not a clinical response determination.</p>
    {excluded}
  </div>;
}
