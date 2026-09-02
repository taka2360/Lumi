/**
 * How a download size is worded. **The point is the unit switch**: the things Lumi fetches
 * span three orders of magnitude, and showing them all in GB hides the difference.
 */

import { describe, expect, it } from "vitest";

import { formatGigabytes, formatSize } from "./format";

describe("formatSize", () => {
  it("uses gigabytes at a gigabyte and above", () => {
    expect(formatSize(6_600_000_000, "en")).toBe("6.6 GB");
    expect(formatSize(1_000_000_000, "en")).toBe("1.0 GB");
  });

  it("uses megabytes below that", () => {
    // 196 MB next to 6.6 GB is the case this exists for: "0.2 GB" would flatten it.
    expect(formatSize(196_000_000, "en")).toBe("196 MB");
    expect(formatSize(999_000_000, "en")).toBe("999 MB");
  });

  it("translates the unit", () => {
    expect(formatSize(6_600_000_000, "ja")).toContain("6.6");
    expect(formatSize(196_000_000, "ja")).toContain("196");
  });
});

describe("formatGigabytes", () => {
  it("always shows one decimal place, so sizes line up in a column", () => {
    expect(formatGigabytes(2_000_000_000, "en")).toBe("2.0 GB");
    expect(formatGigabytes(2_060_000_000, "en")).toBe("2.1 GB");
  });
});
