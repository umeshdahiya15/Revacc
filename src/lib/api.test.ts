import { afterEach, describe, expect, it } from "vitest";
import { normalizeApiBaseUrl, wsUrl } from "./api";

describe("API base URL normalization", () => {
  const originalApiUrl = process.env.NEXT_PUBLIC_API_URL;

  afterEach(() => {
    if (originalApiUrl === undefined) delete process.env.NEXT_PUBLIC_API_URL;
    else process.env.NEXT_PUBLIC_API_URL = originalApiUrl;
    delete (globalThis as { window?: unknown }).window;
  });

  it("adds HTTPS to a deployed hostname without a scheme", () => {
    expect(normalizeApiBaseUrl("revacc-production.up.railway.app")).toBe(
      "https://revacc-production.up.railway.app",
    );
  });

  it("keeps intentional localhost HTTP development URLs on HTTP", () => {
    expect(normalizeApiBaseUrl("localhost:8000")).toBe("http://localhost:8000");
    expect(normalizeApiBaseUrl("127.0.0.1:8000/")).toBe("http://127.0.0.1:8000");
  });

  it("normalizes the Settings localStorage value before deriving WebSocket URLs", () => {
    process.env.NEXT_PUBLIC_API_URL = "";
    (globalThis as { window?: unknown }).window = {
      localStorage: {
        getItem: () => JSON.stringify({ apiUrl: "revacc-production.up.railway.app" }),
      },
    };

    expect(wsUrl("job-123")).toBe(
      "wss://revacc-production.up.railway.app/ws/pipeline/job-123",
    );
  });
});
