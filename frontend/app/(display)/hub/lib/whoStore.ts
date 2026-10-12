// Who is currently logging — one shared value for the whole hub.
//
// This was per-component useState, which meant tapping someone in the
// toggle updated the toggle but not the themed shell: the panel only
// changed colour after navigating. A header claiming one person while the
// pill claims the other is worse than no theming at all.
//
// Module-level store + subscriptions so every component sees one value.
// Shaped for useSyncExternalStore: getWho and getPeople return stable
// values between changes.
//
// The household comes from the server (GET /api/hub/who → `people`,
// EDWARD_HOUSEHOLD, owner first) and is remembered on the panel, so an
// offline reload still knows who lives here. Until it is known there is
// nobody to pick and `who` is "".

const STORAGE_KEY = "hub.who";
const PEOPLE_KEY = "hub.people";

const NOBODY: readonly string[] = Object.freeze([]);

let people: readonly string[] = NOBODY;
let current = "";
const listeners = new Set<() => void>();

function emit(): void {
  listeners.forEach((fn) => fn());
}

export function getWho(): string {
  return current;
}

/** The household, owner first ([] until the server or storage says). */
export function getPeople(): readonly string[] {
  return people;
}

export function subscribeWho(fn: () => void): () => void {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}

function cleanPeople(raw: unknown): string[] {
  if (!Array.isArray(raw)) return [];
  const out: string[] = [];
  for (const p of raw) {
    if (typeof p !== "string") continue;
    const name = p.trim();
    if (name && !out.includes(name)) out.push(name);
  }
  return out;
}

function savedWho(): string | null {
  try {
    return window.localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

/**
 * The household is known (from the server, or remembered on the panel).
 * Returns true when it changed. An empty or malformed list is ignored —
 * a known household is never emptied by a bad answer. If the person
 * logging is no longer in it, the remembered choice or the first person
 * takes over.
 */
export function setPeople(next: unknown, { persist = true }: { persist?: boolean } = {}): boolean {
  const list = cleanPeople(next);
  if (list.length === 0) return false;
  const same = list.length === people.length && list.every((p, i) => p === people[i]);
  if (same) return false;

  people = Object.freeze(list);
  if (!people.includes(current)) {
    const saved = savedWho();
    current = saved && people.includes(saved) ? saved : people[0];
  }
  if (persist) {
    try {
      window.localStorage.setItem(PEOPLE_KEY, JSON.stringify(people));
    } catch {
      /* no storage — the server answers again on the next load */
    }
  }
  emit();
  return true;
}

/** Returns true when the choice actually changed (the hub posts only then). */
export function setWho(person: string): boolean {
  // An unknown name would theme to nothing; keep the last good value.
  if (!people.includes(person)) return false;
  if (person === current) return false; // no-op sets must not churn subscribers

  current = person;
  try {
    window.localStorage.setItem(STORAGE_KEY, person);
  } catch {
    /* private mode / no storage — the session still works, it just won't
       survive a reload */
  }
  emit();
  return true;
}

/**
 * Load the remembered household and choice. Called from an effect after
 * hydration rather than at module load, so server and first client render
 * agree.
 */
export function hydrateWho(): void {
  try {
    const raw = window.localStorage.getItem(PEOPLE_KEY);
    if (raw) setPeople(JSON.parse(raw), { persist: false });
  } catch {
    /* no storage, or a value from an older build */
  }
  const saved = savedWho();
  if (saved && people.includes(saved) && saved !== current) {
    current = saved;
    emit();
  }
}

/** Test seam — module state would otherwise leak between cases. */
export function resetWhoForTests(): void {
  people = NOBODY;
  current = "";
  listeners.clear();
}
