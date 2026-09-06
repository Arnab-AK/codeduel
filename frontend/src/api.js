// Thin fetch wrapper around the CodeDuel backend. No axios/react-query --
// the whole surface is a handful of endpoints, and native fetch is enough
// not to need a dependency for it.
const BASE_URL = import.meta.env.VITE_API_URL || "http://localhost:8000";
const WS_BASE_URL = import.meta.env.VITE_WS_URL || "ws://localhost:8000";

async function request(path, { method = "GET", token, body } = {}) {
  const headers = { "Content-Type": "application/json" };
  if (token) headers.Authorization = `Bearer ${token}`;

  const res = await fetch(`${BASE_URL}${path}`, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });

  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail || `Request failed (${res.status})`);
  }
  if (res.status === 204) return null;
  return res.json();
}

export const api = {
  register: (email, password) =>
    request("/auth/register", { method: "POST", body: { email, password } }),
  login: (email, password) =>
    request("/auth/login", { method: "POST", body: { email, password } }),
  logout: (token) => request("/auth/logout", { method: "POST", token }),

  joinQueue: (token, displayName) =>
    request("/queue/join", { method: "POST", token, body: { display_name: displayName } }),
  leaveQueue: (token) => request("/queue/leave", { method: "DELETE", token }),
  queueStatus: (token) => request("/queue/status", { token }),

  getProblem: (problemId) => request(`/problems/${problemId}`),
  getMatch: (matchId) => request(`/matches/${matchId}`),
  getRating: (playerId) => request(`/players/${playerId}/rating`),

  submit: (token, { problemId, code, matchId }) =>
    request("/submissions", {
      method: "POST",
      token,
      body: { problem_id: problemId, code, language: "python", match_id: matchId },
    }),
};

// Auth for the match WebSocket is a query param, not a header -- browsers
// can't set custom headers on a WS handshake. See the backend README's
// Design Decisions for the tradeoff this implies (token visible in access
// logs).
export function matchSocketUrl(matchId, token) {
  return `${WS_BASE_URL}/ws/matches/${matchId}?token=${encodeURIComponent(token)}`;
}
