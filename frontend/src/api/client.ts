import createClient from "openapi-fetch";
import type { paths } from "./generated";
export const api = createClient<paths>({ baseUrl: "" });

/** 取出可展示的失败原因；错误体没有 message 时用状态码兜底。 */
function errorMessage(error: unknown, status: number): string {
  return error && typeof error === "object" && "message" in error
    ? String(error.message)
    : "请求失败（" + status + "）";
}

export function requireResponse<T>(
  response: Response,
  value: T | undefined,
  error?: unknown,
): T {
  if (!response.ok) throw new Error(errorMessage(error, response.status));
  if (value === undefined) throw new Error("服务器返回了空响应");
  return value;
}

/** 无响应体的请求（204 删除）成功即返回；失败时抛出与 requireResponse 同样的信息。 */
export function requireNoContent(response: Response, error?: unknown): void {
  if (!response.ok) throw new Error(errorMessage(error, response.status));
}
