"use client";

import { Monitor, Moon, Sun } from "lucide-react";
import Image from "next/image";
import { useTheme } from "next-themes";
import { useSyncExternalStore } from "react";

import { cn } from "@/lib/utils";

const noopSubscribe = () => () => {};

export function LogoMark({ className }: { className?: string }) {
  return (
    <span
      className={cn(
        "inline-flex size-8 shrink-0 items-center justify-center rounded-lg bg-white p-1 ring-1 ring-black/5",
        className,
      )}
    >
      <Image
        src="/logo-mark.png"
        alt=""
        width={64}
        height={64}
        priority
        className="size-full object-contain"
      />
    </span>
  );
}

export function Logo({ className, inverted }: { className?: string; inverted?: boolean }) {
  return (
    <span className={cn("inline-flex items-center gap-2.5", className)}>
      <LogoMark />
      <span
        className={cn(
          "text-[19px] font-semibold tracking-tight",
          inverted ? "text-white" : "text-foreground",
        )}
      >
        Ce<span className="text-primary-ink">Desk</span>
      </span>
    </span>
  );
}

/** Light / dark / system. Renders a stable placeholder until mounted so the
 *  server and client markup match (no hydration flash). */
export function ThemeToggle({ className }: { className?: string }) {
  const { theme, setTheme } = useTheme();
  const mounted = useSyncExternalStore(
    noopSubscribe,
    () => true,
    () => false,
  );

  const opts = [
    { v: "light", icon: Sun, label: "Light" },
    { v: "system", icon: Monitor, label: "System" },
    { v: "dark", icon: Moon, label: "Dark" },
  ] as const;

  return (
    <div
      role="radiogroup"
      aria-label="Theme"
      className={cn("inline-flex rounded-full border border-border bg-surface p-0.5", className)}
    >
      {opts.map(({ v, icon: Icon, label }) => {
        const active = mounted && theme === v;
        return (
          <button
            key={v}
            type="button"
            role="radio"
            aria-checked={active}
            aria-label={label}
            title={label}
            onClick={() => setTheme(v)}
            className={cn(
              "grid size-7 place-items-center rounded-full transition-colors",
              active
                ? "bg-primary text-primary-foreground"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            <Icon className="size-3.5" />
          </button>
        );
      })}
    </div>
  );
}
