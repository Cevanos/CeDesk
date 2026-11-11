import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { TicketRow } from "@/components/tickets/list/ticket-row";
import { makeSummary } from "@/test/fixtures";

describe("TicketRow", () => {
  it("links to the ticket and shows its key facts", () => {
    render(
      <TicketRow
        t={makeSummary({ id: "abc", label: "T-77", subject: "Refund please", priority: "urgent" })}
        showStatus={false}
      />,
    );
    expect(screen.getByRole("link")).toHaveAttribute("href", "/tickets/abc");
    expect(screen.getByText("T-77")).toBeInTheDocument();
    expect(screen.getByText("Refund please")).toBeInTheDocument();
    expect(screen.getByText("Urgent")).toBeInTheDocument();
  });

  it("marks unassigned tickets and VIP customers", () => {
    render(
      <TicketRow
        t={makeSummary({
          assignee_name: null,
          contact: { name: "Bo", email: "b@x.io", vip: true },
        })}
        showStatus={false}
      />,
    );
    expect(screen.getByTitle("Unassigned")).toBeInTheDocument();
    expect(screen.getByText("VIP")).toBeInTheDocument();
  });

  it("shows the assignee when there is one", () => {
    render(<TicketRow t={makeSummary({ assignee_name: "Grace Hopper" })} showStatus={false} />);
    expect(screen.getByTitle("Assigned to Grace Hopper")).toBeInTheDocument();
  });

  it("shows the status badge only when it differs from the active tab", () => {
    const { rerender } = render(<TicketRow t={makeSummary({ status: "pending" })} showStatus />);
    expect(screen.getByText("Pending")).toBeInTheDocument();
    rerender(<TicketRow t={makeSummary({ status: "pending" })} showStatus={false} />);
    expect(screen.queryByText("Pending")).not.toBeInTheDocument();
  });

  it("collapses extra tags into a count", () => {
    render(<TicketRow t={makeSummary({ tags: ["a", "b", "c", "d"] })} showStatus={false} />);
    expect(screen.getByText("+2")).toBeInTheDocument();
  });
});
