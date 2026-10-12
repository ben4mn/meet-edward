"use client";

import { usePeople } from "../lib/useWho";
import { themeFor } from "../lib/whoTheme";

/** One button per person in the household; nothing to pick with one. */
export function WhoToggle({
  who,
  onPick,
}: {
  who: string;
  onPick: (person: string) => void;
}) {
  const people = usePeople();
  if (people.length < 2) return null;
  return (
    <div className="hub-who">
      {people.map((person) => (
        <button
          key={person}
          data-person={themeFor(person, people)}
          data-active={person === who}
          onClick={() => onPick(person)}
        >
          {person}
        </button>
      ))}
    </div>
  );
}
