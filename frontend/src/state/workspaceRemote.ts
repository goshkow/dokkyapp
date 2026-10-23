/**
 * Рабочая область (дерево файлов и редактор) принадлежит правой панели. Если панель - нативная плитка Liquid Glass,
 * главный вид отправляет ей команды (открыть файл, показать карточку), иначе выполняет их на месте.
 */
import { nativeTiles } from '../lib/glass';
import { toTile } from '../lib/mirror';
import { openFile as realOpenFile, selectInspector as realSelectInspector } from './workspace';

export const openFile = toTile('inspector', 'openFile', realOpenFile, nativeTiles);
export const selectInspector = toTile('inspector', 'selectInspector', realSelectInspector, nativeTiles);
