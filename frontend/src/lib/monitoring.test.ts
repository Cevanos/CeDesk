import { afterEach, describe, expect, it, vi } from "vitest";

import { reportError, setErrorReporter, setEventSink, trackEvent } from "@/lib/monitoring";

afterEach(() => {
  setErrorReporter(null);
  setEventSink(null);
});

describe("monitoring", () => {
  it("routes errors to the configured reporter", () => {
    const fn = vi.fn();
    setErrorReporter(fn);
    const err = new Error("x");
    reportError(err, { scope: "t" });
    expect(fn).toHaveBeenCalledWith(err, { scope: "t" });
  });

  it("never throws, even if the reporter does", () => {
    setErrorReporter(() => {
      throw new Error("reporter down");
    });
    expect(() => reportError(new Error("x"))).not.toThrow();
  });

  it("falls back to the console so errors are not swallowed", () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    reportError(new Error("visible"));
    expect(spy).toHaveBeenCalled();
  });

  it("sends events to the sink and tolerates a failing one", () => {
    const fn = vi.fn();
    setEventSink(fn);
    trackEvent("web-vital", { name: "LCP" });
    expect(fn).toHaveBeenCalledWith("web-vital", { name: "LCP" });
    setEventSink(() => {
      throw new Error("down");
    });
    expect(() => trackEvent("x")).not.toThrow();
  });
});
