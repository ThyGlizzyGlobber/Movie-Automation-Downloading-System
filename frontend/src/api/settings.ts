import { putJson, request } from './client'
import type {
  PipelineSettings,
  PipelineSettingsBody,
  RetentionSettings,
  TvScheduleSettings,
  TvScheduleSettingsBody,
} from '../types/settings'

export function getRetention() {
  return request<RetentionSettings>('/api/settings/retention')
}

export function setRetention(days: number | null) {
  return putJson<RetentionSettings>('/api/settings/retention', { days })
}

export function getPipelineSettings() {
  return request<PipelineSettings>('/api/settings/pipeline')
}

export function setPipelineSettings(body: PipelineSettingsBody) {
  return putJson<PipelineSettings>('/api/settings/pipeline', body)
}

export function getTvSettings() {
  return request<TvScheduleSettings>('/api/settings/tv')
}

export function setTvSettings(body: TvScheduleSettingsBody) {
  return putJson<TvScheduleSettings>('/api/settings/tv', body)
}
