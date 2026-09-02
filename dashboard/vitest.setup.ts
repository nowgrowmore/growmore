import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// vitest doesn't auto-cleanup between tests the way Jest + RTL's setup does,
// so without this, DOM from one test's render() leaks into the next test's
// queries (e.g. duplicate `role="switch"` matches).
afterEach(() => {
  cleanup();
});
