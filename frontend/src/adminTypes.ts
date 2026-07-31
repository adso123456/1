import type { AssistantAppearance } from './assistantAppearance';

export interface AdminDataSource {
  source_id: string;
  database_type: string;
  display_name: string;
}

export interface AssistantApplicationLink {
  link_id: string;
  name: string;
  url: string;
  open_mode: 'new_tab' | 'same_tab';
  enabled: boolean;
  sort_order: number;
}

export interface AssistantApplicationView extends AssistantAppearance {
  app_id: string;
  name: string;
  enabled: boolean;
  allowed_origins: string[];
  allowed_source_ids: string[];
  application_links: AssistantApplicationLink[];
  show_history: boolean;
  created_at: number;
  updated_at: number;
}

export interface CreateAssistantApplication extends AssistantAppearance {
  app_id: string;
  name: string;
  allowed_origins: string[];
  allowed_source_ids: string[];
  application_links: AssistantApplicationLink[];
  show_history: boolean;
  enabled: boolean;
}

export type UpdateAssistantApplication = Partial<Omit<
  CreateAssistantApplication,
  'app_id' | 'enabled'
>>;
