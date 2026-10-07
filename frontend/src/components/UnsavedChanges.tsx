import { useEffect, useRef, type RefObject } from "react";
import { useBlocker } from "react-router-dom";
import { Button } from "./ui";
import { Sheet, SheetContent, SheetTitle, SheetDescription } from "./ui/sheet";

/** 在路由切换和浏览器关闭时保护输入，保存成功可即时放行导航。 */
export function UnsavedChanges({
  dirty,
  allowNavigation = () => false,
}: {
  dirty: boolean;
  allowNavigation?: () => boolean;
}) {
  const returnFocus = useRef<HTMLElement | null>(null);
  const blocker = useBlocker(({ currentLocation, nextLocation }) => {
    if (currentLocation.pathname === nextLocation.pathname) return false;
    if (!dirty || allowNavigation()) return false;
    if (document.activeElement instanceof HTMLElement)
      returnFocus.current = document.activeElement;
    return true;
  });
  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => {
      if (dirty && !allowNavigation()) event.preventDefault();
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty, allowNavigation]);
  return (
    <UnsavedDialog
      open={blocker.state === "blocked"}
      onKeep={() => {
        if (blocker.state === "blocked") blocker.reset();
      }}
      onDiscard={() => {
        if (blocker.state === "blocked") blocker.proceed();
      }}
      returnFocus={returnFocus}
    />
  );
}

/** 路由离开和局部表单关闭共用确认界面及焦点恢复行为。 */
export function UnsavedDialog({
  open,
  onKeep,
  onDiscard,
  returnFocus,
}: {
  open: boolean;
  onKeep: () => void;
  onDiscard: () => void;
  returnFocus: RefObject<HTMLElement | null>;
}) {
  const keepEditing = useRef<HTMLButtonElement | null>(null);
  return (
    <Sheet
      open={open}
      onOpenChange={(isOpen) => {
        if (!isOpen) onKeep();
      }}
    >
      <SheetContent
        side="bottom"
        role="alertdialog"
        className="sm:mx-auto sm:max-w-lg"
        onOpenAutoFocus={(event) => {
          event.preventDefault();
          keepEditing.current?.focus();
        }}
        onCloseAutoFocus={(event) => {
          event.preventDefault();
          returnFocus.current?.focus();
        }}
      >
        <SheetTitle>未保存的修改</SheetTitle>
        <SheetDescription>有未保存的修改，确定离开吗？</SheetDescription>
        <div className="toolbar">
          <Button ref={keepEditing} onClick={onKeep}>
            继续编辑
          </Button>
          <Button className="secondary" onClick={onDiscard}>
            放弃修改并离开
          </Button>
        </div>
      </SheetContent>
    </Sheet>
  );
}
