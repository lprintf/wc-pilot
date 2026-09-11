import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { api, formatTime, Profile, User } from "../shared/api"

export function Avatar({ user }: { user: Pick<User, "nickname" | "avatar_url"> }) {
  const [failedUrl, setFailedUrl] = useState("")
  return <span className="avatar">{user.avatar_url && failedUrl !== user.avatar_url
    ? <img src={user.avatar_url} alt="" referrerPolicy="no-referrer" onError={() => setFailedUrl(user.avatar_url)} />
    : user.nickname.slice(0, 1) || "微"}</span>
}

type ManagedProfile = Profile & { notes: string; tags: string[] }

function CustomerEditor({ user, onDeleted }: { user: ManagedProfile; onDeleted: () => void }) {
  const cache = useQueryClient()
  const [nickname, setNickname] = useState(user.nickname)
  const [gender, setGender] = useState(user.gender)
  const [notes, setNotes] = useState(user.notes)
  const [tags, setTags] = useState(user.tags.join("，"))
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [confirmation, setConfirmation] = useState("")
  const save = useMutation({ mutationFn: () => api(`/api/admin/users/${user.id}`, {
    method: "PATCH", headers: { "content-type": "application/json", "x-requested-with": "XMLHttpRequest" },
    body: JSON.stringify({ nickname, gender, notes, tags: tags.split(/[,，]/).map(tag => tag.trim()).filter(Boolean) }),
  }), onSuccess: () => { cache.invalidateQueries({ queryKey: ["admin-users"] }); cache.invalidateQueries({ queryKey: ["user", user.id] }) } })
  const remove = useMutation({ mutationFn: () => api(`/api/admin/users/${user.id}`, {
    method: "DELETE", headers: { "content-type": "application/json", "x-requested-with": "XMLHttpRequest" }, body: JSON.stringify({ confirm_user_id: user.id }),
  }), onSuccess: () => { cache.removeQueries({ queryKey: ["user", user.id] }); cache.removeQueries({ queryKey: ["conversation", user.id] }); cache.removeQueries({ queryKey: ["customer-identity", user.id] }); cache.invalidateQueries({ queryKey: ["admin-users"] }); onDeleted() } })
  return <div className="customer-editor"><h3>编辑客户资料</h3><p className="muted">仅修改本系统展示，微信资料同步不会覆盖这些设置。</p>
    <form onSubmit={event => { event.preventDefault(); save.mutate() }}><fieldset disabled={save.isPending || remove.isPending}>
      <label>昵称<input value={nickname} maxLength={100} onChange={event => setNickname(event.target.value)}/></label>
      <label>性别<select value={gender} onChange={event => setGender(Number(event.target.value))}><option value={0}>未知</option><option value={1}>男</option><option value={2}>女</option></select></label>
      <label>标签<input value={tags} onChange={event => setTags(event.target.value)} placeholder="用逗号分隔，最多 10 个"/></label>
      <label>客户备注<textarea value={notes} maxLength={2000} rows={4} onChange={event => setNotes(event.target.value)}/></label>
      <button className="btn" type="submit">{save.isPending ? "保存中…" : "保存资料"}</button>
    </fieldset></form>
    {save.error && <p role="alert" className="error">{save.error.message}</p>}{save.isSuccess && <p role="status">已保存</p>}
    <div className="delete-customer"><button className="danger-btn" disabled={save.isPending || remove.isPending} onClick={() => setConfirmDelete(true)}>删除客户</button>
      {confirmDelete && <div role="group" aria-label="确认删除客户"><p>将删除本系统中的资料、备注、标签、对话及登录会话，无法恢复。保留防重复发送和额度记录；客户再次发消息后会重新出现在列表中。</p><label>输入客户 ID {user.id} 确认<input value={confirmation} onChange={event => setConfirmation(event.target.value)}/></label><div className="customer-actions"><button className="danger-btn" disabled={confirmation !== String(user.id) || remove.isPending || save.isPending} onClick={() => remove.mutate()}>{remove.isPending ? "删除中…" : "确认删除"}</button><button className="secondary-btn" disabled={remove.isPending} onClick={() => { setConfirmDelete(false); setConfirmation("") }}>取消</button></div></div>}
      {remove.error && <p role="alert" className="error">{remove.error.message}</p>}
    </div>
  </div>
}

