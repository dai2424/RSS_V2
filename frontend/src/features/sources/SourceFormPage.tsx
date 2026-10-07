import { useId } from "react";
import { Link } from "react-router-dom";
import { Button, Card, ErrorState, PageHeader } from "../../components/ui";
import { UnsavedChanges } from "../../components/UnsavedChanges";
import { useSourceForm } from "./useSourceForm";
import { SourceFields } from "./SourceFields";

/** 来源新增与编辑页面；数据用例、字段展示和离开保护分别复用。 */
export function SourceFormPage() {
  const id = useId();
  const { sourceId, source, categories, form, save, dirty, saved } =
    useSourceForm();
  if (sourceId && source.isPending)
    return (
      <p className="loading" role="status">
        正在加载来源…
      </p>
    );
  return (
    <div className="page">
      <PageHeader
        title={sourceId ? "编辑 RSS 来源" : "新增 RSS 来源"}
        description="保存配置后，在详情页测试来源并触发采集。"
      />
      {source.isError && (
        <ErrorState
          message={source.error.message}
          onRetry={() => void source.refetch()}
        />
      )}
      {categories.isError && (
        <ErrorState
          message={categories.error.message}
          onRetry={() => void categories.refetch()}
        />
      )}
      <Card className="form-card">
        <form onSubmit={save} noValidate>
          <fieldset
            disabled={
              form.formState.isSubmitting ||
              source.isError ||
              categories.isPending
            }
            className="form-grid"
          >
            <SourceFields id={id} form={form} categories={categories.data} />
          </fieldset>
          {form.formState.errors.root && (
            <p role="alert" className="field-error">
              {form.formState.errors.root.message}
            </p>
          )}
          <div className="form-actions">
            <Link
              className="button secondary"
              to={
                sourceId
                  ? `/sources/${sourceId}`
                  : sessionStorage.getItem("sources-return") || "/sources"
              }
            >
              取消
            </Link>
            <Button
              type="submit"
              disabled={
                form.formState.isSubmitting ||
                source.isError ||
                categories.isPending
              }
            >
              {form.formState.isSubmitting ? "保存中…" : "保存来源"}
            </Button>
          </div>
        </form>
      </Card>
      <UnsavedChanges dirty={dirty} allowNavigation={() => saved.current} />
    </div>
  );
}
