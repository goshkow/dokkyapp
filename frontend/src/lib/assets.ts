/** Статические файлы сборки: адрес зависит от того, где приложение раздаёт интерфейс. */
export const asset = (name: string): string => `${import.meta.env.BASE_URL}assets/${name}`;
export const MARK = asset('mark.png');
