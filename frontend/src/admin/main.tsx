import { StrictMode, useEffect, useRef, useState } from "react"
import { createRoot } from "react-dom/client"
import { QueryClient, QueryClientProvider, useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useVirtualizer } from "@tanstack/react-virtual"
import { api, ConversationResponse, formatTime, GraphState, Message, User } from "../shared/api"
import "../shared/styles.css"
import "./style.css"
import { Avatar, Customers } from "./customers"

const client = new QueryClient()
const senderLabel: Record<string, string> = { user: "用户", customer: "用户", ai: "AI", assistant: "AI", human_agent: "人工客服", system: "系统" }

function Users({ selected, onSelect }: { selected: number | null; onSelect: (id: number) => void }) {
  const [search, setSearch] = useState("")
  const query = useInfiniteQuery({ queryKey: ["admin-users", search], initialPageParam: 1, queryFn: ({pageParam}) => api<{ users: User[]; page: number; page_size: number; total: number }>(`/api/admin/users?q=${encodeURIComponent(search)}&page=${pageParam}&page_size=100`), getNextPageParam: page => page.page * page.page_size < page.total ? page.page + 1 : undefined })
  const parent = useRef<HTMLDivElement>(null)
  const users = query.data?.pages.flatMap(page => page.users) ?? []
  const virtual = useVirtualizer({ count: users.length, getScrollElement: () => parent.current, estimateSize: () => 76, overscan: 10 })
  const virtualRows = virtual.getVirtualItems()
  useEffect(() => { const last = virtualRows.at(-1); if (last && last.index >= users.length - 10 && query.hasNextPage && !query.isFetchingNextPage) void query.fetchNextPage() }, [virtualRows, users.length, query.hasNextPage, query.isFetchingNextPage, query.fetchNextPage])
  return <section className="panel users"><header className="panel-head"><h1>客服后台</h1><div className="search"><input value={search} onChange={e => setSearch(e.target.value)} placeholder="昵称或内部用户 ID"/><button className="btn" onClick={() => query.refetch()}>刷新</button></div></header><div ref={parent} className="user-scroll">{query.isLoading ? <div className="empty">加载中…</div> : query.error ? <div className="error">{query.error.message}</div> : !users.length ? <div className="empty">暂无用户</div> : <div style={{height: virtual.getTotalSize(),position:"relative"}}>{virtual.getVirtualItems().map(row => { const user=users[row.index]; return <button key={user.id} className={`user-row ${selected===user.id?"active":""}`} style={{position:"absolute",top:0,left:0,width:"100%",height:row.size,transform:`translateY(${row.start}px)`}} onClick={() => onSelect(user.id)}><span className="avatar">{user.nickname.slice(0,1)||"微"}</span><span className="user-copy"><strong>{user.nickname||"未命名用户"}</strong><small>{user.last_message_preview||"暂无消息"}</small></span><span className="user-meta">{user.latest_intent&&<span className={`intent-badge intent-${user.latest_intent}`}>{user.latest_intent==="lead_gen"?"获客":user.latest_intent==="after_sales"?"售后":user.latest_intent==="business_discovery"?"咨询":user.latest_intent==="knowledge_qa"?"问答":user.latest_intent==="cost_feasibility"?"评估":user.latest_intent==="greeting"?"欢迎":user.latest_intent==="profile"?"个人":"?"}</span>}{formatTime(user.last_active_at)}{user.unread_count>0&&<b>{user.unread_count}</b>}</span></button>})}</div>}</div></section>
}