function CustomerDetail({ userId, onOpen, onDeleted }: { userId: number; onOpen: (id: number) => void; onDeleted: () => void }) {
  const [showIdentity, setShowIdentity] = useState(false)
  const [copyStatus, setCopyStatus] = useState("")
  const detail = useQuery({ queryKey: ["user", userId], queryFn: () => api<{ user: ManagedProfile }>(`/api/admin/users/${userId}`) })
  const identity = useQuery({ enabled: showIdentity, queryKey: ["customer-identity", userId], gcTime: 0,
    queryFn: () => api<{ external_userid: string; open_kfid: string }>(`/api/admin/users/${userId}/identity`) })
  const user = detail.data?.user
  async function copyId() {
    try { await navigator.clipboard.writeText(identity.data!.external_userid); setCopyStatus("已复制") }
    catch { setCopyStatus("复制失败，请选中 ID 手动复制") }
  }
  return <aside className="panel customer-detail" aria-label="客户详情">
    <h2>客户详情</h2>
    {detail.isPending ? <p className="muted">加载中…</p> : detail.error ? <div role="alert" className="error">{detail.error.message}<button className="btn" onClick={() => detail.refetch()}>重试</button></div> : user && <>
      <div className="customer-name"><Avatar user={user}/><strong>{user.nickname || "未命名客户"}</strong></div>
      <dl><dt>内部用户 ID</dt><dd>{user.id}</dd><dt>性别</dt><dd>{user.gender === 1 ? "男" : user.gender === 2 ? "女" : "未知"}</dd><dt>首次出现</dt><dd>{formatTime(user.first_seen_at)}</dd><dt>最近活跃</dt><dd>{formatTime(user.last_seen_at)}</dd></dl>
      <button className="btn" onClick={() => onOpen(user.id)}>进入会话</button>
      <div className="identity-section"><button className="secondary-btn" aria-expanded={showIdentity} onClick={() => setShowIdentity(value => !value)}>{showIdentity ? "收起微信身份" : "查询企业微信 ID"}</button>
        {showIdentity && <>{identity.isPending ? <p className="muted">查询中…</p> : identity.error ? <p role="alert" className="error">{identity.error.message}<button className="secondary-btn" onClick={() => identity.refetch()}>重试</button></p> : identity.data && <>
          <dl><dt>企业微信客户 ID</dt><dd className="identity-value">{identity.data.external_userid}</dd><dt>客服账号 ID</dt><dd className="identity-value">{identity.data.open_kfid}</dd></dl>
          <button className="secondary-btn" onClick={copyId}>复制客户 ID</button><p role="status" className="muted">{copyStatus}</p>
        </>}</>}
      </div>
      <CustomerEditor user={user} onDeleted={onDeleted}/>
    </>}
  </aside>
}

export function Customers({ onOpen }: { onOpen: (id: number) => void }) {
  const [search, setSearch] = useState("")
  const [sort, setSort] = useState("recent")
  const [page, setPage] = useState(1)
  const [selected, setSelected] = useState<number | null>(null)
  const query = useQuery({ queryKey: ["admin-users", "management", search, sort, page],
    queryFn: () => api<{ users: User[]; total: number }>(`/api/admin/users?q=${encodeURIComponent(search)}&sort=${sort}&page=${page}&page_size=20`) })
  const total = query.data?.total ?? 0
  return <main className="customer-management"><section className="panel customer-list">
    <header className="customer-list-head"><div><h1>客户管理</h1><p className="muted">查看本客服账号已保存的客户资料与历史会话</p></div><button className="secondary-btn" disabled={query.isFetching} onClick={() => query.refetch()}>刷新</button></header>
    <div className="customer-filters"><input aria-label="搜索客户" placeholder="搜索昵称或内部用户 ID" value={search} onChange={event => { setSearch(event.target.value); setPage(1); setSelected(null) }}/><select aria-label="客户排序" value={sort} onChange={event => { setSort(event.target.value); setPage(1); }}><option value="recent">最近活跃优先</option><option value="oldest">最早活跃优先</option><option value="unread">未读消息优先</option></select></div>
    <div className="customer-table-wrap">{query.isPending ? <div className="empty">加载客户中…</div> : query.error ? <div role="alert" className="error">{query.error.message}<button className="secondary-btn" onClick={() => query.refetch()}>重试</button></div> : !query.data.users.length ? <div className="empty">{search ? "没有找到匹配的客户" : "暂无客户"}</div> : <table className="customer-table"><thead><tr><th>客户</th><th>内部 ID</th><th>最近消息</th><th>最近活跃</th><th>未读</th><th>操作</th></tr></thead><tbody>{query.data.users.map(user => <tr key={user.id} className={selected === user.id ? "selected" : ""}><td><div className="customer-name"><Avatar user={user}/><span>{user.nickname || "未命名客户"}</span></div></td><td>#{user.id}</td><td><span className="customer-preview" title={user.last_message_preview}>{user.last_message_preview || "暂无消息"}</span></td><td>{formatTime(user.last_active_at)}</td><td>{user.unread_count || "—"}</td><td><div className="customer-actions"><button className="secondary-btn" onClick={() => setSelected(user.id)}>详情</button><button className="secondary-btn" onClick={() => onOpen(user.id)}>会话</button></div></td></tr>)}</tbody></table>}</div>
    <footer className="customer-pagination"><span>{query.data ? `共 ${total} 位客户 · 第 ${page} 页` : `第 ${page} 页`}</span><div><button className="secondary-btn" disabled={page === 1 || query.isFetching} onClick={() => setPage(value => value - 1)}>上一页</button><button className="secondary-btn" disabled={!query.data || page * 20 >= total || query.isFetching} onClick={() => setPage(value => value + 1)}>下一页</button></div></footer>
  </section>{selected === null ? <aside className="panel customer-detail empty">选择客户查看详情</aside> : <CustomerDetail key={selected} userId={selected} onOpen={onOpen} onDeleted={() => { setSelected(null); setPage(1) }}/>}</main>
}
