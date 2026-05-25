import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { SharePage, type ShareMeta } from "./SharePage";

// The read-only board preview registers kicanvas custom elements; stub
// the bridge so tests don't try to load the real WebGL bundle.
vi.mock("../lib/kicanvas-bridge", () => ({
  loadKicanvas: () => Promise.resolve({ alreadyLoaded: true }),
  KICANVAS_SCRIPT_URL: "/vendor/kicanvas.js",
}));

const META: ShareMeta = {
  ok: true,
  read_only: true,
  token: "a".repeat(64),
  project_name: "esp32_c6_rf",
  created_at: "2026-05-25T10:00:00+00:00",
  files: ["esp32_c6_rf.kicad_pro", "esp32_c6_rf.kicad_pcb", "fp-lib-table"],
};

function okFetcher(meta: ShareMeta): typeof fetch {
  return vi.fn(async () =>
    new Response(JSON.stringify(meta), {
      status: 200,
      headers: { "content-type": "application/json" },
    }),
  ) as unknown as typeof fetch;
}

describe("SharePage (FR-080)", () => {
  it("shows an error when no token is present", async () => {
    render(<SharePage token={null} fetcher={okFetcher(META)} />);
    expect(await screen.findByTestId("share-error")).not.toBeNull();
    expect(screen.getByRole("alert").textContent ?? "").toMatch(/no share token/i);
  });

  it("renders read-only metadata + manifest + download links", async () => {
    const fetcher = okFetcher(META);
    render(<SharePage token={META.token} apiBase="/api/server" fetcher={fetcher} />);

    await screen.findByTestId("share-ready");
    expect(screen.getByTestId("share-readonly-badge").textContent ?? "").toMatch(/read-only/i);
    expect(screen.getByRole("heading", { name: "esp32_c6_rf" })).not.toBeNull();
    expect(screen.getByTestId("share-created-at").textContent ?? "").toContain("2026-05-25");

    // File manifest with direct download links to the share file endpoint.
    const list = screen.getByTestId("share-file-list");
    const links = list.querySelectorAll("a");
    expect(links).toHaveLength(3);
    const pcb = Array.from(links).find((a) => a.textContent === "esp32_c6_rf.kicad_pcb");
    expect(pcb?.getAttribute("href")).toBe(
      `/api/server/share/${META.token}/file?path=esp32_c6_rf.kicad_pcb`,
    );
    expect(pcb?.hasAttribute("download")).toBe(true);

    // Fetched the metadata endpoint exactly once with the token.
    expect(fetcher).toHaveBeenCalledWith(`/api/server/share/${META.token}`);
  });

  it("mounts a read-only kicanvas board preview from the .kicad_pcb file url", async () => {
    render(<SharePage token={META.token} fetcher={okFetcher(META)} />);
    await screen.findByTestId("share-ready");
    const source = screen.getByTestId("share-kicanvas-source");
    expect(source.getAttribute("src")).toBe(
      `/api/server/share/${META.token}/file?path=esp32_c6_rf.kicad_pcb`,
    );
    // Read-only: no download control on the embed.
    expect(
      screen.getByTestId("share-kicanvas-embed").getAttribute("controlslist") ?? "",
    ).toContain("nodownload");
  });

  it("omits the board preview when the snapshot has no .kicad_pcb", async () => {
    const noPcb: ShareMeta = { ...META, files: ["esp32_c6_rf.kicad_sch", "fp-lib-table"] };
    render(<SharePage token={META.token} fetcher={okFetcher(noPcb)} />);
    await screen.findByTestId("share-ready");
    expect(screen.queryByTestId("share-board")).toBeNull();
  });

  it("surfaces a 404 as a friendly not-found message", async () => {
    const fetcher = vi.fn(
      async () => new Response("nope", { status: 404 }),
    ) as unknown as typeof fetch;
    render(<SharePage token={META.token} fetcher={fetcher} />);
    expect(await screen.findByTestId("share-error")).not.toBeNull();
    expect(screen.getByRole("alert").textContent ?? "").toMatch(/not found/i);
  });

  it("surfaces a non-404 HTTP error", async () => {
    const fetcher = vi.fn(
      async () => new Response("boom", { status: 500 }),
    ) as unknown as typeof fetch;
    render(<SharePage token={META.token} fetcher={fetcher} />);
    await screen.findByTestId("share-error");
    await waitFor(() =>
      expect(screen.getByRole("alert").textContent ?? "").toMatch(/HTTP 500/),
    );
  });
});
