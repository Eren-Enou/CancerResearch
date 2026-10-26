import { useRef, useState, type KeyboardEvent, type MouseEvent, type ReactNode } from 'react';
import { clampBox, nudge, parseRegionBox, regionString, type Box } from '../pdfMapping';

export type Overlay = { key: string; box: Box; kind: 'drawn' | 'mapped' | 'pending'; focus?: boolean; title: string };

type Props = {
  label: string; region: string | null; onChange: (region: string | null) => void; overlays: Overlay[]; children: ReactNode;
};

/** Region selection over a page or image: pointer drag, or fully by keyboard (arrows move, Shift+arrows resize). */
export function RegionSelector({ label, region, onChange, overlays, children }: Props) {
  const [drag, setDrag] = useState<{ x: number; y: number; x2: number; y2: number } | null>(null);
  const boxRef = useRef<HTMLDivElement | null>(null);
  const current = parseRegionBox(region);

  const pct = (e: MouseEvent<HTMLDivElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    return { x: Math.min(100, Math.max(0, (e.clientX - r.left) / r.width * 100)), y: Math.min(100, Math.max(0, (e.clientY - r.top) / r.height * 100)) };
  };
  function finish() {
    if (!drag) return;
    const box = { x: Math.min(drag.x, drag.x2), y: Math.min(drag.y, drag.y2), w: Math.abs(drag.x2 - drag.x), h: Math.abs(drag.y2 - drag.y) };
    setDrag(null);
    if (box.w >= 1 && box.h >= 1) onChange(regionString(clampBox(box)));
  }
  function startKeyboard() {
    onChange(regionString(current ?? { x: 10, y: 10, w: 30, h: 8 }));
    setTimeout(() => boxRef.current?.focus(), 0);
  }
  function onKey(e: KeyboardEvent<HTMLDivElement>) {
    if (!current) return;
    if (e.key === 'Escape') { onChange(null); return; }
    const next = nudge(current, e.key, e.shiftKey, e.altKey ? 0.2 : 1);
    if (next !== current) { e.preventDefault(); onChange(regionString(next)); }
  }
  const draft = drag ? { x: Math.min(drag.x, drag.x2), y: Math.min(drag.y, drag.y2), w: Math.abs(drag.x2 - drag.x), h: Math.abs(drag.y2 - drag.y) } : null;
  const style = (b: Box) => ({ left: `${b.x}%`, top: `${b.y}%`, width: `${b.w}%`, height: `${b.h}%` });

  return <div className="region-selector">
    <div className="region-canvas" data-testid={`region-canvas-${label}`}
      onMouseDown={e => { if (e.button !== 0 || (e.target as HTMLElement).dataset.selection) return; e.preventDefault(); const p = pct(e); setDrag({ x: p.x, y: p.y, x2: p.x, y2: p.y }); }}
      onMouseMove={e => { if (drag) { const p = pct(e); setDrag(d => d && { ...d, x2: p.x, y2: p.y }); } }} onMouseUp={finish} onMouseLeave={finish}>
      {children}
      {overlays.map(o => <div key={o.key} className={`region ${o.kind} ${o.focus ? 'focus' : ''}`} style={style(o.box)} title={o.title} data-testid={`overlay-${o.kind}`} data-key={o.key} />)}
      {draft && <div className="region pending" style={style(draft)} />}
      {current && !drag && <div ref={boxRef} data-selection="1" tabIndex={0} role="group" className="region pending selectable" style={style(current)}
        aria-label={`Selected region on ${label}: x ${current.x}%, y ${current.y}%, width ${current.w}%, height ${current.h}%. Arrow keys move, Shift plus arrows resize, Alt for fine steps, Escape clears.`}
        onKeyDown={onKey} data-testid="selected-region" />}
    </div>
    <div className="region-keyboard">
      <button type="button" className="text-button" onClick={startKeyboard}>{current ? 'Adjust region with keyboard' : 'Mark a region with the keyboard'}</button>
      {current && <span className="region-inputs">{(['x', 'y', 'w', 'h'] as const).map(k => <label key={k}>{k}
        <input type="number" step="0.5" min={0} max={100} value={current[k]} aria-label={`Region ${k} percent on ${label}`}
          onChange={e => onChange(regionString(clampBox({ ...current, [k]: Number(e.target.value) })))} /></label>)}</span>}
    </div>
  </div>;
}
