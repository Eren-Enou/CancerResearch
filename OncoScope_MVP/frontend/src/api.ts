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

export async function uploadDocument<T>(sourceId: number, file: File): Promise<T> {
  return request<T>(`/sources/${sourceId}/document?filename=${encodeURIComponent(file.name)}`, {
    method: 'PUT', body: await file.arrayBuffer(), headers: { 'Content-Type': 'application/octet-stream' },
  });
}