function Conversation({ userId, onGraphState }: { userId: number | null; onGraphState?: (gs: GraphState | undefined) => void }) {
  const [content, setContent] = useState("")
  const parent = useRef<HTMLDivElement>(null)
  const queryClient = useQueryClient()
  const query = useInfiniteQuery({ enabled: userId!==null, queryKey: ["conversation", userId], initialPageParam: null as number|null, queryFn: ({pageParam}) => api<ConversationResponse>(`/api/admin/users/${userId}/conversation?limit=80${pageParam?`&before_id=${pageParam}`:""}`), getNextPageParam: page => page.has_more ? page.next_before_id : undefined })
  const send = useMutation({ mutationFn: (retryId?: number) => api(`/api/admin/users/${userId}/messages`, { method:"POST", headers:{"content-type":"application/json","x-requested-with":"XMLHttpRequest"}, body:JSON.stringify(retryId?{retry_message_id:retryId,request_id:crypto.randomUUID()}:{content,request_id:crypto.randomUUID()}) }), onSuccess: () => { setContent(""); queryClient.invalidateQueries({queryKey:["conversation",userId]}); queryClient.invalidateQueries({queryKey:["admin-users"]}) } })
  useEffect(()=>{if(query.data?.pages[0]?.graph_state && onGraphState) onGraphState(query.data.pages[0].graph_state)},[query.data, onGraphState])
  const messages=query.data ? [...query.data.pages].reverse().flatMap(page => page.messages) : []
  const virtual=useVirtualizer({count:messages.length,getScrollElement:()=>parent.current,estimateSize:()=>78,overscan:8})
  if (userId===null) return <section className="panel conversation empty">选择一个用户开始会话</section>
  return <section className="panel conversation"><header className="conversation-head">用户 #{userId}</header><div ref={parent} className="message-scroll">{query.isLoading?<div className="empty">加载中…</div>:query.error?<div className="error">{query.error.message}</div>:<>{query.hasNextPage&&<button className="btn older" disabled={query.isFetchingNextPage} onClick={()=>query.fetchNextPage()}>{query.isFetchingNextPage?"加载中…":"加载更早消息"}</button>}<div style={{height:virtual.getTotalSize(),position:"relative"}}>{virtual.getVirtualItems().map(row=>{const message=messages[row.index];return <article key={message.id} className={`message ${message.sender_type} ${message.send_status}`} ref={virtual.measureElement} data-index={row.index} style={{position:"absolute",top:0,left:0,width:"100%",transform:`translateY(${row.start}px)`}}><div className="bubble">{message.content}<small>{senderLabel[message.sender_type]} · {formatTime(message.occurred_at)} · {message.send_status}{message.send_status==="failed"&&<button className="retry" onClick={()=>send.mutate(message.id)}>重试</button>}</small></div></article>})}</div></>}</div><footer className="composer"><textarea value={content} maxLength={2000} rows={3} disabled={send.isPending} placeholder="Enter 发送，Shift+Enter 换行" onChange={e=>setContent(e.target.value)} onKeyDown={e=>{if(e.key==="Enter"&&!e.shiftKey){e.preventDefault();if(content.trim())send.mutate()}}}/><div className="composer-foot"><span className="muted">{send.isPending?"发送中…":send.error?.message??""}</span><button className="btn" disabled={!content.trim()||send.isPending} onClick={()=>send.mutate()}>发送</button></div></footer></section>
}

