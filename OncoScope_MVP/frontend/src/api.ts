/** Turns FastAPI error bodies (string or list of {loc, msg}) into one readable line. */
export function formatErrorDetail(detail: unknown): string {
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    return detail.map(d => {
      const item = d as { loc?: (string | number)[]; msg?: string };
      const field = (item.loc ?? []).filter(p => p !== 'body' && p !== 'query' && p !== 'path').join('.');
      const msg = (item.msg ?? 'Invalid value').replace(/^Value error, /, '');
      return field ? `${field}: ${msg}` : msg;
    }).join('; ');
  }
  return JSON.stringify(detail);
}

// The session token lives only in this module's memory: never in the URL, localStorage, sessionStorage or a cookie.
// Reloading the page or locking forgets it.
let sessionToken: string | null = null;
let onSessionEnded: (() => void) | null = null;

export function setSession(token: string | null) { sessionToken = token; }
export function hasSession() { return sessionToken !== null; }
/** Called once when the server rejects the current token (expired or revoked), so the UI can lock itself. */
export function setSessionEndedHandler(handler: (() => void) | null) { onSessionEnded = handler; }

export class ApiError extends Error {
  constructor(message: string, readonly status: number, readonly retryAfter: number | null = null) { super(message); }
}

async function send(route: string, options?: RequestInit): Promise<Response> {
  const headers: Record<string, string> = { ...(options?.headers as Record<string, string> ?? {}) };
  if (sessionToken) headers.Authorization = `Bearer ${sessionToken}`;
  const result = await fetch('/api' + route, { ...options, headers, credentials: 'omit', cache: 'no-store' });
  if (!result.ok) {
    let detail = `${result.status} ${result.statusText}`;
    try {
      detail = formatErrorDetail((await result.json()).detail);
    } catch { /* retain HTTP error */ }
    if (result.status === 401 && sessionToken) {
      sessionToken = null;
      onSessionEnded?.();
    }
    const retry = Number(result.headers.get('Retry-After'));
    throw new ApiError(detail, result.status, Number.isFinite(retry) && retry > 0 ? retry : null);
  }
  return result;
}

export async function request<T>(route: string, options?: RequestInit): Promise<T> {
  const result = await send(route, { ...options, headers: { 'Content-Type': 'application/json', ...(options?.headers ?? {}) } });
  return result.json() as Promise<T>;
}

export function post<T>(route: string, data: Record<string, unknown>): Promise<T> {
  return request<T>(route, { method: 'POST', body: JSON.stringify(data) });
}

export function patch<T>(route: string, data: Record<string, unknown>): Promise<T> {
  return request<T>(route, { method: 'PATCH', body: JSON.stringify(data) });
}

// Only these types are shown in the browser; anything else is saved as a plain download, never rendered in this origin.
const VIEWABLE = ['application/pdf', 'text/plain', 'image/png', 'image/jpeg'];

/** An authorized GET as a Blob. Documents, reports and exports are fetched with the session header, never by URL. */
export async function fetchBlob(route: string): Promise<Blob> {
  const result = await send(route);
  const type = (result.headers.get('Content-Type') ?? '').split(';')[0].trim();
  return new Blob([await result.arrayBuffer()], { type: VIEWABLE.includes(type) ? type : 'application/octet-stream' });
}

/** Saves an authorized response as a file. The temporary blob: URL holds no token and is revoked straight after. */
export async function downloadFile(route: string, filename: string): Promise<void> {
  const url = URL.createObjectURL(await fetchBlob(route));
  const link = document.createElement('a');
  link.href = url; link.download = filename; link.rel = 'noopener';
  document.body.appendChild(link); link.click(); link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** Opens an authorized document in a new tab through a blob: URL (revoked after a minute). */
export async function openFile(route: string): Promise<void> {
  const url = URL.createObjectURL(await fetchBlob(route));
  window.open(url, '_blank', 'noopener');
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

/** File bytes; falls back to FileReader where Blob.arrayBuffer is unavailable (older engines, jsdom). */
export function readBytes(file: Blob): Promise<ArrayBuffer> {
  if (typeof file.arrayBuffer === 'function') return file.arrayBuffer();
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as ArrayBuffer);
    reader.onerror = () => reject(reader.error);
    reader.readAsArrayBuffer(file);
  });
}

export async function uploadDocument<T>(sourceId: number, file: File): Promise<T> {
  // The filename goes in a header, not the URL, so it stays out of server access logs.
  return request<T>(`/sources/${sourceId}/document`, {
    method: 'PUT', body: await readBytes(file),
    headers: { 'Content-Type': 'application/octet-stream', 'X-Document-Filename': encodeURIComponent(file.name) },
  });
}
