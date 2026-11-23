import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { formatMinutes, SlaPill } from "@/components/tickets/sla";
import type { SlaClock, TicketSla } from "@/lib/api/types";

const clock = (minutes_left: number): SlaClock => ({
  target_minutes: 240,
  due_at: "2026-11-11T12:00:00Z",
  stopped_at: null,
  state: "on_track",
  minutes_left,
});
const sla = (over: Partial<TicketSla> & { left?: number } = {}): TicketSla => ({
  policy: null,
  current: "first_reply",
  state: "on_track",
  paused: false,
  first_reply: clock(over.left ?? 130),
  resolve: clock(900),
  ...over,
});

describe("formatMinutes", () => {
  it("reads naturally", () => {
    expect(formatMinutes(35)).toBe("35m");
    expect(formatMinutes(130)).toBe("2h 10m");
    expect(formatMinutes(-60)).toBe("1h");
    expect(formatMinutes(60 * 28)).toBe("1d 4h");
  });
});

describe("SlaPill", () => {
  it("shows time left for the current clock", () => {
    render(<SlaPill sla={sla()} />);
    expect(screen.getByText(/First reply: 2h 10m left/)).toBeInTheDocument();
  });

  it("shows Overdue for a breached timer", () => {
    render(<SlaPill sla={sla({ state: "breached", left: -35 })} />);
    expect(screen.getByText(/Overdue 35m/)).toBeInTheDocument();
  });

  it("shows Paused outside business hours", () => {
    render(<SlaPill sla={sla({ paused: true })} />);
    expect(screen.getByText("Paused")).toBeInTheDocument();
  });

  it("hides a met timer in the compact list view and renders nothing without sla", () => {
    const { container, rerender } = render(<SlaPill sla={sla({ state: "met" })} compact />);
    expect(container).toBeEmptyDOMElement();
    rerender(<SlaPill sla={null} />);
    expect(container).toBeEmptyDOMElement();
  });
});
