"use client";

import { useWindowVirtualizer } from "@tanstack/react-virtual";
import { useState } from "react";

import { TicketRow } from "@/components/tickets/list/ticket-row";
import type { TicketStatus, TicketSummary } from "@/lib/api/types";

/**
 * Window-scrolled virtual list: only the rows near the viewport are in the DOM,
 * so a queue that has been paged to hundreds of tickets stays fast. Row heights
 * vary (wrapping subjects, tags), so each row is measured after it renders.
 */
export function VirtualTicketList({
  rows,
  activeStatus,
}: {
  rows: TicketSummary[];
  activeStatus: TicketStatus;
}) {
  const [list, setList] = useState<HTMLUListElement | null>(null);
  // Distance from the top of the document to the list, so window scroll offsets line up.
  const scrollMargin = list ? list.getBoundingClientRect().top + window.scrollY : 0;

  const virtualizer = useWindowVirtualizer({
    count: rows.length,
    estimateSize: () => 112,
    overscan: 8,
    scrollMargin,
    getItemKey: (i) => rows[i]?.id ?? i,
    initialRect: { width: 1024, height: 800 },
  });

  return (
    <ul ref={setList} className="relative" style={{ height: virtualizer.getTotalSize() }}>
      {virtualizer.getVirtualItems().map((v) => {
        const t = rows[v.index];
        if (!t) return null;
        return (
          <li
            key={v.key}
            data-index={v.index}
            ref={virtualizer.measureElement}
            className="absolute top-0 left-0 w-full border-b border-border"
            style={{ transform: `translateY(${v.start - virtualizer.options.scrollMargin}px)` }}
          >
            <TicketRow t={t} showStatus={activeStatus !== t.status} />
          </li>
        );
      })}
    </ul>
  );
}
