import { describe, expect, it } from "vitest";
import { chunkText } from "./chunking";

describe("chunkText", () => {
  it("normalizes line endings and preserves every short paragraph", () => {
    const chunks = chunkText("  First paragraph.\r\n\r\nSecond paragraph.  ");

    expect(chunks).toEqual([{ text: "First paragraph.\n\nSecond paragraph.", index: 0 }]);
  });

  it("splits long text with overlap and sequential indexes", () => {
    const text = "a".repeat(1_100);
    const chunks = chunkText(text);

    expect(chunks.map(({ index }) => index)).toEqual([0, 1, 2]);
    expect(chunks[0].text.slice(-80)).toBe(chunks[1].text.slice(0, 80));
    expect(chunks.map(({ text: part }) => part.length)).toEqual([450, 450, 328]);
  });

  it("returns no chunks for empty input and removes null characters", () => {
    expect(chunkText(" \n \u0000 ")).toEqual([]);
    expect(chunkText("left\u0000right")[0].text).toBe("leftright");
  });
});
