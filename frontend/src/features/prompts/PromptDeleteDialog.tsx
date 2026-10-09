import { Button } from "../../components/ui";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "../../components/ui/dialog";
import { showToast } from "../../components/Toast";
import { usePromptActions, type Prompt } from "./usePrompts";

/**
 * 删除确认：先说明对象（版本串与名称）和影响，再执行。
 * 只有从未被引用的版本才会走到这里，被引用过的版本由详情页挡住并引导到归档。
 */
export function PromptDeleteDialog({
  prompt,
  onClose,
  onDeleted,
}: {
  prompt: Prompt | null;
  onClose: () => void;
  onDeleted: () => void;
}) {
  const { remove } = usePromptActions();
  const confirm = () => {
    const target = prompt;
    if (!target) return;
    remove.mutate(target.id, {
      onSuccess: () => {
        showToast({
          type: "success",
          content: `已删除 ${target.version_string}。`,
        });
        onClose();
        onDeleted();
      },
      onError: (error) => showToast({ type: "error", content: error.message }),
    });
  };
  return (
    <Dialog open={Boolean(prompt)} onOpenChange={(next) => !next && onClose()}>
      <DialogContent role="alertdialog">
        <DialogTitle>删除提示词版本</DialogTitle>
        <DialogDescription>
          将删除「{prompt?.version_string}」（{prompt?.name}）及其提示词文本。
          {prompt?.status === "archived"
            ? "它是已归档版本，且没有被任何调用、结果、任务或来源分类引用。"
            : "它没有被任何调用、结果、任务或来源分类引用。"}
          此操作不可恢复；想保留历史可改用「归档」。
        </DialogDescription>
        <div className="toolbar">
          <Button
            className="secondary"
            disabled={remove.isPending}
            onClick={onClose}
          >
            取消
          </Button>
          <Button
            className="danger"
            disabled={remove.isPending}
            onClick={confirm}
          >
            {remove.isPending ? "删除中…" : "确认删除"}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
