import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ErrorState } from "@/components/feedback/error-state";
import * as monitoring from "@/lib/monitoring";
import { renderWithProviders } from "@/test/render";

describe("ErrorState", () => {
  it("reports the error once with its scope and lets the user retry", async () => {
    const spy = vi.spyOn(monitoring, "reportError").mockImplementation(() => {});
    const reset = vi.fn();
    const error = Object.assign(new Error("boom"), { digest: "d-123" });

    renderWithProviders(<ErrorState error={error} reset={reset} scope="app" showHome />);

    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(screen.getByText(/d-123/)).toBeInTheDocument();
    expect(spy).toHaveBeenCalledWith(error, { scope: "app", digest: "d-123" });

    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(reset).toHaveBeenCalled();
    expect(screen.getByRole("link", { name: "Back to tickets" })).toHaveAttribute(
      "href",
      "/tickets",
    );
  });
});
