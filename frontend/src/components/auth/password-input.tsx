"use client";

import { Eye, EyeOff } from "lucide-react";
import * as React from "react";

import { Input } from "@/components/ui/primitives";
import { cn } from "@/lib/utils";

export const PasswordInput = React.forwardRef<
  HTMLInputElement,
  Omit<React.InputHTMLAttributes<HTMLInputElement>, "type">
>(function PasswordInput({ className, ...props }, ref) {
  const [show, setShow] = React.useState(false);
  return (
    <div className="relative">
      <Input
        ref={ref}
        type={show ? "text" : "password"}
        className={cn("pr-11", className)}
        {...props}
      />
      <button
        type="button"
        onClick={() => setShow((s) => !s)}
        aria-label={show ? "Hide password" : "Show password"}
        aria-pressed={show}
        className="absolute top-1/2 right-1.5 grid size-8 -translate-y-1/2 place-items-center rounded-md text-muted-foreground hover:bg-surface-2 hover:text-foreground"
      >
        {show ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
      </button>
    </div>
  );
});

/** 10–128 characters is the backend rule; the bar rewards length and variety. */
export function PasswordStrength({ value }: { value: string }) {
  const score = Math.min(
    4,
    (value.length >= 10 ? 1 : 0) +
      (value.length >= 14 ? 1 : 0) +
      (/[A-Z]/.test(value) && /[a-z]/.test(value) ? 1 : 0) +
      (/\d/.test(value) && /[^A-Za-z0-9]/.test(value) ? 1 : 0),
  );
  if (!value) return null;
  const label = ["Too short", "Weak", "Okay", "Good", "Strong"][score];
  const color = ["bg-danger", "bg-danger", "bg-warning", "bg-success", "bg-success"][score];
  return (
    <div className="flex items-center gap-2" aria-live="polite">
      <div className="flex flex-1 gap-1">
        {[1, 2, 3, 4].map((i) => (
          <span
            key={i}
            className={cn(
              "h-1 flex-1 rounded-full bg-surface-3 transition-colors",
              i <= score && color,
            )}
          />
        ))}
      </div>
      <span className="text-xs text-muted-foreground">{label}</span>
    </div>
  );
}
