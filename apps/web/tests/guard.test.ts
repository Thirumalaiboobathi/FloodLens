import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const SRC = join(__dirname, "..", "src");
const files = (dir: string): string[] =>
  readdirSync(dir).flatMap((n) => {
    const p = join(dir, n);
    return statSync(p).isDirectory() ? files(p) : [p];
  });
const sources = files(SRC).filter((p) => /\.(ts|tsx|css|html)$/.test(p));

describe("frontend guardrails", () => {
  it("the delivered daily error field never appears in frontend source", () => {
    const hits = sources.filter((p) => /random.?error/i.test(readFileSync(p, "utf8")));
    expect(hits).toEqual([]);
  });
  it("no AWS credentials, Earthdata tokens or secrets are referenced", () => {
    const hits = sources.filter((p) => /EARTHDATA|AWS_SECRET|AWS_ACCESS|accessKeyId|secretAccessKey|Bearer /.test(readFileSync(p, "utf8")));
    expect(hits).toEqual([]);
  });
  it("rainfall is never called flood depth in user-facing text", () => {
    const hits = sources.filter((p) => /rainfall[^.\n]{0,40}flood depth|flood depth[^.\n]{0,40}rainfall/i.test(readFileSync(p, "utf8").replace(/not flood depth|never flood depth|nor flood depth/gi, "")));
    expect(hits).toEqual([]);
  });
  it("the app does not claim live or real-time rainfall", () => {
    const text = sources.map((p) => readFileSync(p, "utf8")).join("\n");
    expect(text).not.toMatch(/live rainfall|real-time rainfall|realtime rainfall/i);
  });
  it("only same-origin API paths are fetched (no absolute third-party URLs in the client)", () => {
    const client = readFileSync(join(SRC, "api", "client.ts"), "utf8");
    expect(client).not.toMatch(/https?:\/\//);
  });
});
