import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api, requireResponse } from "../../api/client";
import { ErrorState } from "../../components/ui";
import { TaskSettingTable } from "./TaskSettingTable";

/** 行业分类的任务默认：来源没有自己的配置时按这里执行。 */
export function TaskSettingsPage() {
  const [categoryId, setCategoryId] = useState("");
  const categories = useQuery({
    queryKey: ["category-options"],
    queryFn: async () => {
      const r = await api.GET("/api/categories");
      return requireResponse(r.response, r.data, r.error);
    },
  });
  const active = categoryId || categories.data?.[0]?.id || "";
  return (
    <div className="page page-wide">
      <div className="page-header">
        <div>
          <h1>任务分配</h1>
          <p className="muted">
            行业分类的默认任务配置；单个来源可以在来源详情里覆盖这里。全局默认由提示词页的启用版本决定。
          </p>
        </div>
      </div>
      {categories.isLoading && <div className="loading">正在加载行业分类…</div>}
      {categories.isError && (
        <ErrorState
          message={categories.error.message}
          onRetry={() => void categories.refetch()}
        />
      )}
      {!!categories.data?.length && (
        <>
          <div className="toolbar">
            <label htmlFor="task-setting-category">行业分类</label>
            <select
              id="task-setting-category"
              value={active}
              onChange={(e) => setCategoryId(e.target.value)}
            >
              {categories.data.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
            </select>
          </div>
          {active && (
            <TaskSettingTable
              key={active}
              scope="category"
              scopeId={active}
              title="分类默认任务"
            />
          )}
        </>
      )}
    </div>
  );
}
