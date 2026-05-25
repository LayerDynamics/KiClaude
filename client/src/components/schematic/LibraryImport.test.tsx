import { act, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { LibraryImportDropZone, useLibraryImport } from "./LibraryImport";

function okFetcher(body: Record<string, unknown>): typeof fetch {
  return vi.fn(async () =>
    new Response(JSON.stringify({ ok: true, ...body }), {
      status: 200,
      headers: { "content-type": "application/json" },
    }),
  ) as unknown as typeof fetch;
}

function symFile(): File {
  return new File(['(kicad_symbol_lib (symbol "X"))'], "Custom.kicad_sym");
}

describe("useLibraryImport (FR-043)", () => {
  it("posts a .kicad_sym as a symbol import", async () => {
    const fetcher = okFetcher({ kind: "symbol", nickname: "Custom", lib_id_prefix: "Custom:" });
    const { result } = renderHook(() => useLibraryImport("p1", { fetcher }));

    await act(async () => {
      await result.current.importFile(symFile());
    });

    expect(result.current.state).toBe("done");
    expect(result.current.result?.nickname).toBe("Custom");
    const [url, init] = (fetcher as unknown as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe("/api/server/project/p1/library/import");
    const sent = JSON.parse((init as RequestInit).body as string);
    expect(sent).toMatchObject({ filename: "Custom.kicad_sym", kind: "symbol" });
    expect(sent.content).toContain("kicad_symbol_lib");
  });

  it("infers footprint kind from .kicad_mod", async () => {
    const fetcher = okFetcher({ kind: "footprint", nickname: "imported", lib_id_prefix: "imported:" });
    const { result } = renderHook(() => useLibraryImport("p1", { fetcher }));
    await act(async () => {
      await result.current.importFile(new File(["(footprint X)"], "MyFP.kicad_mod"));
    });
    const [, init] = (fetcher as unknown as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(JSON.parse((init as RequestInit).body as string).kind).toBe("footprint");
  });

  it("rejects an unsupported extension without calling fetch", async () => {
    const fetcher = okFetcher({});
    const { result } = renderHook(() => useLibraryImport("p1", { fetcher }));
    await act(async () => {
      await result.current.importFile(new File(["x"], "notes.txt"));
    });
    expect(result.current.state).toBe("error");
    expect(result.current.error ?? "").toMatch(/unsupported/i);
    expect((fetcher as unknown as ReturnType<typeof vi.fn>).mock.calls).toHaveLength(0);
  });

  it("surfaces a server error", async () => {
    const fetcher = vi.fn(async () => new Response("nope", { status: 500 })) as unknown as typeof fetch;
    const { result } = renderHook(() => useLibraryImport("p1", { fetcher }));
    await act(async () => {
      await result.current.importFile(symFile());
    });
    expect(result.current.state).toBe("error");
    expect(result.current.error ?? "").toMatch(/HTTP 500/);
  });
});

describe("LibraryImportDropZone", () => {
  it("imports a dropped file and fires onImported", async () => {
    const fetcher = okFetcher({ kind: "symbol", nickname: "Custom", lib_id_prefix: "Custom:" });
    const onImported = vi.fn();
    render(
      <LibraryImportDropZone projectId="p1" fetcher={fetcher} onImported={onImported}>
        <span>drop here</span>
      </LibraryImportDropZone>,
    );
    const zone = screen.getByTestId("library-import-dropzone");
    fireEvent.drop(zone, { dataTransfer: { files: [symFile()] } });

    await waitFor(() => expect(screen.queryByTestId("import-done")).not.toBeNull());
    expect(onImported).toHaveBeenCalledTimes(1);
    expect(onImported.mock.calls[0][0].nickname).toBe("Custom");
  });
});
