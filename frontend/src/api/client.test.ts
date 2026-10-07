import { requireResponse } from "./client";

describe("API 错误边界", () => {
  it("使用已解析的服务端错误，响应体被 client 消费后仍有可读提示", async () => {
    const response = new Response(
      '{"code":"conflict","message":"地址已存在"}',
      { status: 409 },
    );
    const error = await response.json();
    expect(response.bodyUsed).toBe(true);
    expect(() => requireResponse(response, undefined, error)).toThrow(
      "地址已存在",
    );
  });
  it("非 JSON 错误保留 HTTP 状态信息", () => {
    expect(() =>
      requireResponse(
        new Response("upstream error", { status: 502 }),
        undefined,
      ),
    ).toThrow("502");
  });
  it("成功返回空内容时阻止页面误判成功", () => {
    expect(() => requireResponse(new Response(), undefined)).toThrow("空响应");
  });
});
