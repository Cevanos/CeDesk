"use client";

import { useMemo } from "react";

import { qrMatrix } from "@/lib/qr";

/**
 * A QR code drawn as one SVG path (no dependency). Always dark on white with a quiet zone,
 * so phone cameras can read it in dark mode too. Renders nothing when the text is too long.
 */
export function QrCode({
  text,
  label,
  size = 192,
}: {
  text: string;
  label: string;
  size?: number;
}) {
  const path = useMemo(() => {
    const m = qrMatrix(text);
    if (!m) return null;
    const quiet = 4;
    let d = "";
    m.forEach((row, y) =>
      row.forEach((dark, x) => {
        if (dark) d += `M${x + quiet} ${y + quiet}h1v1h-1z`;
      }),
    );
    return { d, n: m.length + quiet * 2 };
  }, [text]);

  if (!path) return null;
  return (
    <svg
      role="img"
      aria-label={label}
      viewBox={`0 0 ${path.n} ${path.n}`}
      width={size}
      height={size}
      shapeRendering="crispEdges"
      className="rounded-lg border border-border bg-white"
    >
      <rect width={path.n} height={path.n} fill="#fff" />
      <path d={path.d} fill="#000" />
    </svg>
  );
}
