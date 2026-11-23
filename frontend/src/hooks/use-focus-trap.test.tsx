import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useRef, useState } from "react";
import { describe, expect, it } from "vitest";

import { useFocusTrap } from "@/hooks/use-focus-trap";

function Harness() {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useFocusTrap(ref, open);
  return (
    <>
      <button onClick={() => setOpen(true)}>opener</button>
      <button>outside</button>
      {open && (
        <div ref={ref} tabIndex={-1} role="dialog" aria-label="trap">
          <button>first</button>
          <button>last</button>
          <button onClick={() => setOpen(false)}>close</button>
        </div>
      )}
    </>
  );
}

describe("useFocusTrap", () => {
  it("keeps Tab inside, wrapping in both directions", async () => {
    render(<Harness />);
    await userEvent.click(screen.getByText("opener"));
    screen.getByText("close").focus();
    await userEvent.tab();
    expect(screen.getByText("first")).toHaveFocus();
    await userEvent.tab({ shift: true });
    expect(screen.getByText("close")).toHaveFocus();
  });

  it("returns focus to the opener when it closes", async () => {
    render(<Harness />);
    const opener = screen.getByText("opener");
    await userEvent.click(opener);
    await userEvent.click(screen.getByText("close"));
    expect(opener).toHaveFocus();
  });
});
