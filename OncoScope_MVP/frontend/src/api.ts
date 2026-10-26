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

export async function request<T>(route: string, options?: RequestInit): Promise<T> {
  const result = await fetch('/api' + route, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...(options?.headers ?? {}) },
  });
  if (!result.ok) {
    let detail = `${result.status} ${result.statusText}`;
    try {
      detail = formatErrorDetail((await result.json()).detail);
    } catch { /* retain HTTP error */ }
    throw new Error(detail);
  }
  return result.json() as Promise<T>;
}

export function post<T>(route: string, data: Record<string, unknown>): Promise<T> {
  return request<T>(route, { method: 'POST', body: JSON.stringify(data) });
}

export function patch<T>(route: string, data: Record<string, unknown>): Promise<T> {
  return request<T>(route, { method: 'PATCH', body: JSON.stringify(data) });
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
  return request<T>(`/sources/${sourceId}/document?filename=${encodeURIComponent(file.name)}`, {
    method: 'PUT', body: await readBytes(file), headers: { 'Content-Type': 'application/octet-stream' },
  });
}
