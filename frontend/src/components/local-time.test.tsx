import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { fmtDateRange, fmtTime } from "@/lib/format";
import { LocalTime, ZoneLabel } from "./local-time";

afterEach(() => {
  vi.restoreAllMocks();
});

const inZone = (timeZone: string) => {
  const resolved = Intl.DateTimeFormat.prototype.resolvedOptions;
  vi.spyOn(Intl.DateTimeFormat.prototype, "resolvedOptions").mockImplementation(function (
    this: Intl.DateTimeFormat,
  ) {
    return { ...resolved.call(this), timeZone };
  });
};

describe("viewer time zone", () => {
  it("formats instants in the requested zone", () => {
    expect(fmtTime("2026-09-10T13:00:00Z")).toBe("10 SEPT, 13:00");
    expect(fmtTime("2026-09-10T13:00:00Z", "Asia/Kolkata")).toBe("10 SEPT, 18:30");
    expect(fmtTime("2026-09-10T13:00:00Z", "America/New_York")).toBe("10 SEPT, 09:00");
    expect(fmtTime(null, "Asia/Kolkata")).toBe("TIME TBC");
    expect(
      fmtDateRange(
        new Date("2026-09-04T23:30:00Z"),
        new Date("2026-09-06T13:00:00Z"),
        "Asia/Tokyo",
      ),
    ).toBe("05–06 SEPT 2026");
  });

  it("renders times and the zone label in the browser's own zone", () => {
    inZone("America/New_York");
    render(
      <>
        <LocalTime value="2026-09-10T13:00:00Z" />
        <ZoneLabel full />
      </>,
    );
    expect(screen.getByText("10 SEPT, 09:00")).toHaveAttribute("dateTime", "2026-09-10T13:00:00Z");
    expect(screen.getByText("All times America/New_York")).toBeInTheDocument();
  });
});
