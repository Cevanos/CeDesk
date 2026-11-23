import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Markdown } from "@/components/public/markdown";

describe("Markdown", () => {
  it("shows raw HTML as text and never creates the elements", () => {
    const { container } = render(
      <Markdown source={'Hello <img src=x onerror="alert(1)"> <script>alert(2)</script>'} />,
    );
    expect(container.querySelector("img, script")).toBeNull();
    expect(container).toHaveTextContent("<script>alert(2)</script>");
  });

  it("keeps http(s) and mailto links, with safe rel", () => {
    render(<Markdown source="[docs](https://example.com/a) and [mail](mailto:a@b.c)" />);
    const link = screen.getByRole("link", { name: "docs" });
    expect(link).toHaveAttribute("href", "https://example.com/a");
    expect(link).toHaveAttribute("rel", expect.stringContaining("noopener"));
    expect(screen.getByRole("link", { name: "mail" })).toHaveAttribute("href", "mailto:a@b.c");
  });

  it.each([["java", "script:void0"].join(""), "data:text/html,x", "/relative", "//evil.test"])(
    "does not turn %s into a link",
    (href) => {
      render(<Markdown source={`[click](${href})`} />);
      expect(screen.queryByRole("link")).toBeNull();
      expect(screen.getByText("click")).toBeInTheDocument();
    },
  );

  it("renders headings, lists, emphasis and code", () => {
    render(<Markdown source={"# Title\n\n- one\n- **two**\n\n1. a\n2. b\n\n`x`"} />);
    expect(screen.getByRole("heading", { name: "Title" })).toBeInTheDocument();
    expect(screen.getAllByRole("listitem")).toHaveLength(4);
    expect(screen.getByText("two").tagName).toBe("STRONG");
    expect(screen.getByText("x").tagName).toBe("CODE");
  });
});
