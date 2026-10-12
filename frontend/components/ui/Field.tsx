"use client";

// Form fields (docs/design-system-2026-09-25.md §3). `Field` owns the
// label, the hint and the error and hands their ids to the control inside
// it (Input, Select or Textarea), so `getByLabelText` and screen readers
// just work. Controls are 16px (iOS zooms the page into anything smaller)
// and at least 44px tall.

import React, { createContext, forwardRef, useContext, useId } from "react";
import type { InputHTMLAttributes, ReactNode, SelectHTMLAttributes, TextareaHTMLAttributes } from "react";
import { ChevronDown } from "lucide-react";
import { cn } from "./cn";

interface FieldContextValue {
  id: string;
  describedBy?: string;
  invalid: boolean;
}

const FieldContext = createContext<FieldContextValue | null>(null);

export function Field({
  label,
  hint,
  error,
  id: idProp,
  className,
  children,
}: {
  label: ReactNode;
  hint?: ReactNode;
  /** Shown instead of the hint; marks the control `aria-invalid`. */
  error?: ReactNode;
  id?: string;
  className?: string;
  children: ReactNode;
}) {
  const auto = useId();
  const id = idProp ?? `field${auto}`;
  const hintId = `${id}-hint`;
  const errorId = `${id}-error`;
  const describedBy = error ? errorId : hint ? hintId : undefined;
  return (
    <div className={cn("space-y-1.5", className)}>
      <label htmlFor={id} className="block text-footnote font-medium text-text-secondary">
        {label}
      </label>
      <FieldContext.Provider value={{ id, describedBy, invalid: Boolean(error) }}>{children}</FieldContext.Provider>
      {error ? (
        <p id={errorId} className="text-footnote text-bad">
          {error}
        </p>
      ) : hint ? (
        <p id={hintId} className="text-footnote text-text-muted">
          {hint}
        </p>
      ) : null}
    </div>
  );
}

/** The shared control look, for anything hand-rolled that must match. */
export const CONTROL =
  "block w-full rounded-md border border-input-border bg-input-bg px-3 text-[16px] leading-6 text-text-primary " +
  "placeholder:text-text-muted transition-[border-color,box-shadow] duration-fast ease-out " +
  "hover:border-text-muted/40 focus:border-terminal/70 focus:outline-none focus:ring-2 focus:ring-terminal/25 " +
  "focus-visible:outline-none disabled:cursor-not-allowed disabled:opacity-50 " +
  "aria-[invalid=true]:border-bad/70 aria-[invalid=true]:focus:ring-bad/25";

function useControlProps(props: {
  id?: string;
  "aria-describedby"?: string;
  "aria-invalid"?: boolean | "true" | "false" | "grammar" | "spelling";
}) {
  const field = useContext(FieldContext);
  const describedBy = [props["aria-describedby"], field?.describedBy].filter(Boolean).join(" ") || undefined;
  return {
    id: props.id ?? field?.id,
    "aria-describedby": describedBy,
    "aria-invalid": props["aria-invalid"] ?? (field?.invalid ? true : undefined),
  };
}

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(function Input(
  { className, ...props },
  ref,
) {
  const a11y = useControlProps(props);
  return <input ref={ref} {...props} {...a11y} className={cn(CONTROL, "min-h-[44px] py-2.5", className)} />;
});

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(
  function Textarea({ className, ...props }, ref) {
    const a11y = useControlProps(props);
    return <textarea ref={ref} {...props} {...a11y} className={cn(CONTROL, "min-h-[88px] resize-y py-2.5", className)} />;
  },
);

export const Select = forwardRef<
  HTMLSelectElement,
  SelectHTMLAttributes<HTMLSelectElement> & { wrapperClassName?: string }
>(function Select({ className, wrapperClassName, children, ...props }, ref) {
  const a11y = useControlProps(props);
  return (
    <div className={cn("relative", wrapperClassName)}>
      <select
        ref={ref}
        {...props}
        {...a11y}
        className={cn(CONTROL, "min-h-[44px] appearance-none py-2.5 pr-9", className)}
      >
        {children}
      </select>
      <ChevronDown
        aria-hidden
        className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-text-muted"
      />
    </div>
  );
});
