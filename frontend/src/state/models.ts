import type { CloudModel } from '../lib/types';
import { composer } from './composer';
import { data } from './data';

/** Режимы Докки, доступные для чата или поиска. */
export function cloudModels(scope: string): CloudModel[] {
  return (data.get().account?.bootstrap?.models ?? []).filter((model) => model.roles?.includes(scope));
}

/** Выбранный режим: чата, затем запомненный для режима, затем по умолчанию из настроек аккаунта. */
export function selectedCloudModel(scope: string, chatModel?: string): string {
  const models = cloudModels(scope);
  const preferred = chatModel || composer.get().cloudModel[scope] || (data.get().account?.bootstrap?.preferences as { default_model?: string } | undefined)?.default_model;
  return models.some((model) => model.id === preferred && model.available) ? (preferred as string) : (models.find((model) => model.available)?.id ?? '');
}
