export type GraphState = {
  intent?: string;
  scenario?: string;
  business_profile?: Record<string, string>;
  conversation_round?: number;
}
export type User = { id: number; nickname: string; avatar_url: string; last_message_preview: string; last_active_at: string; unread_count: number; latest_intent?: string; conversation_round?: number; scenario?: string }
export type Profile = { id: number; nickname: string; avatar_url: string; gender: number; first_seen_at: string; last_seen_at: string }
export type ConversationResponse = { messages: Message[]; has_more: boolean; next_before_id: number | null; graph_state?: GraphState }
export type Message = { id: number; sender_type: "user" | "customer" | "ai" | "assistant" | "human_agent" | "system"; content: string; occurred_at: string; message_type: string; source: string; send_status: "pending" | "sent" | "failed"; error_message: string | null; client_request_id: string | null }
export async function api<T>(url: string, init?: RequestInit): Promise<T> { const response = await fetch(url, init); const payload = await response.json().catch(() => ({})) as { error?: { message?: string } }; if (!response.ok) throw new Error(payload.error?.message ?? "请求失败"); return payload as T }
export const formatTime = (value: string | number) => new Date(typeof value === "number" ? value * 1000 : value).toLocaleString("zh-CN")
