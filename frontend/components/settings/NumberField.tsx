"use client";

// A number input that saves on blur (or Enter), clamped to its range —
// never a PATCH per keystroke. Shared by the settings panels.

import React, { useEffect, useRef, useState } from "react";
import { Field, Input } from "@/components/ui";

export function NumberField({
  label,
  hint,
  value,
  min,
  max,
  onSave,
}: {
  label: string;
  hint?: string;
  value: number;
  min: number;
  max: number;
  onSave: (v: number) => void;
}) {
  const [draft, setDraft] = useState(String(value));
  const focused = useRef(false);
  useEffect(() => {
    if (!focused.current) setDraft(String(value));
  }, [value]);
  const commit = () => {
    focused.current = false;
    const n = Math.min(max, Math.max(min, parseInt(draft, 10) || value));
    setDraft(String(n));
    if (n !== value) onSave(n);
  };
  return (
    <Field label={label} hint={hint}>
      <Input
        type="number"
        inputMode="numeric"
        min={min}
        max={max}
        value={draft}
        onFocus={() => (focused.current = true)}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") (e.target as HTMLInputElement).blur();
        }}
        className="tabular-nums"
      />
    </Field>
  );
}
