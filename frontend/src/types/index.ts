// --- SSE Event Types ---

export interface SSEEvent {
  event?: string;
  data: string;
}

export interface AgentTurnStartEvent {
  agent: string;
  input: string;
}

export interface InferenceStartEvent {
  model: string;
  engine: string;
  turn: number;
}

export interface InferenceEndEvent {
  model: string;
  engine: string;
  turn: number;
}

export interface ToolCallStartEvent {
  tool: string;
  arguments: string;
}

export interface ToolCallEndEvent {
  tool: string;
  success: boolean;
  latency: number;
}

export interface PlanEvent {
  items: Array<{ id?: string; title: string; status: 'pending' | 'in_progress' | 'completed' }>;
}

export interface ThinkingDeltaEvent {
  text: string;
}

export interface FileEditEvent {
  edit_id: string;
  path: string;
  diff: string;
  added: number;
  removed: number;
}

export interface EscalationEvent {
  from: string;
  to: string;
  reason: string;
  score?: number;
}

export interface RoutingEvent {
  brain: string;
  model: string;
  lane?: string;
  health?: string;
}

export interface CitationEvent {
  ref: string;
  url: string;
  title?: string;
}

export interface VerificationEvent {
  cmd: string;
  passed: boolean;
  output?: string;
}

// --- Chat Types ---

export interface ToolCallInfo {
  id: string;
  tool: string;
  arguments: string;
  status: 'running' | 'success' | 'error';
  result?: string;
  latency?: number;
}

export interface PlanItem {
  id: string;
  title: string;
  status: 'pending' | 'in_progress' | 'completed';
}

export interface RoutingInfo {
  brain: string;
  model: string;
  lane?: string;
  health?: string;
}

export type ActivityItem =
  | { kind: 'tool'; id: string; tool: string; arguments: string; status: 'running' | 'success' | 'error'; result?: string; latency?: number }
  | { kind: 'thinking'; text: string }
  | { kind: 'plan'; items: PlanItem[] }
  | { kind: 'file_edit'; editId: string; path: string; diff: string; added: number; removed: number }
  | { kind: 'escalation'; from: string; to: string; reason: string; score?: number }
  | { kind: 'routing'; brain: string; model: string; lane?: string; health?: string }
  | { kind: 'citation'; ref: string; url: string; title?: string }
  | { kind: 'verification'; cmd: string; passed: boolean; output?: string };

export interface TokenUsage {
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
}

export interface MessageTelemetry {
  engine?: string;
  model_id?: string;
  tokens_per_sec?: number;
  ttft_ms?: number;
  total_ms?: number;
  complexity_score?: number;
  complexity_tier?: string;
  suggested_max_tokens?: number;
  // Real llama.cpp timings from the qwen finish chunk (see qwen_timings.py).
  decode_tok_s?: number;
  prefill_tok_s?: number;
  accept_rate?: number | null;
  predicted_n?: number;
}

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  timestamp: number;
  toolCalls?: ToolCallInfo[];
  activity?: ActivityItem[];
  plan?: PlanItem[];
  usage?: TokenUsage;
  telemetry?: MessageTelemetry;
  audio?: { url: string };
}

export interface Conversation {
  id: string;
  title: string;
  createdAt: number;
  updatedAt: number;
  model: string;
  messages: ChatMessage[];
}

export interface ConversationStore {
  version: 1;
  conversations: Record<string, Conversation>;
  activeId: string | null;
}

// --- Stream State ---

export interface StreamState {
  isStreaming: boolean;
  phase: string;
  elapsedMs: number;
  activeToolCalls: ToolCallInfo[];
  content: string;
  tokens: number; // live (approx) output-token count while generating
  thinking: string; // live reasoning text (Phase 3 renders it)
  activity: ActivityItem[]; // live ordered timeline for the streaming message
  plan: PlanItem[]; // live todo list
  routing?: RoutingInfo; // which brain is answering (Phase 4 renders it)
}

// --- API Types ---

export interface ModelInfo {
  id: string;
  object: string;
  created: number;
  owned_by: string;
}

export interface ProviderSavings {
  provider: string;
  label: string;
  input_cost: number;
  output_cost: number;
  total_cost: number;
  energy_wh: number;
  energy_joules: number;
  flops: number;
}

export interface SavingsData {
  total_calls: number;
  total_prompt_tokens: number;
  total_completion_tokens: number;
  total_tokens: number;
  local_cost: number;
  per_provider: ProviderSavings[];
  token_counting_version?: number;
}

export interface ServerInfo {
  model: string;
  agent: string | null;
  engine: string;
}

// --- Log Types ---

export interface LogEntry {
  timestamp: number;
  level: 'info' | 'warn' | 'error';
  category: 'server' | 'model' | 'chat' | 'tool';
  message: string;
}
