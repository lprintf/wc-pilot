import { StrictMode, useEffect, useRef, useState } from "react"
import { createRoot } from "react-dom/client"
import {
  QueryClient,
  QueryClientProvider,
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query"
import { useVirtualizer } from "@tanstack/react-virtual"
import { api, ConversationResponse, formatTime, GraphState, Message, User } from "../shared/api"
import "../shared/styles.css"
import "./style.css"
import { Avatar, Customers } from "./customers"

const client = new QueryClient()

const SENDER_LABEL: Record<string, string> = {
  user: "用户",
  customer: "用户",
  ai: "AI",
  assistant: "AI",
  human_agent: "人工客服",
  system: "系统",
}

const INTENT_LABEL: Record<string, string> = {
  lead_gen: "获客",
  after_sales: "售后",
  business_discovery: "咨询",
  knowledge_qa: "问答",
  cost_feasibility: "评估",
  greeting: "欢迎",
  profile: "个人",
}

/* ------------------------------------------------------------------ */
/*  User list panel (virtual scrolling)                               */
/* ------------------------------------------------------------------ */
function UserList({
  selected,
  onSelect,
}: {
  selected: number | null
  onSelect: (id: number) => void
}) {
  const [search, setSearch] = useState("")
  const query = useInfiniteQuery({
    queryKey: ["admin-users", search],
    initialPageParam: 1,
    queryFn: ({ pageParam }) =>
      api<{ users: User[]; page: number; page_size: number; total: number }>(
        `/api/admin/users?q=${encodeURIComponent(search)}&page=${pageParam}&page_size=100`,
      ),
    getNextPageParam: (page) =>
      page.page * page.page_size < page.total ? page.page + 1 : undefined,
  })

  const parentRef = useRef<HTMLDivElement>(null)
  const users = query.data?.pages.flatMap((p) => p.users) ?? []
  const virtual = useVirtualizer({
    count: users.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 76,
    overscan: 10,
  })
  const rows = virtual.getVirtualItems()

  useEffect(() => {
    const last = rows.at(-1)
    if (last && last.index >= users.length - 10 && query.hasNextPage && !query.isFetchingNextPage) {
      void query.fetchNextPage()
    }
  }, [rows, users.length, query.hasNextPage, query.isFetchingNextPage, query.fetchNextPage])

  return (
    <section className="panel users">
      <header className="panel-head">
        <h1>客服工作台</h1>
        <div className="search">
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="搜索昵称或内部 ID"
          />
          <button className="btn" onClick={() => query.refetch()}>
            刷新
          </button>
        </div>
      </header>
      <div ref={parentRef} className="user-scroll">
        {query.isLoading ? (
          <div className="empty">加载中…</div>
        ) : query.error ? (
          <div className="error">{query.error.message}</div>
        ) : !users.length ? (
          <div className="empty">暂无客户</div>
        ) : (
          <div style={{ height: virtual.getTotalSize(), position: "relative" }}>
            {rows.map((row) => {
              const user = users[row.index]
              return (
                <button
                  key={user.id}
                  className={`user-row${selected === user.id ? " active" : ""}`}
                  style={{
                    position: "absolute",
                    top: 0,
                    left: 0,
                    width: "100%",
                    height: row.size,
                    transform: `translateY(${row.start}px)`,
                  }}
                  onClick={() => onSelect(user.id)}
                >
                  <span className="avatar">
                    {user.nickname.slice(0, 1) || "微"}
                  </span>
                  <span className="user-copy">
                    <strong>{user.nickname || "未命名用户"}</strong>
                    <small>{user.last_message_preview || "暂无消息"}</small>
                  </span>
                  <span className="user-meta">
                    {user.latest_intent && (
                      <span className={`intent-badge intent-${user.latest_intent}`}>
                        {INTENT_LABEL[user.latest_intent] || "?"}
                      </span>
                    )}
                    {formatTime(user.last_active_at)}
                    {user.unread_count > 0 && <b>{user.unread_count}</b>}
                  </span>
                </button>
              )
            })}
          </div>
        )}
      </div>
    </section>
  )
}

/* ------------------------------------------------------------------ */
/*  Conversation pane                                                 */
/* ------------------------------------------------------------------ */
function ConversationPane({
  userId,
  onGraphState,
}: {
  userId: number | null
  onGraphState?: (gs: GraphState | undefined) => void
}) {
  const [content, setContent] = useState("")
  const parentRef = useRef<HTMLDivElement>(null)
  const queryClient = useQueryClient()

  const query = useInfiniteQuery({
    enabled: userId !== null,
    queryKey: ["conversation", userId],
    initialPageParam: null as number | null,
    queryFn: ({ pageParam }) =>
      api<ConversationResponse>(
        `/api/admin/users/${userId}/conversation?limit=80${pageParam ? `&before_id=${pageParam}` : ""}`,
      ),
    getNextPageParam: (page) => (page.has_more ? page.next_before_id : undefined),
  })

  const send = useMutation({
    mutationFn: (retryId?: number) =>
      api(
        `/api/admin/users/${userId}/messages`,
        {
          method: "POST",
          headers: { "content-type": "application/json", "x-requested-with": "XMLHttpRequest" },
          body: JSON.stringify(
            retryId
              ? { retry_message_id: retryId, request_id: crypto.randomUUID() }
              : { content, request_id: crypto.randomUUID() },
          ),
        },
      ),
    onSuccess: () => {
      setContent("")
      queryClient.invalidateQueries({ queryKey: ["conversation", userId] })
      queryClient.invalidateQueries({ queryKey: ["admin-users"] })
    },
  })

  useEffect(() => {
    if (query.data?.pages[0]?.graph_state && onGraphState) {
      onGraphState(query.data.pages[0].graph_state)
    }
  }, [query.data, onGraphState])

  const messages: Message[] = query.data
    ? [...query.data.pages].reverse().flatMap((p) => p.messages)
    : []

  const virtual = useVirtualizer({
    count: messages.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 80,
    overscan: 6,
  })

  useEffect(() => {
    virtual.scrollToIndex(messages.length - 1, { align: "end" })
  }, [messages.length])

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault()
      if (content.trim()) send.mutate()
    }
  }

  if (userId === null) {
    return (
      <section className="panel conversation empty">
        <p className="muted">选择左侧客户查看对话</p>
      </section>
    )
  }

  return (
    <section className="panel conversation">
      <header className="conversation-head">
        <span>客户 {userId}</span>
      </header>

      <div ref={parentRef} className="message-scroll">
        {query.isLoading ? (
          <div className="empty">加载中…</div>
        ) : (
          <div style={{ height: virtual.getTotalSize(), position: "relative" }}>
            {virtual.getVirtualItems().map((row) => {
              const msg = messages[row.index]
              const label = SENDER_LABEL[msg.sender_type] || msg.sender_type
              const isHuman = msg.sender_type === "human_agent"
              const isAi = msg.sender_type === "ai" || msg.sender_type === "assistant"
              const isFailed = msg.send_status === "failed"

              return (
                <div
                  key={msg.id}
                  className={`message ${msg.sender_type} ${isFailed ? "failed" : ""}`}
                  style={{
                    position: "absolute",
                    top: 0,
                    left: 0,
                    width: "100%",
                    height: row.size,
                    transform: `translateY(${row.start}px)`,
                  }}
                >
                  <div className={`bubble ${isAi ? "ai" : isHuman ? "human" : ""}`}>
                    {msg.content}
                    <small>
                      {formatTime(msg.occurred_at)} · {label}
                      {msg.source === "knowledge" && " · 知识库"}
                    </small>
                    {isFailed && (
                      <button
                        className="retry"
                        onClick={() => send.mutate(msg.id)}
                      >
                        重试
                      </button>
                    )}
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>

      <footer className="composer">
        <textarea
          value={content}
          maxLength={2000}
          rows={3}
          disabled={send.isPending}
          placeholder="输入回复，Enter 发送，Shift+Enter 换行"
          onChange={(e) => setContent(e.target.value)}
          onKeyDown={handleKeyDown}
        />
        <div className="composer-foot">
          <span className="muted">
            {send.isPending ? "发送中…" : send.error?.message ?? ""}
          </span>
          <button
            className="btn"
            disabled={!content.trim() || send.isPending}
            onClick={() => send.mutate()}
          >
            发送
          </button>
        </div>
      </footer>
    </section>
  )
}

/* ------------------------------------------------------------------ */
/*  User info sidebar                                                 */
/* ------------------------------------------------------------------ */
function InfoPanel({
  userId,
  graphState,
}: {
  userId: number | null
  graphState?: GraphState
}) {
  const query = useQuery({
    enabled: userId !== null,
    queryKey: ["user", userId],
    queryFn: () =>
      api<{
        user: User & { gender: number; first_seen_at: string; last_seen_at: string }
      }>(`/api/admin/users/${userId}`),
  })

  if (userId === null) {
    return (
      <aside className="panel info empty">
        <p className="muted">选择客户查看详情</p>
      </aside>
    )
  }

  if (query.isLoading) return <aside className="panel info">加载中…</aside>
  if (query.error) return <aside className="panel info error">{query.error.message}</aside>

  const u = query.data!.user

  return (
    <aside className="panel info">
      <div className="large-avatar">{u.nickname.slice(0, 1) || "微"}</div>
      <h2>{u.nickname || "未命名用户"}</h2>
      <dl>
        <dt>内部 ID</dt>
        <dd>{u.id}</dd>
        <dt>首次出现</dt>
        <dd>{formatTime(u.first_seen_at)}</dd>
        <dt>最近活跃</dt>
        <dd>{formatTime(u.last_seen_at)}</dd>
        <dt>性别</dt>
        <dd>{u.gender === 1 ? "男" : u.gender === 2 ? "女" : "未知"}</dd>

        {graphState && Object.keys(graphState).length > 0 && (
          <>
            <dt style={{ marginTop: 20, borderTop: "1px solid #e5e7eb", paddingTop: 12 }}>
              AI 状态
            </dt>
            {graphState.intent && (
              <dd>意图: {INTENT_LABEL[graphState.intent] || graphState.intent}</dd>
            )}
            {graphState.scenario && <dd>场景: {graphState.scenario}</dd>}
            {graphState.conversation_round !== undefined && (
              <dd>对话轮次: {graphState.conversation_round}</dd>
            )}
            {graphState.business_profile &&
              Object.keys(graphState.business_profile).length > 0 && (
                <>
                  {Object.entries(graphState.business_profile).map(([key, value]) => (
                    <dd key={key}>
                      {key}: {value}
                    </dd>
                  ))}
                </>
              )}
          </>
        )}
      </dl>
    </aside>
  )
}

/* ------------------------------------------------------------------ */
/*  Log viewer                                                        */
/* ------------------------------------------------------------------ */
function LogViewer() {
  const [level, setLevel] = useState("INFO")
  const [lines, setLines] = useState(200)
  const [auto, setAuto] = useState(false)

  const query = useQuery({
    queryKey: ["admin-logs", level, lines],
    queryFn: () =>
      api<{ logs: string[] }>(
        `/api/admin/logs?lines=${lines}&level=${level}`,
      ),
    refetchInterval: auto ? 5000 : false,
  })

  return (
    <section className="panel logs-view">
      <header className="panel-head">
        <h1>系统日志</h1>
        <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 8, flexWrap: "wrap" }}>
          <select
            value={level}
            onChange={(e) => setLevel(e.target.value)}
          >
            <option>INFO</option>
            <option>DEBUG</option>
            <option>WARNING</option>
            <option>ERROR</option>
          </select>
          <input
            type="number"
            value={lines}
            onChange={(e) => setLines(Number(e.target.value))}
            min={10}
            max={2000}
            style={{ width: 80 }}
          />
          <button className="secondary-btn" onClick={() => query.refetch()}>
            刷新
          </button>
          <label style={{ fontSize: 12, color: "#667085", display: "flex", alignItems: "center", gap: 4 }}>
            <input
              type="checkbox"
              checked={auto}
              onChange={(e) => setAuto(e.target.checked)}
            />
            自动刷新
          </label>
        </div>
      </header>
      <div className="log-scroll">
        {query.isLoading ? (
          <div className="empty">加载中…</div>
        ) : query.error ? (
          <div className="error">{query.error.message}</div>
        ) : (
          <pre className="log-pre">
            {(query.data?.logs || []).join("\n") || "无匹配日志"}
          </pre>
        )}
      </div>
    </section>
  )
}

/* ------------------------------------------------------------------ */
/*  App shell                                                        */
/* ------------------------------------------------------------------ */
function App() {
  const [selected, setSelected] = useState<number | null>(null)
  const [graphState, setGraphState] = useState<GraphState | undefined>(undefined)
  const [view, setView] = useState<"conversations" | "customers" | "logs">("conversations")

  const openConversation = (id: number) => {
    setSelected(id)
    setView("conversations")
  }

  return (
    <div className="admin-shell">
      <nav className="admin-navigation" aria-label="后台导航">
        <strong>客服后台</strong>
        <button
          aria-current={view === "conversations" ? "page" : undefined}
          onClick={() => setView("conversations")}
        >
          会话工作台
        </button>
        <button
          aria-current={view === "customers" ? "page" : undefined}
          onClick={() => {
            setSelected(null)
            setView("customers")
          }}
        >
          客户管理
        </button>
        <button
          aria-current={view === "logs" ? "page" : undefined}
          onClick={() => setView("logs")}
        >
          系统日志
        </button>
      </nav>

      {view === "logs" ? (
        <LogViewer />
      ) : view === "customers" ? (
        <Customers onOpen={openConversation} />
      ) : (
        <main className="admin-app">
          <UserList selected={selected} onSelect={setSelected} />
          <ConversationPane
            key={selected}
            userId={selected}
            onGraphState={setGraphState}
          />
          <InfoPanel userId={selected} graphState={graphState} />
        </main>
      )}
    </div>
  )
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={client}>
      <App />
    </QueryClientProvider>
  </StrictMode>,
)