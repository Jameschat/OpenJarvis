export interface StudioProject {
  id: string;
  title?: string;
  repo_root?: string;
  vault_project?: string;
}

export interface StudioMessage {
  id?: string;
  role: 'operator' | 'jarvis' | 'system' | string;
  content: string;
  created_at?: string;
  run_id?: string;
}

export interface StudioChat {
  id: string;
  project_id?: string;
  title?: string;
  status?: string;
  messages?: StudioMessage[];
  updated_at?: string;
}

export interface StudioRunEvent {
  type: string;
  message?: string;
  data?: Record<string, unknown>;
  ts?: string;
}

export interface StudioRun {
  id: string;
  project_id?: string;
  chat_id?: string;
  prompt?: string;
  status?: string;
  workflow?: string;
  created_at?: string;
  updated_at?: string;
  events?: StudioRunEvent[];
  tasks?: string[];
  task_details?: Array<Record<string, unknown>>;
  progress_summary?: string;
  selected_skills?: string[];
  ecc_lite_skills?: string[];
  ecc_command?: string;
  outputs?: Array<Record<string, unknown>>;
  file_activity?: Array<Record<string, unknown>>;
}

export interface StudioAgent {
  id?: string;
  name?: string;
  status?: string;
  provider?: string;
  department?: string;
}

export interface StudioQwenProfile {
  active?: 'fast' | 'quality' | 'remote' | string;
  profiles?: Record<string, { label?: string; model?: string; summary?: string }>;
}

export interface StudioRuntimeLane {
  id?: string;
  alias?: string;
  label?: string;
  online?: boolean;
  active?: boolean;
  latest_tok_s?: number;
  status?: string;
  role?: string;
  host?: string;
  port?: number;
  context_tokens?: number;
  verdict?: string;
  notes?: string;
  benchmark?: Record<string, unknown>;
}

export interface StudioState {
  ok?: boolean;
  projects?: StudioProject[];
  project_id?: string;
  chat_id?: string;
  chats?: StudioChat[];
  runs?: StudioRun[];
  agents?: StudioAgent[];
  plugins?: Array<Record<string, unknown>>;
  automations?: Array<Record<string, unknown>>;
  qwen_profile?: StudioQwenProfile;
  qwen_runtime?: {
    lanes?: StudioRuntimeLane[];
    active?: string;
    active_lane?: string;
    active_alias?: string;
    active_online?: boolean;
    promotion_verdict?: string;
  };
  runtime_health?: {
    ok?: boolean;
    summary?: string;
    services?: Array<Record<string, unknown>>;
    checked_at?: string;
  };
  preview?: Record<string, unknown>;
  system?: Record<string, unknown>;
  provider?: string;
  approved?: boolean;
}
