import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

afterEach(() => cleanup());

class RO { observe() {} unobserve() {} disconnect() {} }
(globalThis as unknown as { ResizeObserver: typeof RO }).ResizeObserver = RO;
if (!window.matchMedia) {
  window.matchMedia = ((q: string) => ({
    matches: false, media: q, addEventListener() {}, removeEventListener() {}, addListener() {},
    removeListener() {}, onchange: null, dispatchEvent: () => false,
  })) as typeof window.matchMedia;
}

// jsdom has no canvas; the hatch image helper must degrade quietly to null.
HTMLCanvasElement.prototype.getContext = (() => null) as unknown as typeof HTMLCanvasElement.prototype.getContext;
