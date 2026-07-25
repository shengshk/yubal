type SharedGetResult<T> = {
  ok: boolean;
  status: number;
  data: T | null;
};

const inFlight = new Map<string, Promise<SharedGetResult<unknown>>>();

export function invalidateSharedGets(): void {
  inFlight.clear();
}

/**
 * Coalesce concurrent reads of the same URL.
 *
 * Dashboard sections often refresh together after one library event. Sharing
 * only the in-flight request avoids duplicate network traffic without caching
 * data after a mutation.
 */
export function sharedJsonGet<T>(url: string): Promise<SharedGetResult<T>> {
  const active = inFlight.get(url);
  if (active) return active as Promise<SharedGetResult<T>>;

  const request = fetch(url, { credentials: "include" })
    .then(async (response): Promise<SharedGetResult<T>> => {
      if (!response.ok) {
        return { ok: false, status: response.status, data: null };
      }
      return {
        ok: true,
        status: response.status,
        data: (await response.json()) as T,
      };
    })
    .finally(() => {
      inFlight.delete(url);
    });

  inFlight.set(url, request as Promise<SharedGetResult<unknown>>);
  return request;
}

if (typeof window !== "undefined") {
  window.addEventListener("yubal:ledger-changed", invalidateSharedGets);
  window.addEventListener("yubal:settings-changed", invalidateSharedGets);
}
