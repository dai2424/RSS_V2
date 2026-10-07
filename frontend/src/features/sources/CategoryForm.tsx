import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, requireResponse } from "../../api/client";
import { Button, Input } from "../../components/ui";
import { UnsavedChanges } from "../../components/UnsavedChanges";

/** 新建行业分类；失败保留输入，成功刷新同一分类缓存。 */
export function CategoryForm() {
  const [categoryName, setCategoryName] = useState("");
  const queryClient = useQueryClient();
  const createCategory = useMutation({
    mutationFn: async () => {
      const r = await api.POST("/api/categories", {
        body: { name: categoryName },
      });
      return requireResponse(r.response, r.data, r.error);
    },
    onSuccess: () => {
      setCategoryName("");
      void queryClient.invalidateQueries({ queryKey: ["categories"] });
    },
  });
  return (
    <>
      <form
        className="toolbar category-form"
        onSubmit={(event) => {
          event.preventDefault();
          createCategory.mutate();
        }}
      >
        <label htmlFor="new-category">自定义行业</label>
        <Input
          id="new-category"
          required
          value={categoryName}
          onChange={(e) => setCategoryName(e.target.value)}
          placeholder="例如：新能源"
        />
        <Button
          type="submit"
          className="secondary"
          disabled={createCategory.isPending}
        >
          新增分类
        </Button>
        {createCategory.isError && (
          <span className="field-error" role="alert">
            {createCategory.error.message}
          </span>
        )}
      </form>{" "}
      <UnsavedChanges dirty={Boolean(categoryName)} />
    </>
  );
}
