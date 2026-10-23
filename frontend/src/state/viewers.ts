import type { CloudFile, CloudSource } from '../lib/cloud';
import { createStore, useStore } from '../lib/store';

/** Окна просмотра: источники утверждения, изображение, файл ответа. */
export interface SourcePopoverState {
  sources: CloudSource[];
  anchor: HTMLElement;
}

export interface ImageViewerState {
  file: CloudFile;
  /** Положение миниатюры: изображение вылетает из неё и в неё возвращается. */
  from: DOMRect | null;
  src: string;
}

interface ViewersState {
  sources: SourcePopoverState | null;
  image: ImageViewerState | null;
  artifact: CloudFile | null;
}

export const viewers = createStore<ViewersState>({ sources: null, image: null, artifact: null });
export const useViewers = <S,>(selector: (state: ViewersState) => S): S => useStore(viewers, selector);

export const openSources = (sources: CloudSource[], anchor: HTMLElement): void => viewers.set({ sources: { sources, anchor } });
export const closeSources = (): void => viewers.set({ sources: null });
export const openImage = (file: CloudFile, src: string, origin: HTMLElement | null): void =>
  viewers.set({ image: { file, src, from: origin?.getBoundingClientRect() ?? null } });
export const closeImage = (): void => viewers.set({ image: null });
export const openArtifact = (file: CloudFile): void => viewers.set({ artifact: file });
export const closeArtifact = (): void => viewers.set({ artifact: null });

/** Закрыть всё при смене экрана. */
export const closeViewers = (): void => viewers.set({ sources: null, image: null, artifact: null });
