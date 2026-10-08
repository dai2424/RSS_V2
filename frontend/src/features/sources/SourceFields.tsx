import type { UseFormReturn } from "react-hook-form";
import type { components } from "../../api/generated";
import type { Values } from "./useSourceForm";
import { CategoryDialog } from "./CategoryDialog";

type Category = components["schemas"]["CategoryResponse"];

/** 展示来源配置字段及各字段错误，不负责请求或导航。 */
export function SourceFields({
  id,
  form,
  categories,
}: {
  id: string;
  form: UseFormReturn<Values>;
  categories?: Category[];
}) {
  // 行业用受控 select：新建分类后异步刷新选项，非受控控件对未就绪的值会静默清空。
  const categoryValue = form.watch("category_id");
  const setCategory = (value: string) =>
    form.setValue("category_id", value, { shouldDirty: true });
  return (
    <>
      <Field
        id={`${id}-name`}
        label="来源名称"
        error={form.formState.errors.name?.message}
      >
        <input id={`${id}-name`} className="input" {...form.register("name")} />
      </Field>
      <Field id={`${id}-platform`} label="平台">
        <input
          id={`${id}-platform`}
          className="input"
          placeholder="例如：官方博客"
          {...form.register("platform")}
        />
      </Field>
      <Field
        id={`${id}-url`}
        label="RSS 地址"
        error={form.formState.errors.url?.message}
        full
      >
        <input id={`${id}-url`} className="input" {...form.register("url")} />
      </Field>
      <Field id={`${id}-language`} label="语言">
        <select id={`${id}-language`} {...form.register("language")}>
          <option value="auto">自动识别</option>
          <option value="en">英文</option>
          <option value="zh">中文</option>
          <option value="mixed">混合</option>
        </select>
      </Field>
      <Field
        id={`${id}-category`}
        label="行业分类"
        error={form.formState.errors.category_id?.message}
      >
        <div className="control-group">
          <select
            id={`${id}-category`}
            value={categoryValue}
            onChange={(event) => setCategory(event.target.value)}
          >
            <option value="">请选择</option>
            {categories
              ?.filter((item) => item.is_active)
              .map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
          </select>
          <CategoryDialog onSelect={setCategory} />
        </div>
      </Field>
      <Field
        id={`${id}-offset`}
        label="时间偏移（分钟）"
        error={form.formState.errors.time_offset_minutes?.message}
      >
        <input
          id={`${id}-offset`}
          className="input"
          type="number"
          {...form.register("time_offset_minutes", {
            valueAsNumber: true,
          })}
        />
      </Field>
    </>
  );
}

function Field({
  id,
  label,
  error,
  full,
  children,
}: {
  id: string;
  label: string;
  error?: string;
  full?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div className={`form-field${full ? " full" : ""}`}>
      <label htmlFor={id}>{label}</label>
      {children}
      {error && (
        <span role="alert" className="field-error">
          {error}
        </span>
      )}
    </div>
  );
}
