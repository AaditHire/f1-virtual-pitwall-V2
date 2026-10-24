import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { EditorialNews } from "./editorial-news";

describe("EditorialNews", () => {
  it("shows feed images over the stock artwork and falls back when an article has none", () => {
    const { container } = render(
      <EditorialNews
        compact
        articles={[
          {
            headline: "With image",
            source: "Test",
            url: "https://example.com/a",
            image_url: "https://img.example.com/a.jpg",
          },
          { headline: "Without image", source: "Test", url: "https://example.com/b" },
        ]}
      />,
    );
    const [first, second] = Array.from(container.querySelectorAll<HTMLElement>(".story-image")).map(
      (node) => node.style.backgroundImage,
    );
    expect(first).toContain("https://img.example.com/a.jpg");
    expect(first).toContain("/assets/vpw-editorial-architecture.png");
    expect(second).not.toContain("example.com");
    expect(second).toContain("/assets/vpw-editorial-tyre.png");
  });
});
