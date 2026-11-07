export class APIError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}
export async function api(path, options = {}) {
  const { method = "GET", body, signal } = options;
  let response;
  try {
    response = await fetch("/api/" + path, {
      method,
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal,
    });
  } catch (error) {
    if (error.name === "AbortError") throw error;
    throw new APIError("无法连接到 accbot 服务，请检查服务是否正在运行。", 0);
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    if (response.status === 401)
      window.dispatchEvent(new Event("accbot-auth-required"));
    const detail = Array.isArray(data.detail)
      ? data.detail.map((item) => item.msg).join("；")
      : data.detail;
    throw new APIError(
      data.error?.message || detail || `请求失败（${response.status}）`,
      response.status,
    );
  }
  return data;
}
