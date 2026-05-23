/**
 * `selectionStore` — what's currently selected in the PCB / schematic
 * viewport. Single-source-of-truth so the property panel, the
 * highlight overlay, and any future copy/paste action all read from
 * the same set.
 */

import { create } from "zustand";
import { devtools } from "zustand/middleware";

import type { KcirSelectionRef } from "./kcirStore";

interface SelectionState {
  selected: KcirSelectionRef[];
  hovered: KcirSelectionRef | null;
  select: (refs: KcirSelectionRef[]) => void;
  toggle: (ref: KcirSelectionRef) => void;
  setHovered: (ref: KcirSelectionRef | null) => void;
  clear: () => void;
}

export const useSelectionStore = create<SelectionState>()(
  devtools(
    (set) => ({
      selected: [],
      hovered: null,
      select(refs) {
        set(() => ({ selected: refs }));
      },
      toggle(ref) {
        set((state) => {
          const idx = state.selected.findIndex(
            (s) => s.kind === ref.kind && s.uuid === ref.uuid,
          );
          if (idx >= 0) {
            const next = state.selected.slice();
            next.splice(idx, 1);
            return { selected: next };
          }
          return { selected: [...state.selected, ref] };
        });
      },
      setHovered(ref) {
        set(() => ({ hovered: ref }));
      },
      clear() {
        set(() => ({ selected: [], hovered: null }));
      },
    }),
    { name: "selectionStore" },
  ),
);
