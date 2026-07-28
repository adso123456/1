export interface AdminDataSource {
  source_id: string;
  database_type: string;
}

export interface AssistantApplicationView {
  app_id: string;
  name: string;
  enabled: boolean;
  secret_mask: string;
  allowed_origins: string[];
  allowed_source_ids: string[];
  token_ttl_seconds: number;
  theme: string;
  logo_url: string;
  welcome: string;
  welcome_description: string;
  show_history: boolean;
  created_at: number;
  updated_at: number;
}

export interface CreateAssistantApplication {
  app_id: string;
  name: string;
  allowed_origins: string[];
  allowed_source_ids: string[];
  token_ttl_seconds: number;
  theme: string;
  logo_url: string;
  welcome: string;
  welcome_description: string;
  show_history: boolean;
  enabled: boolean;
}

export type UpdateAssistantApplication = Omit<
  CreateAssistantApplication,
  'app_id' | 'enabled'
>;

export interface AssistantApplicationSecretResponse
  extends AssistantApplicationView {
  app_secret: string;
}