function Info({ userId, graphState }: { userId: number|null; graphState?: GraphState }) { const query=useQuery({enabled:userId!==null,queryKey:["user",userId],queryFn:()=>api<{user:User & {gender:number;first_seen_at:string;last_seen_at:string}}>(`/api/admin/users/${userId}`)}); if(userId===null)return <aside className="panel info empty">用户信息</aside>; if(query.isLoading)return <aside className="panel info">加载中…</aside>; if(query.error)return <aside className="panel info error">{query.error.message}</aside>; const u=query.data!.user; return <aside className="panel info"><div className="large-avatar">{u.nickname.slice(0,1)||"微"}</div><h2>{u.nickname||"未命名用户"}</h2><dl><dt>内部用户 ID</dt><dd>{u.id}</dd><dt>首次出现</dt><dd>{formatTime(u.first_seen_at)}</dd><dt>最近活跃</dt><dd>{formatTime(u.last_seen_at)}</dd><dt>性别</dt><dd>{u.gender===1?"男":u.gender===2?"女":"未知"}</dd>
{graphState && Object.keys(graphState).length>0 && <>
  <dt style={{marginTop:20,borderTop:"1px solid #e5e7eb",paddingTop:12}}>AI 状态</dt>
  {graphState.intent && <dd>意图: {graphState.intent}</dd>}
  {graphState.scenario && <dd>场景: {graphState.scenario}</dd>}
  {graphState.discovery_step!==undefined && <dd>发现步骤: {graphState.discovery_step}</dd>}
  {graphState.business_facts && <>
    <dd>行业: {graphState.business_facts.industry||"-"}</dd>
    <dd>渠道: {graphState.business_facts.channel||"-"}</dd>
    <dd>日咨询量: {graphState.business_facts.volume||"-"}</dd>
    <dd>痛点: {graphState.business_facts.pain||"-"}</dd>
    <dd>目标: {graphState.business_facts.goal||"-"}</dd>
  </>}
</>}
</dl></aside> }

function Logs() {
  const [level, setLevel] = useState("INFO")
  const [lines, setLines] = useState(200)
  const [auto, setAuto] = useState(false)
  const query = useQuery({ queryKey: ["admin-logs",level,lines], queryFn: ()=>api<{logs:string[]}>(`/api/admin/logs?lines=`+lines+`&level=`+level), refetchInterval: auto ? 5000 : false })
  return <section className="panel logs-view"><header className="panel-head"><h1>日志</h1>
  <div style={{display:"flex",gap:8,alignItems:"center",marginTop:8,flexWrap:"wrap"}}>
    <select value={level} onChange={e=>setLevel(e.target.value)} style={{padding:"6px 10px",borderRadius:6,border:"1px solid #d6dbe1"}}><option>INFO</option><option>DEBUG</option><option>WARNING</option><option>ERROR</option></select>
    <input type="number" value={lines} onChange={e=>setLines(Number(e.target.value))} min={10} max={2000} style={{width:70,padding:6,borderRadius:6,border:"1px solid #d6dbe1"}}/>
    <button className="secondary-btn" onClick={()=>query.refetch()}>刷新</button>
    <label style={{fontSize:12,color:"#667085",display:"flex",alignItems:"center",gap:4}}><input type="checkbox" checked={auto} onChange={e=>setAuto(e.target.checked)}/>自动刷新</label>
  </div></header>
  <div className="log-scroll">{query.isLoading?<div className="empty">加载中...</div>:query.error?<div className="error">{query.error.message}</div>:<pre className="log-pre">{(query.data?.logs||[]).join("\n")||"(无匹配日志)"}</pre>}</div></section>
}

function App() {
  const [selected, setSelected] = useState<number | null>(null)
  const [graphState, setGraphState] = useState<GraphState | undefined>(undefined)
  const [view, setView] = useState<"conversations" | "customers" | "logs">("conversations")
  function openConversation(id: number) { setSelected(id); setView("conversations") }
  return <div className="admin-shell"><nav className="admin-navigation" aria-label="后台导航"><strong>客服后台</strong><button aria-current={view === "conversations" ? "page" : undefined} onClick={() => setView("conversations")}>会话工作台</button><button aria-current={view === "customers" ? "page" : undefined} onClick={() => { setSelected(null); setView("customers") }}>客户管理</button>
<button aria-current={view === "logs" ? "page" : undefined} onClick={() => setView("logs")}>日志</button></nav>
    {view === "logs" ? <Logs/> : view === "customers" ? <Customers onOpen={openConversation}/> : <main className="admin-app"><Users selected={selected} onSelect={setSelected}/><Conversation key={selected} userId={selected} onGraphState={setGraphState}/><Info userId={selected} graphState={graphState}/></main>}
  </div>
}
createRoot(document.getElementById("root")!).render(<StrictMode><QueryClientProvider client={client}><App/></QueryClientProvider></StrictMode>)
