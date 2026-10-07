import type {
  ComponentProps,
  HTMLAttributes,
  InputHTMLAttributes,
  ReactNode,
} from "react";
import { Button as BaseButton } from "./ui/button";
import { Input as BaseInput } from "./ui/input";
import { Card as BaseCard } from "./ui/card";
import { Badge as BaseBadge } from "./ui/badge";

export function Button({
  className = "",
  type = "button",
  ...props
}: ComponentProps<"button">) {
  const variant = className.includes("secondary")
    ? "outline"
    : className.includes("danger")
      ? "destructive"
      : "default";
  return (
    <BaseButton
      variant={variant}
      type={type}
      className={className}
      {...props}
    />
  );
}
export function Input(props: InputHTMLAttributes<HTMLInputElement>) {
  return <BaseInput {...props} />;
}
export function Card({
  className = "",
  ...props
}: HTMLAttributes<HTMLDivElement>) {
  return <BaseCard className={`card ${className}`} {...props} />;
}
export function Badge({
  children,
  tone = "neutral",
}: {
  children: ReactNode;
  tone?: "neutral" | "success" | "danger" | "warning";
}) {
  return (
    <BaseBadge variant="secondary" className={"badge-" + tone}>
      {children}
    </BaseBadge>
  );
}
export function PageHeader({
  title,
  description,
  action,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <div className="page-header">
      <div>
        <h1>{title}</h1>
        {description && <p className="muted">{description}</p>}
      </div>
      {action && <div className="page-header-action">{action}</div>}
    </div>
  );
}
export function EmptyState({
  title,
  description,
}: {
  title: string;
  description: string;
}) {
  return (
    <div className="empty-state">
      <strong>{title}</strong>
      <span className="muted">{description}</span>
    </div>
  );
}
export function ErrorState({
  message,
  onRetry,
}: {
  message: string;
  onRetry?: () => void;
}) {
  return (
    <div className="error-state" role="alert">
      <span>{message}</span>
      {onRetry && (
        <Button className="secondary" onClick={onRetry}>
          重试
        </Button>
      )}
    </div>
  );
}
/**
 * 行内启停开关；checked 只反映服务端状态，切换结果由调用方提交后刷新。
 * 旁边始终提供文字状态，不单靠开关颜色表达。
 */
export function Switch({
  checked,
  label,
  disabled,
  onToggle,
}: {
  checked: boolean;
  label: string;
  disabled?: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      className={checked ? "switch switch-on" : "switch"}
      disabled={disabled}
      onClick={onToggle}
    >
      <span className="switch-knob" />
    </button>
  );
}
