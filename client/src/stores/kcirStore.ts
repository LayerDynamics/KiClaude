/**
 * `kcirStore` — derived KCIR slices that change at editor-event
 * frequency (footprint moves, track edits). Kept separate from
 * [`projectStore`](./projectStore.ts) so per-edit re-renders don't
 * thrash subscribers of the project-level slice.
 */

import { create } from "zustand";
import { devtools } from "zustand/middleware";

import type { KcirFootprintInstance, KcirTrack } from "./projectStore";

export interface KcirSelectionRef {
  kind: "footprint" | "track" | "via" | "zone";
  uuid: string;
}

interface KcirState {
  footprints: KcirFootprintInstance[];
  tracks: KcirTrack[];
  dirty: boolean;
  setFootprints: (footprints: KcirFootprintInstance[]) => void;
  setTracks: (tracks: KcirTrack[]) => void;
  upsertFootprint: (footprint: KcirFootprintInstance) => void;
  removeByUuid: (uuid: string) => void;
  markClean: () => void;
}

export const useKcirStore = create<KcirState>()(
  devtools(
    (set) => ({
      footprints: [],
      tracks: [],
      dirty: false,
      setFootprints(footprints) {
        set(() => ({ footprints, dirty: true }));
      },
      setTracks(tracks) {
        set(() => ({ tracks, dirty: true }));
      },
      upsertFootprint(footprint) {
        set((state) => {
          const others = state.footprints.filter((f) => f.uuid !== footprint.uuid);
          return { footprints: [...others, footprint], dirty: true };
        });
      },
      removeByUuid(uuid) {
        set((state) => ({
          footprints: state.footprints.filter((f) => f.uuid !== uuid),
          tracks: state.tracks.filter((t) => t.uuid !== uuid),
          dirty: true,
        }));
      },
      markClean() {
        set(() => ({ dirty: false }));
      },
    }),
    { name: "kcirStore" },
  ),
);
