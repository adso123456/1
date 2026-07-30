import type { AssistantAppearance } from './assistantAppearance';

export interface AdminDataSource {
  source_id: string;
  database_type: string;
  display_name: string;
}

export interface AssistantApplicationView extends AssistantAppearance {
  app_id: string;
  name: string;
  enabled: boolean;
  secret_mask: string;
  allowed_origins: string[];
  allowed_source_ids: string[];
  token_ttl_seconds: number;
  show_history: boolean;
  created_at: number;
  updated_at: number;
}

export interface CreateAssistantApplication extends AssistantAppearance {
  app_id: string;
  name: string;
  allowed_origins: string[];
  allowed_source_ids: string[];
  token_ttl_seconds: number;
  show_history: boolean;
  enabled: boolean;
}

export type UpdateAssistantApplication = Partial<Omit<
  CreateAssistantApplication,
  'app_id' | 'enabled'
>>;

export interface AssistantApplicationSecretResponse
  extends AssistantApplicationView {
  app_secret: string;
}
