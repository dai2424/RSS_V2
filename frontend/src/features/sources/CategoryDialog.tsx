import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { useRef, useState } from "react";
import { api, requireResponse } from "../../api/client";
import { Button, Input } from "../../components/ui";
import { showToast } from "../../components/Toast";
import { UnsavedDialog } from "../../components/UnsavedChanges";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "../../components/ui/dialog";

/**
 * 行业筛选下拉旁的“新增行业”图标按钮：弹层内新建行业，成功后关闭并自动选中新行业。
 * 有未保存输入时关闭弹层需要确认，取消则清空输入。
 */
export function CategoryDialog({
  onSelect,
}: {
  onSelect: (categoryId: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [name, setName] = useState("");
  const trigger = useRef<HTMLButtonElement | null>(null);
  const queryClient = useQueryClient();
  const create = useMutation({
    mutationFn: async () => {
      const r = await api.POST("/api/categories", { body: { name } });
      return requireResponse(r.response, r.data, r.error);
    },
    onSuccess: async (created) => {
      setName("");
      setOpen(false);
      // 非受控下拉对暂时不存在的 option 会把值清空，必须等列表刷新后再回填选值。
      await queryClient.refetchQueries({ queryKey: ["categories"] });
      onSelect(created.id);
      showToast({
        type: "success",
        content: `已新增行业「${created.name}」。`,
      });
    },
  });
  const requestClose = (next: boolean) => {
    if (next) {
      setOpen(true);
      return;
    }
    if (name) {
      setConfirming(true);
    } else {
      setOpen(false);
    }
  };
  return (
    <>
      <button
        ref={trigger}
        type="button"
        className="button secondary icon"
        aria-label="新增行业"
        title="新增行业"
        onClick={() => setOpen(true)}
      >
        <Plus size={16} aria-hidden="true" />
      </button>
      <Dialog open={open} onOpenChange={requestClose}>
        <DialogContent className="max-w-md">
          <DialogTitle>新增行业</DialogTitle>
          <DialogDescription>
            新增后立即可用于来源表单和筛选。
          </DialogDescription>
          <form
            className="inline-form"
            onSubmit={(event) => {
              event.preventDefault();
              create.mutate();
            }}
          >
            <label className="form-field" htmlFor="new-category">
              行业名称
              <Input
                id="new-category"
                required
                autoFocus
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder="例如：新能源"
              />
            </label>
            <Button type="submit" disabled={create.isPending}>
              {create.isPending ? "保存中…" : "新增"}
            </Button>
            {create.isError && (
              <span className="field-error" role="alert">
                {create.error.message}
              </span>
            )}
          </form>
        </DialogContent>
      </Dialog>
      <UnsavedDialog
        open={confirming}
        onKeep={() => setConfirming(false)}
        onDiscard={() => {
          setConfirming(false);
          setName("");
          setOpen(false);
        }}
        returnFocus={trigger}
      />
    </>
  );
}
