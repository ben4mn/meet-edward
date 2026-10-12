"use client";

import "../hub.css";
import { HubRoot } from "../components/HubRoot";

import { useCallback, useState } from "react";

import { usePoll } from "../../display/hooks/usePoll";
import { useWakeLock } from "../../display/hooks/useWakeLock";
import { HubHeader } from "../components/HubHeader";
import { useIdleRedirect } from "../lib/useIdleRedirect";
import { useWho } from "../lib/useWho";
import {
  addListItem,
  deleteListItem,
  fetchLists,
  setListItemDone,
} from "../lib/hubApi";
import type { ListsResponse } from "../lib/types";

const POLL_MS = 8000;

export default function HubListsPage() {
  useWakeLock(true);
  useIdleRedirect();

  const { who } = useWho();
  const { data, refetch } = usePoll<ListsResponse>(fetchLists, POLL_MS);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);

  const names = data?.names ?? ["Shopping", "Household"];

  const submit = useCallback(
    async (listName: string) => {
      const text = (draft[listName] ?? "").trim();
      if (!text || busy) return;
      setBusy(true);
      try {
        await addListItem(listName, text, who.toLowerCase());
        setDraft((d) => ({ ...d, [listName]: "" }));
        refetch();
      } finally {
        setBusy(false);
      }
    },
    [draft, busy, who, refetch],
  );

  const toggle = useCallback(
    async (id: string, done: boolean) => {
      await setListItemDone(id, done, who.toLowerCase());
      refetch();
    },
    [who, refetch],
  );

  return (
    <HubRoot>
      <HubHeader title="Lists" />

      <main className="hub-two-col">
        {names.map((name) => {
          const items = data?.lists?.[name] ?? [];
          return (
            <section className="hub-card" key={name}>
              <h2 className="hub-card-title">{name}</h2>

              <div className="hub-rows hub-rows-scroll">
                {items.length ? (
                  items.map((item) => (
                    <div className="hub-listrow" key={item.id}>
                      <button
                        className="hub-check"
                        data-done={item.done}
                        onClick={() => toggle(item.id, !item.done)}
                        aria-label={item.done ? "Un-tick" : "Tick"}
                      >
                        {item.done ? "✓" : ""}
                      </button>
                      <span className="hub-listrow-text" data-done={item.done}>
                        {item.text}
                      </span>
                      <button
                        className="hub-x"
                        onClick={async () => {
                          await deleteListItem(item.id);
                          refetch();
                        }}
                        aria-label="Remove"
                      >
                        ✕
                      </button>
                    </div>
                  ))
                ) : (
                  <div className="hub-empty-lg">Empty</div>
                )}
              </div>

              {/* Typing on a 7" screen is miserable, so this is a fallback —
                  voice is meant to be the primary way things land here. */}
              <form
                className="hub-add"
                onSubmit={(e) => {
                  e.preventDefault();
                  submit(name);
                }}
              >
                <input
                  className="hub-add-input"
                  value={draft[name] ?? ""}
                  placeholder={`Add to ${name.toLowerCase()}…`}
                  onChange={(e) => setDraft((d) => ({ ...d, [name]: e.target.value }))}
                />
                <button className="hub-add-btn" type="submit" disabled={busy}>
                  Add
                </button>
              </form>
            </section>
          );
        })}
      </main>
    </HubRoot>
  );
}
