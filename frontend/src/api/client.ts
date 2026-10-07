import createClient from "openapi-fetch";
import type { paths } from "./generated";
export const api = createClient<paths>({ baseUrl: "" });
export function requireResponse<T>(
  response: Response,
  value: T | undefined,
  error?: unknown,
): T {
  if (!response.ok) {
    const message =
      error && typeof error === "object" && "message" in error
        ? String(error.message)
        : "请求失败（" + response.status + "）";
    throw new Error(message);
  }
  if (value === undefined) throw new Error("服务器返回了空响应");
  return value;
}
