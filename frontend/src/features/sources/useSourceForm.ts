import { zodResolver } from "@hookform/resolvers/zod";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef } from "react";
import { useForm, type UseFormReturn } from "react-hook-form";
import { useNavigate, useParams } from "react-router-dom";
import { z } from "zod";
import { api, requireResponse } from "../../api/client";

const sourceSchema = z.object({
  name: z.string().trim().min(1, "请输入来源名称"),
  url: z
    .string()
    .url("请输入有效地址")
    .refine((value) => /^https?:\/\//i.test(value), "仅支持 HTTP(S) 地址"),
  platform: z.string(),
  language: z.enum(["auto", "en", "zh", "mixed"]),
  category_id: z.string().min(1, "请选择行业分类"),
  time_offset_minutes: z.number().int().min(-1440).max(1440),
});
export type Values = z.infer<typeof sourceSchema>;

/** 管理来源表单的加载、校验和保存；后台刷新不会覆盖未提交输入。 */
export function useSourceForm() {
  const { sourceId } = useParams();
  const initialized = useRef(false);
  const { source, categories } = useSourceEditorData(sourceId);
  const form = useForm<Values>({
    resolver: zodResolver(sourceSchema),
    defaultValues: {
      name: "",
      url: "",
      platform: "",
      language: "auto",
      category_id: "",
      time_offset_minutes: 0,
    },
  });
  useEffect(() => {
    if (!source.data || initialized.current) return;
    const value = source.data.source;
    const parsed = sourceSchema.safeParse(value);
    if (parsed.success) {
      form.reset(parsed.data);
      initialized.current = true;
    }
  }, [source.data, form]);
  const dirty = form.formState.isDirty;
  const { save, saved } = useSourceSave(form, sourceId);
  return { sourceId, source, categories, form, save, dirty, saved };
}

/** 按路由加载来源及可选分类，新增时不请求不存在的详情。 */
function useSourceEditorData(sourceId?: string) {
  const categories = useQuery({
    queryKey: ["categories"],
    queryFn: async () => {
      const result = await api.GET("/api/categories");
      return requireResponse(result.response, result.data, result.error);
    },
  });
  const source = useQuery({
    enabled: Boolean(sourceId),
    queryKey: ["source", sourceId],
    queryFn: async () => {
      const result = await api.GET("/api/sources/{source_id}", {
        params: { path: { source_id: sourceId! } },
      });
      return requireResponse(result.response, result.data, result.error);
    },
  });
  return { source, categories };
}

/** 保存成功后跳转；业务失败转换为表单内错误并保留全部字段。 */
function useSourceSave(form: UseFormReturn<Values>, sourceId?: string) {
  const navigate = useNavigate();
  const saved = useRef(false);
  const save = form.handleSubmit(async (values) => {
    form.clearErrors("root");
    try {
      const result = sourceId
        ? await api.PATCH("/api/sources/{source_id}", {
            params: { path: { source_id: sourceId } },
            body: values,
          })
        : await api.POST("/api/sources", { body: values });
      const created = requireResponse(
        result.response,
        result.data,
        result.error,
      );
      saved.current = true;
      navigate(`/sources/${created.id}`);
    } catch (error) {
      form.setError("root", {
        message: error instanceof Error ? error.message : "保存失败，请重试",
      });
    }
  });
  return { save, saved };
}
