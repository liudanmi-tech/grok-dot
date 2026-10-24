// 与后端通信。POC 用 X-User-Id 模拟登录用户（生产为 SSO）。
const USER_KEY = "ngap.user";

export function currentUser(): string {
  try {
    return localStorage.getItem(USER_KEY) || "u-country-manager";
  } catch {
    return "u-country-manager";
  }
}

export function setCurrentUser(id: string) {
  try {
    localStorage.setItem(USER_KEY, id);
  } catch {
    /* 浏览器禁用存储时忽略 */
  }
}

export async function api<T = any>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`/api${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", "X-User-Id": currentUser(), ...(init.headers || {}) },
  });
  const text = await res.text();
  const data = text ? JSON.parse(text) : null;
  if (!res.ok) throw new Error(data?.detail || res.statusText);
  return data as T;
}

export const post = <T = any>(path: string, body?: unknown) =>
  api<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

export const STATUS: Record<string, string> = {
  registered: "已登记", planning: "规划中", awaiting_confirmation: "待确认", running: "执行中",
  awaiting_approval: "等待审批", needs_human: "需要人工处理", paused: "已暂停", delivered: "已交付",
  accepted: "已验收", rework: "需修改", cancelled: "已取消", failed: "失败",
  pending: "待审批", escalated: "已升级", executed: "已执行", rejected: "已拒绝", expired: "已过期",
  returned: "预审退回", done: "完成", skipped: "跳过", started: "已启动", proposed: "待确认", active: "生效中",
  deleted: "已删除",
};

export function fmtTime(s?: string | null) {
  if (!s) return "—";
  const d = new Date(s);
  return d.toLocaleString("zh-CN", { hour12: false, timeZone: "Africa/Lagos" });
}
