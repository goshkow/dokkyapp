/* Действия просмотра файла: избранное, копирование картинки, правка по описанию. */
import { api, cloudPath } from '../lib/api';
import { isImageFile, type CloudFile } from '../lib/cloud';
import { send } from './chat';
import { composer } from './composer';
import { notify } from './overlay';
import { ui } from './ui';
import { closeImage, closeArtifact } from './viewers';

/** Идентификатор файла на сервере Докки (`file_…`). */
export function fileId(file: CloudFile): string | null {
  const id = file.file_id || file.id || '';
  return /^file_[A-Za-z0-9_-]{1,128}$/.test(id) ? id : null;
}

/* избранное */
let favorites: Set<string> | null = null;

export async function loadFavorites(force = false): Promise<Set<string>> {
  if (favorites && !force) return favorites;
  const page = await api<{ items?: Array<{ id: string }> }>(cloudPath('library?tab=favorites&limit=100'));
  favorites = new Set((page.items ?? []).map((item) => item.id));
  return favorites;
}

export async function isFavorite(file: CloudFile): Promise<boolean | null> {
  const id = fileId(file);
  if (!id) return null;
  try {
    return (await loadFavorites(true)).has(id);
  } catch {
    return null;
  }
}

export async function setFavorite(file: CloudFile, next: boolean): Promise<boolean> {
  const id = fileId(file);
  if (!id) throw new Error('Файл недоступен для избранного');
  const saved = await api<{ favorite?: boolean }>(cloudPath(`library/${encodeURIComponent(id)}/favorite`), { method: 'PUT', body: JSON.stringify({ favorite: next }) });
  const value = Boolean(saved.favorite);
  (favorites ??= new Set()).add(id);
  if (!value) favorites.delete(id);
  notify(value ? 'Добавлено в избранное' : 'Убрано из избранного');
  return value;
}

/* копирование картинки */
declare global {
  interface Window {
    koddiCopyImage?: (data: string, event: Event) => Promise<boolean>;
  }
}

/** PNG из любой загруженной картинки: буфер обмена принимает его везде. */
async function toPng(src: string): Promise<Blob> {
  const response = await fetch(src);
  if (!response.ok) throw new Error('Изображение недоступно');
  const bitmap = await createImageBitmap(await response.blob());
  const canvas = document.createElement('canvas');
  canvas.width = bitmap.width;
  canvas.height = bitmap.height;
  canvas.getContext('2d')?.drawImage(bitmap, 0, 0);
  bitmap.close();
  return new Promise((resolve, reject) => canvas.toBlob((blob) => (blob ? resolve(blob) : reject(new Error('Не удалось подготовить изображение'))), 'image/png'));
}

const toBase64 = (blob: Blob): Promise<string> =>
  new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(',')[1] ?? '');
    reader.onerror = () => reject(new Error('Не удалось прочитать изображение'));
    reader.readAsDataURL(blob);
  });

export async function copyImage(src: string, event: Event): Promise<void> {
  try {
    const png = await toPng(src);
    if (window.koddiDesktop) {
      if (typeof window.koddiCopyImage !== 'function' || !(await window.koddiCopyImage(await toBase64(png), event))) throw new Error('clipboard');
    } else {
      await navigator.clipboard.write([new ClipboardItem({ 'image/png': png })]);
    }
    notify('Изображение скопировано');
  } catch {
    notify('Не удалось скопировать изображение', true);
  }
}

/* правка по описанию */
/** Файл уходит новым сообщением чата с описанием правки: так же правят изображения и файлы на сайте. */
export async function editWithPrompt(file: CloudFile, prompt: string, preview: string | null): Promise<boolean> {
  const id = fileId(file);
  const text = prompt.trim();
  if (!id || !text) return false;
  if (ui.get().scope === 'development') {
    notify('Правка по описанию доступна в режиме чата', true);
    return false;
  }
  composer.set({
    text,
    uploads: [],
    attachments: [{ id, name: file.name, original_name: file.name, media_type: file.media_type || (isImageFile(file) ? 'image/png' : undefined), preview: preview ?? undefined }],
  });
  closeImage();
  closeArtifact();
  await send();
  return true;
}
