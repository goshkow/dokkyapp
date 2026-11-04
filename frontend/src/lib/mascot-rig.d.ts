/** Маскот сайта: lib/mascot-rig.js - копия assets/mascot-rig.js, при запуске сам кладёт api в window.DokkyMascot. */
export interface MascotRig {
  autoplay(pause: number): void;
  destroy(): void;
}
export interface MascotApi {
  create(host: HTMLElement, options: { followPointer?: boolean; pauseOffscreen?: boolean; reduced?: boolean }): MascotRig;
}
declare global {
  interface Window { DokkyMascot?: MascotApi }
}
